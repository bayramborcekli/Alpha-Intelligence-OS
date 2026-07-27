"""Mission 1900 — Agent 06 Monitoring Export testleri.

Kapsam: geçerli dışa aktarma (SUCCESS/PARTIAL/FAILED, kök şema),
monitoring/alert/source dönüşümleri, meta veri korunumu, kanonik JSON,
derin immutability, bozuk girdi sterilitesi, güvenlik yüzeyi.
"""

from __future__ import annotations

import ast
import copy
import json
from decimal import Decimal
from types import MappingProxyType

import pytest

import monitoring_export
from monitoring_export import (
    ERROR_INVALID_EXPORT_INPUT,
    EXPORT_FIELDS,
    build_monitoring_export,
    serialize_monitoring_export,
)

import alert_engine
import monitoring_api
import monitoring_intelligence
import monitoring_service


# ── Yardımcılar ──────────────────────────────────────────────────────

def _make_response(*, status="SUCCESS", analysis="auto",
                   limitations=(), **overrides):
    if analysis == "auto":
        analysis = _make_analysis()
    response = {
        "api_version": 1,
        "report_id": "11111111-2222-4333-8444-555555555555",
        "observed_at": "2026-07-27T00:00:00+00:00",
        "generated_at": "2026-07-27T00:00:00+00:00",
        "monitoring_analysis": analysis,
        "status": status,
        "limitations": tuple(limitations),
    }
    response.update(overrides)
    return response


def _make_analysis(*, source_status="COMPLETE", source_code=None,
                   limitations=(), alerts=None, report_over=None):
    report = dict.fromkeys(monitoring_export.MONITORING_REPORT_FIELDS)
    report.update({
        "monitoring_version": 1,
        "strategy_version": 1,
        "analysis_version": 1,
        "observation_window": 30,
        "data_quality": "OK",
        "recommendation_count": 10,
        "evaluated_count": 8,
        "success_rate": Decimal("62.50"),
        "average_return": Decimal("1.2500"),
        "maximum_drawdown": Decimal("12.3400"),
        "confidence_accuracy": None,
        "market_regime": "trending",
        "health_status": "HEALTHY",
        "alerts": (),
        "limitations": (),
    })
    if report_over:
        report.update(report_over)
    if alerts is None:
        alerts = ()
    alert_report = {
        "alert_version": 1,
        "monitoring_version": 1,
        "report_id": None,
        "generated_at": None,
        "health_status": report["health_status"],
        "alert_count": len(alerts),
        "highest_severity": (alerts[0].get("severity")
                             if alerts else None),
        "alerts": tuple(alerts),
        "limitations": (),
    }
    return {
        "monitoring_report": MappingProxyType(report),
        "alert_report": MappingProxyType(alert_report),
        "sources": MappingProxyType({
            "strategy_proposal": MappingProxyType(
                {"status": source_status, "code": source_code}),
        }),
        "limitations": tuple(limitations),
    }


def _make_alert(alert_id="A1", code="MONITORING_DEGRADED",
                severity="WARNING"):
    return MappingProxyType({
        "alert_id": alert_id,
        "severity": severity,
        "code": code,
        "title": "İzleme durumu bozulmuş",
        "description": "Açıklama metni — Türkçe karakter: ğüşiöç",
        "affected_component": "monitoring",
        "trigger_reason": "HEALTH_STATUS_DEGRADED",
        "recommended_action": "REVIEW_STRATEGY_PERFORMANCE",
    })


# ── A. Geçerli dışa aktarma ──────────────────────────────────────────

class TestValidExport:
    def test_success_response_root_schema(self):
        export = build_monitoring_export(_make_response())
        assert tuple(export.keys()) == EXPORT_FIELDS
        assert export["status"] == "SUCCESS"

    def test_partial_response(self):
        response = _make_response(
            status="PARTIAL",
            analysis=_make_analysis(source_status="PARTIAL",
                                    source_code="PROVIDER_FAILED",
                                    limitations=("OBSERVATIONS_STALE",)))
        export = build_monitoring_export(response)
        assert export["status"] == "PARTIAL"
        assert export["sources"][0]["status"] == "PARTIAL"

    def test_failed_response_null_analysis(self):
        response = _make_response(
            status="FAILED", analysis=None,
            limitations=("MONITORING_ANALYSIS_ERROR",))
        export = build_monitoring_export(response)
        assert export["monitoring"] is None
        assert export["alerts"] == ()
        assert export["sources"] == ()
        assert export["limitations"] == ("MONITORING_ANALYSIS_ERROR",)

    def test_no_extra_root_fields(self):
        export = build_monitoring_export(_make_response())
        assert set(export.keys()) == set(EXPORT_FIELDS)
        assert len(export) == 9


