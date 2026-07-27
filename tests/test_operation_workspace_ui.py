"""Mission 2200 Agent 02 — çalışma alanı arayüz/erişilebilirlik testleri.

Şablon ve istemci JS kaynak sözleşmelerini doğrular:
- Tam ekran çalışma alanı bölmeleri ve kimlikleri
- Agent 01 `oc-*` kimliklerinin korunması
- Sahte düğme YOK: her düğme ya gerçek uca bağlanır ya da
  açıkça devre dışıdır
- Erişilebilirlik: klavye, aria, sıralama, arama, CSV
- Gerçek zamanlılık: yoklama (bu depoda SSE/WebSocket yasak)
"""
import re
from pathlib import Path

import pytest

import app as app_module

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "templates" / "operation_control.html").read_text(
    encoding="utf-8")
WS_JS = (ROOT / "static" / "js" / "operation_workspace.js").read_text(
    encoding="utf-8")
OC_JS = (ROOT / "static" / "js" / "operation_control.js").read_text(
    encoding="utf-8")
BASE = (ROOT / "templates" / "dash_base.html").read_text(
    encoding="utf-8")


@pytest.fixture(scope="module")
def page():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c.get("/operation-center").get_data(as_text=True)


# ── Yerleşim: tüm bölmeler mevcut ─────────────────────────────────

class TestLayoutPanels:
    @pytest.mark.parametrize("element_id", [
        # Yeni çalışma alanı bölmeleri
        "ows-topbar", "ows-tb-system", "ows-tb-bot", "ows-tb-broker",
        "ows-tb-latency", "ows-tb-kill", "ows-portfolio-bar",
        "ows-strategies", "ows-performance", "ows-broker",
        "ows-journal-table",
        # Agent 01 kimlikleri KORUNMALI (eski testler + istemci)
        "oc-error", "oc-stale", "oc-refreshed", "oc-freshness",
        "oc-mode", "oc-status-grid", "oc-auto-state", "oc-products",
        "oc-positions", "oc-orders", "oc-signals", "oc-stop-entries",
        "oc-close-all", "oc-kill", "oc-kill-off", "oc-safety-grid",
        "oc-sf-blocked", "oc-sf-intents", "oc-sf-closed",
        "oc-sf-open", "oc-sf-recon", "oc-risk-grid", "oc-recon",
        "oc-audit", "oc-dialog", "oc-dialog-reason",
        "oc-dialog-phrase", "oc-dialog-ok",
    ])
    def test_element_present(self, page, element_id):
        assert f'id="{element_id}"' in page, element_id

    def test_full_width_grid_layout(self):
        assert "ows-main" in TEMPLATE
        assert "grid-template-columns" in TEMPLATE

    def test_responsive_breakpoint(self):
        assert "@media" in TEMPLATE

    def test_resizable_tables(self):
        assert "resize:vertical" in TEMPLATE.replace(" ", "")

    @pytest.mark.parametrize("panel_heading", [
        "Aktif Stratejiler", "Canlı Pozisyonlar", "Canlı Sinyaller",
        "Emirler", "Performans", "İşlem Günlüğü",
        "Otomasyon Kontrolü", "Broker Sağlığı", "Portföy",
        "Risk ve Limitler", "Mutabakat", "Denetim Zaman Çizelgesi",
    ])
    def test_panel_heading(self, page, panel_heading):
        assert panel_heading in page

    def test_workspace_js_included(self, page):
        assert "operation_workspace.js" in page
        assert "operation_control.js" in page

    def test_position_table_has_duration_column(self):
        assert "Süre" in TEMPLATE

    @pytest.mark.parametrize("column", [
        "Sembol", "Yön", "Giriş", "Güncel", "Miktar", "Notyonel",
        "Gerç. PnL", "PnL %", "Ücret", "Stop", "TP", "MFE", "MAE",
        "Açılış", "Mutabakat"])
    def test_position_columns(self, column):
        assert column in TEMPLATE

    @pytest.mark.parametrize("column", [
        "Emir ID", "Client ID", "Tip", "Ort. Dolum", "Dolan",
        "Kalan", "Durum", "Oluşturma", "Güncelleme", "Strateji",
        "Korelasyon"])
    def test_order_columns(self, column):
        assert column in TEMPLATE

    @pytest.mark.parametrize("column", [
        "Zaman", "Güven", "Karar", "Risk", "İzin", "Red Kodu",
        "Yürütme"])
    def test_signal_columns(self, column):
        assert column in TEMPLATE

    @pytest.mark.parametrize("label", [
        "Portföy Değeri", "Nakit", "Özkaynak", "Günlük PnL",
        "Son 7 Gün PnL", "Son 30 Gün PnL", "Açık Risk", "Maruziyet",
        "Düşüş %", "En Büyük Kazanan", "En Büyük Kaybeden"])
    def test_portfolio_labels_rendered_by_client(self, label):
        assert label in WS_JS

    @pytest.mark.parametrize("label", [
        "İşlem Sayısı", "Kazanma Oranı %", "Ort. Kazanç",
        "Ort. Kayıp", "Kâr Faktörü", "Sharpe", "Maks. Düşüş %",
        "Ort. Tutma", "Günlük Kâr", "Son 7 Gün Kâr", "Son 30 Gün Kâr"])
    def test_performance_labels_rendered_by_client(self, label):
        assert label in WS_JS

    @pytest.mark.parametrize("label", [
        "Kalp Atışı", "Gecikme", "API Durumu", "Hız Limiti",
        "Yeniden Bağlanma", "Senkronizasyon", "Kimlik Doğrulama"])
    def test_broker_labels_rendered_by_client(self, label):
        assert label in WS_JS


