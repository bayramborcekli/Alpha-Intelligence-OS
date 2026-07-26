"""Mission 1600 / Agent 07 — Güvenlik Doğrulama testleri.

Mission 1600 bileşenlerinin (Core, Service, API, Lifecycle, UI, Export)
güvenlik garantilerini kanıtlayan statik analiz, secret taraması, dosya
bütünlüğü ve penetrasyon senaryoları. Yeni özellik yoktur — yalnız
doğrulama.
"""

import ast
import json
import threading

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import automation_engine
import automation_export_api as aex
import automation_service

PASSWORD = "automation-sec-parola-1"
HASH = generate_password_hash(PASSWORD)

AUTOMATION_MODULES = ("automation_engine.py", "automation_service.py",
                      "automation_export_api.py")


@pytest.fixture
def client(monkeypatch, tmp_path):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m1600sec_attempts.db")
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "automation_state.json"))
    monkeypatch.setenv("ALPHA_INTELLIGENCE_HISTORY_PATH",
                       str(tmp_path / "history.json"))
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


# ── 10. Statik güvenlik analizi ─────────────────────────────────────

class TestStaticAnalysis:
    BANNED_IMPORTS = ("subprocess", "pickle", "marshal", "ctypes",
                      "requests", "websocket", "websockets", "socket",
                      "importlib")

    def _tree(self, path):
        with open(path, encoding="utf-8") as f:
            return ast.parse(f.read())

    @pytest.mark.parametrize("path", AUTOMATION_MODULES)
    def test_no_banned_imports(self, path):
        for node in ast.walk(self._tree(path)):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for n in names:
                root = n.split(".")[0]
                assert root not in self.BANNED_IMPORTS, (path, n)

    @pytest.mark.parametrize("path", AUTOMATION_MODULES)
    def test_no_dynamic_execution(self, path):
        for node in ast.walk(self._tree(path)):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else (
                    fn.attr if isinstance(fn, ast.Attribute) else "")
                assert name not in ("eval", "exec", "compile",
                                    "__import__", "system", "popen",
                                    "spawn", "fork"), (path, name)

    @pytest.mark.parametrize("path", AUTOMATION_MODULES)
    def test_no_user_controlled_dispatch(self, path):
        """Modüller request nesnesine hiç dokunmaz (yol/dispatch girdisi yok)."""
        with open(path, encoding="utf-8") as f:
            src = f.read()
        assert "request." not in src and "flask" not in src.lower(), path

    def test_route_layer_reads_only_format_param(self):
        """Automation route'ları kullanıcıdan yalnız format parametresi alır.

        AST tabanlı: api_automation_* fonksiyon gövdelerinde request
        nesnesinin tek kullanımı request.args.get("format") olabilir.
        """
        with open("app.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef)
                 and n.name.startswith("api_automation_")]
        assert funcs, "automation route fonksiyonları bulunamadı"
        saw_format = False
        for fn in funcs:
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and \
                        isinstance(node.value, ast.Name) and \
                        node.value.id == "request":
                    assert node.attr == "args", (fn.name, node.attr)
                if isinstance(node, ast.Call) and \
                        isinstance(node.func, ast.Attribute) and \
                        node.func.attr == "get" and \
                        isinstance(node.func.value, ast.Attribute) and \
                        node.func.value.attr == "args":
                    assert [getattr(a, "value", None)
                            for a in node.args][:1] == ["format"]
                    saw_format = True
                name = ""
                if isinstance(node, ast.Call):
                    f2 = node.func
                    name = f2.id if isinstance(f2, ast.Name) else \
                        (f2.attr if isinstance(f2, ast.Attribute) else "")
                assert name not in ("send_file", "__import__",
                                    "open"), (fn.name, name)
        assert saw_format


# ── 11. Secret taraması ─────────────────────────────────────────────

class TestSecretScan:
    SECRET_MARKERS = ("api_key", "api_secret", "apikey", "password",
                      "private key", "begin rsa", "mnemonic",
                      "session_secret", "set-cookie")

    @pytest.mark.parametrize("path", AUTOMATION_MODULES +
                             ("templates/automation.html",))
    def test_no_hardcoded_secret_material(self, path):
        with open(path, encoding="utf-8") as f:
            low = f.read().lower()
        for marker in self.SECRET_MARKERS:
            assert marker not in low, (path, marker)

    def test_state_file_contains_no_secret_fields(self, client, tmp_path):
        automation_engine._save_state(dict(
            automation_engine._EMPTY_STATE, state="scheduled"))
        raw = (tmp_path / "automation_state.json").read_text("utf-8").lower()
        for marker in ("key", "secret", "token", "cookie", "password",
                       "session", "/home/", "pid"):
            assert marker not in marker or marker not in raw, marker

    def test_responses_do_not_leak_env_secret_values(self, client,
                                                     monkeypatch):
        import os
        _login(client)
        bodies = [
            client.get("/api/automation/status").get_data(as_text=True),
            client.get("/api/automation/export/status").get_data(
                as_text=True),
            client.get("/api/automation/export/status?format=csv"
                       ).get_data(as_text=True),
            client.get("/automation").get_data(as_text=True),
        ]
        secrets = [os.environ.get(k) for k in (
            "BINANCE_API_KEY", "BINANCE_API_SECRET",
            "BINANCE_TRADING_API_KEY", "BINANCE_TRADING_API_SECRET",
            "SESSION_SECRET", "ALPHA_OWNER_PASSWORD_HASH")]
        for body in bodies:
            for val in secrets:
                if val:
                    assert val not in body


