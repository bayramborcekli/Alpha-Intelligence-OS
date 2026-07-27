"""Mission 1700 / Agent 02 — Portfolio Intelligence çekirdek testleri.

Saf analiz katmanı: determinizm, Decimal disiplini, null dürüstlüğü,
metrik doğruluğu ve mimari yasaklar (import/AST).
"""

from __future__ import annotations

import ast
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

import portfolio_intelligence as pi


def base_inputs():
    return {
        "generated_at": "2026-07-27T00:00:00+00:00",
        "sources": {"ledger": "fresh", "positions": "fresh",
                    "risk": "fresh"},
        "equity": {"nav_usdt": "1000", "cash_usdt": "400",
                   "realized_pnl": "25", "unrealized_pnl": "-5",
                   "total_fees": "3.5"},
        "positions": [
            {"symbol": "BTCUSDT", "side": "LONG", "quantity": "0.004",
             "entry_price": "100000", "mark_price": "100000",
             "leverage": "1"},
            {"symbol": "ETHUSDT", "side": "SHORT", "quantity": "0.05",
             "entry_price": "4000", "mark_price": "4000",
             "leverage": "1"},
        ],
        "risk": {"drawdown_pct": "2",
                 "thresholds": {"max_net_exposure_pct": "200",
                                "max_drawdown_pct": "5",
                                "max_concentration_pct": "80"}},
    }


# ── Zarf ve determinizm ──────────────────────────────────────────────

def test_envelope_flags_and_version():
    out = pi.analyze(base_inputs())
    assert out["ok"] is True
    assert out["read_only"] is True
    assert out["advisory_only"] is True
    assert out["analysis_version"] == 1
    assert out["status"] == "OK"
    assert out["generated_at"] == "2026-07-27T00:00:00+00:00"


def test_deterministic_byte_identical():
    a = json.dumps(pi.analyze(base_inputs()), sort_keys=True)
    b = json.dumps(pi.analyze(base_inputs()), sort_keys=True)
    assert a == b


def test_input_not_mutated():
    inputs = base_inputs()
    snapshot = copy.deepcopy(inputs)
    pi.analyze(inputs)
    assert inputs == snapshot


def test_position_order_does_not_matter():
    inputs = base_inputs()
    reversed_inputs = copy.deepcopy(inputs)
    reversed_inputs["positions"].reverse()
    assert json.dumps(pi.analyze(inputs), sort_keys=True) == \
        json.dumps(pi.analyze(reversed_inputs), sort_keys=True)


def test_all_numbers_serialized_as_strings():
    out = pi.analyze(base_inputs())

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        else:
            assert not isinstance(node, (float, Decimal)), node
    walk(out)


# ── Decimal disiplini ────────────────────────────────────────────────

def test_float_rejected_everywhere():
    for mutate in (
        lambda d: d["equity"].__setitem__("nav_usdt", 1000.0),
        lambda d: d["positions"][0].__setitem__("quantity", 0.004),
        lambda d: d["risk"].__setitem__("drawdown_pct", 2.0),
        lambda d: d["risk"]["thresholds"].__setitem__(
            "max_drawdown_pct", 5.0),
    ):
        inputs = base_inputs()
        mutate(inputs)
        with pytest.raises(ValueError) as e:
            pi.analyze(inputs)
        assert str(e.value) == "FLOAT_REJECTED"


def test_invalid_inputs_sterile():
    bad = [
        ("not-a-dict", None),
        ({"positions": "x"}, None),
        ({"positions": [{"symbol": "", "side": "LONG",
                         "quantity": "1"}]}, None),
        ({"positions": [{"symbol": "BTCUSDT", "side": "YANLIS",
                         "quantity": "1"}]}, None),
        ({"positions": [{"symbol": "BTCUSDT", "side": "LONG",
                         "quantity": "0"}]}, None),
        ({"positions": [{"symbol": "BTCUSDT", "side": "LONG",
                         "quantity": "abc"}]}, None),
        ({"sources": {"ledger": "yanlis"}}, None),
        ({"generated_at": 5}, None),
        ({"equity": {"nav_usdt": True}}, None),
        ({"equity": {"nav_usdt": "NaN"}}, None),
    ]
    for inputs, _ in bad:
        with pytest.raises(ValueError) as e:
            pi.analyze(inputs)
        assert str(e.value) == "INVALID_INPUT", inputs