# ── Sahte düğme yok ────────────────────────────────────────────────

class TestNoFakeButtons:
    def test_unsupported_buttons_disabled(self):
        for match in re.finditer(
                r"<button[^>]*desteklenmiyor[^<]*</button>",
                TEMPLATE, re.S):
            assert "disabled" in match.group(0)

    @pytest.mark.parametrize("label", [
        "Stop Taşı", "TP Güncelle", "Limit Düzenle"])
    def test_unsupported_labeled_honestly(self, label):
        assert label in TEMPLATE
        idx = TEMPLATE.rfind(label)
        block = TEMPLATE[idx - 300:idx + 150]
        assert "disabled" in block

    @pytest.mark.parametrize("cmd", ["pause", "resume", "stop",
                                     "enable"])
    def test_strategy_buttons_bound_to_real_endpoint(self, cmd):
        assert f'data-strategy-cmd=\\"{cmd}\\"' in WS_JS
        assert '"/symbols/" + encodeURIComponent' in WS_JS

    def test_no_direct_exchange_urls_in_js(self):
        for source in (WS_JS, OC_JS):
            assert "binance.com" not in source
            assert "api.binance" not in source
            assert "fapi" not in source

    def test_only_operation_control_api(self):
        for source in (WS_JS, OC_JS):
            assert '"/api/operation-control"' in source

    def test_csrf_header_sent(self):
        for source in (WS_JS, OC_JS):
            assert "X-CSRFToken" in source


# ── Gerçek zamanlılık: yoklama ─────────────────────────────────────

class TestRealtime:
    def test_polling_configured(self):
        assert "setInterval(refresh" in WS_JS
        assert re.search(r"POLL_MS\s*=\s*\d+", WS_JS)

    def test_no_sse_or_websocket(self):
        for source in (WS_JS, OC_JS, TEMPLATE):
            assert "new EventSource" not in source
            assert "new WebSocket" not in source
            assert "socket.io" not in source

    def test_no_page_reload_for_updates(self):
        assert "location.reload" not in WS_JS

    def test_inflight_guard(self):
        assert "inflight" in WS_JS

    @pytest.mark.parametrize("endpoint", [
        "/workspace/portfolio", "/workspace/performance",
        "/workspace/broker-health", "/workspace/strategies",
        "/workspace/journal"])
    def test_polls_workspace_endpoints(self, endpoint):
        assert endpoint in WS_JS


# ── Durum koruma (yenileme seçimleri sıfırlamaz) ───────────────────

class TestStatePreservation:
    def test_expanded_rows_reapplied(self):
        assert "expanded" in WS_JS
        assert "reapplyAll" in WS_JS

    def test_mutation_observer_on_agent01_tables(self):
        assert "MutationObserver" in WS_JS
        for table in ("oc-positions", "oc-orders", "oc-signals"):
            assert table in WS_JS

    def test_sort_state_persisted(self):
        assert "sortState" in WS_JS

    def test_reapply_guard_against_loop(self):
        assert "reapplying" in WS_JS


