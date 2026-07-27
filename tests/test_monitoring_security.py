"""Mission 1900 — Agent 07 Monitoring Security Verifier testleri.

Kapsam: onaylı importlar PASS, yasak importlar FAIL, bağımlılık grafiği
PASS/inversiyon FAIL, meta veri sahipliği, kamu API yüzeyi,
immutability doğrulaması, determinizm, sterile arızalar, yanlış
pozitif yokluğu, AST doğrulaması, regresyon.
"""

from __future__ import annotations

import ast

import pytest

import monitoring_security
from monitoring_security import (
    ALLOWED_IMPORTS,
    ALLOWED_PROJECT_DEPS,
    APPROVED_PUBLIC_API,
    CHECKED_RULES,
    ERROR_VERIFICATION,
    FORBIDDEN_MODULE_PREFIXES,
    SECURITY_REPORT_FIELDS,
    verify_monitoring_security,
)

import alert_engine
import monitoring_api
import monitoring_export
import monitoring_intelligence
import monitoring_service

STACK_NAMES = ("monitoring_intelligence", "alert_engine",
               "monitoring_service", "monitoring_api",
               "monitoring_export")


def _tree(source: str) -> ast.AST:
    return ast.parse(source)


def _mods(source: str) -> frozenset[str]:
    return monitoring_security._imported_modules(_tree(source))


# ── A. Onaylı importlar PASS ─────────────────────────────────────────

class TestApprovedImportsPass:
    def test_stack_verifies_clean(self):
        report = verify_monitoring_security()
        assert report["verified"] is True
        assert report["violations"] == ()

    @pytest.mark.parametrize("name", STACK_NAMES)
    def test_each_layer_import_surface_clean(self, name):
        import inspect
        module = monitoring_security._STACK[name]
        mods = monitoring_security._imported_modules(
            _tree(inspect.getsource(module)))
        assert monitoring_security._check_import_surface(
            name, mods, ALLOWED_IMPORTS) == ()
        assert monitoring_security._check_forbidden(
            name, mods, FORBIDDEN_MODULE_PREFIXES) == ()

    def test_allowed_import_accepted(self):
        mods = _mods("from decimal import Decimal\nimport types\n")
        assert monitoring_security._check_import_surface(
            "monitoring_intelligence", mods, ALLOWED_IMPORTS) == ()


# ── B. Yasak importlar FAIL ──────────────────────────────────────────

class TestForbiddenImportsFail:
    @pytest.mark.parametrize("module", [
        "exchange_client", "broker_sdk", "ccxt", "requests", "httpx",
        "urllib3", "socket", "threading", "asyncio", "subprocess",
        "multiprocessing", "sqlite3", "sqlalchemy", "redis", "pickle",
        "shelve", "pathlib", "os", "sys", "dotenv", "secrets",
        "cryptography",
    ])
    def test_each_forbidden_module_flagged(self, module):
        mods = _mods(f"import {module}\n")
        assert monitoring_security._check_forbidden(
            "monitoring_api", mods, FORBIDDEN_MODULE_PREFIXES) == (
            "FORBIDDEN_MODULE:monitoring_api",)

    def test_from_import_flagged(self):
        mods = _mods("from os import path\n")
        assert monitoring_security._check_forbidden(
            "x", mods, FORBIDDEN_MODULE_PREFIXES) != ()

    def test_submodule_flagged(self):
        mods = _mods("import os.path\n")
        assert monitoring_security._check_forbidden(
            "x", mods, FORBIDDEN_MODULE_PREFIXES) != ()

    def test_function_level_import_seen(self):
        mods = _mods("def f():\n    import socket\n")
        assert "socket" in mods
        assert monitoring_security._check_forbidden(
            "x", mods, FORBIDDEN_MODULE_PREFIXES) != ()

    def test_unapproved_but_not_forbidden_flagged_by_surface(self):
        mods = _mods("import math\n")
        assert monitoring_security._check_import_surface(
            "monitoring_export", mods, ALLOWED_IMPORTS) == (
            "IMPORT_SURFACE:monitoring_export",)


# ── C/D. Bağımlılık grafiği ──────────────────────────────────────────

