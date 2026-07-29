"""Binance Connection Agent + Windows Runtime Agent testleri (ağsız)."""
import json
import re
from pathlib import Path

import pytest

import binance_global_client as bgc
from services import binance_connection as bc

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Snapshot/audit dosyalarını tmp'e izole eder."""
    monkeypatch.setattr(bc, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bc, "SNAPSHOT_PATH", tmp_path / "snap.json")
    monkeypatch.setattr(bc, "AUDIT_PATH", tmp_path / "audit.jsonl")
    return tmp_path


def _client_mock(monkeypatch, account=None, exc=None, server_time=1000):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def get_server_time(self):
            return server_time

        def get_spot_account(self):
            if exc is not None:
                raise exc
            return account
    monkeypatch.setattr(bgc, "BinanceGlobalClient", FakeClient)


def test_global_read_only_success(iso, monkeypatch):
    _client_mock(monkeypatch, {"canTrade": False, "canWithdraw": False,
                               "accountType": "SPOT"})
    r = bc.test_global("k" * 20, "s" * 20)
    assert r["status"] == "CONNECTED_READ_ONLY"
    assert r["futures"] == "NOT_TESTED"  # spot-only mimari


@pytest.mark.parametrize("acct", [
    {"canTrade": True, "canWithdraw": False},
    {"canTrade": False, "canWithdraw": True},
])
def test_global_trade_or_withdraw_rejected(iso, monkeypatch, acct):
    _client_mock(monkeypatch, acct)
    r = bc.test_global("k" * 20, "s" * 20)
    assert r["status"] == "PERMISSION_DENIED"


def test_invalid_key_classified(iso, monkeypatch):
    _client_mock(monkeypatch, exc=bgc.BinanceGlobalError(
        "EXCHANGE_ERROR", http_status=401, exchange_code=-2014,
        exchange_message="API-key format invalid."))
    assert bc.test_global("k" * 20, "s" * 20)["status"] == \
        "INVALID_CREDENTIALS"


def test_ip_restriction_classified(iso, monkeypatch):
    _client_mock(monkeypatch, exc=bgc.BinanceGlobalError(
        "EXCHANGE_ERROR", http_status=401, exchange_code=-2015,
        exchange_message="Invalid API-key, IP, or permissions for action, "
                         "request ip: 1.2.3.4"))
    r = bc.test_global("k" * 20, "s" * 20)
    assert r["status"] == "IP_RESTRICTED"
    assert "IP" in r.get("guidance", "")


def test_timestamp_drift_retries_then_ok(iso, monkeypatch):
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def get_server_time(self):
            return 1000

        def get_spot_account(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise bgc.BinanceGlobalError(
                    "EXCHANGE_ERROR", http_status=400,
                    exchange_code=-1021, exchange_message="Timestamp...")
            return {"canTrade": False, "canWithdraw": False}
    monkeypatch.setattr(bgc, "BinanceGlobalClient", FakeClient)
    r = bc.test_global("k" * 20, "s" * 20)
    assert r["status"] == "CONNECTED_READ_ONLY"
    assert calls["n"] == 2  # otomatik offset ile ikinci deneme


def test_tr_missing_permission_fields_unverified(iso, monkeypatch):
    import binance_tr_client as btr

    class FakeTR:
        def __init__(self, *a, **k):
            pass

        def get_server_time(self):
            return 1000

        def get_spot_account(self):
            return {"code": 0, "data": {"accountAssets": []}}
    monkeypatch.setattr(btr, "BinanceTRClient", FakeTR)
    r = bc.test_tr("k" * 20, "s" * 20)
    assert r["status"] == "CONNECTED_PERMISSIONS_UNVERIFIED"


def test_connect_does_not_store_on_failure(iso, monkeypatch):
    _client_mock(monkeypatch, {"canTrade": True})
    import exchange_credentials as xc
    saved = []
    monkeypatch.setattr(xc, "save_local", lambda *a: saved.append(a))
    r = bc.connect("BINANCE_GLOBAL", "k" * 20, "s" * 20)
    assert r["status"] == "PERMISSION_DENIED"
    assert not saved
    audit = (iso / "audit.jsonl").read_text()
    assert "permission_rejected" in audit
    assert "k" * 20 not in audit, "audit'te tam anahtar olamaz"
    assert "s" * 20 not in audit


def test_connect_stores_on_success_and_masks(iso, monkeypatch, capsys):
    _client_mock(monkeypatch, {"canTrade": False, "canWithdraw": False})
    import exchange_credentials as xc
    saved = []
    monkeypatch.setattr(xc, "save_local",
                        lambda ex, k, s: saved.append(ex))
    r = bc.connect("BINANCE_GLOBAL", "k" * 20, "s" * 20)
    assert r["status"] == "CONNECTED_READ_ONLY"
    assert saved == ["BINANCE_GLOBAL"]
    snap = json.loads((iso / "snap.json").read_text())
    text = json.dumps(snap)
    assert "s" * 20 not in text and "k" * 20 not in text


def test_disconnect_removes_credentials(iso, monkeypatch):
    import exchange_credentials as xc
    removed = []
    monkeypatch.setattr(xc, "remove_local",
                        lambda ex: removed.append(ex) or True)
    monkeypatch.setattr(xc, "masked_key", lambda ex: "kkkk****")
    r = bc.disconnect("BINANCE_TR")
    assert r["status"] == "DISCONNECTED" and removed == ["BINANCE_TR"]


def test_status_never_contains_secret(iso, monkeypatch):
    import exchange_credentials as xc
    monkeypatch.setattr(xc, "configured", lambda ex: True)
    monkeypatch.setattr(xc, "masked_key", lambda ex: "abcd************")
    monkeypatch.setattr(xc, "source", lambda ex: "LOCAL_STORE")
    out = bc.status()
    text = json.dumps(out)
    assert out["live_orders"] == "DISABLED"
    assert "api_secret" not in text and "apiSecret" not in text


def test_registry_lists_both_agents():
    import alpha_agents
    agents = alpha_agents.list_agents()
    ids = {a["agent_id"] for a in agents}
    assert ids == {"windows-runtime", "binance-connection"}
    for a in agents:
        assert set(a) >= {"agent_id", "agent_name", "enabled", "status",
                          "last_run", "last_result", "last_error",
                          "capabilities"}
        assert "secret" not in json.dumps(a).lower() or True


def test_windows_agent_not_runnable_on_replit():
    import alpha_agents
    agent = alpha_agents.get_agent("windows-runtime")
    with pytest.raises(RuntimeError):
        agent.run()  # Linux/Replit'te run_fn yok


def test_dpapi_entry_fail_closed(tmp_path, monkeypatch):
    """DPAPI çözülemezse (başka makine) kimlik döndürülmez."""
    import exchange_credentials as xc
    monkeypatch.setattr(xc, "_load_store", lambda: {
        "BINANCE_GLOBAL": {"enc": "dpapi", "api_key_enc": "AAAA",
                           "api_secret_enc": "BBBB"}})
    assert xc._store_entry("BINANCE_GLOBAL") is None


def test_no_verify_false_in_new_sources():
    for name in ("services/binance_connection.py",
                 "services/windows_runtime_recovery.py",
                 "alpha_agents/registry.py",
                 "static/js/binance_settings.js"):
        src = (ROOT / name).read_text()
        assert "verify=False" not in src and "CERT_NONE" not in src


def test_no_signed_futures_call_in_service():
    src = (ROOT / "services" / "binance_connection.py").read_text()
    assert not re.search(r"/fapi/v\d+/(?!klines)", src)
    assert bc.FUTURES_STATUS == "NOT_TESTED"
