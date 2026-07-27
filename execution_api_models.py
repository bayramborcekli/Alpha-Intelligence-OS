"""Mission 2000 — Agent 08: Yürütme API sınır modelleri.

Yürütme alt sistemine giren TEK kamu yazma sözleşmesinin
değişmez modelleri. HTTP/REST/WebSocket YOKTUR — yalnız kanonik
yürütme sözleşmesi.

Kanonik kavramlar (ExecutionRequest, Portfolio, Instrument,
ExecutionMode, ExecutionServiceResult) sahibi modüllerden import
edilir — KOPYA yoktur. Tüm modeller frozen+slots, açık tipli,
hashlenebilir ve mutable varsayılansızdır. Bilinmeyen opsiyonel
değer None kalır; API hiçbir kimlik üretmez.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, unique
from typing import Optional

from execution_broker_models import ExecutionMode
from execution_models import ExecutionRequest
from execution_risk_models import Instrument, Portfolio
from execution_service_models import ExecutionServiceResult

__all__ = ["ExecutionApiStatus", "ExecutionApiRequest",
           "ExecutionApiResponse"]

_ERROR_FIELD = "INVALID_API_MODEL_FIELD"


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError(_ERROR_FIELD)


def _is_optional_str(value: object) -> bool:
    return value is None or (isinstance(value, str) and bool(value)
                             and not value.isspace())


@unique
class ExecutionApiStatus(Enum):
    """Kapalı API sonuç kümesi — servis iç durumlarını sızdırmadan
    çağırana anlamlı sınıflandırma verir."""

    SUBMITTED = "SUBMITTED"
    NOT_SUBMITTED = "NOT_SUBMITTED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    REJECTED_BY_RISK = "REJECTED_BY_RISK"
    BLOCKED_BY_KILL_SWITCH = "BLOCKED_BY_KILL_SWITCH"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    SIZE_REDUCTION_REQUIRED = "SIZE_REDUCTION_REQUIRED"
    BROKER_FAILURE = "BROKER_FAILURE"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


@dataclass(frozen=True, slots=True)
class ExecutionApiRequest:
    """Değişmez API isteği — kanonik girdiyi KOPYALAMADAN sarar.

    Tüm kimlikler çağıran-sahiplidir; API asla üretmez. Gönderim
    için idempotency_key zorunludur (yürütme anında doğrulanır ve
    eksikse Execution Service ÇAĞRILMADAN reddedilir).
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
class ExecutionApiResponse:
    """Değişmez API yanıtı.

    Kanonik ExecutionServiceResult kayıpsız taşınır; ham broker
    payload'ı, HTTP nesnesi, SDK nesnesi, native istisna, stack
    trace veya secret ASLA taşınmaz. Kodlar sterildir.
    """

    status: ExecutionApiStatus
    code: Optional[str] = None
    service_result: Optional[ExecutionServiceResult] = None
    recommended_quantity: Optional[Decimal] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.status, ExecutionApiStatus))
        _require(_is_optional_str(self.code))
        _require(self.service_result is None
                 or isinstance(self.service_result,
                               ExecutionServiceResult))
        _require(self.recommended_quantity is None
                 or (isinstance(self.recommended_quantity, Decimal)
                     and not isinstance(self.recommended_quantity,
                                        bool)))

    @property
    def submitted(self) -> bool:
        return self.status is ExecutionApiStatus.SUBMITTED
