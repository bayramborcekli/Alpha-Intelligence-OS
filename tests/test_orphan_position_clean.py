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
    monkeypatch.setattr(appmod, "DUAL_RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
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
        assert recs[-1]["reason"] == "POSITION_RECORD_CLEANED"
        assert recs[-1]["symbol"] == "ONDOUSDT"
        assert recs[-1]["source"] == "legacy"
        assert "previous_status=ORPHAN_POSITION" in recs[-1]["detail"]

    def test_healthy_position_never_deleted(self, env):
        env["state_path"].write_text(json.dumps(_healthy_state()),
                                     encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        # NOT_CLEANABLE: sağlıklı (OPEN) kayıt kanıt listesiyle 422
        # reddedilir — sessiz yutma yok.
        assert r.status_code == 422
        assert r.get_json()["code"] == "NOT_CLEANABLE"
        st = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st["position"] is not None  # pozisyona dokunulmadı

    def test_partially_valid_incomplete_never_deleted(self, env):
        # Entry mevcut, quantity eksik → kısmen geçerli: SİLİNMEZ
        # (422 + NOT_CLEANABLE, kanıt listesiyle — sessiz yutma yok).
        st = _orphan_state()
        st["position"] = {"symbol": "ONDOUSDT", "entry": 0.95,
                          "opened_at": _iso(5)}  # quantity yok
        st["trades"] = []
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 422
        body = r.get_json()
        assert body["code"] == "NOT_CLEANABLE"
        assert body["evidence"]
        st2 = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st2["position"] is not None

    def _incomplete_cleanable_state(self) -> dict:
        # ONDOUSDT senaryosu: entry+quantity yok, ledger karşılığı yok
        st = _orphan_state()
        st["position"] = {"symbol": "ONDOUSDT",
                          "opened_at": _iso(5)}
        st["trades"] = [{"symbol": "BTCUSDT", "closed_at": _iso(2)}]
        return st

    def test_incomplete_cleanable_removed_and_audited(self, env):
        st = self._incomplete_cleanable_state()
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["previous_status"] == "INCOMPLETE_POSITION_DATA"
        assert body["cleanup_status"] == \
            "INCOMPLETE_POSITION_DATA_CLEANABLE"
        st2 = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st2["position"] is None
        # Diğer veriler korunur.
        assert st2["balance"] == 1000.0 and len(st2["trades"]) == 1
        recs = [json.loads(x) for x in env["app"].POSITION_AUDIT_PATH
                .read_text(encoding="utf-8").strip().splitlines()]
        last = recs[-1]
        assert last["reason"] == "POSITION_RECORD_CLEANED"
        assert last["symbol"] == "ONDOUSDT"
        assert last["source"] == "legacy"
        assert "previous_status=INCOMPLETE_POSITION_DATA" in \
            last["detail"]
        # Restart eşdeğeri: state yeniden okunduğunda kayıt geri
        # gelmez (dosyada position null).
        assert env["app"]._detect_orphan_state_position() is None

    def test_incomplete_blocked_by_runtime_counterpart(self, env,
                                                       tmp_path):
        # dual_model_runtime'da aynı sembol açıksa fail-closed.
        (tmp_path / "dual_model_runtime.json").write_text(
            json.dumps({"positions": {"ONDOUSDT": {"entry": 1.0}}}),
            encoding="utf-8")
        st = self._incomplete_cleanable_state()
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 422
        assert any("dual_model_runtime" in e
                   for e in r.get_json()["evidence"])

    def test_corrupt_runtime_file_fail_closed(self, env, tmp_path):
        # Mimar bulgusu: runtime dosyası VAR ama bozuksa kanıt yok →
        # fail-closed 422 (fail-open temizlik yasak).
        (tmp_path / "dual_model_runtime.json").write_text(
            "{bozuk json", encoding="utf-8")
        st = self._incomplete_cleanable_state()
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 422
        assert any("okunamadı" in e for e in r.get_json()["evidence"])
        st2 = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st2["position"] is not None

    def test_missing_runtime_file_is_honest_empty(self, env):
        # Dosya hiç yoksa dual motor hiç koşmamıştır → karşılık
        # olamaz; temizlik yapılabilir (test_incomplete_cleanable_*
        # zaten bu yolu kullanıyor — açık sözleşme testi).
        st = self._incomplete_cleanable_state()
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 200

    def test_incomplete_blocked_by_open_ledger(self, env):
        st = self._incomplete_cleanable_state()
        st["trades"].append({"symbol": "ONDOUSDT"})  # kapanmamış
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 422
        st2 = json.loads(env["state_path"].read_text(encoding="utf-8"))
        assert st2["position"] is not None

    def test_bot_running_reason_code(self, env, monkeypatch):
        monkeypatch.setattr(env["app"], "bot_running", lambda: True)
        st = self._incomplete_cleanable_state()
        env["state_path"].write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r.status_code == 409
        assert r.get_json()["code"] == \
            "CLEANUP_REQUIRES_CONTROLLER_PAUSE"
        # Bot durdurulunca aynı istek başarılı olur.
        monkeypatch.setattr(env["app"], "bot_running", lambda: False)
        r2 = env["client"].post(self.URL, json={
            "symbol": "ONDOUSDT", "confirm": True})
        assert r2.status_code == 200

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
        # Temizlenebilir eksik kayıt: Kapat YOK; Kayıttan Kaldır +
        # Ayrıntı var; hata kodları sessizce yutulmaz.
        assert "data-clean-symbol" in js
        assert "Kayıttan Kaldır" in js
        assert "data-detail-symbol" in js
        assert "CLEANUP_REQUIRES_CONTROLLER_PAUSE" in js
        assert "NOT_CLEANABLE" in js
        # Task 159: JSON parse edilemeyen hata gövdesi (ör. proxy 502
        # HTML) genel "ulaşılamadı" mesajına indirgenmez — r.json()
        # reddi yakalanıp gerçek HTTP durum kodu gösterilir.
        clean_block = js.split("/api/state/orphan-position/clean")[1]
        clean_block = clean_block.split("data-detail-symbol")[0]
        assert "return { st: r.status, d: null };" in clean_block
        assert '"HTTP_" + o.st' in clean_block
        appsrc = (ROOT / "app.py").read_text(encoding="utf-8")
        assert '"cleanup_eligible"' in appsrc
        html = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        assert "th-orphan-banner" in html
        assert "th-orphan-clean" in html
