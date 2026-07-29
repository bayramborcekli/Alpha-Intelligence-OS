"""fetch_klines geçici SSL/ağ hatalarında yeniden dener — ağa çıkmadan."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "alpha20_v1"))
import alpha20  # noqa: E402


def _resp(rows=None):
    r = mock.Mock()
    r.raise_for_status.return_value = None
    r.json.return_value = rows or [
        [i, "1", "2", "0.5", "1.5", "10", i + 1, "15", 5, "4", "6", "0"]
        for i in range(40)
    ]
    return r


def test_ssl_error_then_success(monkeypatch):
    monkeypatch.setattr(alpha20, "rate_limit_remaining", lambda: 0)
    monkeypatch.setattr(alpha20, "note_rate_limit_success", lambda: None)
    monkeypatch.setattr(alpha20.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.SSLError("SEC_E_INVALID_TOKEN benzeri")
        return _resp()

    monkeypatch.setattr(alpha20.requests, "get", fake_get)
    df = alpha20.fetch_klines("BTCUSDT", "1h", 40)
    assert calls["n"] == 2 and len(df) == 40


def test_all_retries_fail_raises_diagnosis(monkeypatch):
    monkeypatch.setattr(alpha20, "rate_limit_remaining", lambda: 0)
    monkeypatch.setattr(alpha20.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        raise requests.exceptions.SSLError("handshake bozuldu")

    monkeypatch.setattr(alpha20.requests, "get", fake_get)
    with pytest.raises(RuntimeError):
        alpha20.fetch_klines("SOLUSDT", "1h", 40)
    assert calls["n"] == 3  # 1 + 2 retry


def test_rate_limit_blocks_before_request(monkeypatch):
    monkeypatch.setattr(alpha20, "rate_limit_remaining", lambda: 42.0)
    monkeypatch.setattr(alpha20, "rate_limit_reason", lambda: "test")
    called = {"n": 0}
    monkeypatch.setattr(alpha20.requests, "get",
                        lambda *a, **k: called.__setitem__("n", 1))
    with pytest.raises(RuntimeError):
        alpha20.fetch_klines("BTCUSDT", "1h", 40)
    assert called["n"] == 0