# ── B. Monitoring dönüşümü ───────────────────────────────────────────

class TestMonitoringConversion:
    def test_fields_preserved(self):
        export = build_monitoring_export(_make_response())
        assert tuple(export["monitoring"].keys()) == \
            monitoring_export.MONITORING_REPORT_FIELDS
        assert export["monitoring"]["health_status"] == "HEALTHY"
        assert export["monitoring"]["market_regime"] == "trending"

    def test_decimal_to_string(self):
        export = build_monitoring_export(_make_response())
        assert export["monitoring"]["success_rate"] == "62.50"
        assert export["monitoring"]["maximum_drawdown"] == "12.3400"

    def test_decimal_precision_not_normalized(self):
        response = _make_response(analysis=_make_analysis(
            report_over={"average_return": Decimal("0.0000")}))
        export = build_monitoring_export(response)
        assert export["monitoring"]["average_return"] == "0.0000"

    def test_decimal_no_scientific_notation(self):
        response = _make_response(analysis=_make_analysis(
            report_over={"average_return": Decimal("1E+2")}))
        export = build_monitoring_export(response)
        assert export["monitoring"]["average_return"] == "100"

    def test_null_preserved(self):
        export = build_monitoring_export(_make_response())
        assert export["monitoring"]["confidence_accuracy"] is None
        assert export["monitoring"]["report_id"] is None
        assert export["monitoring"]["observed_at"] is None

    def test_tuple_converted(self):
        response = _make_response(analysis=_make_analysis(
            report_over={"limitations": ("NO_OBSERVATIONS",)}))
        export = build_monitoring_export(response)
        assert export["monitoring"]["limitations"] == ("NO_OBSERVATIONS",)
        assert isinstance(export["monitoring"]["limitations"], tuple)

    def test_no_health_recomputation(self):
        # Sağlık tutarsız görünse bile export AYNEN taşır.
        response = _make_response(analysis=_make_analysis(
            report_over={"health_status": "CRITICAL",
                         "success_rate": Decimal("99.00")}))
        export = build_monitoring_export(response)
        assert export["monitoring"]["health_status"] == "CRITICAL"
        assert export["monitoring"]["success_rate"] == "99.00"


# ── C. Uyarı dönüşümü ────────────────────────────────────────────────

class TestAlertConversion:
    def test_order_and_ids_preserved(self):
        alerts = (_make_alert("A1", "MONITORING_CRITICAL", "CRITICAL"),
                  _make_alert("A2", "LOW_SUCCESS_RATE", "WARNING"),
                  _make_alert("A3", "HIGH_DRAWDOWN", "WARNING"))
        response = _make_response(analysis=_make_analysis(alerts=alerts))
        export = build_monitoring_export(response)
        assert [a["alert_id"] for a in export["alerts"]] == \
            ["A1", "A2", "A3"]
        assert [a["code"] for a in export["alerts"]] == \
            ["MONITORING_CRITICAL", "LOW_SUCCESS_RATE", "HIGH_DRAWDOWN"]

    def test_severity_and_action_preserved(self):
        alerts = (_make_alert("A1", "MONITORING_CRITICAL", "CRITICAL"),)
        export = build_monitoring_export(
            _make_response(analysis=_make_analysis(alerts=alerts)))
        alert = export["alerts"][0]
        assert alert["severity"] == "CRITICAL"
        assert alert["recommended_action"] == \
            "REVIEW_STRATEGY_PERFORMANCE"
        assert alert["title"] and alert["description"]

    def test_no_alert_generation_or_suppression(self):
        export = build_monitoring_export(_make_response())
        assert export["alerts"] == ()  # sıfır uyarı sıfır kalır

    def test_no_renumbering(self):
        alerts = (_make_alert("A7"),)  # sıradışı ID aynen kalır
        export = build_monitoring_export(
            _make_response(analysis=_make_analysis(alerts=alerts)))
        assert export["alerts"][0]["alert_id"] == "A7"


