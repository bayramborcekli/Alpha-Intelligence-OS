"""
Mission 1400.6 — Risk Intelligence Engine (SALT-OKUNUR, TAVSİYE NİTELİĞİNDE).

- Borsaya HİÇBİR yazma isteği göndermez; yalnızca mevcut önbellekli
  salt-okunur modelleri (dashboard_api / portfolio_api) tüketir.
- Tüm para matematiği Decimal'dir; float muhasebe YOKTUR.
- Skorlama DETERMİNİSTİKTİR (yapay zekâ yok, rastgelelik yok).
- Doğrulanamayan değerler ASLA tahmin edilmez → null ("Veri Yok").
- Çapraz kur birleştirme YAPILMAZ: tüm oran/yüzde hesapları tek para
  birimi (Global Futures USDT) evreninde yapılır. Binance TR varlıkları
  yalnızca adet olarak listelenir, USD karşılığı üretilmez.
- Geçmiş anlık görüntüler EKLE-YALNIZ (append-only) tutulur; önceki
  kayıtların üzerine asla yazılmaz.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import dashboard_api as dapi
import portfolio_api as pf

HISTORY_PATH = Path("risk_history.jsonl")   # ekle-yalnız yerel geçmiş
_HISTORY_LOCK = threading.Lock()
_MAX_HISTORY_LINES = 5000                   # sınırsız okuma yok

STABLECOINS = {"USDT", "USDC", "FDUSD", "BUSD", "TUSD", "DAI", "USDP"}
KNOWN_EXCHANGES: set[str] = set()  # Futures kaldırıldı; simulate() artık exchange doğrulaması yapmıyor

# ── PAKET 6.10 — Yapılandırılabilir eşikler ────────────────────────────────
# İş mantığında sabit kodlanmış eşik YOKTUR; tüm değerler risk_config.json
# üzerinden yüklenir (dosya yoksa/geçersizse aşağıdaki varsayılanlar).
CONFIG_PATH = Path("risk_config.json")

_DEFAULT_THRESHOLDS = {
    "RISK_HIGH_MARGIN": "60",          # marj kullanımı uyarı eşiği (%)
    "RISK_CRITICAL_MARGIN": "80",      # marj kullanımı kritik eşiği (%)
    "MAX_POSITION_PERCENT": "25",      # tek pozisyon Medium eşiği (%)
    "POSITION_HIGH_PERCENT": "40",     # tek pozisyon High eşiği (%)
    "POSITION_CRITICAL_PERCENT": "60", # tek pozisyon Critical eşiği (%)
    "MAX_EXCHANGE_PERCENT": "100",     # tek borsa payı eşiği (%)
    "HIGH_EXPOSURE_PERCENT": "150",    # brüt maruziyet / marj eşiği (%)
    "LOW_AVAILABLE_PERCENT": "20",     # düşük kullanılabilir bakiye (%)
    "DRAWDOWN_WARN_PERCENT": "-10",    # günlük düşüş uyarı eşiği (%)
    "MAX_OPEN_ORDERS": "20",           # açık emir yoğunluğu eşiği (adet)
}

_cfg_cache: dict = {"mtime": None, "values": None}


def thresholds() -> dict[str, Decimal]:
    """Eşikleri yapılandırmadan yükler (mtime önbellekli, fail-safe)."""
    vals = {k: Decimal(v) for k, v in _DEFAULT_THRESHOLDS.items()}
    try:
        mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None
    except OSError:
        mtime = None
    if mtime is not None:
        if _cfg_cache["mtime"] == mtime and _cfg_cache["values"]:
            return dict(_cfg_cache["values"])
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in vals:
                    d = _dec(raw.get(k))
                    if d is not None:
                        vals[k] = d
        except (OSError, json.JSONDecodeError, ValueError):
            pass    # geçersiz yapılandırma → güvenli varsayılanlar
        _cfg_cache["mtime"] = mtime
        _cfg_cache["values"] = dict(vals)
    return vals

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None   # NaN/Infinity asla kabul edilmez


def _pct(part: Decimal, whole: Decimal) -> str | None:
    if (whole is None or part is None or not whole.is_finite()
            or not part.is_finite() or whole == 0):
        return None
    try:
        return str((part / whole * 100).quantize(Decimal("0.01"),
                                                 rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def _q2(v: Decimal | None) -> str | None:
    if v is None or not v.is_finite():
        return None
    try:
        return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


# ── Kaynak modeller ─────────────────────────────────────────────────────────

def _cached_model(kind: str) -> dict | None:
    """SADECE mevcut önbelleği okur — asla yeni borsa isteği tetiklemez."""
    with dapi._cache_lock:
        entry = dapi._cache.get(kind)
    if entry and entry.get("ok") and isinstance(entry.get("data"), dict):
        return entry["data"]
    return None

def _open_orders_count() -> int | None:
    """Futures kaldırıldı — artık Spot emirleri için kullanılmıyor; None döner."""
    return None


def _base_asset(symbol: str) -> str:
    for quote in ("USDT", "USDC", "BUSD", "USD", "TRY", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def _notional(p: dict) -> Decimal:
    amt = _dec(p.get("position_amt")) or Decimal(0)
    mark = _dec(p.get("mark_price")) or Decimal(0)
    return abs(amt) * mark


# ── PAKET 6.2 — Maruziyet ──────────────────────────────────────────────────

def _spot_account() -> dict | None:
    """Binance Global Spot hesabı (önbellekli servis üzerinden).

    Başarısızsa None döner — asla tahmin üretilmez."""
    try:
        model = dapi.global_spot_account()
    except Exception:
        return None
    if not model.get("ok"):
        return None
    return model


def exposure() -> dict:
    """Spot-only maruziyet: Binance Global Spot bakiyeleri (USDT değerli)
    + Binance TR bakiyeleri (yalnızca adet — kur uydurulmaz)."""
    spot = _spot_account()
    ta = dapi.tr_account()

    total_spot = None            # toplam Spot varlık değeri (USDT)
    spot_valuation = None        # FULL / PARTIAL
    usdt_free = None
    usdt_locked = None
    by_asset_raw: list[dict] = []
    unpriced_assets: list[dict] = []
    if spot is not None:
        total_spot = _dec(spot.get("total_spot_value_usdt"))
        spot_valuation = spot.get("valuation")
        usdt_free = _dec(spot.get("usdt_free"))
        usdt_locked = _dec(spot.get("usdt_locked"))
        for h in (spot.get("top_holdings") or []):
            if isinstance(h, dict):
                by_asset_raw.append(
                    {"asset": h.get("asset"),
                     "quantity": h.get("amount"),
                     "value_usdt": _dec(h.get("value_usdt"))})
        # Fiyatlanamayan varlıklar (asset + adet; value_usdt her zaman null)
        for u in (spot.get("unpriced_holdings") or []):
            if isinstance(u, dict):
                unpriced_assets.append({"asset": u.get("asset"),
                                        "quantity": u.get("amount"),
                                        "value_usdt": None})

    gross = total_spot if total_spot is not None else Decimal(0)
    stable_value = (usdt_free or Decimal(0)) + (usdt_locked or Decimal(0)) \
        if (usdt_free is not None or usdt_locked is not None) else None

    assets = [{"asset": h["asset"],
               "quantity": h["quantity"],
               "exposure_value_usdt": _q2(h["value_usdt"]),
               "exposure_pct": (_pct(h["value_usdt"], gross)
                                if h["value_usdt"] is not None and gross
                                else None)}
              for h in by_asset_raw]

    # Binance TR: yalnızca adet; USD karşılığı ÜRETİLMEZ (kur uydurulmaz)
    tr_stables, tr_assets = [], []
    if ta.get("ok"):
        u_free = _dec(ta.get("usdt_free")) or Decimal(0)
        u_lock = _dec(ta.get("usdt_locked")) or Decimal(0)
        t_free = _dec(ta.get("try_free")) or Decimal(0)
        t_lock = _dec(ta.get("try_locked")) or Decimal(0)
        if u_free or u_lock:
            tr_stables.append({"asset": "USDT",
                               "quantity": str(u_free + u_lock)})
        if t_free or t_lock:
            tr_assets.append({"asset": "TRY",
                              "quantity": str(t_free + t_lock)})

    return {
        "ok": True, "read_only": True, "as_of": _now_iso(),
        "universe": "SPOT_ONLY",
        # Spot bakiye tabanlı toplam varlık değeri (doğrulanamazsa null)
        "total_spot_value_usdt": _q2(total_spot),
        "spot_valuation": spot_valuation,
        "gross_exposure_usdt": _q2(total_spot) if total_spot is not None
        else None,
        "net_exposure_usdt": _q2(total_spot) if total_spot is not None
        else None,
        "long_exposure_usdt": _q2(total_spot) if total_spot is not None
        else None,
        "short_exposure_usdt": _q2(Decimal(0)) if total_spot is not None
        else None,
        "exposure_pct_of_margin": None,   # Futures marjı yok (Spot-only)
        "cash_available_usdt": _q2(usdt_free),
        "stablecoin_value_usdt": _q2(stable_value),
        "by_asset": assets,
        "unpriced_assets": unpriced_assets,
        "by_direction": {
            "long_pct": "100.00" if total_spot else None,
            "short_pct": "0.00" if total_spot else None,
        },
        "by_quote_currency": [{"quote": "USDT",
                               "exposure_value_usdt": _q2(total_spot),
                               "exposure_pct": "100.00" if total_spot
                               else None}],
        "by_exchange": ([{"exchange": "BINANCE_GLOBAL_SPOT",
                          "exposure_value_usdt": _q2(total_spot),
                          "exposure_pct": "100.00"}]
                        if total_spot else []),
        "by_market": [{"market": "SPOT",
                       "exposure_value_usdt": _q2(total_spot),
                       "exposure_pct": "100.00" if total_spot else None}],
        "binance_tr_holdings": {
            "note": "Yalnızca adet — USD karşılığı doğrulanamadığı için "
                    "hesaplanmaz (kur tahmini yapılmaz).",
            "stablecoins": tr_stables[:50],
            "other_assets": tr_assets[:50],
        },
    }


# ── PAKET 6.3 — Konsantrasyon ──────────────────────────────────────────────

def concentration() -> dict:
    """Spot varlık yoğunlaşması: en büyük varlığın toplam Spot değeri
    içindeki payı (yalnızca fiyatlanmış varlıklar)."""
    spot = _spot_account()
    holdings: list[tuple[str, Decimal]] = []
    gross = Decimal(0)
    if spot is not None:
        total = _dec(spot.get("total_spot_value_usdt"))
        if total is not None:
            gross = total
        for h in (spot.get("top_holdings") or []):
            if isinstance(h, dict):
                v = _dec(h.get("value_usdt"))
                if v is not None:
                    holdings.append((h.get("asset") or "?", v))
    rows = sorted(holdings, key=lambda kv: kv[1], reverse=True)
    top = [{"symbol": a, "direction": "SPOT",
            "notional_usdt": _q2(v),
            "share_pct": _pct(v, gross)}
           for a, v in rows[:5]]
    largest_pct = _dec(top[0]["share_pct"]) if top and top[0]["share_pct"] \
        else None
    th = thresholds()
    warnings = []
    if largest_pct is not None:
        if largest_pct >= th["POSITION_CRITICAL_PERCENT"]:
            warnings.append({"level": "Critical",
                             "message": f"Tek pozisyon payı %{largest_pct} — "
                             f"kritik eşik %{th['POSITION_CRITICAL_PERCENT']}"
                             f" aşıldı."})
        elif largest_pct >= th["POSITION_HIGH_PERCENT"]:
            warnings.append({"level": "High",
                             "message": f"Tek pozisyon payı %{largest_pct} — "
                             f"yüksek eşik %{th['POSITION_HIGH_PERCENT']} "
                             f"aşıldı."})
        elif largest_pct >= th["MAX_POSITION_PERCENT"]:
            warnings.append({"level": "Medium",
                             "message": f"Tek pozisyon payı %{largest_pct} — "
                             f"eşik %{th['MAX_POSITION_PERCENT']} aşıldı."})
    return {
        "ok": True, "read_only": True, "as_of": _now_iso(),
        "largest_position": top[0] if top else None,
        "top5": top,
        "single_position_pct": str(largest_pct) if largest_pct is not None
        else None,
        "exchange_pct": [],
        "sector_grouping": None,   # doğrulanmış sektör verisi yok — uydurulmaz
        "warnings": warnings,
    }


# ── PAKET 6.7 — Geçmiş (ekle-yalnız) ───────────────────────────────────────

def _read_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    out = []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= _MAX_HISTORY_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict) and rec.get("date"):
                        out.append(rec)
                except json.JSONDecodeError:
                    continue    # bozuk satır izole edilir, dosya DÜZENLENMEZ
    except OSError:
        return []
    return out


def _append_snapshot(snap: dict) -> None:
    """Ekle-yalnız: aynı güne ikinci kayıt yazılmaz, eski kayıt değişmez."""
    with _HISTORY_LOCK:
        existing = {r.get("date") for r in _read_history()}
        if snap["date"] in existing:
            return
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap, ensure_ascii=False) + "\n")


def history() -> dict:
    recs = _read_history()
    recs.sort(key=lambda r: r.get("date") or "")
    return {"ok": True, "read_only": True, "append_only": True,
            "as_of": _now_iso(), "count": len(recs),
            "snapshots": recs[-120:]}   # son ~4 ay


def _drawdown(window_days: int, current_balance: Decimal | None) -> str | None:
    """Pencere içi zirveden düşüş %. Yeterli doğrulanmış geçmiş yoksa null."""
    if current_balance is None:
        return None
    cutoff = (datetime.now(timezone.utc) -
              timedelta(days=window_days)).strftime("%Y-%m-%d")
    balances = [_dec(r.get("margin_balance_usdt")) for r in _read_history()
                if (r.get("date") or "") >= cutoff]
    balances = [b for b in balances if b is not None]
    if not balances:
        return None
    peak = max(balances + [current_balance])
    if peak == 0:
        return None
    return _pct(current_balance - peak, peak)


# ── PAKET 6.4 — Hesap Sağlığı Skoru (deterministik) ────────────────────────

def _classify(score: int) -> str:
    # Spec bantları: 90-100 / 75-89 / 60-74 / 40-59 / 0-39
    if score >= 90:
        return "Mükemmel"
    if score >= 75:
        return "İyi"
    if score >= 60:
        return "Orta"
    if score >= 40:
        return "Yüksek Risk"
    return "Kritik"


def health_score(exp: dict, conc: dict, acc: dict | None,
                 open_orders: int | None,
                 dd_day: str | None) -> dict:
    """Deterministik puanlama — 100'den ceza düşülür. Yapay zekâ YOK."""
    if not exp.get("ok") or not conc.get("ok") or acc is None:
        return {"score": None, "classification": None,
                "components": None,
                "note": "Doğrulanmış girdi eksik — skor üretilmez."}
    score = Decimal(100)
    comps: list[dict] = []

    def penalty(name: str, points: Decimal, reason: str):
        nonlocal score
        score -= points
        comps.append({"factor": name, "penalty": str(points),
                      "reason": reason})

    th = thresholds()
    # Spot-only: marj yerine Spot bakiye oranları kullanılır.
    total = _dec(acc.get("total_spot_value_usdt"))
    stable = None
    u_free = _dec(acc.get("usdt_free"))
    u_lock = _dec(acc.get("usdt_locked"))
    if u_free is not None or u_lock is not None:
        stable = (u_free or Decimal(0)) + (u_lock or Decimal(0))

    # 1) Stabil (USDT) tampon oranı — marj kullanımı yerine Spot karşılığı:
    #    riskli (stabil olmayan) varlıkların toplam Spot değerine oranı.
    if total and total > 0 and stable is not None:
        risky_pct = (total - stable) / total * 100
        if risky_pct >= th["RISK_CRITICAL_MARGIN"]:
            penalty("margin_usage", Decimal(30),
                    f"Riskli varlık oranı %{_q2(risky_pct)} — stabil "
                    f"(USDT) tampon kritik düzeyde düşük")
        elif risky_pct >= th["RISK_HIGH_MARGIN"]:
            penalty("margin_usage", Decimal(15),
                    f"Riskli varlık oranı %{_q2(risky_pct)} — stabil "
                    f"(USDT) tampon düşük")
    # 2) Kısmi fiyatlama — toplam değer doğrulanamıyorsa küçük ceza
    if acc.get("valuation") == "PARTIAL":
        penalty("exposure", Decimal(5),
                "Fiyatlama KISMİ — bazı varlıklar USDT ile değerlenemedi")
    # 3) Konsantrasyon
    sp = _dec(conc.get("single_position_pct"))
    if sp is not None:
        if sp >= th["POSITION_CRITICAL_PERCENT"]:
            penalty("concentration", Decimal(20), f"Tek pozisyon %{sp}")
        elif sp >= th["POSITION_HIGH_PERCENT"]:
            penalty("concentration", Decimal(15), f"Tek pozisyon %{sp}")
        elif sp >= th["MAX_POSITION_PERCENT"]:
            penalty("concentration", Decimal(7), f"Tek pozisyon %{sp}")
    # 4) Kullanılabilir (serbest USDT) bakiye oranı
    if total and total > 0 and u_free is not None:
        avail_pct = u_free / total * 100
        if avail_pct <= th["LOW_AVAILABLE_PERCENT"]:
            penalty("available_balance", Decimal(15),
                    f"Serbest USDT bakiyesi toplam Spot değerin "
                    f"%{_q2(avail_pct)}'i")
    # 5) Açık emir yoğunluğu
    if open_orders is not None and Decimal(open_orders) > \
            th["MAX_OPEN_ORDERS"]:
        penalty("open_orders", Decimal(5), f"{open_orders} açık emir")
    # 6) Günlük düşüş
    dd = _dec(dd_day)
    if dd is not None and dd <= th["DRAWDOWN_WARN_PERCENT"]:
        penalty("drawdown", Decimal(15), f"Günlük düşüş %{dd}")

    final = max(0, min(100, int(score)))
    return {"score": final, "classification": _classify(final),
            "components": comps,
            "note": "Deterministik kural tabanlı skor — tavsiye niteliğinde."}


