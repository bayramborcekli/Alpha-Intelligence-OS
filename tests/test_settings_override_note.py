"""
Task 85 — Ana Ayarlar ve Akıllı Coin formları runtime override altında
yanıltmasın: override sözlüğü adaptive dışı anahtar içerirse ilgili formda
uyarı görünür; override yokken davranış değişmez.

Testler gerçek config dosyalarını KALICI değiştirmez: POST /settings için
config.json yedeklenip geri yüklenir; akıllı ayarlar kaydı monkeypatch ile
no-op yapılır.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import app as A                      # noqa: E402
import auto_controller as ac         # noqa: E402
import universe_manager as um        # noqa: E402


@pytest.fixture()
def client():
    A.app.config["TESTING"] = True
    with A.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def clean_override():
    saved = dict(ac.RUNTIME_ADAPTIVE_OVERRIDE)
    ac.RUNTIME_ADAPTIVE_OVERRIDE.clear()
    yield
    ac.RUNTIME_ADAPTIVE_OVERRIDE.clear()
    ac.RUNTIME_ADAPTIVE_OVERRIDE.update(saved)


@pytest.fixture()
def config_backup():
    original = A.CONFIG_PATH.read_bytes()
    yield
    A.CONFIG_PATH.write_bytes(original)


VALID_SETTINGS_FORM = {
    "minimum_score": "65", "scan_seconds": "60",
    "risk_per_trade_pct": "0.5", "daily_loss_limit_pct": "1.5",
    "max_consecutive_losses": "3", "reward_risk_ratio": "2.0",
    "atr_stop_multiplier": "3.0", "max_open_positions": "1",
}


def test_panel_no_override_no_warning(client):
    html = client.get("/panel").get_data(as_text=True)
    assert "bu bölümdeki bazı ayarlar" not in html
    assert "akıllı seçim ayarları şu an" not in html


def test_panel_shows_warnings_for_non_adaptive_override_keys(client):
    ac.set_runtime_adaptive_override(
        {"minimum_score": 80, "max_coins": 5, "enabled": True})
    html = client.get("/panel").get_data(as_text=True)
    # Ayarlar formu: form seviyesi + alan seviyesi uyarı
    assert "bu bölümdeki bazı ayarlar" in html
    assert "minimum_score" in html
    assert "eziliyor (etkin: 80)" in html
    # Akıllı Coin bölümü uyarısı
    assert "akıllı seçim ayarları şu an" in html
    assert "max_coins" in html


def test_panel_adaptive_only_override_leaves_main_forms_clean(client):
    ac.set_runtime_adaptive_override({"enabled": True, "mode": "MONITOR"})
    html = client.get("/panel").get_data(as_text=True)
    assert "bu bölümdeki bazı ayarlar" not in html
    assert "akıllı seçim ayarları şu an" not in html


def test_post_settings_message_includes_override_note(client, config_backup):
    ac.set_runtime_adaptive_override({"minimum_score": 80})
    html = client.post("/settings", data=VALID_SETTINGS_FORM).get_data(as_text=True)
    assert "bellek override" in html and "minimum_score" in html


def test_post_settings_no_override_no_note(client, config_backup):
    html = client.post("/settings", data=VALID_SETTINGS_FORM).get_data(as_text=True)
    assert "Ayarlar başarıyla kaydedildi." in html
    assert "Dikkat: bu ayar şu an bellek override" not in html


def test_post_smart_settings_override_note(client, monkeypatch):
    monkeypatch.setattr(um, "save_smart_config", lambda cfg: None)
    ac.set_runtime_adaptive_override({"max_coins": 5})
    html = client.post("/smart/settings",
                       data={"max_coins": "5"}).get_data(as_text=True)
    assert "Akıllı seçim ayarları kaydedildi." in html
    assert "bellek override" in html and "max_coins" in html


def test_post_smart_settings_no_override_no_note(client, monkeypatch):
    monkeypatch.setattr(um, "save_smart_config", lambda cfg: None)
    html = client.post("/smart/settings",
                       data={"max_coins": "10"}).get_data(as_text=True)
    assert "Akıllı seçim ayarları kaydedildi." in html
    assert "bellek override" not in html
