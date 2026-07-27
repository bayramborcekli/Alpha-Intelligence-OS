"""Mission 2000 — Agent 08 Yürütme API testleri.

Kamu API dondurması, tek execute(), eşleme, doğrulama, kanonik
model sahipliği, servis tam-bir-kez çağrısı, doğrulama
başarısızlığında sıfır servis çağrısı, değişmez modeller, kapalı
enum'lar, HTTP/ağ/retry/UUID/datetime/rastgelelik yasakları,
broker/kill-switch/risk mantığı yokluğu, hata normalizasyonu ve
mimari sertifikasyon doğrulanır.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import os
import sys
from decimal import Decimal

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import asyncio
from enum import Enum

import execution_api
import execution_api_mapper
import execution_api_models
import execution_service
from execution_api import (
    ExecutionApi, ExecutionApiConfigurationError,
    ExecutionApiContractError, ExecutionApiError)
from execution_api_mapper import ExecutionApiMapper
from execution_api_models import (
    ExecutionApiRequest, ExecutionApiResponse, ExecutionApiStatus)
from execution_broker_models import ExecutionMode
from execution_enums import OrderSide, OrderType, TimeInForce
from execution_kill_switch import KillSwitch
from execution_models import ExecutionRequest
from execution_risk_engine import RiskEngine
from execution_risk_models import (
    AssetType, CapitalState, Instrument, Portfolio, RiskDecision,
    RiskDecisionType, RiskLimits)
from execution_service import (
    BrokerAdapterResolver, ExecutionService,
    ExecutionServiceContractError, ExecutionServiceError)
from execution_service_models import (
    ExecutionServiceRequest, ExecutionServiceResult,
    ExecutionServiceStatus, ExecutionTrace, ExecutionTraceStep)

D = Decimal
_STATUS = ExecutionApiStatus
_SSTATUS = ExecutionServiceStatus

API_MODULES = (execution_api, execution_api_models,
               execution_api_mapper)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _exec_request(**overrides):
    base = dict(symbol="BTCUSDT", side=OrderSide.BUY,
                order_type=OrderType.LIMIT, quantity=D("0.5"),
                time_in_force=TimeInForce.GTC, price=D("100"))
    base.update(overrides)
    return ExecutionRequest(**base)


def _portfolio():
    return Portfolio(capital=CapitalState(
        total_capital=D("10000"), available_capital=D("10000")))


def _instrument():
    return Instrument(symbol="BTCUSDT",
                      asset_type=AssetType.CRYPTO,
                      currency="BTC", quote_currency="USDT")


def _api_request(**overrides):
    base = dict(execution_request=_exec_request(),
                portfolio=_portfolio(), instrument=_instrument(),
                broker_id="paper-1", account_id="acc-1",
                portfolio_id="pf-1", strategy_id="st-1",
                request_id="req-1", correlation_id="corr-1",
                idempotency_key="idem-1",
                execution_mode=ExecutionMode.PAPER,
                logical_sequence=7)
    base.update(overrides)
    return ExecutionApiRequest(**base)


def _service_result(status=_SSTATUS.SUBMITTED, code=None,
                    recommended=None):
    return ExecutionServiceResult(
        status=status,
        trace=ExecutionTrace(
            steps=(ExecutionTraceStep.INPUT_VALIDATED,)),
        code=code, recommended_quantity=recommended)


class StubService(ExecutionService):
    """Zorlanmış servis sonucu döndüren deterministik casus."""

    class _Resolver(BrokerAdapterResolver):
        __slots__ = ()

        def resolve(self, broker_id):
            raise KeyError(broker_id)

        def profile(self, broker_id):
            raise KeyError(broker_id)

    def __init__(self, result=None, raise_error=None):
        switch = KillSwitch()
        switch.enable()
        super().__init__(RiskEngine(RiskLimits()), switch,
                         self._Resolver())
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "forced_result", result)
        object.__setattr__(self, "raise_error", raise_error)

    async def execute(self, request):
        self.calls.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        if self.forced_result is not None:
            return self.forced_result
        return _service_result()


# StubService alt sınıfına dinamik alan gerekli — slots'u genişlet
StubService.__slots__ = ()


def _api(result=None, raise_error=None):
    service = StubService(result=result, raise_error=raise_error)
    return ExecutionApi(service), service


# ── Yapı: tek kamu yazma metodu ──────────────────────────────────────

class TestApiStructure:
    def test_single_public_write_method(self):
        public = {n for n in dir(ExecutionApi)
                  if not n.startswith("_")}
        assert public == {"execute"}

    def test_execute_is_async(self):
        assert inspect.iscoroutinefunction(ExecutionApi.execute)

    def test_slots_only_service_and_mapper(self):
        assert ExecutionApi.__slots__ == ("_service", "_mapper")

    @pytest.mark.parametrize("bad", [None, object(), "service",
                                     ExecutionApiMapper()])
    def test_dependency_injection_validated(self, bad):
        with pytest.raises(ExecutionApiConfigurationError):
            ExecutionApi(bad)

    def test_contract_error_on_wrong_request_type(self):
        api, _ = _api()
        with pytest.raises(ExecutionApiContractError):
            _run(api.execute({"symbol": "BTCUSDT"}))

    def test_exception_hierarchy_closed(self):
        assert issubclass(ExecutionApiContractError,
                          ExecutionApiError)
        assert issubclass(ExecutionApiConfigurationError,
                          ExecutionApiError)
        defined = {node.name for node in ast.walk(ast.parse(
            inspect.getsource(execution_api)))
            if isinstance(node, ast.ClassDef)
            and node.name.endswith("Error")}
        assert defined == {"ExecutionApiError",
                           "ExecutionApiContractError",
                           "ExecutionApiConfigurationError"}

    def test_api_knows_only_execution_service(self):
        # API katmanı Risk/KillSwitch/Broker/Binance bilmez
        # (docstring'ler taranmaz — yalnız kod)
        source = _code_source(execution_api)
        for token in ("RiskEngine", "KillSwitch", "BrokerAdapter",
                      "binance", "Binance", "RiskDecision",
                      "PermissionGate"):
            assert token not in source

    def test_no_global_singleton(self):
        source = inspect.getsource(execution_api)
        for token in ("_INSTANCE", "get_instance", "singleton"):
            assert token not in source


# ── Servis tam-bir-kez çağrısı ───────────────────────────────────────

class TestServiceInvocation:
    def test_service_called_exactly_once(self):
        api, service = _api()
        _run(api.execute(_api_request()))
        assert len(service.calls) == 1

    def test_service_receives_service_request(self):
        api, service = _api()
        _run(api.execute(_api_request()))
        assert isinstance(service.calls[0],
                          ExecutionServiceRequest)

    def test_no_second_call_on_failure_result(self):
        api, service = _api(result=_service_result(
            _SSTATUS.BROKER_TEMPORARY_FAILURE, code="TIMEOUT"))
        _run(api.execute(_api_request()))
        assert len(service.calls) == 1  # retry YOK

    def test_no_second_call_on_service_exception(self):
        api, service = _api(
            raise_error=ExecutionServiceError("X"))
        _run(api.execute(_api_request()))
        assert len(service.calls) == 1

    def test_no_call_after_validation_failure(self):
        api, service = _api()
        result = _run(api.execute(
            _api_request(idempotency_key=None)))
        assert service.calls == []
        assert result.status is _STATUS.VALIDATION_FAILED

    def test_no_loop_or_finally_in_api(self):
        tree = ast.parse(inspect.getsource(execution_api))
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.For, ast.While,
                                         ast.AsyncFor))
            if isinstance(node, ast.Try):
                assert node.finalbody == []


# ── Doğrulama ────────────────────────────────────────────────────────

class TestValidation:
    def test_missing_idempotency_key_rejected_before_service(self):
        api, service = _api()
        result = _run(api.execute(
            _api_request(idempotency_key=None)))
        assert result.status is _STATUS.VALIDATION_FAILED
        assert result.code == "MISSING_IDEMPOTENCY_KEY"
        assert result.service_result is None
        assert service.calls == []

    @pytest.mark.parametrize("key", ["", "   "])
    def test_blank_key_rejected_at_model_boundary(self, key):
        with pytest.raises(ValueError,
                           match="INVALID_API_MODEL_FIELD"):
            _api_request(idempotency_key=key)

    @pytest.mark.parametrize("mutation", [
        dict(execution_request=None),
        dict(execution_request="order"),
        dict(portfolio=None), dict(portfolio={"cap": 1}),
        dict(instrument=None), dict(instrument="BTCUSDT"),
        dict(broker_id=""), dict(broker_id="   "),
        dict(broker_id=None), dict(broker_id=7),
        dict(account_id=""), dict(portfolio_id=""),
        dict(strategy_id=""), dict(request_id=""),
        dict(correlation_id=""), dict(idempotency_key=1),
        dict(execution_mode="PAPER"), dict(execution_mode=1),
        dict(logical_sequence=-1), dict(logical_sequence=True),
        dict(logical_sequence="7"),
        dict(logical_sequence=D("7"))])
    def test_request_sterile_validation(self, mutation):
        with pytest.raises(ValueError,
                           match="INVALID_API_MODEL_FIELD"):
            _api_request(**mutation)

    def test_valid_execution_modes_accepted(self):
        for mode in ExecutionMode:
            assert _api_request(
                execution_mode=mode).execution_mode is mode

    def test_logical_sequence_zero_valid(self):
        assert _api_request(
            logical_sequence=0).logical_sequence == 0

    def test_unknown_optionals_stay_none(self):
        request = ExecutionApiRequest(
            execution_request=_exec_request(),
            portfolio=_portfolio(), instrument=_instrument(),
            broker_id="b1")
        for field in ("account_id", "portfolio_id", "strategy_id",
                      "request_id", "correlation_id",
                      "idempotency_key", "execution_mode",
                      "logical_sequence"):
            assert getattr(request, field) is None


# ── Eşleme: istek ────────────────────────────────────────────────────

class TestRequestMapping:
    def test_identifiers_mapped_unchanged(self):
        mapper = ExecutionApiMapper()
        api_request = _api_request()
        service_request = mapper.to_service_request(api_request)
        for field in ("broker_id", "account_id", "portfolio_id",
                      "strategy_id", "request_id",
                      "correlation_id", "idempotency_key",
                      "execution_mode", "logical_sequence"):
            assert getattr(service_request, field) == \
                getattr(api_request, field)

    def test_canonical_objects_passed_by_reference(self):
        mapper = ExecutionApiMapper()
        api_request = _api_request()
        service_request = mapper.to_service_request(api_request)
        assert service_request.execution_request is \
            api_request.execution_request
        assert service_request.portfolio is api_request.portfolio
        assert service_request.instrument is \
            api_request.instrument

    def test_none_identifiers_stay_none(self):
        mapper = ExecutionApiMapper()
        service_request = mapper.to_service_request(
            _api_request(account_id=None, portfolio_id=None,
                         strategy_id=None, request_id=None,
                         correlation_id=None, execution_mode=None,
                         logical_sequence=None))
        for field in ("account_id", "portfolio_id", "strategy_id",
                      "request_id", "correlation_id",
                      "execution_mode", "logical_sequence"):
            assert getattr(service_request, field) is None

    @pytest.mark.parametrize("bad", [None, "request", object(),
                                     _exec_request()])
    def test_mapper_request_input_validated(self, bad):
        with pytest.raises(ValueError,
                           match="INVALID_MAPPER_INPUT"):
            ExecutionApiMapper().to_service_request(bad)

    def test_mapper_stateless(self):
        assert ExecutionApiMapper.__slots__ == ()

    def test_mapper_public_surface(self):
        public = {n for n in dir(ExecutionApiMapper)
                  if not n.startswith("_")}
        assert public == {"to_service_request", "to_api_response"}


# ── Eşleme: yanıt ────────────────────────────────────────────────────

class TestResponseMapping:
    @pytest.mark.parametrize("service_status,api_status", [
        (_SSTATUS.SUBMITTED, _STATUS.SUBMITTED),
        (_SSTATUS.NOT_SUBMITTED, _STATUS.NOT_SUBMITTED),
        (_SSTATUS.REJECTED_BY_RISK, _STATUS.REJECTED_BY_RISK),
        (_SSTATUS.BLOCKED_BY_KILL_SWITCH,
         _STATUS.BLOCKED_BY_KILL_SWITCH),
        (_SSTATUS.REQUIRES_CONFIRMATION,
         _STATUS.REQUIRES_CONFIRMATION),
        (_SSTATUS.SIZE_REDUCTION_REQUIRED,
         _STATUS.SIZE_REDUCTION_REQUIRED),
        (_SSTATUS.BROKER_REJECTED, _STATUS.BROKER_FAILURE),
        (_SSTATUS.BROKER_TEMPORARY_FAILURE,
         _STATUS.BROKER_FAILURE),
        (_SSTATUS.BROKER_PERMANENT_FAILURE,
         _STATUS.BROKER_FAILURE),
        (_SSTATUS.BROKER_UNAVAILABLE, _STATUS.BROKER_FAILURE),
        (_SSTATUS.INVALID_REQUEST, _STATUS.VALIDATION_FAILED),
        (_SSTATUS.UNKNOWN_FAILURE, _STATUS.UNKNOWN_FAILURE)])
    def test_every_service_status_mapped(self, service_status,
                                         api_status):
        response = ExecutionApiMapper().to_api_response(
            _service_result(service_status, code="C1"))
        assert response.status is api_status

    def test_mapping_total_over_service_enum(self):
        # Servis enum'una eklenen her durum eşlenmek ZORUNDA
        mapper = ExecutionApiMapper()
        for status in ExecutionServiceStatus:
            response = mapper.to_api_response(
                _service_result(status))
            assert isinstance(response.status, ExecutionApiStatus)

    def test_service_result_carried_lossless(self):
        result = _service_result(_SSTATUS.SUBMITTED)
        response = ExecutionApiMapper().to_api_response(result)
        assert response.service_result is result

    def test_code_carried_unchanged(self):
        response = ExecutionApiMapper().to_api_response(
            _service_result(_SSTATUS.REJECTED_BY_RISK,
                            code="CAPITAL_EXCEEDED"))
        assert response.code == "CAPITAL_EXCEEDED"

    def test_recommended_quantity_carried(self):
        response = ExecutionApiMapper().to_api_response(
            _service_result(_SSTATUS.SIZE_REDUCTION_REQUIRED,
                            code="EXPOSURE_EXCEEDED",
                            recommended=D("0.25")))
        assert response.recommended_quantity == D("0.25")

    @pytest.mark.parametrize("bad", [None, "result", object(),
                                     {"status": "SUBMITTED"}])
    def test_mapper_response_input_validated(self, bad):
        with pytest.raises(ValueError,
                           match="INVALID_MAPPER_INPUT"):
            ExecutionApiMapper().to_api_response(bad)

    def test_deterministic_mapping(self):
        mapper = ExecutionApiMapper()
        result = _service_result(_SSTATUS.SUBMITTED)
        assert mapper.to_api_response(result) == \
            mapper.to_api_response(result)


# ── Uçtan uca API davranışı ──────────────────────────────────────────

class TestEndToEnd:
    def test_submitted_flow(self):
        api, _ = _api()
        response = _run(api.execute(_api_request()))
        assert isinstance(response, ExecutionApiResponse)
        assert response.status is _STATUS.SUBMITTED
        assert response.submitted is True

    @pytest.mark.parametrize("service_status,api_status", [
        (_SSTATUS.REJECTED_BY_RISK, _STATUS.REJECTED_BY_RISK),
        (_SSTATUS.BLOCKED_BY_KILL_SWITCH,
         _STATUS.BLOCKED_BY_KILL_SWITCH),
        (_SSTATUS.NOT_SUBMITTED, _STATUS.NOT_SUBMITTED),
        (_SSTATUS.BROKER_REJECTED, _STATUS.BROKER_FAILURE),
        (_SSTATUS.UNKNOWN_FAILURE, _STATUS.UNKNOWN_FAILURE)])
    def test_denial_flows(self, service_status, api_status):
        api, _ = _api(result=_service_result(service_status,
                                             code="C"))
        response = _run(api.execute(_api_request()))
        assert response.status is api_status
        assert response.submitted is False

    def test_size_reduction_flow(self):
        api, _ = _api(result=_service_result(
            _SSTATUS.SIZE_REDUCTION_REQUIRED,
            code="EXPOSURE_EXCEEDED", recommended=D("0.25")))
        response = _run(api.execute(_api_request()))
        assert response.status is _STATUS.SIZE_REDUCTION_REQUIRED
        assert response.recommended_quantity == D("0.25")

    def test_same_request_same_response(self):
        first_api, _ = _api()
        second_api, _ = _api()
        assert _run(first_api.execute(_api_request())) == \
            _run(second_api.execute(_api_request()))

    def test_real_service_integration_unknown_broker(self):
        # Gerçek servis + boş resolver: deterministik NOT_SUBMITTED
        switch = KillSwitch()
        switch.enable()

        class EmptyResolver(BrokerAdapterResolver):
            __slots__ = ()

            def resolve(self, broker_id):
                raise KeyError(broker_id)

            def profile(self, broker_id):
                raise KeyError(broker_id)

        api = ExecutionApi(ExecutionService(
            RiskEngine(RiskLimits()), switch, EmptyResolver()))
        response = _run(api.execute(_api_request()))
        assert response.status is _STATUS.NOT_SUBMITTED
        assert response.code == "UNKNOWN_BROKER"
        assert response.service_result is not None


# ── Hata sınırı ──────────────────────────────────────────────────────

class TestErrorBoundary:
    @pytest.mark.parametrize("exc", [
        ExecutionServiceError("iç ayrıntı"),
        ExecutionServiceContractError("iç ayrıntı"),
        RuntimeError("native detail"), ValueError("v"),
        KeyError("k"), TypeError("t")])
    def test_service_exceptions_normalized(self, exc):
        api, _ = _api(raise_error=exc)
        response = _run(api.execute(_api_request()))
        assert response.status is _STATUS.UNKNOWN_FAILURE
        assert response.code == "SERVICE_FAILURE"
        assert response.service_result is None

    def test_no_native_details_in_response(self):
        api, _ = _api(raise_error=RuntimeError(
            "SECRET-BEARING-DETAIL"))
        response = _run(api.execute(_api_request()))
        assert "SECRET" not in str(response.code)
        assert "SECRET" not in response.status.value

    def test_non_result_service_return_normalized(self):
        class WeirdService(StubService):
            async def execute(self, request):
                self.calls.append(request)
                return {"raw": "json"}
        api = ExecutionApi(WeirdService())
        response = _run(api.execute(_api_request()))
        assert response.status is _STATUS.UNKNOWN_FAILURE

    def test_operational_outcomes_never_raise(self):
        for status in ExecutionServiceStatus:
            api, _ = _api(result=_service_result(status))
            response = _run(api.execute(_api_request()))
            assert isinstance(response, ExecutionApiResponse)

    def test_no_traceback_helpers_in_api(self):
        for module in API_MODULES:
            source = inspect.getsource(module)
            for token in ("traceback", "format_exc", "exc_info",
                          "__traceback__"):
                assert token not in source


# ── Değişmez API modelleri ───────────────────────────────────────────

class TestApiModels:
    def test_request_frozen_slots_hashable(self):
        request = _api_request()
        with pytest.raises(dataclasses.FrozenInstanceError):
            request.broker_id = "x"
        assert not hasattr(request, "__dict__")
        assert isinstance(hash(request), int)

    def test_response_frozen_slots_hashable(self):
        response = ExecutionApiResponse(
            status=_STATUS.VALIDATION_FAILED, code="C")
        with pytest.raises(dataclasses.FrozenInstanceError):
            response.status = _STATUS.SUBMITTED
        assert not hasattr(response, "__dict__")
        assert isinstance(hash(response), int)

    def test_request_value_equality(self):
        assert _api_request() == _api_request()

    @pytest.mark.parametrize("model", [ExecutionApiRequest,
                                       ExecutionApiResponse])
    def test_no_mutable_defaults(self, model):
        for field in dataclasses.fields(model):
            if field.default is not dataclasses.MISSING:
                assert not isinstance(field.default,
                                      (list, dict, set))
            assert field.default_factory is dataclasses.MISSING

    @pytest.mark.parametrize("model", [ExecutionApiRequest,
                                       ExecutionApiResponse])
    def test_explicitly_typed(self, model):
        for field in dataclasses.fields(model):
            assert field.type

    @pytest.mark.parametrize("bad", [
        dict(status="SUBMITTED"), dict(status=1),
        dict(status=_SSTATUS.SUBMITTED),
        dict(status=_STATUS.SUBMITTED, code=""),
        dict(status=_STATUS.SUBMITTED, code=7),
        dict(status=_STATUS.SUBMITTED,
             service_result={"raw": 1}),
        dict(status=_STATUS.SUBMITTED,
             recommended_quantity=0.5),
        dict(status=_STATUS.SUBMITTED,
             recommended_quantity="0.5")])
    def test_response_sterile_validation(self, bad):
        with pytest.raises(ValueError,
                           match="INVALID_API_MODEL_FIELD"):
            ExecutionApiResponse(**bad)

    def test_no_raw_payload_fields(self):
        for model in (ExecutionApiRequest, ExecutionApiResponse):
            names = {f.name for f in dataclasses.fields(model)}
            for forbidden in ("raw_response", "native_payload",
                              "http_response", "sdk_object",
                              "exchange_json", "timestamp",
                              "created_at", "stack_trace",
                              "headers", "api_key", "secret",
                              "token"):
                assert forbidden not in names

    def test_decimal_only_money(self):
        response = ExecutionApiResponse(
            status=_STATUS.SIZE_REDUCTION_REQUIRED,
            recommended_quantity=D("0.25"))
        assert isinstance(response.recommended_quantity, Decimal)


# ── Kapalı enum ──────────────────────────────────────────────────────

class TestClosedEnums:
    def test_api_status_closed(self):
        assert tuple(s.name for s in ExecutionApiStatus) == (
            "SUBMITTED", "NOT_SUBMITTED", "VALIDATION_FAILED",
            "REJECTED_BY_RISK", "BLOCKED_BY_KILL_SWITCH",
            "REQUIRES_CONFIRMATION", "SIZE_REDUCTION_REQUIRED",
            "BROKER_FAILURE", "UNKNOWN_FAILURE")

    def test_values_equal_names(self):
        for member in ExecutionApiStatus:
            assert member.value == member.name

    @pytest.mark.parametrize("member", list(ExecutionApiStatus))
    def test_members_hashable(self, member):
        assert isinstance(hash(member), int)


# ── Kanonik sahiplik ─────────────────────────────────────────────────

def _module_imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


def _code_source(module) -> str:
    """Docstring'lerden arındırılmış kaynak (yalnız kod)."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


