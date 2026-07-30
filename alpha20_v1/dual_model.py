"""İKİ DİNAMİK LİSTE + İKİ AYRI KISA VADELİ İŞLEM MODELİ (PAPER).

- CORE LIQUIDITY LIST → ALPHA CORE SCALP
- OPPORTUNITY LIST    → ALPHA OPPORTUNITY BURST

Sözleşmeler:
- LIVE ORDERS DISABLED — yalnız Paper. Gerçek emir gönderimi YOK.
- Tüm runtime durumu git dışı kanonik store'da
  (alpha20_v1/dual_model_runtime.json, flock'lu transaksiyonel yazım)
  → git çalışma ağacı temiz kalır, restart sonrası listeler ve açık
  Paper pozisyonlar korunur.
- Bir sembolde aynı anda TEK pozisyon; iki listede olan sembolde
  sahipliği en yüksek net edge üreten model alır
  (DUPLICATE_MODEL_OWNERSHIP diğerine yazılır).
- Her girişte fee + slippage sonrası pozitif expected_net_edge
  zorunlu; işlem açılmayan her değerlendirme kesin reason_code üretir.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
except ImportError:  # Windows
    import portable_flock as fcntl  # type: ignore

log = logging.getLogger("dual_model")

ROOT = Path(__file__).resolve().parent
RUNTIME_PATH = ROOT / "dual_model_runtime.json"
LOCK_FILE = ROOT / ".dual_model.lock"
SPOT_BASE = "https://api.binance.com"

MODEL_CORE = "ALPHA_CORE_SCALP"
MODEL_OPP = "ALPHA_OPPORTUNITY_BURST"
MODELS = (MODEL_CORE, MODEL_OPP)
PINNED = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

REASON_CODES = (
    "NO_SIGNAL", "LOW_CONFIDENCE", "SPREAD_TOO_HIGH",
    "LOW_BOOK_DEPTH", "LOW_LIQUIDITY", "SLIPPAGE_TOO_HIGH",
    "FEE_DRAG", "EXPECTED_EDGE_TOO_LOW", "MOMENTUM_EXHAUSTED",
    "FALSE_BREAKOUT_RISK", "RISK_LIMIT", "POSITION_LIMIT",
    "COOLDOWN", "DUPLICATE_POSITION", "DUPLICATE_MODEL_OWNERSHIP",
    "DATA_QUALITY",
    # Maliyet-sonrası TP/SL kapıları (net yapı sözleşmesi)
    "NET_TP_NON_POSITIVE", "NET_REWARD_RISK_TOO_LOW",
    "EDGE_BELOW_COST_MULTIPLE")

FEE_RATE = 0.001  # tek yön; gidiş-dönüş 2x

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "core": {
        "list_size": 10,            # 8-15 önerisi
        "min_volume_usdt": 50_000_000,
        "max_spread_pct": 0.05,
        "min_trade_count": 200_000,
        "tp_pct": 0.45, "sl_pct": 0.30,
        "max_hold_minutes": 15,
        "trailing_pct": 0.20,
        "min_confidence": 60,
        "max_slippage_pct": 0.03,
        # Maliyet-sonrası giriş kapıları (net TP/SL sözleşmesi):
        # net_tp = tp - roundtrip, net_sl = sl + roundtrip;
        # net_tp<=0 veya net_rr<1.20 veya beklenen net edge
        # < roundtrip×1.5 ise giriş REDDEDİLİR.
        "min_net_reward_risk": 1.20,
        "min_edge_cost_multiple": 1.5,
        "max_open_positions": 2,
        "position_usdt": 100.0,
        "refresh_seconds": 300,     # liste yenileme 5 dk
        "signal_seconds": 12,       # 10-15 sn
    },
    "opportunity": {
        "list_size": 20,            # 15-30 önerisi
        "min_volume_usdt": 5_000_000,
        "max_spread_pct": 0.15,
        "min_trade_count": 20_000,
        "min_volume_burst": 2.0,    # son hacim / ortalama oranı
        "min_volatility_pct": 1.5,
        "tp_pct": 0.80, "sl_pct": 0.50,
        "max_hold_minutes": 20,
        "trailing_pct": 0.35,
        "min_confidence": 55,
        "max_slippage_pct": 0.08,
        # Maliyet-sonrası giriş kapıları — CORE ile aynı sözleşme.
        "min_net_reward_risk": 1.20,
        "min_edge_cost_multiple": 1.5,
        "max_open_positions": 2,
        "position_usdt": 50.0,      # CORE'dan küçük
        "refresh_seconds": 180,     # 2-5 dk havuz yenileme
        "signal_seconds": 25,       # 20-30 sn
        "cooldown_after_losses": 2,
        "cooldown_minutes": 15,
    },
    "total_max_open_positions": 4,
    "monitor_seconds": 4,           # 3-5 sn pozisyon monitörü
}


def get_config(main_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """config.json 'dual_model' bölümü ile varsayılanları birleştir."""
    cfg = json.loads(json.dumps(DEFAULTS))
    user = (main_cfg or {}).get("dual_model")
    if isinstance(user, dict):
        for key, val in user.items():
            if isinstance(val, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(val)
            else:
                cfg[key] = val
    # Öğrenilmiş champion overlay'i (dual_learning): yalnız izin
    # listesindeki, sınır içinde clamplanmış alanlar + config_version.
    # Öğrenme durumu okunamazsa BASE ile devam edilir (fail-safe).
    try:
        import dual_learning as _dl
        for section, model in (("core", MODEL_CORE),
                               ("opportunity", MODEL_OPP)):
            champ = _dl.champion_overrides(model)
            cfg[section].update(champ.get("overrides") or {})
            cfg[section]["config_version"] = champ.get(
                "config_version", "BASE")
    except Exception:
        cfg["core"].setdefault("config_version", "BASE")
        cfg["opportunity"].setdefault("config_version", "BASE")
    return cfg


# ── Git dışı kanonik runtime store (flock, transaksiyonel) ─────────

def _load_runtime() -> dict[str, Any]:
    try:
        if RUNTIME_PATH.exists():
            with RUNTIME_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _update_runtime(mutator: Callable[[dict], None]) -> dict[str, Any]:
    lock_path = RUNTIME_PATH.with_suffix(".lock")
    with lock_path.open("a+") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            data = _load_runtime()
            mutator(data)
            tmp = RUNTIME_PATH.with_name(
                f".{RUNTIME_PATH.name}.{os.getpid()}."
                f"{threading.get_ident()}.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            tmp.replace(RUNTIME_PATH)
            return data
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Piyasa verisi (yalnız public spot uçları) ──────────────────────
# Paylaşımlı 429/418 koruması: alpha20'nin dosya-tabanlı geri çekilme
# durumuna uyulur ve buradaki 429/418'ler oraya KAYDEDİLİR — dual-model
# sistem geneli anti-ban korumasını atlayamaz.


class RateLimited(Exception):
    """Paylaşımlı geri çekilme aktif — istek atılmadı."""


def _diagnose_net(exc: Exception) -> str:
    """alpha20'nin çalışan teşhis katmanını yeniden kullan (Windows
    AV/proxy TLS müdahalesi mesajları dahil)."""
    import requests
    try:
        import alpha20 as _a20
        if isinstance(exc, requests.exceptions.SSLError):
            return _a20.diagnose_ssl_error(exc)
        return _a20.diagnose_network_error(exc)
    except Exception:
        return str(exc)


def _guarded_get(path: str, params: dict | None = None,
                 timeout: int = 10, retries: int = 2) -> Any:
    """Legacy fetch_klines ile AYNI güvenli HTTP katmanı: geçici
    SSL/ağ hatalarında artan beklemeyle kısa retry (Windows'ta
    antivirüs/proxy TLS müdahalesi aralıklıdır — tek hata çevrimi
    düşürmesin). Doğrulama ASLA kapatılmaz (verify hep açık)."""
    import requests
    try:
        import alpha20 as _a20
        remaining = _a20.rate_limit_remaining()
    except Exception:
        _a20, remaining = None, 0.0
    if remaining > 0:
        raise RateLimited(f"{remaining:.0f}s geri çekilme")
    r = None
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(f"{SPOT_BASE}{path}", params=params,
                             timeout=timeout)
            break
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            last_exc = exc
            log.warning("dual_model AĞ/SSL hatası | %s | deneme %d/%d "
                        "| %s", path, attempt + 1, retries + 1,
                        _diagnose_net(exc))
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    if r is None:
        raise RuntimeError(_diagnose_net(last_exc)) from last_exc
    if r.status_code in (429, 418):
        if _a20 is not None:
            try:
                _a20.register_rate_limit(r.status_code, r)
            except Exception as reg_exc:
                log.warning(
                    "dual_model 429 geri çekilme KAYDI BAŞARISIZ — diğer "
                    "worker'lar geri çekilmeden habersiz kalabilir (ban "
                    "riski) | HTTP %s | %s | %s",
                    r.status_code, path, reg_exc)
        raise RateLimited(f"HTTP {r.status_code}")
    r.raise_for_status()
    return r.json()


def fetch_spot_tickers() -> list[dict[str, Any]]:
    data = _guarded_get("/api/v3/ticker/24hr", timeout=15)
    return data if isinstance(data, list) else []


def fetch_spot_klines(symbol: str, interval: str = "1m",
                      limit: int = 60) -> list[list]:
    data = _guarded_get("/api/v3/klines",
                        {"symbol": symbol, "interval": interval,
                         "limit": limit})
    return data if isinstance(data, list) else []


def fetch_spot_prices(symbols: list[str]) -> dict[str, float]:
    """Açık pozisyon sembolleri için TAZE fiyat (tek toplu istek)."""
    if not symbols:
        return {}
    data = _guarded_get(
        "/api/v3/ticker/price",
        {"symbols": json.dumps(symbols, separators=(",", ":"))})
    out: dict[str, float] = {}
    for row in data if isinstance(data, list) else []:
        try:
            out[row["symbol"]] = float(row["price"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _ticker_fields(t: dict) -> dict[str, float] | None:
    try:
        bid, ask = float(t.get("bidPrice") or 0), float(
            t.get("askPrice") or 0)
        last = float(t.get("lastPrice") or 0)
        high, low = float(t.get("highPrice") or 0), float(
            t.get("lowPrice") or 0)
        if last <= 0:
            return None
        spread_pct = ((ask - bid) / last * 100) if bid > 0 and \
            ask > bid else 999.0
        return {
            "volume_usdt": float(t.get("quoteVolume") or 0),
            "trade_count": float(t.get("count") or 0),
            "spread_pct": spread_pct,
            "volatility_pct": ((high - low) / low * 100)
            if low > 0 else 0.0,
            "change_pct": float(t.get("priceChangePercent") or 0),
            "last": last,
        }
    except (TypeError, ValueError):
        return None


def _eligible_usdt(t: dict) -> bool:
    s = t.get("symbol", "")
    return (s.endswith("USDT") and not any(
        x in s for x in ("UP", "DOWN", "BULL", "BEAR"))
        and "_" not in s)


def build_core_list(tickers: list[dict], cfg: dict) -> list[dict]:
    """Yüksek hacim + dar spread + sık işlem → CORE listesi.

    BTC/ETH/SOL sabit; kalan slotlar skora göre dolar."""
    c = cfg["core"]
    rows = []
    for t in tickers:
        if not _eligible_usdt(t):
            continue
        f = _ticker_fields(t)
        if not f:
            continue
        f["symbol"] = t["symbol"]
        pinned = t["symbol"] in PINNED
        if not pinned and (
                f["volume_usdt"] < c["min_volume_usdt"]
                or f["spread_pct"] > c["max_spread_pct"]
                or f["trade_count"] < c["min_trade_count"]):
            continue
        # skor: hacim + işlem sıklığı, dar spread ödülü
        f["score"] = (f["volume_usdt"] / 1e6 +
                      f["trade_count"] / 1e4 -
                      f["spread_pct"] * 100)
        f["pinned"] = pinned
        rows.append(f)
    pinned_rows = [r for r in rows if r["pinned"]]
    others = sorted((r for r in rows if not r["pinned"]),
                    key=lambda r: -r["score"])
    size = max(len(PINNED), int(c["list_size"]))
    return (pinned_rows + others)[:size]


def build_opportunity_list(tickers: list[dict], cfg: dict,
                           core_symbols: set[str]) -> list[dict]:
    """Hacim patlaması / volatilite genişlemesi → OPPORTUNITY listesi.

    CORE'daki semboller hariç (listeler ayrık)."""
    o = cfg["opportunity"]
    rows = []
    for t in tickers:
        if not _eligible_usdt(t) or t["symbol"] in core_symbols:
            continue
        f = _ticker_fields(t)
        if not f:
            continue
        if (f["volume_usdt"] < o["min_volume_usdt"]
                or f["trade_count"] < o["min_trade_count"]
                or f["spread_pct"] > o["max_spread_pct"]
                or f["volatility_pct"] < o["min_volatility_pct"]):
            continue
        # hacim patlaması proxy'si: işlem yoğunluğu / hacim tabanı +
        # fiyat hareketi; kesin oran için kline ortalaması sinyal
        # aşamasında doğrulanır
        f["symbol"] = t["symbol"]
        f["burst_score"] = (abs(f["change_pct"]) *
                            f["volatility_pct"])
        if f["change_pct"] > 0:
            f["opportunity_type"] = "VOLUME_BREAKOUT"
        else:
            f["opportunity_type"] = "REVERSAL_WATCH"
        rows.append(f)
    rows.sort(key=lambda r: -r["burst_score"])
    return rows[:int(o["list_size"])]


