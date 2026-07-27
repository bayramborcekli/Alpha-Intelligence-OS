"""Mission 1900 — Agent 08 tam regresyon doğrulaması (yalnız test).

Yeni iş mantığı YOK; tamamlanmış Monitoring yığınının (Core → Alert →
Service → API → Export → Security) kararlı, deterministik ve
regresyonsuz olduğu kanıtlanır. Üretim davranışı DEĞİŞMEZ.
"""

from __future__ import annotations

import ast
import inspect
import json
from decimal import Decimal
from types import MappingProxyType

import pytest

import alert_engine
import monitoring_api
import monitoring_export
import monitoring_intelligence
import monitoring_security
import monitoring_service

STACK = MappingProxyType({
    "monitoring_intelligence": monitoring_intelligence,
    "alert_engine": alert_engine,
    "monitoring_service": monitoring_service,
    "monitoring_api": monitoring_api,
    "monitoring_export": monitoring_export,
})


def _source_tree(module):
    return ast.parse(inspect.getsource(module))


def _imports(module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(_source_tree(module)):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


def _core_input(**over):
    base = {"strategy_version": 1, "analysis_version": 1}
    base.update(over)
    return base


def _providers(freshness="fresh"):
    return {"strategy_proposal": lambda: {
        "freshness": freshness,
        "data": {"strategy_version": 1, "analysis_version": 1},
    }}


def _api_response(**over):
    analysis = monitoring_service.analyze_monitoring(_providers())
    response = monitoring_api.analyze_monitoring_api(
        {}, lambda: analysis)
    if over:
        merged = dict(response)
        merged.update(over)
        return merged
    return response


def _strip_metadata(response):
    return {k: v for k, v in dict(response).items()
            if k not in ("report_id", "observed_at", "generated_at")}


# ── B/C. Tekrarlı yürütme + determinizm ──────────────────────────────

class TestDeterminism:
    def test_monitoring_report_identical(self):
        first = monitoring_intelligence.build_monitoring_report(
            _core_input())
        second = monitoring_intelligence.build_monitoring_report(
            _core_input())
        assert dict(first) == dict(second)

    def test_alert_report_identical(self):
        report = monitoring_intelligence.build_monitoring_report(
            _core_input())
        assert dict(alert_engine.build_alert_report(report)) == \
            dict(alert_engine.build_alert_report(report))

    def test_service_analysis_identical(self):
        first = monitoring_service.analyze_monitoring(_providers())
        second = monitoring_service.analyze_monitoring(_providers())
        assert dict(first["monitoring_report"]) == \
            dict(second["monitoring_report"])
        assert dict(first["alert_report"]) == \
            dict(second["alert_report"])
        assert first["limitations"] == second["limitations"]

    def test_api_envelope_identical_excluding_metadata(self):
        assert _strip_metadata(_api_response()) == \
            _strip_metadata(_api_response())

    def test_api_report_ids_unique(self):
        assert _api_response()["report_id"] != \
            _api_response()["report_id"]

    def test_export_structure_identical(self):
        response = _api_response()
        assert monitoring_export.build_monitoring_export(response) == \
            monitoring_export.build_monitoring_export(response)

    def test_export_json_byte_identical(self):
        response = _api_response()
        first = monitoring_export.serialize_monitoring_export(response)
        second = monitoring_export.serialize_monitoring_export(response)
        assert first.encode() == second.encode()

    def test_security_report_identical(self):
        assert monitoring_security.verify_monitoring_security() == \
            monitoring_security.verify_monitoring_security()

    def test_repeated_execution_no_hidden_state(self):
        # 5 ardışık uçtan uca koşu — mantıksal çıktı sabit
        results = [_strip_metadata(_api_response()) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_stable_alert_ordering(self):
        report = monitoring_intelligence.build_monitoring_report(
            _core_input())
        alerts_a = alert_engine.build_alert_report(report)["alerts"]
        alerts_b = alert_engine.build_alert_report(report)["alerts"]
        assert [a["alert_id"] for a in alerts_a] == \
            [a["alert_id"] for a in alerts_b]

    def test_security_verified_true(self):
        report = monitoring_security.verify_monitoring_security()
        assert report["verified"] is True
        assert report["violations"] == ()


# ── D/E. Mimari + bağımlılık grafiği ─────────────────────────────────

class TestArchitecture:
    def test_core_has_no_project_imports(self):
        assert not _imports(monitoring_intelligence) & {
            "alert_engine", "monitoring_service", "monitoring_api",
            "monitoring_export", "monitoring_security"}

    def test_alert_engine_depends_only_on_core(self):
        project = _imports(alert_engine) & {
            "monitoring_intelligence", "monitoring_service",
            "monitoring_api", "monitoring_export",
            "monitoring_security"}
        assert project == {"monitoring_intelligence"}

    def test_service_depends_on_core_and_alert(self):
        project = _imports(monitoring_service) & {
            "monitoring_intelligence", "alert_engine",
            "monitoring_api", "monitoring_export",
            "monitoring_security"}
        assert project == {"monitoring_intelligence", "alert_engine"}

    def test_api_depends_only_on_service(self):
        project = _imports(monitoring_api) & {
            "monitoring_intelligence", "alert_engine",
            "monitoring_service", "monitoring_export",
            "monitoring_security"}
        assert project == {"monitoring_service"}

    def test_export_depends_only_on_api(self):
        project = _imports(monitoring_export) & {
            "monitoring_intelligence", "alert_engine",
            "monitoring_service", "monitoring_api",
            "monitoring_security"}
        assert project == {"monitoring_api"}

    def test_no_layer_imports_security(self):
        for module in STACK.values():
            assert "monitoring_security" not in _imports(module)

    def test_no_circular_dependency(self):
        order = ("monitoring_intelligence", "alert_engine",
                 "monitoring_service", "monitoring_api",
                 "monitoring_export")
        for index, name in enumerate(order):
            higher = set(order[index + 1:])
            assert not _imports(STACK[name]) & higher

    def test_security_layer_dependency_position(self):
        # Security zincirin SONUNDA: yalnız beş yığın modülüne bakar,
        # hiçbir yığın modülü Security'ye bakmaz (inversiyon yok)
        project = _imports(monitoring_security) & {
            "monitoring_intelligence", "alert_engine",
            "monitoring_service", "monitoring_api",
            "monitoring_export", "monitoring_security"}
        assert project == {"monitoring_intelligence", "alert_engine",
                           "monitoring_service", "monitoring_api",
                           "monitoring_export"}

    def test_no_circularity_including_security(self):
        assert "monitoring_security" not in _imports(
            monitoring_security)
        for module in STACK.values():
            assert "monitoring_security" not in _imports(module)

    def test_security_import_surface_readonly(self):
        allowed = {"__future__", "ast", "inspect", "decimal", "types",
                   "typing", "alert_engine", "monitoring_api",
                   "monitoring_export", "monitoring_intelligence",
                   "monitoring_service"}
        assert _imports(monitoring_security) <= allowed

    def test_security_rule_set_covers_architecture(self):
        rules = monitoring_security.verify_monitoring_security()[
            "checked_rules"]
        assert "DEPENDENCY_GRAPH" in rules
        assert "IMPORT_SURFACE" in rules


# ── F/G. Import yüzeyi + güvenlik ────────────────────────────────────

class TestSecuritySurface:
    FORBIDDEN = {"os", "sys", "socket", "requests", "httpx", "urllib3",
                 "threading", "asyncio", "subprocess",
                 "multiprocessing", "sqlite3", "sqlalchemy", "redis",
                 "pickle", "shelve", "pathlib", "dotenv", "secrets",
                 "cryptography", "ccxt"}

    @pytest.mark.parametrize("name", tuple(STACK))
    def test_no_forbidden_imports(self, name):
        roots = {m.split(".")[0] for m in _imports(STACK[name])}
        assert not roots & self.FORBIDDEN

    @pytest.mark.parametrize("name", tuple(STACK))
    def test_no_exchange_or_broker_imports(self, name):
        for module in _imports(STACK[name]):
            root = module.split(".")[0]
            assert not root.startswith("exchange")
            assert not root.startswith("broker")

    @pytest.mark.parametrize("name", tuple(STACK))
    def test_no_dangerous_calls(self, name):
        for node in ast.walk(_source_tree(STACK[name])):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "open", "__import__", "compile")

    @pytest.mark.parametrize("name", tuple(STACK))
    def test_no_env_or_secret_access(self, name):
        text = inspect.getsource(STACK[name])
        for token in ("environ", "getenv", "BINANCE", "API_KEY",
                      "SECRET_KEY"):
            assert token not in text

    @pytest.mark.parametrize("name", tuple(STACK))
    def test_no_float_literals(self, name):
        for node in ast.walk(_source_tree(STACK[name])):
            if isinstance(node, ast.Constant) and isinstance(
                    node.value, float):
                pytest.fail(f"float literal: {name}")

    def test_security_layer_no_forbidden_imports(self):
        roots = {m.split(".")[0]
                 for m in _imports(monitoring_security)}
        assert not roots & self.FORBIDDEN
        for module in _imports(monitoring_security):
            root = module.split(".")[0]
            assert not root.startswith("exchange")
            assert not root.startswith("broker")

    def test_security_layer_no_dangerous_calls(self):
        for node in ast.walk(_source_tree(monitoring_security)):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "open", "__import__", "compile")

    def test_security_layer_no_metadata_modules(self):
        roots = {m.split(".")[0]
                 for m in _imports(monitoring_security)}
        assert not roots & {"uuid", "datetime", "time", "random"}

    def test_verifier_confirms_full_surface(self):
        report = monitoring_security.verify_monitoring_security()
        assert "FORBIDDEN_MODULES" in report["checked_rules"]
        assert "DANGEROUS_CALLS" in report["checked_rules"]


