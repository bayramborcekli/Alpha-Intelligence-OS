"""PROFIT-FIRST DECISION ENGINE (PFDE) — GÖLGE katmanı.

Tek soru: "Bu işlem, komisyon çıktıktan sonra gerçek para
kazanacak mı?"

SÖZLEŞME (kabul şartları):
- PFS gerçek işlemleri DEĞİŞTİRMEZ; giriş kapısına bağlı değildir.
- Confidence'ın yerine geçmez; bağımsız, yan yana raporlanan skordur.
- LIVE ORDERS DISABLED / GLOBAL_PAUSE / champion bu modülden
  etkilenmez — burada emir yolu, config yazımı veya kapı yoktur.

Skorlar:
- TCP (Trend Continuation Probability, 0-100): hareket devam eder mi?
- EPP (Early Profit Probability, 0-100): maliyeti karşılayan İLK net
  kâr hareketi gelme ihtimali.
- PFS (Profit First Score, 0-100): maliyet-sonrası gerçek kazanç
  ihtimalinin bileşik skoru + gerekçe kodları.

Tüm hesaplar deterministik ve kural tabanlıdır; veri yoksa alan None
bırakılır ve DATA_QUALITY nedeni eklenir — UYDURMA YOK.
"""
from __future__ import annotations

import fcntl
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("profit_first")

ROOT = Path(__file__).resolve().parent
SHADOW_PATH = ROOT / "pfs_shadow.jsonl"
SHADOW_MAX_BYTES = 5 * 1024 * 1024
SHADOW_KEEP_LINES = 2000

STABLE_BASES = {"USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP",
                "EURI", "AEUR", "USD1", "XUSD"}

# PFS gerekçe kodları (yalnız gölge raporlama — gerçek kapı DEĞİL)
PFS_REASON_CODES = (
    "LOCAL_TOP_HIGH_RISK",      # 20/30/50 mum zirvesine çok yakın
    "STALE_BREAKOUT",           # kırılım taze değil
    "COST_NOT_COVERED",         # beklenen net hareket maliyeti örtmüyor
    "SPREAD_COST_HEAVY",        # spread maliyet bütçesini yiyor
    "LOW_VOL_QUALITY",          # volatilite maliyeti taşıyamaz/aşırı
    "NO_VOLUME_CONFIRMATION",   # hacim doğrulaması yok
    "BTC_MISALIGNED",           # BTC ters yönde
    "SYMBOL_HISTORY_NEGATIVE",  # sembolün gerçek geçmişi zarar
    "MODEL_HISTORY_NEGATIVE",   # modelin yakın geçmişi zarar
    "EXPECTED_GIVEBACK_HIGH",   # tarihsel kâr geri-verme oranı yüksek
    "STABLECOIN_RISK",          # stabilcoin — hareket üretmez
    "DATA_QUALITY",             # girdi eksik; skor kısmi
)


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── TCP / EPP ──────────────────────────────────────────────────────

def compute_tcp(sig: dict, klines: list[list]) -> float | None:
    """Trend devam ihtimali 0-100. Girdi yoksa None (uydurma yok)."""
    if not klines or len(klines) < 12:
        return None
    closes = [float(k[4]) for k in klines]
    ups = sum(1 for i in range(-10, 0)
              if closes[i] > closes[i - 1])
    persistence = ups / 10.0                         # 0..1
    vol_ratio = float(sig.get("vol_ratio") or 0.0)
    vol_score = _clamp((vol_ratio - 1.0) / 1.5)       # 1.0→0, 2.5→1
    mom = (closes[-1] - closes[-6]) / closes[-6] * 100 \
        if closes[-6] else 0.0
    mom_score = _clamp(mom / 0.5)                     # %0.5 mom → tam
    rsi = sig.get("rsi")
    rsi_score = 0.5
    if isinstance(rsi, (int, float)):
        # 50-70 sağlıklı devam bölgesi; >80 tükenme, <40 zayıf
        rsi_score = _clamp(1 - abs(rsi - 60) / 30)
    tcp = (persistence * 0.35 + vol_score * 0.25 +
           mom_score * 0.25 + rsi_score * 0.15) * 100
    return round(tcp, 2)


def compute_epp(tcp: float | None, expected_gross_edge_pct: float,
                round_trip_cost_pct: float) -> float | None:
    """İlk NET kâr ihtimali: beklenen hareketin maliyeti kaç kez
    örttüğü × devam ihtimali."""
    if tcp is None or round_trip_cost_pct <= 0:
        return None
    coverage = _clamp(expected_gross_edge_pct /
                      (round_trip_cost_pct * 2.0))
    return round(_clamp(tcp / 100 * coverage) * 100, 2)


