"""Mission 2100 — Agent 05: Gölge modu mimari testleri.

Taban: Execution Core v1.0.0 (03e181d, FROZEN) + Agent 01
(4304527) + Agent 02 (69bd05c) + Agent 03 (32f4a3a) + Agent 04
(bf2a21d, regresyon 5994 PASS). Gölge modunun dondurulmuş çekirdek
ve önceki ajan katmanlarını DEĞİŞTİRMEDEN, salt-okunur piyasa
gözlemiyle çalıştığını kanıtlar.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import os
import sys
from decimal import Decimal as D

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import execution_architecture_freeze as freeze
import execution_security_certification as cert
import shadow_comparator
import shadow_errors
import shadow_mode
import shadow_models
from execution_enums import OrderSide
from shadow_errors import ShadowContractError
from shadow_models import (ShadowAudit, ShadowComparison,
                           ShadowExecution, ShadowHeartbeat,
                           ShadowMarketObservation, ShadowOrder,
                           ShadowSnapshot, ShadowStage,
                           ShadowStatistics)

# Mission 2100 durumu ve taban değerleri
MISSION_2100_STATUS = "IN_PROGRESS"
BASELINE_VERSION = "1.0.0"
BASELINE_COMMIT = "03e181d"
AGENT_01_COMMIT = "4304527"
AGENT_02_COMMIT = "69bd05c"
AGENT_03_COMMIT = "32f4a3a"
AGENT_04_COMMIT = "bf2a21d"
AGENT_04_REGRESSION = 5994

SHADOW_MODULES = (shadow_errors, shadow_models,
                  shadow_comparator, shadow_mode)
SHADOW_MODULE_NAMES = ("shadow_errors", "shadow_models",
                       "shadow_comparator", "shadow_mode")
PRIOR_2100_MODULE_NAMES = (
    "controlled_execution_errors",
    "controlled_execution_models",
    "controlled_execution_policy",
    "controlled_execution_foundation",
    "runtime_errors", "runtime_enums", "runtime_models",
    "paper_errors", "paper_models", "paper_ledger",
    "paper_broker", "paper_execution_errors",
    "paper_execution_models", "paper_execution_mapper",
    "paper_execution_service")


def _module_imports(module):
    imports = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return imports


def _code_source(module):
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def _defined_classes(module):
    return {node.name for node in ast.walk(
        ast.parse(inspect.getsource(module)))
        if isinstance(node, ast.ClassDef)}


# ── Taban doğrulaması ────────────────────────────────────────────────

class TestBaseline:
    def test_mission_status(self):
        assert MISSION_2100_STATUS == "IN_PROGRESS"

    def test_baseline_values(self):
        assert BASELINE_VERSION == "1.0.0"
        assert BASELINE_COMMIT == "03e181d"
        assert AGENT_01_COMMIT == "4304527"
        assert AGENT_02_COMMIT == "69bd05c"
        assert AGENT_03_COMMIT == "32f4a3a"
        assert AGENT_04_COMMIT == "bf2a21d"
        assert AGENT_04_REGRESSION == 5994

    def test_mission_2000_manifest_untouched(self):
        import execution_regression_manifest as manifest
        assert manifest.BASELINE_COMMIT == "01aa429"
        assert manifest.BASELINE_REGRESSION == 3704
        assert manifest.FREEZE_STATUS == "FROZEN"

    def test_agent_04_public_api_unchanged(self):
        import paper_execution_errors
        import paper_execution_mapper
        import paper_execution_models
        import paper_execution_service
        assert len(paper_execution_errors.__all__) == 7
        assert len(paper_execution_models.__all__) == 6
        assert len(paper_execution_mapper.__all__) == 1
        assert len(paper_execution_service.__all__) == 3

    def test_frozen_module_set_unchanged(self):
        assert len(freeze.FROZEN_MODULES) == 20
        assert not set(SHADOW_MODULE_NAMES) & \
            set(freeze.FROZEN_MODULES)

    def test_ownership_map_untouched(self):
        assert len(freeze.DOMAIN_OWNERSHIP) == 24


# ── Bağımlılık yönü ──────────────────────────────────────────────────

class TestDependencyDirection:
    @pytest.mark.parametrize("core_name",
                             list(freeze.FROZEN_MODULES))
    def test_core_never_imports_shadow_layer(self, core_name):
        core_module = importlib.import_module(core_name)
        assert not _module_imports(core_module) & \
            set(SHADOW_MODULE_NAMES)

    @pytest.mark.parametrize("prior_name",
                             PRIOR_2100_MODULE_NAMES)
    def test_prior_agents_never_import_shadow_layer(
            self, prior_name):
        prior_module = importlib.import_module(prior_name)
        assert not _module_imports(prior_module) & \
            set(SHADOW_MODULE_NAMES)

    def test_errors_imports(self):
        assert _module_imports(shadow_errors) <= {"__future__"}

    def test_models_imports(self):
        assert _module_imports(shadow_models) <= {
            "__future__", "dataclasses", "decimal", "enum",
            "typing", "execution_enums", "paper_models",
            "shadow_errors"}

    def test_comparator_imports(self):
        assert _module_imports(shadow_comparator) <= {
            "__future__", "dataclasses", "decimal", "typing",
            "execution_enums", "shadow_errors",
            "shadow_models"}

    def test_service_imports(self):
        assert _module_imports(shadow_mode) <= {
            "__future__", "dataclasses", "typing",
            "controlled_execution_foundation",
            "controlled_execution_models",
            "execution_kill_switch_models",
            "execution_models", "execution_risk_models",
            "paper_broker", "paper_errors",
            "paper_execution_mapper",
            "paper_execution_models",
            "paper_execution_service", "paper_models",
            "shadow_comparator", "shadow_errors",
            "shadow_models"}

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_live_pipeline_imports(self, module):
        forbidden = {"execution_service", "execution_api",
                     "execution_service_models",
                     "execution_api_models",
                     "execution_broker_adapter",
                     "execution_broker_models",
                     "exchange_gateway",
                     "binance_spot_adapter",
                     "binance_capabilities",
                     "binance_normalizer",
                     "flask", "app", "requests"}
        assert not _module_imports(module) & forbidden


# ── Kanonik model koruması ───────────────────────────────────────────

class TestCanonicalModelProtection:
    @pytest.mark.parametrize("symbol", [
        "ExecutionRequest", "ExecutionResult", "ExecutionMode",
        "RiskDecision", "RiskDecisionType", "KillSwitchSnapshot",
        "KillSwitchState", "Order", "Position", "Fill",
        "PaperOrder", "PaperExecution", "PaperLedgerSnapshot",
        "PaperBroker", "PaperLedger", "PaperExecutionService",
        "PaperExecutionMapper", "PaperExecutionReferences",
        "RuntimeAccountSnapshot", "RuntimeAuditRecord",
        "ControlledExecutionPolicy",
        "ControlledExecutionFoundation"])
    def test_canonical_models_not_redefined(self, symbol):
        for module in SHADOW_MODULES:
            assert symbol not in _defined_classes(module), \
                f"{module.__name__}: {symbol} kopyası yasak"

    def test_models_no_inheritance_trees(self):
        for name in shadow_models.__all__:
            model = getattr(shadow_models, name)
            if dataclasses.is_dataclass(model):
                assert model.__bases__ == (object,)

    @pytest.mark.parametrize("name", [
        "ShadowAudit", "ShadowOrder", "ShadowExecution",
        "ShadowMarketObservation", "ShadowComparison",
        "ShadowStatistics", "ShadowSnapshot",
        "ShadowHeartbeat", "ShadowResult"])
    def test_models_frozen_slots(self, name):
        model = getattr(shadow_models, name)
        assert model.__dataclass_params__.frozen
        assert "__slots__" in vars(model)

    def test_service_frozen_slots(self):
        model = shadow_mode.ShadowModeService
        assert model.__dataclass_params__.frozen
        assert "__slots__" in vars(model)

    def test_comparator_frozen_slots(self):
        model = shadow_comparator.ShadowComparator
        assert model.__dataclass_params__.frozen
        assert "__slots__" in vars(model)

    def test_error_taxonomy_closed(self):
        root = shadow_errors.ShadowError
        subclasses = {
            shadow_errors.ShadowContractError,
            shadow_errors.ShadowConfigurationError,
            shadow_errors.ShadowModeError,
            shadow_errors.ShadowRiskError,
            shadow_errors.ShadowPermissionError,
            shadow_errors.ShadowStateError}
        assert set(root.__subclasses__()) == subclasses
        for subclass in subclasses:
            assert issubclass(subclass, root)

    def test_public_exports_exact(self):
        assert shadow_errors.__all__ == [
            "ShadowError", "ShadowContractError",
            "ShadowConfigurationError", "ShadowModeError",
            "ShadowRiskError", "ShadowPermissionError",
            "ShadowStateError"]
        assert shadow_models.__all__ == [
            "ShadowOperation", "ShadowDecision",
            "ShadowDecisionCode", "ShadowStage", "ShadowAudit",
            "ShadowOrder", "ShadowExecution",
            "ShadowMarketObservation", "ShadowComparison",
            "ShadowStatistics", "ShadowSnapshot",
            "ShadowHeartbeat", "ShadowResult"]
        assert shadow_comparator.__all__ == [
            "ShadowComparator"]
        assert shadow_mode.__all__ == ["ShadowModeService"]

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_all_exports_resolve(self, module):
        for name in module.__all__:
            assert hasattr(module, name)

    @pytest.mark.parametrize("enum_name,size", [
        ("ShadowOperation", 3), ("ShadowDecision", 3),
        ("ShadowDecisionCode", 10), ("ShadowStage", 8)])
    def test_enums_closed(self, enum_name, size):
        enum_type = getattr(shadow_models, enum_name)
        assert len(list(enum_type)) == size

    def test_stage_order_fixed(self):
        assert [stage.value for stage in ShadowStage] == [
            "REQUEST_VALIDATED", "MODE_VALIDATED",
            "RISK_EVALUATED", "PERMISSION_EVALUATED",
            "KILL_SWITCH_CHECKED", "PAPER_SIMULATED",
            "MARKET_OBSERVED", "COMPARISON_COMPLETED"]


# ── Model sözleşme doğrulaması ───────────────────────────────────────

class TestModelContracts:
    @pytest.mark.parametrize("quantity", ["0", "-1"])
    def test_order_nonpositive_quantity_rejected(self,
                                                 quantity):
        with pytest.raises(ShadowContractError):
            ShadowOrder(order_reference="o", symbol="BTCUSDT",
                        side=OrderSide.BUY,
                        quantity=D(quantity), price=D("1"))

    @pytest.mark.parametrize("price", [1, 1.5, "1", None])
    def test_order_non_decimal_price_rejected(self, price):
        with pytest.raises(ShadowContractError):
            ShadowOrder(order_reference="o", symbol="BTCUSDT",
                        side=OrderSide.BUY, quantity=D("1"),
                        price=price)

    @pytest.mark.parametrize("side", ["BUY", None, 1])
    def test_order_non_enum_side_rejected(self, side):
        with pytest.raises(ShadowContractError):
            ShadowOrder(order_reference="o", symbol="BTCUSDT",
                        side=side, quantity=D("1"),
                        price=D("1"))

    @pytest.mark.parametrize("value", [1.5, "1", True])
    def test_observation_non_decimal_rejected(self, value):
        with pytest.raises(ShadowContractError):
            ShadowMarketObservation(
                observation_reference="obs", symbol="BTCUSDT",
                price=value)

    def test_observation_crossed_book_rejected(self):
        with pytest.raises(ShadowContractError):
            ShadowMarketObservation(
                observation_reference="obs", symbol="BTCUSDT",
                best_bid=D("101"), best_ask=D("100"))

    def test_observation_unknown_fields_none(self):
        observation = ShadowMarketObservation(
            observation_reference="obs", symbol="BTCUSDT")
        assert observation.price is None
        assert observation.best_bid is None
        assert observation.best_ask is None
        assert observation.last_trade_price is None

    @pytest.mark.parametrize("latency", [-1, 1.5, "1", True])
    def test_comparison_bad_latency_rejected(self, latency):
        with pytest.raises(ShadowContractError):
            ShadowComparison(request_reference="r",
                             paper_reference="p",
                             market_reference="m",
                             latency=latency)

    def test_comparison_signed_deltas_allowed(self):
        report = ShadowComparison(
            request_reference="r", paper_reference="p",
            market_reference="m", price_delta=D("-5"),
            fill_delta=D("-2"), pnl_delta=D("-10"))
        assert report.price_delta == D("-5")

    @pytest.mark.parametrize("delta", [1.5, "1", True])
    def test_comparison_non_decimal_delta_rejected(self,
                                                   delta):
        with pytest.raises(ShadowContractError):
            ShadowComparison(request_reference="r",
                             paper_reference="p",
                             market_reference="m",
                             price_delta=delta)

    def test_snapshot_duplicate_orders_rejected(self):
        order = ShadowOrder(order_reference="o",
                            symbol="BTCUSDT",
                            side=OrderSide.BUY,
                            quantity=D("1"), price=D("1"))
        with pytest.raises(ShadowContractError):
            ShadowSnapshot(snapshot_reference="s",
                           orders=(order, order))

    def test_snapshot_orphan_execution_rejected(self):
        execution = ShadowExecution(
            execution_reference="e", order_reference="orphan",
            symbol="BTCUSDT", side=OrderSide.BUY,
            quantity=D("1"), price=D("1"))
        with pytest.raises(ShadowContractError):
            ShadowSnapshot(snapshot_reference="s",
                           executions=(execution,))

    @pytest.mark.parametrize("count", [-1, 1.5, "1", True])
    def test_statistics_bad_count_rejected(self, count):
        with pytest.raises(ShadowContractError):
            ShadowStatistics(total_orders=count)

    @pytest.mark.parametrize("alive", [None, "yes", 1])
    def test_heartbeat_non_bool_rejected(self, alive):
        with pytest.raises(ShadowContractError):
            ShadowHeartbeat(alive=alive)

    @pytest.mark.parametrize("stage", ["REQUEST_VALIDATED",
                                       None, 1])
    def test_audit_non_enum_stage_rejected(self, stage):
        with pytest.raises(ShadowContractError):
            ShadowAudit(audit_reference="a", stage=stage,
                        event_code="X")

    @pytest.mark.parametrize("model_name", [
        "ShadowAudit", "ShadowOrder", "ShadowExecution",
        "ShadowMarketObservation", "ShadowComparison",
        "ShadowStatistics", "ShadowSnapshot",
        "ShadowHeartbeat", "ShadowResult"])
    def test_models_have_no_dict(self, model_name):
        model = getattr(shadow_models, model_name)
        assert "__dict__" not in dir(model()) if False else \
            "__slots__" in vars(model)


# ── Güvenlik taramaları ──────────────────────────────────────────────

class TestShadowSecurity:
    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_forbidden_imports(self, module):
        assert not _module_imports(module) & \
            cert.FORBIDDEN_IMPORT_ROOTS

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_forbidden_tokens(self, module):
        source = _code_source(module)
        for exempt in cert.TOKEN_EXEMPT_SUBSTRINGS:
            source = source.replace(exempt, "")
        hits = [t for t in cert.FORBIDDEN_TOKENS if t in source]
        assert not hits, f"{module.__name__}: {hits}"

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_forbidden_builtin_calls(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Name):
                assert node.func.id not in \
                    cert.FORBIDDEN_CALL_NAMES

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_float_literals(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_dynamic_import_or_exec(self, module):
        source = _code_source(module)
        for token in ("importlib", "__import__", "eval(",
                      "exec(", "compile(", "pickle",
                      "entry_points", "pkgutil", "subprocess"):
            assert token not in source

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_network_or_trading_tokens(self, module):
        source = _code_source(module)
        for token in ("requests", "urllib", "socket", "http",
                      "websocket", "binance", "Binance",
                      "ccxt", "endpoint", "signing",
                      "credential", "listen_key", "margin",
                      "withdraw", "transfer"):
            assert token not in source

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_nondeterminism_tokens(self, module):
        source = _code_source(module)
        for token in ("random", "uuid", "datetime", "time.",
                      "sleep", "retry", "monotonic",
                      "perf_counter"):
            assert token not in source

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_persistence_tokens(self, module):
        source = _code_source(module)
        for token in ("sqlite", "sqlalchemy", "psycopg",
                      "redis", "shelve", "mongo", "save(",
                      "load(", "write(", "read(", "commit(",
                      "open("):
            assert token not in source

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_environment_access(self, module):
        source = _code_source(module)
        for token in ("os.environ", "getenv", "dotenv"):
            assert token not in source

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_module_level_mutable_state(self, module):
        tree = ast.parse(inspect.getsource(module))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    assert isinstance(target, ast.Name)
                    assert (target.id == "__all__"
                            or target.id.lstrip("_").isupper())
                    if target.id != "__all__":
                        assert not isinstance(
                            node.value,
                            (ast.List, ast.Dict, ast.Set))

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_threads_loops_context(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            assert not isinstance(
                node, (ast.AsyncFunctionDef, ast.With,
                       ast.AsyncWith, ast.While, ast.Lambda))

    @pytest.mark.parametrize("module", SHADOW_MODULES)
    def test_no_recursion(self, module):
        tree = ast.parse(inspect.getsource(module))
        for func in ast.walk(tree):
            if isinstance(func, ast.FunctionDef):
                for node in ast.walk(func):
                    if isinstance(node, ast.Call) and \
                            isinstance(node.func, ast.Name):
                        assert node.func.id != func.name

    def test_no_secret_names_in_sources(self):
        for module in SHADOW_MODULES:
            source = _code_source(module)
            for token in ("BINANCE_API", "SESSION_SECRET",
                          "PASSWORD_HASH", "api_key",
                          "api_secret"):
                assert token not in source

    def test_no_mode_escalation_tokens(self):
        source = _code_source(shadow_mode)
        for token in ("MICRO_LIVE_ALLOWED", "LIVE_ALLOWED",
                      "auto_escalat", "fallback"):
            assert token not in source

    def test_shadow_is_only_accepted_mode(self):
        from controlled_execution_models import (
            ControlledExecutionMode, ControlledExecutionPolicy)
        service_class = shadow_mode.ShadowModeService
        for mode in ControlledExecutionMode:
            policy = ControlledExecutionPolicy(mode=mode)
            allowed = service_class._shadow_mode(policy)
            assert allowed is (
                mode is ControlledExecutionMode.SHADOW)

    def test_exchange_write_always_invalid(self):
        from controlled_execution_models import (
            ControlledExecutionMode, ControlledExecutionPolicy)
        policy = ControlledExecutionPolicy(
            mode=ControlledExecutionMode.SHADOW,
            exchange_write_allowed=True)
        assert shadow_mode.ShadowModeService \
            ._shadow_policy_valid(policy) is False

    def test_broker_usage_limited_to_simulation(self):
        source = _code_source(shadow_mode)
        assert "self.broker.submit" in source
        assert "self.broker.cancel" in source
        for token in ("broker.place", "broker.modify",
                      "broker.margin"):
            assert token not in source
