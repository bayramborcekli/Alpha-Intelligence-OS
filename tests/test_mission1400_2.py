"""Mission 1400.2 — salt-okunur canlı pano testleri (mock borsa)."""
import json
import time
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import dashboard_api as dapi

PASSWORD = "pano-test-parola-1"
HASH = generate_password_hash(PASSWORD)


@pytest.fixture
def client(monkeypatch):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14002_attempts.db")
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


def _mock_exchange(monkeypatch, account=None, positions=None, orders=None,
                   dual=None, tr=None, balance=None):
    """_signed_get'i yol bazında mock'lar; ağ isteği asla çıkmaz."""
    def fake(base, path, allowlist, key, secret, params=None, timeout=10):
        if ("GET", path) not in allowlist:
            raise RuntimeError("allowlist ihlali")
        table = {
            "/fapi/v2/account": account,
            "/fapi/v2/positionRisk": positions,
            "/fapi/v1/openOrders": orders,
            "/fapi/v1/positionSide/dualSide": dual,
            "/open/v1/account/spot": tr,
            "/fapi/v2/balance": balance,
        }
        val = table.get(path)
        if isinstance(val, dapi.SafeExchangeError):
            raise val
        if val is None:
            raise dapi.SafeExchangeError("EXCHANGE_UNAVAILABLE", "mock yok")
        return val
    monkeypatch.setattr(dapi, "_signed_get", fake)
    for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET",
              "BINANCE_TRADING_API_KEY", "BINANCE_TRADING_API_SECRET",
              "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET"):
        monkeypatch.setenv(k, "x" * 20)


ACC = {"canTrade": True, "totalUnrealizedProfit": "1.25",
       "assets": [{"asset": "USDT", "walletBalance": "64442.20",
                   "availableBalance": "64000.5", "marginBalance": "64442.9"},
                  {"asset": "BNB", "walletBalance": "0"}],
       "positions": [{"positionAmt": "0.5"}, {"positionAmt": "0"}]}
POS = [{"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "50000",
        "markPrice": "51000", "unRealizedProfit": "500", "leverage": "5",
        "liquidationPrice": "40000", "marginType": "cross",
        "isolatedWallet": "0", "updateTime": 1},
       {"symbol": "ETHUSDT", "positionAmt": "-2", "entryPrice": "3000",
        "markPrice": "2900", "unRealizedProfit": "200", "leverage": "3",
        "liquidationPrice": "4000", "marginType": "isolated",
        "isolatedWallet": "100", "updateTime": 2},
       {"symbol": "XRPUSDT", "positionAmt": "0", "entryPrice": "0",
        "markPrice": "0.5", "unRealizedProfit": "0", "leverage": "10",
        "liquidationPrice": "0", "marginType": "cross",
        "isolatedWallet": "0", "updateTime": 3}]
ORDERS = [{"symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT",
           "status": "NEW", "origQty": "0.5", "executedQty": "0",
           "price": "60000", "stopPrice": "0", "reduceOnly": True,
           "time": 1, "updateTime": 2}]
DUAL = {"dualSidePosition": False}
TR_OK = {"code": 0, "data": {"status": 1, "accountAssets": [
    {"asset": "TRY", "free": "1.3942", "locked": "0"},
    {"asset": "USDT", "free": "20.9685", "locked": "0"},
    {"asset": "SHIB", "free": "0", "locked": "0"}]}}
BAL = [{"asset": "USDT", "balance": "64442.20", "availableBalance": "64000.5"}]


# ── Kimlik doğrulama zorunluluğu ─────────────────────────────────────────────

ROUTES = ["/api/v1/overview", "/api/v1/global/account",
          "/api/v1/global/positions", "/api/v1/global/orders",
          "/api/v1/tr/account", "/api/v1/tr/movements/summary",
          "/api/v1/system/status"]


