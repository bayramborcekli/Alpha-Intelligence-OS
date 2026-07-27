"""Mission 1800 / Agent 03 — Strategy Service testleri.

Servis yalnız orkestrasyon yapar; tüm hesap Strategy Core'dadır.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

import strategy_intelligence as si
import strategy_service as ssv

SRC = Path("strategy_service.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def analysis(status="OK", portfolio=None):
    return {
        "ok": True, "read_only": True, "advisory_only": True,
        "analysis_version": 1, "status": status,
        "generated_at": None, "sources": {},
        "portfolio": portfolio if portfolio is not None else {},
    }


def provider(status="OK", portfolio=None, freshness="fresh"):
    env = analysis(status, portfolio)
    return {"portfolio_analysis":
            (lambda: {"freshness": freshness, "data": env})}


CONCENTRATED = {
    "allocation": {"assets": [{"symbol": "BTCUSDT"}],
                   "cash_weight_pct": "10.00"},
    "exposure": {"gross_pct": "90.00"},
    "concentration": {"top_symbol": "BTCUSDT", "top_share_pct": "80.00",
                      "effective_positions": "4.00"},
    "risk_utilization": {"net_exposure_util_pct": "45.00",
                         "drawdown_util_pct": "10.00",
                         "concentration_util_pct": "50.00",
                         "limits_breached": []},
}


# ── Sağlayıcı enjeksiyonu ve devretme ────────────────────────────────

def test_provider_injection_and_delegation():
    p = ssv.analyze_strategy(provider("OK", CONCENTRATED))
    assert p["strategy_version"] == 1
    assert p["data_quality"] == "OK"
    recs = [r for r in p["recommendations"]
            if si.REASON_CONCENTRATION_HIGH in r["reason_codes"]]
    assert len(recs) == 1 and recs[0]["instrument"] == "BTCUSDT"


def test_delegates_to_core_exactly_once(monkeypatch):
    calls = []
    real = si.build_strategy

    def spy(a):
        calls.append(a)
        return real(a)

    monkeypatch.setattr(si, "build_strategy", spy)
    ssv.analyze_strategy(provider("OK", CONCENTRATED))
    assert len(calls) == 1


def test_provider_called_exactly_once():
    count = {"n": 0}

    def prov():
        count["n"] += 1
        return {"freshness": "fresh", "data": analysis()}

    ssv.analyze_strategy({"portfolio_analysis": prov})
    assert count["n"] == 1


def test_service_result_matches_core_plus_sources():
    env = analysis("OK", CONCENTRATED)
    expected = si.build_strategy(copy.deepcopy(env))
    got = ssv.analyze_strategy(
        {"portfolio_analysis": lambda: {"freshness": "fresh",
                                        "data": env}})
    srcs = got.pop("sources")
    assert got == expected            # çekirdek zarfı DEĞİŞTİRİLMEZ
    assert srcs["portfolio_analysis"]["status"] == "ok"


def test_unknown_provider_rejected():
    with pytest.raises(ValueError) as e:
        ssv.analyze_strategy({"weird": lambda: {}})
    assert str(e.value) == ssv.CODE_UNKNOWN_PROVIDER


def test_non_mapping_rejected():
    with pytest.raises(ValueError):
        ssv.analyze_strategy([])


# ── Sağlayıcı hatası / UNAVAILABLE ───────────────────────────────────

def test_provider_exception_sterile_unavailable():
    def boom():
        raise RuntimeError("secret path /x/y")

    p = ssv.analyze_strategy({"portfolio_analysis": boom})
    assert p["data_quality"] == "UNAVAILABLE"
    assert p["recommendations"] == []
    assert p["confidence"] is None and p["overall_risk"] is None
    meta = p["sources"]["portfolio_analysis"]
    assert meta == {"status": "failed", "freshness": "unavailable",
                    "available": False,
                    "code": ssv.CODE_PROVIDER_FAILED,
                    "degraded_to_partial": False}
    assert "secret" not in json.dumps(p)


def test_missing_provider_unavailable():
    p = ssv.analyze_strategy({})
    assert p["data_quality"] == "UNAVAILABLE"
    assert p["sources"]["portfolio_analysis"]["code"] == \
        ssv.CODE_INVALID_RESULT


def test_malformed_provider_result_unavailable():
    for bad in (None, [], "x", {"data": {}}, {"freshness": "weird",
                                              "data": {}}):
        p = ssv.analyze_strategy(
            {"portfolio_analysis": (lambda b=bad: b)})
        assert p["data_quality"] == "UNAVAILABLE"
        assert p["sources"]["portfolio_analysis"]["code"] == \
            ssv.CODE_INVALID_RESULT


def test_invalid_analysis_sterile_fallback():
    """Core'un reddettiği analiz → sterile INVALID_ANALYSIS düşüşü."""
    for bad in ({}, {"analysis_version": 2, "status": "OK",
                     "portfolio": {}}, {"analysis_version": 1,
                                        "status": "WEIRD",
                                        "portfolio": {}}):
        p = ssv.analyze_strategy(
            {"portfolio_analysis":
             (lambda b=bad: {"freshness": "fresh", "data": b})})
        assert p["data_quality"] == "UNAVAILABLE"
        assert p["sources"]["portfolio_analysis"]["code"] == \
            ssv.CODE_INVALID_ANALYSIS


