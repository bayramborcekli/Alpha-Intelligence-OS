# -*- coding: utf-8 -*-
"""MISSION — TEMPORARY WINDOWS LOCAL TEST AUTH BYPASS regresyonu.

LOCAL_DEV_BYPASS=1 yalnız Windows/lokal geliştirmede parola ekranını
atlar. Default kapalı; Replit'te (kendi flag'i var), FLASK_ENV=production
ve REPLIT_DEPLOYMENT ortamlarında asla çalışmaz.
"""
import os
from unittest.mock import patch

import pytest

import app as appmod

_CLEAR = ("LOCAL_DEV_BYPASS", "REPLIT_DEV_BYPASS", "REPL_ID",
          "REPLIT_DEV_DOMAIN", "REPLIT_DEPLOYMENT", "FLASK_ENV")


def _env(**extra):
    base = {k: v for k, v in os.environ.items() if k not in _CLEAR}
    base.update(extra)
    return patch.dict(os.environ, base, clear=True)


@pytest.fixture(autouse=True)
def _restore_testing():
    yield
    appmod.app.config["TESTING"] = True


def _client():
    appmod.app.config["TESTING"] = False
    appmod.app.config["WTF_CSRF_ENABLED"] = False
    return appmod.app.test_client()


class TestLocalBypassFlag:
    def test_default_off(self):
        with _env():
            assert appmod._local_dev_bypass_active() is False

    def test_on_in_plain_local(self):
        with _env(LOCAL_DEV_BYPASS="1"):
            assert appmod._local_dev_bypass_active() is True

    def test_off_in_replit_workspace(self):
        with _env(LOCAL_DEV_BYPASS="1", REPL_ID="x"):
            assert appmod._local_dev_bypass_active() is False

    def test_off_in_production_flask_env(self):
        with _env(LOCAL_DEV_BYPASS="1", FLASK_ENV="production"):
            assert appmod._local_dev_bypass_active() is False

    def test_off_in_published_deployment(self):
        with _env(LOCAL_DEV_BYPASS="1", REPLIT_DEPLOYMENT="1"):
            assert appmod._local_dev_bypass_active() is False

    def test_off_when_flag_not_exactly_1(self):
        with _env(LOCAL_DEV_BYPASS="true"):
            assert appmod._local_dev_bypass_active() is False


class TestLocalGateBehaviour:
    def test_bypass_grants_dashboard_without_login(self):
        with _env(LOCAL_DEV_BYPASS="1"):
            c = _client()
            assert c.get("/api/v1/overview").status_code == 200

    def test_login_redirects_home_no_form(self):
        with _env(LOCAL_DEV_BYPASS="1"):
            c = _client()
            r = c.get("/login")
            assert r.status_code == 302
            assert "/home" in r.headers.get("Location", "")

    def test_no_flag_keeps_password_flow(self):
        with _env(), \
             patch.object(appmod.auth, "password_hash_configured",
                          return_value=True):
            c = _client()
            assert c.get("/api/v1/overview").status_code == 401

    def test_replit_flow_unchanged(self):
        # Replit kendi flag'iyle çalışmaya devam eder
        with _env(REPLIT_DEV_BYPASS="1", REPL_ID="x", LOCAL_DEV_BYPASS="1"):
            c = _client()
            assert c.get("/api/v1/overview").status_code == 200
