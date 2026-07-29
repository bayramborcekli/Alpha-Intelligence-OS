"""Windows tek-süreç sunucu girişi.

Linux/Replit üretim yolu DEĞİŞMEDİ: orada gunicorn kullanılır
(`gunicorn -c gunicorn.conf.py app:app`). gunicorn ve fcntl Windows'ta
bulunmadığı için masaüstü başlatıcısı (launcher_windows.py) bu girişi
kullanır.

Tek süreç + çoklu iş parçacığı (waitress): worker-arası paylaşımlı
durum sorunları tanım gereği oluşmaz; flock katmanı yine de aktiftir
(ikinci bir kopya başlatılırsa dosya kilitleri korur).

Yalnız yerel makineden erişim: 127.0.0.1'e bağlanır.

Startup logu güvenlidir: Python yolu, proje kökü, port ve config kaynak
ÖZETİ (source/present — asla değer) yazılır.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import local_env

# Tek env yükleyici: Windows'ta proje .env, Replit'te process env kazanır.
# İdempotent — app.py de çağırsa .env yalnız bir kez uygulanır.
local_env.load_project_env()

HOST = "127.0.0.1"
PORT = int(os.environ.get("ALPHA_PORT", "5000"))
ROOT = Path(__file__).resolve().parent

log = logging.getLogger("alpha.serve")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    try:
        from waitress import serve
    except ImportError:
        log.error("waitress kurulu degil; INSTALL_WINDOWS.cmd calistirin "
                  "veya .venv icine 'pip install waitress' yapin.")
        raise SystemExit(3)

    # Güvenli startup özeti — secret DEĞERİ asla yazılmaz.
    log.info("python=%s", sys.executable)
    log.info("project_root=%s port=%d host=%s", ROOT, PORT, HOST)
    for key, meta in local_env.credential_metadata().items():
        log.info("config %s: present=%s source=%s length=%d",
                 key, meta["present"], meta["source"], meta["length"])

    from app import app

    _bootstrap_background_services()

    serve(app, host=HOST, port=PORT, threads=8)


# Tek instance koruması: bootstrap bu süreçte yalnız bir kez koşar
# (waitress tek süreç; ikinci çağrı çift scheduler oluşturamaz).
_BOOTSTRAP_DONE = False


def _bootstrap_background_services() -> None:
    """Gunicorn post_fork PARİTESİ (bkz. gunicorn.conf.py) — Windows'ta
    aynı arka plan servislerini AYNI SIRAYLA başlatır.

    Config davranışı DEĞİŞMEZ: adaptive_system.enabled / kill_switch /
    auto_paper_enabled / ALPHA_AUTOMATION_ENABLED bayraklarına dokunulmaz;
    servisler yalnız mevcut ayarlara göre başlar veya 'DISABLED' loglar.
    Hiçbir başlatma hatası sunucuyu çökertmez.
    """
    global _BOOTSTRAP_DONE
    if _BOOTSTRAP_DONE:
        log.info("bootstrap zaten yapıldı — ikinci kez başlatılmadı.")
        return
    _BOOTSTRAP_DONE = True

    import threading
    import app as _app

    # 1-2. Başlangıç güvenlik kontrolleri (post_fork ile aynı sıra)
    try:
        _app.validate_startup_config()
        _app.enforce_paper_mode_lock()
    except Exception as exc:
        log.warning("startup kontrol hatası (devam ediliyor): %s", exc)

    # 3. Universe manager — koşulsuz (post_fork paritesi).
    #    İkinci instance koruması: aynı adlı thread yaşıyorsa başlatma.
    try:
        if any(t.name == "auto_analysis" and t.is_alive()
               for t in threading.enumerate()):
            log.info("AUTO LOOP zaten çalışıyor — tekrar başlatılmadı.")
        else:
            _app.um.start_auto_loop(_app._get_main_config)
            log.info("AUTO LOOP STARTED (universe_manager)")
    except Exception as exc:
        log.warning("AUTO LOOP başlatılamadı: %s", exc)

    # 4. Adaptive controller — yalnız config'te enabled=true ise
    #    (start_controller_loop kendi tekil kilidini içerir).
    try:
        cfg0 = _app._get_main_config()
        if cfg0.get("adaptive_system", {}).get("enabled", False):
            started = _app.ac.start_controller_loop()
            log.info("CONTROLLER STARTED" if started
                     else "CONTROLLER zaten çalışıyor — tekrar başlatılmadı.")
        else:
            log.info("CONTROLLER DISABLED (adaptive_system.enabled=false)")
    except Exception as exc:
        log.warning("CONTROLLER başlatılamadı: %s", exc)

    # 5. Automation scheduler — yalnız ALPHA_AUTOMATION_ENABLED="true" ise
    #    (start_automation_scheduler kendi tekil guard'ını içerir).
    try:
        th = _app.start_automation_scheduler()
        if th is not None:
            log.info("AUTOMATION SCHEDULER STARTED")
        else:
            log.info("AUTOMATION SCHEDULER DISABLED "
                     "(ALPHA_AUTOMATION_ENABLED != 'true')")
    except Exception as exc:
        log.warning("AUTOMATION SCHEDULER başlatılamadı: %s", exc)


if __name__ == "__main__":
    main()
