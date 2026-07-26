"""Mission 1600 / Agent 06 — Automation Export testleri.

Export yalnız automation_engine durum okuma sözleşmesini kullanır;
koşu başlatmaz, snapshot yazmaz, Intelligence/Exchange çağırmaz.
"""

import ast
import json

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import automation_engine
import automation_export_api as aex

PASSWORD = "automation-export-parola-1"
HASH = generate_password_hash(PASSWORD)


@pytest.fixture
def client(monkeypatch, tmp_path):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m1600ex_attempts.db")
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "automation_state.json"))
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


def _seed_state(tmp_path, **over):
    st = automation_engine.load_state()
    st.update({"state": "succeeded", "run_id": "run-abc",
               "last_run_started_at": "2026-07-26T10:00:00+00:00",
               "last_run_finished_at": "2026-07-26T10:00:07+00:00",
               "last_run_status": "succeeded", "last_error_code": None,
               "last_snapshot_recorded": True}, **over)
    automation_engine._save_state(st)
    return st


class TestAuth:
    def test_anonymous_denied(self, client):
        r = client.get("/api/automation/export/status")
        assert r.status_code == 401

    def test_v1_anonymous_denied(self, client):
        r = client.get("/api/v1/automation/export/status")
        assert r.status_code == 401

    def test_authenticated_success(self, client):
        _login(client)
        r = client.get("/api/automation/export/status")
        assert r.status_code == 200


class TestJsonExport:
    def test_json_default_format(self, client, tmp_path):
        _seed_state(tmp_path)
        _login(client)
        r = client.get("/api/automation/export/status")
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("application/json")
        d = json.loads(r.get_data(as_text=True))
        assert d["ok"] is True and d["read_only"] is True
        assert d["status"]["run_id"] == "run-abc"
        assert d["status"]["last_snapshot_recorded"] is True

    def test_json_field_whitelist_exact(self, client, tmp_path):
        _seed_state(tmp_path)
        _login(client)
        r = client.get("/api/automation/export/status?format=json")
        d = json.loads(r.get_data(as_text=True))
        assert set(d["status"].keys()) == set(aex.STATUS_FIELDS)

    def test_json_deterministic(self, client, tmp_path):
        _seed_state(tmp_path)
        _login(client)
        a = client.get("/api/automation/export/status?format=json").data
        b = client.get("/api/automation/export/status?format=json").data
        assert a == b

    def test_json_matches_status_api_contract(self, client, tmp_path):
        _seed_state(tmp_path)
        _login(client)
        ex = json.loads(client.get(
            "/api/automation/export/status").get_data(as_text=True))
        st = json.loads(client.get(
            "/api/automation/status").get_data(as_text=True))
        for f in aex.STATUS_FIELDS:
            assert ex["status"][f] == st[f], f

    def test_headers(self, client, tmp_path):
        _seed_state(tmp_path)
        _login(client)
        r = client.get("/api/automation/export/status")
        assert r.headers["Cache-Control"] == "no-store, private"
        assert r.headers["Content-Disposition"] == \
            'attachment; filename="automation_status.json"'
        assert r.headers["X-Content-Type-Options"] == "nosniff"


class TestCsvExport:
    def test_csv_success(self, client, tmp_path):
        _seed_state(tmp_path)
        _login(client)
        r = client.get("/api/automation/export/status?format=csv")
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/csv")
        assert r.headers["Content-Disposition"] == \
            'attachment; filename="automation_status.csv"'
        body = r.data.decode("utf-8")
        assert body.startswith("\ufeff")
        assert "run-abc" in body

    def test_csv_stable_column_and_row_order(self, client, tmp_path):
        _seed_state(tmp_path)
        _login(client)
        body = client.get(
            "/api/automation/export/status?format=csv"
        ).data.decode("utf-8").lstrip("\ufeff")
        lines = [l for l in body.split("\r\n") if l]
        assert lines[0] == "field,value"
        fields = [l.split(",", 1)[0] for l in lines[1:]]
        assert fields == list(aex.STATUS_FIELDS)

    def test_csv_deterministic(self, client, tmp_path):
        _seed_state(tmp_path)
        _login(client)
        a = client.get("/api/automation/export/status?format=csv").data
        b = client.get("/api/automation/export/status?format=csv").data
        assert a == b

    def test_csv_unknowns_are_dash_not_zero(self, client, tmp_path):
        _login(client)  # hiç koşu yok — alanlar None
        body = client.get(
            "/api/automation/export/status?format=csv"
        ).data.decode("utf-8")
        assert "—" in body
        for line in body.splitlines():
            if line.startswith("run_id") or line.startswith("last_"):
                assert not line.endswith(",0")

    def test_csv_formula_injection_neutralized(self, client, tmp_path):
        _seed_state(tmp_path, run_id="=HYPERLINK(evil)",
                    last_error_code="+CMD")
        _login(client)
        body = client.get(
            "/api/automation/export/status?format=csv"
        ).data.decode("utf-8")
        assert "'=HYPERLINK(evil)" in body
        assert "'+CMD" in body
        assert "\n=" not in body and ",=" not in body

    def test_csv_newline_and_quote_escaping(self, client, tmp_path):
        _seed_state(tmp_path, run_id='kotu"deger\nsatir')
        _login(client)
        body = client.get(
            "/api/automation/export/status?format=csv"
        ).data.decode("utf-8")
        # csv modülü alıntılar: tırnak ikilenir, satır alan içinde kalır
        assert '"kotu""deger\nsatir"' in body


