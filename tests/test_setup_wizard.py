"""
tests/test_setup_wizard.py — Setup sihirbazı güvenlik testleri

Parola ayarlandıktan sonra /setup ve /setup/hash endpoint'lerinin
yetkisiz erişime kapalı kaldığını doğrular.

Ayrıca Windows/yerel kurulum için /setup/save endpoint'inin
.env'e yazma ve os.environ güncelleme akışını test eder.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════════════
# Fixture'lar
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def configured_client(monkeypatch):
    """
    Flask test istemcisi — ADMIN_PASSWORD_HASH tanımlı (kurulum tamamlanmış).
    TESTING=False → güvenlik kapısı aktif. CSRF devre dışı.
    """
    from werkzeug.security import generate_password_hash

    monkeypatch.delenv("ALPHA_OWNER_USERNAME",      raising=False)
    monkeypatch.delenv("ALPHA_OWNER_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME",      "testadmin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", generate_password_hash("testpass1234"))
    monkeypatch.setenv("FLASK_SECRET_KEY",    "test-secret-key-setup-aabbccdd11223344")

    import app as flask_app
    flask_app.app.config["TESTING"]          = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    flask_app.app.config["SECRET_KEY"]       = "test-secret-key-setup-aabbccdd11223344"

    with flask_app.app.test_client() as c:
        yield c

    flask_app.app.config["TESTING"] = True


@pytest.fixture
def unconfigured_client(monkeypatch):
    """
    Flask test istemcisi — ADMIN_PASSWORD_HASH YOK (kurulum tamamlanmamış).
    TESTING=False → güvenlik kapısı aktif. CSRF devre dışı.
    """
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("ALPHA_OWNER_USERNAME",      raising=False)
    monkeypatch.delenv("ALPHA_OWNER_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key-setup-aabbccdd11223344")

    import app as flask_app
    flask_app.app.config["TESTING"]          = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    flask_app.app.config["SECRET_KEY"]       = "test-secret-key-setup-aabbccdd11223344"

    with flask_app.app.test_client() as c:
        yield c

    flask_app.app.config["TESTING"] = True


# ══════════════════════════════════════════════════════════════════════════════
# Kurulum sayfası erişim sınırları
# ══════════════════════════════════════════════════════════════════════════════

class TestSetupAccess:

    def test_setup_returns_404_when_password_configured(self, configured_client):
        """/setup, ADMIN_PASSWORD_HASH ayarlıyken 404 döndürmeli."""
        resp = configured_client.get("/setup")
        assert resp.status_code == 404, (
            f"/setup, parola yapılandırıldığında 404 bekleniyor; {resp.status_code} alındı"
        )

    def test_setup_accessible_when_password_not_configured(self, unconfigured_client):
        """/setup, ADMIN_PASSWORD_HASH yokken 200 dönmeli."""
        resp = unconfigured_client.get("/setup")
        assert resp.status_code == 200, (
            f"/setup, parola eksikken 200 bekleniyor; {resp.status_code} alındı"
        )

    def test_setup_hash_endpoint_blocked_when_configured(self, configured_client):
        """POST /setup/hash, parola yapılandırılmışken 404 dönmeli (varlık ifşa etmez)."""
        resp = configured_client.post(
            "/setup/hash",
            data=json.dumps({"password": "anypassword"}),
            content_type="application/json",
        )
        assert resp.status_code == 404, (
            f"POST /setup/hash, parola yapılandırıldığında 404 bekleniyor; {resp.status_code} alındı"
        )
        assert not resp.data  # gövde boş — varlık/durum ifşa edilmez


# ══════════════════════════════════════════════════════════════════════════════
# /setup/check — Yapılandırma durumu ifşası (bilgi sızıntısı incelemesi)
# ══════════════════════════════════════════════════════════════════════════════

class TestSetupCheckDisclosure:

    def test_setup_check_returns_status_only_when_not_configured(self, unconfigured_client):
        """GET /setup/check, parola YOKKEN JSON durum döndürmeli (sihirbaz için)."""
        resp = unconfigured_client.get("/setup/check")
        assert resp.status_code == 200, (
            f"/setup/check, parola eksikken 200 bekleniyor; {resp.status_code} alındı"
        )
        body = resp.get_json()
        assert body == {"configured": False}

    def test_setup_check_hidden_after_configuration(self, configured_client):
        """
        GET /setup/check, parola yapılandırıldıktan sonra 404 dönmeli.
        Anonim istemciler yapılandırma durumunu sorgulayamamalı; endpoint
        var olmayan bir route ile ayırt edilememeli (/setup ile aynı davranış).
        """
        resp = configured_client.get("/setup/check")
        assert resp.status_code == 404, (
            f"/setup/check, kurulum sonrası 404 bekleniyor; {resp.status_code} alındı"
        )
        assert resp.get_json() is None, "404 yanıtı JSON durum bilgisi içermemeli"
        assert b"configured" not in resp.data

    def test_setup_check_matches_setup_page_after_configuration(self, configured_client):
        """Kurulum sonrası /setup/check, /setup ile aynı 404 davranışını göstermeli."""
        check = configured_client.get("/setup/check")
        setup = configured_client.get("/setup")
        assert check.status_code == setup.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# /login → /setup yönlendirmesi (parola eksikken)
# ══════════════════════════════════════════════════════════════════════════════

class TestLoginRedirectsToSetup:

    def test_login_redirects_to_setup_when_no_password(self, unconfigured_client):
        """/login, ADMIN_PASSWORD_HASH yokken /setup'a yönlendirmeli."""
        resp = unconfigured_client.get("/login", follow_redirects=False)
        assert resp.status_code == 302, (
            f"/login, parola eksikken 302 bekleniyor; {resp.status_code} alındı"
        )
        location = resp.headers.get("Location", "")
        assert "/setup" in location, (
            f"/login yönlendirmesi /setup içermeli; alınan: {location!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# /setup/hash — Hash üretimi doğrulaması
# ══════════════════════════════════════════════════════════════════════════════

class TestSetupHashEndpoint:

    def test_mismatched_short_password_returns_error(self, unconfigured_client):
        """
        POST /setup/hash, çok kısa parola gönderildiğinde hata JSON döndürmeli,
        hash döndürmemeli.  (Sunucu tarafı: en az 6 karakter zorunlu.)
        """
        resp = unconfigured_client.post(
            "/setup/hash",
            data=json.dumps({"password": "abc"}),
            content_type="application/json",
        )
        assert resp.status_code == 400, (
            f"Kısa parola için 400 bekleniyor; {resp.status_code} alındı"
        )
        body = resp.get_json()
        assert body is not None, "Yanıt JSON olmalı"
        assert "error" in body, "Yanıt 'error' alanı içermeli"
        assert "hash" not in body, "Hata durumunda 'hash' alanı olmamalı"

    def test_empty_password_returns_error(self, unconfigured_client):
        """POST /setup/hash, boş parola gönderildiğinde hata döndürmeli."""
        resp = unconfigured_client.post(
            "/setup/hash",
            data=json.dumps({"password": ""}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body is not None and "error" in body
        assert "hash" not in body

    def test_valid_password_returns_werkzeug_hash(self, unconfigured_client):
        """POST /setup/hash, geçerli parola için Werkzeug hash string döndürmeli."""
        from werkzeug.security import check_password_hash

        resp = unconfigured_client.post(
            "/setup/hash",
            data=json.dumps({"password": "strongpassword99!"}),
            content_type="application/json",
        )
        assert resp.status_code == 200, (
            f"Geçerli parola için 200 bekleniyor; {resp.status_code} alındı"
        )
        body = resp.get_json()
        assert body is not None, "Yanıt JSON olmalı"
        assert "hash" in body, "Yanıt 'hash' alanı içermeli"

        pw_hash = body["hash"]
        assert isinstance(pw_hash, str) and len(pw_hash) > 20, (
            "Hash geçerli bir Werkzeug hash string olmalı"
        )
        # Hash'in doğru parolayı doğruladığını kontrol et
        assert check_password_hash(pw_hash, "strongpassword99!"), (
            "Döndürülen hash orijinal parolayla doğrulanabilmeli"
        )
        # Yanlış parola doğrulanmamalı
        assert not check_password_hash(pw_hash, "wrongpassword"), (
            "Hash yanlış parolayla eşleşmemeli"
        )


# ══════════════════════════════════════════════════════════════════════════════
# /setup/save — Windows/yerel .env otomatik kayıt
# ══════════════════════════════════════════════════════════════════════════════

class TestSetupSaveEndpoint:
    """POST /setup/save yerel ortamda .env'e yazar ve os.environ'u günceller."""

    def _make_hash(self):
        from werkzeug.security import generate_password_hash
        return generate_password_hash("testpassword99!")

    def test_save_blocked_when_already_configured(self, configured_client):
        """Kurulum tamamlanmışken /setup/save 404 dönmeli."""
        resp = configured_client.post(
            "/setup/save",
            data=json.dumps({"password_hash": self._make_hash(), "username": "admin"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_save_blocked_on_replit(self, unconfigured_client):
        """Replit ortamında /setup/save 403 dönmeli."""
        import local_env
        with patch.object(local_env, "is_replit", return_value=True):
            resp = unconfigured_client.post(
                "/setup/save",
                data=json.dumps({"password_hash": self._make_hash(), "username": "admin"}),
                content_type="application/json",
            )
        assert resp.status_code == 403
        body = resp.get_json()
        assert body["error"]["code"] == "REPLIT_ENV"

    def test_save_rejects_missing_hash(self, unconfigured_client):
        """Hash eksikse 400 döndürmeli."""
        import local_env
        with patch.object(local_env, "is_replit", return_value=False):
            resp = unconfigured_client.post(
                "/setup/save",
                data=json.dumps({"username": "admin"}),
                content_type="application/json",
            )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "MISSING_HASH"

    def test_save_rejects_invalid_hash_format(self, unconfigured_client):
        """Werkzeug formatında olmayan hash reddedilmeli."""
        import local_env
        with patch.object(local_env, "is_replit", return_value=False):
            resp = unconfigured_client.post(
                "/setup/save",
                data=json.dumps({"password_hash": "notahash", "username": "admin"}),
                content_type="application/json",
            )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "INVALID_HASH"

    def test_save_rejects_empty_username(self, unconfigured_client):
        """Kullanıcı adı boşsa 400 + Türkçe mesaj döndürmeli."""
        import local_env
        with patch.object(local_env, "is_replit", return_value=False):
            resp = unconfigured_client.post(
                "/setup/save",
                data=json.dumps({"password_hash": self._make_hash(), "username": ""}),
                content_type="application/json",
            )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"]["code"] == "MISSING_USERNAME"
        assert "boş" in body["error"]["message"]

    def test_save_rejects_username_with_invalid_chars(self, unconfigured_client):
        """Boşluk/özel karakter içeren kullanıcı adı 400 + net Türkçe mesaj döndürmeli."""
        import local_env
        pw_hash = self._make_hash()
        for bad in ["admin user", "admin!", "kullanıcı", "a@b", "user\t", " admin"]:
            with patch.object(local_env, "is_replit", return_value=False):
                resp = unconfigured_client.post(
                    "/setup/save",
                    data=json.dumps({"password_hash": pw_hash, "username": bad}),
                    content_type="application/json",
                )
            assert resp.status_code == 400, f"kabul edildi: {bad!r}"
            body = resp.get_json()
            assert body["error"]["code"] == "INVALID_USERNAME", bad
            assert "harf, rakam" in body["error"]["message"]

    def test_save_accepts_valid_username_charset(self, unconfigured_client, tmp_path):
        """[A-Za-z0-9_-] içindeki kullanıcı adları kabul edilmeli."""
        import local_env
        import os
        pw_hash = self._make_hash()
        fake_env = tmp_path / ".env"
        fake_env.write_text("", encoding="utf-8")
        for good in ["admin", "Test_User-01", "a", "A-B_c9"]:
            with patch.object(local_env, "is_replit", return_value=False), \
                 patch.object(local_env, "ENV_FILE", fake_env):
                resp = unconfigured_client.post(
                    "/setup/save",
                    data=json.dumps({"password_hash": pw_hash, "username": good}),
                    content_type="application/json",
                )
            assert resp.status_code == 200, f"reddedildi: {good!r}"
            # Başarılı kayıt os.environ'u günceller ve sihirbazı kapatır;
            # sonraki iterasyon için "yapılandırılmamış" duruma geri dön.
            os.environ.pop("ALPHA_OWNER_PASSWORD_HASH", None)
            os.environ.pop("ALPHA_OWNER_USERNAME", None)

    def test_save_writes_env_and_updates_environ(self, unconfigured_client, tmp_path):
        """Geçerli istek: .env dosyasına yazar ve os.environ'u hemen günceller."""
        import local_env
        import os
        fake_env = tmp_path / ".env"
        fake_env.write_text("EXISTING_KEY=existing_value\n", encoding="utf-8")
        pw_hash = self._make_hash()
        with patch.object(local_env, "is_replit", return_value=False), \
             patch.object(local_env, "ENV_FILE", fake_env):
            resp = unconfigured_client.post(
                "/setup/save",
                data=json.dumps({"password_hash": pw_hash, "username": "testoperator"}),
                content_type="application/json",
            )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        # .env dosyası yeni anahtarları içermeli
        env_text = fake_env.read_text(encoding="utf-8")
        assert "ALPHA_OWNER_USERNAME=testoperator" in env_text
        assert "ALPHA_OWNER_PASSWORD_HASH=" in env_text
        # Değerin kendisi loglanmaz/gizlenmez ama dosyada vardır
        assert pw_hash in env_text
        # Mevcut anahtar korunmalı
        assert "EXISTING_KEY=existing_value" in env_text
        # os.environ güncellenmiş olmalı
        assert os.environ.get("ALPHA_OWNER_USERNAME") == "testoperator"

    def test_save_overwrites_existing_env_key(self, unconfigured_client, tmp_path):
        """Aynı anahtar .env'de zaten varsa satır güncellenmeli (duplikasyon olmamalı)."""
        import local_env
        fake_env = tmp_path / ".env"
        fake_env.write_text(
            "ALPHA_OWNER_USERNAME=olduser\nALPHA_OWNER_PASSWORD_HASH=oldhash\n",
            encoding="utf-8"
        )
        pw_hash = self._make_hash()
        with patch.object(local_env, "is_replit", return_value=False), \
             patch.object(local_env, "ENV_FILE", fake_env):
            resp = unconfigured_client.post(
                "/setup/save",
                data=json.dumps({"password_hash": pw_hash, "username": "newuser"}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        env_text = fake_env.read_text(encoding="utf-8")
        # Sadece bir kez görünmeli
        assert env_text.count("ALPHA_OWNER_USERNAME=") == 1
        assert "ALPHA_OWNER_USERNAME=newuser" in env_text
        assert "olduser" not in env_text
