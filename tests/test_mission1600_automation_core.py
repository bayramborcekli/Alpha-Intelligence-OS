"""Mission 1600 / Agent 02 — Automation çekirdeği testleri."""

from __future__ import annotations

import ast
import fcntl
import inspect
import json
import threading
from decimal import Decimal
from pathlib import Path

import pytest

import automation_engine as ae
import intelligence_timeline as tl


def _ok_summary(**over):
    base = {
        "generated_at": "2026-07-26T12:00:00+00:00",
        "status": "OK",
        "partial": False,
        "insights": [],
        "recommendations": [],
        "warnings": [],
        "portfolio_summary": {"total_value": Decimal("100.5")},
        "risk_summary": {"score": 70},
        "risk_explanations": [],
        "freshness": [],
        "advisory_only": True,
    }
    base.update(over)
    return base


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    state = tmp_path / "automation_state.json"
    hist = tmp_path / "history.jsonl"
    monkeypatch.delenv("ALPHA_AUTOMATION_ENABLED", raising=False)
    monkeypatch.delenv("ALPHA_AUTOMATION_INTERVAL_MINUTES", raising=False)
    monkeypatch.delenv("ALPHA_AUTOMATION_TIMEOUT_SECONDS", raising=False)
    return state, hist


ENABLED = {"enabled": True, "interval_minutes": 60, "timeout_seconds": 120}


# ── Yapılandırma ─────────────────────────────────────────────────────

def test_config_defaults_safe(monkeypatch):
    monkeypatch.delenv("ALPHA_AUTOMATION_ENABLED", raising=False)
    cfg = ae.load_config()
    assert cfg["enabled"] is False          # varsayılan: kapalı
    assert cfg["interval_minutes"] == 60
    assert cfg["timeout_seconds"] == 120


def test_config_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "YES")   # 'true' değil
    monkeypatch.setenv("ALPHA_AUTOMATION_INTERVAL_MINUTES", "abc")
    monkeypatch.setenv("ALPHA_AUTOMATION_TIMEOUT_SECONDS", "1")
    cfg = ae.load_config()
    assert cfg["enabled"] is False
    assert cfg["interval_minutes"] == 60
    assert cfg["timeout_seconds"] == ae.MIN_TIMEOUT_SECONDS  # alt sınır


# ── Durum makinesi ───────────────────────────────────────────────────

def test_valid_transitions():
    s = dict(ae.load_state("/nonexistent"))
    assert s["state"] == "disabled"
    s = ae.transition(s, "scheduled")
    s = ae.transition(s, "running")
    s = ae.transition(s, "succeeded")
    s = ae.transition(s, "running")
    s = ae.transition(s, "failed")
    s = ae.transition(s, "disabled")
    assert s["state"] == "disabled"


@pytest.mark.parametrize("frm,to", [
    ("disabled", "running"), ("disabled", "succeeded"),
    ("scheduled", "succeeded"), ("running", "running"),
    ("running", "scheduled"), ("succeeded", "scheduled"),
    ("failed", "succeeded"), ("running", "disabled"),
])
def test_invalid_transitions_rejected(frm, to):
    with pytest.raises(ValueError):
        ae.transition({"state": frm}, to)


def test_corrupt_state_file_yields_safe_empty(paths):
    state, _ = paths
    state.write_text("{broken json", encoding="utf-8")
    assert ae.load_state(state)["state"] == "disabled"
    state.write_text('{"state": "hacked"}', encoding="utf-8")
    assert ae.load_state(state)["state"] == "disabled"


# ── Runner: başarı → yalnız append_snapshot ──────────────────────────

def test_successful_run_appends_exactly_once(paths):
    state, hist = paths
    out = ae.run_once(_ok_summary, config=ENABLED, state_path=state,
                      history_path=hist)
    assert out == {"ran": True, "skip_reason": None, "appended": True,
                   "error_code": None, "run_id": out["run_id"],
                   "final_state": "succeeded"}
    lines = [l for l in hist.read_text().splitlines() if l]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["portfolio_summary"]["total_value"] == "100.5"  # Decimal-string
    st = ae.load_state(state)
    assert st["state"] == "succeeded"
    assert st["last_snapshot_recorded"] is True
    assert st["last_error_code"] is None


