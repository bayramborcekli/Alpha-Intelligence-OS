"""Mission 2100 — Agent 10: Yayın mimari dondurma testleri.

Dondurulmuş modül bütünlüğü: version_manifest.json içindeki
SHA-256 imzaları CANLI kaynağa uygulanır; mimari değişmezler
(döngüsüzlük, alan tekliği, ihraç bütünlüğü, güvenlik taraması)
yayın anındaki hâliyle yeniden doğrulanır.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import security_validation as sv  # noqa: E402

MANIFEST = json.loads(
    (ROOT / "version_manifest.json").read_text(
        encoding="utf-8"))
MODULES = sv.MISSION_2100_MODULES
PINNED = MANIFEST["module_sha256"]


def _sha256(module_name):
    return hashlib.sha256(
        (ROOT / f"{module_name}.py").read_bytes()).hexdigest()


class TestFrozenModules:
    """Dondurulmuş modüller DEĞİŞMEMİŞ olmalıdır."""

    @pytest.mark.parametrize("module_name", MODULES)
    def test_module_hash_unchanged(self, module_name):
        assert _sha256(module_name) == PINNED[module_name], (
            f"{module_name} yayından sonra değişti — "
            "FROZEN ihlali")

    @pytest.mark.parametrize("module_name", MODULES)
    def test_module_pinned(self, module_name):
        assert module_name in PINNED

    def test_pin_count_exact(self):
        assert len(PINNED) == len(MODULES) == 31

    @pytest.mark.parametrize("module_name", MODULES)
    def test_hash_format(self, module_name):
        value = PINNED[module_name]
        assert len(value) == 64
        assert set(value) <= set("0123456789abcdef")

    def test_manifest_modules_match_certified_set(self):
        assert tuple(MANIFEST["certified_modules"]) == MODULES


class TestReleaseSecuritySweep:
    """Yayın anında 31 modülün güvenlik taraması yeniden
    koşulur — sertifika sahte-temiz olamaz."""

    @pytest.mark.parametrize("module_name", MODULES)
    def test_module_scan_clean(self, module_name):
        source = (ROOT / f"{module_name}.py").read_text(
            encoding="utf-8")
        report = sv.validate_module_source(module_name,
                                           source)
        assert report.clean, report.findings

    @pytest.mark.parametrize("module_name", (
        "release_validator", "security_validation",
        "soak_runner", "regression_runner",
        "system_certification"))
    def test_release_layer_no_forbidden_calls(self,
                                              module_name):
        source = (ROOT / f"{module_name}.py").read_text(
            encoding="utf-8")
        assert sv.find_forbidden_calls(module_name,
                                       source) == ()

    def test_release_validator_no_file_io(self):
        source = (ROOT / "release_validator.py").read_text(
            encoding="utf-8")
        stripped = sv.strip_docstrings(source)
        assert "open(" not in stripped
        assert "read_text" not in stripped
        assert "Path(" not in stripped


class TestReleaseGraph:
    def test_no_circular_imports_at_release(self):
        graph = {}
        module_set = set(MODULES)
        for name in MODULES:
            tree = sv.parse_source(
                (ROOT / f"{name}.py").read_text(
                    encoding="utf-8"))
            graph[name] = {root for root in
                           sv.collect_import_roots(tree)
                           if root in module_set}
        visiting, done = set(), set()

        def visit(node):
            if node in done:
                return
            assert node not in visiting, node
            visiting.add(node)
            for neighbor in sorted(graph[node]):
                visit(neighbor)
            visiting.discard(node)
            done.add(node)

        for name in MODULES:
            visit(name)

    def test_release_validator_reads_only_verification_layer(
            self):
        tree = sv.parse_source(
            (ROOT / "release_validator.py").read_text(
                encoding="utf-8"))
        local = {root for root in
                 sv.collect_import_roots(tree)
                 if (ROOT / f"{root}.py").exists()}
        assert local <= {"regression_runner",
                         "security_validation",
                         "system_certification"}


class TestNoDuplicateDomain:
    def test_release_class_names_unique(self):
        import ast
        seen = {}
        for name in MODULES + ("release_validator",
                               "security_validation",
                               "soak_runner",
                               "regression_runner",
                               "system_certification"):
            tree = ast.parse(
                (ROOT / f"{name}.py").read_text(
                    encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    assert node.name not in seen, (
                        node.name, seen.get(node.name), name)
                    seen[node.name] = name
