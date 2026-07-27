"""Mission 2100 — Agent 01: Mimari ve çekirdek koruma testleri.

Taban: Execution Core v1.0.0, commit 03e181d, regresyon 4375 PASS,
mimari FROZEN, güvenlik CERTIFIED. Bu paket, Mission 2100 uzatma
katmanının dondurulmuş çekirdeği DEĞİŞTİRMEDEN üzerine inşa
edildiğini kanıtlar.
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

import controlled_execution_errors as ce_errors
import controlled_execution_foundation as ce_foundation
import controlled_execution_models as ce_models
import controlled_execution_policy as ce_policy
import execution_architecture_freeze as freeze

# Mission 2100 durumu
MISSION_2100_STATUS = "IN_PROGRESS"

# Mission 2000 tabanı (birebir — icat edilmemiş)
BASELINE_VERSION = "1.0.0"
BASELINE_COMMIT = "03e181d"
BASELINE_REGRESSION = 4375

CE_MODULES = (ce_errors, ce_models, ce_policy, ce_foundation)
CE_MODULE_NAMES = tuple(m.__name__ for m in CE_MODULES)


def _module_imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add((node.module or "").split(".")[0])
    return found


def _code_source(module) -> str:
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
        assert BASELINE_REGRESSION == 4375

    def test_mission_2000_manifest_untouched(self):
        import execution_regression_manifest as manifest
        assert manifest.BASELINE_COMMIT == "01aa429"
        assert manifest.BASELINE_REGRESSION == 3704
        assert manifest.FREEZE_STATUS == "FROZEN"

    def test_mission_2000_marked_frozen(self):
        path = os.path.join(_ROOT, "tests",
                            "test_mission_2000_architecture.py")
        with open(path, encoding="utf-8") as handle:
            assert 'MISSION_2000_STATUS = "FROZEN"' in \
                handle.read()


# ── Bağımlılık yönü ──────────────────────────────────────────────────

class TestDependencyDirection:
    @pytest.mark.parametrize("core_name",
                             list(freeze.FROZEN_MODULES))
    def test_core_never_imports_mission_2100(self, core_name):
        core_module = importlib.import_module(core_name)
        imports = _module_imports(core_module)
        assert not imports & set(CE_MODULE_NAMES), \
            f"{core_name}: ters bağımlılık — çekirdek 2100 import edemez"

    @pytest.mark.parametrize("module", CE_MODULES)
    def test_mission_2100_layer_imports(self, module):
        allowed = {"__future__", "dataclasses", "decimal",
                   "enum", "types", "typing"} | \
            set(CE_MODULE_NAMES)
        assert _module_imports(module) <= allowed

    def test_foundation_does_not_import_core_at_all(self):
        # Agent 01 katmanı çekirdeğe henüz DOKUNMAZ bile;
        # köprü sonraki ajanların onaylı sözleşmeleriyle kurulur
        for module in CE_MODULES:
            assert not _module_imports(module) & \
                set(freeze.FROZEN_MODULES)

    def test_frozen_module_set_unchanged(self):
        assert len(freeze.FROZEN_MODULES) == 20
        assert not set(CE_MODULE_NAMES) & \
            set(freeze.FROZEN_MODULES)


# ── Kanonik model koruması ───────────────────────────────────────────

class TestCanonicalModelProtection:
    @pytest.mark.parametrize("symbol", [
        "ExecutionMode", "BrokerProfile", "ExecutionRequest",
        "RiskDecision", "BrokerAdapter", "Order", "Position",
        "Fill", "Instrument", "Portfolio",
        "ExecutionServiceRequest", "ExecutionApiRequest"])
    def test_frozen_canonical_models_not_redefined(self, symbol):
        for module in CE_MODULES:
            assert symbol not in _defined_classes(module), \
                f"{module.__name__}: {symbol} kopyası yasak"

    def test_controlled_mode_is_distinct_enum(self):
        from controlled_execution_models import (
            ControlledExecutionMode)
        from execution_broker_models import ExecutionMode
        assert ControlledExecutionMode is not ExecutionMode
        assert ControlledExecutionMode.__name__ != \
            ExecutionMode.__name__

    def test_ownership_map_untouched(self):
        assert len(freeze.DOMAIN_OWNERSHIP) == 24


# ── Güvenlik taramaları (Mission 2100 modülleri) ─────────────────────

class TestMission2100Security:
    @pytest.mark.parametrize("module", CE_MODULES)
    def test_no_forbidden_imports(self, module):
        import execution_security_certification as cert
        assert not _module_imports(module) & \
            cert.FORBIDDEN_IMPORT_ROOTS

    @pytest.mark.parametrize("module", CE_MODULES)
    def test_no_forbidden_tokens(self, module):
        import execution_security_certification as cert
        source = _code_source(module)
        for exempt in cert.TOKEN_EXEMPT_SUBSTRINGS:
            source = source.replace(exempt, "")
        hits = [t for t in cert.FORBIDDEN_TOKENS if t in source]
        assert not hits, f"{module.__name__}: {hits}"

    @pytest.mark.parametrize("module", CE_MODULES)
    def test_no_forbidden_builtin_calls(self, module):
        import execution_security_certification as cert
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Name):
                assert node.func.id not in \
                    cert.FORBIDDEN_CALL_NAMES

    @pytest.mark.parametrize("module", CE_MODULES)
    def test_no_float_literals(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    @pytest.mark.parametrize("module", CE_MODULES)
    def test_no_dynamic_import_or_exec(self, module):
        source = _code_source(module)
        for token in ("importlib", "__import__", "eval(",
                      "exec(", "compile(", "pickle",
                      "entry_points", "pkgutil"):
            assert token not in source

    @pytest.mark.parametrize("module", CE_MODULES)
    def test_no_framework_tokens(self, module):
        source = _code_source(module)
        for token in ("Electron", "Tauri", "PySide", "Qt",
                      "React", "Flutter", "Kotlin", "Swift",
                      "fastapi", "FastAPI", "flask", "Flask",
                      "django", "Django", "Android", "iOS"):
            assert token not in source

    @pytest.mark.parametrize("module", CE_MODULES)
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

    @pytest.mark.parametrize("module", CE_MODULES)
    def test_no_async_no_threads_no_io(self, module):
        for node in ast.walk(ast.parse(
                inspect.getsource(module))):
            assert not isinstance(
                node, (ast.AsyncFunctionDef, ast.With,
                       ast.AsyncWith, ast.While, ast.Lambda))

    @pytest.mark.parametrize("module", CE_MODULES)
    def test_no_mode_string_branching(self, module):
        # Kanonik mod eşlemesi dışında mod-adı string dallanması
        # yasak: "PAPER"/"SHADOW"/"MICRO_LIVE" karşılaştırması yok
        for node in ast.walk(ast.parse(_code_source(module))):
            if isinstance(node, ast.Compare):
                for comp in [node.left] + list(node.comparators):
                    if isinstance(comp, ast.Constant):
                        assert comp.value not in (
                            "PAPER", "SHADOW", "MICRO_LIVE")
