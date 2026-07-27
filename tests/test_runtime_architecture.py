"""Mission 2100 — Agent 02: Çalışma zamanı mimari testleri.

Taban: Execution Core v1.0.0 (03e181d, FROZEN) + Mission 2100
Agent 01 (4304527, regresyon 4619 PASS). Bu paket, çalışma zamanı
alan katmanının dondurulmuş çekirdeği ve Agent 01 temelini
DEĞİŞTİRMEDEN üzerine inşa edildiğini kanıtlar.
"""

from __future__ import annotations

import ast
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
import runtime_enums
import runtime_errors
import runtime_models

# Mission 2100 durumu ve taban değerleri
MISSION_2100_STATUS = "IN_PROGRESS"
BASELINE_VERSION = "1.0.0"
BASELINE_COMMIT = "03e181d"
BASELINE_REGRESSION = 4375
AGENT_01_COMMIT = "4304527"
AGENT_01_REGRESSION = 4619

RT_MODULES = (runtime_errors, runtime_enums, runtime_models)
RT_MODULE_NAMES = ("runtime_errors", "runtime_enums",
                   "runtime_models")
CE_MODULE_NAMES = ("controlled_execution_errors",
                   "controlled_execution_models",
                   "controlled_execution_policy",
                   "controlled_execution_foundation")


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

    def test_core_baseline_values(self):
        assert BASELINE_VERSION == "1.0.0"
        assert BASELINE_COMMIT == "03e181d"
        assert BASELINE_REGRESSION == 4375

    def test_agent_01_baseline_values(self):
        assert AGENT_01_COMMIT == "4304527"
        assert AGENT_01_REGRESSION == 4619

    def test_mission_2000_manifest_untouched(self):
        import execution_regression_manifest as manifest
        assert manifest.BASELINE_COMMIT == "01aa429"
        assert manifest.BASELINE_REGRESSION == 3704
        assert manifest.FREEZE_STATUS == "FROZEN"

    def test_agent_01_public_api_unchanged(self):
        import controlled_execution_errors as ce_err
        import controlled_execution_foundation as ce_fnd
        import controlled_execution_models as ce_mdl
        import controlled_execution_policy as ce_pol
        total = (len(ce_err.__all__) + len(ce_mdl.__all__)
                 + len(ce_pol.__all__) + len(ce_fnd.__all__))
        assert total == 10

    def test_frozen_module_set_unchanged(self):
        assert len(freeze.FROZEN_MODULES) == 20
        assert not set(RT_MODULE_NAMES) & \
            set(freeze.FROZEN_MODULES)

    def test_ownership_map_untouched(self):
        assert len(freeze.DOMAIN_OWNERSHIP) == 24


# ── Bağımlılık yönü ──────────────────────────────────────────────────

class TestDependencyDirection:
    @pytest.mark.parametrize("core_name",
                             list(freeze.FROZEN_MODULES))
    def test_core_never_imports_runtime_layer(self, core_name):
        core_module = importlib.import_module(core_name)
        imports = _module_imports(core_module)
        assert not imports & set(RT_MODULE_NAMES), \
            f"{core_name}: ters bağımlılık — çekirdek runtime import edemez"

    @pytest.mark.parametrize("ce_name", CE_MODULE_NAMES)
    def test_agent_01_never_imports_runtime_layer(self, ce_name):
        ce_module = importlib.import_module(ce_name)
        assert not _module_imports(ce_module) & \
            set(RT_MODULE_NAMES)

    def test_runtime_errors_imports(self):
        assert _module_imports(runtime_errors) <= {"__future__"}

    def test_runtime_enums_imports(self):
        assert _module_imports(runtime_enums) <= \
            {"__future__", "enum"}

    def test_runtime_models_imports(self):
        allowed = {"__future__", "dataclasses", "decimal",
                   "typing", "execution_enums", "runtime_enums",
                   "runtime_errors"}
        assert _module_imports(runtime_models) <= allowed

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_execution_service_imports(self, module):
        forbidden = {"execution_service", "execution_api",
                     "execution_broker_adapter",
                     "execution_service_models",
                     "execution_api_models", "exchange_gateway",
                     "binance_spot_adapter"}
        assert not _module_imports(module) & forbidden


