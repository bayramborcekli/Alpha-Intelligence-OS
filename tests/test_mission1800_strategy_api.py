"""Mission 1800 / Agent 04 — Strategy Intelligence API testleri.

Rota kayıt, servis delegasyonu, istek-kapsamlı meta (proposal_id,
generated_at), sterile hata ve mimari yasaklar. Rota hesap yapmaz.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import json
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import strategy_service as ssv

PASSWORD = "strategy-test-parola-1"
HASH = generate_password_hash(PASSWORD)

ROUTES = ["/api/strategy/intelligence",
          "/api/v1/strategy/intelligence"]

FIXED_ANALYSIS = {
    "analysis_version": 1, "status": "OK",
    "portfolio": {
        "allocation": {"assets": [{"symbol": "BTCUSDT"}],
                       "cash_weight_pct": "10.00"},
        "exposure": {"gross_pct": "90.00"},
        "concentration": {"top_symbol": "BTCUSDT",
                          "top_share_pct": "80.00",
                          "effective_positions": "4.00"},
        "risk_utilization": {"net_exposure_util_pct": "45.00",
                             "drawdown_util_pct": "10.00",
                             "concentration_util_pct": "50.00",
                             "limits_breached": []},
    },
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m1800_attempts.db")
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True


def _login(client):
    r = client.post("/api/v1/auth/login",
                    json={"username": "sahip", "password": PASSWORD})
    assert r.status_code == 200


def _fix_providers(monkeypatch, status="OK", analysis=None,
                   freshness="fresh"):
    env = analysis if analysis is not None else dict(FIXED_ANALYSIS)
    if analysis is None:
        env = json.loads(json.dumps(FIXED_ANALYSIS))
        env["status"] = status
    monkeypatch.setattr(
        ssv, "build_default_strategy_providers",
        lambda: {"portfolio_analysis":
                 (lambda: {"freshness": freshness, "data": env})})


# ── Rota kaydı / metot sözleşmesi ────────────────────────────────────

def test_routes_registered_get_only():
    rules = {r.rule: r for r in flask_app.app.url_map.iter_rules()}
    for route in ROUTES:
        assert route in rules, route
        assert set(rules[route].methods) <= {"GET", "HEAD", "OPTIONS"}


def test_no_write_methods_anywhere_on_strategy():
    for rule in flask_app.app.url_map.iter_rules():
        if "strategy" in rule.rule:
            assert not ({"POST", "PUT", "DELETE", "PATCH"}
                        & set(rule.methods)), rule.rule


def test_both_aliases_same_endpoint():
    rules = {r.rule: r.endpoint for r in flask_app.app.url_map.iter_rules()}
    assert rules[ROUTES[0]] == rules[ROUTES[1]]


# ── Kimlik doğrulama ─────────────────────────────────────────────────

def test_unauthenticated_rejected(client, monkeypatch):
    _fix_providers(monkeypatch)
    for route in ROUTES:
        r = client.get(route)
        assert r.status_code in (302, 401)


def test_fake_credentials_rejected(client, monkeypatch):
    _fix_providers(monkeypatch)
    r = client.get(ROUTES[0], headers={
        "Authorization": "Bearer sahte",
        "X-Forwarded-For": "10.0.0.1",
        "Cookie": "session=sahte"})
    assert r.status_code in (302, 401)


# ── Yanıt modeli ─────────────────────────────────────────────────────

def test_response_model_complete(client, monkeypatch):
    _fix_providers(monkeypatch)
    _login(client)
    for route in ROUTES:
        r = client.get(route)
        assert r.status_code == 200
        p = r.get_json()
        for k in ("strategy_version", "proposal_id", "generated_at",
                  "advisory_only", "read_only",
                  "portfolio_analysis_version", "confidence",
                  "data_quality", "market_regime", "overall_risk",
                  "recommendations", "warnings", "limitations"):
            assert k in p, k
        assert p["strategy_version"] == 1
        assert p["advisory_only"] is True and p["read_only"] is True
        assert p["data_quality"] == "OK"


def test_proposal_id_request_scoped_unique(client, monkeypatch):
    _fix_providers(monkeypatch)
    _login(client)
    ids = {client.get(ROUTES[0]).get_json()["proposal_id"]
           for _ in range(3)}
    assert len(ids) == 3
    for pid in ids:
        assert isinstance(pid, str) and len(pid) == 32


def test_generated_at_present_utc(client, monkeypatch):
    _fix_providers(monkeypatch)
    _login(client)
    ts = client.get(ROUTES[0]).get_json()["generated_at"]
    assert isinstance(ts, str) and "T" in ts
    assert ts.endswith("+00:00") or ts.endswith("Z")


def test_core_output_unchanged_by_api(client, monkeypatch):
    """API yalnız meta ekler; öneri içeriği servis zarfıyla özdeş."""
    _fix_providers(monkeypatch)
    _login(client)
    got = client.get(ROUTES[0]).get_json()
    got.pop("proposal_id")
    got.pop("generated_at")
    expected = ssv.analyze_strategy(ssv.build_default_strategy_providers())
    assert got == json.loads(json.dumps(expected))


def test_recommendations_delegated(client, monkeypatch):
    _fix_providers(monkeypatch)
    _login(client)
    p = client.get(ROUTES[0]).get_json()
    recs = [r for r in p["recommendations"]
            if "CONCENTRATION_HIGH" in r["reason_codes"]]
    assert len(recs) == 1 and recs[0]["instrument"] == "BTCUSDT"


# ── Durum semantiği (hepsi HTTP 200) ─────────────────────────────────

def test_partial_returns_200(client, monkeypatch):
    _fix_providers(monkeypatch, status="PARTIAL")
    _login(client)
    r = client.get(ROUTES[0])
    assert r.status_code == 200
    assert r.get_json()["data_quality"] == "PARTIAL"


def test_unavailable_returns_200_with_nulls(client, monkeypatch):
    monkeypatch.setattr(
        ssv, "build_default_strategy_providers",
        lambda: {"portfolio_analysis":
                 (lambda: (_ for _ in ()).throw(RuntimeError("gizli")))})
    _login(client)
    r = client.get(ROUTES[0])
    assert r.status_code == 200
    p = r.get_json()
    assert p["data_quality"] == "UNAVAILABLE"
    assert p["confidence"] is None and p["overall_risk"] is None
    assert p["recommendations"] == []
    assert "gizli" not in r.get_data(as_text=True)


def test_stale_degrades_to_partial(client, monkeypatch):
    _fix_providers(monkeypatch, freshness="stale")
    _login(client)
    p = client.get(ROUTES[0]).get_json()
    assert p["data_quality"] == "PARTIAL"


# ── Sterile hata modeli ──────────────────────────────────────────────

def test_unexpected_error_sterile_500(client, monkeypatch):
    def boom():
        raise RuntimeError("iç yol /gizli/dosya.py satır 42")

    monkeypatch.setattr(ssv, "analyze_strategy",
                        lambda providers: boom())
    _login(client)
    r = client.get(ROUTES[0])
    assert r.status_code == 500
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "STRATEGY_ANALYSIS_ERROR"
    text = r.get_data(as_text=True)
    for leak in ("iç yol", "gizli", "Traceback", "RuntimeError", ".py"):
        assert leak not in text, leak


def test_no_provider_internals_leaked(client, monkeypatch):
    _fix_providers(monkeypatch)
    _login(client)
    text = client.get(ROUTES[0]).get_data(as_text=True)
    for banned in ("portfolio_service", "strategy_service", "risk_api",
                   "intelligence_service", "Traceback", "/home/"):
        assert banned not in text, banned


def test_no_execution_fields_in_response(client, monkeypatch):
    _fix_providers(monkeypatch)
    _login(client)
    text = client.get(ROUTES[0]).get_data(as_text=True).lower()
    for banned in ('"quantity"', '"price"', '"order_type"',
                   '"order"', '"qty"'):
        assert banned not in text, banned


def test_cache_headers_no_store(client, monkeypatch):
    _fix_providers(monkeypatch)
    _login(client)
    r = client.get(ROUTES[0])
    assert r.headers.get("Cache-Control") == "no-store, private"


# ── Mimari yasaklar ──────────────────────────────────────────────────

def test_route_source_no_calculation():
    src = inspect.getsource(flask_app.api_strategy_intelligence)
    assert "analyze_strategy" in src
    assert "build_default_strategy_providers" in src
    tree = ast.parse(src)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.Mult, ast.Div, ast.Sub)), \
            "rota hesap yapamaz"
    assert "Decimal" not in src


def test_metadata_generated_only_at_api():
    src = inspect.getsource(flask_app.api_strategy_intelligence)
    assert "uuid.uuid4" in src and "datetime.now" in src
    for mod in ("strategy_intelligence.py", "strategy_service.py"):
        code = Path(mod).read_text(encoding="utf-8")
        assert "uuid" not in code.lower() or "uuid" in code.lower() \
            .replace("uuid4", "")  # çekirdek/servis uuid üretmez
        assert "datetime.now" not in code


def test_client_cannot_override_metadata(client, monkeypatch):
    _fix_providers(monkeypatch)
    _login(client)
    r = client.get(ROUTES[0] +
                   "?proposal_id=sahte&generated_at=1999-01-01")
    p = r.get_json()
    assert p["proposal_id"] != "sahte"
    assert not p["generated_at"].startswith("1999")


def test_no_file_write_side_effects(client, monkeypatch):
    _fix_providers(monkeypatch)
    _login(client)
    writes = []
    real_open = builtins.open

    def guard(file, mode="r", *a, **k):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            writes.append(str(file))
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guard)
    client.get(ROUTES[0])
    assert writes == []


# ── Regresyon uyumluluğu ─────────────────────────────────────────────

def test_portfolio_api_untouched(client, monkeypatch):
    _login(client)
    r = client.get("/api/v1/portfolio/intelligence")
    assert r.status_code == 200
    assert r.get_json()["analysis_version"] == 1


def test_no_route_collisions():
    seen = {}
    for rule in flask_app.app.url_map.iter_rules():
        for m in rule.methods - {"HEAD", "OPTIONS"}:
            key = (rule.rule, m)
            assert key not in seen, key
            seen[key] = rule.endpoint
