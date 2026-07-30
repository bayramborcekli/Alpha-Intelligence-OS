# -*- coding: utf-8 -*-
"""Windows dual-model saha doğrulama aracı (Task 128).

İki-liste/iki-model PAPER motorunu (ALPHA CORE SCALP /
ALPHA OPPORTUNITY BURST) GERÇEK Windows kurulumunda uçtan uca
doğrular. Kabul kriterleri:

  1. /api/dual-model/state içinde CORE ve OPPORTUNITY listeleri DOLU
     (CORE'da pinned BTC/ETH/SOL, OPP en az 1 sembol).
  2. Bu doğrulama koşusu sırasında en az bir Paper pozisyon AÇILMIŞ
     ve en az bir işlem KAPANMIŞ. --watch N ile N dakika izleyip
     açılış/kapanışı bekleyebilirsiniz (önerilir).
  3. Restart korunumu: --phase pre ile baseline alınır, servis yeniden
     başlatılır, --phase post ile listeler + açık pozisyonların
     (tam kimlik: sembol+model+entry) AYNEN korunduğu doğrulanır.
  4. git status temiz (runtime store git dışı olmalı).
  5. 429 kanıtı (koşullu): rate_limit_state.json varsa loglarda
     paylaşımlı geri çekilme izi aranır.

Gerçek API şeması (/api/dual-model/state → data):
  core_list       : [{"symbol": "BTCUSDT", "spread_pct": ..., ...}, ...]
  opportunity_list: [{"symbol": "DOGEUSDT", ...}, ...]
  positions       : [{"symbol":…, "model":…, "entry":…, "quantity":…,
                      "opened_at":…, "side":…, …}, ...]
  recent_trades   : [{"symbol":…, "model":…, "result":…, "net_pnl":…,
                      "closed_at":…, "exit":…, "entry":…, …}, ...]
  live_orders     : "DISABLED"  (güvenlik sözleşmesi)

Kullanım (Windows, depo kökünden, servis çalışırken):

    py tools\\windows\\verify_dual_model.py
    py tools\\windows\\verify_dual_model.py --watch 30    (30 dk izle)
    py tools\\windows\\verify_dual_model.py --phase pre   (restart ÖNCESİ)
    ...stop_alpha.cmd + start_alpha.cmd...
    py tools\\windows\\verify_dual_model.py --phase post  (restart SONRASI)

Kanıt dosyası: verify_dual_model_report.json (parola içermez).
Yalnız standart kütüphane. Parola getpass ile gizli sorulur, loglanmaz.
Çıkış: 0=PASS, 1=FAIL, 2=erişim/oturum hatası.
"""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OK = "[PASS]"
BAD = "[FAIL]"
INFO = "[BILGI]"

REPO = Path(__file__).resolve().parents[2]
PRE_SNAPSHOT = REPO / "verify_dual_model_pre.json"
RATE_STATE = REPO / "alpha20_v1" / "rate_limit_state.json"
LOG_CANDIDATES = (REPO / "alpha20.log", REPO / "alpha20_v1" / "alpha20.log")
BACKOFF_MARKERS = ("geri çekilme", "geri cekilme", "RateLimited", "429", "418")
PINNED = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


# ── Yardımcılar ────────────────────────────────────────────────────

def _list_symbols(lst: list) -> list[str]:
    """core_list / opportunity_list: her eleman ya str ya dict{"symbol":…}."""
    out = []
    for item in lst:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            sym = item.get("symbol") or item.get("s") or ""
            if sym:
                out.append(str(sym))
    return out


def _pos_list(s: dict) -> list[dict]:
    """positions ya liste ya dict-of-dicts olabilir; her zaman liste döner."""
    raw = s.get("positions") or {}
    if isinstance(raw, dict):
        return list(raw.values())
    if isinstance(raw, list):
        return raw
    return []


def _pos_identity(p: dict) -> tuple:
    """Tam pozisyon kimliği: (sembol, model, entry fiyatı, açılış zamanı).
    entry restart sonrası değişmez (kaydedilmiş); opened_at de değişmez."""
    return (
        p.get("symbol", ""),
        p.get("model", ""),
        p.get("entry", 0.0),
        p.get("opened_at", ""),
    )


def _closed_count(s: dict) -> int:
    metrics = s.get("metrics") or {}
    return (
        (metrics.get("ALPHA_CORE_SCALP") or {}).get("closed_positions", 0)
        + (metrics.get("ALPHA_OPPORTUNITY_BURST") or {}).get(
            "closed_positions", 0)
    )


