"""dual_learning.py — Mevcut öğrenme motorunun DUAL-MODEL köprüsü.

Paralel ikinci bir optimizer DEĞİLDİR: learning_engine.run_learning_update
akışından ve auto_controller döngüsünden çağrılır; ayrı scheduler yoktur.

Döngü (model başına, CORE ve OPPORTUNITY tamamen ayrı):
  dual_model kapanan PAPER işlemleri → doğrulama/dedupe → metrikler
  → teşhis (reason code + kanıt) → kontrollü challenger önerisi
  → gölge (shadow) değerlendirme → terfi (varsayılan: kullanıcı onayı)
  → sonraki girişlerde champion overlay → kötüleşmede rollback.

Güvenlik sözleşmeleri:
- LIVE ORDERS DISABLED — bu modül hiçbir emir yolu açamaz.
- Yalnız LEARNABLE_BOUNDS izin listesindeki config alanları değişebilir;
  risk tavanları, API izinleri, kill-switch, kimlik katmanı ASLA.
- Tur başına en fazla 3 parametre, parametre başına en fazla %10 adım,
  mutlak min/maks sınırlar sabit.
- Yetersiz örneklemde (varsayılan 20/50/75) hiçbir parametre değişmez.
- Tüm state git dışı, flock'lu, atomic-replace ile yazılır.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
except ImportError:  # Windows
    import portable_flock as fcntl  # type: ignore

log = logging.getLogger("dual_learning")

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "dual_learning_state.json"
HISTORY_PATH = ROOT / "dual_learning_history.jsonl"
RUNTIME_PATH = ROOT / "dual_model_runtime.json"

MODEL_CORE = "ALPHA_CORE_SCALP"
MODEL_OPP = "ALPHA_OPPORTUNITY_BURST"
MODELS = (MODEL_CORE, MODEL_OPP)
_SECTION = {MODEL_CORE: "core", MODEL_OPP: "opportunity"}

# ── Öğrenilebilir parametreler (izin listesi + mutlak sınırlar) ─────
# dual_model config anahtarı → (min, max). Bu tablo dışındaki HİÇBİR
# alan öğrenme tarafından değiştirilemez (güvenlik tavanları, LIVE
# kilidi, API izinleri, hesap bağlantıları kapsam DIŞI).
LEARNABLE_BOUNDS: dict[str, tuple[float, float]] = {
    "min_confidence":   (40.0, 90.0),
    "tp_pct":           (0.20, 3.00),
    "sl_pct":           (0.15, 2.00),
    "trailing_pct":     (0.10, 1.50),
    "max_hold_minutes": (5.0, 120.0),
    "cooldown_minutes": (5.0, 120.0),
    "max_spread_pct":   (0.01, 0.50),
    "max_slippage_pct": (0.01, 0.30),
}
MAX_PARAMS_PER_ROUND = 3
MAX_STEP_PCT = 10.0           # tek turda parametre başına en fazla %10
PARAM_COOLDOWN_HOURS = 24.0   # aynı parametre bu süre dolmadan değişmez

DEFAULT_THRESHOLDS = {
    "min_trades_diagnosis": 20,
    "min_trades_proposal": 50,
    "min_trades_promotion": 75,
    "min_new_trades_trigger": 25,
    "interval_hours": 24.0,
    "rollback_min_trades": 10,
    "rollback_max_drawdown_usdt": 25.0,
}

DIAGNOSIS_CODES = (
    "INSUFFICIENT_DATA", "FEE_DRAG_DOMINANT", "SLIPPAGE_DRAG_DOMINANT",
    "NEGATIVE_EXPECTANCY", "TP_TOO_SMALL", "STOP_TOO_TIGHT",
    "EXCESSIVE_TRADING", "LOW_QUALITY_ENTRIES", "HOLD_TIME_TOO_SHORT",
    "SYMBOL_CONCENTRATION", "LOW_NET_EDGE", "HEALTHY")

PROMOTION_CODES = (
    "PROMOTED", "REJECTED_NO_IMPROVEMENT", "REJECTED_DRAWDOWN",
    "REJECTED_FEE_DRAG", "REJECTED_CONCENTRATION",
    "REJECTED_INSUFFICIENT_EVIDENCE", "REJECTED_DATA_QUALITY",
    "NOT_EVALUATED", "NO_CHALLENGER")

ROLLBACK_CODES = ("PERFORMANCE_DEGRADATION", "DRAWDOWN_BREACH",
                  "NEGATIVE_EXPECTANCY")


# ── Git dışı state store (flock + atomic replace) ──────────────────

def _load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            with STATE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _update_state(mutator: Callable[[dict], None]) -> dict[str, Any]:
    lock_path = STATE_PATH.with_suffix(".lock")
    with lock_path.open("a+") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            data = _load_state()
            mutator(data)
            tmp = STATE_PATH.with_name(
                f".{STATE_PATH.name}.{os.getpid()}."
                f"{threading.get_ident()}.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            tmp.replace(STATE_PATH)
            return data
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)


def _append_history(record: dict[str, Any]) -> None:
    lock_path = STATE_PATH.with_suffix(".lock")
    with lock_path.open("a+") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            with HISTORY_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_state(state: dict, model: str) -> dict:
    ms = state.setdefault("models", {}).setdefault(model, {})
    ms.setdefault("champion", {"version": "BASE", "overrides": {},
                               "promoted_at": None, "previous": None})
    ms.setdefault("challenger", None)
    ms.setdefault("processed_ids", [])
    ms.setdefault("param_last_changed", {})
    ms.setdefault("promotion_history", [])
    ms.setdefault("rollback_history", [])
    ms.setdefault("metrics", None)
    ms.setdefault("diagnosis", None)
    return ms


# ── 1) Kanonik veri alımı: dual_model kapanışları → dataset ────────

def _trade_id(t: dict) -> str:
    existing = t.get("trade_id")
    if existing:
        return str(existing)
    raw = "|".join(str(t.get(k, "")) for k in
                   ("symbol", "model", "opened_at", "closed_at",
                    "entry", "exit"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _valid_trade(t: dict) -> bool:
    """Bozuk/eksik kayıt öğrenmeye ALINMAZ."""
    if not isinstance(t, dict) or t.get("model") not in MODELS:
        return False
    if t.get("execution_mode") != "PAPER":
        return False
    try:
        entry, exitp = float(t["entry"]), float(t["exit"])
        float(t["net_pnl"]); float(t["fees"]); float(t["slippage"])
        float(t["gross_pnl"]); float(t["quantity"])
    except (KeyError, TypeError, ValueError):
        return False
    return entry > 0 and exitp > 0 and bool(t.get("closed_at"))


def normalize_trade(t: dict) -> dict:
    """Ortak normalize edilmiş trade record şeması (kanonik katman)."""
    return {
        "trade_id": _trade_id(t),
        "model_name": t["model"],
        "strategy_name": t["model"],
        "symbol": t.get("symbol"),
        "side": t.get("side"),
        "entry_time": t.get("opened_at"),
        "exit_time": t.get("closed_at"),
        "entry_price": float(t["entry"]),
        "exit_price": float(t["exit"]),
        "quantity": float(t["quantity"]),
        "notional": float(t.get("notional_usdt") or
                          float(t["entry"]) * float(t["quantity"])),
        "gross_pnl": float(t["gross_pnl"]),
        "fees": float(t["fees"]),
        "slippage": float(t["slippage"]),
        "net_pnl": float(t["net_pnl"]),
        "exit_reason": t.get("result"),
        "signal_type": t.get("signal_type"),
        "confidence": t.get("confidence"),
        "expected_edge": t.get("net_edge_pct"),
        "market_regime": t.get("market_regime"),
        "spread": t.get("spread_pct"),
        "volatility": t.get("volatility_pct"),
        "volume_ratio": t.get("vol_ratio"),
        "hold_duration": t.get("hold_minutes"),
        "configuration_version": t.get("config_version", "BASE"),
        "learning_version": 1,
    }


def _read_runtime_trades() -> list[dict]:
    try:
        if RUNTIME_PATH.exists():
            with RUNTIME_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            trades = data.get("trades") if isinstance(data, dict) else None
            if isinstance(trades, list):
                return trades
    except (OSError, json.JSONDecodeError):
        pass
    return []


def ingest_closed_trades() -> dict[str, int]:
    """Yeni kapanan dual-model işlemlerini kanonik dataset'e al.

    Duplicate engeli: trade_id model başına processed_ids'te tutulur.
    CORE ve OPPORTUNITY kayıtları KARIŞTIRILMAZ (model_name etiketi +
    ayrı sayaç/işleme)."""
    ingested = {m: 0 for m in MODELS}
    new_records: list[dict] = []

    def _mut(state: dict) -> None:
        for t in reversed(_read_runtime_trades()):  # eski → yeni
            if not _valid_trade(t):
                continue
            ms = _model_state(state, t["model"])
            tid = _trade_id(t)
            if tid in ms["processed_ids"]:
                continue
            rec = normalize_trade(t)
            new_records.append(rec)
            ms["processed_ids"].append(tid)
            ms["processed_ids"] = ms["processed_ids"][-4000:]
            ms.setdefault("dataset", []).append(rec)
            ms["dataset"] = ms["dataset"][-2000:]
            ingested[t["model"]] += 1

    _update_state(_mut)
    for rec in new_records:
        _append_history({"type": "TRADE_INGESTED", "at": _now_iso(),
                         **rec})
    return ingested


# ── 2) Model bazlı metrikler ────────────────────────────────────────

def compute_model_metrics(records: list[dict]) -> dict[str, Any]:
    n = len(records)
    if n == 0:
        return {"closed_trades": 0}
    wins = [r for r in records if r["net_pnl"] > 0]
    losses = [r for r in records if r["net_pnl"] <= 0]
    gross = sum(r["gross_pnl"] for r in records)
    net = sum(r["net_pnl"] for r in records)
    fees = sum(r["fees"] for r in records)
    slip = sum(r["slippage"] for r in records)
    win_sum = sum(r["net_pnl"] for r in wins)
    loss_sum = abs(sum(r["net_pnl"] for r in losses))
    holds = [r["hold_duration"] for r in records
             if isinstance(r.get("hold_duration"), (int, float))]
    # Max drawdown (kümülatif net PnL üzerinden, kronolojik)
    peak = dd = cum = 0.0
    consec = max_consec = 0
    for r in records:
        cum += r["net_pnl"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        if r["net_pnl"] <= 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    by_symbol: dict[str, dict] = {}
    by_exit: dict[str, int] = {}
    for r in records:
        s = by_symbol.setdefault(r["symbol"], {"n": 0, "net": 0.0})
        s["n"] += 1
        s["net"] = round(s["net"] + r["net_pnl"], 6)
        by_exit[r.get("exit_reason") or "?"] = \
            by_exit.get(r.get("exit_reason") or "?", 0) + 1
    top_share = max(v["n"] for v in by_symbol.values()) / n
    return {
        "closed_trades": n,
        "win_rate": round(len(wins) / n, 4),
        "gross_pnl": round(gross, 6),
        "net_pnl": round(net, 6),
        "expectancy_per_trade": round(net / n, 6),
        "profit_factor": round(win_sum / loss_sum, 4)
        if loss_sum > 0 else (float("inf") if win_sum > 0 else 0.0),
        "average_win": round(win_sum / len(wins), 6) if wins else 0.0,
        "average_loss": round(-loss_sum / len(losses), 6)
        if losses else 0.0,
        "win_loss_ratio": round(
            (win_sum / len(wins)) / (loss_sum / len(losses)), 4)
        if wins and losses and loss_sum else None,
        "maximum_drawdown": round(dd, 6),
        "fee_drag": round(fees, 6),
        "slippage_drag": round(slip, 6),
        "average_hold_duration": round(sum(holds) / len(holds), 2)
        if holds else None,
        "median_hold_duration": round(statistics.median(holds), 2)
        if holds else None,
        "exit_reason_distribution": by_exit,
        "symbol_performance": by_symbol,
        "top_symbol_share": round(top_share, 4),
        "consecutive_losses": max_consec,
    }


# ── 3) Teşhis motoru (açıklanabilir reason code + kanıt) ───────────

def diagnose(metrics: dict, thresholds: dict) -> dict[str, Any]:
    n = metrics.get("closed_trades", 0)
    ev = {"sample_size": n}
    if n < thresholds["min_trades_diagnosis"]:
        return {"code": "INSUFFICIENT_DATA", "evidence": ev,
                "confidence": "NONE",
                "note": f"{n}/{thresholds['min_trades_diagnosis']} "
                        "kapanan işlem — teşhis için yetersiz."}
    conf = ("HIGH" if n >= 100 else
            "MEDIUM" if n >= 50 else "LOW")
    codes: list[dict] = []
    gross, net = metrics["gross_pnl"], metrics["net_pnl"]
    fees, slip = metrics["fee_drag"], metrics["slippage_drag"]
    exits = metrics.get("exit_reason_distribution", {})
    if gross > 0 and fees >= gross * 0.6:
        codes.append({"code": "FEE_DRAG_DOMINANT",
                      "affected_parameter": "tp_pct",
                      "evidence": {**ev, "fees": fees, "gross": gross},
                      "expected_improvement":
                          "TP genişletme fee oranını düşürür",
                      "risk_note": "Daha uzun bekleme riski"})
    if gross > 0 and slip >= gross * 0.3:
        codes.append({"code": "SLIPPAGE_DRAG_DOMINANT",
                      "affected_parameter": "max_slippage_pct",
                      "evidence": {**ev, "slippage": slip,
                                   "gross": gross},
                      "expected_improvement": "Daha sıkı kayma limiti",
                      "risk_note": "Daha az işlem fırsatı"})
    if metrics["expectancy_per_trade"] < 0:
        codes.append({"code": "NEGATIVE_EXPECTANCY",
                      "affected_parameter": "min_confidence",
                      "evidence": {**ev, "expectancy":
                                   metrics["expectancy_per_trade"]},
                      "expected_improvement":
                          "Daha seçici giriş beklentiyi yükseltir",
                      "risk_note": "İşlem sayısı düşer"})
    sl_share = exits.get("SL", 0) / n
    if sl_share >= 0.45:
        codes.append({"code": "STOP_TOO_TIGHT",
                      "affected_parameter": "sl_pct",
                      "evidence": {**ev, "sl_share": round(sl_share, 3)},
                      "expected_improvement":
                          "Stop genişletme erken kesilmeyi azaltır",
                      "risk_note": "Kayıp başına zarar artar"})
    tp_share = exits.get("TP", 0) / n
    if tp_share >= 0.5 and net <= 0:
        codes.append({"code": "TP_TOO_SMALL",
                      "affected_parameter": "tp_pct",
                      "evidence": {**ev, "tp_share": round(tp_share, 3),
                                   "net": net},
                      "expected_improvement":
                          "TP küçük; kazançlar maliyeti karşılamıyor",
                      "risk_note": "TP'ye ulaşma oranı düşebilir"})
    time_share = exits.get("TIME_EXIT", 0) / n
    if time_share >= 0.5:
        codes.append({"code": "HOLD_TIME_TOO_SHORT",
                      "affected_parameter": "max_hold_minutes",
                      "evidence": {**ev,
                                   "time_exit_share": round(time_share, 3)},
                      "expected_improvement":
                          "Süre dolmadan hedefe ulaşamıyor",
                      "risk_note": "Sermaye daha uzun bağlanır"})
    if metrics.get("top_symbol_share", 0) >= 0.6:
        codes.append({"code": "SYMBOL_CONCENTRATION",
                      "affected_parameter": None,
                      "evidence": {**ev, "top_symbol_share":
                                   metrics["top_symbol_share"]},
                      "expected_improvement":
                          "Tek sembole aşırı bağımlılık — kanıt zayıf",
                      "risk_note": "Öneriler tek sembol etkisinde"})
    if metrics["win_rate"] < 0.35 and n >= 30:
        codes.append({"code": "LOW_QUALITY_ENTRIES",
                      "affected_parameter": "min_confidence",
                      "evidence": {**ev, "win_rate":
                                   metrics["win_rate"]},
                      "expected_improvement":
                          "Güven eşiğini yükseltmek kaliteyi artırır",
                      "risk_note": "İşlem sıklığı düşer"})
    if not codes:
        return {"code": "HEALTHY", "evidence": ev, "confidence": conf,
                "note": "Belirgin sorun tespit edilmedi.",
                "findings": []}
    return {"code": codes[0]["code"], "confidence": conf,
            "evidence": codes[0]["evidence"], "findings": codes,
            "note": None}


# ── 4) Kontrollü challenger önerisi ────────────────────────────────

def _clamped_step(current: float, target: float,
                  key: str) -> float:
    lo, hi = LEARNABLE_BOUNDS[key]
    max_step = abs(current) * (MAX_STEP_PCT / 100.0) or 0.01
    if target > current:
        val = min(target, current + max_step)
    else:
        val = max(target, current - max_step)
    return round(min(max(val, lo), hi), 6)


def propose_challenger(model: str, metrics: dict, diagnosis: dict,
                       current_cfg: dict, ms: dict,
                       thresholds: dict) -> dict | None:
    """Teşhisten en fazla 3 parametrelik, %10 adımlı öneri üret."""
    n = metrics.get("closed_trades", 0)
    if n < thresholds["min_trades_proposal"]:
        return None
    if diagnosis["code"] in ("INSUFFICIENT_DATA", "HEALTHY"):
        return None
    if metrics.get("top_symbol_share", 0) >= 0.6:
        return None  # tek sembol/işleme aşırı uyum engeli
    now = time.time()
    overrides: dict[str, float] = {}
    changes: list[dict] = []
    for f in diagnosis.get("findings", []):
        if len(overrides) >= MAX_PARAMS_PER_ROUND:
            break
        key = f.get("affected_parameter")
        if key not in LEARNABLE_BOUNDS or key in overrides:
            continue
        last = ms["param_last_changed"].get(key, 0)
        if now - last < PARAM_COOLDOWN_HOURS * 3600:
            continue
        cur = float(current_cfg.get(key,
                                    LEARNABLE_BOUNDS[key][0]))
        direction = {"FEE_DRAG_DOMINANT": 1.10, "TP_TOO_SMALL": 1.10,
                     "STOP_TOO_TIGHT": 1.10,
                     "HOLD_TIME_TOO_SHORT": 1.10,
                     "NEGATIVE_EXPECTANCY": 1.08,
                     "LOW_QUALITY_ENTRIES": 1.08,
                     "SLIPPAGE_DRAG_DOMINANT": 0.90}.get(f["code"])
        if direction is None:
            continue
        new = _clamped_step(cur, cur * direction, key)
        if abs(new - cur) < 1e-9:
            continue
        overrides[key] = new
        changes.append({"parameter": key, "old": cur, "new": new,
                        "reason": f["code"]})
    if not overrides:
        return None
    version = f"{_SECTION[model].upper()}_CHALLENGER_" + \
        datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return {"version": version, "overrides": overrides,
            "changes": changes, "created_at": _now_iso(),
            "diagnosis_code": diagnosis["code"],
            "shadow": None, "status": "SHADOW"}


# ── 5) Gölge değerlendirme (dürüst: yalnız giriş kapısı alt kümesi) ─

def shadow_evaluate(challenger: dict, records: list[dict]) -> dict:
    """Challenger'ın GİRİŞ kapılarını (min_confidence) champion'ın
    gerçekleşmiş işlemlerine uygular; alt kümenin GERÇEK sonuçlarını
    ölçer. Çıkış parametreleri (TP/SL) kayıttan dürüstçe yeniden
    oynatılamaz — bu sınır shadow sonucunda açıkça işaretlenir."""
    ov = challenger["overrides"]
    subset = records
    if "min_confidence" in ov:
        subset = [r for r in records
                  if (r.get("confidence") or 0) >= ov["min_confidence"]]
    m = compute_model_metrics(subset)
    exit_keys = {"tp_pct", "sl_pct", "trailing_pct",
                 "max_hold_minutes"}
    return {
        "evaluated_at": _now_iso(),
        "method": "GATE_SUBSET_REPLAY",
        "sample": m.get("closed_trades", 0),
        "metrics": m,
        "exit_params_replayable": not (set(ov) & exit_keys),
        "note": ("Çıkış parametreleri kayıttan yeniden oynatılamaz; "
                 "yalnız giriş kapısı alt kümesi gerçek sonuçlarla "
                 "ölçüldü." if set(ov) & exit_keys else
                 "Alt küme gerçek kapanış sonuçlarıyla ölçüldü."),
    }


def evaluate_promotion(ms: dict, thresholds: dict) -> dict:
    """Terfi hazırlığı — karar kodu üretir; champion'a YAZMAZ."""
    ch = ms.get("challenger")
    if not ch:
        return {"code": "NO_CHALLENGER"}
    sh = ch.get("shadow")
    if not sh:
        return {"code": "NOT_EVALUATED"}
    champ_m = ms.get("metrics") or {}
    n = sh.get("sample", 0)
    if n < thresholds["min_trades_promotion"]:
        return {"code": "REJECTED_INSUFFICIENT_EVIDENCE",
                "detail": f"gölge örneklem {n} < "
                          f"{thresholds['min_trades_promotion']}"}
    sm = sh["metrics"]
    if sm.get("top_symbol_share", 1) >= 0.6:
        return {"code": "REJECTED_CONCENTRATION"}
    if sm.get("expectancy_per_trade", 0) <= \
            champ_m.get("expectancy_per_trade", 0):
        return {"code": "REJECTED_NO_IMPROVEMENT"}
    if sm.get("maximum_drawdown", 0) > \
            max(champ_m.get("maximum_drawdown", 0) * 1.2, 1e-9):
        return {"code": "REJECTED_DRAWDOWN"}
    if sm.get("fee_drag", 0) > champ_m.get("fee_drag", 0) * 1.2 and \
            champ_m.get("fee_drag", 0) > 0:
        return {"code": "REJECTED_FEE_DRAG"}
    return {"code": "PROMOTED"}  # hazır — uygulanması ayrı adım