class TestDependencyGraph:
    def test_valid_chain_passes(self):
        for name, deps in {
            "alert_engine": "import monitoring_intelligence\n",
            "monitoring_service":
                "import alert_engine\nimport monitoring_intelligence\n",
            "monitoring_api": "import monitoring_service\n",
            "monitoring_export": "import monitoring_api\n",
        }.items():
            assert monitoring_security._check_dependencies(
                name, _mods(deps), ALLOWED_PROJECT_DEPS) == ()

    @pytest.mark.parametrize("name,source", [
        ("monitoring_intelligence", "import alert_engine\n"),
        ("monitoring_intelligence", "import monitoring_service\n"),
        ("alert_engine", "import monitoring_service\n"),
        ("alert_engine", "import monitoring_api\n"),
        ("monitoring_service", "import monitoring_api\n"),
        ("monitoring_api", "import monitoring_export\n"),
        ("monitoring_api", "import monitoring_intelligence\n"),
        ("monitoring_export", "import monitoring_service\n"),
        ("monitoring_export", "import monitoring_intelligence\n"),
        ("monitoring_export", "import alert_engine\n"),
    ])
    def test_inversions_fail(self, name, source):
        assert monitoring_security._check_dependencies(
            name, _mods(source), ALLOWED_PROJECT_DEPS) == (
            f"DEPENDENCY_GRAPH:{name}",)

    def test_service_lazy_strategy_service_allowed(self):
        assert monitoring_security._check_dependencies(
            "monitoring_service", _mods("import strategy_service\n"),
            ALLOWED_PROJECT_DEPS) == ()

    def test_export_depends_only_on_api(self):
        assert ALLOWED_PROJECT_DEPS["monitoring_export"] == frozenset(
            {"monitoring_api"})


# ── E. Meta veri sahipliği ───────────────────────────────────────────

class TestMetadataOwnership:
    def test_api_owns_uuid_datetime(self):
        assert monitoring_security._check_metadata(
            "monitoring_api",
            frozenset({"uuid", "datetime"})) == ()

    @pytest.mark.parametrize("name", [
        "monitoring_intelligence", "alert_engine",
        "monitoring_service", "monitoring_export"])
    @pytest.mark.parametrize("module", ["uuid", "datetime", "time"])
    def test_other_layers_cannot_own_metadata(self, name, module):
        assert monitoring_security._check_metadata(
            name, frozenset({module})) == (
            f"METADATA_OWNERSHIP:{name}",)

    def test_real_stack_metadata_clean(self):
        import inspect
        for name in STACK_NAMES:
            mods = monitoring_security._imported_modules(_tree(
                inspect.getsource(monitoring_security._STACK[name])))
            assert monitoring_security._check_metadata(name, mods) == ()


# ── F. Kamu API yüzeyi ───────────────────────────────────────────────

class TestPublicApi:
    @pytest.mark.parametrize("name", STACK_NAMES)
    def test_approved_surface_matches(self, name):
        module = monitoring_security._STACK[name]
        assert monitoring_security._public_names(module) == \
            APPROVED_PUBLIC_API[name]

    def test_unexpected_public_function_fails(self):
        class Fake:
            __name__ = "fake"
        fake = Fake()

        def rogue():
            pass
        rogue.__module__ = "fake"
        fake.rogue = rogue
        import types as t
        module = t.ModuleType("fake")
        module.rogue = rogue
        approved = {"fake": frozenset()}
        assert monitoring_security._check_public_api(
            "fake", module, approved) == ("PUBLIC_API_SURFACE:fake",)

    def test_missing_approved_function_fails(self):
        import types as t
        module = t.ModuleType("fake2")
        approved = {"fake2": frozenset({"expected_fn"})}
        assert monitoring_security._check_public_api(
            "fake2", module, approved) == ("PUBLIC_API_SURFACE:fake2",)

    def test_private_helpers_ignored(self):
        # Gerçek modüllerde _ önekli yardımcılar yüzeye girmez
        names = monitoring_security._public_names(monitoring_export)
        assert all(not n.startswith("_") for n in names)


# ── G. Immutability doğrulaması ──────────────────────────────────────

