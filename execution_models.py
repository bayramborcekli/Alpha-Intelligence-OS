"""Mission 2000 — Execution Foundation: değişmez yürütme alan modelleri.

Kanonik yürütme sözleşmeleri. Tüm yürütme katmanları YALNIZ bu
modelleri kullanır. Her model dondurulmuş (frozen=True, slots=True)
dataclass'tır: değişmez, hashlenebilir, mutable varsayılan içermez.

Para/miktar alanları YALNIZ Decimal kabul eder (float/int/bool
reddedilir — sterile INVALID_MODEL_FIELD). Bilinmeyen değer null'dur;
asla 0 değildir.

Meta veri sahipliği: `ExecutionMetadata` alanlarını (execution_id,
requested_at, processed_at, correlation_id) YALNIZ Execution API
üretir; bu modül asla üretmez (zaman/UUID importu yoktur), alt
katmanlar yalnız taşır.

Güvenlik: I/O yok, ağ yok, kalıcılık yok, exchange importu yok,
zaman/UUID/rastgelelik yok, ortam/secret erişimi yok.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Optional, Tuple

from execution_enums import (
    ExecutionStatus,
    OrderSide,
    OrderState,
    OrderType,
    PositionSide,
    TimeInForce,
)

__all__ = [
    "ExecutionRequest", "ExecutionResult", "Order", "Position",
    "Fill", "ExecutionMetadata", "ValidationResult",
]

_ERROR_INVALID_FIELD = "INVALID_MODEL_FIELD"

# Meta veri alanları — YALNIZ Execution API üretir
_METADATA_FIELDS = ("execution_id", "requested_at", "processed_at",
                   "correlation_id")


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError(_ERROR_INVALID_FIELD)


def _is_decimal(value: object) -> bool:
    return isinstance(value, Decimal)


def _is_optional_decimal(value: object) -> bool:
    return value is None or isinstance(value, Decimal)


def _is_optional_str(value: object) -> bool:
    return value is None or isinstance(value, str)


@dataclass(frozen=True, slots=True)
class ExecutionMetadata:
    """Yürütme meta verisi — üretimi YALNIZ Execution API'de."""

    execution_id: Optional[str] = None
    requested_at: Optional[str] = None
    processed_at: Optional[str] = None
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        for field in fields(self):
            _require(_is_optional_str(getattr(self, field.name)))


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """Yürütme isteği — YALNIZ girdi."""

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    time_in_force: TimeInForce
    price: Optional[Decimal] = None
    metadata: Optional[ExecutionMetadata] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.symbol, str) and bool(self.symbol))
        _require(isinstance(self.side, OrderSide))
        _require(isinstance(self.order_type, OrderType))
        _require(_is_decimal(self.quantity))
        _require(isinstance(self.time_in_force, TimeInForce))
        _require(_is_optional_decimal(self.price))
        _require(self.metadata is None or
                 isinstance(self.metadata, ExecutionMetadata))


@dataclass(frozen=True, slots=True)
class Order:
    """Kanonik emir durumu."""

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    time_in_force: TimeInForce
    state: OrderState
    price: Optional[Decimal] = None
    filled_quantity: Optional[Decimal] = None
    order_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.symbol, str) and bool(self.symbol))
        _require(isinstance(self.side, OrderSide))
        _require(isinstance(self.order_type, OrderType))
        _require(_is_decimal(self.quantity))
        _require(isinstance(self.time_in_force, TimeInForce))
        _require(isinstance(self.state, OrderState))
        _require(_is_optional_decimal(self.price))
        _require(_is_optional_decimal(self.filled_quantity))
        _require(_is_optional_str(self.order_id))


@dataclass(frozen=True, slots=True)
class Position:
    """Portföy pozisyonu."""

    symbol: str
    side: PositionSide
    quantity: Decimal
    entry_price: Optional[Decimal] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.symbol, str) and bool(self.symbol))
        _require(isinstance(self.side, PositionSide))
        _require(_is_decimal(self.quantity))
        _require(_is_optional_decimal(self.entry_price))


@dataclass(frozen=True, slots=True)
class Fill:
    """Exchange yürütme olayı (dolum)."""

    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Optional[Decimal] = None
    fee_asset: Optional[str] = None
    trade_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.symbol, str) and bool(self.symbol))
        _require(isinstance(self.side, OrderSide))
        _require(_is_decimal(self.quantity))
        _require(_is_decimal(self.price))
        _require(_is_optional_decimal(self.fee))
        _require(_is_optional_str(self.fee_asset))
        _require(_is_optional_str(self.trade_id))


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Risk doğrulama sonucu."""

    approved: bool
    code: Optional[str] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.approved, bool))
        _require(_is_optional_str(self.code))


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Yürütme sonucu — YALNIZ çıktı."""

    status: ExecutionStatus
    order: Optional[Order] = None
    fills: Tuple[Fill, ...] = ()
    code: Optional[str] = None
    metadata: Optional[ExecutionMetadata] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.status, ExecutionStatus))
        _require(self.order is None or isinstance(self.order, Order))
        _require(isinstance(self.fills, tuple))
        for fill in self.fills:
            _require(isinstance(fill, Fill))
        _require(_is_optional_str(self.code))
        _require(self.metadata is None or
                 isinstance(self.metadata, ExecutionMetadata))
