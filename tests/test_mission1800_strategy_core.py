"""Mission 1800 / Agent 02 — Strategy Core testleri.

Saf çekirdek: PortfolioAnalysis → StrategyProposal (advisory-only).
"""

from __future__ import annotations

import ast
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

import strategy_intelligence as si

SRC = Path("strategy_intelligence.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


# ── Yardımcı fabrikalar ──────────────────────────────────────────────

def analysis(status="OK", portfolio=None, version=1):
    return {
        "ok": True, "read_only": True, "advisory_only": True,
        "analysis_version": version, "status": status,
        "generated_at": "2026-07-27T00:00:00+00:00",
        "sources": {}, "portfolio": portfolio if portfolio is not None
        else {},
    }


def healthy_portfolio():
    return {
        "equity": {"nav_usdt": "1000.00000000"},
        "positions": [],
        "allocation": {"assets": [
            {"symbol": "BTCUSDT", "notional": "200", "weight_pct": "20.00"},
            {"symbol": "ETHUSDT", "notional": "180", "weight_pct": "18.00"},
            {"symbol": "SOLUSDT", "notional": "160", "weight_pct": "16.00"}],
            "cash_weight_pct": "46.00",
            "unallocated_or_unknown_pct": "0.00"},
        "exposure": {"gross_pct": "54.00", "net_pct": "54.00",
                     "unknown_positions": 0},
        "concentration": {"hhi": "0.12", "top_symbol": "BTCUSDT",
                          "top_share_pct": "37.04",
                          "effective_positions": "3.20"},
        "performance": {},
        "risk_utilization": {"net_exposure_util_pct": "27.00",
                             "drawdown_util_pct": "20.00",
                             "concentration_util_pct": "46.30",
                             "limits_breached": []},
        "health": {"portfolio_health_score": "90.00"},
    }


def build(status="OK", portfolio=None):
    return si.build_strategy(analysis(status, portfolio))


# ── Zarf sözleşmesi ──────────────────────────────────────────────────

def test_envelope_required_fields():
    p = build("OK", healthy_portfolio())
    for k in ("strategy_version", "advisory_only", "read_only",
              "portfolio_analysis_version", "confidence", "data_quality",
              "market_regime", "overall_risk", "recommendations",
              "warnings", "limitations"):
        assert k in p, k
    assert p["strategy_version"] == 1
    assert p["portfolio_analysis_version"] == 1
    assert p["advisory_only"] is True and p["read_only"] is True


def test_core_never_emits_id_or_timestamp():
    p = build("OK", healthy_portfolio())
    assert "proposal_id" not in p
    assert "generated_at" not in p


def test_market_regime_honest_unknown():
    p = build("OK", healthy_portfolio())
    assert p["market_regime"] == "UNKNOWN"
    assert si.LIMIT_MARKET_REGIME_UNKNOWN in p["limitations"]
    assert si.LIMIT_NO_FORECAST in p["limitations"]


def test_recommendation_required_fields():
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = "80.00"
    p = build("OK", port)
    assert p["recommendations"], "öneri beklenirdi"
    for r in p["recommendations"]:
        for k in ("recommendation_id", "instrument", "action",
                  "reason_codes", "priority", "confidence",
                  "current_weight", "target_weight", "risk_level",
                  "expected_effect", "invalidation_conditions"):
            assert k in r, k
        assert set(r["expected_effect"]) == {"metric", "direction",
                                             "magnitude_pct"}


def test_no_execution_fields_anywhere():
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = "90.00"
    port["allocation"]["cash_weight_pct"] = "70.00"
    p = build("OK", port)
    dump = json.dumps(p).lower()
    for banned in ('"quantity"', '"price"', '"order_type"', '"order"',
                   '"qty"', '"limit_price"', '"stop_price"'):
        assert banned not in dump, banned


# ── Kural motoru ─────────────────────────────────────────────────────

def test_healthy_portfolio_zero_recommendations():
    p = build("OK", healthy_portfolio())
    assert p["recommendations"] == []
    assert p["overall_risk"] == "LOW"
    assert p["warnings"] == []


def test_empty_analysis_unavailable():
    p = build("UNAVAILABLE", {})
    assert p["recommendations"] == []
    assert p["confidence"] is None
    assert p["overall_risk"] is None
    assert si.WARNING_ANALYSIS_UNAVAILABLE in p["warnings"]
    assert si.WARNING_LOW_DATA_QUALITY in p["warnings"]


def test_empty_ok_portfolio_all_limitations():
    p = build("OK", {})
    assert p["recommendations"] == []
    for lim in (si.LIMIT_ALLOCATION_UNKNOWN, si.LIMIT_EXPOSURE_UNKNOWN,
                si.LIMIT_CONCENTRATION_UNKNOWN,
                si.LIMIT_DIVERSIFICATION_UNKNOWN,
                si.LIMIT_RISK_UTILIZATION_UNKNOWN):
        assert lim in p["limitations"], lim
    assert p["overall_risk"] is None  # temel yok — bilinmezlik korunur


def test_high_concentration_reduce():
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = "72.50"
    p = build("OK", port)
    recs = [r for r in p["recommendations"]
            if si.REASON_CONCENTRATION_HIGH in r["reason_codes"]]
    assert len(recs) == 1
    r = recs[0]
    assert r["instrument"] == "BTCUSDT"
    assert r["action"] == "REDUCE"
    assert r["risk_level"] == "HIGH"
    assert r["current_weight"] == "72.50"
    assert r["target_weight"] == "50.00"
    assert r["expected_effect"] == {"metric": "TOP_SHARE_PCT",
                                    "direction": "DECREASE",
                                    "magnitude_pct": "22.50"}
    assert p["overall_risk"] == "HIGH"


def test_low_diversification_diversify():
    port = healthy_portfolio()
    port["concentration"]["effective_positions"] = "1.40"
    p = build("OK", port)
    recs = [r for r in p["recommendations"]
            if r["action"] == "DIVERSIFY"]
    assert len(recs) == 1
    assert recs[0]["reason_codes"] == [si.REASON_DIVERSIFICATION_LOW]
    assert recs[0]["instrument"] == "PORTFOLIO"


def test_no_diversify_recommendation_without_assets():
    port = healthy_portfolio()
    port["concentration"]["effective_positions"] = "0"
    port["allocation"]["assets"] = []
    p = build("OK", port)
    assert all(r["action"] != "DIVERSIFY" for r in p["recommendations"])


def test_high_cash_rebalance():
    port = healthy_portfolio()
    port["allocation"]["cash_weight_pct"] = "75.00"
    p = build("OK", port)
    recs = [r for r in p["recommendations"]
            if si.REASON_EXCESS_CASH in r["reason_codes"]]
    assert len(recs) == 1
    r = recs[0]
    assert r["action"] == "REBALANCE" and r["priority"] == 4
    assert r["target_weight"] == "30.00"
    assert r["expected_effect"]["magnitude_pct"] == "45.00"


def test_under_allocated_increase():
    port = healthy_portfolio()
    port["allocation"]["cash_weight_pct"] = "90.00"
    port["exposure"]["gross_pct"] = "8.00"
    p = build("OK", port)
    actions = {r["action"] for r in p["recommendations"]}
    assert "INCREASE" in actions and "REBALANCE" in actions


def test_over_allocated_reduce():
    port = healthy_portfolio()
    port["exposure"]["gross_pct"] = "180.00"
    p = build("OK", port)
    recs = [r for r in p["recommendations"]
            if si.REASON_OVER_ALLOCATED in r["reason_codes"]]
    assert len(recs) == 1
    assert recs[0]["risk_level"] == "HIGH"
    assert recs[0]["expected_effect"]["magnitude_pct"] == "80.00"


def test_risk_limit_near_hold():
    port = healthy_portfolio()
    port["risk_utilization"]["drawdown_util_pct"] = "92.00"
    p = build("OK", port)
    recs = [r for r in p["recommendations"]
            if si.REASON_RISK_LIMIT_NEAR in r["reason_codes"]]
    assert len(recs) == 1
    assert recs[0]["action"] == "HOLD" and recs[0]["priority"] == 2
    assert p["overall_risk"] == "HIGH"


def test_risk_limit_breached_top_priority():
    port = healthy_portfolio()
    port["risk_utilization"]["net_exposure_util_pct"] = "130.00"
    port["risk_utilization"]["limits_breached"] = ["LIMIT_NET_EXPOSURE"]
    p = build("OK", port)
    first = p["recommendations"][0]
    assert first["priority"] == 1
    assert si.REASON_RISK_LIMIT_BREACHED in first["reason_codes"]
    assert first["action"] == "REDUCE"
    assert si.WARNING_RISK_LIMIT_BREACHED in p["warnings"]
    assert p["overall_risk"] == "CRITICAL"


def test_rules_may_emit_zero_recommendations():
    p = build("OK", healthy_portfolio())
    assert p["recommendations"] == []


# ── Bilinmeyen değer / null koruması ─────────────────────────────────

def test_unknown_sections_skip_silently_with_limitations():
    port = healthy_portfolio()
    port["allocation"]["cash_weight_pct"] = None
    port["exposure"]["gross_pct"] = None
    port["concentration"]["top_share_pct"] = None
    port["concentration"]["effective_positions"] = None
    for k in ("net_exposure_util_pct", "drawdown_util_pct",
              "concentration_util_pct"):
        port["risk_utilization"][k] = None
    p = build("OK", port)
    assert p["recommendations"] == []
    for lim in (si.LIMIT_ALLOCATION_UNKNOWN, si.LIMIT_EXPOSURE_UNKNOWN,
                si.LIMIT_CONCENTRATION_UNKNOWN,
                si.LIMIT_DIVERSIFICATION_UNKNOWN,
                si.LIMIT_RISK_UTILIZATION_UNKNOWN):
        assert lim in p["limitations"], lim


def test_unknown_never_becomes_zero():
    port = healthy_portfolio()
    port["allocation"]["cash_weight_pct"] = None
    p = build("OK", port)
    dump = json.dumps(p)
    # cash bilinmiyor → EXCESS_CASH/UNDER_ALLOCATED üretilmez, 0 yok
    assert all(si.REASON_EXCESS_CASH not in r["reason_codes"]
               for r in p["recommendations"])
    assert '"current_weight": "0' not in dump


def test_partial_quality_reduces_confidence_and_warns():
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = "70.00"
    ok = build("OK", port)
    part = build("PARTIAL", port)
    assert si.WARNING_LOW_DATA_QUALITY in part["warnings"]
    r_ok = ok["recommendations"][0]
    r_part = part["recommendations"][0]
    assert Decimal(r_part["confidence"]) == \
        Decimal(r_ok["confidence"]) - Decimal("20")
    assert Decimal(part["confidence"]) < Decimal(ok["confidence"])


def test_unavailable_confidence_and_risk_null():
    p = build("UNAVAILABLE", healthy_portfolio())
    assert p["confidence"] is None and p["overall_risk"] is None
    assert p["recommendations"] == []


# ── Determinizm ──────────────────────────────────────────────────────

def test_deterministic_byte_identical():
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = "88.00"
    port["allocation"]["cash_weight_pct"] = "65.00"
    a = json.dumps(si.build_strategy(analysis("OK", port)),
                   sort_keys=True)
    b = json.dumps(si.build_strategy(analysis("OK", copy.deepcopy(port))),
                   sort_keys=True)
    assert a == b


def test_stable_ordering_and_ids():
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = "88.00"
    port["allocation"]["cash_weight_pct"] = "65.00"
    port["risk_utilization"]["drawdown_util_pct"] = "150.00"
    p = build("OK", port)
    ids = [r["recommendation_id"] for r in p["recommendations"]]
    assert ids == [f"R{i}" for i in range(1, len(ids) + 1)]
    prios = [r["priority"] for r in p["recommendations"]]
    assert prios == sorted(prios)


def test_all_numbers_fixed_point_strings():
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = "77.777"
    p = build("OK", port)
    r = p["recommendations"][0]
    for v in (r["confidence"], r["current_weight"], r["target_weight"],
              r["expected_effect"]["magnitude_pct"], p["confidence"]):
        assert isinstance(v, str) and Decimal(v) is not None
    assert r["current_weight"] == "77.78"  # 2 hane quantize


def test_code_lists_sorted():
    port = healthy_portfolio()
    port["risk_utilization"]["limits_breached"] = ["X"]
    p = build("PARTIAL", port)
    assert p["warnings"] == sorted(p["warnings"])
    assert p["limitations"] == sorted(p["limitations"])
    for r in p["recommendations"]:
        assert r["invalidation_conditions"] == \
            sorted(r["invalidation_conditions"])


def test_immutable_output_no_shared_state():
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = "80.00"
    a = build("OK", port)
    a["recommendations"][0]["action"] = "MUTATED"
    a["limitations"].append("INJECTED")
    b = build("OK", port)
    assert b["recommendations"][0]["action"] == "REDUCE"
    assert "INJECTED" not in b["limitations"]


def test_input_not_mutated():
    port = healthy_portfolio()
    snapshot = copy.deepcopy(port)
    env = analysis("OK", port)
    si.build_strategy(env)
    assert port == snapshot
    assert env["portfolio"] is port


# ── Float / geçersiz girdi reddi ─────────────────────────────────────

def test_float_rejected():
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = 51.5
    with pytest.raises(ValueError) as e:
        build("OK", port)
    assert str(e.value) == si.ERROR_FLOAT_REJECTED


def test_float_rejected_in_utilization():
    port = healthy_portfolio()
    port["risk_utilization"]["drawdown_util_pct"] = 90.0
    with pytest.raises(ValueError) as e:
        build("OK", port)
    assert str(e.value) == si.ERROR_FLOAT_REJECTED


def test_bool_rejected():
    port = healthy_portfolio()
    port["allocation"]["cash_weight_pct"] = True
    with pytest.raises(ValueError) as e:
        build("OK", port)
    assert str(e.value) == si.ERROR_INVALID_INPUT


def test_garbage_string_rejected():
    port = healthy_portfolio()
    port["exposure"]["gross_pct"] = "not-a-number"
    with pytest.raises(ValueError) as e:
        build("OK", port)
    assert str(e.value) == si.ERROR_INVALID_INPUT


def test_invalid_envelope_rejected():
    for bad in (None, [], "x", 5):
        with pytest.raises(ValueError):
            si.build_strategy(bad)


def test_wrong_analysis_version_rejected():
    with pytest.raises(ValueError) as e:
        si.build_strategy(analysis(version=2))
    assert str(e.value) == si.ERROR_INVALID_INPUT


def test_invalid_status_rejected():
    with pytest.raises(ValueError) as e:
        si.build_strategy(analysis(status="WEIRD"))
    assert str(e.value) == si.ERROR_INVALID_INPUT


def test_error_messages_sterile():
    """Hata mesajı yalnız koddur — veri/yol/detay sızmaz."""
    port = healthy_portfolio()
    port["concentration"]["top_share_pct"] = 1.5
    try:
        build("OK", port)
    except ValueError as e:
        assert str(e) in (si.ERROR_FLOAT_REJECTED, si.ERROR_INVALID_INPUT)


def test_extreme_magnitude_rejected():
    port = healthy_portfolio()
    port["exposure"]["gross_pct"] = "1E+30"
    with pytest.raises(ValueError) as e:
        build("OK", port)
    assert str(e.value) == si.ERROR_INVALID_INPUT


# ── Yan etki yokluğu ─────────────────────────────────────────────────

def test_no_side_effects_no_file_write(tmp_path, monkeypatch):
    import builtins
    calls = []
    real_open = builtins.open

    def guard(file, mode="r", *a, **k):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            calls.append(file)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guard)
    build("OK", healthy_portfolio())
    assert calls == []