class TestImmutability:
    def test_stack_models_immutable(self):
        assert monitoring_security._check_immutability() == ()

    def test_deep_check_rejects_dict(self):
        assert not monitoring_security._is_deeply_immutable({"a": 1})

    def test_deep_check_rejects_list(self):
        assert not monitoring_security._is_deeply_immutable([1, 2])

    def test_deep_check_rejects_nested_mutable(self):
        from types import MappingProxyType
        assert not monitoring_security._is_deeply_immutable(
            MappingProxyType({"a": [1]}))

    def test_deep_check_accepts_immutable(self):
        from decimal import Decimal
        from types import MappingProxyType
        assert monitoring_security._is_deeply_immutable(
            MappingProxyType({"a": (Decimal("1"), None, "x", True)}))

    def test_report_itself_immutable(self):
        report = verify_monitoring_security()
        with pytest.raises(TypeError):
            report["verified"] = False
        assert isinstance(report["violations"], tuple)
        assert isinstance(report["checked_rules"], tuple)

    def test_verifier_does_not_mutate_production(self):
        before = dict(monitoring_security.APPROVED_PUBLIC_API)
        verify_monitoring_security()
        assert dict(monitoring_security.APPROVED_PUBLIC_API) == before
        # Üretim modül sözlükleri değişmedi
        assert monitoring_api.API_VERSION == 1


# ── H. Determinizm ───────────────────────────────────────────────────

class TestDeterminism:
    def test_identical_reports(self):
        assert verify_monitoring_security() == \
            verify_monitoring_security()

    def test_report_schema_fixed(self):
        report = verify_monitoring_security()
        assert tuple(report.keys()) == SECURITY_REPORT_FIELDS
        assert report["version"] == 1
        assert report["checked_rules"] == CHECKED_RULES

    def test_no_timestamp_uuid_random_in_verifier(self):
        tree = ast.parse(open("monitoring_security.py").read())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        assert not modules & {"uuid", "datetime", "time", "random"}

    def test_verified_iff_no_violations(self):
        report = verify_monitoring_security()
        assert report["verified"] is (report["violations"] == ())

    def test_violations_sorted_and_deduped(self):
        # Kural: rapor kararlı sırada, tekrarsız (sözleşme kilidi)
        report = verify_monitoring_security()
        v = report["violations"]
        assert tuple(sorted(set(v))) == v


# ── I. Sterile arızalar ──────────────────────────────────────────────

class TestSterileFailures:
    def test_internal_failure_sterile(self, monkeypatch):
        import inspect

        def boom(_):
            raise RuntimeError("/secret/path/to/code.py")
        monkeypatch.setattr(inspect, "getsource", boom)
        with pytest.raises(ValueError) as exc:
            verify_monitoring_security()
        assert str(exc.value) == ERROR_VERIFICATION
        assert "/secret" not in str(exc.value)

    def test_error_code_constant(self):
        assert ERROR_VERIFICATION == "SECURITY_VERIFICATION_FAILED"

    def test_violation_codes_contain_no_paths(self):
        # Kural kodları yol/iz içermez — yalnız RULE:layer biçimi
        for rule in CHECKED_RULES:
            assert "/" not in rule and "\\" not in rule


# ── J. Yanlış pozitif yok ────────────────────────────────────────────

class TestNoFalsePositives:
    def test_clean_stack_zero_violations(self):
        assert verify_monitoring_security()["violations"] == ()

    def test_future_import_not_flagged(self):
        mods = _mods("from __future__ import annotations\n")
        assert monitoring_security._check_forbidden(
            "x", mods, FORBIDDEN_MODULE_PREFIXES) == ()

    def test_typing_types_not_flagged(self):
        mods = _mods("from types import MappingProxyType\n"
                     "from typing import Any\n")
        assert monitoring_security._check_import_surface(
            "monitoring_intelligence",
            mods | frozenset({"decimal", "__future__"}),
            ALLOWED_IMPORTS) == ()

    def test_uuid_in_api_not_metadata_violation(self):
        assert monitoring_security._check_metadata(
            "monitoring_api", frozenset({"uuid", "datetime"})) == ()

    def test_json_in_export_allowed(self):
        mods = _mods("import json\nimport monitoring_api\n")
        assert monitoring_security._check_import_surface(
            "monitoring_export",
            mods | frozenset({"decimal", "types", "typing",
                              "__future__"}),
            ALLOWED_IMPORTS) == ()


# ── K. AST doğrulaması ───────────────────────────────────────────────

