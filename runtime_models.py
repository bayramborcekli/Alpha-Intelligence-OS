"""Mission 2100 — Agent 02: Çalışma zamanı kanonik alan modelleri.

PAPER / SHADOW / MICRO_LIVE çalışma zamanlarının ortak, değişmez
alan modelleri. Bu modül emir YÜRÜTMEZ, dolum SİMÜLE ETMEZ,
broker'a DOKUNMAZ, mutabakat/muhasebe YAPMAZ — yalnız model tanımlar.

Kurallar: frozen+slots+hashable; para/miktar yalnız Decimal (float
yasak); bilinmeyen → None; kimlik/zaman üretimi yok (çağıran-sahipli
referanslar); steril doğrulama kodu INVALID_RUNTIME_MODEL_FIELD.
Yürütme alanı modelleri (ExecutionRequest, RiskDecision, Portfolio
vb.) KOPYALANMAZ — yalnız kanonik enumlar ve string referanslar
kullanılır.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from execution_enums import (OrderSide, OrderState, OrderType,
                             PositionSide)
from runtime_enums import (AuditSeverity, AuthorizationState,
                           HeartbeatStatus, RuntimeEnvironment,
                           RuntimeState)
from runtime_errors import RuntimeContractError

__all__ = ["RuntimeSession", "RuntimeIdentity",
           "RuntimeAccountSnapshot", "RuntimeBalance",
           "RuntimePosition", "RuntimeOrderIntent",
           "RuntimeOrderRecord", "RuntimeExecutionRecord",
           "RuntimeStatistics", "RuntimeHeartbeat",
           "RuntimeConfiguration", "RuntimeLimits",
           "RuntimeAuthorization", "RuntimeAuditRecord"]

_ERROR_INVALID_FIELD = "INVALID_RUNTIME_MODEL_FIELD"


def _fail(field: str) -> None:
    raise RuntimeContractError(f"{_ERROR_INVALID_FIELD}:{field}")


def _require_enum(value: object, enum_type: type,
                  field: str) -> None:
    if not isinstance(value, enum_type):
        _fail(field)


def _require_reference(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(field)


def _require_optional_reference(value: object,
                                field: str) -> None:
    if value is None:
        return
    _require_reference(value, field)


def _require_decimal(value: object, field: str,
                     minimum_exclusive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        _fail(field)
    if not value.is_finite():
        _fail(field)
    if minimum_exclusive:
        if value <= Decimal("0"):
            _fail(field)
    elif value < Decimal("0"):
        _fail(field)


def _require_optional_decimal(value: object, field: str,
                              minimum_exclusive: bool = False
                              ) -> None:
    if value is None:
        return
    _require_decimal(value, field,
                     minimum_exclusive=minimum_exclusive)


def _require_optional_signed_decimal(value: object,
                                     field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, Decimal):
        _fail(field)
    if not value.is_finite():
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


def _require_unique(keys: Tuple[str, ...], field: str) -> None:
    if len(keys) != len(set(keys)):
        _fail(field)


def _require_unique_sequences(elements: tuple,
                              field: str) -> None:
    sequences = tuple(e.logical_sequence for e in elements
                      if e.logical_sequence is not None)
    if len(sequences) != len(set(sequences)):
        _fail(field)


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Değişmez oturum kimliği — çağıran-sahipli referanslar."""

    session_reference: str
    account_reference: str
    environment: RuntimeEnvironment
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.session_reference,
                           "session_reference")
        _require_reference(self.account_reference,
                           "account_reference")
        _require_enum(self.environment, RuntimeEnvironment,
                      "environment")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    """Değişmez çalışma zamanı oturumu."""

    identity: RuntimeIdentity
    state: RuntimeState
    configuration_reference: Optional[str] = None
    started_reference: Optional[str] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_enum(self.identity, RuntimeIdentity, "identity")
        _require_enum(self.state, RuntimeState, "state")
        _require_optional_reference(self.configuration_reference,
                                    "configuration_reference")
        _require_optional_reference(self.started_reference,
                                    "started_reference")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeBalance:
    """Değişmez varlık bakiyesi — negatif bakiye reddedilir."""

    asset: str
    free: Decimal
    locked: Decimal

    def __post_init__(self) -> None:
        _require_reference(self.asset, "asset")
        _require_decimal(self.free, "free")
        _require_decimal(self.locked, "locked")

    @property
    def total(self) -> Decimal:
        """Serbest + kilitli toplamı."""
        return self.free + self.locked


