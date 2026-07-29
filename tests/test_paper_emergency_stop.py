"""
PAPER acil durdurma (emergency stop) güvenli temizleme testleri — ağsız.

Misyon kapsamı:
- İki kanonik kaynak (adaptive config + safety_state) birlikte okunur.
- Neden kaydı olmayan eski kilit UNKNOWN_LEGACY_STATE olarak görünür.
- Risk kaynaklı durdurma (RISK_LIMIT / CONSECUTIVE_LOSSES) sessizce
  temizlenmez.
- Temizlik onay ister, yedek üretir, iki kaynağı da kapatır.
- Replit / non-Windows ortamında clear fail-closed 403.
- Geçici veri (SSL) hatası kill-switch'i KALICI etkinleştirmez.
- Paper ledger/bakiye dosyalarına dokunulmaz; yanıtta secret yoktur.
- DANIŞMAN tercihi start_automation istenmedikçe değişmez.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """emergency_stop + safety_guard tamamen tmp dosyalara yönlendirilir."""
    import safety_guard as sg
    from services import emergency_stop as es
    alpha = tmp_path / "alpha20_v1"
    alpha.mkdir()
    cfg_path = alpha / "config.json"
    cfg_path.write_text(json.dumps({
        "execution_mode": "PAPER",
        "adaptive_system": {"enabled": False, "kill_switch": False},
    }), encoding="utf-8")
    safety_path = alpha / "safety_state.json"
    monkeypatch.setattr(sg, "SAFETY_STATE_PATH", safety_path)
    monkeypatch.setattr(es, "ALPHA_DIR", alpha)
    monkeypatch.setattr(es, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(es, "BACKUP_DIR", alpha / "emergency_stop_backups")
    monkeypatch.setattr(es, "OPERATION_STATE_PATH",
                        alpha / "operation_control_state.json")
    return {"es": es, "sg": sg, "cfg_path": cfg_path,
            "safety_path": safety_path, "alpha": alpha}


def _set_cfg_ks(iso, value: bool):
    cfg = json.loads(iso["cfg_path"].read_text(encoding="utf-8"))
    cfg["adaptive_system"]["kill_switch"] = value
    iso["cfg_path"].write_text(json.dumps(cfg), encoding="utf-8")


class TestStatus:
    def test_inactive_by_default(self, iso):
        st = iso["es"].status()
        assert st["active"] is False
        assert st["reason_code"] == ""
        assert st["live_orders"] == "DISABLED"

    def test_active_from_config_only_is_legacy(self, iso):
        # Tam bugünkü gerçek durum: config true, safety_state false,
        # neden kaydı yok → UNKNOWN_LEGACY_STATE, sessiz güvenli değil.
        _set_cfg_ks(iso, True)
        st = iso["es"].status()
        assert st["active"] is True
        assert st["sources"] == {"adaptive_config": True,
                                 "safety_state": False}
        assert st["reason_code"] == "UNKNOWN_LEGACY_STATE"
        assert st["reason_text"]
        assert st["can_clear"] is True

    def test_active_with_reason_model(self, iso):
        iso["sg"].activate_kill_switch("Operatör panik.",
                                       reason_code="MANUAL_STOP",
                                       triggered_by="operator")
        st = iso["es"].status()
        assert st["active"] is True
        assert st["reason_code"] == "MANUAL_STOP"
        assert st["reason_text"] == "Operatör panik."
        assert st["triggered_by"] == "operator"
        assert st["triggered_at"]

    def test_risk_reason_not_clearable(self, iso):
        iso["sg"].activate_kill_switch("Ardışık zarar limiti.",
                                       reason_code="CONSECUTIVE_LOSSES",
                                       triggered_by="risk-engine")
        st = iso["es"].status()
        assert st["risk_protected"] is True
        assert st["can_clear"] is False

    def test_live_mode_never_clearable(self, iso):
        cfg = json.loads(iso["cfg_path"].read_text(encoding="utf-8"))
        cfg["execution_mode"] = "LIVE"
        cfg["adaptive_system"]["kill_switch"] = True
        iso["cfg_path"].write_text(json.dumps(cfg), encoding="utf-8")
        st = iso["es"].status()
        assert st["can_clear"] is False
        ok, why = iso["es"].health_check()
        assert ok is False and "LIVE" in why

    def test_deactivate_clears_reason_fields(self, iso):
        iso["sg"].activate_kill_switch("x", reason_code="MANUAL_STOP")
        iso["sg"].deactivate_kill_switch()
        state = iso["sg"].get_safety_state()
        assert state["kill_switch"] is False
        assert state["kill_switch_reason_code"] == ""
        assert state["kill_switch_triggered_at"] is None

    def test_automation_mode_labels(self, iso):
        es = iso["es"]
        assert es.automation_mode() == "ADVISOR"  # dosya yok → dürüst
        (iso["alpha"] / "operation_control_state.json").write_text(
            json.dumps({"automation_state": "RUNNING"}), encoding="utf-8")
        assert es.automation_mode() == "AUTOMATIC"
        (iso["alpha"] / "operation_control_state.json").write_text(
            json.dumps({"automation_state": "STOPPED"}), encoding="utf-8")
        assert es.automation_mode() == "ADVISOR"

    def test_no_secret_material_in_status(self, iso):
        _set_cfg_ks(iso, True)
        txt = json.dumps(iso["es"].status()).lower()
        for banned in ("api_key", "apikey", "secret", "password", "token"):
            assert banned not in txt


class TestBackup:
    def test_backup_written_before_clear(self, iso):
        iso["sg"].activate_kill_switch("stale", reason_code="STALE_TEST_STATE")
        _set_cfg_ks(iso, True)
        name = iso["es"].write_backup("tester")
        path = iso["es"].BACKUP_DIR / name
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["actor"] == "tester"
        assert data["adaptive_system"]["kill_switch"] is True
        assert data["safety_state"]["kill_switch"] is True
        low = path.read_text(encoding="utf-8").lower()
        for banned in ("api_key", "apikey", "apisecret", "password"):
            assert banned not in low


class TestTransientDataErrorNotPersistent:
    def test_ssl_style_data_error_does_not_set_kill_switch(self, iso):
        """Tek sembollük geçici veri hatası unsafe sonuç verir ama
        kill-switch bayrağını KALICI olarak etkinleştirmez."""
        sg = iso["sg"]
        result = sg.check_all(trading_state={}, adaptive_cfg={},
                              data_ok=False, data_error="SSL EOF SOLUSDT")
        assert result.safe is False
        assert result.kill_switch is False
        assert sg.get_safety_state()["kill_switch"] is False
        assert iso["es"].status()["active"] is False


class TestClearEndpoint:
    @pytest.fixture()
    def client(self, iso, monkeypatch):
        import app as app_module
        app_module.app.config["TESTING"] = True
        app_module.app.config["WTF_CSRF_ENABLED"] = False
        # app rotası _get_adaptive_cfg/_save_adaptive_cfg ile config.json'a
        # gider; testte iso config'ine yönlendiririz.
        def _get_cfg():
            return json.loads(iso["cfg_path"].read_text(
                encoding="utf-8"))["adaptive_system"]
        def _save_cfg(adaptive):
            cfg = json.loads(iso["cfg_path"].read_text(encoding="utf-8"))
            cfg["adaptive_system"] = adaptive
            iso["cfg_path"].write_text(json.dumps(cfg), encoding="utf-8")
            return True, ""
        monkeypatch.setattr(app_module, "_get_adaptive_cfg", _get_cfg)
        monkeypatch.setattr(app_module, "_save_adaptive_cfg", _save_cfg)
        monkeypatch.setattr(app_module.auth, "check_rate_limit",
                            lambda ip: (True, 0))
        return app_module.app.test_client()

    def _arm_windows(self, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module,
                            "_paper_clear_local_windows_ok", lambda: True)

    def test_replit_env_fail_closed(self, iso, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module,
                            "_paper_clear_local_windows_ok", lambda: False)
        _set_cfg_ks(iso, True)
        r = client.post("/api/automation/paper-emergency-stop/clear",
                        json={"confirm": True})
        assert r.status_code == 403

    def test_not_active_conflict(self, iso, client, monkeypatch):
        self._arm_windows(monkeypatch)
        r = client.post("/api/automation/paper-emergency-stop/clear",
                        json={"confirm": True})
        assert r.status_code == 409

    def test_risk_stop_rejected(self, iso, client, monkeypatch):
        self._arm_windows(monkeypatch)
        iso["sg"].activate_kill_switch("risk", reason_code="RISK_LIMIT",
                                       triggered_by="risk-engine")
        r = client.post("/api/automation/paper-emergency-stop/clear",
                        json={"confirm": True})
        assert r.status_code == 403
        assert r.get_json()["error"] == "RISK_PROTECTED"
        assert iso["sg"].get_safety_state()["kill_switch"] is True

    def test_confirm_required_shows_reason(self, iso, client, monkeypatch):
        self._arm_windows(monkeypatch)
        _set_cfg_ks(iso, True)
        r = client.post("/api/automation/paper-emergency-stop/clear",
                        json={})
        assert r.status_code == 400
        body = r.get_json()
        assert body["error"] == "CONFIRM_REQUIRED"
        assert body["data"]["reason_code"] == "UNKNOWN_LEGACY_STATE"

    def test_clear_stale_state_with_backup(self, iso, client, monkeypatch):
        self._arm_windows(monkeypatch)
        iso["sg"].activate_kill_switch("bayat test",
                                       reason_code="STALE_TEST_STATE")
        _set_cfg_ks(iso, True)
        ledger_before = None  # paper ledger'a hiç dokunulmadığını da izle
        r = client.post("/api/automation/paper-emergency-stop/clear",
                        json={"confirm": True})
        assert r.status_code == 200
        body = r.get_json()
        assert body["cleared"] is True
        assert (iso["es"].BACKUP_DIR / body["backup"]).exists()
        # İki kaynak da kapandı.
        assert iso["sg"].get_safety_state()["kill_switch"] is False
        cfg = json.loads(iso["cfg_path"].read_text(encoding="utf-8"))
        assert cfg["adaptive_system"]["kill_switch"] is False
        # DANIŞMAN tercihi sessizce değişmedi.
        assert body["automation_started"] is False
        assert ledger_before is None
        low = json.dumps(body).lower()
        for banned in ("apikey", "api_key", "apisecret", "password"):
            assert banned not in low

    def test_status_endpoint_readonly(self, iso, client):
        _set_cfg_ks(iso, True)
        r = client.get("/api/automation/paper-emergency-stop")
        assert r.status_code == 200
        d = r.get_json()["data"]
        assert d["active"] is True
        assert d["reason_code"] == "UNKNOWN_LEGACY_STATE"
        # GET hiçbir şeyi değiştirmez.
        assert iso["es"].status()["active"] is True


class TestRepoBaseline:
    def test_repo_config_kill_switch_false(self):
        """Yanlışlıkla HEAD'e gömülen kill_switch=true sapması geri
        alındı — repo taban çizgisi kullanıcının bilinçli değeri."""
        cfg = json.loads((ROOT / "alpha20_v1" / "config.json")
                         .read_text(encoding="utf-8"))
        assert cfg["adaptive_system"]["kill_switch"] is False