class TestCanonicalOwnership:
    def test_no_duplicate_canonical_models(self):
        forbidden = {"ExecutionRequest", "ExecutionResult",
                     "Order", "Position", "Fill", "Instrument",
                     "Portfolio", "BrokerProfile", "RiskDecision",
                     "BrokerOperationResult",
                     "BrokerRequestContext", "ExecutionMode",
                     "ExecutionServiceRequest",
                     "ExecutionServiceResult", "ExecutionTrace",
                     "KillSwitch"}
        for module in API_MODULES:
            defined = {node.name for node in ast.walk(
                ast.parse(inspect.getsource(module)))
                if isinstance(node, ast.ClassDef)}
            assert not defined & forbidden

    def test_canonical_imports_used(self):
        source = inspect.getsource(execution_api_models)
        assert "from execution_models import" in source
        assert "from execution_service_models import" in source

    def test_api_layer_imports_only_service_layer(self):
        allowed = {"__future__", "enum", "dataclasses", "decimal",
                   "typing",
                   "execution_models", "execution_risk_models",
                   "execution_broker_models",
                   "execution_service", "execution_service_models",
                   "execution_api_models", "execution_api_mapper"}
        for module in API_MODULES:
            assert _module_imports(module) <= allowed

    def test_api_does_not_import_lower_layers(self):
        forbidden = {"execution_risk_engine",
                     "execution_kill_switch",
                     "execution_permission_gate",
                     "execution_broker_adapter",
                     "binance_spot_adapter", "binance_normalizer",
                     "binance_capabilities", "app",
                     "monitoring_service", "strategy_service"}
        for module in API_MODULES:
            assert not _module_imports(module) & forbidden


