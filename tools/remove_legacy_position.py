#!/usr/bin/env python3
"""Tek seferlik bakım komutu: legacy state.json pozisyon kaydını güvenle sil.

Hedef: alpha20_v1/state.json içindeki state["position"] alanı.
Yalnız --symbol ile verilen sembol (tam eşleşme) hedeflenir ve tüm ön
kontroller geçerse SADECE state["position"] = null yapılır; başka hiçbir
alan değiştirilmez.

Fail-closed: herhangi bir ön kontrol başarısızsa HİÇBİR ŞEY yazılmaz ve
komut sıfırdan farklı çıkış koduyla, açık bir hata mesajıyla biter.

Kullanım (Windows / Linux aynı):
    python tools/remove_legacy_position.py --symbol ONDOUSDT --confirm ONDOUSDT

Başarı çıktısı:  CLEANUP_SUCCESS (+ doğrulama satırları)
İkinci çalıştırma: ALREADY_CLEAN
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # portable_flock (Windows flock uyarlaması)

if os.name == "nt":
    import portable_flock as fcntl  # type: ignore
else:
    import fcntl  # type: ignore

ALPHA_DIR = REPO_ROOT / "alpha20_v1"
STATE_PATH = ALPHA_DIR / "state.json"
STATE_LOCK_PATH = ALPHA_DIR / "state.json.lock"
RUNTIME_PATH = ALPHA_DIR / "dual_model_runtime.json"
RUNTIME_LOCK_PATH = ALPHA_DIR / "dual_model_runtime.lock"
AUDIT_PATH = ALPHA_DIR / "position_integrity_audit.jsonl"


def fail(msg: str) -> "None":
    print(f"FAIL_CLOSED: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_json(path: Path, what: str) -> object:
    """Dosyayı oku; okunamazsa fail-closed."""
    if not path.exists():
        fail(f"{what} bulunamadı: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"{what} okunamadı/bozuk: {path} ({e})")


def process_check() -> None:
    """Uygulama/controller/bot süreci açık mı? Açıksa fail-closed.

    Komut satırında bilinen giriş noktalarını arar. Tarama hiç
    yapılamazsa da fail-closed (belirsizlik = durdur).
    """
    markers = ("serve_windows", "gunicorn", "app:app", "auto_controller",
               "alpha20.py", "alpha_platform")
    me = str(os.getpid())
    lines: list[str] = []
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | "
                 "Select-Object ProcessId,CommandLine | "
                 "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=30)
        else:
            out = subprocess.run(["ps", "-eo", "pid,args"],
                                 capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            fail("süreç taraması başarısız (rc!=0); bot kapalı mı bilinemiyor")
        lines = out.stdout.splitlines()
    except Exception as e:  # tarama yapılamadı → belirsiz → durdur
        fail(f"süreç taraması yapılamadı: {e}")
    for ln in lines:
        parts = ln.strip().split(None, 1)
        if len(parts) < 2 or parts[0] == me:
            continue
        cmd = parts[1]
        if "remove_legacy_position" in cmd:
            continue
        if any(m in cmd for m in markers):
            fail(f"uygulama/bot süreci hâlâ çalışıyor: {ln.strip()[:160]} "
                 "— önce uygulamayı kapatın")


def write_audit(symbol: str) -> dict:
    rec = {
        "event": "POSITION_RECORD_CLEANED",
        "symbol": symbol,
        "source": "legacy",
        "method": "maintenance_cli",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return rec


def atomic_write_state(state: dict) -> None:
    tmp = STATE_PATH.with_name(f".{STATE_PATH.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(STATE_PATH)


def active_list_symbols(runtime: dict, state: dict) -> set[str]:
    """Aktif işlemler listesi = dual runtime açık pozisyonları + legacy pozisyon."""
    syms: set[str] = set()
    pos = runtime.get("positions")
    if isinstance(pos, dict):
        for p in pos.values():
            if isinstance(p, dict) and p.get("symbol"):
                syms.add(str(p["symbol"]))
    legacy = state.get("position")
    if isinstance(legacy, dict) and legacy.get("symbol"):
        syms.add(str(legacy["symbol"]))
    return syms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--confirm", required=True,
                    help="Güvenlik için sembolün aynen tekrar yazılması gerekir")
    args = ap.parse_args()
    symbol = args.symbol.strip()
    if not symbol or args.confirm.strip() != symbol:
        fail("--confirm değeri --symbol ile birebir aynı olmalı")

    # 0) Uygulama/bot kapalı mı?
    process_check()

    # 1) Dosyalar okunabilir mi? (fail-closed)
    state = load_json(STATE_PATH, "state.json")
    runtime = load_json(RUNTIME_PATH, "dual_model_runtime.json")
    if not isinstance(state, dict) or not isinstance(runtime, dict):
        fail("state.json / dual_model_runtime.json beklenen sözlük yapısında değil")

    # 2) Dual runtime içinde açık pozisyon var mı?
    open_pos = runtime.get("positions")
    open_pos = open_pos if isinstance(open_pos, dict) else {}
    for key, p in open_pos.items():
        if isinstance(p, dict) and str(p.get("symbol", "")) == symbol:
            fail(f"dual runtime içinde AÇIK {symbol} pozisyonu var ({key}) "
                 "— bu legacy temizlik komutu onu SİLMEZ")

    # 3) Ledger (dual runtime kapalı işlem defteri) okunabilir ve
    #    kapanmamış kayıt içermiyor mu?
    trades = runtime.get("trades")
    trades = trades if isinstance(trades, list) else []
    for t in trades:
        if not isinstance(t, dict) or str(t.get("symbol", "")) != symbol:
            continue
        if t.get("exit") in (None, "", 0) and t.get("exit_ts") in (None, ""):
            fail(f"ledger'da kapanmamış görünen {symbol} işlemi var: "
                 f"{json.dumps(t, ensure_ascii=False)[:200]}")

    # 4) Legacy pozisyon durumu
    position = state.get("position")
    if position is None:
        # İdempotent ikinci çalıştırma
        if symbol in active_list_symbols(runtime, state):
            fail(f"state.position null ama {symbol} hâlâ aktif listede "
                 "(dual runtime) — bu komutun kapsamı dışında")
        print("ALREADY_CLEAN")
        print(f"symbol={symbol}")
        print("state_position=null")
        print("active_list_contains_symbol=false")
        return 0
    if not isinstance(position, dict):
        fail(f"state.position beklenmedik tipte: {type(position).__name__}")
    if str(position.get("symbol", "")) != symbol:
        fail(f"state.position.symbol tam olarak {symbol} değil: "
             f"{position.get('symbol')!r} — hiçbir şey değiştirilmedi")

    # 5) Kilit altında, atomik yazımla YALNIZ position alanını null yap
    with STATE_LOCK_PATH.open("a+") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            state = load_json(STATE_PATH, "state.json (kilit altında yeniden)")
            position = state.get("position")
            if position is None:
                print("ALREADY_CLEAN")
                print(f"symbol={symbol}")
                print("state_position=null")
                print("active_list_contains_symbol="
                      f"{str(symbol in active_list_symbols(runtime, state)).lower()}")
                return 0
            if not isinstance(position, dict) or \
                    str(position.get("symbol", "")) != symbol:
                fail("kilit altında yeniden okunan state.position artık "
                     f"{symbol} değil — hiçbir şey değiştirilmedi")
            state["position"] = None
            atomic_write_state(state)
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)

    # 6) Audit
    write_audit(symbol)

    # 7) Otomatik son doğrulama (dosyadan yeniden okunarak)
    state2 = load_json(STATE_PATH, "state.json (doğrulama)")
    runtime2 = load_json(RUNTIME_PATH, "dual_model_runtime.json (doğrulama)")
    if state2.get("position") is not None:
        fail("doğrulama: state.position hâlâ null değil")
    contains = symbol in active_list_symbols(runtime2, state2)
    audit_ok = False
    try:
        last = AUDIT_PATH.read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(last)
        audit_ok = (rec.get("event") == "POSITION_RECORD_CLEANED"
                    and rec.get("symbol") == symbol)
    except (OSError, json.JSONDecodeError, IndexError):
        audit_ok = False
    if contains or not audit_ok:
        fail(f"doğrulama başarısız: active_list_contains_symbol={contains}, "
             f"audit_written={audit_ok}")

    print("CLEANUP_SUCCESS")
    print(f"symbol={symbol}")
    print("state_position=null")
    print("active_list_contains_symbol=false")
    print("audit_written=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