def promote(model: str, approved_by: str = "OPERATOR") -> dict:
    """Challenger'ı champion yap (yalnız hazırlık PROMOTED ise).

    Varsayılan mod AUTO_SHADOW: bu fonksiyon otomatik ÇAĞRILMAZ;
    kullanıcı onayı (API) veya auto_promote=true gerektirir."""
    out: dict[str, Any] = {}

    def _mut(state: dict) -> None:
        ms = _model_state(state, model)
        readiness = evaluate_promotion(
            ms, _thresholds(state.get("config", {})))
        if readiness["code"] != "PROMOTED":
            out["result"] = readiness
            return
        ch = ms["challenger"]
        prev = ms["champion"]
        ms["champion"] = {
            "version": ch["version"],
            "overrides": dict(ch["overrides"]),
            "promoted_at": _now_iso(),
            "previous": {"version": prev["version"],
                         "overrides": dict(prev["overrides"])},
            "baseline_metrics": ms.get("metrics"),
        }
        now = time.time()
        for key in ch["overrides"]:
            ms["param_last_changed"][key] = now
        ms["promotion_history"].append(
            {"at": _now_iso(), "version": ch["version"],
             "changes": ch.get("changes"), "approved_by": approved_by,
             "shadow": ch.get("shadow")})
        ms["promotion_history"] = ms["promotion_history"][-50:]
        ms["challenger"] = None
        out["result"] = {"code": "PROMOTED", "version": ch["version"]}

    _update_state(_mut)
    _append_history({"type": "PROMOTION_ATTEMPT", "model": model,
                     "at": _now_iso(), **out.get("result", {})})
    return out.get("result", {"code": "NOT_EVALUATED"})


