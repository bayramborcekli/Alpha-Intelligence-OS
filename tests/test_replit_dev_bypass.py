# -*- coding: utf-8 -*-
"""MISSION — TEMPORARY REPLIT ADMIN BYPASS regresyonu.

REPLIT_DEV_BYPASS=1 yalnız Replit workspace'inde parola ekranını atlar.
Default kapalı; Windows/local ve yayınlanmış üretimde asla çalışmaz.
"""
import os
from unittest.mock import patch

import pytest

import app as appmod


def _client():
    appmod.app.config["TESTING"] = False
    appmod.app.config["WTF_CSRF_ENABLED"] = False
    c = appmod.app.test_client()
    return c


@pytest.fixture(autouse=True)
def _restore_testing():
    yield
    appmod.app.config["TESTING"] = True


def _env(**extra):
    base = {k: v for k, v in os.environ.items()
            if k not in ("REPLIT_DEV_BYPASS", "REPL_ID",
                         "REPLIT_DEV_DOMAIN", "REPLIT_DEPLOYMENT")}
    base.update(extra)
    return patch.dict(os.environ, base, clear=True)


class TestBypassFlag:
    def test_default_off(self):
        with _env(REPL_ID="x"):
            assert appmod._replit_dev_bypass_active() is False

    def test_on_in_replit_workspace(self):
        with _env(REPLIT_DEV_BYPASS="1", REPL_ID="x"):
            assert appmod._replit_dev_bypass_active() is True

    def test_off_on_windows_local_even_with_flag(self):
        with _env(REPLIT_DEV_BYPASS="1"):
            assert appmod._replit_dev_bypass_active() is False

    def test_off_in_published_deployment(self):
        with _env(REPLIT_DEV_BYPASS="1", REPL_ID="x",
                  REPLIT_DEPLOYMENT="1"):
            assert appmod._replit_dev_bypass_active() is False

    def test_off_when_flag_not_exactly_1(self):
        with _env(REPLIT_DEV_BYPASS="true", REPL_ID="x"):
            assert appmod._replit_dev_bypass_active() is False


class TestGateBehaviour:
    def test_bypass_grants_dashboard_without_login(self):
        with _env(REPLIT_DEV_BYPASS="1", REPL_ID="x"):
            c = _client()
            r = c.get("/api/v1/overview")
            assert r.status_code == 200

    def test_no_flag_keeps_password_flow(self):
        with _env(REPL_ID="x"), \
             patch.object(appmod.auth, "password_hash_configured",
                          return_value=True):
            c = _client()
            r = c.get("/api/v1/overview")
            assert r.status_code == 401  # mevcut akış aynen

    def test_flag_without_replit_markers_keeps_password_flow(self):
        with _env(REPLIT_DEV_BYPASS="1"), \
             patch.object(appmod.auth, "password_hash_configured",
                          return_value=True):
            c = _client()
            r = c.get("/api/v1/overview")
            assert r.status_code == 401


class TestArchitectureGuard:
    """Mimari koruma — bypass kodu 'kapsam dışı' diye SESSİZCE SİLİNEMEZ.

    Task #70 merge'i bu bloğu bir kez sildi ve login ekranı geri geldi
    (elle restore: commit 13e34a6). Bu testler silinmeyi kırmızıya çevirir.
    Bypass'ın kaldırılmasına yalnız OPERATÖR karar verir; kaldırırken bu
    guard testleri de operatör onayıyla birlikte kaldırılmalıdır.
    """

    def _app_source(self):
        import inspect
        return inspect.getsource(appmod)

    def test_bypass_helper_exists(self):
        assert callable(getattr(appmod, "_replit_dev_bypass_active", None)), (
            "_replit_dev_bypass_active app.py'den silinmiş! Bu bypass "
            "operatör kararı olmadan kaldırılamaz (bkz. Task #72).")

    def test_security_gate_has_bypass_branch(self):
        import inspect
        gate_src = inspect.getsource(appmod._security_gate)
        assert "_replit_dev_bypass_active()" in gate_src, (
            "_security_gate içindeki REPLIT_DEV_BYPASS dalı silinmiş! "
            "Operatör kararı olmadan kaldırılamaz (bkz. Task #72).")

    def test_replit_config_keeps_dev_env_flag(self):
        import re
        from pathlib import Path
        replit_file = Path(appmod.__file__).parent / ".replit"
        text = replit_file.read_text(encoding="utf-8")
        assert re.search(
            r'^\[userenv\.development\]', text, re.MULTILINE), (
            ".replit dosyasından [userenv.development] bölümü silinmiş!")
        assert re.search(
            r'^REPLIT_DEV_BYPASS\s*=\s*"1"', text, re.MULTILINE), (
            '.replit dosyasından REPLIT_DEV_BYPASS = "1" satırı silinmiş! '
            "scripts/post-merge.sh geri ekler; kaldırma kararı operatörün.")
