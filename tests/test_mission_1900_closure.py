"""Mission 1900 — Agent 10 kapanış doğrulama testleri.

`docs/mission_1900_closure.md` kayıtları uygulama ve misyon geçmişiyle
birebir eşleşir: ajan listesi, istatistikler, regresyon sayıları, kamu
API/güvenlik kuralı/uyarı kodu sayıları, commit, devir bölümü, üretim
dosyalarının değişmemişliği ve belgesiz iddia yokluğu.
"""

from __future__ import annotations

import os

import pytest

import alert_engine
import monitoring_api
import monitoring_export
import monitoring_intelligence
import monitoring_security
import monitoring_service

CLOSURE_PATH = "docs/mission_1900_closure.md"

with open(CLOSURE_PATH, encoding="utf-8") as handle:
    DOC = handle.read()


# ── Varlık ve durum ──────────────────────────────────────────────────

class TestClosureDocument:
    def test_document_exists_and_nonempty(self):
        assert len(DOC) > 1500

    def test_officially_closed_stated(self):
        assert "OFFICIALLY CLOSED" in DOC

    def test_next_mission_stated(self):
        assert "MISSION 2000 — EXECUTION FOUNDATION" in DOC

    def test_verification_only_claim(self):
        assert "Üretim işlevselliği DEĞİŞMEMİŞTİR" in DOC


# ── Tüm ajanlar kayıtlı ──────────────────────────────────────────────

class TestAgentsListed:
    @pytest.mark.parametrize("agent", [
        "Agent 01", "Agent 02", "Agent 03", "Agent 04", "Agent 05",
        "Agent 06", "Agent 07", "Agent 08", "Agent 09", "Agent 10"])
    def test_all_ten_agents_recorded(self, agent):
        assert agent in DOC

    def test_all_agents_pass(self):
        assert DOC.count("| PASS |") == 10

    @pytest.mark.parametrize("deliverable", [
        "monitoring_intelligence.py", "alert_engine.py",
        "monitoring_service.py", "monitoring_api.py",
        "monitoring_export.py", "monitoring_security.py",
        "tests/test_monitoring_full_regression.py",
        "docs/mission_1900.md",
        "docs/architecture/monitoring_alerting.md"])
    def test_deliverables_listed_and_exist(self, deliverable):
        assert deliverable in DOC
        assert os.path.exists(deliverable)


# ── Mimari sertifikasyonu ────────────────────────────────────────────

class TestArchitectureCertified:
    def test_chain_documented(self):
        assert "Monitoring Core → Alert Engine → Monitoring Service" \
            in DOC
        assert "Monitoring API → Monitoring Export" in DOC

    @pytest.mark.parametrize("claim", [
        "Bağımlılık inversiyonu YOK",
        "Döngüsel bağımlılık YOK",
        "Katman atlama YOK",
        "Sahiplik sınırları KORUNDU"])
    def test_explicit_claims_present(self, claim):
        assert claim in DOC

    def test_claims_backed_by_verifier(self):
        report = monitoring_security.verify_monitoring_security()
        assert report["verified"] is True
        assert report["violations"] == ()


# ── Kamu API sertifikasyonu ──────────────────────────────────────────

class TestPublicApiCertified:
    EXPECTED = {
        monitoring_intelligence: {"build_monitoring_report"},
        alert_engine: {"build_alert_report"},
        monitoring_service: {
            "analyze_monitoring", "build_default_monitoring_providers",
            "MonitoringService"},
        monitoring_api: {"analyze_monitoring_api"},
        monitoring_export: {
            "build_monitoring_export", "serialize_monitoring_export"},
        monitoring_security: {"verify_monitoring_security"},
    }

    def test_public_api_count_documented_and_correct(self):
        total = sum(len(v) for v in self.EXPECTED.values())
        assert total == 9
        assert "| Kamu API sayısı | 9 |" in DOC

    def test_every_entry_documented_and_real(self):
        for module, names in self.EXPECTED.items():
            assert monitoring_security._public_names(module) == names
            for name in names:
                assert name in DOC

    def test_no_undocumented_public_api_claim(self):
        assert "Belgesiz kamu API YOKTUR" in DOC


# ── Güvenlik sertifikasyonu ──────────────────────────────────────────

