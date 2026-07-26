"""Mission 1500.2 / Agent 06 — Workspace Export testleri."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import intelligence_timeline as tl

PASSWORD = "Workspace-Export-1500!"


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
        "portfolio_summary": {"total_value": Decimal("123.45"),
                              "note": "=SUM(A1:A2)"},
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
    monkeypatch.setenv("SESSION_SECRET", "test-secret-export")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", str(tmp_path / "att.json"))
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    c = flask_app.app.test_client()
    yield c


def _login(c):
    r = c.post("/api/v1/auth/login",
               json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 200


BASE = "/api/workspace/export"
ENDPOINTS = (f"{BASE}/timeline", f"{BASE}/snapshot/1",
             f"{BASE}/compare?a=1&b=2", f"{BASE}/recommendations",
             f"{BASE}/risk-evolution", f"{BASE}/search")


def _fmt(url, fmt):
    return url + ("&" if "?" in url else "?") + "format=" + fmt


# ── auth + tüm uçlar + alias ─────────────────────────────────────────

def test_anonymous_401(client):
    for ep in ENDPOINTS:
        assert client.get(ep).status_code == 401, ep
        assert client.get(
            ep.replace("/api/", "/api/v1/", 1)).status_code == 401, ep


def test_all_endpoints_json_and_csv_with_aliases(client):
    _login(client)
    for ep in ENDPOINTS:
        for path in (ep, ep.replace("/api/", "/api/v1/", 1)):
            for fmt, mime in (("json", "application/json"),
                              ("csv", "text/csv")):
                r = client.get(_fmt(path, fmt))
                assert r.status_code == 200, (path, fmt)
                assert mime in r.headers["Content-Type"], (path, fmt)
                cd = r.headers["Content-Disposition"]
                assert cd.startswith('attachment; filename="workspace_')
                assert cd.endswith(f'.{fmt}"')
                assert r.headers["Cache-Control"] == "no-store, private"


def test_default_format_is_json(client):
    _login(client)
    r = client.get(f"{BASE}/timeline")
    assert "application/json" in r.headers["Content-Type"]
    assert r.headers["Content-Disposition"].endswith('.json"')


# ── içerik doğruluğu ─────────────────────────────────────────────────

def test_timeline_json_envelope(client):
    _login(client)
    body = json.loads(client.get(_fmt(f"{BASE}/timeline", "json")).data
                      .decode("utf-8"))
    assert body["ok"] is True and body["read_only"] is True
    assert body["total"] == 2
    assert [e["id"] for e in body["entries"]] == [1, 2]


def test_timeline_csv_rows(client):
    _login(client)
    text = client.get(_fmt(f"{BASE}/timeline", "csv")).data.decode(
        "utf-8-sig")
    lines = [l for l in text.split("\r\n") if l]
    assert lines[0].startswith("id,generated_at,status,partial")
    assert len(lines) == 3
    assert lines[1].startswith("1,") and lines[2].startswith("2,")
    assert "PARTIAL" in lines[2]


def test_snapshot_export_decimal_string_and_formula_guard(client):
    _login(client)
    raw = client.get(_fmt(f"{BASE}/snapshot/1", "json")).data.decode()
    assert '"123.45"' in raw  # Decimal string korunur
    csv_text = client.get(_fmt(f"{BASE}/snapshot/1", "csv")).data.decode(
        "utf-8-sig")
    assert "123.45" in csv_text
    # Hiçbir hücre formül önekiyle başlamaz (enjeksiyon koruması)
    import csv as _csv
    for row in _csv.reader(csv_text.splitlines()[1:]):
        for cell in row:
            assert not cell.startswith(("=", "+", "@", "\t")), cell
    # Hücre düzeyinde nötralizasyon
    import workspace_export_api as wsx
    assert wsx._cell("=SUM(A1:A2)") == "'=SUM(A1:A2)"
    assert wsx._cell("@cmd") == "'@cmd"
    assert wsx._cell("-12.5") == "-12.5"   # sayısal string DEĞİŞMEZ
    assert wsx._cell(None) == "—"
    assert wsx._cell(True) == "true"


def test_compare_export_deterministic_field_order(client):
    _login(client)
    j1 = client.get(_fmt(f"{BASE}/compare?a=1&b=2", "json")).data
    j2 = client.get(_fmt(f"{BASE}/compare?a=1&b=2", "json")).data
    assert j1 == j2
    body = json.loads(j1.decode())
    fields_json = [d["field"] for d in body["differences"]]
    csv_lines = [l for l in client.get(
        _fmt(f"{BASE}/compare?a=1&b=2", "csv")).data.decode("utf-8-sig")
        .split("\r\n") if l]
    assert csv_lines[0] == "field,change,a,b"
    import csv as _csv
    fields_csv = [row[0] for row in _csv.reader(csv_lines[1:])]
    assert fields_csv == fields_json  # aynı deterministik sıra


def test_recommendations_and_risk_evolution_csv(client):
    _login(client)
    rec = client.get(_fmt(f"{BASE}/recommendations", "csv")).data.decode(
        "utf-8-sig")
    assert rec.startswith("code,occurrences")
    assert "DATA_REVIEW" in rec and "NO_ACTION_NEEDED" in rec
    risk = client.get(_fmt(f"{BASE}/risk-evolution", "csv")).data.decode(
        "utf-8-sig")
    lines = [l for l in risk.split("\r\n") if l]
    assert lines[0].startswith("snapshot_id,generated_at,risk_score")
    assert lines[1].split(",")[2] == "87"


def test_search_export_filters(client):
    _login(client)
    body = json.loads(client.get(
        _fmt(f"{BASE}/search?status=PARTIAL", "json")).data.decode())
    assert body["total"] == 1
    csv_text = client.get(
        _fmt(f"{BASE}/search?status=PARTIAL", "csv")).data.decode(
        "utf-8-sig")
    lines = [l for l in csv_text.split("\r\n") if l]
    assert len(lines) == 2 and "PARTIAL" in lines[1]


def test_unknown_values_dash_in_csv(client, tmp_path, monkeypatch):
    hist = tmp_path / "h2.jsonl"
    monkeypatch.setenv("ALPHA_INTELLIGENCE_HISTORY_PATH", str(hist))
    tl.append_snapshot(_snap("11", risk_summary=None, status=None), hist)
    _login(client)
    risk = client.get(_fmt(f"{BASE}/risk-evolution", "csv")).data.decode(
        "utf-8-sig")
    row = [l for l in risk.split("\r\n") if l][1]
    assert ",—," in row  # bilinmeyen → "—", asla 0 değil
    assert ",0," not in row


# ── hata durumları ───────────────────────────────────────────────────

def test_snapshot_404(client):
    _login(client)
    r = client.get(_fmt(f"{BASE}/snapshot/99", "csv"))
    assert r.status_code == 404
    body = r.get_json()
    assert body["error"]["code"] == "SNAPSHOT_NOT_FOUND"
    assert body["error"]["message"] == "İşlem tamamlanamadı"
    assert r.headers["Cache-Control"] == "no-store, private"


def test_compare_404(client):
    _login(client)
    assert client.get(_fmt(f"{BASE}/compare?a=1&b=99", "json")
                      ).status_code == 404


def test_invalid_params_400(client):
    _login(client)
    for url in (f"{BASE}/timeline?format=xml",
                f"{BASE}/timeline?format=pdf",
                f"{BASE}/timeline?limit=-1",
                f"{BASE}/snapshot/abc", f"{BASE}/snapshot/0",
                f"{BASE}/compare?a=1",
                f"{BASE}/compare?a=x&b=2&format=csv",
                f"{BASE}/search?partial=belki",
                f"{BASE}/search?date=bozuk"):
        r = client.get(url)
        assert r.status_code == 400, url
        assert r.get_json()["error"]["code"] == "INVALID_PARAMETER"
        assert r.headers["Cache-Control"] == "no-store, private"


def test_provider_error_sterile_200(client, monkeypatch):
    _login(client)
    import intelligence_workspace_service as s
    monkeypatch.setattr(s, "get_timeline",
                        lambda **kw: s._sterile("WORKSPACE_TIMELINE_ERROR"))
    r = client.get(_fmt(f"{BASE}/timeline", "csv"))
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"]["message"] == "İşlem tamamlanamadı"
    assert "Traceback" not in r.data.decode()


# ── determinizm / salt-okunurluk / sızıntı ──────────────────────────

def test_determinism_all(client):
    _login(client)
    for ep in ENDPOINTS:
        for fmt in ("json", "csv"):
            assert client.get(_fmt(ep, fmt)).data == \
                client.get(_fmt(ep, fmt)).data, (ep, fmt)


def test_export_does_not_write_history(client, tmp_path):
    _login(client)
    import os
    hist = os.environ["ALPHA_INTELLIGENCE_HISTORY_PATH"]
    before = open(hist, "rb").read()
    for ep in ENDPOINTS:
        client.get(_fmt(ep, "csv"))
    assert open(hist, "rb").read() == before


def test_write_methods_405(client):
    _login(client)
    for ep in (f"{BASE}/timeline", f"{BASE}/snapshot/1",
               f"{BASE}/compare", "/api/v1/workspace/export/timeline"):
        for m in ("post", "put", "patch", "delete"):
            assert getattr(client, m)(ep).status_code == 405, (ep, m)


def test_export_routes_get_only():
    for rule in flask_app.app.url_map.iter_rules():
        if "/workspace/export/" in rule.rule:
            assert set(rule.methods) <= {"GET", "HEAD", "OPTIONS"}


def test_no_secret_leak(client, monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "FAKE-EXPORT-LEAK-999")
    _login(client)
    for ep in ENDPOINTS:
        for fmt in ("json", "csv"):
            text = client.get(_fmt(ep, fmt)).data.decode("utf-8-sig")
            assert "FAKE-EXPORT-LEAK-999" not in text
            for bad in ("api_key", "password_hash", "Traceback"):
                assert bad not in text, (ep, fmt, bad)


def test_module_uses_service_not_timeline():
    import inspect
    import workspace_export_api as wsx
    src = inspect.getsource(wsx)
    assert "intelligence_timeline" not in src
    assert "intelligence_workspace_service" in src
    assert "open(" not in src  # dosyaya doğrudan erişim yok
