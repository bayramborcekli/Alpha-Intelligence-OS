"""Alpha Intelligence OS — Windows tek komut otomatik teşhis (salt okunur).

Kullanım (Windows, proje klasöründe):
    python windows_diagnose.py

Ne yapar: FAZ 1-5 kontrollerini sırayla koşar ve tek bir PASS/FAIL final
raporu basar. Hiçbir dosyayı DEĞİŞTİRMEZ, hiçbir emir GÖNDERMEZ, hiçbir
secret OKUMAZ/BASMAZ. SSL doğrulaması asla kapatılmaz.
"""
from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone

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
    if not all_binance and ssl_fail_msg:
        p("ROOT CAUSE     : " + classify_ssl(ssl_fail_msg))
    elif all_binance:
        p("ROOT CAUSE     : SSL engeli YOK — sunucuyu yeniden baslatin, "
          "kart yesile doner.")
    p("=" * 62)
    return 0 if all_binance else 1


if __name__ == "__main__":
    sys.exit(main())
