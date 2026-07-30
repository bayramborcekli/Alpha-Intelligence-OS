"""
learning_engine.py — PAPER işlem sonuçlarından istatistik ve ağırlık güncellemesi.
Sistem kendi kodunu değiştirmez; yalnızca ağırlık JSON dosyasını günceller.
Paper geçmişi toplam karar puanının en fazla %10'unu etkiler.
"""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import metrics_store as ms

ROOT           = Path(__file__).resolve().parent
WEIGHTS_PATH   = ROOT / "learning_weights.json"
SHADOW_PATH    = ROOT / "shadow_weights.json"
STATE_PATH     = ROOT / "state.json"

_LOCK = threading.Lock()

# ── Güven seviyeleri ──────────────────────────────────────────────────────────
CONFIDENCE_INSUFFICIENT = "İstatistiksel olarak yetersiz"   # <5 işlem
CONFIDENCE_LOW          = "Düşük güven"                     # 5-19
CONFIDENCE_MEDIUM       = "Orta güven"                      # 20-49
CONFIDENCE_HIGH         = "Daha yüksek güven"               # 50+

# ── Öğrenme kısıtları ─────────────────────────────────────────────────────────
MAX_DAILY_WEIGHT_CHANGE = 5.0   # bir günde bir ağırlık en fazla %5 değişir
MAX_PAPER_WEIGHT        = 10.0  # paper_hist ağırlığı en fazla %10
MIN_TRAINING_TRADES     = 20    # temel ağırlıkları değiştirmek için min işlem
SHADOW_THRESHOLD        = 20    # shadow modelin aktive olması için min işlem
DECAY_FACTOR            = 0.97  # eski işlemlerin etkisi azalır (EWMA)

# ── Başlangıç sürüm numarası ──────────────────────────────────────────────────
INITIAL_VERSION = 1

DEFAULT_WEIGHTS: dict[str, float] = {
    "strategy":   35.0,
    "regime":     20.0,
    "coin":       15.0,
    "liquidity":  10.0,
    "volatility": 10.0,
    "paper_hist": 10.0,
    "_version":   float(INITIAL_VERSION),
    "_updated_at": "",
}


# ══════════════════════════════════════════════════════════════════════════════
# Dosya yardımcıları
# ══════════════════════════════════════════════════════════════════════════════

def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def load_weights() -> dict[str, Any]:
    if not WEIGHTS_PATH.exists():
        return dict(DEFAULT_WEIGHTS)
    try:
        with WEIGHTS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_WEIGHTS)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_WEIGHTS)


def save_weights(weights: dict[str, Any]) -> None:
    with _LOCK:
        _atomic_write(WEIGHTS_PATH, weights)


def load_shadow_weights() -> dict[str, Any] | None:
    if not SHADOW_PATH.exists():
        return None
    try:
        with SHADOW_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_shadow(weights: dict[str, Any]) -> None:
    _atomic_write(SHADOW_PATH, weights)


def _load_trades() -> list[dict]:
    if not STATE_PATH.exists():
        return []
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            state = json.load(f)
        trades = state.get("trades", [])
        return trades if isinstance(trades, list) else []
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# İstatistik hesaplama
# ══════════════════════════════════════════════════════════════════════════════

def _confidence_label(n: int) -> str:
    if n < 5:   return CONFIDENCE_INSUFFICIENT
    if n < 20:  return CONFIDENCE_LOW
    if n < 50:  return CONFIDENCE_MEDIUM
    return CONFIDENCE_HIGH


def _ewma_weight(index: int, total: int, decay: float = DECAY_FACTOR) -> float:
    """Son işlemlere daha fazla ağırlık; eski işlemler azalır."""
    return decay ** (total - 1 - index)


