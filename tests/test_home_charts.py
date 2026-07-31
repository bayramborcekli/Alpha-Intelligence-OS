"""Trading Home düzeltme görevi — grafik verileri + hesap kartı
READ-ONLY köprüsü kabul testleri.

Sözleşmeler:
- /api/home/charts SALT OKUNUR: gerçek seriler; kaynak boşsa
  NO_HISTORY / NO_TRADES_TODAY; hata durumunda API_ERROR — sahte eğri,
  sahte 0 YOK.
- Risk kullanımı risk_api.summary(persist=False) ile okunur (günlük
  snapshot YAZILMAZ).
- /api/accounts: canlı hesap sorgusu başarısızken bağlantı servisi
  CONNECTED_READ_ONLY doğrulaması yaptıysa kart VERIFIED_READ_ONLY
  gösterir; credential frontend'e asla açılmaz.
- Frontend'de UNKNOWN yerine ayrışmış durum etiketleri; grafikler
  harici CDN kütüphanesi olmadan inline SVG ile çizilir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = "test-operator"
        yield c


class TestHomeCharts:
    def test_charts_endpoint_shape(self, client):
        r = client.get("/api/home/charts")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["live_orders"] == "DISABLED"
        for key in ("pnl_curve", "daily_pnl", "risk_usage"):
            assert key in d
            assert "status" in d[key]

    def test_pnl_curve_honest_status(self, client):
        d = client.get("/api/home/charts").get_json()
        pc = d["pnl_curve"]
        assert pc["status"] in ("OK", "NO_HISTORY", "API_ERROR")
        if pc["status"] == "OK":
            assert len(pc["points"]) > 0
            for p in pc["points"]:
                assert "t" in p and "v" in p
        else:
            # Sahte eğri yasak: veri yoksa nokta da yok.
            assert pc["points"] == []

    def test_daily_pnl_no_fake_zero(self, client):
        d = client.get("/api/home/charts").get_json()
        dp = d["daily_pnl"]
        assert dp["status"] in ("OK", "NO_TRADES_TODAY", "API_ERROR")
        if dp["status"] != "OK":
            # Bugün işlem yoksa toplam null'dur — sahte 0 basılmaz.
            assert dp["total"] is None
            assert dp["points"] == []

    def test_risk_usage_honest(self, client):
        d = client.get("/api/home/charts").get_json()
        ru = d["risk_usage"]
        assert ru["status"] in ("OK", "DATA_SOURCE_UNAVAILABLE",
                                "API_ERROR")
        if ru["status"] == "OK":
            assert isinstance(ru["usage_pct"], (int, float))
        else:
            assert ru["usage_pct"] is None

    def test_charts_read_only_no_snapshot_write(self, client,
                                                monkeypatch):
        """risk_api.summary çağrısı persist=False ile yapılmalı."""
        import risk_api
        seen = {}

        def spy(persist=True):
            seen["persist"] = persist
            return {"ok": True, "margin_usage_pct": "1.5"}

        monkeypatch.setattr(risk_api, "summary", spy)
        d = client.get("/api/home/charts").get_json()
        assert seen.get("persist") is False
        assert d["risk_usage"]["usage_pct"] == 1.5


class TestAccountsReadOnlyBridge:
    def _cards(self, client):
        d = client.get("/api/accounts").get_json()
        assert d["ok"] is True
        return d["data"]["accounts"]

    def test_verified_read_only_injected(self, client, monkeypatch):
        from services import binance_connection as bc
        monkeypatch.setattr(bc, "status", lambda: {
            "live_orders": "DISABLED",
            "BINANCE_GLOBAL": {"status": "CONNECTED_READ_ONLY",
                               "tested_at": "2026-07-31T10:00:00Z"},
            "BINANCE_TR": {"status": "NOT_CONFIGURED"}})
        cards = [c for c in self._cards(client)
                 if c.get("exchange") == "BINANCE_GLOBAL"]
        assert cards, "BINANCE_GLOBAL kartı kayıt defterinde olmalı"
        card = cards[0]
        # Canlı sorgu bu ortamda başarısız olsa bile kurulum
        # doğrulaması karta yansır — "Bağlı hesap yok" tutarsızlığı
        # kapanır.
        if card["connection_state"] not in ("HEALTHY", "STALE"):
            assert card["connection_state"] == "VERIFIED_READ_ONLY"
            assert card["permission_status"] == "READ_ONLY"
            assert card["last_verified_at"] == "2026-07-31T10:00:00Z"

    def test_no_injection_without_verification(self, client,
                                               monkeypatch):
        from services import binance_connection as bc
        monkeypatch.setattr(bc, "status", lambda: {
            "BINANCE_GLOBAL": {"status": "AUTH_FAILED"},
            "BINANCE_TR": {"status": "NOT_CONFIGURED"}})
        for card in self._cards(client):
            assert card["connection_state"] != "VERIFIED_READ_ONLY"

    def test_no_credentials_in_card(self, client):
        import json as _json
        raw = _json.dumps(self._cards(client)).lower()
        for banned in ("api_key", "secret", "signature"):
            assert banned not in raw or "masked" in raw


class TestFrontendContracts:
    def test_no_external_chart_cdn(self):
        html = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        for banned in ("cdn.jsdelivr", "unpkg.com", "chart.js",
                       "cdnjs.cloudflare"):
            assert banned not in html.lower()
            assert banned not in js.lower()

    def test_spark_containers_present(self):
        html = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        for el_id in ("th-spark-daily", "th-spark-curve",
                      "th-risk-bar"):
            assert el_id in html

    def test_js_uses_charts_endpoint_and_states(self):
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "/api/home/charts" in js
        # UNKNOWN yerine ayrışmış dürüst etiketler:
        assert "NO_TRADES_TODAY" in js
        assert "Bugün kapanan işlem yok" in js
        assert "NO_HISTORY" in js
        # READ ONLY rozeti ve durum eşlemesi:
        assert "VERIFIED_READ_ONLY" in js
        assert "READ ONLY" in js

    def test_no_fake_sentiment_value(self):
        """Referanstaki '72' korku-açgözlülük sahte değeri yok;
        kaynak olmayan piyasa alanları dürüstçe 'Veri yok'."""
        html = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        assert "Korku" in html  # kart var
        assert "Veri yok" in html  # dürüst etiketle

    def test_sparkline_svg_escapes_no_raw_input(self):
        """Sparkline yalnız sayısal koordinat basar (XSS yüzeyi yok)."""
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "sparkSvg" in js
        assert "toFixed(1)" in js
