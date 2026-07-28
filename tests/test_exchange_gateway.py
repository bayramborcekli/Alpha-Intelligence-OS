"""Mission 1400 — salt-okunur borsa geçidi güvenlik testleri (ağ yok).

Spot-only mimari: Futures özeti KALDIRILDI; geçit yalnız TR spot içerir.
"""
import json
from unittest import mock

import pytest

import exchange_gateway as xg


@pytest.fixture(autouse=True)
def _clean_cache():
    xg.clear_cache()
    yield
    xg.clear_cache()
def test_gateway_has_no_own_signed_fetch_path():
    # Kanonik hesap servisi delegasyonu: gateway kendi imzalı fetch'ini
    # taşımaz (tek hesap doğruluk kaynağı).
    assert not hasattr(xg, "_signed_get")
    assert not hasattr(xg, "TR_ALLOWLIST")


def test_no_futures_remnants_in_gateway():
    assert not hasattr(xg, "GLOBAL_BASE")
    assert not hasattr(xg, "GLOBAL_ALLOWLIST")
    assert not hasattr(xg, "global_futures_summary")
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "exchange_gateway.py").read_text()
    assert "fapi" not in src


def test_fail_closed_without_secrets_no_network():
    import dashboard_api as dapi
    with mock.patch.dict(xg.os.environ, {}, clear=True), \
         mock.patch.object(dapi, "_tr_account_raw") as g:
        out = xg.exchange_summary()
        g.assert_not_called()
    assert "global_futures" not in out   # Futures kaldırıldı
    assert out["tr_spot"]["configured"] is False
    assert out["live_trading"] is False


def _fake_resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    return r


def test_no_secret_material_in_summary():
    env = {"BINANCE_TR_API_KEY": "TKEY" + "x" * 20 + "TEND",
           "BINANCE_TR_API_SECRET": "TSECRET" + "y" * 20}
    tr_payload = {"code": 0, "data": {"accountAssets": [
        {"asset": "TRY", "free": "1.39", "locked": "0"}]}}
    import binance_tr_client as btr
    with mock.patch.dict(xg.os.environ, env, clear=True), \
         mock.patch.object(btr.BinanceTRClient, "get_spot_account",
                           return_value=tr_payload):
        out = xg.exchange_summary()
    blob = json.dumps(out)
    for secret in (env["BINANCE_TR_API_KEY"], env["BINANCE_TR_API_SECRET"]):
        assert secret not in blob, "secret yanıt gövdesine sızdı!"
    assert "global_futures" not in out   # Futures kaldırıldı
    assert out["tr_spot"]["ok"]
    assert out["tr_spot"]["key_masked"].count("…") == 1


def test_api_exchange_requires_auth():
    import app as flask_app
    flask_app.app.config["TESTING"] = False
    try:
        client = flask_app.app.test_client()
        r = client.get("/api/exchange/summary")
        # 401 = oturum yok, 403 = kurulum kilitli, 302 = login yönlendirmesi
        assert r.status_code in (302, 401, 403)
    finally:
        flask_app.app.config["TESTING"] = True


def test_malformed_payloads_return_safe_error():
    env = {"BINANCE_TR_API_KEY": "t" * 20, "BINANCE_TR_API_SECRET": "u" * 20}
    import dashboard_api as dapi
    with mock.patch.dict(xg.os.environ, env, clear=True):
        with mock.patch.object(
                dapi, "_tr_account_raw",
                side_effect=dapi.SafeExchangeError(
                    "INVALID_EXCHANGE_RESPONSE", "mock")):
            out = xg.tr_spot_summary()
            assert out["ok"] is False


def test_authenticated_summary_contract_no_secrets():
    import app as flask_app
    env = {"BINANCE_TR_API_KEY": "TKEY" + "x" * 20,
           "BINANCE_TR_API_SECRET": "TSEC" + "y" * 20}
    tr_payload = {"code": 0, "data": {"accountAssets": []}}
    flask_app.app.config["TESTING"] = True
    client = flask_app.app.test_client()
    import binance_tr_client as btr
    with mock.patch.dict(xg.os.environ, env, clear=True), \
         mock.patch.object(btr.BinanceTRClient, "get_spot_account",
                           return_value=tr_payload):
        r = client.get("/api/exchange/summary")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("Cache-Control", "")
    d = r.get_json()
    assert d["live_trading"] is False
    assert "global_futures" not in d   # Futures kaldırıldı
    blob = r.get_data(as_text=True)
    for s in env.values():
        assert s not in blob


def test_gateway_has_no_write_methods_in_source():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "exchange_gateway.py").read_text()
    for banned in ("requests.post", "requests.delete", "requests.put",
                   ".post(", ".delete(", ".put("):
        assert banned not in src, f"yazma metodu bulundu: {banned}"
