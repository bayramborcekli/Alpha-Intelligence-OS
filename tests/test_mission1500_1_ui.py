"""Mission 1500.1 / Agent 08 — Intelligence UI testleri."""

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import dashboard_api as dapi

PASSWORD = "intel-ui-parola-1"
HASH = generate_password_hash(PASSWORD)


@pytest.fixture
def client(monkeypatch):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m15001ui_attempts.db")
    monkeypatch.setenv("ALPHA_ENABLE_INTELLIGENCE", "true")
    auth._ATTEMPTS.clear()
    dapi.invalidate_caches()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True
        dapi.invalidate_caches()


def _login(c):
    return c.post("/api/v1/auth/login",
                  json={"username": "sahip", "password": PASSWORD})


def _page(c):
    _login(c)
    r = c.get("/intelligence")
    assert r.status_code == 200
    return r.get_data(as_text=True)


class TestAuth:
    def test_anonymous_redirected_to_login(self, client):
        r = client.get("/intelligence")
        assert r.status_code == 302 and "/login" in r.headers["Location"]

    def test_authenticated_renders(self, client):
        html = _page(client)
        assert "Intelligence Merkezi" in html


class TestLayout:
    def test_executive_header_preserved(self, client):
        html = _page(client)
        # Mevcut executive üst çubuğu ve PnL şeridi öğeleri korunur
        for eid in ("xh-risk", "xh-upnl", "xh-pt"):
            assert eid in html, eid

    def test_navigation_home_visible(self, client):
        html = _page(client)
        assert 'href="/"' in html and "Başlangıç" in html  # Ana Sayfa
        assert 'href="/intelligence"' in html
        assert 'aria-current="page"' in html

    def test_mobile_markup(self, client):
        html = _page(client)
        assert 'name="viewport"' in html
        assert "menu-btn" in html                 # mobil menü düğmesi
        assert "auto-fill,minmax(" in html        # duyarlı grid

    def test_all_sections_present(self, client):
        html = _page(client)
        for anchor in ("c-status",        # genel durum
                       "c-margin",        # portfolio summary
                       "h-rx",            # risk explanation
                       "h-ins",           # insights
                       "h-rec",           # recommendations
                       "cf-HIGH",         # confidence göstergeleri
                       "Kanıtlar",        # evidence ayrıntıları
                       "h-fresh",         # data freshness
                       "partial-banner",  # partial-data uyarısı
                       "c-upd"):          # son güncelleme
            assert anchor in html, anchor


class TestSafety:
    def test_unknown_shown_as_dash(self, client):
        html = _page(client)
        assert ">—<" in html                      # veri yoksa "—"

    def test_confidence_has_text_not_only_color(self, client):
        html = _page(client)
        for label in ("Yüksek güven", "Orta güven", "Yetersiz veri"):
            assert label in html, label

    def test_advisory_only_visible(self, client):
        html = _page(client)
        assert "YALNIZCA TAVSİYE" in html
        assert "hiçbir alım-satım kararı vermez" in html

    def test_no_trade_buttons(self, client):
        html = _page(client).lower()
        for banned in (">al<", ">sat<", "emir ver", "buy</button>",
                       "sell</button>"):
            assert banned not in html, banned
        # İçerikte işlem formu yok (yalnızca temel şablondaki çıkış
        # formu bulunur — salt-okunur görüntüleme)
        assert html.count("<form") <= 1
        assert "/logout" in html or html.count("<form") == 0

    def test_no_secret_or_trace(self, client):
        html = _page(client)
        for banned in ("BINANCE_API", "API_SECRET", "PASSWORD_HASH",
                       "Traceback", "SESSION_SECRET"):
            assert banned not in html, banned

    def test_xss_escape_helper_used(self, client):
        html = _page(client)
        assert "&amp;" in html and "&lt;" in html  # esc() tanımlı
        # Tüm dinamik alanlar vy()/esc() üzerinden geçer
        assert "function esc(" in html and "function vy(" in html
        assert "innerHTML = empty(" in html


class TestAccessibility:
    def test_labels(self, client):
        html = _page(client)
        assert 'aria-labelledby="h-rx"' in html
        assert 'aria-labelledby="h-ins"' in html
        assert 'aria-labelledby="h-rec"' in html
        assert 'role="alert"' in html
        assert 'aria-live="polite"' in html
        assert 'scope="col"' in html


class TestFeatureFlag:
    def test_page_renders_when_flag_off(self, client, monkeypatch):
        # Sayfa açılır; veri istemcide kapalı-bayrak yanıtıyla gizlenir
        monkeypatch.setenv("ALPHA_ENABLE_INTELLIGENCE", "false")
        html = _page(client)
        assert "disabled-banner" in html
        assert "Intelligence özelliği kapalı" in html


class TestReviewFixes:
    def test_undefined_safe_helper_and_fail_state(self, client):
        html = _page(client)
        assert "function txt(" in html            # null/undefined/"" → —
        assert "failState(" in html               # tutarlı hata durumu
        assert "error-banner" in html

    def test_mobile_data_labels(self, client):
        html = _page(client)
        assert "data-l='Kaynak'" in html
        assert "data-l='Durum'" in html
        assert "data-l='Değer'" in html