def test_extreme_magnitudes_rejected_sterile():
    """Uç üsler ham DecimalException değil sterile kod üretmeli."""
    cases = (
        lambda d: d["equity"].__setitem__("nav_usdt", "1E+40"),
        lambda d: d["equity"].__setitem__("nav_usdt", "-9.9E+99"),
        lambda d: d["positions"][0].__setitem__("mark_price", "1E+30"),
        lambda d: d["positions"][0].__setitem__("quantity", "1E+19"),
        lambda d: d["risk"]["thresholds"].__setitem__(
            "max_drawdown_pct", "1E+120"),
    )
    for mutate in cases:
        inputs = base_inputs()
        mutate(inputs)
        with pytest.raises(ValueError) as e:
            pi.analyze(inputs)
        assert str(e.value) == "INVALID_INPUT"


def test_large_but_valid_magnitudes_do_not_overflow():
    inputs = base_inputs()
    inputs["equity"]["nav_usdt"] = "999999999999999999"  # < 1E+18
    inputs["positions"][0]["quantity"] = "99999999999"
    inputs["positions"][0]["mark_price"] = "9999999"
    out = pi.analyze(inputs)  # DecimalException sızmamalı
    assert out["portfolio"]["exposure"]["gross"] is not None


# ── Metrik doğruluğu ─────────────────────────────────────────────────

def test_exposure_math():
    exp = pi.analyze(base_inputs())["portfolio"]["exposure"]
    # BTC long 400, ETH short 200
    assert exp["gross"] == "600.00000000"
    assert exp["net"] == "200.00000000"
    assert exp["long"] == "400.00000000"
    assert exp["short"] == "200.00000000"
    assert exp["gross_pct"] == "60.00"
    assert exp["net_pct"] == "20.00"
    assert exp["unknown_positions"] == 0


def test_allocation_weights_and_cash():
    alloc = pi.analyze(base_inputs())["portfolio"]["allocation"]
    weights = {a["symbol"]: a["weight_pct"] for a in alloc["assets"]}
    assert weights == {"BTCUSDT": "40.00", "ETHUSDT": "20.00"}
    assert alloc["cash_weight_pct"] == "40.00"
    assert alloc["unallocated_or_unknown_pct"] == "0.00"


def test_concentration_hhi_and_diversification():
    conc = pi.analyze(base_inputs())["portfolio"]["concentration"]
    # paylar: BTC 2/3, ETH 1/3 → HHI = 4/9 + 1/9 = 5/9 ≈ %55.56
    assert conc["top_symbol"] == "BTCUSDT"
    assert conc["top_share_pct"] == "66.67"
    assert conc["hhi"] == "55.56"
    assert conc["effective_positions"] == "1.80"


def test_unrealized_pnl_per_position_sides():
    inputs = base_inputs()
    inputs["positions"][0]["mark_price"] = "110000"  # LONG kâr
    inputs["positions"][1]["mark_price"] = "3900"    # SHORT kâr
    pos = {p["symbol"]: p for p in
           pi.analyze(inputs)["portfolio"]["positions"]}
    assert pos["BTCUSDT"]["unrealized_pnl"] == "40.00000000"
    assert pos["ETHUSDT"]["unrealized_pnl"] == "5.00000000"


def test_risk_utilization_and_breaches():
    inputs = base_inputs()
    inputs["risk"]["drawdown_pct"] = "6"  # limit 5 → ihlal
    ru = pi.analyze(inputs)["portfolio"]["risk_utilization"]
    assert ru["net_exposure_util_pct"] == "10.00"   # 20 / 200
    assert ru["drawdown_util_pct"] == "120.00"
    assert ru["concentration_util_pct"] == "83.33"  # 66.67 / 80
    assert ru["limits_breached"] == ["LIMIT_DRAWDOWN"]


def test_health_score_composition():
    health = pi.analyze(base_inputs())["portfolio"]["health"]
    comps = {c["code"]: c for c in health["components"]}
    assert set(comps) == {"EXPOSURE", "DRAWDOWN", "CONCENTRATION"}
    assert comps["EXPOSURE"]["score"] == "90.00"       # 100-10
    assert comps["DRAWDOWN"]["score"] == "60.00"       # 100-40
    assert comps["CONCENTRATION"]["score"] == "16.67"  # 100-83.33..
    # 90*0.4 + 60*0.4 + 16.666*0.2 = 63.33
    assert health["portfolio_health_score"] == "63.33"


def test_health_score_clamped_zero_on_extreme_breach():
    inputs = base_inputs()
    inputs["risk"]["drawdown_pct"] = "50"  # util 1000 → skor 0'a kilit
    comps = {c["code"]: c for c in
             pi.analyze(inputs)["portfolio"]["health"]["components"]}
    assert comps["DRAWDOWN"]["score"] == "0.00"


