"""Alpha Intelligence OS — Windows TEK TIK kurulum + kurtarma + başlatma akışı.

SETUP_AND_START_WINDOWS.cmd tarafından çağrılır (git/venv adımları
windows_setup.ps1'de biter, bu dosya kalan her şeyi tek akışta yapar):

  .env güvenli onarım → SSL/Binance testleri (5 deneme) → risk kilidi
  görünürlüğü → eski Alpha süreçlerini kapatma → sunucu başlatma →
  health bekleme (180 sn) → isteğe bağlı Binance hesap bağlantısı →
  TEK FINAL raporu.

GÜVENLİK: SSL doğrulaması ASLA kapatılmaz. Canlı emir yolu AÇILMAZ.
Secret'lar terminale BASILMAZ, git'e YAZILMAZ.
"""
from __future__ import annotations

import getpass
import json
import os
import secrets as _secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
MANAGED = {
    "FLASK_ENV": "development",
    "LOCAL_DEV_BYPASS": "1",
    "PAPER_MODE": "true",
    "ALPHA_WINDOWS_PAPER_AUTO": "true",
}
SECRET_KEYS = ("SESSION_SECRET", "FLASK_SECRET_KEY")
FORBIDDEN = ("ALPHA_ENABLE_LIVE_TRADING",)

report: dict[str, str] = {}


def p(line: str = "") -> None:
    print(line, flush=True)


def _decode(raw: bytes) -> str | None:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return None


def repair_env() -> None:
    """`.env`'i güvenle onar: yönetilen 4 anahtarı garanti et, duplicate
    FLASK_ENV satırlarını teke indir, secret yoksa üret (asla basma).

    Mevcut secret/parola/Binance satırları bayt düzeyinde korunur.
    """
    if not ENV_PATH.exists():
        lines = [f"{k}={v}" for k, v in MANAGED.items()]
        lines.append(f"SESSION_SECRET={_secrets.token_hex(32)}")
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        p(".env         : OLUSTURULDU (yerel guvenli varsayilanlar + "
          "SESSION_SECRET uretildi; deger gizli)")
        report["ENV"] = "PASS"
        os.environ.update(MANAGED)
        return
    raw = ENV_PATH.read_bytes()
    text = _decode(raw)
    if text is None:
        p(".env         : OKUNAMADI (bilinmeyen kodlama) — dokunulmadi.")
        report["ENV"] = "FAIL"
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = ENV_PATH.with_name(f".env.backup_{ts}")
    shutil.copy2(ENV_PATH, backup)
    out: list[str] = []
    seen: set[str] = set()
    have_secret = False
    changed = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in MANAGED:
            if key in seen:
                changed = True  # duplicate satır düşürüldü
                continue
            seen.add(key)
            desired = f"{key}={MANAGED[key]}"
            if stripped != desired:
                changed = True
            out.append(desired)
            continue
        if key in SECRET_KEYS and stripped.partition("=")[2].strip():
            have_secret = True
        out.append(line)
    for key, val in MANAGED.items():
        if key not in seen:
            out.append(f"{key}={val}")
            changed = True
    if not have_secret:
        out.append(f"SESSION_SECRET={_secrets.token_hex(32)}")
        changed = True
        p(".env         : SESSION_SECRET uretildi (deger gizli).")
    if changed:
        newline = "\r\n" if b"\r\n" in raw else "\n"
        ENV_PATH.write_text(newline.join(out) + newline, encoding="utf-8")
        p(f".env         : ONARILDI (yedek: {backup.name})")
    else:
        backup.unlink(missing_ok=True)
        p(".env         : ZATEN DOGRU (degisiklik yok)")
    for k, v in MANAGED.items():
        os.environ[k] = v
    report["ENV"] = "PASS"


