"""Mission 1700 / Agent 08 — Full Regression bütünleştirme testleri.

Katmanlar arası uçtan uca zincirler (Core→Service→API→Export), geriye
dönük yüzey uyumluluğu, rota çakışması denetimi, sağlayıcı çağrı
ekonomisi (performans) ve dağıtım duman testi. Üretim kodu DEĞİŞMEZ —
yalnız doğrulama.
"""

from __future__ import annotations

import json
import time

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import portfolio_export as pex
import portfolio_service as psv

PASSWORD = "full-regression-parola-1"
HASH = generate_password_hash(PASSWORD)
GEN_AT = "2026-07-27T00:00:00+00:00"


def _providers(**overrides):
    base = {
        "equity": lambda: {"freshness": "fresh", "data": {
            "nav_usdt": "12345.67890123", "cash_usdt": "-0.00000001",
            "realized_pnl": "25", "unrealized_pnl": "-5",
            "total_fees": "3.5"}},
        "positions": lambda: {"freshness": "fresh", "data": [
            {"symbol": "BTCUSDT", "side": "LONG", "quantity": "0.004",
             "entry_price": "100000", "mark_price": "100000",
             "leverage": "1"}]},
        "risk": lambda: {"freshness": "fresh", "data": {
            "drawdown_pct": "2",
            "thresholds": {"max_net_exposure_pct": "200",
                           "max_drawdown_pct": "5",
                           "max_concentration_pct": "80"}}},
    }
    base.update(overrides)
    return base


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB",
                       "/tmp/test_m1700fr_attempts.db")
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "automation_state.json"))
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True


def _login(c):
    r = c.post("/api/v1/auth/login",
               json={"username": "sahip", "password": PASSWORD})
    assert r.status_code == 200


def _wire(monkeypatch, providers=None):
    fixed = providers if providers is not None else _providers()
    monkeypatch.setattr(psv, "build_default_providers", lambda: fixed)


# ── Uçtan uca zincirler ─────────────────────────────────────────────

class TestEndToEndChains:
    def test_full_chain_ok_byte_deterministic(self):
        env1 = psv.get_portfolio_analysis(_providers(), GEN_AT)
        env2 = psv.get_portfolio_analysis(_providers(), GEN_AT)
        assert env1 == env2 and env1["status"] == "OK"
        assert pex.export_analysis(env1, "json")[1] == \
            pex.export_analysis(env2, "json")[1]
        assert pex.export_analysis(env1, "csv")[1] == \
            pex.export_analysis(env2, "csv")[1]

    def test_partial_degradation_propagates_all_layers(
            self, client, monkeypatch):
        providers = _providers(risk=lambda: (_ for _ in ()).throw(
            RuntimeError("x")))
        _wire(monkeypatch, providers)
        _login(client)
        api = client.get("/api/v1/portfolio/intelligence").get_json()
        assert api["status"] == "PARTIAL"
        assert api["sources"]["risk"]["code"] == "PROVIDER_FAILED"
        body = client.get(
            "/api/v1/portfolio/intelligence/export/csv").get_data()
        text = body.decode("utf-8")
        assert ",drawdown_util_pct,\r\n" in text     # bilinmeyen → boş
        assert '"0"' not in text                     # asla 0 türetilmez

    def test_unavailable_all_layers_honest(self, client, monkeypatch):
        def boom():
            raise RuntimeError("x")
        _wire(monkeypatch, {"equity": boom, "positions": boom,
                            "risk": boom})
        _login(client)
        api = client.get("/api/portfolio/intelligence").get_json()
        assert api["status"] == "UNAVAILABLE"
        assert api["portfolio"]["equity"]["nav_usdt"] is None
        exported = json.loads(client.get(
            "/api/portfolio/intelligence/export/json"
        ).get_data(as_text=True))
        assert exported["status"] == "UNAVAILABLE"
        assert exported["portfolio"]["equity"]["nav_usdt"] is None

    def test_high_precision_negative_decimal_survives_chain(
            self, client, monkeypatch):
        _wire(monkeypatch)
        _login(client)
        api = client.get("/api/v1/portfolio/intelligence").get_json()
        assert api["portfolio"]["equity"]["cash_usdt"] == "-0.00000001"
        exported = json.loads(client.get(
            "/api/v1/portfolio/intelligence/export/json"
        ).get_data(as_text=True))
        assert exported["portfolio"]["equity"]["cash_usdt"] == \
            "-0.00000001"
        assert exported["portfolio"]["equity"]["nav_usdt"] == \
            "12345.67890123"


