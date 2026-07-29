"""Windows Runtime Recovery Agent servisi — tek kaynak.

CLI betikleri (SETUP_AND_START_WINDOWS.cmd → windows_setup_flow.py),
dashboard ve agent registry aynı fonksiyonları kullanır. Ağır kurtarma
adımları (git/venv/pip) PowerShell hazırlığında kalır; bu servis Python
tarafındaki kanıtlanmış akışı (env onarımı, SSL testleri, süreç yönetimi,
sunucu başlatma, health doğrulama) yeniden kullanılabilir kılar.

Import sırasında AĞIR İŞLEM YAPILMAZ; her şey açık çağrıyla çalışır.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = ROOT / "data" / "windows_runtime_agent.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_windows_local() -> bool:
    return os.name == "nt"


def record_report(report: dict, health: dict) -> None:
    """Kurtarma akışının son sonucunu agent snapshot'ına yazar.

    windows_setup_flow.final_report() her koşuda çağırır; dashboard ve
    registry bu snapshot'ı okur. Secret içermez."""
    import windows_setup_flow as wsf
    snap = {
        "last_run": _now(),
        "git": report.get("GIT", "PASS"),
        "python_env": report.get("PYENV", "PASS"),
        "env": report.get("ENV"),
        "truststore": report.get("TRUSTSTORE"),
        "binance_public": report.get("BINANCE PUBLIC"),
        "symbols": {k[8:]: report.get(k) for k in
                    ("BINANCE BTC", "BINANCE ETH", "BINANCE SOL")},
        "server": "RUNNING" if health else "STOPPED",
        "controller": str(health.get("controller", "stopped")).upper(),
        "paper": str(health.get("paper", "disabled")).upper(),
        "cycle_count": int(health.get("cycle_count") or 0),
        "git_head": health.get("git_head"),
        "runtime_card": ("green" if wsf.health_ok(health)
                         else "yellow" if health else "red"),
        "root_cause": report.get("ROOT CAUSE"),
        "last_result": "PASS" if wsf.health_ok(health) else "FAIL",
        "last_error": report.get("ROOT CAUSE") or None,
    }
    try:
        SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(snap, ensure_ascii=False,
                                            indent=2), encoding="utf-8")
    except OSError:
        pass


def load_snapshot() -> dict:
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def live_health() -> dict:
    """Çalışan sunucudan /health/runtime (varsa) — salt okunur."""
    try:
        import requests
        port = os.environ.get("ALPHA_PORT", "5000").strip() or "5000"
        r = requests.get(f"http://127.0.0.1:{port}/health/runtime",
                         timeout=3)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def status() -> dict:
    """Dashboard/registry için birleşik durum (son koşu + canlı health)."""
    import windows_setup_flow as wsf
    snap = load_snapshot()
    health = live_health()
    if health:
        snap.update({
            "server": "RUNNING",
            "controller": str(health.get("controller", "stopped")).upper(),
            "paper": str(health.get("paper", "disabled")).upper(),
            "cycle_count": int(health.get("cycle_count") or 0),
            "git_head": health.get("git_head") or snap.get("git_head"),
            "runtime_card": "green" if wsf.health_ok(health) else "yellow",
        })
    elif snap:
        snap.setdefault("server", "STOPPED")
    snap["live_orders"] = "DISABLED"
    # Kalıcı yapılandırma kaynakları (Mission: tek kaynak prensibi) —
    # panel bunları "nerede saklanıyor" alanında gösterir; secret'sız.
    snap["config_sources"] = {
        "runtime_settings": ".env",
        "credentials": "windows_dpapi",
        "code": "github_main",
    }
    return snap


def run_recovery() -> dict:
    """Tam kurtarma akışını çalıştırır — YALNIZ yerel Windows.

    Kanıtlanmış windows_setup_flow adımlarını aynen kullanır (tek kaynak).
    Replit/public ortamda çağrılamaz (güvenlik: uzaktan Windows makine
    işlemi yok)."""
    if not is_windows_local():
        raise RuntimeError("Windows Runtime Recovery yalnız yerel Windows "
                           "makinede çalıştırılabilir.")
    import windows_setup_flow as wsf
    wsf.report.clear()
    wsf.repair_env()
    proceed = wsf.ssl_and_binance()
    health: dict = {}
    if proceed:
        wsf.stop_old_processes()
        wsf.start_server()
        health = wsf.wait_health(180)
    record_report(dict(wsf.report), health)
    return status()
