"""Mission 2000 — Agent 06: Binance Spot yetenek profili.

Binance Spot yetenekleri YALNIZ kanonik `BrokerProfile` üzerinden
açıklanır. Broker-adı dallanması yoktur; çekirdek katmanlar yalnız
yetenek bayraklarını okur.

Spot hesap gerçekleri: kesirli miktar VAR, market emri VAR, iptal
VAR, OCO VAR; margin/short/opsiyon YOK (spot), emir değiştirme YOK
(Binance spot modify desteklemez, iptal+yeniden gönderim gerekir),
kripto 7/24 işlem gördüğü için after-hours kavramı VAR sayılır.

Güvenlik: ağ yok, secret yok, I/O yok.
"""

from __future__ import annotations

from execution_risk_models import BrokerProfile

__all__ = ["binance_spot_profile"]

_PROFILE = BrokerProfile(
    supports_margin=False,
    supports_short=False,
    supports_fractional=True,
    supports_options=False,
    supports_market_orders=True,
    supports_after_hours=True,
    supports_modify=False,
    supports_cancel=True,
    supports_trailing_stop=True,
    supports_oco=True,
)


def binance_spot_profile() -> BrokerProfile:
    """Dondurulmuş Binance Spot yetenek profili (değişmez)."""
    return _PROFILE
