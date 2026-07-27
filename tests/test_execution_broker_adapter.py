"""Mission 2000 — Agent 05 BrokerAdapter arayüz testleri.

Soyutluk, asenkron imzalar, tam operasyon kümesi, değişmez modeller,
kapalı enum'lar, Decimal-only, kanonik model sahipliği (kopya yok),
idempotency sözleşmesi, deterministik bağlam, normalize sonuçlar,
kapalı hata sınıflandırması, native payload izolasyonu, sağlık ve
bakiye sözleşmeleri, okuma/yazma ayrımı, iptal semantiği, kamu API
dondurması, yasak importlar/yetenekler doğrulanır.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import os
import sys
from decimal import Decimal

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import execution_broker_adapter
import execution_broker_errors
import execution_broker_models
from execution_broker_adapter import BrokerAdapter
from execution_broker_errors import (
    BrokerAdapterError, BrokerConfigurationError,
    BrokerContractError, BrokerErrorCode, BrokerErrorDetail,
    BrokerNormalizationError)
from execution_broker_models import (
    BalancesQuery, BrokerBalance, BrokerHealth, BrokerHealthState,
    BrokerOperationResult, BrokerOperationStatus,
    BrokerRequestContext, CancelOrderRequest, ExecutionMode,
    OpenOrdersQuery, OrderQuery, PositionsQuery)
from execution_enums import OrderSide, OrderState, OrderType, \
    TimeInForce
from execution_models import ExecutionRequest, Order
from execution_risk_models import BrokerProfile

D = Decimal

READ_OPS = ("profile", "health_check", "get_order",
            "list_open_orders", "get_positions", "get_balances")
WRITE_OPS = ("submit_order", "cancel_order")
ALL_OPS = READ_OPS + WRITE_OPS

BROKER_MODULES = (execution_broker_adapter, execution_broker_models,
                  execution_broker_errors)


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


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _exec_request(**overrides):
    base = dict(symbol="SYM-1", side=OrderSide.BUY,
                order_type=OrderType.LIMIT, quantity=D("1"),
                time_in_force=TimeInForce.GTC, price=D("100"))
    base.update(overrides)
    return ExecutionRequest(**base)


def _context(**overrides):
    base = dict(request_id="req-1", idempotency_key="idem-1",
                execution_mode=ExecutionMode.PAPER,
                logical_sequence=1)
    base.update(overrides)
    return BrokerRequestContext(**base)


def _success(**fields):
    return BrokerOperationResult(
        status=BrokerOperationStatus.SUCCESS, **fields)


class _FakeAdapter(BrokerAdapter):
    """Test amaçlı somut adaptör — yalnız kanonik nesneler döner."""

    __slots__ = ("calls",)

    def __init__(self):
        object.__setattr__(self, "calls", [])

    async def _do_profile(self):
        self.calls.append("profile")
        return BrokerProfile(supports_cancel=True)

    async def _do_health_check(self):
        self.calls.append("health_check")
        return BrokerHealth(state=BrokerHealthState.HEALTHY,
                            read_available=True,
                            write_available=False)

    async def _do_get_order(self, query, context):
        self.calls.append("get_order")
        return _success(order=Order(
            symbol=query.symbol, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=D("1"),
            time_in_force=TimeInForce.GTC,
            state=OrderState.SUBMITTED))

    async def _do_list_open_orders(self, query, context):
        self.calls.append("list_open_orders")
        return _success(orders=())

    async def _do_get_positions(self, query, context):
        self.calls.append("get_positions")
        return _success(positions=())

    async def _do_get_balances(self, query, context):
        self.calls.append("get_balances")
        return _success(balances=(BrokerBalance(currency="USD"),))

    async def _do_submit_order(self, request, context):
        self.calls.append("submit_order")
        return _success()

    async def _do_cancel_order(self, request, context):
        self.calls.append("cancel_order")
        return _success()


# ── Soyutluk ve asenkron imzalar ─────────────────────────────────────

class TestAbstractAsyncInterface:
    def test_adapter_is_abstract(self):
        assert inspect.isabstract(BrokerAdapter)
        with pytest.raises(TypeError):
            BrokerAdapter()

    def test_incomplete_subclass_still_abstract(self):
        class Partial(BrokerAdapter):
            async def _do_profile(self):
                ...
        with pytest.raises(TypeError):
            Partial()

    @pytest.mark.parametrize("operation", ALL_OPS)
    def test_operations_are_async(self, operation):
        assert inspect.iscoroutinefunction(
            getattr(BrokerAdapter, operation))

    @pytest.mark.parametrize("hook", [
        "_do_profile", "_do_health_check", "_do_get_order",
        "_do_list_open_orders", "_do_get_positions",
        "_do_get_balances", "_do_submit_order", "_do_cancel_order"])
    def test_hooks_are_abstract_async(self, hook):
        method = getattr(BrokerAdapter, hook)
        assert getattr(method, "__isabstractmethod__", False)
        assert inspect.iscoroutinefunction(method)

    def test_exact_public_operation_set(self):
        public = {name for name in dir(BrokerAdapter)
                  if not name.startswith("_")}
        assert public == set(ALL_OPS)

    def test_no_public_mutable_state(self):
        assert BrokerAdapter.__slots__ == ()

    @pytest.mark.parametrize("operation,request_type", [
        ("get_order", OrderQuery),
        ("list_open_orders", OpenOrdersQuery),
        ("get_positions", PositionsQuery),
        ("get_balances", BalancesQuery),
        ("submit_order", ExecutionRequest),
        ("cancel_order", CancelOrderRequest)])
    def test_signatures_canonical_types(self, operation,
                                        request_type):
        signature = inspect.signature(
            getattr(BrokerAdapter, operation))
        params = list(signature.parameters.values())
        assert len(params) == 3  # self, request/query, context
        assert params[2].name == "context"

    @pytest.mark.parametrize("operation", READ_OPS[2:] + WRITE_OPS)
    def test_operations_return_normalized_result(self, operation):
        adapter = _FakeAdapter()
        args = {
            "get_order": (OrderQuery(symbol="S", order_id="1"),),
            "list_open_orders": (OpenOrdersQuery(),),
            "get_positions": (PositionsQuery(),),
            "get_balances": (BalancesQuery(),),
            "submit_order": (_exec_request(),),
            "cancel_order": (CancelOrderRequest(
                symbol="S", order_id="1"),),
        }[operation]
        result = _run(getattr(adapter, operation)(*args,
                                                  _context()))
        assert isinstance(result, BrokerOperationResult)

    def test_profile_returns_canonical_broker_profile(self):
        assert isinstance(_run(_FakeAdapter().profile()),
                          BrokerProfile)

    def test_health_check_returns_broker_health(self):
        assert isinstance(_run(_FakeAdapter().health_check()),
                          BrokerHealth)


# ── Kanonik model sahipliği ──────────────────────────────────────────

class TestCanonicalOwnership:
    def test_no_duplicate_domain_models(self):
        # Broker modüllerinde ikinci Order/Position/Fill/
        # BrokerProfile/ExecutionRequest tanımı yasak
        forbidden = {"Order", "Position", "Fill", "BrokerProfile",
                     "ExecutionRequest", "Instrument"}
        for module in BROKER_MODULES:
            defined = {node.name for node in ast.walk(
                ast.parse(inspect.getsource(module)))
                if isinstance(node, ast.ClassDef)}
            assert not defined & forbidden

    def test_canonical_imports_used(self):
        source = inspect.getsource(execution_broker_models)
        assert "from execution_models import" in source
        source = inspect.getsource(execution_broker_adapter)
        assert "from execution_risk_models import" in source
        assert "from execution_models import" in source

    def test_result_carries_canonical_order(self):
        result = _run(_FakeAdapter().get_order(
            OrderQuery(symbol="S", order_id="1"), _context()))
        assert isinstance(result.order, Order)

    def test_no_structural_duplicate_of_broker_profile(self):
        # Yetenek bayrakları yalnız kanonik BrokerProfile'da
        for model in (BrokerRequestContext, BrokerHealth,
                      BrokerBalance, BrokerOperationResult):
            names = {f.name for f in dataclasses.fields(model)}
            assert not any(n.startswith("supports_")
                           for n in names)


# ── Değişmez modeller ────────────────────────────────────────────────

BROKER_DATA_MODELS = (BrokerRequestContext, BrokerHealth,
                      BrokerBalance, CancelOrderRequest, OrderQuery,
                      OpenOrdersQuery, PositionsQuery,
                      BalancesQuery, BrokerOperationResult,
                      BrokerErrorDetail)

MODEL_SAMPLES = {
    BrokerRequestContext: _context,
    BrokerHealth: lambda: BrokerHealth(
        state=BrokerHealthState.HEALTHY),
    BrokerBalance: lambda: BrokerBalance(
        currency="USD", total=D("10"), available=D("4"),
        reserved=D("6")),
    CancelOrderRequest: lambda: CancelOrderRequest(
        symbol="S", order_id="1"),
    OrderQuery: lambda: OrderQuery(symbol="S", order_id="1"),
    OpenOrdersQuery: lambda: OpenOrdersQuery(symbol="S"),
    PositionsQuery: lambda: PositionsQuery(),
    BalancesQuery: lambda: BalancesQuery(currency="USD"),
    BrokerOperationResult: _success,
    BrokerErrorDetail: lambda: BrokerErrorDetail(
        code=BrokerErrorCode.TIMEOUT, retryable=True),
}


class TestImmutableModels:
    @pytest.mark.parametrize("model", BROKER_DATA_MODELS)
    def test_frozen(self, model):
        instance = MODEL_SAMPLES[model]()
        field = dataclasses.fields(model)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, field, None)

    @pytest.mark.parametrize("model", BROKER_DATA_MODELS)
    def test_slots(self, model):
        assert not hasattr(MODEL_SAMPLES[model](), "__dict__")

    @pytest.mark.parametrize("model", BROKER_DATA_MODELS)
    def test_hashable_and_value_equal(self, model):
        assert isinstance(hash(MODEL_SAMPLES[model]()), int)
        assert MODEL_SAMPLES[model]() == MODEL_SAMPLES[model]()

    @pytest.mark.parametrize("model", BROKER_DATA_MODELS)
    def test_no_mutable_defaults(self, model):
        for field in dataclasses.fields(model):
            if field.default is not dataclasses.MISSING:
                assert not isinstance(field.default,
                                      (list, dict, set))
            assert field.default_factory is dataclasses.MISSING

    @pytest.mark.parametrize("model", BROKER_DATA_MODELS)
    def test_explicitly_typed_fields(self, model):
        for field in dataclasses.fields(model):
            assert field.type


# ── Kapalı enum'lar ──────────────────────────────────────────────────

class TestClosedEnums:
    def test_operation_statuses_closed(self):
        assert tuple(s.name for s in BrokerOperationStatus) == (
            "SUCCESS", "REJECTED", "NOT_FOUND", "UNSUPPORTED",
            "TEMPORARY_FAILURE", "PERMANENT_FAILURE", "UNKNOWN")

    def test_health_states_closed(self):
        assert tuple(s.name for s in BrokerHealthState) == (
            "HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN")

    def test_execution_modes_closed(self):
        assert tuple(m.name for m in ExecutionMode) == (
            "PAPER", "SHADOW", "MICRO_LIVE", "LIVE")

    def test_error_codes_closed(self):
        assert tuple(c.name for c in BrokerErrorCode) == (
            "INVALID_REQUEST", "INVALID_CONTEXT",
            "MISSING_IDEMPOTENCY_KEY", "IDEMPOTENCY_CONFLICT",
            "UNSUPPORTED_OPERATION", "UNSUPPORTED_ASSET",
            "UNSUPPORTED_ORDER_TYPE", "AUTHENTICATION_FAILURE",
            "AUTHORIZATION_FAILURE", "RATE_LIMITED",
            "MARKET_CLOSED", "ORDER_REJECTED", "ORDER_NOT_FOUND",
            "INSUFFICIENT_FUNDS", "INVALID_INSTRUMENT",
            "NETWORK_FAILURE", "BROKER_UNAVAILABLE", "TIMEOUT",
            "MALFORMED_BROKER_RESPONSE", "UNKNOWN_BROKER_FAILURE")

    @pytest.mark.parametrize("enum_cls", [
        BrokerOperationStatus, BrokerHealthState, ExecutionMode,
        BrokerErrorCode])
    def test_values_equal_names(self, enum_cls):
        for member in enum_cls:
            assert member.value == member.name


# ── Idempotency sözleşmesi ───────────────────────────────────────────

class TestIdempotencyContract:
    @pytest.mark.parametrize("operation,request_obj", [
        ("submit_order", _exec_request()),
        ("cancel_order", CancelOrderRequest(symbol="S",
                                            order_id="1"))])
    @pytest.mark.parametrize("key", [None, "", "   "])
    def test_write_without_key_rejected_before_io(
            self, operation, request_obj, key):
        adapter = _FakeAdapter()
        with pytest.raises(BrokerContractError,
                           match="MISSING_IDEMPOTENCY_KEY"):
            _run(getattr(adapter, operation)(
                request_obj, _context(idempotency_key=key)))
        assert adapter.calls == []  # I/O kancası hiç çağrılmadı

    @pytest.mark.parametrize("operation,request_obj", [
        ("submit_order", _exec_request()),
        ("cancel_order", CancelOrderRequest(symbol="S",
                                            order_id="1"))])
    def test_write_with_key_accepted(self, operation, request_obj):
        adapter = _FakeAdapter()
        result = _run(getattr(adapter, operation)(request_obj,
                                                  _context()))
        assert result.status is BrokerOperationStatus.SUCCESS
        assert adapter.calls == [operation]

    @pytest.mark.parametrize("operation,query", [
        ("get_order", OrderQuery(symbol="S", order_id="1")),
        ("list_open_orders", OpenOrdersQuery()),
        ("get_positions", PositionsQuery()),
        ("get_balances", BalancesQuery())])
    def test_reads_do_not_require_key(self, operation, query):
        adapter = _FakeAdapter()
        result = _run(getattr(adapter, operation)(
            query, _context(idempotency_key=None)))
        assert isinstance(result, BrokerOperationResult)

    def test_adapter_never_invents_key(self):
        # Kaynakta örtük anahtar üretimi yok
        for module in BROKER_MODULES:
            source = inspect.getsource(module)
            for token in ("uuid", "token_hex", "randbytes",
                          "monotonic", "perf_counter"):
                assert token not in source

    def test_idempotency_conflict_code_exists(self):
        assert BrokerErrorCode.IDEMPOTENCY_CONFLICT.value == \
            "IDEMPOTENCY_CONFLICT"


# ── Deterministik bağlam ─────────────────────────────────────────────

class TestRequestContext:
    def test_fields_closed(self):
        assert tuple(f.name for f in dataclasses.fields(
            BrokerRequestContext)) == (
            "request_id", "correlation_id", "idempotency_key",
            "account_id", "portfolio_id", "strategy_id",
            "execution_mode", "logical_sequence")

    def test_all_optional_default_none(self):
        context = BrokerRequestContext()
        for field in dataclasses.fields(BrokerRequestContext):
            assert getattr(context, field.name) is None

    def test_unknown_never_becomes_empty_or_zero(self):
        context = BrokerRequestContext()
        assert context.request_id is None
        assert context.logical_sequence is None

    @pytest.mark.parametrize("field,bad", [
        ("request_id", 1), ("correlation_id", 2.0),
        ("idempotency_key", True), ("account_id", b"x"),
        ("portfolio_id", ()), ("strategy_id", []),
        ("execution_mode", "PAPER"), ("execution_mode", 1),
        ("logical_sequence", "1"), ("logical_sequence", 1.0),
        ("logical_sequence", True), ("logical_sequence", -1)])
    def test_sterile_validation(self, field, bad):
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            BrokerRequestContext(**{field: bad})

    @pytest.mark.parametrize("mode", tuple(ExecutionMode))
    def test_all_modes_carried_not_authorized(self, mode):
        # Adaptör modu taşır; yetkilendirme mantığı yoktur
        context = _context(execution_mode=mode)
        assert context.execution_mode is mode
        result = _run(_FakeAdapter().submit_order(_exec_request(),
                                                  context))
        assert result.status is BrokerOperationStatus.SUCCESS

    def test_invalid_context_rejected(self):
        with pytest.raises(BrokerContractError,
                           match="INVALID_CONTEXT"):
            _run(_FakeAdapter().get_positions(PositionsQuery(),
                                              {"ctx": 1}))

    def test_invalid_request_rejected(self):
        with pytest.raises(BrokerContractError,
                           match="INVALID_REQUEST"):
            _run(_FakeAdapter().submit_order({"symbol": "S"},
                                             _context()))


# ── Sonuç normalizasyonu ─────────────────────────────────────────────

class TestResultNormalization:
    @pytest.mark.parametrize("status", [
        s for s in BrokerOperationStatus
        if s is not BrokerOperationStatus.SUCCESS])
    def test_non_success_requires_error_detail(self, status):
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            BrokerOperationResult(status=status)
        result = BrokerOperationResult(
            status=status, error=BrokerErrorDetail(
                code=BrokerErrorCode.UNKNOWN_BROKER_FAILURE))
        assert result.error is not None

    def test_success_needs_no_error(self):
        assert _success().error is None

    def test_ordinary_outcomes_are_results_not_exceptions(self):
        # Ret/bulunamadı/desteklenmiyor istisna DEĞİL sonuçtur
        for status, code in (
                (BrokerOperationStatus.REJECTED,
                 BrokerErrorCode.ORDER_REJECTED),
                (BrokerOperationStatus.NOT_FOUND,
                 BrokerErrorCode.ORDER_NOT_FOUND),
                (BrokerOperationStatus.UNSUPPORTED,
                 BrokerErrorCode.UNSUPPORTED_OPERATION),
                (BrokerOperationStatus.REJECTED,
                 BrokerErrorCode.INSUFFICIENT_FUNDS),
                (BrokerOperationStatus.REJECTED,
                 BrokerErrorCode.MARKET_CLOSED)):
            result = BrokerOperationResult(
                status=status,
                error=BrokerErrorDetail(code=code))
            assert result.status is status

    def test_typed_payload_fields_only(self):
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            _success(orders=({"raw": 1},))
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            _success(order={"raw": 1})
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            _success(balances=[BrokerBalance(currency="USD")])

    def test_retry_classification_statuses_exist(self):
        assert BrokerOperationStatus.TEMPORARY_FAILURE
        assert BrokerOperationStatus.PERMANENT_FAILURE
        assert BrokerErrorCode.RATE_LIMITED


# ── İptal semantiği ──────────────────────────────────────────────────

class TestCancellationSemantics:
    def test_cancel_request_requires_identifier(self):
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            CancelOrderRequest(symbol="S")

    def test_cancel_by_client_order_id(self):
        request = CancelOrderRequest(symbol="S",
                                     client_order_id="c-1")
        assert request.client_order_id == "c-1"

    def test_outcomes_not_collapsed_to_bool(self):
        # Ayrık iptal sonuçları temsil edilebilir olmalı
        accepted = _success()
        not_found = BrokerOperationResult(
            status=BrokerOperationStatus.NOT_FOUND,
            error=BrokerErrorDetail(
                code=BrokerErrorCode.ORDER_NOT_FOUND))
        unsupported = BrokerOperationResult(
            status=BrokerOperationStatus.UNSUPPORTED,
            error=BrokerErrorDetail(
                code=BrokerErrorCode.UNSUPPORTED_OPERATION))
        temporary = BrokerOperationResult(
            status=BrokerOperationStatus.TEMPORARY_FAILURE,
            error=BrokerErrorDetail(code=BrokerErrorCode.TIMEOUT,
                                    retryable=True))
        already = BrokerOperationResult(
            status=BrokerOperationStatus.REJECTED,
            error=BrokerErrorDetail(
                code=BrokerErrorCode.ORDER_REJECTED))
        outcomes = {accepted, not_found, unsupported, temporary,
                    already}
        assert len(outcomes) == 5
        for outcome in outcomes:
            assert not isinstance(outcome, bool)


# ── Sağlık ve bakiye sözleşmeleri ────────────────────────────────────

class TestHealthAndBalance:
    def test_health_fields_closed(self):
        assert tuple(f.name for f in dataclasses.fields(
            BrokerHealth)) == (
            "state", "logical_sequence", "reason_code", "message",
            "read_available", "write_available")

    @pytest.mark.parametrize("state", tuple(BrokerHealthState))
    def test_all_health_states_valid(self, state):
        assert BrokerHealth(state=state).state is state

    def test_health_no_wall_clock_field(self):
        names = {f.name for f in dataclasses.fields(BrokerHealth)}
        for forbidden in ("timestamp", "checked_at", "time",
                          "datetime"):
            assert forbidden not in names

    def test_health_sterile_validation(self):
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            BrokerHealth(state="HEALTHY")
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            BrokerHealth(state=BrokerHealthState.HEALTHY,
                         logical_sequence=1.5)

    def test_balance_unknowns_stay_none(self):
        balance = BrokerBalance(currency="USD")
        assert balance.total is None
        assert balance.available is None
        assert balance.reserved is None

    def test_balance_unknown_never_zero(self):
        balance = BrokerBalance(currency="USD")
        assert balance.total != D("0")

    @pytest.mark.parametrize("bad", [
        dict(currency=""), dict(currency="   "),
        dict(currency="USD", reserved=D("-1")),
        dict(currency="USD", total=D("-1")),
        dict(currency="USD", total=D("-0.01"), available=D("-1")),
        dict(currency="USD", total=D("5"), available=D("6")),
        dict(currency="USD", total=0.5),
        dict(currency="USD", available=1),
        dict(currency="USD", reserved=True)])
    def test_balance_invariants(self, bad):
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            BrokerBalance(**bad)

    def test_balance_valid_when_consistent(self):
        balance = BrokerBalance(currency="USD", total=D("10"),
                                available=D("4"), reserved=D("6"))
        assert balance.available <= balance.total


# ── Okuma/yazma ayrımı ───────────────────────────────────────────────

class TestReadWriteSeparation:
    def test_classification_frozen(self):
        assert execution_broker_adapter._READ_OPERATIONS == \
            frozenset(READ_OPS)
        assert execution_broker_adapter._WRITE_OPERATIONS == \
            frozenset(WRITE_OPS)

    def test_classification_disjoint_and_complete(self):
        reads = execution_broker_adapter._READ_OPERATIONS
        writes = execution_broker_adapter._WRITE_OPERATIONS
        assert not reads & writes
        assert reads | writes == set(ALL_OPS)

    @pytest.mark.parametrize("operation", WRITE_OPS)
    def test_writes_identifiable_without_broker_knowledge(
            self, operation):
        assert operation in \
            execution_broker_adapter._WRITE_OPERATIONS


# ── Hata sınıflandırması ve istisna sınırı ───────────────────────────

class TestErrorTaxonomy:
    def test_exception_hierarchy_minimal_closed(self):
        assert issubclass(BrokerContractError, BrokerAdapterError)
        assert issubclass(BrokerConfigurationError,
                          BrokerAdapterError)
        assert issubclass(BrokerNormalizationError,
                          BrokerAdapterError)
        defined = {node.name for node in ast.walk(ast.parse(
            inspect.getsource(execution_broker_errors)))
            if isinstance(node, ast.ClassDef)}
        exception_classes = {n for n in defined
                             if n.endswith("Error")}
        assert exception_classes == {
            "BrokerAdapterError", "BrokerContractError",
            "BrokerConfigurationError", "BrokerNormalizationError"}

    def test_error_detail_sterile_fields_only(self):
        names = {f.name for f in dataclasses.fields(
            BrokerErrorDetail)}
        assert names == {"code", "message", "retryable"}
        for forbidden in ("api_key", "secret", "signature",
                          "authorization", "raw_request",
                          "raw_response", "stack_trace",
                          "traceback", "headers"):
            assert forbidden not in names

    @pytest.mark.parametrize("bad", [
        dict(code="TIMEOUT"), dict(code=None),
        dict(code=BrokerErrorCode.TIMEOUT, message=1),
        dict(code=BrokerErrorCode.TIMEOUT, retryable="yes")])
    def test_error_detail_sterile_validation(self, bad):
        with pytest.raises(ValueError,
                           match="INVALID_BROKER_MODEL_FIELD"):
            BrokerErrorDetail(**bad)

    def test_retryable_unknown_stays_none(self):
        detail = BrokerErrorDetail(code=BrokerErrorCode.TIMEOUT)
        assert detail.retryable is None


# ── Native payload izolasyonu ────────────────────────────────────────

class TestNativePayloadIsolation:
    FORBIDDEN_FIELDS = ("raw_response", "native_payload",
                        "exchange_json", "sdk_object",
                        "http_response", "raw_request",
                        "native_response")

    @pytest.mark.parametrize("model", BROKER_DATA_MODELS)
    def test_no_raw_payload_fields(self, model):
        names = {f.name for f in dataclasses.fields(model)}
        assert not names & set(self.FORBIDDEN_FIELDS)

    def test_no_raw_tokens_in_source(self):
        for module in BROKER_MODULES:
            source = _code_source(module)
            for token in self.FORBIDDEN_FIELDS:
                assert token not in source

    def test_no_untyped_dict_contract(self):
        # Hiçbir model alanı dict/Any/bytes tipli değildir
        for model in BROKER_DATA_MODELS:
            for field in dataclasses.fields(model):
                text = str(field.type)
                for forbidden in ("dict", "Dict", "Any", "bytes",
                                  "object"):
                    assert forbidden not in text


# ── Kamu API dondurması ──────────────────────────────────────────────

class TestPublicApiFreeze:
    def test_adapter_module_surface(self):
        assert execution_broker_adapter.__all__ == ["BrokerAdapter"]

    def test_models_module_surface(self):
        assert execution_broker_models.__all__ == [
            "ExecutionMode", "BrokerOperationStatus",
            "BrokerHealthState", "BrokerRequestContext",
            "BrokerOperationResult", "BrokerHealth",
            "BrokerBalance", "CancelOrderRequest", "OrderQuery",
            "OpenOrdersQuery", "PositionsQuery", "BalancesQuery"]

    def test_errors_module_surface(self):
        assert execution_broker_errors.__all__ == [
            "BrokerErrorCode", "BrokerErrorDetail",
            "BrokerAdapterError", "BrokerContractError",
            "BrokerConfigurationError", "BrokerNormalizationError"]

    @pytest.mark.parametrize("module", BROKER_MODULES)
    def test_no_additional_public_callables(self, module):
        public = {name for name, value in vars(module).items()
                  if not name.startswith("_")
                  and (inspect.isfunction(value)
                       or inspect.isclass(value))
                  and getattr(value, "__module__", None)
                  == module.__name__}
        assert public <= set(module.__all__)

    def test_no_binance_classes_or_network_helpers(self):
        for module in BROKER_MODULES:
            source = _code_source(module)
            for token in ("Binance", "binance", "IBKR", "Midas",
                          "Bybit", "OKX", "Kraken", "fetch(",
                          "session", "client_session"):
                assert token not in source


# ── Güvenlik ve yasak importlar ──────────────────────────────────────

def _module_imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


class TestSecurity:
    @pytest.mark.parametrize("module", BROKER_MODULES)
    def test_no_forbidden_imports(self, module):
        roots = {m.split(".")[0] for m in _module_imports(module)}
        forbidden = {"os", "sys", "io", "pathlib", "socket", "ssl",
                     "http", "requests", "httpx", "aiohttp",
                     "urllib", "urllib3", "websocket",
                     "websockets", "uuid", "datetime", "time",
                     "random", "secrets", "hashlib", "hmac",
                     "threading", "multiprocessing", "subprocess",
                     "sqlite3", "pickle", "shelve", "ccxt",
                     "binance", "asyncio",
                     "execution_api", "execution_service",
                     "execution_risk_engine",
                     "execution_kill_switch"}
        assert not roots & forbidden

    @pytest.mark.parametrize("module", BROKER_MODULES)
    def test_allowed_imports_only(self, module):
        allowed = {"__future__", "abc", "enum", "dataclasses",
                   "decimal", "typing", "execution_enums",
                   "execution_models", "execution_risk_models",
                   "execution_broker_errors",
                   "execution_broker_models"}
        assert _module_imports(module) <= allowed

    @pytest.mark.parametrize("token", [
        "sleep", "retry", "backoff", "sched", "Timer", "while True",
        "getenv", "environ", "open(", "datetime.now", "uuid4",
        "random.", "urandom", "sign(", "hmac", "Authorization"])
    def test_no_retry_sleep_env_secret_tokens(self, token):
        for module in BROKER_MODULES:
            # "retryable" alanı sınıflandırma verisidir, retry
            # mekanizması değildir — taramadan muaf tutulur
            source = _code_source(module).replace("retryable", "")
            assert token not in source

    @pytest.mark.parametrize("module", BROKER_MODULES)
    def test_no_dangerous_calls(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "open", "__import__",
                    "compile")

    @pytest.mark.parametrize("module", BROKER_MODULES)
    def test_no_float_literals(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    def test_no_loops_in_adapter(self):
        # Retry döngüsü yok: adaptör modülünde hiç döngü yok
        tree = ast.parse(inspect.getsource(
            execution_broker_adapter))
        loops = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.For, ast.While,
                                      ast.AsyncFor))]
        assert loops == []


# ── Mimari değişmezler ───────────────────────────────────────────────

class TestArchitecturalInvariants:
    def test_adapter_independent_of_upper_layers(self):
        roots = {m.split(".")[0] for m in _module_imports(
            execution_broker_adapter)}
        for upper in ("execution_risk_engine",
                      "execution_kill_switch",
                      "execution_service", "execution_api",
                      "monitoring_api", "monitoring_service"):
            assert upper not in roots

    def test_adapter_never_authorizes_execution(self):
        source = inspect.getsource(execution_broker_adapter)
        for token in ("is_execution_allowed", "KillSwitch",
                      "validate_execution", "RiskEngine"):
            assert token not in source

    def test_no_broker_name_branches(self):
        for module in BROKER_MODULES:
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare):
                    for comp in ast.walk(node):
                        if isinstance(comp, ast.Constant) and \
                                isinstance(comp.value, str):
                            assert comp.value.lower() not in (
                                "binance", "ibkr", "midas",
                                "bybit", "okx", "kraken")

    def test_architecture_test_marks_delivery(self):
        path = os.path.join(_ROOT, "tests",
                            "test_mission_2000_architecture.py")
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert "execution_broker_adapter.py" in content

    def test_scanner_exemption_guard_contract_only(self):
        # Tarayıcı muafiyeti YALNIZ soyut, I/O'suz sözleşme modülü
        # için geçerlidir: modül soyut kalmalı ve tüm _do_ kancaları
        # soyut olmalıdır — aksi halde muafiyet kötüye kullanılıyor
        assert inspect.isabstract(BrokerAdapter)
        for name in dir(BrokerAdapter):
            if name.startswith("_do_"):
                assert getattr(getattr(BrokerAdapter, name),
                               "__isabstractmethod__", False)

    def test_new_broker_requires_no_core_changes(self):
        # Yeni bir somut adaptör sınıfı çekirdek kod değişikliği
        # olmadan tanımlanabilir
        class AnotherBroker(_FakeAdapter):
            __slots__ = ()
        result = _run(AnotherBroker().submit_order(_exec_request(),
                                                   _context()))
        assert result.status is BrokerOperationStatus.SUCCESS