def test_float_in_analysis_sterile_fallback():
    port = copy.deepcopy(CONCENTRATED)
    port["concentration"]["top_share_pct"] = 80.0
    p = ssv.analyze_strategy(provider("OK", port))
    assert p["data_quality"] == "UNAVAILABLE"
    assert p["sources"]["portfolio_analysis"]["code"] == \
        ssv.CODE_INVALID_ANALYSIS


# ── PARTIAL / bayatlık ───────────────────────────────────────────────

def test_partial_analysis_passes_through():
    p = ssv.analyze_strategy(provider("PARTIAL", CONCENTRATED))
    assert p["data_quality"] == "PARTIAL"
    assert si.WARNING_LOW_DATA_QUALITY in p["warnings"]


def test_stale_ok_degrades_to_partial():
    p = ssv.analyze_strategy(provider("OK", CONCENTRATED,
                                      freshness="stale"))
    assert p["data_quality"] == "PARTIAL"
    meta = p["sources"]["portfolio_analysis"]
    assert meta["freshness"] == "stale"
    assert meta["degraded_to_partial"] is True


def test_stale_partial_stays_partial():
    p = ssv.analyze_strategy(provider("PARTIAL", CONCENTRATED,
                                      freshness="stale"))
    assert p["data_quality"] == "PARTIAL"
    assert p["sources"]["portfolio_analysis"][
        "degraded_to_partial"] is False


def test_stale_degradation_does_not_mutate_provider_data():
    env = analysis("OK", CONCENTRATED)
    snapshot = copy.deepcopy(env)
    ssv.analyze_strategy({"portfolio_analysis":
                          lambda: {"freshness": "stale", "data": env}})
    assert env == snapshot


# ── Bilinmeyen koruması / hesap yasağı ───────────────────────────────

def test_unknown_values_preserved_not_zeroed():
    port = copy.deepcopy(CONCENTRATED)
    port["allocation"]["cash_weight_pct"] = None
    port["exposure"]["gross_pct"] = None
    p = ssv.analyze_strategy(provider("OK", port))
    assert si.LIMIT_ALLOCATION_UNKNOWN in p["limitations"]
    assert si.LIMIT_EXPOSURE_UNKNOWN in p["limitations"]
    assert '"0.00"' not in json.dumps(
        [r["current_weight"] for r in p["recommendations"]
         if si.REASON_EXCESS_CASH in r["reason_codes"]])


def test_no_id_or_timestamp_from_service():
    p = ssv.analyze_strategy(provider("OK", CONCENTRATED))
    assert "proposal_id" not in p
    assert "generated_at" not in p


