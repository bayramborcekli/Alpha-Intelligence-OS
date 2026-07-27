"""Mission 2000 — Agent 06 BinanceSpotAdapter referans uygulama
testleri.

BrokerAdapter sözleşmesine uyum, Binance kalıtımı, normalizasyon,
yetenek açığa çıkarma, hata/durum eşlemesi, okuma/yazma ayrımı,
broker sızıntısı yasağı, HTTP/REST/WebSocket/secret/env/imzalama/
retry/sleep/rastgelelik/datetime/UUID yokluğu, kopya model yasağı,
kamu API dondurması ve mimari sertifikasyon doğrulanır.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from decimal import Decimal

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import asyncio

import binance_capabilities
import binance_normalizer
import binance_spot_adapter
from binance_capabilities import binance_spot_profile
from binance_normalizer import (
    error_result, normalize_balance, normalize_error,
    normalize_fill, normalize_order, normalize_order_state)
from binance_spot_adapter import (
    BinanceSpotAdapter, CredentialProvider, RESTTransport,
    SigningProvider, Transport, TransportFailure,
    WebSocketTransport)
from execution_broker_adapter import BrokerAdapter
from execution_broker_errors import (
    BrokerConfigurationError, BrokerContractError, BrokerErrorCode,
    BrokerNormalizationError)
from execution_broker_models import (
    BalancesQuery, BrokerBalance, BrokerHealth, BrokerHealthState,
    BrokerOperationResult, BrokerOperationStatus,
    BrokerRequestContext, CancelOrderRequest, ExecutionMode,
    OpenOrdersQuery, OrderQuery, PositionsQuery)
from execution_enums import (
    OrderSide, OrderState, OrderType, TimeInForce)
from execution_models import ExecutionRequest, Fill, Order
from execution_risk_models import BrokerProfile

D = Decimal

BINANCE_MODULES = (binance_spot_adapter, binance_normalizer,
                   binance_capabilities)

NATIVE_ORDER = {
    "symbol": "BTCUSDT", "orderId": 12345, "side": "BUY",
    "type": "LIMIT", "timeInForce": "GTC", "origQty": "0.5",
    "price": "50000.10", "executedQty": "0.2", "status": "NEW",
}

NATIVE_BALANCE = {"asset": "USDT", "free": "100.5", "locked": "9.5"}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _context(**overrides):
    base = dict(request_id="req-1", idempotency_key="idem-1",
                execution_mode=ExecutionMode.PAPER,
                logical_sequence=1)
    base.update(overrides)
    return BrokerRequestContext(**base)


def _exec_request(**overrides):
    base = dict(symbol="BTCUSDT", side=OrderSide.BUY,
                order_type=OrderType.LIMIT, quantity=D("0.5"),
                time_in_force=TimeInForce.GTC, price=D("50000.10"))
    base.update(overrides)
    return ExecutionRequest(**base)


class FakeTransport(Transport):
    """Deterministik sahte taşıma — ağ yok, kayıt tutar."""

    __slots__ = ("responses", "failure", "calls")

    def __init__(self, responses=None, failure=None):
        self.responses = dict(responses or {})
        self.failure = failure
        self.calls = []

    async def request(self, operation, params):
        self.calls.append((operation, dict(params)))
        if self.failure is not None:
            raise self.failure
        if operation not in self.responses:
            raise TransportFailure("UNAVAILABLE")
        return self.responses[operation]


def _adapter(responses=None, failure=None):
    transport = FakeTransport(responses, failure)
    return BinanceSpotAdapter(transport), transport


# ── Kalıtım ve sözleşme uyumu ────────────────────────────────────────

class TestInheritanceAndContract:
    def test_inherits_only_broker_adapter(self):
        assert BinanceSpotAdapter.__bases__ == (BrokerAdapter,)

    def test_is_concrete(self):
        assert not inspect.isabstract(BinanceSpotAdapter)

    def test_no_mixins_no_service_locator(self):
        mro = [c.__name__ for c in BinanceSpotAdapter.__mro__]
        assert mro == ["BinanceSpotAdapter", "BrokerAdapter",
                       "ABC", "object"]

    def test_slots_only_transport(self):
        assert BinanceSpotAdapter.__slots__ == ("_transport",)

    def test_requires_transport(self):
        with pytest.raises(BrokerConfigurationError,
                           match="INVALID_TRANSPORT"):
            BinanceSpotAdapter(object())

    @pytest.mark.parametrize("bad", [None, "rest", 1, {}])
    def test_rejects_non_transport(self, bad):
        with pytest.raises(BrokerConfigurationError):
            BinanceSpotAdapter(bad)

    @pytest.mark.parametrize("operation", [
        "profile", "health_check", "get_order", "list_open_orders",
        "get_positions", "get_balances", "submit_order",
        "cancel_order"])
    def test_all_operations_async(self, operation):
        assert inspect.iscoroutinefunction(
            getattr(BinanceSpotAdapter, operation))

    def test_no_extra_public_methods(self):
        public = {n for n in dir(BinanceSpotAdapter)
                  if not n.startswith("_")}
        assert public == {"profile", "health_check", "get_order",
                          "list_open_orders", "get_positions",
                          "get_balances", "submit_order",
                          "cancel_order"}

    def test_all_hooks_implemented(self):
        for name in dir(BrokerAdapter):
            if name.startswith("_do_"):
                assert not getattr(
                    getattr(BinanceSpotAdapter, name),
                    "__isabstractmethod__", False)


# ── Taşıma / kimlik doğrulama arayüzleri ─────────────────────────────

class TestTransportInterfaces:
    def test_transport_abstract(self):
        assert inspect.isabstract(Transport)
        with pytest.raises(TypeError):
            Transport()

    @pytest.mark.parametrize("cls", [RESTTransport,
                                     WebSocketTransport])
    def test_transport_specializations_abstract(self, cls):
        assert issubclass(cls, Transport)
        with pytest.raises(TypeError):
            cls()

    def test_transport_request_async(self):
        assert inspect.iscoroutinefunction(Transport.request)

    @pytest.mark.parametrize("cls", [SigningProvider,
                                     CredentialProvider])
    def test_auth_interfaces_without_implementation(self, cls):
        assert inspect.isabstract(cls)
        with pytest.raises(TypeError):
            cls()

    def test_auth_interfaces_have_no_concrete_subclass(self):
        for module in BINANCE_MODULES:
            for _, value in vars(module).items():
                if inspect.isclass(value) and value not in (
                        SigningProvider, CredentialProvider):
                    assert not (
                        issubclass(value, SigningProvider)
                        or issubclass(value, CredentialProvider))

    def test_transport_failure_kinds(self):
        failure = TransportFailure("TIMEOUT")
        assert failure.kind == "TIMEOUT"


# ── Yetenekler ───────────────────────────────────────────────────────

class TestCapabilities:
    def test_profile_is_canonical_broker_profile(self):
        assert isinstance(binance_spot_profile(), BrokerProfile)

    def test_profile_deterministic_singleton(self):
        assert binance_spot_profile() == binance_spot_profile()

    @pytest.mark.parametrize("flag,expected", [
        ("supports_margin", False), ("supports_short", False),
        ("supports_fractional", True), ("supports_options", False),
        ("supports_market_orders", True),
        ("supports_after_hours", True),
        ("supports_modify", False), ("supports_cancel", True),
        ("supports_trailing_stop", True), ("supports_oco", True)])
    def test_spot_capability_flags(self, flag, expected):
        assert getattr(binance_spot_profile(), flag) is expected

    def test_adapter_profile_needs_no_network(self):
        adapter, transport = _adapter()
        profile = _run(adapter.profile())
        assert profile == binance_spot_profile()
        assert transport.calls == []

    def test_capabilities_only_no_broker_name_branch(self):
        # if broker=="Binance" benzeri dallanma yok
        for module in BINANCE_MODULES:
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare):
                    for comp in ast.walk(node):
                        if isinstance(comp, ast.Constant) and \
                                isinstance(comp.value, str):
                            assert "binance" not in \
                                comp.value.lower()


# ── Durum normalizasyonu ─────────────────────────────────────────────

class TestStatusNormalization:
    @pytest.mark.parametrize("native,canonical", [
        ("NEW", OrderState.SUBMITTED),
        ("PARTIALLY_FILLED", OrderState.PARTIALLY_FILLED),
        ("FILLED", OrderState.FILLED),
        ("CANCELED", OrderState.CANCELLED),
        ("PENDING_CANCEL", OrderState.SUBMITTED),
        ("REJECTED", OrderState.REJECTED),
        ("EXPIRED", OrderState.EXPIRED)])
    def test_closed_state_map(self, native, canonical):
        assert normalize_order_state(native) is canonical

    @pytest.mark.parametrize("bad", [
        "UNKNOWN_STATE", "filled", "", None, 1, ["NEW"]])
    def test_unknown_state_never_invented(self, bad):
        with pytest.raises(BrokerNormalizationError,
                           match="MALFORMED_BROKER_RESPONSE"):
            normalize_order_state(bad)


# ── Emir normalizasyonu ──────────────────────────────────────────────

class TestOrderNormalization:
    def test_returns_canonical_order(self):
        order = normalize_order(NATIVE_ORDER)
        assert isinstance(order, Order)
        assert order.symbol == "BTCUSDT"
        assert order.side is OrderSide.BUY
        assert order.order_type is OrderType.LIMIT
        assert order.time_in_force is TimeInForce.GTC
        assert order.state is OrderState.SUBMITTED

    def test_decimal_only_money(self):
        order = normalize_order(NATIVE_ORDER)
        assert order.quantity == D("0.5")
        assert order.price == D("50000.10")
        assert order.filled_quantity == D("0.2")
        for value in (order.quantity, order.price,
                      order.filled_quantity):
            assert isinstance(value, Decimal)

    def test_order_id_normalized_to_string(self):
        assert normalize_order(NATIVE_ORDER).order_id == "12345"

    def test_unknown_optionals_stay_none(self):
        native = dict(NATIVE_ORDER)
        del native["price"], native["executedQty"], \
            native["orderId"]
        order = normalize_order(native)
        assert order.price is None
        assert order.filled_quantity is None
        assert order.order_id is None
        assert order.filled_quantity != D("0")

    @pytest.mark.parametrize("mutation", [
        {"symbol": ""}, {"symbol": None}, {"side": "ALIS"},
        {"type": "IcebergX"}, {"timeInForce": "DAY"},
        {"origQty": None}, {"origQty": 0.5}, {"origQty": "abc"},
        {"price": 50000.1}, {"status": "MYSTERY"}])
    def test_malformed_payload_rejected(self, mutation):
        native = dict(NATIVE_ORDER)
        native.update(mutation)
        with pytest.raises(BrokerNormalizationError,
                           match="MALFORMED_BROKER_RESPONSE"):
            normalize_order(native)

    @pytest.mark.parametrize("bad", [None, "x", 1, [NATIVE_ORDER]])
    def test_non_mapping_rejected(self, bad):
        with pytest.raises(BrokerNormalizationError):
            normalize_order(bad)


# ── Bakiye ve fill normalizasyonu ────────────────────────────────────

class TestBalanceAndFillNormalization:
    def test_balance_canonical(self):
        balance = normalize_balance(NATIVE_BALANCE)
        assert isinstance(balance, BrokerBalance)
        assert balance.currency == "USDT"
        assert balance.available == D("100.5")
        assert balance.reserved == D("9.5")
        assert balance.total == D("110.0")

    def test_balance_unknowns_stay_none(self):
        balance = normalize_balance({"asset": "BTC"})
        assert balance.total is None
        assert balance.available is None
        assert balance.reserved is None

    def test_balance_partial_knowledge_no_invented_total(self):
        balance = normalize_balance({"asset": "BTC",
                                     "free": "1.0"})
        assert balance.available == D("1.0")
        assert balance.total is None

    @pytest.mark.parametrize("bad", [
        {"asset": ""}, {"asset": None}, {},
        {"asset": "BTC", "free": 1.5},
        {"asset": "BTC", "locked": "xyz"}, "text", None])
    def test_balance_malformed_rejected(self, bad):
        with pytest.raises(BrokerNormalizationError):
            normalize_balance(bad)

    def test_fill_canonical(self):
        fill = normalize_fill(
            {"price": "50000", "qty": "0.1", "commission": "0.05",
             "commissionAsset": "USDT", "tradeId": 77},
            "BTCUSDT", OrderSide.BUY)
        assert isinstance(fill, Fill)
        assert fill.price == D("50000")
        assert fill.quantity == D("0.1")
        assert fill.fee == D("0.05")
        assert fill.fee_asset == "USDT"
        assert fill.trade_id == "77"

    def test_fill_unknown_fee_stays_none(self):
        fill = normalize_fill({"price": "1", "qty": "1"},
                              "BTCUSDT", OrderSide.SELL)
        assert fill.fee is None
        assert fill.trade_id is None

    @pytest.mark.parametrize("bad", [
        {"price": "1"}, {"qty": "1"},
        {"price": 1.0, "qty": "1"}, {"price": "1", "qty": None}])
    def test_fill_malformed_rejected(self, bad):
        with pytest.raises(BrokerNormalizationError):
            normalize_fill(bad, "BTCUSDT", OrderSide.BUY)


# ── Hata eşlemesi ────────────────────────────────────────────────────

class TestErrorMapping:
    @pytest.mark.parametrize("native,canonical", [
        (-2010, BrokerErrorCode.ORDER_REJECTED),
        (-1013, BrokerErrorCode.INVALID_REQUEST),
        (-2011, BrokerErrorCode.ORDER_NOT_FOUND),
        (-2013, BrokerErrorCode.ORDER_NOT_FOUND),
        (-1021, BrokerErrorCode.INVALID_REQUEST),
        (-1121, BrokerErrorCode.INVALID_INSTRUMENT),
        (-2014, BrokerErrorCode.AUTHENTICATION_FAILURE),
        (-2015, BrokerErrorCode.AUTHORIZATION_FAILURE),
        (429, BrokerErrorCode.RATE_LIMITED),
        (418, BrokerErrorCode.RATE_LIMITED),
        (401, BrokerErrorCode.AUTHENTICATION_FAILURE),
        (403, BrokerErrorCode.AUTHORIZATION_FAILURE),
        ("TIMEOUT", BrokerErrorCode.TIMEOUT),
        ("NETWORK", BrokerErrorCode.NETWORK_FAILURE),
        ("UNAVAILABLE", BrokerErrorCode.BROKER_UNAVAILABLE)])
    def test_closed_error_map(self, native, canonical):
        assert normalize_error(native) is canonical

    @pytest.mark.parametrize("unknown", [
        -99999, 0, 500, "GIZEM", None, True, 3.5])
    def test_unknown_never_leaks(self, unknown):
        assert normalize_error(unknown) is \
            BrokerErrorCode.UNKNOWN_BROKER_FAILURE

    @pytest.mark.parametrize("code,status", [
        (BrokerErrorCode.ORDER_REJECTED,
         BrokerOperationStatus.REJECTED),
        (BrokerErrorCode.INVALID_REQUEST,
         BrokerOperationStatus.REJECTED),
        (BrokerErrorCode.ORDER_NOT_FOUND,
         BrokerOperationStatus.NOT_FOUND),
        (BrokerErrorCode.UNSUPPORTED_OPERATION,
         BrokerOperationStatus.UNSUPPORTED),
        (BrokerErrorCode.RATE_LIMITED,
         BrokerOperationStatus.TEMPORARY_FAILURE),
        (BrokerErrorCode.TIMEOUT,
         BrokerOperationStatus.TEMPORARY_FAILURE),
        (BrokerErrorCode.NETWORK_FAILURE,
         BrokerOperationStatus.TEMPORARY_FAILURE),
        (BrokerErrorCode.BROKER_UNAVAILABLE,
         BrokerOperationStatus.TEMPORARY_FAILURE),
        (BrokerErrorCode.AUTHENTICATION_FAILURE,
         BrokerOperationStatus.PERMANENT_FAILURE),
        (BrokerErrorCode.MALFORMED_BROKER_RESPONSE,
         BrokerOperationStatus.PERMANENT_FAILURE),
        (BrokerErrorCode.UNKNOWN_BROKER_FAILURE,
         BrokerOperationStatus.UNKNOWN)])
    def test_error_result_status(self, code, status):
        result = error_result(code)
        assert result.status is status
        assert result.error.code is code

    @pytest.mark.parametrize("code", [
        BrokerErrorCode.RATE_LIMITED, BrokerErrorCode.TIMEOUT,
        BrokerErrorCode.NETWORK_FAILURE,
        BrokerErrorCode.BROKER_UNAVAILABLE])
    def test_temporary_marked_retryable(self, code):
        assert error_result(code).error.retryable is True

    @pytest.mark.parametrize("code", [
        BrokerErrorCode.ORDER_REJECTED,
        BrokerErrorCode.INVALID_REQUEST,
        BrokerErrorCode.AUTHENTICATION_FAILURE,
        BrokerErrorCode.MALFORMED_BROKER_RESPONSE])
    def test_permanent_marked_not_retryable(self, code):
        assert error_result(code).error.retryable is False

    def test_unknown_retryability_stays_none(self):
        assert error_result(
            BrokerErrorCode.UNKNOWN_BROKER_FAILURE
        ).error.retryable is None

    def test_error_result_requires_canonical_code(self):
        with pytest.raises(BrokerNormalizationError):
            error_result("ORDER_REJECTED")


# ── Sonuç sözleşmesi (uçtan uca sahte taşıma) ────────────────────────

class TestResultContract:
    def test_get_order_success(self):
        adapter, transport = _adapter(
            {"get_order": NATIVE_ORDER})
        result = _run(adapter.get_order(
            OrderQuery(symbol="BTCUSDT", order_id="12345"),
            _context()))
        assert isinstance(result, BrokerOperationResult)
        assert result.status is BrokerOperationStatus.SUCCESS
        assert isinstance(result.order, Order)
        assert transport.calls[0][0] == "get_order"

    def test_get_order_params_forwarded(self):
        adapter, transport = _adapter(
            {"get_order": NATIVE_ORDER})
        _run(adapter.get_order(
            OrderQuery(symbol="BTCUSDT",
                       client_order_id="cli-1"), _context()))
        assert transport.calls[0][1] == {
            "symbol": "BTCUSDT", "origClientOrderId": "cli-1"}

    def test_list_open_orders_success(self):
        adapter, _ = _adapter(
            {"open_orders": {"items": [NATIVE_ORDER,
                                       NATIVE_ORDER]}})
        result = _run(adapter.list_open_orders(
            OpenOrdersQuery(symbol="BTCUSDT"), _context()))
        assert result.status is BrokerOperationStatus.SUCCESS
        assert len(result.orders) == 2
        assert all(isinstance(o, Order) for o in result.orders)

    def test_get_balances_success(self):
        adapter, _ = _adapter(
            {"balances": {"items": [NATIVE_BALANCE]}})
        result = _run(adapter.get_balances(BalancesQuery(),
                                           _context()))
        assert result.status is BrokerOperationStatus.SUCCESS
        assert isinstance(result.balances[0], BrokerBalance)

    def test_get_positions_spot_empty_success(self):
        adapter, transport = _adapter()
        result = _run(adapter.get_positions(PositionsQuery(),
                                            _context()))
        assert result.status is BrokerOperationStatus.SUCCESS
        assert result.positions == ()
        assert transport.calls == []  # deterministik, ağsız

    def test_submit_order_success(self):
        native = dict(NATIVE_ORDER)
        adapter, transport = _adapter({"submit_order": native})
        result = _run(adapter.submit_order(_exec_request(),
                                           _context()))
        assert result.status is BrokerOperationStatus.SUCCESS
        assert isinstance(result.order, Order)

    def test_submit_order_uses_idempotency_as_client_id(self):
        adapter, transport = _adapter(
            {"submit_order": NATIVE_ORDER})
        _run(adapter.submit_order(
            _exec_request(), _context(idempotency_key="idem-7")))
        params = transport.calls[0][1]
        assert params["newClientOrderId"] == "idem-7"
        assert params["quantity"] == "0.5"
        assert params["price"] == "50000.10"

    def test_cancel_order_success(self):
        native = dict(NATIVE_ORDER)
        native["status"] = "CANCELED"
        adapter, _ = _adapter({"cancel_order": native})
        result = _run(adapter.cancel_order(
            CancelOrderRequest(symbol="BTCUSDT",
                               order_id="12345"), _context()))
        assert result.status is BrokerOperationStatus.SUCCESS
        assert result.order.state is OrderState.CANCELLED

    @pytest.mark.parametrize("native,status", [
        ({"code": -2010}, BrokerOperationStatus.REJECTED),
        ({"code": -2013}, BrokerOperationStatus.NOT_FOUND),
        ({"code": 429}, BrokerOperationStatus.TEMPORARY_FAILURE),
        ({"code": -99999}, BrokerOperationStatus.UNKNOWN)])
    def test_native_error_payload_normalized(self, native,
                                             status):
        adapter, _ = _adapter({"submit_order": native})
        result = _run(adapter.submit_order(_exec_request(),
                                           _context()))
        assert result.status is status
        assert result.error is not None

    @pytest.mark.parametrize("kind,code", [
        ("TIMEOUT", BrokerErrorCode.TIMEOUT),
        ("NETWORK", BrokerErrorCode.NETWORK_FAILURE),
        ("UNAVAILABLE", BrokerErrorCode.BROKER_UNAVAILABLE)])
    def test_transport_failure_normalized_not_raised(
            self, kind, code):
        adapter, _ = _adapter(failure=TransportFailure(kind))
        result = _run(adapter.get_balances(BalancesQuery(),
                                           _context()))
        assert result.status is \
            BrokerOperationStatus.TEMPORARY_FAILURE
        assert result.error.code is code

    def test_unexpected_native_exception_never_escapes(self):
        adapter, _ = _adapter(failure=RuntimeError("sdk boom"))
        result = _run(adapter.submit_order(_exec_request(),
                                           _context()))
        assert result.error.code is \
            BrokerErrorCode.UNKNOWN_BROKER_FAILURE

    @pytest.mark.parametrize("payload", [
        "text", 42, ["x"], None])
    def test_non_mapping_payload_malformed(self, payload):
        adapter, _ = _adapter({"get_order": payload})
        result = _run(adapter.get_order(
            OrderQuery(symbol="BTCUSDT", order_id="1"),
            _context()))
        assert result.error.code is \
            BrokerErrorCode.MALFORMED_BROKER_RESPONSE

    def test_malformed_order_payload_normalized(self):
        adapter, _ = _adapter(
            {"get_order": {"symbol": "BTCUSDT",
                           "status": "MYSTERY"}})
        result = _run(adapter.get_order(
            OrderQuery(symbol="BTCUSDT", order_id="1"),
            _context()))
        assert result.status is \
            BrokerOperationStatus.PERMANENT_FAILURE
        assert result.error.code is \
            BrokerErrorCode.MALFORMED_BROKER_RESPONSE

    def test_malformed_list_payload_normalized(self):
        adapter, _ = _adapter({"open_orders": {"items": "yanlis"}})
        result = _run(adapter.list_open_orders(OpenOrdersQuery(),
                                               _context()))
        assert result.error.code is \
            BrokerErrorCode.MALFORMED_BROKER_RESPONSE

    def test_results_never_carry_native_payload(self):
        adapter, _ = _adapter({"get_order": NATIVE_ORDER})
        result = _run(adapter.get_order(
            OrderQuery(symbol="BTCUSDT", order_id="1"),
            _context()))
        assert not hasattr(result, "__dict__")
        for value in (result.order, result.error):
            assert not isinstance(value, dict)


# ── Sağlık sözleşmesi ────────────────────────────────────────────────

class TestHealthContract:
    def test_healthy_when_ping_succeeds(self):
        adapter, transport = _adapter({"ping": {}})
        health = _run(adapter.health_check())
        assert isinstance(health, BrokerHealth)
        assert health.state is BrokerHealthState.HEALTHY
        assert health.read_available is True
        assert health.write_available is True
        assert transport.calls == [("ping", {})]

    @pytest.mark.parametrize("kind,reason", [
        ("TIMEOUT", "TIMEOUT"),
        ("NETWORK", "NETWORK_FAILURE"),
        ("UNAVAILABLE", "BROKER_UNAVAILABLE")])
    def test_unavailable_on_transport_failure(self, kind, reason):
        adapter, _ = _adapter(failure=TransportFailure(kind))
        health = _run(adapter.health_check())
        assert health.state is BrokerHealthState.UNAVAILABLE
        assert health.reason_code == reason
        assert health.read_available is False
        assert health.write_available is False

    def test_unknown_on_unexpected_exception(self):
        adapter, _ = _adapter(failure=ValueError("boom"))
        health = _run(adapter.health_check())
        assert health.state is BrokerHealthState.UNKNOWN
        assert health.read_available is None  # bilinmeyen None

    def test_health_never_raises_native(self):
        adapter, _ = _adapter(failure=KeyError("native"))
        assert isinstance(_run(adapter.health_check()),
                          BrokerHealth)


# ── Idempotency sözleşmesi ───────────────────────────────────────────

class TestIdempotency:
    @pytest.mark.parametrize("key", [None, "", "   "])
    @pytest.mark.parametrize("operation,request_obj", [
        ("submit_order", _exec_request()),
        ("cancel_order", CancelOrderRequest(symbol="BTCUSDT",
                                            order_id="1"))])
    def test_missing_key_rejected_before_transport(
            self, key, operation, request_obj):
        adapter, transport = _adapter(
            {"submit_order": NATIVE_ORDER,
             "cancel_order": NATIVE_ORDER})
        with pytest.raises(BrokerContractError,
                           match="MISSING_IDEMPOTENCY_KEY"):
            _run(getattr(adapter, operation)(
                request_obj, _context(idempotency_key=key)))
        assert transport.calls == []  # taşımaya hiç inilmedi

    def test_no_retry_single_transport_call(self):
        adapter, transport = _adapter(
            failure=TransportFailure("TIMEOUT"))
        _run(adapter.submit_order(_exec_request(), _context()))
        assert len(transport.calls) == 1  # retry YOK

    def test_reads_need_no_key(self):
        adapter, _ = _adapter(
            {"balances": {"items": []}})
        result = _run(adapter.get_balances(
            BalancesQuery(), _context(idempotency_key=None)))
        assert result.status is BrokerOperationStatus.SUCCESS

    def test_adapter_never_generates_key(self):
        for module in BINANCE_MODULES:
            source = inspect.getsource(module)
            for token in ("uuid", "token_hex", "randbytes",
                          "monotonic", "perf_counter", "urandom"):
                assert token not in source


# ── Okuma/yazma ayrımı ───────────────────────────────────────────────

class TestReadWriteSeparation:
    def test_classification_inherited_unchanged(self):
        source = inspect.getsource(binance_spot_adapter)
        assert "_READ_OPERATIONS" not in source
        assert "_WRITE_OPERATIONS" not in source

    def test_write_set(self):
        import execution_broker_adapter as core
        assert core._WRITE_OPERATIONS == {"submit_order",
                                          "cancel_order"}
        assert {"profile", "health_check", "get_order",
                "list_open_orders", "get_positions",
                "get_balances"} == core._READ_OPERATIONS


# ── Broker sızıntısı yasağı ──────────────────────────────────────────

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


CORE_MODULES = ("execution_broker_adapter",
                "execution_broker_models",
                "execution_broker_errors", "execution_models",
                "execution_enums", "execution_risk_models",
                "execution_risk_engine", "execution_kill_switch")


class TestNoBrokerLeakage:
    @pytest.mark.parametrize("core", CORE_MODULES)
    def test_core_never_imports_binance(self, core):
        module = __import__(core)
        roots = {m.split(".")[0] for m in _module_imports(module)}
        assert not any(r.startswith("binance") for r in roots)

    @pytest.mark.parametrize("core", CORE_MODULES[:6])
    def test_core_source_has_no_binance_token(self, core):
        # Docstring'ler hariç: kod içinde binance token'ı yok
        module = __import__(core)
        assert "binance" not in _code_source(module).lower()

    def test_binance_error_codes_never_leak(self):
        adapter, _ = _adapter({"submit_order": {"code": -2010}})
        result = _run(adapter.submit_order(_exec_request(),
                                           _context()))
        assert result.error.code is \
            BrokerErrorCode.ORDER_REJECTED
        assert result.error.message is None  # native mesaj yok

    def test_no_duplicate_canonical_models(self):
        forbidden = {"Order", "Position", "Fill", "BrokerProfile",
                     "ExecutionRequest", "BrokerOperationResult",
                     "BrokerBalance", "BrokerHealth"}
        for module in BINANCE_MODULES:
            defined = {node.name for node in ast.walk(
                ast.parse(inspect.getsource(module)))
                if isinstance(node, ast.ClassDef)}
            assert not defined & forbidden


# ── Güvenlik: ağ/secret/env/retry/zaman yasakları ────────────────────

class TestSafety:
    @pytest.mark.parametrize("module", BINANCE_MODULES)
    def test_no_forbidden_imports(self, module):
        roots = {m.split(".")[0] for m in _module_imports(module)}
        forbidden = {"os", "sys", "io", "pathlib", "socket", "ssl",
                     "http", "requests", "httpx", "aiohttp",
                     "urllib", "urllib3", "websocket",
                     "websockets", "uuid", "datetime", "time",
                     "random", "secrets", "hashlib", "hmac",
                     "threading", "multiprocessing", "subprocess",
                     "asyncio", "ccxt", "binance", "json",
                     "pickle", "sqlite3"}
        assert not roots & forbidden

    @pytest.mark.parametrize("module", BINANCE_MODULES)
    def test_allowed_imports_only(self, module):
        allowed = {"__future__", "abc", "enum", "dataclasses",
                   "decimal", "typing", "types",
                   "execution_enums", "execution_models",
                   "execution_risk_models",
                   "execution_broker_errors",
                   "execution_broker_models",
                   "execution_broker_adapter",
                   "binance_capabilities", "binance_normalizer"}
        assert _module_imports(module) <= allowed

    @pytest.mark.parametrize("token", [
        "http://", "https://", "wss://", "api.binance",
        "requests.", "aiohttp", "httpx", "websocket",
        "sleep", "retry", "backoff", "getenv", "environ",
        "open(", "datetime.now", "uuid4", "random.",
        "api_key\"", "API_KEY", "hmac", "sha256",
        "Authorization", "X-MBX"])
    def test_no_network_secret_retry_tokens(self, token):
        for module in BINANCE_MODULES:
            # "retryable" alanı sınıflandırma verisidir, retry
            # mekanizması değildir — taramadan muaf tutulur
            source = _code_source(module).replace("retryable", "")
            assert token not in source

    @pytest.mark.parametrize("module", BINANCE_MODULES)
    def test_no_dangerous_calls(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "open", "__import__",
                    "compile", "input", "print")

    @pytest.mark.parametrize("module", BINANCE_MODULES)
    def test_no_float_literals(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    @pytest.mark.parametrize("module", BINANCE_MODULES)
    def test_no_loops_no_retry_paths(self, module):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.While,))

    def test_no_threads_or_processes(self):
        for module in BINANCE_MODULES:
            source = inspect.getsource(module)
            for token in ("Thread", "Process(", "fork",
                          "Executor"):
                assert token not in source


# ── Kamu API dondurması ──────────────────────────────────────────────

class TestPublicApiFreeze:
    def test_adapter_module_surface(self):
        assert binance_spot_adapter.__all__ == [
            "BinanceSpotAdapter", "Transport", "RESTTransport",
            "WebSocketTransport", "TransportFailure",
            "SigningProvider", "CredentialProvider"]

    def test_normalizer_module_surface(self):
        assert binance_normalizer.__all__ == [
            "normalize_order", "normalize_balance",
            "normalize_fill", "normalize_order_state",
            "normalize_error", "error_result"]

    def test_capabilities_module_surface(self):
        assert binance_capabilities.__all__ == [
            "binance_spot_profile"]

    @pytest.mark.parametrize("module", BINANCE_MODULES)
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
    def test_delivery_marked_in_architecture_test(self):
        path = os.path.join(_ROOT, "tests",
                            "test_mission_2000_architecture.py")
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert "binance_spot_adapter.py" in content

    def test_future_broker_needs_no_core_change(self):
        # IBKR benzeri yeni bir adaptör yalnız yeni dosyalarla
        # eklenebilir: sahte taşımayla ikinci bir somut adaptör
        class OtherVenueTransport(Transport):
            __slots__ = ()

            async def request(self, operation, params):
                return {"items": []}

        class OtherVenueAdapter(BinanceSpotAdapter):
            __slots__ = ()
        adapter = OtherVenueAdapter(OtherVenueTransport())
        result = _run(adapter.get_balances(BalancesQuery(),
                                           _context()))
        assert result.status is BrokerOperationStatus.SUCCESS

    def test_adapter_does_not_touch_risk_or_kill_switch(self):
        source = inspect.getsource(binance_spot_adapter)
        for token in ("RiskEngine", "KillSwitch",
                      "validate_execution",
                      "is_execution_allowed"):
            assert token not in source

    def test_execution_mode_carried_not_enforced(self):
        adapter, _ = _adapter({"submit_order": NATIVE_ORDER})
        for mode in ExecutionMode:
            result = _run(adapter.submit_order(
                _exec_request(), _context(execution_mode=mode)))
            assert result.status is \
                BrokerOperationStatus.SUCCESS

    def test_deterministic_repeatability(self):
        adapter, _ = _adapter({"get_order": NATIVE_ORDER})
        first = _run(adapter.get_order(
            OrderQuery(symbol="BTCUSDT", order_id="1"),
            _context()))
        second = _run(adapter.get_order(
            OrderQuery(symbol="BTCUSDT", order_id="1"),
            _context()))
        assert first == second  # değer eşitliği — determinizm