# ── 6) Rollback ────────────────────────────────────────────────────

def _check_rollback(ms: dict, thresholds: dict) -> dict | None:
    champ = ms["champion"]
    if not champ.get("previous"):
        return None
    ver = champ["version"]
    post = [r for r in ms.get("dataset", [])
            if r.get("configuration_version") == ver]
    if len(post) < thresholds["rollback_min_trades"]:
        return None
    m = compute_model_metrics(post)
    base = champ.get("baseline_metrics") or {}
    reason = None
    if m["expectancy_per_trade"] < 0:
        reason = "NEGATIVE_EXPECTANCY"
    elif m["maximum_drawdown"] > \
            thresholds["rollback_max_drawdown_usdt"]:
        reason = "DRAWDOWN_BREACH"
    elif base and m["expectancy_per_trade"] < \
            base.get("expectancy_per_trade", 0) * 0.5:
        reason = "PERFORMANCE_DEGRADATION"
    if not reason:
        return None
    prev = champ["previous"]
    ms["champion"] = {"version": prev["version"],
                      "overrides": dict(prev["overrides"]),
                      "promoted_at": _now_iso(), "previous": None,
                      "rolled_back_from": ver}
    entry = {"at": _now_iso(), "from_version": ver,
             "to_version": prev["version"], "reason": reason,
             "post_metrics": m}
    ms["rollback_history"].append(entry)
    ms["rollback_history"] = ms["rollback_history"][-50:]
    return entry


