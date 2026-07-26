"""Mission 1400.1 — uygulama temeli, giriş ve güvenlik testleri."""
import json
import os
from unittest import mock

import pytest
from werkzeug.security import generate_password_hash

import alpha_platform as ap
import app as flask_app
import auth

PASSWORD = "owner-test-parola-123"
HASH = generate_password_hash(PASSWORD)
OWNER_ENV = {"ALPHA_OWNER_USERNAME": "sahip",
             "ALPHA_OWNER_PASSWORD_HASH": HASH,
             "SESSION_SECRET": "test-session-secret"}


@pytest.fixture
def client(monkeypatch):
    """Sahip secret'ları yapılandırılmış, güvenlik kapısı AKTİF istemci."""
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    for k, v in OWNER_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14001_attempts.db")
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True


@pytest.fixture
def locked_client(monkeypatch):
    """Sahip secret'ları OLMAYAN (kilitli kurulum) istemci."""
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME",
              "ALPHA_OWNER_USERNAME", "ALPHA_OWNER_PASSWORD_HASH"):
        monkeypatch.delenv(k, raising=False)
    flask_app.app.config["TESTING"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True


def _login(c):
    return c.post("/api/v1/auth/login",
                  json={"username": "sahip", "password": PASSWORD})


# ── Yapılandırma ─────────────────────────────────────────────────────────────

class TestConfiguration:
    def test_flags_default_false(self, monkeypatch):
        for n in ap.FEATURE_FLAG_NAMES:
            monkeypatch.delenv(n, raising=False)
        assert all(v is False for v in ap.feature_flags().values())

    def test_malformed_flag_values_are_false(self, monkeypatch):
        for raw in ("1", "yes", "TRUE ish", "evet", "on", "null", " "):
            monkeypatch.setenv("ALPHA_ENABLE_LIVE_TRADING", raw)
            if raw.strip().lower() != "true":
                assert ap.feature_flags()["ALPHA_ENABLE_LIVE_TRADING"] is False

    def test_owner_secret_validation(self, monkeypatch):
        for k in ("ADMIN_PASSWORD_HASH", "ALPHA_OWNER_USERNAME",
                  "ALPHA_OWNER_PASSWORD_HASH"):
            monkeypatch.delenv(k, raising=False)
        assert ap.setup_state() == "LOCKED"
        assert set(ap.missing_owner_secrets()) == set(ap.REQUIRED_OWNER_SECRETS)
        monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
        monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
        assert ap.setup_state() == "READY"

    def test_partial_owner_secrets_stay_locked(self, monkeypatch):
        """Yalnızca hash varken (kullanıcı adı eksik) kilit AÇILMAMALI."""
        for k in ("ADMIN_PASSWORD_HASH", "ALPHA_OWNER_USERNAME"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
        assert ap.setup_state() == "LOCKED"
        assert auth.password_hash_configured() is False  # aynı ölçüt
        flask_app.app.config["TESTING"] = False
        try:
            with flask_app.app.test_client() as c:
                assert c.get("/api/v1/application/config").status_code == 403
                r = c.post("/api/v1/auth/login",
                           json={"username": "sahip", "password": PASSWORD})
                assert r.status_code == 403
        finally:
            flask_app.app.config["TESTING"] = True

    def test_dummy_hash_matches_active_algorithm(self):
        """Zamanlama eşitleme hash'i gerçek hash'lerle aynı algoritma/maliyet
        önekini taşımalı (kullanıcı adı sondajını önler)."""
        dummy = auth._dummy_hash()
        real = generate_password_hash("x")
        assert dummy.split("$")[0] == real.split("$")[0]

    def test_locked_setup_mode_blocks_api(self, locked_client):
        r = locked_client.get("/api/v1/application/config")
        assert r.status_code == 403

    def test_health_lists_only_names_never_values(self, locked_client):
        r = locked_client.get("/api/v1/health")
        assert r.status_code == 200
        d = r.get_json()
        assert d["setup_state"] == "LOCKED"
        assert set(d["required_configuration"]) == set(
            ap.REQUIRED_OWNER_SECRETS)
        blob = r.get_data(as_text=True)
        assert HASH not in blob and PASSWORD not in blob

    def test_config_response_has_no_secret_values(self, client):
        _login(client)
        r = client.get("/api/v1/application/config")
        assert r.status_code == 200
        blob = r.get_data(as_text=True)
        for v in (PASSWORD, HASH, os.environ.get("SESSION_SECRET", "\x00")):
            assert v not in blob
        d = r.get_json()
        assert d["live_trading_enabled"] is False
        assert d["transfers_enabled"] is False
        assert d["withdrawals_enabled"] is False
        assert d["ui_language"] == "tr"
        assert d["owner"] == "sahip"


# ── Kimlik doğrulama ─────────────────────────────────────────────────────────

class TestAuthentication:
    def test_successful_login(self, client):
        r = _login(client)
        assert r.status_code == 200 and r.get_json()["ok"] is True

    def test_invalid_password_generic_error(self, client):
        r = client.post("/api/v1/auth/login",
                        json={"username": "sahip", "password": "yanlis"})
        assert r.status_code == 401
        assert "hatalı" in r.get_json()["error"].lower()

    def test_invalid_username_same_generic_error(self, client):
        r1 = client.post("/api/v1/auth/login",
                         json={"username": "sahip", "password": "yanlis"})
        r2 = client.post("/api/v1/auth/login",
                         json={"username": "baskasi", "password": PASSWORD})
        assert r1.status_code == r2.status_code == 401
        assert r1.get_json()["error"] == r2.get_json()["error"]

    def test_missing_credentials(self, client):
        assert client.post("/api/v1/auth/login", json={}).status_code == 401
        assert client.post("/api/v1/auth/login",
                           data="bozuk").status_code == 400

    def test_rate_limiting(self, client):
        for _ in range(auth.MAX_ATTEMPTS):
            client.post("/api/v1/auth/login",
                        json={"username": "sahip", "password": "x"})
        r = client.post("/api/v1/auth/login",
                        json={"username": "sahip", "password": PASSWORD})
        assert r.status_code == 429

    def test_logout_invalidates_session(self, client):
        _login(client)
        assert client.get("/api/v1/auth/session").get_json()[
            "authenticated"] is True
        assert client.post("/api/v1/auth/logout").status_code == 200
        r = client.get("/api/v1/auth/session")
        assert r.status_code == 401

    def test_expired_session_rejected(self, client, monkeypatch):
        _login(client)
        monkeypatch.setattr(auth, "SESSION_MAX_AGE", -1)
        r = client.get("/api/v1/auth/session")
        assert r.status_code in (302, 401)

    def test_session_fixation_prevented(self, client):
        with client.session_transaction() as s:
            s["planted"] = "attacker-value"
        _login(client)
        with client.session_transaction() as s:
            assert "planted" not in s
            assert s.get("logged_in") is True


# ── Yetkilendirme ────────────────────────────────────────────────────────────

class TestAuthorization:
    def test_unauthenticated_api_401(self, client):
        r = client.get("/api/v1/application/config")
        assert r.status_code == 401
        d = r.get_json()
        assert "error" in d and d["request_id"]

    def test_authenticated_api_200(self, client):
        _login(client)
        assert client.get("/api/v1/application/config").status_code == 200

    def test_unauthorized_page_redirects_to_login(self, client):
        r = client.get("/")
        assert r.status_code == 302 and "/login" in r.headers["Location"]

    def test_shell_renders_after_login(self, client):
        _login(client)
        r = client.get("/")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "Canlı emir yürütme devre dışı" in body
        assert "Başlangıç" in body and "Oturumu Kapat" in body

    def test_request_id_header_present(self, client):
        r = client.get("/api/v1/health")
        assert len(r.headers.get("X-Request-ID", "")) == 16


# ── Güvenlik ─────────────────────────────────────────────────────────────────

class TestSecurity:
    def test_session_cookie_attributes(self, client):
        r = _login(client)
        cookie = r.headers.get("Set-Cookie", "")
        assert "HttpOnly" in cookie
        assert "SameSite" in cookie

    def test_no_hash_in_any_auth_response(self, client):
        for r in (_login(client), client.get("/api/v1/auth/session"),
                  client.get("/")):
            assert HASH not in r.get_data(as_text=True)

    def test_health_has_no_paths_or_balances(self, client):
        d = client.get("/api/v1/health").get_json()
        blob = json.dumps(d)
        assert "/home/" not in blob and "balance" not in blob.lower()

    def test_no_binance_write_route_exists(self):
        for rule in flask_app.app.url_map.iter_rules():
            path = str(rule).lower()
            assert not any(w in path for w in
                           ("order", "transfer", "withdraw", "leverage")), \
                f"yasak rota: {rule}"

    def test_csp_hardened_directives(self, client):
        r = client.get("/api/v1/health")
        csp = r.headers.get("Content-Security-Policy", "")
        assert "object-src 'none'" in csp
        assert "base-uri 'self'" in csp
        assert "form-action 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "unsafe-eval" not in csp

    def test_setup_hash_endpoint_rate_limited(self, monkeypatch):
        for k in ("ADMIN_PASSWORD_HASH", "ALPHA_OWNER_USERNAME",
                  "ALPHA_OWNER_PASSWORD_HASH"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14001r_rl.db")
        auth._ATTEMPTS.clear()
        flask_app.app.config["TESTING"] = False
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        try:
            with flask_app.app.test_client() as c:
                for _ in range(auth.MAX_ATTEMPTS):
                    c.post("/setup/hash", json={"password": "abcdef123"})
                r = c.post("/setup/hash", json={"password": "abcdef123"})
                assert r.status_code == 429
        finally:
            flask_app.app.config["TESTING"] = True
            auth._ATTEMPTS.clear()

    def test_setup_hash_404_after_configuration(self, client):
        """Kurulum sonrası /setup/hash da 404 dönmeli (403 değil)."""
        assert client.post("/setup/hash",
                           json={"password": "abcdef123"}).status_code == 404

    def test_setup_throttle_does_not_consume_login_budget(self, monkeypatch):
        """Sihirbaz hız sınırı ayrı ad alanında; login kilidini tüketmez."""
        for k in ("ADMIN_PASSWORD_HASH", "ALPHA_OWNER_USERNAME",
                  "ALPHA_OWNER_PASSWORD_HASH"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14001r_ns.db")
        auth._ATTEMPTS.clear()
        flask_app.app.config["TESTING"] = False
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        try:
            with flask_app.app.test_client() as c:
                for _ in range(auth.MAX_ATTEMPTS):
                    c.post("/setup/hash", json={"password": "abcdef123"})
            # setup kilitlendi ama login IP bütçesi dokunulmamış olmalı
            allowed, _ = auth.check_rate_limit("127.0.0.1")
            assert allowed is True
            allowed_setup, _ = auth.check_rate_limit("setup:127.0.0.1")
            assert allowed_setup is False
        finally:
            flask_app.app.config["TESTING"] = True
            auth._ATTEMPTS.clear()

    def test_setup_hash_success_is_structured_json(self, monkeypatch):
        for k in ("ADMIN_PASSWORD_HASH", "ALPHA_OWNER_USERNAME",
                  "ALPHA_OWNER_PASSWORD_HASH"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14001hf1.db")
        auth._ATTEMPTS.clear()
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        pw = "hotfix-parola-42"
        with flask_app.app.test_client() as c:
            r = c.post("/setup/hash", json={"password": pw})
        assert r.status_code == 200
        assert r.content_type.startswith("application/json")
        d = r.get_json()
        assert d["ok"] is True and d["password_hash"] == d["hash"]
        assert pw not in r.get_data(as_text=True)  # düz metin parola yok

    def test_setup_hash_invalid_password_structured_json(self, monkeypatch):
        for k in ("ADMIN_PASSWORD_HASH", "ALPHA_OWNER_USERNAME",
                  "ALPHA_OWNER_PASSWORD_HASH"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14001hf2.db")
        auth._ATTEMPTS.clear()
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        with flask_app.app.test_client() as c:
            r = c.post("/setup/hash", json={"password": "abc"})
        assert r.status_code == 400
        d = r.get_json()
        assert d["ok"] is False
        assert d["error"]["code"] == "INVALID_PASSWORD"

    def test_setup_hash_csrf_failure_returns_json(self, monkeypatch):
        """CSRF etkinken token'sız POST → 403 yapılandırılmış JSON (HTML/302 değil)."""
        for k in ("ADMIN_PASSWORD_HASH", "ALPHA_OWNER_USERNAME",
                  "ALPHA_OWNER_PASSWORD_HASH"):
            monkeypatch.delenv(k, raising=False)
        flask_app.app.config["TESTING"] = False
        flask_app.app.config["WTF_CSRF_ENABLED"] = True
        try:
            with flask_app.app.test_client() as c:
                r = c.post("/setup/hash", json={"password": "abcdef123"})
            assert r.status_code == 403
            assert r.content_type.startswith("application/json")
            d = r.get_json()
            assert d["ok"] is False and d["error"]["code"] == "CSRF_FAILED"
        finally:
            flask_app.app.config["TESTING"] = True
            flask_app.app.config["WTF_CSRF_ENABLED"] = False

    def test_setup_hash_rate_limit_structured_json(self, monkeypatch):
        for k in ("ADMIN_PASSWORD_HASH", "ALPHA_OWNER_USERNAME",
                  "ALPHA_OWNER_PASSWORD_HASH"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14001hf3.db")
        auth._ATTEMPTS.clear()
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        try:
            with flask_app.app.test_client() as c:
                for _ in range(auth.MAX_ATTEMPTS):
                    c.post("/setup/hash", json={"password": "abcdef123"})
                r = c.post("/setup/hash", json={"password": "abcdef123"})
            assert r.status_code == 429
            d = r.get_json()
            assert d["ok"] is False and d["error"]["code"] == "RATE_LIMITED"
        finally:
            auth._ATTEMPTS.clear()

    def test_setup_wizard_frontend_handles_non_json(self):
        """Frontend körlemesine response.json() çağırmamalı."""
        from pathlib import Path
        src = Path("templates/setup.html").read_text()
        assert "Content-Type" in src and "includes('application/json')" in src
        assert "X-CSRFToken" in src and 'meta[name="csrf-token"]' in src
        assert "localStorage" not in src and "sessionStorage" not in src

    def test_password_never_written_to_security_log(self, monkeypatch, tmp_path):
        for k in ("ADMIN_PASSWORD_HASH", "ALPHA_OWNER_USERNAME",
                  "ALPHA_OWNER_PASSWORD_HASH"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14001hf4.db")
        auth._ATTEMPTS.clear()
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        pw = "cok-gizli-parola-9x"
        with flask_app.app.test_client() as c:
            c.post("/setup/hash", json={"password": pw})
        from pathlib import Path
        log = Path("security.log")
        if log.exists():
            assert pw not in log.read_text(errors="ignore")

    def test_owner_config_persists_across_simulated_restart(self, client):
        """Env tabanlı model: sahip kimliği yalnızca ortamdan okunur; süreç
        yeniden başlasa da (yeni istemci) yapılandırma READY kalır."""
        assert auth.password_hash_configured() is True
        with flask_app.app.test_client() as fresh:
            r = fresh.get("/api/v1/health")
            assert r.get_json()["setup_state"] == "READY"

    def test_shell_has_mobile_and_desktop_navigation(self, client):
        _login(client)
        body = client.get("/").get_data(as_text=True)
        assert 'id="menu-btn"' in body          # mobil menü düğmesi
        assert 'class="sidebar"' in body        # masaüstü kenar çubuğu
        assert "aria-expanded" in body          # erişilebilirlik

    def test_frontend_templates_have_no_secret_names_as_values(self):
        from pathlib import Path
        for tpl in Path("templates").glob("*.html"):
            text = tpl.read_text()
            for name in ("BINANCE_API_SECRET", "BINANCE_TR_API_SECRET",
                         "BINANCE_TRADING_API_SECRET", "SESSION_SECRET"):
                assert os.environ.get(name, "\x00") not in text
