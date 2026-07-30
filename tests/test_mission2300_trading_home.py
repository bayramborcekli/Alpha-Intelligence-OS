"""Mission 2300 Agent 01 — Trading Home (sahip odaklı ana sayfa).

Değişmezler:
- Operation Center'a DOKUNULMADI (şablon/istemci aynı kaldı).
- Backend/API/servis değişikliği yok: Trading Home yalnız mevcut
  uçları okur; tek yeni rota bir sayfa render'ıdır.
- Teknik gösterge yok (RSI/EMA/MACD).
- Giriş sonrası varsayılan sayfa /home.
"""
import re
from pathlib import Path

import pytest

import app as app_module

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "templates" / "trading_home.html").read_text(
    encoding="utf-8")
JS = (ROOT / "static" / "js" / "trading_home.js").read_text(
    encoding="utf-8")
BASE = (ROOT / "templates" / "dash_base.html").read_text(
    encoding="utf-8")
APP_SRC = (ROOT / "app.py").read_text(encoding="utf-8")


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c


@pytest.fixture()
def anon():
    previous = app_module.app.config.get("TESTING")
    app_module.app.config["TESTING"] = False
    try:
        with app_module.app.test_client() as c:
            yield c
    finally:
        app_module.app.config["TESTING"] = previous


# ── Sayfa ve varsayılan açılış ─────────────────────────────────────

class TestRouting:
    def test_home_renders(self, client):
        r = client.get("/home")
        assert r.status_code == 200
        assert "Trading Home" in r.get_data(as_text=True)

    def test_home_requires_login(self, anon):
        r = anon.get("/home")
        assert r.status_code in (301, 302)
        assert "/login" in r.headers["Location"]

    def test_login_default_next_is_home(self):
        matches = re.findall(
            r'next_url[^\n]*=[^\n]*"(/[a-z-]*)"', APP_SRC)
        assert matches and all(m == "/home" for m in matches)

    def test_operation_center_still_reachable(self, client):
        r = client.get("/operation-center")
        assert r.status_code == 200

    def test_nav_has_both_links(self):
        assert 'href="/home"' in BASE
        assert 'href="/operation-center"' in BASE

    def test_trading_home_first_in_nav(self):
        assert BASE.index('href="/home"') < BASE.index(
            'href="/operation-center"')


# ── Yerleşim: üst çubuk, üç sütun, alt bölüm ───────────────────────