# ── 7) Champion overlay — dual_model kararlarında GERÇEK kullanım ──

_OVERRIDE_CACHE: dict[str, Any] = {"mtime": None, "data": {}}


def champion_overrides(model: str) -> dict[str, Any]:
    """dual_model.get_config için güvenli overlay + config_version.

    Yalnız izin listesindeki anahtarlar, sınırlar içinde döner.
    Açık pozisyonlara dokunmaz; yalnız SONRAKİ girişleri etkiler."""
    try:
        mtime = STATE_PATH.stat().st_mtime if STATE_PATH.exists() else None
        if _OVERRIDE_CACHE["mtime"] != mtime:
            state = _load_state()
            data = {}
            for mdl in MODELS:
                champ = (state.get("models", {}).get(mdl, {})
                         .get("champion") or {})
                ov = {}
                for k, v in (champ.get("overrides") or {}).items():
                    if k in LEARNABLE_BOUNDS:
                        lo, hi = LEARNABLE_BOUNDS[k]
                        ov[k] = min(max(float(v), lo), hi)
                data[mdl] = {"overrides": ov,
                             "config_version":
                                 champ.get("version", "BASE")}
            _OVERRIDE_CACHE.update(mtime=mtime, data=data)
        return _OVERRIDE_CACHE["data"].get(
            model, {"overrides": {}, "config_version": "BASE"})
    except Exception:
        return {"overrides": {}, "config_version": "BASE"}


