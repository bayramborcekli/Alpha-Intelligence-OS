"""Mission 1400 — salt-okunur borsa geçidi güvenlik testleri (ağ yok)."""
import json
from unittest import mock

import pytest

import exchange_gateway as xg


@pytest.fixture(autouse=True)
def _clean_cache():
    xg.clear_cache()
    yield
    xg.clear_cache()


def test_allowlist_blocks_non_listed_paths_before_network():
    with mock.patch.object(xg.requests, "get") as g:
        with pytest.raises(RuntimeError, match="GÜVENLİK BLOĞU"):
            xg._signed_get(xg.GLOBAL_BASE, "/fapi/v1/order",
                           xg.GLOBAL_ALLOWLIST, "k" * 20, "s" * 20)
        g.assert_not_called()


def test_fail_closed_without_secrets_no_network():
    with mock.patch.dict(xg.os.environ, {}, clear=True), \
         mock.patch.object(xg.requests, "get") as g:
        out = xg.exchange_summary()
        g.assert_not_called()
    assert out["global_futures"]["configured"] is False
    assert out["tr_spot"]["configured"] is False
    assert out["live_trading"] is False


def _fake_resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    return r


def test_no_secret_material_in_summary():
    env = {"BINANCE_API_KEY": "GKEY" + "x" * 20 + "GEND",
           "BINANCE_API_SECRET": "GSECRET" + "y" * 20,
           "BINANCE_TR_API_KEY": "TKEY" + "x" * 20 + "TEND",
           "BINANCE_TR_API_SECRET": "TSECRET" + "y" * 20}
    g_payload = [{"asset": "USDT", "balance": "5", "availableBalance": "5"}]
    tr_payload = {"code": 0, "data": {"accountAssets": [
        {"asset": "TRY", "free": "1.39", "locked": "0"}]}}
    import binance_tr_client as btr
    with mock.patch.dict(xg.os.environ, env, clear=True), \
         mock.patch.object(xg.requests, "get",
                           return_value=_fake_resp(g_payload)), \
         mock.patch.object(btr.BinanceTRClient, "get_spot_account",
                           return_value=tr_payload):
        out = xg.exchange_summary()
    blob = json.dumps(out)
    for secret in (env["BINANCE_API_KEY"], env["BINANCE_API_SECRET"],
                   env["BINANCE_TR_API_KEY"], env["BINANCE_TR_API_SECRET"]):
        assert secret not in blob, "secret yanıt gövdesine sızdı!"
    assert out["global_futures"]["ok"] and out["tr_spot"]["ok"]
    assert out["global_futures"]["key_masked"].count("…") == 1


def test_http_error_is_reported_not_raised():
    env = {"BINANCE_API_KEY": "k" * 20, "BINANCE_API_SECRET": "s" * 20}
    with mock.patch.dict(xg.os.environ, env, clear=True), \
         mock.patch.object(xg.requests, "get",
                           return_value=_fake_resp({}, status=401)):
        out = xg.global_futures_summary()
    assert out["ok"] is False and "401" in out["error"]


def test_cache_prevents_repeat_network_calls():
    env = {"BINANCE_API_KEY": "k" * 20, "BINANCE_API_SECRET": "s" * 20}
    payload = [{"asset": "USDT", "balance": "5", "availableBalance": "5"}]
    with mock.patch.dict(xg.os.environ, env, clear=True), \
         mock.patch.object(xg.requests, "get",
                           return_value=_fake_resp(payload)) as g:
        xg.global_futures_summary()
        xg.global_futures_summary()
        assert g.call_count == 1


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
    env = {"BINANCE_API_KEY": "k" * 20, "BINANCE_API_SECRET": "s" * 20,
           "BINANCE_TR_API_KEY": "t" * 20, "BINANCE_TR_API_SECRET": "u" * 20}
    bad = mock.Mock(status_code=200)
    bad.json.side_effect = ValueError("not json")
    weird = _fake_resp([{"balance": "abc"}])          # asset yok, sayı bozuk
    with mock.patch.dict(xg.os.environ, env, clear=True):
        with mock.patch.object(xg.requests, "get", return_value=bad):
            out = xg.global_futures_summary()
            assert out["ok"] is False
        xg.clear_cache()
        with mock.patch.object(xg.requests, "get", return_value=weird):
            out = xg.global_futures_summary()
            assert out["ok"] is False               # istisna fırlatılmaz
        xg.clear_cache()
        import binance_tr_client as btr
        with mock.patch.object(
                btr.BinanceTRClient, "get_spot_account",
                side_effect=btr.BinanceTRError(
                    "INVALID_RESPONSE", http_status=200,
                    path="/open/v1/account/spot")):
            out = xg.tr_spot_summary()
            assert out["ok"] is False


def test_authenticated_summary_contract_no_secrets():
    import app as flask_app
    env = {"BINANCE_API_KEY": "GKEY" + "x" * 20,
           "BINANCE_API_SECRET": "GSEC" + "y" * 20,
           "BINANCE_TR_API_KEY": "TKEY" + "x" * 20,
           "BINANCE_TR_API_SECRET": "TSEC" + "y" * 20}
    g_payload = [{"asset": "USDT", "balance": "5", "availableBalance": "5"}]
    tr_payload = {"code": 0, "data": {"accountAssets": []}}
    flask_app.app.config["TESTING"] = True
    client = flask_app.app.test_client()
    import binance_tr_client as btr
    with mock.patch.dict(xg.os.environ, env, clear=True), \
         mock.patch.object(xg.requests, "get",
                           return_value=_fake_resp(g_payload)), \
         mock.patch.object(btr.BinanceTRClient, "get_spot_account",
                           return_value=tr_payload):
        r = client.get("/api/exchange/summary")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("Cache-Control", "")
    d = r.get_json()
    assert d["live_trading"] is False
    assert d["global_futures"]["read_only"] is True
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
