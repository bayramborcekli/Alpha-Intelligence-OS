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
    # Windows SSL: antivirüs/proxy HTTPS denetimi kendi kök sertifikasını
    # Windows sertifika deposuna kurar; certifi bu depoyu görmez ve
    # doğrulama SSLError ile düşer. truststore, Python SSL'ini işletim
    # sistemi deposuna bağlar — doğrulama AÇIK kalır (kapatmak YASAK).
    if os.name == "nt":
        try:
            import truststore
            truststore.inject_into_ssl()
            log.info("SSL: truststore aktif — Windows sertifika deposu "
                     "kullanılıyor (dogrulama ACIK; antivirus/proxy kok "
                     "sertifikalari artik taniniyor).")
        except ImportError:
            log.warning("SSL: truststore paketi yok — kurumsal antivirus/"
                        "proxy SSL denetimi varsa Binance istekleri SSLError "
                        "verebilir. INSTALL_WINDOWS.cmd'yi yeniden calistirin "
                        "(truststore otomatik kurulur).")
        except Exception as exc:
            log.warning("SSL: truststore etkinlestirilemedi (%s) — certifi "
                        "ile devam ediliyor.", exc)

    # SSL tanılama: Windows'ta kline SSL hataları çoğunlukla eski certifi
    # paketinden kaynaklanır — sürüm ve CA dosyası startup'ta loglanır.
    try:
        import certifi
        log.info("certifi=%s ca_bundle=%s", certifi.__version__, certifi.where())
    except Exception as exc:  # pragma: no cover - certifi requests ile gelir
        log.warning("certifi bilgisi alinamadi (%s); SSL sorunlarinda "
                    "INSTALL_WINDOWS.cmd'yi tekrar calistirin.", exc)
    for key, meta in local_env.credential_metadata().items():
        log.info("config %s: present=%s source=%s length=%d",
                 key, meta["present"], meta["source"], meta["length"])

    from app import app

    _bootstrap_background_services()

    serve(app, host=HOST, port=PORT, threads=8)


# Tek instance koruması: bootstrap bu süreçte yalnız bir kez koşar
# (waitress tek süreç; ikinci çağrı çift scheduler oluşturamaz).
_BOOTSTRAP_DONE = False

CONFIG_PATH = ROOT / "alpha20_v1" / "config.json"

# PAPER test bayrakları — YALNIZ BELLEKTE uygulanır; config.json'a yazılmaz.
_PAPER_TEST_FLAGS = {"enabled": True, "auto_paper_enabled": True,
                     "mode": "AUTO", "kill_switch": False}


def _apply_paper_runtime_override() -> bool:
    """Windows local PAPER otomasyonu — SALT BELLEK override'ı.

    Reviewer kararı (Mission — Safe Paper Auto Runtime Override):
    - Startup HİÇBİR dosyayı değiştirmez; config.json byte-byte aynı kalır.
    - OPT-IN: yalnız .env'de ALPHA_WINDOWS_PAPER_AUTO=true ise uygulanır;
      yoksa varsayılan davranış tamamen değişmez.
    - Yalnız os.name == 'nt' ve FLASK_ENV != 'production'.
    - ALPHA_ENABLE_LIVE_TRADING / canlı emir yolu ASLA değişmez.
    Döndürür: override uygulandı mı (controller startup bunu kullanır).
    """
    if os.name != "nt":
        return False  # Linux/Replit: hiçbir şey yapılmaz
    if os.environ.get("ALPHA_WINDOWS_PAPER_AUTO", "").strip().lower() != "true":
        return False  # opt-in yoksa varsayılan davranış aynen korunur
    if os.environ.get("REPLIT_DEPLOYMENT"):
        log.info("PAPER AUTO override ATLANDI (yayınlanmış üretim ortamı)")
        return False
    if os.environ.get("FLASK_ENV", "").lower() == "production":
        # KÖK NEDEN DÜZELTMESİ (Mission — Override Activation Fix):
        # .env.example şablonu güvenli çerez ayarı için FLASK_ENV=production
        # içerir; Windows local masaüstü (127.0.0.1, waitress) hiçbir zaman
        # gerçek üretim değildir. Operatör ALPHA_WINDOWS_PAPER_AUTO=true ile
        # AÇIKÇA opt-in yaptıysa şablon varsayılanı bunu engellemez.
        log.info("NOT: FLASK_ENV=production (.env şablon varsayılanı) — "
                 "ALPHA_WINDOWS_PAPER_AUTO=true açık opt-in olduğu için "
                 "PAPER override yine uygulanıyor (canlı emir yolu KAPALI).")
    try:
        sys.path.insert(0, str(ROOT / "alpha20_v1"))
        import auto_controller as _ac
        _ac.set_runtime_adaptive_override(dict(_PAPER_TEST_FLAGS))
        log.info("WINDOWS PAPER AUTO ENABLED (RUNTIME) — config.json "
                 "DEĞİŞMEDİ; canlı emir yolu KAPALI "
                 "(ALPHA_ENABLE_LIVE_TRADING dokunulmadı).")
        return True
    except Exception as exc:
        log.warning("PAPER AUTO runtime override uygulanamadı: %s", exc)
        return False


def _watch_first_cycle() -> None:
    """İlk çevrim tamamlanınca 'FIRST CYCLE COMPLETED' logu üretir."""
    import threading
    import time

    def _watch() -> None:
        sys.path.insert(0, str(ROOT / "alpha20_v1"))
        try:
            import auto_controller as _ac
        except Exception:
            return
        deadline = time.monotonic() + 300  # en fazla 5 dk bekle
        while time.monotonic() < deadline:
            try:
                st = _ac.get_status()
                if int(st.get("cycle_count") or 0) >= 1:
                    log.info("FIRST CYCLE COMPLETED — cycle_count=%s "
                             "last_cycle=%s", st.get("cycle_count"),
                             st.get("last_cycle") or st.get("updated_at"))
                    return
            except Exception:
                pass
            time.sleep(5)
        log.warning("FIRST CYCLE bekleniyor — 5 dk içinde tamamlanmadı; "
                    "panelden Son Çevrim alanını kontrol edin.")

    threading.Thread(target=_watch, daemon=True,
                     name="first_cycle_watch").start()


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

    # 0. PAPER AUTO runtime override — yalnız Windows local + opt-in env.
    #    SALT BELLEK: hiçbir dosya değişmez.
    paper_override = _apply_paper_runtime_override()

    # 1-2. Başlangıç güvenlik kontrolleri (post_fork ile aynı sıra, fail-fast)
    # NOT: try/except YOK — gunicorn.conf.py post_fork ile tam pari;
    # bu iki kontrol başarısız olursa sunucu başlamamalıdır.
    _app.validate_startup_config()
    _app.enforce_paper_mode_lock()

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
        if paper_override or cfg0.get("adaptive_system", {}).get(
                "enabled", False):
            started = _app.ac.start_controller_loop()
            log.info("CONTROLLER STARTED" if started
                     else "CONTROLLER zaten çalışıyor — tekrar başlatılmadı.")
            if started:
                _watch_first_cycle()
        else:
            log.info("CONTROLLER DISABLED (adaptive_system.enabled=false)")
    except Exception as exc:
        log.warning("CONTROLLER başlatılamadı: %s", exc)

    # 4b. Automation scheduler — yalnız ALPHA_AUTOMATION_ENABLED="true" ise
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
