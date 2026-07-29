# -*- coding: utf-8 -*-
"""Windows zamanlayıcı saha doğrulama aracının
(tools/windows/verify_scheduler.py) kontrol mantığı regresyonu.

Araç Windows'ta canlı servise karşı çalışır; burada aynı kontrol
fonksiyonları Flask test client'tan alınan GERÇEK /api/paper/state
çıktısıyla doğrulanır — böylece alan adları değişirse test kırılır ve
saha aracı sessizce çürümez.
"""
import importlib.util
from pathlib import Path

import pytest

import dashboard_api as dapi

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "verify_scheduler",
    ROOT / "tools" / "windows" / "verify_scheduler.py")
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)


@pytest.fixture
def client():
    import app as flask_app
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _fresh():
    dapi.invalidate_caches()
    yield
    dapi.invalidate_caches()


def _login(client):
    with client.session_transaction() as s:
        s["authenticated"] = True
        s["username"] = "test"


class _FakeClient:
    """Aracın Client arayüzünün Flask test client sarmalayıcısı."""

    def __init__(self, client):
        self._c = client

    def get_json(self, path):
        r = self._c.get(path)
        assert r.status_code == 200, path
        return r.get_json()


# ── snapshot(): gerçek endpoint alan adları ─────────────────────────

def test_snapshot_fields_exist_on_real_endpoint(client):
    """/api/paper/state aracın beklediği tüm alanları içermeli."""
    _login(client)
    s = vs.snapshot(_FakeClient(client))
    assert set(s) == {
        "overall_pipeline", "pipeline_blockers", "analysis_scheduler",
        "analysis_scheduler_detail", "scan_interval", "universe_size",
        "universe_reason_code", "universe_refresh_result"}
    det = s["analysis_scheduler_detail"]
    assert isinstance(det, dict)
    # Kanonik blok alanları (FIX ANALYSIS SCHEDULER sözleşmesi)
    for key in ("preference", "state", "interval_minutes",
                "last_run", "next_run", "last_result"):
        assert key in det, key


# ── check_snapshot(): karar matrisi ─────────────────────────────────

def _snap(pref="RUNNING", state="RUNNING", interval=5, last_run=None,
          next_run=None, pipeline="YELLOW", usize=3,
          ucode="NOT_RUN_YET", last_error=None, uresult=None):
    if uresult is None:
        uresult = ("NOT_RUN_YET" if ucode == "NOT_RUN_YET"
                   else ("FAILED" if ucode == "UNIVERSE_REFRESH_FAILED"
                         else "COMPLETED"))
    return {
        "overall_pipeline": pipeline,
        "pipeline_blockers": [],
        "analysis_scheduler": state,
        "analysis_scheduler_detail": {
            "preference": pref, "state": state,
            "interval_minutes": interval, "last_run": last_run,
            "next_run": next_run, "last_result": "PASS",
            "last_error": last_error},
        "scan_interval": interval,
        "universe_size": usize,
        "universe_reason_code": ucode,
        "universe_refresh_result": uresult,
    }


def test_running_expanded_universe_passes():
    s = _snap(last_run="2026-07-29T10:00:00+00:00",
              next_run="2026-07-29T10:05:00+00:00",
              pipeline="GREEN", usize=12, ucode=None)
    assert vs.check_snapshot(s) == []


def test_running_base_universe_with_honest_code_passes():
    s = _snap(usize=3, ucode="INSUFFICIENT_ELIGIBLE_SYMBOLS",
              last_run="2026-07-29T10:00:00+00:00",
              next_run="2026-07-29T10:05:00+00:00")
    assert vs.check_snapshot(s) == []


def test_base_universe_without_reason_code_fails():
    """3 sembol + neden kodu yok = false-GREEN, FAIL olmalı."""
    s = _snap(usize=3, ucode=None)
    fails = vs.check_snapshot(s)
    assert any("false-GREEN" in f for f in fails)


def test_expanded_universe_with_reason_code_fails():
    """Genişlemiş evrende neden kodu kalmışsa çelişkili rozet."""
    s = _snap(usize=8, ucode="NOT_RUN_YET")
    fails = vs.check_snapshot(s)
    assert any("çelişkili" in f for f in fails)


def test_startup_failed_with_green_pipeline_fails():
    s = _snap(state="STARTUP_FAILED", pipeline="GREEN")
    fails = vs.check_snapshot(s)
    assert any("false-GREEN regresyonu" in f for f in fails)


def test_startup_failed_with_non_green_pipeline_passes_scheduler():
    """Zamanlayıcı kontrolleri geçer; NOT_RUN_YET yine FAIL üretir
    (NOT_RUN_YET hiçbir durumda PASS değildir)."""
    s = _snap(state="STARTUP_FAILED", pipeline="YELLOW",
              ucode="NOT_RUN_YET")
    fails = vs.check_snapshot(s)
    assert not any("regresyonu" in f for f in fails)
    assert any("NOT_RUN_YET" in f for f in fails)


def test_not_run_yet_never_passes():
    """FIX: NOT_RUN_YET durumunda araç asla PASS vermez."""
    s = _snap(usize=3, ucode="NOT_RUN_YET",
              last_run="2026-07-29T10:00:00+00:00",
              next_run="2026-07-29T10:05:00+00:00")
    fails = vs.check_snapshot(s)
    assert any("NOT_RUN_YET" in f for f in fails)


def test_not_run_yet_with_green_pipeline_double_fails():
    s = _snap(usize=3, ucode="NOT_RUN_YET", pipeline="GREEN",
              last_run="2026-07-29T10:00:00+00:00",
              next_run="2026-07-29T10:05:00+00:00")
    fails = vs.check_snapshot(s)
    assert any("false-GREEN" in f for f in fails)


def test_refresh_failed_is_explicit_fail():
    s = _snap(usize=3, ucode="UNIVERSE_REFRESH_FAILED",
              last_run="2026-07-29T10:00:00+00:00",
              next_run="2026-07-29T10:05:00+00:00")
    fails = vs.check_snapshot(s)
    assert any("FAILED" in f for f in fails)


def test_wrong_interval_fails():
    s = _snap(interval=60, usize=5, ucode=None)
    fails = vs.check_snapshot(s)
    assert any("5 dk değil" in f for f in fails)


def test_last_run_without_next_run_fails():
    s = _snap(last_run="2026-07-29T10:00:00+00:00", next_run=None,
              usize=5, ucode=None)
    fails = vs.check_snapshot(s)
    assert any("next_run" in f for f in fails)


def test_stopped_preference_fails_with_guidance():
    s = _snap(pref="STOPPED", state="STOPPED")
    fails = vs.check_snapshot(s)
    assert any("RUNNING tercihiyle" in f for f in fails)


def test_running_pref_but_unknown_state_fails():
    s = _snap(state="STOPPED")
    fails = vs.check_snapshot(s)
    assert any("kanonik durum" in f for f in fails)


def test_real_endpoint_snapshot_is_checkable(client):
    """Gerçek endpoint çıktısı kontrol fonksiyonundan geçebilmeli
    (sonuç ortama bağlı; amaç alan uyumu, exception olmaması)."""
    _login(client)
    s = vs.snapshot(_FakeClient(client))
    fails = vs.check_snapshot(s)
    assert isinstance(fails, list)
