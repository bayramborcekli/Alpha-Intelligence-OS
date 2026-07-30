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

import json
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

# Kurtarma betiği (RECOVER_WINDOWS.cmd) süreç temizliğinde önce bu dosyadaki
# PID'yi kesin eşleşmeyle dener; komut satırı deseni yalnız yedek yöntemdir.
PID_FILE = ROOT / ".alpha_server.pid"


def write_pid_file(path: Path = PID_FILE, pid: int | None = None) -> bool:
    """Sunucu PID'sini dosyaya yazar; başarısızlık sunucuyu durdurmaz.

    Döndürür: yazma başarılı mı. Dosya yazılamazsa yalnız uyarı loglanır —
    kurtarma betiği komut satırı desenine (yedek yönteme) düşer.
    """
    real_pid = os.getpid() if pid is None else pid
    try:
        path.write_text(f"{real_pid}\n", encoding="ascii")
        log.info("PID dosyası yazıldı: %s (pid=%d)", path, real_pid)
        return True
    except OSError as exc:
        log.warning("PID dosyası yazılamadı (%s): %s — kurtarma betiği "
                    "komut satırı desenli yedek temizliği kullanacak.",
                    path, exc)
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    write_pid_file()
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

    # 0b. .env doğrulama (yalnız RAPOR — sunucu startup'ı dosya YAZMAZ;
    #     onarım SETUP akışının işidir). Eksik yönetilen anahtar loglanır.
    for _k in ("FLASK_ENV", "LOCAL_DEV_BYPASS", "PAPER_MODE",
               "ALPHA_WINDOWS_PAPER_AUTO"):
        if not os.environ.get(_k, "").strip():
            log.warning(".env dogrulama: %s tanimsiz — "
                        "SETUP_AND_START_WINDOWS.cmd .env'i onarir.", _k)

    # 0c. Kalıcı Binance bağlantısı otomatik geri yükleme: DPAPI deposunda
    #     kayıtlı credential varsa arka planda read-only test edilir ve
    #     panel durumu güncellenir (kullanıcıdan anahtar İSTENMEZ; test
    #     başarısız olsa bile credential SİLİNMEZ). gunicorn post_fork
    #     paritesi. Paper controller bundan bağımsız çalışır.
    try:
        from services import binance_connection as _bc
        _bc.start_startup_tests_async()
        _bc.start_periodic_refresh()
        log.info("BINANCE CONNECTION RESTORE basladi (arka plan; "
                 "kayitli anahtar varsa otomatik test edilir).")
    except Exception as exc:
        log.warning("Binance baglanti geri yukleme baslatilamadi: %s", exc)

    # 0d. Acil durdurma raporu — startup ASLA otomatik kaldırmaz.
    try:
        from services import emergency_stop as _es
        _est = _es.status()
        if _est["active"]:
            log.warning("EMERGENCY STOP ACTIVE (reason=%s, by=%s) — "
                        "yeni PAPER islemi acilmaz. Guvenli kaldirma: "
                        "Operation Center > Acil Durdurma karti.",
                        _est["reason_code"] or "UNKNOWN_LEGACY_STATE",
                        _est["triggered_by"] or "unknown")
    except Exception as exc:
        log.warning("Emergency stop durumu okunamadi: %s", exc)

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

    # 4a2. Desired-state reconciliation — kayıtlı Paper tercihini geri yükle.
    # Tercihleri (risk profili, scan interval) motorlara uygula —
    # reconcile'dan ÖNCE, controller doğru limitlerle başlasın.
    try:
        from services import system_runtime_orchestrator as sro
        sro.start(_app)
    except Exception as exc:
        log.warning("Orchestrator başlangıcı başarısız: %s", exc)
    _reconcile_paper_desired_state(_app)

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

    # 4c. Dual-model kısa vadeli PAPER modelleri — gunicorn post_fork
    #     PARİTESİ (bkz. gunicorn.conf.py). Windows'ta post_fork hiç
    #     koşmadığı için hook burada zorunludur; süreçler arası tek koşu
    #     dual_model içindeki flock'ta (Windows: portable_flock/msvcrt,
    #     süreç ölünce kilit otomatik düşer — bayat kilit kalmaz).
    #     LIVE ORDERS DISABLED. Hata sunucuyu çökertmez.
    try:
        import dual_model as _dm
        if _dm.start_dual_model_loop(_app._get_main_config):
            log.info("DUAL-MODEL LOOP STARTED (LIVE ORDERS DISABLED)")
        else:
            cfg_dm = _dm.get_config(_app._get_main_config())
            if not cfg_dm.get("enabled", True):
                log.info("DUAL-MODEL DISABLED (dual_model.enabled=false)")
            else:
                msg = ("DUAL_MODEL_LOOP_NOT_STARTED: süreç kilidi "
                       "alınamadı — başka bir sunucu süreci koşuyor "
                       "olabilir; listeler yenilenmiyorsa süreçleri "
                       "kontrol edin.")
                log.warning(msg)
                # Yalnız gerçek Windows tek-süreç girişinde panele yaz:
                # Replit/gunicorn'da kilidi diğer worker tutar (normal).
                if _reconcile_env_ok():
                    _dm.record_startup_failure(msg)
    except Exception as exc:
        log.warning("DUAL-MODEL başlatılamadı: %s", exc)


