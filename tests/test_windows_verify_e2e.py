# -*- coding: utf-8 -*-
"""Windows saha doğrulama aracının (tools/windows/verify_e2e.py)
kontrol mantığı regresyonu.

Araç Windows'ta canlı servise karşı çalışır; burada aynı kontrol
fonksiyonları Flask test client'tan alınan GERÇEK endpoint çıktısıyla
doğrulanır — böylece uçlardaki alan adları değişirse test kırılır ve
saha aracı sessizce çürümez.
"""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import dashboard_api as dapi

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "verify_e2e", ROOT / "tools" / "windows" / "verify_e2e.py")
ve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ve)


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


@pytest.fixture
def paper_ledger(tmp_path, monkeypatch):
    import app as flask_app
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"balance": 1234.5}), encoding="utf-8")
    monkeypatch.setattr(flask_app, "STATE_PATH", p)
    return p


def _login(client):
    with client.session_transaction() as s:
        s["authenticated"] = True
        s["username"] = "test"


def _maps(client):
    """Aracın collect() eşleniği: test client'tan üç ucu okur."""
    acc = client.get("/api/accounts").get_json()["data"]["accounts"]
    wal = client.get("/api/accounts/wallets").get_json()["data"]["accounts"]
    por = client.get(
        "/api/accounts/portfolio").get_json()["data"]["components"]
    return ({x["account_id"]: x for x in acc},
            {x["account_id"]: x for x in wal},
            {x["account_id"]: x for x in por})


def _not_configured():
    raise dapi.SafeExchangeError(
        "NOT_CONFIGURED", dapi.ERROR_MESSAGES["NOT_CONFIGURED"])


class TestVerifyToolAgainstRealEndpoints:
    def test_consistent_states_pass(self, client, paper_ledger):
        _login(client)
        with patch.object(dapi, "_spot_account_raw",
                          side_effect=_not_configured), \
             patch.object(dapi, "_tr_account_raw",
                          side_effect=_not_configured):
            a, w, p = _maps(client)
        fails = ve.check_consistency(a, w, p)
        assert fails == [], fails

    def test_paper_known_balance_passes(self, client, paper_ledger):
        _login(client)
        with patch.object(dapi, "_spot_account_raw",
                          side_effect=_not_configured), \
             patch.object(dapi, "_tr_account_raw",
                          side_effect=_not_configured):
            a, w, _ = _maps(client)
        assert ve.check_paper(a, w) == []

    def test_paper_unknown_balance_fails(self, client, tmp_path,
                                         monkeypatch):
        import app as flask_app
        monkeypatch.setattr(flask_app, "STATE_PATH",
                            tmp_path / "yok.json")
        _login(client)
        with patch.object(dapi, "_spot_account_raw",
                          side_effect=_not_configured), \
             patch.object(dapi, "_tr_account_raw",
                          side_effect=_not_configured):
            a, w, _ = _maps(client)
        fails = ve.check_paper(a, w)
        assert fails, "UNKNOWN Paper bakiyesi FAIL üretmeliydi"

    def test_contradiction_detected(self, paper_ledger, client):
        _login(client)
        with patch.object(dapi, "_spot_account_raw",
                          side_effect=_not_configured), \
             patch.object(dapi, "_tr_account_raw",
                          side_effect=_not_configured):
            a, w, p = _maps(client)
        # Zıt durum enjekte et: wallets bir hesabı farklı gösteriyor
        for aid, card in a.items():
            if card.get("connected") and aid in w:
                w[aid]["connection_state"] = "CONNECTION_FAILED" \
                    if w[aid]["connection_state"] != "CONNECTION_FAILED" \
                    else "HEALTHY"
                break
        assert ve.check_consistency(a, w, p), \
            "Zıt durum FAIL üretmeliydi"

    def test_csrf_regex_matches_login_page(self, client):
        body = client.get("/login").get_data(as_text=True)
        if 'csrf_token' in body:
            import re
            assert re.search(r'name="csrf_token"\s+value="([^"]+)"',
                             body)
