"""Task 93 — 429/418 geri çekilmesi panelde görünür.

Doğrulananlar:
- register_rate_limit durum geçişinde diske yazar (bot ayrı süreç).
- read_rate_limit_file süresi dolan geri çekilmeyi 0'a düşürür.
- /api/operation-control/status yanıtı rate_limit alanını taşır:
  aktifken kalan süre + Türkçe neden, bitince active=False.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "alpha20_v1"))

import alpha20  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(alpha20, "RATE_LIMIT_STATE_PATH",
                        tmp_path / "rate_limit_state.json")
    alpha20.reset_rate_limit_state()
    yield
    alpha20.reset_rate_limit_state()


class TestPersistence:
    def test_register_writes_state_file(self):
        wait = alpha20.register_rate_limit(429, now=1000.0)
        assert wait > 0
        data = json.loads(alpha20.RATE_LIMIT_STATE_PATH.read_text())
        assert data["blocked_until"] == pytest.approx(1000.0 + wait)
        assert "429" in data["reason"]
        assert "duraklatıldı" in data["reason"]

    def test_418_reason_is_turkish_ban_message(self):
        alpha20.register_rate_limit(418, now=1000.0)
        data = json.loads(alpha20.RATE_LIMIT_STATE_PATH.read_text())
        assert "418" in data["reason"]
        assert "yasağı" in data["reason"]

    def test_reset_clears_state_file(self):
        alpha20.register_rate_limit(429, now=1000.0)
        alpha20.reset_rate_limit_state()
        data = json.loads(alpha20.RATE_LIMIT_STATE_PATH.read_text())
        assert data["blocked_until"] == 0.0
        assert data["reason"] == ""

    def test_write_failure_does_not_raise(self, monkeypatch, tmp_path):
        # Yazılamayan dizin — görünürlük ticaret akışını bloklamaz.
        monkeypatch.setattr(alpha20, "RATE_LIMIT_STATE_PATH",
                            tmp_path / "yok" / "x" / "state.json")
        assert alpha20.register_rate_limit(429, now=1000.0) > 0


class TestReadRateLimitFile:
    def test_active_backoff_has_remaining_and_reason(self):
        alpha20.register_rate_limit(429, now=1000.0)
        snap = alpha20.read_rate_limit_file(now=1010.0)
        assert snap["remaining_seconds"] == pytest.approx(50.0)
        assert "429" in snap["reason"]

    def test_expired_backoff_reports_zero(self):
        alpha20.register_rate_limit(429, now=1000.0)
        snap = alpha20.read_rate_limit_file(now=5000.0)
        assert snap == {"remaining_seconds": 0.0, "reason": ""}

    def test_missing_file_reports_zero(self, tmp_path):
        snap = alpha20.read_rate_limit_file(
            now=1.0, path=tmp_path / "yok.json")
        assert snap == {"remaining_seconds": 0.0, "reason": ""}

    def test_corrupt_file_reports_zero(self, tmp_path):
        p = tmp_path / "bozuk.json"
        p.write_text("{bozuk")
        snap = alpha20.read_rate_limit_file(now=1.0, path=p)
        assert snap == {"remaining_seconds": 0.0, "reason": ""}


class TestStatusEndpoint:
    @pytest.fixture()
    def app_module(self):
        import app as app_module
        return app_module

    @pytest.fixture()
    def client(self, app_module):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            with c.session_transaction() as s:
                s["authenticated"] = True
                s["username"] = "test"
            yield c

    def _write_state(self, app_module, monkeypatch, tmp_path,
                     blocked_until, reason):
        p = tmp_path / "rate_limit_state.json"
        p.write_text(json.dumps(
            {"blocked_until": blocked_until, "reason": reason}))
        monkeypatch.setattr(app_module, "RATE_LIMIT_STATE_PATH", p)

    def test_status_shows_active_backoff(
            self, app_module, client, monkeypatch, tmp_path):
        self._write_state(app_module, monkeypatch, tmp_path,
                          time.time() + 120,
                          "Binance istek limiti (429): tarama duraklatıldı.")
        payload = client.get("/api/operation-control/status").get_json()
        rl = payload["data"]["rate_limit"]
        assert rl["active"] is True
        assert 0 < rl["remaining_seconds"] <= 121
        assert "429" in rl["reason"]

    def test_status_hides_expired_backoff(
            self, app_module, client, monkeypatch, tmp_path):
        self._write_state(app_module, monkeypatch, tmp_path,
                          time.time() - 5, "eski neden")
        payload = client.get("/api/operation-control/status").get_json()
        rl = payload["data"]["rate_limit"]
        assert rl == {"active": False, "remaining_seconds": 0, "reason": ""}

    def test_status_without_file_is_inactive(
            self, app_module, client, monkeypatch, tmp_path):
        monkeypatch.setattr(app_module, "RATE_LIMIT_STATE_PATH",
                            tmp_path / "yok.json")
        payload = client.get("/api/operation-control/status").get_json()
        assert payload["data"]["rate_limit"]["active"] is False
