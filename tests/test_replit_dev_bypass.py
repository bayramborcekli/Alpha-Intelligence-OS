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