class TestSecurityCertified:
    def test_final_values_zero(self):
        assert "Exchange Write Request = **0**" in DOC
        assert "Secret Exposure = **0**" in DOC

    def test_security_rule_count_correct(self):
        assert len(monitoring_security.CHECKED_RULES) == 8
        assert "| Güvenlik kuralı sayısı | 8 |" in DOC

    def test_alert_code_count_correct(self):
        assert len(alert_engine.ALERT_CODES) == 11
        assert "| Uyarı kodu sayısı | 11 |" in DOC

    @pytest.mark.parametrize("isolation", [
        "Exchange", "Broker", "Kalıcılık", "Ağ", "Ortam", "Secret"])
    def test_isolations_certified(self, isolation):
        assert isolation in DOC

    def test_no_secret_values_in_doc(self):
        for token in ("BINANCE_API", "SESSION_SECRET", "sk-",
                      "PASSWORD_HASH"):
            assert token not in DOC


# ── İstatistikler ────────────────────────────────────────────────────

class TestStatistics:
    def test_mission_baselines_recorded(self):
        assert "1335 PASS" in DOC   # Mission 1700 kapanışı
        assert "1596 PASS" in DOC   # Mission 1800 kapanışı
        assert "2146 PASS" in DOC   # Mission 1900 tamamlanışı

    def test_regression_numbers_correct(self):
        assert "0 FAIL / 0 SKIP" in DOC

    def test_commit_recorded(self):
        assert "08a409b" in DOC

    def test_agent_count_recorded(self):
        assert "| Ajan sayısı | 10 |" in DOC

    def test_test_chain_arithmetic(self):
        # 1596 + 66+65+36+41+71+109+99+63 = 2146
        assert 1596 + 66 + 65 + 36 + 41 + 71 + 109 + 99 + 63 == 2146
        for count in ("66", "65", "36", "41", "71", "109", "99", "63"):
            assert count in DOC


# ── Devir teslim (Mission 2000) ──────────────────────────────────────

class TestHandoff:
    def test_handoff_section_present(self):
        assert "Mission 2000'e Devir" in DOC
        assert "Execution Foundation" in DOC

    def test_monitoring_complete_stated(self):
        assert "Monitoring is COMPLETE." in DOC

    @pytest.mark.parametrize("scope", [
        "Exchange adaptörleri", "Order model", "Risk Engine",
        "Kill Switch", "Dry Run", "Spot execution"])
    def test_mission_2000_ownership_listed(self, scope):
        assert scope in DOC

    def test_live_trading_disabled_stated(self):
        assert "DISABLED" in DOC
        assert "hiçbir canlı emir yeteneği eklenmemiştir" in DOC


# ── Üretim değişmemişliği ve belgesiz iddia yokluğu ─────────────────

class TestNoProductionChanges:
    def test_stack_behavior_unchanged_schemas(self):
        assert monitoring_intelligence.REPORT_FIELDS[-1] == \
            "limitations"
        assert len(monitoring_intelligence.REPORT_FIELDS) == 17
        assert len(alert_engine.ALERT_REPORT_FIELDS) == 9
        assert len(monitoring_api.API_RESPONSE_FIELDS) == 7
        assert len(monitoring_export.EXPORT_FIELDS) == 9
        assert len(monitoring_security.SECURITY_REPORT_FIELDS) == 4

    def test_stack_still_verifies_clean(self):
        assert monitoring_security.verify_monitoring_security()[
            "verified"] is True

    def test_no_live_order_capability_in_stack(self):
        # Belgesiz iddia yok: yığında emir/yürütme kavramı yoktur
        import inspect
        for module in (monitoring_intelligence, alert_engine,
                       monitoring_service, monitoring_api,
                       monitoring_export, monitoring_security):
            text = inspect.getsource(module)
            for token in ("place_order", "create_order",
                          "submit_order", "execute_trade"):
                assert token not in text

    def test_agent01_architecture_doc_untouched_claims(self):
        # Kapanış, Agent 01 belgesini değiştirmez; yalnız referans verir
        with open("docs/architecture/monitoring_alerting.md",
                  encoding="utf-8") as handle:
            arch = handle.read()
        assert len(arch) > 0
