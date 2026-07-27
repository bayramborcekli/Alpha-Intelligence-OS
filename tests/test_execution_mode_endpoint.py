"""Dashboard yürütme modu paneli testleri.

/execution/mode fail-closed sözleşmesi: LIVE her varyantıyla
reddedilir, yalnız kapalı küme kabul edilir, her başarılı mod
değişimi bot çekirdeği PAPER kilidini korur, kimliksiz ve
CSRF'siz istekler engellenir.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    original = (ROOT / "alpha20_v1" / "config.json").read_text(
        encoding="utf-8")
    with app_module.app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "test"
            sess["login_time"] = datetime.now(
                timezone.utc).isoformat()
        yield test_client
    (ROOT / "alpha20_v1" / "config.json").write_text(
        original, encoding="utf-8")


def _csrf(client):
    html = client.get("/panel").get_data(as_text=True)
    return re.search(r'var token = "([^"]+)"', html).group(1)


def _post(client, mode, token=None):
    data = {"execution_mode": mode}
    if token is not None:
        data["csrf_token"] = token
    return client.post("/execution/mode", data=data)


def _config():
    return json.loads(
        (ROOT / "alpha20_v1" / "config.json").read_text(
            encoding="utf-8"))


class TestLiveFailClosed:
    @pytest.mark.parametrize("variant", [
        "LIVE", "live", " LIVE ", "Live", "lIvE", "  live  "])
    def test_live_variants_rejected(self, client, variant):
        token = _csrf(client)
        response = _post(client, variant, token)
        body = response.get_data(as_text=True)
        assert "LIVE modu kilitli (fail-closed)" in body
        assert _config().get("execution_mode", "PAPER") != \
            "LIVE"

    @pytest.mark.parametrize("bad", [
        "", "XYZ", "PAPER;LIVE", "MICRO", "LIVE2", "TRUE",
        "paper live"])
    def test_invalid_modes_rejected(self, client, bad):
        token = _csrf(client)
        body = _post(client, bad, token).get_data(as_text=True)
        assert ("Geçersiz yürütme modu" in body or
                "LIVE modu kilitli" in body)


class TestAllowedModes:
    @pytest.mark.parametrize("mode,normalized", [
        ("PAPER", "PAPER"), ("paper", "PAPER"),
        ("SHADOW", "SHADOW"),
        ("MICRO_LIVE", "MICRO_LIVE"),
        ("micro live", "MICRO_LIVE")])
    def test_mode_persisted(self, client, mode, normalized):
        token = _csrf(client)
        body = _post(client, mode, token).get_data(
            as_text=True)
        assert "olarak ayarlandı" in body
        assert _config()["execution_mode"] == normalized

    @pytest.mark.parametrize("mode", ["PAPER", "SHADOW",
                                      "MICRO_LIVE"])
    def test_paper_core_lock_preserved(self, client, mode):
        """Her başarılı mod değişimi bot çekirdeğini PAPER'da
        tutmalıdır."""
        token = _csrf(client)
        _post(client, mode, token)
        assert _config()["mode"] == "PAPER"

    def test_micro_live_warns_authorization_only(self, client):
        token = _csrf(client)
        body = _post(client, "MICRO_LIVE", token).get_data(
            as_text=True)
        assert "yetkilendirme talebi" in body
        assert "borsaya emir yazılmaz" in body


class TestAccessControl:
    """Gerçek güvenlik kapısı TESTING=False iken doğrulanır
    (_security_gate TESTING modunda bilinçli atlanır)."""

    @pytest.fixture()
    def hardened(self):
        app_module.app.config["TESTING"] = False
        app_module.app.config["WTF_CSRF_ENABLED"] = True
        original = (ROOT / "alpha20_v1" /
                    "config.json").read_text(encoding="utf-8")
        yield
        app_module.app.config["TESTING"] = True
        (ROOT / "alpha20_v1" / "config.json").write_text(
            original, encoding="utf-8")

    def test_csrf_required(self, hardened):
        with app_module.app.test_client() as tc:
            with tc.session_transaction() as sess:
                sess["logged_in"] = True
                sess["username"] = "test"
                sess["login_time"] = datetime.now(
                    timezone.utc).isoformat()
            response = tc.post("/execution/mode",
                               data={"execution_mode":
                                     "SHADOW"})
            assert response.status_code == 400

    def test_unauthenticated_blocked(self, hardened):
        before = _config().get("execution_mode", "PAPER")
        with app_module.app.test_client() as anon:
            response = anon.post("/execution/mode",
                                 data={"execution_mode":
                                       "SHADOW"})
            assert response.status_code in (302, 400, 401,
                                            403)
        assert _config().get("execution_mode",
                             "PAPER") == before


class TestHelpers:
    def test_closed_mode_set(self):
        assert app_module.EXECUTION_MODES == (
            "PAPER", "SHADOW", "MICRO_LIVE")
        assert "LIVE" not in app_module.EXECUTION_MODES

    @pytest.mark.parametrize("value,expected", [
        (None, "PAPER"), ({}, "PAPER"),
        ({"execution_mode": "LIVE"}, "PAPER"),
        ({"execution_mode": "HACK"}, "PAPER"),
        ({"execution_mode": "SHADOW"}, "SHADOW"),
        ({"execution_mode": "MICRO_LIVE"}, "MICRO_LIVE")])
    def test_get_execution_mode_fail_closed(self, value,
                                            expected):
        assert app_module.get_execution_mode(value) == expected
