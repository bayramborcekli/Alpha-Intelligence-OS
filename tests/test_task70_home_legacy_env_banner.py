"""Task 70 — Eski Binance env isim uyarısı ana panelde de görünsün.

Bitti göstergesi:
- Ana panelin kullandığı /api/operation-control/status yanıtı
  `legacy_env_warnings` alanını döndürür.
- Ana panel JS'i (trading_home.js) sayfa yüklenince uyarıları
  showWarnings ile banner olarak gösterir.
- Banner metni kanonik isme taşıma yönergesini içerir.
- Sır DEĞERİ asla yanıtta yer almaz.
"""
from pathlib import Path

import pytest

import app as app_module
import local_env

ROOT = Path(__file__).resolve().parent.parent
HOME_JS = (ROOT / "static" / "js" / "trading_home.js").read_text(
    encoding="utf-8")

LEGACY_KEYS = ("BINANCE_GLOBAL_API_KEY", "BINANCE_API_KEY",
               "BINANCE_API_Key", "BINANCE_GLOBAL_API_SECRET",
               "BINANCE_API_SECRET", "BINANCE_Secret_Key")
CANONICAL = ("BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key")


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c


def _clear_env(monkeypatch):
    for k in LEGACY_KEYS + CANONICAL:
        monkeypatch.delenv(k, raising=False)


class TestStatusField:
    def test_legacy_only_produces_warning(self, client, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_API_KEY", "sekret-deger-654")
        r = client.get("/api/operation-control/status")
        body = r.get_json()
        warns = body["data"]["legacy_env_warnings"]
        assert warns and any("BINANCE_API_KEY" in w for w in warns)
        # Sır değeri asla yanıtta yer almaz.
        assert "sekret-deger-654" not in r.get_data(as_text=True)

    def test_warning_contains_fix_instruction(self, client, monkeypatch):
        """Banner metni kanonik isme taşıma yönergesini içerir."""
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_API_KEY", "eski")
        body = client.get("/api/operation-control/status").get_json()
        warns = body["data"]["legacy_env_warnings"]
        assert any("BINANCE_GLOBAL_API_Key" in w and "taşıyın" in w
                   for w in warns)

    def test_canonical_filled_no_warning(self, client, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_API_KEY", "eski")
        monkeypatch.setenv("BINANCE_API_SECRET", "eski2")
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "kanonik")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "kanonik2")
        body = client.get("/api/operation-control/status").get_json()
        assert body["data"]["legacy_env_warnings"] == []

    def test_clean_env_no_warning(self, client, monkeypatch):
        _clear_env(monkeypatch)
        body = client.get("/api/operation-control/status").get_json()
        assert body["data"]["legacy_env_warnings"] == []

    def test_matches_local_env_function(self, client, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_GLOBAL_API_SECRET", "x")
        body = client.get("/api/operation-control/status").get_json()
        assert (body["data"]["legacy_env_warnings"]
                == local_env.legacy_name_warnings())


class TestUiWiring:
    def test_home_js_uses_field(self):
        assert "legacy_env_warnings" in HOME_JS
        assert "showWarnings" in HOME_JS

    def test_base_template_has_banner(self):
        base = (ROOT / "templates" / "dash_base.html").read_text(
            encoding="utf-8")
        assert 'id="warn-banner"' in base
        assert "function showWarnings" in base

    def test_home_template_extends_base(self):
        home = (ROOT / "templates" / "trading_home.html").read_text(
            encoding="utf-8")
        assert 'extends "dash_base.html"' in home
