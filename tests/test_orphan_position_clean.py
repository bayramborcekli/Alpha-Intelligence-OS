"""Task 145: ORPHAN legacy pozisyon kaydının state.json'dan güvenli
temizliği.

Güvenceler:
1. Yalnız yeniden sınıflandırma ORPHAN_POSITION derse silinir —
   sağlıklı (OPEN) veya eksik veri kayıtları ASLA silinmez.
2. Bot çalışırken temizlik reddedilir (yazma yarışı).
3. Açık onay (confirm=true) ve sembol eşleşmesi zorunlu.
4. Temizlik audit zincirine CLEANED olarak yazılır; state.json'daki
   trades/balance verisine dokunulmaz.
5. /api/operation-control/status yeni orphan_position alanını taşır.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) -
            timedelta(hours=hours_ago)).isoformat()


def _orphan_state() -> dict:
    return {
        "balance": 1000.0, "day": "2026-07-30",
        "day_start_balance": 1000.0, "consecutive_losses": 0,
        "position": {"symbol": "ONDOUSDT", "side": "LONG",
                     "entry": 0.95, "quantity": 100.0,
                     "opened_at": _iso(5)},
        "trades": [{"symbol": "ONDOUSDT", "closed_at": _iso(4)}],
    }


def _healthy_state() -> dict:
    st = _orphan_state()
    st["trades"] = []  # kapanış kanıtı yok → OPEN
    return st


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app as appmod
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(appmod, "STATE_PATH", state_path)
    monkeypatch.setattr(appmod, "POSITION_AUDIT_PATH",
                        tmp_path / "audit.jsonl")
    monkeypatch.setattr(appmod, "bot_running", lambda: False)
    monkeypatch.setattr(appmod, "_get_main_config", lambda: {})
    monkeypatch.setattr(appmod.auth, "check_rate_limit",
                        lambda ip: (True, 0))
    appmod.app.config["TESTING"] = True
    appmod.app.config["WTF_CSRF_ENABLED"] = False
    return {"app": appmod, "state_path": state_path,
            "client": appmod.app.test_client()}


class TestDetectHelper:
    def test_detects_orphan(self, env):
        env["state_path"].write_text(json.dumps(_orphan_state()),
                                     encoding="utf-8")
        o = env["app"]._detect_orphan_state_position()
        assert o is not None and o["symbol"] == "ONDOUSDT"

    def test_none_for_healthy(self, env):
        env["state_path"].write_text(json.dumps(_healthy_state()),
                                     encoding="utf-8")
        assert env["app"]._detect_orphan_state_position() is None

    def test_none_without_position(self, env):
        st = _orphan_state()
        st["position"] = None
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        assert env["app"]._detect_orphan_state_position() is None

    def test_none_when_state_missing(self, env):
        assert env["app"]._detect_orphan_state_position() is None


class TestCleanEndpoint:
    URL = "/api/state/orphan-position/clean"

    def test_cleans_orphan_and_audits(self, env):
        env["state_path"].write_text(json.dumps(_orphan_state()),
                                     encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True and body["symbol"] == "ONDOUSDT"
        st = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st["position"] is None
        # Diğer alanlara dokunulmadı.
        assert st["balance"] == 1000.0 and len(st["trades"]) == 1
        recs = [json.loads(x) for x in env["app"].POSITION_AUDIT_PATH
                .read_text(encoding="utf-8").strip().splitlines()]
        assert recs[-1]["reason"] == "CLEANED"
        assert recs[-1]["symbol"] == "ONDOUSDT"

    def test_healthy_position_never_deleted(self, env):
        env["state_path"].write_text(json.dumps(_healthy_state()),
                                     encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 409
        st = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st["position"] is not None  # pozisyona dokunulmadı

    def test_incomplete_position_never_deleted(self, env):
        st = _orphan_state()
        st["position"] = {"symbol": "ONDOUSDT",
                          "opened_at": _iso(5)}  # entry/quantity yok
        st["trades"] = []
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 409
        st2 = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st2["position"] is not None

    def test_rejected_while_bot_running(self, env, monkeypatch):
        monkeypatch.setattr(env["app"], "bot_running", lambda: True)
        env["state_path"].write_text(json.dumps(_orphan_state()),
                                     encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 409
        st = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st["position"] is not None

    def test_confirm_required(self, env):
        env["state_path"].write_text(json.dumps(_orphan_state()),
                                     encoding="utf-8")
        r = env["client"].post(self.URL, json={"symbol": "ONDOUSDT"})
        assert r.status_code == 400

    def test_symbol_required_and_must_match(self, env):
        env["state_path"].write_text(json.dumps(_orphan_state()),
                                     encoding="utf-8")
        assert env["client"].post(self.URL, json={
            "confirm": True}).status_code == 400
        assert env["client"].post(self.URL, json={
            "symbol": "BTCUSDT", "confirm": True}).status_code == 409
        st = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st["position"] is not None

    def test_no_position_conflict(self, env):
        st = _orphan_state()
        st["position"] = None
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 409

    def test_rate_limited(self, env, monkeypatch):
        monkeypatch.setattr(env["app"].auth, "check_rate_limit",
                            lambda ip: (False, 30))
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 429


class TestStatusExposure:
    def test_status_source_contract(self):
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        assert ('data["orphan_position"] = '
                "_detect_orphan_state_position()") in src

    def test_ui_contract(self):
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "/api/state/orphan-position/clean" in js
        assert "orphan_position" in js
        assert "confirm: true" in js
        html = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        assert "th-orphan-banner" in html
        assert "th-orphan-clean" in html