class TestAuthRequired:
    def test_all_dashboard_routes_require_auth(self, client):
        for r in ROUTES:
            resp = client.get(r)
            assert resp.status_code == 401, r
            assert resp.get_json()["request_id"]

    def test_refresh_requires_auth(self, client):
        assert client.post("/api/v1/refresh").status_code == 401

    def test_refresh_requires_csrf(self, client):
        _login(client)
        flask_app.app.config["WTF_CSRF_ENABLED"] = True
        try:
            r = client.post("/api/v1/refresh")
            assert r.status_code == 400  # /api/* CSRF hatası JSON 400
            assert "request_id" in r.get_json()
        finally:
            flask_app.app.config["WTF_CSRF_ENABLED"] = False

    def test_overview_page_requires_auth(self, client):
        r = client.get("/overview")
        assert r.status_code == 302 and "/login" in r.headers["Location"]

    def test_panel_still_requires_auth(self, client):
        r = client.get("/panel")
        assert r.status_code == 302 and "/login" in r.headers["Location"]


# ── Tipli modeller ve eşlemeler ─────────────────────────────────────────────

class TestGlobalMocks:
    def test_account_mapping_and_cantrade_not_live(self, client, monkeypatch):
        _mock_exchange(monkeypatch, account=ACC, positions=POS,
                       orders=ORDERS, dual=DUAL, tr=TR_OK, balance=BAL)
        _login(client)
        d = client.get("/api/v1/global/account").get_json()
        assert d["ok"] is True
        assert d["read_only_auth"] == "OK"
        assert d["trading_key_auth"] == "OK"
        assert d["exchange_can_trade"] is True
        assert d["app_live_execution"] is False  # canTrade ≠ canlı emir
        assert d["position_mode"] == "ONE_WAY"
        assert Decimal(d["usdt_wallet_balance"]) == Decimal("64442.20")
        assert d["open_position_count"] == 1
        assert d["open_order_count"] == 1
        assert d["api_key_masked"].count("…") == 1
        assert "x" * 20 not in json.dumps(d)
        meta = d["meta"]
        assert meta["source"] == "BINANCE_GLOBAL_FUTURES"
        assert meta["freshness"] == "FRESH"
        assert meta["retrieved_at"] and meta["age_seconds"] is not None

    def test_position_direction_mapping_oneway(self, client, monkeypatch):
        _mock_exchange(monkeypatch, positions=POS)
        _login(client)
        d = client.get("/api/v1/global/positions").get_json()
        assert d["ok"] is True
        dirs = {p["symbol"]: p["direction"] for p in d["positions"]}
        assert dirs == {"BTCUSDT": "LONG", "ETHUSDT": "SHORT"}
        assert d["open_position_count"] == 2
        # FLAT yalnızca include_zero=true ile
        d2 = client.get(
            "/api/v1/global/positions?include_zero=true").get_json()
        # önbellek: aynı model, filtre farklı
        assert any(p["direction"] == "FLAT" for p in d2["positions"])

    def test_orders_mapping(self, client, monkeypatch):
        _mock_exchange(monkeypatch, orders=ORDERS)
        _login(client)
        d = client.get("/api/v1/global/orders").get_json()
        assert d["ok"] and d["open_order_count"] == 1
        o = d["orders"][0]
        assert o["side"] == "SELL" and o["reduce_only"] is True
        assert Decimal(o["price"]) == Decimal("60000")

    def test_no_positions_empty(self, client, monkeypatch):
        _mock_exchange(monkeypatch, positions=[])
        _login(client)
        d = client.get("/api/v1/global/positions").get_json()
        assert d["ok"] and d["positions"] == []
        assert d["open_position_count"] == 0

    def test_unavailable_exchange_safe_error(self, client, monkeypatch):
        _mock_exchange(monkeypatch)  # her yol EXCHANGE_UNAVAILABLE
        _login(client)
        d = client.get("/api/v1/global/positions").get_json()
        assert d["ok"] is False
        assert d["error"]["code"] == "EXCHANGE_UNAVAILABLE"
        assert d["meta"]["freshness"] == "UNAVAILABLE"