# ── Yerel tepe / kırılım tazeliği ─────────────────────────────────

def local_top_profile(klines: list[list]) -> dict | None:
    """Son fiyatın 20/30/50 mum zirvesine uzaklığı (%) + tazelik."""
    if not klines or len(klines) < 21:
        return None
    closes = [float(k[4]) for k in klines]
    last = closes[-1]
    out: dict[str, Any] = {}
    for n in (20, 30, 50):
        if len(closes) >= n + 1:
            hi = max(closes[-(n + 1):-1])
            out[f"dist_high_{n}_pct"] = round(
                (hi - last) / hi * 100, 4) if hi > 0 else None
        else:
            out[f"dist_high_{n}_pct"] = None
    # Kırılım tazeliği: son kapanış hi20 üstündeyse kaç mumdur üstte?
    hi20 = max(closes[-21:-1])
    fresh = None
    if last > hi20:
        fresh = 0
        for i in range(2, min(len(closes), 20)):
            prior_hi = max(closes[-(20 + i):-i]) \
                if len(closes) >= 20 + i else None
            if prior_hi is not None and closes[-i] > prior_hi:
                fresh += 1
            else:
                break
    out["breakout_bars_above"] = fresh
    return out


# ── Sembol/model gerçek geçmişi ───────────────────────────────────

def history_stats(trades: list[dict], symbol: str,
                  model: str) -> dict:
    """Gerçek kapanmış işlemlerden sembol+model geçmişi (uydurmasız)."""
    sym = [t for t in trades if t.get("symbol") == symbol
           and t.get("net_pnl") is not None][:20]
    mdl = [t for t in trades if t.get("model") == model
           and t.get("net_pnl") is not None][:30]

    def _agg(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0, "net_sum": None, "win_rate_pct": None,
                    "avg_giveback_pct": None}
        nets = [float(t["net_pnl"]) for t in rows]
        gives = []
        for t in rows:
            mfe = t.get("mfe_pct")
            if isinstance(mfe, (int, float)) and mfe > 0.05:
                exitp, entry = t.get("exit"), t.get("entry")
                if entry and exitp:
                    captured = (float(exitp) / float(entry) - 1) * 100
                    gives.append(_clamp((mfe - captured) / mfe))
        return {"n": len(rows), "net_sum": round(sum(nets), 4),
                "win_rate_pct": round(
                    100 * sum(1 for x in nets if x > 0) / len(nets), 1),
                "avg_giveback_pct": round(
                    100 * sum(gives) / len(gives), 1) if gives else None}
    return {"symbol": _agg(sym), "model": _agg(mdl)}


# ── PFS ────────────────────────────────────────────────────────────

