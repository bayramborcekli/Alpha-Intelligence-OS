"""
decision_engine.py — Strateji sinyali, piyasa rejimi, coin puanı ve
risk bilgisini birleştiren karar motoru.
Her bileşen 0-100 arasında; ağırlıklı toplam nihai karar puanını verir.
"""
from __future__ import annotations

from typing import Any

import metrics_store as ms

# ── Varsayılan ağırlıklar (%35+%20+%15+%10+%10+%10 = 100) ───────────────────
DEFAULT_WEIGHTS: dict[str, float] = {
    "strategy":    35.0,
    "regime":      20.0,
    "coin":        15.0,
    "liquidity":   10.0,
    "volatility":  10.0,
    "paper_hist":  10.0,
}

# ── Nihai puan kategorileri ───────────────────────────────────────────────────
SCORE_NONE      = "İşlem Yok"      # 0-49
SCORE_WATCH     = "İzle"           # 50-64
SCORE_WEAK      = "Zayıf Aday"     # 65-74
SCORE_SUITABLE  = "Uygun Aday"     # 75-84
SCORE_STRONG    = "Güçlü Aday"     # 85-100

# Otomatik PAPER için varsayılan minimum
DEFAULT_AUTO_THRESHOLD = 78.0


def _validate_weights(weights: dict[str, float]) -> dict[str, float]:
    """Ağırlıkların toplamı 100 olmalı; değilse normalize et."""
    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    if abs(total - 100.0) > 0.5:
        factor = 100.0 / total
        return {k: round(v * factor, 4) for k, v in weights.items()}
    return weights


def _category(score: float) -> str:
    if score >= 85:
        return SCORE_STRONG
    if score >= 75:
        return SCORE_SUITABLE
    if score >= 65:
        return SCORE_WEAK
    if score >= 50:
        return SCORE_WATCH
    return SCORE_NONE


def _liquidity_score(volume_24h_usdt: float) -> float:
    """Hacme göre 0-100 likidite puanı."""
    if volume_24h_usdt >= 5_000_000_000:
        return 100.0
    if volume_24h_usdt >= 1_000_000_000:
        return 90.0
    if volume_24h_usdt >= 500_000_000:
        return 80.0
    if volume_24h_usdt >= 100_000_000:
        return 70.0
    if volume_24h_usdt >= 50_000_000:
        return 60.0
    if volume_24h_usdt >= 10_000_000:
        return 45.0
    return 20.0


def _volatility_fit_score(atr_pct: float, regime: str) -> float:
    """
    ATR yüzdesi ve rejime göre volatilite uygunluk puanı.
    Çok düşük veya çok yüksek volatilite düşük puan alır.
    """
    from market_regime import REGIME_HIGH_VOL, REGIME_LOW_VOL
    if regime == REGIME_HIGH_VOL:
        return 20.0
    if regime == REGIME_LOW_VOL:
        return 40.0
    # Normal zon: %0.8 – %3.0 ATR
    if 0.8 <= atr_pct <= 3.0:
        return 100.0
    if 0.5 <= atr_pct < 0.8 or 3.0 < atr_pct <= 4.0:
        return 70.0
    if atr_pct < 0.5:
        return 30.0
    return 25.0   # atr_pct > 4.0


def score_decision(
    *,
    strategy_score: float,
    regime_score: float,
    regime_confidence: float,
    coin_score: float,
    volume_24h_usdt: float,
    atr_pct: float,
    regime: str,
    paper_hist_score: float,
    data_quality_score: float,
    weights_override: dict[str, float] | None = None,
) -> tuple[float, str, dict[str, float], str]:
    """
    Nihai karar puanını hesapla.
    Döndürür: (final_score, category, components, reason)
    """
    weights = _validate_weights(weights_override or dict(DEFAULT_WEIGHTS))

    liq_score  = _liquidity_score(volume_24h_usdt)
    vol_fit    = _volatility_fit_score(atr_pct, regime)

    # Veri kalitesi çarpanı: düşük veri kalitesi tüm puanı ölçekler
    dq_factor  = min(1.0, data_quality_score / 100.0)

    components = {
        "strategy":   round(min(100, max(0, strategy_score)), 2),
        "regime":     round(min(100, max(0, regime_score)), 2),
        "coin":       round(min(100, max(0, coin_score)), 2),
        "liquidity":  round(liq_score, 2),
        "volatility": round(vol_fit, 2),
        "paper_hist": round(min(100, max(0, paper_hist_score)), 2),
    }

    # Ağırlıklı toplam
    raw = sum(
        components[k] * weights.get(k, 0) / 100.0
        for k in components
    )
    # Veri kalitesi ölçeği
    final = round(raw * dq_factor, 2)

    # Rejim güveni düşükse ceza
    if regime_confidence < 65:
        penalty = (65 - regime_confidence) * 0.3
        final   = max(0, final - penalty)

    category = _category(final)

    # İnsan okunabilir gerekçe
    reasons: list[str] = []
    if components["strategy"] >= 75:
        reasons.append("Güçlü strateji sinyali.")
    elif components["strategy"] < 50:
        reasons.append("Zayıf strateji sinyali.")
    if components["regime"] >= 70:
        reasons.append("Piyasa rejimi uygun.")
    elif components["regime"] < 40:
        reasons.append("Piyasa rejimi olumsuz.")
    if components["liquidity"] < 60:
        reasons.append("Likidite düşük.")
    if vol_fit < 50:
        reasons.append("Volatilite uygunsuz.")
    if dq_factor < 0.9:
        reasons.append(f"Veri kalitesi düşük (%{data_quality_score:.0f}).")
    if not reasons:
        reasons.append("Dengeli koşullar.")

    return final, category, components, " ".join(reasons)