# ── D. Kaynak dönüşümü ───────────────────────────────────────────────

class TestSourceConversion:
    @pytest.mark.parametrize("status,code", [
        ("COMPLETE", None),
        ("PARTIAL", None),
        ("UNAVAILABLE", "PROVIDER_FAILED"),
        ("UNAVAILABLE", "INVALID_PROVIDER_RESULT"),
    ])
    def test_status_and_code_preserved(self, status, code):
        response = _make_response(
            status="PARTIAL" if status != "COMPLETE" else "SUCCESS",
            analysis=_make_analysis(source_status=status,
                                    source_code=code))
        export = build_monitoring_export(response)
        source = export["sources"][0]
        assert source["name"] == "strategy_proposal"
        assert source["status"] == status
        assert source["code"] == code

    def test_source_order_preserved(self):
        analysis = _make_analysis()
        analysis["sources"] = MappingProxyType({
            "b_source": MappingProxyType(
                {"status": "COMPLETE", "code": None}),
            "a_source": MappingProxyType(
                {"status": "PARTIAL", "code": None}),
        })
        export = build_monitoring_export(
            _make_response(status="PARTIAL", analysis=analysis))
        assert [s["name"] for s in export["sources"]] == \
            ["b_source", "a_source"]

    def test_no_provider_payload_leakage(self):
        export = build_monitoring_export(_make_response())
        for source in export["sources"]:
            assert tuple(source.keys()) == ("name", "status", "code")


# ── E. Meta veri ─────────────────────────────────────────────────────

class TestMetadata:
    def test_metadata_preserved_exactly(self):
        response = _make_response()
        export = build_monitoring_export(response)
        for key in ("report_id", "observed_at", "generated_at",
                    "api_version", "status"):
            assert export[key] == response[key]

    def test_root_limitations_preserved_exactly(self):
        response = _make_response(status="PARTIAL",
                                  limitations=("B_LIMIT", "A_LIMIT"))
        export = build_monitoring_export(response)
        assert export["limitations"] == ("B_LIMIT", "A_LIMIT")  # sırasız

    def test_no_uuid_or_clock_in_module(self):
        source = ast.parse(open("monitoring_export.py").read())
        modules = set()
        for node in ast.walk(source):
            if isinstance(node, ast.Import):
                modules |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        assert "uuid" not in modules
        assert "datetime" not in modules
        assert "time" not in modules
        assert "random" not in modules

    def test_two_builds_identical(self):
        response = _make_response()
        assert build_monitoring_export(response) == \
            build_monitoring_export(response)


# ── F. Kanonik JSON ──────────────────────────────────────────────────

class TestCanonicalJson:
    def test_sorted_keys_compact(self):
        text = serialize_monitoring_export(_make_response())
        parsed = json.loads(text)
        assert list(parsed.keys()) == sorted(parsed.keys())
        assert ": " not in text and ", " not in text

    def test_no_newline_no_indent(self):
        text = serialize_monitoring_export(_make_response())
        assert "\n" not in text
        assert not text.endswith("\n")

    def test_ensure_ascii_false(self):
        alerts = (_make_alert(),)
        text = serialize_monitoring_export(
            _make_response(analysis=_make_analysis(alerts=alerts)))
        assert "ğüşiöç" in text  # UTF-8 kaçışsız

    def test_byte_identical(self):
        response = _make_response()
        first = serialize_monitoring_export(response)
        second = serialize_monitoring_export(response)
        assert first.encode("utf-8") == second.encode("utf-8")

    def test_decimal_serialized_as_string(self):
        text = serialize_monitoring_export(_make_response())
        parsed = json.loads(text)
        assert parsed["monitoring"]["success_rate"] == "62.50"
        assert isinstance(parsed["monitoring"]["success_rate"], str)

    def test_nan_decimal_rejected(self):
        response = _make_response(analysis=_make_analysis(
            report_over={"average_return": Decimal("NaN")}))
        with pytest.raises(ValueError) as exc:
            serialize_monitoring_export(response)
        assert str(exc.value) == ERROR_INVALID_EXPORT_INPUT

    def test_roundtrip_schema(self):
        parsed = json.loads(serialize_monitoring_export(_make_response()))
        assert set(parsed.keys()) == set(EXPORT_FIELDS)
        assert isinstance(parsed["alerts"], list)
        assert isinstance(parsed["sources"], list)