class TestLayout:
    @pytest.mark.parametrize("element_id", [
        "th-topbar", "th-portfolio-value", "th-daily-pnl",
        "th-active-count", "th-queued-count", "th-auto-mode",
        "th-bot-status", "th-wallet-conn", "th-wallets",
        "th-trades", "th-queue", "th-activity", "th-dialog",
        "th-dialog-reason", "th-dialog-phrase"])
    def test_element_present(self, element_id):
        assert f'id="{element_id}"' in TEMPLATE, element_id

    def test_topbar_sticky(self):
        idx = TEMPLATE.index("#th-topbar")
        assert "position:sticky" in TEMPLATE[idx:idx + 200]

    def test_three_columns_desktop_first(self):
        compact = TEMPLATE.replace(" ", "").replace("\n", "")
        assert "grid-template-columns:minmax" in compact
        assert "@media" in TEMPLATE  # dar ekranda tek sütun

    @pytest.mark.parametrize("heading", [
        "Aktif İşlemler", "İzlenen Piyasalar", "Son Hareketler",
        "AI Durumu"])
    def test_section_headings(self, heading):
        assert heading in TEMPLATE

    @pytest.mark.parametrize("label", [
        "Alpha Intelligence OS", "Portföy", "Bugünkü Kazanç",
        "Aktif İşlemler", "İzlenen Piyasalar", "Otomasyon Modu",
        "Sistem Durumu", "Son Güncelleme"])
    def test_topbar_labels(self, label):
        assert label in TEMPLATE

    @pytest.mark.parametrize("column", [
        "Varlık", "Yön", "Giriş Fiyatı", "Anlık Fiyat",
        "Anlık Kazanç", "Süre", "Durum", "İşlem"])
    def test_trade_columns(self, column):
        assert column in TEMPLATE

    # ── Mission 2300 A04: Binance-stili görsel göç ────────────────

    def test_no_oversized_title_block(self):
        # Sayfa doğrudan finansal bilgiyle açılır: varsayılan büyük
        # başlık ve alt açıklama gizlenir; karşılama afişi yok.
        compact = TEMPLATE.replace(" ", "")
        assert "main.content>h1" in compact
        assert "display:none" in compact

    def test_accounts_strip_horizontal(self):
        # Hesaplar TEK yatay şerittir; dikey cüzdan sütunu yok.
        idx = TEMPLATE.index("#th-wallets")
        block = TEMPLATE[idx:idx + 220]
        assert "flex-direction:row" in block.replace(" ", "")
        assert "th-wallet " not in TEMPLATE  # eski dikey kart sınıfı

    def test_manage_accounts_link(self):
        assert 'id="th-manage-accounts"' in TEMPLATE
        assert 'href="/settings/accounts"' in TEMPLATE

    def test_main_grid_75_25(self):
        compact = TEMPLATE.replace(" ", "").replace("\n", "")
        assert "minmax(0,3fr)minmax" in compact  # sol ~%75, sağ ~%25

    def test_ai_panel_elements(self):
        for el in ("th-ai", "th-ai-mode", "th-ai-scanned",
                   "th-ai-eligible", "th-ai-last"):
            assert f'id="{el}"' in TEMPLATE, el
        assert 'href="/operation-center"' in TEMPLATE  # Ayrıntılı Durum

    def test_activity_is_table(self):
        # Son hareketler tam genişlik tablo: Zaman/Varlık/Olay/Sonuç.
        for col in ("Zaman", "Olay", "Sonuç"):
            assert col in TEMPLATE, col
        assert '<tbody id="th-activity">' in TEMPLATE

    def test_amber_accent_present(self):
        assert "#f0b90b" in TEMPLATE or "--amber" in TEMPLATE

    def test_tr_number_formatting_centralized(self):
        # Ham float asla gösterilmez: merkezî tr-TR biçimlendirici.
        assert 'toLocaleString("tr-TR"' in JS
        for fn in ("function fmtMoney", "function fmtPrice",
                   "function fmtSigned"):
            assert fn in JS, fn
        # Değerler biçimlendiriciden geçirilir.
        assert "fmtPrice(p.entry_price)" in JS
        assert "fmtSigned(p.unrealized_pnl" in JS
        assert "fmtMoney(portfolio.portfolio_value" in JS

    def test_unknown_not_converted_to_zero(self):
        # Bilinmeyen değer 0'a çevrilmez; UNKNOWN döner.
        idx = JS.index("function fmtMoney")
        assert '"UNKNOWN"' in JS[idx:idx + 400]

    def test_sidebar_user_menu_reduced(self):
        # Kullanıcı menüsünde iç mühendislik bağlantıları yok;
        # gelişmiş sayfalar alttaki Sistem grubunda kalır.
        user_nav = BASE[BASE.index('<div class="nav">'):
                        BASE.index('nav-system')]
        for banned in ("/intelligence", "/workspace", "/ledger",
                       "/audit", "/overview"):
            assert f'href="{banned}"' not in user_nav, banned
        assert 'href="/operation-center"' not in user_nav
        system_nav = BASE[BASE.index("nav-system"):]
        assert 'href="/operation-center"' in system_nav
        assert 'href="/workspace"' in system_nav  # rota silinmedi


# ── Felsefe: teknik gösterge YOK ───────────────────────────────────

