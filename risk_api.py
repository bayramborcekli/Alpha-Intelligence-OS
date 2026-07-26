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

# Konsantrasyon eşikleri (tavsiye uyarıları için)
SINGLE_POSITION_WARN_PCT = Decimal("25")
SINGLE_POSITION_HIGH_PCT = Decimal("40")
EXPOSURE_WARN_PCT = Decimal("150")          # brüt maruziyet / marj bakiyesi
MARGIN_USAGE_WARN_PCT = Decimal("60")
MARGIN_USAGE_HIGH_PCT = Decimal("80")
LOW_AVAILABLE_PCT = Decimal("20")
DRAWDOWN_WARN_PCT = Decimal("-10")

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

def _account() -> dict | None:
    ga = dapi.global_account()
    if not ga.get("ok"):
        return None
    return ga.get("account") or {}


def _active_positions() -> list[dict] | None:
    gp = pf.positions_view()
    if not gp.get("ok"):
        return None
    return [p for p in (gp.get("positions") or [])
            if p.get("direction") != "FLAT"]


def _open_orders_count() -> int | None:
    go = pf.orders_view()
    if not go.get("ok"):
        return None
    return (go.get("summary") or {}).get("open_count")


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

def exposure() -> dict:
    acc = _account()
    positions = _active_positions()
    ta = dapi.tr_account()

    if positions is None:
        return {"ok": False, "error": {"code": "SOURCE_UNAVAILABLE",
                "message": "Pozisyon verisi doğrulanamadı — maruziyet "
                           "hesaplanmıyor (tahmin üretilmez)."},
                "read_only": True}

    margin_balance = _dec(acc.get("usdt_margin_balance")) if acc else None
    available = _dec(acc.get("usdt_available_balance")) if acc else None

    long_notional = Decimal(0)
    short_notional = Decimal(0)
    by_asset: dict[str, Decimal] = {}
    for p in positions:
        n = _notional(p)
        if p.get("direction") == "LONG":
            long_notional += n
        elif p.get("direction") == "SHORT":
            short_notional += n
        base = _base_asset(p.get("symbol") or "")
        by_asset[base] = by_asset.get(base, Decimal(0)) + n

    gross = long_notional + short_notional
    net = long_notional - short_notional

    assets = [{"asset": a,
               "exposure_value_usdt": _q2(v),
               "exposure_pct": _pct(v, gross) if gross else None}
              for a, v in sorted(by_asset.items(),
                                 key=lambda kv: kv[1], reverse=True)]

    # Binance TR: yalnızca adet; USD karşılığı ÜRETİLMEZ (kur uydurulmaz)
    tr_stables, tr_assets = [], []
    if ta.get("ok"):
        tacc = ta.get("account") or {}
        u_free = _dec(tacc.get("usdt_free")) or Decimal(0)
        u_lock = _dec(tacc.get("usdt_locked")) or Decimal(0)
        t_free = _dec(tacc.get("try_free")) or Decimal(0)
        t_lock = _dec(tacc.get("try_locked")) or Decimal(0)
        if u_free or u_lock:
            tr_stables.append({"asset": "USDT",
                               "quantity": str(u_free + u_lock)})
        if t_free or t_lock:
            tr_assets.append({"asset": "TRY",
                              "quantity": str(t_free + t_lock)})

    return {
        "ok": True, "read_only": True, "as_of": _now_iso(),
        "universe": "BINANCE_GLOBAL_FUTURES_USDT",
        "gross_exposure_usdt": _q2(gross),
        "net_exposure_usdt": _q2(net),
        "long_exposure_usdt": _q2(long_notional),
        "short_exposure_usdt": _q2(short_notional),
        "exposure_pct_of_margin": _pct(gross, margin_balance),
        "cash_available_usdt": _q2(available),
        "by_asset": assets,
        "by_direction": {
            "long_pct": _pct(long_notional, gross) if gross else None,
            "short_pct": _pct(short_notional, gross) if gross else None,
        },
        "by_exchange": [{"exchange": "BINANCE_GLOBAL_FUTURES",
                         "exposure_value_usdt": _q2(gross),
                         "exposure_pct": "100.00" if gross else None}],
        "by_market": [{"market": "USDT-M FUTURES",
                       "exposure_value_usdt": _q2(gross),
                       "exposure_pct": "100.00" if gross else None}],
        "binance_tr_holdings": {
            "note": "Yalnızca adet — USD karşılığı doğrulanamadığı için "
                    "hesaplanmaz (kur tahmini yapılmaz).",
            "stablecoins": tr_stables[:50],
            "other_assets": tr_assets[:50],
        },
    }


