"""Mission 1700 / Agent 04 — Portfolio Intelligence API testleri.

Rota kayıt, servis delegasyonu, zarf koruma, sterile hata, determinizm
ve mimari yasaklar. Rota hesap yapmaz; her şey servis → çekirdek.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import portfolio_service as psv

PASSWORD = "portfolio-test-parola-1"
HASH = generate_password_hash(PASSWORD)

ROUTES = ["/api/portfolio/intelligence",
          "/api/v1/portfolio/intelligence"]
GEN_AT = "2026-07-27T00:00:00+00:00"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m1700_attempts.db")
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


def _fake_providers():
    return {
        "equity": lambda: {"freshness": "fresh", "data": {
            "nav_usdt": "1000", "cash_usdt": "400",
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


def _wire(monkeypatch, providers=None, svc_exc=None,
          capture: dict | None = None):
    """Varsayılan sağlayıcıları sahte deterministik set ile değiştirir."""
    fixed = providers if providers is not None else _fake_providers()
    monkeypatch.setattr(psv, "build_default_providers", lambda: fixed)
    if svc_exc is not None or capture is not None:
        real = psv.get_portfolio_analysis

        def spy(prov, generated_at=None):
            if capture is not None:
                capture["generated_at"] = generated_at
                capture["providers"] = prov
            if svc_exc is not None:
                raise svc_exc
            return real(prov, generated_at)
        monkeypatch.setattr(psv, "get_portfolio_analysis", spy)


# ── Rota kaydı ve auth sınırı ────────────────────────────────────────

def test_routes_registered_get_only():
    rules = {r.rule: r for r in flask_app.app.url_map.iter_rules()}
    for route in ROUTES:
        assert route in rules
        methods = rules[route].methods - {"HEAD", "OPTIONS"}
        assert methods == {"GET"}


def test_requires_authentication(client):
    for route in ROUTES:
        r = client.get(route)
        assert r.status_code == 401


def test_unsupported_methods_rejected(client):
    _login(client)
    for method in ("post", "put", "patch", "delete"):
        r = getattr(client, method)(ROUTES[0])
        assert r.status_code == 405


# ── Başarı ve zarf koruması ──────────────────────────────────────────

def test_get_success_ok_envelope(client, monkeypatch):
    _wire(monkeypatch)
    _login(client)
    for route in ROUTES:
        r = client.get(route)
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["read_only"] is True
        assert body["advisory_only"] is True
        assert body["analysis_version"] == 1
        assert body["status"] == "OK"
        assert isinstance(body["generated_at"], str)
        assert set(body) >= {"ok", "read_only", "advisory_only",
                             "analysis_version", "status",
                             "generated_at", "sources", "portfolio"}
        assert r.headers["Cache-Control"] == "no-store, private"


def test_fixed_point_strings_and_nulls_preserved(client, monkeypatch):
    providers = _fake_providers()
    providers["equity"] = lambda: {"freshness": "fresh", "data": {
        "nav_usdt": "1000", "cash_usdt": "400",
        "realized_pnl": None, "unrealized_pnl": "-5",
        "total_fees": None}}
    _wire(monkeypatch, providers=providers)
    _login(client)
    body = client.get(ROUTES[0]).get_json()
    assert body["status"] == "PARTIAL"             # eksik alan → PARTIAL
    eq = body["portfolio"]["equity"]
    assert eq["nav_usdt"] == "1000.00000000"       # string, sayı değil
    assert eq["realized_pnl"] is None              # null korunur, 0 değil
    assert eq["total_fees"] is None
    assert body["portfolio"]["exposure"]["gross"] == "400.00000000"


def test_partial_response(client, monkeypatch):
    providers = _fake_providers()

    def boom():
        raise RuntimeError("/gizli/yol secret token")
    providers["risk"] = boom
    _wire(monkeypatch, providers=providers)
    _login(client)
    r = client.get(ROUTES[0])
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "PARTIAL"
    assert body["sources"]["risk"] == {
        "status": "failed", "freshness": "unavailable",
        "available": False, "code": "PROVIDER_FAILED"}
    text = r.get_data(as_text=True)
    assert "gizli" not in text and "secret" not in text \
        and "Traceback" not in text


def test_unavailable_response_http_200(client, monkeypatch):
    def boom():
        raise OSError("x")
    _wire(monkeypatch, providers={"equity": boom, "positions": boom,
                                  "risk": boom})
    _login(client)
    r = client.get(ROUTES[0])
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "UNAVAILABLE"
    assert body["portfolio"]["equity"]["nav_usdt"] is None
    assert body["portfolio"]["health"]["portfolio_health_score"] is None


# ── Servis delegasyonu ───────────────────────────────────────────────

def test_route_delegates_to_service(client, monkeypatch):
    capture: dict = {}
    _wire(monkeypatch, capture=capture)
    _login(client)
    client.get(ROUTES[0])
    assert sorted(capture["providers"]) == ["equity", "positions",
                                            "risk"]
    assert isinstance(capture["generated_at"], str)
    assert capture["generated_at"].endswith("+00:00")  # UTC ISO


def test_generated_at_not_client_overridable(client, monkeypatch):
    capture: dict = {}
    _wire(monkeypatch, capture=capture)
    _login(client)
    body = client.get(
        ROUTES[0] + "?generated_at=1999-01-01T00:00:00").get_json()
    assert body["generated_at"] == capture["generated_at"]
    assert not body["generated_at"].startswith("1999")


def test_deterministic_payload_for_same_service_result(client,
                                                       monkeypatch):
    result = psv.get_portfolio_analysis(_fake_providers(), GEN_AT)
    monkeypatch.setattr(psv, "build_default_providers", lambda: {})
    monkeypatch.setattr(psv, "get_portfolio_analysis",
                        lambda prov, generated_at=None: result)
    _login(client)
    a = client.get(ROUTES[0]).get_data()
    b = client.get(ROUTES[0]).get_data()
    assert a == b  # bayt-özdeş
    assert json.loads(a)["generated_at"] == GEN_AT


def test_service_error_sterilized(client, monkeypatch):
    _wire(monkeypatch,
          svc_exc=RuntimeError("api-key=XYZ /home/runner/secret.pem"))
    _login(client)
    r = client.get(ROUTES[0])
    assert r.status_code == 500
    body = r.get_json()
    assert body == {"ok": False, "error": {
        "code": "PORTFOLIO_ANALYSIS_ERROR",
        "message": "Portföy analizi üretilemedi."}}
    text = r.get_data(as_text=True)
    for leak in ("XYZ", "secret", "Traceback", "runner", "RuntimeError"):
        assert leak not in text


def test_valid_envelope_not_swallowed(client, monkeypatch):
    """Geçerli UNAVAILABLE zarfı hataya dönüştürülmez."""
    _wire(monkeypatch, providers={})
    _login(client)
    r = client.get(ROUTES[0])
    assert r.status_code == 200
    assert r.get_json()["status"] == "UNAVAILABLE"


# ── Mimari yasaklar (statik — rota kaynağı) ──────────────────────────

def _route_source() -> str:
    return inspect.getsource(flask_app.api_portfolio_intelligence)


def test_route_has_no_calculations_or_provider_logic():
    src = _route_source()
    tree = ast.parse("def _w():\n" +
                     "".join("    " + line + "\n"
                             for line in src.splitlines()[2:]))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.BinOp), \
            "rotada aritmetik yasak — hesap çekirdekte"
    for banned in ("Decimal", "quantize", "risk_api",
                   "import portfolio_intelligence", "append_snapshot",
                   "import intelligence_service", "binance", "Thread",
                   "sched"):
        assert banned not in src, banned


def test_route_uses_service_not_core():
    src = _route_source()
    assert "portfolio_service" in src
    assert "get_portfolio_analysis" in src
    assert "analyze_portfolio" not in src
    assert "build_default_providers" in src  # sağlayıcı sahipliği serviste


def test_no_snapshot_write_from_route(client, monkeypatch):
    import intelligence_timeline as tl
    calls = []
    monkeypatch.setattr(tl, "append_snapshot",
                        lambda *a, **k: calls.append(1))
    _wire(monkeypatch)
    _login(client)
    client.get(ROUTES[0])
    assert calls == []


def test_no_filesystem_writes_from_route(client, monkeypatch,
                                         tmp_path):
    _wire(monkeypatch)
    _login(client)
    import builtins
    real_open = builtins.open
    writes = []

    def guard(file, mode="r", *a, **k):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            writes.append(str(file))
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr(builtins, "open", guard)
    client.get(ROUTES[0])
    assert writes == []


def test_backward_compat_automation_status_untouched(client,
                                                     monkeypatch,
                                                     tmp_path):
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "state.json"))
    monkeypatch.delenv("ALPHA_AUTOMATION_ENABLED", raising=False)
    _login(client)
    r = client.get("/api/v1/automation/status")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
