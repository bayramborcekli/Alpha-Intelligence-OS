"""Mission 1400.5 — Yönetici Çalışma Alanı testleri."""
import json
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import dashboard_api as dapi
import ledger_api as la
import executive_api as xa

PASSWORD = "exec-test-parola-1"
HASH = generate_password_hash(PASSWORD)


@pytest.fixture
def client(monkeypatch):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14005_attempts.db")
    auth._ATTEMPTS.clear()
    dapi.invalidate_caches()
    la.invalidate_ledger_caches()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True
        dapi.invalidate_caches()
        la.invalidate_ledger_caches()


def _login(c):
    return c.post("/api/v1/auth/login",
                  json={"username": "sahip", "password": PASSWORD})


ALL_PAGES = ["/", "/overview", "/portfolio",
             "/ledger", "/audit", "/reports"]


class TestAuth:
    def test_summary_requires_auth(self, client):
        assert client.get("/api/v1/executive/summary").status_code == 401

    def test_summary_get_only(self, client):
        _login(client)
        for m in ("post", "put", "patch", "delete"):
            r = getattr(client, m)("/api/v1/executive/summary")
            assert r.status_code == 405, m


class TestSummaryModel:
    def test_shape_and_honesty(self, client):
        _login(client)
        d = client.get("/api/v1/executive/summary").get_json()
        assert d["ok"] is True
        assert d["live_execution"] is False
        assert d["mode"]  # PAPER
        p = d["performance"]
        # Doğrulanmamış alanlar ASLA uydurulmaz → null
        assert p["realized_pnl_usdt"] is None
        assert p["daily_pnl_pct"] is None
        assert p["total_pnl_pct"] is None
        assert p["pnl_7d_usdt"] is None
        assert p["pnl_30d_usdt"] is None
        # 1400.6 sonrası: risk_level doğrulanmış deterministik motor
        # skorundan gelir (veya kaynak yoksa null) — asla tahmin değildir.
        assert p["risk_level"] is None or "/100" in p["risk_level"]
        assert p["portfolio_total_label"] in (
            "Global Spot (USDT)", "Global Spot (USDT, kısmi)")
        s = d["status_bar"]
        assert "binance_futures" not in s  # Spot-only: kalıntı yok
        for k in ("binance_global", "binance_tr",
                  "ledger", "audit", "risk_engine", "health"):
            assert s[k] in ("Bağlı", "Kısmi", "Bağlantı Yok"), k
        assert s["health"] == "Bağlı"

    def test_source_failure_isolated(self, client, monkeypatch):
        # Borsa kaynakları çökse bile özet ayakta kalır, değerler null olur
        def boom(*a, **k):
            return {"ok": False, "error": {"code": "X", "message": "y"}}
        monkeypatch.setattr(xa.dapi, "global_spot_account", boom)
        monkeypatch.setattr(xa.dapi, "tr_account", boom)
        _login(client)
        d = client.get("/api/v1/executive/summary").get_json()
        assert d["ok"] is True
        p = d["performance"]
        assert p["portfolio_total_usdt"] is None
        assert p["unrealized_pnl_usdt"] is None
        assert p["open_position_count"] is None
        assert p["open_order_count"] is None
        s = d["status_bar"]
        assert s["binance_global"] == "Bağlantı Yok"
        assert s["binance_tr"] == "Bağlantı Yok"
        assert s["health"] == "Bağlı"

    def test_no_secret_leak(self, client):
        _login(client)
        body = client.get("/api/v1/executive/summary"
                          ).get_data(as_text=True).lower()
        for w in ("api_key", "secret", "password", "hash", "token",
                  "signature"):
            assert w not in body, w


class TestTopbarEverywhere:
    def test_topbar_on_all_pages(self, client):
        _login(client)
        for page in ALL_PAGES:
            html = client.get(page, follow_redirects=True
                              ).get_data(as_text=True)
            assert 'id="exec-topbar"' in html, page
            assert "🏠 Ana Ekran" in html, page
            assert 'href="/overview"' in html, page
            assert "Canlı Emir: KAPALI" in html, page

    def test_strip_and_status_labels(self, client):
        _login(client)
        html = client.get("/ledger").get_data(as_text=True)
        for label in ("Toplam Portföy", "Gerçekleşmemiş PnL",
                      "Gerçekleşmiş PnL", "Günlük K/Z %", "Toplam K/Z %",
                      "7 Günlük", "30 Günlük", "Açık Pozisyon", "Açık Emir",
                      "Risk Seviyesi", "Binance Global",
                      "Binance TR", "Defter", "Denetim", "Risk Motoru",
                      "Sağlık", "Son Güncelleme"):
            assert label in html, label
        assert "Binance Futures" not in html  # Spot-only

    def test_no_action_controls_in_topbar(self, client):
        _login(client)
        html = client.get("/overview").get_data(as_text=True)
        start = html.index('id="exec-topbar"')
        end = html.index("</script>", start)
        bar = html[start:end].lower()
        for banned in ("<form", "type=\"submit\"", "order\"", "cancel",
                       "close-", "leverage", "transfer", "withdraw"):
            assert banned not in bar, banned
        assert "<button" not in bar  # üst çubukta hiçbir eylem düğmesi yok


class TestQuickActions:
    def test_quick_cards_on_overview(self, client):
        _login(client)
        html = client.get("/overview").get_data(as_text=True)
        assert "Hızlı Erişim" in html
        for href in ("/portfolio", "/ledger",
                     "/audit", "/reports", "/risk"):   # /risk 1400.6'da aktif
            assert f'class="quick-card" href="{href}"' in html, href
        for gone in ("/positions", "/orders"):  # Spot-only: kaldırıldı
            assert f'href="{gone}"' not in html, gone
        for disabled in ("Dry Run", "Sistem Sağlığı", "Ayarlar"):
            assert disabled in html, disabled
        assert html.count('aria-disabled="true"') >= 3


class TestWriteSafety:
    def test_no_new_write_routes(self, client):
        for rule in flask_app.app.url_map.iter_rules():
            if "executive" in rule.rule:
                assert rule.methods <= {"GET", "HEAD", "OPTIONS"}, rule.rule

    def test_write_counters_zero(self, client):
        _login(client)
        client.get("/api/v1/executive/summary")
        assert all(v == 0 for v in dapi.WRITE_COUNTERS.values())