# ── AST güvenlik denetimi ────────────────────────────────────────────

def _imports():
    names = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_ast_only_stdlib_pure_imports():
    assert _imports() <= {"__future__", "decimal", "typing"}


def test_ast_no_banned_imports():
    banned = {"flask", "requests", "socket", "http", "urllib",
              "websocket", "binance", "ccxt", "os", "sys", "subprocess",
              "threading", "multiprocessing", "tempfile", "pathlib",
              "shutil", "sqlite3", "pickle", "random", "uuid", "time",
              "datetime"}
    assert _imports() & banned == set()


def test_ast_no_dynamic_execution():
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and \
                isinstance(node.func, ast.Name):
            assert node.func.id not in ("eval", "exec", "compile",
                                        "__import__", "open"), node.func.id


def test_ast_no_attribute_sinks():
    src_low = SRC.lower()
    for banned in ("append_snapshot", "workspace", "timeline",
                   "subprocess", "socket(", "thread("):
        assert banned not in src_low, banned


def test_ast_no_global_mutable_state():
    """Modül düzeyi atamalar yalnız sabittir (büyük harf/_ ile)."""
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                assert isinstance(t, ast.Name)
                assert t.id.isupper() or t.id.startswith("_"), t.id


def test_module_docstring_declares_advisory_only():
    assert "ADVISORY-ONLY" in SRC


