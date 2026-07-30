"""Continuous Strategy Lab — otonom strateji üretimi, aşamalı ön test,
Paper terfisi ve kontrollü canlıya-hazırlık katmanı.

TASARIM İLKELERİ
- dual_learning altyapısının UZANTISIDIR: kanonik dataset, LEARNABLE_BOUNDS
  izin listesi, challenger/promote/rollback yolları AYNEN kullanılır.
  Paralel ikinci öğrenme sistemi YOKTUR.
- LIVE ORDERS DISABLED korunur. Bu modül hiçbir koşulda gerçek emir
  açamaz; en ileri durum LIVE_ELIGIBLE etiketi üretmektir.
  LIVE_ENABLED bu modülde TANIMSIZDIR ve buradan yazılamaz.
- Dürüstlük: elimizde tik-seviyesi geçmiş yok; adaylar kapanan gerçek
  PAPER işlemlerinin kanonik dataset'i üzerinde GATE_SUBSET_REPLAY ile
  (giriş filtreleri: min_confidence, max_spread_pct) değerlendirilir.
  Çıkış parametreleri (tp/sl/trailing/max_hold) geçmişte dürüst
  replay edilemez — bu açıkça exit_params_replayable=False ile işaretlenir
  ve nihai kanıt STAGE 4-5'te GERÇEK ileri-zaman Paper akışından gelir.
- Gelecek bilgi sızıntısı yok: dataset kronolojik bölünür
  (train 60% / walk-forward 15% / dokunulmamış holdout 25%); holdout
  aday başına yalnız BİR kez değerlendirilir, parametreler aday
  yaratıldıktan sonra değişmez (immutable).
- Başarısızlar silinmez: graveyard + rejected_fingerprints hafızası
  aynı kötü adayın yeniden üretilmesini engeller.
- Runtime state Git DIŞI, flock + atomic replace (dual_learning kalıbı).
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows: fcntl yok — taşınabilir kilit kalıbı
    import portable_flock as fcntl  # type: ignore

import hashlib
import json
import logging
import math
import os
import random
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import dual_learning as dl

log = logging.getLogger("strategy_lab")

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "strategy_lab_state.json"
HISTORY_PATH = ROOT / "strategy_lab_history.jsonl"

CODE_VERSION = "lab-1.0.0"

MODELS = dl.MODELS

# ── Emniyet sabitleri ────────────────────────────────────────────────
# Adaylar YALNIZ dual_learning.LEARNABLE_BOUNDS izin listesindeki
# parametrelerde üretilebilir. Güvenlik tavanları, LIVE kilidi, API
# izinleri, hesap bağlantıları kapsam DIŞIDIR ve değiştirilemez.
SAFE_PARAM_KEYS = tuple(dl.LEARNABLE_BOUNDS.keys())
MAX_CANDIDATES_PER_GENERATION = 4      # model başına / jenerasyon
MAX_ACTIVE_CANDIDATES = 6              # model başına eşzamanlı
MAX_MUTATION_STEP_PCT = 15.0           # aday üretiminde tek param sapması
CYCLE_INTERVAL_HOURS = 6.0

STAGES = ("STAGE0_STATIC", "STAGE1_HISTORICAL", "STAGE2_WALK_FORWARD",
          "STAGE3_HOLDOUT", "STAGE4_PAPER_SHADOW",
          "STAGE5_PAPER_CHALLENGER", "STAGE6_LIVE_ELIGIBLE")

GENERATION_METHODS = (
    "PARAM_MUTATION", "DIAGNOSIS_DRIVEN", "CROSSOVER",
    "REGIME_VARIANT", "FEE_DRAG_REDUCER", "GIVEBACK_REDUCER",
    "LOSS_REDUCTION", "PROFIT_CAPTURE", "COST_STRUCTURE")

# Maliyet-duyarlı TP/SL ızgaraları (net TP/SL sözleşmesi): yalnız
# maliyet-sonrası net reward/risk >= MIN_NET_REWARD_RISK olan
# kombinasyonlar aday olur; champion'a DOKUNULMAZ, shadow/paper'da
# kanıtlanır. Izgara sabittir — rastgele değil, denetlenebilir.
MIN_NET_REWARD_RISK = 1.20
COST_GRIDS: dict[str, dict[str, tuple[float, ...]]] = {
    "ALPHA_CORE_SCALP": {
        "tp_pct": (0.80, 1.00, 1.20),
        "sl_pct": (0.35, 0.40, 0.50),
    },
    "ALPHA_OPPORTUNITY_BURST": {
        "tp_pct": (1.00, 1.25, 1.50),
        "sl_pct": (0.45, 0.55, 0.65),
    },
}
MAX_COST_CANDIDATES_PER_GEN = 2   # jenerasyon başına; kap MAX_… içinde

LOSS_REASON_CODES = (
    "LOW_QUALITY_ENTRY", "FALSE_BREAKOUT", "MOMENTUM_EXHAUSTED",
    "SIGNAL_REVERSAL", "STOP_TOO_TIGHT", "STOP_TOO_WIDE", "TP_TOO_FAR",
    "TP_TOO_SMALL", "TRAILING_TOO_EARLY", "TRAILING_TOO_LATE",
    "PROFIT_GIVEBACK", "HOLD_TOO_LONG", "HOLD_TOO_SHORT", "FEE_DRAG",
    "SLIPPAGE_DRAG", "SPREAD_SPIKE", "REGIME_MISMATCH",
    "LIQUIDITY_FAILURE", "VOLATILITY_SHOCK", "SYMBOL_SPECIFIC_FAILURE",
    "DATA_QUALITY_FAILURE")

CAPTURE_CODES = (
    "PROFIT_CAPTURE_GOOD", "EXIT_TOO_EARLY", "EXIT_TOO_LATE",
    "EXCESSIVE_GIVEBACK", "TP_NOT_REACHED_BUT_OPTIMAL_EXIT_PASSED",
    "STRONG_MOMENTUM_CORRECTLY_HELD", "EVIDENCE_MISSING")

DEFAULTS = {
    "min_dataset_stage1": 60,      # STAGE1 için toplam dataset alt sınırı
    "min_subset_sample": 20,       # her aşamada aday alt-kümesi alt sınırı
    "walk_forward_windows": 4,
    "walk_forward_pass_ratio": 0.6,
    "min_shadow_sample": 30,       # STAGE4 ileri-zaman örneklem
    "min_capture_sample": 15,      # kâr-yakalama teşhisi örneklemi
    "max_consecutive_failed_challengers": 5,
    "live_min_paper_trades": 200,
    "live_min_paper_days": 14.0,
    "live_min_regimes": 2,
    "live_max_drawdown_usdt": 40.0,
    "live_max_consecutive_losses": 8,
    "live_min_net_over_costs": 1.5,   # net kâr / (fee+slip) oranı
}

CONTROL_ACTIONS = (
    "PAUSE_LAB", "RESUME_LAB", "PAUSE_GENERATION", "RESUME_GENERATION",
    "FREEZE_AUTO_PROMOTE", "UNFREEZE_AUTO_PROMOTE",
    "PAUSE_LIVE_EVAL", "RESUME_LIVE_EVAL", "CANCEL_CHALLENGERS",
    "REVERT_CHAMPION", "EMERGENCY_STOP", "CLEAR_EMERGENCY_STOP")


# ── Git dışı state store (dual_learning kalıbı: flock + atomic) ─────

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


def _parse_iso(v: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _model_lab(state: dict, model: str) -> dict:
    ml = state.setdefault("models", {}).setdefault(model, {})
    ml.setdefault("candidates", {})       # cid -> candidate
    ml.setdefault("graveyard", [])        # başarısızlar (silinmez)
    ml.setdefault("rejected_fingerprints", [])
    ml.setdefault("generation", 0)
    ml.setdefault("consecutive_failed_challengers", 0)
    ml.setdefault("promotion_history", [])
    ml.setdefault("rollback_history", [])
    ml.setdefault("live_eligibility", None)
    ml.setdefault("live_eligibility_history", [])
    ml.setdefault("loss_diagnosis", None)
    ml.setdefault("profit_capture", None)
    ml.setdefault("candidates_tested_total", 0)
    return ml


def _controls(state: dict) -> dict:
    c = state.setdefault("controls", {})
    c.setdefault("lab_enabled", True)
    c.setdefault("generation_paused", False)
    c.setdefault("auto_promote_frozen", False)
    c.setdefault("live_eval_paused", False)
    c.setdefault("emergency_stop", False)
    return c


# ── Aday kimliği / parmak izi ────────────────────────────────────────

def _fingerprint(model: str, params: dict) -> str:
    canon = json.dumps({k: round(float(v), 6)
                        for k, v in sorted(params.items())})
    return hashlib.sha256(f"{model}|{canon}".encode()).hexdigest()[:20]


def _clamp(key: str, value: float) -> float:
    lo, hi = dl.LEARNABLE_BOUNDS[key]
    return max(lo, min(hi, float(value)))


def validate_candidate_params(params: dict) -> tuple[bool, str]:
    """STAGE 0 güvenlik: yalnız izin listesi, mutlak sınırlar içinde."""
    if not isinstance(params, dict) or not params:
        return False, "EMPTY_PARAMS"
    for k, v in params.items():
        if k not in dl.LEARNABLE_BOUNDS:
            return False, f"FORBIDDEN_PARAM:{k}"
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return False, f"NON_NUMERIC:{k}"
        lo, hi = dl.LEARNABLE_BOUNDS[k]
        if not (lo <= fv <= hi) or not math.isfinite(fv):
            return False, f"OUT_OF_BOUNDS:{k}"
    return True, "OK"


# ── 1-2) Aday üretim motoru ─────────────────────────────────────────

def _base_params(model: str) -> dict:
    """Champion etkin parametre seti (BASE + champion overrides)."""
    try:
        import dual_model as _dm
        cfg = _dm.get_config()
        section = dict(cfg[dl._SECTION[model]])
    except Exception:
        section = {}
    out = {}
    for k in SAFE_PARAM_KEYS:
        if isinstance(section.get(k), (int, float)):
            out[k] = float(section[k])
    return out


def _mutate(params: dict, keys: list[str], direction: dict[str, float],
            rng: random.Random) -> dict:
    out = dict(params)
    for k in keys:
        if k not in out:
            continue
        step = direction.get(k, rng.choice((-1.0, 1.0))) * \
            rng.uniform(3.0, MAX_MUTATION_STEP_PCT) / 100.0
        out[k] = _clamp(k, out[k] * (1 + step))
    return out


def generate_candidates(model: str, ml: dict, diagnosis: dict | None,
                        loss_diag: dict | None,
                        capture: dict | None,
                        rng: random.Random | None = None) -> list[dict]:
    """Kontrollü aday üretimi. Kaynak kod YENİDEN YAZILMAZ; yalnız
    izin listesi parametre alanında, jenerasyon başına sınırlı sayıda.
    Duplicate/graveyard parmak izleri elenir."""
    rng = rng or random.Random()
    base = _base_params(model)
    if not base:
        return []
    known = set(ml.get("rejected_fingerprints", []))
    for c in ml.get("candidates", {}).values():
        known.add(c.get("fingerprint"))
    known.add(_fingerprint(model, base))

    plans: list[tuple[str, dict, dict[str, float], str]] = [
        ("PARAM_MUTATION", base, {},
         "küçük rastgele mutasyon — mevcut champion çevresini tara"),
    ]
    dcode = (diagnosis or {}).get("code")
    if dcode == "FEE_DRAG_DOMINANT":
        plans.append(("FEE_DRAG_REDUCER", base,
                      {"min_confidence": 1, "cooldown_minutes": 1,
                       "tp_pct": 1},
                      "fee-drag baskın — daha az/daha seçici işlem"))
    if dcode in ("STOP_TOO_TIGHT", "NEGATIVE_EXPECTANCY"):
        plans.append(("LOSS_REDUCTION", base,
                      {"sl_pct": 1, "min_confidence": 1},
                      "zarar azaltma — stop genişlet, giriş kalitesini artır"))
    top_loss = (loss_diag or {}).get("most_frequent")
    if top_loss in ("PROFIT_GIVEBACK", "TRAILING_TOO_LATE"):
        plans.append(("GIVEBACK_REDUCER", base,
                      {"trailing_pct": -1},
                      "kâr geri verme — trailing mesafesini daralt"))
    cap_code = (capture or {}).get("dominant")
    if cap_code == "EXIT_TOO_EARLY":
        plans.append(("PROFIT_CAPTURE", base,
                      {"tp_pct": 1, "trailing_pct": 1},
                      "erken çıkış — TP/trailing alanını genişlet"))
    # Çapraz kombinasyon: geçmiş başarılı terfi parametreleri varsa
    promoted = [h for h in ml.get("promotion_history", [])
                if isinstance(h.get("parameters"), dict)]
    if len(promoted) >= 1:
        mix = dict(base)
        src = rng.choice(promoted)["parameters"]
        for k in list(src)[:2]:
            if k in dl.LEARNABLE_BOUNDS:
                mix[k] = _clamp(k, (float(src[k]) + mix.get(k, src[k])) / 2)
        plans.append(("CROSSOVER", mix, {},
                      "başarılı geçmiş stratejiyle çapraz kombinasyon"))

    out: list[dict] = []
    gen = int(ml.get("generation", 0)) + 1

    # COST_STRUCTURE: sabit maliyet-duyarlı TP/SL ızgarası. Yalnız
    # maliyet-sonrası net_rr >= MIN_NET_REWARD_RISK kombinasyonlar;
    # net_rr en yüksekten sıralanır, jenerasyon başına en fazla
    # MAX_COST_CANDIDATES_PER_GEN (parmak izi eleme döngüler boyunca
    # ızgarayı tüketir). Parametreler mutasyonsuz, olduğu gibi.
    import dual_model as _dm
    grid = COST_GRIDS.get(model)
    if grid:
        combos = []
        for tp in grid["tp_pct"]:
            for sl in grid["sl_pct"]:
                cp = _dm.cost_profile(
                    {"tp_pct": tp, "sl_pct": sl,
                     "max_slippage_pct": base.get("max_slippage_pct")})
                rr = cp["net_reward_risk"]
                if rr is not None and rr >= MIN_NET_REWARD_RISK \
                        and cp["net_tp_pct"] > 0:
                    combos.append((rr, tp, sl, cp))
        combos.sort(key=lambda c: -c[0])
        added = 0
        for rr, tp, sl, cp in combos:
            if added >= MAX_COST_CANDIDATES_PER_GEN or \
                    len(out) >= MAX_CANDIDATES_PER_GENERATION:
                break
            params = dict(base)
            params["tp_pct"] = tp
            params["sl_pct"] = sl
            ok, _why = validate_candidate_params(params)
            if not ok:
                continue
            fp = _fingerprint(model, params)
            if fp in known:
                continue
            known.add(fp)
            out.append({
                "strategy_candidate_id": f"{model[:4]}-g{gen}-{fp[:8]}",
                "parent_strategy_id": "CHAMPION",
                "generation": gen,
                "model_family": model,
                "parameters": params,
                "fingerprint": fp,
                "created_reason": "COST_STRUCTURE",
                "hypothesis": (
                    f"maliyet-sonrası yapı: TP {tp}% / SL {sl}% → "
                    f"net RR {rr} (net TP {cp['net_tp_pct']}%, "
                    f"net SL {cp['net_sl_pct']}%, başabaş WR "
                    f"{cp['break_even_win_rate_pct']}%)"),
                "expected_improvement":
                    "fee drag payı düşer; net expectancy pozitife döner",
                "risk_notes": "izin listesi içinde; güvenlik tavanları "
                              "sabit; champion'a dokunulmaz",
                "cost_profile": cp,
                "data_version": None,
                "code_version": CODE_VERSION,
                "created_at": _now_iso(),
                "stage": "STAGE0_STATIC",
                "status": "ACTIVE",
                "stage_results": {},
            })
            added += 1

    for method, seed, direction, hyp in plans:
        if len(out) >= MAX_CANDIDATES_PER_GENERATION:
            break
        keys = list(direction) or rng.sample(
            [k for k in SAFE_PARAM_KEYS if k in seed],
            k=min(2, len(seed)))
        params = _mutate(seed, keys, direction, rng)
        ok, why = validate_candidate_params(params)
        if not ok:
            continue
        fp = _fingerprint(model, params)
        if fp in known:
            continue  # duplicate veya daha önce başarısız — üretme
        known.add(fp)
        cid = f"{model[:4]}-g{gen}-{fp[:8]}"
        out.append({
            "strategy_candidate_id": cid,
            "parent_strategy_id": "CHAMPION",
            "generation": gen,
            "model_family": model,
            "parameters": params,
            "fingerprint": fp,
            "created_reason": method,
            "hypothesis": hyp,
            "expected_improvement": "net expectancy / composite skor",
            "risk_notes": "izin listesi içinde; güvenlik tavanları sabit",
            "data_version": None,   # değerlendirmede damgalanır
            "code_version": CODE_VERSION,
            "created_at": _now_iso(),
            "stage": "STAGE0_STATIC",
            "status": "ACTIVE",
            "stage_results": {},
        })
    return out


# ── 3) Zarar nedeni analizi ─────────────────────────────────────────

def classify_loss(rec: dict) -> dict[str, Any]:
    """Kapanan zararlı işlem için açıklanabilir neden + kanıt.
    MFE/MAE yoksa uydurulmaz — DATA_QUALITY_FAILURE (kanıt eksik)."""
    net = float(rec.get("net_pnl") or 0)
    ev = {
        "entry_snapshot": {"price": rec.get("entry_price"),
                           "confidence": rec.get("confidence"),
                           "spread": rec.get("spread"),
                           "regime": rec.get("market_regime")},
        "exit_snapshot": {"price": rec.get("exit_price"),
                          "reason": rec.get("exit_reason")},
        "mfe_pct": rec.get("mfe_pct"), "mae_pct": rec.get("mae_pct"),
        "final_net_pnl": net,
        "fees": rec.get("fees"), "slippage": rec.get("slippage"),
        "hold_duration": rec.get("hold_duration"),
    }
    gross = float(rec.get("gross_pnl") or 0)
    fees = float(rec.get("fees") or 0)
    slip = float(rec.get("slippage") or 0)
    mfe = rec.get("mfe_pct")
    exit_reason = rec.get("exit_reason")

    if net >= 0:
        return {"code": None, "evidence": ev}
    # Maliyet baskın: brüt pozitif ama net negatif
    if gross > 0 and fees >= slip and fees > gross:
        return {"code": "FEE_DRAG", "evidence": ev}
    if gross > 0 and slip > fees and slip > gross:
        return {"code": "SLIPPAGE_DRAG", "evidence": ev}
    if not isinstance(mfe, (int, float)):
        return {"code": "DATA_QUALITY_FAILURE",
                "evidence": {**ev, "note": "MFE/MAE kanıtı yok — "
                             "eski kayıt, sınıflandırma yapılamaz"}}
    if mfe >= 0.5 and exit_reason in ("SL", "TRAILING"):
        return {"code": "PROFIT_GIVEBACK", "evidence": ev}
    if mfe >= 0.3 and exit_reason == "SL":
        return {"code": "STOP_TOO_TIGHT", "evidence": ev}
    if exit_reason == "TIME_EXIT":
        return {"code": "HOLD_TOO_LONG" if mfe >= 0.2 else
                "MOMENTUM_EXHAUSTED", "evidence": ev}
    if mfe < 0.1:
        return {"code": "LOW_QUALITY_ENTRY", "evidence": ev}
    return {"code": "FALSE_BREAKOUT", "evidence": ev}


def aggregate_loss_diagnosis(records: list[dict]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    costs: dict[str, float] = {}
    by_symbol: dict[str, float] = {}
    by_regime: dict[str, float] = {}
    for r in records:
        d = classify_loss(r)
        if not d["code"]:
            continue
        net = float(r.get("net_pnl") or 0)
        counts[d["code"]] = counts.get(d["code"], 0) + 1
        costs[d["code"]] = round(costs.get(d["code"], 0) + net, 6)
        sym = r.get("symbol") or "?"
        by_symbol[sym] = round(by_symbol.get(sym, 0) + net, 6)
        reg = r.get("market_regime") or "UNKNOWN"
        by_regime[reg] = round(by_regime.get(reg, 0) + net, 6)
    most = max(counts, key=lambda k: counts[k]) if counts else None
    worst = min(costs, key=lambda k: costs[k]) if costs else None
    return {"counts": counts, "cost_by_reason": costs,
            "most_frequent": most, "most_expensive": worst,
            "loss_by_symbol": by_symbol, "loss_by_regime": by_regime,
            "at": _now_iso()}


# ── 4) Kâr yakalama analizi ─────────────────────────────────────────

def profit_capture_metrics(rec: dict) -> dict[str, Any]:
    """Tek işlemin kâr-yakalama metrikleri. MFE kanıtı yoksa
    EVIDENCE_MISSING (uydurma yok). Zirve zamanı kaydedilmediğinden
    time_to_peak dürüstçe None'dır."""
    entry = float(rec.get("entry_price") or 0)
    qty = float(rec.get("quantity") or 0)
    net = float(rec.get("net_pnl") or 0)
    fees = float(rec.get("fees") or 0)
    slip = float(rec.get("slippage") or 0)
    mfe = rec.get("mfe_pct")
    if not isinstance(mfe, (int, float)) or entry <= 0 or qty <= 0:
        return {"code": "EVIDENCE_MISSING", "captured_profit_ratio": None}
    max_gross = entry * (mfe / 100.0) * qty
    max_net = max_gross - fees - slip  # maliyet tahmini: gerçekleşen
    out = {
        "maximum_gross_profit": round(max_gross, 6),
        "maximum_net_profit": round(max_net, 6),
        "realized_net_profit": round(net, 6),
        "profit_giveback": round(max(0.0, max_net - net), 6),
        "time_to_peak": None,   # tik geçmişi yok — dürüst bilinmiyor
        "time_from_peak_to_exit": None,
    }
    if max_net <= 0:
        out["captured_profit_ratio"] = None
        out["code"] = "TP_NOT_REACHED_BUT_OPTIMAL_EXIT_PASSED" \
            if net < 0 and mfe > 0 else "PROFIT_CAPTURE_GOOD" \
            if net >= 0 else "EVIDENCE_MISSING"
        return out
    ratio = net / max_net
    out["captured_profit_ratio"] = round(ratio, 4)
    exit_reason = rec.get("exit_reason")
    if ratio >= 0.7:
        out["code"] = "STRONG_MOMENTUM_CORRECTLY_HELD" \
            if exit_reason == "TP" and mfe >= 1.0 else \
            "PROFIT_CAPTURE_GOOD"
    elif net <= 0:
        out["code"] = "EXCESSIVE_GIVEBACK"
    elif exit_reason in ("TIME_EXIT", "TRAILING") and ratio < 0.3:
        out["code"] = "EXIT_TOO_LATE"
    elif exit_reason == "TP" and ratio < 0.5:
        out["code"] = "EXIT_TOO_EARLY"
    else:
        out["code"] = "EXCESSIVE_GIVEBACK" if ratio < 0.3 else \
            "PROFIT_CAPTURE_GOOD"
    return out


