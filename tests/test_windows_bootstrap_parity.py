"""Windows arka plan servis bootstrap paritesi (gunicorn post_fork ↔ serve_windows).

MERGE GUARD NOTU: Operatör onaylı davranış; görev ajanları 'kapsam dışı'
diye kaldıramaz.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import serve_windows as sw  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_bootstrap_flag():
    old = sw._BOOTSTRAP_DONE
    yield
    sw._BOOTSTRAP_DONE = old


def _thread_count(name: str) -> int:
    return sum(1 for t in threading.enumerate()
               if t.name == name and t.is_alive())


def test_bootstrap_starts_and_logs_disabled_states(caplog, monkeypatch):
    """A/B) Bootstrap post_fork sırasıyla koşar; enabled=false → DISABLED
    logu, exception yok; scheduler env kapalı → DISABLED logu."""
    monkeypatch.delenv("ALPHA_AUTOMATION_ENABLED", raising=False)
    sw._BOOTSTRAP_DONE = False
    with caplog.at_level("INFO", logger="alpha.serve"):
        sw._bootstrap_background_services()
    msgs = " | ".join(r.message for r in caplog.records)
    # Universe loop: başlar veya zaten çalışıyordur — ikisi de kabul.
    assert ("AUTO LOOP STARTED" in msgs
            or "AUTO LOOP zaten çalışıyor" in msgs)
    # Config'te adaptive_system.enabled=false → controller başlatılmaz.
    cfg = json.loads((ROOT / "alpha20_v1" / "config.json")
                     .read_text(encoding="utf-8"))
    if not cfg.get("adaptive_system", {}).get("enabled", False):
        assert "CONTROLLER DISABLED (adaptive_system.enabled=false)" in msgs
    assert "AUTOMATION SCHEDULER DISABLED" in msgs


def test_bootstrap_is_idempotent(caplog):
    """C) İkinci çağrı: çift thread oluşmaz, 'zaten yapıldı' loglanır."""
    sw._BOOTSTRAP_DONE = False
    sw._bootstrap_background_services()
    before = _thread_count("auto_analysis")
    with caplog.at_level("INFO", logger="alpha.serve"):
        sw._bootstrap_background_services()  # ikinci çağrı
    assert _thread_count("auto_analysis") == before  # çift loop YOK
    assert any("zaten yapıldı" in r.message for r in caplog.records)


def test_bootstrap_does_not_mutate_config():
    """4) Config bayrakları değişmez — bootstrap yalnız okur."""
    p = ROOT / "alpha20_v1" / "config.json"
    original = p.read_text(encoding="utf-8")
    sw._BOOTSTRAP_DONE = False
    sw._bootstrap_background_services()
    assert p.read_text(encoding="utf-8") == original


def test_gunicorn_post_fork_unchanged():
    """D) Linux/Replit davranışı korunur: post_fork zinciri aynen yerinde."""
    src = (ROOT / "gunicorn.conf.py").read_text(encoding="utf-8")
    for piece in ("validate_startup_config", "enforce_paper_mode_lock",
                  "um.start_auto_loop", "ac.start_controller_loop",
                  "start_automation_scheduler"):
        assert piece in src


def test_serve_windows_bootstrap_before_serve():
    """Bootstrap, waitress.serve'den ÖNCE çağrılır (kaynak denetimi)."""
    src = (ROOT / "serve_windows.py").read_text(encoding="utf-8")
    assert src.index("_bootstrap_background_services()") < src.index(
        "serve(app, host=HOST")
