"""
tests/test_local_admin_auth.py — Windows yerel giriş ayrımı senaryoları

Görev: Windows/yerel girişin TEK kaynağı data/local_admin.json;
Replit Secrets akışı değişmeden kalır; system env Windows girişini
override EDEMEZ; plaintext parola hiçbir yere yazılmaz; bozuk dosya
fail-closed davranır; env→dosya migration tek seferliktir.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent.parent

PASSWORD = "local-admin-pass-2026!"
USERNAME = "winoperator"


# ══════════════════════════════════════════════════════════════════════════════
# Fixture'lar
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def windows_env(monkeypatch, tmp_path):
    """Windows/yerel ortam simülasyonu: is_replit=False, depo tmp dizininde,
    ilgili env değişkenleri temiz."""
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
    return tmp_path / "data" / "local_admin.json"


@pytest.fixture
def client(monkeypatch):
    """Güvenlik kapısı aktif Flask test istemcisi."""
    monkeypatch.setenv("FLASK_SECRET_KEY",
                       "test-secret-key-localadmin-a1b2c3d4e5f6")
    import app as flask_app
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    flask_app.app.config["SECRET_KEY"] = "test-secret-key-localadmin-a1b2c3d4e5f6"
    with flask_app.app.test_client() as c:
        yield c
    flask_app.app.config["TESTING"] = True


def _write_valid(file_: Path, username: str = USERNAME,
                 password: str = PASSWORD) -> str:
    import local_admin
    pw_hash = generate_password_hash(password)
    local_admin.save(username, pw_hash)
    assert file_.is_file()
    return pw_hash


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 1-3: taze kurulum → dosya oluşur → "restart" sonrası login
# ══════════════════════════════════════════════════════════════════════════════

class TestFreshSetupFlow:

    def test_no_file_opens_setup_and_login_redirects(self, windows_env, client):
        """Dosya yokken /setup 200 döner ve /login sihirbaza yönlendirir."""
        assert not windows_env.exists()
        assert client.get("/setup").status_code == 200
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert "/setup" in resp.headers.get("Location", "")

    def test_save_creates_file_with_only_allowed_fields(self, windows_env, client):
        """Kayıt data/local_admin.json'ı yalnızca 4 izinli alanla oluşturur."""
        pw_hash = generate_password_hash(PASSWORD)
        resp = client.post(
            "/setup/save",
            data=json.dumps({"password_hash": pw_hash, "username": USERNAME}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        rec = json.loads(windows_env.read_text(encoding="utf-8"))
        assert set(rec) == {"schema_version", "username",
                            "password_hash", "created_at"}
        assert rec["schema_version"] == 1
        assert rec["username"] == USERNAME
        assert rec["password_hash"].startswith(("pbkdf2:", "scrypt:"))
        # env'e YAZILMADI
        assert "ALPHA_OWNER_PASSWORD_HASH" not in os.environ
        assert "ALPHA_OWNER_USERNAME" not in os.environ

    def test_restart_shows_login_not_setup(self, windows_env, client):
        """Dosya varken (restart simülasyonu — durum yalnızca dosyada)
        /setup 404, /login giriş formunu gösterir."""
        _write_valid(windows_env)
        assert client.get("/setup").status_code == 404
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 200  # form; sihirbaza yönlendirme YOK

    def test_file_permissions_owner_only(self, windows_env):
        """POSIX'te dosya 0600, dizin 0700 olmalı."""
        if sys.platform.startswith("win"):
            pytest.skip("POSIX izin kontrolü")
        _write_valid(windows_env)
        assert stat.S_IMODE(windows_env.stat().st_mode) == 0o600
        assert stat.S_IMODE(windows_env.parent.stat().st_mode) == 0o700


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 4-5: doğru / yanlış parola
# ══════════════════════════════════════════════════════════════════════════════

class TestLoginAgainstFile:

    def test_correct_password_logs_in(self, windows_env, client):
        _write_valid(windows_env)
        resp = client.post("/login",
                           data={"username": USERNAME, "password": PASSWORD},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("Location", "").endswith("/home")

    def test_wrong_password_rejected(self, windows_env, client):
        _write_valid(windows_env)
        resp = client.post("/login",
                           data={"username": USERNAME, "password": "wrong!!"},
                           follow_redirects=False)
        assert resp.status_code == 200
        assert "hatal" in resp.get_data(as_text=True).lower()


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 6: git güncellemesi kalıcılığı (.gitignore + dosya dokunulmaz)
# ══════════════════════════════════════════════════════════════════════════════

class TestGitUpdatePersistence:

    def test_local_admin_json_is_gitignored(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        lines = [ln.strip() for ln in gitignore.splitlines()]
        assert "data/local_admin.json" in lines or "data/" in lines

    def test_file_survives_between_processes(self, windows_env):
        """Kayıt dosyası süreçler arası kalıcıdır; yeniden okuma aynı
        kimliği verir (git pull kodu değiştirir, data/ dokunulmaz)."""
        import local_admin
        pw_hash = _write_valid(windows_env)
        creds = local_admin.get_credentials()
        assert creds == (USERNAME, pw_hash)


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 7: Replit akışı DEĞİŞMEDİ
# ══════════════════════════════════════════════════════════════════════════════

class TestReplitUnchanged:

    def test_replit_uses_env_and_ignores_file(self, monkeypatch, tmp_path, client):
        """Replit'te giriş kaynağı Secrets(env); local_admin.json okunmaz."""
        import auth
        import local_admin
        import local_env
        monkeypatch.setattr(local_env, "is_replit", lambda: True)
        # Sahte dosya bile olsa dikkate alınmamalı
        monkeypatch.setattr(local_admin, "ROOT", tmp_path)
        monkeypatch.setattr(local_admin, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(local_admin, "FILE",
                            tmp_path / "data" / "local_admin.json")
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "local_admin.json").write_text(json.dumps({
            "schema_version": 1, "username": "fileuser",
            "password_hash": generate_password_hash("filepass"),
            "created_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")

        env_hash = generate_password_hash("envpass-123!")
        monkeypatch.setenv("ALPHA_OWNER_USERNAME", "envuser")
        monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", env_hash)

        assert local_admin.enabled() is False
        assert auth.password_hash_configured() is True
        assert auth.verify_credentials("envuser", "envpass-123!") is True
        assert auth.verify_credentials("fileuser", "filepass") is False

    def test_setup_save_still_403_on_replit(self, monkeypatch, client):
        import local_env
        for key in ("ALPHA_OWNER_USERNAME", "ALPHA_OWNER_PASSWORD_HASH",
                    "ADMIN_PASSWORD_HASH"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(local_env, "is_replit", lambda: True)
        resp = client.post(
            "/setup/save",
            data=json.dumps({"password_hash": generate_password_hash("x" * 8),
                             "username": "admin"}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        assert resp.get_json()["error"]["code"] == "REPLIT_ENV"


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 8: system env Windows girişini OVERRIDE EDEMEZ
# ══════════════════════════════════════════════════════════════════════════════

class TestEnvCannotOverrideOnWindows:

    def test_env_credentials_ignored_when_file_exists(self, windows_env,
                                                      monkeypatch, client):
        """Dosya varken eski clone / system env kimliği girişte GEÇERSİZDİR."""
        import auth
        _write_valid(windows_env)
        env_hash = generate_password_hash("stale-env-pass!")
        monkeypatch.setenv("ALPHA_OWNER_USERNAME", "staleuser")
        monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", env_hash)
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", env_hash)

        # env kimliğiyle giriş REDDEDİLİR
        assert auth.verify_credentials("staleuser", "stale-env-pass!") is False
        # dosya kimliğiyle giriş çalışır
        assert auth.verify_credentials(USERNAME, PASSWORD) is True

    def test_env_hash_alone_does_not_configure_windows(self, windows_env,
                                                       monkeypatch):
        """Dosya yoksa env yalnızca TEK SEFERLİK migration kaynağıdır;
        migration sonrası dosya tek kaynak olur."""
        import auth
        import local_admin
        env_hash = generate_password_hash("migrate-me-pass!")
        monkeypatch.setenv("ALPHA_OWNER_USERNAME", "migrated")
        monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", env_hash)

        assert auth.password_hash_configured() is True  # migration tetiklendi
        rec = json.loads(windows_env.read_text(encoding="utf-8"))
        assert rec["username"] == "migrated"
        assert rec["password_hash"] == env_hash

        # Artık env değişse bile dosya kazanır
        monkeypatch.setenv("ALPHA_OWNER_USERNAME", "attacker")
        creds = local_admin.get_credentials()
        assert creds[0] == "migrated"


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 9: plaintext parola hiçbir yerde saklanmaz
# ══════════════════════════════════════════════════════════════════════════════

class TestNoPlaintextAnywhere:

    def test_file_never_contains_plaintext_password(self, windows_env):
        _write_valid(windows_env)
        text = windows_env.read_text(encoding="utf-8")
        assert PASSWORD not in text
        assert '"password"' not in text  # yalnızca password_hash alanı

    def test_save_rejects_non_hash_values(self, windows_env):
        """Plaintext parolayı hash sanıp kaydetmek İMKANSIZ olmalı."""
        import local_admin
        with pytest.raises(ValueError):
            local_admin.save(USERNAME, PASSWORD)  # hash prefix'i yok
        assert not windows_env.exists()


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 10: fail-closed — bozuk dosya, symlink, eksik alanlar
# ══════════════════════════════════════════════════════════════════════════════

class TestFailClosed:

    def test_corrupt_json_locks_and_reopens_setup(self, windows_env, client):
        windows_env.parent.mkdir(parents=True, exist_ok=True)
        windows_env.write_text("{not valid json!!", encoding="utf-8")
        import auth
        assert auth.password_hash_configured() is False
        assert client.get("/setup").status_code == 200
        # Bozuk dosya sessizce SİLİNMEZ
        assert windows_env.read_text(encoding="utf-8") == "{not valid json!!"

    @pytest.mark.parametrize("payload", [
        "[]",                                           # dict değil
        json.dumps({"username": "u"}),                  # hash yok
        json.dumps({"password_hash": "pbkdf2:x"}),      # username yok
        json.dumps({"username": "", "password_hash": "pbkdf2:x"}),
        json.dumps({"username": "u", "password_hash": "plaintext"}),
        json.dumps({"username": 5, "password_hash": "pbkdf2:x"}),
    ])
    def test_invalid_schema_fails_closed(self, windows_env, payload):
        import local_admin
        windows_env.parent.mkdir(parents=True, exist_ok=True)
        windows_env.write_text(payload, encoding="utf-8")
        assert local_admin.load() is None

    def test_corrupt_file_plus_env_stays_locked(self, windows_env, monkeypatch):
        """Bozuk dosya + dolu env → migration ÇALIŞMAZ; kilit kapalı kalır
        ve bozuk dosyanın üzerine yazılmaz (architect regresyon testi)."""
        import auth
        import local_admin
        windows_env.parent.mkdir(parents=True, exist_ok=True)
        windows_env.write_text("{corrupt!!", encoding="utf-8")
        monkeypatch.setenv("ALPHA_OWNER_USERNAME", "envuser")
        monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH",
                           generate_password_hash("envpass!"))
        monkeypatch.setenv("ADMIN_PASSWORD_HASH",
                           generate_password_hash("envpass!"))
        assert local_admin.migrate_from_env() is False
        assert auth.password_hash_configured() is False
        assert auth.verify_credentials("envuser", "envpass!") is False
        assert windows_env.read_text(encoding="utf-8") == "{corrupt!!"

    def test_symlink_file_refused(self, windows_env, tmp_path):
        """Dosya symlink ise okuma fail-closed, yazma ValueError."""
        import local_admin
        windows_env.parent.mkdir(parents=True, exist_ok=True)
        target = tmp_path / "evil.json"
        target.write_text(json.dumps({
            "schema_version": 1, "username": "evil",
            "password_hash": generate_password_hash("evilpass"),
            "created_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
        windows_env.symlink_to(target)
        assert local_admin.load() is None
        with pytest.raises(ValueError):
            local_admin.save(USERNAME, generate_password_hash(PASSWORD))

    def test_atomic_write_no_partial_file(self, windows_env):
        """Yazma sonrası dizinde geçici artık dosya kalmaz; kayıt bütündür."""
        _write_valid(windows_env)
        leftovers = [p for p in windows_env.parent.iterdir()
                     if p.name != "local_admin.json"]
        assert leftovers == []
        json.loads(windows_env.read_text(encoding="utf-8"))  # geçerli JSON
