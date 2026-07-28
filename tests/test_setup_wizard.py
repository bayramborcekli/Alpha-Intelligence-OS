"""
tests/test_setup_wizard.py — Setup sihirbazı güvenlik testleri

Parola ayarlandıktan sonra /setup ve /setup/hash endpoint'lerinin
yetkisiz erişime kapalı kaldığını doğrular.

Ayrıca Windows/yerel kurulum için /setup/save endpoint'inin
data/local_admin.json dosyasına atomic yazma akışını test eder
(env/.env'e YAZILMAZ — Replit Secrets'tan tam ayrım).
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
# Yardımcı: local_admin deposunu geçici dizine yönlendir
# ══════════════════════════════════════════════════════════════════════════════

def _patch_local_admin(tmp_path):
    """local_admin dosya yollarını tmp_path altına taşıyan patch context'leri."""
    import local_admin
    root = tmp_path
    data_dir = root / "data"
    file_ = data_dir / "local_admin.json"
    return [
        patch.object(local_admin, "ROOT", root),
        patch.object(local_admin, "DATA_DIR", data_dir),
        patch.object(local_admin, "FILE", file_),
    ], file_


# ══════════════════════════════════════════════════════════════════════════════
# /setup/save — Windows/yerel data/local_admin.json otomatik kayıt
# ══════════════════════════════════════════════════════════════════════════════

