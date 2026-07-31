# -*- coding: utf-8 -*-
"""GÖREV 116 — Kanonik paylaşımlı scheduler snapshot regresyon testleri.

Hata: gunicorn çoklu worker'da scheduler durumu process-local
auto_controller._last_status'tan okunuyordu; istek döngü sahibi olmayan
worker'a düşünce sahte STARTUP_FAILED / 'Veri yok' / YELLOW üretiliyordu.

Düzeltme: sahip worker durumu paylaşımlı controller_status_runtime.json
snapshot'ına yazar; scheduler_status() yerel bellek running=False iken
YALNIZ sahibi canlı (owner_alive) snapshot'a düşer. Sahip ölmüşse
fallback yok — gerçek arıza görünür kalır (fail-closed).
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# legacy-root-alpha20 gölgesine karşı: alpha20_v1 öne eklenir
sys.path.insert(0, str(ROOT / "alpha20_v1"))
sys.path.insert(0, str(ROOT))

import auto_controller as ac  # noqa: E402
from services import system_runtime_orchestrator as sro  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture()
def shared_snapshot(tmp_path, monkeypatch):
    """SHARED_STATUS_PATH'i geçici dosyaya yönlendir ve yazıcı döndür."""
    p = tmp_path / "controller_status_runtime.json"
    monkeypatch.setattr(ac, "SHARED_STATUS_PATH", p, raising=False)

    def write(running=True, pid=None, last_cycle_time=None,
              last_cycle_error=None, **extra):
        snap = dict(ac._last_status)
        snap.update({
            "running": running,
            "pid": pid if pid is not None else os.getpid(),
            "updated_at": _now(),
            "last_cycle_time": last_cycle_time or _now(),
            "next_cycle_time": _now(),
            "last_cycle_error": last_cycle_error,
            "cycle_count": 7,
        })
        snap.update(extra)
        p.write_text(json.dumps(snap), encoding="utf-8")
        return snap

    return write


@pytest.fixture()
def local_not_owner(monkeypatch):
    """Bu süreç döngü sahibi DEĞİL: yerel bellek running=False."""
    monkeypatch.setattr(
        ac, "get_status",
        lambda: {"running": False, "last_cycle_time": None,
                 "last_cycle_error": None, "cycle_count": 0})


# ── Senaryo 1: process-local yanlış durum → kanonik snapshot kazanır ──
def test_worker_local_false_startup_failed_fixed(
        shared_snapshot, local_not_owner):
    t = _now()
    shared_snapshot(running=True, last_cycle_time=t)
    s = sro.scheduler_status(None, "RUNNING")
    assert s["state"] == "RUNNING", (
        "Sahibi canlı paylaşımlı snapshot RUNNING iken sahte "
        "STARTUP_FAILED döndü (process-local kaynak kanonik sanıldı)")
    assert s["running"] is True
    assert s["last_run"] == t  # 'Veri yok' imkânsız


# ── Senaryo 2: iki worker aynı kanonik sonucu döndürür ──
def test_two_workers_consistent(shared_snapshot, local_not_owner):
    t = _now()
    shared_snapshot(running=True, last_cycle_time=t)
    # Worker A: döngü sahibi (yerel running=True ile çağrılır)
    a = sro.scheduler_status(
        {"running": True, "last_cycle_time": t,
         "last_cycle_error": None, "cycle_count": 7}, "RUNNING")
    # Worker B: sahibi değil (yerel False → snapshot fallback)
    b = sro.scheduler_status(None, "RUNNING")
    assert a["state"] == b["state"] == "RUNNING"
    assert a["last_run"] == b["last_run"] == t


# ── Senaryo 3: gerçek arıza saklanmaz (sahip ölü → fallback yok) ──
def test_dead_owner_shows_real_failure(shared_snapshot, local_not_owner):
    shared_snapshot(running=True, pid=99999999)  # ölü PID
    s = sro.scheduler_status(None, "RUNNING")
    assert s["state"] == "STARTUP_FAILED", (
        "Sahibi ölmüş snapshot RUNNING kabul edildi — gerçek arıza "
        "maskelendi")


