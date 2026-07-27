"""Mission 2000 — Agent 08: Yürütme API eşleyicisi.

ExecutionApiRequest → ExecutionServiceRequest ve
ExecutionServiceResult → ExecutionApiResponse arasında saf,
durumsuz, deterministik çeviri. İş mantığı YOKTUR: risk hesabı,
yeniden boyutlandırma, kimlik üretimi, broker/Binance bilgisi
yoktur. Alanlar DEĞİŞMEDEN taşınır.
"""

from __future__ import annotations

from execution_api_models import (
    ExecutionApiRequest,
    ExecutionApiResponse,
    ExecutionApiStatus,
)
from execution_service_models import (
    ExecutionServiceRequest,
    ExecutionServiceResult,
    ExecutionServiceStatus,
)

__all__ = ["ExecutionApiMapper"]

_ERROR_INPUT = "INVALID_MAPPER_INPUT"

# Kapalı, kayıpsız durum çevirisi — servis iç ayrımı korunur,
# broker ayrıntısı API katmanında yorumlanmaz
_STATUS_MAP = {
    ExecutionServiceStatus.SUBMITTED:
        ExecutionApiStatus.SUBMITTED,
    ExecutionServiceStatus.NOT_SUBMITTED:
        ExecutionApiStatus.NOT_SUBMITTED,
    ExecutionServiceStatus.REJECTED_BY_RISK:
        ExecutionApiStatus.REJECTED_BY_RISK,
    ExecutionServiceStatus.BLOCKED_BY_KILL_SWITCH:
        ExecutionApiStatus.BLOCKED_BY_KILL_SWITCH,
    ExecutionServiceStatus.REQUIRES_CONFIRMATION:
        ExecutionApiStatus.REQUIRES_CONFIRMATION,
    ExecutionServiceStatus.SIZE_REDUCTION_REQUIRED:
        ExecutionApiStatus.SIZE_REDUCTION_REQUIRED,
    ExecutionServiceStatus.BROKER_REJECTED:
        ExecutionApiStatus.BROKER_FAILURE,
    ExecutionServiceStatus.BROKER_TEMPORARY_FAILURE:
        ExecutionApiStatus.BROKER_FAILURE,
    ExecutionServiceStatus.BROKER_PERMANENT_FAILURE:
        ExecutionApiStatus.BROKER_FAILURE,
    ExecutionServiceStatus.BROKER_UNAVAILABLE:
        ExecutionApiStatus.BROKER_FAILURE,
    ExecutionServiceStatus.INVALID_REQUEST:
        ExecutionApiStatus.VALIDATION_FAILED,
    ExecutionServiceStatus.UNKNOWN_FAILURE:
        ExecutionApiStatus.UNKNOWN_FAILURE,
}


class ExecutionApiMapper:
    """Durumsuz çift yönlü sınır eşleyicisi."""

    __slots__ = ()

    def to_service_request(self, request: ExecutionApiRequest
                           ) -> ExecutionServiceRequest:
        """API isteği → servis isteği (alanlar DEĞİŞMEDEN)."""
        if not isinstance(request, ExecutionApiRequest):
            raise ValueError(_ERROR_INPUT)
        return ExecutionServiceRequest(
            execution_request=request.execution_request,
            portfolio=request.portfolio,
            instrument=request.instrument,
            broker_id=request.broker_id,
            account_id=request.account_id,
            portfolio_id=request.portfolio_id,
            strategy_id=request.strategy_id,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            idempotency_key=request.idempotency_key,
            execution_mode=request.execution_mode,
            logical_sequence=request.logical_sequence)

    def to_api_response(self, result: ExecutionServiceResult
                        ) -> ExecutionApiResponse:
        """Servis sonucu → API yanıtı (kanonik sonuç kayıpsız)."""
        if not isinstance(result, ExecutionServiceResult):
            raise ValueError(_ERROR_INPUT)
        return ExecutionApiResponse(
            status=_STATUS_MAP[result.status],
            code=result.code,
            service_result=result,
            recommended_quantity=result.recommended_quantity)