def aggregate_profit_capture(records: list[dict],
                             min_sample: int) -> dict[str, Any]:
    """Model bazında toplulaştırma. Tek işlemle strateji değişikliği
    YAPILMAZ — min_sample altında dominant teşhis üretilmez."""
    rows = [profit_capture_metrics(r) for r in records]
    with_ev = [r for r in rows
               if r.get("captured_profit_ratio") is not None]
    codes: dict[str, int] = {}
    for r in rows:
        c = r.get("code")
        if c:
            codes[c] = codes.get(c, 0) + 1
    dominant = None
    if len(with_ev) >= min_sample:
        scored = {k: v for k, v in codes.items()
                  if k not in ("EVIDENCE_MISSING",)}
        dominant = max(scored, key=lambda k: scored[k]) if scored else None
    ratios = [r["captured_profit_ratio"] for r in with_ev]
    return {
        "sample": len(rows), "evidence_sample": len(with_ev),
        "avg_captured_ratio": round(sum(ratios) / len(ratios), 4)
        if ratios else None,
        "total_giveback": round(sum(r.get("profit_giveback") or 0
                                    for r in rows), 6),
        "missed_upside": round(sum(
            max(0.0, (r.get("maximum_net_profit") or 0) -
                (r.get("realized_net_profit") or 0)) for r in with_ev), 6),
        "early_exits": codes.get("EXIT_TOO_EARLY", 0),
        "late_exits": codes.get("EXIT_TOO_LATE", 0),
        "code_counts": codes, "dominant": dominant,
        "min_sample": min_sample, "at": _now_iso(),
    }