# ── Sinyal + execution quality ─────────────────────────────────────

def _ema(vals: list[float], n: int) -> float:
    if not vals:
        return 0.0
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(closes: list[float], n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def evaluate_signal(symbol: str, klines: list[list],
                    model: str) -> dict[str, Any]:
    """1m klines → sinyal (side/confidence/expected_gross_edge_pct).

    Kural tabanlı, deterministik; her iki model aynı çekirdek
    göstergeleri farklı ağırlıkla kullanır."""
    if len(klines) < 30:
        return {"symbol": symbol, "side": None, "confidence": 0,
                "reason_code": "DATA_QUALITY"}
    closes = [float(k[4]) for k in klines]
    vols = [float(k[5]) for k in klines]
    last = closes[-1]
    ema9, ema21 = _ema(closes[-30:], 9), _ema(closes[-30:], 21)
    rsi = _rsi(closes)
    # VWAP (pencere)
    pv = sum(c * v for c, v in zip(closes[-30:], vols[-30:]))
    vsum = sum(vols[-30:]) or 1.0
    vwap = pv / vsum
    mom_pct = (last - closes[-6]) / closes[-6] * 100 \
        if closes[-6] else 0.0
    vol_recent = sum(vols[-5:]) / 5
    vol_base = (sum(vols[-30:-5]) / 25) or 1e-9
    vol_ratio = vol_recent / vol_base
    hi20 = max(closes[-21:-1])

    conf = 0
    side = None
    if ema9 > ema21 and last > vwap:
        side = "LONG"
        conf += 30
        if mom_pct > 0.05:
            conf += 15
        if last > hi20:                     # kısa vadeli breakout
            conf += 15
        if vol_ratio >= 1.5:                # hacim doğrulaması
            conf += 20
        if 35 <= rsi <= 65:                 # RSI toparlanma bölgesi
            conf += 10
        if last <= vwap * 1.004:            # VWAP'a yakın giriş
            conf += 10
    if side is None:
        return {"symbol": symbol, "side": None, "confidence": 0,
                "reason_code": "NO_SIGNAL", "rsi": rsi,
                "vol_ratio": vol_ratio}
    if model == MODEL_OPP and vol_ratio < 1.2:
        return {"symbol": symbol, "side": None, "confidence": conf,
                "reason_code": "MOMENTUM_EXHAUSTED"}
    if last > hi20 and vol_ratio < 1.2:
        return {"symbol": symbol, "side": None, "confidence": conf,
                "reason_code": "FALSE_BREAKOUT_RISK"}
    edge = min(abs(mom_pct) * 0.6 + (vol_ratio - 1) * 0.15, 2.0)
    return {"symbol": symbol, "side": side, "confidence": min(conf, 100),
            "expected_gross_edge_pct": round(edge, 4),
            "rsi": round(rsi, 1), "vol_ratio": round(vol_ratio, 2),
            "vwap": vwap, "last": last}


def cost_profile(m: dict, slippage_pct: float | None = None) -> dict:
    """Maliyet-sonrası TP/SL profili — TEK hesap noktası.

    slippage_pct verilmezse max_slippage_pct ÜST SINIR tahmini
    kullanılır (panelde dürüst kötü-senaryo gösterimi); giriş
    kapısında işlemin gerçek spread tahmini geçirilir.
    round_trip_cost = gidiş-dönüş komisyon + slippage tahmini.
    break_even_win_rate = net_sl / (net_tp + net_sl)."""
    tp = float(m.get("tp_pct") or 0.0)
    sl = float(m.get("sl_pct") or 0.0)
    slip = float(m.get("max_slippage_pct") or 0.0) \
        if slippage_pct is None else float(slippage_pct)
    cost = FEE_RATE * 2 * 100 + slip
    net_tp = tp - cost
    net_sl = sl + cost
    rr = (net_tp / net_sl) if net_sl > 0 else None
    be = (net_sl / (net_tp + net_sl) * 100) \
        if net_tp > 0 and net_sl > 0 else None
    return {
        "gross_tp_pct": round(tp, 4),
        "gross_sl_pct": round(sl, 4),
        "round_trip_cost_pct": round(cost, 4),
        "net_tp_pct": round(net_tp, 4),
        "net_sl_pct": round(net_sl, 4),
        "net_reward_risk": round(rr, 4) if rr is not None else None,
        "break_even_win_rate_pct":
            round(be, 2) if be is not None else None,
    }


def execution_quality_gate(row: dict, sig: dict, model: str,
                           cfg: dict) -> tuple[bool, str | None, float]:
    """Zorunlu kalite kapıları → (geçti, reason_code, net_edge_pct)."""
    m = cfg["core"] if model == MODEL_CORE else cfg["opportunity"]
    if sig.get("confidence", 0) < m["min_confidence"]:
        return False, "LOW_CONFIDENCE", 0.0
    if row.get("spread_pct", 999) > m["max_spread_pct"]:
        return False, "SPREAD_TOO_HIGH", 0.0
    if row.get("volume_usdt", 0) < m["min_volume_usdt"]:
        return False, "LOW_LIQUIDITY", 0.0
    if row.get("trade_count", 0) < m["min_trade_count"]:
        return False, "LOW_BOOK_DEPTH", 0.0
    slippage_pct = row.get("spread_pct", 0) * 0.75  # tahmin
    if slippage_pct > m["max_slippage_pct"]:
        return False, "SLIPPAGE_TOO_HIGH", 0.0
    gross = sig.get("expected_gross_edge_pct", 0.0)
    fee_pct = FEE_RATE * 2 * 100
    net = gross - fee_pct - slippage_pct
    if gross <= fee_pct:
        return False, "FEE_DRAG", round(net, 4)
    if net <= 0:
        return False, "EXPECTED_EDGE_TOO_LOW", round(net, 4)
    # Maliyet-sonrası TP/SL kapıları: komisyon+slippage düşüldükten
    # sonra ödül/risk yapısı sürdürülebilir değilse giriş YOK.
    cp = cost_profile(m, slippage_pct)
    if cp["net_tp_pct"] <= 0:
        return False, "NET_TP_NON_POSITIVE", round(net, 4)
    min_rr = float(m.get("min_net_reward_risk", 1.20))
    if cp["net_reward_risk"] is None or cp["net_reward_risk"] < min_rr:
        return False, "NET_REWARD_RISK_TOO_LOW", round(net, 4)
    mult = float(m.get("min_edge_cost_multiple", 1.5))
    if net < cp["round_trip_cost_pct"] * mult:
        return False, "EDGE_BELOW_COST_MULTIPLE", round(net, 4)
    return True, None, round(net, 4)


# ── Sahiplik, limitler, pozisyon yaşam döngüsü ─────────────────────

def resolve_ownership(candidates: dict[str, dict]) -> dict[str, Any]:
    """Aynı sembol iki modelden aday olduysa: yüksek net edge kazanır."""
    by_symbol: dict[str, list] = {}
    for model, cand in candidates.items():
        for c in cand:
            by_symbol.setdefault(c["symbol"], []).append(
                {**c, "model": model})
    winners, rejected = [], []
    for sym, lst in by_symbol.items():
        lst.sort(key=lambda c: -c["net_edge_pct"])
        winners.append(lst[0])
        for loser in lst[1:]:
            rejected.append({**loser,
                             "reason_code": "DUPLICATE_MODEL_OWNERSHIP",
                             "winner_model": lst[0]["model"]})
    return {"winners": winners, "rejected": rejected}


def _open_positions(rt: dict) -> dict[str, dict]:
    pos = rt.get("positions")
    return pos if isinstance(pos, dict) else {}


# ── Legacy alpha20 (state.json) pozisyonu — çapraz motor risk birleşimi ──
LEGACY_STATE_PATH = ROOT / "state.json"


def legacy_open_position() -> dict | None:
    """Eski tek-evren botunun (alpha20.py) açık pozisyonunu oku.

    state.json'daki tekil 'position' anahtarı; yoksa/okunamazsa None.
    Toplam pozisyon tavanı ve mükerrer-sembol engeli bu pozisyonu da
    saymalı — iki motor aynı Paper portföyünü paylaşır.
    """
    try:
        if LEGACY_STATE_PATH.exists():
            with LEGACY_STATE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            pos = data.get("position") if isinstance(data, dict) else None
            if isinstance(pos, dict):
                return pos
    except (OSError, json.JSONDecodeError):
        pass
    return None


def try_open_position(symbol: str, model: str, sig: dict,
                      net_edge_pct: float, cfg: dict,
                      now: float | None = None,
                      shadow: dict | None = None
                      ) -> tuple[bool, str | None]:
    """Limit + cooldown kontrolleriyle Paper pozisyonu aç (LIVE yok)."""
    m = cfg["core"] if model == MODEL_CORE else cfg["opportunity"]
    now = now or time.time()

    result: dict[str, Any] = {}

    def _mut(rt: dict) -> None:
        pos = _open_positions(rt)
        legacy = legacy_open_position()
        if symbol in pos:
            result["reason"] = "DUPLICATE_POSITION"
            return
        if legacy is not None and legacy.get("symbol") == symbol:
            # Eski tek-evren botu aynı sembolde pozisyon tutuyor.
            result["reason"] = "DUPLICATE_POSITION"
            return
        model_open = [p for p in pos.values() if p["model"] == model]
        if len(model_open) >= m["max_open_positions"]:
            result["reason"] = "POSITION_LIMIT"
            return
        total_open = len(pos) + (1 if legacy is not None else 0)
        if total_open >= cfg["total_max_open_positions"]:
            result["reason"] = "RISK_LIMIT"
            return
        cd = rt.get("cooldowns", {}).get(model)
        if cd and now < float(cd):
            result["reason"] = "COOLDOWN"
            return
        entry = float(sig["last"])
        qty = m["position_usdt"] / entry if entry > 0 else 0.0
        pos[symbol] = {
            "symbol": symbol, "model": model, "side": sig["side"],
            "entry": entry, "quantity": qty,
            "notional_usdt": m["position_usdt"],
            "tp": entry * (1 + m["tp_pct"] / 100),
            "sl": entry * (1 - m["sl_pct"] / 100),
            "trailing_pct": m["trailing_pct"],
            "peak": entry,
            "trough": entry,  # MAE kanıtı için en düşük görülen fiyat
            "max_hold_minutes": m["max_hold_minutes"],
            "opened_at": _now_iso(), "opened_ts": now,
            "confidence": sig["confidence"],
            "net_edge_pct": net_edge_pct,
            "execution_mode": "PAPER",
            # Öğrenme köprüsü: bu girişte hangi config sürümü etkindi?
            "config_version": m.get("config_version", "BASE"),
            # PFDE gölge skorları (raporlama; karar etkisi YOK)
            "shadow_scores": shadow,
            # İlk 3 dk davranış izleri (30/60/90/180 sn MFE/MAE)
            "early_marks": {},
        }
        rt["positions"] = pos
        result["ok"] = True

    _update_runtime(_mut)
    if result.get("ok"):
        log.info("PAPER AÇILDI [%s] %s %s @%.6f", model, symbol,
                 sig["side"], sig["last"])
        return True, None
    return False, result.get("reason", "RISK_LIMIT")


def _build_trade(p: dict, price: float, result: str,
                 now: float) -> dict:
    """Kapanış muhasebesi tek yerde: fee + slippage sonrası net PnL."""
    import uuid
    gross = (price - p["entry"]) * p["quantity"]
    fee = (p["entry"] + price) * p["quantity"] * FEE_RATE
    slip = price * p["quantity"] * 0.0002
    # MFE/MAE kanıtı (Strategy Lab zarar/kâr-yakalama analizi için).
    # peak/trough izlenmemişse (eski kayıt) alanlar None — analiz
    # katmanı bunu DATA_QUALITY (kanıt eksik) olarak işler, UYDURMAZ.
    peak = p.get("peak")
    trough = p.get("trough")
    entry = p["entry"]
    mfe_pct = round((float(peak) / entry - 1) * 100, 4) \
        if isinstance(peak, (int, float)) and entry > 0 else None
    mae_pct = round((1 - float(trough) / entry) * 100, 4) \
        if isinstance(trough, (int, float)) and entry > 0 else None
    return {
        "peak_price": peak, "trough_price": trough,
        "mfe_pct": mfe_pct, "mae_pct": mae_pct,
        "trade_id": uuid.uuid4().hex[:16],
        "config_version": p.get("config_version", "BASE"),
        "notional_usdt": p.get("notional_usdt"),
        "net_edge_pct": p.get("net_edge_pct"),
        **{k: p[k] for k in ("symbol", "model", "side", "entry",
                             "quantity", "opened_at", "confidence")},
        "shadow_scores": p.get("shadow_scores"),
        "early_marks": p.get("early_marks") or None,
        # Hold Intelligence gölge izleri (karar etkisi YOK)
        "hold_track": p.get("hold_track"),
        "hold_shadow": p.get("hold_shadow"),
        "exit": price, "result": result,
        "gross_pnl": round(gross, 6),
        "fees": round(fee, 6),
        "slippage": round(slip, 6),
        "net_pnl": round(gross - fee - slip, 6),
        "hold_minutes": round((now - p["opened_ts"]) / 60, 2),
        "closed_at": _now_iso(),
        "execution_mode": "PAPER",
    }


def _position_fields_valid(p: dict) -> bool:
    """Pozisyon kaydında exit/PnL hesabı için zorunlu sayısal alanlar
    (entry, quantity) pozitif ve sonlu mu? Eksikse pozisyon
    'Yönetiliyor' sayılamaz ve kapatma/monitor hesap YAPAMAZ."""
    import math
    for key in ("entry", "quantity"):
        try:
            v = float(p.get(key))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(v) or v <= 0:
            return False
    return True


def manual_close(symbol: str,
                 price: float | None = None) -> tuple[bool, str]:
    """Operatör kapatması (PAPER). Fiyat verilmezse TAZE fiyat çekilir;
    taze fiyat alınamazsa kapatma REDDEDİLİR (bayat fiyatla kapanış yok).
    """
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return False, "SYMBOL_REQUIRED"
    if price is None:
        try:
            price = fetch_spot_prices([symbol]).get(symbol)
        except RateLimited as exc:
            return False, f"RATE_LIMITED: {exc}"
        except Exception:
            price = None
        if price is None:
            return False, "PRICE_UNAVAILABLE"
    now = time.time()
    out: dict[str, Any] = {}

    def _mut(rt: dict) -> None:
        pos = _open_positions(rt)
        p = pos.get(symbol)
        if not p:
            out["err"] = "POSITION_NOT_FOUND"
            return
        # Miktar/entry doğrulanmadan kapatma İŞLEM YAPMAZ: eksik
        # veriyle net PnL hesabı uydurma olur — reddet, kayda dokunma.
        if not _position_fields_valid(p):
            out["err"] = "INCOMPLETE_POSITION_DATA"
            return
        trade = _build_trade(p, float(price), "MANUAL_CLOSE", now)
        trades = rt.get("trades", [])
        trades.insert(0, trade)
        rt["trades"] = trades[:2000]
        del pos[symbol]
        rt["positions"] = pos
        out["trade"] = trade

    _update_runtime(_mut)
    if "trade" in out:
        log.info("PAPER MANUAL_CLOSE %s net=%.4f", symbol,
                 out["trade"]["net_pnl"])
        return True, "CLOSED"
    return False, out.get("err", "POSITION_NOT_FOUND")


def acknowledge_incomplete(symbol: str) -> tuple[bool, str, dict | None]:
    """Task 152: operatör onayı — INCOMPLETE_POSITION_DATA kaydı
    'görüldü / manuel kapatıldı' olarak aktif listeden çıkarılır.

    Fail-closed: alanları GEÇERLİ (yönetilebilir) pozisyon bu yolla
    silinemez — o yalnız manual_close ile (taze fiyat + trade kaydı)
    kapatılabilir. Başarıda çıkarılan kaydın kopyası döner (audit
    detayında kullanılır)."""
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return False, "SYMBOL_REQUIRED", None
    out: dict[str, Any] = {}

    def _mut(rt: dict) -> None:
        pos = _open_positions(rt)
        p = pos.get(symbol)
        if not p:
            out["err"] = "POSITION_NOT_FOUND"
            return
        if _position_fields_valid(p):
            out["err"] = "NOT_INCOMPLETE"
            return
        del pos[symbol]
        rt["positions"] = pos
        out["removed"] = dict(p)

    _update_runtime(_mut)
    if "removed" in out:
        log.info("OPERATOR_ACK %s: eksik-veri kaydı aktif listeden "
                 "çıkarıldı", symbol)
        return True, "ACKED", out["removed"]
    return False, out.get("err", "POSITION_NOT_FOUND"), None


def monitor_positions(price_of: Callable[[str], float | None],
                      cfg: dict, now: float | None = None) -> list[dict]:
    """TP/SL/trailing/time-exit kontrolü; kapananlar ledger'a yazılır."""
    now = now or time.time()
    closed: list[dict] = []

    def _mut(rt: dict) -> None:
        pos = _open_positions(rt)
        for sym in list(pos):
            p = pos[sym]
            # Eksik/bozuk kayıtta otomatik çıkış değerlendirmesi
            # DURDURULUR (KeyError/uydurma PnL yerine dürüst atlama;
            # snapshot bu kaydı INCOMPLETE_POSITION_DATA gösterir).
            if not _position_fields_valid(p):
                rt["last_error"] = (
                    f"{sym}: çıkış değerlendirmesi durduruldu — "
                    "pozisyon verisi eksik")
                continue
            price = price_of(sym)
            if price is None:
                continue
            p["peak"] = max(p.get("peak", p["entry"]), price)
            p["trough"] = min(p.get("trough", p["entry"]), price)
            # Hold Intelligence GÖLGE izi: anlık net PnL / zirve /
            # yeni-zirve sayacı (ucuz, deterministik; karar etkisi
            # YOK — gerçek çıkış kuralları aşağıda aynen durur).
            try:
                import hold_intelligence as _hi
                _hi.update_track(p, price, now)
            except Exception as exc:
                log.error("hold_track güncellenemedi (%s): %s",
                          sym, exc)
            trail_stop = p["peak"] * (1 - p["trailing_pct"] / 100)
            held_min = (now - p["opened_ts"]) / 60
            # PFDE erken-pencere izi: 30/60/90/180 sn eşiği ilk kez
            # aşıldığında o ana kadarki MFE/MAE dondurulur (gölge).
            marks = p.get("early_marks")
            if isinstance(marks, dict):
                held_sec = now - p["opened_ts"]
                for th in (30, 60, 90, 180):
                    key = str(th)
                    if held_sec >= th and key not in marks:
                        e = p["entry"]
                        # at_sec: ölçümün GERÇEK anı — poll aralığı
                        # yüzünden eşikten geç olabilir; analiz katmanı
                        # gecikmeli ölçümü bununla ayıklar (uydurmasız
                        # gecikmeli-ölçüm semantiği).
                        marks[key] = {
                            "mfe": round((p["peak"] / e - 1) * 100, 4),
                            "mae": round((1 - p["trough"] / e) * 100,
                                         4),
                            "at_sec": round(held_sec, 1)}
            result = None
            if price >= p["tp"]:
                result = "TP"
            elif price <= p["sl"]:
                result = "SL"
            elif price <= trail_stop and p["peak"] > p["entry"]:
                result = "TRAILING"
            elif held_min >= p["max_hold_minutes"]:
                result = "TIME_EXIT"
            if not result:
                continue
            trade = _build_trade(p, price, result, now)
            trades = rt.get("trades", [])
            trades.insert(0, trade)
            rt["trades"] = trades[:2000]
            del pos[sym]
            closed.append(trade)
            # OPPORTUNITY: arka arkaya kayıpta cooldown
            if p["model"] == MODEL_OPP and trade["net_pnl"] < 0:
                o = cfg["opportunity"]
                recent = [t for t in trades[:5]
                          if t["model"] == MODEL_OPP]
                losses = 0
                for t in recent:
                    if t["net_pnl"] < 0:
                        losses += 1
                    else:
                        break
                if losses >= o["cooldown_after_losses"]:
                    rt.setdefault("cooldowns", {})[MODEL_OPP] = \
                        now + o["cooldown_minutes"] * 60
        rt["positions"] = pos

    _update_runtime(_mut)
    for t in closed:
        log.info("PAPER KAPANDI [%s] %s %s net=%.4f", t["model"],
                 t["symbol"], t["result"], t["net_pnl"])
    # Hold Intelligence kapanış değerlendirmesi (GÖLGE: hafıza +
    # gölge dosyası + trade'e hold_review alanı; karar etkisi YOK).
    if closed:
        try:
            import hold_intelligence as _hi
            reviews = {t["trade_id"]: _hi.on_trade_closed(t)
                       for t in closed}

            def _mut_rv(rt: dict) -> None:
                for t in rt.get("trades", [])[:len(closed) + 10]:
                    rv = reviews.get(t.get("trade_id"))
                    if rv is not None:
                        t["hold_review"] = rv

            _update_runtime(_mut_rv)
            for t in closed:
                t["hold_review"] = reviews.get(t["trade_id"])
        except Exception as exc:
            log.error("hold_review üretilemedi: %s", exc)
    return closed


def record_rejection(symbol: str, model: str, reason_code: str) -> None:
    if reason_code not in REASON_CODES:
        reason_code = "DATA_QUALITY"

    def _mut(rt: dict) -> None:
        rej = rt.get("rejections", [])
        rej.insert(0, {"symbol": symbol, "model": model,
                       "reason_code": reason_code,
                       "at": _now_iso()})
        rt["rejections"] = rej[:300]

    _update_runtime(_mut)


# ── Ayrı performans ölçümü ─────────────────────────────────────────

def model_metrics(model: str, rt: dict | None = None) -> dict[str, Any]:
    rt = rt if rt is not None else _load_runtime()
    trades = [t for t in rt.get("trades", []) if t["model"] == model]
    rejections = [r for r in rt.get("rejections", [])
                  if r["model"] == model]
    wins = [t for t in trades if t["net_pnl"] > 0]
    gross = sum(t["gross_pnl"] for t in trades)
    fees = sum(t["fees"] for t in trades)
    slip = sum(t["slippage"] for t in trades)
    net = sum(t["net_pnl"] for t in trades)
    gains = sum(t["net_pnl"] for t in wins)
    losses = -sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0)
    # max drawdown (net_pnl kümülatif, kronolojik)
    equity, peak, mdd = 0.0, 0.0, 0.0
    for t in reversed(trades):
        equity += t["net_pnl"]
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    lst_key = "core_list" if model == MODEL_CORE else "opportunity_list"
    reasons: dict[str, int] = {}
    for r in rejections:
        reasons[r["reason_code"]] = reasons.get(r["reason_code"], 0) + 1
    open_pos = [p for p in _open_positions(rt).values()
                if p["model"] == model]
    day_secs = 86400.0
    first_ts = None
    if trades:
        try:
            first_ts = datetime.fromisoformat(
                trades[-1]["closed_at"]).timestamp()
        except (ValueError, KeyError):
            first_ts = None
    span_days = max((time.time() - first_ts) / day_secs, 1 / 24) \
        if first_ts else None
    return {
        "model": model,
        "scanned_symbols": len(rt.get(lst_key, [])),
        "candidates": rt.get("last_candidates", {}).get(model, 0),
        "paper_intents": len(trades) + len(open_pos),
        "opened_positions": len(open_pos),
        "closed_positions": len(trades),
        "trades_per_day": round(len(trades) / span_days, 2)
        if span_days else 0.0,
        "win_rate": round(len(wins) / len(trades) * 100, 1)
        if trades else None,
        "gross_pnl": round(gross, 4), "fees": round(fees, 4),
        "slippage": round(slip, 4), "net_pnl": round(net, 4),
        "average_hold_minutes": round(
            sum(t["hold_minutes"] for t in trades) / len(trades), 2)
        if trades else None,
        "profit_factor": round(gains / losses, 2)
        if losses > 0 else None,
        "max_drawdown": round(mdd, 4),
        "expectancy_per_trade": round(net / len(trades), 4)
        if trades else None,
        "rejection_reasons": reasons,
    }


