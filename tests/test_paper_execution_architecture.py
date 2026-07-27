"""Mission 2100 — Agent 04: Kağıt yürütme mimari testleri.

Taban: Execution Core v1.0.0 (03e181d, FROZEN) + Agent 01
(4304527) + Agent 02 (69bd05c) + Agent 03 (32f4a3a, regresyon
5585 PASS). Kağıt yürütme servisinin dondurulmuş çekirdek ve önceki
ajan katmanlarını DEĞİŞTİRMEDEN üzerine inşa edildiğini kanıtlar.
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
import paper_execution_errors
import paper_execution_mapper
import paper_execution_models
import paper_execution_service

# Mission 2100 durumu ve taban değerleri
MISSION_2100_STATUS = "IN_PROGRESS"
BASELINE_VERSION = "1.0.0"
BASELINE_COMMIT = "03e181d"
AGENT_01_COMMIT = "4304527"
AGENT_02_COMMIT = "69bd05c"
AGENT_03_COMMIT = "32f4a3a"
AGENT_03_REGRESSION = 5585

SERVICE_MODULES = (paper_execution_errors,
                   paper_execution_models,
                   paper_execution_mapper,
                   paper_execution_service)
SERVICE_MODULE_NAMES = ("paper_execution_errors",
                        "paper_execution_models",
                        "paper_execution_mapper",
                        "paper_execution_service")
PRIOR_2100_MODULE_NAMES = (
    "controlled_execution_errors",
    "controlled_execution_models",
    "controlled_execution_policy",
    "controlled_execution_foundation",
    "runtime_errors", "runtime_enums", "runtime_models",
    "paper_errors", "paper_models", "paper_ledger",
    "paper_broker")


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
        assert AGENT_03_REGRESSION == 5585

    def test_mission_2000_manifest_untouched(self):
        import execution_regression_manifest as manifest
        assert manifest.BASELINE_COMMIT == "01aa429"
        assert manifest.BASELINE_REGRESSION == 3704
        assert manifest.FREEZE_STATUS == "FROZEN"

    def test_agent_01_public_api_unchanged(self):
        import controlled_execution_errors as a
        import controlled_execution_foundation as d
        import controlled_execution_models as b
        import controlled_execution_policy as c
        assert (len(a.__all__) + len(b.__all__)
                + len(c.__all__) + len(d.__all__)) == 10

    def test_agent_02_public_api_unchanged(self):
        import runtime_enums
        import runtime_errors
        import runtime_models
        assert len(runtime_errors.__all__) == 3
        assert len(runtime_enums.__all__) == 5
        assert len(runtime_models.__all__) == 14

    def test_agent_03_public_api_unchanged(self):
        import paper_broker
        import paper_errors
        import paper_ledger
        import paper_models
        assert len(paper_errors.__all__) == 4
        assert len(paper_models.__all__) == 8
        assert len(paper_ledger.__all__) == 2
        assert len(paper_broker.__all__) == 4

    def test_frozen_module_set_unchanged(self):
        assert len(freeze.FROZEN_MODULES) == 20
        assert not set(SERVICE_MODULE_NAMES) & \
            set(freeze.FROZEN_MODULES)

    def test_ownership_map_untouched(self):
        assert len(freeze.DOMAIN_OWNERSHIP) == 24


# ── Bağımlılık yönü ──────────────────────────────────────────────────

class TestDependencyDirection:
    @pytest.mark.parametrize("core_name",
                             list(freeze.FROZEN_MODULES))
    def test_core_never_imports_service_layer(self, core_name):
        core_module = importlib.import_module(core_name)
        assert not _module_imports(core_module) & \
            set(SERVICE_MODULE_NAMES)

    @pytest.mark.parametrize("prior_name",
                             PRIOR_2100_MODULE_NAMES)
    def test_prior_agents_never_import_service_layer(
            self, prior_name):
        prior_module = importlib.import_module(prior_name)
        assert not _module_imports(prior_module) & \
            set(SERVICE_MODULE_NAMES)

    def test_errors_imports(self):
        assert _module_imports(paper_execution_errors) <= {
            "__future__"}

    def test_models_imports(self):
        assert _module_imports(paper_execution_models) <= {
            "__future__", "dataclasses", "decimal", "enum",
            "typing", "execution_models",
            "paper_execution_errors", "paper_models",
            "runtime_models"}

    def test_mapper_imports(self):
        assert _module_imports(paper_execution_mapper) <= {
            "__future__", "dataclasses", "decimal", "typing",
            "execution_enums", "execution_models",
            "paper_execution_errors", "paper_models",
            "runtime_models"}

    def test_service_imports(self):
        assert _module_imports(paper_execution_service) <= {
            "__future__", "dataclasses", "typing",
            "controlled_execution_foundation",
            "controlled_execution_models",
            "execution_kill_switch_models",
            "execution_models", "execution_risk_models",
            "paper_broker", "paper_errors",
            "paper_execution_errors",
            "paper_execution_mapper",
            "paper_execution_models", "paper_models",
            "runtime_enums", "runtime_models"}

    @pytest.mark.parametrize("module", SERVICE_MODULES)
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
        "PaperBroker", "PaperLedger",
        "RuntimeAccountSnapshot", "RuntimeBalance",
        "RuntimePosition", "RuntimeAuditRecord",
        "ControlledExecutionPolicy",
        "ControlledExecutionFoundation"])
    def test_canonical_models_not_redefined(self, symbol):
        for module in SERVICE_MODULES:
            assert symbol not in _defined_classes(module), \
                f"{module.__name__}: {symbol} kopyası yasak"

    def test_models_no_inheritance_trees(self):
        for name in paper_execution_models.__all__:
            model = getattr(paper_execution_models, name)
            if dataclasses.is_dataclass(model):
                assert model.__bases__ == (object,)

    @pytest.mark.parametrize("name", [
        "PaperExecutionReferences",
        "PaperExecutionServiceResult"])
    def test_models_frozen_slots(self, name):
        model = getattr(paper_execution_models, name)
        assert model.__dataclass_params__.frozen
        assert "__slots__" in vars(model)

    def test_service_frozen_slots(self):
        model = paper_execution_service.PaperExecutionService
        assert model.__dataclass_params__.frozen
        assert "__slots__" in vars(model)

    def test_mapper_frozen_slots(self):
        model = paper_execution_mapper.PaperExecutionMapper
        assert model.__dataclass_params__.frozen
        assert "__slots__" in vars(model)

    def test_error_taxonomy_closed(self):
        root = paper_execution_errors.PaperExecutionError
        subclasses = {
            paper_execution_errors.PaperExecutionContractError,
            paper_execution_errors
            .PaperExecutionConfigurationError,
            paper_execution_errors.PaperExecutionModeError,
            paper_execution_errors.PaperExecutionRiskError,
            paper_execution_errors
            .PaperExecutionPermissionError,
            paper_execution_errors.PaperExecutionStateError}
        assert set(root.__subclasses__()) == subclasses
        for subclass in subclasses:
            assert issubclass(subclass, root)

    def test_public_exports_exact(self):
        assert paper_execution_errors.__all__ == [
            "PaperExecutionError",
            "PaperExecutionContractError",
            "PaperExecutionConfigurationError",
            "PaperExecutionModeError",
            "PaperExecutionRiskError",
            "PaperExecutionPermissionError",
            "PaperExecutionStateError"]
        assert paper_execution_models.__all__ == [
            "PaperExecutionOperation",
            "PaperExecutionDecision",
            "PaperExecutionDecisionCode", "PaperAuditStage",
            "PaperExecutionReferences",
            "PaperExecutionServiceResult"]
        assert paper_execution_mapper.__all__ == [
            "PaperExecutionMapper"]
        assert paper_execution_service.__all__ == [
            "PaperExecutionService", "PaperRiskEvaluator",
            "StaticRiskEvaluator"]

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_all_exports_resolve(self, module):
        for name in module.__all__:
            assert hasattr(module, name)


# ── Güvenlik taramaları ──────────────────────────────────────────────

class TestServiceSecurity:
    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_forbidden_imports(self, module):
        assert not _module_imports(module) & \
            cert.FORBIDDEN_IMPORT_ROOTS

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_forbidden_tokens(self, module):
        source = _code_source(module)
        for exempt in cert.TOKEN_EXEMPT_SUBSTRINGS:
            source = source.replace(exempt, "")
        hits = [t for t in cert.FORBIDDEN_TOKENS if t in source]
        assert not hits, f"{module.__name__}: {hits}"

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_forbidden_builtin_calls(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Name):
                assert node.func.id not in \
                    cert.FORBIDDEN_CALL_NAMES

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_float_literals(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_dynamic_import_or_exec(self, module):
        source = _code_source(module)
        for token in ("importlib", "__import__", "eval(",
                      "exec(", "compile(", "pickle",
                      "entry_points", "pkgutil",
                      "subprocess"):
            assert token not in source

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_network_broker_tokens(self, module):
        source = _code_source(module)
        for token in ("requests", "urllib", "socket", "http",
                      "websocket", "binance", "Binance",
                      "ccxt", "REST", "endpoint", "signing",
                      "credential"):
            assert token not in source

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_nondeterminism_tokens(self, module):
        source = _code_source(module)
        for token in ("random", "uuid", "datetime", "time.",
                      "sleep", "retry", "monotonic",
                      "perf_counter"):
            assert token not in source

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_persistence_tokens(self, module):
        source = _code_source(module)
        for token in ("sqlite", "sqlalchemy", "psycopg",
                      "redis", "shelve", "mongo", "save(",
                      "load(", "write(", "read(", "commit(",
                      "open("):
            assert token not in source

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_environment_access(self, module):
        source = _code_source(module)
        for token in ("os.environ", "getenv", "dotenv"):
            assert token not in source

    @pytest.mark.parametrize("module", SERVICE_MODULES)
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

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_async_no_threads_no_loops(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            assert not isinstance(
                node, (ast.AsyncFunctionDef, ast.With,
                       ast.AsyncWith, ast.While, ast.Lambda))

    @pytest.mark.parametrize("module", SERVICE_MODULES)
    def test_no_recursion(self, module):
        tree = ast.parse(inspect.getsource(module))
        for func in ast.walk(tree):
            if isinstance(func, ast.FunctionDef):
                for node in ast.walk(func):
                    if isinstance(node, ast.Call) and \
                            isinstance(node.func, ast.Name):
                        assert node.func.id != func.name

    def test_no_secret_names_in_sources(self):
        for module in SERVICE_MODULES:
            source = _code_source(module)
            for token in ("BINANCE_API", "SESSION_SECRET",
                          "PASSWORD_HASH", "api_key",
                          "api_secret"):
                assert token not in source

    def test_no_mode_escalation_tokens(self):
        source = _code_source(paper_execution_service)
        for token in ("SHADOW_POLICY_ALLOWED",
                      "MICRO_LIVE_ALLOWED", "LIVE_ALLOWED",
                      "auto_escalat", "fallback"):
            assert token not in source

    def test_paper_is_only_accepted_mode(self):
        from controlled_execution_models import (
            ControlledExecutionMode)
        service_class = (paper_execution_service
                         .PaperExecutionService)
        for mode in ControlledExecutionMode:
            from controlled_execution_models import (
                ControlledExecutionPolicy)
            policy = ControlledExecutionPolicy(mode=mode)
            allowed = service_class._paper_mode(policy)
            assert allowed is (
                mode is ControlledExecutionMode.PAPER)