def _stats(trades: list[dict], weighted: bool = True) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trade_count": 0, "confidence": CONFIDENCE_INSUFFICIENT}
    if n < 5:
        return {
            "trade_count": n,
            "confidence": CONFIDENCE_INSUFFICIENT,
            "insufficient": True,
        }

    # EWMA ağırlıkları
    weights = [_ewma_weight(i, n) for i in range(n)] if weighted else [1.0] * n
    w_total = sum(weights)

    wins      = [(w, t) for w, t in zip(weights, trades) if t.get("result") == "WIN"]
    w_wins    = sum(w for w, _ in wins)
    win_rate  = w_wins / w_total * 100

    pnls      = [float(t.get("pnl", 0) or 0) for t in trades]
    net_pnl   = sum(pnls)

    pos_pnl   = [p for p in pnls if p > 0]
    neg_pnl   = [p for p in pnls if p < 0]
    avg_win   = sum(pos_pnl) / len(pos_pnl) if pos_pnl else 0
    avg_loss  = sum(neg_pnl) / len(neg_pnl) if neg_pnl else 0
    gross_win = sum(pos_pnl)
    gross_los = abs(sum(neg_pnl))
    pf        = round(gross_win / gross_los, 3) if gross_los > 0 else None

    # Beklenen değer (EV)
    ev = (win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss) if (pos_pnl or neg_pnl) else 0

    # Maksimum drawdown
    running = 0.0; peak = 0.0; max_dd = 0.0
    for p in pnls:
        running += p
        peak     = max(peak, running)
        max_dd   = max(max_dd, peak - running)

    # Ardışık zarar
    max_consec = 0; cur_consec = 0
    for t in trades:
        if t.get("result") == "LOSS":
            cur_consec += 1
            max_consec  = max(max_consec, cur_consec)
        else:
            cur_consec  = 0

    # Ortalama süre (saat)
    durations = []
    for t in trades:
        try:
            opened = datetime.fromisoformat(t["opened_at"])
            closed = datetime.fromisoformat(t["closed_at"])
            durations.append((closed - opened).total_seconds() / 3600)
        except Exception:
            pass
    avg_duration = round(sum(durations) / len(durations), 2) if durations else None

    return {
        "trade_count":    n,
        "win_rate":       round(win_rate, 1),
        "net_pnl":        round(net_pnl, 4),
        "avg_win":        round(avg_win, 4),
        "avg_loss":       round(avg_loss, 4),
        "profit_factor":  pf,
        "expected_value": round(ev, 4),
        "max_drawdown":   round(max_dd, 4),
        "max_consecutive_losses": max_consec,
        "avg_duration_h": avg_duration,
        "confidence":     _confidence_label(n),
        "insufficient":   n < 5,
    }


def _group_by(trades: list[dict], key_fn: Any) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for t in trades:
        k = str(key_fn(t))
        groups.setdefault(k, []).append(t)
    return groups


def _hour_key(t: dict) -> str:
    try:
        return str(datetime.fromisoformat(t.get("opened_at", "")).hour)
    except Exception:
        return "?"


def _day_key(t: dict) -> str:
    try:
        return datetime.fromisoformat(t.get("opened_at", "")).strftime("%A")
    except Exception:
        return "?"


