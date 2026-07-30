"""Yetim / eksik aktif pozisyon koruması (ONDOUSDT vakası).

Güvenceler:
1. Restart sonrası sağlıklı pozisyon eksiksiz hydrate edilir (OPEN).
2. Yetim (trades'te kapanışı olan) kayıt aktif listede görünmez ve
   ORPHAN_POSITION audit kaydı yazılır.
3. Eksik veriyle pozisyon 'Yönetiliyor' (OPEN/ACTIVE) gösterilmez —
   INCOMPLETE_POSITION_DATA / STALE_POSITION dürüst durum kodları.
4. Eksik fiyat/miktarla otomatik veya manuel exit YAPILMAZ.
5. LIVE ORDERS DISABLED korunur.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_model as dm  # noqa: E402


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) -
            timedelta(hours=hours_ago)).isoformat()


@pytest.fixture()
def appmod(tmp_path, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "POSITION_AUDIT_PATH",
                        tmp_path / "audit.jsonl")
    return appmod


# ── Legacy state.json pozisyon sınıflandırması ─────────────────────

class TestLegacyClassification:
    def test_healthy_position_hydrates_open(self, appmod):
        pos = {"symbol": "ONDOUSDT", "side": "LONG", "entry": 0.95,
               "quantity": 100.0, "opened_at": _iso(0.5)}
        c = appmod._classify_legacy_position(pos, {"trades": []})
        assert c == {"status": "OPEN", "entry": 0.95,
                     "quantity": 100.0}

    def test_alternate_keys_hydrate(self, appmod):
        # entry/quantity yoksa entry_price/qty'den hydrate edilir.
        pos = {"symbol": "ONDOUSDT", "entry_price": 0.95, "qty": 100,
               "opened_at": _iso(0.5)}
        c = appmod._classify_legacy_position(pos, {})
        assert c["status"] == "OPEN"
        assert c["entry"] == 0.95 and c["quantity"] == 100.0

    def test_missing_fields_incomplete_not_managed(self, appmod):
        pos = {"symbol": "ONDOUSDT", "side": "LONG",
               "opened_at": _iso(1)}
        c = appmod._classify_legacy_position(pos, {})
        assert c["status"] == "INCOMPLETE_POSITION_DATA"
        audit = appmod.POSITION_AUDIT_PATH.read_text(encoding="utf-8")
        rec = json.loads(audit.strip().splitlines()[-1])
        assert rec["symbol"] == "ONDOUSDT"
        assert rec["reason"] == "INCOMPLETE_POSITION_DATA"

    def test_orphan_when_trade_closed_after_open(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(5)}
        state = {"trades": [{"symbol": "ONDOUSDT",
                             "closed_at": _iso(4)}]}
        c = appmod._classify_legacy_position(pos, state)
        assert c["status"] == "ORPHAN_POSITION"
        rec = json.loads(appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()[-1])
        assert rec["reason"] == "ORPHAN_POSITION"

    def test_orphan_epoch_timestamp_normalized(self, appmod):
        # trades 'time' alanı epoch gelse bile kıyas normalize
        # datetime üzerinden yapılır (string kıyası yok).
        opened = datetime.now(timezone.utc) - timedelta(hours=5)
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": opened.isoformat()}
        state = {"trades": [{
            "symbol": "ONDOUSDT",
            "time": (opened + timedelta(hours=1)).timestamp()}]}
        c = appmod._classify_legacy_position(pos, state)
        assert c["status"] == "ORPHAN_POSITION"

    def test_unparseable_trade_ts_is_not_orphan_proof(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(1)}
        state = {"trades": [{"symbol": "ONDOUSDT",
                             "closed_at": "zzz-bozuk"}]}
        c = appmod._classify_legacy_position(pos, state)
        assert c["status"] == "OPEN"  # yanlış pozitif ORPHAN yok

    def test_stale_threshold_configurable(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(2)}
        c = appmod._classify_legacy_position(pos, {}, stale_hours=1.0)
        assert c["status"] == "STALE_POSITION"
        c2 = appmod._classify_legacy_position(pos, {},
                                              stale_hours=10.0)
        assert c2["status"] == "OPEN"

    def test_default_stale_threshold_not_aggressive(self, appmod):
        # Legacy motorda max-hold yok — 5 saatlik sağlıklı pozisyon
        # varsayılan eşikte bayat sayılmaz (mimar bulgusu).
        assert appmod.LEGACY_POSITION_STALE_HOURS >= 24
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(5)}
        assert appmod._classify_legacy_position(
            pos, {"trades": []})["status"] == "OPEN"

    def test_stale_after_threshold(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(
                   appmod.LEGACY_POSITION_STALE_HOURS + 1.5)}
        c = appmod._classify_legacy_position(pos, {"trades": []})
        assert c["status"] == "STALE_POSITION"

    def test_bad_opened_at_incomplete(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": "bozuk-tarih"}
        c = appmod._classify_legacy_position(pos, {})
        assert c["status"] == "INCOMPLETE_POSITION_DATA"

    def test_audit_dedupes_consecutive(self, appmod):
        pos = {"symbol": "ONDOUSDT", "opened_at": _iso(1)}
        appmod._classify_legacy_position(pos, {})
        appmod._classify_legacy_position(pos, {})
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_orphan_excluded_from_overview_source(self):
        # _operation_raw ORPHAN'ı aktif listeye almaz (kaynak kodu
        # sözleşmesi — davranış birim testleri yukarıda).
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        assert '_cls["status"] != "ORPHAN_POSITION"' in src
        assert '"position_status": _cls["status"]' in src
        assert '"position_status": "OPEN"' not in src.split(
            "_cls = _classify_legacy_position(")[1][:2000]


# ── Dual-model pozisyon durumu + exit korumaları ───────────────────

@pytest.fixture()
def dmiso(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
    return tmp_path


def _rt(positions: dict) -> dict:
    return {"positions": positions, "trades": [], "core_list": [],
            "opportunity_list": []}


def _dm_pos(**over) -> dict:
    base = {"symbol": "ONDOUSDT", "model": dm.MODEL_CORE,
            "side": "LONG", "entry": 0.95, "quantity": 100.0,
            "notional_usdt": 95.0, "opened_at": _iso(0.2),
            "opened_ts": 0.0, "peak": 0.95, "tp": 1.0, "sl": 0.9,
            "trailing_pct": 0.5, "max_hold_minutes": 60,
            "confidence": 70, "config_version": "BASE"}
    base.update(over)
    return base


class TestDualPositionStatus:
    def test_price_refresh_failed_status(self, dmiso, monkeypatch):
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": _dm_pos()})))
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {})
        snap = dm.snapshot(with_prices=True)
        p = snap["positions"][0]
        assert p["position_status"] == "PRICE_REFRESH_FAILED"
        assert p["current_price"] is None
        assert p["unrealized_net_pnl"] is None  # uydurma PnL yok
        assert snap["live_orders"] == "DISABLED"

    def test_incomplete_status_when_fields_missing(self, dmiso,
                                                   monkeypatch):
        bad = _dm_pos(); del bad["quantity"]
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": bad})))
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {"ONDOUSDT": 0.97})
        snap = dm.snapshot(with_prices=True)
        p = snap["positions"][0]
        assert p["position_status"] == "INCOMPLETE_POSITION_DATA"
        assert p["unrealized_net_pnl"] is None

    def test_active_when_healthy(self, dmiso, monkeypatch):
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": _dm_pos()})))
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {"ONDOUSDT": 0.97})
        p = dm.snapshot(with_prices=True)["positions"][0]
        assert p["position_status"] == "ACTIVE"
        assert p["unrealized_net_pnl"] is not None


class TestExitGuards:
    def test_monitor_skips_incomplete_and_flags(self, dmiso):
        bad = _dm_pos(quantity=0)  # geçersiz miktar
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": bad})))
        closed = dm.monitor_positions(
            lambda s: 10.0, dm.get_config())  # fiyat TP üstünde bile
        assert closed == []  # eksik veriyle exit YOK
        rt = json.loads((dmiso / "dual_model_runtime.json")
                        .read_text())
        assert "ONDOUSDT" in rt["positions"]  # pozisyona dokunulmadı
        assert "pozisyon verisi eksik" in (rt.get("last_error") or "")

    def test_monitor_skips_when_price_missing(self, dmiso):
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": _dm_pos()})))
        assert dm.monitor_positions(
            lambda s: None, dm.get_config()) == []

    def test_manual_close_rejects_incomplete(self, dmiso):
        bad = _dm_pos(); bad["entry"] = None
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": bad})))
        ok, msg = dm.manual_close("ONDOUSDT", price=0.97)
        assert not ok and msg == "INCOMPLETE_POSITION_DATA"
        rt = json.loads((dmiso / "dual_model_runtime.json")
                        .read_text())
        assert "ONDOUSDT" in rt["positions"]

    def test_manual_close_rejects_without_fresh_price(self, dmiso,
                                                      monkeypatch):
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": _dm_pos()})))
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {})
        ok, msg = dm.manual_close("ONDOUSDT")
        assert not ok and msg == "PRICE_UNAVAILABLE"


# ── UI sözleşmesi ──────────────────────────────────────────────────

class TestUiContract:
    def test_status_codes_rendered_honestly(self):
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        for code in ("PRICE_REFRESH_FAILED", "RECONCILIATION_REQUIRED",
                     "ORPHAN_POSITION", "INCOMPLETE_POSITION_DATA",
                     "STALE_POSITION"):
            assert code in js, code
        assert ("Çıkış değerlendirmesi durduruldu — "
                "pozisyon verisi eksik") in js
        # Varsayılan artık körlemesine "Yönetiliyor" değil.
        assert 'STATUS_TR[p.position_status] || "Yönetiliyor"' not in js
        # Eksik veride Kapat düğmesi devre dışı.
        assert "disabled title=" in js

    def test_gitignore_covers_audit(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "alpha20_v1/position_integrity_audit.jsonl" in gi