# ── PAKET 6.5 — Uyarı motoru (tekrarsız) ───────────────────────────────────

def alerts() -> dict:
    """Risk uyarıları — Spot-only mimari.

    Futures kaldırıldı: marj/pozisyon tabanlı uyarılar (HIGH_EXPOSURE,
    HIGH_MARGIN_USAGE, LOW_AVAILABLE_BALANCE, SINGLE_ASSET_CONCENTRATION,
    NEGATIVE_UNREALIZED_PNL) artık üretilmez. Drawdown uyarısı
    bakiye-bağımsız geçmiş veriyle çalışmaya devam eder.
    """
    th = thresholds()
    out: dict[str, dict] = {}    # kod → uyarı (tekrar imkânsız)

    def add(code: str, severity: str, source: str, message: str):
        if code not in out:
            out[code] = {"code": code, "severity": severity,
                         "source": source, "explanation": message,
                         "message": message, "timestamp": _now_iso(),
                         "advisory_only": True}

    # Drawdown: hesap bakiyesinden bağımsız geçmiş anlık görüntülerle çalışır.
    dd_day = _drawdown(1, None)
    dd = _dec(dd_day)
    if dd is not None and dd <= th["DRAWDOWN_WARN_PERCENT"]:
        add("LARGE_DRAWDOWN", "HIGH", "drawdown", f"Günlük düşüş %{dd}.")

    return {"ok": True, "read_only": True, "advisory_only": True,
            "as_of": _now_iso(), "count": len(out),
            "alerts": sorted(out.values(), key=lambda a: a["code"])}


