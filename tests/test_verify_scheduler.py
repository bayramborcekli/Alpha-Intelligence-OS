# -*- coding: utf-8 -*-
"""verify_scheduler.py saha aracının CI birim testleri (Task 138).

Saha aracı Windows'ta elle koşturulur; ama çekirdek mantık
(zamanlayıcı kanonik durum kontrolü, evren dürüstlük kuralları,
snapshot alan çıkarımı, HTTP istemcisi + login) mock HTTP sunucu
ile burada otomatik doğrulanır — verify_dual_model deseninin
(tests/test_verify_dual_model.py) birebir uygulamasıdır.
"""
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "windows" / "verify_scheduler.py"

_spec = importlib.util.spec_from_file_location("verify_scheduler", TOOL)
vs = importlib.util.module_from_spec(_spec)
sys.modules["verify_scheduler"] = vs
_spec.loader.exec_module(vs)


# ── Durum üreticileri ─────────────────────────────────────────────

def make_snapshot(pref="RUNNING", state="RUNNING", interval=5,
                  last_run="2026-07-30T00:00:00Z",
                  next_run="2026-07-30T00:05:00Z",
                  pipeline="GREEN", universe_size=12,
                  reason_code=None, refresh_result="COMPLETED",
                  last_error=None):
    """check_snapshot() girdisi: sağlıklı varsayılanlarla snapshot."""
    return {
        "overall_pipeline": pipeline,
        "pipeline_blockers": [],
        "analysis_scheduler": state,
        "analysis_scheduler_detail": {
            "preference": pref,
            "state": state,
            "interval_minutes": interval,
            "last_run": last_run,
            "next_run": next_run,
            "last_result": "OK",
            "last_error": last_error,
        },
        "scan_interval": interval,
        "universe_size": universe_size,
        "universe_reason_code": reason_code,
        "universe_refresh_result": refresh_result,
    }


def make_api_state(**kw):
    """/api/paper/state gövdesi (snapshot() girdisi)."""
    s = make_snapshot(**kw)
    s["extra_field_ignored"] = "x"  # snapshot() yalnız gerekli alanları alır
    return s


# ── check_snapshot: zamanlayıcı kanonik durum ─────────────────────

class TestSchedulerState:
    def test_healthy_running_expanded_universe_passes(self):
        assert vs.check_snapshot(make_snapshot()) == []

    def test_wrong_interval_fails(self):
        fails = vs.check_snapshot(make_snapshot(interval=15))
        assert any("5 dk değil" in f for f in fails)

    def test_last_run_without_next_run_fails(self):
        fails = vs.check_snapshot(make_snapshot(next_run=None))
        assert any("next_run boş" in f for f in fails)

    def test_startup_failed_is_honest_but_green_pipeline_fails(self):
        s = make_snapshot(state="STARTUP_FAILED", pipeline="GREEN",
                          last_error="worker yok")
        fails = vs.check_snapshot(s)
        assert any("false-GREEN" in f for f in fails)

    def test_startup_failed_with_red_pipeline_no_state_fail(self):
        s = make_snapshot(state="STARTUP_FAILED", pipeline="RED",
                          last_error="worker yok")
        fails = vs.check_snapshot(s)
        assert not any("STARTUP_FAILED iken pipeline GREEN" in f
                       for f in fails)

    def test_running_pref_with_unexpected_state_fails(self):
        fails = vs.check_snapshot(make_snapshot(state="IDLE"))
        assert any("kanonik durum RUNNING/STARTUP_FAILED olmalıydı" in f
                   for f in fails)

    def test_stopped_preference_fails(self):
        fails = vs.check_snapshot(make_snapshot(pref="STOPPED",
                                                state="STOPPED"))
        assert any("Tercih STOPPED" in f for f in fails)

    def test_unreadable_preference_fails(self):
        fails = vs.check_snapshot(make_snapshot(pref=None, state=None))
        assert any("tercihi okunamadı" in f for f in fails)


# ── check_snapshot: evren dürüstlük kuralları ─────────────────────