def compute_statistics(trades: list[dict]) -> dict[str, Any]:
    """Tüm kategoriler için istatistik hesapla."""
    overall = _stats(trades)

    # Coin bazlı
    by_coin = {sym: _stats(batch) for sym, batch in
               _group_by(trades, lambda t: t.get("symbol", "?")).items()}

    # Yön bazlı
    by_side = {side: _stats(batch) for side, batch in
               _group_by(trades, lambda t: t.get("side", "?")).items()}

    # Saat bazlı
    by_hour = {h: _stats(batch) for h, batch in
               _group_by(trades, _hour_key).items()}

    # Gün bazlı
    by_day  = {d: _stats(batch) for d, batch in
               _group_by(trades, _day_key).items()}

    # Piyasa rejimi bazlı (trade kaydında varsa)
    by_regime = {r: _stats(batch) for r, batch in
                 _group_by(trades, lambda t: t.get("regime", "Bilinmiyor")).items()}

    # En iyi / en kötü coin
    valid_coins = [(sym, s) for sym, s in by_coin.items() if not s.get("insufficient")]
    best_coin  = max(valid_coins, key=lambda x: x[1].get("net_pnl", -999), default=("—", {}))[0]
    worst_coin = min(valid_coins, key=lambda x: x[1].get("net_pnl", 999),  default=("—", {}))[0]

    # En iyi / en kötü rejim
    valid_reg  = [(r, s) for r, s in by_regime.items() if not s.get("insufficient")]
    best_reg   = max(valid_reg, key=lambda x: x[1].get("win_rate", 0), default=("—", {}))[0]
    worst_reg  = min(valid_reg, key=lambda x: x[1].get("win_rate", 100), default=("—", {}))[0]

    return {
        "overall":    overall,
        "by_coin":    by_coin,
        "by_side":    by_side,
        "by_hour":    by_hour,
        "by_day":     by_day,
        "by_regime":  by_regime,
        "best_coin":  best_coin,
        "worst_coin": worst_coin,
        "best_regime": best_reg,
        "worst_regime": worst_reg,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Paper geçmiş puanı (karar motoruna girdi)
# ══════════════════════════════════════════════════════════════════════════════

def get_paper_history_score(symbol: str, trades: list[dict]) -> float:
    """
    Belirli bir sembol için 0-100 paper geçmiş puanı.
    En fazla karar puanının %10'unu etkiler (dışarıda uygulanır).
    <5 işlem: 50 (nötr).
    """
    coin_trades = [t for t in trades if t.get("symbol") == symbol]
    n = len(coin_trades)
    if n < 5:
        return 50.0   # nötr başlangıç

    stats = _stats(coin_trades)
    win_rate = stats.get("win_rate", 50)
    pf       = stats.get("profit_factor") or 1.0
    ev       = stats.get("expected_value", 0)

    # Basit bileşim
    score = (
        win_rate * 0.4
        + min(pf, 3.0) / 3.0 * 100 * 0.4
        + (50 + ev * 1000) * 0.2
    )
    # Güven düzeltmesi: az işlem çok etkili olmasın
    if n < 20:
        score = 50 + (score - 50) * (n / 20)

    return round(max(0, min(100, score)), 2)


# ══════════════════════════════════════════════════════════════════════════════
# Ağırlık güncelleme
# ══════════════════════════════════════════════════════════════════════════════

def _clamp_change(old: float, new: float, max_change: float) -> float:
    """Günlük maksimum değişimi sınırla."""
    delta = new - old
    delta = max(-max_change, min(max_change, delta))
    return round(old + delta, 4)


def _suggest_weights(stats: dict[str, Any], current: dict[str, float]) -> dict[str, float]:
    """İstatistiklere göre yeni ağırlıklar öner. Büyük değişikliklerden kaçın."""
    overall = stats.get("overall", {})
    n       = overall.get("trade_count", 0)
    if n < MIN_TRAINING_TRADES:
        return {k: v for k, v in current.items() if not k.startswith("_")}

    new_w = {k: v for k, v in current.items() if not k.startswith("_")}

    win_rate = overall.get("win_rate", 50)
    pf       = overall.get("profit_factor") or 1.0

    # Paper hist: performans iyiyse ağırlığını artır, kötüyse azalt
    paper_target = current.get("paper_hist", 10.0)
    if win_rate > 55 and pf > 1.5:
        paper_target = min(MAX_PAPER_WEIGHT, paper_target + 1.0)
    elif win_rate < 45 or pf < 0.8:
        paper_target = max(2.0, paper_target - 1.0)
    new_w["paper_hist"] = _clamp_change(current.get("paper_hist", 10), paper_target, MAX_DAILY_WEIGHT_CHANGE)

    # Dengeyi koru: paper_hist değişince strategy'yi kompense et
    diff = new_w["paper_hist"] - current.get("paper_hist", 10)
    new_w["strategy"] = _clamp_change(current.get("strategy", 35), current.get("strategy", 35) - diff, MAX_DAILY_WEIGHT_CHANGE)

    # Toplam 100'e normalize et
    total = sum(new_w.values())
    if abs(total - 100) > 0.1:
        factor = 100.0 / total
        new_w  = {k: round(v * factor, 4) for k, v in new_w.items()}

    return new_w


def run_dual_learning_update(
    adaptive_cfg: dict[str, Any],
    force: bool = False,
) -> dict[str, Any] | None:
    """Dual-model (CORE/OPPORTUNITY) kapanışlarını mevcut öğrenme
    akışına bağlayan köprü. Ayrı bir scheduler DEĞİLDİR: bu fonksiyon
    run_learning_update içinden ve auto_controller döngüsünden
    çağrılır; uygunluk (interval/yeni işlem eşiği) dual_learning
    içinde denetlenir."""
    try:
        import dual_learning as _dl
        return _dl.run_update(adaptive_cfg, force=force)
    except Exception as exc:
        # Öğrenme köprüsü ana motoru asla düşürmez.
        import logging
        logging.getLogger("learning_engine").warning(
            "dual_learning köprü hatası: %s", exc)
        return None


def run_strategy_lab_cycle(
    adaptive_cfg: dict[str, Any],
    force: bool = False,
) -> dict[str, Any] | None:
    """Continuous Strategy Lab köprüsü. dual_learning altyapısının
    uzantısıdır; ayrı scheduler DEĞİLDİR — auto_controller döngüsünden
    dual_learning köprüsünün ardından çağrılır; uygunluk (interval,
    devre kesici, kontroller) strategy_lab içinde denetlenir."""
    try:
        import strategy_lab as _sl
        return _sl.run_cycle(adaptive_cfg, force=force)
    except Exception as exc:
        import logging
        logging.getLogger("learning_engine").warning(
            "strategy_lab köprü hatası: %s", exc)
        return None


def run_learning_update(
    adaptive_cfg: dict[str, Any],
    force: bool = False,
) -> dict[str, Any] | None:
    """
    Öğrenme motorunu çalıştır. Son güncellemeden bu yana yeterli süre geçmediyse None döndür.
    """
    if not adaptive_cfg.get("learning_enabled", True):
        return None

    interval_h = float(adaptive_cfg.get("learning_interval_hours", 24))
    trades     = _load_trades()
    n          = len(trades)

    current_w = load_weights()
    last_upd  = current_w.get("_updated_at", "")

    if not force and last_upd:
        try:
            last_dt  = datetime.fromisoformat(last_upd)
            elapsed  = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if elapsed < interval_h:
                return None
        except Exception:
            pass

    stats = compute_statistics(trades)

    # Eğitim/değerlendirme ayrımı (walk-forward): son %20 validation
    if n >= MIN_TRAINING_TRADES:
        split       = max(5, int(n * 0.8))
        train       = trades[:split]
        validation  = trades[split:]
        train_stats = compute_statistics(train)
    else:
        train       = trades
        validation  = []
        train_stats = stats

    current_weights = {k: v for k, v in current_w.items() if not k.startswith("_")}
    proposed        = _suggest_weights(train_stats, current_weights)
    changes         = {k: round(proposed[k] - current_weights.get(k, 0), 4)
                       for k in proposed if abs(proposed[k] - current_weights.get(k, 0)) > 0.01}

    # Shadow model test
    shadow_result = _run_shadow_test(proposed, validation) if len(validation) >= 5 else None

    # Shadow kötüleşirse uygulama
    apply_new = True
    shadow_note = ""
    if shadow_result:
        if shadow_result.get("worse"):
            apply_new  = False
            shadow_note = "Gölge model daha kötü; mevcut ağırlıklar korundu."
        else:
            shadow_note = f"Gölge model onaylandı (iyileşme: {shadow_result.get('improvement', 0):.2f}%)."

    version = int(current_w.get("_version", INITIAL_VERSION))
    if apply_new and changes:
        version += 1
        new_weights = dict(proposed)
        new_weights["_version"]    = float(version)
        new_weights["_updated_at"] = datetime.now(timezone.utc).isoformat()
        save_weights(new_weights)

        # Gölge ağırlıkları da kaydet
        _save_shadow({"weights": proposed, "tested_at": datetime.now(timezone.utc).isoformat(),
                      "shadow_result": shadow_result or {}})

        ms.append_learning_update(
            version=version, changes=changes, trade_count=n,
            confidence=stats["overall"].get("confidence", CONFIDENCE_INSUFFICIENT),
            shadow_result=shadow_result,
        )

    # Not: dual-model köprüsü (run_dual_learning_update) buradan
    # ÇAĞRILMAZ — auto_controller döngüsü her çevrimde tek kez çağırır
    # (çift tetikleme/gereksiz IO olmasın; uygunluk dual_learning'de).
    return {
        "version":       version,
        "trade_count":   n,
        "confidence":    stats["overall"].get("confidence", CONFIDENCE_INSUFFICIENT),
        "stats":         stats,
        "proposed":      proposed,
        "changes":       changes,
        "applied":       apply_new,
        "shadow_note":   shadow_note,
        "shadow_result": shadow_result,
    }


def _run_shadow_test(
    new_weights: dict[str, float],
    validation_trades: list[dict],
) -> dict[str, Any] | None:
    """
    Yeni ağırlıkları validation setine uygula;
    mevcut ağırlıklarla kıyasla.
    Gerçek paper kararlarını etkilemez.
    """
    if len(validation_trades) < 5:
        return None
    try:
        current_w = load_weights()
        old_w     = {k: v for k, v in current_w.items() if not k.startswith("_")}

        def sim_score(w: dict, t: dict) -> float:
            # Kaydedilmiş component'lardan ağırlıklı toplam hesapla
            comps = t.get("components", {})
            if not comps:
                return 50.0
            return sum(comps.get(k, 50) * w.get(k, 0) / 100 for k in w)

        old_scores = [sim_score(old_w, t) for t in validation_trades]
        new_scores = [sim_score(new_weights, t) for t in validation_trades]

        # Gerçek sonuçla korelasyon (WIN=1, diğer=0)
        actuals = [1 if t.get("result") == "WIN" else 0 for t in validation_trades]

        def corr(scores: list, acts: list) -> float:
            if len(scores) < 2:
                return 0.0
            mean_s = sum(scores) / len(scores)
            mean_a = sum(acts) / len(acts)
            cov    = sum((s - mean_s) * (a - mean_a) for s, a in zip(scores, acts)) / len(scores)
            std_s  = math.sqrt(sum((s - mean_s) ** 2 for s in scores) / len(scores) + 1e-9)
            std_a  = math.sqrt(sum((a - mean_a) ** 2 for a in acts) / len(acts) + 1e-9)
            return cov / (std_s * std_a)

        old_corr = corr(old_scores, actuals)
        new_corr = corr(new_scores, actuals)
        improvement = (new_corr - old_corr) * 100
        worse       = new_corr < old_corr - 0.02   # 2 puan kötüleşme toleransı

        return {
            "old_correlation": round(old_corr, 4),
            "new_correlation": round(new_corr, 4),
            "improvement":     round(improvement, 2),
            "worse":           worse,
            "sample_size":     len(validation_trades),
        }
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Panel verisi
# ══════════════════════════════════════════════════════════════════════════════

def get_panel_data() -> dict[str, Any]:
    trades  = _load_trades()
    stats   = compute_statistics(trades)
    weights = load_weights()
    recent_updates = ms.get_recent_learning_updates(10)

    return {
        "total_trades":   len(trades),
        "confidence":     stats["overall"].get("confidence", CONFIDENCE_INSUFFICIENT),
        "overall":        stats["overall"],
        "best_regime":    stats.get("best_regime", "—"),
        "worst_regime":   stats.get("worst_regime", "—"),
        "best_coin":      stats.get("best_coin", "—"),
        "worst_coin":     stats.get("worst_coin", "—"),
        "by_side":        stats.get("by_side", {}),
        "by_coin":        {k: v for k, v in (stats.get("by_coin") or {}).items()
                           if not v.get("insufficient")},
        "by_hour":        stats.get("by_hour", {}),
        "current_weights": {k: v for k, v in weights.items() if not str(k).startswith("_")},
        "weight_version": int(weights.get("_version", 1)),
        "last_updated":   weights.get("_updated_at", ""),
        "recent_updates": recent_updates,
        "shadow_weights": load_shadow_weights(),
    }