class TestTrMocks:
    def test_tr_balance_mapping(self, client, monkeypatch):
        _mock_exchange(monkeypatch, tr=TR_OK)
        _login(client)
        d = client.get("/api/v1/tr/account").get_json()
        assert d["ok"] is True
        assert Decimal(d["try_free"]) == Decimal("1.3942")
        assert Decimal(d["usdt_free"]) == Decimal("20.9685")
        assert d["asset_count"] == 3 and d["nonzero_asset_count"] == 2

    def test_tr_missing_assets_zero(self, client, monkeypatch):
        _mock_exchange(monkeypatch,
                       tr={"code": 0, "data": {"accountAssets": []}})
        _login(client)
        d = client.get("/api/v1/tr/account").get_json()
        assert d["try_free"] == "0" and d["usdt_free"] == "0"

    def test_tr_data_as_list(self, client, monkeypatch):
        _mock_exchange(monkeypatch, tr={"code": 0, "data": [
            {"asset": "TRY", "free": "5", "locked": "0"}]})
        _login(client)
        d = client.get("/api/v1/tr/account").get_json()
        assert d["ok"] and Decimal(d["try_free"]) == Decimal("5")

    def test_movement_summary_from_ledger(self, client):
        _login(client)
        d = client.get("/api/v1/tr/movements/summary").get_json()
        if d["ok"]:  # ledger dosyası repo'da mevcut
            assert d["ledger_event_count"] >= 1
            assert d["reconciliation"] in ("PARTIAL", "FULL", "OK")
            assert "coverage_warning" in d
        else:
            assert d["error"]["code"] == "EXCHANGE_UNAVAILABLE"


# ── Genel bakış ve kısmi hata ───────────────────────────────────────────────

class TestOverview:
    def test_overview_aggregates(self, client, monkeypatch):
        _mock_exchange(monkeypatch, account=ACC, positions=POS,
                       orders=ORDERS, dual=DUAL, tr=TR_OK, balance=BAL)
        _login(client)
        d = client.get("/api/v1/overview").get_json()
        assert d["application"]["live_trading_enabled"] is False
        assert d["application"]["transfers_enabled"] is False
        assert d["application"]["withdrawals_enabled"] is False
        assert d["global_futures"]["ok"] is True
        assert d["tr"]["ok"] is True
        assert isinstance(d["warnings"], list)

    def test_partial_source_failure(self, client, monkeypatch):
        # TR çalışıyor, Global kullanılamıyor → pano tamamen kararmaz
        _mock_exchange(monkeypatch, tr=TR_OK)
        _login(client)
        d = client.get("/api/v1/overview").get_json()
        assert d["tr"]["ok"] is True
        assert d["global_futures"]["ok"] is False
        assert any("Binance Global" in w for w in d["warnings"])

    def test_no_combined_portfolio_total(self, client, monkeypatch):
        _mock_exchange(monkeypatch, account=ACC, positions=POS,
                       orders=ORDERS, dual=DUAL, tr=TR_OK, balance=BAL)
        _login(client)
        blob = client.get("/api/v1/overview").get_data(as_text=True)
        assert "total_portfolio" not in blob and "combined_total" not in blob


# ── Önbellek, tazelik, yenileme ─────────────────────────────────────────────

