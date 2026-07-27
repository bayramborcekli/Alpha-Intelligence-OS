"""Mission 1700 / Agent 06 — Portfolio Intelligence Export testleri.

JSON/CSV determinizmi, null ve sabit-nokta koruması, kolon sırası,
UTF-8, sterile hata modeli ve mimari yasaklar. Export zarfı üretmez,
değiştirmez ve hesap yapmaz.
"""

from __future__ import annotations

import ast
import csv
import io
import json
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import portfolio_export as pex
import portfolio_service as psv

PASSWORD = "portfolio-export-parola-1"
HASH = generate_password_hash(PASSWORD)

GEN_AT = "2026-07-27T00:00:00+00:00"
JSON_ROUTES = ["/api/portfolio/intelligence/export/json",
               "/api/v1/portfolio/intelligence/export/json"]
CSV_ROUTES = ["/api/portfolio/intelligence/export/csv",
              "/api/v1/portfolio/intelligence/export/csv"]


def _providers():
    return {
        "equity": lambda: {"freshness": "fresh", "data": {
            "nav_usdt": "1000", "cash_usdt": "400",
            "realized_pnl": "25", "unrealized_pnl": "-5",
            "total_fees": "3.5"}},
        "positions": lambda: {"freshness": "fresh", "data": [
            {"symbol": "BTCUSDT", "side": "LONG", "quantity": "0.004",
             "entry_price": "100000", "mark_price": "100000",
             "leverage": "1"},
            {"symbol": "ETHUSDT", "side": "SHORT", "quantity": "0.05",
             "entry_price": "4000", "mark_price": "3900",
             "leverage": "2"}]},
        "risk": lambda: {"freshness": "fresh", "data": {
            "drawdown_pct": "2",
            "thresholds": {"max_net_exposure_pct": "200",
                           "max_drawdown_pct": "5",
                           "max_concentration_pct": "80"}}},
    }


def _envelope():
    return psv.get_portfolio_analysis(_providers(), GEN_AT)


def _partial_envelope():
    providers = _providers()

    def boom():
        raise RuntimeError("gizli")
    providers["risk"] = boom
    return psv.get_portfolio_analysis(providers, GEN_AT)


def _csv_rows(body: bytes) -> list[list[str]]:
    text = body.decode("utf-8")
    assert text.startswith("\ufeff")
    return list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB",
                       "/tmp/test_m1700ex_attempts.db")
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


# ── JSON export ──────────────────────────────────────────────────────

def test_json_export_faithful_and_deterministic():
    env = _envelope()
    _, body1, mime, name = pex.export_analysis(env, "json")
    _, body2, _, _ = pex.export_analysis(_envelope(), "json")
    assert body1 == body2                       # bayt-özdeş
    assert mime == "application/json; charset=utf-8"
    assert name == "portfolio_intelligence.json"
    decoded = json.loads(body1.decode("utf-8"))
    assert decoded == env                       # yeniden yapılandırma yok


def test_json_export_preserves_invariants_and_nulls():
    env = _partial_envelope()
    _, body, _, _ = pex.export_analysis(env, "json")
    decoded = json.loads(body.decode("utf-8"))
    assert decoded["analysis_version"] == 1
    assert decoded["generated_at"] == GEN_AT
    assert decoded["read_only"] is True
    assert decoded["advisory_only"] is True
    assert decoded["status"] == "PARTIAL"
    # risk kaynağı düştü → null'lar aynen JSON null olarak korunur
    assert decoded["portfolio"]["performance"]["drawdown_pct"] is None
    assert decoded["portfolio"]["risk_utilization"][
        "drawdown_util_pct"] is None
    assert decoded["sources"] == env["sources"]
    # sabit-nokta string'ler string kalır
    assert decoded["portfolio"]["equity"]["nav_usdt"] == "1000.00000000"


def test_json_export_does_not_mutate_envelope():
    env = _envelope()
    snapshot = json.dumps(env, sort_keys=True)
    pex.export_analysis(env, "json")
    pex.export_analysis(env, "csv")
    assert json.dumps(env, sort_keys=True) == snapshot


# ── CSV export ───────────────────────────────────────────────────────