# ── Erişilebilirlik + tablo araçları ───────────────────────────────

class TestAccessibility:
    def test_sortable_headers_focusable(self):
        assert 'setAttribute("tabindex", "0")' in WS_JS
        assert "columnheader" in WS_JS

    def test_keyboard_enter_space_handled(self):
        assert '"Enter"' in WS_JS
        assert "keydown" in WS_JS

    @pytest.mark.parametrize("search_id", [
        "ows-search-positions", "ows-search-orders",
        "ows-search-signals", "ows-search-journal"])
    def test_search_inputs(self, search_id):
        assert f'id="{search_id}"' in TEMPLATE
        block = TEMPLATE[TEMPLATE.index(search_id) - 200:
                         TEMPLATE.index(search_id) + 300]
        assert "aria-label" in block
        assert "data-filter-table" in block

    @pytest.mark.parametrize("table_id", [
        "oc-positions", "oc-orders", "oc-signals", "oc-products",
        "oc-recon", "oc-audit", "ows-journal-table"])
    def test_tables_sortable(self, table_id):
        idx = TEMPLATE.index(f'id="{table_id}"')
        assert "data-sortable" in TEMPLATE[idx - 100:idx + 120]

    @pytest.mark.parametrize("aria", [
        'role="alert"', 'role="status"', "aria-live",
        "aria-labelledby", "aria-label"])
    def test_aria_markup(self, aria):
        assert aria in TEMPLATE

    def test_expandable_rows_keyboard(self):
        assert 'tabindex=\\"0\\"' in OC_JS
        assert "data-expandable" in OC_JS
        assert "data-expandable" in WS_JS


# ── CSV dışa aktarım bağlantıları ──────────────────────────────────

class TestCsvLinks:
    @pytest.mark.parametrize("name", [
        "positions", "orders", "signals", "journal"])
    def test_csv_link_present(self, name):
        assert (f"/api/operation-control/workspace/export/{name}.csv"
                in TEMPLATE)

    def test_csv_links_download(self):
        assert "download" in TEMPLATE


# ── Renk semantiği ─────────────────────────────────────────────────

class TestColorSemantics:
    @pytest.mark.parametrize("cls", [
        "ows-profit", "ows-loss", "ows-pending", "ows-info",
        "ows-unknown"])
    def test_semantic_classes_defined(self, cls):
        assert cls in TEMPLATE
        assert cls in WS_JS

    def test_pnl_class_logic(self):
        assert "pnlClass" in WS_JS

    def test_unknown_never_styled_success(self):
        # UNKNOWN gri sınıfa düşer; good/profit sınıfına asla.
        assert 'return "ows-unknown"' in WS_JS


# ── Dürüstlük: UNKNOWN, sahte 0 yok ───────────────────────────────

class TestHonesty:
    def test_unknown_rendering(self):
        assert '"UNKNOWN"' in WS_JS

    def test_template_explains_unknown(self):
        assert "UNKNOWN" in TEMPLATE
        assert "sahte" in TEMPLATE.lower()

    def test_destructive_actions_explained(self):
        assert "ONAYLIYORUM" in TEMPLATE
        assert "idempotency" in TEMPLATE.lower()

    def test_close_all_labeled_as_request(self):
        assert "Kapatma İsteği" in TEMPLATE


# ── Gezinme ────────────────────────────────────────────────────────

class TestNavigation:
    def test_operation_center_in_system_group(self):
        # Mission 2300 A04: kullanıcı menüsü sadeleşti; Operation
        # Center alttaki "Sistem" grubunda erişilebilir kalır.
        assert 'href="/operation-center"' in BASE
        assert BASE.index('nav-system') < BASE.index(
            'href="/operation-center"')

    def test_login_default_is_a_workspace_page(self):
        # Mission 2300: varsayılan açılış Trading Home'a taşındı;
        # Operation Center menüden erişilebilir kalır.
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        matches = re.findall(
            r'next_url[^\n]*=[^\n]*"(/[a-z-]*)"', app_source)
        assert "/home" in matches
