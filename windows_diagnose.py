"""Alpha Intelligence OS — Windows tek komut otomatik teşhis (salt okunur).

Kullanım (Windows, proje klasöründe):
    python windows_diagnose.py

Ne yapar: FAZ 1-5 kontrollerini sırayla koşar ve tek bir PASS/FAIL final
raporu basar. Hiçbir emir GÖNDERMEZ, hiçbir secret OKUMAZ/BASMAZ. SSL
doğrulaması asla kapatılmaz.

Tek istisna (operatör ONAYIYLA): .env'de ALPHA_WINDOWS_PAPER_AUTO satırı
eksikse teşhis sorar ve 'E' yanıtında .env sonuna YALNIZ bu satırı ekler
(önce yedek alınır). Mevcut satırlara ve secret'lara ASLA dokunulmaz.
"""
from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PAPER_AUTO_KEY = "ALPHA_WINDOWS_PAPER_AUTO"
PAPER_AUTO_LINE = f"{PAPER_AUTO_KEY}=true"

URLS = [
    ("BINANCE ANA", "https://fapi.binance.com/fapi/v1/ping"),
    ("BINANCE BTC", "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=10"),
    ("BINANCE SOL", "https://fapi.binance.com/fapi/v1/klines?symbol=SOLUSDT&interval=1h&limit=10"),
]

results: dict[str, str] = {}


def p(line: str = "") -> None:
    print(line, flush=True)


def classify_ssl(msg: str) -> str:
    m = msg.upper()
    if "CERTIFICATE_VERIFY_FAILED" in m:
        return ("CA sertifika sorunu → antivirüs/proxy HTTPS denetimi kök "
                "sertifikası certifi'de yok. Çözüm: truststore aktif başlatma "
                "(serve_windows bunu otomatik yapar) veya antivirüste "
                "*.binance.com'u SSL taramasından istisna yapın.")
    if "TLSV1_ALERT" in m or "WRONG_VERSION" in m or "HANDSHAKE" in m:
        return "TLS el sıkışması bozuluyor → antivirüs HTTPS inspection / proxy / VPN müdahalesi."
    if "TIMED OUT" in m or "TIMEOUT" in m:
        return "Ağ zaman aşımı → güvenlik duvarı/ağ engeli."
    if "GETADDRINFO" in m or "NAME RESOLUTION" in m:
        return "DNS çözülemedi → DNS/ağ sorunu."
    return "Sınıflandırılamadı — mesajı operatöre iletin."


def paper_auto_status(env_path: Path) -> str:
    """`.env` içinde ALPHA_WINDOWS_PAPER_AUTO satırının durumunu döndürür.

    Dönüş: "missing_file" | "missing_line" | "present".
    Dosya asla DEĞİŞTİRİLMEZ; yalnız okunur.
    """
    try:
        raw = env_path.read_bytes()
    except OSError:
        return "missing_file"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-16")
        except UnicodeError:
            return "missing_line"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, _ = stripped.partition("=")
        if key.strip() == PAPER_AUTO_KEY:
            return "present"
    return "missing_line"


def add_paper_auto_line(env_path: Path) -> str:
    """`.env` sonuna YALNIZ `ALPHA_WINDOWS_PAPER_AUTO=true` satırını ekler.

    - Anahtar zaten varsa (değeri ne olursa olsun) HİÇBİR ŞEY değiştirilmez
      → "already_present" döner.
    - Mevcut satırlar (secret'lar dahil) bayt bayt korunur; yalnız sona
      tek satır eklenir.
    - Yazmadan önce `.env.bak` yedeği alınır (dosya varsa).
    - Dönüş: "added" | "already_present".
    """
    status = paper_auto_status(env_path)
    if status == "present":
        return "already_present"
    if status == "missing_file":
        env_path.write_text(PAPER_AUTO_LINE + "\n", encoding="utf-8")
        return "added"
    # Yedek al, sonra sona ekle (mevcut baytlara dokunma)
    shutil.copy2(env_path, env_path.with_suffix(env_path.suffix + ".bak"))
    raw = env_path.read_bytes()
    suffix = b"" if (not raw or raw.endswith((b"\n", b"\r"))) else b"\r\n" if b"\r\n" in raw else b"\n"
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    with env_path.open("ab") as fh:
        fh.write(suffix + PAPER_AUTO_LINE.encode("ascii") + newline)
    return "added"


def paper_auto_present_guidance(env_path: Path) -> str | None:
    """Satır .env'de MEVCUT ama değeri 'true' değilse net talimat döndürür.

    Dosyaya asla dokunulmaz. Satır yoksa veya değeri zaten 'true' ise None.
    """
    try:
        raw = env_path.read_bytes()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-16")
        except UnicodeError:
            return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == PAPER_AUTO_KEY:
            value = value.strip()
            if value.lower() == "true":
                return None
            shown = value if value else "(bos)"
            return (f"{PAPER_AUTO_KEY} satiri .env'de MEVCUT ama degeri "
                    f"'{shown}' — 'true' degil. Teshis mevcut degeri bilerek "
                    "EZMEZ. Duzeltme: .env dosyasini acin ve satiri su hale "
                    f"getirin: {PAPER_AUTO_LINE}  (sonra sunucuyu yeniden "
                    "baslatin)")
    return None


