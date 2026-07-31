"""Trading Home kokpit yerleşimi bekçileri.

Referans tasarım: sol nav + üst durum şeridi + özet kartları +
geniş Aktif İşlemler tablosu + CORE|OPPORTUNITY|İzlenen Piyasalar
üç kolonu + alt kartlar + fiyat şeridi. Uydurma veri YOK: kaynak
olmayan alanlar "Veri yok" gösterir.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "templates/trading_home.html").read_text(
    encoding="utf-8")
JS = (ROOT / "static/js/trading_home.js").read_text(encoding="utf-8")
BASE = (ROOT / "templates/dash_base.html").read_text(encoding="utf-8")


class TestTopStrip:
    def test_paper_and_live_disabled_badges(self):
        assert "PAPER" in TEMPLATE
        assert "CANLI EMİRLER DEVRE DIŞI" in TEMPLATE

    def test_pipeline_cells_present(self):
        for el in ("th-strip-pipeline", "th-strip-scheduler",
                   "th-strip-scan", "th-strip-universe",
                   "th-strip-risk", "th-strip-lastanalysis"):
            assert el in TEMPLATE, el
        # Kaynak: mevcut /api/paper/state (yeni uç icat edilmedi).
        assert "/api/paper/state" in JS
        assert "renderStrip" in JS

    def test_strip_honest_when_endpoint_down(self):
        # Uç düşerse hücreler "Veri yok"a döner; bayat değer kalmaz.
        idx = JS.index("function renderStrip")
        assert "setDatum" in JS[idx:idx + 600]


class TestSummaryCards:
    def test_second_row_cards(self):
        for el in ("th-sc-open", "th-sc-notional", "th-sc-intraday",
                   "th-sc-realized", "th-sc-risk"):
            assert el in TEMPLATE, el

    def test_partial_notional_is_honest(self):
        # Fiyatlanamayan satır varsa toplam "en az X (kısmi)" olur.
        assert "(kısmi)" in JS


class TestMergedActiveTrades:
    def test_extended_columns(self):
        for col in ("Miktar", "Poz. Değeri", "PnL %", "TP", "SL",
                    "Model", "Liste"):
            assert col in TEMPLATE, col

    def test_dual_positions_join_main_table(self):
        # Model pozisyonları da Aktif İşlemler'e girer; sayaç
        # birleşik toplamdır (satırlar == sayaç).
        assert "renderTrades(data(1, \"positions\")" in JS
        assert "dual ? dual.positions : null" in JS
        assert "legacy.length + dual.length" in JS

    def test_partial_source_outage_is_honest(self):
        # Kaynaklardan biri düşerse "N (kısmi)"; ikisi de düşerse
        # UNKNOWN — asla "açık işlem yok" yalanı basılmaz.
        idx = JS.index("function renderTrades")
        seg = JS[idx:idx + 1600]
        assert '" (kısmi)"' in seg
        assert "pozisyon verisi" in seg  # UNKNOWN satırı
        assert "bothDown" in seg

    def test_dual_close_confirm_shows_costs(self):
        # PAPER MANUAL_CLOSE onayı tahmini ücret/kayma/net PnL içerir.
        idx = JS.index("function dmConfirmText")
        seg = JS[idx:idx + 700]
        for token in ("Tahmini ücret", "Tahmini kayma",
                      "Tahmini net PnL", "(PAPER)"):
            assert token in seg, token


class TestTriColumnAndBottom:
    def test_lists_and_markets(self):
        assert 'id="th-lists"' in TEMPLATE
        assert 'id="th-markets"' in TEMPLATE
        # Piyasalar tablosu kolonları:
        # Sinyal görünürlüğü görevi: İzlenen Piyasalar artık son
        # analiz KARARINI gösterir (fiyat/hacim yerine karar sütunları)
        for col in ("Varlık", "Model", "Sonuç", "Son red nedeni",
                    "Son analiz", "Giriş durumu"):
            assert col in TEMPLATE, col
        assert "renderMarkets" in JS

    def test_opportunity_columns_match_core(self):
        # Referans tasarım: iki listenin sütunları aynı.
        assert TEMPLATE.count(">Sinyal</th>") >= 2
        assert "Fırsat Türü" not in TEMPLATE

    def test_market_summary_never_fabricates(self):
        # Toplam piyasa değeri / dominans / korku endeksi için
        # denetimli kaynak yok → şablonda dürüstçe "Veri yok".
        for label in ("Toplam Piyasa Değeri", "BTC Dominansı",
                      "Korku &amp; Açgözlülük", "24s Toplam Hacim"):
            assert label in TEMPLATE, label
        assert "Veri yok" in TEMPLATE

    def test_movers_and_ticker_use_snapshot(self):
        assert "renderMovers" in JS and "renderTicker" in JS
        # Kaynak yalnız dual-model snapshot satırları (change_pct).
        assert "unionRows" in JS
        assert 'id="th-ticker"' in TEMPLATE

    def test_numeric_string_payloads_do_not_crash(self):
        # Backend sayısal alanı string döndürse bile render kırılmaz:
        # toFixed'e ham alan değil parseFloat sonucu gider.
        assert "spread_pct.toFixed" not in JS
        idx = JS.index("function chgCell")
        assert "parseFloat" in JS[idx:idx + 200]

    def test_dual_state_outage_clears_all_blocks(self):
        # /api/dual-model/state düşünce sayaçlar, pozisyon tablosu
        # ve metrik kartları da UNKNOWN'a döner (bayat blok kalmaz).
        idx = JS.index("function renderDualModel")
        seg = JS[idx:JS.index("renderDualHealth", idx) + 2200]
        assert "th-dm-total-open" in seg
        assert "durum alınamadı" in seg
        assert "renderDualMetrics(null)" in seg

    def test_confidence_is_escaped(self):
        # XSS: confidence artık ham metin olarak basılmaz —
        # parseFloat ile YALNIZ sayısal değere dönüştürülür ve mini
        # güven barı + yüzde olarak çizilir (Trading Home düzeltme
        # görevi). Sayısal olmayan girdi bar üretmez ("—").
        assert "parseFloat(p.confidence)" in JS
        assert "th-confbar" in JS

    def test_system_card(self):
        for el in ("th-sys-api", "th-sys-data", "th-sys-auto",
                   "th-sys-uptime"):
            assert el in TEMPLATE, el


class TestNavAndCache:
    def test_sidebar_menu_items(self):
        for label in ("Piyasalar", "İzleme Listeleri", "Stratejiler",
                      "AI Tarayıcı", "Risk Yönetimi", "Raporlar"):
            assert label in BASE, label

    def test_sidebar_bottom_badges(self):
        assert 'id="nav-health"' in BASE
        assert "LIVE ORDERS DISABLED" in BASE

    def test_js_cache_busted(self):
        # Ctrl+F5 gerekmesin: sürüm parametresiyle önbellek kırılır.
        assert "trading_home.js') }}?v={{ app_version }}" in TEMPLATE
