"""
tests/test_security.py — Alpha-20 v1 Güvenlik Taban Çizgisi Testleri
Kimlik doğrulama, rate limiting, güvenlik başlıkları, PAPER kilidi,
güvenlik logu ve giriş doğrulama testleri.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════════════
# Yardımcılar ve fixture'lar
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Her testten önce/sonra rate limiter belleğini temizle."""
    import auth
    with auth._LOCK:
        auth._ATTEMPTS.clear()
    yield
    with auth._LOCK:
        auth._ATTEMPTS.clear()


@pytest.fixture
def auth_client(monkeypatch):
    """
    Flask test istemcisi — AUTH etkin, CSRF devre dışı.
    TESTING=False → /login zorunlu.
    """
    from werkzeug.security import generate_password_hash

    monkeypatch.setenv("ADMIN_USERNAME",        "testadmin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH",   generate_password_hash("testpass1234"))
    monkeypatch.setenv("FLASK_SECRET_KEY",      "test-secret-key-aabbccdd11223344aabbccdd")

    import app as flask_app
    flask_app.app.config["TESTING"]          = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    flask_app.app.config["SECRET_KEY"]       = "test-secret-key-aabbccdd11223344aabbccdd"

    with flask_app.app.test_client() as c:
        yield c

    # Sonraki testler için TESTING'i geri al
    flask_app.app.config["TESTING"] = True


def _login(client) -> None:
    """Test istemcisini giriş yapmış konuma getir."""
    client.post("/login", data={
        "username": "testadmin",
        "password": "testpass1234",
    }, follow_redirects=False)