def test_deterministic_service_output():
    a = json.dumps(ssv.analyze_strategy(provider("OK", CONCENTRATED)),
                   sort_keys=True)
    b = json.dumps(ssv.analyze_strategy(provider("OK", CONCENTRATED)),
                   sort_keys=True)
    assert a == b


def test_oo_wrapper_equivalent():
    svc = ssv.StrategyService(provider("OK", CONCENTRATED))
    assert svc.get_proposal() == \
        ssv.analyze_strategy(provider("OK", CONCENTRATED))


# ── Yan etki / kalıcılık yokluğu ─────────────────────────────────────

def test_no_file_write_side_effects(monkeypatch):
    import builtins
    writes = []
    real_open = builtins.open

    def guard(file, mode="r", *a, **k):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            writes.append(file)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guard)
    ssv.analyze_strategy(provider("OK", CONCENTRATED))
    ssv.analyze_strategy({"portfolio_analysis":
                          lambda: (_ for _ in ()).throw(OSError())})
    assert writes == []


def test_real_default_provider_chain_no_write(monkeypatch):
    """Gerçek 1700 zinciri üzerinden uçtan uca — dosya yazımı YOK."""
    import builtins
    writes = []
    real_open = builtins.open

    def guard(file, mode="r", *a, **k):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            writes.append(str(file))
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guard)
    p = ssv.analyze_strategy(ssv.build_default_strategy_providers())
    assert writes == []
    assert p["strategy_version"] == 1
    assert p["data_quality"] in ("OK", "PARTIAL", "UNAVAILABLE")
    assert "proposal_id" not in p and "generated_at" not in p


# ── AST denetimleri ──────────────────────────────────────────────────

def _imports():
    names = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_ast_import_whitelist():
    assert _imports() <= {"__future__", "typing",
                          "strategy_intelligence", "portfolio_service"}


def test_ast_no_banned_imports():
    banned = {"flask", "requests", "socket", "urllib", "websocket",
              "binance", "ccxt", "os", "sys", "subprocess", "threading",
              "multiprocessing", "tempfile", "pathlib", "shutil",
              "pickle", "random", "uuid", "time", "datetime", "json",
              "csv", "io"}
    assert _imports() & banned == set()


def test_ast_no_dynamic_exec_or_open():
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("eval", "exec", "compile",
                                        "__import__", "open"), node.func.id


def test_ast_no_persistence_sinks():
    """Yasak isimler yalnız KOD düzeyinde denetlenir (docstring hariç)."""
    names = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
    for banned in ("append_snapshot", "workspace", "timeline",
                   "subprocess", "thread", "popen", "system"):
        assert not any(banned in n for n in names), banned


def test_no_strategy_math_in_service():
    """Servis strateji hesabı yapmaz: Decimal/aritmetik yok."""
    assert "decimal" not in SRC.lower()
    for node in ast.walk(TREE):
        assert not isinstance(node, (ast.Mult, ast.Div, ast.Sub)), \
            "serviste aritmetik olamaz"


def test_no_reverse_dependency():
    core_src = Path("strategy_intelligence.py").read_text(
        encoding="utf-8")
    assert "strategy_service" not in core_src
    psrc = Path("portfolio_service.py").read_text(encoding="utf-8")
    assert "strategy" not in psrc.lower().replace(
        "strategy yok", "")  # portföy katmanı strateji bilmez


def test_regression_core_suite_untouched():
    """Core sözleşmesi servisle aynı kalır (Agent 02 zarfı)."""
    env = analysis("OK", CONCENTRATED)
    core = si.build_strategy(copy.deepcopy(env))
    for k in ("strategy_version", "advisory_only", "read_only",
              "portfolio_analysis_version", "confidence", "data_quality",
              "market_regime", "overall_risk", "recommendations",
              "warnings", "limitations"):
        assert k in core, k
