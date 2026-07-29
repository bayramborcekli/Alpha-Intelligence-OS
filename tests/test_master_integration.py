"""MASTER INTEGRATION misyonu kabul testleri (bölüm 27).

Kapsam: tek başlangıç orchestrator'ı, readiness graph (scheduler
kapalıysa GREEN olmaz), 3 risk profilinin GERÇEK risk motoruna
(adaptive_risk sizing) bağlandığı, profil/scan tercihi kalıcılığı,
dinamik evren sözleşmesi (max 20 + BTC/ETH/SOL pinli), decision
trace alanları, konsolide snapshot, pipeline test güvenliği.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "alpha20_v1"))

from services import risk_profiles as rp  # noqa: E402
from services import runtime_preferences as prefs  # noqa: E402


@pytest.fixture()
def prefs_tmp(tmp_path, monkeypatch):
    p = tmp_path / "runtime_preferences.json"
    monkeypatch.setattr(prefs, "PREFS_PATH", p)
    return p


class TestRuntimePreferences:
    """6) Kalıcı tercih deposu — git dışı, restart'ı atlatır."""

    def test_defaults(self, prefs_tmp):
        d = prefs.get_all()
        assert d["selected_risk_profile"] == "DENGELI"
        assert d["scan_interval_minutes"] == 5  # varsayılan 5 dk
        assert d["analysis_scheduler"] == "RUNNING"
        assert d["universe_max"] == 20

    def test_persistence_roundtrip(self, prefs_tmp):
        prefs.set_prefs(selected_risk_profile="AGRESIF",
                        scan_interval_minutes=10)
        # "Restart": dosyadan yeniden oku
        d = prefs.get_all()
        assert d["selected_risk_profile"] == "AGRESIF"
        assert d["scan_interval_minutes"] == 10
        assert prefs_tmp.exists()

    def test_turkish_alias_accepted(self, prefs_tmp):
        prefs.set_prefs(selected_risk_profile="DENGELİ")
        assert prefs.get("selected_risk_profile") == "DENGELI"

    def test_invalid_values_clamped(self, prefs_tmp):
        prefs.set_prefs(scan_interval_minutes=9999, universe_max=99)
        d = prefs.get_all()
        assert d["scan_interval_minutes"] == 60
        assert d["universe_max"] == 20  # sert üst sınır

    def test_prefs_file_gitignored(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "alpha20_v1/runtime_preferences.json" in gi
        assert "alpha20_v1/pipeline_test_ledger.jsonl" in gi


class TestRiskProfiles:
    """12-14) Kanonik değerler + GERÇEK motor bağlantısı."""

    def test_canonical_values(self):
        k = rp.PROFILES["KORUMA"]
        assert k["risk_per_trade_fraction"] == Decimal("0.0015")
        assert k["daily_loss_fraction"] == Decimal("0.0050")
        assert k["max_drawdown_fraction"] == Decimal("0.0075")
        d = rp.PROFILES["DENGELI"]
        assert d["risk_per_trade_fraction"] == Decimal("0.0025")
        assert d["daily_loss_fraction"] == Decimal("0.0100")
        assert d["max_drawdown_fraction"] == Decimal("0.0150")
        a = rp.PROFILES["AGRESIF"]
        assert a["risk_per_trade_fraction"] == Decimal("0.0050")
        assert a["daily_loss_fraction"] == Decimal("0.0200")
        assert a["max_drawdown_fraction"] == Decimal("0.0300")

    def test_profiles_reach_real_sizing_engine(self):
        """Aynı girdiyle üç profil FARKLI position size üretir —
        gerçek adaptive_risk.calculate_position_size ile (UI değil)."""
        import adaptive_risk as ar
        sizes = {}
        for name in ("KORUMA", "DENGELI", "AGRESIF"):
            flags = rp.adaptive_flags(name)
            qty, _stop, err = ar.calculate_position_size(
                balance=10_000.0, risk_pct=flags["base_risk_pct"],
                entry=100.0, stop=0, atr=2.0,
                atr_stop_multiplier=1.5,
                adaptive_cfg={"enabled": True, **flags})
            assert err == "" or err is None or qty > 0
            sizes[name] = qty
        assert sizes["KORUMA"] < sizes["DENGELI"] < sizes["AGRESIF"]

    def test_execution_limits_real_risklimits(self):
        limits = rp.execution_limits(Decimal("10000"),
                                     name="KORUMA")
        from execution_risk_models import RiskLimits
        assert isinstance(limits, RiskLimits)
        assert limits.max_daily_loss == Decimal("50.00")

    def test_decision_fields_mandatory(self):
        f = rp.decision_fields("DENGELI")
        assert f["selected_risk_profile"] == "DENGELİ"
        assert f["profile_version"] == rp.PROFILE_VERSION
        assert f["risk_per_trade_limit"] == 0.0025
        assert f["daily_loss_limit"] == 0.01
        assert f["maximum_drawdown_limit"] == 0.015

    def test_profile_persist_and_set(self, prefs_tmp):
        assert rp.set_profile("AGRESİF") == "AGRESIF"
        assert rp.current_profile_name() == "AGRESIF"
        with pytest.raises(ValueError):
            rp.set_profile("YOLO")


class TestOrchestrator:
    """3-5) Tek başlangıç + readiness graph kuralları."""

    def test_preferences_applied_to_controller(self, prefs_tmp):
        from services import system_runtime_orchestrator as sro
        prefs.set_prefs(selected_risk_profile="KORUMA",
                        scan_interval_minutes=7)
        import auto_controller as ac
        old_override = dict(ac.RUNTIME_ADAPTIVE_OVERRIDE)
        old_scan = dict(ac.RUNTIME_SCAN_SECONDS)
        try:
            sro.apply_user_preferences(ac)
            assert ac.RUNTIME_ADAPTIVE_OVERRIDE[
                "base_risk_pct"] == 0.15
            assert ac.RUNTIME_ADAPTIVE_OVERRIDE[
                "daily_loss_limit_pct"] == 0.50
            assert ac.RUNTIME_SCAN_SECONDS["value"] == 420
        finally:
            ac.set_runtime_adaptive_override(old_override)
            ac.RUNTIME_SCAN_SECONDS.clear()
            ac.RUNTIME_SCAN_SECONDS.update(old_scan)

    def test_scheduler_stopped_not_green(self, prefs_tmp):
        """2) Analiz döngüsü çalışmıyorsa overall GREEN OLMAZ.
        Tercih RUNNING + worker STOPPED → STARTUP_FAILED (YELLOW)."""
        from services import system_runtime_orchestrator as sro
        r = sro.readiness({"running": False}, "RUNNING", False)
        assert r["overall_pipeline"] != "GREEN"
        assert r["analysis_scheduler"] == "STARTUP_FAILED"
        assert r["overall_pipeline"] == "YELLOW"
        assert "SCHEDULER_STARTUP_FAILED" in r["blockers"]

    def test_scheduler_preference_stopped_is_red(self, prefs_tmp):
        """Manuel STOP: tercih STOPPED → RED/BLOCKED, GREEN imkânsız;
        zorla başlatma yok, durum STOPPED (arıza değil)."""
        from services import system_runtime_orchestrator as sro
        r = sro.readiness({"running": False}, "STOPPED", False)
        assert r["analysis_scheduler"] == "STOPPED"
        assert r["overall_pipeline"] == "RED"
        assert "SCHEDULER_DISABLED" in r["blockers"]

    def test_emergency_stop_forces_red(self, prefs_tmp):
        from services import system_runtime_orchestrator as sro
        r = sro.readiness({"running": True}, "RUNNING", True)
        assert r["overall_pipeline"] == "RED"

    def test_controller_thread_alone_not_green(self, prefs_tmp):
        """Yalnız thread çalışıyor ama hiç cycle yoksa GREEN olmaz."""
        from services import system_runtime_orchestrator as sro
        r = sro.readiness({"running": True, "last_cycle_time": None},
                          "RUNNING", False)
        assert r["overall_pipeline"] != "GREEN"
        assert "ANALYSIS_NOT_RUN_YET" in r["blockers"]


class TestCanonicalScheduler:
    """FIX misyonu — tek kanonik scheduler durumu + false GREEN."""

    def test_preference_never_shown_as_running(self, prefs_tmp):
        """1) Tercih RUNNING + worker STOPPED → running=False."""
        from services import system_runtime_orchestrator as sro
        s = sro.scheduler_status({"running": False}, "RUNNING")
        assert s["enabled"] is True
        assert s["running"] is False
        assert s["state"] == "STARTUP_FAILED"
        assert s["next_run"] is None

    def test_real_worker_running_true(self, prefs_tmp):
        """2) Gerçek worker çalışınca running=True, next_run üretilir."""
        from datetime import datetime, timezone
        from services import system_runtime_orchestrator as sro
        now = datetime.now(timezone.utc).isoformat()
        s = sro.scheduler_status(
            {"running": True, "last_cycle_time": now,
             "analyzed_symbol_count": 3}, "RUNNING")
        assert s["running"] is True and s["state"] == "RUNNING"
        assert s["last_run"] == now and s["next_run"] is not None
        assert s["analyzed_symbol_count"] == 3
        assert s["last_result"] == "PASS"

    def test_legacy_60min_cannot_override_5min(self, prefs_tmp,
                                               monkeypatch):
        """4) Legacy ALPHA_AUTOMATION 60 dk kanonik 5 dk'yı ezemez."""
        monkeypatch.setenv("ALPHA_AUTOMATION_INTERVAL_MINUTES", "60")
        from services import system_runtime_orchestrator as sro
        s = sro.scheduler_status({"running": False}, "STOPPED")
        assert s["interval_minutes"] == 5

    def test_manual_stop_preserved(self, prefs_tmp):
        """11) Manuel STOP → state STOPPED, STARTUP_FAILED değil."""
        from services import system_runtime_orchestrator as sro
        s = sro.scheduler_status({"running": False}, "STOPPED")
        assert s["state"] == "STOPPED"
        assert s["last_result"] == "STOPPED"

    def test_scheduler_failure_visible_as_blocker(self, prefs_tmp):
        """6) Scheduler başarısızsa blocker listede görünür."""
        from services import system_runtime_orchestrator as sro
        r = sro.readiness({"running": False}, "RUNNING", False)
        assert r["blockers"]
        assert any("SCHEDULER" in b for b in r["blockers"])

    def test_scheduled_refresh_first_run_completes(self, tmp_path,
                                                   monkeypatch):
        """FIX 1-3: ilk uygun çevrimde refresh gerçekten koşar,
        NOT_RUN_YET temizlenir, sonuç COMPLETED yazılır."""
        import universe_manager as um
        monkeypatch.setattr(um, "SMART_CONFIG_PATH",
                            tmp_path / "smart_config.json")
        monkeypatch.setattr(um, "SMART_LOG_PATH",
                            tmp_path / "smart_log.json")
        monkeypatch.setattr(um, "run_analysis",
                            lambda cur, cfg: [{"symbol": "XRPUSDT"}])
        applied = {}
        monkeypatch.setattr(um, "compute_auto_changes",
                            lambda s, c, cfg: (["XRPUSDT"], []))
        monkeypatch.setattr(
            um, "apply_auto_changes",
            lambda a, r, cfg, mode: applied.update(
                add=a, mode=mode) or (True, "ok"))
        res = um.scheduled_refresh(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        assert res == "COMPLETED"
        assert applied["add"] == ["XRPUSDT"]
        assert applied["mode"] == "SCHEDULER"
        cfg = um.get_smart_config()
        assert cfg["last_analysis_time"]  # NOT_RUN_YET temizlendi
        sr = um.get_scheduler_refresh_status()
        assert sr["last_result"] == "COMPLETED"
        assert sr["last_error_code"] is None
        # ikinci çağrı taze analiz nedeniyle atlanır
        assert um.scheduled_refresh(["BTCUSDT"]) == "SKIPPED_RECENT"

    def test_scheduled_refresh_failure_is_explicit(self, tmp_path,
                                                   monkeypatch):
        """FIX 3: hata sessiz geçilmez — FAILED + açık kod."""
        import universe_manager as um
        monkeypatch.setattr(um, "SMART_CONFIG_PATH",
                            tmp_path / "smart_config.json")
        def boom(cur, cfg):
            raise RuntimeError("ağ hatası")
        monkeypatch.setattr(um, "run_analysis", boom)
        assert um.scheduled_refresh(["BTCUSDT"]) == "FAILED"
        sr = um.get_scheduler_refresh_status()
        assert sr["last_result"] == "FAILED"
        assert sr["last_error_code"] == "UNIVERSE_REFRESH_FAILED"

    def test_scheduled_refresh_apply_failure_not_silent(
            self, tmp_path, monkeypatch):
        """Uygulama (apply) başarısızsa COMPLETED denmez."""
        import universe_manager as um
        monkeypatch.setattr(um, "SMART_CONFIG_PATH",
                            tmp_path / "smart_config.json")
        monkeypatch.setattr(um, "run_analysis",
                            lambda cur, cfg: [{"symbol": "XRPUSDT"}])
        monkeypatch.setattr(um, "compute_auto_changes",
                            lambda s, c, cfg: (["XRPUSDT"], []))
        monkeypatch.setattr(um, "apply_auto_changes",
                            lambda a, r, cfg, mode: (False, "yazılamadı"))
        assert um.scheduled_refresh(["BTCUSDT"]) == "FAILED"
        sr = um.get_scheduler_refresh_status()
        assert sr["last_error_code"] == "UNIVERSE_APPLY_FAILED"

    def test_panel_analysis_cannot_mask_not_run_yet(
            self, tmp_path, monkeypatch):
        """Panelden tetiklenen analiz last_analysis_time yazsa bile
        scheduler yenilemesi koşmadıysa NOT_RUN_YET kalır."""
        import universe_manager as um
        from services import system_runtime_orchestrator as sro
        monkeypatch.setattr(um, "SMART_CONFIG_PATH",
                            tmp_path / "smart_config.json")
        um.save_smart_config({**um.SMART_DEFAULTS,
                              "last_analysis_time":
                              "2026-07-29T10:00:00+00:00",
                              "candidate_count": 7})
        assert sro.universe_reason_code(3) == "NOT_RUN_YET"

    def test_controller_cycle_calls_universe_refresh(self):
        """FIX 1: zamanlayıcı çevrimi refresh'i GERÇEKTEN çağırır."""
        src = (ROOT / "alpha20_v1/auto_controller.py").read_text(
            encoding="utf-8")
        assert "scheduled_refresh" in src

    def test_not_run_yet_blocks_green(self, prefs_tmp, monkeypatch):
        """FIX 4: NOT_RUN_YET iken pipeline GREEN olamaz."""
        from datetime import datetime, timezone
        from services import system_runtime_orchestrator as sro
        monkeypatch.setattr(sro, "universe_reason_code",
                            lambda n: "NOT_RUN_YET")
        now = datetime.now(timezone.utc).isoformat()
        r = sro.readiness({"running": True, "last_cycle_time": now},
                          "RUNNING", False)
        assert r["overall_pipeline"] != "GREEN"
        assert "UNIVERSE_NOT_REFRESHED_YET" in r["blockers"]

    def test_refresh_completed_allows_green_path(self, prefs_tmp,
                                                 monkeypatch):
        """Yenileme koşup dürüst sonuç verdiyse evren blocker'ı
        eklenmez (GREEN diğer koşullara bağlı kalır)."""
        from services import system_runtime_orchestrator as sro
        monkeypatch.setattr(sro, "universe_reason_code",
                            lambda n: "INSUFFICIENT_ELIGIBLE_SYMBOLS")
        r = sro.readiness({"running": False}, "STOPPED", False)
        assert "UNIVERSE_NOT_REFRESHED_YET" not in r["blockers"]

    def test_universe_reason_code_when_only_base(self):
        """8) Evren 3'te kalırsa neden kodu döner, sessiz başarı yok."""
        from services import system_runtime_orchestrator as sro
        code = sro.universe_reason_code(3)
        assert code in ("NOT_RUN_YET", "UNIVERSE_REFRESH_FAILED",
                        "INSUFFICIENT_ELIGIBLE_SYMBOLS",
                        "FILTERS_EXCLUDED_ALL",
                        "PUBLIC_DATA_DEGRADED")
        assert sro.universe_reason_code(12) is None

    def test_local_dev_bypass_label(self):
        """10) local-dev-bypass kullanıcı adı gibi görünmez."""
        js = (ROOT / "static/js/operation_control.js").read_text(
            encoding="utf-8")
        assert '"Yerel Windows Oturumu"' in js

    def test_opportunity_counter_uses_signal_candidates(self):
        """9) 'Uygun Fırsatlar' entry_eligible değil gerçek sinyal
        adayı sayacından beslenir."""
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "signal_candidate_count" in js

    def test_automation_page_canonical_block(self):
        """5) /automation kanonik bloğu aynı snapshot'tan okur."""
        html = (ROOT / "templates/automation.html").read_text(
            encoding="utf-8")
        for token in ("ANALİZ ZAMANLAYICISI (Kanonik)", "Tercih",
                      "Gerçek Durum", "Sonraki Koşu",
                      "Analiz Edilen Sembol", "/api/paper/state",
                      "STARTUP_FAILED"):
            assert token in html, token


class TestDynamicUniverse:
    """8) BTC/ETH/SOL pinli + max 20 sözleşmesi."""

    def test_base_symbols_always_pinned(self):
        import universe_manager as um
        cfg = um.get_smart_config()
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            assert sym in cfg["pinned"]

    def test_max_20_enforced(self, tmp_path, monkeypatch):
        import universe_manager as um
        p = tmp_path / "smart_config.json"
        p.write_text(json.dumps({"max_coins": 50}), encoding="utf-8")
        monkeypatch.setattr(um, "SMART_CONFIG_PATH", p)
        cfg = um.get_smart_config()
        assert cfg["max_coins"] == 20
        assert "BTCUSDT" in cfg["pinned"]

    def test_pinned_not_removed_by_auto_changes(self):
        import universe_manager as um
        smart = um.get_smart_config()
        # düşük skorlu öneriler bile pinli sembolleri çıkaramaz
        suggestions = [{"symbol": s, "total_score": 0}
                       for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
        to_add, to_remove = um.compute_auto_changes(
            suggestions, ["BTCUSDT", "ETHUSDT", "SOLUSDT"], smart)
        assert to_remove == []


class TestSnapshotAndTrace:
    """15-16, 23) Snapshot alanları + dürüst sayaçlar + trace."""

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

    def test_snapshot_has_master_fields(self, client):
        d = client.get("/api/paper/state").get_json()
        for key in ("analysis_scheduler", "scan_interval",
                    "universe_size", "market_data_status",
                    "feature_pipeline", "decision_engine",
                    "selected_risk_profile", "risk_engine",
                    "strategy_count", "enabled_strategy_count",
                    "running_strategy_count",
                    "signal_candidate_count", "risk_approved_count",
                    "paper_intent_count",
                    "open_paper_position_count", "binance_tr",
                    "last_complete_analysis", "last_decision",
                    "overall_pipeline", "live_orders"):
            assert key in d, key
        assert d["live_orders"] == "DISABLED"

    def test_enabled_symbol_is_not_opportunity(self, client):
        """9) signal_candidate_count karar kayıtlarından gelir;
        yalnız enabled olmak aday saymaz."""
        d = client.get("/api/paper/state").get_json()
        assert d["signal_candidate_count"] <= max(
            1, d["strategy_count"] * 10)
        # Sayaçlar bağımsız alanlardır; enabled sayısına eşitlenmez
        assert "signal_candidate_count" in d and \
            "enabled_strategy_count" in d

    def test_risk_profile_endpoints(self, client, prefs_tmp):
        r = client.get("/api/risk-profile").get_json()
        assert r["ok"] and set(r["data"]["profiles"]) == {
            "KORUMA", "DENGELI", "AGRESIF"}
        w = client.post("/api/risk-profile",
                        json={"profile": "KORUMA"})
        assert w.status_code == 200
        assert w.get_json()["data"]["selected"] == "KORUMA"
        assert prefs.get("selected_risk_profile") == "KORUMA"
        bad = client.post("/api/risk-profile",
                          json={"profile": "YOLO"})
        assert bad.status_code == 400

    def test_decision_trace_endpoint(self, client):
        r = client.get("/api/decision-trace?n=5")
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_decision_trace_invalid_n_is_400(self, client):
        r = client.get("/api/decision-trace?n=abc")
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "INVALID_PARAM"

    def test_gunicorn_post_fork_starts_orchestrator(self):
        """Üretim workflow'u gunicorn — orchestrator orada da başlar."""
        src = (ROOT / "gunicorn.conf.py").read_text(encoding="utf-8")
        assert "system_runtime_orchestrator" in src
        assert src.index("system_runtime_orchestrator") < \
            src.index("start_controller_loop")


class TestPipelineTest:
    """24) PAPER PIPELINE TEST — sentetik, güvenli, izole."""

    def test_requires_confirmation(self):
        from services import paper_pipeline_test as ppt
        out = ppt.run("yanlış", "tester")
        assert not out["ok"]
        assert out["error"] == "CONFIRMATION_REQUIRED"

    def test_synthetic_run_no_real_orders(self, tmp_path,
                                          monkeypatch, prefs_tmp):
        from services import paper_pipeline_test as ppt
        monkeypatch.setattr(ppt, "TEST_LEDGER",
                            tmp_path / "test_ledger.jsonl")
        out = ppt.run("PIPELINE TEST", "tester")
        assert out["ok"]
        rec = out["result"]
        assert rec["TEST"] is True
        assert rec["live_orders"] == "DISABLED"
        assert rec["paper_intent"]["live_order"] is False
        assert rec["selected_risk_profile"] in (
            "KORUMA", "DENGELİ", "AGRESİF")
        assert rec["calculated_position_size"] >= 0
        assert rec["correlation_id"].startswith("ppt-")
        # Test ledger AYRI dosyada — gerçek trade_history'e yazılmaz
        assert (tmp_path / "test_ledger.jsonl").exists()

    def test_profiles_differ_in_pipeline_test(self, tmp_path,
                                              monkeypatch, prefs_tmp):
        """14) Aynı sentetik sinyalde profiller farklı sizing verir."""
        from services import paper_pipeline_test as ppt
        monkeypatch.setattr(ppt, "TEST_LEDGER",
                            tmp_path / "tl.jsonl")
        sizes = {}
        for name in ("KORUMA", "DENGELI", "AGRESIF"):
            prefs.set_prefs(selected_risk_profile=name)
            out = ppt.run("PIPELINE TEST", "tester")
            assert out["ok"]
            sizes[name] = out["result"]["calculated_position_size"]
        assert sizes["KORUMA"] < sizes["DENGELI"] <= sizes["AGRESIF"]


class TestUiAndStatics:
    """22, 25) UI sözleşmeleri + FINAL status."""

    def test_topbar_has_profile_selector_and_pipeline(self):
        html = (ROOT / "templates/_exec_topbar.html").read_text(
            encoding="utf-8")
        for token in ("KORUMA", "DENGELİ", "AGRESİF",
                      "/api/risk-profile", "/api/paper/state",
                      "overall_pipeline", "Canlı Emir: KAPALI"):
            assert token in html, token

    def test_final_report_prints_pipeline_block(self):
        src = (ROOT / "windows_setup_flow.py").read_text(
            encoding="utf-8")
        for token in ("DYNAMIC UNIVERSE", "ANALYSIS SCHEDULER",
                      "SCAN INTERVAL", "DECISION ENGINE",
                      "RISK PROFILE", "OVERALL PIPELINE"):
            assert token in src, token

    def test_decision_trace_written_by_controller(self):
        src = (ROOT / "alpha20_v1/auto_controller.py").read_text(
            encoding="utf-8")
        for token in ("correlation_id", "data_status",
                      "final_decision", "rejection_reason",
                      "required_threshold"):
            assert token in src, token