class TestCacheAndFreshness:
    def test_cache_hit_within_ttl(self, client, monkeypatch):
        calls = {"n": 0}
        real = dict(tr=TR_OK)

        def fake(base, path, allowlist, key, secret, params=None, timeout=10):
            calls["n"] += 1
            return real["tr"]
        monkeypatch.setattr(dapi, "_signed_get", fake)
        for k in ("BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET"):
            monkeypatch.setenv(k, "x" * 20)
        _login(client)
        client.get("/api/v1/tr/account")
        client.get("/api/v1/tr/account")
        assert calls["n"] == 1  # ikinci istek önbellekten

    def test_manual_refresh_invalidates_and_csrf_audits(self, client,
                                                        monkeypatch):
        _mock_exchange(monkeypatch, account=ACC, positions=POS,
                       orders=ORDERS, dual=DUAL, tr=TR_OK, balance=BAL)
        _login(client)
        client.get("/api/v1/tr/account")
        r = client.post("/api/v1/refresh")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True and d["refreshed_at"]
        assert d["overview"]["last_full_refresh"] == d["refreshed_at"]

    def test_stale_classification(self, monkeypatch):
        # yaş > limit → STALE (önbellek girdisinin yaşını yapay eskit)
        dapi.invalidate_caches()
        with dapi._cache_lock:
            dapi._cache["tr_account"] = {
                "mono": time.monotonic() - 120, "retrieved_at": "2026-01-01",
                "latency_ms": 5, "ok": True,
                "data": {"try_free": "1"}, "error": None}
        # TTL de geçmiş ama builder hata verirse son bilinen veri sunulur
        def boom():
            raise dapi.SafeExchangeError("EXCHANGE_TIMEOUT", "t/o")
        out = dapi._serve("tr_account", "BINANCE_TR", boom)
        assert out["meta"]["freshness"] == "STALE"
        assert out["try_free"] == "1"          # son bilinen veri korunur
        assert out["error"]["code"] == "EXCHANGE_TIMEOUT"
        dapi.invalidate_caches()

    def test_retry_bounded(self, monkeypatch):
        attempts = {"n": 0}

        class FakeResp:
            status_code = 503
        def fake_get(*a, **k):
            attempts["n"] += 1
            return FakeResp()
        monkeypatch.setattr(dapi.requests, "get", fake_get)
        monkeypatch.setattr(dapi.time, "sleep", lambda s: None)
        with pytest.raises(dapi.SafeExchangeError) as e:
            dapi._signed_get(dapi.GLOBAL_BASE, "/fapi/v2/balance",
                             dapi.GLOBAL_ALLOWLIST, "k", "s")
        assert e.value.code == "EXCHANGE_UNAVAILABLE"
        assert attempts["n"] == dapi.MAX_RETRIES + 1  # sınır aşılmaz

    def test_rate_limit_no_retry(self, monkeypatch):
        attempts = {"n": 0}

        class FakeResp:
            status_code = 429
        monkeypatch.setattr(dapi.requests, "get",
                            lambda *a, **k: (attempts.__setitem__(
                                "n", attempts["n"] + 1), FakeResp())[1])
        with pytest.raises(dapi.SafeExchangeError) as e:
            dapi._signed_get(dapi.GLOBAL_BASE, "/fapi/v2/balance",
                             dapi.GLOBAL_ALLOWLIST, "k", "s")
        assert e.value.code == "EXCHANGE_RATE_LIMITED"
        assert attempts["n"] == 1  # oran sınırında tekrar deneme YOK

    def test_non_json_response(self, monkeypatch):
        class FakeResp:
            status_code = 200
            def json(self):
                raise ValueError("html geldi")
        monkeypatch.setattr(dapi.requests, "get", lambda *a, **k: FakeResp())
        monkeypatch.setattr(dapi.time, "sleep", lambda s: None)
        with pytest.raises(dapi.SafeExchangeError) as e:
            dapi._signed_get(dapi.GLOBAL_BASE, "/fapi/v2/balance",
                             dapi.GLOBAL_ALLOWLIST, "k", "s")
        assert e.value.code == "INVALID_EXCHANGE_RESPONSE"


# ── Yazma güvenliği ─────────────────────────────────────────────────────────

class TestWriteSafety:
    def test_write_counters_zero(self, client, monkeypatch):
        _mock_exchange(monkeypatch, account=ACC, positions=POS,
                       orders=ORDERS, dual=DUAL, tr=TR_OK, balance=BAL)
        _login(client)
        client.get("/api/v1/overview")
        client.post("/api/v1/refresh")
        d = client.get("/api/v1/system/status").get_json()
        assert all(v == 0 for v in d["write_counters"].values())

    def test_allowlist_blocks_non_get(self):
        with pytest.raises(RuntimeError, match="GÜVENLİK BLOĞU"):
            dapi._signed_get(dapi.GLOBAL_BASE, "/fapi/v1/order",
                             dapi.GLOBAL_ALLOWLIST, "k", "s")

    def test_allowlist_only_get_methods(self):
        for method, _ in dapi.GLOBAL_ALLOWLIST | dapi.TR_ALLOWLIST:
            assert method == "GET"

    READ_ONLY_ALLOWED = {"/api/v1/global/orders",
                         "/api/v1/global/orders/export.csv",
                         "/orders",
                         # Mission 2200: operasyon merkezi salt-okunur
                         # emir görünümü (GET, borsa yazması yok).
                         "/api/operation-control/orders",
                         # Task 29: tek emrin salt-okunur yaşam
                         # döngüsü zinciri (GET, borsa yazması yok).
                         "/api/operation-control/workspace/orders/"
                         "<order_id>/lifecycle"}

    def test_route_map_no_write_routes(self):
        """Hassas kelime içeren rotalar yalnızca açık izinli + GET olabilir."""
        for rule in flask_app.app.url_map.iter_rules():
            p = str(rule).lower()
            if not any(w in p for w in
                       ("order", "transfer", "withdraw", "leverage",
                        "margin", "positionside")):
                continue
            assert p in self.READ_ONLY_ALLOWED, f"yasak rota: {rule}"
            methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
            assert methods == {"GET"}, f"yazma metodu yasak: {rule}"