def summarize(s: dict) -> dict:
    """Kanıt/karşılaştırma için deterministik özet — API şemasına uygun."""
    core_syms = sorted(_list_symbols(s.get("core_list", [])))
    opp_syms = sorted(_list_symbols(s.get("opportunity_list", [])))
    pos_list = _pos_list(s)
    # Tam kimlik tuple'ları (symbol, model, entry, opened_at)
    open_ids = sorted(_pos_identity(p) for p in pos_list)
    return {
        "core_list": core_syms,
        "opportunity_list": opp_syms,
        "open_position_ids": open_ids,   # tam kimlik — kısmi karşılaştırma yok
        "counters": s.get("counters", {}),
        "closed_trades": _closed_count(s),
        "last_refresh": s.get("last_refresh"),
        "last_error": s.get("last_error"),
    }


# ── HTTP istemcisi ─────────────────────────────────────────────────

class Client:
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
        if "/home" in body or "Trading" in body:
            return
        raise RuntimeError("Login sayfasında CSRF token bulunamadı.")
    user = input("Kullanıcı adı: ").strip()
    pw = getpass.getpass("Parola (gizli): ")
    c.post("/login", {
        "csrf_token": m.group(1), "username": user, "password": pw})
    st, _ = c.get("/api/dual-model/state")
    if st != 200:
        raise RuntimeError("Giriş başarısız görünüyor: /api/dual-model/"
                           f"state HTTP {st}. Bilgileri kontrol edin.")


def get_state(c: Client) -> dict:
    body = c.get_json("/api/dual-model/state")
    if not body.get("ok"):
        raise RuntimeError(f"/api/dual-model/state ok=False: {body}")
    return body["data"]


# ── Kontroller ─────────────────────────────────────────────────────

def check_lists(s: dict) -> list[str]:
    fails: list[str] = []
    core = _list_symbols(s.get("core_list", []))
    opp = _list_symbols(s.get("opportunity_list", []))
    print(f"\n{INFO} Liste kontrolü:")
    print(f"  - CORE ({len(core)}): {', '.join(core[:12]) or '-'}")
    print(f"  - OPPORTUNITY ({len(opp)}): "
          f"{', '.join(opp[:12]) or '-'}")
    print(f"  - last_refresh={s.get('last_refresh')} "
          f"last_error={s.get('last_error')}")
    if s.get("live_orders") != "DISABLED":
        fails.append(f"live_orders={s.get('live_orders')!r} — "
                     "DISABLED bekleniyordu (güvenlik sözleşmesi)")
    if len(core) < 3:
        fails.append(f"CORE listesi dolu değil ({len(core)} sembol; "
                     "en az pinned BTC/ETH/SOL beklenir)")
    else:
        for pin in PINNED:
            if pin not in core:
                fails.append(f"Pinned {pin} CORE listesinde yok")
    if not opp:
        fails.append("OPPORTUNITY listesi BOŞ — geniş havuz taraması "
                     "çalışmıyor (last_error'a bakın)")
    return fails


def check_trades_scoped(s: dict, baseline_closed: int) -> list[str]:
    """Koşu-kapsamlı açılış/kapanış: baseline'dan bu yana yeni işlem var mı?

    baseline_closed: bu doğrulama oturumu başladığında kaydedilen
    kapanmış işlem sayısı. Bu sayıdan fazla işlem varsa kapanış döngüsü
    BU KOŞU sırasında gerçekleşti demektir (geçmişten ödünç alınmaz).
    """
    fails: list[str] = []
    counters = s.get("counters") or {}
    trades = s.get("recent_trades") or []
    total_open = counters.get("total_open", 0)
    now_closed = _closed_count(s)

    print(f"\n{INFO} Açılış/kapanış kontrolü (koşu-kapsamlı):")
    print(f"  - açık pozisyon şu an: {total_open} "
          f"(CORE={counters.get('core_open')} "
          f"OPP={counters.get('opportunity_open')})")
    print(f"  - kapanan işlem: baseline={baseline_closed} "
          f"şu an={now_closed} (fark={now_closed - baseline_closed})")
    for t in trades[:5]:
        print(f"    * [{t.get('model')}] {t.get('symbol')} "
              f"net={t.get('net_pnl')} result={t.get('result')} "
              f"kapanış={t.get('closed_at')}")
    rej = s.get("recent_rejections") or []
    if rej:
        reasons: dict[str, int] = {}
        for r in rej:
            code = r.get("reason_code", "?")
            reasons[code] = reasons.get(code, 0) + 1
        print(f"  - son ret nedenleri: {reasons}")

    # Bu koşu sırasında en az bir açılış görmeli (toplam > 0 ya da yeni kapanış)
    opened_this_run = total_open > 0 or (now_closed > baseline_closed)
    closed_this_run = now_closed > baseline_closed

    if not opened_this_run:
        fails.append(
            "Bu doğrulama koşusunda hiç Paper pozisyon açılmadı "
            f"(açık={total_open}, baseline_closed={baseline_closed}, "
            f"now_closed={now_closed}) — --watch N ile izleyin ya da "
            "ret nedenlerine bakın (recent_rejections)")
    if not closed_this_run:
        fails.append(
            "Bu doğrulama koşusunda hiç işlem KAPANMADI "
            f"(baseline={baseline_closed}, şu an={now_closed}) — "
            "kabul kriteri bu koşu içinde tam bir döngü görmektir; "
            "--watch N ile izleyin")
    return fails