def check_conditions(
    *,
    final_score: float,
    regime_confidence: float,
    data_quality_score: float,
    liquidity_score: float,
    risk_allowed: bool,
    daily_loss_ok: bool,
    max_positions_ok: bool,
    symbol_no_position: bool,
    cooldown_ok: bool,
    kill_switch_off: bool,
    adaptive_cfg: dict[str, Any],
) -> tuple[bool, str]:
    """
    Otomatik PAPER işlem açma için tüm ek koşulları kontrol et.
    (approved, reason) döndürür.
    """
    threshold      = float(adaptive_cfg.get("final_decision_threshold", DEFAULT_AUTO_THRESHOLD))
    min_reg_conf   = float(adaptive_cfg.get("regime_min_confidence", 65))

    if not kill_switch_off:
        return False, "Acil durdur etkin."
    if not daily_loss_ok:
        return False, "Günlük zarar limiti aşıldı."
    if not max_positions_ok:
        return False, "Maksimum açık pozisyon sayısı doldu."
    if not symbol_no_position:
        return False, "Bu sembolde zaten açık pozisyon var."
    if not cooldown_ok:
        return False, "Cooldown süresi dolmadı."
    if not risk_allowed:
        return False, "Risk motoru izin vermedi."
    if final_score < threshold:
        return False, f"Nihai skor ({final_score:.1f}) eşiğin altında ({threshold})."
    if regime_confidence < min_reg_conf:
        return False, f"Rejim güveni ({regime_confidence:.0f}) yetersiz (min {min_reg_conf:.0f})."
    if data_quality_score < 80:
        return False, f"Veri kalitesi ({data_quality_score:.0f}) yetersiz."
    if liquidity_score < 60:
        return False, f"Likidite ({liquidity_score:.0f}) yetersiz."
    return True, "Tüm koşullar sağlandı."


def calculate_data_quality(
    df_15m_len: int,
    df_1h_len: int,
    timestamp_ok: bool,
    price: float,
    prev_price: float,
) -> float:
    """0-100 veri kalite puanı."""
    score = 100.0

    # Yeterli mum sayısı
    if df_15m_len < 50:
        score -= 30
    elif df_15m_len < 80:
        score -= 10
    if df_1h_len < 50:
        score -= 20
    elif df_1h_len < 80:
        score -= 5

    # Zaman damgası tutarlılığı
    if not timestamp_ok:
        score -= 20

    # Ani fiyat sıçraması kontrolü (%10'dan fazla anlık değişim şüpheli)
    if prev_price > 0:
        chg = abs(price - prev_price) / prev_price * 100
        if chg > 10:
            score -= 25
        elif chg > 5:
            score -= 10

    return max(0.0, min(100.0, score))


def log_decision(
    *,
    symbol: str,
    price: float,
    side: str | None,
    final_score: float,
    category: str,
    components: dict[str, float],
    regime: str,
    regime_confidence: float,
    strategy_score: float,
    risk_pct: float,
    stop: float | None,
    target: float | None,
    decision: str,
    reason: str,
    config_version: str = "1",
    trace: dict | None = None,
) -> None:
    try:
        ms.append_decision(
            symbol=symbol, price=price, regime=regime,
            regime_confidence=regime_confidence,
            strategy_score=strategy_score, final_score=final_score,
            risk_pct=risk_pct, stop=stop, target=target,
            decision=decision, reason=reason,
            config_version=config_version, components=components,
            trace=trace,
        )
    except Exception:
        pass