# ── Uyumluluk: önceki mission yüzeyleri ─────────────────────────────

class TestBackwardCompatibility:
    PAGES = ("/", "/intelligence", "/workspace", "/automation",
             "/portfolio-intelligence")
    APIS = ("/api/automation/status",
            "/api/v1/automation/export/status?format=json",
            "/api/v1/automation/export/status?format=csv")

    def test_previous_pages_render(self, client):
        _login(client)
        for page in self.PAGES:
            r = client.get(page)
            assert r.status_code == 200, page

    def test_previous_apis_respond(self, client):
        _login(client)
        for api in self.APIS:
            r = client.get(api)
            assert r.status_code in (200, 503), api
            assert "Traceback" not in r.get_data(as_text=True)

    def test_auth_gate_unchanged(self, client):
        for path in self.PAGES[1:] + ("/api/automation/status",
                                      "/api/v1/portfolio/intelligence"):
            r = client.get(path)
            assert r.status_code in (401, 302), path

    def test_no_route_collisions(self):
        seen = {}
        for rule in flask_app.app.url_map.iter_rules():
            for method in rule.methods - {"HEAD", "OPTIONS"}:
                key = (rule.rule, method)
                assert key not in seen, key
                seen[key] = rule.endpoint

    def test_cache_headers_unchanged(self, client, monkeypatch):
        _wire(monkeypatch)
        _login(client)
        r = client.get("/api/v1/portfolio/intelligence")
        assert r.headers["Cache-Control"] == "no-store, private"
        r2 = client.get("/api/automation/status")
        assert r2.headers["Cache-Control"] == "no-store, private"


# ── Performans: sağlayıcı ekonomisi + üst sınırlar ──────────────────

class TestPerformance:
    def test_each_provider_called_exactly_once(self):
        calls = {"equity": 0, "positions": 0, "risk": 0}
        base = _providers()

        def counted(name):
            def wrapper():
                calls[name] += 1
                return base[name]()
            return wrapper
        env = psv.get_portfolio_analysis(
            {k: counted(k) for k in calls}, GEN_AT)
        assert env["status"] == "OK"
        assert calls == {"equity": 1, "positions": 1, "risk": 1}

    def test_core_and_export_within_bounds(self):
        providers = _providers()
        t0 = time.perf_counter()
        for _ in range(50):
            env = psv.get_portfolio_analysis(providers, GEN_AT)
        assert time.perf_counter() - t0 < 5.0    # 50 analiz < 5 sn
        t1 = time.perf_counter()
        for _ in range(50):
            pex.export_analysis(env, "json")
            pex.export_analysis(env, "csv")
        assert time.perf_counter() - t1 < 5.0    # 100 export < 5 sn

    def test_api_response_within_bounds(self, client, monkeypatch):
        _wire(monkeypatch)
        _login(client)
        t0 = time.perf_counter()
        for _ in range(10):
            assert client.get(
                "/api/v1/portfolio/intelligence").status_code == 200
        assert time.perf_counter() - t0 < 10.0


# ── Dağıtım duman testi ─────────────────────────────────────────────

class TestDeploymentSmoke:
    def test_app_importable_and_routes_registered(self):
        rules = {r.rule for r in flask_app.app.url_map.iter_rules()}
        for must in ("/", "/intelligence", "/automation",
                     "/portfolio-intelligence",
                     "/api/v1/portfolio/intelligence",
                     "/api/v1/portfolio/intelligence/export/json",
                     "/api/v1/portfolio/intelligence/export/csv"):
            assert must in rules, must

    def test_root_reachable_without_error(self, client):
        assert client.get("/").status_code in (200, 302, 401)

    def test_templates_parse(self):
        envn = flask_app.app.jinja_env
        for name in ("dash_base.html", "portfolio_intelligence.html",
                     "automation.html"):
            envn.get_template(name)              # derleme hatası yok