# ── G. Immutability ──────────────────────────────────────────────────

class TestImmutability:
    def test_root_immutable(self):
        export = build_monitoring_export(_make_response())
        with pytest.raises(TypeError):
            export["status"] = "X"

    def test_nested_mapping_immutable(self):
        export = build_monitoring_export(_make_response())
        with pytest.raises(TypeError):
            export["monitoring"]["health_status"] = "X"
        with pytest.raises(TypeError):
            export["sources"][0]["status"] = "X"

    def test_tuples_immutable(self):
        export = build_monitoring_export(_make_response())
        assert isinstance(export["alerts"], tuple)
        assert isinstance(export["sources"], tuple)
        assert isinstance(export["limitations"], tuple)

    def test_input_unchanged_after_build_and_serialize(self):
        response = _make_response()
        snapshot = copy.deepcopy(
            {k: v for k, v in response.items()
             if k != "monitoring_analysis"})
        report_before = dict(
            response["monitoring_analysis"]["monitoring_report"])
        build_monitoring_export(response)
        serialize_monitoring_export(response)
        assert {k: v for k, v in response.items()
                if k != "monitoring_analysis"} == snapshot
        assert dict(response["monitoring_analysis"]
                    ["monitoring_report"]) == report_before


# ── H. Bozuk girdi ───────────────────────────────────────────────────

class TestInvalidInput:
    @pytest.mark.parametrize("bad", [
        None, 1, "x", (), [], True,
        {},                                        # eksik kök alanlar
        {**_make_response(), "extra": 1},          # fazladan kök alan
        _make_response(api_version=2),
        _make_response(api_version=True),
        _make_response(report_id=None),
        _make_response(report_id=""),
        _make_response(observed_at=None),
        _make_response(generated_at=123),
        _make_response(status="WEIRD"),
        {**_make_response(), "limitations": "NOPE"},
        _make_response(limitations=(1,)),
        _make_response(analysis=None),             # null analiz + SUCCESS
        _make_response(analysis={"garip": 1}),
        _make_response(analysis={**_make_analysis(), "extra": 1}),
    ])
    def test_sterile_rejection(self, bad):
        with pytest.raises(ValueError) as exc:
            build_monitoring_export(bad)
        assert str(exc.value) == ERROR_INVALID_EXPORT_INPUT

    def test_malformed_monitoring_report(self):
        analysis = _make_analysis()
        analysis["monitoring_report"] = {"eksik": 1}
        with pytest.raises(ValueError) as exc:
            build_monitoring_export(_make_response(analysis=analysis))
        assert str(exc.value) == ERROR_INVALID_EXPORT_INPUT

    def test_malformed_alert_report(self):
        analysis = _make_analysis()
        analysis["alert_report"] = {"eksik": 1}
        with pytest.raises(ValueError):
            build_monitoring_export(_make_response(analysis=analysis))

    def test_malformed_alert_entry(self):
        analysis = _make_analysis(alerts=({"alert_id": "A1"},))
        with pytest.raises(ValueError):
            build_monitoring_export(_make_response(analysis=analysis))

    def test_malformed_source_entry(self):
        analysis = _make_analysis()
        analysis["sources"] = {"s": {"status": "WEIRD", "code": None}}
        with pytest.raises(ValueError):
            build_monitoring_export(_make_response(analysis=analysis))
        analysis["sources"] = {"s": None}
        with pytest.raises(ValueError):
            build_monitoring_export(_make_response(analysis=analysis))

    def test_malformed_nested_limitations(self):
        analysis = _make_analysis()
        analysis["limitations"] = (None,)
        with pytest.raises(ValueError):
            build_monitoring_export(_make_response(analysis=analysis))

    def test_float_bearing_field_rejected(self):
        response = _make_response(analysis=_make_analysis(
            report_over={"success_rate": 62.5}))
        with pytest.raises(ValueError) as exc:
            build_monitoring_export(response)
        assert str(exc.value) == ERROR_INVALID_EXPORT_INPUT

    def test_non_string_mapping_key_rejected(self):
        # JSON-güvenli olmayan anahtarlar sterile reddedilir
        for bad_key in ((1, 2), 5, None, frozenset()):
            analysis = _make_analysis()
            report = dict(analysis["monitoring_report"])
            report["alerts"] = ({bad_key: "x"},)
            analysis["monitoring_report"] = report
            with pytest.raises(ValueError) as exc:
                build_monitoring_export(_make_response(analysis=analysis))
            assert str(exc.value) == ERROR_INVALID_EXPORT_INPUT

    def test_serialize_failures_sterile(self):
        # Serileştirme yolundan da yalnız sterile kod çıkar
        analysis = _make_analysis()
        report = dict(analysis["monitoring_report"])
        report["alerts"] = ({(1,): "x"},)
        analysis["monitoring_report"] = report
        with pytest.raises(ValueError) as exc:
            serialize_monitoring_export(_make_response(analysis=analysis))
        assert str(exc.value) == ERROR_INVALID_EXPORT_INPUT
        assert "tuple" not in str(exc.value)

    def test_error_message_sterile(self):
        try:
            build_monitoring_export({"secret": "sk-XYZ"})
        except ValueError as exc:
            assert str(exc) == ERROR_INVALID_EXPORT_INPUT
            assert "sk-XYZ" not in str(exc)


