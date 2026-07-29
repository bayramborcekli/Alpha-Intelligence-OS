"""Windows /proc uyumluluğu — HTTP 500 kök neden düzeltmesi.

Kanıtlanan: find_bot_pids() Windows'ta (/proc yokken) FileNotFoundError
fırlatmaz; /api/v1/executive/summary 200 döner; Linux davranışı değişmez.

MERGE GUARD NOTU: Operatör onaylı davranış; görev ajanları 'kapsam dışı'
diye kaldıramaz.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path as _RealPath

import pytest

ROOT = _RealPath(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


class _FakeProcMissing:
    """Path('/proc') taklidi: yok sayılır (Windows davranışı)."""
    def exists(self):
        return False

    def iterdir(self):  # asla çağrılmamalı
        raise FileNotFoundError("[WinError 3] C:\\proc")


class _FakeProcOSError:
    def exists(self):
        return True

    def iterdir(self):
        raise OSError("beklenmeyen OS hatası")


def _fake_path_factory(fake_proc):
    def factory(arg, *a, **k):
        if str(arg) == "/proc":
            return fake_proc
        return _RealPath(arg, *a, **k)
    return factory


class _OsNtProxy:
    """os taklidi: yalnız name='nt'; geri kalanı gerçek os'a delege.

    os.name'i global patch'lemek pathlib'i WindowsPath'e zorlar ve Linux'ta
    NotImplementedError üretir; bu proxy yalnız app modülünün görüşünü
    değiştirir."""
    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


def test_windows_os_name_nt_returns_empty(monkeypatch):
    """A) os.name == 'nt' → tarama yok, boş liste, exception yok."""
    monkeypatch.setattr(app_module, "os", _OsNtProxy())
    assert app_module.find_bot_pids() == []
    assert app_module.bot_running() is False


def test_missing_proc_returns_empty(monkeypatch):
    """A) /proc yok (Windows) → FileNotFoundError OLUŞMAZ, boş liste."""
    monkeypatch.setattr(app_module, "Path",
                        _fake_path_factory(_FakeProcMissing()))
    assert app_module.find_bot_pids() == []


def test_unexpected_oserror_is_defended(monkeypatch, caplog):
    """Savunma katmanı: iterdir OSError verirse warning + boş liste."""
    monkeypatch.setattr(app_module, "Path",
                        _fake_path_factory(_FakeProcOSError()))
    with caplog.at_level("WARNING"):
        assert app_module.find_bot_pids() == []
    assert any("PID taraması" in r.message for r in caplog.records)


def test_linux_behavior_unchanged():
    """E) Linux/Replit: /proc taraması aynen çalışır, kendi PID'i hariç."""
    assert _RealPath("/proc").exists(), "Bu test Linux ortamında koşar"
    pids = app_module.find_bot_pids()
    assert isinstance(pids, list)
    assert os.getpid() not in pids


def test_executive_summary_returns_200_on_windows_sim(monkeypatch):
    """B) Windows simülasyonunda /api/v1/executive/summary → HTTP 200."""
    monkeypatch.setattr(app_module, "os", _OsNtProxy())
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as s:
            s["logged_in"] = True
            s["username"] = "test"
        r = c.get("/api/v1/executive/summary")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    assert "status_bar" in body  # D) servis durumları gerçek değerlerle döner