# ── 5) Çoklu-hedef kompozit skor ────────────────────────────────────

def composite_score(metrics: dict, capture_ratio: float | None,
                    n_params_changed: int,
                    candidates_tested: int) -> float:
    """Tek başına net PnL/win-rate DEĞİL; çok hedefli skor + karmaşıklık
    ve çoklu-test cezası."""
    n = metrics.get("closed_trades", 0)
    if n == 0:
        return 0.0
    exp = float(metrics.get("expectancy_per_trade") or 0)
    pf = metrics.get("profit_factor") or 0
    pf = min(float(pf) if pf != float("inf") else 3.0, 3.0)
    dd = float(metrics.get("maximum_drawdown") or 0)
    fee = float(metrics.get("fee_drag") or 0)
    net = float(metrics.get("net_pnl") or 0)
    conc = float(metrics.get("top_symbol_share") or 1.0)
    score = (exp * 10.0) + (pf - 1.0) + (net / max(n, 1)) \
        - (dd / max(abs(net), 1.0)) * 0.5 \
        - (fee / max(abs(net) + fee, 1.0)) * 0.5 \
        - max(0.0, conc - 0.5)
    if capture_ratio is not None:
        score += (capture_ratio - 0.5)
    # karmaşıklık cezası + çoklu-test cezası (tesadüfi başarı deflasyonu)
    score -= 0.05 * max(0, n_params_changed - 1)
    score -= 0.1 * math.log1p(max(0, candidates_tested))
    return round(score, 4)


