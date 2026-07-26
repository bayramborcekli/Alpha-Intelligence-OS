"""
tests/test_setup_wizard.py — Setup sihirbazı güvenlik testleri

Parola ayarlandıktan sonra /setup ve /setup/hash endpoint'lerinin
yetkisiz erişime kapalı kaldığını doğrular.
"""
from __future__ import annotations

import json
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
        """POST /setup/hash, parola yapılandırılmışken 403 dönmeli."""
        resp = configured_client.post(
            "/setup/hash",
            data=json.dumps({"password": "anypassword"}),
            content_type="application/json",
        )
        assert resp.status_code == 403, (
            f"POST /setup/hash, parola yapılandırıldığında 403 bekleniyor; {resp.status_code} alındı"
        )
        body = resp.get_json()
        assert body is not None and "error" in body


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