# ── J. Meta veri sahipliği ───────────────────────────────────────────

class TestMetadataOwnership:
    @pytest.mark.parametrize("name", (
        "monitoring_intelligence", "alert_engine",
        "monitoring_service", "monitoring_export"))
    def test_no_uuid_datetime_outside_api(self, name):
        roots = {m.split(".")[0] for m in _imports(STACK[name])}
        assert not roots & {"uuid", "datetime", "time"}

    def test_api_owns_uuid_datetime(self):
        roots = {m.split(".")[0] for m in _imports(monitoring_api)}
        assert {"uuid", "datetime"} <= roots

    def test_core_report_metadata_null(self):
        report = monitoring_intelligence.build_monitoring_report(
            _core_input())
        assert report["report_id"] is None
        assert report["observed_at"] is None

    def test_alert_report_metadata_null(self):
        report = monitoring_intelligence.build_monitoring_report(
            _core_input())
        alert_report = alert_engine.build_alert_report(report)
        assert alert_report["generated_at"] is None
        assert alert_report["report_id"] is None

    def test_api_fills_metadata(self):
        response = _api_response()
        assert response["report_id"]
        assert response["observed_at"] == response["generated_at"]
        assert response["observed_at"].endswith("+00:00")

    def test_export_preserves_metadata_exactly(self):
        response = _api_response()
        export = monitoring_export.build_monitoring_export(response)
        for key in ("report_id", "observed_at", "generated_at"):
            assert export[key] == response[key]


