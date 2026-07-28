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


ACC = {"canTrade": True, "balances": [
    {"asset": "USDT", "free": "64442.20", "locked": "0"},
    {"asset": "BNB", "free": "0", "locked": "0"},
    {"asset": "=EVIL", "free": "1", "locked": "0"}]}
TR_OK = {"code": 0, "data": {"status": 1, "accountAssets": [
    {"asset": "TRY", "free": "1.3942", "locked": "0.5", "updateTime": 9},
    {"asset": "USDT", "free": "20.9685", "locked": "0"},
    {"asset": "+HACK", "free": "1", "locked": "0"},
    {"asset": "SHIB", "free": "0", "locked": "0"}]}}


def _mock(monkeypatch, account=ACC, tr=TR_OK):
    def fake(base, path, allowlist, key, secret, params=None, timeout=10):
        if ("GET", path) not in allowlist:
            raise RuntimeError("allowlist ihlali")
        table = {"/api/v3/account": account,
                 "/open/v1/account/spot": tr}
        val = table.get(path)
        if val is None:
            raise dapi.SafeExchangeError("EXCHANGE_UNAVAILABLE", "mock yok")
        return val
    monkeypatch.setattr(dapi, "_signed_get", fake)
    # portfolio_api artık kendi imzalı fetch'ini yapmaz; kanonik hesap
    # servisi (dashboard_api) üzerinden paylaşımlı ham yanıtı okur.
    for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET",
              "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET"):
        monkeypatch.setenv(k, "x" * 20)


PAGES = ["/portfolio"]
APIS = ["/api/v1/portfolio", "/api/v1/portfolio/export.csv"]


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
        assert srcs == ["BINANCE_GLOBAL_SPOT", "BINANCE_TR"]
        gf = d["sections"][0]
        usdt = next(a for a in gf["assets"] if a["asset"] == "USDT")
        assert Decimal(usdt["free"]) == Decimal("64442.20")
        assert Decimal(usdt["total"]) == Decimal("64442.20")
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
        assert any("BINANCE_GLOBAL_SPOT" in w for w in d["warnings"])

    def test_malformed_asset_isolation(self, client, monkeypatch):
        bad = {"canTrade": True, "balances": [
            {"asset": "USDT", "free": "not-a-number"}, "çöp", None]}
        _mock(monkeypatch, account=bad)
        _login(client)
        r = client.get("/api/v1/portfolio?include_zero=true")
        assert r.status_code == 200
        gf = r.get_json()["sections"][0]
        assert gf["ok"] is True
        assert gf["assets"][0]["free"] == "0"

    def test_empty_assets_with_sort_no_500(self, client, monkeypatch):
        # Boş varlık listesi + sort parametresi 500 üretmemeli
        empty_acc = {"canTrade": True, "balances": []}
        empty_tr = {"code": 0, "data": {"accountAssets": []}}
        _mock(monkeypatch, account=empty_acc, tr=empty_tr)
        _login(client)
        for qs in ("?sort=asset", "?sort=total&order=desc",
                   "?sort=free&include_zero=true"):
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
        r2 = client.get("/api/v1/portfolio?sort=free")
        assert r2.status_code == 200

    def test_limit_bounded(self, client, monkeypatch):
        big = {"canTrade": True, "balances": [
            {"asset": f"A{i}", "free": "1", "locked": "0"}
            for i in range(700)]}
        _mock(monkeypatch, account=big)
        _login(client)
        d = client.get("/api/v1/portfolio?limit=500").get_json()
        assert len(d["sections"][0]["assets"]) <= 500


class TestFuturesSurfacesRemoved:
    def test_positions_orders_apis_gone(self, client, monkeypatch):
        _mock(monkeypatch)
        _login(client)
        for u in ("/api/v1/global/positions", "/api/v1/global/orders",
                  "/api/v1/global/positions/export.csv",
                  "/api/v1/global/orders/export.csv"):
            assert client.get(u).status_code == 404, u

    def test_views_pass_through_tombstone(self):
        for view in (pf.positions_view, pf.orders_view):
            m = view()
            assert m["ok"] is False
            assert m["error"]["code"] == "FUTURES_REMOVED"

    def test_no_write_routes(self):
        allowed = set()
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
        for u in ["/api/v1/portfolio", "/api/v1/portfolio/export.csv"]:
            client.get(u)
        client.post("/api/v1/refresh")
        d = client.get("/api/v1/system/status").get_json()
        assert all(v == 0 for v in d["write_counters"].values())

    def test_no_action_strings_in_templates(self):
        from pathlib import Path
        for name in ("portfolio.html",):
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
        assert 'href="/portfolio"' in shell
        # Spot-only: Futures sayfa bağlantıları kaldırıldı
        assert 'href="/positions"' not in shell
        assert 'href="/orders"' not in shell

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