def ssl_and_binance() -> bool:
    """truststore + 4 endpoint x 5 deneme. En az ana + 2 sembol gerekir."""
    import windows_diagnose as wd
    try:
        import truststore
        truststore.inject_into_ssl()
        p("truststore   : AKTIF (Windows sertifika deposu; dogrulama ACIK)")
        report["TRUSTSTORE"] = "PASS"
    except Exception as exc:
        p(f"truststore   : PASIF ({exc}) — certifi ile devam")
        report["TRUSTSTORE"] = "FAIL"
    import requests
    fail_msg = ""
    for label, url in wd.URLS:
        report[label] = "FAIL"
        for attempt in range(5):
            try:
                with requests.Session() as s:
                    r = s.get(url, timeout=15)
                if r.status_code == 200:
                    report[label] = "PASS"
                    p(f"{label:<12} : PASS"
                      + (f"  [deneme {attempt + 1}/5]" if attempt else ""))
                    break
                report[label] = f"FAIL (HTTP {r.status_code})"
            except requests.exceptions.SSLError as exc:
                fail_msg = str(exc)
                report[label] = "FAIL (SSL)"
            except Exception as exc:
                fail_msg = fail_msg or str(exc)
                report[label] = f"FAIL ({type(exc).__name__})"
            if attempt < 4:
                time.sleep(1.5 * (attempt + 1))
        else:
            p(f"{label:<12} : {report[label]}  (5 deneme)")
    syms = [k for k in wd.SYMBOL_LABELS if report.get(k) == "PASS"]
    ana_ok = report.get("BINANCE ANA") == "PASS"
    n = len(syms)
    report["BINANCE PUBLIC"] = ("PASS" if n == 3 and ana_ok
                                else "DEGRADED" if n >= 2 and ana_ok
                                else "FAIL")
    if report["BINANCE PUBLIC"] == "FAIL" and fail_msg:
        report["ROOT CAUSE"] = wd.classify_ssl(fail_msg)
        return False
    if report["BINANCE PUBLIC"] == "DEGRADED":
        p(f"NOT          : {n}/3 sembol calisiyor — controller calisan "
          "sembollerle devam eder, sorunlu sembol gecici atlanir.")
    return True


def risk_lock_check() -> None:
    """consecutive_losses kilidi varsa GOSTER; yalniz onayla sifirla."""
    state_path = ROOT / "alpha20_v1" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return
    losses = int(state.get("consecutive_losses", 0) or 0)
    if losses < 3:
        return
    p(f"RISK KILIDI  : consecutive_losses={losses} (limit 3) — guard yeni "
      "PAPER islemleri bloke eder. Bakiye/gecmis korunur.")
    try:
        ans = input("Paper test ortaminda sayac sifirlansin mi? (E/H): ")
    except (EOFError, OSError):
        p("Etkilesimli girdi yok — sayac degistirilmedi.")
        return
    if str(ans).strip().lower() in ("e", "evet", "y", "yes"):
        shutil.copy2(state_path, state_path.with_suffix(".json.bak"))
        state["consecutive_losses"] = 0
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        p("RISK KILIDI  : sayac 0 yapildi (yedek: state.json.bak). Bakiye ve "
          "islem gecmisi AYNEN korundu.")
    else:
        p("RISK KILIDI  : degistirilmedi (kilit aktif kalir).")


def stop_old_processes() -> None:
    if os.name != "nt":
        return
    # Proje kökü env üzerinden geçirilir — tek tırnak/boşluk içeren
    # kullanıcı yolları PowerShell alıntılamasını KIRAMAZ.
    env = dict(os.environ, ALPHA_KILL_ROOT=str(ROOT))
    cmd = ("$root=[regex]::Escape($env:ALPHA_KILL_ROOT); "
           "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or "
           "Name='pythonw.exe'\" | Where-Object { $_.ProcessId -ne $PID -and "
           "$_.CommandLine -match ($root+'.*(serve_windows|launcher_windows)') } "
           "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
           "-ErrorAction SilentlyContinue }")
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                   capture_output=True, timeout=30, env=env)
    p("SUREC        : eski Alpha surecleri kapatildi (yalniz bu klasor).")


