"""Mission 1700 / Agent 05 — Portfolio Intelligence UI testleri.

Sayfa sunucuda Jinja ile render edilir; veri tarayıcıda YALNIZ Agent 04
API'sından (GET /api/v1/portfolio/intelligence) çekilir. Testler render
edilen HTML yapısını, sterilliğini ve salt-okunur sınırları doğrular.
"""

from __future__ import annotations

import re

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth

PASSWORD = "portfolio-ui-parola-1"
HASH = generate_password_hash(PASSWORD)

TEMPLATE_PATH = "templates/portfolio_intelligence.html"
PAGE = "/portfolio-intelligence"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB",
                       "/tmp/test_m1700ui_attempts.db")
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
    assert rules[PAGE].methods - {"HEAD", "OPTIONS"} == {"GET"}


def test_page_requires_login(client):
    r = client.get(PAGE)
    assert r.status_code in (302, 401)


def test_page_loads_with_expected_sections(client):
    html = _page(client)
    for anchor in ("pi-status", "pi-health", "pi-nav", "pi-cash",
                   "pi-gross", "pi-net", "pi-perf-body",
                   "pi-alloc-body", "pi-pos-body", "pi-hhi", "pi-top",
                   "pi-topshare", "pi-eff", "pi-u-net", "pi-u-dd",
                   "pi-u-conc", "pi-breach", "pi-health-body",
                   "pi-src-body", "pi-envelope-meta"):
        assert f'id="{anchor}"' in html, anchor


def test_nav_link_present(client):
    html = _page(client)
    assert 'href="/portfolio-intelligence"' in html
    assert "aria-current" in html  # aktif sayfa işaretlenir


# ── API tüketimi ve katman sınırı ────────────────────────────────────

def test_ui_consumes_only_agent04_api():
    src = _template_source()
    apis = set(re.findall(r"""fetch\(["']([^"']+)["']""", src))
    assert apis == {"/api/v1/portfolio/intelligence"}
    for banned in ("portfolio_service", "portfolio_intelligence.py",
                   "analyze_portfolio", "get_portfolio_analysis",
                   "build_default_providers", "risk_api",
                   "intelligence_service", "append_snapshot"):
        assert banned not in src, banned


def test_no_post_or_mutation_from_ui():
    src = _template_source()
    assert "method=\"post\"" not in src.lower()
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{verb}"' not in src and f"'{verb}'" not in src


# ── Görsel durumlar ──────────────────────────────────────────────────

def test_visual_states_present():
    src = _template_source()
    assert "Yükleniyor" in src                       # Loading
    for state in ("OK", "PARTIAL", "UNAVAILABLE"):
        assert f'"{state}"' in src or state in src   # domain durumları
    assert 'id="pi-partial-banner"' in src
    assert 'id="pi-unavailable-banner"' in src
    assert 'id="pi-error-banner"' in src
    assert "renderError" in src                      # API Error yolu


def test_unavailable_state_not_healthy_looking():
    src = _template_source()
    assert "KULLANILAMAZ" in src
    assert "st-unavailable" in src
    # UNAVAILABLE banner'ı hata renginde (err sınıfı)
    assert re.search(r'class="pi-banner err" id="pi-unavailable-banner"',
                     src)
    # UNAVAILABLE dalı: önce clearAllValues, portföy render'ı YOK,
    # erken return VAR — payload null'luğuna güvenilmez.
    m = re.search(r'if \(status === "UNAVAILABLE"\) \{(.*?)\n    \}',
                  src, re.S)
    assert m, "UNAVAILABLE dalı bulunamadı"
    branch = m.group(1)
    assert "clearAllValues()" in branch
    assert "renderPortfolio" not in branch
    assert "return;" in branch
    # temizleme, meta/kaynak render'ından ÖNCE gelir
    assert branch.index("clearAllValues()") < \
        branch.index("renderEnvelopeMeta")