class TestSetupSaveEndpoint:
    """POST /setup/save yerel ortamda data/local_admin.json'a atomic yazar."""

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
        import contextlib
        import local_env
        pw_hash = self._make_hash()
        patches, file_ = _patch_local_admin(tmp_path)
        for good in ["admin", "Test_User-01", "a", "A-B_c9"]:
            with contextlib.ExitStack() as stack:
                stack.enter_context(
                    patch.object(local_env, "is_replit", return_value=False))
                for p in patches:
                    stack.enter_context(p)
                resp = unconfigured_client.post(
                    "/setup/save",
                    data=json.dumps({"password_hash": pw_hash, "username": good}),
                    content_type="application/json",
                )
                assert resp.status_code == 200, f"reddedildi: {good!r}"
                # Başarılı kayıt dosyayı oluşturur ve sihirbazı kapatır;
                # sonraki iterasyon için "yapılandırılmamış" duruma geri dön.
                file_.unlink()

    def test_save_writes_local_admin_file(self, unconfigured_client, tmp_path):
        """Geçerli istek: data/local_admin.json'a yazar — .env/env'e DOKUNMAZ."""
        import contextlib
        import os
        import local_env
        pw_hash = self._make_hash()
        patches, file_ = _patch_local_admin(tmp_path)
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(local_env, "is_replit", return_value=False))
            for p in patches:
                stack.enter_context(p)
            resp = unconfigured_client.post(
                "/setup/save",
                data=json.dumps({"password_hash": pw_hash, "username": "testoperator"}),
                content_type="application/json",
            )
            assert resp.status_code == 200, resp.get_data(as_text=True)
            assert resp.get_json()["ok"] is True
            # Dosya oluşmalı ve yalnızca izinli 4 alanı içermeli
            rec = json.loads(file_.read_text(encoding="utf-8"))
            assert set(rec) == {"schema_version", "username",
                                "password_hash", "created_at"}
            assert rec["username"] == "testoperator"
            assert rec["password_hash"] == pw_hash
        # env/os.environ ASLA güncellenmez (Secrets'tan tam ayrım)
        assert os.environ.get("ALPHA_OWNER_USERNAME") != "testoperator"

    def test_save_overwrites_existing_file(self, unconfigured_client, tmp_path):
        """Dosya zaten varsa (ör. bozuk/yarım kurulum) yeni kayıt üzerine yazılır."""
        import contextlib
        import local_env
        pw_hash = self._make_hash()
        patches, file_ = _patch_local_admin(tmp_path)
        file_.parent.mkdir(parents=True, exist_ok=True)
        file_.write_text("{bozuk json", encoding="utf-8")  # fail-closed durum
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(local_env, "is_replit", return_value=False))
            for p in patches:
                stack.enter_context(p)
            resp = unconfigured_client.post(
                "/setup/save",
                data=json.dumps({"password_hash": pw_hash, "username": "newuser"}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            rec = json.loads(file_.read_text(encoding="utf-8"))
            assert rec["username"] == "newuser"


# ══════════════════════════════════════════════════════════════════════════════
# Kurulum → giriş tam döngüsü (Windows giriş regresyon testi)
# ══════════════════════════════════════════════════════════════════════════════

class TestSetupToLoginFlow:
    """
    İlk kurulum sihirbazının TAM döngüsünü kapsayan entegrasyon testi:

        POST /setup/hash  → paroladan hash üret
        POST /setup/save  → data/local_admin.json'a atomic yaz
        GET  /setup/check → kurulum tamamlandı (404 = artık ifşa yok)
        POST /login       → YENİDEN BAŞLATMA OLMADAN giriş başarılı

    Windows'ta auth her istekte local_admin.json'ı okuduğu için restart
    gerekmez; bu test o davranışın sessizce kırılmasını önler.
    """

    PASSWORD = "wizard-flow-pass-2026!"
    USERNAME = "flowoperator"

    def _patch_windows(self, monkeypatch, tmp_path):
        """Tüm test boyunca Windows/yerel ortamı simüle et."""
        import local_admin
        import local_env
        monkeypatch.setattr(local_env, "is_replit", lambda: False)
        monkeypatch.setattr(local_admin, "ROOT", tmp_path)
        monkeypatch.setattr(local_admin, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(local_admin, "FILE",
                            tmp_path / "data" / "local_admin.json")
        return tmp_path / "data" / "local_admin.json"

    def _run_wizard(self, client):
        """Sihirbaz adımlarını (hash → save) çalıştırır; save yanıtını döndürür."""
        # Adım 1: paroladan hash üret
        resp = client.post(
            "/setup/hash",
            data=json.dumps({"password": self.PASSWORD}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        pw_hash = resp.get_json()["password_hash"]
        assert pw_hash.startswith(("pbkdf2:", "scrypt:"))

        # Adım 2: data/local_admin.json'a kaydet (yerel/Windows yolu)
        save = client.post(
            "/setup/save",
            data=json.dumps({"password_hash": pw_hash,
                             "username": self.USERNAME}),
            content_type="application/json",
        )
        return save, pw_hash

    def test_full_cycle_login_without_restart(self, unconfigured_client,
                                              tmp_path, monkeypatch):
        """Kurulum → kaydet → doğrula → giriş; restart olmadan başarılı olmalı."""
        import auth

        file_ = self._patch_windows(monkeypatch, tmp_path)

        # Kurulumdan önce sistem kilidi: giriş sihirbaza yönlendirir
        pre = unconfigured_client.get("/login", follow_redirects=False)
        assert pre.status_code == 302 and "/setup" in pre.headers.get("Location", "")

        save, pw_hash = self._run_wizard(unconfigured_client)
        assert save.status_code == 200, save.get_data(as_text=True)
        assert save.get_json()["ok"] is True

        # Dosya kalıcı kayıt içermeli (env'e YAZILMAZ)
        rec = json.loads(file_.read_text(encoding="utf-8"))
        assert rec["username"] == self.USERNAME
        assert rec["password_hash"] == pw_hash

        # Adım 3: /setup/check — kurulum tamamlandı, endpoint artık 404
        # (güvenlik kararı: yapılandırma durumu anonim istemciye ifşa edilmez)
        assert auth.password_hash_configured() is True
        check = unconfigured_client.get("/setup/check")
        assert check.status_code == 404

        # Sihirbaz da kapanmış olmalı
        assert unconfigured_client.get("/setup").status_code == 404

        # Adım 4: POST /login — restart olmadan giriş başarılı
        login = unconfigured_client.post(
            "/login",
            data={"username": self.USERNAME, "password": self.PASSWORD},
            follow_redirects=False,
        )
        assert login.status_code == 302, (
            f"Giriş 302 (başarı) bekleniyor; {login.status_code} alındı: "
            f"{login.get_data(as_text=True)[:300]}"
        )
        assert login.headers.get("Location", "").endswith("/home")

        # Oturum gerçekten açık: korumalı bir sayfa login'e yönlendirmemeli
        home = unconfigured_client.get("/login", follow_redirects=False)
        assert home.status_code == 302
        assert "/home" in home.headers.get("Location", "")

    def test_wrong_password_still_rejected_after_setup(self, unconfigured_client,
                                                       tmp_path, monkeypatch):
        """Kurulum sonrası YANLIŞ parola ile giriş başarısız kalmalı."""
        self._patch_windows(monkeypatch, tmp_path)
        save, pw_hash = self._run_wizard(unconfigured_client)
        assert save.status_code == 200

        resp = unconfigured_client.post(
            "/login",
            data={"username": self.USERNAME, "password": "wrong-password-!!"},
            follow_redirects=False,
        )
        # Başarısız giriş: yönlendirme YOK, login sayfası hata ile döner
        assert resp.status_code == 200, (
            f"Yanlış parola için 200 (hata sayfası) bekleniyor; "
            f"{resp.status_code} alındı"
        )
        assert "hatal" in resp.get_data(as_text=True).lower(), (
            "Yanıt gövdesinde hata mesajı bekleniyor"
        )
        # Oturum açılmamış olmalı: /login hâlâ erişilebilir (yönlendirme yok)
        again = unconfigured_client.get("/login", follow_redirects=False)
        assert again.status_code == 200

        # Doğru parola ile giriş hâlâ mümkün (kilitlenme yok)
        ok = unconfigured_client.post(
            "/login",
            data={"username": self.USERNAME, "password": self.PASSWORD},
            follow_redirects=False,
        )
        assert ok.status_code == 302
        assert ok.headers.get("Location", "").endswith("/home")
