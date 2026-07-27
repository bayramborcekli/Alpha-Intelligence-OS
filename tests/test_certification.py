"""Mission 2100 — Agent 09: Sertifikasyon ve mimari testleri.

Sertifikaların bütünlüğü ile Mission 2100 modül kümesi üzerinde
mimari doğrulama: bağımlılık yönü, döngüsel import yok, model
adı çakışması yok, frozen+slots modeller, kamu ihracı (__all__),
performans yasakları (while/özyineleme/zamanlayıcı yok) ve
sınırlı koleksiyonlar (mutable varsayılan yok).
"""

import ast
import importlib
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import security_validation as sv  # noqa: E402
import system_certification as sc  # noqa: E402
from regression_runner import (BASELINE_COMMIT,  # noqa: E402
                               BASELINE_REGRESSION)

MODULES = sv.MISSION_2100_MODULES
SOURCES = {name: (ROOT / f"{name}.py").read_text(
    encoding="utf-8") for name in MODULES}
TREES = {name: ast.parse(source)
         for name, source in SOURCES.items()}
IMPORTED = {name: importlib.import_module(name)
            for name in MODULES}

CERTIFICATES = {
    "security": sc.SECURITY_CERTIFICATE,
    "architecture": sc.ARCHITECTURE_CERTIFICATE,
    "regression": sc.REGRESSION_CERTIFICATE,
    "soak": sc.SOAK_CERTIFICATE,
    "readiness": sc.MISSION_2100_READINESS,
}

# Hata/temel modülleri servis modüllerini IMPORT EDEMEZ
SERVICE_MODULES = frozenset({
    "paper_execution_service", "shadow_mode",
    "micro_live_authorization", "controlled_execution_api",
    "controlled_execution_router", "order_lifecycle",
    "reconciliation", "paper_broker"})
MODEL_MODULES = tuple(name for name in MODULES
                      if name.endswith(("_models", "_errors")))


def _mission_imports(name):
    return {root for root in
            sv.collect_import_roots(TREES[name])
            if root in set(MODULES)}


class TestCertificates:
    @pytest.mark.parametrize("kind", sorted(CERTIFICATES))
    def test_certificate_immutable(self, kind):
        certificate = CERTIFICATES[kind]
        assert isinstance(certificate, MappingProxyType)
        with pytest.raises(TypeError):
            certificate["status"] = "REVOKED"

    @pytest.mark.parametrize("kind", ["security",
                                      "architecture",
                                      "regression", "soak"])
    def test_certified_status(self, kind):
        assert CERTIFICATES[kind]["status"] == "CERTIFIED"

    def test_readiness_ready(self):
        readiness = sc.MISSION_2100_READINESS
        assert readiness["status"] == "READY"
        assert readiness["mission_2000_unchanged"] is True
        assert readiness["agents_01_08_unchanged"] is True

    @pytest.mark.parametrize("counter", [
        "exchange_write", "secret_exposure",
        "production_network_request"])
    def test_readiness_zero_counters(self, counter):
        assert sc.MISSION_2100_READINESS[counter] == 0

    @pytest.mark.parametrize("counter", [
        "exchange_write", "secret_exposure",
        "credential_logging", "filesystem_write",
        "database_write", "environment_mutation",
        "dynamic_import", "eval_exec", "pickle",
        "subprocess", "thread_leak", "process_leak"])
    def test_security_zero_counters(self, counter):
        assert sc.SECURITY_CERTIFICATE[counter] == 0

    def test_security_covers_all_modules(self):
        assert sc.SECURITY_CERTIFICATE[
            "certified_modules"] == MODULES

    def test_regression_certificate_consistent(self):
        certificate = sc.REGRESSION_CERTIFICATE
        assert certificate["baseline_commit"] == \
            BASELINE_COMMIT
        assert certificate["baseline_regression"] == \
            BASELINE_REGRESSION
        assert certificate["critical_skips"] == 0

    def test_soak_certificate_profiles(self):
        assert sc.SOAK_CERTIFICATE["max_logical_hours"] == 24
        assert len(sc.SOAK_CERTIFICATE["profiles"]) == 4

    def test_architecture_module_count(self):
        assert sc.ARCHITECTURE_CERTIFICATE["module_count"] \
            == len(MODULES) == 31

    def test_readiness_references_certificates(self):
        readiness = sc.MISSION_2100_READINESS
        assert readiness["security"] == \
            sc.SECURITY_CERTIFICATE["status"]
        assert readiness["soak"] == \
            sc.SOAK_CERTIFICATE["status"]