def test_stopped_snapshot_not_running(shared_snapshot, local_not_owner):
    shared_snapshot(running=False)
    s = sro.scheduler_status(None, "STOPPED")
    assert s["state"] == "STOPPED"
    s2 = sro.scheduler_status(None, "RUNNING")
    assert s2["state"] == "STARTUP_FAILED"  # dürüst görünürlük


# ── Senaryo 4: kanonik last analysis varken 'Veri yok' imkânsız ──
def test_last_analysis_consistency(shared_snapshot, local_not_owner):
    t = _now()
    shared_snapshot(running=True, last_cycle_time=t)
    s = sro.scheduler_status(None, "RUNNING")
    assert s["last_run"] is not None
    assert s["next_run"] is not None


# ── Senaryo 5: private-auth ayrımı — readiness blocker'larında auth yok ──
def test_private_auth_not_a_pipeline_blocker():
    import inspect
    src = inspect.getsource(sro.readiness)
    assert "AUTH" not in src.upper().replace("AUTHOR", ""), (
        "readiness() private-auth'u pipeline blocker'ı yapamaz")


# ── Senaryo 6: tarihsel hata güncel durum gibi gösterilmez ──
def test_historical_error_separate_field(shared_snapshot,
                                         local_not_owner):
    t = _now()
    shared_snapshot(running=True, last_cycle_time=t,
                    last_cycle_error="eski çevrim hatası")
    s = sro.scheduler_status(None, "RUNNING")
    assert s["state"] == "RUNNING"          # güncel durum
    assert s["last_error"] == "eski çevrim hatası"  # ayrı alan


# ── Snapshot yazımı: atomik, pid/updated_at içerir; owner_alive doğru ──
def test_persist_and_read_shared_status(tmp_path, monkeypatch):
    p = tmp_path / "controller_status_runtime.json"
    monkeypatch.setattr(ac, "SHARED_STATUS_PATH", p, raising=False)
    ac._persist_shared_status()
    snap = ac.get_shared_status()
    assert snap["pid"] == os.getpid()
    assert snap["owner_alive"] is True
    assert snap["updated_at"]
    # bozuk dosya → boş sözlük (istisna sızmaz)
    p.write_text("{bozuk", encoding="utf-8")
    assert ac.get_shared_status() == {}


# ── Kod incelemesi bulguları: gerçek endpoint yolu + tazelik kapısı ──
def test_readiness_none_uses_canonical_fallback(
        shared_snapshot, local_not_owner):
    """readiness(None, ...) — gerçek /api/paper/state yolu artık None
    geçer; non-owner worker'da bile kanonik RUNNING dönmeli."""
    t = _now()
    shared_snapshot(running=True, last_cycle_time=t)
    r = sro.readiness(None, "RUNNING", False)
    assert r["analysis_scheduler"] == "RUNNING"
    assert r["last_complete_analysis"] == t
    assert "SCHEDULER_STARTUP_FAILED" not in r["blockers"]


def test_stale_snapshot_rejected(shared_snapshot, local_not_owner):
    """Sahip PID canlı ama snapshot bayat (takılı döngü / PID geri
    dönüşümü) → fallback reddedilir, dürüst STARTUP_FAILED."""
    from datetime import timedelta
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    shared_snapshot(running=True, last_cycle_time=old, updated_at=old)
    s = sro.scheduler_status(None, "RUNNING")
    assert s["state"] == "STARTUP_FAILED", (
        "Bayat snapshot RUNNING kabul edildi — takılı döngü maskelendi")


def test_explicit_controller_status_unchanged(shared_snapshot):
    """Testlerin açık controller_status geçişi fallback'e uğramaz
    (test_master_integration sözleşmesi korunur)."""
    shared_snapshot(running=True)
    s = sro.scheduler_status({"running": False}, "RUNNING")
    assert s["state"] == "STARTUP_FAILED"
