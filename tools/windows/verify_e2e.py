# -*- coding: utf-8 -*-
"""Windows uçtan uca saha doğrulama aracı (Task: üç uç tutarlılığı).

Gerçek Windows kurulumunda, git pull sonrası ÇALIŞAN servise karşı
aşağıdaki kabul kriterlerini otomatik doğrular:

  1. Aynı oturumda /api/accounts, /api/accounts/wallets ve
     /api/accounts/portfolio her hesap için AYNI connection_state
     döndürüyor mu? (Zıt durum = FAIL)
  2. Paper (simülasyon) bakiyesi UNKNOWN mu? Servis farklı bir çalışma
     dizininden başlatılmış olsa bile UNKNOWN olmamalı.
  3. (İsteğe bağlı, --watch) Hesaplarım → Düzenle ile anahtar
     kaydedildikten sonra RESTART OLMADAN hesabın HEALTHY'ye geçtiğini
     izler.

Kullanım (Windows, servis çalışırken — start_alpha.cmd sonrası):

    py tools\\windows\\verify_e2e.py
    py tools\\windows\\verify_e2e.py --url http://127.0.0.1:5000
    py tools\\windows\\verify_e2e.py --watch BINANCE_GLOBAL

Yalnız standart kütüphane kullanır; ek paket gerekmez. Parola asla
komut satırına yazılmaz (getpass ile gizli sorulur), asla loglanmaz.
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

OK = "[PASS]"
BAD = "[FAIL]"
INFO = "[BILGI]"


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
    status, body = c.post("/login", {
        "csrf_token": m.group(1), "username": user, "password": pw})
    # Başarılı giriş redirect (302) veya /home içeriği döndürür
    st, chk = c.get("/api/accounts")
    if st != 200:
        raise RuntimeError("Giriş başarısız görünüyor: /api/accounts "
                           f"HTTP {st}. Kullanıcı adı/parolayı kontrol edin.")


def collect(c: Client) -> tuple[dict, dict, dict]:
    """Aynı oturumda üç ucu okur; account_id → state haritaları döner."""
    acc = c.get_json("/api/accounts")["data"]["accounts"]
    wal = c.get_json("/api/accounts/wallets")["data"]["accounts"]
    por = c.get_json("/api/accounts/portfolio")["data"]["components"]
    a_map = {x["account_id"]: x for x in acc}
    w_map = {x["account_id"]: x for x in wal}
    p_map = {x["account_id"]: x for x in por}
    return a_map, w_map, p_map


def check_consistency(a_map: dict, w_map: dict, p_map: dict) -> list[str]:
    fails: list[str] = []
    print(f"\n{INFO} Üç uç tutarlılık kontrolü "
          "(/api/accounts vs wallets vs portfolio):")
    for aid, card in a_map.items():
        name = f"{card.get('exchange', aid)} ({card.get('nickname', '')})"
        s_a = card.get("connection_state", "?")
        if not card.get("connected"):
            print(f"  - {name}: bağlı değil, atlandı (accounts={s_a})")
            # Bağlı olmayan hesap wallets/portfolio'da GÖRÜNMEMELİ
            if aid in w_map or aid in p_map:
                fails.append(f"{name}: bağlı değilken wallets/portfolio "
                             "listesinde görünüyor")
            continue
        s_w = w_map.get(aid, {}).get("connection_state", "EKSİK")
        s_p = p_map.get(aid, {}).get("connection_state", "EKSİK")
        line = f"  - {name}: accounts={s_a} wallets={s_w} portfolio={s_p}"
        if s_a == s_w == s_p:
            print(f"{line}  {OK}")
        else:
            print(f"{line}  {BAD} ZIT DURUM")
            fails.append(f"{name}: zıt durum accounts={s_a} "
                         f"wallets={s_w} portfolio={s_p}")
    return fails


def check_paper(a_map: dict, w_map: dict) -> list[str]:
    fails: list[str] = []
    print(f"\n{INFO} Paper bakiye kontrolü (çalışma dizininden bağımsız):")
    papers = [(aid, c) for aid, c in a_map.items()
              if c.get("exchange") == "PAPER"]
    if not papers:
        print("  - PAPER hesabı bulunamadı; kontrol atlandı.")
        return fails
    for aid, card in papers:
        if not card.get("connected"):
            print("  - PAPER bağlı değil; kontrol atlandı.")
            continue
        w = w_map.get(aid, {})
        val = w.get("value_usdt", "EKSİK")
        state = w.get("connection_state", "EKSİK")
        if val == "UNKNOWN" or state != "HEALTHY":
            print(f"  - Paper: state={state} value={val}  {BAD}")
            fails.append("Paper bakiyesi UNKNOWN/HEALTHY değil — servis "
                         "farklı çalışma dizininden başlatıldıysa ROOT "
                         "bağlı STATE_PATH regresyonu demektir")
        else:
            print(f"  - Paper: state={state} value={val}  {OK}")
    return fails


def watch_healthy(c: Client, exchange: str, timeout_s: int) -> list[str]:
    """Anahtar kaydı sonrası restartsız HEALTHY geçişini izler."""
    print(f"\n{INFO} {exchange} için HEALTHY geçişi izleniyor "
          f"(en fazla {timeout_s} sn).")
    print("  Şimdi tarayıcıda Hesaplarım → Düzenle ile API anahtarını "
          "kaydedin. Servisi YENİDEN BAŞLATMAYIN.")
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        a_map, _, _ = collect(c)
        card = next((x for x in a_map.values()
                     if x.get("exchange") == exchange), None)
        state = card.get("connection_state") if card else "HESAP_YOK"
        if state != last:
            print(f"  - {time.strftime('%H:%M:%S')} durum: {state}")
            last = state
        if state == "HEALTHY":
            print(f"  {OK} Restart olmadan HEALTHY'ye geçti.")
            return []
        time.sleep(3)
    return [f"{exchange}: {timeout_s} sn içinde HEALTHY'ye geçmedi "
            "(restart gerekmeden geçiş kabul kriteridir)"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:5000",
                    help="Servis adresi (vars: http://127.0.0.1:5000)")
    ap.add_argument("--watch", metavar="EXCHANGE",
                    help="Anahtar kaydı sonrası restartsız HEALTHY "
                         "geçişini izle (ör. BINANCE_GLOBAL, BINANCE_TR)")
    ap.add_argument("--watch-timeout", type=int, default=180,
                    help="--watch için saniye cinsinden bekleme (vars: 180)")
    args = ap.parse_args()

    c = Client(args.url)
    try:
        login(c)
    except RuntimeError as exc:
        print(f"{BAD} Oturum açılamadı: {exc}")
        return 2

    try:
        a_map, w_map, p_map = collect(c)
    except RuntimeError as exc:
        print(f"{BAD} Uçlar okunamadı: {exc}")
        return 2

    fails = check_consistency(a_map, w_map, p_map)
    fails += check_paper(a_map, w_map)
    if args.watch:
        fails += watch_healthy(c, args.watch.upper(), args.watch_timeout)

    print("\n" + "=" * 60)
    if fails:
        print(f"{BAD} {len(fails)} kontrol BAŞARISIZ:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"{OK} Tüm saha kontrolleri geçti: üç uç tutarlı, Paper "
          "bakiyesi biliniyor.")
    if not args.watch:
        print(f"{INFO} Restartsız HEALTHY kontrolü için: "
              "py tools\\windows\\verify_e2e.py --watch BINANCE_GLOBAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
