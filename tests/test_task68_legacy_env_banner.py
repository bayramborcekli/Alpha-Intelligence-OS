"""Task 68 — Eski Binance env isim uyarısı panelde de görünsün.

Bitti göstergesi:
- legacy_name_warnings() çıktısı /api/accounts yanıtında
  `legacy_env_warnings` alanı olarak döner ve UI banner'ında gösterilir.
- Kanonik isimler doluyken liste boştur (banner yok).
- Sır DEĞERİ asla yanıtta yer almaz.
"""
from pathlib import Path

import pytest

import accounts_registry as reg
import app as app_module
import local_env

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "js" / "my_accounts.js").read_text(
    encoding="utf-8")

LEGACY_KEYS = ("BINANCE_GLOBAL_API_KEY", "BINANCE_API_KEY",
               "BINANCE_API_Key", "BINANCE_GLOBAL_API_SECRET",
               "BINANCE_API_SECRET", "BINANCE_Secret_Key")
CANONICAL = ("BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "REGISTRY_PATH",
                        tmp_path / "accounts.json")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c


def _clear_env(monkeypatch):
    for k in LEGACY_KEYS + CANONICAL:
        monkeypatch.delenv(k, raising=False)


class TestApiField:
    def test_legacy_only_produces_warning(self, client, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_API_KEY", "sekret-deger-123")
        r = client.get("/api/accounts")
        body = r.get_json()
        warns = body["data"]["legacy_env_warnings"]
        assert warns and any("BINANCE_API_KEY" in w for w in warns)
        # Sır değeri asla yanıtta yer almaz.
        assert "sekret-deger-123" not in r.get_data(as_text=True)

    def test_canonical_filled_no_warning(self, client, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_API_KEY", "eski")
        monkeypatch.setenv("BINANCE_API_SECRET", "eski2")
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "kanonik")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "kanonik2")
        body = client.get("/api/accounts").get_json()
        assert body["data"]["legacy_env_warnings"] == []

    def test_clean_env_no_warning(self, client, monkeypatch):
        _clear_env(monkeypatch)
        body = client.get("/api/accounts").get_json()
        assert body["data"]["legacy_env_warnings"] == []

    def test_matches_local_env_function(self, client, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_GLOBAL_API_SECRET", "x")
        body = client.get("/api/accounts").get_json()
        assert (body["data"]["legacy_env_warnings"]
                == local_env.legacy_name_warnings())


class TestUiWiring:
    def test_js_calls_show_warnings_with_field(self):
        assert "legacy_env_warnings" in JS
        assert "showWarnings" in JS

    def test_base_template_has_banner(self):
        base = (ROOT / "templates" / "dash_base.html").read_text(
            encoding="utf-8")
        assert 'id="warn-banner"' in base
        assert "function showWarnings" in base
