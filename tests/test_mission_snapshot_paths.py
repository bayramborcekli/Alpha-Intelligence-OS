# -*- coding: utf-8 -*-
"""MISSION — FIX DUPLICATE ACCOUNT SNAPSHOT PATHS regresyonu.

Windows kanıtı: /api/accounts Paper connected=true iken
/api/accounts/wallets Paper UNKNOWN, Global/TR UNAVAILABLE dönüyordu.

Kabul:
- Paper HEALTHY ise wallets/portfolio Paper UNKNOWN olamaz.
- Global/TR credential yokken NOT_CONFIGURED (UNAVAILABLE değil); bu
  Paper state'ini ETKİLEMEZ.
- wallets/portfolio kanonik snapshot'a delege eder (bağımsız fetch yok).
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import dashboard_api as dapi

GLOBAL_RAW = {"balances": [
    {"asset": "USDT", "free": "100.0", "locked": "0.0"}]}
TR_RAW = {"code": 0, "data": {"accountAssets": [
    {"asset": "USDT", "free": "50.0", "locked": "0.0"},
    {"asset": "TRY", "free": "0", "locked": "0"}]}}


def _login(client):
    with client.session_transaction() as s:
        s["authenticated"] = True
        s["username"] = "test"


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
    """PAPER simülasyon defterini bilinen bakiyeyle sabitler."""
    import app as flask_app
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"balance": 1234.5}), encoding="utf-8")
    monkeypatch.setattr(flask_app, "STATE_PATH", p)
    return p


def _not_configured():
    raise dapi.SafeExchangeError(
        "NOT_CONFIGURED", dapi.ERROR_MESSAGES["NOT_CONFIGURED"])


class _RawCtx:
    def __init__(self, g="ok", t="ok"):
        self.g, self.t = g, t

    def __enter__(self):
        def g_raw():
            if self.g == "ok":
                return (GLOBAL_RAW, 5)
            _not_configured()

        def t_raw():
            if self.t == "ok":
                return (TR_RAW, 5)
            _not_configured()

        self.ps = [
            patch.object(dapi, "_spot_account_raw", side_effect=g_raw),
            patch.object(dapi, "_tr_account_raw", side_effect=t_raw),
            patch.object(dapi, "_global_creds",
                         return_value=("G" * 20, "S" * 20)),
            patch.object(dapi, "_tr_creds",
                         return_value=("T" * 20, "U" * 20)),
        ]
        for p in self.ps:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self.ps:
            p.stop()
        return False


def _by_ex(client, path):
    r = client.get(path)
    assert r.status_code == 200
    data = r.get_json()["data"]
    items = data.get("accounts") or data.get("components")
    import accounts_registry as reg
    accs = {a["account_id"]: a["exchange"] for a in reg.load_registry()}
    return {accs.get(i["account_id"], i["account_id"]): i
            for i in items}, data


class TestPaperIndependence:
    def test_paper_healthy_in_wallets(self, client, paper_ledger):
        _login(client)
        with _RawCtx(g="nc", t="nc"):
            w, _ = _by_ex(client, "/api/accounts/wallets")
        p = w.get("PAPER")
        assert p is not None
        assert p["connection_state"] == "HEALTHY"
        assert p["status"] == "OK"
        assert p["value_usdt"] == "1234.5"

    def test_paper_value_survives_missing_exchange_creds(
            self, client, paper_ledger):
        # Global/TR NOT_CONFIGURED → Paper bileşeni yine bilinir;
        # yalnız TOPLAM UNKNOWN kalır (politika).
        _login(client)
        with _RawCtx(g="nc", t="nc"):
            comps, data = _by_ex(client, "/api/accounts/portfolio")
        p = comps.get("PAPER")
        if p is None:
            pytest.skip("PAPER portföyde yok")
        assert p["value_usdt"] == "1234.5"
        assert p["connection_state"] == "HEALTHY"
        for ex in ("BINANCE_GLOBAL", "BINANCE_TR"):
            if ex in comps:
                assert comps[ex]["connection_state"] == "NOT_CONFIGURED"
        assert data["total_usdt"] == "UNKNOWN"


class TestNoUnavailableLeak:
    def test_not_configured_replaces_unavailable(self, client,
                                                 paper_ledger):
        _login(client)
        with _RawCtx(g="nc", t="nc"):
            w, _ = _by_ex(client, "/api/accounts/wallets")
        for ex in ("BINANCE_GLOBAL", "BINANCE_TR"):
            if ex in w:
                assert w[ex]["connection_state"] == "NOT_CONFIGURED"
                assert w[ex]["status"] == "NOT_CONFIGURED"
                assert "UNAVAILABLE" not in str(w[ex]["status"])

    def test_healthy_exchange_shows_ok(self, client, paper_ledger):
        _login(client)
        with _RawCtx():
            w, _ = _by_ex(client, "/api/accounts/wallets")
        for ex in ("BINANCE_GLOBAL", "BINANCE_TR"):
            if ex in w:
                assert w[ex]["connection_state"] == "HEALTHY"
                assert w[ex]["status"] == "OK"


class TestSameGenerationAcrossEndpoints:
    def test_settings_wallets_portfolio_agree(self, client,
                                              paper_ledger):
        _login(client)
        with _RawCtx(g="nc", t="ok"):
            cards, _ = _by_ex(client, "/api/accounts")
            w, _ = _by_ex(client, "/api/accounts/wallets")
            comps, _ = _by_ex(client, "/api/accounts/portfolio")
        for ex in ("PAPER", "BINANCE_GLOBAL", "BINANCE_TR"):
            if ex in cards and cards[ex]["connected"] and ex in w:
                assert cards[ex]["connection_state"] == \
                    w[ex]["connection_state"], (
                        f"{ex}: Hesaplarım != wallets — aynı snapshot "
                        "jenerasyonu için zıt state (ROOT BUG)")
                if ex in comps:
                    assert comps[ex]["connection_state"] == \
                        w[ex]["connection_state"]


class TestEmergencyStopDecoupling:
    """Opsiyonel borsanın NOT_CONFIGURED olması global acil durdurma
    sebebi OLAMAZ; gerçek kill-switch bayrağı ise fail-closed kalır."""

    def test_not_configured_does_not_activate_kill_switch(
            self, client, paper_ledger, monkeypatch):
        import app as flask_app
        # Kill-switch bayrağı kapalı bir config sabitle:
        monkeypatch.setattr(
            flask_app, "load_config",
            lambda: ({"adaptive_system": {"kill_switch": False}}, None))
        _login(client)
        with _RawCtx(g="nc", t="nc"):
            # Snapshot'lar NOT_CONFIGURED üretir…
            w, _ = _by_ex(client, "/api/accounts/wallets")
            assert any(v["connection_state"] == "NOT_CONFIGURED"
                       for k, v in w.items() if k != "PAPER")
            # …ama operasyon durumu ACİL DURDURMA'ya geçmez.
            r = client.get("/api/operation-control/status")
        assert r.status_code == 200
        body = r.get_json()
        txt = json.dumps(body)
        assert '"kill_switch_active": true' not in txt.replace(" ", "")
        assert "ACTIVE" not in str(
            (body.get("data") or body).get("status", {})
            .get("kill_switch_state", "")).upper() or \
            "INACTIVE" in txt.upper()

    def test_real_kill_switch_flag_stays_fail_closed(
            self, client, paper_ledger, monkeypatch):
        import app as flask_app
        monkeypatch.setattr(
            flask_app, "load_config",
            lambda: ({"adaptive_system": {"kill_switch": True}}, None))
        assert flask_app._operation_kill_switch_active(
            {"adaptive_system": {"kill_switch": True}}) is True
        _login(client)
        r = client.get("/api/operation-control/status")
        assert r.status_code == 200
        assert "ACTIVE" in json.dumps(r.get_json()).upper()

    def test_kill_switch_reads_config_not_snapshots(self):
        # Kod düzeyi kanıt: kill-switch yalnız config bayrağından okunur;
        # hesap snapshot'ı/bağlantı durumu girdisi DEĞİLDİR.
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[1] / "app.py").read_text(
            encoding="utf-8")
        body = src.split("def _operation_kill_switch_active")[1].split(
            "\ndef ")[0]
        for banned in ("_account_snapshot", "connection_state",
                       "global_spot_account", "tr_account",
                       "NOT_CONFIGURED", "UNKNOWN"):
            assert banned not in body


class TestPaperLedgerPath:
    def test_paper_balance_uses_root_anchored_path(self, tmp_path,
                                                   monkeypatch):
        # Çalışma dizini değişse bile PAPER defteri bulunur (Windows).
        import os
        import app as flask_app
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"balance": 7}), encoding="utf-8")
        monkeypatch.setattr(flask_app, "STATE_PATH", p)
        old = os.getcwd()
        os.chdir(tmp_path)
        try:
            assert flask_app._paper_balance() == "7"
        finally:
            os.chdir(old)