def test_partial_status_is_recorded(paths):
    state, hist = paths
    out = ae.run_once(lambda: _ok_summary(status="PARTIAL", partial=True),
                      config=ENABLED, state_path=state, history_path=hist)
    assert out["appended"] is True


# ── Runner: başarısızlık → snapshot YOK ──────────────────────────────

def test_provider_exception_no_append_sterile(paths):
    state, hist = paths

    def boom():
        raise RuntimeError("secret=BINANCE_KEY /etc/passwd")

    out = ae.run_once(boom, config=ENABLED, state_path=state,
                      history_path=hist)
    assert out["appended"] is False
    assert out["error_code"] == "EXECUTION_FAILED"
    assert not hist.exists()
    raw = Path(state).read_text(encoding="utf-8")
    assert "secret" not in raw and "passwd" not in raw  # sterile durum


def test_unavailable_status_not_recorded(paths):
    state, hist = paths
    out = ae.run_once(lambda: _ok_summary(status="UNAVAILABLE"),
                      config=ENABLED, state_path=state, history_path=hist)
    assert out["error_code"] == "INVALID_RESULT"
    assert not hist.exists()


def test_timeout_no_append(paths):
    state, hist = paths
    ticks = iter([0.0, 999.0])
    out = ae.run_once(_ok_summary, config=ENABLED, state_path=state,
                      history_path=hist, clock=lambda: next(ticks))
    assert out["error_code"] == "TIMEOUT"
    assert out["appended"] is False and not hist.exists()


def test_append_failure_no_retry(paths, monkeypatch):
    state, hist = paths
    calls = []

    def failing_append(snapshot, path=None):
        calls.append(1)
        raise tl.TimelineError("HISTORY_FULL", "dolu")

    monkeypatch.setattr(ae.intelligence_timeline, "append_snapshot",
                        failing_append)
    out = ae.run_once(_ok_summary, config=ENABLED, state_path=state,
                      history_path=hist)
    assert out["error_code"] == "APPEND_FAILED"
    assert len(calls) == 1                      # retry YOK
    assert ae.load_state(state)["state"] == "failed"


# ── Kilit: duplicate execution engeli ────────────────────────────────

def test_concurrent_run_skipped_by_lock(paths):
    state, hist = paths
    lock_file = state.with_name(state.name + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_file, "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        out = ae.run_once(_ok_summary, config=ENABLED, state_path=state,
                          history_path=hist)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    assert out == {"ran": False, "skip_reason": "DUPLICATE_RUN",
                   "appended": False, "error_code": None}
    assert not hist.exists()


def test_threaded_duplicate_only_one_appends(paths):
    state, hist = paths
    barrier = threading.Barrier(2)
    results = []

    def slow_summary():
        import time
        time.sleep(0.2)
        return _ok_summary()

    def worker():
        barrier.wait()
        results.append(ae.run_once(slow_summary, config=ENABLED,
                                   state_path=state, history_path=hist))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    appended = [r for r in results if r.get("appended")]
    skipped = [r for r in results if r.get("skip_reason") == "DUPLICATE_RUN"]
    assert len(appended) == 1 and len(skipped) == 1
    assert len([l for l in hist.read_text().splitlines() if l]) == 1


# ── Interrupted / restart ────────────────────────────────────────────

def test_recover_interrupted_marks_failed_no_append(paths):
    state, hist = paths
    ae._save_state({**ae._EMPTY_STATE, "state": "running",
                    "run_id": "x"}, state)
    st = ae.recover_interrupted(state)
    assert st["state"] == "failed"
    assert st["last_error_code"] == "INTERRUPTED"
    assert st["last_snapshot_recorded"] is False
    assert not hist.exists()                    # otomatik append YOK


