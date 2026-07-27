"""Mission 1800 / Agent 05 — Strategy Intelligence UI testleri.

Sayfa sunucuda Jinja ile render edilir; veri tarayıcıda YALNIZ Agent 04
API'sından (GET /api/v1/strategy/intelligence) çekilir. Testler render
edilen HTML yapısını, sterilliğini ve salt-okunur sınırları doğrular.
"""

from __future__ import annotations

import re

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth

PASSWORD = "strategy-ui-parola-1"
HASH = generate_password_hash(PASSWORD)

TEMPLATE_PATH = "templates/strategy_intelligence.html"
PAGE = "/strategy-intelligence"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB",
                       "/tmp/test_m1800ui_attempts.db")
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


def _page(c):
    _login(c)
    r = c.get(PAGE)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _template_source() -> str:
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


# ── Sayfa yükleme ve kayıt ───────────────────────────────────────────

def test_page_registered_get_only():
    rules = {r.rule: r for r in flask_app.app.url_map.iter_rules()}
    assert PAGE in rules
    assert set(rules[PAGE].methods) <= {"GET", "HEAD", "OPTIONS"}


def test_page_requires_auth(client):
    r = client.get(PAGE)
    assert r.status_code in (302, 401)


def test_page_renders_for_owner(client):
    html = _page(client)
    assert "Strategy Intelligence" in html


def test_nav_link_present(client):
    html = _page(client)
    assert 'href="/strategy-intelligence"' in html


# ── Panel yapısı ─────────────────────────────────────────────────────

def test_required_summary_fields_present(client):
    html = _page(client)
    for elem_id in ("si-status", "si-version", "si-generated",
                    "si-confidence", "si-regime", "si-risk",
                    "si-envelope-meta"):
        assert f'id="{elem_id}"' in html, elem_id


def test_sections_present(client):
    html = _page(client)
    for elem_id in ("si-recs-body", "si-warnings", "si-limitations"):
        assert f'id="{elem_id}"' in html, elem_id


def test_recommendation_columns_complete(client):
    html = _page(client)
    for col in ("Enstrüman", "Aksiyon", "Öncelik", "Güven",
                "Mevcut Ağırlık", "Hedef Ağırlık", "Risk Seviyesi",
                "Neden Kodları", "Beklenen Etki",
                "Geçersizlik Koşulları"):
        assert col in html, col


def test_state_banners_present(client):
    html = _page(client)
    for elem_id in ("si-partial-banner", "si-unavailable-banner",
                    "si-error-banner"):
        assert f'id="{elem_id}"' in html, elem_id
    assert "Strategy unavailable" in html
    assert "Partial data available" in html


def test_advisory_readonly_tag_present(client):
    html = _page(client)
    assert "SALT OKUNUR · TAVSİYE" in html


# ── Sunum kuralları (kaynak denetimi) ────────────────────────────────

def test_only_strategy_api_consumed():
    src = _template_source()
    fetches = re.findall(r'fetch\("([^"]+)"', src)
    assert fetches == ["/api/v1/strategy/intelligence"]


def test_null_renders_as_unknown():
    src = _template_source()
    assert 'null || v === undefined || v === ""' in src.replace(
        "v === ", "v === ") or "Unknown" in src
    m = re.search(r"function txt\(v\) \{(.*?)\}", src, re.S)
    assert m and '"Unknown"' in m.group(1)


def test_empty_recommendations_message():
    src = _template_source()
    assert "No recommendations." in src


def test_no_reordering_or_math_in_js():
    src = _template_source()
    js = src[src.find("<script>"):]
    for banned in (".sort(", ".reverse(", "parseFloat", "parseInt",
                   "Number(", "Math.", "toFixed", "reduce("):
        assert banned not in js, banned


def test_no_identifier_generation_in_js():
    src = _template_source()
    for banned in ("uuid", "crypto.randomUUID", "Math.random",
                   "Date.now", "new Date("):
        assert banned not in src, banned


def test_xss_safe_dom_only():
    """Tüm dinamik içerik textContent ile yazılır; HTML enjekte edilmez."""
    src = _template_source()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                   "document.write", "eval(", 'setAttribute("on'):
        assert banned not in src, banned
    assert "textContent" in src


def test_xss_payload_never_marked_safe():
    src = _template_source()
    assert "| safe" not in src and "|safe" not in src
    assert "autoescape false" not in src


def test_no_execution_controls():
    # Denetim ŞABLON kaynağında yapılır: temel yerleşimin çıkış (logout)
    # formu bu panelin parçası değildir.
    src = _template_source().lower()
    # "AL/SAT" sözcüğü yalnız NEGATİF bildirimde geçer ("... yoktur"),
    # bu yüzden yasak listesinde kontrol öğeleri esas alınır.
    for banned in ("emir ver", 'type="submit"', "<form",
                   "uygula</button", "onayla</button",
                   'action="/api/strategy', "post"):
        assert banned not in src, banned


def test_no_provider_internals_in_template():
    src = _template_source()
    for banned in ("portfolio_service", "strategy_service", "risk_api",
                   "intelligence_service", "binance", "Traceback"):
        assert banned not in src, banned


def test_stable_order_comment_and_direct_iteration():
    src = _template_source()
    assert "list.forEach" in src  # API sırası aynen dolaşılır


# ── Durum semantiği ──────────────────────────────────────────────────

def test_quality_states_handled():
    src = _template_source()
    for state in ("OK", "PARTIAL", "UNAVAILABLE"):
        assert state in src, state
    assert "QUALITY_TR" in src


def test_error_path_sterile():
    src = _template_source()
    assert "renderError" in src
    assert "catch(renderError)" in src


# ── Regresyon uyumluluğu ─────────────────────────────────────────────

def test_portfolio_page_untouched(client):
    _login(client)
    r = client.get("/portfolio-intelligence")
    assert r.status_code == 200
    assert "Portfolio Intelligence" in r.get_data(as_text=True)


def test_page_route_in_intel_allowlist():
    from tests.test_mission1500_1_regression import TestRouteSurface
    assert PAGE in TestRouteSurface.EXPECTED_INTEL_ROUTES


def test_no_write_methods_on_page():
    for rule in flask_app.app.url_map.iter_rules():
        if rule.rule == PAGE:
            assert not ({"POST", "PUT", "DELETE", "PATCH"}
                        & set(rule.methods))
