"""Mission 1500.2 / Agent 04 — Workspace Read-Only API testleri."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import intelligence_timeline as tl

PASSWORD = "Workspace-Test-1500!"


def _snap(hour="10", **over):
    base = {
        "generated_at": f"2026-07-26T{hour}:00:00+00:00",
        "status": "OK",
        "partial": False,
        "freshness": [{"source": "account", "status": "OK"}],
        "insights": [{"code": "PORTFOLIO_OK", "confidence": "HIGH"}],
        "recommendations": [{"code": "NO_ACTION_NEEDED", "priority": 99,
                             "confidence": "MEDIUM"}],
        "warnings": [],
        "portfolio_summary": {"total_value": Decimal("123.45")},
        "risk_summary": {"score": 87, "status": "SAGLIKLI",
                         "components": []},
        "risk_explanations": [],
        "advisory_only": True,
    }
    base.update(over)
    return base


@pytest.fixture()
def client(tmp_path, monkeypatch):
    hist = tmp_path / "history.jsonl"
    monkeypatch.setenv("ALPHA_INTELLIGENCE_HISTORY_PATH", str(hist))
    tl.append_snapshot(_snap("08"), hist)
    tl.append_snapshot(
        _snap("09", status="PARTIAL", partial=True,
              recommendations=[{"code": "DATA_REVIEW", "priority": 3,
                                "confidence": "LOW"}]), hist)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "owner")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH",
                       generate_password_hash(PASSWORD))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-workspace")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", str(tmp_path / "att.json"))
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    c = flask_app.app.test_client()
    yield c
    flask_app.app.config["TESTING"] = False


def _login(c):
    r = c.post("/api/v1/auth/login",
               json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 200


ENDPOINTS = ("/api/workspace/timeline", "/api/workspace/snapshot/1",
             "/api/workspace/compare?a=1&b=2",
             "/api/workspace/recommendations",
             "/api/workspace/risk-evolution", "/api/workspace/search")


# ── authentication ───────────────────────────────────────────────────

def test_anonymous_rejected_all_endpoints(client):
    for ep in ENDPOINTS:
        r = client.get(ep)
        assert r.status_code == 401, ep
        r = client.get(ep.replace("/api/", "/api/v1/", 1))
        assert r.status_code == 401, ep


# ── GET uçları + v1 alias ────────────────────────────────────────────

def test_all_endpoints_and_aliases_ok(client):
    _login(client)
    for ep in ENDPOINTS:
        for path in (ep, ep.replace("/api/", "/api/v1/", 1)):
            r = client.get(path)
            assert r.status_code == 200, path
            body = r.get_json()
            assert body["ok"] is True
            assert body["read_only"] is True
            assert body["advisory_only"] is True


def test_timeline(client):
    _login(client)
    body = client.get("/api/workspace/timeline").get_json()
    assert body["total"] == 2
    assert [e["id"] for e in body["entries"]] == [1, 2]
    limited = client.get(
        "/api/workspace/timeline?limit=1&offset=1").get_json()
    assert [e["id"] for e in limited["entries"]] == [2]


def test_snapshot(client):
    _login(client)
    body = client.get("/api/workspace/snapshot/2").get_json()
    assert body["id"] == 2
    assert body["snapshot"]["status"] == "PARTIAL"


def test_compare(client):
    _login(client)
    body = client.get("/api/workspace/compare?a=1&b=2").get_json()
    assert body["ok"] and not body["identical"]
    changes = {d["change"] for d in body["differences"]}
    assert changes <= {"NEW", "CHANGED", "REMOVED"}


def test_recommendations_and_risk_evolution(client):
    _login(client)
    recs = client.get("/api/workspace/recommendations").get_json()
    assert {i["code"] for i in recs["items"]} == \
        {"NO_ACTION_NEEDED", "DATA_REVIEW"}
    risk = client.get("/api/workspace/risk-evolution").get_json()
    assert [p["risk_score"] for p in risk["series"]] == [87, 87]
    assert risk["forecast"] is None


def test_search(client):
    _login(client)
    assert client.get("/api/workspace/search?status=PARTIAL"
                      ).get_json()["total"] == 1
    assert client.get("/api/workspace/search?partial=false"
                      ).get_json()["total"] == 1
    assert client.get("/api/workspace/search?recommendation=DATA_REVIEW"
                      ).get_json()["entries"][0]["id"] == 2
    assert client.get("/api/workspace/search?insight=PORTFOLIO_OK"
                      ).get_json()["total"] == 2
    assert client.get("/api/workspace/search?confidence=LOW"
                      ).get_json()["total"] == 1
    assert client.get(
        "/api/workspace/search?date=2026-07-26T09:00:00%2B00:00"
    ).get_json()["total"] == 1


# ── status kodları ───────────────────────────────────────────────────

def test_snapshot_404(client):
    _login(client)
    r = client.get("/api/workspace/snapshot/99")
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "SNAPSHOT_NOT_FOUND"


def test_compare_404(client):
    _login(client)
    r = client.get("/api/workspace/compare?a=1&b=99")
    assert r.status_code == 404


def test_invalid_params_400(client):
    _login(client)
    for url in ("/api/workspace/timeline?limit=abc",
                "/api/workspace/timeline?limit=-5",
                "/api/workspace/timeline?offset=-2",
                "/api/workspace/compare?a=1",       # b eksik
                "/api/workspace/compare?a=x&b=2",
                "/api/workspace/compare?a=0&b=1",
                "/api/workspace/compare?a=1&b=-3",
                "/api/workspace/snapshot/true",
                "/api/workspace/snapshot/0",
                "/api/workspace/snapshot/-1",
                "/api/v1/workspace/snapshot/abc",
                "/api/workspace/search?partial=belki",
                "/api/workspace/search?advisory_only=1",
                "/api/workspace/search?date=bozuk-tarih"):
        r = client.get(url)
        assert r.status_code == 400, url
        assert r.get_json()["error"]["code"] == "INVALID_PARAMETER"
        assert r.headers["Cache-Control"] == "no-store, private", url


def test_provider_error_returns_200_sterile(client, monkeypatch):
    _login(client)
    import intelligence_workspace_service as wss
    monkeypatch.setattr(
        wss, "get_timeline",
        lambda **kw: wss._sterile("WORKSPACE_TIMELINE_ERROR"))
    r = client.get("/api/workspace/timeline")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"]["message"] == "İşlem tamamlanamadı"


# ── cache / sözleşme / determinizm ───────────────────────────────────

def test_no_store_headers(client):
    _login(client)
    for ep in ENDPOINTS:
        r = client.get(ep)
        assert r.headers["Cache-Control"] == "no-store, private", ep
    r404 = client.get("/api/workspace/snapshot/99")
    assert r404.headers["Cache-Control"] == "no-store, private"
    r400 = client.get("/api/workspace/timeline?limit=x")
    assert r400.headers["Cache-Control"] == "no-store, private"


def test_decimal_as_string(client):
    _login(client)
    body = client.get("/api/workspace/snapshot/1").get_json()
    assert body["snapshot"]["portfolio_summary"]["total_value"] == "123.45"
    raw = client.get("/api/workspace/snapshot/1").data.decode()
    assert '"123.45"' in raw and "123.45," not in raw


def test_determinism(client):
    _login(client)
    for ep in ENDPOINTS:
        assert client.get(ep).data == client.get(ep).data, ep


# ── metot kısıtı / sızıntı ───────────────────────────────────────────

def test_write_methods_405(client):
    _login(client)
    for ep in ("/api/workspace/timeline", "/api/workspace/compare",
               "/api/workspace/search", "/api/workspace/snapshot/1",
               "/api/v1/workspace/timeline"):
        for method in ("post", "put", "patch", "delete"):
            r = getattr(client, method)(ep)
            assert r.status_code == 405, (ep, method)


def test_workspace_routes_get_only_in_url_map():
    for rule in flask_app.app.url_map.iter_rules():
        if "/workspace/" in rule.rule and rule.rule.startswith("/api"):
            assert set(rule.methods) <= {"GET", "HEAD", "OPTIONS"}, \
                rule.rule


def test_no_secret_leak_in_responses(client, monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "FAKE-KEY-LEAK-CHECK-123")
    _login(client)
    for ep in ENDPOINTS:
        text = client.get(ep).data.decode()
        assert "FAKE-KEY-LEAK-CHECK-123" not in text
        for bad in ("api_key", "password_hash", "session_secret",
                    "Traceback"):
            assert bad not in text, (ep, bad)


def test_routes_use_service_not_timeline_directly():
    """Workspace rotaları timeline modülüne doğrudan erişmez."""
    import inspect
    src = inspect.getsource(flask_app)
    start = src.index("Mission 1500.2: Workspace Read-Only API")
    end = src.index("api_executive_summary")
    section = src[start:end]
    assert "intelligence_timeline" not in section
    assert "intelligence_workspace_service" in section
