"""Mission 1600 / Agent 04 — Automation API & Lifecycle testleri."""

from __future__ import annotations

import ast
import fcntl
import inspect
import json
import textwrap
import threading

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import automation_engine as ae
import automation_service as asv

PASSWORD = "automation-test-parola-1"
HASH = generate_password_hash(PASSWORD)

STATUS_APIS = ["/api/automation/status", "/api/v1/automation/status"]
RUN_APIS = ["/api/automation/run", "/api/v1/automation/run"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m1600_attempts.db")
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "state.json"))
    monkeypatch.setenv("ALPHA_INTELLIGENCE_HISTORY_PATH",
                       str(tmp_path / "history.jsonl"))
    monkeypatch.delenv("ALPHA_AUTOMATION_ENABLED", raising=False)
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True


def _login(c):
    r = c.post("/api/v1/auth/login",
               json={"username": "sahip", "password": PASSWORD})
    assert r.status_code == 200
    return r


def _ok_payload():
    from decimal import Decimal
    return {
        "ok": True, "status": "OK", "partial": False,
        "generated_at": "2026-07-26T12:00:00+00:00",
        "insights": [], "recommendations": [], "warnings": [],
        "freshness": [], "portfolio_summary": {"v": Decimal("1")},
        "risk_summary": {"score": 50}, "risk_explanations": [],
        "advisory_only": True,
    }


class FakeSvc:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc = payload, exc

    def get_summary(self, generated_at=None):
        if self.exc:
            raise self.exc
        return self.payload


def _use_fake_service(monkeypatch, **kw):
    fake = FakeSvc(**kw)
    monkeypatch.setattr(asv, "_service_factory", lambda: fake)


# ── Auth ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", STATUS_APIS)
def test_unauthenticated_status_denied(client, path):
    r = client.get(path)
    assert r.status_code == 401
    assert "traceback" not in r.get_data(as_text=True).lower()


@pytest.mark.parametrize("path", RUN_APIS)
def test_unauthenticated_run_denied(client, path):
    assert client.post(path).status_code == 401


def test_authenticated_status_success(client):
    _login(client)
    r = client.get("/api/automation/status")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True and data["read_only"] is True
    assert data["enabled"] is False          # varsayılan kapalı
    assert data["state"] == "disabled"
    assert data["running"] is False
    assert data["next_due"] is None
    assert r.headers["Cache-Control"] == "no-store, private"


# ── CSRF ─────────────────────────────────────────────────────────────

def test_csrf_missing_denied(client, monkeypatch):
    flask_app.app.config["WTF_CSRF_ENABLED"] = True
    try:
        _login(client)   # login csrf.exempt
        monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
        r = client.post("/api/automation/run")
        assert r.status_code == 400
        assert "CSRF" in json.dumps(r.get_json())   # mevcut _api_error deseni
    finally:
        flask_app.app.config["WTF_CSRF_ENABLED"] = False


def test_csrf_invalid_denied(client, monkeypatch):
    flask_app.app.config["WTF_CSRF_ENABLED"] = True
    try:
        _login(client)
        monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
        r = client.post("/api/automation/run",
                        headers={"X-CSRFToken": "gecersiz-token"})
        assert r.status_code == 400
    finally:
        flask_app.app.config["WTF_CSRF_ENABLED"] = False


# ── Manuel çalıştırma ────────────────────────────────────────────────

def test_disabled_automation_run_503(client):
    _login(client)
    r = client.post("/api/automation/run")
    assert r.status_code == 503
    assert r.get_json()["error"]["code"] == "AUTOMATION_DISABLED"


def test_manual_run_success(client, monkeypatch, tmp_path):
    _login(client)
    monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
    _use_fake_service(monkeypatch, payload=_ok_payload())
    r = client.post("/api/automation/run")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True and data["appended"] is True
    assert data["final_state"] == "succeeded"
    hist = tmp_path / "history.jsonl"
    assert len(hist.read_text().splitlines()) == 1
    # Durum ucu koşuyu yansıtır
    st = client.get("/api/automation/status").get_json()
    assert st["state"] == "succeeded"
    assert st["last_snapshot_recorded"] is True
    assert st["next_due"] is not None


