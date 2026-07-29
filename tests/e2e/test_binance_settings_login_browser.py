"""Task 115 — Bağlantı sayfası GERÇEK girişli oturumda tarayıcıyla doğrulanır.

Task 114'ün harness'i gerçek JS'i test etti; burada kalan boşluk kapatılır:
GERÇEK Flask uygulaması (app.py) ayağa kaldırılır, Playwright Chromium ile
gerçek /login formu üzerinden (CSRF AÇIK) giriş yapılır ve:
  1. /settings/binance sayfası girişli oturumda açılır, kartlar yüklenir.
  2. Sayfanın attığı /api/integrations/binance/status isteği 200 döner.
  3. window.BC_CSRF şablon tarafından GERÇEK bir CSRF token ile doldurulur.
  4. Girişsiz (anonim) istemci status endpoint'inden 200 ALAMAZ
     (yetki kontrolü devrede).

Mevcut harness testleri (test_binance_autorefresh_browser.py) aynen korunur.
"""
import os
import pathlib
import socket
import sys
import threading
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
# `import app` proje kökünden bağımsız çalışsın (pytest rootdir/pythonpath
# pinlenmemiş olabilir).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

USERNAME = "e2eadmin"
PASSWORD = "e2e-parola-1234"
SECRET = "e2e-secret-key-aabbccdd11223344aabbccdd"

# app import'undan ve her istekten ÖNCE geçerli olması gereken env.
_ENV_KEYS = (
    "ALPHA_OWNER_USERNAME", "ALPHA_OWNER_PASSWORD_HASH",
    "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY", "REPLIT_DEV_BYPASS", "LOCAL_DEV_BYPASS",
)


@pytest.fixture(scope="module")
def env():
    import shutil

    from werkzeug.security import generate_password_hash

    # Chromium kontrolü, global env/config'e DOKUNMADAN önce yapılır:
    # skip yolunda hiçbir paylaşılan durum kirletilmez.
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=chromium)
        except Exception as exc:  # tarayıcı yoksa suite kırılmasın
            pytest.skip(f"Chromium başlatılamadı: {exc}")

        saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        old_cfg = None
        try:
            os.environ.pop("ALPHA_OWNER_USERNAME", None)
            os.environ.pop("ALPHA_OWNER_PASSWORD_HASH", None)
            os.environ.pop("REPLIT_DEV_BYPASS", None)
            os.environ.pop("LOCAL_DEV_BYPASS", None)
            os.environ["ADMIN_USERNAME"] = USERNAME
            os.environ["ADMIN_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
            os.environ["FLASK_SECRET_KEY"] = SECRET

            import app as flask_app

            old_cfg = {k: flask_app.app.config.get(k)
                       for k in ("TESTING", "WTF_CSRF_ENABLED", "SECRET_KEY")}
            flask_app.app.config["TESTING"] = False          # auth zorunlu
            flask_app.app.config["WTF_CSRF_ENABLED"] = True  # gerçek CSRF
            flask_app.app.config["SECRET_KEY"] = SECRET

            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            threading.Thread(
                target=lambda: flask_app.app.run(
                    host="127.0.0.1", port=port,
                    use_reloader=False, threaded=True),
                daemon=True,
            ).start()
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    c = socket.create_connection(("127.0.0.1", port), 0.2)
                    c.close()
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("gerçek uygulama başlamadı")

            yield port, browser
        finally:
            # Skip/hata dahil HER yolda global durum geri alınır.
            browser.close()
            if old_cfg is not None:
                import app as flask_app
                for k, v in old_cfg.items():
                    flask_app.app.config[k] = v
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _login(page, base):
    page.goto(f"{base}/login")
    page.fill("input[name='username']", USERNAME)
    page.fill("input[name='password']", PASSWORD)
    with page.expect_navigation():
        page.click("button[type='submit'], input[type='submit']")
    assert "/login" not in page.url, f"giriş başarısız, hâlâ {page.url}"


def test_logged_in_binance_page_cards_and_status_200(env):
    """Girişli oturum: kartlar yüklenir, status 200, BC_CSRF gerçek token."""
    port, browser = env
    base = f"http://127.0.0.1:{port}"
    ctx = browser.new_context()
    page = ctx.new_page()
    _login(page, base)

    with page.expect_response(
        lambda r: "/api/integrations/binance/status" in r.url
    ) as resp_info:
        page.goto(f"{base}/settings/binance")
    resp = resp_info.value
    assert resp.status == 200, f"status endpoint {resp.status} döndü"
    body = resp.json()
    assert body.get("ok") is True
    assert "BINANCE_GLOBAL" in body.get("data", {})
    assert "BINANCE_TR" in body.get("data", {})

    # Kartlar gerçek JS ile çizildi (her iki sağlayıcı için).
    page.wait_for_selector(".bc-card[data-p='global']")
    page.wait_for_selector(".bc-card[data-p='tr']")

    # CSRF token şablondan GERÇEK değerle enjekte edildi.
    token = page.evaluate("window.BC_CSRF")
    assert isinstance(token, str) and len(token) > 16
    assert token != "test-csrf"
    ctx.close()


def test_anonymous_cannot_read_status(env):
    """Yetki kontrolü: girişsiz istemci status endpoint'inden 200 alamaz."""
    port, browser = env
    base = f"http://127.0.0.1:{port}"
    ctx = browser.new_context()  # çerezsiz, temiz bağlam
    page = ctx.new_page()
    resp = page.request.get(
        f"{base}/api/integrations/binance/status", max_redirects=0)
    assert resp.status in (401, 403), (
        f"anonim istek {resp.status} aldı; 401/403 beklenirdi")
    ctx.close()
