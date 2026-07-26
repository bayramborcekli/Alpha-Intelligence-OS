"""Mission 1500.2 / Agent 07 — Workspace Güvenlik Doğrulaması.

Yeni özellik eklenmez; yalnızca güvenlik sınırları doğrulanır:
auth, HTTP metot, CSRF/rate-limit bypass yokluğu, başlıklar, XSS,
path traversal, parametre doğrulama, sterile hata, exchange/ağ
sınırları ve geçmiş (history) bütünlüğü.
"""

from __future__ import annotations

import inspect
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import intelligence_timeline as tl
import intelligence_workspace_service as wss
import workspace_export_api as wsx

PASSWORD = "Workspace-Sec-1500!"

WS_MODULES = (tl, wss, wsx)
WS_TEMPLATE = Path("templates/intelligence_workspace.html").read_text(
    encoding="utf-8")

XSS = "<script>alert(1)</script>"


def _snap(hour="10", **over):
    base = {
        "generated_at": f"2026-07-26T{hour}:00:00+00:00",
        "status": "OK",
        "partial": False,
        "freshness": [],
        "insights": [{"code": XSS, "confidence": "HIGH",
                      "title": XSS}],
        "recommendations": [{"code": "NO_ACTION_NEEDED", "priority": 99,
                             "confidence": "MEDIUM"}],
        "warnings": [],
        "portfolio_summary": {"total_value": Decimal("55.10")},
        "risk_summary": {"score": 42, "status": "SAGLIKLI",
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
    tl.append_snapshot(_snap("09", status="PARTIAL", partial=True), hist)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "owner")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH",
                       generate_password_hash(PASSWORD))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-sec-07")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", str(tmp_path / "att.json"))
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    yield flask_app.app.test_client()