# ══════════════════════════════════════════════════════════════════════════════
# Kimlik doğrulama testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthentication:

    def test_login_page_accessible_without_session(self, auth_client):
        """Giriş sayfası oturum olmadan erişilebilmeli."""
        resp = auth_client.get("/login")
        assert resp.status_code == 200
        assert b"login" in resp.data.lower() or b"giri" in resp.data.lower()

    def test_dashboard_requires_login(self, auth_client):
        """Dashboard oturum olmadan /login'e yönlendirmeli."""
        resp = auth_client.get("/")
        assert resp.status_code == 302
        loc = resp.headers.get("Location", "")
        assert "/login" in loc

    def test_api_status_requires_login(self, auth_client):
        """API rotaları da oturum gerektirmeli."""
        resp = auth_client.get("/api/status")
        assert resp.status_code in (302, 401)

    def test_correct_credentials_log_in(self, auth_client):
        """Doğru kimlik bilgileri başarılı giriş yapmalı."""
        resp = auth_client.post("/login", data={
            "username": "testadmin",
            "password": "testpass1234",
        }, follow_redirects=False)
        assert resp.status_code == 302
        loc = resp.headers.get("Location", "")
        assert "/login" not in loc  # login sayfasına geri dönmemeli

    def test_wrong_password_rejected(self, auth_client):
        """Yanlış parola login sayfasında kalmalı."""
        resp = auth_client.post("/login", data={
            "username": "testadmin",
            "password": "wrongpass",
        })
        assert resp.status_code == 200
        body = resp.data.lower()
        assert b"hatal" in body or b"ge" in body or b"incorrect" in body

    def test_wrong_username_rejected(self, auth_client):
        """Yanlış kullanıcı adı reddedilmeli."""
        resp = auth_client.post("/login", data={
            "username": "hackerx",
            "password": "testpass1234",
        })
        assert resp.status_code == 200

    def test_dashboard_accessible_after_login(self, auth_client):
        """Giriş sonrası dashboard 200 dönmeli."""
        _login(auth_client)
        resp = auth_client.get("/")
        assert resp.status_code == 200

    def test_logout_clears_session(self, auth_client):
        """Çıkış oturumu temizlemeli."""
        _login(auth_client)
        resp = auth_client.get("/logout")
        assert resp.status_code == 302
        # Çıkıştan sonra dashboard erişilemez
        resp2 = auth_client.get("/")
        assert resp2.status_code == 302
        assert "/login" in resp2.headers.get("Location", "")

    def test_no_password_hash_blocks_login(self, auth_client, monkeypatch):
        """ADMIN_PASSWORD_HASH tanımlı değilse /login kurulum sihirbazına yönlendirmeli."""
        monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
        resp = auth_client.get("/login")
        # Parola yokken /login → /setup yönlendirmesi (302)
        assert resp.status_code == 302
        assert "/setup" in resp.headers.get("Location", "")

    def test_next_url_redirect_relative_only(self, auth_client):
        """next parametresi yalnızca göreceli URL'lere yönlendirmeli."""
        resp = auth_client.get("/login?next=http://evil.com/steal")
        assert resp.status_code == 200  # login sayfası gösterilmeli

    def test_post_to_protected_route_redirects(self, auth_client):
        """POST ile korumalı route'a oturumsuz erişim yönlendirmeli."""
        resp = auth_client.post("/settings", data={
            "minimum_score": "65", "scan_seconds": "60",
            "risk_per_trade_pct": "0.5", "daily_loss_limit_pct": "1.5",
            "max_consecutive_losses": "3", "reward_risk_ratio": "2.0",
            "atr_stop_multiplier": "1.5", "max_open_positions": "1",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


# ══════════════════════════════════════════════════════════════════════════════
# Rate limiting testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestRateLimiting:

    def test_5_failures_triggers_lockout(self, auth_client):
        """5 başarısız denemeden sonra IP kilitlenmeli."""
        for _ in range(5):
            auth_client.post("/login", data={
                "username": "testadmin", "password": "badpass"
            })
        resp = auth_client.post("/login", data={
            "username": "testadmin", "password": "testpass1234"
        })
        # Kilitli mesajı içermeli
        assert resp.status_code == 200
        assert b"bekleyin" in resp.data.lower()

    def test_successful_login_resets_counter(self):
        """Başarılı giriş sayacı sıfırlamalı."""
        import auth
        auth.record_attempt("1.2.3.4", success=False)
        auth.record_attempt("1.2.3.4", success=False)
        auth.record_attempt("1.2.3.4", success=True)
        allowed, secs = auth.check_rate_limit("1.2.3.4")
        assert allowed is True
        assert secs == 0

    def test_different_ips_are_independent(self):
        """Farklı IP'lerin sayaçları birbirinden bağımsız olmalı."""
        import auth
        for _ in range(5):
            auth.record_attempt("10.0.0.1", success=False)
        allowed, _ = auth.check_rate_limit("10.0.0.2")
        assert allowed is True

    def test_check_rate_limit_returns_seconds_when_locked(self):
        """Kilitliyken kalan süre pozitif olmalı."""
        import auth
        for _ in range(5):
            auth.record_attempt("5.5.5.5", success=False)
        allowed, secs = auth.check_rate_limit("5.5.5.5")
        assert allowed is False
        assert secs > 0


# ══════════════════════════════════════════════════════════════════════════════
# IP sahteciliği (X-Forwarded-For spoofing) testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestIpSpoofing:

    def test_get_client_ip_ignores_forwarded_header_by_default(self):
        """TRUSTED_PROXY_IPS tanımsızken X-Forwarded-For tamamen yok sayılmalı;
        soket adresi (remote_addr) döndürülmeli."""
        import auth
        from flask import Flask
        raw_app = Flask(__name__)
        with raw_app.test_request_context(
            "/", headers={"X-Forwarded-For": "6.6.6.6"},
            environ_base={"REMOTE_ADDR": "9.9.9.9"},
        ):
            assert auth.get_client_ip() == "9.9.9.9"

    def test_rotating_forwarded_header_does_not_reset_lockout(self, auth_client):
        """ANA GEREKSİNİM: Doğrudan bağlanan saldırgan her istekte FARKLI
        tek-girdilik X-Forwarded-For gönderse bile kilit sıfırlanmamalı.
        Başlık güvenilmediği için kilit soket adresi üzerinden işler."""
        for i in range(5):
            auth_client.post(
                "/login",
                data={"username": "testadmin", "password": "badpass"},
                headers={"X-Forwarded-For": f"10.66.{i}.{i}"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        # 6. deneme: doğru parola + yine farklı sahte başlık — yine de kilitli
        resp = auth_client.post(
            "/login",
            data={"username": "testadmin", "password": "testpass1234"},
            headers={"X-Forwarded-For": "10.99.99.99"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert resp.status_code == 200
        assert b"bekleyin" in resp.data.lower()

    def test_untrusted_peer_forwarded_header_never_trusted(self, auth_client):
        """Sahte başlıklı denemeler soket IP'si altında sayılmalı; başlıktaki
        IP'ler için ayrı sayaç OLUŞMAMALI."""
        import auth
        for i in range(5):
            auth_client.post(
                "/login",
                data={"username": "testadmin", "password": "badpass"},
                headers={"X-Forwarded-For": f"198.51.100.{i}"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        # Kilit gerçek soket adresinde
        allowed, _ = auth.check_rate_limit("127.0.0.1")
        assert allowed is False
        # Sahte IP'ler hiç sayılmamış olmalı
        with auth._LOCK:
            assert not any(k.startswith("198.51.100.") for k in auth._ATTEMPTS)

    def test_trusted_proxy_uses_last_forwarded_entry(self, auth_client, monkeypatch):
        """Güvenilir proxy tanımlıyken yalnızca zincirin SON girdisi (proxy'nin
        eklediği gerçek istemci IP'si) kullanılmalı; saldırganın öne eklediği
        sahte girdiler ve rotasyonu kilidi sıfırlamamalı."""
        import auth
        monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1")
        real_ip = "203.0.113.7"
        for i in range(5):
            auth_client.post(
                "/login",
                data={"username": "testadmin", "password": "badpass"},
                headers={"X-Forwarded-For": f"10.66.{i}.{i}, {real_ip}"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        allowed, secs = auth.check_rate_limit(real_ip)
        assert allowed is False
        assert secs > 0

    def test_trusted_proxy_malformed_header_falls_back_to_socket(self, monkeypatch):
        """Güvenilir proxy'den gelse bile bozuk (IP olmayan) başlık değeri
        kullanılmamalı; soket adresine dönülmeli."""
        import auth
        from flask import Flask
        monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1")
        raw_app = Flask(__name__)
        with raw_app.test_request_context(
            "/", headers={"X-Forwarded-For": "not-an-ip"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            assert auth.get_client_ip() == "127.0.0.1"


# ══════════════════════════════════════════════════════════════════════════════
# Güvenlik HTTP başlıkları testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurityHeaders:

    def test_headers_on_login_page(self, auth_client):
        """Login sayfasında güvenlik başlıkları mevcut olmalı."""
        resp = auth_client.get("/login")
        h = resp.headers
        assert h.get("X-Content-Type-Options") == "nosniff"
        assert h.get("X-Frame-Options") == "DENY"
        assert "Referrer-Policy" in h
        assert "Content-Security-Policy" in h
        assert "Permissions-Policy" in h

    def test_headers_on_dashboard(self, auth_client):
        """Dashboard sayfasında da güvenlik başlıkları olmalı."""
        _login(auth_client)
        resp = auth_client.get("/")
        h = resp.headers
        assert h.get("X-Content-Type-Options") == "nosniff"
        assert h.get("X-Frame-Options") == "DENY"
        assert "Content-Security-Policy" in h

    def test_csp_blocks_external_resources(self, auth_client):
        """CSP default-src 'self' ile harici kaynakları engellmeli."""
        resp = auth_client.get("/login")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp


# ══════════════════════════════════════════════════════════════════════════════
# PAPER modu güvenlik testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestPaperModeLock:

    def test_config_mode_is_paper(self):
        """config.json içindeki mode 'PAPER' olmalı."""
        cfg_path = ROOT / "alpha20_v1" / "config.json"
        if not cfg_path.exists():
            pytest.skip("config.json bulunamadı")
        with cfg_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        assert cfg.get("mode") == "PAPER", "Mod PAPER değil!"

    def test_kill_switch_defaults_false_in_code(self):
        """ADAPTIVE_DEFAULTS kod seviyesinde kill_switch=False olmalı."""
        import app as flask_app
        assert flask_app.ADAPTIVE_DEFAULTS.get("kill_switch") is False, \
            "ADAPTIVE_DEFAULTS'ta kill_switch True olmamalı!"

    def test_no_live_order_functions_in_source(self):
        """Üretim kaynak kodunda canlı emir fonksiyonu olmamalı (test dosyaları hariç)."""
        forbidden_calls = [
            "create" + "_order(",   # parçalı — test dosyasını kirletmesin
            "place" + "_order(",
            "submit" + "_order(",
            "new" + "_order(",
        ]
        excluded = (".pythonlibs", "__pycache__", ".git", "/tests/", "test_")
        for src in ROOT.rglob("*.py"):
            src_str = str(src)
            if any(skip in src_str for skip in excluded):
                continue
            try:
                text = src.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for call in forbidden_calls:
                assert call not in text, (
                    f"Canlı emir kodu: '{call}' bulundu → {src.relative_to(ROOT)}"
                )

    def test_config_has_no_api_credentials(self):
        """config.json API anahtarı veya parola içermemeli."""
        cfg_path = ROOT / "alpha20_v1" / "config.json"
        if not cfg_path.exists():
            pytest.skip("config.json bulunamadı")
        text = cfg_path.read_text(encoding="utf-8").lower()
        for forbidden in ("api_key", "api_secret", "secret_key", "password"):
            assert forbidden not in text, (
                f"config.json içinde hassas alan: '{forbidden}'"
            )

    def test_enforce_paper_mode_lock_resets_mode(self, tmp_path, monkeypatch):
        """enforce_paper_mode_lock PAPER olmayan modu düzeltmeli."""
        import json as _json
        test_cfg = tmp_path / "config.json"
        test_cfg.write_text(_json.dumps({"mode": "LIVE", "symbols": ["BTCUSDT"]}), encoding="utf-8")

        import app as flask_app
        monkeypatch.setattr(flask_app, "CONFIG_PATH", test_cfg)
        flask_app.enforce_paper_mode_lock()

        with test_cfg.open(encoding="utf-8") as f:
            result = _json.load(f)
        assert result.get("mode") == "PAPER"


# ══════════════════════════════════════════════════════════════════════════════
# Güvenlik logu testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurityLog:

    def test_log_event_writes_to_file(self, tmp_path):
        """log_event güvenlik loguna yazmalı."""
        import security_log as slog
        test_log = tmp_path / "sec.log"
        handler  = logging.FileHandler(str(test_log), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        slog._logger.addHandler(handler)
        try:
            slog.log_event(slog.LOGIN_OK, username="alice", ip="1.2.3.4",
                           detail="unit-test-marker-ok")
            handler.flush()
        finally:
            slog._logger.removeHandler(handler)
            handler.close()
        assert test_log.exists()
        content = test_log.read_text(encoding="utf-8")
        assert "LOGIN_OK" in content
        assert "alice" in content
        assert "unit-test-marker-ok" in content

    def test_sanitize_masks_password_in_detail(self):
        """Parola içeren detay alanı maskelenmeli."""
        import security_log as slog
        assert slog._sanitize("password=mysecret") == "[REDACTED]"
        assert slog._sanitize("api_key=abc123")    == "[REDACTED]"
        assert slog._sanitize("normal detail")     == "normal detail"

    def test_log_event_does_not_include_password_values(self, tmp_path):
        """log_event parola değerlerini loga yazmamalı."""
        import security_log as slog
        test_log = tmp_path / "sec2.log"
        handler  = logging.FileHandler(str(test_log), encoding="utf-8")
        slog._logger.addHandler(handler)
        try:
            # Parola içeren detail otomatik maskelenmeli
            slog.log_event(slog.LOGIN_FAIL, detail="password=secretval", ip="9.9.9.9")
            handler.flush()
        finally:
            slog._logger.removeHandler(handler)
            handler.close()
        content = test_log.read_text(encoding="utf-8")
        assert "secretval" not in content
        assert "[REDACTED]" in content


# ══════════════════════════════════════════════════════════════════════════════
# CSRF güvenliği regresyon testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestCsrfSecurity:

    def test_unauthenticated_post_csrf_error_does_not_leak_dashboard(self, auth_client):
        """
        Kritik regresyon: Kimliksiz POST'ta CSRF hatası dashboard içeriği
        döndürmemeli — /login'e yönlendirmeli.
        """
        import app as flask_app
        flask_app.app.config["WTF_CSRF_ENABLED"] = True
        try:
            # Oturum açmadan, CSRF token'sız POST
            resp = auth_client.post("/settings", data={
                "minimum_score": "65",
                "scan_seconds": "30",
                "risk_per_trade_pct": "0.5",
                "daily_loss_limit_pct": "1.5",
                "max_consecutive_losses": "3",
                "reward_risk_ratio": "2.0",
                "atr_stop_multiplier": "1.5",
                "max_open_positions": "1",
            })
            body = resp.data.lower()
            # Dashboard içeriği KESINLIKLE sızmamalı
            assert b"kontrol paneli" not in body, \
                "Dashboard içeriği kimliksiz CSRF hatasında sızdı!"
            # 302 (login'e yönlendirme) tercih edilir
            if resp.status_code == 302:
                assert "/login" in resp.headers.get("Location", "")
        finally:
            flask_app.app.config["WTF_CSRF_ENABLED"] = False

    def test_authenticated_csrf_error_returns_400(self, auth_client):
        """Giriş yapılmış oturumda CSRF hatası 400 dönmeli (dashboard ile)."""
        import app as flask_app
        _login(auth_client)
        flask_app.app.config["WTF_CSRF_ENABLED"] = True
        try:
            resp = auth_client.post("/settings", data={
                "minimum_score": "65",
                "scan_seconds": "30",
                "risk_per_trade_pct": "0.5",
                "daily_loss_limit_pct": "1.5",
                "max_consecutive_losses": "3",
                "reward_risk_ratio": "2.0",
                "atr_stop_multiplier": "1.5",
                "max_open_positions": "1",
            })
            assert resp.status_code == 400
            # Giriş yapılmış kullanıcıya dashboard içeriği gösterilebilir
        finally:
            flask_app.app.config["WTF_CSRF_ENABLED"] = False


# ══════════════════════════════════════════════════════════════════════════════
# Giriş doğrulama testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestInputValidation:

    def test_invalid_symbol_rejected(self, auth_client):
        """Geçersiz coin sembolü PAPER listesine eklenememeli."""
        _login(auth_client)
        resp = auth_client.post("/coins/add", data={"symbol": "not-a-coin"})
        assert resp.status_code == 200
        assert b"Hata" in resp.data or b"hata" in resp.data.lower()

    def test_xss_payload_in_symbol_rejected(self, auth_client):
        """XSS payload'ı coin sembolü olarak eklenememeli."""
        _login(auth_client)
        auth_client.post("/coins/add", data={"symbol": "<script>alert(1)</script>"})
        resp = auth_client.get("/panel")
        assert b"<script>alert(1)</script>" not in resp.data

    def test_setting_minimum_score_out_of_range(self, auth_client):
        """minimum_score 0–100 dışı reddedilmeli."""
        _login(auth_client)
        resp = auth_client.post("/settings", data={
            "minimum_score":          "999",
            "scan_seconds":           "30",
            "risk_per_trade_pct":     "0.5",
            "daily_loss_limit_pct":   "1.5",
            "max_consecutive_losses": "3",
            "reward_risk_ratio":      "2.0",
            "atr_stop_multiplier":    "1.5",
            "max_open_positions":     "1",
        })
        assert resp.status_code == 200
        assert b"Hata" in resp.data

    def test_atomic_write_json_creates_valid_file(self, tmp_path):
        """atomic_write_json doğru JSON yazar ve dosyayı bırakmaz."""
        import app as flask_app
        target  = tmp_path / "test.json"
        payload = {"mode": "PAPER", "symbols": ["BTCUSDT"], "score": 75}
        flask_app.atomic_write_json(target, payload)
        assert target.exists()
        with target.open(encoding="utf-8") as f:
            result = json.load(f)
        assert result == payload
        # Geçici dosya bırakılmamalı
        assert not list(tmp_path.glob(".*.tmp"))
