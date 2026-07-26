"""
adaptive_risk.py — Piyasa koşullarına göre dinamik risk hesaplama.
Risk hiçbir koşulda zarar sonrası artırılmaz.
Kesin üst sınırlar her zaman uygulanır.
"""
from __future__ import annotations

import math
from typing import Any

import metrics_store as ms
import safety_guard as sg
from market_regime import (
    REGIME_HIGH_VOL, REGIME_INSUFFICIENT, REGIME_UNCLEAR,
    REGIME_SIDEWAYS, REGIME_LOW_VOL,
)

# ── Sabit üst sınırlar (config'e bakılmaksızın geçerli) ──────────────────────
ABS_MAX_RISK_PCT       = 0.50   # işlem başına
ABS_DAILY_LOSS_PCT     = 1.0    # günlük kayıp
ABS_MAX_DRAWDOWN_PCT   = 5.0    # toplam drawdown kill-switch
MIN_REWARD_RISK        = 1.5    # minimum ödül/risk

# ── Pozisyon büyüklüğü limitleri ─────────────────────────────────────────────
MIN_STOP_ATR_MULT      = 0.5    # minimum ATR çarpanı
MAX_STOP_ATR_MULT      = 5.0
MAX_POSITION_PCT_BAL   = 20.0   # bakiyenin en fazla %20'si nominal maruz kalma


class RiskResult:
    __slots__ = ("allowed", "risk_pct", "reason", "reduction_reason",
                 "data_quality_ok", "liquidity_ok")

    def __init__(self, allowed: bool, risk_pct: float, reason: str,
                 reduction_reason: str = "", data_quality_ok: bool = True,
                 liquidity_ok: bool = True) -> None:
        self.allowed         = allowed
        self.risk_pct        = risk_pct
        self.reason          = reason
        self.reduction_reason = reduction_reason
        self.data_quality_ok  = data_quality_ok
        self.liquidity_ok     = liquidity_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed, "risk_pct": self.risk_pct,
            "reason": self.reason, "reduction_reason": self.reduction_reason,
        }


def _log_risk_event(event_type: str, reason: str, details: dict | None = None) -> None:
    try:
        ms.append_risk_event(event_type=event_type, reason=reason, details=details or {})
    except Exception:
        pass


def calculate_risk(
    trading_state: dict[str, Any],
    adaptive_cfg: dict[str, Any],
    regime_info: dict[str, Any] | None = None,
    final_decision_score: float = 0.0,
    liquidity_score: float = 100.0,
    data_quality_score: float = 100.0,
) -> RiskResult:
    """
    İzin verilen risk yüzdesini hesapla.
    Hiçbir koşulda ABS_MAX_RISK_PCT'yi aşmaz.
    """
    base_risk  = float(adaptive_cfg.get("base_risk_pct", 0.25))
    max_risk   = min(float(adaptive_cfg.get("max_risk_pct", 0.50)), ABS_MAX_RISK_PCT)
    consec     = int(trading_state.get("consecutive_losses", 0))
    balance    = float(trading_state.get("balance", 0))

    # 1. Veri kalitesi düşükse işlem yok
    if data_quality_score < 80:
        return RiskResult(False, 0.0,
            f"Veri kalitesi yetersiz ({data_quality_score:.0f}/100 < 80).",
            data_quality_ok=False)

    # 2. Likidite düşükse işlem yok
    if liquidity_score < 60:
        return RiskResult(False, 0.0,
            f"Likidite yetersiz ({liquidity_score:.0f}/100 < 60).",
            liquidity_ok=False)

    # 3. Rejim belirsiz veya yüksek volatilite → işlem yok
    if regime_info:
        reg = regime_info.get("regime", "")
        if reg in (REGIME_INSUFFICIENT, REGIME_UNCLEAR):
            return RiskResult(False, 0.0, f"Piyasa rejimi uygun değil: {reg}.")
        if reg == REGIME_SIDEWAYS and regime_info.get("confidence", 0) < 50:
            return RiskResult(False, 0.0, "Yatay ve belirsiz piyasa.")

    # 4. Zayıf sinyal (karar skoru düşükse)
    if final_decision_score > 0 and final_decision_score < 50:
        return RiskResult(False, 0.0,
            f"Karar skoru çok düşük ({final_decision_score:.0f} < 50).")

    # Risk hesaplama — adım adım azalt, asla artırma
    risk   = base_risk
    reduc  = []

    # 5. Güçlü + doğrulanmış koşulda max risk
    if final_decision_score >= 85 and (regime_info or {}).get("confidence", 0) >= 75:
        risk = max_risk
    # Normal koşul: base_risk

    # 6. Yüksek volatilite: riski yarıya indir
    if regime_info and regime_info.get("regime") == REGIME_HIGH_VOL:
        risk *= 0.5
        reduc.append("Yüksek volatilite: risk %50 azaltıldı.")

    # 7. Art arda 2 zarar: %50 azalt
    risk_reduce_after = int(adaptive_cfg.get("risk_reduction_after_losses", 2))
    max_consec        = int(adaptive_cfg.get("max_consecutive_losses", 3))

    if consec >= max_consec:
        return RiskResult(False, 0.0,
            f"Ardışık zarar limiti ({consec}/{max_consec}): yeni işlem yok.")

    if consec >= risk_reduce_after:
        risk *= 0.5
        reduc.append(f"Ardışık {consec} zarar: risk %50 azaltıldı.")

    # 8. Günlük kâr yüksekse riski artırma
    day_start = float(trading_state.get("day_start_balance", balance) or balance)
    if day_start > 0:
        daily_pnl_pct = (balance - day_start) / day_start * 100
        daily_profit_ceil = 2.0
        if daily_pnl_pct >= daily_profit_ceil:
            risk = min(risk, base_risk)
            reduc.append(f"Günlük kâr %{daily_pnl_pct:.2f}: risk artırılmıyor.")

    # 9. Kesin üst sınır
    risk = min(risk, ABS_MAX_RISK_PCT)
    # Sıfır veya negatif risk gelmesini engelle
    risk = max(risk, 0.01)

    reduction_note = " ".join(reduc)
    if reduction_note:
        _log_risk_event("RISK_REDUCED", reduction_note,
                        {"from_base": base_risk, "to": risk, "consec": consec})

    return RiskResult(True, round(risk, 4), "Risk hesaplandı.", reduction_note)


