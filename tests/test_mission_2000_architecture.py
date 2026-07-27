"""Mission 2000 — Agent 01 mimari doğrulama testleri.

`docs/architecture/execution_foundation.md` dondurulmuş Execution
mimarisini tanımlar: bağımlılık grafiği, sahiplik, kamu API, meta veri
sahipliği, yasak bağımlılıklar, yaşam döngüsü, Monitoring/Strategy/
Exchange izolasyonu ve üretim davranışı yokluğu doğrulanır.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys

import pytest

# Proje kökünü içe aktarma yoluna ekle (varsayılan pytest
# çağrısında da taşınabilir olsun diye)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DOC_PATH = os.path.join(
    _ROOT, "docs", "architecture", "execution_foundation.md")

with open(DOC_PATH, encoding="utf-8") as handle:
    DOC = handle.read()

# Mimarinin tanımladığı (henüz UYGULANMAMIŞ) yürütme modülleri
PLANNED_MODULES = (
    "execution_api.py", "execution_service.py",
    "execution_risk_engine.py", "execution_kill_switch.py",
    "broker_adapter.py", "binance_spot_adapter.py")

LAYER_ORDER = (
    "Execution API", "Execution Service", "Risk Engine",
    "Kill Switch", "Exchange Adapter", "Exchange Implementation")


def _module_imports(module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


# ── Belge varlığı ve bölümler ────────────────────────────────────────

class TestDocument:
    def test_document_exists_and_nonempty(self):
        assert len(DOC) > 2000

    def test_frozen_declared(self):
        assert "DONDURULMUŞ" in DOC

    def test_baseline_recorded(self):
        assert "a79415e" in DOC
        assert "2207 PASS" in DOC

    @pytest.mark.parametrize("section", [
        "## 1. Onaylı Yığın",
        "## 2. Katman Sorumlulukları",
        "## 3. Sahiplik Kuralları",
        "## 4. Onaylı Bağımlılık Grafiği",
        "## 5. Yürütme Yaşam Döngüsü",
        "## 6. Meta Veri Sahipliği",
        "## 7. Onaylı Kamu API'si",
        "## 8. Güvenlik Modeli",
        "## 9. Kapsam Dışı",
    ])
    def test_required_sections_present(self, section):
        assert section in DOC


# ── Mimari diyagramı ve zincir sırası ────────────────────────────────

class TestArchitectureDiagram:
    @pytest.mark.parametrize("layer", LAYER_ORDER)
    def test_all_layers_documented(self, layer):
        assert layer in DOC

    def test_layer_order_in_stack_diagram(self):
        positions = [DOC.index(layer) for layer in LAYER_ORDER]
        assert positions == sorted(positions)

    def test_upstream_stack_documented(self):
        for name in ("Portfolio Intelligence", "Strategy Intelligence",
                     "Monitoring"):
            assert name in DOC

    def test_planned_module_files_documented(self):
        for module_file in PLANNED_MODULES:
            assert module_file in DOC

    def test_future_adapters_documented(self):
        for adapter in ("BinanceSpotAdapter",
                        "BinanceFuturesAdapter",
                        "InteractiveBrokersAdapter", "MidasAdapter",
                        "BybitAdapter", "OKXAdapter",
                        "KrakenAdapter"):
            assert adapter in DOC

    def test_mandatory_execution_chain_documented(self):
        assert ("Execution Service → Risk Engine → Kill Switch "
                "→ Exchange Adapter") in DOC


# ── Bağımlılık grafiği ve yasaklar ───────────────────────────────────

class TestDependencyGraph:
    def test_graph_documented_in_order(self):
        graph = DOC[DOC.index("## 4."):DOC.index("## 5.")]
        positions = [graph.index(layer) for layer in LAYER_ORDER]
        assert positions == sorted(positions)

    @pytest.mark.parametrize("forbidden", [
        "ters bağımlılık", "döngüsel bağımlılık", "katman atlama",
        "çapraz sahiplik"])
    def test_forbidden_dependencies_documented(self, forbidden):
        assert forbidden in DOC

    def test_strategy_cannot_reach_exchange_documented(self):
        assert ("Hiçbir Strategy katmanı bir Exchange ile doğrudan "
                "iletişim") in DOC


# ── Sahiplik ─────────────────────────────────────────────────────────

class TestOwnership:
    @pytest.mark.parametrize("ownership", [
        "İstek doğrulama", "execution_id", "yanıt zarfı",
        "Emir yaşam döngüsü orkestrasyonu", "durum geçişleri",
        "Maruziyet doğrulama", "pozisyon boyutlama", "günlük limitler",
        "Acil durdurma", "devre kesici", "zorunlu ret",
        "Exchange soyutlaması", "yanıt normalizasyonu",
        "REST/WebSocket protokolü", "imzalama"])
    def test_layer_ownership_documented(self, ownership):
        assert ownership in DOC

    @pytest.mark.parametrize("rule", [
        "Execution API emir YÜRÜTEMEZ",
        "Execution Service risk HESAPLAYAMAZ",
        "Risk Engine exchange ile İLETİŞEMEZ",
        "Kill Switch strateji HESAPLAYAMAZ",
        "Exchange Adapter iş mantığı İÇEREMEZ",
        "Exchange Implementation risk mantığı İÇEREMEZ"])
    def test_must_not_rules_documented(self, rule):
        assert rule in DOC

    def test_no_ownership_overlap_declared(self):
        assert "Sahiplik örtüşmesi YOKTUR" in DOC


# ── Yaşam döngüsü ────────────────────────────────────────────────────

class TestLifecycle:
    def test_lifecycle_steps_in_order(self):
        steps = ("Execution Request", "Risk Validation",
                 "Kill Switch Validation", "Exchange Translation",
                 "Exchange Execution", "Exchange Response",
                 "Normalized Result")
        section = DOC[DOC.index("## 5."):DOC.index("## 6.")]
        positions = [section.index(step) for step in steps]
        assert positions == sorted(positions)

    def test_monitoring_read_only_declared(self):
        assert "Monitoring SALT-OKUNUR kalır" in DOC
        assert "Monitoring asla emir yürütmez" in DOC


# ── Meta veri sahipliği ──────────────────────────────────────────────

class TestMetadataOwnership:
    @pytest.mark.parametrize("field", [
        "execution_id", "requested_at", "processed_at"])
    def test_metadata_fields_documented(self, field):
        assert field in DOC

    def test_only_api_generates(self):
        assert "YALNIZ Execution API üretir" in DOC
        assert "yalnız TAŞIR" in DOC


# ── Kamu API ─────────────────────────────────────────────────────────

class TestPublicApi:
    APPROVED = ("execute_order_api", "execute_order",
                "ExecutionService", "validate_execution",
                "verify_execution", "BrokerAdapter",
                "BinanceSpotAdapter")

    @pytest.mark.parametrize("entry", APPROVED)
    def test_approved_entries_documented(self, entry):
        assert f"`{entry}`" in DOC

    def test_entry_count_is_seven(self):
        assert len(self.APPROVED) == 7
        assert "toplam 7 giriş" in DOC

    def test_no_additional_apis_declared(self):
        assert "Ek kamu API YOKTUR" in DOC


# ── Güvenlik modeli ──────────────────────────────────────────────────

class TestSecurityModel:
    @pytest.mark.parametrize("isolation", [
        "Exchange izolasyonu", "Risk izolasyonu",
        "Kill Switch izolasyonu", "Strategy izolasyonu",
        "Monitoring izolasyonu"])
    def test_isolations_documented(self, isolation):
        assert isolation in DOC

    @pytest.mark.parametrize("guarantee", [
        "gizli yürütme yolu YOK", "doğrudan exchange erişimi YOK",
        "arka planda yürütme YOK", "otomatik yeniden deneme YOK",
        "kalıcılık\nYOK", "zamanlayıcı YOK"])
    def test_negative_guarantees_documented(self, guarantee):
        assert guarantee in DOC

    def test_live_trading_disabled(self):
        assert "DISABLED" in DOC

    def test_no_secret_values_in_doc(self):
        for token in ("BINANCE_API_KEY", "BINANCE_API_SECRET",
                      "SESSION_SECRET", "PASSWORD_HASH"):
            assert token not in DOC


# ── Üretim davranışı yokluğu (mimari-yalnız) ─────────────────────────

class TestNoProductionBehavior:
    def test_planned_modules_not_yet_implemented(self):
        # Yalnız teslim edilen ajanların modülleri mevcut olabilir.
        # Teslim edildi: Agent 03 risk engine, Agent 04 kill switch
        delivered = {"execution_risk_engine.py",
                     "execution_kill_switch.py"}
        for module_file in PLANNED_MODULES:
            if module_file in delivered:
                assert os.path.exists(
                    os.path.join(_ROOT, module_file))
            else:
                assert not os.path.exists(
                    os.path.join(_ROOT, module_file))

    def test_out_of_scope_documented(self):
        for item in ("HMAC", "WebSocket", "Paper\nTrading",
                     "Shadow Trading", "Micro Live"):
            assert item in DOC

    def test_monitoring_stack_untouched(self):
        import monitoring_security
        report = monitoring_security.verify_monitoring_security()
        assert report["verified"] is True
        assert report["violations"] == ()

    def test_monitoring_has_no_execution_path(self):
        import alert_engine
        import monitoring_api
        import monitoring_export
        import monitoring_intelligence
        import monitoring_security
        import monitoring_service
        for module in (monitoring_intelligence, alert_engine,
                       monitoring_service, monitoring_api,
                       monitoring_export, monitoring_security):
            text = inspect.getsource(module)
            for token in ("execute_order", "place_order",
                          "create_order", "submit_order"):
                assert token not in text

    def test_strategy_layers_do_not_import_execution(self):
        import strategy_service
        forbidden = {"execution_api", "execution_service",
                     "execution_risk_engine", "execution_kill_switch",
                     "broker_adapter", "binance_spot_adapter"}
        assert not _module_imports(strategy_service) & forbidden

    def test_monitoring_does_not_import_execution(self):
        import monitoring_service
        forbidden = {"execution_api", "execution_service",
                     "execution_risk_engine", "execution_kill_switch",
                     "broker_adapter", "binance_spot_adapter"}
        assert not _module_imports(monitoring_service) & forbidden

    def test_doc_introduces_no_code(self):
        # Belge markdown'dır; python kod bloğu içermez
        assert "```python" not in DOC