def start_server() -> int | None:
    py = str(ROOT / ".venv" / "Scripts" / "python.exe")
    if not Path(py).exists():
        py = sys.executable
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    proc = subprocess.Popen([py, str(ROOT / "serve_windows.py")],
                            cwd=str(ROOT), creationflags=flags)
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5,
                              cwd=str(ROOT)).stdout.strip() or None
    except Exception:
        head = None
    info = {"pid": proc.pid, "entrypoint": "serve_windows",
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_head": head}
    try:
        data_dir = ROOT / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "windows_server_info.json").write_text(
            json.dumps(info), encoding="utf-8")
    except OSError:
        pass
    p(f"SUNUCU       : baslatildi (PID {proc.pid}, ayri pencere).")
    return proc.pid


def health_ok(h: dict) -> bool:
    """Misyon sözleşmesinin TAMAMI: yalnız controller+cycle değil."""
    return bool(
        h.get("entrypoint") == "serve_windows"
        and h.get("runtime_override") is True
        and str(h.get("paper")) == "active"
        and str(h.get("auto_loop")) == "running"
        and str(h.get("controller")) == "running"
        and (h.get("cycle_count") or 0) >= 1
        and h.get("last_cycle"))


def wait_health(timeout_s: int = 180) -> dict:
    import requests
    port = os.environ.get("ALPHA_PORT", "5000").strip() or "5000"
    url = f"http://127.0.0.1:{port}/health/runtime"
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                last = r.json()
                if health_ok(last):
                    break
        except Exception:
            pass
        time.sleep(5)
    return last


def _mask_echo_input(prompt: str) -> str:
    try:
        return getpass.getpass(prompt)
    except Exception:
        return input(prompt)