# ── İş mantığı yokluğu ───────────────────────────────────────────────

class TestNoBusinessLogic:
    def test_no_risk_logic(self):
        for module in API_MODULES:
            source = _code_source(module)
            for token in ("notional", "exposure", "max_position",
                          "daily_loss", "validate_execution",
                          "calculate_", "approved_quantity *",
                          "quantity *"):
                assert token not in source

    def test_no_kill_switch_logic(self):
        for module in API_MODULES:
            source = _code_source(module)
            for token in ("kill_switch", "KillSwitch",
                          "is_execution_allowed", ".enable(",
                          ".disable(", ".lock("):
                assert token not in source

    def test_no_broker_logic(self):
        for module in API_MODULES:
            source = _code_source(module)
            for token in ("submit_order", "cancel_order",
                          "newClientOrderId", "-2010", "binance",
                          "Transport", "adapter"):
                assert token not in source

    def test_no_resizing_no_id_generation(self):
        for module in API_MODULES:
            source = _code_source(module)
            for token in ("uuid", "uuid4", "token_hex", "random",
                          "monotonic", "urandom", "Decimal(0",
                          "quantize"):
                assert token not in source

    def test_no_arithmetic_on_quantities(self):
        # Eşleyici/API alanları yalnız TAŞIR — aritmetik yok
        for module in API_MODULES:
            for node in ast.walk(ast.parse(
                    inspect.getsource(module))):
                assert not isinstance(node, ast.BinOp)