def test_csv_export_structure_and_column_order():
    _, body, mime, name = pex.export_analysis(_envelope(), "csv")
    assert mime == "text/csv; charset=utf-8"
    assert name == "portfolio_intelligence.csv"
    assert b"\r\n" in body                      # sabit satır sonu
    rows = _csv_rows(body)
    assert rows[0] == ["section", "field", "value"]
    sections = [r[0] for r in rows[1:]]
    # bölüm sırası deterministik: meta→summary→positions→risk→div→sources
    order = ["meta", "summary", "positions", "risk",
             "diversification", "sources"]
    assert [s for s in order if s in sections] == order
    assert sections == sorted(sections, key=order.index)


def test_csv_export_required_fields_present():
    rows = _csv_rows(pex.export_analysis(_envelope(), "csv")[1])
    idx = {(r[0], r[1]): r[2] for r in rows[1:]}
    assert idx[("summary", "status")] == "OK"
    assert idx[("summary", "health")] == "63.09"
    assert idx[("summary", "nav")] == "1000.00000000"
    assert idx[("summary", "cash")] == "400.00000000"
    assert idx[("summary", "gross_exposure")] == "595.00000000"
    assert idx[("summary", "net_exposure")] == "205.00000000"
    assert idx[("positions", "1.symbol")] == "BTCUSDT"
    assert idx[("positions", "1.side")] == "LONG"
    assert idx[("positions", "1.quantity")] == "0.00400000"
    assert idx[("positions", "1.mark_price")] == "100000.00000000"
    assert idx[("positions", "1.weight_pct")] == "40.00"
    assert idx[("positions", "2.symbol")] == "ETHUSDT"
    assert idx[("positions", "2.unrealized_pnl")] == "5.00000000"
    assert idx[("risk", "net_exposure_util_pct")] == "10.25"
    assert idx[("risk", "violations")] == ""     # aşım yok → boş
    assert idx[("diversification", "hhi")] == "55.94"
    assert idx[("diversification", "effective_positions")] == "1.79"
    assert idx[("diversification", "top_position")] == "BTCUSDT"
    assert idx[("diversification", "top_weight")] == "67.23"
    assert idx[("meta", "analysis_version")] == "1"
    assert idx[("meta", "generated_at")] == GEN_AT
    assert idx[("meta", "read_only")] == "true"
    assert idx[("meta", "advisory_only")] == "true"
    assert idx[("sources", "equity.status")] == "ok"


def test_csv_unknown_values_empty_never_zero():
    rows = _csv_rows(pex.export_analysis(_partial_envelope(), "csv")[1])
    idx = {(r[0], r[1]): r[2] for r in rows[1:]}
    assert idx[("risk", "drawdown_util_pct")] == ""   # bilinmeyen → boş
    assert idx[("sources", "risk.code")] == "PROVIDER_FAILED"
    for value in idx.values():
        assert value != "0"                            # 0 türetilmez


def test_csv_deterministic_bytes():
    a = pex.export_analysis(_envelope(), "csv")[1]
    b = pex.export_analysis(_envelope(), "csv")[1]
    assert a == b


def test_csv_formula_injection_neutralized():
    env = _envelope()
    env["portfolio"]["concentration"]["top_symbol"] = "=HYPERLINK(x)"
    rows = _csv_rows(pex.export_analysis(env, "csv")[1])
    idx = {(r[0], r[1]): r[2] for r in rows[1:]}
    assert idx[("diversification", "top_position")].startswith("'=")


def test_csv_negative_numbers_not_quoted():
    rows = _csv_rows(pex.export_analysis(_envelope(), "csv")[1])
    idx = {(r[0], r[1]): r[2] for r in rows[1:]}
    # "-5.00000000" sayıdır; formül koruması sayıları bozamaz
    assert idx[("positions", "2.side")] == "SHORT"
    for (_, _), v in idx.items():
        assert not (v.startswith("'-") and v[2:3].isdigit())


# ── Sterile hata modeli ──────────────────────────────────────────────

def test_unsupported_format_sterile():
    env, body, mime, name = pex.export_analysis(_envelope(), "xml")
    assert body is None and mime is None and name is None
    assert env == {"ok": False, "error": {
        "code": "INVALID_FORMAT",
        "message": "Geçersiz format parametresi."}}


def test_invalid_envelope_sterile():
    for bad in (None, "x", {}, {"ok": True}):
        env, body, _, _ = pex.export_analysis(bad, "json")
        assert body is None
        assert env["error"]["code"] == "ANALYSIS_UNAVAILABLE"


# ── API entegrasyonu ─────────────────────────────────────────────────

def test_export_routes_registered_get_only():
    rules = {r.rule: r for r in flask_app.app.url_map.iter_rules()}
    for route in JSON_ROUTES + CSV_ROUTES:
        assert route in rules
        assert rules[route].methods - {"HEAD", "OPTIONS"} == {"GET"}


