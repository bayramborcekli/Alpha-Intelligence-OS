"""Mission 1900 — Agent 05 Monitoring API testleri.

Kapsam: istek doğrulama, servis çağrısı, meta veri, durum
normalizasyonu, zarf, hatalar, güvenlik.
"""

from __future__ import annotations

import ast
import re
import uuid as uuid_module
from pathlib import Path
from types import MappingProxyType

import pytest

import monitoring_api as api
import monitoring_service as ms
from monitoring_api import analyze_monitoring_api

MODULE_SOURCE = Path(api.__file__).read_text(encoding="utf-8")
MODULE_AST = ast.parse(MODULE_SOURCE)

RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)$")


def mk_proposal(**overrides):
    base = {
        "strategy_version": 1, "advisory_only": True, "read_only": True,
        "portfolio_analysis_version": 1, "confidence": "80.00",
        "data_quality": "OK", "market_regime": "UNKNOWN",
        "overall_risk": "LOW", "recommendations": [], "warnings": [],
        "limitations": [],
    }
    base.update(overrides)
    return base


def analysis_supplier(freshness="fresh", proposal=None):
    providers = {"strategy_proposal":
                 lambda: {"freshness": freshness,
                          "data": proposal or mk_proposal()}}
    return lambda: ms.analyze_monitoring(providers)


def call(**kwargs):
    kwargs.setdefault("analysis_supplier", analysis_supplier())
    return analyze_monitoring_api(kwargs.pop("request", {}), **kwargs)


# ── A. İstek doğrulama ───────────────────────────────────────────────

class TestRequestValidation:
    def test_valid_request(self):
        assert call(request={"api_version": 1,
                             "provider": "default"})["status"] == "SUCCESS"

    def test_empty_and_none_request_valid(self):
        assert call(request={})["status"] == "SUCCESS"
        assert analyze_monitoring_api(
            None, analysis_supplier())["status"] == "SUCCESS"

    def test_invalid_request_type(self):
        with pytest.raises(ValueError, match="^INVALID_API_REQUEST$"):
            analyze_monitoring_api([1], analysis_supplier())

    def test_unknown_field_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_API_REQUEST$"):
            call(request={"strategy_proposal": {}})

    def test_provider_payload_injection_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_API_REQUEST$"):
            call(request={"recommendations": []})

    def test_unsupported_version(self):
        for bad in (0, 2, "1", True, None):
            with pytest.raises(ValueError,
                               match="^UNSUPPORTED_API_VERSION$"):
                call(request={"api_version": bad})

    def test_unknown_provider(self):
        with pytest.raises(ValueError, match="^UNKNOWN_PROVIDER$"):
            call(request={"provider": "live_exchange"})

    def test_request_fields_closed(self):
        assert api.REQUEST_FIELDS == ("api_version", "provider")


# ── B. Servis çağrısı ────────────────────────────────────────────────

class TestServiceInvocation:
    def test_exactly_one_call(self):
        calls = []
        supplier = analysis_supplier()

        def counting():
            calls.append(1)
            return supplier()

        analyze_monitoring_api({}, counting)
        assert len(calls) == 1

    def test_no_retry_on_failure(self):
        calls = []

        def failing():
            calls.append(1)
            raise RuntimeError("iç")

        response = analyze_monitoring_api({}, failing)
        assert len(calls) == 1
        assert response["status"] == "FAILED"

    def test_no_duplicate_across_calls(self):
        calls = []
        supplier = analysis_supplier()

        def counting():
            calls.append(1)
            return supplier()

        analyze_monitoring_api({}, counting)
        analyze_monitoring_api({}, counting)
        assert len(calls) == 2  # çağrı başına bir; önbellek yok

    def test_validation_failure_prevents_service_call(self):
        calls = []

        def counting():
            calls.append(1)

        with pytest.raises(ValueError):
            analyze_monitoring_api({"api_version": 9}, counting)
        assert calls == []


# ── C. Meta veri ─────────────────────────────────────────────────────

class TestMetadata:
    def test_report_id_is_uuid4(self):
        response = call()
        parsed = uuid_module.UUID(response["report_id"])
        assert parsed.version == 4

    def test_timestamps_rfc3339_utc(self):
        response = call()
        assert RFC3339.match(response["observed_at"])
        assert RFC3339.match(response["generated_at"])

    def test_report_id_unique_per_call(self):
        ids = {call()["report_id"] for _ in range(5)}
        assert len(ids) == 5

    def test_metadata_only_at_api_layer(self):
        response = call()
        analysis = response["monitoring_analysis"]
        assert analysis["monitoring_report"]["report_id"] is None
        assert analysis["monitoring_report"]["observed_at"] is None
        assert analysis["alert_report"]["generated_at"] is None

    def test_lower_layers_have_no_clock(self):
        for module in (ms, ):
            source = Path(module.__file__).read_text(encoding="utf-8")
            assert "datetime" not in source
            assert "uuid" not in source


# ── D. Durum ─────────────────────────────────────────────────────────

