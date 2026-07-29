"""Üç merkezi risk profili — KORUMA / DENGELİ / AGRESİF.

TEK kanonik tanım burasıdır. Değerler misyon sözleşmesidir; sessizce
değiştirilemez (PROFILE_VERSION artmadan değer değişikliği yasak).

İki GERÇEK risk hattına bağlanır (UI etiketi değildir):

1. Paper controller hattı (alpha20_v1/adaptive_risk):
   ``adaptive_flags()`` çıktısı auto_controller'ın bellek-içi
   RUNTIME_ADAPTIVE_OVERRIDE mekanizmasına verilir; base/max risk %,
   günlük zarar ve drawdown limitleri position sizing'i doğrudan
   etkiler (adaptive_risk.calculate_risk / calculate_position_size).
   config.json'a ASLA yazılmaz (runtime-config-drift önlemi).

2. Controlled Execution hattı (execution_risk_engine):
   ``execution_limits(equity)`` gerçek RiskLimits üretir.

Öncelik sırası her zaman: Emergency Stop → Risk Stop → Data
Freshness → kullanıcı profili. Profil hiçbirini bypass edemez.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from services import runtime_preferences as prefs

PROFILE_VERSION = "1.0"

# Kanonik değerler (fraction = kod değeri, pct = yüzde gösterimi).
# adaptive_risk yüzde birimi kullanır (0.15 == %0,15).
PROFILES: dict[str, dict[str, Any]] = {
    "KORUMA": {
        "label": "KORUMA",
        "risk_per_trade_fraction": Decimal("0.0015"),
        "daily_loss_fraction": Decimal("0.0050"),
        "max_drawdown_fraction": Decimal("0.0075"),
        "risk_per_trade_pct": 0.15,
        "daily_loss_pct": 0.50,
        "max_drawdown_pct": 0.75,
    },
    "DENGELI": {
        "label": "DENGELİ",
        "risk_per_trade_fraction": Decimal("0.0025"),
        "daily_loss_fraction": Decimal("0.0100"),
        "max_drawdown_fraction": Decimal("0.0150"),
        "risk_per_trade_pct": 0.25,
        "daily_loss_pct": 1.00,
        "max_drawdown_pct": 1.50,
    },
    "AGRESIF": {
        "label": "AGRESİF",
        "risk_per_trade_fraction": Decimal("0.0050"),
        "daily_loss_fraction": Decimal("0.0200"),
        "max_drawdown_fraction": Decimal("0.0300"),
        "risk_per_trade_pct": 0.50,
        "daily_loss_pct": 2.00,
        "max_drawdown_pct": 3.00,
    },
}


def current_profile_name() -> str:
    return prefs.get("selected_risk_profile")


def current_profile() -> dict[str, Any]:
    name = current_profile_name()
    return {"name": name, "version": PROFILE_VERSION,
            **PROFILES[name]}


def set_profile(name: str) -> str:
    norm = prefs.normalize_profile(name)
    if norm is None:
        raise ValueError(f"INVALID_RISK_PROFILE:{name}")
    prefs.set_prefs(selected_risk_profile=norm)
    return norm


def adaptive_flags(name: str | None = None) -> dict[str, Any]:
    """Paper controller (adaptive_risk) için GERÇEK limit girdileri.

    auto_controller.set_runtime_adaptive_override ile bellekte
    uygulanır; base_risk_pct/max_risk_pct sizing'e, daily_loss/
    max_drawdown limitleri güvenlik kesicilerine gider."""
    p = PROFILES[name or current_profile_name()]
    return {
        "base_risk_pct": p["risk_per_trade_pct"],
        "max_risk_pct": p["risk_per_trade_pct"],
        "daily_loss_limit_pct": p["daily_loss_pct"],
        "max_drawdown_pct": p["max_drawdown_pct"],
    }


def execution_limits(equity_usdt: Decimal,
                     name: str | None = None):
    """Controlled Execution hattı için gerçek RiskLimits."""
    from execution_risk_models import RiskLimits
    p = PROFILES[name or current_profile_name()]
    eq = Decimal(equity_usdt)
    return RiskLimits(
        max_position_size=eq * p["risk_per_trade_fraction"] * 100,
        max_daily_loss=eq * p["daily_loss_fraction"],
        max_exposure=eq,
        max_portfolio_exposure=eq,
    )


def decision_fields(name: str | None = None) -> dict[str, Any]:
    """Her karar kaydına eklenmesi zorunlu profil alanları."""
    n = name or current_profile_name()
    p = PROFILES[n]
    return {
        "selected_risk_profile": p["label"],
        "profile_version": PROFILE_VERSION,
        "risk_per_trade_limit": float(p["risk_per_trade_fraction"]),
        "daily_loss_limit": float(p["daily_loss_fraction"]),
        "maximum_drawdown_limit": float(p["max_drawdown_fraction"]),
    }