def test_recover_skips_actively_locked_run(paths):
    state, hist = paths
    ae._save_state({**ae._EMPTY_STATE, "state": "running",
                    "run_id": "aktif"}, state)
    lock_file = state.with_name(state.name + ".lock")
    holder = open(lock_file, "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        st = ae.recover_interrupted(state)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    assert st["state"] == "running"             # aktif koşuya dokunulmaz
    assert ae.load_state(state)["last_error_code"] is None


def test_recover_noop_when_not_running(paths):
    state, _ = paths
    ae._save_state({**ae._EMPTY_STATE, "state": "succeeded"}, state)
    assert ae.recover_interrupted(state)["state"] == "succeeded"


# ── Scheduler / coordinator ──────────────────────────────────────────

def test_should_run_disabled_and_interval():
    cfg = dict(ENABLED)
    st = dict(ae._EMPTY_STATE)
    assert ae.should_run(st, {**cfg, "enabled": False}, 0, None) == \
        (False, "DISABLED")
    assert ae.should_run(st, cfg, 0, None) == (True, None)      # hiç koşmadı
    assert ae.should_run(st, cfg, 1000.0, 500.0) == (False, "NOT_DUE")
    assert ae.should_run(st, cfg, 500.0 + 3600, 500.0) == (True, None)
    assert ae.should_run({**st, "state": "running"}, cfg, 1e9, None) == \
        (False, "DUPLICATE_RUN")


def test_scheduler_tick_runs_when_due(paths):
    state, hist = paths
    out = ae.scheduler_tick(_ok_summary, config=ENABLED, state_path=state,
                            history_path=hist, now_epoch=1000.0)
    assert out["appended"] is True
    # Hemen ikinci vuruş: vade dolmadı
    out2 = ae.scheduler_tick(_ok_summary, config=ENABLED, state_path=state,
                             history_path=hist, now_epoch=1001.0)
    assert out2 == {"ran": False, "skip_reason": "NOT_DUE",
                    "appended": False, "error_code": None}


def test_scheduler_tick_disabled_never_runs(paths):
    state, hist = paths
    out = ae.scheduler_tick(_ok_summary,
                            config={**ENABLED, "enabled": False},
                            state_path=state, history_path=hist,
                            now_epoch=0.0)
    assert out["skip_reason"] == "DISABLED" and not hist.exists()


def test_start_loop_recovers_and_stops(paths):
    state, hist = paths
    ae._save_state({**ae._EMPTY_STATE, "state": "running"}, state)
    stop = threading.Event()
    th = ae.start_loop(_ok_summary, state_path=state, history_path=hist,
                       poll_seconds=0.05, stop_event=stop)
    import time
    time.sleep(0.2)
    stop.set()
    th.join(timeout=2)
    assert not th.is_alive()
    st = ae.load_state(state)
    assert st["last_error_code"] == "INTERRUPTED"   # kurtarma çalıştı
    assert not hist.exists()                        # varsayılan config kapalı


# ── Determinizm ──────────────────────────────────────────────────────

def test_deterministic_same_input_same_record(tmp_path):
    recs = []
    for i in range(2):
        state = tmp_path / f"s{i}.json"
        hist = tmp_path / f"h{i}.jsonl"
        ae.run_once(_ok_summary, config=ENABLED, state_path=state,
                    history_path=hist, now_iso="2026-07-26T12:00:00+00:00")
        recs.append(hist.read_bytes())
    assert recs[0] == recs[1]


# ── Güvenlik sınırları (statik) ──────────────────────────────────────

def test_no_exchange_network_llm_imports():
    src = inspect.getsource(ae)
    tree = ast.parse(src)
    banned = {"requests", "urllib", "socket", "http", "websocket",
              "binance", "ccxt", "openai", "anthropic"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            continue
        assert not (names & banned), names
    # Tanımlayıcı düzeyinde yasak (docstring'ler hariç)
    idents = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            idents.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            idents.add(node.attr.lower())
    for word in ("ledger", "audit", "exchange", "_signed", "order"):
        assert not any(word in i for i in idents), word


def test_timeline_module_untouched_appends_only():
    # Otomasyon yalnız resmî append yüzeyini çağırır
    src = inspect.getsource(ae)
    assert "append_snapshot" in src
    for forbidden in ("truncate", "writelines", "unlink(", "remove("):
        assert forbidden not in src


def test_state_file_content_is_minimal_and_sterile(paths):
    state, hist = paths
    ae.run_once(_ok_summary, config=ENABLED, state_path=state,
                history_path=hist)
    data = json.loads(state.read_text())
    assert set(data) == set(ae._EMPTY_STATE)
    raw = state.read_text().lower()
    for w in ("secret", "token", "api_key", "password"):
        assert w not in raw
