"""Mission 2000 — Execution Foundation: kanonik emir durum makinesi.

Deterministik, dondurulmuş yaşam döngüsü. Onaylı geçişler dışında her
geçiş yasaktır. FILLED / CANCELLED / REJECTED / EXPIRED terminal
durumlardır (çıkış geçişi yoktur).

Exchange normalizasyonu: exchange'e özgü durumlar YALNIZ adaptör
katmanında kanonik `OrderState`'e çevrilir; dışarı sızamaz. Binance
Spot eşlemesi `_BINANCE_STATE_NORMALIZATION` sabitindedir.

Güvenlik: I/O yok, ağ yok, zaman/UUID/rastgelelik yok. Sterile hata:
INVALID_ORDER_STATE.
"""

from __future__ import annotations

from types import MappingProxyType

from execution_enums import OrderState

__all__ = ["validate_transition"]

_ERROR_INVALID_STATE = "INVALID_ORDER_STATE"

# Onaylı geçiş grafiği (dondurulmuş, deterministik)
_APPROVED_TRANSITIONS = MappingProxyType({
    OrderState.CREATED: (OrderState.VALIDATED,),
    OrderState.VALIDATED: (OrderState.SUBMITTED, OrderState.REJECTED),
    OrderState.SUBMITTED: (OrderState.ACKNOWLEDGED,
                           OrderState.CANCELLED, OrderState.EXPIRED),
    OrderState.ACKNOWLEDGED: (OrderState.PARTIALLY_FILLED,
                              OrderState.FILLED),
    OrderState.PARTIALLY_FILLED: (OrderState.FILLED,
                                  OrderState.CANCELLED),
    OrderState.FILLED: (),
    OrderState.CANCELLED: (),
    OrderState.REJECTED: (),
    OrderState.EXPIRED: (),
})

# Terminal durumlar (çıkış geçişi yok)
_TERMINAL_STATES = (OrderState.FILLED, OrderState.CANCELLED,
                   OrderState.REJECTED, OrderState.EXPIRED)

# Binance Spot → kanonik OrderState eşlemesi (adaptör katmanı için
# belgelenmiş normalizasyon tablosu; exchange durumu dışarı sızamaz)
_BINANCE_STATE_NORMALIZATION = MappingProxyType({
    "NEW": OrderState.SUBMITTED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
    "FILLED": OrderState.FILLED,
    "CANCELED": OrderState.CANCELLED,
    "REJECTED": OrderState.REJECTED,
})


def validate_transition(current: OrderState,
                        target: OrderState) -> bool:
    """Kanonik durum geçişini doğrular.

    Onaylı geçiş → True; yasak geçiş → False. Girdi `OrderState`
    değilse sterile INVALID_ORDER_STATE yükselir. Deterministiktir:
    aynı girdi her zaman aynı sonucu verir; yan etki yoktur.
    """
    if not isinstance(current, OrderState) or \
            not isinstance(target, OrderState):
        raise ValueError(_ERROR_INVALID_STATE)
    return target in _APPROVED_TRANSITIONS[current]
