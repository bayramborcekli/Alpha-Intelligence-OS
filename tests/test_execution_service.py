"""Mission 2000 — Agent 07 Yürütme Servisi testleri.

Tam boru hattı sırası, girdi doğrulama, değişmez servis modelleri,
kapalı durum kümesi, kanonik model sahipliği, deterministik broker
çözümleme, risk/kill-switch zorunluluğu, reddedilen HER yolda broker
çağrı sayısı = 0, onaylı yolda tam BİR broker çağrısı, idempotency
yayılımı, örtük yeniden boyutlandırma yasağı, sonuç eşlemesi,
bağımlılık istisna sınırı, deterministik iz, güvenlik yasakları ve
kamu API dondurması doğrulanır.
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

import execution_permission_gate
import execution_service
import execution_service_models
from execution_broker_adapter import BrokerAdapter
from execution_broker_errors import (
    BrokerContractError, BrokerErrorCode, BrokerErrorDetail)
from execution_broker_models import (
    BrokerHealth, BrokerHealthState, BrokerOperationResult,
    BrokerOperationStatus, BrokerRequestContext, ExecutionMode)
from execution_enums import (
    OrderSide, OrderState, OrderType, TimeInForce)
from execution_kill_switch import KillSwitch
from execution_models import ExecutionRequest, Order
from execution_permission_gate import (
    ExecutionPermission, ExecutionPermissionGate)
from execution_risk_engine import RiskEngine
from execution_risk_models import (
    AssetType, BrokerProfile, CapitalState, Instrument, Portfolio,
    RiskDecision, RiskDecisionType, RiskLimits)
from execution_service import (
    BrokerAdapterResolver, ExecutionService,
    ExecutionServiceConfigurationError,
    ExecutionServiceContractError, ExecutionServiceError)
from execution_service_models import (
    ExecutionServiceRequest, ExecutionServiceResult,
    ExecutionServiceStatus, ExecutionTrace, ExecutionTraceStep)

D = Decimal
_STEP = ExecutionTraceStep
_STATUS = ExecutionServiceStatus

SERVICE_MODULES = (execution_service, execution_service_models,
                   execution_permission_gate)


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


def _service_request(**overrides):
    base = dict(execution_request=_exec_request(),
                portfolio=_portfolio(), instrument=_instrument(),
                broker_id="paper-1", request_id="req-1",
                correlation_id="corr-1", account_id="acc-1",
                portfolio_id="pf-1", strategy_id="st-1",
                idempotency_key="idem-1",
                execution_mode=ExecutionMode.PAPER,
                logical_sequence=7)
    base.update(overrides)
    return ExecutionServiceRequest(**base)


class SpyAdapter(BrokerAdapter):
    """Deterministik casus adaptör — çağrı sayısı sertifikasyonu."""

    __slots__ = ("submit_calls", "contexts", "result",
                 "submit_error")

    def __init__(self, result=None, submit_error=None):
        object.__setattr__(self, "submit_calls", [])
        object.__setattr__(self, "contexts", [])
        object.__setattr__(self, "result", result)
        object.__setattr__(self, "submit_error", submit_error)

    async def _do_profile(self):
        raise AssertionError("cagrilmamali")

    async def _do_health_check(self):
        raise AssertionError("cagrilmamali")

    async def _do_get_order(self, query, context):
        raise AssertionError("cagrilmamali")

    async def _do_list_open_orders(self, query, context):
        raise AssertionError("cagrilmamali")

    async def _do_get_positions(self, query, context):
        raise AssertionError("cagrilmamali")

    async def _do_get_balances(self, query, context):
        raise AssertionError("cagrilmamali")

    async def _do_submit_order(self, request, context):
        self.submit_calls.append(request)
        self.contexts.append(context)
        if self.submit_error is not None:
            raise self.submit_error
        if self.result is not None:
            return self.result
        return BrokerOperationResult(
            status=BrokerOperationStatus.SUCCESS,
            order=Order(symbol=request.symbol, side=request.side,
                        order_type=request.order_type,
                        quantity=request.quantity,
                        time_in_force=request.time_in_force,
                        state=OrderState.SUBMITTED))

    async def _do_cancel_order(self, request, context):
        raise AssertionError("cagrilmamali")


class MapResolver(BrokerAdapterResolver):
    """Deterministik enjekte çözümleyici."""

    __slots__ = ("_adapters", "resolve_calls")

    def __init__(self, adapters):
        self._adapters = dict(adapters)
        self.resolve_calls = []

    def resolve(self, broker_id):
        self.resolve_calls.append(broker_id)
        return self._adapters[broker_id]

    def profile(self, broker_id):
        if broker_id not in self._adapters:
            raise KeyError(broker_id)
        return BrokerProfile(supports_market_orders=True,
                             supports_fractional=True,
                             supports_cancel=True)


class StubRiskEngine(RiskEngine):
    """Zorlanmış risk kararı döndüren deterministik motor."""

    __slots__ = ("decision", "validate_calls", "raise_error")

    def __init__(self, decision=None, raise_error=None):
        super().__init__(RiskLimits())
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "validate_calls", [])
        object.__setattr__(self, "raise_error", raise_error)

    def validate(self, request, portfolio, instrument,
                 broker_profile):
        self.validate_calls.append(
            (request, portfolio, instrument, broker_profile))
        if self.raise_error is not None:
            raise self.raise_error
        if self.decision is not None:
            return self.decision
        return RiskDecision(decision=RiskDecisionType.ALLOW,
                            approved_quantity=request.quantity)


def _allow():
    return RiskDecision(decision=RiskDecisionType.ALLOW,
                        approved_quantity=D("0.5"))


def _enabled_kill_switch():
    switch = KillSwitch()
    switch.enable()
    return switch


def _service(adapter=None, decision=None, kill_switch=None,
             raise_risk=None, broker_id="paper-1"):
    adapter = adapter if adapter is not None else SpyAdapter()
    engine = StubRiskEngine(decision=decision,
                            raise_error=raise_risk)
    switch = kill_switch if kill_switch is not None else \
        _enabled_kill_switch()
    resolver = MapResolver({broker_id: adapter})
    return ExecutionService(engine, switch, resolver), adapter, \
        engine, switch, resolver


# ── Yapı: tek giriş noktası, asenkron sözleşme ───────────────────────

class TestServiceStructure:
    def test_single_public_entry_point(self):
        public = {n for n in dir(ExecutionService)
                  if not n.startswith("_")}
        assert public == {"execute"}

    def test_execute_is_async(self):
        assert inspect.iscoroutinefunction(
            ExecutionService.execute)

    def test_stateless_slots_only_dependencies(self):
        assert ExecutionService.__slots__ == (
            "_risk_engine", "_kill_switch", "_resolver", "_gate")

    @pytest.mark.parametrize("index,bad", [
        (0, object()), (1, object()), (2, object()),
        (0, None), (1, None), (2, None)])
    def test_dependency_injection_validated(self, index, bad):
        args = [StubRiskEngine(), _enabled_kill_switch(),
                MapResolver({})]
        args[index] = bad
        with pytest.raises(ExecutionServiceConfigurationError):
            ExecutionService(*args)

    def test_contract_error_on_wrong_request_type(self):
        service, *_ = _service()
        with pytest.raises(ExecutionServiceContractError):
            _run(service.execute({"symbol": "BTCUSDT"}))

    def test_exception_hierarchy(self):
        assert issubclass(ExecutionServiceContractError,
                          ExecutionServiceError)
        assert issubclass(ExecutionServiceConfigurationError,
                          ExecutionServiceError)
        defined = {node.name for node in ast.walk(ast.parse(
            inspect.getsource(execution_service)))
            if isinstance(node, ast.ClassDef)
            and node.name.endswith("Error")}
        assert defined == {"ExecutionServiceError",
                           "ExecutionServiceContractError",
                           "ExecutionServiceConfigurationError"}

    def test_resolver_abstract_injected(self):
        assert inspect.isabstract(BrokerAdapterResolver)
        with pytest.raises(TypeError):
            BrokerAdapterResolver()
        assert BrokerAdapterResolver.__slots__ == ()
        assert BrokerAdapterResolver.__abstractmethods__ == \
            frozenset({"resolve", "profile"})

    def test_no_global_singleton_or_default_broker(self):
        source = inspect.getsource(execution_service)
        for token in ("_INSTANCE", "get_instance",
                      "default_broker", "DEFAULT_BROKER",
                      "singleton"):
            assert token not in source


# ── Onaylı yol: tam boru hattı ───────────────────────────────────────

class TestApprovedPath:
    def test_submitted_result(self):
        service, adapter, engine, _, resolver = _service()
        result = _run(service.execute(_service_request()))
        assert isinstance(result, ExecutionServiceResult)
        assert result.status is _STATUS.SUBMITTED
        assert result.submitted is True
        assert isinstance(result.broker_result,
                          BrokerOperationResult)

    def test_exact_frozen_trace_order(self):
        service, *_ = _service()
        result = _run(service.execute(_service_request()))
        assert result.trace.steps == (
            _STEP.INPUT_VALIDATED, _STEP.BROKER_RESOLVED,
            _STEP.RISK_EVALUATED, _STEP.RISK_ALLOWED,
            _STEP.KILL_SWITCH_CHECKED, _STEP.EXECUTION_PERMITTED,
            _STEP.BROKER_SUBMISSION_STARTED,
            _STEP.BROKER_SUBMISSION_COMPLETED,
            _STEP.RESULT_NORMALIZED)

    def test_broker_called_exactly_once(self):
        service, adapter, *_ = _service()
        _run(service.execute(_service_request()))
        assert len(adapter.submit_calls) == 1

    def test_risk_engine_called_exactly_once(self):
        service, _, engine, *_ = _service()
        _run(service.execute(_service_request()))
        assert len(engine.validate_calls) == 1

    def test_risk_receives_canonical_inputs_unchanged(self):
        service, _, engine, *_ = _service()
        request = _service_request()
        _run(service.execute(request))
        passed = engine.validate_calls[0]
        assert passed[0] is request.execution_request
        assert passed[1] is request.portfolio
        assert passed[2] is request.instrument
        assert isinstance(passed[3], BrokerProfile)

    def test_execution_request_not_mutated(self):
        service, adapter, *_ = _service()
        request = _service_request()
        _run(service.execute(request))
        assert adapter.submit_calls[0] is request.execution_request
        assert adapter.submit_calls[0].quantity == D("0.5")

    def test_permission_included(self):
        service, *_ = _service()
        result = _run(service.execute(_service_request()))
        assert isinstance(result.permission, ExecutionPermission)
        assert result.permission.permitted is True

    def test_risk_decision_preserved(self):
        service, *_ = _service()
        result = _run(service.execute(_service_request()))
        assert result.risk_decision.decision is \
            RiskDecisionType.ALLOW

    def test_deterministic_same_inputs_same_result(self):
        first_service, *_ = _service()
        second_service, *_ = _service()
        first = _run(first_service.execute(_service_request()))
        second = _run(second_service.execute(_service_request()))
        assert first == second
        assert first.trace == second.trace


# ── Idempotency yayılımı ─────────────────────────────────────────────

class TestIdempotencyPropagation:
    @pytest.mark.parametrize("key", ["", "   "])
    def test_blank_key_rejected_at_model_boundary(self, key):
        with pytest.raises(ValueError,
                           match="INVALID_SERVICE_MODEL_FIELD"):
            _service_request(idempotency_key=key)

    def test_missing_key_invalid_request_no_calls(self):
        service, adapter, engine, _, resolver = _service()
        result = _run(service.execute(
            _service_request(idempotency_key=None)))
        assert result.status is _STATUS.INVALID_REQUEST
        assert result.code == "MISSING_IDEMPOTENCY_KEY"
        assert adapter.submit_calls == []
        assert engine.validate_calls == []
        assert resolver.resolve_calls == []
        assert result.trace.steps == ()

    def test_key_propagated_unchanged(self):
        service, adapter, *_ = _service()
        _run(service.execute(
            _service_request(idempotency_key="caller-key-42")))
        context = adapter.contexts[0]
        assert isinstance(context, BrokerRequestContext)
        assert context.idempotency_key == "caller-key-42"

    def test_all_caller_identifiers_propagated_unchanged(self):
        service, adapter, *_ = _service()
        _run(service.execute(_service_request()))
        context = adapter.contexts[0]
        assert context.request_id == "req-1"
        assert context.correlation_id == "corr-1"
        assert context.account_id == "acc-1"
        assert context.portfolio_id == "pf-1"
        assert context.strategy_id == "st-1"
        assert context.execution_mode is ExecutionMode.PAPER
        assert context.logical_sequence == 7

    def test_unknown_identifiers_stay_none(self):
        service, adapter, *_ = _service()
        _run(service.execute(_service_request(
            request_id=None, correlation_id=None, account_id=None,
            portfolio_id=None, strategy_id=None,
            execution_mode=None, logical_sequence=None)))
        context = adapter.contexts[0]
        for field in ("request_id", "correlation_id", "account_id",
                      "portfolio_id", "strategy_id",
                      "execution_mode", "logical_sequence"):
            assert getattr(context, field) is None

    def test_service_never_generates_identifiers(self):
        for module in SERVICE_MODULES:
            source = inspect.getsource(module)
            for token in ("uuid", "token_hex", "randbytes",
                          "monotonic", "perf_counter", "urandom",
                          "uuid4"):
                assert token not in source


# ── Kısa devre: risk reddi ───────────────────────────────────────────

class TestRiskDenialShortCircuit:
    def test_reject_no_broker_call(self):
        decision = RiskDecision(decision=RiskDecisionType.REJECT,
                                code="CAPITAL_EXCEEDED")
        service, adapter, *_ = _service(decision=decision)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.REJECTED_BY_RISK
        assert result.code == "CAPITAL_EXCEEDED"
        assert adapter.submit_calls == []

    def test_reject_trace_ends_at_denial(self):
        decision = RiskDecision(decision=RiskDecisionType.REJECT,
                                code="CAPITAL_EXCEEDED")
        service, *_ = _service(decision=decision)
        result = _run(service.execute(_service_request()))
        assert result.trace.steps == (
            _STEP.INPUT_VALIDATED, _STEP.BROKER_RESOLVED,
            _STEP.RISK_EVALUATED, _STEP.RISK_DENIED,
            _STEP.EXECUTION_DENIED)

    def test_require_confirmation_no_broker_call(self):
        decision = RiskDecision(
            decision=RiskDecisionType.REQUIRE_CONFIRMATION,
            code="UNKNOWN_NOTIONAL")
        service, adapter, *_ = _service(decision=decision)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.REQUIRES_CONFIRMATION
        assert adapter.submit_calls == []

    def test_kill_switch_not_read_after_risk_denial(self):
        decision = RiskDecision(decision=RiskDecisionType.REJECT,
                                code="X")

        class RecordingSwitch(KillSwitch):
            reads = []

            def is_execution_allowed(self):
                RecordingSwitch.reads.append(True)
                return super().is_execution_allowed()

        switch = RecordingSwitch()
        switch.enable()
        RecordingSwitch.reads.clear()
        service, adapter, *_ = _service(decision=decision,
                                        kill_switch=switch)
        result = _run(service.execute(_service_request()))
        assert RecordingSwitch.reads == []
        assert _STEP.KILL_SWITCH_CHECKED not in \
            result.trace.steps

    def test_risk_decision_preserved_on_denial(self):
        decision = RiskDecision(decision=RiskDecisionType.REJECT,
                                code="X")
        service, *_ = _service(decision=decision)
        result = _run(service.execute(_service_request()))
        assert result.risk_decision is decision


# ── Örtük yeniden boyutlandırma yasağı ───────────────────────────────

class TestNoImplicitResizing:
    def test_reduce_size_not_submitted(self):
        decision = RiskDecision(
            decision=RiskDecisionType.REDUCE_SIZE,
            code="EXPOSURE_EXCEEDED",
            approved_quantity=D("0.25"))
        service, adapter, *_ = _service(decision=decision)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.SIZE_REDUCTION_REQUIRED
        assert adapter.submit_calls == []

    def test_recommended_quantity_preserved(self):
        decision = RiskDecision(
            decision=RiskDecisionType.REDUCE_SIZE,
            code="EXPOSURE_EXCEEDED",
            approved_quantity=D("0.25"))
        service, *_ = _service(decision=decision)
        result = _run(service.execute(_service_request()))
        assert result.recommended_quantity == D("0.25")
        assert isinstance(result.recommended_quantity, Decimal)

    def test_recommended_quantity_unknown_stays_none(self):
        decision = RiskDecision(
            decision=RiskDecisionType.REDUCE_SIZE, code="X")
        service, *_ = _service(decision=decision)
        result = _run(service.execute(_service_request()))
        assert result.recommended_quantity is None

    def test_original_quantity_never_silently_changed(self):
        decision = RiskDecision(
            decision=RiskDecisionType.REDUCE_SIZE,
            code="X", approved_quantity=D("0.25"))
        service, adapter, *_ = _service(decision=decision)
        request = _service_request()
        _run(service.execute(request))
        assert request.execution_request.quantity == D("0.5")
        assert adapter.submit_calls == []

    def test_explicit_new_request_can_proceed(self):
        # Çağıran açıkça YENİ istek + YENİ anahtar oluşturur
        service, adapter, *_ = _service()
        revised = _service_request(
            execution_request=_exec_request(quantity=D("0.25")),
            idempotency_key="idem-2-revised")
        result = _run(service.execute(revised))
        assert result.status is _STATUS.SUBMITTED
        assert adapter.submit_calls[0].quantity == D("0.25")
        assert adapter.contexts[0].idempotency_key == \
            "idem-2-revised"


# ── Kill Switch zorunluluğu ──────────────────────────────────────────

class TestKillSwitchEnforcement:
    def test_disabled_blocks_broker(self):
        service, adapter, *_ = _service(kill_switch=KillSwitch())
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.BLOCKED_BY_KILL_SWITCH
        assert result.code == "KILL_SWITCH_DENIED"
        assert adapter.submit_calls == []

    def test_locked_blocks_broker(self):
        switch = KillSwitch()
        switch.lock()
        service, adapter, *_ = _service(kill_switch=switch)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.BLOCKED_BY_KILL_SWITCH
        assert adapter.submit_calls == []

    def test_maintenance_blocks_broker(self):
        switch = KillSwitch()
        switch.enable()
        switch.maintenance()
        service, adapter, *_ = _service(kill_switch=switch)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.BLOCKED_BY_KILL_SWITCH
        assert adapter.submit_calls == []

    def test_denied_trace_order(self):
        service, *_ = _service(kill_switch=KillSwitch())
        result = _run(service.execute(_service_request()))
        assert result.trace.steps == (
            _STEP.INPUT_VALIDATED, _STEP.BROKER_RESOLVED,
            _STEP.RISK_EVALUATED, _STEP.RISK_ALLOWED,
            _STEP.KILL_SWITCH_CHECKED, _STEP.EXECUTION_DENIED)

    def test_fresh_permission_each_attempt_no_cache(self):
        switch = _enabled_kill_switch()
        service, adapter, *_ = _service(kill_switch=switch)
        first = _run(service.execute(_service_request()))
        assert first.status is _STATUS.SUBMITTED
        switch.disable()
        second = _run(service.execute(
            _service_request(idempotency_key="idem-2")))
        assert second.status is _STATUS.BLOCKED_BY_KILL_SWITCH
        assert len(adapter.submit_calls) == 1  # ikinci çağrı yok

    def test_reenabled_allows_again(self):
        switch = KillSwitch()
        service, adapter, *_ = _service(kill_switch=switch)
        blocked = _run(service.execute(_service_request()))
        assert blocked.status is _STATUS.BLOCKED_BY_KILL_SWITCH
        switch.enable()
        allowed = _run(service.execute(
            _service_request(idempotency_key="idem-2")))
        assert allowed.status is _STATUS.SUBMITTED
        assert len(adapter.submit_calls) == 1

    def test_service_never_mutates_kill_switch(self):
        source = inspect.getsource(execution_service)
        for token in (".enable(", ".disable(", ".lock(",
                      ".maintenance("):
            assert token not in source

    def test_gate_owns_kill_switch_read(self):
        # Servis is_execution_allowed'u doğrudan çağırmaz; tek
        # otorite kapıdır
        source = inspect.getsource(execution_service)
        assert "is_execution_allowed" not in source


# ── İzin kapısı ──────────────────────────────────────────────────────

class TestPermissionGate:
    def _gate(self):
        return ExecutionPermissionGate()

    def test_allow_enabled_permit(self):
        permission = self._gate().evaluate(
            _allow(), _enabled_kill_switch())
        assert permission.permitted is True
        assert permission.kill_switch_allowed is True
        assert permission.code is None

    @pytest.mark.parametrize("prepare", [
        lambda s: None,                              # DISABLED
        lambda s: s.lock(),                          # LOCKED
        lambda s: (s.enable(), s.maintenance())])    # MAINTENANCE
    def test_allow_with_non_enabled_denied(self, prepare):
        switch = KillSwitch()
        prepare(switch)
        permission = self._gate().evaluate(_allow(), switch)
        assert permission.permitted is False
        assert permission.code == "KILL_SWITCH_DENIED"

    @pytest.mark.parametrize("decision_type,code", [
        (RiskDecisionType.REJECT, "RISK_REJECTED"),
        (RiskDecisionType.REQUIRE_CONFIRMATION,
         "RISK_REQUIRES_CONFIRMATION"),
        (RiskDecisionType.REDUCE_SIZE,
         "RISK_SIZE_REDUCTION_REQUIRED")])
    def test_non_allow_denied_even_when_enabled(
            self, decision_type, code):
        decision = RiskDecision(decision=decision_type, code="X")
        permission = self._gate().evaluate(
            decision, _enabled_kill_switch())
        assert permission.permitted is False
        assert permission.code == code
        assert permission.kill_switch_allowed is None

    def test_gate_stateless(self):
        assert ExecutionPermissionGate.__slots__ == ()

    def test_gate_single_public_operation(self):
        public = {n for n in dir(ExecutionPermissionGate)
                  if not n.startswith("_")}
        assert public == {"evaluate"}

    @pytest.mark.parametrize("bad_decision,bad_switch", [
        ("ALLOW", None), (None, None), (1, None)])
    def test_gate_sterile_validation(self, bad_decision,
                                     bad_switch):
        switch = bad_switch or _enabled_kill_switch()
        with pytest.raises(ValueError,
                           match="INVALID_PERMISSION_INPUT"):
            self._gate().evaluate(bad_decision, switch)

    def test_gate_rejects_non_kill_switch(self):
        with pytest.raises(ValueError,
                           match="INVALID_PERMISSION_INPUT"):
            self._gate().evaluate(_allow(), object())

    def test_gate_never_mutates_state(self):
        switch = _enabled_kill_switch()
        before = switch.current_state()
        decision = _allow()
        self._gate().evaluate(decision, switch)
        assert switch.current_state() is before
        assert decision.approved_quantity == D("0.5")

    def test_gate_never_calls_broker(self):
        source = _code_source(execution_permission_gate)
        for token in ("submit", "BrokerAdapter", "await"):
            assert token not in source

    def test_permission_model_immutable(self):
        permission = ExecutionPermission(
            permitted=True, risk_decision=_allow(),
            kill_switch_allowed=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            permission.permitted = False
        assert not hasattr(permission, "__dict__")
        assert isinstance(hash(permission), int)

    @pytest.mark.parametrize("bad", [
        dict(permitted="yes", risk_decision=_allow()),
        dict(permitted=True, risk_decision="ALLOW"),
        dict(permitted=True, risk_decision=_allow(),
             kill_switch_allowed="yes"),
        dict(permitted=True, risk_decision=_allow(), code="")])
    def test_permission_sterile_validation(self, bad):
        with pytest.raises(ValueError,
                           match="INVALID_PERMISSION_INPUT"):
            ExecutionPermission(**bad)


# ── Broker çözümleme ─────────────────────────────────────────────────

class TestBrokerRouting:
    def test_unknown_broker_deterministic_not_submitted(self):
        service, adapter, engine, *_ = _service()
        result = _run(service.execute(
            _service_request(broker_id="bilinmeyen")))
        assert result.status is _STATUS.NOT_SUBMITTED
        assert result.code == "UNKNOWN_BROKER"
        assert adapter.submit_calls == []
        assert engine.validate_calls == []  # risk de çağrılmaz
        assert result.trace.steps == (_STEP.INPUT_VALIDATED,)

    def test_no_fallback_broker(self):
        # Bilinmeyen broker asla başka adaptöre yönlenmez
        primary, secondary = SpyAdapter(), SpyAdapter()
        resolver = MapResolver({"a": primary, "b": secondary})
        service = ExecutionService(StubRiskEngine(),
                                   _enabled_kill_switch(),
                                   resolver)
        _run(service.execute(_service_request(broker_id="yok")))
        assert primary.submit_calls == []
        assert secondary.submit_calls == []

    def test_resolver_returning_non_adapter_rejected(self):
        class BadResolver(BrokerAdapterResolver):
            __slots__ = ()

            def resolve(self, broker_id):
                return object()

            def profile(self, broker_id):
                return BrokerProfile()
        service = ExecutionService(StubRiskEngine(),
                                   _enabled_kill_switch(),
                                   BadResolver())
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.NOT_SUBMITTED
        assert result.code == "UNKNOWN_BROKER"

    def test_resolver_exception_normalized(self):
        class ExplodingResolver(BrokerAdapterResolver):
            __slots__ = ()

            def resolve(self, broker_id):
                raise RuntimeError("vendor internals")

            def profile(self, broker_id):
                return BrokerProfile()
        service = ExecutionService(StubRiskEngine(),
                                   _enabled_kill_switch(),
                                   ExplodingResolver())
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.NOT_SUBMITTED

    def test_resolution_deterministic(self):
        service, _, _, _, resolver = _service()
        _run(service.execute(_service_request()))
        assert resolver.resolve_calls == ["paper-1"]

    def test_no_broker_name_branches_in_service(self):
        for module in SERVICE_MODULES:
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare):
                    for comp in ast.walk(node):
                        if isinstance(comp, ast.Constant) and \
                                isinstance(comp.value, str):
                            assert comp.value.lower() not in (
                                "binance", "ibkr", "midas",
                                "bybit", "okx", "kraken")

    def test_no_dynamic_import_or_reflection(self):
        for module in SERVICE_MODULES:
            source = inspect.getsource(module)
            for token in ("importlib", "__import__", "globals()",
                          "getattr(sys", "entry_points",
                          "pkgutil"):
                assert token not in source


# ── Sonuç eşlemesi ───────────────────────────────────────────────────

def _broker_result(status, code=None):
    error = None
    if status is not BrokerOperationStatus.SUCCESS:
        error = BrokerErrorDetail(
            code=code or BrokerErrorCode.UNKNOWN_BROKER_FAILURE)
    return BrokerOperationResult(status=status, error=error)


class TestResultTranslation:
    @pytest.mark.parametrize("broker_status,service_status", [
        (BrokerOperationStatus.SUCCESS, _STATUS.SUBMITTED),
        (BrokerOperationStatus.REJECTED, _STATUS.BROKER_REJECTED),
        (BrokerOperationStatus.TEMPORARY_FAILURE,
         _STATUS.BROKER_TEMPORARY_FAILURE),
        (BrokerOperationStatus.PERMANENT_FAILURE,
         _STATUS.BROKER_PERMANENT_FAILURE),
        (BrokerOperationStatus.UNSUPPORTED,
         _STATUS.BROKER_PERMANENT_FAILURE),
        (BrokerOperationStatus.UNKNOWN, _STATUS.UNKNOWN_FAILURE),
        (BrokerOperationStatus.NOT_FOUND,
         _STATUS.UNKNOWN_FAILURE)])
    def test_status_map(self, broker_status, service_status):
        adapter = SpyAdapter(result=_broker_result(broker_status))
        service, *_ = _service(adapter=adapter)
        result = _run(service.execute(_service_request()))
        assert result.status is service_status

    def test_broker_unavailable_distinguished(self):
        adapter = SpyAdapter(result=_broker_result(
            BrokerOperationStatus.TEMPORARY_FAILURE,
            BrokerErrorCode.BROKER_UNAVAILABLE))
        service, *_ = _service(adapter=adapter)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.BROKER_UNAVAILABLE

    def test_canonical_broker_result_never_lost(self):
        broker_result = _broker_result(
            BrokerOperationStatus.REJECTED,
            BrokerErrorCode.ORDER_REJECTED)
        adapter = SpyAdapter(result=broker_result)
        service, *_ = _service(adapter=adapter)
        result = _run(service.execute(_service_request()))
        assert result.broker_result is broker_result
        assert result.code == "ORDER_REJECTED"

    def test_broker_failure_trace(self):
        adapter = SpyAdapter(result=_broker_result(
            BrokerOperationStatus.REJECTED,
            BrokerErrorCode.ORDER_REJECTED))
        service, *_ = _service(adapter=adapter)
        result = _run(service.execute(_service_request()))
        assert result.trace.steps[-3:] == (
            _STEP.BROKER_SUBMISSION_STARTED,
            _STEP.BROKER_SUBMISSION_COMPLETED,
            _STEP.RESULT_NORMALIZED)

    def test_no_binance_code_inspection_in_service(self):
        source = inspect.getsource(execution_service)
        for token in ("-2010", "-1013", "429", "binance"):
            assert token not in source


# ── Bağımlılık istisna sınırı ────────────────────────────────────────

class TestDependencyExceptionBoundary:
    def test_risk_engine_exception_normalized(self):
        service, adapter, *_ = _service(
            raise_risk=RuntimeError("internal policy detail"))
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.UNKNOWN_FAILURE
        assert result.code == "RISK_ENGINE_FAILURE"
        assert adapter.submit_calls == []

    def test_broker_exception_normalized(self):
        adapter = SpyAdapter(
            submit_error=RuntimeError("vendor sdk detail"))
        service, *_ = _service(adapter=adapter)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.UNKNOWN_FAILURE
        assert result.code == "BROKER_FAILURE"
        assert result.trace.steps[-1] is \
            _STEP.BROKER_SUBMISSION_FAILED

    def test_broker_contract_error_normalized(self):
        adapter = SpyAdapter(submit_error=BrokerContractError(
            "INVALID_REQUEST"))
        service, *_ = _service(adapter=adapter)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.UNKNOWN_FAILURE

    def test_profile_exception_normalized(self):
        class BrokenProfileResolver(MapResolver):
            def profile(self, broker_id):
                raise ValueError("native detail")
        adapter = SpyAdapter()
        service = ExecutionService(
            StubRiskEngine(), _enabled_kill_switch(),
            BrokenProfileResolver({"paper-1": adapter}))
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.UNKNOWN_FAILURE
        assert result.code == "BROKER_PROFILE_FAILURE"
        assert adapter.submit_calls == []

    def test_non_profile_return_normalized(self):
        class WrongProfileResolver(MapResolver):
            def profile(self, broker_id):
                return {"supports": True}
        adapter = SpyAdapter()
        service = ExecutionService(
            StubRiskEngine(), _enabled_kill_switch(),
            WrongProfileResolver({"paper-1": adapter}))
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.UNKNOWN_FAILURE
        assert result.code == "BROKER_PROFILE_FAILURE"
        assert adapter.submit_calls == []

    def test_gate_dependency_fault_normalized(self):
        class ExplodingSwitch(KillSwitch):
            def is_execution_allowed(self):
                raise RuntimeError("switch backend fault")
        switch = ExplodingSwitch()
        switch.enable()
        service, adapter, *_ = _service(kill_switch=switch)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.UNKNOWN_FAILURE
        assert result.code == "PERMISSION_GATE_FAILURE"
        assert adapter.submit_calls == []

    def test_non_result_broker_return_normalized(self):
        class WeirdAdapter(SpyAdapter):
            __slots__ = ()

            async def _do_submit_order(self, request, context):
                self.submit_calls.append(request)
                return {"raw": "json"}
        service, adapter, *_ = _service(adapter=WeirdAdapter())
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.UNKNOWN_FAILURE
        assert len(adapter.submit_calls) == 1  # retry yok

    def test_no_native_details_in_result(self):
        adapter = SpyAdapter(
            submit_error=RuntimeError("SECRET-BEARING-DETAIL"))
        service, *_ = _service(adapter=adapter)
        result = _run(service.execute(_service_request()))
        assert result.code == "BROKER_FAILURE"  # steril kod
        for value in (result.code, result.status.value):
            assert "SECRET" not in str(value)


# ── Tek broker yazma kuralı ──────────────────────────────────────────

class TestSingleBrokerWriteRule:
    def test_exactly_one_call_on_success(self):
        service, adapter, *_ = _service()
        _run(service.execute(_service_request()))
        assert len(adapter.submit_calls) == 1

    def test_no_second_call_on_failure(self):
        adapter = SpyAdapter(result=_broker_result(
            BrokerOperationStatus.TEMPORARY_FAILURE,
            BrokerErrorCode.TIMEOUT))
        service, *_ = _service(adapter=adapter)
        _run(service.execute(_service_request()))
        assert len(adapter.submit_calls) == 1  # otomatik retry YOK

    def test_no_call_from_exception_recovery(self):
        adapter = SpyAdapter(submit_error=RuntimeError("boom"))
        service, *_ = _service(adapter=adapter)
        _run(service.execute(_service_request()))
        assert len(adapter.submit_calls) == 1

    @pytest.mark.parametrize("denial", [
        RiskDecision(decision=RiskDecisionType.REJECT, code="X"),
        RiskDecision(decision=RiskDecisionType.REQUIRE_CONFIRMATION,
                     code="X"),
        RiskDecision(decision=RiskDecisionType.REDUCE_SIZE,
                     code="X", approved_quantity=D("0.1"))])
    def test_zero_calls_on_every_denied_path(self, denial):
        service, adapter, *_ = _service(decision=denial)
        _run(service.execute(_service_request()))
        assert adapter.submit_calls == []

    @pytest.mark.parametrize("denial", [
        RiskDecision(decision=RiskDecisionType.REJECT, code="X"),
        RiskDecision(decision=RiskDecisionType.REQUIRE_CONFIRMATION,
                     code="X"),
        RiskDecision(decision=RiskDecisionType.REDUCE_SIZE,
                     code="X", approved_quantity=D("0.1"))])
    def test_zero_adapter_runtime_on_denied_paths(self, denial):
        # SpyAdapter'da gönderim dışı TÜM kancalar patlar; reddedilen
        # yol adaptör çalışma zamanına hiç dokunmamalıdır
        service, adapter, *_ = _service(decision=denial)
        result = _run(service.execute(_service_request()))
        assert result.status is not _STATUS.UNKNOWN_FAILURE
        assert adapter.submit_calls == []

    def test_zero_adapter_runtime_when_kill_switch_blocks(self):
        service, adapter, *_ = _service(kill_switch=KillSwitch())
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.BLOCKED_BY_KILL_SWITCH
        assert adapter.submit_calls == []

    def test_no_adapter_profile_call_in_service(self):
        source = _code_source(execution_service)
        assert "adapter.profile" not in source
        assert "health_check" not in source

    def test_no_loop_or_finally_around_submission(self):
        tree = ast.parse(inspect.getsource(execution_service))
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.For, ast.While,
                                         ast.AsyncFor))
            if isinstance(node, ast.Try):
                assert node.finalbody == []

    def test_read_operations_never_used_for_writes(self):
        source = inspect.getsource(execution_service)
        for token in ("cancel_order", "get_balances",
                      "get_positions", "list_open_orders"):
            assert token not in source

    def test_no_exactly_once_claim_in_docs(self):
        for module in SERVICE_MODULES:
            source = inspect.getsource(module).lower()
            assert "exactly-once" not in source
            assert "exactly once" not in source
            assert "tam olarak bir kez garanti" not in source

    def test_toctou_limitation_documented(self):
        doc = execution_service.__doc__
        assert "atomiklik" in doc.lower() or \
            "atomicity" in doc.lower()
        assert "İDDİA" in doc


# ── Değişmez servis modelleri ────────────────────────────────────────

class TestServiceModels:
    def test_request_frozen_slots(self):
        request = _service_request()
        with pytest.raises(dataclasses.FrozenInstanceError):
            request.broker_id = "x"
        assert not hasattr(request, "__dict__")

    def test_result_frozen_slots_hashable(self):
        result = ExecutionServiceResult(
            status=_STATUS.INVALID_REQUEST,
            trace=ExecutionTrace())
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.status = _STATUS.SUBMITTED
        assert not hasattr(result, "__dict__")
        assert isinstance(hash(result), int)

    def test_trace_frozen_hashable(self):
        trace = ExecutionTrace(steps=(_STEP.INPUT_VALIDATED,))
        with pytest.raises(dataclasses.FrozenInstanceError):
            trace.steps = ()
        assert isinstance(hash(trace), int)

    @pytest.mark.parametrize("model", [
        ExecutionServiceRequest, ExecutionServiceResult,
        ExecutionTrace, ExecutionPermission])
    def test_no_mutable_defaults(self, model):
        for field in dataclasses.fields(model):
            if field.default is not dataclasses.MISSING:
                assert not isinstance(field.default,
                                      (list, dict, set))
            assert field.default_factory is dataclasses.MISSING

    @pytest.mark.parametrize("model", [
        ExecutionServiceRequest, ExecutionServiceResult,
        ExecutionTrace, ExecutionPermission])
    def test_explicitly_typed(self, model):
        for field in dataclasses.fields(model):
            assert field.type

    @pytest.mark.parametrize("mutation", [
        dict(execution_request=None), dict(portfolio=None),
        dict(instrument="BTCUSDT"), dict(broker_id=""),
        dict(broker_id="   "), dict(broker_id=1),
        dict(request_id=""), dict(idempotency_key=1),
        dict(execution_mode="PAPER"),
        dict(logical_sequence=-1), dict(logical_sequence=True),
        dict(logical_sequence="1")])
    def test_request_sterile_validation(self, mutation):
        with pytest.raises(ValueError,
                           match="INVALID_SERVICE_MODEL_FIELD"):
            _service_request(**mutation)

    def test_request_unknown_optionals_stay_none(self):
        request = ExecutionServiceRequest(
            execution_request=_exec_request(),
            portfolio=_portfolio(), instrument=_instrument(),
            broker_id="b1")
        for field in ("account_id", "portfolio_id", "strategy_id",
                      "request_id", "correlation_id",
                      "idempotency_key", "execution_mode",
                      "logical_sequence"):
            assert getattr(request, field) is None

    @pytest.mark.parametrize("bad", [
        dict(status="SUBMITTED", trace=ExecutionTrace()),
        dict(status=_STATUS.SUBMITTED, trace=()),
        dict(status=_STATUS.SUBMITTED, trace=ExecutionTrace(),
             code=""),
        dict(status=_STATUS.SUBMITTED, trace=ExecutionTrace(),
             recommended_quantity=0.5),
        dict(status=_STATUS.SUBMITTED, trace=ExecutionTrace(),
             broker_result={"raw": 1}),
        dict(status=_STATUS.SUBMITTED, trace=ExecutionTrace(),
             permission="PERMIT"),
        dict(status=_STATUS.SUBMITTED, trace=ExecutionTrace(),
             risk_decision="ALLOW")])
    def test_result_sterile_validation(self, bad):
        with pytest.raises(ValueError,
                           match="INVALID_SERVICE_MODEL_FIELD"):
            ExecutionServiceResult(**bad)

    @pytest.mark.parametrize("bad_steps", [
        ["INPUT_VALIDATED"], (1,), ("INPUT_VALIDATED",),
        [_STEP.INPUT_VALIDATED]])
    def test_trace_sterile_validation(self, bad_steps):
        with pytest.raises(ValueError,
                           match="INVALID_SERVICE_MODEL_FIELD"):
            ExecutionTrace(steps=bad_steps)

    def test_no_raw_payload_fields(self):
        for model in (ExecutionServiceRequest,
                      ExecutionServiceResult, ExecutionTrace,
                      ExecutionPermission):
            names = {f.name for f in dataclasses.fields(model)}
            for forbidden in ("raw_response", "native_payload",
                              "exchange_json", "sdk_object",
                              "http_response", "timestamp",
                              "created_at", "stack_trace",
                              "api_key", "secret"):
                assert forbidden not in names


# ── Kapalı enum'lar ──────────────────────────────────────────────────

class TestClosedEnums:
    def test_service_status_closed(self):
        assert tuple(s.name for s in ExecutionServiceStatus) == (
            "SUBMITTED", "NOT_SUBMITTED", "REJECTED_BY_RISK",
            "BLOCKED_BY_KILL_SWITCH", "REQUIRES_CONFIRMATION",
            "SIZE_REDUCTION_REQUIRED", "BROKER_REJECTED",
            "BROKER_TEMPORARY_FAILURE", "BROKER_PERMANENT_FAILURE",
            "BROKER_UNAVAILABLE", "INVALID_REQUEST",
            "UNKNOWN_FAILURE")

    def test_trace_steps_closed(self):
        assert tuple(s.name for s in ExecutionTraceStep) == (
            "INPUT_VALIDATED", "BROKER_RESOLVED", "RISK_EVALUATED",
            "RISK_ALLOWED", "RISK_DENIED", "KILL_SWITCH_CHECKED",
            "EXECUTION_PERMITTED", "EXECUTION_DENIED",
            "BROKER_SUBMISSION_STARTED",
            "BROKER_SUBMISSION_COMPLETED",
            "BROKER_SUBMISSION_FAILED", "RESULT_NORMALIZED")

    @pytest.mark.parametrize("enum_cls", [ExecutionServiceStatus,
                                          ExecutionTraceStep])
    def test_values_equal_names(self, enum_cls):
        for member in enum_cls:
            assert member.value == member.name


# ── İz determinizmi ──────────────────────────────────────────────────

class TestTraceDeterminism:
    def test_only_reached_steps_appear(self):
        service, *_ = _service(decision=RiskDecision(
            decision=RiskDecisionType.REJECT, code="X"))
        result = _run(service.execute(_service_request()))
        unreachable = {_STEP.RISK_ALLOWED,
                       _STEP.KILL_SWITCH_CHECKED,
                       _STEP.EXECUTION_PERMITTED,
                       _STEP.BROKER_SUBMISSION_STARTED,
                       _STEP.BROKER_SUBMISSION_COMPLETED,
                       _STEP.BROKER_SUBMISSION_FAILED,
                       _STEP.RESULT_NORMALIZED}
        assert not set(result.trace.steps) & unreachable

    def test_same_inputs_same_trace(self):
        for _ in range(3):
            service, *_ = _service()
            result = _run(service.execute(_service_request()))
            assert result.trace.steps[-1] is \
                _STEP.RESULT_NORMALIZED

    def test_trace_has_no_duplicates(self):
        service, *_ = _service()
        result = _run(service.execute(_service_request()))
        assert len(result.trace.steps) == \
            len(set(result.trace.steps))

    def test_trace_contains_only_logical_values(self):
        service, *_ = _service()
        result = _run(service.execute(_service_request()))
        for step in result.trace.steps:
            assert isinstance(step, ExecutionTraceStep)


# ── Kanonik sahiplik ─────────────────────────────────────────────────

class TestCanonicalOwnership:
    def test_no_duplicate_canonical_models(self):
        forbidden = {"ExecutionRequest", "ExecutionResult",
                     "Order", "Position", "Fill", "Instrument",
                     "BrokerProfile", "RiskDecision",
                     "BrokerOperationResult",
                     "BrokerRequestContext", "KillSwitch",
                     "ExecutionMode", "Portfolio"}
        for module in SERVICE_MODULES:
            defined = {node.name for node in ast.walk(
                ast.parse(inspect.getsource(module)))
                if isinstance(node, ast.ClassDef)}
            assert not defined & forbidden

    def test_canonical_imports_used(self):
        source = inspect.getsource(execution_service_models)
        assert "from execution_models import" in source
        assert "from execution_risk_models import" in source
        assert "from execution_broker_models import" in source

    def test_no_risk_calculation_in_service(self):
        source = inspect.getsource(execution_service)
        for token in ("validate_execution", "calculate_exposure",
                      "calculate_position_size", "notional",
                      "max_exposure", "daily_loss"):
            assert token not in source


# ── Güvenlik yasakları ───────────────────────────────────────────────

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


def _module_imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


class TestSecurity:
    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_forbidden_imports(self, module):
        roots = {m.split(".")[0] for m in _module_imports(module)}
        forbidden = {"os", "sys", "io", "pathlib", "socket", "ssl",
                     "http", "requests", "httpx", "aiohttp",
                     "urllib", "websockets", "uuid", "datetime",
                     "time", "random", "secrets", "hashlib",
                     "hmac", "threading", "multiprocessing",
                     "subprocess", "asyncio", "json", "pickle",
                     "sqlite3", "queue", "binance_spot_adapter",
                     "binance_normalizer", "binance_capabilities",
                     "monitoring_service", "monitoring_api",
                     "strategy_service", "app"}
        assert not roots & forbidden

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_allowed_imports_only(self, module):
        allowed = {"__future__", "abc", "enum", "dataclasses",
                   "types",
                   "decimal", "typing",
                   "execution_enums", "execution_models",
                   "execution_risk_models", "execution_risk_engine",
                   "execution_kill_switch",
                   "execution_broker_errors",
                   "execution_broker_models",
                   "execution_broker_adapter",
                   "execution_permission_gate",
                   "execution_service_models"}
        assert _module_imports(module) <= allowed

    @pytest.mark.parametrize("token", [
        "sleep", "retry", "backoff", "create_task", "ensure_future",
        "run_until_complete", "asyncio.run", "Thread", "Process(",
        "getenv", "environ", "open(", "datetime.now", "uuid4",
        "random.", "urandom", "http://", "https://", "wss://",
        "while True"])
    def test_no_forbidden_tokens(self, token):
        for module in SERVICE_MODULES:
            source = _code_source(module).replace("retryable", "")
            assert token not in source

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_dangerous_calls(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "open", "__import__",
                    "compile", "print", "input")

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_float_literals(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    def test_no_persistence_or_cache(self):
        for module in SERVICE_MODULES:
            source = _code_source(module)
            for token in ("cache", "session", "database", "sqlite",
                          "pickle", "shelve", "save(", "load(",
                          "write(", "read("):
                assert token not in source

    def test_stateless_no_mutable_class_state(self):
        # Emir önbelleği / istek haritası / kilit / kuyruk yok
        source = _code_source(execution_service)
        for token in ("self._orders", "self._requests",
                      "Lock(", "Queue(", "deque(", "defaultdict"):
            assert token not in source


# ── Kamu API dondurması ──────────────────────────────────────────────

class TestPublicApiFreeze:
    def test_service_module_surface(self):
        assert execution_service.__all__ == [
            "BrokerAdapterResolver", "ExecutionService",
            "ExecutionServiceError",
            "ExecutionServiceContractError",
            "ExecutionServiceConfigurationError"]

    def test_models_module_surface(self):
        assert execution_service_models.__all__ == [
            "ExecutionServiceStatus", "ExecutionTraceStep",
            "ExecutionTrace", "ExecutionServiceRequest",
            "ExecutionServiceResult"]

    def test_gate_module_surface(self):
        assert execution_permission_gate.__all__ == [
            "ExecutionPermission", "ExecutionPermissionGate"]

    def test_combined_surface_matches_spec(self):
        combined = set(execution_service.__all__) | \
            set(execution_service_models.__all__) | \
            set(execution_permission_gate.__all__)
        assert combined == {
            "ExecutionService", "ExecutionServiceRequest",
            "ExecutionServiceResult", "ExecutionServiceStatus",
            "ExecutionPermissionGate", "ExecutionPermission",
            "ExecutionTrace", "ExecutionTraceStep",
            "BrokerAdapterResolver", "ExecutionServiceError",
            "ExecutionServiceContractError",
            "ExecutionServiceConfigurationError"}

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_additional_public_names(self, module):
        public = {name for name, value in vars(module).items()
                  if not name.startswith("_")
                  and (inspect.isfunction(value)
                       or inspect.isclass(value))
                  and getattr(value, "__module__", None)
                  == module.__name__}
        assert public <= set(module.__all__)

    def test_no_http_framework_in_service(self):
        for module in SERVICE_MODULES:
            source = _code_source(module)
            for token in ("flask", "Flask", "route", "Blueprint",
                          "fastapi", "endpoint"):
                assert token not in source


# ── Mimari sertifikasyon ─────────────────────────────────────────────

class TestArchitectureCertification:
    def test_delivery_marked(self):
        path = os.path.join(_ROOT, "tests",
                            "test_mission_2000_architecture.py")
        with open(path, encoding="utf-8") as handle:
            assert "execution_service.py" in handle.read()

    def test_monitoring_independent_of_service(self):
        import monitoring_service
        roots = {m.split(".")[0]
                 for m in _module_imports(monitoring_service)}
        assert "execution_service" not in roots

    def test_service_mode_carried_not_authorized(self):
        # Mod politikası Mission 2100'ün işi; mod yalnız taşınır
        for mode in ExecutionMode:
            service, adapter, *_ = _service()
            result = _run(service.execute(_service_request(
                execution_mode=mode)))
            assert result.status is _STATUS.SUBMITTED
            assert adapter.contexts[0].execution_mode is mode

    def test_no_mode_branching_in_service(self):
        source = _code_source(execution_service)
        for token in ("MICRO_LIVE", "SHADOW", "LIVE", "PAPER"):
            assert token not in source

    def test_future_broker_pluggable_without_core_change(self):
        # Yeni adaptör yalnız resolver kaydıyla eklenir
        other = SpyAdapter()
        resolver = MapResolver({"other-venue": other})
        service = ExecutionService(StubRiskEngine(),
                                   _enabled_kill_switch(),
                                   resolver)
        result = _run(service.execute(_service_request(
            broker_id="other-venue")))
        assert result.status is _STATUS.SUBMITTED
        assert len(other.submit_calls) == 1


# ── Ek sertifikasyon: sonuç bütünlüğü ve determinizm ─────────────────

class TestResultIntegrity:
    @pytest.mark.parametrize("status", list(ExecutionServiceStatus))
    def test_every_status_constructible(self, status):
        result = ExecutionServiceResult(status=status,
                                        trace=ExecutionTrace())
        assert result.status is status
        assert result.submitted is (status is _STATUS.SUBMITTED)

    @pytest.mark.parametrize("denial,expect_broker_result", [
        (RiskDecisionType.REJECT, None),
        (RiskDecisionType.REQUIRE_CONFIRMATION, None),
        (RiskDecisionType.REDUCE_SIZE, None)])
    def test_denied_paths_carry_no_broker_result(
            self, denial, expect_broker_result):
        decision = RiskDecision(decision=denial, code="X")
        service, *_ = _service(decision=decision)
        result = _run(service.execute(_service_request()))
        assert result.broker_result is expect_broker_result

    def test_kill_switch_block_carries_permission(self):
        service, *_ = _service(kill_switch=KillSwitch())
        result = _run(service.execute(_service_request()))
        assert isinstance(result.permission, ExecutionPermission)
        assert result.permission.permitted is False
        assert result.broker_result is None

    def test_invalid_request_carries_nothing_else(self):
        service, *_ = _service()
        result = _run(service.execute(
            _service_request(idempotency_key=None)))
        assert result.risk_decision is None
        assert result.permission is None
        assert result.broker_result is None
        assert result.recommended_quantity is None

    def test_unknown_broker_carries_no_decision(self):
        service, *_ = _service()
        result = _run(service.execute(
            _service_request(broker_id="yok")))
        assert result.risk_decision is None
        assert result.broker_result is None

    @pytest.mark.parametrize("step", list(ExecutionTraceStep))
    def test_every_trace_step_hashable(self, step):
        assert isinstance(hash(step), int)
        assert hash(ExecutionTrace(steps=(step,))) == \
            hash(ExecutionTrace(steps=(step,)))

    def test_results_value_equal_when_inputs_equal(self):
        first = ExecutionServiceResult(
            status=_STATUS.NOT_SUBMITTED, trace=ExecutionTrace(),
            code="UNKNOWN_BROKER")
        second = ExecutionServiceResult(
            status=_STATUS.NOT_SUBMITTED, trace=ExecutionTrace(),
            code="UNKNOWN_BROKER")
        assert first == second and hash(first) == hash(second)

    def test_request_hashable(self):
        assert isinstance(hash(_service_request()), int)

    def test_request_value_equality(self):
        assert _service_request() == _service_request()


class TestAdditionalBoundaryCertification:
    @pytest.mark.parametrize("broker_status", [
        BrokerOperationStatus.REJECTED,
        BrokerOperationStatus.TEMPORARY_FAILURE,
        BrokerOperationStatus.PERMANENT_FAILURE,
        BrokerOperationStatus.UNSUPPORTED,
        BrokerOperationStatus.UNKNOWN])
    def test_broker_failures_keep_error_code(self, broker_status):
        adapter = SpyAdapter(result=_broker_result(broker_status))
        service, *_ = _service(adapter=adapter)
        result = _run(service.execute(_service_request()))
        assert result.code == "UNKNOWN_BROKER_FAILURE"
        assert result.broker_result.status is broker_status

    @pytest.mark.parametrize("exc", [
        ValueError("v"), KeyError("k"), TypeError("t"),
        RuntimeError("r")])
    def test_any_broker_exception_type_normalized(self, exc):
        adapter = SpyAdapter(submit_error=exc)
        service, *_ = _service(adapter=adapter)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.UNKNOWN_FAILURE
        assert result.code == "BROKER_FAILURE"

    @pytest.mark.parametrize("exc", [
        ValueError("v"), KeyError("k"), RuntimeError("r")])
    def test_any_risk_exception_type_normalized(self, exc):
        service, adapter, *_ = _service(raise_risk=exc)
        result = _run(service.execute(_service_request()))
        assert result.status is _STATUS.UNKNOWN_FAILURE
        assert adapter.submit_calls == []

    def test_service_result_never_raises_operationally(self):
        # Operasyonel sonuçlar istisna DEĞİL, sonuçtur
        for switch_state in (KillSwitch(),
                             _enabled_kill_switch()):
            service, *_ = _service(kill_switch=switch_state)
            result = _run(service.execute(_service_request()))
            assert isinstance(result, ExecutionServiceResult)

    def test_no_timestamps_anywhere_in_results(self):
        service, *_ = _service()
        result = _run(service.execute(_service_request()))
        for field in dataclasses.fields(ExecutionServiceResult):
            assert "time" not in field.name
            assert "stamp" not in field.name
        assert not hasattr(result.trace, "timestamps")

    def test_trace_is_tuple_not_list(self):
        service, *_ = _service()
        result = _run(service.execute(_service_request()))
        assert type(result.trace.steps) is tuple

    def test_gate_used_by_service_is_canonical(self):
        service, *_ = _service()
        assert isinstance(service._gate, ExecutionPermissionGate)

    def test_service_docs_mention_local_boundary(self):
        doc = execution_service.__doc__
        assert "yerel" in doc.lower() or "local" in doc.lower()
