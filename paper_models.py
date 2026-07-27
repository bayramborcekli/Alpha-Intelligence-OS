"""Mission 2100 — Agent 03: Kağıt alan kanonik modelleri.

Deterministik kağıt ticaret alanının değişmez modelleri: emir,
gerçekleşme, pozisyon, defter anlık görüntüsü, bakiye, komisyon,
istatistik ve dolum politikası.

Kurallar: frozen+slots+hashable; para/miktar yalnız Decimal (float
yasak); ortalama maliyet muhasebesi cost_basis üzerinden EXACT
yürütülür (gizli yeniden hesap yok); kimlik/zaman/UUID/rastgelelik
üretimi yok; steril kod INVALID_PAPER_MODEL_FIELD. Yürütme alanı ve
runtime modelleri KOPYALANMAZ — kanonik enumlar referansla kullanılır.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, unique
from typing import Mapping, Optional, Tuple

from execution_enums import OrderSide, OrderState, PositionSide
from paper_errors import PaperContractError

__all__ = ["PaperFillPolicy", "PaperOrder", "PaperExecution",
           "PaperPosition", "PaperBalance", "PaperCommission",
           "PaperLedgerSnapshot", "PaperStatistics"]

_ERROR_INVALID_FIELD = "INVALID_PAPER_MODEL_FIELD"

_ZERO = Decimal("0")


def _fail(field: str) -> None:
    raise PaperContractError(f"{_ERROR_INVALID_FIELD}:{field}")


def _require_reference(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(field)


def _require_enum(value: object, enum_type: type,
                  field: str) -> None:
    if not isinstance(value, enum_type):
        _fail(field)


def _require_decimal(value: object, field: str,
                     positive: bool = False,
                     signed: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        _fail(field)
    if not value.is_finite():
        _fail(field)
    if signed:
        return
    if positive:
        if value <= _ZERO:
            _fail(field)
    elif value < _ZERO:
        _fail(field)


def _require_int(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or \
            value < 0:
        _fail(field)


def _require_optional_int(value: object, field: str) -> None:
    if value is None:
        return
    _require_int(value, field)


def _require_tuple_of(value: object, element_type: type,
                      field: str) -> None:
    if not isinstance(value, tuple):
        _fail(field)
    for element in value:
        if not isinstance(element, element_type):
            _fail(field)


@unique
class PaperFillPolicy(Enum):
    """Tek dolum politikası — kısmi dolum/kuyruk/açık artırma YOK."""

    IMMEDIATE_FULL_FILL = "IMMEDIATE_FULL_FILL"


@dataclass(frozen=True, slots=True)
class PaperCommission:
    """Değişmez komisyon kaydı — negatif komisyon yoktur."""

    amount: Decimal
    asset: str

    def __post_init__(self) -> None:
        _require_decimal(self.amount, "amount")
        _require_reference(self.asset, "asset")


@dataclass(frozen=True, slots=True)
class PaperOrder:
    """Değişmez kağıt emri — fiyat/miktar kesin pozitif Decimal."""

    order_reference: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    state: OrderState
    fill_policy: PaperFillPolicy = (
        PaperFillPolicy.IMMEDIATE_FULL_FILL)
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.order_reference,
                           "order_reference")
        _require_reference(self.symbol, "symbol")
        _require_enum(self.side, OrderSide, "side")
        _require_decimal(self.quantity, "quantity", positive=True)
        _require_decimal(self.price, "price", positive=True)
        _require_enum(self.state, OrderState, "state")
        _require_enum(self.fill_policy, PaperFillPolicy,
                      "fill_policy")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class PaperExecution:
    """Değişmez gerçekleşme — fiyat = gönderilen fiyat (kayma YOK)."""

    execution_reference: str
    order_reference: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    commission: PaperCommission
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.execution_reference,
                           "execution_reference")
        _require_reference(self.order_reference,
                           "order_reference")
        _require_reference(self.symbol, "symbol")
        _require_enum(self.side, OrderSide, "side")
        _require_decimal(self.quantity, "quantity", positive=True)
        _require_decimal(self.price, "price", positive=True)
        _require_enum(self.commission, PaperCommission,
                      "commission")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")

    @property
    def notional(self) -> Decimal:
        """Miktar × fiyat — deterministik."""
        return self.quantity * self.price


@dataclass(frozen=True, slots=True)
class PaperPosition:
    """Değişmez pozisyon — EXACT maliyet esaslı muhasebe.

    average_price türetilmiş görünümdür; muhasebe cost_basis
    üzerinden yürür, gizli yeniden hesap yoktur.
    """

    symbol: str
    quantity: Decimal
    cost_basis: Decimal

    def __post_init__(self) -> None:
        _require_reference(self.symbol, "symbol")
        _require_decimal(self.quantity, "quantity",
                         positive=True)
        _require_decimal(self.cost_basis, "cost_basis")

    @property
    def side(self) -> PositionSide:
        """Kağıt alanında açık pozisyon her zaman LONG'dur."""
        return PositionSide.LONG

    @property
    def average_price(self) -> Decimal:
        """Türetilmiş ortalama fiyat (yalnız görünüm)."""
        return self.cost_basis / self.quantity


