"""Windows PAPER test modu — güvenli bayrak etkinleştirme.

Yalnız Windows local development'ta adaptive_system bayrakları açılır;
Linux/Replit, production ve canlı emir yolu değişmez.

MERGE GUARD NOTU: Operatör onaylı davranış; görev ajanları 'kapsam dışı'
diye kaldıramaz.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import serve_windows as sw  # noqa: E402


class _OsNtProxy:
    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    src = json.loads((ROOT / "alpha20_v1" / "config.json")
                     .read_text(encoding="utf-8"))
    p = tmp_path / "config.json"
    p.write_text(json.dumps(src, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    monkeypatch.setattr(sw, "CONFIG_PATH", p)
    return p


def test_linux_is_untouched(tmp_config):
    """F) Linux/Replit: fonksiyon hiçbir şey yazmaz."""
    before = tmp_config.read_text(encoding="utf-8")
    sw._enable_paper_test_mode()  # os.name == 'posix'
    assert tmp_config.read_text(encoding="utf-8") == before


def test_windows_enables_paper_flags_only(tmp_config, monkeypatch):
    """1/2) Windows'ta 4 bayrak açılır; mode=PAPER ve canlı emir değişmez."""
    monkeypatch.setattr(sw, "os", _OsNtProxy())
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("ALPHA_WINDOWS_PAPER_AUTO", raising=False)
    live_before = os.environ.get("ALPHA_ENABLE_LIVE_TRADING")
    sw._enable_paper_test_mode()
    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    a = cfg["adaptive_system"]
    assert a["enabled"] is True
    assert a["auto_paper_enabled"] is True
    assert a["mode"] == "AUTO"
    assert a["kill_switch"] is False
    assert cfg["mode"] == "PAPER"  # üst düzey mod PAPER kalır
    assert os.environ.get("ALPHA_ENABLE_LIVE_TRADING") == live_before
    # diğer adaptive alanları korunur
    assert "learning_enabled" in a and "cooldown_minutes" in a


def test_windows_production_skips(tmp_config, monkeypatch):
    monkeypatch.setattr(sw, "os", _OsNtProxy())
    monkeypatch.setenv("FLASK_ENV", "production")
    before = tmp_config.read_text(encoding="utf-8")
    sw._enable_paper_test_mode()
    assert tmp_config.read_text(encoding="utf-8") == before


def test_windows_opt_out(tmp_config, monkeypatch):
    monkeypatch.setattr(sw, "os", _OsNtProxy())
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("ALPHA_WINDOWS_PAPER_AUTO", "false")
    before = tmp_config.read_text(encoding="utf-8")
    sw._enable_paper_test_mode()
    assert tmp_config.read_text(encoding="utf-8") == before


def test_windows_idempotent(tmp_config, monkeypatch, caplog):
    monkeypatch.setattr(sw, "os", _OsNtProxy())
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("ALPHA_WINDOWS_PAPER_AUTO", raising=False)
    sw._enable_paper_test_mode()
    after_first = tmp_config.read_text(encoding="utf-8")
    with caplog.at_level("INFO", logger="alpha.serve"):
        sw._enable_paper_test_mode()
    assert tmp_config.read_text(encoding="utf-8") == after_first
    assert any("zaten aktif" in r.message for r in caplog.records)


def test_repo_config_flags_unchanged():
    """F) Depodaki config.json Paper otomasyon kilitleriyle kalır (Replit
    davranışı bu committe DEĞİŞMEDİ)."""
    cfg = json.loads((ROOT / "alpha20_v1" / "config.json")
                     .read_text(encoding="utf-8"))
    a = cfg["adaptive_system"]
    assert a["enabled"] is False
    assert a["kill_switch"] is True
    assert a["auto_paper_enabled"] is False
    assert a["mode"] == "MONITOR"