class TestNoTechnicalNoise:
    @pytest.mark.parametrize("banned", [
        "RSI", "EMA", "MACD", "Bollinger", "Fibonacci", "ATR",
        "stochastic", "correlation_id", "idempotency görüntüle"])
    def test_no_indicator_terms(self, banned):
        assert banned not in TEMPLATE, banned

    @pytest.mark.parametrize("banned", ["RSI", "EMA", "MACD"])
    def test_no_indicator_terms_in_js(self, banned):
        assert banned not in JS, banned

    def test_page_response_clean(self, client):
        html = client.get("/home").get_data(as_text=True)
        for banned in ("RSI", "EMA", "MACD"):
            assert banned not in html

    def test_owner_language(self):
        # Sahip diliyle konuşur: yön Yükseliş/Düşüş olarak çevrilir.
        assert "Yükseliş" in JS and "Düşüş" in JS


# ── Cüzdanlar (sol panel) ──────────────────────────────────────────

class TestWallets:
    def test_connection_indicator_not_color_only(self):
        # Bağlantı durumu yalnız renge dayanmaz: metin/aria da var.
        assert "aria-label" in JS and "bağlı" in JS

    def test_wallet_rows_come_from_account_source(self):
        # Bakiye sunucu anlık görüntüsünden gelir (w.name/w.balance);
        # istemcide borsa adı sabitlenmez, bakiye uydurulmaz.
        assert "w.name" in JS and "w.balance" in JS
        idx = JS.index("function stripBalance")
        assert '"UNKNOWN"' in JS[idx:idx + 500]

    def test_single_source_is_connected_accounts(self):
        # Mission 2300 A03: cüzdan paneli YALNIZ bağlı kişisel
        # hesaplardan okur; borsa uçlarına doğrudan bağımlılık yok.
        assert "/api/accounts/wallets" in JS
        assert "/api/v1/global/account" not in JS
        assert "/api/v1/tr/account" not in JS

    def test_exchange_agnostic_rendering(self):
        # Yeni borsa eklemek UI değişikliği gerektirmez: kart
        # genel alanlardan (nickname/logo/wallets) üretilir.
        assert "a.nickname" in JS and "a.wallets" in JS

    def test_offline_accounts_shown_honestly(self):
        # Task 38: bağlı olmayan hesaplar (Bybit/OKX) şeridin sonuna
        # sade 'bağlı değil' etiketiyle eklenir; bakiye uydurulmaz.
        assert "/api/accounts\"" in JS or '"/api/accounts"' in JS
        wallet_fn = JS[JS.index("function renderWallets"):
                       JS.index("function renderTrades")]
        assert "bağlı değil" in wallet_fn
        assert "th-offline" in wallet_fn
        assert "a.connected" in wallet_fn
        # Bağlı olmayan karta bakiye yazılmaz: em-dash yer tutucu.
        assert "—" in wallet_fn

    def test_offline_accounts_appended_after_connected(self):
        wallet_fn = JS[JS.index("function renderWallets"):
                       JS.index("function renderTrades")]
        assert wallet_fn.index("ordered.map") < \
            wallet_fn.index("offline.map")

    def test_no_settings_controls(self):
        # Cüzdan panelinde ayar yok: sol panelde düğme üretilmez.
        wallet_fn = JS[JS.index("function renderWallets"):
                       JS.index("function renderTrades")]
        assert "<button" not in wallet_fn


# ── Aktif işlemler + kapatma ───────────────────────────────────────

class TestTrades:
    def test_close_button_bound_to_real_endpoint(self):
        assert "data-close" in JS
        assert "/api/operation-control/positions/" in JS
        assert "/close" in JS

    def test_close_requires_guard(self):
        assert "confirm_phrase" in JS
        assert "idempotency_key" in JS
        assert "ONAYLIYORUM" in TEMPLATE

    def test_only_dialog_buttons_in_template(self):
        # Şablondaki tek düğmeler onay diyaloğuna aittir (value=
        # ile method=dialog formunu kapatır); dekoratif düğme yok.
        # İstisna: th-orphan-clean (Task 145) yetim kayıt temizleme
        # eylem düğmesidir — dekoratif değil, JS'te korumalı akışa
        # bağlıdır (bilinçli genişletme).
        for match in re.finditer(r"<button[^>]*>", TEMPLATE):
            if 'id="th-orphan-clean"' in match.group(0):
                continue
            assert 'value="' in match.group(0), match.group(0)

    def test_duration_helper(self):
        assert "function duration" in JS