def score_candidate(row: dict, sig: dict, klines: list[list],
                    model: str, model_cfg: dict,
                    context: dict | None = None) -> dict:
    """Aday için gölge skor seti: confidence/TCP/EPP/PFS + nedenler.

    context: {"btc_change_pct": float|None, "trades": list[dict]}
    Emir yolu YOK; salt hesap + raporlama sözlüğü döner."""
    import dual_model as _dm
    ctx = context or {}
    reasons: list[str] = []
    comps: dict[str, float | None] = {}

    spread = float(row.get("spread_pct") or 0.0)
    slippage = spread * 0.75
    cp = _dm.cost_profile(model_cfg, slippage)
    cost = cp["round_trip_cost_pct"]
    gross = float(sig.get("expected_gross_edge_pct") or 0.0)
    net_after_cost = gross - cost

    # 1) Maliyet-sonrası beklenen hareket
    comps["cost_after_move"] = _clamp(net_after_cost / max(cost, 1e-9)
                                      / 1.5)
    if net_after_cost <= 0:
        reasons.append("COST_NOT_COVERED")

    # 2-3) Devam + ilk net kâr ihtimali
    tcp = compute_tcp(sig, klines)
    epp = compute_epp(tcp, gross, cost)
    comps["continuation"] = (tcp / 100) if tcp is not None else None
    comps["early_profit"] = (epp / 100) if epp is not None else None

    # 4-5) Yerel tepe + kırılım tazeliği
    lt = local_top_profile(klines)
    if lt:
        d20 = lt.get("dist_high_20_pct")
        # Zirveye <%0.15 mesafe = yüksek yerel tepe riski
        if isinstance(d20, (int, float)):
            comps["local_top"] = _clamp(d20 / 0.5)
            if d20 < 0.15:
                reasons.append("LOCAL_TOP_HIGH_RISK")
        fresh = lt.get("breakout_bars_above")
        if fresh is not None:
            comps["breakout_freshness"] = _clamp(1 - fresh / 8)
            if fresh >= 8:
                reasons.append("STALE_BREAKOUT")
    else:
        reasons.append("DATA_QUALITY")

    # 6) Göreli güç + BTC hizası + rejim
    btc = ctx.get("btc_change_pct")
    chg = float(row.get("change_pct") or 0.0)
    if isinstance(btc, (int, float)):
        comps["relative_strength"] = _clamp((chg - btc) / 3 + 0.5)
        comps["btc_alignment"] = 1.0 if btc >= 0 else 0.0
        if btc < -0.5:
            reasons.append("BTC_MISALIGNED")
    else:
        comps["relative_strength"] = None
        comps["btc_alignment"] = None

    # 7) Volatilite kalitesi: maliyeti taşıyacak kadar var, SL'yi
    # gürültüyle vuracak kadar aşırı değil
    volat = float(row.get("volatility_pct") or 0.0)
    lo, hi = cost * 8, cost * 60
    comps["volatility_quality"] = _clamp(
        (volat - lo) / max(lo, 1e-9)) if volat < lo * 2 else \
        (_clamp(1 - (volat - hi) / hi) if volat > hi else 1.0)
    if volat < lo:
        reasons.append("LOW_VOL_QUALITY")

    # 8) Spread / likidite / hacim doğrulaması
    max_spread = float(model_cfg.get("max_spread_pct") or 0.05)
    comps["spread"] = _clamp(1 - spread / max(max_spread, 1e-9))
    if spread > cost * 0.5:
        reasons.append("SPREAD_COST_HEAVY")
    min_vol = float(model_cfg.get("min_volume_usdt") or 1)
    comps["liquidity"] = _clamp(
        math.log10(max(float(row.get("volume_usdt") or 1), 1) /
                   min_vol) / 1.0 + 0.5)
    vr = float(sig.get("vol_ratio") or 0.0)
    comps["volume_confirmation"] = _clamp((vr - 1.0) / 1.0)
    if vr < 1.2:
        reasons.append("NO_VOLUME_CONFIRMATION")

    # 9) Sembol/model gerçek geçmişi + beklenen geri-verme
    hs = history_stats(ctx.get("trades") or [], row.get("symbol", ""),
                       model)
    for key, code in (("symbol", "SYMBOL_HISTORY_NEGATIVE"),
                      ("model", "MODEL_HISTORY_NEGATIVE")):
        st = hs[key]
        if st["n"] >= 5 and st["net_sum"] is not None:
            comps[f"{key}_history"] = 1.0 if st["net_sum"] > 0 else \
                _clamp(0.5 + st["net_sum"] / 5)
            if st["net_sum"] < 0:
                reasons.append(code)
        else:
            comps[f"{key}_history"] = None
    gb = hs["model"]["avg_giveback_pct"]
    if gb is not None:
        comps["expected_giveback"] = _clamp(1 - gb / 100)
        if gb > 70:
            reasons.append("EXPECTED_GIVEBACK_HIGH")
    else:
        comps["expected_giveback"] = None

    # 10) Stablecoin riski
    base = str(row.get("symbol") or "").removesuffix("USDT")
    if base in STABLE_BASES:
        comps["stablecoin"] = 0.0
        reasons.append("STABLECOIN_RISK")
    else:
        comps["stablecoin"] = 1.0

    # Bileşik PFS: mevcut bileşenlerin ağırlıklı ortalaması.
    weights = {
        "cost_after_move": 2.0, "early_profit": 2.0,
        "continuation": 1.5, "local_top": 1.5,
        "breakout_freshness": 1.0, "relative_strength": 0.5,
        "btc_alignment": 0.75, "volatility_quality": 1.0,
        "spread": 0.75, "liquidity": 0.5,
        "volume_confirmation": 1.0, "symbol_history": 1.0,
        "model_history": 0.75, "expected_giveback": 0.75,
        "stablecoin": 1.0,
    }
    num = den = 0.0
    missing = 0
    for k, w in weights.items():
        v = comps.get(k)
        if v is None:
            missing += 1
            continue
        num += v * w
        den += w
    pfs = round(num / den * 100, 2) if den else None
    if missing and "DATA_QUALITY" not in reasons and \
            missing >= len(weights) // 3:
        reasons.append("DATA_QUALITY")

    return {
        "at": _now_iso(), "symbol": row.get("symbol"),
        "model": model, "confidence": sig.get("confidence"),
        "tcp": tcp, "epp": epp, "pfs": pfs,
        "components": {k: (round(v, 4) if isinstance(v, float)
                           else v) for k, v in comps.items()},
        "reasons": reasons, "local_top": lt,
        "cost_pct": cost, "expected_gross_edge_pct": gross,
        "history": hs,
    }


