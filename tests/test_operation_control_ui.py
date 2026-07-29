"""Mission 2200 — Agent 01: Operasyon Merkezi UI sözleşme testleri.

Şablon + JS statik içerik denetimi: tarayıcı hiçbir zaman borsaya
doğrudan gitmez, yıkıcı eylemler onay diyaloğu ister, CSRF başlığı
her POST'ta bulunur.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "templates" /
            "operation_control.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "js" /
          "operation_control.js").read_text(encoding="utf-8")


class TestTemplateContract:
    @pytest.mark.parametrize("section_id", [
        "oc-status-grid", "oc-auto-state", "oc-products",
        "oc-positions", "oc-orders", "oc-signals",
        "oc-recon", "oc-risk-grid", "oc-kill",
        "oc-stop-entries", "oc-close-all", "oc-audit"])
    def test_section_present(self, section_id):
        assert section_id in TEMPLATE

    def test_confirm_dialog_present(self):
        assert "<dialog" in TEMPLATE
        assert "oc-dialog" in TEMPLATE
        assert "oc-dialog-phrase" in TEMPLATE
        assert "oc-dialog-reason" in TEMPLATE

    def test_csrf_token_exposed(self):
        assert "OC_CSRF" in TEMPLATE

    def test_reconcile_status_present(self):
        # Son restart reconcile kararı /health/runtime'dan beslenir;
        # kayıt yoksa "kayıt yok" gösterilir (görev: karar bağlamı
        # Operasyon Kontrol sayfasında da görünsün).
        assert "oc-reconcile" in TEMPLATE
        assert "/health/runtime" in TEMPLATE
        assert "paper_reconcile" in TEMPLATE
        assert "kayıt yok" in TEMPLATE
        for result in ("RESTORED_RUNNING", "BLOCKED_EMERGENCY",
                       "PRESERVED_STOPPED", "LIVE_FAIL_CLOSED", "ERROR"):
            assert result in TEMPLATE

    def test_paper_intent_disclosed(self):
        # Kapatma gerçek borsa pozisyonu kapattığını İDDİA ETMEZ.
        assert "PAPER" in TEMPLATE

    def test_unsupported_buttons_disabled(self):
        assert "disabled" in TEMPLATE

    def test_no_inline_exchange_calls(self):
        for token in ("binance", "api.binance", "wss://", "ccxt"):
            assert token not in TEMPLATE.lower().replace(
                "binance tr", "")

    def test_script_included(self):
        assert "operation_control.js" in TEMPLATE


class TestScriptContract:
    def test_csrf_header_on_posts(self):
        assert "X-CSRFToken" in SCRIPT

    def test_confirmation_phrase_required(self):
        assert "confirm_phrase" in SCRIPT

    def test_reason_required(self):
        assert "reason" in SCRIPT

    def test_idempotency_key_generated(self):
        assert "idempotency_key" in SCRIPT

    def test_auto_refresh_configured(self):
        assert "15000" in SCRIPT or "15_000" in SCRIPT

    def test_pending_lock(self):
        assert "disabled" in SCRIPT

    def test_only_relative_api_calls(self):
        assert "/api/operation-control/" in SCRIPT
        for token in ("http://", "https://", "wss://", "ws://"):
            assert token not in SCRIPT

    def test_no_secret_literals(self):
        lowered = SCRIPT.lower()
        for token in ("api_key", "apikey", "secret",
                      "password", "bearer "):
            assert token not in lowered

    def test_stale_banner_handled(self):
        assert "STALE" in SCRIPT

    def test_no_eval(self):
        assert "eval(" not in SCRIPT
        assert "new Function" not in SCRIPT