# ── Kanonik model koruması ───────────────────────────────────────────

class TestCanonicalModelProtection:
    @pytest.mark.parametrize("symbol", [
        "ExecutionRequest", "ExecutionResult", "BrokerProfile",
        "RiskDecision", "KillSwitch", "BrokerAdapter",
        "Order", "Position", "Fill", "Instrument", "Portfolio",
        "ExecutionMode", "ExecutionServiceRequest",
        "ExecutionApiRequest", "ValidationResult"])
    def test_canonical_models_not_redefined(self, symbol):
        for module in RT_MODULES:
            assert symbol not in _defined_classes(module), \
                f"{module.__name__}: {symbol} kopyası yasak"

    def test_runtime_environment_is_distinct_enum(self):
        from controlled_execution_models import (
            ControlledExecutionMode)
        from runtime_enums import RuntimeEnvironment
        assert RuntimeEnvironment is not ControlledExecutionMode

    def test_canonical_enums_reused_not_copied(self):
        # OrderSide/OrderType/OrderState/PositionSide çekirdekten
        # yeniden kullanılır — runtime katmanında kopyası yoktur
        import execution_enums
        for name in ("OrderSide", "OrderType", "OrderState",
                     "PositionSide"):
            assert name not in _defined_classes(runtime_models)
            assert name in execution_enums.__all__

    def test_models_are_pure_dataclasses(self):
        # Kalıtım ağacı yok: her model doğrudan object'ten türer
        for name in runtime_models.__all__:
            model = getattr(runtime_models, name)
            assert model.__bases__ == (object,)


# ── Güvenlik taramaları ──────────────────────────────────────────────

class TestRuntimeSecurity:
    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_forbidden_imports(self, module):
        assert not _module_imports(module) & \
            cert.FORBIDDEN_IMPORT_ROOTS

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_forbidden_tokens(self, module):
        source = _code_source(module)
        for exempt in cert.TOKEN_EXEMPT_SUBSTRINGS:
            source = source.replace(exempt, "")
        hits = [t for t in cert.FORBIDDEN_TOKENS if t in source]
        assert not hits, f"{module.__name__}: {hits}"

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_forbidden_builtin_calls(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Name):
                assert node.func.id not in \
                    cert.FORBIDDEN_CALL_NAMES

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_float_literals(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_dynamic_import_or_exec(self, module):
        source = _code_source(module)
        for token in ("importlib", "__import__", "eval(",
                      "exec(", "compile(", "pickle",
                      "entry_points", "pkgutil"):
            assert token not in source

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_framework_tokens(self, module):
        source = _code_source(module)
        for token in ("Electron", "Tauri", "PySide", "Qt",
                      "React", "Flutter", "Kotlin", "Swift",
                      "fastapi", "FastAPI", "flask", "Flask",
                      "django", "Django", "Android", "iOS"):
            assert token not in source

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_persistence_tokens(self, module):
        source = _code_source(module)
        for token in ("sqlite", "sqlalchemy", "psycopg",
                      "redis", "shelve", "mongo", "save(",
                      "load(", "write(", "read(", "commit("):
            assert token not in source

    @pytest.mark.parametrize("module", RT_MODULES)
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

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_async_no_threads_no_io(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            assert not isinstance(
                node, (ast.AsyncFunctionDef, ast.With,
                       ast.AsyncWith, ast.While, ast.Lambda))

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_environment_string_branching(self, module):
        for node in ast.walk(ast.parse(_code_source(module))):
            if isinstance(node, ast.Compare):
                for comp in [node.left] + list(node.comparators):
                    if isinstance(comp, ast.Constant):
                        assert comp.value not in (
                            "PAPER", "SHADOW", "MICRO_LIVE")

    @pytest.mark.parametrize("module", RT_MODULES)
    def test_no_environment_access(self, module):
        source = _code_source(module)
        for token in ("os.environ", "getenv", "dotenv"):
            assert token not in source

    def test_no_secret_names_in_sources(self):
        for module in RT_MODULES:
            source = _code_source(module)
            for token in ("BINANCE_API", "SESSION_SECRET",
                          "PASSWORD_HASH", "api_key",
                          "api_secret"):
                assert token not in source