def check_git_clean() -> list[str]:
    print(f"\n{INFO} git status kontrolü (runtime store git dışı mı):")
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(REPO),
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as exc:
        print(f"  - git çalıştırılamadı: {exc}  {BAD}")
        return [f"git status alınamadı: {exc}"]
    dirty = [ln for ln in out.splitlines()
             if "verify_dual_model" not in ln
             and "verify_scheduler_report" not in ln]
    if dirty:
        print(f"  - kirli dosyalar:  {BAD}")
        for ln in dirty[:10]:
            print(f"    {ln}")
        return ["git status temiz değil — runtime durumu git içine "
                f"sızıyor olabilir: {dirty[:5]}"]
    print(f"  - çalışma ağacı temiz  {OK}")
    return []


def check_backoff_evidence() -> list[str]:
    print(f"\n{INFO} Paylaşımlı 429/418 geri çekilme kanıtı (koşullu):")
    if not RATE_STATE.exists():
        print("  - rate_limit_state.json yok → bu koşuda hiç 429/418 "
              "yaşanmamış; kontrol UYGULANAMAZ (sorun değil).")
        return []
    try:
        st = json.loads(RATE_STATE.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"rate_limit_state.json okunamadı: {exc}"]
    print(f"  - paylaşımlı durum dosyası: {st}")
    hits: list[str] = []
    for logp in LOG_CANDIDATES:
        if not logp.exists():
            continue
        try:
            tail = logp.read_text(encoding="utf-8",
                                  errors="replace")[-200_000:]
        except Exception:
            continue
        for ln in tail.splitlines():
            if any(marker in ln for marker in BACKOFF_MARKERS):
                hits.append(f"{logp.name}: {ln.strip()[:160]}")
    if hits:
        print(f"  - loglarda geri çekilme izi bulundu  {OK}")
        for h in hits[-5:]:
            print(f"    {h}")
        return []
    return ["429/418 kaydı var (rate_limit_state.json) ama loglarda "
            "geri çekilme izi bulunamadı — dual-model paylaşımlı "
            "korumaya uymuyor olabilir"]


# ── İzleme (koşu-kapsamlı) ────────────────────────────────────────

def watch(c: Client, minutes: int, baseline_closed: int) -> dict:
    """Açılış + kapanış döngüsünü bekler; baseline'dan beri yeni olaylar."""
    print(f"\n{INFO} {minutes} dk izleme (baseline closed={baseline_closed}): "
          "bu koşu içinde en az bir açılış ve bir kapanış bekleniyor...")
    deadline = time.time() + minutes * 60
    s = get_state(c)
    while time.time() < deadline:
        cnt = s.get("counters") or {}
        now_closed = _closed_count(s)
        open_n = cnt.get("total_open", 0)
        print(f"  - {time.strftime('%H:%M:%S')} açık={open_n} "
              f"kapanan={now_closed} (baseline={baseline_closed}) "
              f"core={cnt.get('core_universe')} "
              f"opp={cnt.get('opportunity_universe')}")
        opened = open_n > 0 or now_closed > baseline_closed
        closed_new = now_closed > baseline_closed
        if opened and closed_new:
            print(f"  {OK} Bu koşu içinde açılış+kapanış döngüsü gözlendi.")
            break
        time.sleep(60)
        s = get_state(c)
    return s


# ── Restart korunum ────────────────────────────────────────────────