def connect_accounts() -> None:
    """İsteğe bağlı read-only Binance hesap bağlantısı (yeşil SONRASI)."""
    report.setdefault("BINANCE GLOBAL ACCOUNT", "NOT_CONFIGURED")
    report.setdefault("BINANCE TR ACCOUNT", "NOT_CONFIGURED")
    # KALICILIK SÖZLEŞMESİ: mevcut DPAPI credential'ları KORUNUR — SETUP
    # tekrar çalıştırıldığında yeniden API Key/Secret İSTENMEZ, credential
    # silinmez; yalnız otomatik bağlantı testi yapılır.
    from services import binance_connection as bcn
    from services import secure_credentials as sc
    labels = {"BINANCE_GLOBAL": "BINANCE GLOBAL ACCOUNT",
              "BINANCE_TR": "BINANCE TR ACCOUNT"}
    existing = [x for x in ("BINANCE_GLOBAL", "BINANCE_TR")
                if sc.configured(x)]
    for exchange in existing:
        p(f"{exchange}  : ZATEN YAPILANDIRILMIS "
          f"(kaynak: {sc.credential_store(exchange)}; anahtar korunuyor, "
          "yeniden girmeniz gerekmez). Otomatik test yapiliyor...")
        try:
            result = bcn.test_stored(exchange)
            status = str(result.get("status", "ERROR"))
        except Exception as exc:
            status = f"ERROR({type(exc).__name__})"
        if status == "CONNECTED_READ_ONLY":
            p(f"{exchange}  : CONNECTED_READ_ONLY")
            report[labels[exchange]] = "CONNECTED"
        elif status == "CONNECTED_PERMISSIONS_UNVERIFIED":
            p(f"{exchange}  : CONNECTED (yetki dogrulanamadi — sari)")
            report[labels[exchange]] = "CONNECTED (UNVERIFIED)"
        else:
            # Geçici hata credential'ı SİLMEZ — yalnız hata kodu gösterilir.
            p(f"{exchange}  : TEST BASARISIZ ({status}) — anahtar KORUNDU; "
              "gerekirse panelden 'Test Et' ile tekrar deneyin.")
            report[labels[exchange]] = f"PRESERVED ({status})"
    if len(existing) == len(labels):
        return  # her iki hesap da kayıtlı — yeniden bağlantı sorulmaz
    try:
        ans = input("Binance hesap baglantisi yapilandirilsin mi? "
                    "(salt okunur; E/H): ")
    except (EOFError, OSError):
        return
    if str(ans).strip().lower() not in ("e", "evet", "y", "yes"):
        p("HESAP        : atlandi — Paper sistem baglanti olmadan calisir.")
        return
    # Tercih edilen yol: guvenli maskeli form (secret terminale girilmez).
    port = os.environ.get("ALPHA_PORT", "5000").strip() or "5000"
    url = f"http://127.0.0.1:{port}/settings/binance"
    try:
        import webbrowser
        if webbrowser.open(url):
            p(f"HESAP        : tarayicida acildi → {url}")
            p("API Key/Secret'i oradaki maskeli forma girin; bu pencere "
              "sunucuyu calistirmaya devam eder.")
            return
    except Exception:
        pass
    p(f"HESAP        : tarayici acilamadi — {url} adresini elle acabilir "
      "veya asagidaki gizli girisle devam edebilirsiniz.")
    # Tek kanonik yol: bağlantı testi + izin kontrolü + şifreli saklama
    # services.binance_connection'da yapılır (duplicate fetch YASAĞI).
    # Yalnız HENÜZ yapılandırılmamış hesaplar sorulur (mevcutlar korunur).
    plans = [(x, labels[x]) for x in ("BINANCE_GLOBAL", "BINANCE_TR")
             if x not in existing]
    for exchange, label in plans:
        try:
            ans = input(f"{exchange} baglansin mi? (E/H): ")
        except (EOFError, OSError):
            return
        if str(ans).strip().lower() not in ("e", "evet", "y", "yes"):
            continue
        key = _mask_echo_input(f"{exchange} API Key (gizli girilir): ").strip()
        sec = _mask_echo_input(f"{exchange} API Secret (gizli girilir): ").strip()
        if not key or not sec:
            p(f"{exchange}  : bos deger — atlandi.")
            continue
        try:
            result = bcn.connect(exchange, key, sec)
        except Exception as exc:
            p(f"{exchange}  : BAGLANTI TESTI BASARISIZ ({type(exc).__name__})"
              " — anahtar kaydedilmedi. Binance'te anahtari ve IP iznini "
              "kontrol edin.")
            report[label] = "FAIL"
            continue
        status = str(result.get("status", "ERROR"))
        if status == "PERMISSION_DENIED":
            p(f"{exchange}  : REDDEDILDI — anahtar islem/cekim yetkisi "
              "tasiyor. Binance'te YALNIZ 'Enable Reading' yetkili yeni "
              "anahtar olusturun. Anahtar KAYDEDILMEDI.")
            report[label] = "FAIL"
        elif status == "CONNECTED_READ_ONLY":
            p(f"{exchange}  : BAGLANDI (salt okunur dogrulandi; guvenli "
              "yerel depoya yazildi, terminale/git'e yazilmadi).")
            report[label] = "CONNECTED"
        elif status == "CONNECTED_PERMISSIONS_UNVERIFIED":
            # Yetki alanları yanıtta HİÇ yoksa salt-okunurluk kanıtlanamaz
            # (özellikle Binance TR) — bağlantı çalışır ama durum açıkça
            # UNVERIFIED raporlanır; canlı emir yolu zaten kapalı.
            p(f"{exchange}  : BAGLANDI ama yetki durumu API yanitinda "
              "yok — salt-okunurluk KANITLANAMADI. Binance panelinden "
              "anahtarin yalniz okuma yetkili oldugunu kontrol edin. "
              "(Canli emir yolu bu yazilimda zaten kapali.)")
            report[label] = "CONNECTED (UNVERIFIED)"
        else:
            guidance = str(result.get("guidance", "")).strip()
            p(f"{exchange}  : BAGLANTI TESTI BASARISIZ ({status}) — "
              "anahtar kaydedilmedi." + (f" {guidance}" if guidance else ""))
            report[label] = "FAIL"