# ── Frontend ────────────────────────────────────────────────────────────────

class TestMalformedPayloads:
    """Bozuk-ama-JSON borsa yanıtı 500'e dönüşmemeli (kaynak izolasyonu)."""

    BAD_ACC = {"canTrade": True, "totalUnrealizedProfit": "??",
               "assets": [{"asset": "USDT", "walletBalance": "not-a-number",
                           "availableBalance": None, "marginBalance": ""}],
               "positions": [{"positionAmt": "garbage"}]}
    BAD_POS = [{"symbol": "BTCUSDT", "positionAmt": "NaN?", "entryPrice": "x",
                "markPrice": None, "unRealizedProfit": "", "leverage": "y",
                "liquidationPrice": "z", "marginType": "cross",
                "isolatedWallet": "w", "updateTime": 1}]
    BAD_TR = {"code": 0, "data": {"accountAssets": [
        {"asset": "TRY", "free": "bozuk", "locked": None}]}}

    def test_malformed_global_account_no_500(self, client, monkeypatch):
        _mock_exchange(monkeypatch, account=self.BAD_ACC, positions=POS,
                       orders=ORDERS, dual=DUAL, tr=TR_OK, balance=BAL)
        _login(client)
        r = client.get("/api/v1/global/account")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True  # güvenli ayrıştırıcı: bozuk alan → "0"
        assert d["usdt_wallet_balance"] == "0"

    def test_malformed_positions_no_500(self, client, monkeypatch):
        _mock_exchange(monkeypatch, positions=self.BAD_POS)
        _login(client)
        r = client.get("/api/v1/global/positions")
        assert r.status_code == 200 and r.get_json()["ok"] is True

    def test_overview_isolates_unexpected_error(self, client, monkeypatch):
        # Global tarafı beklenmedik hata fırlatsa bile TR kartı çalışır
        def boom(base, path, allowlist, key, secret, params=None, timeout=10):
            if base == dapi.TR_BASE:
                return TR_OK
            raise KeyError("beklenmedik iç hata")
        monkeypatch.setattr(dapi, "_signed_get", boom)
        for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET",
                  "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET"):
            monkeypatch.setenv(k, "x" * 20)
        _login(client)
        r = client.get("/api/v1/overview")
        assert r.status_code == 200
        d = r.get_json()
        assert d["tr"]["ok"] is True
        assert d["global_futures"]["ok"] is False
        assert d["global_futures"]["error"]["code"] == \
            "INVALID_EXCHANGE_RESPONSE"
        assert "beklenmedik" not in json.dumps(d)  # iç hata sızmaz

    def test_malformed_tr_no_500(self, client, monkeypatch):
        _mock_exchange(monkeypatch, tr=self.BAD_TR)
        _login(client)
        r = client.get("/api/v1/tr/account")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True and d["try_free"] == "0"


class TestFrontend:
    def test_overview_renders_after_login(self, client):
        _login(client)
        r = client.get("/overview")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        for label in ("Genel Bakış", "Tümünü Yenile", "Binance Futures",
                      "Binance TR", "Sistem Sağlığı",
                      "Canlı emir: DEVRE DIŞI", "GÜNCEL", "ESKİ VERİ",
                      "KULLANILAMIYOR"):
            assert label in body, label
        # erişilebilirlik ve mobil
        assert "aria-live" in body and 'id="menu-btn"' in body
        assert "csrf-token" in body

    def test_no_secrets_in_rendered_overview(self, client, monkeypatch):
        import os
        _login(client)
        body = client.get("/overview").get_data(as_text=True)
        for name in ("BINANCE_API_SECRET", "BINANCE_TR_API_SECRET",
                     "BINANCE_TRADING_API_SECRET", "SESSION_SECRET"):
            v = os.environ.get(name)
            if v and len(v) > 8:
                assert v not in body

    def test_panel_no_longer_fetches_exchange(self, client):
        _login(client)
        body = client.get("/panel").get_data(as_text=True)
        assert "/api/exchange/summary" not in body
        assert "/overview" in body  # birleştirme bağlantısı

    def test_shell_nav_links_overview(self, client):
        _login(client)
        body = client.get("/").get_data(as_text=True)
        assert 'href="/overview"' in body
