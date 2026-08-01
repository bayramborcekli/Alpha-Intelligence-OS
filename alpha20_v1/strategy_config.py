"""Merkezi Strateji Konfigürasyonu — ADR-015.

Tüm strateji parametreleri BURADAN yönetilir. Hiçbir modül kendi içinde
hard-coded parametre taşımaz; hepsi bu dosyayı import eder.
Bu, çakışmaları önler ve otonom evrimi merkezileştirir.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

# ── PAPER Mod Sabitleri ──
MODE = "PAPER"
EXECUTION_MODE = "PAPER"
LIVE_ORDERS_ENABLED = False

# ── Binance API ──
SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"  # Salt-okunur veri için

# ── CORE Model: ALPHA CORE SCALP ──
CORE = {
    "list_size": 10,
    "min_volume_usdt": 50_000_000,
    "max_spread_pct": 0.05,
    "min_trade_count": 200_000,
    "tp_pct": 0.60,
    "sl_pct": 0.25,
    "max_hold_minutes": 20,
    "trailing_pct": 0.30,
    "min_confidence": 72,
    "max_slippage_pct": 0.03,
    "min_net_reward_risk": 1.20,
    "min_edge_cost_multiple": 1.5,
    "max_open_positions": 2,
    "position_usdt": 150.0,
    "refresh_seconds": 300,
    "signal_seconds": 12,
}

# ── OPPORTUNITY Model: ALPHA OPPORTUNITY BURST ──
OPPORTUNITY = {
    "list_size": 20,
    "min_volume_usdt": 5_000_000,
    "max_spread_pct": 0.15,
    "min_trade_count": 20_000,
    "min_volume_burst": 2.0,
    "min_volatility_pct": 1.5,
    "tp_pct": 1.00,
    "sl_pct": 0.40,
    "max_hold_minutes": 25,
    "trailing_pct": 0.45,
    "min_confidence": 68,
    "max_slippage_pct": 0.08,
    "min_net_reward_risk": 1.20,
    "min_edge_cost_multiple": 1.5,
    "max_open_positions": 2,
    "position_usdt": 80.0,
    "refresh_seconds": 180,
    "signal_seconds": 25,
    "cooldown_after_losses": 2,
    "cooldown_minutes": 15,
}

# ── Genel Limitler ──
TOTAL_MAX_OPEN_POSITIONS = 4
MIN_HOLD_HOURS = 4.0
MONITOR_SECONDS = 4

# ── Paper Learning ──
PAPER_LEARNING = {
    "enabled": True,
    "relaxed_gate": "EMA_VWAP_COMBINED",
}

# ── alpha20.py Legacy Parametreleri ──
LEGACY = {
    "symbols": [
        "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
        "DOGEUSDT","ADAUSDT","LINKUSDT","AVAXUSDT","SUIUSDT",
        "LTCUSDT","BCHUSDT","DOTUSDT","TRXUSDT","UNIUSDT",
        "ETCUSDT","ATOMUSDT","NEARUSDT","ICPUSDT","AAVEUSDT",
    ],
    "interval": "15m",
    "trend_interval": "1h",
    "scan_seconds": 60,
    "starting_balance_usdt": 10000.0,
    "risk_per_trade_pct": 0.5,
    "daily_loss_limit_pct": 1.5,
    "max_consecutive_losses": 3,
    "minimum_score": 72,
    "reward_risk_ratio": 2.0,
    "atr_stop_multiplier": 3.0,
    "fee_safety_factor": 3.0,
    "max_open_positions": 1,
}

# ── Decision Engine ──
DECISION = {
    "final_decision_threshold": 82.0,
    "regime_min_confidence": 65.0,
    "weights": {
        "strategy": 35.0,
        "regime": 20.0,
        "coin": 15.0,
        "liquidity": 10.0,
        "volatility": 10.0,
        "paper_hist": 10.0,
    },
}

# ── Adaptive Risk ──
ADAPTIVE = {
    "enabled": True,
    "mode": "MONITOR",
    "auto_paper_enabled": False,
    "regime_min_confidence": 65.0,
    "final_decision_threshold": 82.0,
    "base_risk_pct": 0.25,
    "max_risk_pct": 0.50,
    "daily_loss_limit_pct": 1.0,
    "max_drawdown_pct": 5.0,
    "max_consecutive_losses": 3,
    "risk_reduction_after_losses": 2,
    "learning_enabled": True,
    "learning_interval_hours": 24.0,
    "minimum_learning_trades": 20,
    "max_daily_weight_change_pct": 5,
    "cooldown_minutes": 60,
    "break_even_enabled": False,
    "trailing_stop_enabled": False,
    "partial_take_profit_enabled": False,
    "kill_switch": False,
}

# ── Fee / Maliyet ──
FEE_RATE = 0.001  # Tek yön %0.1
ROUND_TRIP_FEE_PCT = FEE_RATE * 2 * 100  # %0.2


def get_full_config() -> dict[str, Any]:
    """Tüm konfigürasyonu tek sözlükte döndür."""
    return {
        "mode": MODE,
        "execution_mode": EXECUTION_MODE,
        "live_orders_enabled": LIVE_ORDERS_ENABLED,
        "spot_base": SPOT_BASE,
        "futures_base": FUTURES_BASE,
        "core": dict(CORE),
        "opportunity": dict(OPPORTUNITY),
        "total_max_open_positions": TOTAL_MAX_OPEN_POSITIONS,
        "min_hold_hours": MIN_HOLD_HOURS,
        "monitor_seconds": MONITOR_SECONDS,
        "paper_learning": dict(PAPER_LEARNING),
        "legacy": dict(LEGACY),
        "decision": dict(DECISION),
        "adaptive": dict(ADAPTIVE),
        "fee_rate": FEE_RATE,
        "round_trip_fee_pct": ROUND_TRIP_FEE_PCT,
    }


def merge_into_dual_model_defaults() -> dict[str, Any]:
    """dual_model.py DEFAULTS formatına dönüştür."""
    return {
        "enabled": True,
        "core": dict(CORE),
        "opportunity": dict(OPPORTUNITY),
        "total_max_open_positions": TOTAL_MAX_OPEN_POSITIONS,
        "min_hold_hours": MIN_HOLD_HOURS,
        "monitor_seconds": MONITOR_SECONDS,
        "paper_learning": dict(PAPER_LEARNING),
    }


def merge_into_alpha20_config() -> dict[str, Any]:
    """alpha20.py config.json formatına dönüştür."""
    cfg = dict(LEGACY)
    cfg["mode"] = MODE
    cfg["adaptive_system"] = dict(ADAPTIVE)
    cfg["dual_model"] = {
        "core": {"max_open_positions": CORE["max_open_positions"]},
        "opportunity": {"max_open_positions": OPPORTUNITY["max_open_positions"]},
        "total_max_open_positions": TOTAL_MAX_OPEN_POSITIONS,
        "paper_learning": dict(PAPER_LEARNING),
    }
    cfg["execution_mode"] = EXECUTION_MODE
    return cfg

# ── UI Validation Kuralları (app.py için) ──
SETTING_RULES: dict[str, tuple[str, float, float]] = {
    "minimum_score":           ("int",   0,   100),
    "scan_seconds":            ("int",   15,  3600),
    "risk_per_trade_pct":      ("float", 0.1, 2.0),
    "daily_loss_limit_pct":    ("float", 0.5, 10.0),
    "max_consecutive_losses":  ("int",   1,   10),
    "reward_risk_ratio":       ("float", 1.0, 5.0),
    "atr_stop_multiplier":     ("float", 0.5, 5.0),
    "max_open_positions":      ("int",   1,   5),
    "fee_safety_factor":       ("float", 1.0, 5.0),
}

ADAPTIVE_SETTING_RULES: dict[str, tuple[str, float, float]] = {
    "regime_min_confidence":      ("float", 0,   100),
    "final_decision_threshold":   ("float", 50,  100),
    "base_risk_pct":              ("float", 0.05, 0.50),
    "max_risk_pct":               ("float", 0.05, 0.50),
    "daily_loss_limit_pct":       ("float", 0.1,  5.0),
    "max_drawdown_pct":           ("float", 1.0,  20.0),
    "max_consecutive_losses":     ("int",   1,    10),
    "risk_reduction_after_losses":("int",   1,    10),
    "learning_interval_hours":    ("float", 1,    168),
    "minimum_learning_trades":    ("int",   5,    200),
    "max_daily_weight_change_pct":("float", 0.5,  20.0),
    "cooldown_minutes":           ("int",   0,    1440),
}

DEFAULT_PRESETS = {
    "default": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "top10": ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
              "DOGEUSDT","ADAUSDT","LINKUSDT","AVAXUSDT","SUIUSDT"],
    "top20": ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
              "DOGEUSDT","ADAUSDT","LINKUSDT","AVAXUSDT","SUIUSDT",
              "LTCUSDT","BCHUSDT","DOTUSDT","TRXUSDT","UNIUSDT",
              "ETCUSDT","ATOMUSDT","NEARUSDT","ICPUSDT","AAVEUSDT"],
}

# ── alpha20.py Risk Sınırları ──
MIN_RISK_PCT = 0.25
MAX_RISK_PCT = 0.50