# ── Güvenlik yasakları ───────────────────────────────────────────────

class TestSecurity:
    @pytest.mark.parametrize("module", API_MODULES)
    def test_no_forbidden_imports(self, module):
        roots = {m.split(".")[0] for m in _module_imports(module)}
        forbidden = {"os", "sys", "io", "pathlib", "socket", "ssl",
                     "http", "flask", "fastapi", "django",
                     "aiohttp", "requests", "httpx", "urllib",
                     "websockets", "websocket", "uuid", "datetime",
                     "time", "random", "secrets", "hashlib",
                     "hmac", "threading", "multiprocessing",
                     "subprocess", "asyncio", "json", "pickle",
                     "sqlite3", "queue", "functools"}
        assert not roots & forbidden

    @pytest.mark.parametrize("token", [
        "sleep", "retry", "backoff", "Thread", "Process(",
        "create_task", "ensure_future", "run_until_complete",
        "asyncio.run", "getenv", "environ", "open(", "Path(",
        "datetime.now", "uuid4", "random.", "urandom", "http://",
        "https://", "wss://", "ws://", "websocket", "@app.route",
        "Flask", "FastAPI", "Blueprint", "APIRouter", "session",
        "cookie", "jwt", "api_key", "API_KEY", "while True"])
    def test_no_forbidden_tokens(self, token):
        for module in API_MODULES:
            source = _code_source(module).replace("retryable", "")
            assert token not in source

    @pytest.mark.parametrize("module", API_MODULES)
    def test_no_dangerous_calls(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "open", "__import__",
                    "compile", "print", "input")

    @pytest.mark.parametrize("module", API_MODULES)
    def test_no_float_literals(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    def test_no_persistence_or_cache(self):
        for module in API_MODULES:
            source = _code_source(module)
            for token in ("cache", "lru_cache", "database",
                          "sqlite", "pickle", "shelve", "save(",
                          "load(", "write(", "read("):
                assert token not in source

    def test_stateless_no_mutable_class_state(self):
        source = _code_source(execution_api)
        for token in ("self._requests", "self._responses",
                      "Lock(", "Queue(", "deque(", "defaultdict",
                      "append("):
            assert token not in source


# ── Kamu API dondurması ──────────────────────────────────────────────

class TestPublicApiFreeze:
    def test_api_module_surface(self):
        assert execution_api.__all__ == [
            "ExecutionApi", "ExecutionApiError",
            "ExecutionApiContractError",
            "ExecutionApiConfigurationError"]

    def test_models_module_surface(self):
        assert execution_api_models.__all__ == [
            "ExecutionApiStatus", "ExecutionApiRequest",
            "ExecutionApiResponse"]

    def test_mapper_module_surface(self):
        assert execution_api_mapper.__all__ == [
            "ExecutionApiMapper"]

    def test_combined_surface_matches_spec(self):
        combined = set(execution_api.__all__) | \
            set(execution_api_models.__all__) | \
            set(execution_api_mapper.__all__)
        assert combined == {
            "ExecutionApi", "ExecutionApiRequest",
            "ExecutionApiResponse", "ExecutionApiStatus",
            "ExecutionApiMapper", "ExecutionApiError",
            "ExecutionApiContractError",
            "ExecutionApiConfigurationError"}

    @pytest.mark.parametrize("module", API_MODULES)
    def test_no_additional_public_names(self, module):
        public = {name for name, value in vars(module).items()
                  if not name.startswith("_")
                  and (inspect.isfunction(value)
                       or inspect.isclass(value))
                  and getattr(value, "__module__", None)
                  == module.__name__}
        assert public <= set(module.__all__)


# ── Mimari sertifikasyon ─────────────────────────────────────────────

class TestArchitectureCertification:
    def test_delivery_marked(self):
        path = os.path.join(_ROOT, "tests",
                            "test_mission_2000_architecture.py")
        with open(path, encoding="utf-8") as handle:
            assert "execution_api.py" in handle.read()

    def test_monitoring_independent_of_api(self):
        import monitoring_service
        roots = {m.split(".")[0]
                 for m in _module_imports(monitoring_service)}
        assert "execution_api" not in roots

    def test_service_layer_does_not_import_api(self):
        import execution_service as service_module
        import execution_service_models as models_module
        for module in (service_module, models_module):
            roots = {m.split(".")[0]
                     for m in _module_imports(module)}
            assert "execution_api" not in roots
            assert "execution_api_models" not in roots

    def test_mode_carried_not_branched(self):
        source = _code_source(execution_api) + _code_source(
            execution_api_mapper)
        for token in ("MICRO_LIVE", "SHADOW", "LIVE", "PAPER"):
            assert token not in source

    @pytest.mark.parametrize("mode", list(ExecutionMode))
    def test_every_mode_passes_through(self, mode):
        api, service = _api()
        _run(api.execute(_api_request(execution_mode=mode)))
        assert service.calls[0].execution_mode is mode

    def test_identifiers_propagated_end_to_end(self):
        api, service = _api()
        _run(api.execute(_api_request(
            idempotency_key="caller-key-9",
            request_id="rq-9", correlation_id="co-9")))
        passed = service.calls[0]
        assert passed.idempotency_key == "caller-key-9"
        assert passed.request_id == "rq-9"
        assert passed.correlation_id == "co-9"


# ── Ek sertifikasyon: sınır bütünlüğü ────────────────────────────────

class TestBoundaryIntegrityExtra:
    @pytest.mark.parametrize("status", list(ExecutionApiStatus))
    def test_every_api_status_constructible(self, status):
        response = ExecutionApiResponse(status=status)
        assert response.status is status
        assert response.submitted is (
            status is _STATUS.SUBMITTED)

    @pytest.mark.parametrize("service_status", [
        _SSTATUS.REJECTED_BY_RISK,
        _SSTATUS.BLOCKED_BY_KILL_SWITCH,
        _SSTATUS.REQUIRES_CONFIRMATION,
        _SSTATUS.SIZE_REDUCTION_REQUIRED,
        _SSTATUS.BROKER_REJECTED,
        _SSTATUS.BROKER_TEMPORARY_FAILURE,
        _SSTATUS.BROKER_PERMANENT_FAILURE,
        _SSTATUS.BROKER_UNAVAILABLE,
        _SSTATUS.INVALID_REQUEST,
        _SSTATUS.UNKNOWN_FAILURE,
        _SSTATUS.NOT_SUBMITTED])
    def test_non_submitted_statuses_not_submitted(
            self, service_status):
        api, _ = _api(result=_service_result(service_status))
        response = _run(api.execute(_api_request()))
        assert response.submitted is False

    def test_service_trace_reachable_via_service_result(self):
        api, _ = _api()
        response = _run(api.execute(_api_request()))
        assert response.service_result.trace.steps == (
            ExecutionTraceStep.INPUT_VALIDATED,)

    def test_response_hash_equality_consistent(self):
        first = ExecutionApiResponse(
            status=_STATUS.NOT_SUBMITTED, code="UNKNOWN_BROKER")
        second = ExecutionApiResponse(
            status=_STATUS.NOT_SUBMITTED, code="UNKNOWN_BROKER")
        assert first == second and hash(first) == hash(second)

    def test_validation_failure_is_deterministic(self):
        api, _ = _api()
        first = _run(api.execute(
            _api_request(idempotency_key=None)))
        second = _run(api.execute(
            _api_request(idempotency_key=None)))
        assert first == second

    def test_request_not_mutated_by_execute(self):
        api, _ = _api()
        request = _api_request()
        _run(api.execute(request))
        assert request == _api_request()
        assert request.execution_request.quantity == D("0.5")

    @pytest.mark.parametrize("field", [
        "account_id", "portfolio_id", "strategy_id",
        "request_id", "correlation_id"])
    def test_each_optional_identifier_propagates(self, field):
        api, service = _api()
        _run(api.execute(_api_request(**{field: "val-x"})))
        assert getattr(service.calls[0], field) == "val-x"

    @pytest.mark.parametrize("sequence", [0, 1, 999999])
    def test_logical_sequence_propagates(self, sequence):
        api, service = _api()
        _run(api.execute(
            _api_request(logical_sequence=sequence)))
        assert service.calls[0].logical_sequence == sequence

    def test_no_datetime_import_anywhere(self):
        for module in API_MODULES:
            assert "datetime" not in inspect.getsource(module)

    def test_api_module_docstring_mentions_no_http(self):
        assert "HTTP" in execution_api.__doc__

    @pytest.mark.parametrize("module", API_MODULES)
    def test_all_classes_use_slots(self, module):
        for name, value in vars(module).items():
            if inspect.isclass(value) and \
                    value.__module__ == module.__name__ and \
                    not issubclass(value, Exception) and \
                    not issubclass(value, Enum):
                assert "__slots__" in value.__dict__

    def test_status_map_keys_cover_service_enum_exactly(self):
        from execution_api_mapper import _STATUS_MAP
        assert set(_STATUS_MAP.keys()) == \
            set(ExecutionServiceStatus)

    @pytest.mark.parametrize("service_status", [
        _SSTATUS.SUBMITTED, _SSTATUS.REJECTED_BY_RISK,
        _SSTATUS.BROKER_REJECTED, _SSTATUS.UNKNOWN_FAILURE])
    def test_service_result_identity_preserved_end_to_end(
            self, service_status):
        result = _service_result(service_status)
        api, _ = _api(result=result)
        response = _run(api.execute(_api_request()))
        assert response.service_result is result
