"""Mission 1400.3 — Portföy / Pozisyonlar / Emirler testleri (mock borsa)."""
import json
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import dashboard_api as dapi
import portfolio_api as pf

PASSWORD = "portfoy-test-parola-1"
HASH = generate_password_hash(PASSWORD)


@pytest.fixture
def client(monkeypatch):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14003_attempts.db")
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


ACC = {"canTrade": True, "totalUnrealizedProfit": "1.25", "assets": [
    {"asset": "USDT", "walletBalance": "64442.20",
     "availableBalance": "64000.5", "marginBalance": "64442.9",
     "unrealizedProfit": "0.7", "initialMargin": "10", "maintMargin": "5",
     "openOrderInitialMargin": "2", "positionInitialMargin": "8",
     "updateTime": 111},
    {"asset": "BNB", "walletBalance": "0", "availableBalance": "0",
     "marginBalance": "0", "unrealizedProfit": "0", "updateTime": 0},
    {"asset": "=EVIL", "walletBalance": "1", "availableBalance": "1",
     "marginBalance": "1", "unrealizedProfit": "0", "updateTime": 0}],
    "positions": []}
POS = [{"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "50000",
        "markPrice": "51000", "unRealizedProfit": "500.5", "leverage": "5",
        "liquidationPrice": "40000", "marginType": "cross",
        "isolatedWallet": "0", "updateTime": 1},
       {"symbol": "ETHUSDT", "positionAmt": "-2", "entryPrice": "3000",
        "markPrice": "2900", "unRealizedProfit": "-100.2", "leverage": "3",
        "liquidationPrice": "4000", "marginType": "isolated",
        "isolatedWallet": "100", "updateTime": 2},
       {"symbol": "XRPUSDT", "positionAmt": "0", "entryPrice": "0",
        "markPrice": "0.5", "unRealizedProfit": "0", "leverage": "10",
        "liquidationPrice": "0", "marginType": "cross",
        "isolatedWallet": "0", "updateTime": 3}]
ORDERS = [{"symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT",
           "status": "PARTIALLY_FILLED", "origQty": "0.5",
           "executedQty": "0.2", "price": "60000", "stopPrice": "0",
           "reduceOnly": True, "time": 10, "updateTime": 20},
          {"symbol": "ETHUSDT", "side": "BUY", "type": "STOP_MARKET",
           "status": "NEW", "origQty": "1", "executedQty": "0",
           "price": "0", "stopPrice": "2500", "reduceOnly": False,
           "time": 5, "updateTime": 6}]
TR_OK = {"code": 0, "data": {"status": 1, "accountAssets": [
    {"asset": "TRY", "free": "1.3942", "locked": "0.5", "updateTime": 9},
    {"asset": "USDT", "free": "20.9685", "locked": "0"},
    {"asset": "+HACK", "free": "1", "locked": "0"},
    {"asset": "SHIB", "free": "0", "locked": "0"}]}}


def _mock(monkeypatch, account=ACC, positions=POS, orders=ORDERS, tr=TR_OK):
    def fake(base, path, allowlist, key, secret, params=None, timeout=10):
        if ("GET", path) not in allowlist:
            raise RuntimeError("allowlist ihlali")
        table = {"/fapi/v2/account": account,
                 "/fapi/v2/positionRisk": positions,
                 "/fapi/v1/openOrders": orders,
                 "/fapi/v1/positionSide/dualSide": {"dualSidePosition": False},
                 "/open/v1/account/spot": tr,
                 "/fapi/v2/balance": []}
        val = table.get(path)
        if val is None:
            raise dapi.SafeExchangeError("EXCHANGE_UNAVAILABLE", "mock yok")
        return val
    monkeypatch.setattr(dapi, "_signed_get", fake)
    monkeypatch.setattr(pf, "_signed_get", fake)
    for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET",
              "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET"):
        monkeypatch.setenv(k, "x" * 20)


PAGES = ["/portfolio", "/positions", "/orders"]
APIS = ["/api/v1/portfolio", "/api/v1/portfolio/export.csv",
        "/api/v1/global/positions/export.csv",
        "/api/v1/global/orders/export.csv"]


class TestAuth:
    def test_pages_require_auth(self, client):
        for p in PAGES:
            r = client.get(p)
            assert r.status_code == 302 and "/login" in r.headers["Location"], p

    def test_apis_require_auth(self, client):
        for a in APIS:
            assert client.get(a).status_code == 401, a