# ── PAKET 6.1 — Özet panosu ────────────────────────────────────────────────

def summary(persist: bool = True) -> dict:
    """Risk özeti. ``persist=False`` ile SALT-OKUNUR görünüm: günlük
    ekle-yalnız anlık görüntü YAZILMAZ (Mission 1700 portföy yolu bu
    modu kullanır; varsayılan davranış mevcut çağıranlar için aynıdır).

    Spot-only mimari: maruziyet, yoğunlaşma ve sağlık skoru Binance
    Global Spot bakiyeleri (USDT değerli) üzerinden hesaplanır; Spot
    hesabı doğrulanamıyorsa skor null döner (tahmin üretilmez).
    """
    exp = exposure()
    conc = concentration()
    open_orders = _open_orders_count()
    spot = _spot_account()

    # Spot-only: "bakiye" = toplam Spot varlık değeri (USDT)
    total_spot = _dec(spot.get("total_spot_value_usdt")) \
        if spot is not None else None
    u_free = _dec(spot.get("usdt_free")) if spot is not None else None
    usage_pct = None
    if total_spot and total_spot > 0 and u_free is not None:
        # Stabil olmayan (riskli) varlık oranı — marj kullanımı karşılığı
        u_lock = (_dec(spot.get("usdt_locked")) or Decimal(0)) \
            if spot is not None else Decimal(0)
        usage_pct = _pct(total_spot - (u_free + u_lock), total_spot)

    dd_day = _drawdown(1, total_spot)
    dd_week = _drawdown(7, total_spot)
    dd_month = _drawdown(30, total_spot)

    hs = health_score(exp, conc, spot, open_orders, dd_day)

    # Günlük ekle-yalnız anlık görüntü (varsa dokunulmaz);
    # persist=False → hiç yazılmaz (salt-okunur çağıranlar için).
    if persist and hs["score"] is not None:
        alert_model = alerts()
        _append_snapshot({
            "date": _today(), "recorded_at": _now_iso(),
            "alert_codes": [a["code"] for a in alert_model["alerts"]]
            if alert_model.get("ok") else [],
            "risk_score": hs["score"],
            "classification": hs["classification"],
            "gross_exposure_usdt": exp.get("gross_exposure_usdt"),
            "exposure_pct_of_margin": exp.get("exposure_pct_of_margin"),
            "margin_usage_pct": usage_pct,
            # Spot-only: geçmiş alan adı korunur; değer toplam Spot değeridir
            "margin_balance_usdt": _q2(total_spot),
            "total_spot_value_usdt": _q2(total_spot),
            "daily_drawdown_pct": dd_day,
        })

    return {
        "ok": True, "read_only": True, "advisory_only": True,
        "as_of": _now_iso(),
        "risk_score": hs["score"],
        "classification": hs["classification"],
        "score_components": hs["components"],
        "score_note": hs["note"],
        "portfolio_health": hs["classification"],
        "current_exposure_usdt": exp.get("gross_exposure_usdt")
        if exp.get("ok") else None,
        "total_spot_value_usdt": _q2(total_spot),
        "spot_valuation": exp.get("spot_valuation")
        if exp.get("ok") else None,
        "unpriced_assets": exp.get("unpriced_assets")
        if exp.get("ok") else None,
        "exposure_pct_of_margin": exp.get("exposure_pct_of_margin")
        if exp.get("ok") else None,
        "available_margin_usdt": _q2(u_free),
        "available_usdt": _q2(u_free),
        "margin_usage_pct": usage_pct,
        "open_position_count": None,
        "open_order_count": open_orders,
        "largest_position": conc.get("largest_position")
        if conc.get("ok") else None,
        "top5_positions": conc.get("top5") if conc.get("ok") else None,
        "concentration_warnings": conc.get("warnings")
        if conc.get("ok") else None,
        "largest_unrealized_loss": None,
        "largest_unrealized_gain": None,
        "daily_drawdown_pct": dd_day,
        "weekly_drawdown_pct": dd_week,
        "monthly_drawdown_pct": dd_month,
        "drawdown_note": ("Düşüş değerleri yalnızca yerel doğrulanmış "
                          "anlık görüntülerden hesaplanır; yeterli geçmiş "
                          "yoksa gösterilmez (tahmin üretilmez)."),
    }


