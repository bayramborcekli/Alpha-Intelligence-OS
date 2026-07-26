"""Mission 1500.2 / Agent 05 — Workspace UI testleri."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth

PASSWORD = "Workspace-UI-1500!"
TEMPLATE = Path("templates/intelligence_workspace.html").read_text(
    encoding="utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "owner")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH",
                       generate_password_hash(PASSWORD))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-ws-ui")
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


def _page(c):
    _login(c)
    r = c.get("/workspace")
    assert r.status_code == 200
    return r.data.decode()


# ── auth + render ────────────────────────────────────────────────────

def test_anonymous_redirected(client):
    r = client.get("/workspace")
    assert r.status_code in (301, 302, 401)
    if r.status_code in (301, 302):
        assert "/login" in r.headers.get("Location", "")


def test_page_renders(client):
    html = _page(client)
    assert "Intelligence Workspace" in html
    assert "YALNIZCA TAVSİYE" in html


def test_uses_dash_base(client):
    html = _page(client)
    assert 'id="sidebar"' in html  # dash_base iskeleti
    assert "{% extends" in TEMPLATE
    assert 'extends "dash_base.html"' in TEMPLATE


def test_nav_link_present_and_single(client):
    html = _page(client)
    assert html.count('href="/workspace"') == 1
    # 1500.1 nav bağlantısı bozulmadı
    assert 'href="/intelligence"' in html


# ── bölümler ─────────────────────────────────────────────────────────

def test_all_sections_present(client):
    html = _page(client)
    for sec in ("Zaman Çizelgesi", "Snapshot Detayı",
                "İki Snapshot Karşılaştırma", "Tavsiye Geçmişi",
                "Risk Evrimi", "Arama ve Filtreler", "Dışa Aktarım"):
        assert sec in html, sec


def test_export_area_placeholder_only(client):
    html = _page(client)
    # Yalnızca mevcut GET ucuna bağlantı; export mantığı yok
    assert "/api/workspace/timeline" in html
    # Yeni export ucu/mantığı yok — yalnızca mevcut GET ucuna bağlantı
    assert "/api/workspace/export" not in TEMPLATE
    assert "download" not in TEMPLATE.lower()
    assert "yer tutucu" in html.lower()


# ── API entegrasyonu ─────────────────────────────────────────────────

def test_only_workspace_api_endpoints_used():
    urls = set(re.findall(r'["\'](/api/[^"\']*)["\']', TEMPLATE))
    allowed_prefixes = ("/api/workspace/",)
    for u in urls:
        assert u.startswith(allowed_prefixes), u
    for ep in ("timeline", "snapshot/", "compare", "recommendations",
               "risk-evolution", "search"):
        assert any(ep in u for u in urls) or f"/api/workspace/{ep}" in \
            TEMPLATE, ep


def test_no_direct_service_or_timeline_access():
    assert "intelligence_timeline" not in TEMPLATE
    assert "intelligence_workspace_service" not in TEMPLATE


def test_only_get_requests():
    # fetch çağrılarında method belirtilmez (GET) — yazma metodu yok
    for bad in ("method", "POST", "PUT", "PATCH", "DELETE",
                "XMLHttpRequest"):
        assert bad not in TEMPLATE, bad


# ── XSS / CSP ────────────────────────────────────────────────────────

def test_xss_helpers_defined_and_used():
    for helper in ("function esc(", "function vy(", "function txt(",
                   "function failState("):
        assert helper in TEMPLATE, helper


def test_innerhtml_only_from_escaped_builders():
    """Her innerHTML ataması esc()/vy() zincirli üreticiden gelmeli."""
    for m in re.finditer(r"\.innerHTML\s*=\s*(.+)", TEMPLATE):
        rhs = m.group(1)
        assert ("vy(" in rhs or "esc(" in rhs or "evet(" in rhs
                or '"' == rhs.strip()[0] or "sterile" in rhs
                or rhs.strip().startswith(("rows.length", "items.length",
                                           "pts.length", "diffs.length"))), rhs


def test_snapshot_json_rendered_via_textcontent():
    assert "pre.textContent = JSON.stringify" in TEMPLATE


def test_no_dangerous_js_apis():
    for bad in ("document.write", "eval(", "new Function",
                "insertAdjacentHTML", "outerHTML", "|safe"):
        assert bad not in TEMPLATE, bad


def test_no_external_resources():
    for bad in ("http://", "https://", "cdn.", "<script src",
                "<link rel=\"stylesheet\" href=\"http",
                "@import", "integrity="):
        assert bad not in TEMPLATE, bad


# ── bilinmeyen değer / sterile hata ──────────────────────────────────

def test_unknown_renders_dash():
    assert '? "—"' in TEMPLATE  # vy() ve txt() null → "—"


def test_veri_yok_for_missing_compare_side(client):
    html = _page(client)
    assert "Veri Yok" in html or "Veri Yok" in TEMPLATE


def test_no_zero_fallbacks():
    assert re.search(r'\|\|\s*0[^a-zA-Z0-9]', TEMPLATE) is None
    assert '? "0"' not in TEMPLATE


def test_sterile_error_ux():
    # API hata mesajı bile ekrana taşınmaz; sabit metin kullanılır
    assert "Veri alınamadı." in TEMPLATE
    assert "d.error" not in TEMPLATE  # hata nesnesi hiç okunmaz
    for bad in ("stacktrace", "stack trace", "Traceback"):
        assert bad not in TEMPLATE


# ── salt-okunur / işlem düğmesi yok ──────────────────────────────────

def test_no_trade_or_mutation_controls(client):
    html = _page(client).lower()
    for bad in ("emir ver", "al</button", "sat</button", "pozisyon aç",
                "onayla", "sil</button", "güncelle</button", "delete",
                "işlem yap"):
        assert bad not in html, bad
    # Şablonda form yok (dash_base'deki logout formu hariç sayılır)
    assert TEMPLATE.count("<form") == 0


def test_buttons_are_readonly_queries():
    labels = re.findall(r"<button[^>]*>([^<]+)</button>", TEMPLATE)
    assert set(labels) == {"Görüntüle", "Karşılaştır", "Ara"}
    for b in re.findall(r"<button[^>]*>", TEMPLATE):
        assert 'type="button"' in b  # submit yok


def test_search_datetime_converted_local_to_utc():
    """datetime-local UTC ISO'ya çevrilir; sabit +00:00 eki YOKTUR."""
    assert "toUtcIso" in TEMPLATE
    assert "toISOString()" in TEMPLATE
    assert ':00+00:00"' not in TEMPLATE and ":59+00:00" not in TEMPLATE
    # Bitiş sınırı dakika sonuna kadar DAHİLDİR
    assert ":59.999" in TEMPLATE


# ── mobil / responsive ───────────────────────────────────────────────

def test_mobile_data_l_pattern():
    assert TEMPLATE.count("data-l=") >= 15
    assert "attr(data-l)" in TEMPLATE
    assert "@media" in TEMPLATE


# ── 1500.1 UI regresyonu ─────────────────────────────────────────────

def test_intelligence_page_unchanged(client):
    _login(client)
    r = client.get("/intelligence")
    assert r.status_code == 200
    html = r.data.decode()
    assert "Intelligence Merkezi" in html
    assert 'href="/workspace"' in html  # nav genişledi, bozulmadı
