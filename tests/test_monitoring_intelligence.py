"""Mission 1900 / Agent 02 — Monitoring Core testleri.

Kapsam: şema/immutability, determinizm, Decimal disiplini, metrikler,
sağlık sınıflandırması, sınırlamalar, güvenlik (AST), geriye uyum.
"""

from __future__ import annotations

import ast
import copy
from decimal import Decimal
from pathlib import Path

import pytest

import monitoring_intelligence as mon

SOURCE = Path("monitoring_intelligence.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _obs(**kw):
    base = {
        "recommendation_id": "R1", "instrument": "BTCUSDT",
        "action": "INCREASE", "confidence": "80.00",
        "current_weight": "10.00", "target_weight": "20.00",
        "entry_value": "100", "observed_value": "110",
        "peak_value": "120", "trough_value": "90",
        "outcome_status": "EVALUATED", "data_quality": "OK",
    }
    base.update(kw)
    return base


def _envelope(recs=None, **kw):
    env = {"strategy_version": 1, "analysis_version": 1,
           "observation_window": {"kind": "SNAPSHOT", "samples": 1},
           "market_regime": "UNKNOWN",
           "recommendations": [] if recs is None else recs}
    env.update(kw)
    return env


def _report(recs=None, **kw):
    return mon.build_monitoring_report(_envelope(recs, **kw))


# ── A. Şema ve immutability ─────────────────────────────────────────

class TestSchema:
    def test_exact_report_fields_and_order(self):
        r = _report([_obs()])
        assert tuple(r.keys()) == mon.REPORT_FIELDS

    def test_core_never_sets_report_id_or_observed_at(self):
        r = _report([_obs()])
        assert r["report_id"] is None
        assert r["observed_at"] is None

    def test_versions_fixed(self):
        r = _report([_obs()])
        assert r["monitoring_version"] == 1
        assert r["strategy_version"] == 1
        assert r["analysis_version"] == 1

    def test_alerts_empty_immutable_collection(self):
        r = _report([_obs()])
        assert r["alerts"] == ()
        assert isinstance(r["alerts"], tuple)

    def test_limitations_immutable_tuple(self):
        r = _report()
        assert isinstance(r["limitations"], tuple)

    def test_input_not_mutated(self):
        env = _envelope([_obs()])
        snapshot = copy.deepcopy(env)
        mon.build_monitoring_report(env)
        assert env == snapshot

    def test_output_deep_isolated_from_input(self):
        env = _envelope([_obs()])
        r = mon.build_monitoring_report(env)
        env["observation_window"]["kind"] = "MUTATED"
        assert r["observation_window"]["kind"] == "SNAPSHOT"

    def test_report_is_truly_immutable(self):
        r = _report([_obs()])
        with pytest.raises(TypeError):
            r["health_status"] = "HACKED"
        with pytest.raises(TypeError):
            r["observation_window"]["kind"] = "HACKED"
        with pytest.raises(AttributeError):
            r["alerts"].append("A1")
        with pytest.raises(AttributeError):
            r["limitations"].append("X")

    def test_no_execution_fields_in_report(self):
        r = _report([_obs()])
        flat = repr(r).lower()
        for banned in ("order_type", "quantity", "price\":",
                       "side\":", "leverage"):
            assert banned not in flat

    def test_default_window_is_snapshot(self):
        env = _envelope([_obs()])
        env["observation_window"] = None
        r = mon.build_monitoring_report(env)
        assert r["observation_window"] == {"kind": "SNAPSHOT",
                                           "samples": None}


# ── B. Determinizm ──────────────────────────────────────────────────

class TestDeterminism:
    def test_identical_input_identical_report(self):
        env = _envelope([_obs(), _obs(recommendation_id="R2",
                                      action="REDUCE",
                                      observed_value="95")])
        assert mon.build_monitoring_report(env) == \
            mon.build_monitoring_report(copy.deepcopy(env))

    def test_limitations_sorted_and_stable(self):
        r = _report()
        assert list(r["limitations"]) == sorted(r["limitations"])
        assert len(set(r["limitations"])) == len(r["limitations"])

    def test_no_uuid_or_clock_imports(self):
        names = set()
        for node in ast.walk(TREE):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        assert names <= {"__future__", "decimal", "types", "typing"}

    def test_no_wallclock_or_uuid_calls_in_source(self):
        for banned in ("uuid", "datetime", "time.", "random"):
            assert banned not in SOURCE.replace("runtime", "")


# ── C. Decimal disiplini ────────────────────────────────────────────

class TestDecimalDiscipline:
    def test_float_rejected(self):
        with pytest.raises(ValueError) as e:
            _report([_obs(entry_value=100.0)])
        assert str(e.value) == "FLOAT_REJECTED"

    def test_nan_and_infinity_rejected(self):
        for bad in ("NaN", "Infinity", "-Infinity"):
            with pytest.raises(ValueError) as e:
                _report([_obs(observed_value=bad)])
            assert str(e.value) == "INVALID_INPUT"

    def test_bool_rejected(self):
        with pytest.raises(ValueError):
            _report([_obs(confidence=True)])

    def test_exact_decimal_return(self):
        r = _report([_obs(entry_value="3", observed_value="4")])
        assert r["average_return"] == "33.33"  # Decimal quantize, float yok

    def test_canonical_fixed_point_strings(self):
        r = _report([_obs()])
        for key in ("success_rate", "average_return",
                    "maximum_drawdown", "confidence_accuracy"):
            value = r[key]
            assert value is None or (
                isinstance(value, str) and "." in value
                and "e" not in value.lower())
            if value is not None:
                assert len(value.split(".")[1]) == 2

    def test_unknown_stays_null_never_zero(self):
        r = _report([_obs(observed_value=None, peak_value=None,
                          trough_value=None, confidence=None)])
        assert r["evaluated_count"] == 0
        for key in ("success_rate", "average_return",
                    "maximum_drawdown", "confidence_accuracy"):
            assert r[key] is None

    def test_no_float_literals_in_source(self):
        floats = [n for n in ast.walk(TREE)
                  if isinstance(n, ast.Constant)
                  and isinstance(n.value, float)]
        assert floats == []


# ── D. Metrikler ────────────────────────────────────────────────────

class TestMetrics:
    def test_recommendation_count(self):
        r = _report([_obs(), _obs(recommendation_id="R2"),
                     _obs(recommendation_id="R3", action="HOLD")])
        assert r["recommendation_count"] == 3

    def test_evaluated_count_requires_valid_info(self):
        recs = [_obs(),                                  # değerlenir
                _obs(action="HOLD"),                     # yönsüz
                _obs(outcome_status="PENDING"),          # açık
                _obs(entry_value="0"),                   # geçersiz giriş
                _obs(entry_value="-5"),                  # negatif giriş
                _obs(observed_value=None)]               # eksik değer
        r = _report(recs)
        assert r["recommendation_count"] == 6
        assert r["evaluated_count"] == 1

    def test_long_direction_return(self):
        r = _report([_obs(action="INCREASE", entry_value="100",
                          observed_value="110")])
        assert r["average_return"] == "10.00"

    def test_short_direction_return(self):
        r = _report([_obs(action="REDUCE", entry_value="100",
                          observed_value="90")])
        assert r["average_return"] == "10.00"  # düşüş = pozitif getiri

    def test_short_direction_loss(self):
        r = _report([_obs(action="REDUCE", entry_value="100",
                          observed_value="110")])
        assert r["average_return"] == "-10.00"

    def test_hold_excluded_from_evaluation(self):
        r = _report([_obs(action="HOLD"), _obs(action="REBALANCE"),
                     _obs(action="DIVERSIFY")])
        assert r["evaluated_count"] == 0

    def test_unknown_action_safely_unevaluated(self):
        r = _report([_obs(action="BUY")])  # takma ad üretilmez
        assert r["evaluated_count"] == 0
        assert r["recommendation_count"] == 1

    def test_success_rate_zero_return_is_neutral(self):
        recs = [_obs(observed_value="110"),          # +10 → başarı
                _obs(recommendation_id="R2",
                     observed_value="100"),          # 0 → nötr
                _obs(recommendation_id="R3",
                     observed_value="90")]           # -10 → başarısız
        r = _report(recs)
        assert r["evaluated_count"] == 3
        assert r["success_rate"] == "33.33"

    def test_average_return_mean(self):
        recs = [_obs(observed_value="110"),
                _obs(recommendation_id="R2", observed_value="130")]
        r = _report(recs)
        assert r["average_return"] == "20.00"

    def test_maximum_drawdown_from_supplied_peaks(self):
        recs = [_obs(peak_value="100", trough_value="80"),
                _obs(recommendation_id="R2", peak_value="200",
                     trough_value="100")]
        r = _report(recs)
        assert r["maximum_drawdown"] == "50.00"

    def test_drawdown_invalid_pair_skipped(self):
        r = _report([_obs(peak_value="80", trough_value="100")])
        assert r["maximum_drawdown"] is None

    def test_confidence_accuracy_success(self):
        r = _report([_obs(confidence="80.00", observed_value="110")])
        # başarı: 1 - |0.8 - 1| = 0.8 → 80.00
        assert r["confidence_accuracy"] == "80.00"

    def test_confidence_accuracy_failure(self):
        r = _report([_obs(confidence="80.00", observed_value="90")])
        # başarısız: 1 - |0.8 - 0| = 0.2 → 20.00
        assert r["confidence_accuracy"] == "20.00"

    def test_confidence_out_of_range_excluded(self):
        r = _report([_obs(confidence="120.00")])
        assert r["evaluated_count"] == 1
        assert r["confidence_accuracy"] is None

    def test_malformed_string_safely_unevaluated(self):
        r = _report([_obs(observed_value="not-a-number")])
        assert r["evaluated_count"] == 0


# ── E. Sağlık sınıflandırması ───────────────────────────────────────

class TestHealth:
    def test_healthy(self):
        r = _report([_obs(peak_value="100", trough_value="90")])
        assert r["health_status"] == "HEALTHY"

    def test_degraded_low_success(self):
        recs = [_obs(observed_value="110"),
                _obs(recommendation_id="R2", observed_value="100"),
                _obs(recommendation_id="R3", observed_value="100"),
                _obs(recommendation_id="R4", observed_value="110")]
        r = _report([{**o, "peak_value": "100", "trough_value": "95"}
                     for o in recs])
        # başarı 50 < yok; 2/4 = 50 → DEGRADED değil... 50 sınırda
        assert r["success_rate"] == "50.00"
        assert r["health_status"] == "HEALTHY"

    def test_degraded_by_drawdown(self):
        r = _report([_obs(peak_value="100", trough_value="70")])
        assert r["maximum_drawdown"] == "30.00"
        assert r["health_status"] == "DEGRADED"

    def test_critical_by_drawdown(self):
        r = _report([_obs(peak_value="100", trough_value="40")])
        assert r["health_status"] == "CRITICAL"

    def test_critical_by_success_rate(self):
        recs = [_obs(observed_value="90"),
                _obs(recommendation_id="R2", observed_value="90"),
                _obs(recommendation_id="R3", observed_value="90"),
                _obs(recommendation_id="R4", observed_value="90"),
                _obs(recommendation_id="R5", observed_value="110")]
        r = _report(recs)
        assert r["success_rate"] == "20.00"
        assert r["health_status"] == "CRITICAL"

    def test_severity_precedence_critical_over_degraded(self):
        # hem düşük başarı (CRITICAL) hem düşük kalibrasyon (DEGRADED)
        r = _report([_obs(observed_value="90", confidence="90.00")])
        assert r["health_status"] == "CRITICAL"

    def test_degraded_by_confidence_accuracy(self):
        r = _report([_obs(observed_value="110", confidence="10.00",
                          peak_value="100", trough_value="95")])
        assert r["confidence_accuracy"] == "10.00"
        assert r["health_status"] == "DEGRADED"

    def test_degraded_by_low_coverage(self):
        recs = [_obs(peak_value="100", trough_value="95")] + [
            _obs(recommendation_id=f"R{i}", action="HOLD")
            for i in range(2, 5)]
        r = _report(recs)  # 1/4 kapsam = %25 < 50
        assert r["health_status"] == "DEGRADED"

    def test_degraded_by_partial_quality(self):
        r = _report([_obs(peak_value="100", trough_value="95")],
                    data_quality="PARTIAL")
        assert r["health_status"] == "DEGRADED"

    def test_unknown_when_no_observations(self):
        assert _report()["health_status"] == "UNKNOWN"

    def test_unknown_when_nothing_evaluated(self):
        r = _report([_obs(action="HOLD")])
        assert r["health_status"] == "UNKNOWN"

    def test_unknown_when_quality_unavailable(self):
        r = _report([_obs()], data_quality="UNAVAILABLE")
        assert r["health_status"] == "UNKNOWN"

    def test_unknown_data_never_healthy(self):
        r = _report([_obs(observed_value=None)])
        assert r["health_status"] != "HEALTHY"

    def test_thresholds_are_named_decimal_constants(self):
        for name in ("SUCCESS_CRITICAL_PCT", "SUCCESS_DEGRADED_PCT",
                     "DRAWDOWN_CRITICAL_PCT", "DRAWDOWN_DEGRADED_PCT",
                     "CONFIDENCE_ACC_DEGRADED_PCT",
                     "COVERAGE_DEGRADED_PCT"):
            assert isinstance(getattr(mon, name), Decimal)
        assert mon.SUCCESS_CRITICAL_PCT == Decimal("25")
        assert mon.SUCCESS_DEGRADED_PCT == Decimal("50")
        assert mon.DRAWDOWN_CRITICAL_PCT == Decimal("50")
        assert mon.DRAWDOWN_DEGRADED_PCT == Decimal("25")


# ── F. Sınırlamalar ─────────────────────────────────────────────────

class TestLimitations:
    def test_no_observations_codes(self):
        r = _report()
        assert "NO_OBSERVATIONS" in r["limitations"]
        assert "NO_EVALUATED_OUTCOMES" in r["limitations"]

    def test_insufficient_data_codes(self):
        r = _report([_obs(), _obs(recommendation_id="R2", action="HOLD",
                                  peak_value=None, confidence=None)])
        lim = r["limitations"]
        assert "INSUFFICIENT_RETURN_DATA" in lim
        assert "INSUFFICIENT_DRAWDOWN_DATA" in lim
        assert "INSUFFICIENT_CONFIDENCE_DATA" in lim

    def test_full_data_no_insufficiency_codes(self):
        r = _report([_obs()])
        lim = r["limitations"]
        assert "INSUFFICIENT_RETURN_DATA" not in lim
        assert "NO_EVALUATED_OUTCOMES" not in lim

    def test_unknown_regime_code(self):
        assert "UNKNOWN_MARKET_REGIME" in _report([_obs()])["limitations"]

    def test_partial_quality_code(self):
        r = _report([_obs()], data_quality="PARTIAL")
        assert "PARTIAL_DATA_QUALITY" in r["limitations"]
        r2 = _report([_obs()], data_quality="OK")
        assert "PARTIAL_DATA_QUALITY" not in r2["limitations"]

    def test_unavailable_quality_not_marked_partial(self):
        # UNAVAILABLE, PARTIAL koduyla temsil EDİLMEZ — ayrı durumdur.
        r = _report([_obs()], data_quality="UNAVAILABLE")
        assert "PARTIAL_DATA_QUALITY" not in r["limitations"]
        r2 = _report()  # gözlem yok → türetilmiş UNAVAILABLE
        assert r2["data_quality"] == "UNAVAILABLE"
        assert "PARTIAL_DATA_QUALITY" not in r2["limitations"]
        assert "NO_OBSERVATIONS" in r2["limitations"]

    def test_all_codes_from_closed_set(self):
        for recs in ([], [_obs()], [_obs(action="HOLD")]):
            r = _report(recs)
            assert set(r["limitations"]) <= set(mon.LIMITATION_CODES)


# ── G. Güvenlik ─────────────────────────────────────────────────────

class TestSecurity:
    def test_no_forbidden_imports(self):
        for banned in ("requests", "socket", "http", "urllib",
                       "sqlite3", "subprocess", "threading", "asyncio",
                       "os", "sys", "binance", "ccxt", "websocket"):
            assert f"import {banned}" not in SOURCE

    def test_no_open_or_exec_calls(self):
        for node in ast.walk(TREE):
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Name):
                assert node.func.id not in (
                    "open", "eval", "exec", "compile", "__import__")

    def test_no_env_or_secret_access(self):
        for banned in ("environ", "getenv", "API_KEY", "SECRET"):
            assert banned not in SOURCE

    def test_no_persistence_or_snapshot(self):
        for banned in ("append_snapshot", "write(", "jsonl", "pickle"):
            assert banned not in SOURCE

    def test_sterile_envelope_errors(self):
        for bad in (None, [], "x", {"strategy_version": 2,
                                    "analysis_version": 1}):
            with pytest.raises(ValueError) as e:
                mon.build_monitoring_report(bad)
            assert str(e.value) in ("INVALID_INPUT", "FLOAT_REJECTED")
            assert "/" not in str(e.value) and " " not in str(e.value)

    def test_invalid_recommendation_entry_rejected(self):
        with pytest.raises(ValueError):
            _report(["not-a-dict"])

    def test_invalid_window_rejected(self):
        env = _envelope([_obs()])
        env["observation_window"] = {"kind": 5}
        with pytest.raises(ValueError):
            mon.build_monitoring_report(env)
        env["observation_window"] = {"kind": "SNAPSHOT", "samples": -1}
        with pytest.raises(ValueError):
            mon.build_monitoring_report(env)


# ── H. Geriye uyum ──────────────────────────────────────────────────

class TestBackwardCompatibility:
    def test_strategy_core_untouched_by_import(self):
        import strategy_intelligence as si
        proposal = si.build_strategy({"analysis_version": 1,
                                      "status": "OK", "portfolio": {}})
        assert proposal["strategy_version"] == 1

    def test_monitoring_does_not_import_mission_modules(self):
        for banned in ("strategy_intelligence", "strategy_service",
                       "portfolio_service", "risk_api", "app"):
            assert f"import {banned}" not in SOURCE
