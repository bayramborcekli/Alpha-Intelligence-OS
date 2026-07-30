"""Task 135 — Dual-model sağlık rozeti GERÇEK tarayıcıda doğrulanır.

Playwright (Chromium headless) ile static/js/trading_home.js'in canlı
davranışı test edilir:
  1. Listeler dolu + hata yok → rozet YEŞİL; son yenileme görünür.
  2. Liste(ler) boş → SARI.
  3. last_error dolu → KIRMIZI ve hata metni görünür.
  4. /api/dual-model/state başarısız → KIRMIZI "durum alınamadı".
  5. Yoklama (12 sn) sahte saatle ileri sarılınca rozet elle yenileme
     olmadan yeni duruma geçer.

Harness: gerçek JS dosyasını ve rozet DOM kancalarını sunan mini Flask
app; dual-model state endpoint'i test içinden değiştirilebilir.
"""
import pathlib
import socket
import threading
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from flask import Flask, jsonify  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]

PAGE = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>th harness</title></head><body>
<span id="th-dm-health">SAĞLIK: UNKNOWN</span>
<div>Son yenileme: <span id="th-dm-last-refresh">UNKNOWN</span>
<span id="th-dm-last-error"></span></div>
<tbody id="th-dm-core"></tbody>
<tbody id="th-dm-opp"></tbody>
<tbody id="th-dm-pos"></tbody>
<script>window.TH_CSRF = "test-csrf";
window.showWarnings = function () {};</script>
<script src="/static/js/trading_home.js"></script>
</body></html>"""

EMPTY_OK = {"ok": True, "data": {}}


def _dual(core, opp, last_error=None, last_refresh="2026-07-30T06:00:00+00:00"):
    return {
        "ok": True,
        "data": {
            "core_list": [{"symbol": s} for s in core],
            "opportunity_list": [{"symbol": s} for s in opp],
            "positions": [],
            "counters": {
                "core_universe": len(core),
                "opportunity_universe": len(opp),
                "core_open": 0, "opportunity_open": 0, "total_open": 0,
            },
            "metrics": {},
            "recent_trades": [],
            "recent_rejections": [],
            "last_refresh": last_refresh,
            "last_error": last_error,
        },
    }


class Harness:
    def __init__(self):
        self.app = Flask(
            "th_harness", static_folder=str(ROOT / "static"),
            static_url_path="/static")
        self.dual = _dual(["BTCUSDT"], ["DOGEUSDT"])
        self.dual_http = 200

        @self.app.get("/")
        def index():
            return PAGE

        @self.app.get("/api/dual-model/state")
        def dual_state():
            if self.dual_http != 200:
                return jsonify({"ok": False}), self.dual_http
            return jsonify(self.dual)

        for path in ("/api/operation-control/status",
                     "/api/operation-control/overview",
                     "/api/operation-control/workspace/portfolio",
                     "/api/operation-control/workspace/journal",
                     "/api/accounts/wallets",
                     "/api/accounts"):
            self.app.add_url_rule(
                path, path, lambda: jsonify(EMPTY_OK))

    def start(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        threading.Thread(
            target=lambda: self.app.run(
                host="127.0.0.1", port=self.port, use_reloader=False),
            daemon=True).start()
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                c = socket.create_connection(("127.0.0.1", self.port), 0.2)
                c.close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("harness başlamadı")


import shutil  # noqa: E402

CHROMIUM = shutil.which("chromium")


@pytest.fixture(scope="module")
def harness():
    h = Harness()
    h.start()
    return h


@pytest.fixture()
def page_ctx(harness):
    if not CHROMIUM:
        pytest.skip("Nix chromium yok")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        page = browser.new_page()
        page.clock.install()
        yield harness, page
        browser.close()


def _badge(page):
    return page.text_content("#th-dm-health")


def test_green_when_lists_full(page_ctx):
    h, page = page_ctx
    h.dual = _dual(["BTCUSDT"], ["DOGEUSDT"])
    h.dual_http = 200
    page.goto(f"http://127.0.0.1:{h.port}/")
    page.wait_for_function(
        "document.getElementById('th-dm-health')"
        ".textContent.includes('YE\\u015e\\u0130L')")
    assert "2026-07-30" in page.text_content("#th-dm-last-refresh")
    assert page.text_content("#th-dm-last-error") == ""


def test_yellow_when_list_empty(page_ctx):
    h, page = page_ctx
    h.dual = _dual(["BTCUSDT"], [])
    h.dual_http = 200
    page.goto(f"http://127.0.0.1:{h.port}/")
    page.wait_for_function(
        "document.getElementById('th-dm-health')"
        ".textContent.includes('SARI')")


def test_red_on_last_error(page_ctx):
    h, page = page_ctx
    h.dual = _dual(["BTCUSDT"], ["DOGEUSDT"], last_error="429 backoff")
    h.dual_http = 200
    page.goto(f"http://127.0.0.1:{h.port}/")
    page.wait_for_function(
        "document.getElementById('th-dm-health')"
        ".textContent.includes('KIRMIZI')")
    assert "429 backoff" in page.text_content("#th-dm-last-error")


def test_red_when_state_unreachable(page_ctx):
    h, page = page_ctx
    h.dual_http = 500
    page.goto(f"http://127.0.0.1:{h.port}/")
    page.wait_for_function(
        "document.getElementById('th-dm-health')"
        ".textContent.includes('durum al\\u0131namad\\u0131')")
    h.dual_http = 200


def test_polling_updates_badge_without_reload(page_ctx):
    h, page = page_ctx
    h.dual = _dual([], [])
    h.dual_http = 200
    page.goto(f"http://127.0.0.1:{h.port}/")
    page.wait_for_function(
        "document.getElementById('th-dm-health')"
        ".textContent.includes('SARI')")
    h.dual = _dual(["BTCUSDT"], ["DOGEUSDT"])
    page.clock.fast_forward(13_000)
    page.wait_for_function(
        "document.getElementById('th-dm-health')"
        ".textContent.includes('YE\\u015e\\u0130L')")