# ── Regresyon uyumluluğu ─────────────────────────────────────────────

def test_consumes_real_portfolio_service_envelope():
    """Gerçek 1700 zinciri çıktısı Core tarafından kabul edilir."""
    import portfolio_service as psv
    env = psv.get_portfolio_analysis({
        "equity": lambda: {"freshness": "fresh", "data": {
            "nav_usdt": "1000", "cash_usdt": "900", "realized_pnl": "0",
            "unrealized_pnl": "0", "total_fees": "0"}},
        "positions": lambda: {"freshness": "fresh", "data": [{
            "symbol": "BTCUSDT", "side": "LONG", "quantity": "0.001",
            "entry_price": "50000", "mark_price": "60000",
            "leverage": "1", "unrealized_pnl": "10"}]},
        "risk": lambda: {"freshness": "fresh", "data": {
            "drawdown_pct": "1",
            "thresholds": {"max_net_exposure_pct": "200",
                           "max_drawdown_pct": "5",
                           "max_concentration_pct": "80"}}},
    }, "2026-07-27T00:00:00+00:00")
    p = si.build_strategy(env)
    assert p["data_quality"] == env["status"]
    assert p["strategy_version"] == 1


def test_no_project_module_imports():
    for name in ("portfolio_intelligence", "portfolio_service",
                 "risk_api", "intelligence_service", "app"):
        assert name not in _imports()
