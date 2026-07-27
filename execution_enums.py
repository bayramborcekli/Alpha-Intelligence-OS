"""Mission 2000 — Execution Foundation: dondurulmuş yürütme enumları.

Bu modül yürütme alanının kapalı enum kümelerini tanımlar. Tüm
yürütme katmanları YALNIZ bu enumları kullanır. Exchange'e özgü
durumlar adaptör dışına sızamaz.

Güvenlik: I/O yok, ağ yok, zaman/UUID/rastgelelik yok, exchange
importu yok. Yalnız `enum` importu vardır.
"""

from __future__ import annotations

from enum import Enum, unique

__all__ = [
    "OrderSide", "OrderType", "TimeInForce", "OrderState",
    "PositionSide", "ExecutionStatus",
]


@unique
class OrderSide(Enum):
    """Emir yönü."""

    BUY = "BUY"
    SELL = "SELL"


@unique
class OrderType(Enum):
    """Emir tipi."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LIMIT = "STOP_LIMIT"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT = "TAKE_PROFIT"


@unique
class TimeInForce(Enum):
    """Emir geçerlilik süresi."""

    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


@unique
class OrderState(Enum):
    """Kanonik emir durumu (tüm exchange'ler buna normalize edilir)."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@unique
class PositionSide(Enum):
    """Pozisyon yönü."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@unique
class ExecutionStatus(Enum):
    """Yürütme sonucu durumu."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    PARTIAL = "PARTIAL"
