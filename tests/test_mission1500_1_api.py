"""Mission 1500.1 / Agent 07 — Read-Only Intelligence API testleri."""

import json

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import dashboard_api as dapi

PASSWORD = "intel-test-parola-1"
HASH = generate_password_hash(PASSWORD)

INTEL_APIS = ["/api/intelligence", "/api/intelligence/summary",
              "/api/intelligence/insights",
              "/api/intelligence/recommendations",
              "/api/intelligence/status"]
V1_APIS = [p.replace("/api/", "/api/v1/") for p in INTEL_APIS
           if p != "/api/intelligence"] + ["/api/v1/intelligence"]


@pytest.fixture
def client(monkeypatch):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m15001_attempts.db")
    monkeypatch.setenv("ALPHA_ENABLE_INTELLIGENCE", "true")
    auth._ATTEMPTS.clear()
    dapi.invalidate_caches()
    flask_app._intel_service = None
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True
        flask_app._intel_service = None
        dapi.invalidate_caches()


def _login(c):
    return c.post("/api/v1/auth/login",
                  json={"username": "sahip", "password": PASSWORD})


def _mock_service(monkeypatch, boom=False):
    from intelligence_service import IntelligenceService

    def ga():
        if boom:
            raise RuntimeError("api_secret=HAM_SECRET_123")
        return {"ok": True, "meta": {"freshness": "FRESH",
                                     "retrieved_at":
                                     "2026-07-26T11:59:58+00:00",
                                     "age_seconds": 2.0},
                "account": {"usdt_margin_balance": "1000",
                            "usdt_available_balance": "800",
                            "unrealized_pnl": "5"}}

    svc = IntelligenceService(
        account_provider=ga,
        positions_provider=lambda: {"ok": True,
                                    "meta": {"freshness": "FRESH",
                                             "age_seconds": 2.0},
                                    "positions": []},
        risk_provider=lambda: {"ok": True, "risk_score": 95,
                               "classification": "Mükemmel",
                               "score_components": [],
                               "single_position_pct": "0",
                               "exposure_pct_of_margin": "0"},
        alerts_provider=lambda: {"ok": True, "alerts": []})
    monkeypatch.setattr(flask_app, "_intel_service", svc)
    return svc


class TestAuth:
    def test_anonymous_rejected(self, client):
        for path in INTEL_APIS + V1_APIS:
            r = client.get(path)
            assert r.status_code == 401, path
            assert "error" in r.get_json()

    def test_authenticated_access(self, client, monkeypatch):
        _mock_service(monkeypatch)
        assert _login(client).status_code == 200
        for path in INTEL_APIS:
            r = client.get(path)
            assert r.status_code == 200, path
            assert r.get_json()["ok"] is True


class TestMethods:
    def test_write_methods_rejected(self, client):
        _login(client)
        for path in INTEL_APIS:
            for method in ("post", "put", "patch", "delete"):
                r = getattr(client, method)(path)
                assert r.status_code == 405, (path, method)


class TestHeaders:
    def test_content_type_and_no_store(self, client, monkeypatch):
        _mock_service(monkeypatch)
        _login(client)
        for path in INTEL_APIS:
            r = client.get(path)
            assert r.content_type.startswith("application/json"), path
            assert r.headers["Cache-Control"] == "no-store, private", path

    def test_no_store_even_when_disabled(self, client, monkeypatch):
        monkeypatch.setenv("ALPHA_ENABLE_INTELLIGENCE", "false")
        _login(client)
        r = client.get("/api/intelligence/status")
        assert r.headers["Cache-Control"] == "no-store, private"


class TestSecrets:
    def test_no_secret_in_responses(self, client, monkeypatch):
        _mock_service(monkeypatch)
        _login(client)
        for path in INTEL_APIS:
            blob = client.get(path).get_data(as_text=True)
            for banned in ("BINANCE", "API_KEY", "API_SECRET",
                           "PASSWORD_HASH", "SESSION_SECRET",
                           "Traceback", "HAM_SECRET"):
                assert banned not in blob, (path, banned)

    def test_provider_failure_sterile(self, client, monkeypatch):
        _mock_service(monkeypatch, boom=True)
        _login(client)
        r = client.get("/api/intelligence/summary")
        assert r.status_code == 200          # servis izole eder, çökmez
        blob = r.get_data(as_text=True)
        assert "HAM_SECRET" not in blob and "Traceback" not in blob
        body = r.get_json()
        assert body["status"] == "PARTIAL"   # partial açıkça işaretli
        assert body["partial"] is True
        assert body["source_errors"]["global_account"]["code"] == \
            "PROVIDER_ERROR"


class TestSchema:
    def test_summary_schema(self, client, monkeypatch):
        _mock_service(monkeypatch)
        _login(client)
        body = client.get("/api/intelligence/summary").get_json()
        for key in ("ok", "enabled", "read_only", "advisory_only",
                    "status", "generated_at", "portfolio_summary",
                    "risk_summary", "insights", "recommendations",
                    "risk_explanations", "freshness", "partial"):
            assert key in body, key
        assert body["advisory_only"] is True
        assert body["risk_summary"]["risk_score"] == 95
        # Decimal string sözleşmesi
        assert body["portfolio_summary"]["usdt_margin_balance"] == "1000"

    def test_list_endpoints_wrapped(self, client, monkeypatch):
        _mock_service(monkeypatch)
        _login(client)
        for path in ("/api/intelligence/insights",
                     "/api/intelligence/recommendations"):
            body = client.get(path).get_json()
            assert body["ok"] is True and isinstance(body["items"], list)
            assert body["advisory_only"] is True

    def test_status_endpoint(self, client, monkeypatch):
        _mock_service(monkeypatch)
        _login(client)
        body = client.get("/api/intelligence/status").get_json()
        assert body["status"] == "OK" and body["partial"] is False
        assert {s["source"] for s in body["sources"]} >= \
            {"global_account", "global_positions", "risk_engine"}


class TestFeatureFlag:
    def test_disabled_safe_response(self, client, monkeypatch):
        monkeypatch.setenv("ALPHA_ENABLE_INTELLIGENCE", "false")
        _login(client)
        for path in INTEL_APIS:
            body = client.get(path).get_json()
            assert body == {"ok": True, "enabled": False,
                            "read_only": True, "advisory_only": True,
                            "status": "UNAVAILABLE",
                            "message": "Intelligence özelliği kapalı "
                                       "(ALPHA_ENABLE_INTELLIGENCE)."}, path

    def test_missing_flag_defaults_off(self, client, monkeypatch):
        monkeypatch.delenv("ALPHA_ENABLE_INTELLIGENCE", raising=False)
        _login(client)
        body = client.get("/api/intelligence").get_json()
        assert body["enabled"] is False      # bilinmeyen/boş bayrak = kapalı

    def test_flag_registered(self):
        import alpha_platform as ap
        assert "ALPHA_ENABLE_INTELLIGENCE" in ap.FEATURE_FLAG_NAMES