def test_error_path_clears_all_values():
    src = _template_source()
    m = re.search(r"function renderError\(\) \{(.*?)\n  \}", src, re.S)
    assert m, "renderError bulunamadı"
    body = m.group(1)
    assert "clearAllValues()" in body
    assert 'clearBody("pi-src-body"' in body   # kaynak tablosu da boş
    assert "renderPortfolio" not in body
    # bilinmeyen/yeni durum kodları da hata yoluna düşer
    assert 'status !== "OK" && status !== "PARTIAL"' in src
    # clearAllValues her değer alanını ve tabloyu kapsar
    m2 = re.search(r"function clearAllValues\(\) \{(.*?)\n  \}", src,
                   re.S)
    assert m2
    cav = m2.group(1)
    assert "resetFields()" in cav
    for table in ("pi-perf-body", "pi-alloc-body", "pi-pos-body",
                  "pi-health-body"):
        assert table in cav, table
    assert "pi-alloc-meta" in cav


def test_null_rendering_never_zero():
    src = _template_source()
    # txt() null/undefined/boş → "—"; 0'a çevirme yok
    assert 'v === null || v === undefined || v === ""' in src
    assert '? "—"' in src
    assert "|| 0" not in src
    assert '?? 0' not in src


# ── Zarf alanları ────────────────────────────────────────────────────

def test_envelope_meta_fields_displayed():
    src = _template_source()
    for field in ("analysis_version", "generated_at", "read_only",
                  "advisory_only"):
        assert field in src, field


def test_display_model_fields_bound():
    src = _template_source()
    for field in ("portfolio_health_score", "nav_usdt", "cash_usdt",
                  "gross", "net", "realized_pnl", "unrealized_pnl",
                  "total_fees", "drawdown_pct", "forecast", "assets",
                  "cash_weight_pct", "unallocated_or_unknown_pct",
                  "hhi", "top_symbol", "top_share_pct",
                  "effective_positions", "net_exposure_util_pct",
                  "drawdown_util_pct", "concentration_util_pct",
                  "limits_breached", "components"):
        assert field in src, field


def test_source_status_table_bound():
    src = _template_source()
    assert "renderSources" in src
    for field in ("status", "freshness", "code"):
        assert field in src
    # deterministik sıralama: kaynak adları sort edilir
    assert ".sort()" in src


# ── Salt-okunur sunum ────────────────────────────────────────────────

def test_no_execution_controls(client):
    html = _page(client)
    lowered = html.lower()
    for banned in ("buy", "sell", " al</button", "sat</button",
                   "order-entry", "execute"):
        assert banned not in lowered, banned
    # şablonda buton yalnız yok — form/input alanı da yok
    src = _template_source()
    assert "<button" not in src
    assert "<input" not in src
    assert "<select" not in src
    assert "<textarea" not in src
    assert "contenteditable" not in src


def test_advisory_read_only_visible(client):
    html = _page(client)
    assert "SALT OKUNUR" in html
    assert "TAVSİYE" in html
    assert "Canlı emir: DEVRE DIŞI" in html  # mevcut üst şerit korunur


# ── Determinizm ve sterilite ─────────────────────────────────────────

def test_no_client_side_calculation_or_randomness():
    src = _template_source()
    for banned in ("Math.random", "Date.now", "new Date(",
                   "toLocaleString", "toFixed", "parseFloat",
                   "Number("):
        assert banned not in src, banned
    # payload mutasyonu yok: env/portföy nesnesine atama yapılmaz
    assert not re.search(r"env\.\w+\s*=", src)
    assert not re.search(r"\bp\.\w+\s*=[^=]", src)


def test_rendering_deterministic_same_template(client):
    a = _page(client)
    b = _page(client)
    # CSRF meta gibi oturum-bağımlı satırlar dışında şablon sabittir
    strip = re.compile(r'name="csrf-token" content="[^"]*"')
    assert strip.sub("", a) == strip.sub("", b)


def test_no_provider_internals_or_secret_markers(client):
    html = _page(client)
    for leak in ("Traceback", "BINANCE_API", "api_key", "apikey",
                 "/home/runner", "Exception"):
        assert leak not in html, leak


def test_template_source_sterile():
    src = _template_source()
    for leak in ("secret", "password", "traceback"):
        assert leak not in src.lower(), leak


# ── Geriye dönük uyumluluk ───────────────────────────────────────────

def test_existing_pages_untouched(client):
    _login(client)
    for path in ("/automation", "/portfolio", "/risk"):
        assert client.get(path).status_code == 200
