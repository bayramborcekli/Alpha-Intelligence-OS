"""
tests/test_password_change.py — /settings/password güvenli parola değiştirme

Görev #63: Windows/yerelde girişli operatör mevcut parolasını doğrulayarak
data/local_admin.json'u atomic günceller; Replit'te form kapalıdır ve
Secrets yönlendirmesi gösterilir; yanlış mevcut parola reddedilir ve
rate-limit'e sayılır.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

PASSWORD = "old-pass-2026!"
NEW_PASSWORD = "new-pass-2026!!"
USERNAME = "winoperator"


@pytest.fixture
def windows_env(monkeypatch, tmp_path):
    import local_admin
    import local_env
    for key in ("ALPHA_OWNER_USERNAME", "ALPHA_OWNER_PASSWORD_HASH",
                "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(local_env, "is_replit", lambda: False)
    monkeypatch.setattr(local_admin, "ROOT", tmp_path)
    monkeypatch.setattr(local_admin, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(local_admin, "FILE",
                        tmp_path / "data" / "local_admin.json")
    local_admin.save(USERNAME, generate_password_hash(PASSWORD))
    return tmp_path / "data" / "local_admin.json"


@pytest.fixture
def replit_env(monkeypatch):
    import local_env
    monkeypatch.setattr(local_env, "is_replit", lambda: True)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", USERNAME)
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH",
                       generate_password_hash(PASSWORD))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY",
                       "test-secret-key-pwchange-a1b2c3d4e5f6")
    import app as flask_app
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    flask_app.app.config["SECRET_KEY"] = "test-secret-key-pwchange-a1b2c3d4e5f6"
    with flask_app.app.test_client() as c:
        yield c
    flask_app.app.config["TESTING"] = True


def _login(client, password: str = PASSWORD):
    r = client.post("/login", data={"username": USERNAME,
                                    "password": password})
    assert r.status_code == 302
    return r


def _change(client, current, new, confirm=None):
    return client.post("/settings/password", data={
        "current_password": current,
        "new_password": new,
        "confirm_password": confirm if confirm is not None else new,
    })


class TestWindowsFlow:

    def test_requires_login(self, windows_env, client):
        r = client.get("/settings/password")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_page_shows_form_when_logged_in(self, windows_env, client):
        _login(client)
        r = client.get("/settings/password")
        assert r.status_code == 200
        assert b"current_password" in r.data

    def test_successful_change_updates_file_atomically(self, windows_env,
                                                       client):
        import json
        _login(client)
        r = _change(client, PASSWORD, NEW_PASSWORD)
        assert r.status_code == 200
        assert "güncellendi".encode() in r.data
        data = json.loads(Path(windows_env).read_text(encoding="utf-8"))
        assert check_password_hash(data["password_hash"], NEW_PASSWORD)
        assert data["username"] == USERNAME
        assert NEW_PASSWORD not in Path(windows_env).read_text(
            encoding="utf-8")
        # yeni parola ile giriş çalışır, eskisi çalışmaz
        import auth
        assert auth.verify_credentials(USERNAME, NEW_PASSWORD)
        assert not auth.verify_credentials(USERNAME, PASSWORD)

    def test_wrong_current_password_rejected(self, windows_env, client):
        _login(client)
        r = _change(client, "wrong-pass", NEW_PASSWORD)
        assert r.status_code == 200
        assert "Mevcut parola hatalı".encode() in r.data
        import auth
        assert auth.verify_credentials(USERNAME, PASSWORD)

    def test_mismatched_confirmation_rejected(self, windows_env, client):
        _login(client)
        r = _change(client, PASSWORD, NEW_PASSWORD, confirm="different")
        assert "eşleşmiyor".encode() in r.data
        import auth
        assert auth.verify_credentials(USERNAME, PASSWORD)

    def test_short_new_password_rejected(self, windows_env, client):
        _login(client)
        r = _change(client, PASSWORD, "abc")
        assert "en az 6".encode() in r.data
        import auth
        assert auth.verify_credentials(USERNAME, PASSWORD)

    def test_same_password_rejected(self, windows_env, client):
        _login(client)
        r = _change(client, PASSWORD, PASSWORD)
        assert "farklı olmalı".encode() in r.data

    def test_wrong_current_attempts_rate_limited(self, windows_env, client):
        import auth
        _login(client)
        for _ in range(auth.MAX_ATTEMPTS):
            _change(client, "wrong-pass", NEW_PASSWORD)
        r = _change(client, PASSWORD, NEW_PASSWORD)
        assert "Çok fazla".encode() in r.data
        # doğru parola bile limit altında kabul edilmedi
        assert auth.verify_credentials(USERNAME, PASSWORD)

    def test_pwchange_limit_does_not_consume_login_budget(self, windows_env,
                                                          client):
        import auth
        _login(client)
        for _ in range(auth.MAX_ATTEMPTS):
            _change(client, "wrong-pass", NEW_PASSWORD)
        # login ad alanı hâlâ açık
        client.get("/logout")
        _login(client)


class TestReplitFlow:

    def test_page_shows_secrets_guidance(self, replit_env, client):
        _login(client)
        r = client.get("/settings/password")
        assert r.status_code == 200
        assert b"Secrets" in r.data
        assert b"current_password" not in r.data

    def test_post_returns_403(self, replit_env, client):
        _login(client)
        r = _change(client, PASSWORD, NEW_PASSWORD)
        assert r.status_code == 403
