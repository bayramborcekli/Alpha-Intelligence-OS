"""Mission 2000 — Agent 07: Yürütme Servisi orkestrasyon modelleri.

Yalnız servis-sahipli orkestrasyon modelleri tanımlanır; kanonik
kavramlar (ExecutionRequest, Order, RiskDecision,
BrokerOperationResult, Portfolio, Instrument, ExecutionMode, ...)
sahibi modüllerden import edilir — KOPYA yoktur.

Tüm modeller frozen+slots, açık tipli, deterministik, mutable
varsayılansız ve hashlenebilirdir. Finansal değerler yalnız
Decimal'dir. Bilinmeyen opsiyonel değer None kalır — asla sıfır,
boş string, sentetik kimlik veya güncel zaman damgası olmaz.

İz (trace) yalnız GERÇEKTEN ulaşılan adımları içerir; mantıksal
sıra dışında zaman kavramı yoktur (duvar saati/UUID/rastgelelik
üretilmez).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, unique
from typing import Optional, Tuple

from execution_broker_models import (
    BrokerOperationResult,
    ExecutionMode,
)
from execution_models import ExecutionRequest
from execution_permission_gate import ExecutionPermission
from execution_risk_models import Instrument, Portfolio, RiskDecision

__all__ = ["ExecutionServiceStatus", "ExecutionTraceStep",
           "ExecutionTrace", "ExecutionServiceRequest",
           "ExecutionServiceResult"]

_ERROR_FIELD = "INVALID_SERVICE_MODEL_FIELD"


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError(_ERROR_FIELD)


def _is_optional_str(value: object) -> bool:
    return value is None or (isinstance(value, str) and bool(value)
                             and not value.isspace())


@unique
class ExecutionServiceStatus(Enum):
    """Kapalı servis sonuç kümesi."""

    SUBMITTED = "SUBMITTED"
    NOT_SUBMITTED = "NOT_SUBMITTED"
    REJECTED_BY_RISK = "REJECTED_BY_RISK"
    BLOCKED_BY_KILL_SWITCH = "BLOCKED_BY_KILL_SWITCH"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    SIZE_REDUCTION_REQUIRED = "SIZE_REDUCTION_REQUIRED"
    BROKER_REJECTED = "BROKER_REJECTED"
    BROKER_TEMPORARY_FAILURE = "BROKER_TEMPORARY_FAILURE"
    BROKER_PERMANENT_FAILURE = "BROKER_PERMANENT_FAILURE"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


@unique
class ExecutionTraceStep(Enum):
    """Kapalı iz adımı kümesi — yalnız ulaşılan adımlar izlenir."""

    INPUT_VALIDATED = "INPUT_VALIDATED"
    BROKER_RESOLVED = "BROKER_RESOLVED"
    RISK_EVALUATED = "RISK_EVALUATED"
    RISK_ALLOWED = "RISK_ALLOWED"
    RISK_DENIED = "RISK_DENIED"
    KILL_SWITCH_CHECKED = "KILL_SWITCH_CHECKED"
    EXECUTION_PERMITTED = "EXECUTION_PERMITTED"
    EXECUTION_DENIED = "EXECUTION_DENIED"
    BROKER_SUBMISSION_STARTED = "BROKER_SUBMISSION_STARTED"
    BROKER_SUBMISSION_COMPLETED = "BROKER_SUBMISSION_COMPLETED"
    BROKER_SUBMISSION_FAILED = "BROKER_SUBMISSION_FAILED"
    RESULT_NORMALIZED = "RESULT_NORMALIZED"


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    """Deterministik, değişmez yürütme izi.

    Yalnız mantıksal adım dizisi taşır: duvar saati, UUID, ham
    payload, secret veya stack trace içermez. Aynı girdiler ve
    aynı bağımlılık yanıtları aynı izi üretir.
    """

    steps: Tuple[ExecutionTraceStep, ...] = ()

    def __post_init__(self) -> None:
        _require(isinstance(self.steps, tuple))
        for step in self.steps:
            _require(isinstance(step, ExecutionTraceStep))


@dataclass(frozen=True, slots=True)
class ExecutionServiceRequest:
    """Kanonik çağıran girdisini KOPYALAMADAN saran servis isteği.

    Servis bu alanların HİÇBİRİNİ üretmez; hepsi çağıran-sahiplidir.
    Bilinmeyen opsiyonel değer None kalır.
    """

    execution_request: ExecutionRequest
    portfolio: Portfolio
    instrument: Instrument
    broker_id: str
    account_id: Optional[str] = None
    portfolio_id: Optional[str] = None
    strategy_id: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    execution_mode: Optional[ExecutionMode] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.execution_request,
                            ExecutionRequest))
        _require(isinstance(self.portfolio, Portfolio))
        _require(isinstance(self.instrument, Instrument))
        _require(isinstance(self.broker_id, str)
                 and bool(self.broker_id)
                 and not self.broker_id.isspace())
        for value in (self.account_id, self.portfolio_id,
                      self.strategy_id, self.request_id,
                      self.correlation_id, self.idempotency_key):
            _require(_is_optional_str(value))
        _require(self.execution_mode is None
                 or isinstance(self.execution_mode, ExecutionMode))
        if self.logical_sequence is not None:
            _require(isinstance(self.logical_sequence, int)
                     and not isinstance(self.logical_sequence, bool)
                     and self.logical_sequence >= 0)


@dataclass(frozen=True, slots=True)
class ExecutionServiceResult:
    """Değişmez kanonik servis sonucu.

    Native broker istisnası, ham JSON, HTTP yanıtı, SDK nesnesi,
    secret veya yetkilendirme bilgisi TAŞIMAZ. Kanonik
    BrokerOperationResult ve RiskDecision kayıpsız korunur.
    """

    status: ExecutionServiceStatus
    trace: ExecutionTrace
    code: Optional[str] = None
    risk_decision: Optional[RiskDecision] = None
    permission: Optional[ExecutionPermission] = None
    broker_result: Optional[BrokerOperationResult] = None
    recommended_quantity: Optional[Decimal] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.status, ExecutionServiceStatus))
        _require(isinstance(self.trace, ExecutionTrace))
        _require(_is_optional_str(self.code))
        _require(self.risk_decision is None
                 or isinstance(self.risk_decision, RiskDecision))
        _require(self.permission is None
                 or isinstance(self.permission,
                               ExecutionPermission))
        _require(self.broker_result is None
                 or isinstance(self.broker_result,
                               BrokerOperationResult))
        _require(self.recommended_quantity is None
                 or (isinstance(self.recommended_quantity, Decimal)
                     and not isinstance(self.recommended_quantity,
                                        bool)))

    @property
    def submitted(self) -> bool:
        return self.status is ExecutionServiceStatus.SUBMITTED
