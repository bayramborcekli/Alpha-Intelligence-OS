"""Mission — Windows exchange credentials + tek hesap doğruluk kaynağı.

Senaryolar:
- POST /api/accounts/<id>/credentials: Replit'te 403 REPLIT_ENV;
  Windows'ta yerel depoya kaydeder, yanıt maskeli (sır sızmaz).
- Credential yokken hesap durumu NOT_CONFIGURED (yanıltıcı AUTH/TLS
  teşhisi yok).
- Genel Bakış + Portföy AYNI ham hesap yanıtını paylaşır (tek imzalı
  fetch — duplicate wallet fetch yasağı) ve aynı değeri raporlar.
- /api/accounts local_credentials_editable bayrağını taşır.
"""
import json

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import dashboard_api as dapi
import exchange_credentials as xc
import local_env
import portfolio_api as pf

PASSWORD = "hesap-kaynak-parola-1"
HASH = generate_password_hash(PASSWORD)

ACC = {"canTrade": True,
       "balances": [{"asset": "USDT", "free": "100", "locked": "0"}]}
TR_OK = {"code": 0, "data": {"status": 1, "accountAssets": [
    {"asset": "USDT", "free": "5", "locked": "0"},
    {"asset": "TRY", "free": "0", "locked": "0"}]}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_msot_attempts.db")
    auth._ATTEMPTS.clear()
    dapi.invalidate_caches()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True
        dapi.invalidate_caches()


def _login(c):
    return c.post("/api/v1/auth/login",
                  json={"username": "sahip", "password": PASSWORD})


def _clear_creds(monkeypatch):
    for k in ("BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key",
              "BINANCE_GLOBAL_API_KEY", "BINANCE_GLOBAL_API_SECRET",
              "BINANCE_API_KEY", "BINANCE_API_SECRET",
              "BINANCE_API_Key", "BINANCE_Secret_Key",
              "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET"):
        monkeypatch.delenv(k, raising=False)


def _windows(monkeypatch, tmp_path):
    monkeypatch.setattr(local_env, "is_replit", lambda: False)
    monkeypatch.setattr(xc, "ROOT", tmp_path)
    monkeypatch.setattr(xc, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(xc, "FILE", tmp_path / "data" /
                        "exchange_credentials.json")


class TestCredentialsEndpoint:
    def test_replit_rejects_with_replit_env(self, client, monkeypatch):
        monkeypatch.setattr(local_env, "is_replit", lambda: True)
        _login(client)
        r = client.post("/api/accounts/binance-global/credentials",
                        json={"apiKey": "k" * 20,
                              "apiSecret": "s" * 20})
        assert r.status_code == 403
        assert r.get_json()["error_code"] == "REPLIT_ENV"

    def test_windows_saves_and_masks(self, client, monkeypatch,
                                     tmp_path):
        _clear_creds(monkeypatch)
        _windows(monkeypatch, tmp_path)
        _login(client)
        r = client.post("/api/accounts/binance-global/credentials",
                        json={"apiKey": "WINKEY1234567890",
                              "apiSecret": "WINSEC1234567890"})
        assert r.status_code == 200
        d = r.get_json()["data"]
        assert d["source"] == "LOCAL_STORE"
        blob = json.dumps(r.get_json())
        assert "WINSEC1234567890" not in blob
        assert "WINKEY1234567890" not in blob  # yalnız maskeli
        assert d["api_key_masked"].startswith("WINK")
        assert xc.credentials("BINANCE_GLOBAL") == (
            "WINKEY1234567890", "WINSEC1234567890")
        # Restart olmadan etkin: kart artık configured görünür.
        cards = client.get("/api/accounts").get_json()["data"]
        acct = [a for a in cards["accounts"]
                if a["account_id"] == "binance-global"][0]
        assert acct["credentials_configured"] is True
        assert cards["local_credentials_editable"] is True

    def test_validation_error(self, client, monkeypatch, tmp_path):
        _windows(monkeypatch, tmp_path)
        _login(client)
        r = client.post("/api/accounts/binance-tr/credentials",
                        json={"apiKey": "", "apiSecret": ""})
        assert r.status_code == 400
        assert r.get_json()["error_code"] == "VALIDATION"

    def test_unsupported_exchange(self, client, monkeypatch, tmp_path):
        _windows(monkeypatch, tmp_path)
        _login(client)
        r = client.post("/api/accounts/paper/credentials",
                        json={"apiKey": "k" * 20,
                              "apiSecret": "s" * 20})
        assert r.status_code == 400

    def test_requires_auth(self, client, monkeypatch, tmp_path):
        _windows(monkeypatch, tmp_path)
        r = client.post("/api/accounts/binance-global/credentials",
                        json={"apiKey": "k" * 20,
                              "apiSecret": "s" * 20})
        assert r.status_code in (302, 401, 403)
        assert xc.credentials("BINANCE_GLOBAL") == ("", "")


class TestNotConfiguredState:
    def test_account_test_not_configured(self, client, monkeypatch):
        _clear_creds(monkeypatch)
        dapi.invalidate_caches()
        _login(client)
        r = client.post("/api/accounts/binance-global/test")
        d = r.get_json()["data"]
        assert d["overall"] == "NOT_CONFIGURED"
        assert d["connection_state"] == "NOT_CONFIGURED"
        assert d["checks"]["connected"] == "NOT_CONFIGURED"

    def test_tr_not_configured(self, client, monkeypatch):
        _clear_creds(monkeypatch)
        dapi.invalidate_caches()
        _login(client)
        d = client.post("/api/accounts/binance-tr/test").get_json()["data"]
        assert d["overall"] == "NOT_CONFIGURED"

    def test_dashboard_model_not_configured(self, monkeypatch):
        _clear_creds(monkeypatch)
        dapi.invalidate_caches()
        model = dapi.global_spot_account()
        assert model["ok"] is False
        assert model["error"]["code"] == "NOT_CONFIGURED"
        model = dapi.tr_account()
        assert model["error"]["code"] == "NOT_CONFIGURED"


class TestSingleFetchConsistency:
    def _mock(self, monkeypatch):
        calls = {"account": 0}

        def fake(base, path, allow, key, sec, params=None, timeout=10):
            if path == "/api/v3/account":
                calls["account"] += 1
                return ACC
            if path == "/open/v1/account/spot":
                return TR_OK
            raise dapi.SafeExchangeError("EXCHANGE_UNAVAILABLE", "mock")
        monkeypatch.setattr(dapi, "_signed_get", fake)
        monkeypatch.setattr(dapi, "_public_get", lambda *a, **k: [])
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "k" * 20)
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "s" * 20)
        dapi.invalidate_caches()
        return calls

    def test_overview_and_portfolio_share_one_fetch(self, monkeypatch):
        calls = self._mock(monkeypatch)
        acc = dapi.global_spot_account()
        assets = pf.global_assets()
        assert acc["ok"] is True and assets["ok"] is True
        assert calls["account"] == 1  # tek imzalı fetch, iki ekran
        # Aynı jenerasyon: iki ekran aynı toplamı görür.
        total_overview = acc["total_spot_value_usdt"]
        total_portfolio = sum(
            float(a["value_usdt"]) for a in assets["assets"]
            if a.get("value_usdt"))
        assert float(total_overview) == total_portfolio

    def test_invalidate_forces_fresh_fetch(self, monkeypatch):
        calls = self._mock(monkeypatch)
        dapi.global_spot_account()
        dapi.invalidate_caches()
        dapi.global_spot_account()
        assert calls["account"] == 2
