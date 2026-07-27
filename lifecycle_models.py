"""Mission 2100 — Agent 07: Emir yaşam döngüsü & mutabakat modelleri.

Tamamı değişmez (frozen+slots) veri sınıfları ve kapalı enum'lar.
Bu katman emir yaşam döngüsünü DETERMİNİSTİK kaydeder ve Paper /
Shadow / gelecekteki Micro Live sonuçlarını MUTABAKATA tabi tutar:
emir vermez, borsaya/broker'a BAĞLANMAZ, broker durumu değiştirmez,
işlem YÜRÜTMEZ.

Kimlik / zaman / UUID / rastgelelik ÜRETİLMEZ — tüm kimlikler,
zaman damgaları ve mantıksal sıralar çağıran-sahiplidir. Finansal
değerler YALNIZ Decimal; eksik zorunlu alan sözleşme hatasıyla
REDDEDİLİR (fail-closed).

Bilinçli ek modeller (spesifikasyon listesi dışında, raporda
gerekçeli): ReconciliationMismatch (tekil uyuşmazlık kaydı),
ReconciliationAudit (mutabakat denetim kaydı) ve
ReconciliationStatistics (türetilmiş sayaçlar).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, unique
from typing import Optional, Tuple

from execution_enums import OrderSide
from reconciliation_errors import (LifecycleContractError,
                                   ReconciliationContractError)

__all__ = ["OrderLifecycleState", "LifecycleOperation",
           "ReconciliationSource", "ReconciliationMismatchCode",
           "ReconciliationDecision", "ReconciliationReportDecision",
           "LifecycleEvent", "LifecycleAudit", "OrderLifecycle",
           "OrderSnapshot", "ReconciliationMismatch",
           "ReconciliationAudit", "ReconciliationResult",
           "ReconciliationStatistics", "ReconciliationReport"]

_ERROR_LIFECYCLE_FIELD = "INVALID_LIFECYCLE_FIELD"
_ERROR_RECONCILIATION_FIELD = "INVALID_RECONCILIATION_FIELD"

_ZERO = Decimal("0")


def _fail_lifecycle(field: str) -> None:
    raise LifecycleContractError(
        f"{_ERROR_LIFECYCLE_FIELD}:{field}")


def _fail_reconciliation(field: str) -> None:
    raise ReconciliationContractError(
        f"{_ERROR_RECONCILIATION_FIELD}:{field}")


def _require_reference(value: object, field: str,
                       fail) -> None:
    if not isinstance(value, str) or not value.strip():
        fail(field)


def _require_optional_reference(value: object, field: str,
                                fail) -> None:
    if value is None:
        return
    _require_reference(value, field, fail)


def _require_int(value: object, field: str, fail) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or \
            value < 0:
        fail(field)


def _require_enum(value: object, enum_type: type, field: str,
                  fail) -> None:
    if not isinstance(value, enum_type):
        fail(field)


def _require_positive_decimal(value: object, field: str,
                              fail) -> None:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        fail(field)
    if not value.is_finite() or value <= _ZERO:
        fail(field)


def _require_optional_positive_decimal(value: object, field: str,
                                       fail) -> None:
    if value is None:
        return
    _require_positive_decimal(value, field, fail)


def _require_optional_signed_decimal(value: object, field: str,
                                     fail) -> None:
    """PnL işaretli olabilir — sonlu Decimal yeterlidir."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, Decimal):
        fail(field)
    if not value.is_finite():
        fail(field)


def _require_tuple_of(value: object, element_type: type,
                      field: str, fail) -> None:
    if not isinstance(value, tuple):
        fail(field)
    for element in value:
        if not isinstance(element, element_type):
            fail(field)


@unique
class OrderLifecycleState(Enum):
    """Kapalı emir durum kümesi — örtük durum YOKTUR."""

    NEW = "NEW"
    VALIDATED = "VALIDATED"
    ACCEPTED = "ACCEPTED"
    QUEUED = "QUEUED"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


@unique
class LifecycleOperation(Enum):
    """Kapalı geçiş işlemi kümesi.

    QUEUE ve FAIL bilinçli eklerdir: QUEUED ve FAILED durumları
    spesifikasyonda tanımlıdır ve deterministik bir geçişle
    ulaşılabilir olmak ZORUNDADIR."""

    VALIDATE = "VALIDATE"
    ACCEPT = "ACCEPT"
    QUEUE = "QUEUE"
    SUBMIT = "SUBMIT"
    FILL = "FILL"
    CANCEL = "CANCEL"
    REJECT = "REJECT"
    FAIL = "FAIL"
    CLOSE = "CLOSE"


