"""Binance Global SPOT adaptörü + ortak transport + futures kapatma testleri.

Gerçek secret gerektirmez; ağ istekleri mock'lanır.
"""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from unittest import mock

import pytest

import binance_global_client as bgc
import exchange_transport as xt

ROOT = Path(__file__).resolve().parent.parent


def _resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    return r


class TestSignature:
    def test_exact_hmac_over_query(self):
        sec = "global-secret"
        c = bgc.BinanceGlobalClient("k", sec)
        ts = 1753800000000
        qs = f"timestamp={ts}&recvWindow=5000"
        expected = hmac.new(sec.encode(), qs.encode(),
                            hashlib.sha256).hexdigest()
        assert c.signed_query(ts) == f"{qs}&signature={expected}"


class TestRequests:
    def test_account_header_path_server_timestamp(self):
        c = bgc.BinanceGlobalClient("GKEY", "GSEC")
        calls = []

        def fake_get(url, headers=None, timeout=None):
            calls.append((url, headers or {}))
            if bgc.PATH_TIME in url:
                return _resp({"serverTime": 1753800000000})
            return _resp({"balances": [], "canTrade": False})

        with mock.patch.object(c._session, "get", side_effect=fake_get):
            c.get_spot_account()
        url, headers = calls[-1]
        assert url.startswith("https://api.binance.com/api/v3/account?")
        assert "timestamp=1753800000000" in url
        assert "recvWindow=5000" in url
        assert "&signature=" in url
        assert headers.get("X-MBX-APIKEY") == "GKEY"

    def test_not_configured_fail_closed(self):
        c = bgc.BinanceGlobalClient("", "")
        with mock.patch.object(c._session, "get") as g:
            with pytest.raises(bgc.BinanceGlobalError) as ei:
                c.get_spot_account()
            g.assert_not_called()
        assert ei.value.kind == "NOT_CONFIGURED"

    def test_fapi_paths_blocked_before_network(self):
        c = bgc.BinanceGlobalClient("k", "s")
        with mock.patch.object(c._session, "get") as g:
            with pytest.raises(RuntimeError, match="GÜVENLİK BLOĞU"):
                c._get("/fapi/v2/account")
            g.assert_not_called()

    def test_no_fapi_in_source(self):
        src = (ROOT / "binance_global_client.py").read_text(encoding="utf-8")
        assert "fapi.binance.com" not in src
        # /fapi yalnız güvenlik bloğu satırlarında geçer
        for line in src.splitlines():
            if "/fapi" in line:
                assert "GÜVENLİK" in line or "startswith" in line \
                    or "YOKTUR" in line or "#" in line.split("/fapi")[0]

    def test_error_envelope_preserved(self):
        c = bgc.BinanceGlobalClient("k", "s")

        def fake_get(url, headers=None, timeout=None):
            if bgc.PATH_TIME in url:
                return _resp({"serverTime": 1})
            return _resp({"code": -2015, "msg": "Invalid API-key, IP, or "
                          "permissions."}, 401)

        with mock.patch.object(c._session, "get", side_effect=fake_get):
            with pytest.raises(bgc.BinanceGlobalError) as ei:
                c.get_spot_account()
        assert ei.value.exchange_code == -2015
        assert "Invalid API-key" in ei.value.exchange_message
        assert ei.value.http_status == 401

    def test_no_secret_in_error_text(self):
        c = bgc.BinanceGlobalClient("VERYSECRETKEY", "VERYSECRETSEC")

        def fake_get(url, headers=None, timeout=None):
            return _resp({"code": -1021, "msg": "ts"}, 400)

        with mock.patch.object(c._session, "get", side_effect=fake_get):
            with pytest.raises(bgc.BinanceGlobalError) as ei:
                c.get_spot_account()
        text = str(ei.value)
        assert "VERYSECRET" not in text and "signature=" not in text


class TestTransportPolicy:
    def test_session_trust_env_false_default_verify(self):
        c = bgc.BinanceGlobalClient("k", "s")
        assert c._session.trust_env is False
        assert c._session.verify is True

    def test_transport_shared_by_global_client(self):
        src = (ROOT / "binance_global_client.py").read_text(encoding="utf-8")
        assert "from exchange_transport import" in src

    def test_no_tls_workarounds_in_sources(self):
        for name in ("exchange_transport.py", "binance_global_client.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            assert "verify=False" not in src
            assert "certifi" not in src

    def test_transport_no_write_methods(self):
        src = (ROOT / "exchange_transport.py").read_text(encoding="utf-8")
        for banned in (".post(", ".put(", ".delete(", "requests.post"):
            assert banned not in src

    def test_transport_retries_only_safe_errors(self):
        s = xt.make_session()
        r401 = _resp({"code": -2015, "msg": "x"}, 401)
        with mock.patch.object(s, "get", return_value=r401) as g:
            status, body, _ = xt.safe_get_json(s, "u", "/p")
            assert status == 401 and g.call_count == 1  # 4xx retry YOK


class TestFuturesDisabled:
    def test_disabled_by_default_no_network(self, monkeypatch):
        import dashboard_api as dapi
        monkeypatch.delenv("ALPHA_FUTURES_ENABLED", raising=False)
        with mock.patch.object(dapi, "_signed_get") as g:
            for fn in (dapi.global_account, dapi.global_positions,
                       dapi.global_orders):
                model = fn()
                assert model["status"] == "DISABLED"
                assert model["error"]["code"] == "FUTURES_DISABLED"
            g.assert_not_called()

    def test_overview_no_warning_when_disabled(self, monkeypatch):
        import dashboard_api as dapi
        monkeypatch.delenv("ALPHA_FUTURES_ENABLED", raising=False)
        dapi.invalidate_caches()
        boom = dapi.SafeExchangeError("EXCHANGE_UNAVAILABLE", "mock")

        def fake(base, path, allowlist, key, secret, params=None,
                 timeout=10):
            raise boom

        monkeypatch.setattr(dapi, "_signed_get", fake)
        monkeypatch.setattr(dapi, "_public_get", fake)
        monkeypatch.setattr(dapi, "tr_movements_summary",
                            lambda: {"ok": True, "meta":
                                     {"freshness": "FRESH",
                                      "age_seconds": 1}})
        out = dapi.overview({"mode": "PAPER"})
        joined = " ".join(out["warnings"])
        assert "Futures" not in joined
        assert "Pozisyonlar" not in joined
        dapi.invalidate_caches()

    def test_enabled_flag_restores_futures(self, monkeypatch):
        import dashboard_api as dapi
        monkeypatch.setenv("ALPHA_FUTURES_ENABLED", "1")
        assert dapi.futures_enabled() is True


class TestGlobalCredAliases:
    def test_alias_names_accepted(self, monkeypatch):
        import dashboard_api as dapi
        for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "aliaskey")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "aliassec")
        assert dapi._global_creds() == ("aliaskey", "aliassec")

    def test_canonical_wins(self, monkeypatch):
        import dashboard_api as dapi
        monkeypatch.setenv("BINANCE_API_KEY", "canon")
        monkeypatch.setenv("BINANCE_API_SECRET", "canonsec")
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "alias")
        assert dapi._global_creds()[0] == "canon"