def _login(c):
    r = c.post("/api/v1/auth/login",
               json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 200


def _ws_rules():
    return [r for r in flask_app.app.url_map.iter_rules()
            if "/workspace" in r.rule]


API_EPS = ("/api/workspace/timeline", "/api/workspace/snapshot/1",
           "/api/workspace/compare?a=1&b=2",
           "/api/workspace/recommendations",
           "/api/workspace/risk-evolution", "/api/workspace/search",
           "/api/workspace/export/timeline",
           "/api/workspace/export/snapshot/1",
           "/api/workspace/export/compare?a=1&b=2",
           "/api/workspace/export/recommendations",
           "/api/workspace/export/risk-evolution",
           "/api/workspace/export/search")


# ── 1-2. Authentication ──────────────────────────────────────────────

def test_all_api_endpoints_require_auth(client):
    for ep in API_EPS:
        for p in (ep, ep.replace("/api/", "/api/v1/", 1)):
            assert client.get(p).status_code == 401, p


def test_page_requires_auth_redirect(client):
    r = client.get("/workspace")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers.get("Location", "")


def test_workspace_not_in_security_gate_exemptions():
    src = inspect.getsource(flask_app._security_gate)
    assert "workspace" not in src.lower()


# ── 3. HTTP metot sınırları ─────────────────────────────────────────

def test_url_map_workspace_get_only():
    rules = _ws_rules()
    assert rules, "workspace rotaları bulunamadı"
    for r in rules:
        assert set(r.methods) <= {"GET", "HEAD", "OPTIONS"}, r.rule


def test_write_methods_405_even_authenticated(client):
    _login(client)
    for ep in ("/api/workspace/timeline", "/api/workspace/export/timeline",
               "/workspace"):
        for m in ("post", "put", "patch", "delete"):
            assert getattr(client, m)(ep).status_code == 405, (ep, m)


def test_head_and_options_allowed(client):
    _login(client)
    assert client.head("/api/workspace/timeline").status_code == 200
    assert client.options("/api/workspace/timeline").status_code == 200


# ── 4-5. CSRF / rate-limit bypass yokluğu ───────────────────────────

def test_no_new_csrf_exemptions():
    """@csrf.exempt yalnızca önceden var olan login ucundadır."""
    src = Path("app.py").read_text(encoding="utf-8")
    exempt_positions = [m.start() for m in
                        re.finditer(r"@csrf\.exempt", src)]
    assert len(exempt_positions) == 1
    after = src[exempt_positions[0]:exempt_positions[0] + 200]
    assert "api_v1_login" in after


def test_no_rate_limit_bypass_in_workspace_code():
    for mod in WS_MODULES:
        src = inspect.getsource(mod)
        assert "check_rate_limit" not in src
        assert "_ATTEMPTS" not in src


# ── 6. Güvenlik başlıkları ──────────────────────────────────────────

def test_headers_no_store_and_global_security_headers(client):
    _login(client)
    for ep in API_EPS:
        r = client.get(ep)
        assert r.headers["Cache-Control"] == "no-store, private", ep
        assert r.headers.get("X-Frame-Options") == "DENY", ep
        assert "Content-Security-Policy" in r.headers, ep
        assert r.headers.get("X-Content-Type-Options"), ep


def test_workspace_page_no_store(client):
    """Kimlik doğrulamalı /workspace HTML'i önbelleğe alınmaz."""
    _login(client)
    r = client.get("/workspace")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store, private"
    assert r.headers.get("X-Frame-Options") == "DENY"


def test_export_headers(client):
    _login(client)
    for fmt, mime in (("json", "application/json"), ("csv", "text/csv")):
        r = client.get(f"/api/workspace/export/timeline?format={fmt}")
        assert mime in r.headers["Content-Type"]
        assert r.headers["Content-Disposition"].startswith(
            'attachment; filename="workspace_')


# ── 7. XSS ──────────────────────────────────────────────────────────

def test_autoescape_not_disabled():
    for t in Path("templates").glob("*.html"):
        text = t.read_text(encoding="utf-8")
        assert "autoescape false" not in text.lower(), t.name
        assert "{% autoescape off" not in text.lower(), t.name
        assert "| safe" not in text and "|safe" not in text, t.name


def test_no_external_scripts_in_workspace_template():
    for bad in ("http://", "https://", "<script src", "cdn."):
        assert bad not in WS_TEMPLATE, bad


def test_api_returns_xss_payload_json_encoded_not_html(client):
    """API, XSS yükünü veri olarak döner; Content-Type HTML değildir."""
    _login(client)
    r = client.get("/api/workspace/snapshot/1")
    assert "application/json" in r.headers["Content-Type"]
    body = r.get_json()
    # veri bozulmaz (sözleşme) — ama HTML olarak sunulmaz
    assert body["snapshot"]["insights"][0]["code"] == XSS


def test_ui_escapes_before_innerhtml():
    """Şablonda innerHTML'e giden her dinamik değer kaçış zincirindedir."""
    for m in re.finditer(r"\.innerHTML\s*=\s*(.+)", WS_TEMPLATE):
        rhs = m.group(1).strip()
        assert (rhs.startswith('"') or "vy(" in rhs or "esc(" in rhs
                or "evet(" in rhs or "sterile" in rhs
                or rhs.startswith(("rows.length", "items.length",
                                   "pts.length", "diffs.length"))), rhs
    assert "pre.textContent = JSON.stringify" in WS_TEMPLATE


# ── 8. Path traversal / dosya erişimi ───────────────────────────────

def test_no_user_controlled_paths_in_routes(client):
    _login(client)
    # Traversal denemeleri: reddedilir, dosya içeriği sızmaz
    for url in ("/api/workspace/snapshot/..%2F..%2Fetc%2Fpasswd",
                "/api/workspace/export/snapshot/..%2Fsecret"):
        r = client.get(url)
        assert r.status_code in (400, 404), url
        text = r.data.decode()
        assert "root:" not in text and "Traceback" not in text
    # Bilinmeyen "path" parametresi ETKİSİZDİR — yanıt değişmez
    base = client.get("/api/workspace/timeline").data
    assert client.get("/api/workspace/timeline?path=/etc/passwd"
                      ).data == base
    exp = client.get("/api/workspace/export/timeline?format=csv").data
    assert client.get("/api/workspace/export/timeline?path=../x&format=csv"
                      ).data == exp
    assert b"root:" not in base


def test_history_path_not_request_controlled():
    """Dosya yolu yalnızca sunucu ortam değişkeninden gelir."""
    src_routes = inspect.getsource(flask_app)
    start = src_routes.index("Mission 1500.2: Workspace Read-Only API")
    end = src_routes.index("def api_executive_summary")
    section = src_routes[start:end]
    assert "path=" not in section.replace("history_path", "") or \
        "request.args.get(\"path\"" not in section
    assert 'request.args.get("path")' not in section
    assert "ALPHA_INTELLIGENCE_HISTORY_PATH" not in section  # servis işi
    assert "request.args" not in inspect.getsource(wsx)
    assert "request" not in [n for n in dir(wsx)]


# ── 9. Parametre doğrulama → sterile 400 ────────────────────────────

@pytest.mark.parametrize("url", [
    "/api/workspace/timeline?limit=abc",
    "/api/workspace/timeline?limit=-1",
    "/api/workspace/timeline?offset=1e9",
    "/api/workspace/snapshot/0",
    "/api/workspace/snapshot/-5",
    "/api/workspace/snapshot/true",
    "/api/workspace/compare?a=1",
    "/api/workspace/compare?a=0&b=2",
    "/api/workspace/compare?a=%3Cscript%3E&b=2",
    "/api/workspace/search?date=';DROP TABLE--",
    "/api/workspace/search?partial=evet",
    "/api/workspace/search?advisory_only=YES",
    "/api/workspace/export/timeline?format=xml",
    "/api/workspace/export/timeline?format=%3Cscript%3E",
    "/api/workspace/export/snapshot/1?format=pdf",
    "/api/workspace/export/compare?a=1&b=y&format=json",
    "/api/workspace/export/search?date=bozuk&format=csv",
])
def test_invalid_params_sterile_400(client, url):
    _login(client)
    r = client.get(url)
    assert r.status_code == 400, url
    body = r.get_json()
    assert body["error"]["code"] == "INVALID_PARAMETER"
    text = r.data.decode()
    assert "<script>" not in text          # yansıtılmış XSS yok
    assert "Traceback" not in text


def test_status_confidence_free_text_no_reflection_unescaped(client):
    _login(client)
    r = client.get("/api/workspace/search?status=" + XSS)
    assert r.status_code == 200
    assert "application/json" in r.headers["Content-Type"]
    assert r.get_json()["total"] == 0  # eşleşme yok, yansıtma yok


# ── 10. Sterile hata gövdesi ────────────────────────────────────────

def test_error_bodies_sterile(client, monkeypatch):
    _login(client)
    def boom(**kw):
        raise RuntimeError("SECRET-PATH /home/x .env BINANCE_API_KEY")
    monkeypatch.setattr(wss.timeline, "load_history",
                        lambda *a, **k: boom())
    for ep in ("/api/workspace/timeline",
               "/api/workspace/export/timeline?format=csv"):
        r = client.get(ep)
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is False
        assert body["error"]["message"] == "İşlem tamamlanamadı"
        text = r.data.decode()
        for bad in ("SECRET-PATH", "/home/", ".env", "BINANCE",
                    "RuntimeError", "Traceback"):
            assert bad not in text, bad


def test_404_body_sterile(client):
    _login(client)
    r = client.get("/api/workspace/snapshot/999")
    assert r.status_code == 404
    body = r.get_json()
    assert body["error"] == {"code": "SNAPSHOT_NOT_FOUND",
                             "message": "İşlem tamamlanamadı"}


def test_no_secret_leak_live(client, monkeypatch):
    for k, v in (("BINANCE_API_KEY", "LEAK-A"),
                 ("BINANCE_API_SECRET", "LEAK-B"),
                 ("SESSION_SECRET", "test-secret-sec-07")):
        monkeypatch.setenv(k, v)
    _login(client)
    for ep in API_EPS:
        text = client.get(ep).data.decode("utf-8-sig")
        assert "LEAK-A" not in text and "LEAK-B" not in text, ep


# ── 11. Exchange / ağ sınırları ─────────────────────────────────────

def test_no_exchange_or_network_in_workspace_modules():
    banned = ("binance", "exchange_", "requests.", "urllib",
              "http.client", "socket", "websocket", "openai",
              "anthropic")
    for mod in WS_MODULES:
        src = inspect.getsource(mod).lower()
        for b in banned:
            assert b not in src, (mod.__name__, b)


def test_no_network_imports_in_workspace_modules():
    import sys
    for name in ("intelligence_timeline",
                 "intelligence_workspace_service",
                 "workspace_export_api"):
        mod = sys.modules[name]
        for attr in vars(mod).values():
            modname = getattr(attr, "__module__", "") or ""
            assert not modname.startswith(("requests", "urllib3",
                                           "binance")), (name, attr)


def test_ui_fetches_only_relative_workspace_urls():
    for m in re.finditer(r'fetch\(\s*"([^"]+)"', WS_TEMPLATE):
        assert m.group(1).startswith("/api/workspace/"), m.group(1)
    assert 'getJSON("/api/workspace/' in WS_TEMPLATE


# ── 12. Geçmiş bütünlüğü ────────────────────────────────────────────

def test_reads_do_not_modify_history(client, tmp_path):
    _login(client)
    import os
    hist = Path(os.environ["ALPHA_INTELLIGENCE_HISTORY_PATH"])
    before = hist.read_bytes()
    for ep in API_EPS:
        client.get(ep)
        client.get(ep.replace("/api/", "/api/v1/", 1))
    client.get("/workspace")
    assert hist.read_bytes() == before


def test_service_and_export_have_no_write_calls():
    for mod in (wss, wsx):
        src = inspect.getsource(mod)
        for bad in ("append_snapshot", "build_record", 'open(',
                    ".write(", "os.remove", "os.unlink", "truncate"):
            assert bad not in src, (mod.__name__, bad)


def test_timeline_append_only_contract_intact():
    src = inspect.getsource(tl)
    # Dosya yalnızca "a" (append) ve "r" (read) modlarında açılır
    modes = re.findall(r'\.open\(\s*["\']([^"\']+)["\']', src)
    assert modes and set(modes) <= {"a", "r"}, modes


# ── Determinizm / Decimal (güvenlik sözleşmesi regresyonu) ──────────

def test_decimal_string_and_determinism(client):
    _login(client)
    r1 = client.get("/api/workspace/snapshot/1")
    assert '"55.10"' in r1.data.decode()
    for ep in ("/api/workspace/timeline",
               "/api/workspace/export/timeline?format=csv"):
        assert client.get(ep).data == client.get(ep).data, ep