@unique
class ReconciliationSource(Enum):
    """Kapalı mutabakat kaynak zinciri (sabit sıra)."""

    EXECUTION_REQUEST = "EXECUTION_REQUEST"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    MICRO_LIVE = "MICRO_LIVE"


@unique
class ReconciliationMismatchCode(Enum):
    """Kapalı uyuşmazlık kodu kümesi (steril)."""

    MISSING_ORDER = "MISSING_ORDER"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    DUPLICATE_EXECUTION = "DUPLICATE_EXECUTION"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    PNL_MISMATCH = "PNL_MISMATCH"
    TIMESTAMP_SEQUENCE_VIOLATION = "TIMESTAMP_SEQUENCE_VIOLATION"
    LOGICAL_SEQUENCE_VIOLATION = "LOGICAL_SEQUENCE_VIOLATION"


@unique
class ReconciliationDecision(Enum):
    """Emir başına kapalı mutabakat kararı."""

    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    MISSING = "MISSING"


@unique
class ReconciliationReportDecision(Enum):
    """Rapor başına kapalı üst karar."""

    RECONCILED = "RECONCILED"
    DISCREPANT = "DISCREPANT"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """Tekil, değişmez geçiş kaydı — her geçiş loglanır."""

    event_reference: str
    order_reference: str
    operation: LifecycleOperation
    from_state: OrderLifecycleState
    to_state: OrderLifecycleState
    logical_sequence: int

    def __post_init__(self) -> None:
        _require_reference(self.event_reference,
                           "event_reference", _fail_lifecycle)
        _require_reference(self.order_reference,
                           "order_reference", _fail_lifecycle)
        _require_enum(self.operation, LifecycleOperation,
                      "operation", _fail_lifecycle)
        _require_enum(self.from_state, OrderLifecycleState,
                      "from_state", _fail_lifecycle)
        _require_enum(self.to_state, OrderLifecycleState,
                      "to_state", _fail_lifecycle)
        _require_int(self.logical_sequence, "logical_sequence",
                     _fail_lifecycle)


@dataclass(frozen=True, slots=True)
class LifecycleAudit:
    """Değişmez yaşam döngüsü denetim kaydı (steril kod)."""

    audit_code: str
    order_reference: str
    logical_sequence: int

    def __post_init__(self) -> None:
        _require_reference(self.audit_code, "audit_code",
                           _fail_lifecycle)
        _require_reference(self.order_reference,
                           "order_reference", _fail_lifecycle)
        _require_int(self.logical_sequence, "logical_sequence",
                     _fail_lifecycle)


@dataclass(frozen=True, slots=True)
class OrderLifecycle:
    """Değişmez emir yaşam döngüsü durumu.

    Servis durumsuzdur; her geçiş YENİ bir OrderLifecycle üretir.
    Gizli mutasyon YOKTUR."""

    order_reference: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Optional[Decimal] = None
    state: OrderLifecycleState = OrderLifecycleState.NEW
    filled_quantity: Optional[Decimal] = None
    filled_price: Optional[Decimal] = None
    events: Tuple[LifecycleEvent, ...] = ()
    audit: Tuple[LifecycleAudit, ...] = ()
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.order_reference,
                           "order_reference", _fail_lifecycle)
        _require_reference(self.symbol, "symbol", _fail_lifecycle)
        _require_enum(self.side, OrderSide, "side",
                      _fail_lifecycle)
        _require_positive_decimal(self.quantity, "quantity",
                                  _fail_lifecycle)
        _require_optional_positive_decimal(self.price, "price",
                                           _fail_lifecycle)
        _require_enum(self.state, OrderLifecycleState, "state",
                      _fail_lifecycle)
        _require_optional_positive_decimal(
            self.filled_quantity, "filled_quantity",
            _fail_lifecycle)
        _require_optional_positive_decimal(
            self.filled_price, "filled_price", _fail_lifecycle)
        _require_tuple_of(self.events, LifecycleEvent, "events",
                          _fail_lifecycle)
        _require_tuple_of(self.audit, LifecycleAudit, "audit",
                          _fail_lifecycle)
        _require_int(self.logical_sequence, "logical_sequence",
                     _fail_lifecycle)


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    """Tek kaynaktan gelen değişmez emir görüntüsü.

    Mutabakat girdisi — kaynak katmanına GERİ YAZILMAZ."""

    source: ReconciliationSource
    order_reference: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    status: OrderLifecycleState
    price: Optional[Decimal] = None
    pnl: Optional[Decimal] = None
    timestamp: int = 0
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_enum(self.source, ReconciliationSource, "source",
                      _fail_reconciliation)
        _require_reference(self.order_reference,
                           "order_reference", _fail_reconciliation)
        _require_reference(self.symbol, "symbol",
                           _fail_reconciliation)
        _require_enum(self.side, OrderSide, "side",
                      _fail_reconciliation)
        _require_positive_decimal(self.quantity, "quantity",
                                  _fail_reconciliation)
        _require_enum(self.status, OrderLifecycleState, "status",
                      _fail_reconciliation)
        _require_optional_positive_decimal(
            self.price, "price", _fail_reconciliation)
        _require_optional_signed_decimal(
            self.pnl, "pnl", _fail_reconciliation)
        _require_int(self.timestamp, "timestamp",
                     _fail_reconciliation)
        _require_int(self.logical_sequence, "logical_sequence",
                     _fail_reconciliation)