def paper_auto_env_conflict_guidance(env_path: Path, env_value: str) -> str | None:
    """.env'de deger 'true' iken islem ortaminda true-olmayan deger varsa uyar.

    Bu durumda deger .env'den DEGIL, sistem/kullanici ortam degiskeninden
    geliyor demektir. Dosyaya asla dokunulmaz. Celiski yoksa None.
    """
    if env_value.strip().lower() == "true":
        return None
    try:
        raw = env_path.read_bytes()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-16")
        except UnicodeError:
            return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == PAPER_AUTO_KEY and value.strip().lower() == "true":
            shown = env_value.strip() if env_value.strip() else "(bos)"
            return (f"{PAPER_AUTO_KEY} .env'de 'true' AMA islem ortaminda "
                    f"degeri '{shown}' — deger .env'den degil, sistem/"
                    "kullanici ORTAM DEGISKENINDEN geliyor ve .env'i eziyor. "
                    "Duzeltme: Windows'ta 'Ortam degiskenlerini duzenle' "
                    f"ekranindan {PAPER_AUTO_KEY} degiskenini KALDIRIN veya "
                    "'true' yapin; PowerShell gecici cozum: "
                    f"Remove-Item Env:{PAPER_AUTO_KEY}  (sonra sunucuyu "
                    "yeniden baslatin)")
    return None


def offer_paper_auto_fix(env_path: Path,
                         ask=input) -> str:
    """Eksik PAPER_AUTO için operatöre sorar; onayda satırı ekler.

    Dönüş: "present" | "added" | "declined" | "no_tty".
    """
    if paper_auto_status(env_path) == "present":
        return "present"
    p()
    p(f"{PAPER_AUTO_KEY} .env'de eksik — controller bu satir olmadan")
    p("ASLA baslamaz. Teshis bu satiri sizin onayinizla ekleyebilir.")
    p(f"Eklenecek TEK satir: {PAPER_AUTO_LINE}")
    p("Mevcut satirlara ve secret'lara DOKUNULMAZ; once .env.bak yedegi alinir.")
    try:
        answer = ask(f"{env_path} sonuna eklensin mi? (E/H): ")
    except (EOFError, OSError):
        p("Etkilesimli girdi yok — satiri elle ekleyin: " + PAPER_AUTO_LINE)
        return "no_tty"
    if str(answer).strip().lower() in ("e", "evet", "y", "yes"):
        result = add_paper_auto_line(env_path)
        if result == "added":
            os.environ[PAPER_AUTO_KEY] = "true"
            p("EKLENDI: " + PAPER_AUTO_LINE + "  (yedek: .env.bak)")
        return result
    p("Eklenmedi. Elle eklemek icin .env sonuna su satiri yazin: "
      + PAPER_AUTO_LINE)
    return "declined"

def repair_notice(outcome: str) -> str:
    """Onarım sonucunu FINAL raporda basılacak tek satıra çevirir.

    Yalnız "added" için mesaj döner; diğer durumlarda boş string.
    RECOVER_WINDOWS.cmd akışı kesintisizdir: satır eklendiyse yeniden
    başlatma GEREKMEZ, sunucu bir sonraki adımda zaten başlatılır.
    """
    if outcome == "added":
        return ("ENV ONARIMI    : " + PAPER_AUTO_LINE + " .env'e eklendi "
                "(yedek: .env.bak) — sunucu simdi baslatiliyor, ek islem gerekmez.")
    return ""
