"""Alpha Intelligence OS — Windows masaüstü başlatıcısı (tek akıl).

start_alpha.cmd / stop_alpha.cmd / INSTALL_WINDOWS.cmd yalnız bu dosyayı
çağırır. Tüm mantık buradadır:

- Project root:  Path(__file__).resolve().parent  (cwd'den BAĞIMSIZ)
- .venv:         yalnız <root>/.venv  (eski clone .venv'i ASLA kullanılmaz)
- Python:        yalnız <root>/.venv/Scripts/python.exe
                 (sistem Python'a fallback YOK; bootstrap dışında)
- Bootstrap:     .venv yoksa oluşturur, pip günceller, requirements kurar,
                 waitress'i doğrular; idempotenttir.
- Port 5000:     Alpha sağlıklıysa yeni kopya AÇMAZ, tarayıcıya gider;
                 yabancı süreç varsa net hata verir; asla süreç öldürmez.
- PID:           <root>/runtime/alpha.pid (pid + start_time + root).
- Log:           <root>/runtime/launcher.log — SECRET/ENV DEĞERİ YAZILMAZ.
- PATH:          global PATH asla değiştirilmez; subprocess çağrıları
                 mutlak (absolute) executable yolları kullanır.

Kullanım:
    python launcher_windows.py            # kur (gerekirse) + başlat
    python launcher_windows.py --install  # bootstrap + kısayol + smoke test
    python launcher_windows.py --stop     # güvenli durdurma
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
PID_FILE = RUNTIME / "alpha.pid"
LOG_FILE = RUNTIME / "launcher.log"
PORT = int(os.environ.get("ALPHA_PORT", "5000"))
HEALTH_URL = f"http://127.0.0.1:{PORT}/health"
HOME_URL = f"http://127.0.0.1:{PORT}/home"
READY_TIMEOUT_S = 60


def venv_python() -> Path:
    """Yalnız BU clone'un .venv python'u (platforma göre)."""
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def log(msg: str) -> None:
    """Sabit metin loglar; ortam değişkeni/secret ASLA yazılmaz."""
    RUNTIME.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def fail(msg: str, code: int = 1) -> "int":
    log(f"HATA: {msg}")
    return code


# ── Bootstrap (GÖREV A) ─────────────────────────────────────────────────────

def find_bootstrap_python() -> list[str] | None:
    """Yalnız .venv OLUŞTURMAK için sistem Python'u arar (py -3 tercih).
    Runtime asla bu yorumlayıcıyla çalışmaz."""
    import shutil
    py = shutil.which("py")
    if py:
        return [py, "-3"]
    python = shutil.which("python") or shutil.which("python3")
    if python:
        return [python]
    return None


def bootstrap_venv() -> int:
    """.venv yoksa oluşturur ve bağımlılıkları kurar. İdempotent:
    .venv + waitress mevcutsa hiçbir şey kurmaz."""
    vpy = venv_python()
    if vpy.exists():
        chk = subprocess.run([str(vpy), "-c", "import waitress, flask"],
                             cwd=ROOT, capture_output=True)
        if chk.returncode == 0:
            log("Bootstrap: .venv mevcut ve dogrulandi (kurulum atlandi).")
            # SSL guvenilirligi icin certifi'yi her calistirmada guncel tut
            # (Windows'ta eski CA paketi Binance SSL dogrulamasini bozabilir).
            r = subprocess.run([str(vpy), "-m", "pip", "install", "--upgrade",
                                "certifi", "--quiet"], cwd=ROOT)
            if r.returncode == 0:
                log("Bootstrap: certifi guncellendi (SSL CA paketi).")
            else:
                log("Bootstrap: UYARI - certifi guncellenemedi; "
                    "SSL sorunlarinda INSTALL_WINDOWS.cmd'yi tekrar calistirin.")
            return 0
        log("Bootstrap: .venv var ama bagimliliklar eksik; kurulacak.")
    else:
        base = find_bootstrap_python()
        if base is None:
            return fail("Python 3.11+ bulunamadi. Lutfen python.org'dan "
                        "Python 3.11 veya ustunu kurun (py launcher ile).")
        log("Bootstrap: .venv olusturuluyor...")
        r = subprocess.run(base + ["-m", "venv", str(ROOT / ".venv")],
                           cwd=ROOT)
        if r.returncode != 0 or not vpy.exists():
            return fail(".venv olusturulamadi (python -m venv basarisiz).")
    # sys.prefix dogrulamasi: venv gercekten BU root altinda mi?
    pref = subprocess.run([str(vpy), "-c", "import sys; print(sys.prefix)"],
                          cwd=ROOT, capture_output=True, text=True)
    if str(ROOT) not in (pref.stdout or ""):
        return fail(".venv bu proje kokune ait degil (izolasyon ihlali).")
    log("Bootstrap: pip guncelleniyor ve bagimliliklar kuruluyor...")
    steps = [
        [str(vpy), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        [str(vpy), "-m", "pip", "install", "-r",
         str(ROOT / "requirements.txt"), "--quiet"],
        # Windows'ta SSL dogrulamasinin guvenilir calismasi icin CA paketi
        # (certifi) her kurulumda en guncel surume cekilir.
        [str(vpy), "-m", "pip", "install", "--upgrade", "certifi", "--quiet"],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            return fail("Bagimlilik kurulumu basarisiz (pip). "
                        "runtime\\launcher.log dosyasina bakin.")
    chk = subprocess.run([str(vpy), "-c", "import waitress"], cwd=ROOT)
    if chk.returncode != 0:
        return fail("waitress kurulamadi; requirements.txt kontrol edin.")
    log("Bootstrap: tamamlandi.")
    return 0


# ── Port / saglik / PID (GÖREV C) ───────────────────────────────────────────

def alpha_healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def port_in_use() -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def read_pid_meta() -> dict | None:
    try:
        meta = json.loads(PID_FILE.read_text(encoding="utf-8"))
        return meta if isinstance(meta, dict) else None
    except (OSError, ValueError):
        return None


def write_pid_meta(pid: int) -> None:
    RUNTIME.mkdir(exist_ok=True)
    PID_FILE.write_text(json.dumps({
        "pid": pid, "started_at": time.time(),
        "root": str(ROOT), "port": PORT}), encoding="utf-8")


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True)
        return f'"{pid}"' in (out.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_belongs_to_this_root(pid: int) -> bool:
    """PID'in komut satırı BU clone'un serve_windows.py'sini mi çalıştırıyor?
    (Eski clone/yabancı süreç korunur — asla öldürülmez.)"""
    if os.name != "nt":
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(
                "utf-8", "replace")
        except OSError:
            return False
        return "serve_windows.py" in cmdline and str(ROOT) in cmdline
    ps = ("$p = Get-CimInstance Win32_Process -Filter \"ProcessId = "
          f"{pid}\"; if ($p) {{ $p.CommandLine }}")
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True)
    cmdline = out.stdout or ""
    return "serve_windows.py" in cmdline and str(ROOT) in cmdline


def open_browser(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


# ── Baslat / durdur ─────────────────────────────────────────────────────────

def start() -> int:
    os.chdir(ROOT)
    rc = bootstrap_venv()
    if rc != 0:
        return rc
    vpy = venv_python()
    # Kopya koruması: canli /health denetimi PID dosyasından ÖNCE gelir.
    if alpha_healthy():
        log("Alpha zaten calisiyor; kopya baslatilmadi, tarayici aciliyor.")
        open_browser(HOME_URL)
        return 0
    if port_in_use():
        meta = read_pid_meta()
        if meta and meta.get("root") and meta["root"] != str(ROOT):
            return fail(f"Eski Alpha instance calisiyor: {meta['root']} — "
                        "once onu durdurun (stop_alpha.cmd).")
        return fail(f"Port {PORT} baska uygulama tarafindan kullaniliyor.")
    # Bayat PID temizligi
    meta = read_pid_meta()
    if meta and not pid_alive(int(meta.get("pid", -1))):
        log("Bayat PID dosyasi temizlendi.")
        PID_FILE.unlink(missing_ok=True)
    log(f"Sunucu baslatiliyor: .venv python + serve_windows.py "
        f"(port {PORT})")
    kwargs: dict = {"cwd": str(ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.Popen([str(vpy), str(ROOT / "serve_windows.py")],
                            **kwargs)
    write_pid_meta(proc.pid)
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if alpha_healthy():
            log("Hazirlik denetimi OK; tarayici aciliyor.")
            open_browser(HOME_URL)
            return 0
        if proc.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            return fail(f"Sunucu erken kapandi (exit={proc.returncode}). "
                        "runtime\\launcher.log dosyasina bakin.")
        time.sleep(1)
    return fail(f"Sunucu {READY_TIMEOUT_S} sn icinde hazir olmadi; "
                "tarayici ACILMADI.")


def stop() -> int:
    meta = read_pid_meta()
    if not meta:
        if alpha_healthy():
            return fail("PID dosyasi yok ama Alpha calisiyor; sureci "
                        "gorev yoneticisinden kapatin (guvenlik geregi "
                        "otomatik oldurulmedi).")
        log("Durdurulacak calisan Alpha bulunamadi.")
        return 0
    pid = int(meta.get("pid", -1))
    if meta.get("root") != str(ROOT):
        return fail("PID dosyasi baska bir clone'a ait; DOKUNULMADI.")
    if not pid_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        log("Surec zaten kapali; bayat PID temizlendi.")
        return 0
    if not pid_belongs_to_this_root(pid):
        return fail("PID baska bir surece ait (PID yeniden kullanimi); "
                    "DOKUNULMADI.")
    log(f"Alpha durduruluyor (PID {pid})...")
    if os.name == "nt":
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)
        r = subprocess.CompletedProcess([], 0)
    if r.returncode == 0:
        PID_FILE.unlink(missing_ok=True)
        log("Alpha durduruldu.")
        return 0
    return fail("Durdurma basarisiz (taskkill hata dondurdu).")


def install() -> int:
    """INSTALL_WINDOWS.cmd akisi: bootstrap + kisayol + smoke test.
    Gercek secret OLUSTURMAZ; .env kullanicinin sorumlulugundadir."""
    os.chdir(ROOT)
    rc = bootstrap_venv()
    if rc != 0:
        return rc
    if not (ROOT / ".env").exists():
        log("Not: .env bulunamadi — exchange baglantisi icin proje kokune "
            ".env koyabilirsiniz (.env.example'a bakin). Secret degerleri "
            "asla loglanmaz.")
    if os.name == "nt":
        ps1 = ROOT / "tools" / "windows" / "create_desktop_shortcut.ps1"
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy",
                            "Bypass", "-File", str(ps1)], cwd=ROOT)
        if r.returncode != 0:
            return fail("Masaustu kisayolu olusturulamadi.")
        log("Masaustu kisayolu olusturuldu/guncellendi.")
    # Smoke test: app import edilebiliyor mu (sunucu ACILMADAN)?
    smoke = subprocess.run(
        [str(venv_python()), "-c",
         "import app; print('SMOKE_OK')"],
        cwd=ROOT, capture_output=True, text=True)
    if "SMOKE_OK" not in (smoke.stdout or ""):
        return fail("Smoke test basarisiz: uygulama import edilemedi. "
                    "runtime\\launcher.log dosyasina bakin.")
    log("Installation completed.")
    return 0


def main(argv: list[str]) -> int:
    if "--stop" in argv:
        return stop()
    if "--install" in argv:
        return install()
    return start()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