# ── Null dürüstlüğü ve durumlar ──────────────────────────────────────

def test_unknown_mark_price_yields_nulls_and_partial():
    inputs = base_inputs()
    inputs["positions"][0]["mark_price"] = None
    out = pi.analyze(inputs)
    assert out["status"] == "PARTIAL"
    pos = {p["symbol"]: p for p in out["portfolio"]["positions"]}
    assert pos["BTCUSDT"]["notional"] is None
    assert pos["BTCUSDT"]["weight_pct"] is None
    assert pos["BTCUSDT"]["unrealized_pnl"] is None
    exp = out["portfolio"]["exposure"]
    assert exp["unknown_positions"] == 1
    assert exp["gross"] == "200.00000000"  # yalnız bilinen ETH
    assert out["portfolio"]["allocation"]["unallocated_or_unknown_pct"] \
        is None


def test_missing_nav_yields_unavailable_not_zero():
    inputs = base_inputs()
    inputs["equity"]["nav_usdt"] = None
    out = pi.analyze(inputs)
    assert out["status"] == "UNAVAILABLE"
    p = out["portfolio"]
    assert p["equity"]["nav_usdt"] is None
    assert p["exposure"]["net_pct"] is None
    assert p["allocation"]["cash_weight_pct"] is None
    # mutlak exposure yine hesaplanır (bilinen notional'lar)
    assert p["exposure"]["gross"] == "600.00000000"


def test_stale_source_yields_partial():
    inputs = base_inputs()
    inputs["sources"]["risk"] = "stale"
    assert pi.analyze(inputs)["status"] == "PARTIAL"


def test_missing_thresholds_yield_null_utilization_partial():
    inputs = base_inputs()
    inputs["risk"]["thresholds"]["max_drawdown_pct"] = None
    out = pi.analyze(inputs)
    assert out["status"] == "PARTIAL"
    ru = out["portfolio"]["risk_utilization"]
    assert ru["drawdown_util_pct"] is None
    assert ru["limits_breached"] == []
    comps = {c["code"]: c for c in
             out["portfolio"]["health"]["components"]}
    assert comps["DRAWDOWN"]["score"] is None
    # sağlık kalan bileşenlerle yeniden ağırlıklandırılır (0.4+0.2)
    # (90*0.4 + 16.67*0.2) / 0.6 = 65.56
    assert out["portfolio"]["health"]["portfolio_health_score"] == "65.56"


def test_empty_portfolio_all_cash():
    inputs = base_inputs()
    inputs["positions"] = []
    inputs["equity"]["cash_usdt"] = "1000"
    out = pi.analyze(inputs)
    assert out["status"] == "OK"
    p = out["portfolio"]
    assert p["exposure"]["gross"] == "0.00000000"
    assert p["allocation"]["cash_weight_pct"] == "100.00"
    assert p["allocation"]["unallocated_or_unknown_pct"] == "0.00"
    assert p["concentration"]["hhi"] is None
    assert p["risk_utilization"]["limits_breached"] == []


def test_no_inputs_at_all_is_unavailable_with_nulls():
    out = pi.analyze({})
    assert out["status"] == "UNAVAILABLE"
    p = out["portfolio"]
    assert p["equity"]["nav_usdt"] is None
    assert p["positions"] == []
    assert p["health"]["portfolio_health_score"] is None
    assert p["performance"]["forecast"] is None


def test_forecast_always_null():
    assert pi.analyze(base_inputs())["portfolio"]["performance"][
        "forecast"] is None


# ── Mimari yasaklar ──────────────────────────────────────────────────

def test_module_is_pure_stdlib_only():
    tree = ast.parse(Path("portfolio_intelligence.py")
                     .read_text(encoding="utf-8"))
    allowed = {"decimal", "typing", "__future__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] in allowed, node.module


def test_no_io_or_exec_calls():
    src = Path("portfolio_intelligence.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"open", "eval", "exec", "__import__", "compile", "input"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned, node.func.id
    for marker in ("requests", "socket", "subprocess", "os.", "time.",
                   "datetime.now", "append_snapshot", "binance"):
        assert marker not in src, marker


def test_error_messages_are_sterile_codes_only():
    tree = ast.parse(Path("portfolio_intelligence.py")
                     .read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            args = node.exc.args
            assert len(args) == 1
            arg = args[0]
            assert isinstance(arg, (ast.Name, ast.Constant))
            if isinstance(arg, ast.Constant):
                assert arg.value in ("FLOAT_REJECTED", "INVALID_INPUT")
