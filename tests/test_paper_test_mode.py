"""Windows PAPER AUTO — SALT BELLEK runtime override (config mutasyonu YOK).

Reviewer kararı: startup hiçbir dosya değiştirmez; override yalnız
os.name=='nt' + ALPHA_WINDOWS_PAPER_AUTO=true (opt-in) iken bellekte
uygulanır. Linux/Replit ve production davranışı değişmez.

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
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import serve_windows as sw  # noqa: E402
import auto_controller as ac  # noqa: E402

CONFIG = ROOT / "alpha20_v1" / "config.json"


class _OsNtProxy:
    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


@pytest.fixture(autouse=True)
def _clean_override():
    ac.RUNTIME_ADAPTIVE_OVERRIDE.clear()
    yield
    ac.RUNTIME_ADAPTIVE_OVERRIDE.clear()


def test_linux_no_override(monkeypatch):
    """Linux/Replit: env açık olsa bile override uygulanmaz."""
    monkeypatch.setenv("ALPHA_WINDOWS_PAPER_AUTO", "true")
    assert sw._apply_paper_runtime_override() is False
    assert ac.RUNTIME_ADAPTIVE_OVERRIDE == {}


def test_opt_in_required(monkeypatch):
    """Opt-in yoksa Windows'ta bile hiçbir override yapılmaz."""
    monkeypatch.setattr(sw, "os", _OsNtProxy())
    monkeypatch.delenv("ALPHA_WINDOWS_PAPER_AUTO", raising=False)
    assert sw._apply_paper_runtime_override() is False
    assert ac.RUNTIME_ADAPTIVE_OVERRIDE == {}


def test_deployment_skips(monkeypatch):
    """Gerçek yayınlanmış üretim (REPLIT_DEPLOYMENT): override ASLA uygulanmaz."""
    monkeypatch.setattr(sw, "os", _OsNtProxy())
    monkeypatch.setenv("ALPHA_WINDOWS_PAPER_AUTO", "true")
    monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")
    assert sw._apply_paper_runtime_override() is False
    assert ac.RUNTIME_ADAPTIVE_OVERRIDE == {}


def test_flask_env_production_does_not_block_explicit_optin(monkeypatch,
                                                            caplog):
    """KÖK NEDEN DÜZELTMESİ: .env.example şablonu FLASK_ENV=production
    içerir; açık opt-in (ALPHA_WINDOWS_PAPER_AUTO=true) bunu ezer.
    Windows local hiçbir zaman gerçek üretim değildir (127.0.0.1)."""
    monkeypatch.setattr(sw, "os", _OsNtProxy())
    monkeypatch.setenv("ALPHA_WINDOWS_PAPER_AUTO", "true")
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("REPLIT_DEPLOYMENT", raising=False)
    before = CONFIG.read_bytes()
    with caplog.at_level("INFO", logger="alpha.serve"):
        assert sw._apply_paper_runtime_override() is True
    assert CONFIG.read_bytes() == before
    assert any("FLASK_ENV=production" in r.message and "yine uygulanıyor"
               in r.message for r in caplog.records)


def test_windows_optin_overrides_in_memory_only(monkeypatch, caplog):
    """Windows + opt-in: bellek override'ı aktif; config.json BYTE-BYTE aynı."""
    monkeypatch.setattr(sw, "os", _OsNtProxy())
    monkeypatch.setenv("ALPHA_WINDOWS_PAPER_AUTO", "true")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    before = CONFIG.read_bytes()
    with caplog.at_level("INFO", logger="alpha.serve"):
        assert sw._apply_paper_runtime_override() is True
    assert CONFIG.read_bytes() == before  # dosya değişmedi
    assert any("WINDOWS PAPER AUTO ENABLED (RUNTIME)" in r.message
               for r in caplog.records)
    # Runtime'da etkin görünüm: _load_config merge eder, dosya değişmez.
    cfg = ac._load_config()
    a = cfg["adaptive_system"]
    assert a["enabled"] is True and a["mode"] == "AUTO"
    assert a["auto_paper_enabled"] is True and a["kill_switch"] is False
    assert cfg["mode"] == "PAPER"  # üst düzey mod PAPER kalır
    assert CONFIG.read_bytes() == before  # _load_config da yazmadı
    # Dosyadaki gerçek değerler kilitli kalır (restart sonrası aynı):
    disk = json.loads(before)
    assert disk["adaptive_system"]["enabled"] is False
    assert disk["adaptive_system"]["kill_switch"] is True


def test_override_cleared_means_default(monkeypatch):
    """Override temizlenince (yeni süreç eşdeğeri) davranış varsayılana döner."""
    ac.set_runtime_adaptive_override({"enabled": True})
    ac.RUNTIME_ADAPTIVE_OVERRIDE.clear()
    a = ac._load_config()["adaptive_system"]
    assert a["enabled"] is False


def test_repo_config_flags_unchanged():
    """Depodaki config.json Paper otomasyon kilitleriyle kalır."""
    a = json.loads(CONFIG.read_text(encoding="utf-8"))["adaptive_system"]
    assert a["enabled"] is False
    assert a["kill_switch"] is True
    assert a["auto_paper_enabled"] is False
    assert a["mode"] == "MONITOR"


def test_no_file_write_calls_in_override_path():
    """Kaynak denetimi: override yolu hiçbir dosyaya yazmaz."""
    src = (ROOT / "serve_windows.py").read_text(encoding="utf-8")
    fn = src[src.index("def _apply_paper_runtime_override"):
             src.index("def _watch_first_cycle")]
    for banned in ("write_text", "json.dump", "open(", ".write("):
        assert banned not in fn