@dataclass(frozen=True, slots=True)
class ReconciliationMismatch:
    """Tekil, değişmez uyuşmazlık kaydı (steril)."""

    mismatch_code: ReconciliationMismatchCode
    order_reference: str
    source_a: ReconciliationSource
    source_b: ReconciliationSource
    logical_sequence: int

    def __post_init__(self) -> None:
        _require_enum(self.mismatch_code,
                      ReconciliationMismatchCode, "mismatch_code",
                      _fail_reconciliation)
        _require_reference(self.order_reference,
                           "order_reference", _fail_reconciliation)
        _require_enum(self.source_a, ReconciliationSource,
                      "source_a", _fail_reconciliation)
        _require_enum(self.source_b, ReconciliationSource,
                      "source_b", _fail_reconciliation)
        _require_int(self.logical_sequence, "logical_sequence",
                     _fail_reconciliation)


@dataclass(frozen=True, slots=True)
class ReconciliationAudit:
    """Değişmez mutabakat denetim kaydı — her uyuşmazlık kaydedilir."""

    audit_code: str
    logical_sequence: int
    order_reference: Optional[str] = None

    def __post_init__(self) -> None:
        _require_reference(self.audit_code, "audit_code",
                           _fail_reconciliation)
        _require_int(self.logical_sequence, "logical_sequence",
                     _fail_reconciliation)
        _require_optional_reference(self.order_reference,
                                    "order_reference",
                                    _fail_reconciliation)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Emir başına değişmez mutabakat sonucu."""

    order_reference: str
    decision: ReconciliationDecision
    mismatches: Tuple[ReconciliationMismatch, ...] = ()
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.order_reference,
                           "order_reference", _fail_reconciliation)
        _require_enum(self.decision, ReconciliationDecision,
                      "decision", _fail_reconciliation)
        _require_tuple_of(self.mismatches, ReconciliationMismatch,
                          "mismatches", _fail_reconciliation)
        _require_int(self.logical_sequence, "logical_sequence",
                     _fail_reconciliation)
        if self.decision is ReconciliationDecision.MATCHED and \
                self.mismatches:
            _fail_reconciliation("mismatches")
        if self.decision is not ReconciliationDecision.MATCHED \
                and not self.mismatches:
            _fail_reconciliation("mismatches")


@dataclass(frozen=True, slots=True)
class ReconciliationStatistics:
    """Türetilmiş, değişmez sayaçlar (bilinçli ek model)."""

    total_orders: int
    matched_orders: int
    mismatched_orders: int
    missing_orders: int
    total_mismatches: int

    def __post_init__(self) -> None:
        _require_int(self.total_orders, "total_orders",
                     _fail_reconciliation)
        _require_int(self.matched_orders, "matched_orders",
                     _fail_reconciliation)
        _require_int(self.mismatched_orders, "mismatched_orders",
                     _fail_reconciliation)
        _require_int(self.missing_orders, "missing_orders",
                     _fail_reconciliation)
        _require_int(self.total_mismatches, "total_mismatches",
                     _fail_reconciliation)
        expected = self.matched_orders + self.mismatched_orders \
            + self.missing_orders
        if expected != self.total_orders:
            _fail_reconciliation("total_orders")


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Nihai, değişmez mutabakat raporu."""

    report_reference: str
    decision: ReconciliationReportDecision
    results: Tuple[ReconciliationResult, ...]
    statistics: ReconciliationStatistics
    audit: Tuple[ReconciliationAudit, ...] = ()
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.report_reference,
                           "report_reference", _fail_reconciliation)
        _require_enum(self.decision, ReconciliationReportDecision,
                      "decision", _fail_reconciliation)
        _require_tuple_of(self.results, ReconciliationResult,
                          "results", _fail_reconciliation)
        _require_enum(self.statistics, ReconciliationStatistics,
                      "statistics", _fail_reconciliation)
        _require_tuple_of(self.audit, ReconciliationAudit,
                          "audit", _fail_reconciliation)
        _require_int(self.logical_sequence, "logical_sequence",
                     _fail_reconciliation)