# ── Sıra (sağ panel) ───────────────────────────────────────────────

class TestQueue:
    @pytest.mark.parametrize("label", [
        # Dürüst etiketler: sinyal beklerken "Sinyal bekliyor"/"İzleniyor";
        # yalnız gerçek emir niyeti "Emir niyeti oluştu"dur.
        "Sinyal bekliyor", "İzleniyor", "Emir niyeti oluştu", "Yürütülüyor",
        "Kapanıyor"])
    def test_queue_states(self, label):
        assert label in JS

    @pytest.mark.parametrize("badge", ["wait", "prep", "exec",
                                       "close"])
    def test_badge_classes(self, badge):
        assert f"th-badge.{badge}" in TEMPLATE

    def test_queue_derived_from_existing_endpoints(self):
        # UI senkron sözleşmesi: sıra/tablo verisi artık TEK atomik
        # overview snapshot'ından gelir (ayrı uçlar çelişki üretiyordu)
        assert "/api/operation-control/overview" in JS


# ── Son hareketler ─────────────────────────────────────────────────

class TestActivity:
    def test_journal_endpoint_used(self):
        assert "/api/operation-control/workspace/journal" in JS

    def test_plain_language_mapping(self):
        assert "plainEvent" in JS
        assert "işlemi açıldı" in JS or "açıldı" in JS


# ── Backend dokunulmazlığı ─────────────────────────────────────────

class TestBackendUntouched:
    def test_only_existing_apis_called(self):
        # İstemcinin çağırdığı her uç app.py'de zaten tanımlı olmalı.
        called = set(re.findall(r'"(/api/[a-z0-9/._-]+)"', JS))
        assert called
        for path in called:
            base = path.replace("/close", "")
            assert base in APP_SRC or path in APP_SRC, path

    def test_no_new_write_endpoints_invented(self):
        posts = re.findall(r'method:\s*"POST"[\s\S]{0,300}?'
                           r'(/api[^"]*)"', JS)
        # Tek yazma eylemi: mevcut kontrollü kapatma niyeti.
        assert all("/close" in p or "operation-control" in p
                   for p in posts)

    def test_realtime_is_polling(self):
        assert "setInterval(refresh" in JS
        assert "new EventSource" not in JS
        assert "new WebSocket" not in JS

    def test_unknown_never_faked(self):
        assert '"UNKNOWN"' in JS

    def test_operation_center_template_untouched_ids(self, client):
        # Operation Center hâlâ Agent 01/02 kimlikleriyle çalışır.
        html = client.get("/operation-center").get_data(as_text=True)
        for element_id in ("oc-positions", "ows-topbar",
                           "oc-kill", "ows-portfolio-bar"):
            assert f'id="{element_id}"' in html


# ── Mission 2300 A02: AI Karar Panosu ──────────────────────────────

class TestAgent02ModeCard:
    def test_mode_lives_in_topbar_and_ai_panel(self):
        # A04: ayrı büyük mod kartı kaldırıldı; mod üst çubukta ve
        # AI panelinde görünür (başlıkta bilgi tekrarı yok).
        assert 'id="th-mode-card"' not in TEMPLATE
        assert 'id="th-auto-mode"' in TEMPLATE
        assert 'id="th-ai-mode"' in TEMPLATE

    def test_mode_values(self):
        assert '"OTONOM"' in JS and '"DANIŞMAN"' in JS

    def test_simple_status_badges(self):
        for label in ("Çalışıyor", "Duraklatıldı", "Çevrimdışı"):
            assert label in JS, label

    def test_no_internal_automation_logic_exposed(self):
        # Büyük kartta yalnız mod ve rozet var; iç durum adları yok.
        assert "kill_switch_state" not in TEMPLATE
        assert "automation_state" not in TEMPLATE