def main() -> int:
    p("=" * 62)
    p("ALPHA INTELLIGENCE OS — WINDOWS OTOMATIK TESHIS (salt okunur)")
    p("=" * 62)

    # FAZ 1 — runtime/sürüm
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        head = "?"
    p(f"git_head       : {head}")
    p(f"python         : {sys.version.split()[0]}  ({sys.executable})")
    p(f"os.name        : {os.name}  (nt beklenir)")
    env_auto = os.environ.get("ALPHA_WINDOWS_PAPER_AUTO", "")
    if not env_auto:
        # .env henüz yüklenmemiş olabilir — proje yükleyicisiyle dene
        try:
            import local_env
            local_env.load_project_env()
            env_auto = os.environ.get("ALPHA_WINDOWS_PAPER_AUTO", "")
        except Exception:
            pass
    p(f"PAPER_AUTO env : {env_auto or 'YOK'}  ('true' beklenir)")
    results["ENV"] = "PASS" if env_auto.strip().lower() == "true" else "FAIL"
    env_fix_outcome = ""
    if results["ENV"] == "FAIL":
        env_path = Path(__file__).resolve().parent / ".env"
        if paper_auto_status(env_path) != "present":
            env_fix_outcome = offer_paper_auto_fix(env_path)
            if env_fix_outcome == "added":
                results["ENV"] = "PASS"
                p("PAPER_AUTO env : true  (.env'e eklendi — akis kesintisiz "
                  "devam eder, yeniden baslatma gerekmez)")
        else:
            guidance = paper_auto_present_guidance(env_path)
            if guidance is None:
                guidance = paper_auto_env_conflict_guidance(env_path, env_auto)
            if guidance:
                p("ONARIM         : " + guidance)
    p(f"saat (UTC)     : {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
      "  (gercek saatten sapma varsa SSL bozulur)")

    # FAZ 2 — SSL altyapısı
    p("-" * 62)
    p(f"OpenSSL        : {ssl.OPENSSL_VERSION}")
    try:
        import certifi
        p(f"certifi        : {getattr(certifi, '__version__', '?')}  ({certifi.where()})")
    except Exception as exc:
        p(f"certifi        : YOK ({exc})")
    try:
        import truststore
        truststore.inject_into_ssl()
        p("truststore     : AKTIF (Windows sertifika deposu kullaniliyor)")
        results["TRUSTSTORE"] = "PASS"
    except ImportError:
        p("truststore     : KURULU DEGIL → INSTALL_WINDOWS.cmd'yi calistirin")
        results["TRUSTSTORE"] = "FAIL"
    except Exception as exc:
        p(f"truststore     : etkinlestirilemedi ({exc})")
        results["TRUSTSTORE"] = "FAIL"

    # DNS
    try:
        ip = socket.gethostbyname("fapi.binance.com")
        p(f"DNS            : fapi.binance.com → {ip}")
        results["DNS"] = "PASS"
    except Exception as exc:
        p(f"DNS            : FAIL ({exc})")
        results["DNS"] = "FAIL"
    proxy = {k: v for k, v in os.environ.items()
             if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") and v}
    p(f"proxy env      : {', '.join(proxy) if proxy else 'yok'}")

    # FAZ 3 — doğrudan bağlantı testleri (doğrulama AÇIK)
    p("-" * 62)
    import requests
    ssl_fail_msg = ""
    for label, url in URLS:
        try:
            r = requests.get(url, timeout=15)
            ok = r.status_code == 200
            results[label] = "PASS" if ok else f"FAIL (HTTP {r.status_code})"
            p(f"{label:<14} : {'PASS' if ok else 'FAIL'} (HTTP {r.status_code})")
        except requests.exceptions.SSLError as exc:
            results[label] = "FAIL (SSL)"
            ssl_fail_msg = str(exc)
            p(f"{label:<14} : FAIL — SSLError")
        except Exception as exc:
            results[label] = f"FAIL ({type(exc).__name__})"
            ssl_fail_msg = ssl_fail_msg or str(exc)
            p(f"{label:<14} : FAIL — {type(exc).__name__}: {str(exc)[:140]}")

    # FAZ 5 — çalışan sunucu varsa runtime durumu
    p("-" * 62)
    try:
        r = requests.get("http://127.0.0.1:5000/health/runtime", timeout=5)
        if r.status_code == 200:
            d = r.json()
            for k in ("entrypoint", "git_head", "runtime_override", "paper",
                      "auto_loop", "controller", "cycle_count", "last_cycle"):
                p(f"runtime {k:<16}: {d.get(k)}")
            results["CONTROLLER"] = ("RUNNING" if d.get("controller") == "running"
                                     else str(d.get("controller", "?")).upper())
        else:
            p(f"/health/runtime: HTTP {r.status_code} (oturum gerekebilir — "
              "tarayicidan giris yaptiktan sonra bakin)")
    except Exception:
        p("/health/runtime: sunucu calismiyor (once: python serve_windows.py)")

    # FINAL
    p("=" * 62)
    p("FINAL RAPOR")
    all_binance = all(results.get(k) == "PASS" for k, _ in [(l, u) for l, u in URLS])
    p(f"SSL/BINANCE    : {'PASS' if all_binance else 'FAIL'}")
    for label, _ in URLS:
        p(f"  {label:<12} : {results.get(label)}")
    p(f"TRUSTSTORE     : {results.get('TRUSTSTORE')}")
    p(f"ENV PAPER_AUTO : {results.get('ENV')}")
    p(f"CONTROLLER     : {results.get('CONTROLLER', 'SUNUCU KAPALI')}")
    notice = repair_notice(env_fix_outcome)
    if notice:
        p(notice)
    if not all_binance and ssl_fail_msg:
        p("ROOT CAUSE     : " + classify_ssl(ssl_fail_msg))
    elif all_binance:
        p("ROOT CAUSE     : SSL engeli YOK — sunucuyu yeniden baslatin, "
          "kart yesile doner.")
    p("=" * 62)
    return 0 if all_binance else 1


if __name__ == "__main__":
    sys.exit(main())
