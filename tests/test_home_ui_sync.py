"""UI senkron sözleşmesi — /home widget'ları tek kanonik kaynaktan.

Kabul kriterleri (saha bulgusu: üst şerit 5, tablo 3, aktif işlem 0
ama runtime'da açık pozisyon):
- İzlenen Piyasalar / AI Durumu / üst şerit Evren = effective_symbols.
- Aktif İşlemler = Paper ledger (state.json position) — Windows
  Runtime kartıyla aynı kaynak.
- Tek /api/operation-control/overview snapshot'ında çelişki yok.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import app as app_module  # noqa: E402
import universe_manager as um  # noqa: E402

JS = (ROOT / "static" / "js" / "trading_home.js").read_text(
    encoding="utf-8")


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c


@pytest.fixture()
def universe5(tmp_path, monkeypatch):
    """Etkin evren = 3 taban + 2 dinamik (HYPE/ESP) + açık pozisyon."""
    cfgp = tmp_path / "config.json"
    base_cfg = {"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                "interval": "1m", "trend_interval": "15m",
                "minimum_score": 60}
    cfgp.write_text(json.dumps(base_cfg), encoding="utf-8")
    statep = tmp_path / "state.json"
    statep.write_text(json.dumps({
        "balance": 1000.0,
        "position": {"symbol": "ESPUSDT", "side": "SHORT",
                     "entry": 0.06067, "stop": 0.062,
                     "target": 0.058, "quantity": 5000.0,
                     "risk_usdt": 10.0,
                     "opened_at": "2026-07-30T09:00:00+00:00"},
    }), encoding="utf-8")
    monkeypatch.setattr(um, "RUNTIME_STORE_PATH",
                        tmp_path / "universe_runtime.json")
    um._save_runtime({"dynamic_symbols": ["HYPEUSDT", "ESPUSDT"]})
    monkeypatch.setattr(app_module, "CONFIG_PATH", cfgp)
    monkeypatch.setattr(app_module, "STATE_PATH", statep)
    return base_cfg


def _overview(client):
    r = client.get("/api/operation-control/overview")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    return body["data"]


class TestSingleSnapshotConsistency:
    def test_products_match_effective_universe(self, client,
                                               universe5):
        data = _overview(client)
        syms = [p["symbol"] for p in data["products"]]
        assert syms == ["BTCUSDT", "ETHUSDT", "SOLUSDT",
                        "HYPEUSDT", "ESPUSDT"]  # evren 5 ise 5

    def test_open_position_visible_in_positions(self, client,
                                                universe5):
        data = _overview(client)
        assert len(data["positions"]) == 1
        pos = data["positions"][0]
        assert pos["symbol"] == "ESPUSDT"
        assert pos["side"] == "SHORT"
        assert pos["execution_mode"] == "PAPER"

    def test_position_id_survives_close_lookup(self, client,
                                               universe5):
        """Close ucu position_id.upper() ile arar — id zaten büyük
        harf olmalı, yoksa kapatma UNKNOWN_TARGET döner."""
        data = _overview(client)
        pid = data["positions"][0]["position_id"]
        assert pid == pid.upper() == "PAPER-ESPUSDT"
        # UNKNOWN_TARGET olmamalı (doğrulama katmanı başka nedenle
        # reddedebilir ama pozisyon BULUNMALI)
        r = client.post(
            f"/api/operation-control/positions/{pid}/close",
            json={"reason": "t", "confirm_phrase": "x",
                  "idempotency_key": "k1"})
        body = r.get_json() or {}
        assert "UNKNOWN_TARGET" not in json.dumps(body)

    def test_no_contradiction_within_snapshot(self, client,
                                              universe5):
        """Tek snapshot içinde: pozisyon sembolü evrende olmalı,
        universe_size = products sayısı olmalı."""
        data = _overview(client)
        product_syms = {p["symbol"] for p in data["products"]}
        for pos in data["positions"]:
            assert pos["symbol"] in product_syms
        r = client.get("/api/paper/state")
        st = r.get_json()
        assert st["universe_size"] == len(data["products"])
        assert st["open_paper_position_count"] == \
            len(data["positions"])

    def test_snapshot_version_present(self, client, universe5):
        assert "snapshot_version" in _overview(client)


class TestPositionsMatchPaperLedger:
    def test_no_position_means_zero(self, client, universe5,
                                    tmp_path, monkeypatch):
        statep = tmp_path / "state2.json"
        statep.write_text(json.dumps({"balance": 1000.0,
                                      "position": None}),
                          encoding="utf-8")
        monkeypatch.setattr(app_module, "STATE_PATH", statep)
        assert _overview(client)["positions"] == []


class TestClientSingleSource:
    """JS polling tüm widget'ları TEK overview yanıtından beslemeli."""

    def test_js_uses_overview_only(self):
        assert "/api/operation-control/overview" in JS
        for old in ("/api/operation-control/positions\"",
                    "/api/operation-control/products\"",
                    "/api/operation-control/orders\"",
                    "/api/operation-control/signals\""):
            assert old not in JS, f"ayrı uç hâlâ kullanılıyor: {old}"

    def test_all_products_rendered(self):
        # DISABLED semboller tablodan düşmez — evren sayısı tutar
        assert "İzleniyor (giriş kapalı)" in JS