def test_export_requires_auth(client):
    for route in JSON_ROUTES + CSV_ROUTES:
        assert client.get(route).status_code == 401


def test_json_endpoint_matches_api_envelope(client, monkeypatch):
    _wire(monkeypatch)
    _login(client)
    r = client.get(JSON_ROUTES[1])
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/") is False
    assert "attachment" in r.headers["Content-Disposition"]
    assert r.headers["Cache-Control"] == "no-store, private"
    exported = json.loads(r.get_data(as_text=True))
    api_env = client.get("/api/v1/portfolio/intelligence").get_json()
    api_env.pop("generated_at")
    gen = exported.pop("generated_at")
    assert isinstance(gen, str) and gen.endswith("+00:00")
    assert exported == api_env                  # aynı veri yolu


def test_csv_endpoint(client, monkeypatch):
    _wire(monkeypatch)
    _login(client)
    r = client.get(CSV_ROUTES[1])
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/csv")
    assert 'filename="portfolio_intelligence.csv"' in \
        r.headers["Content-Disposition"]
    rows = _csv_rows(r.get_data())
    assert rows[0] == ["section", "field", "value"]


def test_export_endpoint_error_sterile(client, monkeypatch):
    monkeypatch.setattr(
        psv, "build_default_providers",
        lambda: (_ for _ in ()).throw(RuntimeError("/gizli secret")))
    _login(client)
    r = client.get(JSON_ROUTES[0])
    assert r.status_code == 500
    body = r.get_json()
    assert body["error"]["code"] == "PORTFOLIO_ANALYSIS_ERROR"
    text = r.get_data(as_text=True)
    assert "gizli" not in text and "secret" not in text


def test_export_assembly_error_sterile(client, monkeypatch):
    """export_analysis kendisi patlarsa da yanıt sterile 500 olur."""
    _wire(monkeypatch)

    def boom(envelope, fmt):
        raise RuntimeError("iç ayrıntı /yol/secret.pem Traceback")
    monkeypatch.setattr(pex, "export_analysis", boom)
    _login(client)
    for route in (JSON_ROUTES[0], CSV_ROUTES[0]):
        r = client.get(route)
        assert r.status_code == 500
        assert r.get_json() == {"ok": False, "error": {
            "code": "PORTFOLIO_ANALYSIS_ERROR",
            "message": "Portföy analizi üretilemedi."}}
        text = r.get_data(as_text=True)
        for leak in ("iç ayrıntı", "secret", "Traceback", "RuntimeError"):
            assert leak not in text


# ── Mimari yasaklar ──────────────────────────────────────────────────

def test_export_module_pure_no_banned_imports():
    tree = ast.parse(Path("portfolio_export.py")
                     .read_text(encoding="utf-8"))
    allowed = {"csv", "io", "json", "typing", "__future__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] in allowed, node.module
        elif isinstance(node, ast.Call) and \
                isinstance(node.func, ast.Name):
            assert node.func.id not in ("open", "eval", "exec",
                                        "__import__", "compile")
    src = Path("portfolio_export.py").read_text(encoding="utf-8")
    for banned in ("datetime", "time.", "uuid", "random", "tempfile",
                   "Path(", "os.", "append_snapshot", "binance",
                   "requests", "socket"):
        assert banned not in src, banned


def test_export_no_calculations():
    tree = ast.parse(Path("portfolio_export.py")
                     .read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.BinOp) or not isinstance(
            node.op, (ast.Mult, ast.Div, ast.Sub)), \
            "export'ta çarpma/bölme/çıkarma yasak"
    src = Path("portfolio_export.py").read_text(encoding="utf-8")
    for banned in ("Decimal", "quantize", "analyze_portfolio",
                   "get_portfolio_analysis", "build_default_providers"):
        assert banned not in src, banned         # Core/Service tekrarı yok


def test_export_no_filesystem_writes(monkeypatch):
    import builtins
    real_open = builtins.open
    writes = []

    def guard(file, mode="r", *a, **k):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            writes.append(str(file))
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr(builtins, "open", guard)
    env = _envelope()
    pex.export_analysis(env, "json")
    pex.export_analysis(env, "csv")
    assert writes == []


def test_backward_compat_automation_export(client, monkeypatch,
                                           tmp_path):
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "state.json"))
    _login(client)
    r = client.get("/api/v1/automation/export/status?format=json")
    assert r.status_code == 200