# ── 12. Dosya bütünlüğü ─────────────────────────────────────────────

class TestFileIntegrity:
    def test_state_file_exact_schema(self, client, tmp_path):
        automation_engine._save_state(dict(
            automation_engine._EMPTY_STATE, state="scheduled",
            beklenmeyen_alan="x"))
        data = json.loads(
            (tmp_path / "automation_state.json").read_text("utf-8"))
        assert set(data.keys()) == set(automation_engine._EMPTY_STATE)

    def test_read_endpoints_do_not_touch_history(self, client, tmp_path):
        hist = tmp_path / "history.json"
        hist.write_text('{"entries": []}', encoding="utf-8")
        before = hist.read_text("utf-8")
        _login(client)
        client.get("/api/automation/status")
        client.get("/api/automation/export/status")
        client.get("/api/automation/export/status?format=csv")
        assert hist.read_text("utf-8") == before

    def test_automation_modules_do_not_import_protected(self):
        for path in AUTOMATION_MODULES:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            mods = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    mods.add(node.module or "")
            for banned in ("ledger", "audit", "auth", "binance",
                           "exchange", "portfolio"):
                assert not any(banned in m.lower() for m in mods), \
                    (path, banned)

    def test_config_read_does_not_mutate_env(self, monkeypatch):
        import os
        monkeypatch.setenv("ALPHA_AUTOMATION_INTERVAL_MINUTES", "2")
        before = dict(os.environ)
        cfg = automation_engine.load_config()
        assert cfg["interval_minutes"] >= 5  # minimum zorlanır, env bozulmaz
        assert dict(os.environ) == before


# ── 13. Penetrasyon senaryoları ─────────────────────────────────────