# ── 6) Aşamalı ön test ──────────────────────────────────────────────

def _subset(records: list[dict], params: dict) -> list[dict]:
    """GATE_SUBSET_REPLAY: giriş filtreleri gerçekleşmiş işlemlere
    dürüstçe uygulanır (geleceğe bakmadan). Çıkış paramları
    replay edilemez — işaretlenir."""
    minc = params.get("min_confidence")
    maxsp = params.get("max_spread_pct")
    out = []
    for r in records:
        c = r.get("confidence")
        if isinstance(minc, (int, float)) and \
                isinstance(c, (int, float)) and c < minc:
            continue
        sp = r.get("spread")
        if isinstance(maxsp, (int, float)) and \
                isinstance(sp, (int, float)) and sp > maxsp:
            continue
        out.append(r)
    return out


def _sorted_dataset(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: str(r.get("exit_time") or ""))


def split_dataset(records: list[dict]) -> dict[str, list[dict]]:
    """Katı zaman bölmesi: train 60% / walk 15% / holdout son 25%.
    Holdout ÜRETİMDE KULLANILMAZ."""
    recs = _sorted_dataset(records)
    n = len(recs)
    a, b = int(n * 0.60), int(n * 0.75)
    return {"train": recs[:a], "walk": recs[a:b], "holdout": recs[b:]}


def _stage_metrics(records: list[dict], params: dict,
                   min_sample: int) -> dict[str, Any]:
    sub = _subset(records, params)
    m = dl.compute_model_metrics(sub)
    return {"method": "GATE_SUBSET_REPLAY", "sample": len(sub),
            "exit_params_replayable": False,
            "sufficient": len(sub) >= min_sample,
            "metrics": m,
            "note": "fee/slippage dahil (net_pnl); çıkış paramları "
                    "yalnız ileri-zaman Paper'da kanıtlanır"}