# ── I. Güvenlik yüzeyi ───────────────────────────────────────────────

class TestSecurity:
    def _imports(self):
        tree = ast.parse(open("monitoring_export.py").read())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        return modules

    def test_import_surface_minimal(self):
        allowed = {"__future__", "json", "decimal", "types", "typing",
                   "monitoring_api"}
        assert self._imports() <= allowed

    def test_no_forbidden_modules(self):
        forbidden = {"monitoring_service", "monitoring_intelligence",
                     "alert_engine", "strategy_service", "os", "sys",
                     "socket", "requests", "urllib", "threading",
                     "subprocess", "sqlite3", "pathlib", "io"}
        assert not (self._imports() & forbidden)

    def test_no_dangerous_calls(self):
        text = open("monitoring_export.py").read()
        for token in ("eval(", "exec(", "open(", "environ",
                      "getenv", "Thread", "Popen"):
            assert token not in text

    def test_no_float_literals(self):
        tree = ast.parse(open("monitoring_export.py").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(
                    node.value, float):
                pytest.fail("float literal bulundu")

    def test_local_schemas_match_lower_layers(self):
        # Yerel şema kopyaları kaynak modüllerle eş kalmalı
        assert monitoring_export.ANALYSIS_FIELDS == \
            monitoring_service.ANALYSIS_FIELDS
        assert monitoring_export.MONITORING_REPORT_FIELDS == \
            monitoring_intelligence.REPORT_FIELDS
        assert monitoring_export.ALERT_REPORT_FIELDS == \
            alert_engine.ALERT_REPORT_FIELDS
        assert monitoring_export.SOURCE_STATES == \
            monitoring_service.SOURCE_STATES


# ── J. Gerçek zincir entegrasyonu ────────────────────────────────────

class TestRealChain:
    def test_export_of_real_api_response(self):
        # Gerçek API yanıtı (DI edilen deterministik sağlayıcı ile)
        analysis = monitoring_service.analyze_monitoring({
            "strategy_proposal": lambda: {
                "freshness": "fresh",
                "data": {"observations": (), "strategy_version": 1,
                         "analysis_version": 1},
            },
        })
        response = monitoring_api.analyze_monitoring_api(
            {}, lambda: analysis)
        export = build_monitoring_export(response)
        assert tuple(export.keys()) == EXPORT_FIELDS
        text = serialize_monitoring_export(response)
        assert json.loads(text)["report_id"] == response["report_id"]
