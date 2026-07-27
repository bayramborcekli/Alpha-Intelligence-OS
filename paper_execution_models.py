"""Mission 2100 — Agent 04: Kağıt yürütme servis modelleri.

Değişmez servis sonucu, kapalı işlem/karar/denetim aşaması
enum'ları ve çağıran-sahipli referans kümesi. Kanonik yürütme
modelleri (ExecutionRequest/ExecutionResult/RiskDecision/
KillSwitchSnapshot) KOPYALANMAZ; referansla kullanılır.

Kurallar: frozen+slots+hashable; para/miktar yalnız Decimal (float
yasak); kimlik/zaman/UUID/rastgelelik üretimi yok (tüm kimlikler
çağıran-sahipli); steril kod INVALID_PAPER_EXECUTION_FIELD.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, unique
from typing import Optional, Tuple

from execution_models import ExecutionResult
from paper_execution_errors import PaperExecutionContractError
from paper_models import PaperLedgerSnapshot
from runtime_models import RuntimeAuditRecord

__all__ = ["PaperExecutionOperation", "PaperExecutionDecision",
           "PaperExecutionDecisionCode", "PaperAuditStage",
           "PaperExecutionReferences",
           "PaperExecutionServiceResult"]

_ERROR_INVALID_FIELD = "INVALID_PAPER_EXECUTION_FIELD"

_ZERO = Decimal("0")


def _fail(field: str) -> None:
    raise PaperExecutionContractError(
        f"{_ERROR_INVALID_FIELD}:{field}")


def _require_reference(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(field)


def _require_optional_reference(value: object,
                                field: str) -> None:
    if value is None:
        return
    _require_reference(value, field)


def _require_int(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or \
            value < 0:
        _fail(field)


def _require_enum(value: object, enum_type: type,
                  field: str) -> None:
    if not isinstance(value, enum_type):
        _fail(field)


def _require_tuple_of(value: object, element_type: type,
                      field: str) -> None:
    if not isinstance(value, tuple):
        _fail(field)
    for element in value:
        if not isinstance(element, element_type):
            _fail(field)


@unique
class PaperExecutionOperation(Enum):
    """Kapalı servis işlem kümesi — yalnız yazan işlemler."""

    SUBMIT_ORDER = "SUBMIT_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"


@unique
class PaperExecutionDecision(Enum):
    """Kapalı üst karar kümesi."""

    EXECUTED = "EXECUTED"
    DENIED = "DENIED"
    RECOMMENDATION_ONLY = "RECOMMENDATION_ONLY"


@unique
class PaperExecutionDecisionCode(Enum):
    """Kapalı karar kodu kümesi — steril, deterministik."""

    ORDER_EXECUTED = "ORDER_EXECUTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    MODE_DENIED = "MODE_DENIED"
    POLICY_DENIED = "POLICY_DENIED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_REDUCE_SIZE = "RISK_REDUCE_SIZE"
    RISK_CONFIRMATION_REQUIRED = "RISK_CONFIRMATION_REQUIRED"
    KILL_SWITCH_DENIED = "KILL_SWITCH_DENIED"


@unique
class PaperAuditStage(Enum):
    """Kalıcı boru hattı denetim aşamaları — sıra sabittir."""

    REQUEST_VALIDATED = "REQUEST_VALIDATED"
    MODE_VALIDATED = "MODE_VALIDATED"
    RISK_EVALUATED = "RISK_EVALUATED"
    PERMISSION_EVALUATED = "PERMISSION_EVALUATED"
    KILL_SWITCH_CHECKED = "KILL_SWITCH_CHECKED"
    PAPER_BROKER_INVOKED = "PAPER_BROKER_INVOKED"
    LEDGER_UPDATED = "LEDGER_UPDATED"
    RESULT_MAPPED = "RESULT_MAPPED"


# Karar ↔ kod eşleşmesi (kapalı, dondurulmuş tutarlılık kuralı)
_EXECUTED_CODES = frozenset({
    PaperExecutionDecisionCode.ORDER_EXECUTED,
    PaperExecutionDecisionCode.ORDER_CANCELLED})
_RECOMMENDATION_CODES = frozenset({
    PaperExecutionDecisionCode.RISK_REDUCE_SIZE})
_DENIED_CODES = frozenset({
    PaperExecutionDecisionCode.MODE_DENIED,
    PaperExecutionDecisionCode.POLICY_DENIED,
    PaperExecutionDecisionCode.PERMISSION_DENIED,
    PaperExecutionDecisionCode.RISK_REJECTED,
    PaperExecutionDecisionCode.RISK_CONFIRMATION_REQUIRED,
    PaperExecutionDecisionCode.KILL_SWITCH_DENIED})


@dataclass(frozen=True, slots=True)
class PaperExecutionReferences:
    """Çağıran-sahipli kimlik kümesi — servis kimlik ÜRETMEZ."""

    request_reference: str
    previous_ledger_reference: str
    current_ledger_reference: str
    risk_decision_reference: Optional[str] = None
    kill_switch_reference: Optional[str] = None
    execution_result_reference: Optional[str] = None
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.request_reference,
                           "request_reference")
        _require_reference(self.previous_ledger_reference,
                           "previous_ledger_reference")
        _require_reference(self.current_ledger_reference,
                           "current_ledger_reference")
        _require_optional_reference(self.risk_decision_reference,
                                    "risk_decision_reference")
        _require_optional_reference(self.kill_switch_reference,
                                    "kill_switch_reference")
        _require_optional_reference(
            self.execution_result_reference,
            "execution_result_reference")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class PaperExecutionServiceResult:
    """Değişmez denetlenebilir servis sonucu.

    Karar ile karar kodu kapalı eşleşme kuralına bağlıdır; denetim
    kayıtları yalnız ULAŞILAN aşamaları içerir. `ledger` bir SONRAKİ
    değişmez defter durumudur (reddedilen yollarda girdiyle aynı).
    """

    operation: PaperExecutionOperation
    decision: PaperExecutionDecision
    decision_code: PaperExecutionDecisionCode
    previous_ledger_reference: str
    current_ledger_reference: str
    ledger: PaperLedgerSnapshot
    order_reference: Optional[str] = None
    execution_result: Optional[ExecutionResult] = None
    execution_result_reference: Optional[str] = None
    execution_references: Tuple[str, ...] = ()
    risk_decision_reference: Optional[str] = None
    kill_switch_reference: Optional[str] = None
    recommended_quantity: Optional[Decimal] = None
    audit_records: Tuple[RuntimeAuditRecord, ...] = ()
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_enum(self.operation, PaperExecutionOperation,
                      "operation")
        _require_enum(self.decision, PaperExecutionDecision,
                      "decision")
        _require_enum(self.decision_code,
                      PaperExecutionDecisionCode, "decision_code")
        _require_reference(self.previous_ledger_reference,
                           "previous_ledger_reference")
        _require_reference(self.current_ledger_reference,
                           "current_ledger_reference")
        if not isinstance(self.ledger, PaperLedgerSnapshot):
            _fail("ledger")
        _require_optional_reference(self.order_reference,
                                    "order_reference")
        if self.execution_result is not None and not isinstance(
                self.execution_result, ExecutionResult):
            _fail("execution_result")
        _require_optional_reference(
            self.execution_result_reference,
            "execution_result_reference")
        _require_tuple_of(self.execution_references, str,
                          "execution_references")
        _require_optional_reference(self.risk_decision_reference,
                                    "risk_decision_reference")
        _require_optional_reference(self.kill_switch_reference,
                                    "kill_switch_reference")
        if self.recommended_quantity is not None:
            if isinstance(self.recommended_quantity, bool) or \
                    not isinstance(self.recommended_quantity,
                                   Decimal):
                _fail("recommended_quantity")
            if not self.recommended_quantity.is_finite() or \
                    self.recommended_quantity <= _ZERO:
                _fail("recommended_quantity")
        _require_tuple_of(self.audit_records, RuntimeAuditRecord,
                          "audit_records")
        _require_int(self.logical_sequence, "logical_sequence")
        self._require_consistency()

    def _require_consistency(self) -> None:
        if self.decision is PaperExecutionDecision.EXECUTED:
            if self.decision_code not in _EXECUTED_CODES:
                _fail("decision_code")
        elif self.decision is (PaperExecutionDecision
                               .RECOMMENDATION_ONLY):
            if self.decision_code not in _RECOMMENDATION_CODES:
                _fail("decision_code")
        elif self.decision_code not in _DENIED_CODES:
            _fail("decision_code")

    @property
    def executed(self) -> bool:
        """Yalnız gerçekleşen işlem gerçektir."""
        return self.decision is PaperExecutionDecision.EXECUTED

    def audit_stage_codes(self) -> Tuple[str, ...]:
        """Ulaşılan aşamaların steril olay kodları (sıralı)."""
        return tuple(record.event_code
                     for record in self.audit_records)