# ── 8) Çalışma döngüsü (scheduler'a bağlanan tek giriş noktası) ────

def _thresholds(cfg: dict | None) -> dict:
    th = dict(DEFAULT_THRESHOLDS)
    user = (cfg or {}).get("dual_learning")
    if isinstance(user, dict):
        for k in th:
            if k in user:
                try:
                    th[k] = type(th[k])(user[k])
                except (TypeError, ValueError):
                    pass
    return th


def run_update(adaptive_cfg: dict | None = None,
               force: bool = False) -> dict[str, Any] | None:
    """Tek öğrenme turu. Uygunluk: interval_hours doldu VEYA yeni
    kapanan işlem sayısı eşiği aştı (force hepsini atlar).

    auto_controller / learning_engine tarafından çağrılır — ikinci
    paralel scheduler YOKTUR."""
    cfg = adaptive_cfg or {}
    if not cfg.get("learning_enabled", True):
        return None
    th = _thresholds(cfg)
    state = _load_state()
    last_run = state.get("last_run")
    new_count = sum(ingest_closed_trades().values())
    if not force and last_run:
        try:
            elapsed_h = (datetime.now(timezone.utc) -
                         datetime.fromisoformat(last_run)
                         ).total_seconds() / 3600
            pending = state.get("new_trades_since_run", 0) + new_count
            if elapsed_h < th["interval_hours"] and \
                    pending < th["min_new_trades_trigger"]:
                _update_state(lambda s: s.__setitem__(
                    "new_trades_since_run", pending))
                return None
        except (TypeError, ValueError):
            pass

    result: dict[str, Any] = {"ran_at": _now_iso(), "models": {}}

    def _mut(state: dict) -> None:
        state["config"] = {"dual_learning": cfg.get("dual_learning", {})}
        try:
            import dual_model as _dm
            base_cfg = _dm.get_config()
        except Exception:
            base_cfg = None
        for model in MODELS:
            ms = _model_state(state, model)
            records = ms.get("dataset", [])
            metrics = compute_model_metrics(records)
            ms["metrics"] = metrics
            diag = diagnose(metrics, th)
            ms["diagnosis"] = {**diag, "at": _now_iso()}
            action = "DIAGNOSED"
            # Challenger üretimi (yoksa) — champion'a yazmaz
            if ms.get("challenger") is None and base_cfg:
                section = dict(base_cfg[_SECTION[model]])
                section.update(ms["champion"].get("overrides", {}))
                ch = propose_challenger(model, metrics, diag,
                                        section, ms, th)
                if ch:
                    ms["challenger"] = ch
                    action = "CHALLENGER_CREATED"
            # Gölge değerlendirme + terfi hazırlığı
            if ms.get("challenger"):
                ms["challenger"]["shadow"] = shadow_evaluate(
                    ms["challenger"], records)
                readiness = evaluate_promotion(ms, th)
                ms["promotion_readiness"] = readiness
                if readiness["code"] == "PROMOTED" and \
                        cfg.get("dual_learning", {}).get(
                            "auto_promote", False):
                    # AUTO_PROMOTE varsayılan KAPALI
                    action = "AUTO_PROMOTE_PENDING"
            else:
                ms["promotion_readiness"] = {"code": "NO_CHALLENGER"}
            rb = _check_rollback(ms, th)
            if rb:
                action = "ROLLED_BACK:" + rb["reason"]
            ms["last_learning_time"] = _now_iso()
            ms["last_action"] = action
            result["models"][model] = {
                "closed_trades": metrics.get("closed_trades", 0),
                "diagnosis": diag.get("code"), "action": action}
        state["last_run"] = _now_iso()
        state["new_trades_since_run"] = 0
        state["last_error"] = None

    try:
        _update_state(_mut)
    except Exception as exc:  # asla ana döngüyü düşürme
        log.warning("dual_learning turu hatası: %s", exc)
        try:
            _update_state(lambda s: s.__setitem__(
                "last_error", str(exc)[:300]))
        except Exception:
            pass
        return None
    _append_history({"type": "LEARNING_RUN", **result})
    # Auto-promote (varsayılan kapalı) turdan SONRA, ayrı adımda
    if cfg.get("dual_learning", {}).get("auto_promote", False):
        for model in MODELS:
            ms = _load_state().get("models", {}).get(model, {})
            if (ms.get("promotion_readiness") or {}).get("code") == \
                    "PROMOTED":
                promote(model, approved_by="AUTO_PROMOTE")
    return result