class TestPenetration:
    def test_anonymous_all_automation_endpoints(self, client):
        assert client.get("/api/automation/status").status_code == 401
        assert client.get("/api/v1/automation/status").status_code == 401
        assert client.get("/api/automation/export/status").status_code == 401
        assert client.get(
            "/api/v1/automation/export/status").status_code == 401
        # POST run: CSRF kapalıyken anonim istek 401 alır (fixture);
        # CSRF açıkken 400'ün 401'den önce gelmesi ayrıca dokümante
        # edilmiş middleware sırasıdır — erişim her iki durumda engellidir.
        assert client.post("/api/automation/run",
                           json={}).status_code == 401
        assert client.post("/api/v1/automation/run",
                           json={}).status_code == 401
        r = client.get("/automation")
        assert r.status_code == 302 and "/login" in r.headers["Location"]

    def test_csrf_missing_and_wrong(self, client, monkeypatch):
        monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
        flask_app.app.config["WTF_CSRF_ENABLED"] = True
        try:
            _login(client)
            r = client.post("/api/automation/run", json={})
            assert r.status_code == 400  # token yok
            r = client.post("/api/automation/run", json={},
                            headers={"X-CSRFToken": "sahte-token"})
            assert r.status_code == 400  # token geçersiz
        finally:
            flask_app.app.config["WTF_CSRF_ENABLED"] = False

    def test_duplicate_run_flood(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", "true")
        calls = []

        def fake_run(config=None):
            calls.append(1)
            if len(calls) > 1:
                return {"skip_reason": "DUPLICATE_RUN"}
            return {"ran": True, "appended": True, "error_code": None,
                    "final_state": "succeeded", "run_id": "r1"}

        monkeypatch.setattr(automation_service, "run_automation", fake_run)
        _login(client)
        codes = [client.post("/api/automation/run", json={}).status_code
                 for _ in range(6)]
        assert codes.count(200) == 1 and codes.count(409) == 5

    def test_lock_race_no_append_while_locked(self, monkeypatch, tmp_path):
        """Kilit tutulurken hiçbir run_once koşamaz veya append edemez."""
        import fcntl
        monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                           str(tmp_path / "st.json"))
        appended = []
        monkeypatch.setattr(
            automation_engine.intelligence_timeline, "append_snapshot",
            lambda summary, path=None: appended.append(summary))

        def provider():
            return {"status": "OK", "generated_at":
                    "2026-07-26T10:00:00+00:00"}

        lock_holder = open(tmp_path / "st.json.lock", "a",
                           encoding="utf-8")
        fcntl.flock(lock_holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            results = []

            def worker():
                results.append(automation_engine.run_once(
                    provider,
                    config=dict(automation_engine.load_config(),
                                enabled=True)))

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert appended == []
            assert all(r.get("skip_reason") == "DUPLICATE_RUN"
                       for r in results) and len(results) == 4
        finally:
            fcntl.flock(lock_holder.fileno(), fcntl.LOCK_UN)
            lock_holder.close()
        # Kilit bırakıldıktan sonra normal koşu mümkün ve tekil append olur
        out = automation_engine.run_once(
            provider, config=dict(automation_engine.load_config(),
                                  enabled=True))
        assert out["appended"] is True and len(appended) == 1

    @pytest.mark.parametrize("fmt", [
        "A" * 4096,                       # çok uzun girdi
        "json\u0000csv",                  # null bayt
        "джсон",                          # unicode
        "../../../etc/passwd",            # path traversal denemesi
        "json; rm -rf /",                 # shell benzeri
    ])
    def test_export_hostile_format_rejected(self, client, fmt):
        _login(client)
        r = client.get("/api/automation/export/status",
                       query_string={"format": fmt})
        assert r.status_code == 400
        body = r.get_data(as_text=True)
        assert "passwd" not in body and "rm -rf" not in body

    def test_xss_payload_in_state_not_executable_in_export(self, client,
                                                           tmp_path):
        automation_engine._save_state(dict(
            automation_engine._EMPTY_STATE, state="failed",
            run_id="<script>alert(1)</script>",
            last_error_code="<img src=x onerror=alert(1)>"))
        _login(client)
        d = json.loads(client.get(
            "/api/automation/export/status").get_data(as_text=True))
        # JSON veri olarak taşınır; UI textContent ile bastığı için inert.
        assert d["status"]["run_id"] == "<script>alert(1)</script>"
        html = client.get("/automation").get_data(as_text=True)
        assert "<script>alert(1)</script>" not in html  # sunucu gömmez

    def test_csv_injection_all_prefixes(self, client, tmp_path):
        automation_engine._save_state(dict(
            automation_engine._EMPTY_STATE, state="failed",
            run_id="=1+1", last_error_code="@SUM(A1)",
            last_run_status="+cmd|' /C calc'!A0"))
        _login(client)
        body = client.get("/api/automation/export/status?format=csv"
                          ).data.decode("utf-8")
        assert "'=1+1" in body and "'@SUM(A1)" in body
        assert "'+cmd" in body

    def test_empty_state_file(self, client, tmp_path):
        (tmp_path / "automation_state.json").write_text("", encoding="utf-8")
        _login(client)
        r = client.get("/api/automation/status")
        assert r.status_code == 200
        assert r.get_json()["state"] == "disabled"

    def test_missing_and_corrupted_config(self, client, monkeypatch):
        monkeypatch.setenv("ALPHA_AUTOMATION_INTERVAL_MINUTES", "bozuk")
        monkeypatch.setenv("ALPHA_AUTOMATION_TIMEOUT_SECONDS", "-99")
        cfg = automation_engine.load_config()
        assert cfg["enabled"] is False          # varsayılan kapalı
        assert cfg["interval_minutes"] >= 5     # güvenli varsayılan
        assert cfg["timeout_seconds"] >= 10
        _login(client)
        assert client.get("/api/automation/status").status_code == 200

    def test_exception_flood_stays_sterile(self, client, monkeypatch):
        _login(client)
        monkeypatch.setattr(automation_engine, "load_state",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("ic sir /gizli/yol")))
        for _ in range(5):
            r = client.get("/api/automation/export/status")
            assert r.status_code == 503
            assert "ic sir" not in r.get_data(as_text=True)


# ── 3-9. Garanti doğrulamaları (çapraz kontrol) ─────────────────────

class TestGuarantees:
    def test_run_route_thin_no_direct_intelligence(self):
        with open("app.py", encoding="utf-8") as f:
            src = f.read()
        i = src.index("def api_automation_run")
        block = src[i:src.index("def ", i + 10)]
        for banned in ("append_snapshot", "IntelligenceService",
                       "get_summary", "execute_intelligence_run"):
            assert banned not in block

    def test_failed_result_produces_no_append(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                           str(tmp_path / "st.json"))
        appended = []
        monkeypatch.setattr(
            automation_engine.intelligence_timeline, "append_snapshot",
            lambda summary, path=None: appended.append(summary))
        out = automation_engine.run_once(
            lambda: {"status": "FAILED"},
            config=dict(automation_engine.load_config(), enabled=True))
        assert appended == [] and out["appended"] is False

    def test_scheduler_default_off(self, monkeypatch):
        monkeypatch.delenv("ALPHA_AUTOMATION_ENABLED", raising=False)
        assert flask_app.start_automation_scheduler() is None

    def test_scheduler_not_started_on_non_true_values(self, monkeypatch):
        # Yapılandırma sözleşmesi: yalnız "true" (büyük/küçük harf
        # duyarsız) etkinleştirir; diğer tüm değerler kapalı bırakır.
        for v in ("1", "yes", "evet", "on", "false", " ", "truex"):
            monkeypatch.setenv("ALPHA_AUTOMATION_ENABLED", v)
            assert flask_app.start_automation_scheduler() is None, v
