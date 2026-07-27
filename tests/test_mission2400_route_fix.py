"""Mission 2400 — Varsayılan açılış rotası: her yol Trading Home'a çıkar.

Kilitlenen davranışlar:
- `/` her zaman `/home`'a yönlendirir (eski Başlangıç kabuğu değil).
- Giriş sonrası her zaman `/home` açılır; önceki rota GERİ YÜKLENMEZ
  (login'e `next` parametresi taşınmaz, taşınsa da yok sayılır).
- Bilinmeyen/geçersiz HTML rotası `/home`'a yönlendirir; bilinmeyen
  API rotası sterile 404 JSON zarfı döner.
- Eski Başlangıç kabuğu `/start` altında menüden erişilebilir kalır.
"""
from __future__ import annotations

import pytest

import app as flask_app


@pytest.fixture()
def client():
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        with c.session_transaction() as s:
            s["logged_in"] = True
            s["username"] = "owner"
        yield c


@pytest.fixture()
def anon_client():
    flask_app.app.config["TESTING"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True


class TestDefaultLanding:
    def test_root_redirects_to_trading_home(self, client):
        r = client.get("/")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/home")

    def test_root_lands_on_trading_home(self, client):
        r = client.get("/", follow_redirects=True)
        assert r.status_code == 200
        assert r.request.path == "/home"
        assert 'id="th-topbar"' in r.get_data(as_text=True)

    def test_legacy_shell_reachable_at_start(self, client):
        r = client.get("/start")
        assert r.status_code == 200
        assert "Başlangıç" in r.get_data(as_text=True)

    def test_sidebar_links_start_not_root(self, client):
        html = client.get("/home").get_data(as_text=True)
        assert 'href="/start"' in html


class TestNoRouteRestore:
    def test_login_redirect_has_no_next_param(self, anon_client):
        r = anon_client.get("/portfolio")
        assert r.status_code == 302
        assert "next=" not in r.headers["Location"]

    def test_login_page_ignores_next_param(self, anon_client):
        r = anon_client.get("/login?next=/operation-center")
        html = r.get_data(as_text=True)
        # Gizli next alanı her zaman /home'dur; önceki rota taşınmaz.
        assert 'value="/home"' in html
        assert "/operation-center" not in html


class TestUnknownRouteFallback:
    def test_unknown_html_route_redirects_home(self, client):
        r = client.get("/no-such-page-xyz")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/home")

    def test_unknown_api_route_sterile_404(self, client):
        r = client.get("/api/no-such-endpoint-xyz")
        assert r.status_code == 404
        body = r.get_json()
        assert body and "error" in str(body).lower() or "hata" in str(body).lower()
        # Yığın izi / iç yol sızıntısı yok.
        text = r.get_data(as_text=True)
        assert "Traceback" not in text