class TestFormatValidation:
    @pytest.mark.parametrize("fmt", ["xml", "pdf", "xlsx", "'; DROP", ""])
    def test_unsupported_format_rejected(self, client, fmt):
        _login(client)
        r = client.get("/api/automation/export/status?format=" + fmt)
        if fmt == "":
            assert r.status_code == 200  # boş → varsayılan json
        else:
            assert r.status_code == 400
            d = r.get_json()
            assert d["error"]["code"] == "INVALID_FORMAT"


class TestFailureBehavior:
    def test_unavailable_source_sterile_503(self, client, monkeypatch):
        _login(client)
        monkeypatch.setattr(automation_engine, "load_state",
                            lambda *a, **k: (_ for _ in ()).throw(
                                OSError("disk /gizli/yol hatasi")))
        r = client.get("/api/automation/export/status")
        assert r.status_code == 503
        body = r.get_data(as_text=True)
        assert "STATUS_UNAVAILABLE" in body
        assert "gizli" not in body and "OSError" not in body

    def test_malformed_state_sterile(self, client, tmp_path, monkeypatch):
        (tmp_path / "automation_state.json").write_text("{bozuk json",
                                                        encoding="utf-8")
        _login(client)
        r = client.get("/api/automation/export/status")
        # load_state bozuk dosyada güvenli varsayılana döner ya da
        # sterile 503 üretir; her iki durumda sızıntı yoktur
        assert r.status_code in (200, 503)
        body = r.get_data(as_text=True)
        assert "Traceback" not in body and "bozuk" not in body

    def test_export_exception_sterile(self, client, monkeypatch):
        _login(client)
        monkeypatch.setattr(automation_engine, "load_config",
                            lambda: (_ for _ in ()).throw(
                                RuntimeError("ic detay")))
        r = client.get("/api/automation/export/status")
        assert r.status_code == 503
        assert "ic detay" not in r.get_data(as_text=True)


class TestSecurityBoundaries:
    def test_no_secret_or_path_leak(self, client, tmp_path):
        _seed_state(tmp_path)
        _login(client)
        for fmt in ("json", "csv"):
            body = client.get(
                "/api/automation/export/status?format=" + fmt
            ).data.decode("utf-8").lower()
            for banned in ("api_key", "api_secret", "password", "secret",
                           "session", "csrf", "/home/", "/tmp/", ".lock",
                           "pid", "thread", "traceback"):
                assert banned not in body, (fmt, banned)

    def test_module_does_not_call_forbidden(self):
        with open("automation_export_api.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute):
                    called.add(fn.attr)
                elif isinstance(fn, ast.Name):
                    called.add(fn.id)
        for banned in ("append_snapshot", "run_automation", "run_once",
                       "scheduler_tick", "automation_scheduler_tick",
                       "execute_intelligence_run", "start_loop",
                       "IntelligenceService", "get_summary"):
            assert banned not in called, banned

    def test_module_imports_are_read_only(self):
        with open("automation_export_api.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        for banned in ("automation_service", "intelligence_service",
                       "intelligence_timeline", "binance_client",
                       "exchange", "requests", "urllib"):
            assert not any(banned in m for m in imported), banned

    def test_route_does_not_write_state(self, client, tmp_path):
        st_before = _seed_state(tmp_path)
        _login(client)
        client.get("/api/automation/export/status?format=csv")
        client.get("/api/automation/export/status?format=json")
        assert automation_engine.load_state() == st_before


class TestHistoryDecision:
    def test_history_endpoint_not_added(self, client):
        _login(client)
        # History modeli repo'da yok — endpoint bilinçli olarak eklenmedi
        r = client.get("/api/automation/export/history")
        assert r.status_code == 404
        rules = {str(r) for r in flask_app.app.url_map.iter_rules()}
        assert not any("automation/export/history" in x for x in rules)
