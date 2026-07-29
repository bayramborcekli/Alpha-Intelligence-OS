---
name: Browser e2e with Playwright on Replit
description: How to run real-browser (Chromium) e2e tests in this workspace
---
Playwright'ın kendi indirdiği chromium-headless-shell bu NixOS ortamında
`libnspr4.so` eksikliğiyle çöker. Kök çözüm: Nix `chromium` sistem paketi
kurulur ve `p.chromium.launch(executable_path=shutil.which("chromium"))`
ile başlatılır.

**Why:** `playwright install chromium` glibc/apt tabanlı paylaşımlı
kütüphaneler bekler; Nix'te bunlar yoktur ve `--with-deps` de çalışmaz.

**How to apply:** Tarayıcı e2e testi gerekince playwright pip paketi +
Nix `chromium`; testte executable yoksa `pytest.skip` ile suite kırılmaz.
5 dakikalık interval gibi zamanlayıcılar `page.clock.install()` +
`fast_forward` ile sarılır; sekme gizleme `document.hidden`'ı
`Object.defineProperty` ile ezip `visibilitychange` dispatch ederek taklit
edilir. Örnek: `tests/e2e/test_binance_autorefresh_browser.py`.
