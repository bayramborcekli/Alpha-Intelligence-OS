"""Hold Intelligence — pozisyon YÖNETİMİ gölge katmanı (PHI).

PFDE'nin devamı: giriş skorları (Confidence/TCP/EPP/PFS) mevcut;
bu modül işlem AÇILDIKTAN SONRA her analiz çevriminde "beklemek mi,
çıkmak mı?" sorusunu GÖLGEDE öğrenir.

SÖZLEŞME (değişmez):
- GERÇEK ÇIKIŞ DAVRANIŞI DEĞİŞMEZ: TP/SL/trailing/time-exit aynen
  kalır; bu modül yalnız kayıt üretir. PHI/hold_state hiçbir gerçek
  karara bağlanamaz.
- LIVE ORDERS DISABLED; champion değişmez.
- Fail-closed: zorunlu girdi eksikse skor None + DATA_QUALITY —
  0/1 ikamesi ve uydurma YASAK (PFDE kuralıyla aynı).
- Look-ahead yok: her ölçüm yalnız o ana kadar görülen veriyle
  hesaplanır; kapanış sonrası değerlendirme yalnız işlem İÇİ kanıt
  kullanır ("erken çıkış" işlem-sonrası veri olmadan KANITLANAMAZ ve
  NOT_PROVABLE olarak işaretlenir).
- Gölge dosyası ayrı kalıcı .lock ile yazılır (PFDE dersleri);
  hafıza deposu flock + atomic replace (dual_learning kalıbı).
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows: fcntl yok — taşınabilir kilit kalıbı
    import portable_flock as fcntl  # type: ignore

import json
import logging
import math
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("hold_intelligence")

BASE_DIR = Path(__file__).resolve().parent
SHADOW_PATH = BASE_DIR / "hold_shadow.jsonl"
MEMORY_PATH = BASE_DIR / "hold_memory.json"
SHADOW_MAX_BYTES = 5 * 1024 * 1024
SHADOW_KEEP_LINES = 2000

FEE_RATE = 0.001          # dual_model ile aynı (tek yön)
SLIP_RATE = 0.0002        # dual_model _build_trade ile aynı

TREND_STATES = ("STRENGTHENING", "STABLE", "WEAKENING",
                "BREAKING", "DEAD")
HOLD_STATES = ("HOLD_STRONG", "HOLD_NORMAL", "HOLD_WEAK",
               "EXIT_WATCH", "EXIT_READY", "EXIT_NOW")
REGIMES = ("STRONG_TREND", "WEAK_TREND", "RANGE", "STRESS",
           "HIGH_VOLATILITY", "LOW_VOLATILITY")
DECAY_CODES = ("EMA_COLLAPSING", "VWAP_LOST", "LOWER_HIGH",
               "LOWER_LOW", "BTC_REVERSAL", "MOMENTUM_LOST",
               "VOLUME_COLLAPSE", "BREAKOUT_FAILED", "RANGE_FORMING",
               "VOLATILITY_DROPPED", "LIQUIDITY_LOST", "DATA_QUALITY")
VARIANTS = ("balanced", "conservative", "aggressive")


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(float(v)) else None


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _ema(vals: list[float], n: int) -> float:
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


# ── MODÜL 2: TREND HEALTH ──────────────────────────────────────────

def trend_health(klines: list[list],
                 btc_change_pct: Any = None) -> dict | None:
    """Trend sağlığı: güçleniyor / aynı / zayıflıyor / bozuluyor /
    öldü. Tek indikatör karar VERMEZ — EMA eğimi+açılımı, HH/HL,
    MACD, RSI yönü, VWAP, ADX-benzeri yön gücü ve hacim birlikte
    oylanır. Veri yetersizse None (fail-closed)."""
    if not klines or len(klines) < 40:
        return None
    try:
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        vols = [float(k[5]) for k in klines]
    except (TypeError, ValueError, IndexError):
        return None
    # Fail-closed girdi doğrulama: sonlu olmayan / pozitif olmayan
    # fiyat verisiyle skor ÜRETİLMEZ (uydurma yasak).
    if not all(math.isfinite(x) and x > 0
               for x in closes + highs + lows):
        return None
    if not all(math.isfinite(v) and v >= 0 for v in vols):
        return None
    votes: dict[str, float] = {}

    # EMA eğimi + açılımı (expansion)
    e9_now = _ema(closes[-30:], 9)
    e21_now = _ema(closes[-30:], 21)
    e9_prev = _ema(closes[-35:-5], 9)
    e21_prev = _ema(closes[-35:-5], 21)
    slope = (e9_now - e9_prev) / e9_prev * 100 if e9_prev else 0.0
    gap_now = (e9_now - e21_now) / e21_now * 100 if e21_now else 0.0
    gap_prev = (e9_prev - e21_prev) / e21_prev * 100 \
        if e21_prev else 0.0
    votes["ema_slope"] = _clamp(slope / 0.15 + 0.5)
    votes["ema_expansion"] = _clamp((gap_now - gap_prev) / 0.10 + 0.5)

    # Higher highs / higher lows (son 3 x 5'lik pencere)
    h1 = max(highs[-5:]); h2 = max(highs[-10:-5]); h3 = max(highs[-15:-10])
    l1 = min(lows[-5:]); l2 = min(lows[-10:-5]); l3 = min(lows[-15:-10])
    hh = (1 if h1 > h2 else 0) + (1 if h2 > h3 else 0)
    hl = (1 if l1 > l2 else 0) + (1 if l2 > l3 else 0)
    votes["higher_highs"] = hh / 2.0
    votes["higher_lows"] = hl / 2.0

    # MACD (12/26 EMA farkının yönü)
    macd_now = _ema(closes[-40:], 12) - _ema(closes[-40:], 26)
    macd_prev = _ema(closes[-45:-5] if len(closes) >= 45 else
                     closes[:-5], 12) - \
        _ema(closes[-45:-5] if len(closes) >= 45 else closes[:-5], 26)
    ref = abs(closes[-1]) * 0.001 or 1e-9
    votes["macd"] = _clamp((macd_now - macd_prev) / ref + 0.5)

    # RSI yönü
    def _rsi(cs: list[float], n: int = 14) -> float:
        gains, losses = 0.0, 0.0
        for i in range(-n, 0):
            d = cs[i] - cs[i - 1]
            gains += max(d, 0)
            losses += max(-d, 0)
        if losses == 0:
            return 100.0
        rs = gains / losses
        return 100 - 100 / (1 + rs)
    rsi_now = _rsi(closes)
    rsi_prev = _rsi(closes[:-5])
    votes["rsi_direction"] = _clamp((rsi_now - rsi_prev) / 15 + 0.5)

    # VWAP (30 pencere) üstünde mi
    pv = sum(c * v for c, v in zip(closes[-30:], vols[-30:]))
    vsum = sum(vols[-30:]) or 1.0
    vwap = pv / vsum
    votes["vwap"] = 1.0 if closes[-1] > vwap else 0.0

    # ADX-benzeri yön gücü: |net hareket| / toplam hareket
    net_move = abs(closes[-1] - closes[-15])
    path = sum(abs(closes[i] - closes[i - 1])
               for i in range(-14, 0)) or 1e-9
    votes["adx_like"] = _clamp(net_move / path * 2)

    # Hacim: son 5 vs önceki 25
    v_recent = sum(vols[-5:]) / 5
    v_base = (sum(vols[-30:-5]) / 25) or 1e-9
    vol_ratio = v_recent / v_base
    votes["volume"] = _clamp((vol_ratio - 0.5) / 1.0)

    score = round(sum(votes.values()) / len(votes) * 100, 2)
    if score >= 65:
        state = "STRENGTHENING"
    elif score >= 50:
        state = "STABLE"
    elif score >= 38:
        state = "WEAKENING"
    elif score >= 25:
        state = "BREAKING"
    else:
        state = "DEAD"
    out = {"state": state, "score": score,
           "components": {k: round(v, 4) for k, v in votes.items()},
           "vwap": vwap, "vol_ratio": round(vol_ratio, 3),
           "ema_gap_pct": round(gap_now, 4),
           "rsi": round(rsi_now, 1)}
    btc = _num(btc_change_pct)
    out["btc_change_pct"] = btc
    return out


# ── MODÜL 9 girdisi: rejim sınıflaması ─────────────────────────────

def classify_regime(klines: list[list]) -> str | None:
    """6 rejimden biri; veri yetersizse None (fail-closed)."""
    th = trend_health(klines)
    if th is None:
        return None
    closes = [float(k[4]) for k in klines]
    rets = [abs(closes[i] / closes[i - 1] - 1) * 100
            for i in range(-20, 0) if closes[i - 1]]
    volat = sum(rets) / len(rets) if rets else 0.0
    if volat >= 0.30:
        return "HIGH_VOLATILITY" if th["score"] >= 38 else "STRESS"
    if volat <= 0.03:
        return "LOW_VOLATILITY"
    if th["state"] in ("STRENGTHENING", "STABLE") and \
            th["components"]["adx_like"] >= 0.5:
        return "STRONG_TREND"
    if th["state"] in ("WEAKENING",) or \
            th["components"]["adx_like"] >= 0.25:
        return "WEAK_TREND"
    return "RANGE"


# ── MODÜL 5: TREND DECAY reason code'ları ──────────────────────────

def trend_decay_reasons(th: dict | None,
                        prev_th: dict | None = None,
                        entry_breakout: bool = False,
                        liquidity_ok: bool = True) -> list[str]:
    """Trend neden ölüyor? Yalnız kanıtlanabilir kodlar üretilir."""
    if th is None:
        return ["DATA_QUALITY"]
    r: list[str] = []
    c = th["components"]
    if c["ema_slope"] < 0.3 and c["ema_expansion"] < 0.3:
        r.append("EMA_COLLAPSING")
    if c["vwap"] == 0.0:
        r.append("VWAP_LOST")
    if c["higher_highs"] == 0.0:
        r.append("LOWER_HIGH")
    if c["higher_lows"] == 0.0:
        r.append("LOWER_LOW")
    btc = th.get("btc_change_pct")
    if isinstance(btc, (int, float)) and btc < -0.5:
        r.append("BTC_REVERSAL")
    if c["macd"] < 0.3 and c["rsi_direction"] < 0.3:
        r.append("MOMENTUM_LOST")
    if c["volume"] < 0.2:
        r.append("VOLUME_COLLAPSE")
    if entry_breakout and th["state"] in ("BREAKING", "DEAD"):
        r.append("BREAKOUT_FAILED")
    if c["adx_like"] < 0.2:
        r.append("RANGE_FORMING")
    if prev_th is not None and \
            th.get("vol_ratio", 0) < prev_th.get("vol_ratio", 0) * 0.5:
        r.append("VOLATILITY_DROPPED")
    if not liquidity_ok:
        r.append("LIQUIDITY_LOST")
    return r


# ── MODÜL 4: PROFIT QUALITY ────────────────────────────────────────

def profit_quality(track: dict | None,
                   held_sec: float | None = None) -> dict | None:
    """Net kâr kalitesi — yalnız izlenen (uydurmasız) verilerle.
    track: monitor'ün her poll'da güncellediği hold_track sözlüğü."""
    if not isinstance(track, dict):
        return None
    max_net = _num(track.get("max_net_pnl"))
    cur_net = _num(track.get("net_pnl"))
    if max_net is None or cur_net is None:
        return None
    out: dict[str, Any] = {
        "max_net_pnl": max_net, "net_pnl": cur_net}
    if max_net > 0:
        captured = cur_net / max_net
        out["captured_ratio"] = round(_clamp(captured, -5, 1), 4)
        out["giveback_pnl"] = round(max_net - cur_net, 6)
        out["giveback_ratio"] = round(
            _clamp((max_net - cur_net) / max_net, 0, 10), 4)
    else:
        out["captured_ratio"] = None   # net kâr hiç oluşmadı
        out["giveback_pnl"] = None
        out["giveback_ratio"] = None
    peak_at = _num(track.get("max_net_at_sec"))
    if held_sec is not None and peak_at is not None:
        out["time_since_peak_sec"] = round(max(held_sec - peak_at, 0), 1)
    else:
        out["time_since_peak_sec"] = None
    nh = track.get("new_high_count")
    out["new_high_count"] = nh if isinstance(nh, int) else None
    if isinstance(nh, int) and held_sec and held_sec > 0:
        out["new_high_per_min"] = round(nh / (held_sec / 60), 3)
    else:
        out["new_high_per_min"] = None
    # profit velocity: kâr / dakika (yalnız pozitif zirvede anlamlı)
    if max_net > 0 and peak_at and peak_at > 0:
        out["profit_velocity_per_min"] = round(
            max_net / (peak_at / 60), 6)
    else:
        out["profit_velocity_per_min"] = None
    # profit decay: zirveden bu yana erime hızı
    tsp = out["time_since_peak_sec"]
    if tsp and tsp > 0 and out["giveback_pnl"] is not None:
        out["profit_decay_per_min"] = round(
            out["giveback_pnl"] / (tsp / 60), 6)
    else:
        out["profit_decay_per_min"] = None
    return out


# ── MODÜL 10: ADAPTIVE HOLD (gölge toleransı) ─────────────────────

def adaptive_giveback_limit(th: dict | None, regime: str | None,
                            phi: float | None) -> float | None:
    """Gölge giveback limiti (max_net'in oranı). Sabit trailing
    DEĞİL: trend gücü + rejim + PHI ile değişir. Girdi eksikse None."""
    if th is None or regime is None:
        return None
    base = 0.35
    score = th["score"]
    if score >= 65:
        base += 0.20          # güçlü trend → daha fazla tolerans
    elif score < 38:
        base -= 0.15          # zayıf/bozulan → daha az tolerans
    if regime == "STRONG_TREND":
        base += 0.10
    elif regime in ("STRESS", "RANGE"):
        base -= 0.10
    elif regime == "HIGH_VOLATILITY":
        base += 0.05          # gürültüye pay
    if phi is not None:
        base += (phi - 50) / 100 * 0.2
    return round(_clamp(base, 0.10, 0.80), 4)


# ── MODÜL 1: PHI ───────────────────────────────────────────────────

def compute_phi(shadow_scores: dict | None, th: dict | None,
                pq: dict | None, decay: list[str]) -> float | None:
    """Pozisyon Sağlık Endeksi 0-100. Yeni bağımsız skor DEĞİL:
    giriş gölge skorları (TCP/EPP/PFS) + trend sağlığı + kâr
    kalitesi + decay kanıtlarının deterministik bileşimi.
    Trend sağlığı yoksa None (fail-closed)."""
    if th is None:
        return None
    parts: list[tuple[float, float]] = []       # (ağırlık, 0-1 değer)
    parts.append((0.40, th["score"] / 100))
    if isinstance(shadow_scores, dict):
        for key, w in (("tcp", 0.10), ("pfs", 0.10)):
            v = _num(shadow_scores.get(key))
            if v is not None:
                parts.append((w, v / 100))
    if pq is not None:
        cr = pq.get("captured_ratio")
        if cr is not None:
            parts.append((0.20, _clamp(cr)))
        nh = pq.get("new_high_per_min")
        if nh is not None:
            parts.append((0.10, _clamp(nh / 1.0)))
    # decay kanıtları puan düşürür (DATA_QUALITY hariç)
    hard = [d for d in decay if d != "DATA_QUALITY"]
    penalty = _clamp(len(hard) * 0.08, 0, 0.4)
    den = sum(w for w, _ in parts)
    if den <= 0:
        return None
    raw = sum(w * v for w, v in parts) / den
    return round(_clamp(raw - penalty) * 100, 2)


# ── MODÜL 3: HOLD QUALITY sınıfı ───────────────────────────────────

def hold_state(phi: float | None, th: dict | None,
               pq: dict | None,
               giveback_limit: float | None) -> str | None:
    """6 sınıf — Shadow Only. PHI yoksa None (fail-closed)."""
    if phi is None or th is None:
        return None
    gb = pq.get("giveback_ratio") if pq else None
    if gb is not None and giveback_limit is not None and \
            gb >= giveback_limit and th["state"] in (
                "WEAKENING", "BREAKING", "DEAD"):
        return "EXIT_NOW"
    if th["state"] == "DEAD":
        return "EXIT_NOW"
    if th["state"] == "BREAKING":
        return "EXIT_READY"
    if phi >= 70:
        return "HOLD_STRONG"
    if phi >= 55:
        return "HOLD_NORMAL"
    if phi >= 45:
        return "HOLD_WEAK"
    if phi >= 35:
        return "EXIT_WATCH"
    return "EXIT_READY"


# ── MODÜL 11: gölge varyant kararları ──────────────────────────────

def variant_decisions(state: str | None, pq: dict | None,
                      giveback_limit: float | None) -> dict:
    """Balanced / Conservative / Aggressive gölge kararı (HOLD/EXIT).
    Gerçek çıkış DEĞİŞMEZ; yalnız kayıt."""
    if state is None:
        return {v: None for v in VARIANTS}
    order = HOLD_STATES.index(state)
    gb = pq.get("giveback_ratio") if pq else None
    out: dict[str, str | None] = {}
    # balanced: EXIT_READY ve sonrası çıkar
    out["balanced"] = "EXIT" if order >= HOLD_STATES.index(
        "EXIT_READY") else "HOLD"
    # conservative: EXIT_WATCH'ta veya giveback limitin %60'ında çıkar
    cons = order >= HOLD_STATES.index("EXIT_WATCH")
    if not cons and gb is not None and giveback_limit is not None:
        cons = gb >= giveback_limit * 0.6
    out["conservative"] = "EXIT" if cons else "HOLD"
    # aggressive: yalnız EXIT_NOW'da çıkar (trende sonuna dek tolerans)
    out["aggressive"] = "EXIT" if state == "EXIT_NOW" else "HOLD"
    return out


# ── Poll bazlı net PnL izleme (monitor için ucuz yardımcı) ─────────

def update_track(p: dict, price: float, now: float) -> dict | None:
    """Pozisyonun hold_track alanını günceller (her poll'da, ucuz).
    Aynı fee/slip modeliyle ANLIK net PnL; max_net, zirve zamanı ve
    yeni-zirve sayısı izlenir. Zorunlu alan yoksa None."""
    entry = _num(p.get("entry"))
    qty = _num(p.get("quantity"))
    opened = _num(p.get("opened_ts"))
    price = _num(price)  # type: ignore[assignment]
    now = _num(now)      # type: ignore[assignment]
    if entry is None or qty is None or opened is None or \
            price is None or now is None or entry <= 0 or price <= 0:
        return None      # NaN/geçersiz girdi kalıcılaştırılmaz
    gross = (price - entry) * qty
    fee = (entry + price) * qty * FEE_RATE
    slip = price * qty * SLIP_RATE
    net = gross - fee - slip
    held = now - opened
    tr = p.get("hold_track")
    if not isinstance(tr, dict):
        tr = {"max_net_pnl": net, "max_net_at_sec": round(held, 1),
              "new_high_count": 0, "variant_exits": {}}
    tr["net_pnl"] = round(net, 6)
    if net > tr.get("max_net_pnl", float("-inf")):
        tr["max_net_pnl"] = round(net, 6)
        tr["max_net_at_sec"] = round(held, 1)
        if net > 0:
            tr["new_high_count"] = int(tr.get("new_high_count", 0)) + 1
    p["hold_track"] = tr
    return tr


def apply_variant_exits(p: dict, decisions: dict,
                        held_sec: float) -> None:
    """Gölge varyantı ilk kez EXIT dediğinde o anki net PnL dondurulur
    (look-ahead yok: yalnız o ana kadarki izlenen değer)."""
    tr = p.get("hold_track")
    if not isinstance(tr, dict):
        return
    ve = tr.setdefault("variant_exits", {})
    for v in VARIANTS:
        if decisions.get(v) == "EXIT" and v not in ve:
            ve[v] = {"net_pnl": tr.get("net_pnl"),
                     "at_sec": round(held_sec, 1)}


# ── MODÜL 7: kapanış değerlendirmesi (yalnız işlem-içi kanıt) ─────

def hold_review(trade: dict) -> dict:
    """Çıkış değerlendirmesi. Look-ahead YOK: yalnız işlem içinde
    izlenen kanıt kullanılır. 'Erken çıkış' işlem-sonrası veri
    olmadan kanıtlanamaz → NOT_PROVABLE (dürüst sınır)."""
    tr = trade.get("hold_track")
    net = _num(trade.get("net_pnl"))
    if not isinstance(tr, dict) or net is None:
        return {"verdict": "DATA_QUALITY",
                "early_exit": "NOT_PROVABLE"}
    max_net = _num(tr.get("max_net_pnl"))
    out: dict[str, Any] = {"early_exit": "NOT_PROVABLE",
                           "max_net_pnl": max_net,
                           "exit_net_pnl": net}
    if max_net is None:
        out["verdict"] = "DATA_QUALITY"
        return out
    if max_net <= 0:
        out["verdict"] = "NEVER_PROFITABLE"
        out["captured_ratio"] = None
        out["missed_net_pnl"] = None
    else:
        captured = net / max_net
        out["captured_ratio"] = round(captured, 4)
        out["missed_net_pnl"] = round(max_net - net, 6)
        out["verdict"] = "CAPTURED_WELL" if captured >= 0.6 else \
            "GAVE_BACK"
    # Gölge varyant karşılaştırması (gerçek vs dondurulmuş gölge net)
    ve = tr.get("variant_exits") or {}
    comp = {}
    for v in VARIANTS:
        x = ve.get(v)
        vnet = _num(x.get("net_pnl")) if isinstance(x, dict) else None
        # Varyant EXIT demediyse gerçek kapanışla aynı sonucu alır
        comp[v] = {"net_pnl": vnet if vnet is not None else net,
                   "exited_early": vnet is not None,
                   "delta_vs_real": round(
                       (vnet if vnet is not None else net) - net, 6)}
    out["variants"] = comp
    return out


# ── MODÜL 8-9: CONTINUATION + REGIME MEMORY (gölge öğrenme) ───────

def _memory_update(mut) -> None:
    lock_path = str(MEMORY_PATH) + ".lock"
    with open(lock_path, "a") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        data = {}
        try:
            with open(MEMORY_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        mut(data)
        tmp = f"{MEMORY_PATH}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as g:
            json.dump(data, g, ensure_ascii=False)
            g.flush()
            os.fsync(g.fileno())
        os.replace(tmp, MEMORY_PATH)


def read_memory() -> dict:
    try:
        with open(MEMORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def record_closed_trade(trade: dict, review: dict,
                        regime: str | None) -> bool:
    """Sembol + rejim hafızasına kapanış istatistiği ekler (gölge
    öğrenme; hiçbir gerçek karar okumaz). Yazılamazsa False."""
    sym = trade.get("symbol")
    if not sym:
        return False
    hold_min = _num(trade.get("hold_minutes"))
    cr = review.get("captured_ratio")
    gb = None
    tr = trade.get("hold_track")
    if isinstance(tr, dict):
        mx = _num(tr.get("max_net_pnl"))
        nt = _num(trade.get("net_pnl"))
        if mx is not None and mx > 0 and nt is not None:
            gb = (mx - nt) / mx

    def _upd(bucket: dict) -> None:
        n = bucket.get("n", 0)
        bucket["n"] = n + 1
        for key, val in (("hold_minutes", hold_min),
                         ("captured_ratio", cr),
                         ("giveback_ratio", gb)):
            if val is None:
                continue
            cnt_k, sum_k = key + "_n", key + "_sum"
            bucket[cnt_k] = bucket.get(cnt_k, 0) + 1
            bucket[sum_k] = round(bucket.get(sum_k, 0.0) + val, 6)
        if review.get("verdict") == "CAPTURED_WELL":
            bucket["captured_well"] = bucket.get("captured_well", 0) + 1
        if review.get("verdict") == "NEVER_PROFITABLE":
            bucket["never_profitable"] = \
                bucket.get("never_profitable", 0) + 1

    def _mut(data: dict) -> None:
        _upd(data.setdefault("symbols", {}).setdefault(sym, {}))
        if regime in REGIMES:
            _upd(data.setdefault("regimes", {}).setdefault(regime, {}))

    try:
        _memory_update(_mut)
        return True
    except OSError as exc:
        log.error("hold_memory yazılamadı: %s", exc)
        return False


# ── Gölge dosyası (PFDE kalıbı: ayrı .lock + kırpma) ──────────────

def append_shadow(record: dict, path: Path | None = None) -> bool:
    path = path if path is not None else SHADOW_PATH
    lock_path = str(path) + ".lock"
    try:
        with open(lock_path, "a") as lk:
            fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False,
                                   separators=(",", ":")) + "\n")
                f.flush()
                size = f.tell()
            if size > SHADOW_MAX_BYTES:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()[-SHADOW_KEEP_LINES:]
                tmp = f"{path}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as g:
                    g.write("\n".join(lines) + "\n")
                    g.flush()
                    os.fsync(g.fileno())
                os.replace(tmp, path)
        return True
    except OSError as exc:
        log.error("hold_shadow yazılamadı: %s", exc)
        return False


def read_shadow(limit: int = 500,
                path: Path | None = None) -> list[dict]:
    """Gölge dosyasının SON kayıtları — kuyruktan sınırlı okuma:
    5MB dosyada bile yalnız son ~256KB okunur (worker bloklamaz)."""
    path = path if path is not None else SHADOW_PATH
    tail_bytes = 256 * 1024
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(size - tail_bytes, 0))
            chunk = f.read().decode("utf-8", errors="replace")
        lines = chunk.splitlines()
        if size > tail_bytes and lines:
            lines = lines[1:]  # kısmi ilk satırı at
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


# ── Çevrim değerlendirmesi (loop'tan çağrılır; gölge) ─────────────

def evaluate_position_cycle(p: dict, klines: list[list],
                            btc_change_pct: Any,
                            now: float) -> dict | None:
    """Açık pozisyon için tek çevrim gölge değerlendirmesi.
    Pozisyon sözlüğüne son gölge durumu yazılır (görüntüleme);
    gölge dosyasına kayıt düşülür. Karar etkisi YOKTUR."""
    opened = _num(p.get("opened_ts"))
    if opened is None:
        return None
    held_sec = now - opened
    th = trend_health(klines, btc_change_pct)
    regime = classify_regime(klines) if th is not None else None
    prev = p.get("hold_shadow") or {}
    prev_th = prev.get("trend") if isinstance(prev, dict) else None
    entry_breakout = False
    ss = p.get("shadow_scores")
    if isinstance(ss, dict):
        ltp = ss.get("local_top") or {}
        entry_breakout = bool(ltp.get("breakout_bars_above"))
    decay = trend_decay_reasons(th, prev_th, entry_breakout)
    pq = profit_quality(p.get("hold_track"), held_sec)
    phi = compute_phi(ss if isinstance(ss, dict) else None,
                      th, pq, decay)
    limit = adaptive_giveback_limit(th, regime, phi)
    state = hold_state(phi, th, pq, limit)
    decisions = variant_decisions(state, pq, limit)
    apply_variant_exits(p, decisions, held_sec)
    snap = {
        "at_sec": round(held_sec, 1),
        "trend": th, "regime": regime, "decay": decay,
        "phi": phi, "hold_state": state,
        "giveback_limit": limit, "variants": decisions,
        "profit_quality": pq,
    }
    p["hold_shadow"] = snap
    rec = {"symbol": p.get("symbol"), "model": p.get("model"),
           "phi": phi, "hold_state": state, "regime": regime,
           "decay": decay, "variants": decisions,
           "net_pnl": (pq or {}).get("net_pnl"),
           "at_sec": snap["at_sec"]}
    append_shadow(rec)
    return snap


def on_trade_closed(trade: dict) -> dict:
    """Kapanışta: değerlendirme + hafıza + gölge kaydı (yan etkisi
    yalnız gölge/hafıza dosyaları)."""
    review = hold_review(trade)
    hs = trade.get("hold_shadow") or {}
    regime = hs.get("regime") if isinstance(hs, dict) else None
    record_closed_trade(trade, review, regime)
    append_shadow({"event": "CLOSE", "symbol": trade.get("symbol"),
                   "model": trade.get("model"),
                   "result": trade.get("result"),
                   "net_pnl": trade.get("net_pnl"),
                   "review": review, "regime": regime})
    return review


# ── MODÜL 6 + rapor ────────────────────────────────────────────────

_LAB_CTX_CACHE: dict[str, Any] = {"ts": 0.0, "value": None}
_LAB_CTX_TTL_SEC = 60.0


def _lab_context() -> dict:
    """MODÜL 12: Strategy Lab bağlamı — SALT OKUNUR + süreç içi
    kısa önbellek (her GET isteğinde lab durumu yeniden hesaplanıp
    worker bloklanmaz). Skorlar lab'de yalnız GÖLGE challenger
    verisidir; champion'a asla yazılmaz."""
    import time as _t
    now = _t.monotonic()
    if _LAB_CTX_CACHE["value"] is not None and \
            now - _LAB_CTX_CACHE["ts"] < _LAB_CTX_TTL_SEC:
        return _LAB_CTX_CACHE["value"]
    ctx: dict[str, Any] = {
        "scores_role": "SHADOW_CHALLENGER_ONLY",
        "champion_changed": False}
    try:
        import strategy_lab as _sl
        st = _sl.status() if hasattr(_sl, "status") else None
        if isinstance(st, dict):
            ctx["live_orders"] = st.get("live_orders", "DISABLED")
            ctx["lab_reachable"] = True
        else:
            ctx["lab_reachable"] = False
    except Exception:
        ctx["lab_reachable"] = False
    _LAB_CTX_CACHE["ts"] = now
    _LAB_CTX_CACHE["value"] = ctx
    return ctx

def build_report(trades: list[dict]) -> dict:
    """Salt okunur rapor: gölge HOLD vs EXIT karşılaştırması, kâr
    kalitesi ve hafıza özetleri. Uydurma yok — kapsam dürüst."""
    reviewed = []
    for t in trades:
        if isinstance(t.get("hold_review"), dict):
            reviewed.append((t, t["hold_review"]))
    variant_totals: dict[str, dict] = {
        v: {"net_pnl": 0.0, "trades": 0, "improved": 0}
        for v in VARIANTS}
    real_total = 0.0
    verdicts: dict[str, int] = {}
    for t, rv in reviewed:
        net = _num(t.get("net_pnl"))
        if net is None:
            continue
        real_total += net
        verdicts[rv.get("verdict", "DATA_QUALITY")] = \
            verdicts.get(rv.get("verdict", "DATA_QUALITY"), 0) + 1
        for v, c in (rv.get("variants") or {}).items():
            if v not in variant_totals:
                continue
            variant_totals[v]["trades"] += 1
            variant_totals[v]["net_pnl"] = round(
                variant_totals[v]["net_pnl"] + c["net_pnl"], 6)
            if c["delta_vs_real"] > 0:
                variant_totals[v]["improved"] += 1
    mem = read_memory()

    def _avg(b: dict, key: str) -> float | None:
        n = b.get(key + "_n", 0)
        return round(b[key + "_sum"] / n, 4) if n else None

    mem_out = {"symbols": {}, "regimes": {}}
    for scope in ("symbols", "regimes"):
        items = list((mem.get(scope) or {}).items())
        # Sınırlı projeksiyon: en çok örneklemli 50 kova (rapor
        # sınırsız sembol kardinalitesiyle şişmez)
        items.sort(key=lambda kv: kv[1].get("n", 0), reverse=True)
        for k, b in items[:50]:
            mem_out[scope][k] = {
                "n": b.get("n", 0),
                "avg_hold_minutes": _avg(b, "hold_minutes"),
                "avg_captured_ratio": _avg(b, "captured_ratio"),
                "avg_giveback_ratio": _avg(b, "giveback_ratio"),
                "captured_well": b.get("captured_well", 0),
                "never_profitable": b.get("never_profitable", 0)}
    # MODÜL 12: Strategy Lab entegrasyonu — SALT OKUNUR bağlam.
    # TCP/EPP/PFS/PHI lab'de yalnız GÖLGE challenger verisi olarak
    # değerlendirilir; champion'a ve lab durumuna YAZILMAZ.
    lab_ctx = _lab_context()
    return {
        "strategy_lab": lab_ctx,
        "coverage": {
            "closed_trades": len(trades),
            "with_hold_review": len(reviewed),
            "note": ("hold_review bu görevle başladı — eski "
                     "işlemlerde yoktur (kapsam dürüstlüğü)")},
        "real_vs_variants": {
            "real_net_pnl": round(real_total, 6),
            "variants": variant_totals},
        "verdicts": verdicts,
        "memory": mem_out,
        "shadow_tail": read_shadow(50),
        "contract": {
            "phi_changes_real_trades": False,
            "champion_changed": False,
            "live_orders": "DISABLED"},
    }
