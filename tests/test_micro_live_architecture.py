"""Mission 2100 — Agent 06: Micro Live yetkilendirme mimari
testleri.

Taban: Execution Core v1.0.0 (03e181d, FROZEN) + Agent 01
(4304527) + Agent 02 (69bd05c) + Agent 03 (32f4a3a) + Agent 04
(bf2a21d) + Agent 05 (459ca5a, regresyon 6392 PASS). Micro Live
yetkilendirme katmanının dondurulmuş çekirdeği ve önceki ajan
katmanlarını DEĞİŞTİRMEDEN, emir/borsa/broker erişimi OLMADAN
çalıştığını kanıtlar.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import execution_architecture_freeze as freeze
import execution_security_certification as cert
import micro_live_authorization
import micro_live_errors
import micro_live_models
import micro_live_policy
from micro_live_models import (MicroLiveAuthorizationState,
                               MicroLiveDecision,
                               MicroLiveDecisionCode,
                               MicroLiveOperation,
                               MicroLiveStage)

# Mission 2100 durumu ve taban değerleri
MISSION_2100_STATUS = "IN_PROGRESS"
BASELINE_VERSION = "1.0.0"
BASELINE_COMMIT = "03e181d"
AGENT_01_COMMIT = "4304527"
AGENT_02_COMMIT = "69bd05c"
AGENT_03_COMMIT = "32f4a3a"
AGENT_04_COMMIT = "bf2a21d"
AGENT_05_COMMIT = "459ca5a"
AGENT_05_REGRESSION = 6392

MICRO_LIVE_MODULES = (micro_live_errors, micro_live_models,
                      micro_live_policy,
                      micro_live_authorization)
MICRO_LIVE_MODULE_NAMES = ("micro_live_errors",
                           "micro_live_models",
                           "micro_live_policy",
                           "micro_live_authorization")
PRIOR_2100_MODULE_NAMES = (
    "controlled_execution_errors",
    "controlled_execution_models",
    "controlled_execution_policy",
    "controlled_execution_foundation",
    "runtime_errors", "runtime_enums", "runtime_models",
    "paper_errors", "paper_models", "paper_ledger",
    "paper_broker", "paper_execution_errors",
    "paper_execution_models", "paper_execution_mapper",
    "paper_execution_service", "shadow_errors",
    "shadow_models", "shadow_comparator", "shadow_mode")


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
        assert AGENT_05_COMMIT == "459ca5a"
        assert AGENT_05_REGRESSION == 6392

    def test_mission_2000_manifest_untouched(self):
        import execution_regression_manifest as manifest
        assert manifest.BASELINE_COMMIT == "01aa429"
        assert manifest.BASELINE_REGRESSION == 3704
        assert manifest.FREEZE_STATUS == "FROZEN"

    def test_agent_04_05_public_api_unchanged(self):
        import paper_execution_service
        import shadow_comparator
        import shadow_errors
        import shadow_mode
        import shadow_models
        assert len(paper_execution_service.__all__) == 3
        assert len(shadow_errors.__all__) == 7
        assert len(shadow_models.__all__) == 13
        assert shadow_comparator.__all__ == ["ShadowComparator"]
        assert shadow_mode.__all__ == ["ShadowModeService"]

    def test_frozen_module_set_unchanged(self):
        assert len(freeze.FROZEN_MODULES) == 20
        assert not set(MICRO_LIVE_MODULE_NAMES) & \
            set(freeze.FROZEN_MODULES)

    def test_ownership_map_untouched(self):
        assert len(freeze.DOMAIN_OWNERSHIP) == 24


# ── Bağımlılık yönü ──────────────────────────────────────────────────

class TestDependencyDirection:
    @pytest.mark.parametrize("core_name",
                             list(freeze.FROZEN_MODULES))
    def test_core_never_imports_micro_live_layer(self,
                                                 core_name):
        core_module = importlib.import_module(core_name)
        assert not _module_imports(core_module) & \
            set(MICRO_LIVE_MODULE_NAMES)

    @pytest.mark.parametrize("prior_name",
                             PRIOR_2100_MODULE_NAMES)
    def test_prior_agents_never_import_micro_live_layer(
            self, prior_name):
        prior_module = importlib.import_module(prior_name)
        assert not _module_imports(prior_module) & \
            set(MICRO_LIVE_MODULE_NAMES)

    def test_errors_imports(self):
        assert _module_imports(micro_live_errors) <= {
            "__future__"}

    def test_models_imports(self):
        assert _module_imports(micro_live_models) <= {
            "__future__", "dataclasses", "decimal", "enum",
            "typing", "controlled_execution_models",
            "execution_enums", "micro_live_errors"}

    def test_policy_imports(self):
        assert _module_imports(micro_live_policy) <= {
            "__future__", "dataclasses", "typing",
            "controlled_execution_models",
            "execution_kill_switch_models",
            "execution_risk_models", "micro_live_models"}

    def test_service_imports(self):
        assert _module_imports(micro_live_authorization) <= {
            "__future__", "dataclasses", "typing",
            "controlled_execution_foundation",
            "controlled_execution_models",
            "micro_live_errors", "micro_live_models",
            "micro_live_policy"}

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_execution_or_broker_imports(self, module):
        forbidden = {"execution_service", "execution_api",
                     "execution_service_models",
                     "execution_api_models",
                     "execution_broker_adapter",
                     "execution_broker_models",
                     "exchange_gateway",
                     "binance_spot_adapter",
                     "binance_capabilities",
                     "binance_normalizer", "paper_broker",
                     "paper_ledger", "paper_execution_service",
                     "shadow_mode", "flask", "app", "requests"}
        assert not _module_imports(module) & forbidden

    def test_no_ai_or_ui_imports(self):
        for module in MICRO_LIVE_MODULES:
            imports = _module_imports(module)
            for root in imports:
                assert not root.startswith("alpha20")
                assert root not in {"numpy", "pandas",
                                    "sklearn", "torch"}


# ── Kanonik model koruması ───────────────────────────────────────────

class TestCanonicalModelProtection:
    @pytest.mark.parametrize("symbol", [
        "ExecutionRequest", "ExecutionResult", "ExecutionMode",
        "RiskDecision", "RiskDecisionType", "KillSwitchSnapshot",
        "KillSwitchState", "Order", "Position", "Fill",
        "PaperOrder", "PaperExecution", "PaperLedgerSnapshot",
        "PaperBroker", "PaperLedger", "PaperExecutionService",
        "ShadowModeService", "ShadowSnapshot", "ShadowOrder",
        "ControlledExecutionPolicy", "ControlledExecutionMode",
        "ControlledExecutionFoundation"])
    def test_canonical_models_not_redefined(self, symbol):
        for module in MICRO_LIVE_MODULES:
            assert symbol not in _defined_classes(module), \
                f"{module.__name__}: {symbol} kopyası yasak"

    def test_models_no_inheritance_trees(self):
        for name in micro_live_models.__all__:
            model = getattr(micro_live_models, name)
            if dataclasses.is_dataclass(model):
                assert model.__bases__ == (object,)

    @pytest.mark.parametrize("name", [
        "MicroLiveScope", "MicroLiveLimits", "MicroLiveRequest",
        "MicroLiveApproval", "MicroLiveAudit",
        "MicroLiveAuthorization", "MicroLiveReferences",
        "MicroLiveSnapshot", "MicroLiveStatistics",
        "MicroLiveHeartbeat", "MicroLiveResult"])
    def test_models_frozen_slots(self, name):
        model = getattr(micro_live_models, name)
        assert model.__dataclass_params__.frozen
        assert "__slots__" in vars(model)

    @pytest.mark.parametrize("name,module", [
        ("MicroLiveAuthorizationService",
         micro_live_authorization),
        ("MicroLiveTransitionPolicy", micro_live_policy),
        ("MicroLiveAuthorizationPolicy", micro_live_policy)])
    def test_services_frozen_slots(self, name, module):
        model = getattr(module, name)
        assert model.__dataclass_params__.frozen
        assert "__slots__" in vars(model)

    def test_error_taxonomy_closed(self):
        root = micro_live_errors.MicroLiveError
        subclasses = {
            micro_live_errors.MicroLiveContractError,
            micro_live_errors.MicroLiveConfigurationError,
            micro_live_errors.MicroLiveModeError,
            micro_live_errors.MicroLivePolicyError,
            micro_live_errors.MicroLiveTransitionError,
            micro_live_errors.MicroLiveStateError}
        assert set(root.__subclasses__()) == subclasses
        for subclass in subclasses:
            assert issubclass(subclass, root)

    def test_public_exports_exact(self):
        assert micro_live_errors.__all__ == [
            "MicroLiveError", "MicroLiveContractError",
            "MicroLiveConfigurationError", "MicroLiveModeError",
            "MicroLivePolicyError", "MicroLiveTransitionError",
            "MicroLiveStateError"]
        assert micro_live_models.__all__ == [
            "MicroLiveAuthorizationState",
            "MicroLiveOperation", "MicroLiveDecision",
            "MicroLiveDecisionCode", "MicroLiveStage",
            "MicroLiveScope", "MicroLiveLimits",
            "MicroLiveRequest", "MicroLiveApproval",
            "MicroLiveAudit", "MicroLiveAuthorization",
            "MicroLiveReferences", "MicroLiveSnapshot",
            "MicroLiveStatistics", "MicroLiveHeartbeat",
            "MicroLiveResult"]
        assert micro_live_policy.__all__ == [
            "MicroLiveTransitionPolicy",
            "MicroLiveAuthorizationPolicy"]
        assert micro_live_authorization.__all__ == [
            "MicroLiveAuthorizationService"]

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_all_exports_resolve(self, module):
        for name in module.__all__:
            assert hasattr(module, name)

    @pytest.mark.parametrize("enum_name,size", [
        ("MicroLiveAuthorizationState", 6),
        ("MicroLiveOperation", 6), ("MicroLiveDecision", 4),
        ("MicroLiveDecisionCode", 16), ("MicroLiveStage", 9)])
    def test_enums_closed(self, enum_name, size):
        enum_type = getattr(micro_live_models, enum_name)
        assert len(list(enum_type)) == size

    def test_state_members_exact(self):
        assert [state.value
                for state in MicroLiveAuthorizationState] == [
            "NONE", "PENDING", "APPROVED", "DENIED",
            "EXPIRED", "REVOKED"]

    def test_operation_members_exact(self):
        assert [op.value for op in MicroLiveOperation] == [
            "REQUEST_AUTHORIZATION", "APPROVE", "DENY",
            "EXPIRE", "REVOKE", "EVALUATE"]

    def test_decision_members_exact(self):
        assert [d.value for d in MicroLiveDecision] == [
            "ACCEPTED", "DENIED", "AUTHORIZED",
            "NOT_AUTHORIZED"]

    def test_stage_order_fixed(self):
        assert [stage.value for stage in MicroLiveStage] == [
            "REQUEST_VALIDATED", "MODE_VALIDATED",
            "TRANSITION_VALIDATED", "RISK_EVALUATED",
            "PERMISSION_EVALUATED", "KILL_SWITCH_CHECKED",
            "LIMITS_VALIDATED", "AUTHORIZATION_RECORDED",
            "EVALUATION_COMPLETED"]

    def test_no_unrestricted_live_code(self):
        codes = {code.value for code in MicroLiveDecisionCode}
        assert "LIVE_ENABLED" not in codes
        assert "UNRESTRICTED" not in codes


# ── Güvenlik taramaları ──────────────────────────────────────────────

class TestMicroLiveSecurity:
    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_forbidden_imports(self, module):
        assert not _module_imports(module) & \
            cert.FORBIDDEN_IMPORT_ROOTS

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_forbidden_tokens(self, module):
        source = _code_source(module)
        for exempt in cert.TOKEN_EXEMPT_SUBSTRINGS:
            source = source.replace(exempt, "")
        hits = [t for t in cert.FORBIDDEN_TOKENS if t in source]
        assert not hits, f"{module.__name__}: {hits}"

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_forbidden_builtin_calls(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Name):
                assert node.func.id not in \
                    cert.FORBIDDEN_CALL_NAMES

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_float_literals(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_dynamic_import_or_exec(self, module):
        source = _code_source(module)
        for token in ("importlib", "__import__", "eval(",
                      "exec(", "compile(", "pickle",
                      "entry_points", "pkgutil", "subprocess"):
            assert token not in source

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_broker_or_trading_tokens(self, module):
        source = _code_source(module)
        for token in ("requests", "urllib", "socket", "http",
                      "websocket", "binance", "Binance",
                      "ccxt", "endpoint", "signing",
                      "credential", "listen_key", "margin",
                      "withdraw", "transfer", "broker",
                      "Broker", "order_input", "submit(",
                      "cancel(", "fill(", "trade("):
            assert token not in source, token

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_nondeterminism_tokens(self, module):
        source = _code_source(module)
        for token in ("random", "uuid", "datetime", "time.",
                      "sleep", "retry", "monotonic",
                      "perf_counter", "sched"):
            assert token not in source

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_persistence_tokens(self, module):
        source = _code_source(module)
        for token in ("sqlite", "sqlalchemy", "psycopg",
                      "redis", "shelve", "mongo", "save(",
                      "load(", "write(", "read(", "commit(",
                      "open("):
            assert token not in source

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_environment_access(self, module):
        source = _code_source(module)
        for token in ("os.environ", "getenv", "dotenv"):
            assert token not in source

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
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

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_threads_loops_context(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            assert not isinstance(
                node, (ast.AsyncFunctionDef, ast.With,
                       ast.AsyncWith, ast.While, ast.Lambda))

    @pytest.mark.parametrize("module", MICRO_LIVE_MODULES)
    def test_no_recursion(self, module):
        tree = ast.parse(inspect.getsource(module))
        for func in ast.walk(tree):
            if isinstance(func, ast.FunctionDef):
                for node in ast.walk(func):
                    if isinstance(node, ast.Call) and \
                            isinstance(node.func, ast.Name):
                        assert node.func.id != func.name

    def test_no_secret_names_in_sources(self):
        for module in MICRO_LIVE_MODULES:
            source = _code_source(module)
            for token in ("BINANCE_API", "SESSION_SECRET",
                          "PASSWORD_HASH", "api_key",
                          "api_secret"):
                assert token not in source

    def test_no_mode_escalation_tokens(self):
        for module in MICRO_LIVE_MODULES:
            source = _code_source(module)
            for token in ("auto_escalat", "fallback",
                          "auto_approve", "permanent"):
                assert token not in source

    def test_no_order_placement_capability(self):
        """Servis yüzeyinde emir/borsa işlemi YOKTUR."""
        from micro_live_authorization import (
            MicroLiveAuthorizationService)
        surface = {name for name in dir(
            MicroLiveAuthorizationService)
            if not name.startswith("_")}
        assert surface == {"request_authorization", "approve",
                           "deny", "expire", "revoke",
                           "evaluate", "statistics",
                           "heartbeat", "foundation",
                           "transition_policy",
                           "policy_rules"}

    def test_exchange_write_never_valid(self):
        from controlled_execution_models import (
            ControlledExecutionMode, ControlledExecutionPolicy)
        from micro_live_policy import (
            MicroLiveAuthorizationPolicy)
        policy = ControlledExecutionPolicy(
            mode=ControlledExecutionMode.MICRO_LIVE,
            exchange_write_allowed=True,
            human_confirmation_required=True,
            explicit_authorization_required=True,
            authorization_reference="auth-1")
        assert MicroLiveAuthorizationPolicy.policy_valid(
            policy) is False
