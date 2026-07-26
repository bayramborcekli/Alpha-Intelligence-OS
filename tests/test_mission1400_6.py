"""Mission 1400.6 — Risk Intelligence Engine testleri."""
import json
from decimal import Decimal
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import dashboard_api as dapi
import ledger_api as la
import risk_api as ra

PASSWORD = "risk-test-parola-1"
HASH = generate_password_hash(PASSWORD)


@pytest.fixture
def client(monkeypatch, tmp_path):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14006_attempts.db")
    # Geçmiş dosyasını testte izole et — gerçek dosyaya yazma
    monkeypatch.setattr(ra, "HISTORY_PATH", tmp_path / "risk_history.jsonl")
    auth._ATTEMPTS.clear()
    dapi.invalidate_caches()
    la.invalidate_ledger_caches()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True
        dapi.invalidate_caches()
        la.invalidate_ledger_caches()


def _login(c):
    return c.post("/api/v1/auth/login",
                  json={"username": "sahip", "password": PASSWORD})


RISK_APIS = ["/api/risk/summary", "/api/risk/exposure", "/api/risk/alerts",
             "/api/risk/history",
             "/api/risk/simulator?symbol=BTCUSDT&direction=LONG"
             "&entry_price=100&quantity=1&leverage=2"]


def _fake_sources(monkeypatch, positions=None, margin="1000",
                  avail="800", orders=0):
    positions = positions if positions is not None else [
        {"symbol": "BTCUSDT", "direction": "LONG", "position_amt": "0.5",
         "mark_price": "400", "entry_price": "390",
         "unrealized_pnl": "5", "leverage": "5"},
        {"symbol": "ETHUSDT", "direction": "SHORT", "position_amt": "-2",
         "mark_price": "50", "entry_price": "55",
         "unrealized_pnl": "-3", "leverage": "3"},
    ]
    monkeypatch.setattr(ra, "_account", lambda: {
        "usdt_margin_balance": margin, "usdt_available_balance": avail})
    monkeypatch.setattr(ra, "_active_positions", lambda: positions)
    monkeypatch.setattr(ra, "_open_orders_count", lambda: orders)
    monkeypatch.setattr(ra.dapi, "tr_account", lambda: {"ok": False})
    monkeypatch.setattr(ra.pf, "positions_view", lambda: {
        "ok": True, "positions": positions,
        "summary": {"total_unrealized_pnl": "2"}})


class TestAuth:
    def test_page_requires_auth(self, client):
        r = client.get("/risk")
        assert r.status_code == 302 and "/login" in r.headers["Location"]

    def test_apis_require_auth(self, client):
        for a in RISK_APIS:
            assert client.get(a).status_code == 401, a

    def test_get_only(self, client):
        _login(client)
        for m in ("post", "put", "patch", "delete"):
            for a in ("/api/risk/summary", "/api/risk/simulator"):
                assert getattr(client, m)(a).status_code == 405, (m, a)