# ── PAKET 6.6 — İşlem öncesi risk simülatörü (borsa iletişimi YOK) ─────────

def simulate(params: dict) -> dict:
    """Tamamen yerel hesap — borsaya istek atılmaz, emir önizlemesi yok."""
    exchange = (params.get("exchange") or "SPOT").strip().upper()
    symbol = (params.get("symbol") or "").strip().upper()
    direction = (params.get("direction") or "").strip().upper()
    if not _SYMBOL_RE.match(symbol):
        raise ValueError("symbol: 2-20 büyük harf/rakam olmalı")
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction: LONG veya SHORT olmalı")
    price = _dec(params.get("entry_price"))
    qty = _dec(params.get("quantity"))
    lev = _dec(params.get("leverage"))
    if price is None or price <= 0:
        raise ValueError("entry_price: pozitif sayı olmalı")
    if qty is None or qty <= 0:
        raise ValueError("quantity: pozitif sayı olmalı")
    if lev is None or lev < 1 or lev > 125 or lev != lev.to_integral_value():
        raise ValueError("leverage: 1-125 arası tam sayı olmalı")

    value = price * qty
    est_margin = value / lev

    # Futures kaldırıldı; portföy bağlamı önbellekten okunamıyor.
    after_exposure_pct = after_concentration_pct = None
    largest_position_after_pct = None

    # Yaklaşık tasfiye tamponu: 1/kaldıraç (bakım marjı hariç, açıkça etiketli)
    liq_buffer_pct = _q2(Decimal(100) / lev)

    return {
        "ok": True, "read_only": True, "simulation_only": True,
        "no_exchange_communication": True,
        "as_of": _now_iso(),
        "input": {"exchange": exchange, "symbol": symbol,
                  "direction": direction,
                  "entry_price": str(price), "quantity": str(qty),
                  "leverage": str(int(lev))},
        "position_value_usdt": _q2(value),
        "estimated_margin_usdt": _q2(est_margin),
        "portfolio_exposure_after_pct": after_exposure_pct,
        "concentration_after_pct": after_concentration_pct,
        "largest_position_after_pct": largest_position_after_pct,
        "estimated_liquidation_buffer_pct": liq_buffer_pct,
        "liquidation_note": "Yaklaşık değer (1/kaldıraç); bakım marjı ve "
                            "fonlama hariç — emir önizlemesi DEĞİLDİR.",
    }