@dataclass(frozen=True, slots=True)
class RuntimePosition:
    """Değişmez pozisyon görünümü — negatif miktar reddedilir."""

    symbol: str
    side: PositionSide
    quantity: Decimal
    entry_price: Optional[Decimal] = None
    position_reference: Optional[str] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.symbol, "symbol")
        _require_enum(self.side, PositionSide, "side")
        _require_decimal(self.quantity, "quantity")
        _require_optional_decimal(self.entry_price, "entry_price",
                                  minimum_exclusive=True)
        _require_optional_reference(self.position_reference,
                                    "position_reference")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeAccountSnapshot:
    """Değişmez hesap anlık görüntüsü — muhasebe YAPMAZ.

    Bakiyelerde varlık, pozisyonlarda sembol tekrarı reddedilir.
    """

    account_reference: str
    balances: Tuple[RuntimeBalance, ...] = ()
    positions: Tuple[RuntimePosition, ...] = ()
    snapshot_reference: Optional[str] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.account_reference,
                           "account_reference")
        _require_tuple_of(self.balances, RuntimeBalance,
                          "balances")
        _require_tuple_of(self.positions, RuntimePosition,
                          "positions")
        _require_unique(tuple(b.asset for b in self.balances),
                        "balances")
        _require_unique(tuple(p.symbol for p in self.positions),
                        "positions")
        _require_unique_sequences(self.positions, "positions")
        _require_optional_reference(self.snapshot_reference,
                                    "snapshot_reference")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeOrderIntent:
    """Değişmez emir niyeti — YÜRÜTME DEĞİLDİR, yalnız kayıttır."""

    intent_reference: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Optional[Decimal] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.intent_reference,
                           "intent_reference")
        _require_reference(self.symbol, "symbol")
        _require_enum(self.side, OrderSide, "side")
        _require_enum(self.order_type, OrderType, "order_type")
        _require_decimal(self.quantity, "quantity",
                         minimum_exclusive=True)
        _require_optional_decimal(self.limit_price, "limit_price",
                                  minimum_exclusive=True)
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeOrderRecord:
    """Değişmez emir kaydı — kanonik OrderState referansı taşır."""

    order_reference: str
    intent_reference: str
    state: OrderState
    filled_quantity: Decimal = Decimal("0")
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.order_reference,
                           "order_reference")
        _require_reference(self.intent_reference,
                           "intent_reference")
        _require_enum(self.state, OrderState, "state")
        _require_decimal(self.filled_quantity, "filled_quantity")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeExecutionRecord:
    """Değişmez gerçekleşme kaydı — dolum ÜRETMEZ, yalnız temsil."""

    execution_reference: str
    order_reference: str
    quantity: Decimal
    price: Decimal
    fee: Optional[Decimal] = None
    fee_asset: Optional[str] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.execution_reference,
                           "execution_reference")
        _require_reference(self.order_reference,
                           "order_reference")
        _require_decimal(self.quantity, "quantity",
                         minimum_exclusive=True)
        _require_decimal(self.price, "price",
                         minimum_exclusive=True)
        _require_optional_decimal(self.fee, "fee")
        _require_optional_reference(self.fee_asset, "fee_asset")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeStatistics:
    """Değişmez oturum istatistikleri — türetme/muhasebe YAPMAZ."""

    session_reference: str
    orders_submitted: int = 0
    orders_filled: int = 0
    orders_rejected: int = 0
    gross_notional: Optional[Decimal] = None
    realized_pnl: Optional[Decimal] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.session_reference,
                           "session_reference")
        _require_int(self.orders_submitted, "orders_submitted")
        _require_int(self.orders_filled, "orders_filled")
        _require_int(self.orders_rejected, "orders_rejected")
        _require_optional_decimal(self.gross_notional,
                                  "gross_notional")
        _require_optional_signed_decimal(self.realized_pnl,
                                         "realized_pnl")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeHeartbeat:
    """Değişmez kalp atışı kaydı — zamanlayıcı DEĞİLDİR."""

    session_reference: str
    status: HeartbeatStatus
    heartbeat_reference: Optional[str] = None
    detail_code: Optional[str] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.session_reference,
                           "session_reference")
        _require_enum(self.status, HeartbeatStatus, "status")
        _require_optional_reference(self.heartbeat_reference,
                                    "heartbeat_reference")
        _require_optional_reference(self.detail_code,
                                    "detail_code")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """Değişmez çalışma zamanı limitleri — yalnız Decimal."""

    maximum_order_notional: Optional[Decimal] = None
    maximum_daily_notional: Optional[Decimal] = None
    maximum_open_orders: Optional[int] = None
    maximum_position_quantity: Optional[Decimal] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_optional_decimal(self.maximum_order_notional,
                                  "maximum_order_notional")
        _require_optional_decimal(self.maximum_daily_notional,
                                  "maximum_daily_notional")
        _require_optional_int(self.maximum_open_orders,
                              "maximum_open_orders")
        _require_optional_decimal(self.maximum_position_quantity,
                                  "maximum_position_quantity")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Değişmez çalışma zamanı yapılandırması."""

    configuration_reference: str
    environment: RuntimeEnvironment
    limits: Optional[RuntimeLimits] = None
    policy_reference: Optional[str] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.configuration_reference,
                           "configuration_reference")
        _require_enum(self.environment, RuntimeEnvironment,
                      "environment")
        if self.limits is not None and not isinstance(
                self.limits, RuntimeLimits):
            _fail("limits")
        _require_optional_reference(self.policy_reference,
                                    "policy_reference")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeAuthorization:
    """Değişmez yetkilendirme kaydı — boş yetkilendirme reddedilir.

    APPROVED durumu grantör referansı olmadan (semantik olarak boş
    yetkilendirme) temsil EDİLEMEZ.
    """

    authorization_reference: str
    state: AuthorizationState
    granted_by_reference: Optional[str] = None
    scope_reference: Optional[str] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.authorization_reference,
                           "authorization_reference")
        _require_enum(self.state, AuthorizationState, "state")
        _require_optional_reference(self.granted_by_reference,
                                    "granted_by_reference")
        _require_optional_reference(self.scope_reference,
                                    "scope_reference")
        if self.state is AuthorizationState.APPROVED and \
                self.granted_by_reference is None:
            _fail("granted_by_reference")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class RuntimeAuditRecord:
    """Değişmez denetim kaydı — steril olay kodu taşır."""

    audit_reference: str
    severity: AuditSeverity
    event_code: str
    subject_reference: Optional[str] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require_reference(self.audit_reference,
                           "audit_reference")
        _require_enum(self.severity, AuditSeverity, "severity")
        _require_reference(self.event_code, "event_code")
        _require_optional_reference(self.subject_reference,
                                    "subject_reference")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")