@dataclass(frozen=True, slots=True)
class PaperBalance:
    """Değişmez nakit bakiyesi görünümü."""

    asset: str
    free: Decimal
    reserved: Decimal

    def __post_init__(self) -> None:
        _require_reference(self.asset, "asset")
        _require_decimal(self.free, "free")
        _require_decimal(self.reserved, "reserved")

    @property
    def total(self) -> Decimal:
        return self.free + self.reserved


@dataclass(frozen=True, slots=True)
class PaperStatistics:
    """Değişmez oturum istatistikleri — anlık görüntüden türetilir."""

    orders_submitted: int
    orders_filled: int
    executions_recorded: int
    gross_notional: Decimal
    commission_paid: Decimal
    realized_pnl: Decimal

    def __post_init__(self) -> None:
        _require_int(self.orders_submitted, "orders_submitted")
        _require_int(self.orders_filled, "orders_filled")
        _require_int(self.executions_recorded,
                     "executions_recorded")
        _require_decimal(self.gross_notional, "gross_notional")
        _require_decimal(self.commission_paid,
                         "commission_paid")
        _require_decimal(self.realized_pnl, "realized_pnl",
                         signed=True)


@dataclass(frozen=True, slots=True)
class PaperLedgerSnapshot:
    """Değişmez defter anlık görüntüsü — her geçiş YENİ kopya üretir.

    Çift kayıt değişmezi (EXACT, yuvarlama kaymasız):
    cash == initial_cash − Σcost_basis + realized_pnl − commission_paid
    """

    quote_asset: str
    initial_cash: Decimal
    cash: Decimal
    reserved_cash: Decimal
    realized_pnl: Decimal
    commission_paid: Decimal
    positions: Tuple[PaperPosition, ...] = ()
    orders: Tuple[PaperOrder, ...] = ()
    executions: Tuple[PaperExecution, ...] = ()
    sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.quote_asset, "quote_asset")
        _require_decimal(self.initial_cash, "initial_cash")
        _require_decimal(self.cash, "cash")
        _require_decimal(self.reserved_cash, "reserved_cash")
        _require_decimal(self.realized_pnl, "realized_pnl",
                         signed=True)
        _require_decimal(self.commission_paid,
                         "commission_paid")
        _require_tuple_of(self.positions, PaperPosition,
                          "positions")
        _require_tuple_of(self.orders, PaperOrder, "orders")
        _require_tuple_of(self.executions, PaperExecution,
                          "executions")
        symbols = tuple(p.symbol for p in self.positions)
        if len(symbols) != len(set(symbols)):
            _fail("positions")
        references = tuple(o.order_reference
                           for o in self.orders)
        if len(references) != len(set(references)):
            _fail("orders")
        _require_int(self.sequence, "sequence")

    def position_for(self, symbol: str
                     ) -> Optional[PaperPosition]:
        """Sembol için pozisyon (yoksa None) — sabit tarama."""
        _require_reference(symbol, "symbol")
        for position in self.positions:
            if position.symbol == symbol:
                return position
        return None

    def order_for(self, order_reference: str
                  ) -> Optional[PaperOrder]:
        """Referans için emir kaydı (yoksa None)."""
        _require_reference(order_reference, "order_reference")
        for order in self.orders:
            if order.order_reference == order_reference:
                return order
        return None

    @property
    def cost_basis_total(self) -> Decimal:
        """Açık pozisyonların toplam maliyeti."""
        total = _ZERO
        for position in self.positions:
            total = total + position.cost_basis
        return total

    def audit(self) -> bool:
        """Çift kayıt değişmezi — nakit asla kaybolmaz."""
        expected = (self.initial_cash - self.cost_basis_total
                    + self.realized_pnl - self.commission_paid)
        return self.cash == expected and self.cash >= _ZERO \
            and self.reserved_cash >= _ZERO

    def unrealized_pnl(self, prices: Mapping[str, Decimal]
                       ) -> Decimal:
        """Verilen işaret fiyatlarıyla deterministik açık K/Z."""
        total = _ZERO
        for position in self.positions:
            price = prices.get(position.symbol)
            if isinstance(price, bool) or \
                    not isinstance(price, Decimal) or \
                    not price.is_finite() or price <= _ZERO:
                _fail("prices")
            total = total + (position.quantity * price
                             - position.cost_basis)
        return total

    def statistics(self) -> PaperStatistics:
        """Anlık görüntüden türetilmiş deterministik istatistik."""
        filled = 0
        gross = _ZERO
        for order in self.orders:
            if order.state is OrderState.FILLED:
                filled = filled + 1
        for execution in self.executions:
            gross = gross + execution.notional
        return PaperStatistics(
            orders_submitted=len(self.orders),
            orders_filled=filled,
            executions_recorded=len(self.executions),
            gross_notional=gross,
            commission_paid=self.commission_paid,
            realized_pnl=self.realized_pnl)