class TestPortfolioBackend:
    def test_sections_and_mapping(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        d = client.get("/api/v1/portfolio?include_zero=true").get_json()
        assert d["live_execution_enabled"] is False
        srcs = [s["source"] for s in d["sections"]]
        assert srcs == ["BINANCE_GLOBAL_FUTURES", "BINANCE_TR"]
        gf = d["sections"][0]
        usdt = next(a for a in gf["assets"] if a["asset"] == "USDT")
        assert Decimal(usdt["wallet_balance"]) == Decimal("64442.20")
        assert usdt["margin_balance"] == "64442.9"
        assert usdt["initial_margin"] == "10"
        tr = d["sections"][1]
        try_row = next(a for a in tr["assets"] if a["asset"] == "TRY")
        assert Decimal(try_row["total"]) == Decimal("1.8942")  # Decimal toplam
        assert gf["meta"]["freshness"] == "FRESH"

    def test_zero_filter_default(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        d = client.get("/api/v1/portfolio").get_json()
        gf_assets = [a["asset"] for a in d["sections"][0]["assets"]]
        assert "BNB" not in gf_assets  # sıfır bakiye varsayılan gizli
        tr_assets = [a["asset"] for a in d["sections"][1]["assets"]]
        assert "SHIB" not in tr_assets

    def test_search_and_sort(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        d = client.get("/api/v1/portfolio?search=usdt").get_json()
        for s in d["sections"]:
            assert all("USDT" in a["asset"] for a in s["assets"])
        d2 = client.get(
            "/api/v1/portfolio?sort=asset&order=desc&include_zero=true"
        ).get_json()
        names = [a["asset"] for a in d2["sections"][0]["assets"]]
        assert names == sorted(names, reverse=True)

    def test_invalid_params_rejected(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        for bad in ("?sort=DROP TABLE", "?order=up", "?include_zero=maybe",
                    "?limit=99999", "?limit=abc", "?search=" + "A" * 50,
                    "?search=<script>"):
            r = client.get("/api/v1/portfolio" + bad)
            assert r.status_code == 400, bad
            assert r.get_json()["error"]["code"] == "INVALID_PARAMETER"

    def test_no_combined_total(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        blob = client.get(
            "/api/v1/portfolio?include_zero=true").get_data(as_text=True)
        for banned in ("combined_total", "net_worth", "total_value",
                       "grand_total"):
            assert banned not in blob

    def test_partial_source_failure(self, client, monkeypatch):
        _mock(monkeypatch, account=None)  # Global çöker, TR çalışır
        _login(client)
        d = client.get("/api/v1/portfolio").get_json()
        gf, tr = d["sections"]
        assert gf["ok"] is False and gf["assets"] == []
        assert tr["ok"] is True and len(tr["assets"]) >= 1
        assert any("BINANCE_GLOBAL_FUTURES" in w for w in d["warnings"])

    def test_malformed_asset_isolation(self, client, monkeypatch):
        bad = {"canTrade": True, "assets": [
            {"asset": "USDT", "walletBalance": "not-a-number"}, "çöp", None],
            "positions": []}
        _mock(monkeypatch, account=bad)
        _login(client)
        r = client.get("/api/v1/portfolio?include_zero=true")
        assert r.status_code == 200
        gf = r.get_json()["sections"][0]
        assert gf["ok"] is True
        assert gf["assets"][0]["wallet_balance"] == "0"

    def test_empty_assets_with_sort_no_500(self, client, monkeypatch):
        # Boş varlık listesi + sort parametresi 500 üretmemeli
        empty_acc = {"canTrade": True, "assets": [], "positions": []}
        empty_tr = {"code": 0, "data": {"accountAssets": []}}
        _mock(monkeypatch, account=empty_acc, tr=empty_tr)
        _login(client)
        for qs in ("?sort=asset", "?sort=total&order=desc",
                   "?sort=wallet_balance&include_zero=true"):
            r = client.get("/api/v1/portfolio" + qs)
            assert r.status_code == 200, qs
            d = r.get_json()
            assert all(s["assets"] == [] for s in d["sections"])
        r = client.get("/api/v1/portfolio/export.csv?sort=asset")
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/csv")

    def test_cross_section_sort_field_no_500(self, client, monkeypatch):
        # 'total' TR alanıdır; Global bölümünde varsayılana düşmeli (500 yok)
        _mock(monkeypatch)
        _login(client)
        r = client.get("/api/v1/portfolio?sort=total&include_zero=true")
        assert r.status_code == 200
        r2 = client.get("/api/v1/portfolio?sort=wallet_balance")
        assert r2.status_code == 200

    def test_limit_bounded(self, client, monkeypatch):
        big = {"canTrade": True, "positions": [], "assets": [
            {"asset": f"A{i}", "walletBalance": "1"} for i in range(700)]}
        _mock(monkeypatch, account=big)
        _login(client)
        d = client.get("/api/v1/portfolio?limit=500").get_json()
        assert len(d["sections"][0]["assets"]) <= 500


class TestPositionsBackend:
    def test_direction_and_summary(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        d = client.get("/api/v1/global/positions").get_json()
        assert d["ok"] is True
        dirs = {p["symbol"]: p["direction"] for p in d["positions"]}
        assert dirs == {"BTCUSDT": "LONG", "ETHUSDT": "SHORT"}
        btc = next(p for p in d["positions"] if p["symbol"] == "BTCUSDT")
        eth = next(p for p in d["positions"] if p["symbol"] == "ETHUSDT")
        assert btc["position_amt"] == "0.5" and btc["abs_quantity"] == "0.5"
        assert eth["position_amt"] == "-2" and eth["abs_quantity"] == "2"
        s = d["summary"]
        assert s["active_count"] == 2
        assert s["long_count"] == 1 and s["short_count"] == 1
        # Decimal toplama: 500.5 + (-100.2) = 400.3 (float sapması yok)
        assert Decimal(s["total_unrealized_pnl"]) == Decimal("400.3")
        assert "Gerçekleşmemiş" in s["pnl_note"]

    def test_include_zero_flat(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        d = client.get(
            "/api/v1/global/positions?include_zero=true").get_json()
        flat = [p for p in d["positions"] if p["direction"] == "FLAT"]
        assert len(flat) == 1 and flat[0]["symbol"] == "XRPUSDT"
        # TEK YÖN: aynı sembolde eşzamanlı LONG+SHORT üretilmez
        symbols = [p["symbol"] for p in d["positions"]]
        assert len(symbols) == len(set(symbols))

    def test_invalid_include_zero(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        r = client.get("/api/v1/global/positions?include_zero=belki")
        assert r.status_code == 400

    def test_malformed_row_isolation(self, client, monkeypatch):
        _mock(monkeypatch, positions=[{"symbol": "BTCUSDT",
                                       "positionAmt": "çöp"}])
        _login(client)
        r = client.get("/api/v1/global/positions")
        assert r.status_code == 200 and r.get_json()["ok"] is True


class TestOrdersBackend:
    def test_mapping_and_remaining(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        d = client.get("/api/v1/global/orders").get_json()
        assert d["ok"] is True
        btc = next(o for o in d["orders"] if o["symbol"] == "BTCUSDT")
        assert Decimal(btc["remaining_qty"]) == Decimal("0.3")  # Decimal fark
        assert btc["status"] == "PARTIALLY_FILLED"  # durumdan türetilmez
        s = d["summary"]
        assert s == {"open_count": 2, "buy_count": 1, "sell_count": 1,
                     "reduce_only_count": 1}

    def test_no_write_routes(self):
        allowed = {"/api/v1/global/orders", "/api/v1/global/orders/export.csv",
                   "/orders"}  # /orders sayfası: salt-okunur GET görünümü
        for rule in flask_app.app.url_map.iter_rules():
            p = str(rule).lower()
            # Mission 2200 bilinçli genişletme: operasyon merkezi
            # rotaları sertifikalı PAPER kontrollü yürütme katmanından
            # geçer ve tests/test_operation_control_* ile korunur;
            # doğrudan borsa yazması içermez.
            if p.startswith("/api/operation-control/"):
                continue
            if any(w in p for w in ("order", "cancel", "close", "transfer",
                                    "withdraw", "leverage", "margin")):
                assert p in allowed, f"yasak rota: {rule}"
                methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
                assert methods == {"GET"}, f"yazma metodu: {rule}"

    def test_malformed_order_isolation(self, client, monkeypatch):
        _mock(monkeypatch, orders=[{"symbol": "X", "origQty": "abc",
                                    "executedQty": None}])
        _login(client)
        r = client.get("/api/v1/global/orders")
        assert r.status_code == 200 and r.get_json()["ok"] is True
        assert r.get_json()["orders"][0]["remaining_qty"] == "0"


class TestCsvExport:
    def _csv(self, client, url):
        r = client.get(url)
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/csv")
        assert "attachment; filename=alpha-" in r.headers["Content-Disposition"]
        body = r.get_data()
        assert body.startswith("\ufeff".encode("utf-8"))  # BOM
        return body.decode("utf-8-sig")

    def test_portfolio_csv(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        text = self._csv(client, "/api/v1/portfolio/export.csv?include_zero=true")
        lines = text.strip().splitlines()
        assert lines[0].startswith("source,asset,")
        assert any("64442.20" in ln for ln in lines)  # ham Decimal string
        # formül enjeksiyonu: '=EVIL' ve '+HACK' nötralize
        assert any("'=EVIL" in ln for ln in lines)
        assert any("'+HACK" in ln for ln in lines)
        assert "x" * 20 not in text

    def test_positions_csv_negative_preserved(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        text = self._csv(client, "/api/v1/global/positions/export.csv")
        assert "-2" in text          # negatif Decimal DEĞİŞMEZ
        assert "-100.2" in text
        assert "'-2" not in text     # sayısal sütun tırnaklanmaz

    def test_orders_csv(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        text = self._csv(client, "/api/v1/global/orders/export.csv")
        lines = text.strip().splitlines()
        assert lines[0].startswith("symbol,side,type,status,")
        assert any(",0.3," in ln for ln in lines)  # remaining_qty

    def test_csv_invalid_param(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        r = client.get("/api/v1/portfolio/export.csv?sort=hack")
        assert r.status_code == 400

    def test_formula_neutralizer_unit(self):
        for prefix in ("=", "+", "@", "\t", "\r"):
            assert pf._csv_text(prefix + "x").startswith("'")
        assert pf._csv_text("-abc").startswith("'")
        assert pf._csv_num("-12.5") == "-12.5"   # sayısal negatif korunur
        assert pf._csv_text("BTCUSDT") == "BTCUSDT"


class TestWriteSafety:
    def test_counters_zero_after_all(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        for u in ["/api/v1/portfolio", "/api/v1/global/positions",
                  "/api/v1/global/orders", "/api/v1/portfolio/export.csv",
                  "/api/v1/global/positions/export.csv",
                  "/api/v1/global/orders/export.csv"]:
            client.get(u)
        client.post("/api/v1/refresh")
        d = client.get("/api/v1/system/status").get_json()
        assert all(v == 0 for v in d["write_counters"].values())

    def test_no_action_strings_in_templates(self):
        from pathlib import Path
        for name in ("portfolio.html", "positions.html", "orders.html"):
            body = Path("templates", name).read_text().lower()
            for banned in ("emri iptal", "pozisyonu kapat", "emir gönder",
                           "cancelorder", "closeposition", "submitorder"):
                assert banned not in body, (name, banned)


class TestFrontend:
    def test_pages_render(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        checks = {
            "/portfolio": ["Portföy", "Sıfır Bakiyeleri Göster",
                           "CSV Dışa Aktar", "Canlı emir: DEVRE DIŞI",
                           "birleşik toplam gösterilmez"],
            "/positions": ["Pozisyonlar", "Sıfır Pozisyonları Göster",
                           "Toplam Gerçekleşmemiş PnL", "TEK YÖN"],
            "/orders": ["Emirler", "Reduce-Only", "iptal/düzenleme/gönderme "
                        "yoktur"],
        }
        for url, labels in checks.items():
            body = client.get(url).get_data(as_text=True)
            for label in labels:
                assert label in body, (url, label)
            assert "aria-live" in body and 'id="refresh-btn"' in body
            assert "csrf-token" in body

    def test_nav_links_active(self, client):
        _login(client)
        shell = client.get("/start").get_data(as_text=True)
        for href in ('href="/portfolio"', 'href="/positions"',
                     'href="/orders"'):
            assert href in shell
        assert "Sonraki sprint\">Portföy" not in shell

    def test_no_secrets_in_pages(self, client, monkeypatch):
        import os
        _mock(monkeypatch)
        _login(client)
        for url in PAGES:
            body = client.get(url).get_data(as_text=True)
            for name in ("BINANCE_API_SECRET", "SESSION_SECRET",
                         "BINANCE_TR_API_SECRET"):
                v = os.environ.get(name)
                if v and len(v) > 8:
                    assert v not in body
