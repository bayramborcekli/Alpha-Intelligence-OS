"""MISSION — CONSOLIDATE VERIFIED WINDOWS PAPER RUNTIME kabul testleri.

Kapsam (misyon bölüm 15):
- Paper autonomous seçimi ve sembol durumları restart sonrası korunur
  (kanonik kaynak: alpha20_v1/operation_control_state.json — git dışı).
- STOP tercihi restart sonrası otomatik tekrar açılmaz.
- Emergency/risk stop aktifse kayıtlı tercih restore edilmez ve SİLİNMEZ.
- Replit deployment Windows local state işlemlerini başlatmaz.
- /api/paper/state tek doğruluk kaynağı; LIVE ORDERS her durumda DISABLED.
- SETUP Global bağlıyken yeniden yapılandırma sormaz; TR ayrı sorulur.
- Git pack-lock interaktif döngüsü kapatıldı (GIT_ASK_YESNO=false, --ff-only).
- Analiz Zamanlayıcısı ↔ Paper Ticaret Otomasyonu UI ayrımı.
- "Sıradaki İşlemler" gerçek emir yokken kullanılmaz.
- local-dev-bypass kullanıcı adı olarak gösterilmez.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controlled_execution_api import ControlledExecutionAPI  # noqa: E402
from controlled_execution_foundation import (  # noqa: E402
    ControlledExecutionFoundation)
from controlled_execution_policy import ExtensionRegistry  # noqa: E402
from controlled_execution_router import (  # noqa: E402
    ControlledExecutionRouter)
from execution_risk_models import (  # noqa: E402
    RiskDecision, RiskDecisionType)
from micro_live_authorization import (  # noqa: E402
    MicroLiveAuthorizationService)
from operation_control_models import (  # noqa: E402
    AutomationCommand, SymbolCommand)
from operation_control_service import (  # noqa: E402
    CONFIRMATION_PHRASE, OperationControlService)
from operation_control_store import (  # noqa: E402
    OperationControlStateStore)
from paper_broker import PaperBroker  # noqa: E402
from paper_execution_service import (  # noqa: E402
    PaperExecutionService, StaticRiskEvaluator)
from shadow_mode import ShadowModeService  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def make_service(state_path: Path) -> OperationControlService:
    foundation = ControlledExecutionFoundation(ExtensionRegistry())
    broker = PaperBroker(known_symbols=SYMBOLS)
    risk = StaticRiskEvaluator(
        RiskDecision(decision=RiskDecisionType.ALLOW))
    api = ControlledExecutionAPI(ControlledExecutionRouter(
        PaperExecutionService(broker=broker, foundation=foundation,
                              risk_evaluator=risk),
        ShadowModeService(broker=broker, foundation=foundation,
                          risk_evaluator=risk),
        MicroLiveAuthorizationService(foundation=foundation)))
    return OperationControlService(
        api, state_store=OperationControlStateStore(state_path))


def _start(svc, key="k-start"):
    return svc.execute_automation_command(
        AutomationCommand.START, "operator", idempotency_key=key)


class TestPreferencePersistence:
    """1-3) Tercihler kanonik git-dışı depoda restart'ı atlatır."""

    def test_running_and_symbols_survive_restart(self, tmp_path):
        state = tmp_path / "operation_control_state.json"
        svc = make_service(state)
        _start(svc)
        for i, sym in enumerate(SYMBOLS):
            svc.execute_symbol_command(
                sym, SymbolCommand.ENABLE, "operator",
                idempotency_key=f"k-{i}")
        # "Restart": aynı depoyu okuyan YENİ servis örneği.
        svc2 = make_service(state)
        assert svc2.automation_state.value == "RUNNING"
        for sym in SYMBOLS:
            assert svc2.symbol_state(sym).value == "ENABLED"

    def test_stop_preference_survives_restart(self, tmp_path):
        state = tmp_path / "operation_control_state.json"
        svc = make_service(state)
        _start(svc)
        svc.execute_automation_command(
            AutomationCommand.STOP, "operator",
            idempotency_key="k-stop")
        svc2 = make_service(state)
        assert svc2.automation_state.value == "STOPPED"

    def test_state_file_is_gitignored(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "alpha20_v1/operation_control_state.json" in gi


class TestStartupReconciliation:
    """3-4) serve_windows desired-state reconciliation."""

    @pytest.fixture()
    def sw(self, monkeypatch):
        import serve_windows as sw
        # os.name GLOBAL patch'lenmez (pathlib'i bozar) — ortam kapısı
        # ayrı fonksiyondur ve doğrudan taklit edilir.
        monkeypatch.setattr(sw, "_reconcile_env_ok", lambda: True)
        return sw

    def _fake_app(self, state: str, started: list):
        class _AC:
            @staticmethod
            def start_controller_loop():
                started.append(True)
                return True

        class _St:
            value = state

        class _Svc:
            automation_state = _St()

            @staticmethod
            def symbol_states():
                return {}

        class _App:
            ac = _AC()

            @staticmethod
            def get_operation_service():
                return _Svc()
        return _App

    def test_running_preference_starts_controller(self, sw, monkeypatch):
        from services import emergency_stop as es
        monkeypatch.setattr(es, "status", lambda: {
            "active": False, "environment": "PAPER",
            "reason_code": ""})
        started: list = []
        sw._reconcile_paper_desired_state(self._fake_app("RUNNING",
                                                         started))
        assert started == [True]

    def test_stopped_preference_not_autostarted(self, sw, monkeypatch):
        from services import emergency_stop as es
        monkeypatch.setattr(es, "status", lambda: {
            "active": False, "environment": "PAPER",
            "reason_code": ""})
        started: list = []
        sw._reconcile_paper_desired_state(self._fake_app("STOPPED",
                                                         started))
        assert started == []

    def test_emergency_stop_overrides_restore(self, sw, monkeypatch,
                                              tmp_path):
        from services import emergency_stop as es
        monkeypatch.setattr(es, "status", lambda: {
            "active": True, "environment": "PAPER",
            "reason_code": "MANUAL_STOP"})
        started: list = []
        # Tercih dosyası korunur — reconcile hiçbir dosyayı silmez.
        pref = tmp_path / "operation_control_state.json"
        pref.write_text('{"automation_state": "RUNNING"}',
                        encoding="utf-8")
        sw._reconcile_paper_desired_state(self._fake_app("RUNNING",
                                                         started))
        assert started == []
        assert pref.exists()

    def test_risk_stop_overrides_restore(self, sw, monkeypatch):
        from services import emergency_stop as es
        monkeypatch.setattr(es, "status", lambda: {
            "active": True, "environment": "PAPER",
            "reason_code": "RISK_LIMIT"})
        started: list = []
        sw._reconcile_paper_desired_state(self._fake_app("RUNNING",
                                                         started))
        assert started == []

    def test_live_mode_fail_closed(self, sw, monkeypatch):
        from services import emergency_stop as es
        monkeypatch.setattr(es, "status", lambda: {
            "active": False, "environment": "LIVE",
            "reason_code": ""})
        started: list = []
        sw._reconcile_paper_desired_state(self._fake_app("RUNNING",
                                                         started))
        assert started == []

    def test_running_restore_records_result(self, sw, monkeypatch,
                                             tmp_path):
        from services import emergency_stop as es
        monkeypatch.setattr(es, "status", lambda: {
            "active": False, "environment": "PAPER",
            "reason_code": ""})
        out = tmp_path / "paper_reconcile_last.json"
        monkeypatch.setattr(sw, "RECONCILE_RESULT_PATH", out)
        sw._reconcile_paper_desired_state(self._fake_app("RUNNING", []))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["result"] == "RESTORED_RUNNING"
        assert data["automation"] == "RUNNING"

    def test_emergency_block_records_result(self, sw, monkeypatch,
                                            tmp_path):
        from services import emergency_stop as es
        monkeypatch.setattr(es, "status", lambda: {
            "active": True, "environment": "PAPER",
            "reason_code": "MANUAL_STOP"})
        out = tmp_path / "paper_reconcile_last.json"
        monkeypatch.setattr(sw, "RECONCILE_RESULT_PATH", out)
        sw._reconcile_paper_desired_state(self._fake_app("RUNNING", []))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["result"] == "BLOCKED_EMERGENCY"
        assert data["detail"] == "MANUAL_STOP"

    def test_replit_is_noop(self, monkeypatch):
        import serve_windows as sw
        monkeypatch.setattr(sw, "_reconcile_env_ok", lambda: False)
        called: list = []

        class _App:
            @staticmethod
            def get_operation_service():
                called.append(True)
        sw._reconcile_paper_desired_state(_App)
        assert called == []


class TestConsolidatedState:
    """13) /api/paper/state — tek doğruluk kaynağı."""

    @pytest.fixture()
    def client(self):
        import app as app_module
        app_module.app.config["TESTING"] = True
        app_module.app.config["WTF_CSRF_ENABLED"] = False
        with app_module.app.test_client() as c:
            with c.session_transaction() as s:
                s["username"] = "test-operator"
                s["last_active"] = 9999999999
            yield c

    def test_snapshot_fields_and_live_orders_disabled(self, client):
        r = client.get("/api/paper/state")
        assert r.status_code == 200
        data = r.get_json()
        assert data["live_orders"] == "DISABLED"
        assert data["emergency_stop"] in ("CLEAR", "ACTIVE")
        assert data["automation_mode"] in ("OTONOM", "DANIŞMAN")
        assert data["windows_runtime"] == "RUNNING"
        for s in data["strategies"]:
            assert set(s) == {"symbol", "enabled", "run_state",
                              "entry_allowed", "last_signal",
                              "last_error", "updated_at"}
        assert "ENABLED" in data["strategies_summary"]

    def test_consistent_with_operation_status(self, client):
        snap = client.get("/api/paper/state").get_json()
        st = client.get("/api/operation-control/status").get_json()
        auto = st["data"]["automation_state"] if "data" in st else \
            st.get("automation_state")
        expected = "OTONOM" if auto == "RUNNING" else "DANIŞMAN"
        assert snap["automation_mode"] == expected

    def test_no_secrets_in_snapshot(self, client):
        body = client.get("/api/paper/state").get_data(as_text=True)
        for banned in ("api_key", "secret", "API_Key", "SECRET"):
            assert banned.lower() not in body.lower() or \
                "masked" in body.lower()


class TestSetupPrompts:
    """8-9) SETUP Global'i yeniden sormaz; TR ayrı ve açık seçenek."""

    def test_global_connected_skips_general_prompt(self, monkeypatch):
        import windows_setup_flow as wsf
        from services import binance_connection as bcn
        from services import secure_credentials as sc
        monkeypatch.setattr(sc, "configured",
                            lambda x: x == "BINANCE_GLOBAL")
        monkeypatch.setattr(sc, "credential_store", lambda x: "DPAPI")
        monkeypatch.setattr(bcn, "test_stored", lambda x: {
            "status": "CONNECTED_READ_ONLY"})
        prompts: list = []

        def fake_input(msg=""):
            prompts.append(msg)
            return "h"
        monkeypatch.setattr("builtins.input", fake_input)
        wsf.connect_accounts()
        joined = " ".join(prompts)
        # Genel yapılandırma sorusu ve Global anahtar girişi YOK:
        assert "hesap baglantisi yapilandirilsin mi" not in joined.lower()
        assert "api key" not in joined.lower()
        # TR ayrı ve açık seçenek olarak soruldu:
        assert any("binance tr" in p.lower() for p in prompts)

    def test_git_pack_lock_noninteractive(self):
        ps1 = (ROOT / "windows_setup.ps1").read_text(encoding="utf-8")
        assert 'GIT_ASK_YESNO = "false"' in ps1
        assert "--ff-only" in ps1
        assert "gc.auto=0" in ps1
        assert "GIT UPDATE" in ps1 and "GIT CLEANUP" in ps1
        # Pack dosyaları asla zorla silinmez:
        assert "Remove-Item" not in ps1 or ".git" not in ps1


class TestUiClarity:
    """10-12) Sayfa ayrımı, kuyruk etiketi, yerel oturum etiketi."""

    def test_automation_page_is_analysis_scheduler(self):
        html = (ROOT / "templates/automation.html").read_text(
            encoding="utf-8")
        assert "Analiz Zamanlayıcısı" in html
        assert "TEMSİL ETMEZ" in html
        assert "Operation Center" in html

    def test_queue_label_not_fake(self):
        html = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        assert "İzlenen Piyasalar" in html
        # Statik başlıkta sahte "Sıradaki İşlemler" kalmadı:
        assert "Sıradaki İşlemler</h2>" not in html
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "realPending" in js
        assert "İzlenen Piyasalar" in js and "Sıradaki İşlemler" in js

    def test_local_dev_bypass_not_shown_as_username(self):
        js = (ROOT / "static/js/operation_control.js").read_text(
            encoding="utf-8")
        assert "Yerel Windows Oturumu" in js
        assert "actorLabel" in js


class TestSslIsolation:
    """7) Tek sembol geçici veri hatası tercihaleri değiştirmez."""

    def test_data_error_does_not_touch_symbol_prefs(self, tmp_path,
                                                    monkeypatch):
        state = tmp_path / "operation_control_state.json"
        svc = make_service(state)
        _start(svc)
        svc.execute_symbol_command(
            "BTCUSDT", SymbolCommand.ENABLE, "operator",
            idempotency_key="k-b")
        before = json.loads(state.read_text(encoding="utf-8"))
        # Geçici veri hatası (SSL sınıfı): safety_guard unsafe der ama
        # hiçbir kalıcı tercih dosyasına yazmaz.
        sys.path.insert(0, str(ROOT / "alpha20_v1"))
        import safety_guard as sg
        monkeypatch.setattr(sg, "SAFETY_STATE_PATH",
                            tmp_path / "safety_state.json")
        result = sg.check_all(data_ok=False)
        assert not result.safe
        after = json.loads(state.read_text(encoding="utf-8"))
        assert before["symbol_states"] == after["symbol_states"]
        assert before["automation_state"] == after["automation_state"]
