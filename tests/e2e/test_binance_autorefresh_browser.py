"""Task 114 — Bağlantı kartlarının otomatik tazelenmesi GERÇEK tarayıcıda doğrulanır.

Playwright (Chromium headless) ile static/js/binance_settings.js'in canlı
davranışı uçtan uca test edilir:
  1. Sayfa açıkken sunucu snapshot'ı değişince kart rozeti elle yenileme
     olmadan güncellenir (5 dk'lık interval, sahte saatle ileri sarılır).
  2. Sekme gizliyken hiçbir status isteği atılmaz; sekmeye dönünce hemen
     bir tazeleme yapılır.
  3. Form açıkken otomatik tazeleme kartları yeniden çizmez; girilen
     değerler kaybolmaz.

Harness: gerçek JS dosyasını ve #bc-grid iskeletini sunan mini Flask app;
status endpoint'i test içinden değiştirilebilir ve istekleri sayar.
Gerçek uygulamadaki sayfa aynı JS'i aynı DOM kancalarıyla yükler
(templates/binance_settings.html), dolayısıyla davranış birebir örtüşür.
"""
import threading
import socket
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from flask import Flask, jsonify  # noqa: E402
import pathlib  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]

PAGE = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>bc harness</title></head><body>
<div class="bc-grid" id="bc-grid" aria-live="polite">
  <div class="bc-card">Yükleniyor…</div>
</div>
<script>window.BC_CSRF = "test-csrf";</script>
<script src="/static/js/binance_settings.js"></script>
</body></html>"""


class Harness:
    def __init__(self):
        self.app = Flask(
            "bc_harness", static_folder=str(ROOT / "static"), static_url_path="/static"
        )
        self.status_requests = 0
        self.snapshot = {
            "BINANCE_GLOBAL": {
                "status": "NOT_CONFIGURED",
            },
            "BINANCE_TR": {"status": "NOT_CONFIGURED"},
        }

        @self.app.get("/")
        def index():
            return PAGE

        @self.app.get("/api/integrations/binance/status")
        def status():
            self.status_requests += 1
            return jsonify({"ok": True, "data": self.snapshot})

    def start(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        t = threading.Thread(
            target=lambda: self.app.run(
                host="127.0.0.1", port=self.port, use_reloader=False
            ),
            daemon=True,
        )
        t.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                c = socket.create_connection(("127.0.0.1", self.port), 0.2)
                c.close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("harness başlamadı")


@pytest.fixture(scope="module")
def env():
    import shutil

    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    h = Harness()
    h.start()
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=chromium)
        except Exception as exc:  # tarayıcı yoksa suite kırılmasın
            pytest.skip(f"Chromium başlatılamadı: {exc}")
        yield h, browser
        browser.close()


def _open(h, browser):
    page = browser.new_page()
    page.clock.install()
    page.goto(f"http://127.0.0.1:{h.port}/")
    page.wait_for_selector(".bc-card[data-p='global']")
    return page


def _wait_requests(h, n, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if h.status_requests >= n:
            return
        time.sleep(0.05)
    raise AssertionError(
        f"status istek sayısı {h.status_requests}, beklenen >= {n}"
    )


def _set_hidden(page, hidden):
    page.evaluate(
        """(hidden) => {
            Object.defineProperty(document, 'hidden',
                { configurable: true, get: () => hidden });
            Object.defineProperty(document, 'visibilityState',
                { configurable: true, get: () => hidden ? 'hidden' : 'visible' });
            document.dispatchEvent(new Event('visibilitychange'));
        }""",
        hidden,
    )


def test_badge_updates_without_manual_reload(env):
    """Kriter 1: snapshot değişince rozet elle yenileme olmadan güncellenir."""
    h, browser = env
    page = _open(h, browser)
    badge = page.locator(".bc-card[data-p='global'] .bc-badge")
    assert "Bağlı değil" in badge.inner_text()

    h.snapshot["BINANCE_GLOBAL"] = {
        "status": "CONNECTED_READ_ONLY",
        "tested_at": "2026-07-29 12:00",
        "masked_api_key": "AB****YZ",
    }
    before = h.status_requests
    page.clock.fast_forward("05:01")  # 5 dk interval tetiklenir
    _wait_requests(h, before + 1)
    page.wait_for_function(
        "document.querySelector(\".bc-card[data-p='global'] .bc-badge\")"
        ".textContent.includes('Bağlı (salt okunur)')"
    )
    assert "ok" in badge.get_attribute("class")
    page.close()


def test_hidden_tab_pauses_and_resume_refreshes(env):
    """Kriter 2: gizliyken istek yok; görünür olunca hemen tazeleme."""
    h, browser = env
    page = _open(h, browser)
    _set_hidden(page, True)
    baseline = h.status_requests
    page.clock.fast_forward("16:00")  # 3 interval'lik süre
    time.sleep(0.5)
    assert h.status_requests == baseline, "sekme gizliyken istek atıldı"

    _set_hidden(page, False)
    _wait_requests(h, baseline + 1)  # dönüşte anında tazeleme
    page.close()


def test_open_form_preserves_input_and_skips_redraw(env):
    """Kriter 3: form açıkken kart yeniden çizilmez, girdiler korunur."""
    h, browser = env
    h.snapshot["BINANCE_TR"] = {"status": "NOT_CONFIGURED"}
    page = _open(h, browser)
    card = page.locator(".bc-card[data-p='tr']")
    card.locator("button[data-act='form']").click()
    key_input = card.locator("input[name='apiKey']")
    key_input.fill("test-anahtar-123")
    page.evaluate("document.querySelector('#bc-grid').dataset.marker = 'kept'")

    h.snapshot["BINANCE_TR"] = {
        "status": "CONNECTED_READ_ONLY",
        "tested_at": "2026-07-29 13:00",
    }
    before = h.status_requests
    page.clock.fast_forward("11:00")  # iki interval geçir
    time.sleep(0.5)
    assert h.status_requests == before, "form açıkken status isteği atıldı"
    # Kart yeniden çizilmedi (marker duruyor) ve girdi korunur
    assert page.evaluate("document.querySelector('#bc-grid').dataset.marker") == "kept"
    assert key_input.input_value() == "test-anahtar-123"
    # Form hâlâ açık, rozet eski haliyle (yeniden çizim yok)
    assert "Bağlı değil" in card.locator(".bc-badge").inner_text()
    page.close()
