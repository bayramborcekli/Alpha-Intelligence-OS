"""Mission 1600 / Agent 05 — Automation UI testleri.

Panel sunucuda Jinja ile render edilir; veri tarayıcıda yalnız Agent 04
API'larından (status/run) çekilir. Testler render edilen HTML'in yapısını,
sterilliğini ve güvenlik sınırlarını doğrular.
"""

import re

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth

PASSWORD = "automation-ui-parola-1"
HASH = generate_password_hash(PASSWORD)

TEMPLATE_PATH = "templates/automation.html"


@pytest.fixture
def client(monkeypatch, tmp_path):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m1600ui_attempts.db")
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "automation_state.json"))
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
    return r


def _page(c):
    _login(c)
    r = c.get("/automation")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _template_source():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


class TestAuth:
    def test_anonymous_redirected_to_login(self, client):
        r = client.get("/automation")
        assert r.status_code == 302 and "/login" in r.headers["Location"]

    def test_authenticated_renders(self, client):
        html = _page(client)
        # Konsolidasyon misyonu: sayfa "Analiz Zamanlayıcısı"dır ve
        # trading botunu temsil etmediği açıkça yazılıdır.
        assert "Analiz Zamanlayıcısı" in html


class TestLayout:
    def test_extends_dashboard_shell_with_nav(self, client):
        html = _page(client)
        # dash_base iskeleti korunur; automation nav'da aktif işaretlenir
        assert 'id="sidebar"' in html
        assert 'href="/automation"' in html
        assert 'aria-current="page"' in html

    def test_status_cards_present_with_placeholders(self, client):
        html = _page(client)
        for el_id in ("a-enabled", "a-state", "a-running",
                      "a-interval", "a-next"):
            assert 'id="%s"' % el_id in html
        # Bilinmeyen değer 0 değil "—" olarak başlar
        assert "—" in html

    def test_last_run_table_and_loading_state(self, client):
        html = _page(client)
        assert 'id="last-run-body"' in html
        assert "Yükleniyor…" in html

    def test_banners_present_but_hidden_by_default(self, client):
        html = _page(client)
        assert 'id="au-disabled-banner"' in html
        assert 'id="au-error-banner"' in html
        assert html.count("display:none") >= 2

    def test_responsive_layout_primitives(self, client):
        html = _page(client)
        # Izgara auto-fill ile daralır; taşan tablo yatay kaydırılır
        assert "auto-fill" in html
        assert "overflow-x:auto" in html
        assert 'name="viewport"' in html


class TestActions:
    def test_run_and_refresh_buttons_only(self, client):
        html = _page(client)
        assert 'id="btn-run"' in html and 'id="btn-refresh"' in html
        # Enable/disable API'ı yok — ölü düğme render edilmez
        low = html.lower()
        assert "btn-enable" not in low and "btn-disable" not in low
        assert "/api/automation/enable" not in html
        assert "/api/automation/disable" not in html

    def test_run_uses_csrf_token_from_meta(self, client):
        html = _page(client)
        assert 'meta[name="csrf-token"]' in html
        assert "X-CSRFToken" in html
        assert 'name="csrf-token"' in html  # dash_base meta gerçekten var

    def test_only_agent04_endpoints_used(self):
        src = _template_source()
        called = set(re.findall(r'fetch\("([^"]+)"', src))
        # MASTER INTEGRATION FIX: kanonik Analysis Scheduler bloğu
        # üst şeritle AYNI snapshot'ı (/api/paper/state) okur —
        # bilinçli genişletme; legacy blok yalnız Agent 04 uçlarını
        # kullanmaya devam eder.
        assert called == {"/api/automation/status",
                          "/api/automation/run", "/api/paper/state"}

    def test_no_websocket_or_sse(self):
        src = _template_source()
        assert "WebSocket" not in src
        assert "EventSource" not in src
        # Otomatik yenileme yalnız status polling'idir
        assert "setInterval(refresh" in src


class TestStateRendering:
    def test_all_state_styles_defined(self, client):
        html = _page(client)
        for cls in ("st-disabled", "st-scheduled", "st-running",
                    "st-succeeded", "st-failed", "st-interrupted",
                    "st-unknown"):
            assert cls in html

    def test_state_labels_turkish(self, client):
        html = _page(client)
        for label in ("Kapalı", "Zamanlandı", "Çalışıyor",
                      "Başarılı", "Başarısız", "Bilinmiyor"):
            assert label in html

    def test_error_codes_sterile_only(self):
        src = _template_source()
        # Hata kodu sözlüğü yalnız sterile kodlar içerir; serbest metin yok
        assert "DUPLICATE_RUN" in src and "TIMEOUT" in src
        for banned in ("traceback", "Traceback", "str(e)", "exc_info",
                       "stack"):
            assert banned not in src


class TestSecurityBoundaries:
    def test_no_business_logic_in_template(self):
        src = _template_source()
        # UI hesap yapmaz, snapshot yazmaz, servis çağırmaz
        for banned in ("append_snapshot", "IntelligenceService",
                       "get_summary", "innerHTML = d.", "eval("):
            assert banned not in src

    def test_dynamic_values_use_textcontent(self):
        src = _template_source()
        # API'dan gelen değerler textContent ile basılır (XSS koruması)
        assert "td.textContent" in src
        # innerHTML yalnız sabit/badge içeriği için kullanılır
        # stBadge textContent ile kurulur ve sınıfı whitelist'lidir; onun
        # dışında hiçbir innerHTML API verisi içeremez
        for m in re.findall(r'\.innerHTML\s*=\s*(.+);', src):
            assert "d." not in m or m.startswith("stBadge("), m

    def test_no_secret_leak_in_page(self, client):
        html = _page(client)
        low = html.lower()
        for banned in ("api_key", "api_secret", "binance_api",
                       "password_hash", "session_secret"):
            assert banned not in low

    def test_page_is_deterministic(self, client):
        _login(client)
        a = client.get("/automation").get_data(as_text=True)
        b = client.get("/automation").get_data(as_text=True)
        # CSRF token oturum içinde sabittir; sayfa deterministik render edilir
        strip = re.compile(r'name="csrf-token" content="[^"]*"')
        assert strip.sub("", a) == strip.sub("", b)


class TestApiContractAlignment:
    """UI'nin kullandığı alan adları status API sözleşmesiyle eşleşir."""

    STATUS_FIELDS = ("enabled", "interval_minutes", "state", "running",
                     "last_run_started_at", "last_run_finished_at",
                     "last_run_status", "last_error_code",
                     "last_snapshot_recorded", "next_due")

    def test_template_reads_real_status_fields(self):
        src = _template_source()
        for field in self.STATUS_FIELDS:
            assert "d.%s" % field in src or '"%s"' % field in src

    def test_run_response_fields(self):
        src = _template_source()
        for field in ("appended", "error_code"):
            assert field in src