# ── Gölge kalıcılığı (append-only, flock, sınırlı büyüme) ─────────

def append_shadow(record: dict, path: Path = SHADOW_PATH) -> bool:
    """Gölge skoru dosyaya ekle; dosya sınırı aşınca eski kayıtları
    kırp (flock altında). Yazılamazsa False — sessiz uydurma yok."""
    try:
        with open(path, "a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(record, ensure_ascii=False,
                               separators=(",", ":")) + "\n")
            f.flush()
            if f.tell() > SHADOW_MAX_BYTES:
                f.seek(0)
                lines = f.read().splitlines()[-SHADOW_KEEP_LINES:]
                tmp = str(path) + ".tmp"
                with open(tmp, "w", encoding="utf-8") as g:
                    g.write("\n".join(lines) + "\n")
                os.replace(tmp, path)
        return True
    except OSError as exc:
        log.error("pfs_shadow yazılamadı: %s", exc)
        return False


def read_shadow(limit: int = 1000,
                path: Path = SHADOW_PATH) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()[-limit:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except OSError:
        return []


# ── İstatistik (deterministik, saf python) ────────────────────────

def roc_auc(scores: list[float], labels: list[int]) -> float | None:
    """Rank tabanlı AUC (Mann-Whitney). Tek sınıf varsa None."""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return round((wins + ties * 0.5) / (len(pos) * len(neg)), 4)


def brier(scores01: list[float], labels: list[int]) -> float | None:
    if not scores01:
        return None
    return round(sum((s - y) ** 2 for s, y in
                     zip(scores01, labels)) / len(scores01), 4)


def precision_recall(scores: list[float], labels: list[int],
                     threshold: float) -> dict:
    tp = sum(1 for s, y in zip(scores, labels)
             if s >= threshold and y == 1)
    fp = sum(1 for s, y in zip(scores, labels)
             if s >= threshold and y == 0)
    fn = sum(1 for s, y in zip(scores, labels)
             if s < threshold and y == 1)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    return {"threshold": threshold,
            "precision": round(prec, 4) if prec is not None else None,
            "recall": round(rec, 4) if rec is not None else None,
            "selected": tp + fp}


def calibration(scores: list[float], labels: list[int],
                buckets: int = 5) -> list[dict]:
    out = []
    for b in range(buckets):
        lo, hi = b * 100 / buckets, (b + 1) * 100 / buckets
        idx = [i for i, s in enumerate(scores)
               if lo <= s < hi or (b == buckets - 1 and s == 100)]
        if not idx:
            out.append({"bucket": f"{lo:.0f}-{hi:.0f}", "n": 0,
                        "predicted_pct": None, "actual_pct": None})
            continue
        out.append({
            "bucket": f"{lo:.0f}-{hi:.0f}", "n": len(idx),
            "predicted_pct": round(
                sum(scores[i] for i in idx) / len(idx), 1),
            "actual_pct": round(
                100 * sum(labels[i] for i in idx) / len(idx), 1)})
    return out


def predictor_stats(scores: list[float | None],
                    labels: list[int], name: str,
                    threshold: float = 60.0) -> dict:
    pairs = [(s, y) for s, y in zip(scores, labels) if s is not None]
    if len(pairs) < 5:
        return {"predictor": name, "n": len(pairs),
                "note": "yetersiz örneklem — kanıt yok"}
    s = [p[0] for p in pairs]
    y = [p[1] for p in pairs]
    return {"predictor": name, "n": len(pairs),
            "roc_auc": roc_auc(s, y),
            "brier": brier([v / 100 for v in s], y),
            **precision_recall(s, y, threshold),
            "calibration": calibration(s, y)}


# ── Never-profitable + rapor ──────────────────────────────────────

def classify_never_profitable(t: dict, cost_pct: float) -> dict | None:
    """İşlem HİÇ net kâra ulaşmadıysa nedeni grupla (kayıttan kanıtla
    ayrılabilen gruplar; giriş bağlamı olmayan nedenler PROSPEKTİF)."""
    net = t.get("net_pnl")
    mfe = t.get("mfe_pct")
    if net is None or float(net) > 0:
        return None
    if not isinstance(mfe, (int, float)):
        return {"cause": "DATA_QUALITY", "net_pnl": net,
                "note": "MFE kaydı yok — neden kanıtlanamaz"}
    mae = t.get("mae_pct")
    if mfe <= cost_pct:
        if mfe <= 0.02:
            cause = "NO_FAVORABLE_MOVE"   # trend ölümü / geç giriş
        else:
            cause = "FEE_SPREAD_DOMINANT"  # hareket maliyeti örtmedi
    else:
        cause = "PROFIT_GIVEBACK"          # net kâra değdi, geri verdi
    if isinstance(mae, (int, float)) and mae > mfe * 2 and \
            cause == "NO_FAVORABLE_MOVE":
        cause = "WRONG_TIMING"             # önce ters hareket
    return {"cause": cause, "mfe_pct": mfe, "mae_pct": mae,
            "net_pnl": net}


def prevention_rule(cause: str) -> str:
    return {
        "NO_FAVORABLE_MOVE": "Giriş anında devam kanıtı yoktu — TCP "
        "düşük adaylar gölgede işaretlenir (STALE_BREAKOUT / "
        "NO_VOLUME_CONFIRMATION).",
        "FEE_SPREAD_DOMINANT": "Beklenen hareket maliyeti örtmüyordu "
        "— COST_NOT_COVERED nedeni giriş öncesi görünür.",
        "PROFIT_GIVEBACK": "Kâr yakalanamadı — EXPECTED_GIVEBACK_HIGH "
        "bileşeni ve erken-pencere izleri (30/60/90/180 sn) bunu "
        "önceden ölçer.",
        "WRONG_TIMING": "Önce ters hareket — LOCAL_TOP_HIGH_RISK ve "
        "BTC_MISALIGNED nedenleri zamanlama riskini işaretler.",
        "DATA_QUALITY": "Kanıt eksik — MFE/MAE izleme zorunlu kalır.",
    }.get(cause, "")


def build_report(trades: list[dict], cost_pct_default: float = 0.23,
                 shadow_limit: int = 1000) -> dict:
    """FAZ 0/2/4/5/6/7 raporu — yalnız GERÇEK kayıtlardan.

    Tarihsel işlemlerde TCP/EPP/PFS yoktur (gölge bu görevle başladı);
    bu dürüstçe 'coverage' alanlarında raporlanır."""
    closed = [t for t in trades if t.get("net_pnl") is not None]
    labels = [1 if float(t["net_pnl"]) > 0 else 0 for t in closed]

    # FAZ 0 — Confidence analizi
    buckets: dict[int, list] = {}
    for t in closed:
        c = t.get("confidence")
        if isinstance(c, (int, float)):
            buckets.setdefault(int(c // 10 * 10), []).append(t)
    conf_rows = []
    for k in sorted(buckets):
        g = buckets[k]
        nets = [float(t["net_pnl"]) for t in g]
        mfes = [t["mfe_pct"] for t in g
                if isinstance(t.get("mfe_pct"), (int, float))]
        maes = [t["mae_pct"] for t in g
                if isinstance(t.get("mae_pct"), (int, float))]
        conf_rows.append({
            "confidence_bucket": f"{k}-{k + 9}", "n": len(g),
            "net_pnl_sum": round(sum(nets), 4),
            "win_rate_pct": round(
                100 * sum(1 for x in nets if x > 0) / len(nets), 1),
            "avg_mfe_pct": round(sum(mfes) / len(mfes), 4)
            if mfes else None,
            "avg_mae_pct": round(sum(maes) / len(maes), 4)
            if maes else None})

    # FAZ 2 — Tahmincilerin karşılaştırması (mevcut alanlarla)
    conf_scores = [t.get("confidence") for t in closed]
    edge_scores = [min(max((t.get("net_edge_pct") or 0) * 100, 0), 100)
                   if t.get("net_edge_pct") is not None else None
                   for t in closed]
    sh = [t.get("shadow_scores") or {} for t in closed]
    predictors = [
        predictor_stats(conf_scores, labels, "confidence"),
        predictor_stats(edge_scores, labels,
                        "net_edge_pct_x100 (EPP-öncülü)"),
        predictor_stats([s.get("tcp") for s in sh], labels, "TCP"),
        predictor_stats([s.get("epp") for s in sh], labels, "EPP"),
        predictor_stats([s.get("pfs") for s in sh], labels, "PFS"),
    ]

    # FAZ 4 — erken pencere (early_marks alanı olan işlemler)
    early = [t for t in closed if isinstance(t.get("early_marks"),
                                             dict)]
    early_rows = []
    for sec in ("30", "60", "90", "180"):
        rows = [(t["early_marks"][sec],
                 1 if float(t["net_pnl"]) > 0 else 0)
                for t in early if sec in t["early_marks"]]
        if not rows:
            early_rows.append({"window_sec": int(sec), "n": 0})
            continue
        pos_mfe = [m.get("mfe", 0) for m, y in rows if y == 1]
        neg_mfe = [m.get("mfe", 0) for m, y in rows if y == 0]
        early_rows.append({
            "window_sec": int(sec), "n": len(rows),
            "winner_avg_mfe_pct": round(
                sum(pos_mfe) / len(pos_mfe), 4) if pos_mfe else None,
            "loser_avg_mfe_pct": round(
                sum(neg_mfe) / len(neg_mfe), 4) if neg_mfe else None})
    short = [t for t in closed
             if isinstance(t.get("hold_minutes"), (int, float))
             and t["hold_minutes"] <= 3]
    early_proxy = {
        "n": len(short),
        "win_rate_pct": round(100 * sum(
            1 for t in short if float(t["net_pnl"]) > 0) /
            len(short), 1) if short else None,
        "note": "hold<=3dk işlemler — tüm ömrü ilk 3 dk içinde"}

    # FAZ 5 — yerel tepe (gölge local_top verisi taşıyan işlemler)
    lt_rows = []
    lt_trades = [(t, (t.get("shadow_scores") or {}).get("local_top"))
                 for t in closed]
    lt_trades = [(t, lt) for t, lt in lt_trades if isinstance(lt, dict)]
    for n in (20, 30, 50):
        key = f"dist_high_{n}_pct"
        near = [(t, lt) for t, lt in lt_trades
                if isinstance(lt.get(key), (int, float))
                and lt[key] < 0.15]
        far = [(t, lt) for t, lt in lt_trades
               if isinstance(lt.get(key), (int, float))
               and lt[key] >= 0.15]

        def _wr(rows):
            return round(100 * sum(
                1 for t, _ in rows if float(t["net_pnl"]) > 0) /
                len(rows), 1) if rows else None
        lt_rows.append({"lookback": n, "near_top_n": len(near),
                        "near_top_win_rate_pct": _wr(near),
                        "away_n": len(far),
                        "away_win_rate_pct": _wr(far)})

    # FAZ 6/7 — never profitable + önleme kuralı
    np_groups: dict[str, list] = {}
    for t in closed:
        c = classify_never_profitable(t, cost_pct_default)
        if c:
            np_groups.setdefault(c["cause"], []).append(c)
    never_profitable = [
        {"cause": k, "n": len(v),
         "net_pnl_sum": round(sum(float(x["net_pnl"]) for x in v
                                  if x.get("net_pnl") is not None), 4),
         "prevention": prevention_rule(k)}
        for k, v in sorted(np_groups.items(),
                           key=lambda kv: -len(kv[1]))]

    shadow_records = read_shadow(shadow_limit)
    return {
        "generated_at": _now_iso(),
        "closed_trades": len(closed),
        "confidence_analysis": conf_rows,
        "predictor_comparison": predictors,
        "early_window": {"marks": early_rows,
                         "short_hold_proxy": early_proxy,
                         "coverage_n": len(early)},
        "local_top": {"rows": lt_rows,
                      "coverage_n": len(lt_trades)},
        "never_profitable": never_profitable,
        "shadow": {
            "recorded_candidates": len(shadow_records),
            "trades_with_shadow_scores": sum(
                1 for s in sh if s.get("pfs") is not None)},
        "contract": {
            "pfs_changes_real_trades": False,
            "live_orders": "DISABLED",
            "champion_changed": False,
        },
        "notes": [
            "TCP/EPP/PFS gölge skorları bu görevle kayda başladı; "
            "tarihsel işlemlerde yoktur ve UYDURULMAZ.",
            "Yerel tepe ve erken-pencere kanıtı, gölge alanlı yeni "
            "işlemler biriktikçe otomatik dolar.",
        ],
    }