class TestUniverseHonesty:
    def test_not_run_yet_is_never_pass(self):
        s = make_snapshot(universe_size=3, reason_code="NOT_RUN_YET",
                          refresh_result="NOT_RUN_YET", pipeline="RED")
        fails = vs.check_snapshot(s)
        assert any("NOT_RUN_YET" in f and "PASS verilemez" in f
                   for f in fails)

    def test_not_run_yet_with_green_pipeline_is_false_green(self):
        s = make_snapshot(universe_size=3, reason_code="NOT_RUN_YET",
                          refresh_result="NOT_RUN_YET", pipeline="GREEN")
        fails = vs.check_snapshot(s)
        assert any("NOT_RUN_YET iken pipeline GREEN" in f for f in fails)

    def test_expanded_universe_with_reason_code_is_contradiction(self):
        s = make_snapshot(universe_size=10,
                          reason_code="FILTERS_EXCLUDED_ALL")
        fails = vs.check_snapshot(s)
        assert any("çelişkili rozet" in f for f in fails)

    def test_expanded_universe_with_non_completed_result_fails(self):
        s = make_snapshot(universe_size=10, refresh_result="FAILED")
        fails = vs.check_snapshot(s)
        assert any("COMPLETED bekleniyordu" in f for f in fails)

    def test_base_universe_with_honest_reason_passes(self):
        s = make_snapshot(universe_size=3,
                          reason_code="INSUFFICIENT_ELIGIBLE_SYMBOLS",
                          refresh_result="COMPLETED")
        assert vs.check_snapshot(s) == []

    def test_base_universe_refresh_failed_fails(self):
        s = make_snapshot(universe_size=3,
                          reason_code="UNIVERSE_REFRESH_FAILED",
                          refresh_result="FAILED", pipeline="RED")
        fails = vs.check_snapshot(s)
        assert any("FAILED" in f for f in fails)

    def test_base_universe_without_reason_code_is_false_green(self):
        s = make_snapshot(universe_size=3, reason_code=None,
                          refresh_result=None)
        fails = vs.check_snapshot(s)
        assert any("dürüst neden kodu" in f for f in fails)

    def test_unreadable_universe_size_fails(self):
        s = make_snapshot(universe_size=None)
        fails = vs.check_snapshot(s)
        assert any("universe_size okunamadı" in f for f in fails)


# ── Mock HTTP sunucusu: Client / login / snapshot ─────────────────

class _Handler(BaseHTTPRequestHandler):
    state = make_api_state()
    require_login = True

    def log_message(self, *a):  # sessiz
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if getattr(self, "_set_cookie", False):
            self.send_header("Set-Cookie", "session=ok; Path=/")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/login":
            self._send(200,
                       '<form><input name="csrf_token" value="tok123">'
                       "</form>", "text/html")
        elif self.path == "/api/paper/state":
            cookies = self.headers.get("Cookie", "")
            if self.require_login and "session=ok" not in cookies:
                self._send(401, '{"ok": false}')
            else:
                self._send(200, json.dumps(type(self).state))
        else:
            self._send(404, "{}")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        if self.path == "/login" and "csrf_token=tok123" in body:
            self._set_cookie = True
            self._send(200, "ok", "text/html")
        else:
            self._send(400, "bad", "text/html")


@pytest.fixture()
def mock_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


class TestHttpClient:
    def test_login_snapshot_and_checks(self, mock_server, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a: "operator")
        monkeypatch.setattr(vs.getpass, "getpass", lambda *a: "pw")
        c = vs.Client(mock_server)
        vs.login(c)
        s = vs.snapshot(c)
        # snapshot yalnız gerekli alanları çıkarır
        assert "extra_field_ignored" not in s
        assert s["overall_pipeline"] == "GREEN"
        assert s["analysis_scheduler_detail"]["preference"] == "RUNNING"
        assert vs.check_snapshot(s) == []

    def test_snapshot_from_unhealthy_state_fails(self, mock_server,
                                                 monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a: "operator")
        monkeypatch.setattr(vs.getpass, "getpass", lambda *a: "pw")
        old = _Handler.state
        _Handler.state = make_api_state(
            universe_size=3, reason_code="NOT_RUN_YET",
            refresh_result="NOT_RUN_YET")
        try:
            c = vs.Client(mock_server)
            vs.login(c)
            fails = vs.check_snapshot(vs.snapshot(c))
            assert fails  # NOT_RUN_YET asla PASS değil
        finally:
            _Handler.state = old

    def test_non_json_state_raises(self, mock_server, monkeypatch):
        class Broken(_Handler):
            def do_GET(self):
                if self.path == "/api/paper/state":
                    self._send(200, "<html>bozuk</html>", "text/html")
                else:
                    _Handler.do_GET(self)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Broken)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            srv.RequestHandlerClass.require_login = False
            c = vs.Client(f"http://127.0.0.1:{srv.server_address[1]}")
            with pytest.raises(RuntimeError, match="JSON değil"):
                c.get_json("/api/paper/state")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_login_unreachable_raises(self):
        c = vs.Client("http://127.0.0.1:1")
        with pytest.raises((RuntimeError, OSError)):
            vs.login(c)
