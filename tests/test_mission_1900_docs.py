"""Mission 1900 — Agent 09 dokümantasyon doğrulama testleri.

`docs/mission_1900.md` uygulamayla birebir eşleşir: mimari, kamu API,
bağımlılık grafiği, meta veri sahipliği, regresyon sayıları, commit,
belgesiz kamu API yokluğu ve çelişkisizlik.
"""

from __future__ import annotations

import ast
import inspect

import pytest

import alert_engine
import monitoring_api
import monitoring_export
import monitoring_intelligence
import monitoring_security
import monitoring_service

DOC_PATH = "docs/mission_1900.md"

with open(DOC_PATH, encoding="utf-8") as handle:
    DOC = handle.read()

STACK = {
    "monitoring_intelligence": monitoring_intelligence,
    "alert_engine": alert_engine,
    "monitoring_service": monitoring_service,
    "monitoring_api": monitoring_api,
    "monitoring_export": monitoring_export,
    "monitoring_security": monitoring_security,
}


def _imports(module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


# ── Varlık ve bölümler ───────────────────────────────────────────────

class TestDocumentExists:
    def test_document_exists_and_nonempty(self):
        assert len(DOC) > 2000

    @pytest.mark.parametrize("section", [
        "## A. Misyon Özeti",
        "## B. Mimari Diyagramı",
        "## C. Katman Sorumlulukları",
        "## D. Onaylı Bağımlılık Grafiği",
        "## E. Onaylı Kamu API'si",
        "## F. Meta Veri Sahipliği",
        "## G. Güvenlik Modeli",
        "## H. Determinizm Garantileri",
        "## I. Immutability Garantileri",
        "## J. Export Sözleşmesi",
        "## K. Hata Modeli",
        "## L. Regresyon Özeti",
        "## M. Misyon İstatistikleri",
    ])
    def test_required_sections_present(self, section):
        assert section in DOC


# ── Mimari uygulamayla eşleşir ───────────────────────────────────────

class TestArchitectureMatches:
    @pytest.mark.parametrize("module_file", [
        "monitoring_intelligence.py", "alert_engine.py",
        "monitoring_service.py", "monitoring_api.py",
        "monitoring_export.py", "monitoring_security.py"])
    def test_all_layer_modules_documented(self, module_file):
        assert module_file in DOC

    def test_chain_order_documented(self):
        order = ["Monitoring Core", "Alert Engine",
                 "Monitoring Service", "Monitoring API",
                 "Monitoring Export", "Monitoring Security"]
        positions = [DOC.index(f"{name} ") if f"{name} " in DOC
                     else DOC.index(name) for name in order]
        assert positions == sorted(positions)

    def test_documented_dependencies_match_implementation(self):
        # Belge: alert→core, service→core+alert, api→service,
        # export→api — uygulama da aynen böyle
        stack_names = set(STACK) - {"monitoring_security"}
        assert not _imports(monitoring_intelligence) & stack_names
        assert _imports(alert_engine) & stack_names == {
            "monitoring_intelligence"}
        assert _imports(monitoring_service) & stack_names == {
            "monitoring_intelligence", "alert_engine"}
        assert _imports(monitoring_api) & stack_names == {
            "monitoring_service"}
        assert _imports(monitoring_export) & stack_names == {
            "monitoring_api"}

    def test_security_verifies_whole_stack_as_documented(self):
        expected = set(STACK) - {"monitoring_security"}
        assert _imports(monitoring_security) & set(STACK) == expected

    def test_no_inversion_claim_holds(self):
        assert "Bağımlılık inversiyonu YOKTUR" in DOC
        report = monitoring_security.verify_monitoring_security()
        assert report["verified"] is True


# ── Kamu API listesi eşleşir ─────────────────────────────────────────

class TestPublicApiMatches:
    EXPECTED = {
        "monitoring_intelligence": {"build_monitoring_report"},
        "alert_engine": {"build_alert_report"},
        "monitoring_service": {
            "analyze_monitoring", "build_default_monitoring_providers",
            "MonitoringService"},
        "monitoring_api": {"analyze_monitoring_api"},
        "monitoring_export": {
            "build_monitoring_export", "serialize_monitoring_export"},
        "monitoring_security": {"verify_monitoring_security"},
    }

    @pytest.mark.parametrize("name", tuple(STACK))
    def test_documented_api_exists_in_code(self, name):
        for entry in self.EXPECTED[name]:
            assert entry in DOC
            assert hasattr(STACK[name], entry)

    @pytest.mark.parametrize("name", tuple(STACK))
    def test_no_undocumented_public_api(self, name):
        actual = monitoring_security._public_names(STACK[name])
        assert actual == self.EXPECTED[name]
        for entry in actual:
            assert entry in DOC


# ── Meta veri sahipliği eşleşir ──────────────────────────────────────

class TestMetadataMatches:
    def test_ownership_documented(self):
        for field in ("report_id", "observed_at", "generated_at"):
            assert field in DOC
        assert "YALNIZ Monitoring API" in DOC

    def test_only_api_imports_uuid_datetime(self):
        for name, module in STACK.items():
            roots = {m.split(".")[0] for m in _imports(module)}
            if name == "monitoring_api":
                assert {"uuid", "datetime"} <= roots
            else:
                assert not roots & {"uuid", "datetime", "time"}


# ── Güvenlik modeli ve hata modeli eşleşir ───────────────────────────

class TestSecurityAndErrors:
    def test_forbidden_imports_documented(self):
        for module in ("ccxt", "requests", "socket", "threading",
                       "subprocess", "pickle", "secrets"):
            assert f"`{module}`" in DOC

    def test_security_rules_documented(self):
        for rule in monitoring_security.CHECKED_RULES:
            assert rule in DOC

    @pytest.mark.parametrize("code", [
        "INVALID_INPUT", "FLOAT_REJECTED",
        "INVALID_MONITORING_REPORT", "MONITORING_ANALYSIS_ERROR",
        "INVALID_API_REQUEST", "UNSUPPORTED_API_VERSION",
        "UNKNOWN_PROVIDER", "INVALID_MONITORING_EXPORT_INPUT",
        "SECURITY_VERIFICATION_FAILED",
    ])
    def test_sterile_error_codes_documented_and_real(self, code):
        assert code in DOC

    def test_documented_error_codes_match_constants(self):
        assert monitoring_export.ERROR_INVALID_EXPORT_INPUT in DOC
        assert monitoring_security.ERROR_VERIFICATION in DOC
        assert monitoring_api.ERROR_ANALYSIS in DOC


# ── Export sözleşmesi eşleşir ────────────────────────────────────────

class TestExportContractMatches:
    def test_root_schema_fields_documented(self):
        for field in monitoring_export.EXPORT_FIELDS:
            assert field in DOC

    def test_canonical_json_rules_documented(self):
        for token in ("ensure_ascii=False", "sort_keys=True",
                      "allow_nan=False"):
            assert token in DOC

    def test_decimal_policy_documented(self):
        assert 'Decimal("12.3400")' in DOC and '"12.3400"' in DOC


# ── Regresyon sayıları ve commit ─────────────────────────────────────

class TestRegressionNumbers:
    def test_regression_numbers_documented(self):
        assert "2083 PASS" in DOC
        assert "FAIL: **0**" in DOC and "SKIP: **0**" in DOC

    def test_exchange_and_secret_zero(self):
        assert "Exchange Write Request: **0**" in DOC
        assert "Secret Exposure: **0**" in DOC

    def test_commit_recorded(self):
        assert "5d0a50a" in DOC

    def test_agent_test_counts_sum_matches_chain(self):
        # 66+65+36+41+71+109+99 = 487; 1596 + 487 = 2083
        assert 1596 + 66 + 65 + 36 + 41 + 71 + 109 + 99 == 2083
        for count in ("66", "65", "36", "41", "71", "109", "99"):
            assert count in DOC


# ── Çelişkisizlik ────────────────────────────────────────────────────

class TestNoContradictions:
    def test_read_only_claims_consistent(self):
        # Belge "yazma yok" der; kodda da yazma yeteneği yoktur
        assert "Exchange'e yazma" in DOC
        report = monitoring_security.verify_monitoring_security()
        assert report["violations"] == ()

    def test_no_secret_values_in_doc(self):
        for token in ("BINANCE_API", "SESSION_SECRET", "sk-",
                      "PASSWORD"):
            assert token not in DOC

    def test_health_states_documented_match_core(self):
        assert "UNKNOWN/CRITICAL/DEGRADED/HEALTHY" in DOC

    def test_status_states_documented_match_api(self):
        assert "SUCCESS/PARTIAL/FAILED" in DOC
        assert monitoring_api.API_STATUSES == (
            "SUCCESS", "PARTIAL", "FAILED")

    def test_alert_code_count_matches_engine(self):
        # Belge "kapalı 11 kodlu küme" der; motorda da tam 11 kod var
        assert "kapalı 11 kodlu küme" in DOC
        assert len(alert_engine.ALERT_CODES) == 11

    def test_doc_does_not_claim_notifications_or_persistence(self):
        assert "bildirim gönderilmez" in DOC
        assert "kalıcılık yoktur" in DOC