def status() -> dict[str, Any]:
    """UI/API için gerçek öğrenme durumu — sahte veri yok."""
    state = _load_state()
    th = _thresholds(state.get("config", {}).get("dual_learning")
                     and state.get("config") or {})
    out: dict[str, Any] = {
        "learning_worker": "SCHEDULER_DRIVEN",
        "mode": "AUTO_SHADOW",
        "auto_promote": False,
        "last_run": state.get("last_run"),
        "new_trades_since_run": state.get("new_trades_since_run", 0),
        "last_error": state.get("last_error"),
        "thresholds": th,
        "models": {},
    }
    for model in MODELS:
        ms = state.get("models", {}).get(model)
        if not ms:
            out["models"][model] = {
                "sample_size": 0, "diagnosis": "INSUFFICIENT_DATA",
                "champion": {"version": "BASE", "overrides": {}},
                "challenger": None,
                "promotion_readiness": "NO_CHALLENGER",
                "data_sufficiency": "INSUFFICIENT_DATA"}
            continue
        metrics = ms.get("metrics") or {}
        n = metrics.get("closed_trades", 0)
        ch = ms.get("challenger")
        out["models"][model] = {
            "sample_size": n,
            "data_sufficiency": (
                "OK" if n >= th["min_trades_proposal"] else
                "DIAGNOSIS_ONLY" if n >= th["min_trades_diagnosis"]
                else "INSUFFICIENT_DATA"),
            "metrics": metrics or None,
            "diagnosis": ms.get("diagnosis"),
            "champion": {k: ms["champion"].get(k) for k in
                         ("version", "overrides", "promoted_at")},
            "challenger": ({"version": ch["version"],
                            "changes": ch.get("changes"),
                            "shadow": ch.get("shadow"),
                            "status": ch.get("status")} if ch else None),
            "promotion_readiness":
                (ms.get("promotion_readiness") or
                 {"code": "NOT_EVALUATED"}).get("code"),
            "last_promotion": (ms.get("promotion_history") or
                               [None])[-1],
            "last_rollback": (ms.get("rollback_history") or
                              [None])[-1],
            "last_learning_time": ms.get("last_learning_time"),
            "last_action": ms.get("last_action"),
        }
    return out
