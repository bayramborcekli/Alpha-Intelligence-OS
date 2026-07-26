"""Mission 1600 / Agent 03 — Automation Service Layer testleri."""

from __future__ import annotations

import ast
import fcntl
import inspect
import json
from decimal import Decimal

import pytest

import automation_engine as ae
import automation_service as asv


class FakeIntelService:
    """Mevcut IntelligenceService.get_summary sözleşmesini taklit eder."""

    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.calls = 0

    def get_summary(self, generated_at=None):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.payload


def _payload(**over):
    base = {
        "ok": True,
        "read_only": True,
        "status": "OK",
        "partial": False,
        "generated_at": "2026-07-26T12:00:00+00:00",
        "insights": [{"code": "I1"}],
        "recommendations": [{"code": "R1", "advisory_only": True}],
        "warnings": ["W1"],
        "freshness": [{"source": "risk_engine", "status": "FRESH"}],
        "portfolio_summary": {"total_value": Decimal("250.75")},
        "risk_summary": {"score": 42},
        "risk_explanations": [{"code": "E1"}],
        "source_errors": {},
        "advisory_only": True,
    }
    base.update(over)
    return base


@pytest.fixture()
def paths(tmp_path):
    return tmp_path / "state.json", tmp_path / "history.jsonl"


ENABLED = {"enabled": True, "interval_minutes": 60, "timeout_seconds": 120}


# ── service success + mapping ────────────────────────────────────────

def test_service_success_normalized():
    snap = asv.execute_intelligence_run(FakeIntelService(_payload()))
    assert snap["status"] == "OK"
    assert snap["advisory_only"] is True
    # Timeline beyaz-listesi dışı alanlar taşınmaz
    assert "ok" not in snap and "source_errors" not in snap and \
        "read_only" not in snap


def test_recommendation_mapping_passthrough():
    snap = asv.execute_intelligence_run(FakeIntelService(_payload()))
    assert snap["recommendations"] == [{"code": "R1", "advisory_only": True}]
    assert snap["insights"] == [{"code": "I1"}]


def test_risk_mapping_passthrough_decimal_preserved():
    snap = asv.execute_intelligence_run(FakeIntelService(_payload()))
    assert snap["risk_summary"] == {"score": 42}
    assert snap["risk_explanations"] == [{"code": "E1"}]
    assert snap["portfolio_summary"]["total_value"] == Decimal("250.75")


def test_summary_generation_partial():
    snap = asv.execute_intelligence_run(
        FakeIntelService(_payload(status="PARTIAL", partial=True)))
    assert snap["status"] == "PARTIAL" and snap["partial"] is True


# ── service unavailable / sterile error ──────────────────────────────

def test_service_unavailable_when_not_ok():
    snap = asv.execute_intelligence_run(
        FakeIntelService(_payload(ok=False)))
    assert snap["status"] == "UNAVAILABLE"


def test_unknown_status_passthrough_not_recordable(paths):
    state, hist = paths
    svc = FakeIntelService(_payload(status="STALE"))
    out = asv.run_automation(service=svc, config=ENABLED,
                             state_path=state, history_path=hist)
    assert out["appended"] is False
    assert out["error_code"] == "INVALID_RESULT"   # core kaydetmez


def test_intelligence_exception_sterile():
    snap = asv.execute_intelligence_run(
        FakeIntelService(exc=RuntimeError("secret=API_KEY /home/x")))
    assert snap == {"status": "FAILED", "advisory_only": True}
    text = json.dumps(snap)
    assert "secret" not in text and "API_KEY" not in text


def test_malformed_payload_unavailable():
    for bad in (None, [], "x", 42):
        assert asv.normalize_summary(bad)["status"] == "UNAVAILABLE"


# ── deterministic output ─────────────────────────────────────────────

def test_deterministic_output():
    a = asv.execute_intelligence_run(FakeIntelService(_payload()))
    b = asv.execute_intelligence_run(FakeIntelService(_payload()))
    assert a == b


# ── timeout propagation (core sınıflandırır) ─────────────────────────

def test_timeout_propagation_no_append(paths):
    state, hist = paths
    ticks = iter([0.0, 999.0])
    out = ae.run_once(asv.build_summary_provider(FakeIntelService(_payload())),
                      config=ENABLED, state_path=state, history_path=hist,
                      clock=lambda: next(ticks))
    assert out["error_code"] == "TIMEOUT" and not hist.exists()


# ── duplicate call protection ────────────────────────────────────────

def test_duplicate_call_protection(paths):
    state, hist = paths
    lock = state.with_name(state.name + ".lock")
    holder = open(lock, "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        out = asv.run_automation(service=FakeIntelService(_payload()),
                                 config=ENABLED, state_path=state,
                                 history_path=hist)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    assert out["skip_reason"] == "DUPLICATE_RUN"
    assert not hist.exists()


# ── integration with automation core ─────────────────────────────────

def test_integration_success_appends_via_core(paths):
    state, hist = paths
    svc = FakeIntelService(_payload())
    out = asv.run_automation(service=svc, config=ENABLED,
                             state_path=state, history_path=hist)
    assert out["appended"] is True and out["final_state"] == "succeeded"
    assert svc.calls == 1
    rec = json.loads(hist.read_text().splitlines()[0])
    assert rec["advisory_only"] is True and rec["read_only"] is True
    assert rec["portfolio_summary"]["total_value"] == "250.75"
    assert rec["recommendations"] == [{"code": "R1", "advisory_only": True}]


def test_integration_failure_no_snapshot(paths):
    state, hist = paths
    out = asv.run_automation(service=FakeIntelService(exc=ValueError("x")),
                             config=ENABLED, state_path=state,
                             history_path=hist)
    assert out["appended"] is False
    assert out["error_code"] == "INVALID_RESULT"   # FAILED → kaydedilmez
    assert not hist.exists()
    assert ae.load_state(state)["state"] == "failed"


def test_scheduler_tick_wrapper(paths):
    state, hist = paths
    out = asv.automation_scheduler_tick(service=FakeIntelService(_payload()),
                                        config=ENABLED, state_path=state,
                                        history_path=hist, now_epoch=100.0)
    assert out["appended"] is True
    out2 = asv.automation_scheduler_tick(service=FakeIntelService(_payload()),
                                         config=ENABLED, state_path=state,
                                         history_path=hist, now_epoch=101.0)
    assert out2["skip_reason"] == "NOT_DUE"


# ── sınırlar (statik) ────────────────────────────────────────────────

def test_service_layer_never_appends_snapshot():
    # Kayıt yalnız core'da — servis kodunda append_snapshot ÇAĞRISI yok
    tree = ast.parse(inspect.getsource(asv))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "append_snapshot"
        if isinstance(node, ast.Name):
            assert node.id != "append_snapshot"


def test_no_flask_routes_or_network_imports():
    tree = ast.parse(inspect.getsource(asv))
    banned = {"flask", "requests", "urllib", "socket", "http",
              "binance", "ccxt", "openai", "anthropic"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            continue
        assert not (names & banned), names
    src = inspect.getsource(asv)
    for word in ("@app.route", "Blueprint", "post_fork", "start_loop"):
        assert word not in src, word


def test_default_service_uses_existing_providers():
    # Gerçek bağlanış: mevcut dashboard_api/risk_api sağlayıcıları
    svc = asv._default_service()
    import dashboard_api, risk_api
    assert svc._account is dashboard_api.global_account
    assert svc._positions is dashboard_api.global_positions
    assert svc._risk is risk_api.summary
    assert svc._alerts is risk_api.alerts
