# -*- coding: utf-8 -*-
"""MISSION — MERGE REGRESSION GUARD.

Tamamlanmış kritik özelliklerin sonraki görev merge'lerinde "kapsam
dışı" diye SİLİNMESİNİ engelleyen mimari koruma testleri. Task #70
merge'i Replit bypass'ını hem koddan hem .replit userenv'den silmişti
(login ekranı geri geldi) — bu dosya o sınıf regresyonu kırmızı yapar.

KURAL: Bu testlerden biri kırmızıysa, yeni görev eski kritik davranışı
kaldırmıştır → değişiklik geri alınmalı veya operatör onayı alınmalıdır.
Bu dosyanın kendisi de silinmemelidir.
"""
import inspect
from pathlib import Path

import app as appmod
import auth
import local_env

ROOT = Path(__file__).resolve().parent.parent
APP_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
LOCAL_ENV_SRC = (ROOT / "local_env.py").read_text(encoding="utf-8")


class TestReplitBypassGuard:
    """Replit geliştirme bypass'ı (kaldırma kararı yalnız operatörün)."""

    def test_bypass_function_exists(self):
        assert callable(getattr(appmod, "_replit_dev_bypass_active", None)), \
            "_replit_dev_bypass_active silinmiş — bkz. commit 13e34a6 restore"

    def test_gate_calls_bypass(self):
        src = inspect.getsource(appmod._security_gate)
        assert "_replit_dev_bypass_active()" in src, \
            "_security_gate bypass dalını kaybetmiş"

    def test_bypass_three_locks_intact(self):
        src = inspect.getsource(appmod._replit_dev_bypass_active)
        assert "REPLIT_DEV_BYPASS" in src
        assert "REPLIT_DEPLOYMENT" in src, "üretim kilidi silinmiş"
        assert "REPL_ID" in src, "Replit workspace kilidi silinmiş"

    def test_local_windows_bypass_intact(self):
        assert callable(getattr(appmod, "_local_dev_bypass_active", None)), \
            "_local_dev_bypass_active silinmiş — Windows test bypass'ı"
        src = inspect.getsource(appmod._local_dev_bypass_active)
        assert "LOCAL_DEV_BYPASS" in src
        assert "FLASK_ENV" in src, "production kilidi silinmiş"
        assert "REPLIT_DEPLOYMENT" in src, "yayın kilidi silinmiş"
        gate = inspect.getsource(appmod._security_gate)
        assert "_local_dev_bypass_active()" in gate, \
            "_security_gate lokal bypass dalını kaybetmiş"


class TestPaperBootstrapGuard:
    """Temiz clone'da PAPER defterinin otomatik oluşturulması."""

    def test_ensure_paper_state_exists(self):
        assert callable(getattr(appmod, "_ensure_paper_state", None)), \
            "_ensure_paper_state silinmiş — temiz clone'da Kağıt Hesap " \
            "CONNECTION_FAILED olur"

    def test_called_at_module_level(self):
        assert "\n_ensure_paper_state()" in APP_SRC, \
            "_ensure_paper_state ilk açılışta çağrılmıyor"

    def test_exclusive_create_and_fail_closed(self):
        src = inspect.getsource(appmod._ensure_paper_state)
        assert '"x"' in src, "exclusive-create (yarış koruması) silinmiş"
        assert "isinstance(cfg, dict)" in src, "fail-closed guard silinmiş"
        assert "starting_balance_usdt" in src


class TestCleanBootGuard:
    """Temiz kurulum uyarıları ve .env dayanıklılığı."""

    def test_env_missing_warning_intact(self):
        assert "İLK KURULUM" in LOCAL_ENV_SRC, \
            ".env-yok kurulum uyarısı silinmiş"

    def test_env_encoding_fallbacks_intact(self):
        src = inspect.getsource(local_env._parse_env_file)
        assert "utf-8-sig" in src, "Windows BOM düzeltmesi silinmiş"
        assert "utf-16" in src, "Windows UTF-16 düzeltmesi silinmiş"

    def test_env_example_canonical_names(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in ("BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key",
                    "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET"):
            assert f"{key}=" in text, f".env.example kanonik alanı kayıp: {key}"


class TestAuthFlowGuard:
    """Parola sistemi ve güvenlik kapısı (Windows/local akışı)."""

    def test_security_gate_core_checks_intact(self):
        src = inspect.getsource(appmod._security_gate)
        assert "password_hash_configured" in src, "kurulum kilidi silinmiş"
        assert 'session.get("logged_in")' in src, "oturum kontrolü silinmiş"
        assert "_session_expired" in src, "oturum süresi kontrolü silinmiş"

    def test_auth_session_primitives_intact(self):
        for fn in ("start_session", "clear_session", "_session_expired",
                   "password_hash_configured"):
            assert callable(getattr(auth, fn, None)), f"auth.{fn} silinmiş"

    def test_setup_wizard_routes_intact(self):
        rules = {r.rule for r in appmod.app.url_map.iter_rules()}
        for route in ("/setup", "/setup/hash", "/setup/save", "/setup/check",
                      "/login", "/logout"):
            assert route in rules, f"rota silinmiş: {route}"
