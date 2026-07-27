"""HOTFIX 2100-HF-001 — Binance Global panosu SPOT hesabı testleri.

Global pano Spot hesabını gösterir (/api/v3/account); Futures paneli
DEĞİŞMEDEN kalır. Ağ isteği asla çıkmaz (mock)."""
import pytest
from decimal import Decimal
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import dashboard_api as dapi

PASSWORD = "hotfix-test-parola-1"
HASH = generate_password_hash(PASSWORD)


@pytest.fixture
def client(monkeypatch):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_hf001_attempts.db")
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


SPOT_ACC = {"canTrade": True, "balances": [
    {"asset": "USDT", "free": "1500.50", "locked": "10.25"},
    {"asset": "BTC", "free": "0.5", "locked": "0"},
    {"asset": "ETH", "free": "2", "locked": "1"},
    {"asset": "XRP", "free": "0", "locked": "0"},
]}
TICKER = [{"symbol": "BTCUSDT", "price": "50000"},
          {"symbol": "ETHUSDT", "price": "3000"}]


def _mock_spot(monkeypatch, account=SPOT_ACC, ticker=TICKER,
               track=None):
    dapi.invalidate_caches()
    calls = track if track is not None else []

    def fake_signed(base, path, allowlist, key, secret,
                    params=None, timeout=10):
        if ("GET", path) not in allowlist:
            raise RuntimeError("allowlist ihlali")
        calls.append(path)
        if path == "/api/v3/account":
            if isinstance(account, dapi.SafeExchangeError):
                raise account
            return account
        raise dapi.SafeExchangeError("EXCHANGE_UNAVAILABLE", "mock yok")

    def fake_public(base, path, allowlist, params=None, timeout=10):
        if ("GET", path) not in allowlist:
            raise RuntimeError("allowlist ihlali")
        calls.append(path)
        if isinstance(ticker, dapi.SafeExchangeError):
            raise ticker
        return ticker

    monkeypatch.setattr(dapi, "_signed_get", fake_signed)
    monkeypatch.setattr(dapi, "_public_get", fake_public)
    for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET"):
        monkeypatch.setenv(k, "x" * 20)
    return calls