def test_duplicate_run_conflict_409(client, monkeypatch, tmp_path):
    _login(client)
    monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
    _use_fake_service(monkeypatch, payload=_ok_payload())
    lock = tmp_path / "state.json.lock"
    holder = open(lock, "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        r = client.post("/api/automation/run")
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    assert r.status_code == 409
    assert r.get_json()["error"]["code"] == "DUPLICATE_RUN"
    assert not (tmp_path / "history.jsonl").exists()


def test_unavailable_service_no_snapshot(client, monkeypatch, tmp_path):
    _login(client)
    monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
    _use_fake_service(monkeypatch, payload={**_ok_payload(), "ok": False})
    r = client.post("/api/automation/run")
    assert r.status_code == 200
    data = r.get_json()
    assert data["appended"] is False
    assert data["error_code"] == "INVALID_RESULT"
    assert not (tmp_path / "history.jsonl").exists()


def test_sterile_exception_response(client, monkeypatch, tmp_path):
    _login(client)
    monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
    _use_fake_service(monkeypatch,
                      exc=RuntimeError("api_secret=SIZINTI /etc/passwd"))
    r = client.post("/api/automation/run")
    text = r.get_data(as_text=True)
    assert "SIZINTI" not in text and "passwd" not in text and \
        "Traceback" not in text
    assert r.get_json()["appended"] is False
    assert not (tmp_path / "history.jsonl").exists()


def test_deterministic_json(client, monkeypatch):
    _login(client)
    a = client.get("/api/automation/status").get_data(as_text=True)
    b = client.get("/api/automation/status").get_data(as_text=True)
    assert a == b


def test_status_no_secret_or_path_leakage(client):
    _login(client)
    text = client.get("/api/automation/status").get_data(as_text=True).lower()
    for w in ("secret", "token", "password", "/home/", "traceback", ".py"):
        assert w not in text, w


# ── Route sınırları (statik) ─────────────────────────────────────────

def _route_sources():
    src = inspect.getsource(flask_app)
    marker = "Mission 1600 / Agent 04"
    start = src.index(marker)
    end = src.index("Mission 1500.2: Workspace Read-Only API")
    return src[start:end]


def test_route_does_not_call_append_snapshot_or_engine_directly():
    # Yorum satırları hariç: yalnız gerçek kod incelenir
    block = "\n".join(l for l in _route_sources().splitlines()
                      if not l.lstrip().startswith("#"))
    assert "append_snapshot" not in block
    assert "IntelligenceService" not in block
    assert "get_summary" not in block            # engine'e doğrudan çağrı yok
    for banned in ("binance", "_signed", "requests.", "urllib"):
        assert banned not in block


# ── Lifecycle ────────────────────────────────────────────────────────

@pytest.fixture
def clean_thread():
    old = flask_app._automation_thread
    flask_app._automation_thread = None
    yield
    th = flask_app._automation_thread
    if th is not None and th.is_alive():
        th._alpha_stop_event.set()
        th.join(timeout=2)
    flask_app._automation_thread = old


def test_disabled_by_default_no_loop(monkeypatch, clean_thread):
    monkeypatch.delenv("ALPHA_AUTOMATION_ENABLED", raising=False)
    assert flask_app.start_automation_scheduler() is None
    assert flask_app._automation_thread is None


def test_literal_true_starts_loop(monkeypatch, tmp_path, clean_thread):
    monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "s.json"))
    th = flask_app.start_automation_scheduler()
    assert th is not None and th.is_alive() and th.daemon
    th._alpha_stop_event.set()
    th.join(timeout=2)
    assert not th.is_alive()                     # stop_event ile kapanır


def test_second_loop_not_started_same_process(monkeypatch, tmp_path,
                                              clean_thread):
    monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "s.json"))
    t1 = flask_app.start_automation_scheduler()
    t2 = flask_app.start_automation_scheduler()
    assert t1 is t2                              # tekil guard
    t1._alpha_stop_event.set()
    t1.join(timeout=2)


def test_startup_error_does_not_crash(monkeypatch, clean_thread):
    monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
    monkeypatch.setattr(ae, "start_loop",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert flask_app.start_automation_scheduler() is None   # çökmez


def test_active_run_tick_no_duplicate_append(monkeypatch, tmp_path):
    # Aktif koşu sırasında yeni tick: flock DUPLICATE_RUN ile atlar
    state = tmp_path / "s.json"
    hist = tmp_path / "h.jsonl"
    cfg = {"enabled": True, "interval_minutes": 60, "timeout_seconds": 120}
    barrier = threading.Barrier(2)
    results = []

    def slow_provider():
        import time
        time.sleep(0.2)
        return {k: v for k, v in _ok_payload().items() if k != "ok"} | \
            {"status": "OK"}

    def w():
        barrier.wait()
        results.append(ae.run_once(slow_provider, config=cfg,
                                   state_path=state, history_path=hist))

    ts = [threading.Thread(target=w) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in results if r.get("appended")) == 1
    assert len(hist.read_text().splitlines()) == 1


def test_post_fork_wires_automation_scheduler():
    src = open("gunicorn.conf.py", encoding="utf-8").read()
    assert "start_automation_scheduler" in src
    # post_fork içinde olduğunu doğrula
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "post_fork":
            body = ast.unparse(node)
            assert "start_automation_scheduler" in body
            return
    pytest.fail("post_fork bulunamadı")