class TestExposure:
    def test_exposure_math_decimal(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        _login(client)
        d = client.get("/api/risk/exposure").get_json()
        assert d["ok"] is True and d["read_only"] is True
        # 0.5*400=200 long, 2*50=100 short
        assert Decimal(d["gross_exposure_usdt"]) == Decimal("300.00")
        assert Decimal(d["net_exposure_usdt"]) == Decimal("100.00")
        assert Decimal(d["exposure_pct_of_margin"]) == Decimal("30.00")
        assets = {a["asset"]: a for a in d["by_asset"]}
        assert Decimal(assets["BTC"]["exposure_pct"]) == Decimal("66.67")
        assert d["by_direction"]["long_pct"] == "66.67"

    def test_source_failure_no_estimates(self, client, monkeypatch):
        monkeypatch.setattr(ra, "_active_positions", lambda: None)
        _login(client)
        d = client.get("/api/risk/exposure").get_json()
        assert d["ok"] is False
        assert d["error"]["code"] == "SOURCE_UNAVAILABLE"


class TestConcentration:
    def test_top5_and_warning(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        _login(client)
        d = client.get("/api/risk/summary").get_json()
        lp = d["largest_position"]
        assert lp["symbol"] == "BTCUSDT"
        assert Decimal(lp["share_pct"]) == Decimal("66.67")
        # %66 > %40 → yüksek konsantrasyon uyarısı
        al = client.get("/api/risk/alerts").get_json()
        codes = [a["code"] for a in al["alerts"]]
        assert "SINGLE_ASSET_CONCENTRATION" in codes

    def test_sector_grouping_not_fabricated(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        _login(client)
        assert ra.concentration()["sector_grouping"] is None


class TestHealthScore:
    def test_deterministic(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        _login(client)
        s1 = client.get("/api/risk/summary").get_json()
        s2 = client.get("/api/risk/summary").get_json()
        assert s1["risk_score"] == s2["risk_score"]
        assert 0 <= s1["risk_score"] <= 100
        assert s1["classification"] in ("Mükemmel", "İyi", "Orta",
                                        "Yüksek Risk", "Kritik")

    def test_penalties_lower_score(self, client, monkeypatch):
        _fake_sources(monkeypatch, margin="1000", avail="50")  # %95 kullanım
        _login(client)
        risky = client.get("/api/risk/summary").get_json()
        _fake_sources(monkeypatch, margin="1000", avail="900")
        healthy = client.get("/api/risk/summary").get_json()
        assert risky["risk_score"] < healthy["risk_score"]

    def test_missing_inputs_no_score(self, client, monkeypatch):
        monkeypatch.setattr(ra, "_account", lambda: None)
        monkeypatch.setattr(ra, "_active_positions", lambda: None)
        monkeypatch.setattr(ra, "_open_orders_count", lambda: None)
        _login(client)
        d = client.get("/api/risk/summary").get_json()
        assert d["risk_score"] is None
        assert d["classification"] is None


class TestAlerts:
    def test_no_duplicates(self, client, monkeypatch):
        _fake_sources(monkeypatch, avail="50")
        _login(client)
        d = client.get("/api/risk/alerts").get_json()
        codes = [a["code"] for a in d["alerts"]]
        assert len(codes) == len(set(codes))
        assert all(a["advisory_only"] for a in d["alerts"])

    def test_margin_usage_alert(self, client, monkeypatch):
        _fake_sources(monkeypatch, avail="100")  # %90 kullanım
        _login(client)
        codes = [a["code"] for a in
                 client.get("/api/risk/alerts").get_json()["alerts"]]
        assert "HIGH_MARGIN_USAGE" in codes
        assert "LOW_AVAILABLE_BALANCE" in codes


class TestSimulator:
    def test_math(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        # Simülatör bağlamı SADECE mevcut önbellekten okur — tohumla
        monkeypatch.setattr(ra.dapi, "_cache", {
            "global_account": {"ok": True, "mono": 0, "data": {
                "usdt_margin_balance": "1000",
                "usdt_available_balance": "800"}},
            "global_positions": {"ok": True, "mono": 0, "data": {
                "positions_all": [
                    {"symbol": "BTCUSDT", "direction": "LONG",
                     "position_amt": "0.5", "mark_price": "400"},
                    {"symbol": "ETHUSDT", "direction": "SHORT",
                     "position_amt": "-2", "mark_price": "50"}]}},
        }, raising=False)
        _login(client)
        d = client.get("/api/risk/simulator?symbol=BTCUSDT&direction=LONG"
                       "&entry_price=200&quantity=2&leverage=4").get_json()
        assert d["ok"] and d["no_exchange_communication"] is True
        assert Decimal(d["position_value_usdt"]) == Decimal("400.00")
        assert Decimal(d["estimated_margin_usdt"]) == Decimal("100.00")
        # (300+400)/1000 = %70
        assert Decimal(d["portfolio_exposure_after_pct"]) == Decimal("70.00")
        # 400/700
        assert Decimal(d["concentration_after_pct"]) == Decimal("57.14")
        assert Decimal(d["estimated_liquidation_buffer_pct"]) == Decimal("25.00")

    @pytest.mark.parametrize("qs", [
        "symbol=x&direction=LONG&entry_price=1&quantity=1&leverage=1",
        "symbol=BTCUSDT&direction=YUKARI&entry_price=1&quantity=1&leverage=1",
        "symbol=BTCUSDT&direction=LONG&entry_price=-5&quantity=1&leverage=1",
        "symbol=BTCUSDT&direction=LONG&entry_price=1&quantity=0&leverage=1",
        "symbol=BTCUSDT&direction=LONG&entry_price=1&quantity=1&leverage=200",
        "symbol=BTCUSDT&direction=LONG&entry_price=abc&quantity=1&leverage=1",
        "symbol=../../etc&direction=LONG&entry_price=1&quantity=1&leverage=1",
    ])
    def test_invalid_input_rejected(self, client, monkeypatch, qs):
        _fake_sources(monkeypatch)
        _login(client)
        r = client.get("/api/risk/simulator?" + qs)
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "INVALID_PARAMETER"

    def test_no_exchange_call_even_cold_cache(self, client, monkeypatch):
        # Önbellek boş + tüm borsa modelleri tuzaklı: simülatör hiçbirini
        # ÇAĞIRMAMALI; yalnızca null bağlam döner.
        called = []
        for fn in ("global_account", "global_positions", "global_orders",
                   "tr_account"):
            monkeypatch.setattr(ra.dapi, fn,
                                lambda *a, **k: called.append(1) or
                                {"ok": False})
        monkeypatch.setattr(ra.dapi, "_cache", {}, raising=False)
        _login(client)
        d = client.get("/api/risk/simulator?symbol=BTCUSDT&direction=LONG"
                       "&entry_price=10&quantity=1&leverage=2").get_json()
        assert not called                      # borsa iletişimi SIFIR
        assert d["ok"] is True
        assert d["portfolio_exposure_after_pct"] is None  # tahmin yok
        assert d["concentration_after_pct"] is None
        assert Decimal(d["position_value_usdt"]) == Decimal("10.00")

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "inf"])
    def test_non_finite_rejected(self, client, monkeypatch, bad):
        _fake_sources(monkeypatch)
        _login(client)
        r = client.get("/api/risk/simulator?symbol=BTCUSDT&direction=LONG"
                       f"&entry_price={bad}&quantity=1&leverage=2")
        assert r.status_code == 400, bad
        assert r.get_json()["error"]["code"] == "INVALID_PARAMETER"


class TestHistory:
    def test_append_only_no_overwrite(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        _login(client)
        client.get("/api/risk/summary")
        h1 = client.get("/api/risk/history").get_json()
        assert h1["append_only"] is True and h1["count"] == 1
        first = json.dumps(h1["snapshots"][0], sort_keys=True)
        client.get("/api/risk/summary")   # aynı gün → ikinci kayıt YOK
        h2 = client.get("/api/risk/history").get_json()
        assert h2["count"] == 1
        assert json.dumps(h2["snapshots"][0], sort_keys=True) == first

    def test_malformed_line_isolated(self, client, monkeypatch, tmp_path):
        p = tmp_path / "hist.jsonl"
        p.write_text('{"date":"2026-07-25","risk_score":80}\nBOZUK\n')
        monkeypatch.setattr(ra, "HISTORY_PATH", p)
        _login(client)
        d = client.get("/api/risk/history").get_json()
        assert d["count"] == 1
        # dosya DÜZENLENMEDİ
        assert "BOZUK" in p.read_text()


class TestFrontend:
    def test_page_renders(self, client):
        _login(client)
        html = client.get("/risk").get_data(as_text=True)
        for label in ("Risk İstihbarat Motoru", "Genel Risk Skoru",
                      "Portföy Sağlığı", "Marj Kullanımı",
                      "Tavsiye Uyarıları", "Pozisyon Konsantrasyonu",
                      "Risk Trendi", "Risk Simülatörü", "Günlük Düşüş"):
            assert label in html, label
        assert 'id="exec-topbar"' in html   # üst çubuk burada da var

    def test_nav_enabled_everywhere(self, client):
        _login(client)
        for page in ("/overview", "/ledger", "/"):
            html = client.get(page).get_data(as_text=True)
            assert 'href="/risk"' in html, page

    def test_no_secret_or_action(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        _login(client)
        html = client.get("/risk").get_data(as_text=True).lower()
        for w in ("api_key", "secret", "password", "signature"):
            assert w not in html, w
        body = client.get("/api/risk/summary").get_data(as_text=True).lower()
        for w in ("api_key", "secret", "password", "signature"):
            assert w not in body, w


class TestWriteSafety:
    def test_risk_routes_get_only(self, client):
        # Tek istisna: spec 6.8 POST /api/v1/risk/simulator — YALNIZCA
        # yerel hesap yapar, borsa iletişimi yoktur (ayrıca testli).
        for rule in flask_app.app.url_map.iter_rules():
            if "risk" not in rule.rule:
                continue
            if rule.rule == "/api/v1/risk/simulator":
                assert rule.methods <= {"POST", "HEAD", "OPTIONS"}, rule.rule
            else:
                assert rule.methods <= {"GET", "HEAD", "OPTIONS"}, rule.rule

    def test_post_simulator_local_only(self, client, monkeypatch):
        # POST simülatör: borsa modelleri tuzaklı, önbellek boş → sıfır çağrı
        called = []
        for fn in ("global_account", "global_positions", "global_orders",
                   "tr_account"):
            monkeypatch.setattr(ra.dapi, fn,
                                lambda *a, **k: called.append(1) or
                                {"ok": False})
        monkeypatch.setattr(ra.dapi, "_cache", {}, raising=False)
        _login(client)
        r = client.post("/api/v1/risk/simulator", json={
            "exchange": "BINANCE_GLOBAL_FUTURES", "symbol": "BTCUSDT",
            "direction": "SHORT", "entry_price": "100", "quantity": "2",
            "leverage": "4"})
        assert r.status_code == 200
        d = r.get_json()
        assert not called
        assert Decimal(d["position_value_usdt"]) == Decimal("200.00")
        assert Decimal(d["estimated_margin_usdt"]) == Decimal("50.00")

    def test_post_simulator_bad_exchange(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        _login(client)
        r = client.post("/api/v1/risk/simulator", json={
            "exchange": "KRAKEN", "symbol": "BTCUSDT", "direction": "LONG",
            "entry_price": "1", "quantity": "1", "leverage": "1"})
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "INVALID_PARAMETER"

    def test_v1_aliases(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        _login(client)
        for p in ("summary", "exposure", "alerts", "history"):
            assert client.get(f"/api/v1/risk/{p}").status_code == 200


class TestThresholdConfig:
    def test_thresholds_loaded_from_config(self, client, monkeypatch,
                                           tmp_path):
        cfg = tmp_path / "risk_config.json"
        cfg.write_text('{"MAX_POSITION_PERCENT": "5", '
                       '"POSITION_HIGH_PERCENT": "6", '
                       '"POSITION_CRITICAL_PERCENT": "7"}',
                       encoding="utf-8")
        monkeypatch.setattr(ra, "CONFIG_PATH", cfg)
        monkeypatch.setattr(ra, "_cfg_cache",
                            {"mtime": None, "values": None})
        th = ra.thresholds()
        assert th["MAX_POSITION_PERCENT"] == Decimal("5")
        assert th["RISK_HIGH_MARGIN"] == Decimal("60")   # varsayılan korunur
        # %66.67'lik pozisyon artık Critical eşiği (7) üstünde
        _fake_sources(monkeypatch)
        conc = ra.concentration()
        assert conc["warnings"] and conc["warnings"][0]["level"] == "Critical"

    def test_open_orders_threshold_configurable(self, monkeypatch, tmp_path):
        cfg = tmp_path / "risk_config.json"
        cfg.write_text('{"MAX_OPEN_ORDERS": "2"}', encoding="utf-8")
        monkeypatch.setattr(ra, "CONFIG_PATH", cfg)
        monkeypatch.setattr(ra, "_cfg_cache",
                            {"mtime": None, "values": None})
        _fake_sources(monkeypatch, orders=3)   # 3 > 2 → ceza uygulanır
        hs = ra.health_score(ra.exposure(), ra.concentration(),
                             ra._account(), 3, None)
        assert any(c["factor"] == "open_orders" for c in hs["components"])

    def test_invalid_config_falls_back(self, monkeypatch, tmp_path):
        cfg = tmp_path / "risk_config.json"
        cfg.write_text("{bozuk json", encoding="utf-8")
        monkeypatch.setattr(ra, "CONFIG_PATH", cfg)
        monkeypatch.setattr(ra, "_cfg_cache",
                            {"mtime": None, "values": None})
        th = ra.thresholds()
        assert th["RISK_CRITICAL_MARGIN"] == Decimal("80")

    def test_alert_fields_complete(self, client, monkeypatch):
        # Her uyarı: timestamp + severity + source + explanation (spec 6.5)
        _fake_sources(monkeypatch, margin="1000", avail="50")
        _login(client)
        d = client.get("/api/risk/alerts").get_json()
        assert d["alerts"]
        for a in d["alerts"]:
            for f in ("timestamp", "severity", "source", "explanation"):
                assert a.get(f), (a["code"], f)

    def test_write_counters_zero(self, client, monkeypatch):
        _fake_sources(monkeypatch)
        _login(client)
        for a in RISK_APIS:
            client.get(a)
        assert all(v == 0 for v in dapi.WRITE_COUNTERS.values())