class TestDependencyDirection:
    @pytest.mark.parametrize("module_name", MODEL_MODULES)
    def test_models_do_not_import_services(self, module_name):
        assert _mission_imports(module_name) & \
            SERVICE_MODULES == set()

    def test_no_circular_imports(self):
        graph = {name: _mission_imports(name)
                 for name in MODULES}
        visiting, done = set(), set()

        def visit(node, stack):
            if node in done:
                return
            assert node not in visiting, \
                f"Döngü: {stack + [node]}"
            visiting.add(node)
            for neighbor in sorted(graph.get(node, ())):
                visit(neighbor, stack + [node])
            visiting.discard(node)
            done.add(node)

        for name in MODULES:
            visit(name, [])

    def test_foundation_root_imports_nothing(self):
        assert _mission_imports(
            "controlled_execution_errors") == set()
        assert _mission_imports(
            "controlled_execution_models") <= {
            "controlled_execution_errors"}

    def test_api_top_of_graph(self):
        importers = [name for name in MODULES
                     if "controlled_execution_api" in
                     _mission_imports(name)]
        assert importers == []


class TestNoDuplicateModels:
    def test_class_names_unique_across_mission(self):
        seen = {}
        for name in MODULES:
            for node in TREES[name].body:
                if isinstance(node, ast.ClassDef):
                    assert node.name not in seen, (
                        f"{node.name}: {seen.get(node.name)}"
                        f" ve {name}")
                    seen[node.name] = name


class TestImmutableModels:
    @pytest.mark.parametrize("module_name", MODULES)
    def test_all_dataclasses_frozen_with_slots(
            self, module_name):
        module = IMPORTED[module_name]
        for attr in dir(module):
            value = getattr(module, attr)
            if isinstance(value, type) and \
                    is_dataclass(value) and \
                    value.__module__ == module_name:
                assert value.__dataclass_params__.frozen, \
                    f"{module_name}.{attr}"
                assert "__slots__" in value.__dict__, \
                    f"{module_name}.{attr}"

    @pytest.mark.parametrize("module_name", MODULES)
    def test_no_mutable_dataclass_defaults(self, module_name):
        module = IMPORTED[module_name]
        for attr in dir(module):
            value = getattr(module, attr)
            if isinstance(value, type) and \
                    is_dataclass(value) and \
                    value.__module__ == module_name:
                for field in fields(value):
                    assert not isinstance(
                        field.default, (list, dict, set)), \
                        f"{module_name}.{attr}.{field.name}"


class TestPublicExports:
    @pytest.mark.parametrize("module_name", MODULES)
    def test_declares_all(self, module_name):
        assert hasattr(IMPORTED[module_name], "__all__")
        assert len(IMPORTED[module_name].__all__) > 0

    @pytest.mark.parametrize("module_name", MODULES)
    def test_exports_resolve(self, module_name):
        module = IMPORTED[module_name]
        for export in module.__all__:
            assert hasattr(module, export), \
                f"{module_name}.{export}"


class TestPerformanceBans:
    @pytest.mark.parametrize("module_name", MODULES)
    def test_no_while_loops(self, module_name):
        nodes = [node for node in ast.walk(TREES[module_name])
                 if isinstance(node, ast.While)]
        assert nodes == []

    @pytest.mark.parametrize("module_name", MODULES)
    def test_no_direct_recursion(self, module_name):
        for node in ast.walk(TREES[module_name]):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and \
                        isinstance(call.func, ast.Name):
                    assert call.func.id != node.name, (
                        f"{module_name}.{node.name}")

    @pytest.mark.parametrize("module_name", MODULES)
    @pytest.mark.parametrize("token", ["sched", "Timer(",
                                       "poll(", "interval",
                                       "sleep("])
    def test_no_scheduler_tokens(self, module_name, token):
        stripped = sv.strip_docstrings(SOURCES[module_name])
        assert token not in stripped

    @pytest.mark.parametrize("module_name", MODULES)
    def test_no_async_constructs(self, module_name):
        nodes = [node for node in ast.walk(TREES[module_name])
                 if isinstance(node, (ast.AsyncFunctionDef,
                                      ast.Await,
                                      ast.AsyncWith,
                                      ast.AsyncFor))]
        assert nodes == []


class TestAgent09Modules:
    """Agent 09 modüllerinin kendisi de temiz olmalı."""

    A09_MODULES = ("security_validation", "soak_runner",
                   "regression_runner",
                   "system_certification")

    @pytest.mark.parametrize("module_name", A09_MODULES)
    def test_no_forbidden_imports(self, module_name):
        source = (ROOT / f"{module_name}.py").read_text(
            encoding="utf-8")
        roots = sv.collect_import_roots(ast.parse(source))
        banned = sv.FORBIDDEN_IMPORT_ROOTS - {"ast"}
        assert roots & banned == set()

    @pytest.mark.parametrize("module_name", A09_MODULES)
    def test_no_forbidden_calls(self, module_name):
        source = (ROOT / f"{module_name}.py").read_text(
            encoding="utf-8")
        assert sv.find_forbidden_calls(module_name,
                                       source) == ()

    @pytest.mark.parametrize("module_name", A09_MODULES)
    def test_turkish_docstring(self, module_name):
        module = importlib.import_module(module_name)
        assert module.__doc__
        assert "Mission 2100" in module.__doc__