def final_report(health: dict) -> int:
    ctrl = str(health.get("controller", "stopped"))
    cyc = int(health.get("cycle_count") or 0)
    ok = health_ok(health)
    card = "\U0001F7E2" if ok else ("\U0001F7E1" if health else "\U0001F534")
    p("=" * 62)
    p("ALPHA INTELLIGENCE OS — WINDOWS FINAL STATUS")
    p("=" * 62)
    p(f"GIT            : {report.get('GIT', 'PASS')}")
    p(f"HEAD           : {health.get('git_head') or report.get('HEAD', '?')}")
    p(f"PYTHON ENV     : {report.get('PYENV', 'PASS')}")
    p(f"ENV            : {report.get('ENV')}")
    p(f"TRUSTSTORE     : {report.get('TRUSTSTORE')}")
    p(f"BINANCE PUBLIC : {report.get('BINANCE PUBLIC')}")
    for lbl in ("BINANCE BTC", "BINANCE ETH", "BINANCE SOL"):
        p(f"  {lbl[8:]:<12} : {report.get(lbl)}")
    p(f"SERVER         : {'RUNNING' if health else 'STOPPED'}")
    p(f"ENTRYPOINT     : {health.get('entrypoint', '-')}")
    p(f"RUNTIME OVERRIDE: {health.get('runtime_override', False)}")
    p(f"AUTO LOOP      : {str(health.get('auto_loop', 'stopped')).upper()}")
    p(f"CONTROLLER     : {ctrl.upper()}")
    p(f"PAPER          : {str(health.get('paper', 'disabled')).upper()}")
    p(f"CYCLE COUNT    : {cyc}")
    p(f"LAST CYCLE     : {health.get('last_cycle')}")
    p(f"BINANCE GLOBAL ACCOUNT : {report.get('BINANCE GLOBAL ACCOUNT', 'NOT_CONFIGURED')}")
    p(f"BINANCE TR ACCOUNT     : {report.get('BINANCE TR ACCOUNT', 'NOT_CONFIGURED')}")
    p("LIVE ORDERS    : DISABLED")
    p(f"RUNTIME CARD   : {card}")
    if not ok:
        cause = report.get("ROOT CAUSE")
        if not cause:
            if not health:
                cause = "Sunucu baslamadi — acilan pencerede loglari kontrol edin."
            elif not health.get("runtime_override"):
                cause = ("runtime_override=false — sistem ortam degiskeni "
                         ".env'i eziyor olabilir; teshis ONARIM satirina bakin.")
            elif ctrl != "running":
                cause = "Controller baslamadi — sunucu penceresi loglarina bakin."
            else:
                cause = ("Ilk cevrim henuz bitmedi — sunucu calisiyor, panel "
                         "birkac dakika icinde yesillenir.")
        p(f"ROOT CAUSE     : {cause}")
    p("=" * 62)
    try:  # agent snapshot — dashboard/registry aynı sonucu görür
        from services import windows_runtime_recovery as wrr
        wrr.record_report(dict(report), health)
    except Exception:
        pass
    return 0 if ok else 1


def main() -> int:
    for key in FORBIDDEN:
        os.environ.pop(key, None)  # canlı emir yolu bu akışta asla açılmaz
    p("-" * 62)
    p("[A] .env guvenli onarim...")
    repair_env()
    p("-" * 62)
    p("[B] SSL / Binance public data (5'er deneme)...")
    proceed = ssl_and_binance()
    if not proceed:
        p("DURDU: tum semboller basarisiz. " + report.get("ROOT CAUSE", ""))
        return final_report({})
    p("-" * 62)
    p("[C] Paper risk kilidi kontrolu...")
    risk_lock_check()
    p("-" * 62)
    p("[D] Surec yonetimi + sunucu baslatma...")
    stop_old_processes()
    start_server()
    p("[E] Runtime dogrulaniyor (ilk cevrim icin en fazla 180 sn)...")
    health = wait_health(180)
    if health_ok(health):
        p("-" * 62)
        p("[F] Istege bagli Binance hesap baglantisi...")
        connect_accounts()
    return final_report(health)


if __name__ == "__main__":
    sys.exit(main())