class TestAgent02Trades:
    def test_entry_price_column(self):
        assert "entry_price" in JS
        assert "Giriş Fiyatı" in TEMPLATE

    def test_owner_status_mapping(self):
        for label in ("Yönetiliyor", "Kapatılıyor", "Çıkış Bekliyor",
                      "Acil Çıkış", "Tamamlandı"):
            assert label in JS, label

    def test_single_action_button(self):
        # Satırda tek eylem: Kapat. Başka data-* eylem düğmesi yok.
        actions = re.findall(r'data-(\w+)=\\"', JS)
        assert set(a for a in actions if a not in
                   ("symbol",)) == {"close"}


class TestAgent02Activity:
    def test_max_20_records(self):
        assert "slice(0, 20)" in JS

    def test_no_raw_operator_detail_leaked(self):
        # Operatör olayları teknik detay sızdırmaz.
        assert "esc(e.detail)" not in JS


class TestAgent02BannedTerms:
    # NOT: "Confidence"/"confidence" yasak listesinden çıkarıldı —
    # dual-model misyonu (CORE/OPPORTUNITY tabloları) confidence
    # sütununu açıkça şart koşuyor; şablonda Türkçe "Güven" kullanılır.
    @pytest.mark.parametrize("banned", [
        "ADX", "Confidence", "Güven %", "risk skoru",
        "correlation_id", "JSON.stringify(e", "strategy"])
    def test_no_technical_leak_in_template(self, banned):
        assert banned not in TEMPLATE, banned

    @pytest.mark.parametrize("banned", [
        "ADX", "last_rejection_reason",
        "last_decision", "signal_state"])
    def test_no_technical_leak_in_js(self, banned):
        assert banned not in JS, banned


class TestAgent02ArchitectFixes:
    def test_topbar_reset_when_status_missing(self):
        # Durum ucu düşerse üst çubuk bayat değer göstermez.
        idx = JS.index("if (!status) {")
        block = JS[idx:idx + 500]
        assert "th-auto-mode" in block and "th-bot-status" in block

    def test_queue_mutually_exclusive_per_symbol(self):
        assert "function push(symbol" in JS
        assert "if (taken[symbol]) return;" in JS

    def test_activity_sorted_newest_first(self):
        assert ".sort(" in JS and "event_time" in JS

    def test_unknown_position_status_not_leaked(self):
        # Yetim/eksik pozisyon düzeltmesi sonrası sözleşme değişti:
        # bilinmeyen durum artık körlemesine "Yönetiliyor" DEĞİL —
        # statusCell dürüst kodu gösterir; boş/eksik durum UNKNOWN'dur.
        assert "function statusCell(status" in JS
        assert 'esc(known || status || "UNKNOWN")' in JS
        # Eksik veri durumları "Yönetiliyor" rozetiyle maskelenemez.
        assert "EXIT_BLOCKED" in JS


class TestAgent04ArchitectFixes:
    def test_last_update_only_on_real_data(self):
        # "Son Güncelleme" yalnız veri gerçekten geldiyse yenilenir;
        # her iki uç da düşerse UNKNOWN'a döner.
        idx = JS.index("if (status || portfolio) {")
        block = JS[idx:idx + 300]
        assert "th-last-update" in block
        assert 'setText("th-last-update", null)' in JS

    def test_portfolio_reset_when_missing(self):
        # Portföy ucu düşünce bayat değer taze gibi kalmaz.
        assert 'setText("th-portfolio-value", null)' in JS
        assert 'setText("th-daily-pnl", null)' in JS
