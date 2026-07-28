"""Task 69 — Eski Binance env isim uyarısı güvenlik sayfasında da görünsün.

Bitti göstergesi:
- /api/v1/audit/summary yanıtı `legacy_env_warnings` alanını döndürür.
- Denetim sayfası yüklenince uyarılar banner olarak görünür;
  kanonik isimler doluyken liste boştur (banner yok).
- Sır DEĞERİ asla yanıtta yer almaz.
"""
from pathlib import Path

import pytest

import app as app_module
import local_env

ROOT = Path(__file__).resolve().parent.parent
AUDIT_HTML = (ROOT / "templates" / "audit.html").read_text(encoding="utf-8")

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


class TestSummaryField:
    def test_legacy_only_produces_warning(self, client, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_API_KEY", "sekret-deger-987")
        r = client.get("/api/v1/audit/summary")
        body = r.get_json()
        warns = body["legacy_env_warnings"]
        assert warns and any("BINANCE_API_KEY" in w for w in warns)
        # Sır değeri asla yanıtta yer almaz.
        assert "sekret-deger-987" not in r.get_data(as_text=True)

    def test_canonical_filled_no_warning(self, client, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_API_KEY", "eski")
        monkeypatch.setenv("BINANCE_API_SECRET", "eski2")
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "kanonik")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "kanonik2")
        body = client.get("/api/v1/audit/summary").get_json()
        assert body["legacy_env_warnings"] == []

    def test_clean_env_no_warning(self, client, monkeypatch):
        _clear_env(monkeypatch)
        body = client.get("/api/v1/audit/summary").get_json()
        assert body["legacy_env_warnings"] == []

    def test_matches_local_env_function(self, client, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("BINANCE_GLOBAL_API_SECRET", "x")
        body = client.get("/api/v1/audit/summary").get_json()
        assert (body["legacy_env_warnings"]
                == local_env.legacy_name_warnings())


class TestUiWiring:
    def test_audit_template_uses_field(self):
        assert "legacy_env_warnings" in AUDIT_HTML
        assert "showWarnings" in AUDIT_HTML

    def test_base_template_has_banner(self):
        base = (ROOT / "templates" / "dash_base.html").read_text(
            encoding="utf-8")
        assert 'id="warn-banner"' in base
        assert "function showWarnings" in base
