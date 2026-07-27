"""Mission 2200 — Agent 01: mimari sınır testleri.

- Mission 2100 sertifikalı dondurulmuş modüller DEĞİŞMEMİŞTİR
  (SHA-256 doğrulaması version_manifest.json'a karşı).
- Operasyon katmanı yalnız sertifikalı katman + kendi modülleri +
  stdlib'e import eder.
- LIVE hiçbir yerde varsayılan değildir.
"""

import ast
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads(
    (ROOT / "version_manifest.json").read_text(encoding="utf-8"))
FROZEN_MODULES = tuple(sorted(MANIFEST["module_sha256"]))

OPERATION_MODULES = (
    "operation_control_errors", "operation_control_models",
    "operation_control_policy", "operation_control_audit",
    "operation_control_mapper", "operation_control_snapshot",
    "operation_control_service", "operation_control_api",
    "operation_control_store")

# Operasyon katmanının izinli import kökleri.
ALLOWED_STDLIB = frozenset({
    "__future__", "contextlib", "dataclasses", "decimal",
    "enum", "fcntl", "functools", "json", "os", "pathlib",
    "threading", "types", "typing"})
ALLOWED_PROJECT = frozenset(OPERATION_MODULES) | frozenset(
    FROZEN_MODULES) | frozenset({
        # Mission 2100 öncesi sertifikalı çekirdek modeller
        # (manifest kapsamı dışı ama yürütme katmanının parçası).
        "execution_enums", "execution_models"})


def source_of(name: str) -> str:
    return (ROOT / f"{name}.py").read_text(encoding="utf-8")


def import_roots(name: str) -> frozenset:
    tree = ast.parse(source_of(name))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return frozenset(roots)


class TestFrozenLayerIntegrity:
    def test_frozen_module_count(self):
        assert len(FROZEN_MODULES) == 31

    @pytest.mark.parametrize("name", FROZEN_MODULES)
    def test_sha256_unchanged(self, name):
        digest = hashlib.sha256(
            (ROOT / f"{name}.py").read_bytes()).hexdigest()
        assert digest == MANIFEST["module_sha256"][name], (
            f"Dondurulmuş modül DEĞİŞTİRİLMİŞ: {name}")

    def test_release_validator_manifest_identity(self):
        import release_validator as rv
        assert rv.verify_manifest_identity(MANIFEST) == ()

    def test_operation_modules_not_in_frozen_set(self):
        assert not set(OPERATION_MODULES) & set(FROZEN_MODULES)


class TestOperationLayerBoundaries:
    @pytest.mark.parametrize("name", OPERATION_MODULES)
    def test_imports_only_certified_or_stdlib(self, name):
        extra = import_roots(name) - ALLOWED_STDLIB - \
            ALLOWED_PROJECT
        assert extra == frozenset(), f"{name}: {sorted(extra)}"

    @pytest.mark.parametrize("name", [
        "operation_control_errors", "operation_control_models",
        "operation_control_policy", "operation_control_audit",
        "operation_control_mapper", "operation_control_api",
        "operation_control_store"])
    def test_pure_modules_no_certified_execution_import(self, name):
        """Yalnız servis katmanı sertifikalı yürütmeye dokunur."""
        roots = import_roots(name)
        assert "controlled_execution_api" not in roots

    def test_service_uses_controlled_api_only(self):
        roots = import_roots("operation_control_service")
        assert "controlled_execution_api" in roots
        assert "paper_broker" not in roots  # doğrudan broker YOK

    @pytest.mark.parametrize("name", OPERATION_MODULES)
    def test_no_live_default(self, name):
        source = source_of(name)
        assert 'ControlledExecutionMode.LIVE' not in source
        assert '"LIVE"' not in source.replace(
            '"MICRO_LIVE"', "")

    def test_default_mode_paper(self):
        import operation_control_policy as pol
        assert pol.DEFAULT_EXECUTION_MODE == "PAPER"

    @pytest.mark.parametrize("name", OPERATION_MODULES)
    def test_module_has_docstring_contract(self, name):
        tree = ast.parse(source_of(name))
        assert ast.get_docstring(tree), name

    def test_api_module_framework_free(self):
        roots = import_roots("operation_control_api")
        assert roots <= (ALLOWED_STDLIB |
                         frozenset(OPERATION_MODULES))


class TestAppWiring:
    def test_app_uses_service_not_broker_directly(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "OperationControlService" in source
        assert "get_operation_service" in source

    def test_close_route_flows_through_controlled_api(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "request_position_close" in source
        # Rota gövdesinde doğrudan borsa emri çağrısı yoktur.
        assert "client.create_order" not in source