def phase_pre(s: dict) -> list[str]:
    PRE_SNAPSHOT.write_text(json.dumps(
        {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
         "summary": summarize(s)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n{INFO} Restart ÖNCESİ snapshot yazıldı: {PRE_SNAPSHOT.name}")
    print("  Şimdi servisi yeniden başlatın (stop_alpha.cmd + "
          "start_alpha.cmd), sonra:\n"
          "  py tools\\windows\\verify_dual_model.py --phase post")
    return []


def phase_post(s: dict) -> list[str]:
    """Restart sonrası korunum — tam kimlik (symbol+model+entry+opened_at)."""
    fails: list[str] = []
    print(f"\n{INFO} Restart SONRASI korunum kontrolü:")
    if not PRE_SNAPSHOT.exists():
        return [f"Önce --phase pre çalıştırılmalı ({PRE_SNAPSHOT.name} yok)"]
    pre = json.loads(PRE_SNAPSHOT.read_text(encoding="utf-8"))["summary"]
    now_sum = summarize(s)

    # Listeler restart ile kaybolmamalı (arka plan yenilemesi değiştirebilir;
    # kural: 'boşalmadı' ve 'pinned korunuyor')
    for key in ("core_list", "opportunity_list"):
        if pre[key] and not now_sum[key]:
            fails.append(f"{key} restart sonrası BOŞALDI "
                         f"(önce {len(pre[key])} sembol vardı)")
        else:
            print(f"  - {key}: önce {len(pre[key])} → sonra "
                  f"{len(now_sum[key])} sembol  {OK}")

    # TAM kimlik seti karşılaştırması (symbol + model + entry + opened_at)
    pre_ids = {tuple(p) for p in pre["open_position_ids"]}
    now_ids = {tuple(p) for p in now_sum["open_position_ids"]}
    lost_ids = pre_ids - now_ids
    # Kapanan → trades'e düştü, bu kayıp değil; doğrula
    closed_syms = {t.get("symbol") for t in (s.get("recent_trades") or [])}
    truly_lost = {ident for ident in lost_ids if ident[0] not in closed_syms}
    if truly_lost:
        fails.append(
            "Restart sonrası pozisyon kimliği KAYBOLDU "
            "(ne açık ne kapanmış trades'de — runtime sıfırlanmış olabilir): "
            + ", ".join(f"{i[0]}@{i[2]}" for i in sorted(truly_lost)))
    else:
        # Yeniden açılmış ama entry değişmiş (piyasa hareketi yok, hata var)
        missing_not_closed = {i[0] for i in lost_ids} - closed_syms
        reopened = [(i, nid) for i in lost_ids for nid in now_ids
                    if nid[0] == i[0] and nid[3] != i[3]]
        if reopened:
            for old, new in reopened[:3]:
                fails.append(
                    f"Pozisyon {old[0]} restart sonrası farklı kimlikle "
                    f"yeniden açıldı: entry {old[2]}→{new[2]} "
                    f"opened_at {old[3]!r}→{new[3]!r} — "
                    "beklenen: aynı entry/opened_at (runtime korunum)"
                )
        else:
            print(f"  - açık pozisyonlar: önce {sorted(pre_ids) or '-'} → "
                  f"sonra {sorted(now_ids) or '-'} "
                  f"(kapananlar: {sorted(lost_ids & (pre_ids - now_ids)) or '-'})  {OK}")

    # trades sayısı azaldıysa runtime sıfırlanmış
    if now_sum["closed_trades"] < pre["closed_trades"]:
        fails.append(
            f"Kapalı işlem sayısı restart sonrası düştü: "
            f"{pre['closed_trades']} → {now_sum['closed_trades']} — "
            "runtime store sıfırlanmış veya üzerine yazılmış")
    else:
        print(f"  - kapanan işlem sayısı: önce {pre['closed_trades']} → "
              f"sonra {now_sum['closed_trades']}  {OK}")
    return fails


# ── Ana akış ───────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:5000",
                    help="Servis adresi (vars: http://127.0.0.1:5000)")
    ap.add_argument("--phase", choices=("pre", "post"),
                    help="Restart korunum testi: pre=snapshot al, "
                         "post=restart sonrası karşılaştır")
    ap.add_argument("--watch", type=int, metavar="DAKIKA", default=0,
                    help="Açılış/kapanış döngüsünü bu kadar dakika izle")
    ap.add_argument("--report", default="verify_dual_model_report.json",
                    help="Kanıt JSON dosyası yolu")
    args = ap.parse_args()

    c = Client(args.url)
    try:
        login(c)
        s = get_state(c)
    except RuntimeError as exc:
        print(f"{BAD} {exc}")
        return 2

    # Koşu başlangıcı baseline — tarihi işlemler PASS'a sayılmaz
    baseline_closed = _closed_count(s)
    print(f"{INFO} Başlangıç baseline: closed_trades={baseline_closed}")

    if args.watch > 0:
        s = watch(c, args.watch, baseline_closed)

    fails = check_lists(s)
    if args.phase == "pre":
        fails += phase_pre(s)
    else:
        fails += check_trades_scoped(s, baseline_closed)
        fails += check_git_clean()
        fails += check_backoff_evidence()
        if args.phase == "post":
            fails += phase_post(s)

    report = {
        "tool": "verify_dual_model",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "url": args.url, "phase": args.phase,
        "baseline_closed": baseline_closed,
        "summary": summarize(s),
        "failures": fails,
        "result": "PASS" if not fails else "FAIL",
    }
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n{INFO} Kanıt dosyası yazıldı: {args.report}")

    if fails:
        print(f"\n{BAD} {len(fails)} kontrol başarısız:")
        for x in fails:
            print(f"  * {x}")
        return 1
    print(f"\n{OK} Dual-model saha kontrolleri geçti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