def calculate_position_size(
    balance: float,
    risk_pct: float,
    entry: float,
    stop: float,
    atr: float,
    atr_stop_multiplier: float,
    adaptive_cfg: dict[str, Any],
) -> tuple[float, float, str]:
    """
    Pozisyon büyüklüğü ve stop mesafesini hesapla.
    (quantity, stop_distance, hata_mesajı) döndürür.
    """
    # ATR çarpanı sınırla
    mult = max(MIN_STOP_ATR_MULT, min(MAX_STOP_ATR_MULT, atr_stop_multiplier))
    stop_distance = atr * mult

    if stop_distance <= 0 or not math.isfinite(stop_distance):
        return 0.0, 0.0, "ATR stop mesafesi geçersiz."

    # Stop en az fiyatın %0.1'i olmalı (aşırı küçük stop engeli)
    min_stop = entry * 0.001
    if stop_distance < min_stop:
        stop_distance = min_stop

    # Maksimum stop mesafesi: fiyatın %10'u
    max_stop = entry * 0.10
    if stop_distance > max_stop:
        stop_distance = max_stop

    risk_usdt = balance * risk_pct / 100
    if risk_usdt <= 0:
        return 0.0, 0.0, "Risk USDT tutarı geçersiz."

    quantity = risk_usdt / stop_distance

    # Nominal maruz kalma limiti
    nominal  = quantity * entry
    max_nom  = balance * MAX_POSITION_PCT_BAL / 100
    if nominal > max_nom:
        quantity = max_nom / entry

    if quantity <= 0 or not math.isfinite(quantity):
        return 0.0, 0.0, "Pozisyon büyüklüğü geçersiz."

    return round(quantity, 8), round(stop_distance, 8), ""


def calculate_targets(
    entry: float,
    stop_distance: float,
    side: str,
    reward_risk: float,
    adaptive_cfg: dict[str, Any],
) -> tuple[float, float, float]:
    """
    (stop, target, actual_rr) döndürür.
    """
    rr = max(MIN_REWARD_RISK, reward_risk)
    if side == "LONG":
        stop   = entry - stop_distance
        target = entry + stop_distance * rr
    else:
        stop   = entry + stop_distance
        target = entry - stop_distance * rr
    actual_rr = rr
    return round(stop, 8), round(target, 8), actual_rr


def get_risk_panel(
    trading_state: dict[str, Any],
    adaptive_cfg: dict[str, Any],
    last_risk_result: RiskResult | None = None,
) -> dict[str, Any]:
    """Panel için risk özetini döndür."""
    balance    = float(trading_state.get("balance", 0))
    day_start  = float(trading_state.get("day_start_balance", balance) or balance)
    consec     = int(trading_state.get("consecutive_losses", 0))
    daily_pnl  = round(balance - day_start, 4)
    daily_pnl_pct = round((daily_pnl / day_start * 100) if day_start > 0 else 0, 3)
    dd_pct     = sg.get_drawdown_pct(trading_state)

    max_risk       = min(float(adaptive_cfg.get("max_risk_pct", 0.50)), ABS_MAX_RISK_PCT)
    daily_lim      = float(adaptive_cfg.get("daily_loss_limit_pct", 1.0))
    max_dd         = float(adaptive_cfg.get("max_drawdown_pct", 5.0))
    max_consec     = int(adaptive_cfg.get("max_consecutive_losses", 3))
    reduce_after   = int(adaptive_cfg.get("risk_reduction_after_losses", 2))

    risk_reduced   = consec >= reduce_after
    trade_allowed  = (last_risk_result.allowed if last_risk_result else None)

    return {
        "current_risk_pct":    last_risk_result.risk_pct if last_risk_result else adaptive_cfg.get("base_risk_pct", 0.25),
        "max_risk_pct":        max_risk,
        "daily_pnl":           daily_pnl,
        "daily_pnl_pct":       daily_pnl_pct,
        "daily_loss_limit_pct": daily_lim,
        "drawdown_pct":        round(dd_pct, 3),
        "max_drawdown_pct":    max_dd,
        "consecutive_losses":  consec,
        "max_consecutive":     max_consec,
        "risk_reduced":        risk_reduced,
        "trade_allowed":       trade_allowed,
        "balance":             round(balance, 4),
    }