class TestStatus:
    def test_success(self):
        assert call()["status"] == "SUCCESS"

    def test_partial_on_stale(self):
        response = analyze_monitoring_api(
            {}, analysis_supplier(freshness="stale"))
        assert response["status"] == "PARTIAL"

    def test_partial_on_unavailable_provider(self):
        supplier = lambda: ms.analyze_monitoring({})  # noqa: E731
        assert analyze_monitoring_api({}, supplier)["status"] == "PARTIAL"

    def test_failed_on_service_error(self):
        def failing():
            raise ValueError("MONITORING_ANALYSIS_ERROR")

        response = analyze_monitoring_api({}, failing)
        assert response["status"] == "FAILED"
        assert response["monitoring_analysis"] is None
        assert response["limitations"] == ("MONITORING_ANALYSIS_ERROR",)

    def test_failed_on_malformed_analysis(self):
        response = analyze_monitoring_api({}, lambda: {"garip": 1})
        assert response["status"] == "FAILED"

    def test_failed_on_key_correct_but_malformed_analysis(self):
        # Anahtarlar doğru ama gövde bozuk: sterile FAILED, fırlatma YOK
        for bad in (
            {"monitoring_report": {}, "alert_report": {},
             "sources": None, "limitations": ()},
            {"monitoring_report": {}, "alert_report": {},
             "sources": {"x": {"status": "WEIRD"}}, "limitations": ()},
            {"monitoring_report": {}, "alert_report": {},
             "sources": {"x": None}, "limitations": ()},
            {"monitoring_report": {}, "alert_report": {},
             "sources": {}, "limitations": "NOPE"},
        ):
            response = analyze_monitoring_api({}, lambda b=bad: b)
            assert response["status"] == "FAILED"
            assert response["monitoring_analysis"] is None
            assert response["limitations"] == (
                "MONITORING_ANALYSIS_ERROR",)

    def test_status_independent_from_health(self):
        # UNAVAILABLE sağlayıcı → health UNKNOWN ama API durumu PARTIAL
        supplier = lambda: ms.analyze_monitoring({})  # noqa: E731
        response = analyze_monitoring_api({}, supplier)
        report = response["monitoring_analysis"]["monitoring_report"]
        assert report["health_status"] == "UNKNOWN"
        assert response["status"] == "PARTIAL"
        # CRITICAL sağlık bile SUCCESS durumunu etkilemez (bağımsızlık)
        assert call()["status"] == "SUCCESS"

    def test_statuses_closed(self):
        assert api.API_STATUSES == ("SUCCESS", "PARTIAL", "FAILED")


# ── E. Zarf ──────────────────────────────────────────────────────────

class TestEnvelope:
    def test_exact_schema(self):
        assert tuple(call().keys()) == api.API_RESPONSE_FIELDS

    def test_immutable_response(self):
        response = call()
        assert isinstance(response, MappingProxyType)
        with pytest.raises(TypeError):
            response["status"] = "HACKED"

    def test_analysis_passed_through_unchanged(self):
        supplier = analysis_supplier()
        analysis = supplier()
        response = analyze_monitoring_api({}, lambda: analysis)
        assert response["monitoring_analysis"] is analysis
        assert dict(analysis["monitoring_report"]) == dict(
            supplier()["monitoring_report"])

    def test_no_extra_fields(self):
        response = call()
        assert len(response) == len(api.API_RESPONSE_FIELDS)

    def test_analysis_still_immutable_via_response(self):
        response = call()
        with pytest.raises(TypeError):
            response["monitoring_analysis"]["sources"] = {}


# ── F. Hatalar ───────────────────────────────────────────────────────

class TestErrors:
    def test_error_codes_closed(self):
        assert api.API_ERROR_CODES == (
            "INVALID_API_REQUEST", "UNSUPPORTED_API_VERSION",
            "UNKNOWN_PROVIDER", "MONITORING_ANALYSIS_ERROR")

    def test_sterile_service_failure_no_leak(self):
        def failing():
            raise RuntimeError("SECRET=xyz /home/runner/app.py Traceback")

        response = analyze_monitoring_api({}, failing)
        text = repr(dict(response))
        for leak in ("SECRET=xyz", "/home/runner", "Traceback"):
            assert leak not in text

    def test_provider_failure_still_partial_not_crash(self):
        def broken_provider():
            raise RuntimeError("sağlayıcı çöktü")

        supplier = lambda: ms.analyze_monitoring(  # noqa: E731
            {"strategy_proposal": broken_provider})
        response = analyze_monitoring_api({}, supplier)
        assert response["status"] == "PARTIAL"

    def test_validation_errors_sterile(self):
        for request in (5, {"x": 1}, {"api_version": 3},
                        {"provider": "??"}):
            try:
                analyze_monitoring_api(request, analysis_supplier())
            except ValueError as exc:
                assert str(exc) in api.API_ERROR_CODES
            else:  # pragma: no cover
                pytest.fail("reddetmedi")


# ── G. Güvenlik / determinizm ────────────────────────────────────────

class TestSecurity:
    def test_import_surface(self):
        allowed = {"__future__", "uuid", "datetime", "types", "typing",
                   "monitoring_service"}
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in allowed
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] in allowed

    def test_no_forbidden_tokens(self):
        forbidden = ("requests", "socket", "urllib", "smtplib",
                     "subprocess", "threading", "sched", "sqlite3",
                     "binance", "ccxt", "open(", "eval(", "exec(",
                     "os.environ", "getenv", "append_snapshot",
                     "random.")
        lowered = MODULE_SOURCE.lower()
        for token in forbidden:
            assert token not in lowered, token

    def test_utc_only(self):
        assert "timezone.utc" in MODULE_SOURCE
        assert "astimezone()" not in MODULE_SOURCE

    def test_no_float_literals(self):
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    def test_uuid_only_for_report_id(self):
        # uuid çağrısı modülde tek yerde (report_id) geçer
        assert MODULE_SOURCE.count("uuid.uuid4()") == 1

    def test_response_fields_frozen(self):
        assert api.API_RESPONSE_FIELDS == (
            "api_version", "report_id", "observed_at", "generated_at",
            "monitoring_analysis", "status", "limitations")

    def test_supported_lists_frozen(self):
        assert api.SUPPORTED_API_VERSIONS == (1,)
        assert api.SUPPORTED_PROVIDERS == ("default",)
