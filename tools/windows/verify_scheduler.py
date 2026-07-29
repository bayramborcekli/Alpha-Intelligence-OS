# -*- coding: utf-8 -*-
"""Windows zamanlayıcı + evren saha doğrulama aracı (Task 121).

FIX ANALYSIS SCHEDULER misyonu tek kanonik zamanlayıcı durumunu kurdu;
bu araç GERÇEK Windows PAPER başlangıcında uçtan uca kanıt toplar:

  1. /api/paper/state içinden overall_pipeline,
     analysis_scheduler_detail, universe_size, universe_reason_code
     alanlarını okur ve rapora yazar.
  2. Tercih RUNNING iken kanonik durum "RUNNING" mı, tarama aralığı
     5 dk mı, last_run/next_run doluyor mu doğrular.
  3. Evren BTC/ETH/SOL'un (3 temel sembol) üzerine genişledi mi;
     3'te kaldıysa dürüst neden kodu (NOT_RUN_YET /
     INSUFFICIENT_ELIGIBLE_SYMBOLS / UNIVERSE_REFRESH_FAILED) var mı
     kontrol eder. Genişlemeden neden kodu yoksa FAIL (false-GREEN).
  4. Tercih RUNNING ama worker yoksa durum STARTUP_FAILED olmalı ve
     pipeline GREEN OLMAMALI — aksi false-GREEN regresyonudur.
  5. (İsteğe bağlı, --watch N) N dakika boyunca izler; zamanlayıcının
     ilk başarılı koşusunu (last_run dolması) ve evren genişlemesini
     ya da dürüst neden kodunu bekler.

Kullanım (Windows, servis çalışırken — SETUP_AND_START sonrası):

    py tools\\windows\\verify_scheduler.py
    py tools\\windows\\verify_scheduler.py --url http://127.0.0.1:5000
    py tools\\windows\\verify_scheduler.py --watch 12

Kanıt dosyası: çalışma dizinine verify_scheduler_report.json yazılır
(rapora eklemek için). Yalnız standart kütüphane kullanır. Parola asla
komut satırına yazılmaz (getpass), asla loglanmaz/rapora yazılmaz.
Çıkış kodu: 0 = tüm kontroller PASS, 1 = en az bir FAIL, 2 = erişim/
oturum hatası.
"""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

OK = "[PASS]"
BAD = "[FAIL]"
INFO = "[BILGI]"

BASE_UNIVERSE_SIZE = 3  # BTC/ETH/SOL — temel evren
HONEST_REASON_CODES = ("NOT_RUN_YET", "INSUFFICIENT_ELIGIBLE_SYMBOLS",
                       "UNIVERSE_REFRESH_FAILED")


class Client:
    """Çerezli basit HTTP istemcisi (tek oturum)."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, path: str) -> tuple[int, str]:
        req = urllib.request.Request(self.base + path)
        try:
            with self.opener.open(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def post(self, path: str, form: dict) -> tuple[int, str]:
        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(self.base + path, data=data)
        try:
            with self.opener.open(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def get_json(self, path: str) -> dict:
        status, body = self.get(path)
        if status != 200:
            raise RuntimeError(f"{path} -> HTTP {status}")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise RuntimeError(f"{path} JSON değil: {body[:120]}") from exc


def login(c: Client) -> None:
    status, body = c.get("/login")
    if status != 200:
        raise RuntimeError(f"/login erişilemedi (HTTP {status}). "
                           "Servis çalışıyor mu?")
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', body)
    if not m:
        # Zaten girişli olabilir (redirect edilmiş sayfa)
        if "/home" in body or "Trading" in body:
            return
        raise RuntimeError("Login sayfasında CSRF token bulunamadı.")
    user = input("Kullanıcı adı: ").strip()
    pw = getpass.getpass("Parola (gizli): ")
    c.post("/login", {
        "csrf_token": m.group(1), "username": user, "password": pw})
    st, _ = c.get("/api/paper/state")
    if st != 200:
        raise RuntimeError("Giriş başarısız görünüyor: /api/paper/state "
                           f"HTTP {st}. Kullanıcı adı/parolayı kontrol edin.")


def snapshot(c: Client) -> dict:
    """Kanıt için gereken alanları /api/paper/state'ten çıkarır."""
    st = c.get_json("/api/paper/state")
    det = st.get("analysis_scheduler_detail") or {}
    return {
        "overall_pipeline": st.get("overall_pipeline"),
        "pipeline_blockers": st.get("pipeline_blockers"),
        "analysis_scheduler": st.get("analysis_scheduler"),
        "analysis_scheduler_detail": det,
        "scan_interval": st.get("scan_interval"),
        "universe_size": st.get("universe_size"),
        "universe_reason_code": st.get("universe_reason_code"),
    }