class TestSpotEndpoint:
    def test_api_v3_account_called(self, monkeypatch):
        calls = _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        assert model["ok"] is True
        assert "/api/v3/account" in calls

    def test_futures_account_not_called_by_global_panel(self,
                                                        monkeypatch):
        calls = _mock_spot(monkeypatch)
        dapi.global_spot_account()
        assert "/fapi/v2/account" not in calls
        assert all(p.startswith("/api/v3/") for p in calls)

    def test_source_label_binance_global_spot(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        assert model["meta"]["source"] == "BINANCE_GLOBAL_SPOT"

    def test_spot_endpoint_in_allowlist_get_only(self):
        assert ("GET", "/api/v3/account") in dapi.SPOT_ALLOWLIST
        assert all(m == "GET" for m, _ in dapi.SPOT_ALLOWLIST)

    def test_latency_reported(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        assert model["meta"]["latency_ms"] is not None


class TestBalanceParsing:
    def test_usdt_detected(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        assert model["usdt_free"] == "1500.50"
        assert model["usdt_locked"] == "10.25"

    def test_btc_detected(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        assets = {h["asset"]: h for h in model["top_holdings"]}
        assert assets["BTC"]["amount"] == "0.5"
        assert Decimal(assets["BTC"]["value_usdt"]) == Decimal("25000")

    def test_eth_detected(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        assets = {h["asset"]: h for h in model["top_holdings"]}
        assert assets["ETH"]["amount"] == "3"
        assert Decimal(assets["ETH"]["value_usdt"]) == Decimal("9000")

    def test_multiple_assets_counted(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        assert model["asset_count"] == 3          # sıfırlar sayılmaz
        assert model["total_asset_count"] == 4
        assert model["has_spot_assets"] is True

    def test_total_value_decimal_exact(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        assert Decimal(model["total_spot_value_usdt"]) == \
            Decimal("1500.50") + Decimal("10.25") + \
            Decimal("25000") + Decimal("9000")
        assert model["valuation"] == "FULL"

    def test_top_holdings_sorted_desc(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        values = [Decimal(h["value_usdt"] or 0)
                  for h in model["top_holdings"]]
        assert values == sorted(values, reverse=True)

    def test_unpriced_asset_marks_partial(self, monkeypatch):
        acc = {"canTrade": True, "balances": [
            {"asset": "USDT", "free": "10", "locked": "0"},
            {"asset": "OBSCURE", "free": "5", "locked": "0"}]}
        _mock_spot(monkeypatch, account=acc)
        model = dapi.global_spot_account()
        assert model["valuation"] == "PARTIAL"
        assert Decimal(model["total_spot_value_usdt"]) == Decimal("10")

    def test_malformed_price_marks_partial_not_zero(self,
                                                    monkeypatch):
        bad_ticker = [{"symbol": "BTCUSDT", "price": "bozuk"},
                      {"symbol": "ETHUSDT", "price": "3000"}]
        _mock_spot(monkeypatch, ticker=bad_ticker)
        model = dapi.global_spot_account()
        assert model["valuation"] == "PARTIAL"
        assets = {h["asset"]: h for h in model["top_holdings"]}
        # BTC sessizce 0 USDT sayılmaz — fiyatlanamadı olarak sunulur.
        assert assets["BTC"]["value_usdt"] is None
        assert Decimal(model["total_spot_value_usdt"]) == \
            Decimal("1500.50") + Decimal("10.25") + Decimal("9000")

    def test_zero_price_marks_partial(self, monkeypatch):
        zero_ticker = [{"symbol": "BTCUSDT", "price": "0"},
                       {"symbol": "ETHUSDT", "price": "3000"}]
        _mock_spot(monkeypatch, ticker=zero_ticker)
        model = dapi.global_spot_account()
        assert model["valuation"] == "PARTIAL"

    def test_ticker_failure_marks_partial(self, monkeypatch):
        _mock_spot(monkeypatch, ticker=dapi.SafeExchangeError(
            "EXCHANGE_UNAVAILABLE", "mock"))
        model = dapi.global_spot_account()
        assert model["ok"] is True
        assert model["valuation"] == "PARTIAL"


class TestEmptyAndZero:
    def test_zero_assets_no_spot_assets(self, monkeypatch):
        _mock_spot(monkeypatch, account={"canTrade": False,
                                         "balances": []})
        model = dapi.global_spot_account()
        assert model["ok"] is True
        assert model["has_spot_assets"] is False
        assert model["asset_count"] == 0
        assert model["usdt_free"] is None

    def test_all_zero_balances_no_spot_assets(self, monkeypatch):
        acc = {"canTrade": True, "balances": [
            {"asset": "USDT", "free": "0", "locked": "0"}]}
        _mock_spot(monkeypatch, account=acc)
        model = dapi.global_spot_account()
        assert model["has_spot_assets"] is False
        # API sıfırı AÇIKÇA döndürdü — USDT alanları sıfır olarak sunulur.
        assert model["usdt_free"] == "0"
        assert model["usdt_locked"] == "0"

    def test_malformed_balances_sterile_error(self, monkeypatch):
        _mock_spot(monkeypatch, account={"balances": "bozuk"})
        model = dapi.global_spot_account()
        assert model["ok"] is False
        assert model["error"]["code"] == "INVALID_EXCHANGE_RESPONSE"


class TestFailures:
    def test_api_failure_sterile(self, monkeypatch):
        _mock_spot(monkeypatch, account=dapi.SafeExchangeError(
            "EXCHANGE_UNAVAILABLE", "mock"))
        model = dapi.global_spot_account()
        assert model["ok"] is False
        assert model["error"]["code"] == "EXCHANGE_UNAVAILABLE"

    def test_permission_failure_sterile(self, monkeypatch):
        _mock_spot(monkeypatch, account=dapi.SafeExchangeError(
            "EXCHANGE_AUTH_FAILED",
            dapi.ERROR_MESSAGES["EXCHANGE_AUTH_FAILED"]))
        model = dapi.global_spot_account()
        assert model["ok"] is False
        assert model["error"]["code"] == "EXCHANGE_AUTH_FAILED"

    def test_missing_keys_fail_closed(self, monkeypatch):
        monkeypatch.delenv("BINANCE_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
        dapi.invalidate_caches(["global_spot"])
        model = dapi.global_spot_account()
        assert model["ok"] is False
        assert model["error"]["code"] == "EXCHANGE_AUTH_FAILED"

    def test_no_secret_in_model(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        text = str(model)
        assert "x" * 20 not in text
        assert "signature" not in text.lower()


class TestUiBinding:
    def test_overview_contains_global_spot(self, client, monkeypatch):
        _mock_spot(monkeypatch)
        _login(client)
        r = client.get("/api/v1/overview")
        assert r.status_code == 200
        d = r.get_json()
        assert d["global_spot"]["meta"]["source"] == \
            "BINANCE_GLOBAL_SPOT"

    def test_template_binds_spot_not_futures_for_global_card(self):
        html = open("templates/overview.html", encoding="utf-8").read()
        assert "d.global_spot" in html
        assert "Spot Varlık Yok" in html
        idx_card = html.index("Kart 1: Binance Global")
        idx_end = html.index("Kart 2:")
        card1 = html[idx_card:idx_end]
        assert "gs." in card1
        assert "usdt_wallet_balance" not in card1

    def test_futures_card_unchanged_fields(self):
        html = open("templates/overview.html", encoding="utf-8").read()
        card2 = html[html.index("Kart 2:"):html.index("Kart 3:")]
        for token in ("usdt_wallet_balance", "usdt_available_balance",
                      "unrealized_pnl", "open_position_count",
                      "open_order_count", "position_mode"):
            assert token in card2


class TestFuturesUnchanged:
    def test_futures_model_source_unchanged(self):
        import inspect
        src = inspect.getsource(dapi.global_account)
        assert "/fapi/v2/account" in src
        assert "BINANCE_GLOBAL_FUTURES" in src
        assert "/api/v3/account" not in src

    def test_global_allowlist_unchanged(self):
        assert dapi.GLOBAL_ALLOWLIST == {
            ("GET", "/fapi/v2/account"),
            ("GET", "/fapi/v2/balance"),
            ("GET", "/fapi/v2/positionRisk"),
            ("GET", "/fapi/v1/openOrders"),
            ("GET", "/fapi/v1/positionSide/dualSide")}

    def test_overview_keeps_global_futures_key(self, client,
                                               monkeypatch):
        _mock_spot(monkeypatch)
        _login(client)
        d = client.get("/api/v1/overview").get_json()
        assert "global_futures" in d
        assert "global_spot" in d


class TestArchitectureUnchanged:
    def test_write_counters_still_zero(self):
        assert all(v == 0 for v in dapi.WRITE_COUNTERS.values())

    def test_no_write_paths_in_module(self):
        import inspect
        src = inspect.getsource(dapi)
        for token in ("requests.post", "requests.put",
                      "requests.delete", "/api/v3/order",
                      "/sapi/v1/capital/withdraw",
                      "/sapi/v1/asset/transfer"):
            assert token not in src

    def test_spot_allowlist_read_only(self):
        for method, path in dapi.SPOT_ALLOWLIST:
            assert method == "GET"
            assert "order" not in path
            assert "withdraw" not in path
            assert "transfer" not in path

    def test_public_get_sends_no_key(self):
        import inspect
        src = inspect.getsource(dapi._public_get)
        assert "X-MBX-APIKEY" not in src
        assert "signature" not in src
