"""Mission 1700 / Agent 03 — Portfolio Intelligence servis testleri.

Sağlayıcı toplama, sterile normalizasyon, çekirdek delegasyonu ve
mimari yasaklar. Servis hiçbir portföy matematiği yapmaz.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

import portfolio_intelligence as pi
import portfolio_service as ps

GEN_AT = "2026-07-27T00:00:00+00:00"


def fake_providers():
    return {
        "equity": lambda: {"freshness": "fresh", "data": {
            "nav_usdt": "1000", "cash_usdt": "400",
            "realized_pnl": "25", "unrealized_pnl": "-5",
            "total_fees": "3.5"}},
        "positions": lambda: {"freshness": "fresh", "data": [
            {"symbol": "BTCUSDT", "side": "LONG", "quantity": "0.004",
             "entry_price": "100000", "mark_price": "100000",
             "leverage": "1"}]},
        "risk": lambda: {"freshness": "fresh", "data": {
            "drawdown_pct": "2",
            "thresholds": {"max_net_exposure_pct": "200",
                           "max_drawdown_pct": "5",
                           "max_concentration_pct": "80"}}},
    }


# ── Başarılı toplama ve zarf ─────────────────────────────────────────

def test_successful_aggregation_ok_envelope():
    out = ps.get_portfolio_analysis(fake_providers(), GEN_AT)
    assert out["ok"] is True
    assert out["read_only"] is True
    assert out["advisory_only"] is True
    assert out["analysis_version"] == 1
    assert out["status"] == "OK"
    assert out["generated_at"] == GEN_AT
    assert out["portfolio"]["equity"]["nav_usdt"] == "1000.00000000"
    assert out["portfolio"]["exposure"]["gross"] == "400.00000000"


def test_envelope_matches_core_except_source_metadata():
    """Servis zarfı, çekirdek zarfından yalnız sources ile ayrışır."""
    svc = ps.get_portfolio_analysis(fake_providers(), GEN_AT)
    core = pi.analyze_portfolio({
        "generated_at": GEN_AT,
        "sources": {"equity": "fresh", "positions": "fresh",
                    "risk": "fresh"},
        "equity": fake_providers()["equity"]()["data"],
        "positions": fake_providers()["positions"]()["data"],
        "risk": fake_providers()["risk"]()["data"],
    })
    svc2 = dict(svc)
    core2 = dict(core)
    svc2.pop("sources")
    core2.pop("sources")
    assert json.dumps(svc2, sort_keys=True) == \
        json.dumps(core2, sort_keys=True)


def test_class_wrapper_equivalent():
    a = ps.get_portfolio_analysis(fake_providers(), GEN_AT)
    b = ps.PortfolioService(fake_providers()).get_analysis(GEN_AT)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ── Kaynak normalizasyonu ────────────────────────────────────────────

def test_source_metadata_fields_sterile():
    out = ps.get_portfolio_analysis(fake_providers(), GEN_AT)
    assert sorted(out["sources"]) == ["equity", "positions", "risk"]
    for meta in out["sources"].values():
        assert set(meta) == {"status", "freshness", "available", "code"}
        assert meta["status"] == "ok"
        assert meta["freshness"] == "fresh"
        assert meta["available"] is True
        assert meta["code"] is None


def test_stale_provider_yields_partial():
    providers = fake_providers()
    data = providers["risk"]()
    providers["risk"] = lambda: {"freshness": "stale",
                                 "data": data["data"]}
    out = ps.get_portfolio_analysis(providers, GEN_AT)
    assert out["status"] == "PARTIAL"
    assert out["sources"]["risk"]["freshness"] == "stale"
    assert out["sources"]["risk"]["available"] is True


def test_provider_exception_sterilized():
    providers = fake_providers()

    def boom():
        raise RuntimeError("/gizli/yol/secret.pem ve API anahtarı")
    providers["risk"] = boom
    out = ps.get_portfolio_analysis(providers, GEN_AT)
    assert out["status"] == "PARTIAL"
    meta = out["sources"]["risk"]
    assert meta == {"status": "failed", "freshness": "unavailable",
                    "available": False, "code": "PROVIDER_FAILED"}
    dumped = json.dumps(out)
    assert "gizli" not in dumped and "secret" not in dumped \
        and "Traceback" not in dumped


def test_malformed_provider_result_sterilized():
    for bad in (lambda: "metin", lambda: {"freshness": "yanlis",
                                          "data": {}},
                lambda: {"data": {}}, None, "callable-degil"):
        providers = fake_providers()
        providers["risk"] = bad
        out = ps.get_portfolio_analysis(providers, GEN_AT)
        assert out["sources"]["risk"]["available"] is False
        assert out["sources"]["risk"]["code"] in (
            "PROVIDER_FAILED", "INVALID_PROVIDER_RESULT")


def test_all_providers_unavailable_yields_unavailable():
    def boom():
        raise OSError("x")
    out = ps.get_portfolio_analysis(
        {"equity": boom, "positions": boom, "risk": boom}, GEN_AT)
    assert out["status"] == "UNAVAILABLE"
    p = out["portfolio"]
    assert p["equity"]["nav_usdt"] is None
    assert p["positions"] == []
    assert p["health"]["portfolio_health_score"] is None
    for meta in out["sources"].values():
        assert meta["available"] is False


def test_missing_provider_entry_is_unavailable_not_zero():
    providers = fake_providers()
    del providers["equity"]
    out = ps.get_portfolio_analysis(providers, GEN_AT)
    assert out["status"] == "UNAVAILABLE"  # NAV bilinmiyor
    assert out["portfolio"]["equity"]["nav_usdt"] is None
    assert out["sources"]["equity"]["available"] is False


def test_invalid_provider_data_degraded_not_raised():
    """Çekirdek doğrulamasına takılan veri sterile düşürülür."""
    providers = fake_providers()
    providers["positions"] = lambda: {"freshness": "fresh", "data": [
        {"symbol": "BTCUSDT", "side": "YANLIS", "quantity": "1"}]}
    out = ps.get_portfolio_analysis(providers, GEN_AT)
    assert out["status"] == "PARTIAL"
    assert out["portfolio"]["positions"] == []
    assert out["sources"]["positions"]["available"] is False
    assert out["sources"]["positions"]["code"] == \
        "INVALID_PROVIDER_RESULT"
    # equity sağlam kalır
    assert out["portfolio"]["equity"]["nav_usdt"] == "1000.00000000"


def test_unknown_provider_name_rejected():
    providers = fake_providers()
    providers["exchange"] = lambda: {"freshness": "fresh", "data": {}}
    with pytest.raises(ValueError) as e:
        ps.get_portfolio_analysis(providers, GEN_AT)
    assert str(e.value) == "UNKNOWN_PROVIDER"


# ── Determinizm ──────────────────────────────────────────────────────

def test_deterministic_byte_identical():
    a = json.dumps(ps.get_portfolio_analysis(fake_providers(), GEN_AT),
                   sort_keys=True)
    b = json.dumps(ps.get_portfolio_analysis(fake_providers(), GEN_AT),
                   sort_keys=True)
    assert a == b


def test_provider_dict_order_independent():
    providers = fake_providers()
    reordered = {k: providers[k] for k in
                 ("risk", "positions", "equity")}
    a = json.dumps(ps.get_portfolio_analysis(providers, GEN_AT),
                   sort_keys=True)
    b = json.dumps(ps.get_portfolio_analysis(reordered, GEN_AT),
                   sort_keys=True)
    assert a == b


def test_generated_at_injected_only():
    out = ps.get_portfolio_analysis(fake_providers())
    assert out["generated_at"] is None
    src = Path("portfolio_service.py").read_text(encoding="utf-8")
    for marker in ("datetime.now", "time.time", "utcnow", "uuid",
                   "random"):
        assert marker not in src, marker


# ── Delegasyon ve sınırlar ───────────────────────────────────────────

def test_core_is_sole_calculation_authority(monkeypatch):
    called = {}
    real = pi.analyze_portfolio

    def spy(inputs):
        called["inputs"] = copy.deepcopy(inputs)
        return real(inputs)
    monkeypatch.setattr(ps.portfolio_intelligence,
                        "analyze_portfolio", spy)
    ps.get_portfolio_analysis(fake_providers(), GEN_AT)
    assert called["inputs"]["equity"]["nav_usdt"] == "1000"


def test_service_has_no_portfolio_math():
    """Serviste aritmetik operatörlü portföy hesabı bulunmaz."""
    tree = ast.parse(Path("portfolio_service.py")
                     .read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.BinOp) or not isinstance(
            node.op, (ast.Mult, ast.Div, ast.Sub)), \
            "serviste çarpma/bölme/çıkarma yasak (hesap çekirdekte)"
    src = Path("portfolio_service.py").read_text(encoding="utf-8")
    for banned in ("hhi", "weight_pct", "health_score", "quantize"):
        assert banned not in src, banned


def test_risk_engine_read_only_mapping():
    risk = ps.map_risk_view(
        {"HIGH_EXPOSURE_PERCENT": "150", "DRAWDOWN_WARN_PERCENT": "-10",
         "POSITION_CRITICAL_PERCENT": "60"}, "3")
    assert risk == {"drawdown_pct": "3", "thresholds": {
        "max_net_exposure_pct": "150", "max_drawdown_pct": "10",
        "max_concentration_pct": "60"}}
    # eksik eşik → null (yeniden tanım yok)
    empty = ps.map_risk_view({}, None)
    assert empty["thresholds"] == {"max_net_exposure_pct": None,
                                   "max_drawdown_pct": None,
                                   "max_concentration_pct": None}


def test_position_mapper_skips_flat_and_uses_abs():
    rows = [
        {"symbol": "BTCUSDT", "direction": "LONG",
         "position_amt": "0.004", "entry_price": "1", "mark_price": "2",
         "leverage": "3"},
        {"symbol": "ETHUSDT", "direction": "SHORT",
         "position_amt": "-0.05", "entry_price": "4", "mark_price": "5",
         "leverage": "6"},
        {"symbol": "XRPUSDT", "direction": "FLAT", "position_amt": "0"},
        {"symbol": "ADAUSDT", "direction": "LONG", "position_amt": "0"},
        "bozuk",
    ]
    mapped = ps.map_positions(rows)
    assert [(m["symbol"], m["side"], m["quantity"]) for m in mapped] == \
        [("BTCUSDT", "LONG", "0.004"), ("ETHUSDT", "SHORT", "0.05")]


def test_account_mapper_honest_nulls():
    eq = ps.map_account_to_equity({"usdt_margin_balance": "1000",
                                   "usdt_available_balance": "400",
                                   "unrealized_pnl": "-5"})
    assert eq["nav_usdt"] == "1000"
    assert eq["cash_usdt"] == "400"
    assert eq["realized_pnl"] is None    # bu görünümde yok → null
    assert eq["total_fees"] is None
    assert ps.map_account_to_equity(None)["nav_usdt"] is None


def test_default_providers_shape_without_live_calls():
    providers = ps.build_default_providers()
    assert sorted(providers) == ["equity", "positions", "risk"]
    assert all(callable(p) for p in providers.values())


# ── Mimari yasaklar (statik) ─────────────────────────────────────────

def test_no_banned_imports_or_calls():
    tree = ast.parse(Path("portfolio_service.py")
                     .read_text(encoding="utf-8"))
    banned = {"requests", "websocket", "socket", "subprocess", "pickle",
              "marshal", "ctypes", "flask", "threading", "sched",
              "intelligence_timeline", "os", "io", "pathlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] not in banned, node.module
        elif isinstance(node, ast.Call) and \
                isinstance(node.func, ast.Name):
            assert node.func.id not in ("eval", "exec", "open",
                                        "__import__", "compile")
    src = Path("portfolio_service.py").read_text(encoding="utf-8")
    for marker in ("binance", "Thread(", "request."):
        assert marker not in src, marker
    # append_snapshot: kod olarak ASLA çağrılmaz (yorum/docstring hariç)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) \
                else getattr(node.func, "id", None)
            assert name != "append_snapshot"


def test_no_global_mutable_state():
    out1 = ps.get_portfolio_analysis(fake_providers(), GEN_AT)
    boom = {"equity": lambda: (_ for _ in ()).throw(OSError()),
            "positions": lambda: None, "risk": lambda: None}
    ps.get_portfolio_analysis(boom, GEN_AT)  # kirletme denemesi
    out2 = ps.get_portfolio_analysis(fake_providers(), GEN_AT)
    assert json.dumps(out1, sort_keys=True) == \
        json.dumps(out2, sort_keys=True)


def test_no_snapshot_embedding_or_timeline_access(tmp_path, monkeypatch):
    import intelligence_timeline as tl
    calls = []
    monkeypatch.setattr(tl, "append_snapshot",
                        lambda *a, **k: calls.append(1))
    ps.get_portfolio_analysis(fake_providers(), GEN_AT)
    assert calls == []