def snapshot(with_prices: bool = False,
             main_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """TEK kanonik snapshot — UI/API/ledger/runtime aynı kaynaktan.

    with_prices=True: açık semboller için TAZE fiyat çekilir ve
    current_price / unrealized net PnL / PnL% alanları eklenir.
    Fiyat alınamazsa alanlar None kalır (UI 'UNKNOWN' gösterir) —
    bayat/uydurma fiyat asla yazılmaz.
    """
    rt = _load_runtime()
    positions = list(_open_positions(rt).values())
    prices: dict[str, float] = {}
    if with_prices and positions:
        try:
            prices = fetch_spot_prices(
                [p["symbol"] for p in positions])
        except Exception:
            prices = {}
    for p in positions:
        cur = prices.get(p["symbol"])
        p["current_price"] = cur
        # Dürüst durum ayrımı: eksik/bozuk kayıt ACTIVE görünmez;
        # taze fiyat alınamayan pozisyon PRICE_REFRESH_FAILED olur
        # (çıkış değerlendirmesi bu durumda koşamaz — UI'da açıkça
        # söylenir). Uydurma değer üretilmez.
        if not _position_fields_valid(p):
            p["position_status"] = "INCOMPLETE_POSITION_DATA"
        elif with_prices and cur is None:
            p["position_status"] = "PRICE_REFRESH_FAILED"
        else:
            p["position_status"] = "ACTIVE"
        if cur is not None and _position_fields_valid(p):
            gross = (cur - p["entry"]) * p["quantity"]
            fee = (p["entry"] + cur) * p["quantity"] * FEE_RATE
            slip = cur * p["quantity"] * 0.0002
            net = gross - fee - slip
            p["unrealized_net_pnl"] = round(net, 6)
            p["unrealized_pnl_pct"] = round(
                net / p["notional_usdt"] * 100, 4) \
                if p.get("notional_usdt") else None
            p["est_fees"] = round(fee, 6)
            p["est_slippage"] = round(slip, 6)
        else:
            p["unrealized_net_pnl"] = None
            p["unrealized_pnl_pct"] = None
            p["est_fees"] = None
            p["est_slippage"] = None
    core_open = [p for p in positions if p["model"] == MODEL_CORE]
    opp_open = [p for p in positions if p["model"] == MODEL_OPP]
    # Maliyet-sonrası TP/SL profili (etkin config: kullanıcı ayarı +
    # champion overlay). Hesap başarısızsa None — uydurma değer yok.
    try:
        _cfg = get_config(main_cfg)
        cost_profiles = {
            "core": cost_profile(_cfg["core"]),
            "opportunity": cost_profile(_cfg["opportunity"]),
        }
    except Exception:
        cost_profiles = None
    return {
        "cost_profiles": cost_profiles,
        "snapshot_version": int(rt.get("updated_ts") or 0),
        "live_orders": "DISABLED",
        "core_list": rt.get("core_list", []),
        "opportunity_list": rt.get("opportunity_list", []),
        "positions": positions,
        "counters": {
            "core_universe": len(rt.get("core_list", [])),
            "opportunity_universe": len(
                rt.get("opportunity_list", [])),
            "core_open": len(core_open),
            "opportunity_open": len(opp_open),
            "total_open": len(positions),
        },
        "metrics": {
            MODEL_CORE: model_metrics(MODEL_CORE, rt),
            MODEL_OPP: model_metrics(MODEL_OPP, rt),
        },
        "portfolio_net_pnl": round(sum(
            t["net_pnl"] for t in rt.get("trades", [])), 4),
        "recent_trades": rt.get("trades", [])[:20],
        "recent_rejections": rt.get("rejections", [])[:30],
        "last_refresh": rt.get("last_refresh"),
        "last_error": rt.get("last_error"),
    }


# ── Arka plan döngüsü (flock: süreçler arası tek koşu) ─────────────

_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_lock_fh = None


def _acquire_file_lock() -> bool:
    global _lock_fh
    try:
        _lock_fh = LOCK_FILE.open("a+")
        fcntl.flock(_lock_fh.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _monitor_open_positions(open_syms: list[str],
                            price_cache: dict[str, float],
                            cfg: dict, prev_fail: bool) -> bool:
    """Açık pozisyonları YALNIZ taze fiyatla monitör et.

    Fiyat yenileme başarısızsa (SSL/ağ/rate-limit) çıkış kararları
    GÜVENLİ biçimde ertelenir ve neden last_error'a yazılır (sağlık
    panelde görünür). Başarılı turda kendi yazdığımız hata temizlenir.
    Döndürdüğü bool: bu turda fiyat yenileme başarısız mı."""
    try:
        price_cache.update(fetch_spot_prices(open_syms))
        monitor_positions(price_cache.get, cfg, time.time())
    except RateLimited:
        return prev_fail  # geri çekilme — çıkışlar sessizce ertelenir
    except Exception as exc:
        msg = (f"PRICE_REFRESH_FAILED: {exc} — çıkış kararları taze "
               f"fiyat gelene dek ertelendi (pozisyonlar kapatılmadı)")
        log.warning(msg)

        def _mf(rt: dict) -> None:
            rt["last_error"] = msg

        try:
            _update_runtime(_mf)
        except OSError:
            pass
        return True
    if prev_fail:
        # Toparlandı: kendi yazdığımız fiyat hatasını temizle.
        def _mc(rt: dict) -> None:
            le = rt.get("last_error") or ""
            if le.startswith("PRICE_REFRESH_FAILED"):
                rt["last_error"] = None

        try:
            _update_runtime(_mc)
        except OSError:
            pass
    return False


_HOLD_RR = {"i": 0}


def _hold_shadow_cycle(open_syms: list[str], now: float) -> None:
    """Açık pozisyonlar için Hold Intelligence GÖLGE değerlendirmesi.

    Round-robin en fazla 3 sembol/çevrim (rate-limit dostu); klines
    kilit DIŞINDA çekilir, değerlendirme kopya üzerinde yapılır,
    sonuç kısa mutasyonla yazılır. Karar etkisi YOK."""
    import copy
    import hold_intelligence as _hi
    batch = open_syms[_HOLD_RR["i"] % len(open_syms):] + \
        open_syms[:_HOLD_RR["i"] % len(open_syms)]
    batch = batch[:3]
    _HOLD_RR["i"] = (_HOLD_RR["i"] + len(batch)) % max(len(open_syms), 1)
    rt = _load_runtime()
    btc = rt.get("btc_change_pct")
    pos = _open_positions(rt)
    updates: dict[str, dict] = {}
    for sym in batch:
        p = pos.get(sym)
        if not isinstance(p, dict):
            continue
        try:
            kl = fetch_spot_klines(sym)
        except RateLimited:
            break  # paylaşımlı geri çekilme — tur biter
        except Exception:
            kl = None
        pc = copy.deepcopy(p)
        _hi.evaluate_position_cycle(pc, kl or [], btc, now)
        updates[sym] = {"hold_shadow": pc.get("hold_shadow"),
                        "hold_track": pc.get("hold_track"),
                        "opened_ts": p.get("opened_ts")}
    if not updates:
        return

    def _mut(rtm: dict) -> None:
        posm = _open_positions(rtm)
        for sym, u in updates.items():
            live = posm.get(sym)
            if not isinstance(live, dict):
                continue  # pozisyon bu arada kapandı — bayat yazma YOK
            snap = updates[sym]
            if live.get("opened_ts") != snap.get("opened_ts"):
                continue  # aynı sembolde YENİ pozisyon — atla
            live["hold_shadow"] = u["hold_shadow"]
            # hold_track MERGE-ONLY: monitör bu arada ilerlemiş
            # olabilir; canlı sayaçlar/zirveler ASLA geri alınmaz,
            # yalnız yeni variant_exit anahtarları eklenir
            # (karar anındaki dondurulmuş değerle).
            new_tr = u["hold_track"]
            if isinstance(new_tr, dict):
                live_tr = live.get("hold_track")
                if not isinstance(live_tr, dict):
                    live["hold_track"] = new_tr
                else:
                    ve = live_tr.setdefault("variant_exits", {})
                    for k, v in (new_tr.get("variant_exits")
                                 or {}).items():
                        ve.setdefault(k, v)
        rtm["positions"] = posm

    _update_runtime(_mut)


def _loop(get_main_config: Callable[[], dict]) -> None:
    last_refresh = 0.0
    price_fail_flag = False
    last_core_sig = 0.0
    last_opp_sig = 0.0
    price_cache: dict[str, float] = {}
    rr = {"core": 0, "opp": 0}
    while not _STOP.is_set():
        try:
            cfg = get_config(get_main_config())
            if not cfg.get("enabled", True):
                _STOP.wait(30)
                continue
            now = time.time()
            # 1) Liste yenileme
            refresh_every = min(cfg["core"]["refresh_seconds"],
                                cfg["opportunity"]["refresh_seconds"])
            if now - last_refresh >= refresh_every:
                tickers = fetch_spot_tickers()
                core = build_core_list(tickers, cfg)
                opp = build_opportunity_list(
                    tickers, cfg, {r["symbol"] for r in core})

                # PFDE gölge bağlamı: BTC 24s değişimi (rejim/hiza)
                btc_chg = None
                for t in tickers:
                    if t.get("symbol") == "BTCUSDT":
                        try:
                            btc_chg = float(
                                t.get("priceChangePercent") or 0)
                        except (TypeError, ValueError):
                            btc_chg = None
                        break

                def _mut(rt: dict) -> None:
                    rt["core_list"] = core
                    rt["opportunity_list"] = opp
                    rt["last_refresh"] = _now_iso()
                    rt["updated_ts"] = int(now)
                    rt["last_error"] = None
                    rt["btc_change_pct"] = btc_chg

                _update_runtime(_mut)
                price_cache = {r["symbol"]: r["last"]
                               for r in core + opp}
                last_refresh = now
            rt = _load_runtime()
            # 2) Sinyal turları (round-robin batch, rate-limit dostu).
            # Adaylar İKİ modelden de toplanır; sahiplik arbitrajı
            # birleşik kümede TEK seferde çözülür (spec: iki listede
            # olan sembolde en yüksek net edge kazanır).
            all_cands: dict[str, list] = {}
            for model, key, tkey, batch in (
                    (MODEL_CORE, "core", "core_list", 4),
                    (MODEL_OPP, "opp", "opportunity_list", 4)):
                m = cfg["core"] if model == MODEL_CORE \
                    else cfg["opportunity"]
                last_t = last_core_sig if key == "core" else last_opp_sig
                if now - last_t < m["signal_seconds"]:
                    continue
                rows = rt.get(tkey, [])
                if not rows:
                    continue
                cands = []
                i0 = rr[key]
                for j in range(min(batch, len(rows))):
                    row = rows[(i0 + j) % len(rows)]
                    sym = row["symbol"]
                    try:
                        kl = fetch_spot_klines(sym)
                    except RateLimited:
                        break  # paylaşımlı geri çekilme — tur biter
                    except Exception:
                        record_rejection(sym, model, "DATA_QUALITY")
                        continue
                    if kl:
                        price_cache[sym] = float(kl[-1][4])
                    sig = evaluate_signal(sym, kl, model)
                    if not sig.get("side"):
                        record_rejection(sym, model,
                                         sig.get("reason_code",
                                                 "NO_SIGNAL"))
                        continue
                    # PFDE GÖLGE skoru: gerçek kapıdan BAĞIMSIZ,
                    # kararı DEĞİŞTİRMEZ; yalnız kayıt (raporlama).
                    shadow = None
                    try:
                        import profit_first as _pf
                        shadow = _pf.score_candidate(
                            row, sig, kl, model, m,
                            {"btc_change_pct":
                             rt.get("btc_change_pct"),
                             "trades": rt.get("trades", [])[:100]})
                        _pf.append_shadow(shadow)
                    except Exception as exc:
                        log.error("PFS gölge skoru üretilemedi "
                                  "(%s): %s", sym, exc)
                    ok, reason, net = execution_quality_gate(
                        row, sig, model, cfg)
                    if not ok:
                        record_rejection(sym, model, reason)
                        continue
                    cands.append({"symbol": sym, "sig": sig,
                                  "net_edge_pct": net,
                                  "shadow": shadow})
                rr[key] = (i0 + batch) % max(len(rows), 1)

                def _mutc(rtc: dict, model=model, n=len(cands)) -> None:
                    rtc.setdefault("last_candidates", {})[model] = n

                _update_runtime(_mutc)
                if key == "core":
                    last_core_sig = now
                else:
                    last_opp_sig = now
                all_cands[model] = cands
            # 3) Birleşik sahiplik arbitrajı + açılış
            if all_cands:
                own = resolve_ownership(all_cands)
                for rej in own["rejected"]:
                    record_rejection(rej["symbol"], rej["model"],
                                     "DUPLICATE_MODEL_OWNERSHIP")
                for w in own["winners"]:
                    opened, reason = try_open_position(
                        w["symbol"], w["model"], w["sig"],
                        w["net_edge_pct"], cfg, now,
                        shadow=w.get("shadow"))
                    if not opened:
                        record_rejection(w["symbol"], w["model"],
                                         reason)
            # 4) Pozisyon monitörü — açık semboller için TAZE fiyat
            # (bayat cache ile TP/SL kararı verilmez)
            open_syms = list(_open_positions(_load_runtime()))
            if open_syms:
                price_fail_flag = _monitor_open_positions(
                    open_syms, price_cache, cfg, price_fail_flag)
            # 5) Hold Intelligence GÖLGE çevrimi: açık pozisyonlar
            # için trend sağlığı / PHI / hold-vs-exit gölge kararı.
            # Gerçek çıkış kuralları ETKİLENMEZ; hata yutulur.
            if open_syms:
                try:
                    _hold_shadow_cycle(open_syms, time.time())
                except Exception as exc:
                    log.error("hold gölge çevrimi hatası: %s", exc)
            _STOP.wait(cfg["monitor_seconds"])
        except Exception as exc:  # döngü asla ölmez; hata görünür
            log.error("dual_model döngü hatası: %s", exc)

            def _me(rt: dict) -> None:
                rt["last_error"] = str(exc)

            try:
                _update_runtime(_me)
            except OSError:
                pass
            _STOP.wait(15)


def record_startup_failure(message: str) -> None:
    """Loop başlatılamadığında nedeni panele görünür kıl (last_error).

    Yalnız TEK-süreçli girişler (serve_windows) çağırmalı: gunicorn'da
    kilidi alamayan diğer worker'lar NORMALDİR ve bunu çağırmamalıdır."""
    def _mut(rt: dict) -> None:
        rt["last_error"] = message

    try:
        _update_runtime(_mut)
    except OSError:
        log.warning("Dual-model startup hatası runtime'a yazılamadı: %s",
                    message)


def start_dual_model_loop(get_main_config: Callable[[], dict]) -> bool:
    """Süreçler arası flock: yalnız tek worker döngüyü koşturur."""
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return True
    if not _acquire_file_lock():
        return False
    cfg = get_config(get_main_config())
    if not cfg.get("enabled", True):
        return False
    _STOP.clear()
    _THREAD = threading.Thread(
        target=_loop, args=(get_main_config,),
        name="dual-model-loop", daemon=True)
    _THREAD.start()
    log.info("Dual-model PAPER döngüsü başladı (LIVE ORDERS DISABLED).")
    return True