def check_snapshot(s: dict) -> list[str]:
    """Kanonik zamanlayıcı + evren tutarlılık kontrolleri."""
    fails: list[str] = []
    det = s["analysis_scheduler_detail"] or {}
    pref = det.get("preference")
    state = det.get("state") or s.get("analysis_scheduler")
    pipeline = s.get("overall_pipeline")
    usize = s.get("universe_size")
    ucode = s.get("universe_reason_code")

    print(f"\n{INFO} Kanonik zamanlayıcı durumu:")
    print(f"  - tercih={pref} durum={state} "
          f"interval={det.get('interval_minutes')}dk "
          f"last_run={det.get('last_run')} next_run={det.get('next_run')} "
          f"last_result={det.get('last_result')}")
    print(f"  - pipeline={pipeline} blockers={s.get('pipeline_blockers')}")
    print(f"  - universe_size={usize} reason_code={ucode}")

    if pref == "RUNNING":
        if state == "RUNNING":
            print(f"  - tercih RUNNING → kanonik durum RUNNING  {OK}")
            if det.get("interval_minutes") != 5:
                fails.append("Tarama aralığı 5 dk değil: "
                             f"{det.get('interval_minutes')}")
            if det.get("last_run") and not det.get("next_run"):
                fails.append("last_run dolu ama next_run boş — "
                             "next_run hesaplanmıyor")
        elif state == "STARTUP_FAILED":
            print(f"  - tercih RUNNING ama worker YOK → STARTUP_FAILED "
                  f"(dürüst)  {OK}")
            if pipeline == "GREEN":
                fails.append("STARTUP_FAILED iken pipeline GREEN — "
                             "false-GREEN regresyonu")
            if det.get("last_error"):
                print(f"  - last_error: {det['last_error']}")
        else:
            fails.append(f"Tercih RUNNING ama durum '{state}' — "
                         "kanonik durum RUNNING/STARTUP_FAILED olmalıydı")
    elif pref == "STOPPED":
        print(f"  - tercih STOPPED — zamanlayıcı bilinçli kapalı; "
              "kanıt için /automation'dan RUNNING yapın  {0}".format(BAD))
        fails.append("Tercih STOPPED: doğrulama RUNNING tercihiyle "
                     "yapılmalı (Operasyon Merkezi → Otomasyon → Başlat)")
    else:
        fails.append(f"Zamanlayıcı tercihi okunamadı: {pref!r}")

    # Evren dürüstlük kuralı
    if isinstance(usize, int) and usize > BASE_UNIVERSE_SIZE:
        print(f"  - evren {usize} sembole genişlemiş (>3)  {OK}")
        if ucode:
            fails.append(f"Evren genişlemişken reason_code={ucode} — "
                         "çelişkili rozet")
    elif isinstance(usize, int):
        if ucode in HONEST_REASON_CODES:
            print(f"  - evren {usize} sembolde, dürüst neden kodu "
                  f"{ucode}  {OK}")
        else:
            fails.append(f"Evren {usize} sembolde ama dürüst neden kodu "
                         f"yok (reason_code={ucode!r}) — false-GREEN")
    else:
        fails.append(f"universe_size okunamadı: {usize!r}")
    return fails


def watch(c: Client, minutes: int) -> tuple[dict, list[str]]:
    """Zamanlayıcının ilk koşusunu ve evren sonucunu izler."""
    print(f"\n{INFO} {minutes} dk izleme: ilk başarılı koşu (last_run) "
          "ve evren genişlemesi / dürüst neden kodu bekleniyor...")
    deadline = time.time() + minutes * 60
    s = snapshot(c)
    while time.time() < deadline:
        det = s["analysis_scheduler_detail"] or {}
        ran = bool(det.get("last_run"))
        expanded = (isinstance(s.get("universe_size"), int)
                    and s["universe_size"] > BASE_UNIVERSE_SIZE)
        honest = s.get("universe_reason_code") in HONEST_REASON_CODES
        if ran and (expanded or (honest and s.get("universe_reason_code")
                                 != "NOT_RUN_YET")):
            print(f"  - koşu tamam: last_run={det.get('last_run')} "
                  f"universe_size={s['universe_size']} "
                  f"reason={s.get('universe_reason_code')}")
            break
        print(f"  - bekleniyor... durum={det.get('state')} "
              f"last_run={det.get('last_run')} "
              f"universe={s.get('universe_size')} "
              f"reason={s.get('universe_reason_code')}")
        time.sleep(30)
        s = snapshot(c)
    return s, check_snapshot(s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:5000",
                    help="Servis adresi (vars: http://127.0.0.1:5000)")
    ap.add_argument("--watch", type=int, metavar="DAKIKA", default=0,
                    help="İlk koşuyu/evren sonucunu bu kadar dakika izle")
    ap.add_argument("--report", default="verify_scheduler_report.json",
                    help="Kanıt JSON dosyası yolu")
    args = ap.parse_args()

    c = Client(args.url)
    try:
        login(c)
    except RuntimeError as exc:
        print(f"{BAD} {exc}")
        return 2

    s = snapshot(c)
    fails = check_snapshot(s)
    if args.watch > 0:
        s, fails = watch(c, args.watch)

    report = {
        "tool": "verify_scheduler",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "snapshot": s,
        "failures": fails,
        "result": "PASS" if not fails else "FAIL",
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n{INFO} Kanıt dosyası yazıldı: {args.report}")

    if fails:
        print(f"\n{BAD} {len(fails)} kontrol başarısız:")
        for x in fails:
            print(f"  * {x}")
        return 1
    print(f"\n{OK} Tüm zamanlayıcı/evren kontrolleri geçti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