def conservative_exit_replay(records: list[dict], params: dict,
                             min_sample: int) -> dict[str, Any]:
    """Aday TP/SL'inin MFE/MAE ile MUHAFAZAKÂR ileri-zaman replay'i.

    Kayıt sırası bilinmediğinden iki taraf da dokunduysa SL sayılır
    (kötü senaryo). MFE/MAE'siz veya karar verilemeyen işlem KANIT
    DEĞİLDİR — sayılmaz. Geçiş: karar örneklemi >= min_sample VE
    replay win-rate, maliyet-sonrası başabaş win-rate'in ÜZERİNDE."""
    import dual_model as _dm
    tp = float(params.get("tp_pct") or 0)
    sl = float(params.get("sl_pct") or 0)
    cp = _dm.cost_profile(params)
    tp_hits = sl_hits = skipped = 0
    for r in records:
        mfe, mae = r.get("mfe_pct"), r.get("mae_pct")
        if not isinstance(mfe, (int, float)) or \
                not isinstance(mae, (int, float)):
            skipped += 1
            continue
        if mae >= sl:            # muhafazakâr: önce stop varsayımı
            sl_hits += 1
        elif mfe >= tp:
            tp_hits += 1
        else:
            skipped += 1         # aday açısından karar verilemedi
    decided = tp_hits + sl_hits
    wr = (tp_hits / decided * 100) if decided else None
    be = cp["break_even_win_rate_pct"]
    if decided < min_sample:
        ok, reason = False, "FORWARD_PROOF_INSUFFICIENT_SAMPLE"
    elif be is None or wr is None or wr <= be:
        ok, reason = False, "REPLAY_WIN_RATE_BELOW_BREAK_EVEN"
    else:
        ok, reason = True, "OK"
    return {"ok": ok, "reason": reason,
            "method": "CONSERVATIVE_MFE_MAE_REPLAY",
            "decided_sample": decided, "tp_hits": tp_hits,
            "sl_hits": sl_hits, "skipped": skipped,
            "replay_win_rate_pct": round(wr, 2) if wr is not None
            else None,
            "break_even_win_rate_pct": be,
            "note": "iki taraf da dokunduysa SL sayıldı (kötü "
                    "senaryo); karar verilemeyen işlem kanıt değil"}


