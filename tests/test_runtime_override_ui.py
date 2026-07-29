"""Görev #86 — Override uyarı notları için kalıcı otomatik test.

Kanıtlanan (Görev #84 davranışı):
- RUNTIME_ADAPTIVE_OVERRIDE aktifken /panel çıktısında 'eziliyor' notları
  görünür; override boşken görünmez.
- POST /adaptive/mode, /adaptive/auto-paper, /adaptive/kill-switch ve
  /adaptive/enable yanıt mesajlarına override uyarısı eklenir; override
  boşken mesajlar temizdir.
- Test sonunda config.json içeriği geri alınır (POST'lar dosyaya yazar).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import auto_controller as ac  # noqa: E402
import app as app_module      # noqa: E402

OVERRIDE_FLAGS = {
    "enabled": True,
    "mode": "AUTO",
    "auto_paper_enabled": True,
    "kill_switch": False,
}

WARN_TOKEN = "eziliyor"  # hem form notları hem POST mesajı bu kelimeyi taşır


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Override'ı temizle, config.json'u yedekle, yan etkili çağrıları sustur."""
    # config.json yedeği — POST rotaları dosyaya yazar
    cfg_path = app_module.CONFIG_PATH
    original_cfg = cfg_path.read_bytes()

    saved = dict(ac.RUNTIME_ADAPTIVE_OVERRIDE)
    ac.RUNTIME_ADAPTIVE_OVERRIDE.clear()

    # Yan etkiler: controller thread'i, kill-switch dosya yazımı, log kaydı
    monkeypatch.setattr(app_module.ac, "start_controller_loop", lambda *a, **k: None)
    monkeypatch.setattr(app_module.ac, "stop_controller_loop", lambda *a, **k: None)
    monkeypatch.setattr(app_module.sg, "activate_kill_switch", lambda *a, **k: None)
    monkeypatch.setattr(app_module.sg, "deactivate_kill_switch", lambda *a, **k: None)
    monkeypatch.setattr(app_module.ms, "append_system_error", lambda *a, **k: None)

    app_module.app.config["TESTING"] = True
    try:
        yield
    finally:
        ac.RUNTIME_ADAPTIVE_OVERRIDE.clear()
        ac.RUNTIME_ADAPTIVE_OVERRIDE.update(saved)
        cfg_path.write_bytes(original_cfg)
        app_module.app.config["TESTING"] = False


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "test"
        sess["login_time"] = datetime.now(timezone.utc).isoformat()
    return c


def _set_override():
    ac.set_runtime_adaptive_override(dict(OVERRIDE_FLAGS))


# ── /panel form notları ───────────────────────────────────────────────────────

def test_panel_shows_override_notes_when_active():
    _set_override()
    r = _client().get("/panel")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert WARN_TOKEN in html
    # Hem genel not hem kill-switch'e özel not şablonda mevcut olmalı
    assert "bellek override'ı tarafından eziliyor" in html


def test_panel_has_no_override_notes_when_empty():
    r = _client().get("/panel")
    assert r.status_code == 200
    assert WARN_TOKEN not in r.get_data(as_text=True)


# ── POST yanıt mesajları — override aktif ────────────────────────────────────

def test_post_adaptive_mode_warns_under_override():
    _set_override()
    r = _client().post("/adaptive/mode", data={"mode": "MONITOR"})
    html = r.get_data(as_text=True)
    assert "Çalışma modu" in html
    assert "Dikkat: bu ayar şu an bellek override" in html


def test_post_auto_paper_warns_under_override():
    _set_override()
    r = _client().post("/adaptive/auto-paper", data={"enabled": "0"})
    html = r.get_data(as_text=True)
    assert "Otomatik PAPER kapatıldı" in html
    assert "Dikkat: bu ayar şu an bellek override" in html


def test_post_kill_switch_warns_under_override():
    _set_override()
    c = _client()
    r = c.post("/adaptive/kill-switch", data={"activate": "1"})
    html = r.get_data(as_text=True)
    assert "Acil durdur etkinleştirildi" in html
    assert "Dikkat: bu ayar şu an bellek override" in html
    r2 = c.post("/adaptive/kill-switch", data={"activate": "0"})
    assert "Dikkat: bu ayar şu an bellek override" in r2.get_data(as_text=True)


def test_post_adaptive_enable_warns_under_override():
    _set_override()
    r = _client().post("/adaptive/enable", data={"enabled": "1"})
    html = r.get_data(as_text=True)
    assert "Uyarlanabilir motor etkinleştirildi" in html
    assert "Dikkat: bu ayar şu an bellek override" in html


def test_post_adaptive_settings_warns_only_for_overridden_keys():
    ac.set_runtime_adaptive_override({"cooldown_minutes": 15})
    c = _client()
    # Override'lı alan gönderilirse uyarı çıkar
    r = c.post("/adaptive/settings", data={"cooldown_minutes": "30"})
    assert "Dikkat: bu ayar şu an bellek override" in r.get_data(as_text=True)
    # Override'sız alan gönderilirse uyarı çıkmaz
    r2 = c.post("/adaptive/settings", data={"minimum_learning_trades": "25"})
    html2 = r2.get_data(as_text=True)
    assert "kaydedildi" in html2
    assert "Dikkat: bu ayar şu an bellek override" not in html2


# ── POST yanıt mesajları — override boş ──────────────────────────────────────

@pytest.mark.parametrize("path,data,expected", [
    ("/adaptive/mode",        {"mode": "MONITOR"}, "Çalışma modu"),
    ("/adaptive/auto-paper",  {"enabled": "0"},    "Otomatik PAPER kapatıldı"),
    ("/adaptive/kill-switch", {"activate": "0"},   "Acil durdur devre dışı"),
    ("/adaptive/enable",      {"enabled": "0"},    "devre dışı bırakıldı"),
])
def test_post_messages_clean_without_override(path, data, expected):
    r = _client().post(path, data=data)
    html = r.get_data(as_text=True)
    assert expected in html
    assert "Dikkat: bu ayar şu an bellek override" not in html
    assert WARN_TOKEN not in html