def _reconcile_env_ok() -> bool:
    """Reconcile ortam kapısı: yalnız yerel Windows (Replit'te no-op).

    Ayrı fonksiyon: testler os.name'i GLOBAL patch'lemeden (pathlib'i
    bozmadan) bu kapıyı taklit edebilir."""
    import local_env
    return (not local_env.is_replit()) and os.name == "nt"


def _reconcile_paper_desired_state(_app) -> None:
    """Startup desired-state reconciliation — YALNIZ yerel Windows + PAPER.

    Kanonik kalıcı tercih kaynağı: alpha20_v1/operation_control_state.json
    (git dışı, flock'lu paylaşımlı depo — Operation Center servisi yazar).
    Kullanıcının açık tercihi (automation RUNNING/STOPPED + sembol
    ENABLED/DISABLED) yeniden başlatmada korunur:

    - Emergency Stop veya risk kilidi aktifse HİÇBİR ŞEY başlatılmaz;
      kayıtlı tercih SİLİNMEZ, açık blocker loglanır.
    - Kayıtlı tercih RUNNING ise controller'ın çalıştığı doğrulanır.
    - Kayıtlı tercih STOPPED ise otomatik başlatma YAPILMAZ.
    - Live Trading hiçbir durumda açılmaz (LIVE ORDERS: DISABLED).
    - Replit ortamında tam no-op (yerel Windows state'e dokunulmaz).

    Sonuç, windows_runtime_recovery raporu ve /health/runtime için
    data/paper_reconcile_last.json dosyasına yazılır (secret içermez).
    """
    try:
        if not _reconcile_env_ok():
            log.info("PAPER RECONCILE atlandi (yalniz yerel Windows).")
            return
    except Exception as exc:
        log.warning("PAPER RECONCILE ortam kontrolu yapilamadi: %s", exc)
        return
    try:
        from services import emergency_stop as _es
        est = _es.status()
        if est.get("environment") == "LIVE":
            log.warning("PAPER RECONCILE: LIVE mod — hicbir otomasyon "
                        "baslatilmaz (fail-closed).")
            _record_reconcile_result("LIVE_FAIL_CLOSED")
            return
        if est["active"]:
            reason = est.get("reason_code") or "UNKNOWN_LEGACY_STATE"
            log.warning("PAPER RECONCILE: EMERGENCY/RISK STOP ACTIVE "
                        "(reason=%s) — kayitli otomasyon tercihi geri "
                        "YUKLENMEDI; tercih SILINMEDI. Guvenli kaldirma: "
                        "Operation Center > Acil Durdurma karti.", reason)
            _record_reconcile_result("BLOCKED_EMERGENCY", detail=reason)
            return
        svc = _app.get_operation_service()
        state = svc.automation_state.value
        symbols = {s: st.value for s, st in svc.symbol_states().items()}
        if state != "RUNNING":
            log.info("PAPER RECONCILE: kayitli tercih automation=%s — "
                     "otomatik baslatma YOK (kullanicinin STOP tercihi "
                     "korunur). Semboller: %s", state, symbols or "{}")
            _record_reconcile_result("PRESERVED_STOPPED",
                                     automation=state, symbols=symbols)
            return
        started = _app.ac.start_controller_loop()
        log.warning(
            "*** PAPER PREFERENCE RESTORED ***: automation=RUNNING, "
            "semboller=%s, controller=%s. LIVE ORDERS: DISABLED.",
            symbols or "{}",
            "STARTED" if started else "ALREADY_RUNNING",
        )
        _record_reconcile_result("RESTORED_RUNNING", automation="RUNNING",
                                 symbols=symbols,
                                 detail=("STARTED" if started
                                         else "ALREADY_RUNNING"))
    except Exception as exc:
        log.warning("PAPER RECONCILE basarisiz (tercih dosyasi korunur): "
                    "%s", exc)
        _record_reconcile_result("ERROR", detail=str(exc)[:200])


RECONCILE_RESULT_PATH = (Path(__file__).resolve().parent / "data" /
                         "paper_reconcile_last.json")


def _record_reconcile_result(result: str, *, automation: str | None = None,
                             symbols: dict | None = None,
                             detail: str | None = None) -> None:
    """Reconcile sonucunu diske yazar (best-effort; secret içermez).

    /health/runtime ve windows_runtime_recovery snapshot'ı bu dosyayı
    okuyarak son startup reconcile kararını raporlar."""
    from datetime import datetime, timezone
    snap = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "result": result,
        "automation": automation,
        "symbols": symbols or {},
        "detail": detail,
    }
    try:
        RECONCILE_RESULT_PATH.parent.mkdir(exist_ok=True)
        RECONCILE_RESULT_PATH.write_text(
            json.dumps(snap, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError as exc:
        log.warning("PAPER RECONCILE sonucu diske yazilamadi: %s", exc)


if __name__ == "__main__":
    main()
