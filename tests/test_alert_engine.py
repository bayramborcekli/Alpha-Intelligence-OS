"""Mission 1900 — Agent 03 Alert Engine testleri.

Kapsam: şema, determinizm, kural eşlemesi, severity, tekilleştirme,
doğrulama, sınır bütünlüğü, güvenlik.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest

import alert_engine as ae
import monitoring_intelligence as mi
from alert_engine import build_alert_report

MODULE_SOURCE = Path(ae.__file__).read_text(encoding="utf-8")
MODULE_AST = ast.parse(MODULE_SOURCE)


def mk_report(**overrides):
    """El yapımı geçerli MonitoringReport (Agent 02 şeması)."""
    base = {
        "monitoring_version": 1,
        "report_id": None,
        "observed_at": None,
        "strategy_version": 1,
        "analysis_version": 1,
        "observation_window": MappingProxyType(
            {"kind": "SNAPSHOT", "samples": None}),
        "data_quality": "OK",
        "recommendation_count": 4,
        "evaluated_count": 4,
        "success_rate": "75.00",
        "average_return": "3.10",
        "maximum_drawdown": "10.00",
        "confidence_accuracy": "80.00",
        "market_regime": "BULL",
        "health_status": "HEALTHY",
        "alerts": (),
        "limitations": (),
    }
    base.update(overrides)
    return MappingProxyType(base)


def core_report(**overrides):
    """Gerçek Monitoring Core çıktısı (sınır bütünlüğü testleri)."""
    payload = {"strategy_version": 1, "analysis_version": 1,
               "recommendations": []}
    payload.update(overrides)
    return mi.build_monitoring_report(payload)


HEALTHY = mk_report()


# ── A. Şema ──────────────────────────────────────────────────────────

class TestSchema:
    def test_report_fields_exact(self):
        report = build_alert_report(HEALTHY)
        assert tuple(report.keys()) == ae.ALERT_REPORT_FIELDS

    def test_alert_fields_exact(self):
        report = build_alert_report(mk_report(health_status="CRITICAL"))
        for alert in report["alerts"]:
            assert tuple(alert.keys()) == ae.ALERT_FIELDS

    def test_report_immutable(self):
        report = build_alert_report(HEALTHY)
        assert isinstance(report, MappingProxyType)
        with pytest.raises(TypeError):
            report["alert_count"] = 99

    def test_alerts_immutable(self):
        report = build_alert_report(mk_report(health_status="CRITICAL"))
        assert isinstance(report["alerts"], tuple)
        alert = report["alerts"][0]
        assert isinstance(alert, MappingProxyType)
        with pytest.raises(TypeError):
            alert["severity"] = "INFO"

    def test_healthy_report_no_alerts(self):
        report = build_alert_report(HEALTHY)
        assert report["alert_count"] == 0
        assert report["highest_severity"] is None
        assert report["alerts"] == ()
        assert report["limitations"] == ()

    def test_generated_at_null(self):
        assert build_alert_report(HEALTHY)["generated_at"] is None

    def test_report_id_passthrough(self):
        report = build_alert_report(mk_report(report_id="RID-7"))
        assert report["report_id"] == "RID-7"

    def test_versions(self):
        report = build_alert_report(HEALTHY)
        assert report["alert_version"] == 1
        assert report["monitoring_version"] == 1

    def test_health_status_carried(self):
        report = build_alert_report(mk_report(health_status="CRITICAL"))
        assert report["health_status"] == "CRITICAL"


# ── B. Determinizm ───────────────────────────────────────────────────

class TestDeterminism:
    def test_identical_input_identical_output(self):
        source = mk_report(health_status="DEGRADED",
                           success_rate="30.00")
        assert (dict(build_alert_report(source))
                == dict(build_alert_report(source)))

    def test_stable_alert_ids(self):
        source = mk_report(health_status="CRITICAL",
                           success_rate="10.00",
                           market_regime="UNKNOWN")
        for _ in range(3):
            report = build_alert_report(source)
            assert [a["alert_id"] for a in report["alerts"]] == [
                f"A{i}" for i in range(1, len(report["alerts"]) + 1)]

    def test_stable_order(self):
        source = mk_report(health_status="CRITICAL",
                           success_rate="10.00",
                           market_regime="UNKNOWN")
        codes1 = [a["code"] for a in build_alert_report(source)["alerts"]]
        codes2 = [a["code"] for a in build_alert_report(source)["alerts"]]
        assert codes1 == codes2

    def test_no_uuid_or_timestamp_fields(self):
        report = build_alert_report(mk_report(health_status="DEGRADED",
                                              success_rate="30.00"))
        assert report["generated_at"] is None
        for alert in report["alerts"]:
            assert alert["alert_id"].startswith("A")
            assert alert["alert_id"][1:].isdigit()

    def test_no_clock_uuid_random_imports(self):
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                assert root not in ("uuid", "random", "time",
                                    "datetime", "secrets")


# ── C. Kural eşlemesi ────────────────────────────────────────────────

class TestRuleMapping:
    def codes(self, report):
        return [a["code"] for a in build_alert_report(report)["alerts"]]

    def test_critical_health(self):
        assert "MONITORING_CRITICAL" in self.codes(
            mk_report(health_status="CRITICAL"))

    def test_degraded_health(self):
        assert "MONITORING_DEGRADED" in self.codes(
            mk_report(health_status="DEGRADED"))

    def test_data_unavailable(self):
        assert "DATA_UNAVAILABLE" in self.codes(
            mk_report(data_quality="UNAVAILABLE"))

    def test_data_partial(self):
        assert "DATA_PARTIAL" in self.codes(
            mk_report(data_quality="PARTIAL"))

    def test_no_observations(self):
        codes = self.codes(mk_report(
            recommendation_count=0, evaluated_count=0,
            success_rate=None, average_return=None,
            maximum_drawdown=None, confidence_accuracy=None,
            health_status="UNKNOWN"))
        assert "NO_OBSERVATIONS" in codes
        assert "NO_EVALUATED_OUTCOMES" in codes

    def test_no_evaluated_outcomes_via_limitation(self):
        codes = self.codes(mk_report(
            limitations=("NO_EVALUATED_OUTCOMES",)))
        assert "NO_EVALUATED_OUTCOMES" in codes

    def test_low_success_rate(self):
        assert "LOW_SUCCESS_RATE" in self.codes(
            mk_report(success_rate="49.99"))
        assert "LOW_SUCCESS_RATE" not in self.codes(
            mk_report(success_rate="50.00"))

    def test_high_drawdown(self):
        assert "HIGH_DRAWDOWN" in self.codes(
            mk_report(maximum_drawdown="25.01"))
        assert "HIGH_DRAWDOWN" not in self.codes(
            mk_report(maximum_drawdown="25.00"))

    def test_low_confidence_accuracy(self):
        assert "LOW_CONFIDENCE_ACCURACY" in self.codes(
            mk_report(confidence_accuracy="49.99"))
        assert "LOW_CONFIDENCE_ACCURACY" not in self.codes(
            mk_report(confidence_accuracy="50.00"))

    def test_low_evaluation_coverage(self):
        assert "LOW_EVALUATION_COVERAGE" in self.codes(
            mk_report(recommendation_count=10, evaluated_count=4))
        assert "LOW_EVALUATION_COVERAGE" not in self.codes(
            mk_report(recommendation_count=10, evaluated_count=5))

    def test_unknown_market_regime(self):
        assert "UNKNOWN_MARKET_REGIME" in self.codes(
            mk_report(market_regime="UNKNOWN"))
        assert "UNKNOWN_MARKET_REGIME" in self.codes(
            mk_report(market_regime=None))
        assert "UNKNOWN_MARKET_REGIME" not in self.codes(HEALTHY)

    def test_null_metrics_no_metric_alerts(self):
        codes = self.codes(mk_report(
            success_rate=None, maximum_drawdown=None,
            confidence_accuracy=None))
        for code in ("LOW_SUCCESS_RATE", "HIGH_DRAWDOWN",
                     "LOW_CONFIDENCE_ACCURACY"):
            assert code not in codes


# ── D. Severity ──────────────────────────────────────────────────────

class TestSeverity:
    def test_critical_precedence(self):
        report = build_alert_report(
            mk_report(health_status="CRITICAL", success_rate="10.00",
                      market_regime="UNKNOWN"))
        assert report["highest_severity"] == "CRITICAL"
        assert report["alerts"][0]["code"] == "MONITORING_CRITICAL"

    def test_warning_precedence(self):
        report = build_alert_report(
            mk_report(health_status="DEGRADED", success_rate="30.00",
                      market_regime="UNKNOWN"))
        assert report["highest_severity"] == "WARNING"

    def test_info_only(self):
        report = build_alert_report(mk_report(market_regime="UNKNOWN"))
        assert report["highest_severity"] == "INFO"
        assert all(a["severity"] == "INFO" for a in report["alerts"])

    def test_null_when_no_alerts(self):
        assert build_alert_report(HEALTHY)["highest_severity"] is None

    def test_severity_ordering_not_alphabetical(self):
        report = build_alert_report(
            mk_report(health_status="CRITICAL", success_rate="10.00",
                      market_regime="UNKNOWN"))
        ranks = [ae.SEVERITY_PRECEDENCE[a["severity"]]
                 for a in report["alerts"]]
        assert ranks == sorted(ranks)

    def test_fixed_severity_table(self):
        for code, template in ae.ALERT_CODES.items():
            assert template["severity"] in ae.SEVERITIES
            assert template["recommended_action"] in ae.RECOMMENDED_ACTIONS


# ── E. Tekilleştirme ─────────────────────────────────────────────────

class TestDeduplication:
    def test_metric_and_limitation_single_alert(self):
        report = build_alert_report(mk_report(
            market_regime="UNKNOWN",
            limitations=("UNKNOWN_MARKET_REGIME",)))
        codes = [a["code"] for a in report["alerts"]]
        assert codes.count("UNKNOWN_MARKET_REGIME") == 1

    def test_each_code_once(self):
        report = build_alert_report(mk_report(
            health_status="CRITICAL", data_quality="PARTIAL",
            success_rate="10.00", maximum_drawdown="60.00",
            confidence_accuracy="20.00", market_regime="UNKNOWN",
            recommendation_count=10, evaluated_count=2,
            limitations=("PARTIAL_DATA_QUALITY",
                         "UNKNOWN_MARKET_REGIME")))
        codes = [a["code"] for a in report["alerts"]]
        assert len(codes) == len(set(codes))

    def test_stable_order_after_dedup(self):
        source = mk_report(
            health_status="DEGRADED", data_quality="PARTIAL",
            success_rate="30.00", market_regime="UNKNOWN",
            limitations=("PARTIAL_DATA_QUALITY",
                         "UNKNOWN_MARKET_REGIME"))
        expected = ["MONITORING_DEGRADED", "DATA_PARTIAL",
                    "LOW_SUCCESS_RATE", "UNKNOWN_MARKET_REGIME"]
        assert [a["code"] for a in
                build_alert_report(source)["alerts"]] == expected


# ── F. Doğrulama ─────────────────────────────────────────────────────

class TestValidation:
    def test_non_mapping_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(["not", "a", "report"])

    def test_missing_field_rejected(self):
        broken = dict(HEALTHY)
        del broken["health_status"]
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(broken)

    def test_unsupported_version_rejected(self):
        with pytest.raises(ValueError,
                           match="^UNSUPPORTED_MONITORING_VERSION$"):
            build_alert_report(mk_report(monitoring_version=2))

    def test_unknown_health_status_rejected(self):
        with pytest.raises(ValueError, match="^UNKNOWN_HEALTH_STATUS$"):
            build_alert_report(mk_report(health_status="FINE"))

    def test_unknown_data_quality_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(mk_report(data_quality="GREAT"))

    def test_negative_counts_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(mk_report(recommendation_count=-1))
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(mk_report(evaluated_count=-1))

    def test_bool_count_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(mk_report(evaluated_count=True))

    def test_inconsistent_counts_rejected(self):
        with pytest.raises(ValueError,
                           match="^INCONSISTENT_MONITORING_REPORT$"):
            build_alert_report(mk_report(recommendation_count=1,
                                         evaluated_count=2))

    def test_non_empty_input_alerts_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(mk_report(alerts=({"code": "X"},)))

    def test_malformed_decimal_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(mk_report(success_rate="abc"))
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(mk_report(maximum_drawdown="NaN"))

    def test_float_metric_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(mk_report(success_rate=49.9))

    def test_malformed_limitations_rejected(self):
        with pytest.raises(ValueError, match="^INVALID_MONITORING_REPORT$"):
            build_alert_report(mk_report(limitations="NOPE"))

    def test_sterile_errors_only(self):
        for bad in (None, 5, mk_report(monitoring_version=0),
                    mk_report(health_status="???")):
            try:
                build_alert_report(bad)
            except ValueError as exc:
                assert str(exc) in ae.ALERT_LIMITATION_CODES
            else:  # pragma: no cover
                pytest.fail("bekleneni reddetmedi")


# ── G. Sınır bütünlüğü ───────────────────────────────────────────────

class TestBoundaryIntegrity:
    def test_accepts_real_core_output(self):
        report = build_alert_report(core_report())
        codes = [a["code"] for a in report["alerts"]]
        assert "NO_OBSERVATIONS" in codes

    def test_core_output_unchanged(self):
        source = core_report()
        snapshot = dict(source)
        build_alert_report(source)
        assert dict(source) == snapshot

    def test_accepts_custom_immutable_mapping(self):
        from collections.abc import Mapping as _Mapping

        class FrozenReport(_Mapping):
            def __init__(self, data):
                self._data = dict(data)

            def __getitem__(self, key):
                return self._data[key]

            def __iter__(self):
                return iter(self._data)

            def __len__(self):
                return len(self._data)

        report = build_alert_report(FrozenReport(dict(HEALTHY)))
        assert report["alert_count"] == 0

    def test_no_mutation_of_dict_input(self):
        source = dict(HEALTHY)
        snapshot = dict(source)
        build_alert_report(source)
        assert source == snapshot

    def test_no_metric_or_health_recomputation(self):
        # Motor, rapor CRITICAL derken metriklerden farklı bir sağlık
        # türetmez: health_status aynen taşınır.
        report = build_alert_report(
            mk_report(health_status="CRITICAL", success_rate="90.00"))
        assert report["health_status"] == "CRITICAL"
        assert report["alerts"][0]["code"] == "MONITORING_CRITICAL"

    def test_thresholds_imported_not_duplicated(self):
        # Sayısal eşik sabitleri modülde ÇOĞALTILMAZ; Monitoring
        # Core'dan içe aktarılır (25/50 gibi Decimal literal yok).
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Name) and func.id == "Decimal"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    assert node.args[0].value in ("0", "100")
        assert ae.SUCCESS_DEGRADED_PCT is mi.SUCCESS_DEGRADED_PCT

    def test_no_circular_dependency(self):
        mi_source = Path(mi.__file__).read_text(encoding="utf-8")
        assert "alert_engine" not in mi_source


# ── H. Güvenlik ──────────────────────────────────────────────────────

class TestSecurity:
    def test_import_surface_stdlib_only(self):
        allowed = {"__future__", "collections", "decimal", "types",
                   "typing", "monitoring_intelligence"}
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in allowed
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] in allowed

    def test_no_forbidden_tokens(self):
        forbidden = ("requests", "socket", "urllib", "http.client",
                     "smtplib", "subprocess", "threading", "sched",
                     "sqlite3", "binance", "ccxt", "webhook",
                     "os.environ", "getenv", "open(", "eval(", "exec(")
        lowered = MODULE_SOURCE.lower()
        for token in forbidden:
            assert token not in lowered, token

    def test_no_float_literals(self):
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    def test_no_trade_instructions_in_templates(self):
        forbidden = ("order", "buy", "sell", "emir", "al ", "sat ",
                     "pozisyon boyutu", "entry price", "exit price")
        for template in ae.ALERT_CODES.values():
            text = (template["title"] + " "
                    + template["description"]).lower()
            for token in forbidden:
                assert token not in text

    def test_closed_code_set(self):
        assert set(ae.ALERT_CODES.keys()) == {
            "MONITORING_CRITICAL", "MONITORING_DEGRADED",
            "DATA_UNAVAILABLE", "DATA_PARTIAL", "NO_OBSERVATIONS",
            "NO_EVALUATED_OUTCOMES", "LOW_SUCCESS_RATE",
            "HIGH_DRAWDOWN", "LOW_CONFIDENCE_ACCURACY",
            "LOW_EVALUATION_COVERAGE", "UNKNOWN_MARKET_REGIME"}
        with pytest.raises(TypeError):
            ae.ALERT_CODES["NEW_CODE"] = {}

    def test_templates_immutable(self):
        for template in ae.ALERT_CODES.values():
            with pytest.raises(TypeError):
                template["severity"] = "INFO"


# ── I. Geriye dönük uyumluluk ────────────────────────────────────────

class TestBackwardCompatibility:
    def test_thresholds_unchanged(self):
        assert mi.SUCCESS_CRITICAL_PCT == Decimal("25")
        assert mi.SUCCESS_DEGRADED_PCT == Decimal("50")
        assert mi.DRAWDOWN_CRITICAL_PCT == Decimal("50")
        assert mi.DRAWDOWN_DEGRADED_PCT == Decimal("25")
        assert mi.CONFIDENCE_ACC_DEGRADED_PCT == Decimal("50")
        assert mi.COVERAGE_DEGRADED_PCT == Decimal("50")

    def test_alert_report_fields_frozen(self):
        assert ae.ALERT_REPORT_FIELDS == (
            "alert_version", "monitoring_version", "report_id",
            "generated_at", "health_status", "alert_count",
            "highest_severity", "alerts", "limitations")

    def test_alert_fields_frozen(self):
        assert ae.ALERT_FIELDS == (
            "alert_id", "severity", "code", "title", "description",
            "affected_component", "trigger_reason",
            "recommended_action")

    def test_alert_limitation_codes_frozen(self):
        assert ae.ALERT_LIMITATION_CODES == (
            "INCONSISTENT_MONITORING_REPORT",
            "INVALID_MONITORING_REPORT",
            "UNKNOWN_HEALTH_STATUS",
            "UNSUPPORTED_MONITORING_VERSION")
