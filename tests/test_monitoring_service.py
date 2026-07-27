"""Mission 1900 — Agent 04 Monitoring Service testleri.

Kapsam: sağlayıcı orkestrasyonu, kaynak normalizasyonu, hata yönetimi,
zarf şeması, determinizm, güvenlik.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType

import pytest

import alert_engine as ae
import monitoring_intelligence as mi
import monitoring_service as ms
from monitoring_service import analyze_monitoring

MODULE_SOURCE = Path(ms.__file__).read_text(encoding="utf-8")
MODULE_AST = ast.parse(MODULE_SOURCE)


def mk_proposal(**overrides):
    base = {
        "strategy_version": 1,
        "advisory_only": True,
        "read_only": True,
        "portfolio_analysis_version": 1,
        "confidence": "80.00",
        "data_quality": "OK",
        "market_regime": "UNKNOWN",
        "overall_risk": "LOW",
        "recommendations": [],
        "warnings": [],
        "limitations": [],
    }
    base.update(overrides)
    return base


def provider_for(proposal, freshness="fresh"):
    return {"strategy_proposal":
            lambda: {"freshness": freshness, "data": proposal}}


# ── A. Sağlayıcı orkestrasyonu ───────────────────────────────────────

class TestOrchestration:
    def test_provider_called_once(self):
        calls = []

        def provider():
            calls.append(1)
            return {"freshness": "fresh", "data": mk_proposal()}

        analyze_monitoring({"strategy_proposal": provider})
        assert len(calls) == 1

    def test_core_called_once(self, monkeypatch):
        calls = []
        real = mi.build_monitoring_report

        def spy(payload):
            calls.append(1)
            return real(payload)

        monkeypatch.setattr(mi, "build_monitoring_report", spy)
        analyze_monitoring(provider_for(mk_proposal()))
        assert len(calls) == 1

    def test_alert_engine_called_once(self, monkeypatch):
        calls = []
        real = ae.build_alert_report

        def spy(report):
            calls.append(1)
            return real(report)

        monkeypatch.setattr(ae, "build_alert_report", spy)
        analyze_monitoring(provider_for(mk_proposal()))
        assert len(calls) == 1

    def test_no_repeated_execution_across_calls(self):
        calls = []

        def provider():
            calls.append(1)
            return {"freshness": "fresh", "data": mk_proposal()}

        providers = {"strategy_proposal": provider}
        analyze_monitoring(providers)
        analyze_monitoring(providers)
        assert len(calls) == 2  # çağrı başına tam bir kez, önbellek yok

    def test_unknown_provider_rejected(self):
        with pytest.raises(ValueError, match="^UNKNOWN_PROVIDER$"):
            analyze_monitoring({"other": lambda: None})

    def test_non_mapping_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_INPUT$"):
            analyze_monitoring([1, 2])

    def test_service_class_delegates(self):
        service = ms.MonitoringService(provider_for(mk_proposal()))
        analysis = service.get_analysis()
        assert analysis["monitoring_report"]["monitoring_version"] == 1


# ── B. Kaynak normalizasyonu ─────────────────────────────────────────

class TestSourceNormalization:
    def test_complete(self):
        analysis = analyze_monitoring(provider_for(mk_proposal()))
        source = analysis["sources"]["strategy_proposal"]
        assert source["status"] == "COMPLETE"
        assert source["code"] is None
        assert analysis["limitations"] == ()

    def test_partial_on_stale(self):
        analysis = analyze_monitoring(
            provider_for(mk_proposal(), freshness="stale"))
        source = analysis["sources"]["strategy_proposal"]
        assert source["status"] == "PARTIAL"
        assert "OBSERVATIONS_STALE" in analysis["limitations"]
        # Kalite beyanı: OK → PARTIAL düşürmesi Core raporunda görünür
        assert analysis["monitoring_report"]["data_quality"] == "PARTIAL"

    def test_unavailable_on_failure(self):
        def broken():
            raise RuntimeError("gizli sağlayıcı detayı /etc/secret")

        analysis = analyze_monitoring({"strategy_proposal": broken})
        source = analysis["sources"]["strategy_proposal"]
        assert source["status"] == "UNAVAILABLE"
        assert source["code"] == "PROVIDER_FAILED"
        assert "OBSERVATIONS_UNAVAILABLE" in analysis["limitations"]

    def test_unavailable_on_missing_provider(self):
        analysis = analyze_monitoring({})
        source = analysis["sources"]["strategy_proposal"]
        assert source["status"] == "UNAVAILABLE"
        assert source["code"] == "INVALID_PROVIDER_RESULT"

    def test_stable_source_ordering(self):
        analysis = analyze_monitoring(provider_for(mk_proposal()))
        assert tuple(analysis["sources"].keys()) == ("strategy_proposal",)

    def test_source_states_closed(self):
        assert ms.SOURCE_STATES == ("COMPLETE", "PARTIAL", "UNAVAILABLE")


# ── C. Hata yönetimi ─────────────────────────────────────────────────

class TestFailureHandling:
    def test_provider_exception_core_still_runs(self):
        def broken():
            raise ValueError("iç detay")

        analysis = analyze_monitoring({"strategy_proposal": broken})
        report = analysis["monitoring_report"]
        assert report["data_quality"] == "UNAVAILABLE"
        assert report["health_status"] == "UNKNOWN"
        assert report["recommendation_count"] == 0

    def test_malformed_provider_result(self):
        for bad in (None, 42, {"freshness": "fresh"},
                    {"freshness": "hourly", "data": {}},
                    {"freshness": "fresh", "data": "text"}):
            analysis = analyze_monitoring(
                {"strategy_proposal": (lambda b=bad: b)})
            source = analysis["sources"]["strategy_proposal"]
            assert source["status"] == "UNAVAILABLE"
            assert source["code"] == "INVALID_PROVIDER_RESULT"

    def test_empty_proposal_recommendations(self):
        analysis = analyze_monitoring(provider_for(mk_proposal()))
        assert analysis["monitoring_report"]["recommendation_count"] == 0

    def test_sterile_failure_no_leak(self):
        def broken():
            raise RuntimeError("SECRET_TOKEN=abc /home/runner/x.py")

        analysis = analyze_monitoring({"strategy_proposal": broken})
        text = repr(dict(analysis["sources"]))
        assert "SECRET_TOKEN" not in text
        assert "/home/runner" not in text
        assert "Traceback" not in text

    def test_internal_error_sterile(self, monkeypatch):
        def boom(payload):
            raise RuntimeError("iç iz /tmp/x")

        monkeypatch.setattr(mi, "build_monitoring_report", boom)
        with pytest.raises(ValueError, match="^MONITORING_ANALYSIS_ERROR$"):
            analyze_monitoring(provider_for(mk_proposal()))

    def test_base_exception_from_provider_contained(self):
        def broken():
            raise KeyboardInterrupt()

        analysis = analyze_monitoring({"strategy_proposal": broken})
        assert analysis["sources"]["strategy_proposal"][
            "status"] == "UNAVAILABLE"


# ── D. Zarf ──────────────────────────────────────────────────────────

class TestEnvelope:
    def test_exact_fields(self):
        analysis = analyze_monitoring(provider_for(mk_proposal()))
        assert tuple(analysis.keys()) == ms.ANALYSIS_FIELDS

    def test_immutable_envelope(self):
        analysis = analyze_monitoring(provider_for(mk_proposal()))
        assert isinstance(analysis, MappingProxyType)
        with pytest.raises(TypeError):
            analysis["sources"] = {}
        with pytest.raises(TypeError):
            analysis["sources"]["strategy_proposal"]["status"] = "X"

    def test_reports_passed_through_untouched(self):
        analysis = analyze_monitoring(provider_for(mk_proposal()))
        report = analysis["monitoring_report"]
        alert = analysis["alert_report"]
        assert tuple(report.keys()) == mi.REPORT_FIELDS
        assert tuple(alert.keys()) == ae.ALERT_REPORT_FIELDS

    def test_no_metadata_injection(self):
        analysis = analyze_monitoring(provider_for(mk_proposal()))
        assert analysis["monitoring_report"]["report_id"] is None
        assert analysis["monitoring_report"]["observed_at"] is None
        assert analysis["alert_report"]["generated_at"] is None
        for banned in ("report_id", "generated_at", "observed_at"):
            assert banned not in analysis  # zarf kökünde meta yok

    def test_limitation_codes_closed(self):
        assert ms.SERVICE_LIMITATION_CODES == (
            "OBSERVATIONS_STALE", "OBSERVATIONS_UNAVAILABLE")


# ── E. Determinizm ───────────────────────────────────────────────────

class TestDeterminism:
    def test_repeated_identical_input(self):
        providers = provider_for(mk_proposal())
        first = analyze_monitoring(providers)
        second = analyze_monitoring(providers)
        assert dict(first["monitoring_report"]) == \
            dict(second["monitoring_report"])
        assert dict(first["alert_report"]) == dict(second["alert_report"])
        assert first["limitations"] == second["limitations"]

    def test_no_mutation_of_proposal(self):
        proposal = mk_proposal(recommendations=[
            {"instrument": "BTCUSDT", "action": "HOLD"}])
        import copy
        snapshot = copy.deepcopy(proposal)
        analyze_monitoring(provider_for(proposal))
        assert proposal == snapshot

    def test_no_clock_uuid_random_imports(self):
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in (
                    "uuid", "random", "time", "datetime", "secrets")

    def test_no_float_literals(self):
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    def test_no_caching_state(self):
        service = ms.MonitoringService(provider_for(mk_proposal()))
        a = service.get_analysis()
        b = service.get_analysis()
        assert a is not b  # her çağrı taze zarf; gizli önbellek yok


# ── F. Güvenlik ──────────────────────────────────────────────────────

class TestSecurity:
    def test_import_surface(self):
        allowed = {"__future__", "types", "typing",
                   "alert_engine", "monitoring_intelligence",
                   "strategy_service"}
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in allowed
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] in allowed

    def test_no_forbidden_tokens(self):
        forbidden = ("requests", "socket", "urllib", "smtplib",
                     "subprocess", "threading", "sched", "sqlite3",
                     "binance", "ccxt", "flask", "open(", "eval(",
                     "exec(", "os.environ", "getenv",
                     "append_snapshot", "persist=true")
        lowered = MODULE_SOURCE.lower()
        for token in forbidden:
            assert token not in lowered, token

    def test_default_chain_read_only_declaration(self):
        providers = ms.build_default_monitoring_providers()
        assert tuple(providers.keys()) == ("strategy_proposal",)
        assert callable(providers["strategy_proposal"])

    def test_no_persistence_calls_in_ast(self):
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", getattr(func, "id", ""))
                assert name not in ("open", "write", "append_snapshot",
                                    "save", "dump")


# ── G. Geriye dönük uyumluluk ────────────────────────────────────────

class TestBackwardCompatibility:
    def test_analysis_fields_frozen(self):
        assert ms.ANALYSIS_FIELDS == (
            "monitoring_report", "alert_report", "sources",
            "limitations")

    def test_provider_names_frozen(self):
        assert ms.PROVIDER_NAMES == ("strategy_proposal",)

    def test_alert_flow_end_to_end(self):
        # UNAVAILABLE sağlayıcı → Core UNKNOWN → Alert Engine uyarıları
        analysis = analyze_monitoring({})
        codes = [a["code"] for a in
                 analysis["alert_report"]["alerts"]]
        assert "DATA_UNAVAILABLE" in codes
        assert "NO_OBSERVATIONS" in codes
