"""Windows Runtime Control Bridge — /health/runtime güvenlik + içerik.

MERGE GUARD NOTU: Operatör onaylı davranış; görev ajanları 'kapsam dışı'
diye kaldıramaz.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _login(c):
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["username"] = "test"


def test_requires_session(monkeypatch):
    """Oturumsuz istek 401 alır (exempt listesinde DEĞİL)."""
    monkeypatch.delenv("REPLIT_DEV_BYPASS", raising=False)
    monkeypatch.delenv("LOCAL_DEV_BYPASS", raising=False)
    app_module.app.config["TESTING"] = False
    try:
        with app_module.app.test_client() as c:
            r = c.get("/health/runtime")
        # API-öneki olmayan yol: oturumsuz istek login'e yönlendirilir
        # (veri sızmaz); 401 de kabul.
        assert r.status_code in (302, 401)
        if r.status_code == 302:
            assert "/login" in (r.headers.get("Location") or "")
    finally:
        app_module.app.config["TESTING"] = True


def test_payload_fields_and_read_only(client):
    r = client.get("/health/runtime")
    assert r.status_code == 200
    d = r.get_json()
    for key in ("app", "paper", "controller", "auto_loop", "cycle_count",
                "last_cycle", "positions", "paper_balance", "last_trade",
                "last_error", "runtime_override", "safe_mode"):
        assert key in d
    assert d["app"] == "running"
    assert d["controller"] in ("running", "stopped")
    assert d["paper"] in ("active", "starting", "disabled")


def test_no_secrets_in_payload(client):
    """Güvenlik: yanıt hiçbir anahtar/parola/secret içermez."""
    raw = client.get("/health/runtime").get_data(as_text=True).lower()
    for banned in ("api_key", "apikey", "secret", "password", "parola",
                   "hash", "token", "binance_global", "binance_tr_api"):
        assert banned not in raw


def test_get_only_no_mutation(client):
    """Salt okunur: POST kabul edilmez; state dosyası değişmez."""
    state_p = ROOT / "alpha20_v1" / "state.json"
    before = state_p.read_bytes() if state_p.exists() else b""
    assert client.post("/health/runtime").status_code == 405
    client.get("/health/runtime")
    after = state_p.read_bytes() if state_p.exists() else b""
    assert before == after


def test_dashboard_card_present():
    src = (ROOT / "templates" / "trading_home.html").read_text(
        encoding="utf-8")
    assert "WINDOWS RUNTIME" in src
    assert "/health/runtime" in src
    for el in ("th-winrt-controller", "th-winrt-cycle",
               "th-winrt-balance", "th-winrt-pos"):
        assert el in src