def run_stage(cand: dict, model: str, dataset: list[dict],
              champion_metrics: dict | None, ml: dict,
              cfg: dict) -> dict[str, Any]:
    """Adayın mevcut aşamasını BİR adım ilerletir; sonuç kaydı döner.
    Parametreler aday yaratıldıktan sonra DEĞİŞMEZ."""
    d = {**DEFAULTS, **(cfg.get("strategy_lab") or {})}
    params = cand["parameters"]
    stage = cand["stage"]
    res: dict[str, Any] = {"stage": stage, "at": _now_iso()}
    split = split_dataset(dataset)

    if stage == "STAGE0_STATIC":
        ok, why = validate_candidate_params(params)
        dup = cand["fingerprint"] in ml.get("rejected_fingerprints", [])
        data_ok = len(dataset) >= d["min_dataset_stage1"]
        res.update(ok=ok and not dup and data_ok,
                   reason=("DUPLICATE" if dup else why if not ok
                           else "INSUFFICIENT_DATASET"
                           if not data_ok else "OK"))
        cand["data_version"] = hashlib.sha256(
            f"{len(dataset)}|{dataset[-1].get('trade_id') if dataset else ''}"
            .encode()).hexdigest()[:12]

    elif stage == "STAGE1_HISTORICAL":
        sm = _stage_metrics(split["train"], params, d["min_subset_sample"])
        m = sm["metrics"]
        ok = sm["sufficient"] and \
            float(m.get("expectancy_per_trade") or 0) > 0
        res.update(ok=ok, reason="OK" if ok else
                   ("INSUFFICIENT_SAMPLE" if not sm["sufficient"]
                    else "NEGATIVE_EXPECTANCY"), **sm)

    elif stage == "STAGE2_WALK_FORWARD":
        seq = split["train"] + split["walk"]  # kronolojik; holdout YOK
        k = max(2, int(d["walk_forward_windows"]))
        size = max(1, len(seq) // k)
        wins = 0; windows = []
        for i in range(k):
            w = seq[i * size:(i + 1) * size] if i < k - 1 else seq[(k-1)*size:]
            sm = _stage_metrics(w, params, max(5, d["min_subset_sample"] // k))
            pos = sm["sufficient"] and \
                float(sm["metrics"].get("expectancy_per_trade") or 0) > 0
            wins += 1 if pos else 0
            windows.append({"window": i, "sample": sm["sample"],
                            "positive": pos})
        ratio = wins / k
        ok = ratio >= d["walk_forward_pass_ratio"]
        res.update(ok=ok, windows=windows, pass_ratio=round(ratio, 3),
                   reason="OK" if ok else "UNSTABLE_ACROSS_WINDOWS")

    elif stage == "STAGE3_HOLDOUT":
        if cand["stage_results"].get("STAGE3_HOLDOUT"):
            # Holdout aday başına TEK değerlendirme — tekrar yok
            res.update(ok=False, reason="HOLDOUT_ALREADY_CONSUMED")
        else:
            sm = _stage_metrics(split["holdout"], params,
                                max(5, d["min_subset_sample"] // 2))
            ok = sm["sufficient"] and \
                float(sm["metrics"].get("expectancy_per_trade") or 0) > 0
            res.update(ok=ok, reason="OK" if ok else
                       ("INSUFFICIENT_SAMPLE" if not sm["sufficient"]
                        else "HOLDOUT_FAILED"), **sm)

    elif stage == "STAGE4_PAPER_SHADOW":
        created = _parse_iso(cand.get("created_at"))
        fwd = [r for r in _sorted_dataset(dataset)
               if (_parse_iso(r.get("exit_time")) or
                   datetime.min.replace(tzinfo=timezone.utc)) >
               (created or datetime.max.replace(tzinfo=timezone.utc))]
        sm = _stage_metrics(fwd, params, d["min_shadow_sample"])
        m = sm["metrics"]
        if not sm["sufficient"]:
            res.update(ok=None, reason="WAITING_FORWARD_SAMPLE", **sm)
        else:
            champ = dl.compute_model_metrics(fwd)  # aynı pencere
            better = float(m.get("expectancy_per_trade") or 0) >= \
                float(champ.get("expectancy_per_trade") or 0)
            ok = better and float(m.get("expectancy_per_trade") or 0) > 0
            fail_reason = "NOT_BETTER_THAN_CHAMPION"
            # COST_STRUCTURE adayı: net RR hedefi GERÇEK ileri-zaman
            # sonuçlarla doğrulanmalı. Alt küme metrikleri champion
            # ÇIKIŞLARINI ölçer (GATE_SUBSET_REPLAY) — adayın TP/SL'i
            # için tek dürüst kanıt MFE/MAE muhafazakâr replay'idir:
            # her ileri-zaman işlemde önce SL dokunuşu varsayılır
            # (kötü senaryo); karar verilemeyen işlem sayılmaz.
            if ok and cand.get("created_reason") == "COST_STRUCTURE":
                pf = m.get("profit_factor") or 0
                wlr = m.get("win_loss_ratio")
                if not (pf and pf > 1):
                    ok = False
                    res["cost_gate"] = "PROFIT_FACTOR_NOT_ABOVE_1"
                elif wlr is None or wlr < MIN_NET_REWARD_RISK:
                    # WLR hesaplanamıyorsa da fail-closed — kanıtsız
                    # geçiş yok.
                    ok = False
                    res["cost_gate"] = "REALIZED_NET_RR_BELOW_TARGET"
                else:
                    proof = conservative_exit_replay(
                        fwd, params, d["min_shadow_sample"])
                    res["forward_exit_proof"] = proof
                    if not proof["ok"]:
                        ok = False
                        res["cost_gate"] = proof["reason"]
                    else:
                        res["cost_gate"] = "OK"
                if not ok:
                    fail_reason = "COST_TARGET_NOT_MET"
            res.update(ok=ok, champion_same_window={
                "expectancy": champ.get("expectancy_per_trade"),
                "sample": champ.get("closed_trades")},
                reason="OK" if ok else fail_reason, **sm)

    elif stage == "STAGE5_PAPER_CHALLENGER":
        # dual_learning challenger yuvasına devret — terfi kapıları
        # (min örneklem, expectancy, DD, fee, konsantrasyon) dl'de.
        res.update(ok=None, reason="HANDED_TO_DUAL_LEARNING")

    else:
        res.update(ok=False, reason="UNKNOWN_STAGE")
    return res


# ── 9-10) Terfi köprüsü + canlıya uygunluk ──────────────────────────

def install_as_challenger(model: str, cand: dict) -> bool:
    """Adayı dual_learning challenger yuvasına yerleştir (yuva boşsa)."""
    installed = {"ok": False}

    base = _base_params(model)  # UI sözleşmesi: parameter/old/new

    def _mut(s: dict) -> None:
        ms = dl._model_state(s, model)
        if ms.get("challenger") is not None:
            return
        ms["challenger"] = {
            "version": cand["strategy_candidate_id"],
            "overrides": cand["parameters"],
            "created_at": _now_iso(),
            "source": "STRATEGY_LAB",
            "changes": [{"parameter": k, "old": base.get(k),
                         "new": v}
                        for k, v in cand["parameters"].items()],
            "shadow": None,
            # Çıkış parametreli adayın STAGE4 muhafazakâr MFE/MAE
            # replay kanıtı — dl terfi kapısı bunu arar (kanıtsız
            # exit-param challenger PROMOTED olamaz).
            "forward_exit_proof": (cand.get("stage_results", {})
                                   .get("STAGE4_PAPER_SHADOW", {})
                                   .get("forward_exit_proof")),
        }
        installed["ok"] = True
    dl._update_state(_mut)
    return installed["ok"]


def evaluate_live_eligibility(model: str, dataset: list[dict],
                              metrics: dict, runtime_healthy: bool,
                              cfg: dict) -> dict[str, Any]:
    """LIVE_ELIGIBLE yalnız ETİKETTİR. Gerçek emir yolu bu modülden
    AÇILAMAZ — LIVE_ENABLED ayrı güvenlik kabulü ve açık kullanıcı
    kararı gerektirir ve burada tanımsızdır."""
    d = {**DEFAULTS, **(cfg.get("strategy_lab") or {})}
    recs = _sorted_dataset(dataset)
    n = len(recs)
    first = _parse_iso(recs[0].get("exit_time")) if recs else None
    days = ((datetime.now(timezone.utc) - first).total_seconds() / 86400
            if first else 0.0)
    regimes = {r.get("market_regime") for r in recs
               if r.get("market_regime")}
    net = float(metrics.get("net_pnl") or 0)
    costs = float(metrics.get("fee_drag") or 0) + \
        float(metrics.get("slippage_drag") or 0)
    checks = {
        "min_paper_trades": n >= d["live_min_paper_trades"],
        "min_paper_days": days >= d["live_min_paper_days"],
        "regime_coverage": len(regimes) >= d["live_min_regimes"],
        "net_over_costs": costs > 0 and
        net >= d["live_min_net_over_costs"] * costs,
        "drawdown": float(metrics.get("maximum_drawdown") or 0) <=
        d["live_max_drawdown_usdt"],
        "consecutive_losses": int(metrics.get("consecutive_losses") or 0)
        <= d["live_max_consecutive_losses"],
        "transport_health": bool(runtime_healthy),
        # Operasyonel kanıtlar ayrı saha testleriyle işaretlenir:
        "execution_simulator": bool(d.get("attest_execution_simulator")),
        "stress_test": bool(d.get("attest_stress_test")),
        "restart_recovery": bool(d.get("attest_restart_recovery")),
        "rollback_test": bool(d.get("attest_rollback_test")),
        "kill_switch_test": bool(d.get("attest_kill_switch_test")),
    }
    eligible = all(checks.values())
    return {"model": model, "checks": checks,
            "status": "LIVE_ELIGIBLE" if eligible else "NOT_ELIGIBLE",
            "live_orders": "DISABLED",  # değişmez — bilgi amaçlı
            "evaluated_at": _now_iso(),
            "sample": n, "paper_days": round(days, 2),
            "regimes": sorted(x for x in regimes if x)}


# ── 13) Devre kesici ────────────────────────────────────────────────

def evaluate_circuit_breaker(ml: dict, dataset: list[dict],
                             runtime_error: str | None,
                             cfg: dict) -> dict[str, Any]:
    d = {**DEFAULTS, **(cfg.get("strategy_lab") or {})}
    reasons = []
    if ml.get("consecutive_failed_challengers", 0) >= \
            d["max_consecutive_failed_challengers"]:
        reasons.append("CONSECUTIVE_FAILED_CHALLENGERS")
    if len(dataset) < d["min_dataset_stage1"] // 2:
        reasons.append("INSUFFICIENT_TRADES")
    err = (runtime_error or "").upper()
    if any(k in err for k in ("SSL", "TRANSPORT", "CONNECT", "TIMEOUT",
                              "NETWORK")):
        reasons.append("TRANSPORT_FAILURE")
    if cfg.get("risk_engine_red"):
        reasons.append("RISK_ENGINE_RED")
    return {"tripped": bool(reasons), "reasons": reasons,
            "code": "STRATEGY_LAB_CIRCUIT_BREAKER" if reasons else "OK",
            "at": _now_iso()}


# ── Ana çevrim ──────────────────────────────────────────────────────

def run_cycle(adaptive_cfg: dict | None = None,
              force: bool = False) -> dict[str, Any] | None:
    """Tek lab çevrimi. auto_controller döngüsünden (dual_learning
    köprüsünün hemen ardından) çağrılır — ikinci scheduler YOKTUR.
    Uygunluk: interval doldu VEYA force."""
    cfg = adaptive_cfg or {}
    lab_cfg = cfg.get("strategy_lab") or {}
    state = _load_state()
    ctl = _controls(state)
    if not ctl["lab_enabled"] or ctl["emergency_stop"]:
        return None
    last = _parse_iso(state.get("last_cycle"))
    interval = float(lab_cfg.get("cycle_interval_hours")
                     or CYCLE_INTERVAL_HOURS)
    if not force and last and \
            (datetime.now(timezone.utc) - last).total_seconds() / 3600 \
            < interval:
        return None

    dl_state = dl._load_state()
    try:
        import dual_model as _dm
        rt_err = (_dm._load_runtime() or {}).get("last_error")
    except Exception:
        rt_err = None

    result: dict[str, Any] = {"ran_at": _now_iso(), "models": {}}
    events: list[dict] = []

    def _mut(s: dict) -> None:
        c = _controls(s)
        d = {**DEFAULTS, **lab_cfg}
        for model in MODELS:
            ml = _model_lab(s, model)
            ms = (dl_state.get("models") or {}).get(model, {})
            dataset = ms.get("dataset") or []
            losses = [r for r in dataset
                      if float(r.get("net_pnl") or 0) < 0]
            # Panel raporlaması tüm veri üzerinden; ADAY ÜRETİMİ ise
            # SIZINTI OLMASIN diye holdout HARİÇ pencereden beslenir
            # (holdout tasarımı da etkileyemez — untouched).
            ml["loss_diagnosis"] = aggregate_loss_diagnosis(losses)
            ml["profit_capture"] = aggregate_profit_capture(
                dataset, int(d["min_capture_sample"]))
            _split = split_dataset(dataset)
            gen_window = _split["train"] + _split["walk"]
            gen_loss = aggregate_loss_diagnosis(
                [r for r in gen_window
                 if float(r.get("net_pnl") or 0) < 0])
            gen_capture = aggregate_profit_capture(
                gen_window, int(d["min_capture_sample"]))
            gen_diagnosis = dl.diagnose(
                dl.compute_model_metrics(gen_window),
                dl.DEFAULT_THRESHOLDS)
            cb = evaluate_circuit_breaker(ml, dataset, rt_err, cfg)
            ml["circuit_breaker"] = cb
            actions: list[str] = []

            # Aday üretimi (fren + duraklatma + kapasite koşulları)
            active = {cid: cd for cid, cd in ml["candidates"].items()
                      if cd.get("status") == "ACTIVE"}
            if not cb["tripped"] and not c["generation_paused"] and \
                    len(active) < MAX_ACTIVE_CANDIDATES and \
                    len(dataset) >= d["min_dataset_stage1"]:
                new = generate_candidates(
                    model, ml, gen_diagnosis, gen_loss, gen_capture)
                if new:
                    ml["generation"] += 1
                    for cd in new[:MAX_ACTIVE_CANDIDATES - len(active)]:
                        ml["candidates"][cd["strategy_candidate_id"]] = cd
                        events.append({"type": "CANDIDATE_CREATED",
                                       **{k: cd[k] for k in
                                          ("strategy_candidate_id",
                                           "model_family", "generation",
                                           "created_reason",
                                           "parameters")}})
                    actions.append(f"GENERATED:{len(new)}")

            # Aşama ilerletme (çevrim başına aday başına tek adım)
            champ_metrics = ms.get("metrics")
            for cid, cd in list(ml["candidates"].items()):
                if cd.get("status") != "ACTIVE" or cb["tripped"]:
                    continue
                sr = run_stage(cd, model, dataset, champ_metrics, ml, cfg)
                cd["stage_results"][cd["stage"]] = sr
                events.append({"type": "STAGE_RESULT",
                               "candidate": cid, **sr})
                if sr.get("ok") is True:
                    idx = STAGES.index(cd["stage"])
                    if cd["stage"] == "STAGE4_PAPER_SHADOW":
                        if not c["auto_promote_frozen"] and \
                                install_as_challenger(model, cd):
                            cd["stage"] = "STAGE5_PAPER_CHALLENGER"
                            cd["installed_as_challenger"] = True
                            ml["candidates_tested_total"] += 1
                            actions.append(f"CHALLENGER:{cid}")
                        # yuva doluysa beklemede kalır
                    elif idx < len(STAGES) - 1:
                        cd["stage"] = STAGES[idx + 1]
                elif sr.get("ok") is False:
                    cd["status"] = "REJECTED"
                    cd["rejected_reason"] = sr.get("reason")
                    ml["graveyard"].append({
                        "strategy_candidate_id": cid,
                        "fingerprint": cd["fingerprint"],
                        "stage": cd["stage"],
                        "reason": sr.get("reason"),
                        "at": _now_iso()})
                    ml["graveyard"] = ml["graveyard"][-500:]
                    if cd["fingerprint"] not in \
                            ml["rejected_fingerprints"]:
                        ml["rejected_fingerprints"].append(
                            cd["fingerprint"])
                    ml["rejected_fingerprints"] = \
                        ml["rejected_fingerprints"][-2000:]
                    ml["candidates_tested_total"] += 1
                    del ml["candidates"][cid]
                    actions.append(f"REJECTED:{cid}")

            # STAGE5 sonuç takibi: dl state'i TAZE oku — çevrim içinde
            # yapılan kurulum/terfi bayat snapshot'la yanlış "düştü"
            # sayılmasın (mimar bulgusu). Yalnız kurulumu doğrulanmış
            # adaylar başarısız challenger sayılır.
            ms_fresh = (dl._load_state().get("models") or {}).get(
                model, {})
            for cid, cd in list(ml["candidates"].items()):
                if cd.get("stage") != "STAGE5_PAPER_CHALLENGER":
                    continue
                if not cd.get("installed_as_challenger"):
                    continue  # kurulum doğrulanmadan hüküm verilmez
                champ_v = ((ms_fresh.get("champion") or
                            {}).get("version"))
                chal = ms_fresh.get("challenger")
                if champ_v == cid:
                    cd["status"] = "PROMOTED"
                    cd["stage"] = "STAGE6_LIVE_ELIGIBLE"
                    ml["consecutive_failed_challengers"] = 0
                    ml["promotion_history"].append({
                        "candidate": cid, "parameters": cd["parameters"],
                        "at": _now_iso()})
                    events.append({"type": "LAB_PROMOTION",
                                   "candidate": cid, "model": model})
                elif chal is None or \
                        (chal or {}).get("version") != cid:
                    # dl challenger yuvasından düşmüş → reddedildi
                    if cd.get("status") == "ACTIVE":
                        cd["status"] = "REJECTED"
                        cd["rejected_reason"] = "DL_CHALLENGER_DROPPED"
                        ml["consecutive_failed_challengers"] += 1
                        ml["graveyard"].append({
                            "strategy_candidate_id": cid,
                            "fingerprint": cd["fingerprint"],
                            "stage": cd["stage"],
                            "reason": "DL_CHALLENGER_DROPPED",
                            "at": _now_iso()})
                        if cd["fingerprint"] not in \
                                ml["rejected_fingerprints"]:
                            ml["rejected_fingerprints"].append(
                                cd["fingerprint"])
                        del ml["candidates"][cid]

            # Canlıya uygunluk (yalnız etiket; emir yolu kapalı)
            if not c["live_eval_paused"] and champ_metrics:
                le_ = evaluate_live_eligibility(
                    model, dataset, champ_metrics,
                    runtime_healthy=not rt_err, cfg=cfg)
                prev = (ml.get("live_eligibility") or {}).get("status")
                ml["live_eligibility"] = le_
                if le_["status"] != prev:
                    ml["live_eligibility_history"].append(le_)
                    ml["live_eligibility_history"] = \
                        ml["live_eligibility_history"][-100:]
                    events.append({"type": "LIVE_ELIGIBILITY",
                                   "model": model,
                                   "status": le_["status"]})

            result["models"][model] = {
                "dataset": len(dataset),
                "active_candidates": sum(
                    1 for x in ml["candidates"].values()
                    if x.get("status") == "ACTIVE"),
                "circuit_breaker": cb["code"],
                "actions": actions}
        s["last_cycle"] = _now_iso()
        s["last_error"] = None

    try:
        _update_state(_mut)
    except Exception as exc:  # ana döngüyü asla düşürme
        log.warning("strategy_lab çevrim hatası: %s", exc)
        try:
            _update_state(lambda s: s.__setitem__(
                "last_error", str(exc)[:300]))
        except Exception:
            pass
        return None
    for ev in events:
        _append_history({"at": _now_iso(), **ev})
    _append_history({"type": "LAB_CYCLE", **result})
    return result


# ── 15) Kullanıcı kontrolleri ───────────────────────────────────────

def control(action: str, actor: str,
            model: str | None = None) -> dict[str, Any]:
    if action not in CONTROL_ACTIONS:
        return {"ok": False, "error": "UNKNOWN_ACTION"}

    out: dict[str, Any] = {"ok": True, "action": action}

    def _mut(s: dict) -> None:
        c = _controls(s)
        if action == "PAUSE_LAB":
            c["lab_enabled"] = False
        elif action == "RESUME_LAB":
            c["lab_enabled"] = True
        elif action == "PAUSE_GENERATION":
            c["generation_paused"] = True
        elif action == "RESUME_GENERATION":
            c["generation_paused"] = False
        elif action == "FREEZE_AUTO_PROMOTE":
            c["auto_promote_frozen"] = True
        elif action == "UNFREEZE_AUTO_PROMOTE":
            c["auto_promote_frozen"] = False
        elif action == "PAUSE_LIVE_EVAL":
            c["live_eval_paused"] = True
        elif action == "RESUME_LIVE_EVAL":
            c["live_eval_paused"] = False
        elif action == "EMERGENCY_STOP":
            c["emergency_stop"] = True
            c["lab_enabled"] = False
        elif action == "CLEAR_EMERGENCY_STOP":
            c["emergency_stop"] = False

    _update_state(_mut)

    if action == "CANCEL_CHALLENGERS":
        def _dlm(s: dict) -> None:
            for m in MODELS:
                ms = dl._model_state(s, m)
                ms["challenger"] = None
                ms["promotion_readiness"] = {"code": "NO_CHALLENGER"}
        dl._update_state(_dlm)

        def _labm(s: dict) -> None:
            for m in MODELS:
                ml = _model_lab(s, m)
                for cid, cd in list(ml["candidates"].items()):
                    if cd.get("stage") == "STAGE5_PAPER_CHALLENGER" and \
                            cd.get("status") == "ACTIVE":
                        cd["status"] = "REJECTED"
                        cd["rejected_reason"] = "CANCELLED_BY_OPERATOR"
                        ml["graveyard"].append({
                            "strategy_candidate_id": cid,
                            "fingerprint": cd["fingerprint"],
                            "stage": cd["stage"],
                            "reason": "CANCELLED_BY_OPERATOR",
                            "at": _now_iso()})
                        del ml["candidates"][cid]
        _update_state(_labm)

    if action == "REVERT_CHAMPION":
        targets = [model] if model in MODELS else list(MODELS)
        reverted = []

        def _dlr(s: dict) -> None:
            for m in targets:
                ms = dl._model_state(s, m)
                prev = (ms.get("champion") or {}).get("previous")
                if prev:
                    ms["champion"] = prev
                    ms["rollback_history"].append({
                        "reason": "OPERATOR_REVERT", "by": actor,
                        "at": _now_iso()})
                    reverted.append(m)
        dl._update_state(_dlr)
        out["reverted"] = reverted

    _append_history({"type": "CONTROL", "action": action,
                     "actor": actor, "model": model, "at": _now_iso()})
    return out


# ── 14) Panel durumu ────────────────────────────────────────────────

def status() -> dict[str, Any]:
    s = _load_state()
    ctl = dict(s.get("controls") or {})
    dl_state = dl._load_state()
    models: dict[str, Any] = {}
    tot = {"generated": 0, "tested": 0, "rejected": 0, "promoted": 0,
           "active_challengers": 0, "live_eligible": 0, "rollbacks": 0}
    for m in MODELS:
        ml = (s.get("models") or {}).get(m) or {}
        ms = (dl_state.get("models") or {}).get(m) or {}
        cands = ml.get("candidates") or {}
        active = [c for c in cands.values() if c.get("status") == "ACTIVE"]
        grave = ml.get("graveyard") or []
        promos = ml.get("promotion_history") or []
        le_ = ml.get("live_eligibility") or {}
        tot["generated"] += len(cands) + len(grave)
        tot["tested"] += int(ml.get("candidates_tested_total") or 0)
        tot["rejected"] += len(grave)
        tot["promoted"] += len(promos)
        tot["active_challengers"] += 1 if ms.get("challenger") else 0
        tot["live_eligible"] += 1 if le_.get("status") == \
            "LIVE_ELIGIBLE" else 0
        tot["rollbacks"] += len(ml.get("rollback_history") or []) + \
            len(ms.get("rollback_history") or [])
        models[m] = {
            "generation": ml.get("generation", 0),
            "active_candidates": [
                {"id": c["strategy_candidate_id"],
                 "stage": c["stage"],
                 "method": c.get("created_reason"),
                 "created_at": c.get("created_at")}
                for c in active],
            "graveyard_size": len(grave),
            "consecutive_failed_challengers":
                ml.get("consecutive_failed_challengers", 0),
            "circuit_breaker": (ml.get("circuit_breaker") or
                                {}).get("code", "NOT_EVALUATED"),
            "loss_diagnosis": ml.get("loss_diagnosis"),
            "profit_capture": ml.get("profit_capture"),
            "live_eligibility": le_ or {"status": "NOT_EVALUATED"},
            "promotion_history": promos[-5:],
            "dl_champion": (ms.get("champion") or {}).get("version"),
            "dl_challenger": (ms.get("challenger") or {}).get("version")
            if ms.get("challenger") else None,
        }
    return {"controls": ctl, "totals": tot, "models": models,
            "last_cycle": s.get("last_cycle"),
            "last_error": s.get("last_error"),
            "live_orders": "DISABLED"}
