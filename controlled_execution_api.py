"""Mission 2100 — Agent 08: Birleşik Kontrollü Yürütme API'si.

PAPER / SHADOW / MICRO_LIVE için TEK kamu giriş noktası:

    submit / cancel / status / positions / orders / executions /
    statistics / heartbeat

API'nin kendisi emir YÜRÜTMEZ, borsaya/broker'a BAĞLANMAZ ve Risk
Motoru / Kill Switch / Yetkilendirme / İzin Kapısı zorlamalarını
BAYPAS EDEMEZ: yazan işlemler alt servislerin tam boru hattından
geçer; API yalnız doğrular, yönlendirir ve sonucu değişmez zarfa
sarar. Doğrulama sırası: mod → istek → (alt serviste)
yetkilendirme → risk → kill switch → izin → mantıksal sıra.
Örtük varsayılan YOKTUR: işlem için zorunlu alan eksikse istek
MISSING_API_FIELD ile REDDEDİLİR.

Bilinçli kararlar (raporda gerekçeli):
- MICRO_LIVE submit = yetkilendirme İSTEĞİ (Agent 06 yalnız
  yetkilendirir, yürütme YOK); cancel = REVOKE.
- MICRO_LIVE positions/orders/executions fail-closed reddedilir:
  yetkilendirme katmanında pozisyon/emir/yürütme YOKTUR ve boş
  değer UYDURULMAZ.
- SHADOW positions/orders/executions kağıt defterinden okunur
  (gölge, paper defterini paylaşır).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Optional, Tuple

from controlled_execution_api_errors import (
    ControlledExecutionAPIConfigurationError,
    ControlledExecutionAPIContractError,
    ControlledExecutionAPIModeError)
from controlled_execution_api_models import (
    ControlledExecutionAPIDecision, ControlledExecutionAudit,
    ControlledExecutionOperation, ControlledExecutionRequest,
    ControlledExecutionResponse, ControlledExecutionState,
    ControlledExecutionStatistics, ControlledExecutionStatus)
from controlled_execution_models import ControlledExecutionMode
from controlled_execution_router import ControlledExecutionRouter
from micro_live_models import MicroLiveDecision
from paper_execution_models import PaperExecutionDecision
from runtime_enums import HeartbeatStatus
from shadow_models import ShadowDecision

__all__ = ["ControlledExecutionAPI"]

_MODE = ControlledExecutionMode
_OP = ControlledExecutionOperation
_API = ControlledExecutionAPIDecision

_ERROR_MISSING = "MISSING_API_FIELD"
_ERROR_INVALID = "INVALID_API_FIELD"
_ERROR_MODE = "API_MODE"
_ERROR_CONFIGURATION = "API_CONFIGURATION"

# Kapalı karar eşlemeleri — alt servis kararları API kararına.
_PAPER_DECISION_MAP = MappingProxyType({
    PaperExecutionDecision.EXECUTED: _API.ACCEPTED,
    PaperExecutionDecision.DENIED: _API.DENIED,
    PaperExecutionDecision.RECOMMENDATION_ONLY:
        _API.RECOMMENDATION_ONLY,
})
_SHADOW_DECISION_MAP = MappingProxyType({
    ShadowDecision.SIMULATED: _API.ACCEPTED,
    ShadowDecision.DENIED: _API.DENIED,
    ShadowDecision.RECOMMENDATION_ONLY:
        _API.RECOMMENDATION_ONLY,
})
_MICRO_DECISION_MAP = MappingProxyType({
    MicroLiveDecision.ACCEPTED: _API.ACCEPTED,
    MicroLiveDecision.AUTHORIZED: _API.ACCEPTED,
    MicroLiveDecision.DENIED: _API.DENIED,
    MicroLiveDecision.NOT_AUTHORIZED: _API.DENIED,
})

# Yetkilendirme katmanında pozisyon/emir/yürütme YOKTUR.
_MICRO_UNSUPPORTED = (_OP.POSITIONS, _OP.ORDERS, _OP.EXECUTIONS)


def _missing(field: str) -> None:
    raise ControlledExecutionAPIContractError(
        f"{_ERROR_MISSING}:{field}")


def _require_request(request: object,
                     operation: ControlledExecutionOperation
                     ) -> None:
    if not isinstance(request, ControlledExecutionRequest):
        raise ControlledExecutionAPIContractError(
            f"{_ERROR_INVALID}:request")
    if request.operation is not operation:
        raise ControlledExecutionAPIContractError(
            f"{_ERROR_INVALID}:operation")


def _require_state(state: object) -> None:
    if not isinstance(state, ControlledExecutionState):
        raise ControlledExecutionAPIContractError(
            f"{_ERROR_INVALID}:state")


def _require_present(value: object, field: str) -> None:
    if value is None:
        _missing(field)


class ControlledExecutionAPI:
    """Tek kamu API'si — durumsuz, deterministik, salt yönlendiren."""

    __slots__ = ("_router",)

    def __init__(self,
                 router: ControlledExecutionRouter) -> None:
        if not isinstance(router, ControlledExecutionRouter):
            raise ControlledExecutionAPIConfigurationError(
                f"{_ERROR_CONFIGURATION}:INVALID_ROUTER")
        object.__setattr__(self, "_router", router)

    def __setattr__(self, name: str, value: object) -> None:
        raise ControlledExecutionAPIConfigurationError(
            f"{_ERROR_CONFIGURATION}:API_IMMUTABLE")

    # ------------------------------------------------ yardımcılar

    def _audit(self, request: ControlledExecutionRequest,
               codes: Tuple[str, ...]
               ) -> Tuple[ControlledExecutionAudit, ...]:
        records = []
        for code in codes:
            records.append(ControlledExecutionAudit(
                audit_code=code,
                request_reference=request.request_reference,
                logical_sequence=request.logical_sequence))
        return tuple(records)

    def _write_audit(self, request: ControlledExecutionRequest
                     ) -> Tuple[ControlledExecutionAudit, ...]:
        return self._audit(request, (
            "API_REQUEST_VALIDATED",
            f"API_MODE_ROUTED:{request.mode.value}",
            "API_RESULT_MAPPED"))

    def _read_audit(self, request: ControlledExecutionRequest
                    ) -> Tuple[ControlledExecutionAudit, ...]:
        return self._audit(request, (
            "API_REQUEST_VALIDATED",
            f"API_MODE_ROUTED:{request.mode.value}",
            "API_READ_COMPLETED"))

    def _require_write_fields(
            self, request: ControlledExecutionRequest,
            state: ControlledExecutionState,
            gates_required: bool = True) -> None:
        # MICRO_LIVE revoke fail-safe'tir (Agent 06 sözleşmesi):
        # acil yetki iptali policy/kill switch KAPISINA bağlanamaz.
        if gates_required:
            _require_present(request.policy, "policy")
            _require_present(request.kill_switch, "kill_switch")
        if request.mode is _MODE.MICRO_LIVE:
            _require_present(state.micro_live, "micro_live")
            _require_present(state.micro_live_references,
                             "micro_live_references")
            return
        _require_present(request.order_reference,
                         "order_reference")
        _require_present(state.ledger, "ledger")
        _require_present(state.paper_references,
                         "paper_references")
        if request.mode is _MODE.SHADOW:
            _require_present(state.shadow, "shadow")

    def _reject_micro_read(
            self, request: ControlledExecutionRequest) -> None:
        if request.mode is _MODE.MICRO_LIVE and \
                request.operation in _MICRO_UNSUPPORTED:
            raise ControlledExecutionAPIModeError(
                f"{_ERROR_MODE}:UNSUPPORTED_OPERATION"
                f":MICRO_LIVE:{request.operation.value}")

    def _paper_response(self, request, result
                        ) -> ControlledExecutionResponse:
        return ControlledExecutionResponse(
            mode=request.mode, operation=request.operation,
            decision=_PAPER_DECISION_MAP[result.decision],
            decision_code=result.decision_code.value,
            request_reference=request.request_reference,
            logical_sequence=request.logical_sequence,
            execution_reference=(
                result.execution_result_reference),
            ledger_reference=result.current_ledger_reference,
            audit=self._write_audit(request), payload=result)

    def _shadow_response(self, request, state, result
                         ) -> ControlledExecutionResponse:
        return ControlledExecutionResponse(
            mode=request.mode, operation=request.operation,
            decision=_SHADOW_DECISION_MAP[result.decision],
            decision_code=result.decision_code.value,
            request_reference=request.request_reference,
            logical_sequence=request.logical_sequence,
            execution_reference=result.order_reference,
            ledger_reference=(
                state.paper_references.current_ledger_reference),
            audit=self._write_audit(request), payload=result)

    def _micro_response(self, request, result
                        ) -> ControlledExecutionResponse:
        return ControlledExecutionResponse(
            mode=request.mode, operation=request.operation,
            decision=_MICRO_DECISION_MAP[result.decision],
            decision_code=result.decision_code.value,
            request_reference=request.request_reference,
            logical_sequence=request.logical_sequence,
            execution_reference=result.authorization_reference,
            ledger_reference=None,
            audit=self._write_audit(request), payload=result)

    def _read_response(self, request, payload,
                       decision_code: str,
                       statistics: Optional[
                           ControlledExecutionStatistics] = None,
                       status: Optional[
                           ControlledExecutionStatus] = None
                       ) -> ControlledExecutionResponse:
        return ControlledExecutionResponse(
            mode=request.mode, operation=request.operation,
            decision=_API.REPORTED, decision_code=decision_code,
            request_reference=request.request_reference,
            logical_sequence=request.logical_sequence,
            audit=self._read_audit(request),
            statistics=statistics, status=status,
            payload=payload)

    # ------------------------------------------------- yazanlar

    def submit(self, request: ControlledExecutionRequest,
               state: ControlledExecutionState
               ) -> ControlledExecutionResponse:
        _require_request(request, _OP.SUBMIT)
        _require_state(state)
        self._require_write_fields(request, state)
        service = self._router.resolve(request.mode)
        if request.mode is _MODE.PAPER:
            _require_present(request.execution, "execution")
            result = service.submit_order(
                state.ledger, request.execution,
                request.order_reference, request.policy,
                request.kill_switch, state.paper_references)
            return self._paper_response(request, result)
        if request.mode is _MODE.SHADOW:
            _require_present(request.execution, "execution")
            _require_present(request.observation, "observation")
            result = service.submit_shadow(
                state.ledger, state.shadow, request.execution,
                request.order_reference, request.policy,
                request.kill_switch, request.observation,
                state.paper_references)
            return self._shadow_response(request, state, result)
        _require_present(request.micro_live_request,
                         "micro_live_request")
        _require_present(request.micro_live_limits,
                         "micro_live_limits")
        result = service.request_authorization(
            state.micro_live, request.micro_live_request,
            request.micro_live_limits, request.policy,
            request.kill_switch, state.micro_live_references)
        return self._micro_response(request, result)

    def cancel(self, request: ControlledExecutionRequest,
               state: ControlledExecutionState
               ) -> ControlledExecutionResponse:
        _require_request(request, _OP.CANCEL)
        _require_state(state)
        self._require_write_fields(
            request, state,
            gates_required=request.mode is not _MODE.MICRO_LIVE)
        service = self._router.resolve(request.mode)
        if request.mode is _MODE.PAPER:
            result = service.cancel_order(
                state.ledger, request.order_reference,
                request.policy, request.kill_switch,
                state.paper_references)
            return self._paper_response(request, result)
        if request.mode is _MODE.SHADOW:
            result = service.cancel_shadow(
                state.ledger, state.shadow,
                request.order_reference, request.policy,
                request.kill_switch, state.paper_references)
            return self._shadow_response(request, state, result)
        # MICRO_LIVE iptali = yetkinin REVOKE edilmesi.
        _require_present(request.order_reference,
                         "order_reference")
        result = service.revoke(
            state.micro_live, request.order_reference,
            state.micro_live_references)
        return self._micro_response(request, result)

    # ------------------------------------------------ okuyanlar

    def positions(self, request: ControlledExecutionRequest,
                  state: ControlledExecutionState
                  ) -> ControlledExecutionResponse:
        return self._ledger_read(request, state, _OP.POSITIONS,
                                 "get_positions",
                                 "POSITIONS_REPORTED")

    def orders(self, request: ControlledExecutionRequest,
               state: ControlledExecutionState
               ) -> ControlledExecutionResponse:
        return self._ledger_read(request, state, _OP.ORDERS,
                                 "get_orders",
                                 "ORDERS_REPORTED")

    def executions(self, request: ControlledExecutionRequest,
                   state: ControlledExecutionState
                   ) -> ControlledExecutionResponse:
        return self._ledger_read(request, state, _OP.EXECUTIONS,
                                 "get_executions",
                                 "EXECUTIONS_REPORTED")

    def _ledger_read(self, request, state, operation,
                     accessor: str, decision_code: str
                     ) -> ControlledExecutionResponse:
        _require_request(request, operation)
        _require_state(state)
        self._reject_micro_read(request)
        _require_present(state.ledger, "ledger")
        # Gölge kağıt defterini paylaşır: her iki modda da kayıt
        # kaynağı PAPER servis okuyucularıdır.
        paper = self._router.resolve(_MODE.PAPER)
        self._router.resolve(request.mode)
        payload = getattr(paper, accessor)(state.ledger)
        return self._read_response(request, payload,
                                   decision_code)

    def statistics(self, request: ControlledExecutionRequest,
                   state: ControlledExecutionState
                   ) -> ControlledExecutionResponse:
        _require_request(request, _OP.STATISTICS)
        _require_state(state)
        service = self._router.resolve(request.mode)
        if request.mode is _MODE.PAPER:
            _require_present(state.ledger, "ledger")
            raw = service.get_statistics(state.ledger)
            mapped = ControlledExecutionStatistics(
                mode=request.mode,
                total_orders=raw.orders_submitted,
                total_executions=raw.executions_recorded,
                logical_sequence=request.logical_sequence)
        elif request.mode is _MODE.SHADOW:
            _require_present(state.shadow, "shadow")
            raw = service.statistics(state.shadow)
            mapped = ControlledExecutionStatistics(
                mode=request.mode,
                total_orders=raw.total_orders,
                total_executions=raw.total_executions,
                total_denied=raw.total_denied,
                total_cancels=raw.total_cancels,
                logical_sequence=request.logical_sequence)
        else:
            _require_present(state.micro_live, "micro_live")
            raw = service.statistics(state.micro_live)
            mapped = ControlledExecutionStatistics(
                mode=request.mode,
                total_orders=raw.total_authorizations,
                total_denied=raw.total_denied,
                total_cancels=raw.revoked_count,
                logical_sequence=request.logical_sequence)
        return self._read_response(request, raw,
                                   "STATISTICS_REPORTED",
                                   statistics=mapped)

    def status(self, request: ControlledExecutionRequest,
               state: ControlledExecutionState
               ) -> ControlledExecutionResponse:
        _require_request(request, _OP.STATUS)
        _require_state(state)
        mapped = self._derive_status(request, state)
        return self._read_response(request, mapped,
                                   "STATUS_REPORTED",
                                   status=mapped)

    def heartbeat(self, request: ControlledExecutionRequest,
                  state: ControlledExecutionState
                  ) -> ControlledExecutionResponse:
        _require_request(request, _OP.HEARTBEAT)
        _require_state(state)
        service = self._router.resolve(request.mode)
        if request.mode is _MODE.PAPER:
            _require_present(state.ledger, "ledger")
            payload = service.heartbeat(state.ledger)
        elif request.mode is _MODE.SHADOW:
            _require_present(state.shadow, "shadow")
            payload = service.heartbeat(state.shadow)
        else:
            _require_present(state.micro_live, "micro_live")
            payload = service.heartbeat(state.micro_live)
        return self._read_response(request, payload,
                                   "HEARTBEAT_REPORTED")

    def _derive_status(self, request, state
                       ) -> ControlledExecutionStatus:
        service = self._router.resolve(request.mode)
        if request.mode is _MODE.PAPER:
            _require_present(state.ledger, "ledger")
            beat = service.heartbeat(state.ledger)
            counters = service.get_statistics(state.ledger)
            return ControlledExecutionStatus(
                mode=request.mode,
                alive=beat is HeartbeatStatus.OK,
                order_count=counters.orders_submitted,
                execution_count=counters.executions_recorded,
                logical_sequence=request.logical_sequence)
        if request.mode is _MODE.SHADOW:
            _require_present(state.shadow, "shadow")
            beat = service.heartbeat(state.shadow)
            return ControlledExecutionStatus(
                mode=request.mode, alive=beat.alive,
                order_count=beat.order_count,
                execution_count=beat.execution_count,
                logical_sequence=request.logical_sequence)
        _require_present(state.micro_live, "micro_live")
        beat = service.heartbeat(state.micro_live)
        return ControlledExecutionStatus(
            mode=request.mode, alive=beat.alive,
            authorization_count=beat.authorization_count,
            logical_sequence=request.logical_sequence)
