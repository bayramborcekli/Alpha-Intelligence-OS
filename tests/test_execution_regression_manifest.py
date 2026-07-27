"""Mission 2000 — Agent 09: Regresyon manifestosu + determinizm.

Değişmez regresyon manifestosu (Mission 2100 tabanı) ve Yürütme
Çekirdeği'nin determinizm sertifikası: aynı girdi + aynı
bağımlılık çıktıları → aynı sonuç, aynı iz, aynı broker çağrı
sayısı. Gizli durum yoktur.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from decimal import Decimal
from types import MappingProxyType

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import asyncio

import execution_regression_manifest as manifest
from execution_api import ExecutionApi
from execution_api_models import (
    ExecutionApiRequest, ExecutionApiStatus)
from execution_broker_adapter import BrokerAdapter
from execution_broker_models import (
    BrokerOperationResult, BrokerOperationStatus, ExecutionMode)
from execution_enums import (
    OrderSide, OrderState, OrderType, TimeInForce)
from execution_kill_switch import KillSwitch
from execution_models import ExecutionRequest, Order
from execution_risk_engine import RiskEngine
from execution_risk_models import (
    AssetType, BrokerProfile, CapitalState, Instrument,
    Portfolio, RiskLimits)
from execution_service import (
    BrokerAdapterResolver, ExecutionService)
from execution_service_models import ExecutionServiceRequest

D = Decimal


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Manifesto ────────────────────────────────────────────────────────

class TestRegressionManifest:
    def test_mission_and_agent(self):
        assert manifest.MISSION == "2000"
        assert manifest.AGENT == "09"

    def test_baseline_commit_from_agent_08(self):
        assert manifest.BASELINE_COMMIT == "01aa429"

    def test_baseline_regression_from_agent_08(self):
        assert manifest.BASELINE_REGRESSION == 3704

    def test_architecture_status(self):
        assert manifest.ARCHITECTURE_STATUS == "FROZEN"

    def test_security_status(self):
        assert manifest.SECURITY_STATUS == "CERTIFIED"

    def test_freeze_status(self):
        assert manifest.FREEZE_STATUS == "FROZEN"

    def test_statuses_consistent_with_freeze_modules(self):
        import execution_architecture_freeze as freeze
        import execution_security_certification as cert
        assert manifest.FREEZE_STATUS == freeze.FREEZE_STATUS
        assert manifest.SECURITY_STATUS == cert.SECURITY_STATUS

    def test_agent_chain_frozen(self):
        assert dict(manifest.AGENT_CHAIN) == {
            "05": ("74c157e", 2982),
            "06": ("98a9c20", 3219),
            "07": ("f1ab9a2", 3471),
            "08": ("01aa429", 3704)}

    def test_chain_regressions_monotonic(self):
        counts = [entry[1]
                  for entry in manifest.AGENT_CHAIN.values()]
        assert counts == sorted(counts)

    def test_manifest_mapping_immutable(self):
        assert isinstance(manifest.REGRESSION_MANIFEST,
                          MappingProxyType)
        assert isinstance(manifest.AGENT_CHAIN, MappingProxyType)
        with pytest.raises(TypeError):
            manifest.REGRESSION_MANIFEST["commit"] = "sahte"
        with pytest.raises(TypeError):
            manifest.AGENT_CHAIN["10"] = ("x", 0)

    @pytest.mark.parametrize("key,value", [
        ("mission", "2000"), ("agent", "09"),
        ("commit", "01aa429"), ("regression", 3704),
        ("architecture_status", "FROZEN"),
        ("security_status", "CERTIFIED"),
        ("freeze_status", "FROZEN")])
    def test_manifest_entries(self, key, value):
        assert manifest.REGRESSION_MANIFEST[key] == value

    def test_manifest_keys_closed(self):
        assert set(manifest.REGRESSION_MANIFEST.keys()) == {
            "mission", "agent", "commit", "regression",
            "architecture_status", "security_status",
            "freeze_status"}

    def test_manifest_surface_frozen(self):
        assert manifest.__all__ == [
            "MISSION", "AGENT", "BASELINE_COMMIT",
            "BASELINE_REGRESSION", "ARCHITECTURE_STATUS",
            "SECURITY_STATUS", "FREEZE_STATUS", "AGENT_CHAIN",
            "REGRESSION_MANIFEST"]

    def test_no_business_logic_in_manifest_modules(self):
        import execution_architecture_freeze as freeze
        import execution_security_certification as cert
        for module in (manifest, freeze, cert):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                assert not isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef, ast.For, ast.While,
                           ast.If, ast.Try)), \
                    f"{module.__name__}: bildirimsel olmalı"


# ── Determinizm fikstürleri ──────────────────────────────────────────

class CountingAdapter(BrokerAdapter):
    """Tüm operasyon çağrılarını sayan deterministik adaptör."""

    __slots__ = ("invocations",)

    def __init__(self):
        object.__setattr__(self, "invocations", [])

    async def _do_profile(self):
        self.invocations.append("profile")
        raise AssertionError("cekirdek cagirmamali")

    async def _do_health_check(self):
        self.invocations.append("health_check")
        raise AssertionError("cekirdek cagirmamali")

    async def _do_get_order(self, query, context):
        self.invocations.append("get_order")
        raise AssertionError("cekirdek cagirmamali")

    async def _do_list_open_orders(self, query, context):
        self.invocations.append("list_open_orders")
        raise AssertionError("cekirdek cagirmamali")

    async def _do_get_positions(self, query, context):
        self.invocations.append("get_positions")
        raise AssertionError("cekirdek cagirmamali")

    async def _do_get_balances(self, query, context):
        self.invocations.append("get_balances")
        raise AssertionError("cekirdek cagirmamali")

    async def _do_submit_order(self, request, context):
        self.invocations.append("submit_order")
        return BrokerOperationResult(
            status=BrokerOperationStatus.SUCCESS,
            order=Order(symbol=request.symbol, side=request.side,
                        order_type=request.order_type,
                        quantity=request.quantity,
                        time_in_force=request.time_in_force,
                        state=OrderState.SUBMITTED))

    async def _do_cancel_order(self, request, context):
        self.invocations.append("cancel_order")
        raise AssertionError("cekirdek cagirmamali")


class StaticResolver(BrokerAdapterResolver):
    __slots__ = ("_adapter",)

    def __init__(self, adapter):
        self._adapter = adapter

    def resolve(self, broker_id):
        if broker_id != "paper-1":
            raise KeyError(broker_id)
        return self._adapter

    def profile(self, broker_id):
        if broker_id != "paper-1":
            raise KeyError(broker_id)
        return BrokerProfile(supports_market_orders=True,
                             supports_fractional=True,
                             supports_cancel=True)


def _exec_request():
    return ExecutionRequest(symbol="BTCUSDT", side=OrderSide.BUY,
                            order_type=OrderType.LIMIT,
                            quantity=D("0.5"),
                            time_in_force=TimeInForce.GTC,
                            price=D("100"))


def _portfolio():
    return Portfolio(capital=CapitalState(
        total_capital=D("10000"), available_capital=D("10000")))


def _instrument():
    return Instrument(symbol="BTCUSDT",
                      asset_type=AssetType.CRYPTO,
                      currency="BTC", quote_currency="USDT")


def _stack(enabled=True):
    adapter = CountingAdapter()
    switch = KillSwitch()
    if enabled:
        switch.enable()
    service = ExecutionService(RiskEngine(RiskLimits()), switch,
                               StaticResolver(adapter))
    return ExecutionApi(service), service, adapter


def _api_request(**overrides):
    base = dict(execution_request=_exec_request(),
                portfolio=_portfolio(), instrument=_instrument(),
                broker_id="paper-1", idempotency_key="idem-1",
                execution_mode=ExecutionMode.PAPER)
    base.update(overrides)
    return ExecutionApiRequest(**base)


def _service_request(**overrides):
    base = dict(execution_request=_exec_request(),
                portfolio=_portfolio(), instrument=_instrument(),
                broker_id="paper-1", idempotency_key="idem-1")
    base.update(overrides)
    return ExecutionServiceRequest(**base)


# ── Determinizm sertifikası ──────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_api_response(self):
        responses = []
        for _ in range(3):
            api, _, _ = _stack()
            responses.append(_run(api.execute(_api_request())))
        assert responses[0] == responses[1] == responses[2]

    def test_same_input_same_trace(self):
        traces = []
        for _ in range(3):
            api, _, _ = _stack()
            response = _run(api.execute(_api_request()))
            traces.append(response.service_result.trace)
        assert traces[0] == traces[1] == traces[2]

    def test_same_input_same_broker_invocation_count(self):
        for _ in range(3):
            api, _, adapter = _stack()
            _run(api.execute(_api_request()))
            assert adapter.invocations == ["submit_order"]

    def test_denied_input_zero_invocations_every_time(self):
        for _ in range(3):
            api, _, adapter = _stack(enabled=False)
            response = _run(api.execute(_api_request()))
            assert response.status is \
                ExecutionApiStatus.BLOCKED_BY_KILL_SWITCH
            assert adapter.invocations == []

    def test_no_hidden_state_between_calls(self):
        # Aynı yığın üzerinde tekrar tekrar çalıştırma: sonuç
        # değişmez, çağrı sayısı doğrusal artar (birikinti yok)
        api, _, adapter = _stack()
        first = _run(api.execute(_api_request()))
        second = _run(api.execute(_api_request()))
        third = _run(api.execute(_api_request()))
        assert first == second == third
        assert adapter.invocations == ["submit_order"] * 3

    def test_validation_failure_deterministic(self):
        api, _, adapter = _stack()
        results = [_run(api.execute(
            _api_request(idempotency_key=None)))
            for _ in range(3)]
        assert results[0] == results[1] == results[2]
        assert adapter.invocations == []

    def test_unknown_broker_deterministic(self):
        api, _, adapter = _stack()
        results = [_run(api.execute(
            _api_request(broker_id="bilinmeyen")))
            for _ in range(3)]
        assert results[0] == results[1] == results[2]
        assert results[0].status is \
            ExecutionApiStatus.NOT_SUBMITTED
        assert adapter.invocations == []

    def test_service_layer_deterministic(self):
        _, service, adapter = _stack()
        first = _run(service.execute(_service_request()))
        second = _run(service.execute(_service_request()))
        assert first == second
        assert first.trace == second.trace
        assert adapter.invocations == ["submit_order"] * 2

    def test_risk_engine_deterministic(self):
        engine = RiskEngine(RiskLimits())
        profile = BrokerProfile(supports_market_orders=True,
                                supports_fractional=True)
        decisions = [engine.validate(_exec_request(), _portfolio(),
                                     _instrument(), profile)
                     for _ in range(3)]
        assert decisions[0] == decisions[1] == decisions[2]

    def test_kill_switch_state_deterministic(self):
        switch = KillSwitch()
        assert switch.is_execution_allowed() is False
        switch.enable()
        assert all(switch.is_execution_allowed()
                   for _ in range(5))

    def test_stateless_service_slots_certified(self):
        assert ExecutionService.__slots__ == (
            "_risk_engine", "_kill_switch", "_resolver", "_gate")
        assert ExecutionApi.__slots__ == ("_service", "_mapper")

    @pytest.mark.parametrize("mode", list(ExecutionMode))
    def test_mode_does_not_change_determinism(self, mode):
        api, _, adapter = _stack()
        first = _run(api.execute(
            _api_request(execution_mode=mode)))
        second = _run(api.execute(
            _api_request(execution_mode=mode)))
        assert first == second
        assert adapter.invocations == ["submit_order"] * 2