# ── PAKET 6.3 — Konsantrasyon ──────────────────────────────────────────────

def concentration() -> dict:
    positions = _active_positions()
    if positions is None:
        return {"ok": False, "error": {"code": "SOURCE_UNAVAILABLE",
                "message": "Pozisyon verisi doğrulanamadı."},
                "read_only": True}
    rows = sorted(positions, key=_notional, reverse=True)
    gross = sum((_notional(p) for p in rows), Decimal(0))
    top = [{"symbol": p.get("symbol"), "direction": p.get("direction"),
            "notional_usdt": _q2(_notional(p)),
            "share_pct": _pct(_notional(p), gross)}
           for p in rows[:5]]
    largest_pct = _dec(top[0]["share_pct"]) if top and top[0]["share_pct"] \
        else None
    warnings = []
    if largest_pct is not None and largest_pct >= SINGLE_POSITION_HIGH_PCT:
        warnings.append(f"Tek pozisyon payı %{largest_pct} — yüksek "
                        f"konsantrasyon (eşik %{SINGLE_POSITION_HIGH_PCT}).")
    elif largest_pct is not None and largest_pct >= SINGLE_POSITION_WARN_PCT:
        warnings.append(f"Tek pozisyon payı %{largest_pct} — eşik "
                        f"%{SINGLE_POSITION_WARN_PCT} aşıldı.")
    return {
        "ok": True, "read_only": True, "as_of": _now_iso(),
        "largest_position": top[0] if top else None,
        "top5": top,
        "single_position_pct": str(largest_pct) if largest_pct is not None
        else None,
        "exchange_pct": [{"exchange": "BINANCE_GLOBAL_FUTURES",
                          "pct": "100.00" if gross else None}],
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
    if score >= 85:
        return "Mükemmel"
    if score >= 70:
        return "İyi"
    if score >= 55:
        return "Orta"
    if score >= 35:
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

    margin = _dec(acc.get("usdt_margin_balance"))
    avail = _dec(acc.get("usdt_available_balance"))
    gross = _dec(exp.get("gross_exposure_usdt")) or Decimal(0)

    # 1) Marj kullanımı
    if margin and margin > 0 and avail is not None:
        usage = (margin - avail) / margin * 100
        if usage >= MARGIN_USAGE_HIGH_PCT:
            penalty("margin_usage", Decimal(30), f"Marj kullanımı %{_q2(usage)}")
        elif usage >= MARGIN_USAGE_WARN_PCT:
            penalty("margin_usage", Decimal(15), f"Marj kullanımı %{_q2(usage)}")
    # 2) Brüt maruziyet / marj
    if margin and margin > 0:
        exp_pct = gross / margin * 100
        if exp_pct >= EXPOSURE_WARN_PCT * 2:
            penalty("exposure", Decimal(25), f"Maruziyet %{_q2(exp_pct)}")
        elif exp_pct >= EXPOSURE_WARN_PCT:
            penalty("exposure", Decimal(12), f"Maruziyet %{_q2(exp_pct)}")
    # 3) Konsantrasyon
    sp = _dec(conc.get("single_position_pct"))
    if sp is not None:
        if sp >= SINGLE_POSITION_HIGH_PCT:
            penalty("concentration", Decimal(15), f"Tek pozisyon %{sp}")
        elif sp >= SINGLE_POSITION_WARN_PCT:
            penalty("concentration", Decimal(7), f"Tek pozisyon %{sp}")
    # 4) Kullanılabilir bakiye oranı
    if margin and margin > 0 and avail is not None:
        avail_pct = avail / margin * 100
        if avail_pct <= LOW_AVAILABLE_PCT:
            penalty("available_balance", Decimal(15),
                    f"Kullanılabilir bakiye %{_q2(avail_pct)}")
    # 5) Açık emir yoğunluğu
    if open_orders is not None and open_orders > 20:
        penalty("open_orders", Decimal(5), f"{open_orders} açık emir")
    # 6) Günlük düşüş
    dd = _dec(dd_day)
    if dd is not None and dd <= DRAWDOWN_WARN_PCT:
        penalty("drawdown", Decimal(15), f"Günlük düşüş %{dd}")

    final = max(0, min(100, int(score)))
    return {"score": final, "classification": _classify(final),
            "components": comps,
            "note": "Deterministik kural tabanlı skor — tavsiye niteliğinde."}


# ── PAKET 6.5 — Uyarı motoru (tekrarsız) ───────────────────────────────────

def alerts() -> dict:
    exp = exposure()
    conc = concentration()
    acc = _account()
    out: dict[str, dict] = {}    # kod → uyarı (tekrar imkânsız)

    def add(code: str, severity: str, message: str):
        if code not in out:
            out[code] = {"code": code, "severity": severity,
                         "message": message, "advisory_only": True}

    if exp.get("ok") and acc:
        margin = _dec(acc.get("usdt_margin_balance"))
        avail = _dec(acc.get("usdt_available_balance"))
        gross = _dec(exp.get("gross_exposure_usdt")) or Decimal(0)
        if margin and margin > 0:
            exp_pct = gross / margin * 100
            if exp_pct >= EXPOSURE_WARN_PCT:
                add("HIGH_EXPOSURE", "WARNING",
                    f"Brüt maruziyet marj bakiyesinin %{_q2(exp_pct)}'i.")
            if avail is not None:
                usage = (margin - avail) / margin * 100
                if usage >= MARGIN_USAGE_HIGH_PCT:
                    add("HIGH_MARGIN_USAGE", "HIGH",
                        f"Marj kullanımı %{_q2(usage)}.")
                elif usage >= MARGIN_USAGE_WARN_PCT:
                    add("HIGH_MARGIN_USAGE", "WARNING",
                        f"Marj kullanımı %{_q2(usage)}.")
                if avail / margin * 100 <= LOW_AVAILABLE_PCT:
                    add("LOW_AVAILABLE_BALANCE", "WARNING",
                        "Kullanılabilir bakiye marjın %20'sinin altında.")
    if conc.get("ok"):
        sp = _dec(conc.get("single_position_pct"))
        if sp is not None and sp >= SINGLE_POSITION_WARN_PCT:
            add("SINGLE_ASSET_CONCENTRATION",
                "HIGH" if sp >= SINGLE_POSITION_HIGH_PCT else "WARNING",
                f"En büyük pozisyonun payı %{sp}.")
    margin_now = _dec((acc or {}).get("usdt_margin_balance")) if acc else None
    dd_day = _drawdown(1, margin_now)
    dd = _dec(dd_day)
    if dd is not None and dd <= DRAWDOWN_WARN_PCT:
        add("LARGE_DRAWDOWN", "HIGH", f"Günlük düşüş %{dd}.")
    upnl = None
    gp = pf.positions_view()
    if gp.get("ok"):
        upnl = _dec((gp.get("summary") or {}).get("total_unrealized_pnl"))
    if upnl is not None and upnl < 0:
        add("NEGATIVE_UNREALIZED_PNL", "INFO",
            f"Toplam gerçekleşmemiş PnL negatif ({_q2(upnl)} USDT).")

    return {"ok": True, "read_only": True, "advisory_only": True,
            "as_of": _now_iso(), "count": len(out),
            "alerts": sorted(out.values(), key=lambda a: a["code"])}


# ── PAKET 6.1 — Özet panosu ────────────────────────────────────────────────

def summary() -> dict:
    acc = _account()
    exp = exposure()
    conc = concentration()
    open_orders = _open_orders_count()
    positions = _active_positions()

    margin = _dec(acc.get("usdt_margin_balance")) if acc else None
    avail = _dec(acc.get("usdt_available_balance")) if acc else None
    usage_pct = None
    if margin and margin > 0 and avail is not None:
        usage_pct = _pct(margin - avail, margin)

    largest_loss = largest_gain = None
    if positions:
        pnls = [(_dec(p.get("unrealized_pnl")) or Decimal(0), p)
                for p in positions]
        lo = min(pnls, key=lambda t: t[0])
        hi = max(pnls, key=lambda t: t[0])
        if lo[0] < 0:
            largest_loss = {"symbol": lo[1].get("symbol"),
                            "unrealized_pnl_usdt": _q2(lo[0])}
        if hi[0] > 0:
            largest_gain = {"symbol": hi[1].get("symbol"),
                            "unrealized_pnl_usdt": _q2(hi[0])}

    dd_day = _drawdown(1, margin)
    dd_week = _drawdown(7, margin)
    dd_month = _drawdown(30, margin)

    hs = health_score(exp, conc, acc, open_orders, dd_day)

    # Günlük ekle-yalnız anlık görüntü (varsa dokunulmaz)
    if hs["score"] is not None and margin is not None:
        _append_snapshot({
            "date": _today(), "recorded_at": _now_iso(),
            "risk_score": hs["score"],
            "classification": hs["classification"],
            "gross_exposure_usdt": exp.get("gross_exposure_usdt"),
            "exposure_pct_of_margin": exp.get("exposure_pct_of_margin"),
            "margin_usage_pct": usage_pct,
            "margin_balance_usdt": _q2(margin),
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
        "exposure_pct_of_margin": exp.get("exposure_pct_of_margin")
        if exp.get("ok") else None,
        "available_margin_usdt": _q2(avail),
        "margin_usage_pct": usage_pct,
        "open_position_count": len(positions) if positions is not None
        else None,
        "open_order_count": open_orders,
        "largest_position": conc.get("largest_position")
        if conc.get("ok") else None,
        "top5_positions": conc.get("top5") if conc.get("ok") else None,
        "concentration_warnings": conc.get("warnings")
        if conc.get("ok") else None,
        "largest_unrealized_loss": largest_loss,
        "largest_unrealized_gain": largest_gain,
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

    # KESİNLİKLE YEREL: yalnızca zaten bellekte olan önbellek okunur;
    # önbellek boşsa borsa ÇAĞRILMAZ, ilgili alanlar null döner.
    after_exposure_pct = after_concentration_pct = None
    acc = _cached_model("global_account")
    pos = _cached_model("global_positions")
    if acc is not None and pos is not None:
        margin_bal = _dec(acc.get("usdt_margin_balance"))
        gross = sum((_notional(p) for p in pos.get("positions_all") or []
                     if p.get("direction") != "FLAT"), Decimal(0))
        if margin_bal and margin_bal > 0:
            after_exposure_pct = _pct(gross + value, margin_bal)
        if gross + value > 0:
            after_concentration_pct = _pct(value, gross + value)

    # Yaklaşık tasfiye tamponu: 1/kaldıraç (bakım marjı hariç, açıkça etiketli)
    liq_buffer_pct = _q2(Decimal(100) / lev)

    return {
        "ok": True, "read_only": True, "simulation_only": True,
        "no_exchange_communication": True,
        "as_of": _now_iso(),
        "input": {"symbol": symbol, "direction": direction,
                  "entry_price": str(price), "quantity": str(qty),
                  "leverage": str(int(lev))},
        "position_value_usdt": _q2(value),
        "estimated_margin_usdt": _q2(est_margin),
        "portfolio_exposure_after_pct": after_exposure_pct,
        "concentration_after_pct": after_concentration_pct,
        "estimated_liquidation_buffer_pct": liq_buffer_pct,
        "liquidation_note": "Yaklaşık değer (1/kaldıraç); bakım marjı ve "
                            "fonlama hariç — emir önizlemesi DEĞİLDİR.",
    }