# ── H. Kamu API yüzeyi ───────────────────────────────────────────────

class TestPublicApi:
    EXPECTED = {
        "monitoring_intelligence": {"build_monitoring_report"},
        "alert_engine": {"build_alert_report"},
        "monitoring_service": {
            "analyze_monitoring", "build_default_monitoring_providers",
            "MonitoringService"},
        "monitoring_api": {"analyze_monitoring_api"},
        "monitoring_export": {
            "build_monitoring_export", "serialize_monitoring_export"},
    }

    @pytest.mark.parametrize("name", tuple(STACK))
    def test_public_surface_unchanged(self, name):
        assert monitoring_security._public_names(STACK[name]) == \
            self.EXPECTED[name]

    def test_security_single_entry_point(self):
        assert monitoring_security._public_names(
            monitoring_security) == {"verify_monitoring_security"}

    def test_verifier_locks_public_api(self):
        assert "PUBLIC_API_SURFACE" in \
            monitoring_security.verify_monitoring_security()[
                "checked_rules"]


# ── I. Export doğrulaması ────────────────────────────────────────────

class TestExport:
    def test_canonical_json_rules(self):
        text = monitoring_export.serialize_monitoring_export(
            _api_response())
        assert "\n" not in text and ": " not in text and ", " not in text
        parsed = json.loads(text)
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_root_schema_fixed(self):
        export = monitoring_export.build_monitoring_export(
            _api_response())
        assert tuple(export.keys()) == monitoring_export.EXPORT_FIELDS

    def test_decimal_string_policy(self):
        response = _api_response()
        export = monitoring_export.build_monitoring_export(response)
        monitoring = export["monitoring"]
        for key in ("success_rate", "average_return",
                    "maximum_drawdown", "confidence_accuracy"):
            value = monitoring[key]
            assert value is None or isinstance(value, str)

    def test_null_preservation(self):
        response = _api_response()
        export = monitoring_export.build_monitoring_export(response)
        assert export["monitoring"]["report_id"] is None
        assert export["monitoring"]["observed_at"] is None

    def test_failed_export_behavior(self):
        response = {
            "api_version": 1,
            "report_id": "00000000-0000-4000-8000-000000000000",
            "observed_at": "2026-01-01T00:00:00+00:00",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "monitoring_analysis": None,
            "status": "FAILED",
            "limitations": ("MONITORING_ANALYSIS_ERROR",),
        }
        export = monitoring_export.build_monitoring_export(response)
        assert export["monitoring"] is None
        assert export["alerts"] == () and export["sources"] == ()

    def test_utf8_preserved(self):
        text = monitoring_export.serialize_monitoring_export(
            _api_response())
        assert "\\u" not in text.replace("\\u0000", "")

    def test_status_passthrough_not_recomputed(self):
        response = _api_response(status="PARTIAL")
        export = monitoring_export.build_monitoring_export(response)
        assert export["status"] == "PARTIAL"