class TestAstVerification:
    def test_dangerous_call_eval_flagged(self):
        tree = _tree("eval('1+1')\n")
        assert monitoring_security._check_dangerous_calls(
            "x", tree) == ("DANGEROUS_CALL:x",)

    def test_dangerous_call_exec_flagged(self):
        tree = _tree("exec('pass')\n")
        assert monitoring_security._check_dangerous_calls(
            "x", tree) != ()

    def test_dangerous_call_open_flagged(self):
        tree = _tree("def f():\n    open('x')\n")
        assert monitoring_security._check_dangerous_calls(
            "x", tree) != ()

    def test_dunder_import_flagged(self):
        tree = _tree("__import__('os')\n")
        assert monitoring_security._check_dangerous_calls(
            "x", tree) != ()

    def test_clean_code_not_flagged(self):
        tree = _tree("def f(x):\n    return x + 1\n")
        assert monitoring_security._check_dangerous_calls(
            "x", tree) == ()

    def test_stack_has_no_dangerous_calls(self):
        import inspect
        for name in STACK_NAMES:
            tree = _tree(inspect.getsource(
                monitoring_security._STACK[name]))
            assert monitoring_security._check_dangerous_calls(
                name, tree) == ()

    def test_attribute_call_bypass_flagged(self):
        tree = _tree("import builtins\nbuiltins.__import__('os')\n")
        assert monitoring_security._check_dangerous_calls(
            "x", tree) != ()

    def test_alias_bypass_flagged(self):
        tree = _tree("e = eval\ne('1+1')\n")
        assert monitoring_security._check_dangerous_calls(
            "x", tree) != ()

    def test_attribute_open_bypass_flagged(self):
        tree = _tree("import io\nio.open('x')\n")
        assert monitoring_security._check_dangerous_calls(
            "x", tree) != ()

    def test_metadata_generation_call_flagged(self):
        source = ("def f():\n"
                  "    return {'report_id': make_id(),\n"
                  "            'observed_at': None}\n")
        assert monitoring_security._check_metadata_generation(
            "monitoring_service", _tree(source)) == (
            "METADATA_GENERATION:monitoring_service",)

    def test_metadata_generation_literal_flagged(self):
        source = "x = {'generated_at': '2026-01-01T00:00:00Z'}\n"
        assert monitoring_security._check_metadata_generation(
            "alert_engine", _tree(source)) != ()

    def test_metadata_null_and_passthrough_allowed(self):
        source = ("x = {'report_id': None,\n"
                  "     'observed_at': report['observed_at'],\n"
                  "     'generated_at': incoming}\n")
        assert monitoring_security._check_metadata_generation(
            "monitoring_export", _tree(source)) == ()

    def test_metadata_generation_allowed_in_api(self):
        source = "x = {'report_id': make_id()}\n"
        assert monitoring_security._check_metadata_generation(
            "monitoring_api", _tree(source)) == ()

    def test_real_stack_no_metadata_generation(self):
        import inspect
        for name in STACK_NAMES:
            tree = _tree(inspect.getsource(
                monitoring_security._STACK[name]))
            assert monitoring_security._check_metadata_generation(
                name, tree) == ()

    def test_project_reexport_counts_as_public(self):
        import types as t
        module = t.ModuleType("fake3")
        module.alias = alert_engine.build_alert_report  # re-export
        approved = {"fake3": frozenset()}
        assert monitoring_security._check_public_api(
            "fake3", module, approved) == ("PUBLIC_API_SURFACE:fake3",)

    def test_import_extraction_handles_both_forms(self):
        mods = _mods("import a.b\nfrom c.d import e\n")
        assert "a.b" in mods and "c.d" in mods


# ── L. Doğrulayıcının kendi güvenlik yüzeyi ─────────────────────────

class TestVerifierSecurity:
    def _imports(self):
        tree = ast.parse(open("monitoring_security.py").read())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        return modules

    def test_verifier_import_surface(self):
        allowed = {"__future__", "ast", "inspect", "decimal", "types",
                   "typing", "alert_engine", "monitoring_api",
                   "monitoring_export", "monitoring_intelligence",
                   "monitoring_service"}
        assert self._imports() <= allowed

    def test_verifier_no_network_persistence(self):
        forbidden = {"socket", "requests", "urllib", "httpx", "os",
                     "sys", "subprocess", "threading", "sqlite3",
                     "pathlib", "pickle", "secrets"}
        assert not (self._imports() & forbidden)

    def test_verifier_no_eval_exec(self):
        # AST düzeyi: doğrulayıcının kendisinde eval/exec çağrısı yok
        tree = ast.parse(open("monitoring_security.py").read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in ("eval", "exec")):
                pytest.fail("eval/exec çağrısı bulundu")

    def test_single_public_entry_point(self):
        names = monitoring_security._public_names(monitoring_security)
        assert names == frozenset({"verify_monitoring_security"})

    def test_no_float_literals(self):
        tree = ast.parse(open("monitoring_security.py").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(
                    node.value, float):
                pytest.fail("float literal bulundu")