# ── K/M. Immutability + üretim mutasyonsuzluğu ───────────────────────

class TestImmutability:
    def test_core_report_immutable(self):
        report = monitoring_intelligence.build_monitoring_report(
            _core_input())
        with pytest.raises(TypeError):
            report["health_status"] = "X"

    def test_alert_report_immutable(self):
        report = monitoring_intelligence.build_monitoring_report(
            _core_input())
        alert_report = alert_engine.build_alert_report(report)
        with pytest.raises(TypeError):
            alert_report["alert_count"] = 99

    def test_analysis_immutable(self):
        analysis = monitoring_service.analyze_monitoring(_providers())
        with pytest.raises(TypeError):
            analysis["limitations"] = ()

    def test_api_response_immutable(self):
        response = _api_response()
        with pytest.raises(TypeError):
            response["status"] = "X"

    def test_export_immutable(self):
        export = monitoring_export.build_monitoring_export(
            _api_response())
        with pytest.raises(TypeError):
            export["monitoring"] = None

    def test_security_report_immutable(self):
        report = monitoring_security.verify_monitoring_security()
        with pytest.raises(TypeError):
            report["verified"] = False

    def test_repeated_runs_do_not_mutate_constants(self):
        before = (monitoring_intelligence.REPORT_FIELDS,
                  alert_engine.ALERT_REPORT_FIELDS,
                  monitoring_service.ANALYSIS_FIELDS,
                  monitoring_api.API_RESPONSE_FIELDS,
                  monitoring_export.EXPORT_FIELDS)
        for _ in range(3):
            monitoring_export.serialize_monitoring_export(
                _api_response())
            monitoring_security.verify_monitoring_security()
        assert before == (monitoring_intelligence.REPORT_FIELDS,
                          alert_engine.ALERT_REPORT_FIELDS,
                          monitoring_service.ANALYSIS_FIELDS,
                          monitoring_api.API_RESPONSE_FIELDS,
                          monitoring_export.EXPORT_FIELDS)

    def test_export_does_not_mutate_input(self):
        response = _api_response()
        snapshot = _strip_metadata(response)
        monitoring_export.serialize_monitoring_export(response)
        assert _strip_metadata(response) == snapshot


# ── L. Sterile hata modeli ───────────────────────────────────────────

class TestSterileErrors:
    def test_core_sterile(self):
        with pytest.raises(ValueError) as exc:
            monitoring_intelligence.build_monitoring_report(None)
        assert str(exc.value) == "INVALID_INPUT"

    def test_alert_engine_sterile(self):
        with pytest.raises(ValueError) as exc:
            alert_engine.build_alert_report(None)
        assert str(exc.value) == "INVALID_MONITORING_REPORT"

    def test_api_failure_sterile(self):
        def boom():
            raise RuntimeError("/gizli/yol/kod.py BINANCE_API_KEY")
        response = monitoring_api.analyze_monitoring_api({}, boom)
        assert response["status"] == "FAILED"
        text = json.dumps(
            {k: v for k, v in dict(response).items()
             if k != "monitoring_analysis"})
        assert "/gizli" not in text and "BINANCE" not in text

    def test_api_request_error_sterile(self):
        with pytest.raises(ValueError) as exc:
            monitoring_api.analyze_monitoring_api({"api_version": 99})
        assert str(exc.value) == "UNSUPPORTED_API_VERSION"

    def test_export_error_sterile(self):
        with pytest.raises(ValueError) as exc:
            monitoring_export.build_monitoring_export(
                {"payload": "/etc/passwd"})
        assert str(exc.value) == "INVALID_MONITORING_EXPORT_INPUT"
        assert "passwd" not in str(exc.value)

    def test_security_error_code_fixed(self):
        assert monitoring_security.ERROR_VERIFICATION == \
            "SECURITY_VERIFICATION_FAILED"

    def test_provider_failure_no_leak(self):
        def boom():
            raise RuntimeError("raw provider payload {secret}")
        analysis = monitoring_service.analyze_monitoring(
            {"strategy_proposal": boom})
        text = json.dumps({
            "sources": {k: dict(v)
                        for k, v in analysis["sources"].items()},
            "limitations": list(analysis["limitations"]),
        })
        assert "secret" not in text and "payload" not in text


# ── N. Yanlış pozitif yok (uçtan uca akış) ───────────────────────────

class TestEndToEnd:
    def test_full_chain_success_path(self):
        response = _api_response()
        assert response["status"] in ("SUCCESS", "PARTIAL")
        export = monitoring_export.build_monitoring_export(response)
        assert export["api_version"] == 1
        parsed = json.loads(
            monitoring_export.serialize_monitoring_export(response))
        assert parsed["report_id"] == response["report_id"]

    def test_full_chain_stale_path(self):
        analysis = monitoring_service.analyze_monitoring(
            _providers("stale"))
        response = monitoring_api.analyze_monitoring_api(
            {}, lambda: analysis)
        assert response["status"] == "PARTIAL"
        export = monitoring_export.build_monitoring_export(response)
        assert "OBSERVATIONS_STALE" in \
            export["monitoring"]["limitations"] or \
            "OBSERVATIONS_STALE" in dict(
                analysis)["limitations"]

    def test_full_chain_failed_path(self):
        def boom():
            raise RuntimeError("x")
        response = monitoring_api.analyze_monitoring_api({}, boom)
        export = monitoring_export.build_monitoring_export(response)
        assert export["status"] == "FAILED"
        assert export["monitoring"] is None

    def test_health_status_closed_set(self):
        report = monitoring_intelligence.build_monitoring_report(
            _core_input())
        assert report["health_status"] in (
            "UNKNOWN", "CRITICAL", "DEGRADED", "HEALTHY")

    def test_alert_codes_closed_set(self):
        report = monitoring_intelligence.build_monitoring_report(
            _core_input())
        alert_report = alert_engine.build_alert_report(report)
        for alert in alert_report["alerts"]:
            assert alert["code"] in alert_engine.ALERT_CODES

    def test_source_states_closed_set(self):
        analysis = monitoring_service.analyze_monitoring(_providers())
        for meta in analysis["sources"].values():
            assert meta["status"] in monitoring_service.SOURCE_STATES

    def test_decimal_thresholds_unchanged(self):
        assert monitoring_intelligence.SUCCESS_CRITICAL_PCT == \
            Decimal("25")
        assert monitoring_intelligence.SUCCESS_DEGRADED_PCT == \
            Decimal("50")
        assert monitoring_intelligence.DRAWDOWN_CRITICAL_PCT == \
            Decimal("50")
        assert monitoring_intelligence.DRAWDOWN_DEGRADED_PCT == \
            Decimal("25")

    def test_export_schema_frozen(self):
        assert monitoring_export.EXPORT_FIELDS == (
            "api_version", "report_id", "observed_at", "generated_at",
            "status", "limitations", "monitoring", "alerts", "sources")

    def test_api_schema_frozen(self):
        assert monitoring_api.API_RESPONSE_FIELDS == (
            "api_version", "report_id", "observed_at", "generated_at",
            "monitoring_analysis", "status", "limitations")

    def test_security_report_schema_frozen(self):
        assert monitoring_security.SECURITY_REPORT_FIELDS == (
            "verified", "violations", "checked_rules", "version")
