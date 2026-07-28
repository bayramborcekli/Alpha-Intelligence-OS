"""Binance TR temizlik görevi — tek adaptör + tek env yükleyici testleri.

Gerçek secret GEREKTİRMEZ; ağ istekleri mock'lanır.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from unittest import mock

import pytest
import requests

import binance_tr_client as btr
import local_env

ROOT = Path(__file__).resolve().parent.parent


def _resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    return r


# ── 1) exact HMAC query imzası ──────────────────────────────────────────────

class TestSignature:
    def test_exact_hmac_signature_over_query_string(self):
        secret = "test-secret-abc"
        c = btr.BinanceTRClient("key", secret)
        ts = 1753700000000
        qs = f"timestamp={ts}&recvWindow=5000"
        expected = hmac.new(secret.encode(), qs.encode(),
                            hashlib.sha256).hexdigest()
        assert c.signed_query(ts) == f"{qs}&signature={expected}"

    def test_signature_appended_last(self):
        c = btr.BinanceTRClient("key", "sec")
        assert c.signed_query(1).rsplit("&", 1)[1].startswith("signature=")


# ── 2) /open/v1/common/time timestamp parse ─────────────────────────────────

class TestServerTime:
    def test_time_parse_top_level_timestamp(self):
        c = btr.BinanceTRClient("k", "s")
        with mock.patch.object(c._session, "get",
                               return_value=_resp({"code": 0, "msg": "",
                                                   "data": None,
                                                   "timestamp": 1753700000123})):
            assert c.get_server_time() == 1753700000123

    def test_time_invalid_body_raises(self):
        c = btr.BinanceTRClient("k", "s")
        with mock.patch.object(c._session, "get",
                               return_value=_resp({"code": 0, "msg": "",
                                                   "data": None})):
            with pytest.raises(btr.BinanceTRError) as ei:
                c.get_server_time()
            assert ei.value.kind == "INVALID_RESPONSE"


# ── 3) signed account request header/path ───────────────────────────────────

class TestSignedAccountRequest:
    def test_header_path_and_query(self):
        c = btr.BinanceTRClient("MYKEY", "MYSEC")
        calls = []

        def fake_get(url, headers=None, timeout=None):
            calls.append((url, headers or {}))
            if btr.PATH_TIME in url:
                return _resp({"code": 0, "timestamp": 1753700000000})
            return _resp({"code": 0, "msg": "",
                          "data": {"accountAssets": []},
                          "timestamp": 1753700000001})

        with mock.patch.object(c._session, "get", side_effect=fake_get):
            c.get_spot_account()
        acc_url, acc_headers = calls[-1]
        assert acc_url.startswith(
            "https://www.binance.tr/open/v1/account/spot?")
        assert "timestamp=1753700000000" in acc_url
        assert "recvWindow=5000" in acc_url
        assert "&signature=" in acc_url
        assert acc_headers.get("X-MBX-APIKEY") == "MYKEY"
        # time çağrısı imzasız ve header'sız
        assert calls[0][1] == {}

    def test_not_configured_fails_closed_without_network(self):
        c = btr.BinanceTRClient("", "")
        with mock.patch.object(c._session, "get") as g:
            with pytest.raises(btr.BinanceTRError) as ei:
                c.get_spot_account()
            g.assert_not_called()
        assert ei.value.kind == "NOT_CONFIGURED"

    def test_allowlist_blocks_unknown_path_before_network(self):
        c = btr.BinanceTRClient("k", "s")
        with mock.patch.object(c._session, "get") as g:
            with pytest.raises(RuntimeError, match="GÜVENLİK BLOĞU"):
                c._get("/open/v1/orders")
            g.assert_not_called()


# ── 4) code=0 success parsing ───────────────────────────────────────────────

class TestSuccessParsing:
    def test_code_zero_returns_envelope(self):
        c = btr.BinanceTRClient("k", "s")
        payload = {"code": 0, "msg": "", "timestamp": 2,
                   "data": {"accountAssets": [
                       {"asset": "TRY", "free": "1.5", "locked": "0"}]}}

        def fake_get(url, headers=None, timeout=None):
            if btr.PATH_TIME in url:
                return _resp({"code": 0, "timestamp": 1})
            return _resp(payload)

        with mock.patch.object(c._session, "get", side_effect=fake_get):
            body = c.get_spot_account()
        assert body["data"]["accountAssets"][0]["asset"] == "TRY"


# ── 5) nonzero code → exchange_code + sanitize msg korunur ─────────────────

class TestErrorNormalization:
    def _client_with_account_response(self, payload, status=200):
        c = btr.BinanceTRClient("k", "s")

        def fake_get(url, headers=None, timeout=None):
            if btr.PATH_TIME in url:
                return _resp({"code": 0, "timestamp": 1})
            return _resp(payload, status)

        return c, fake_get

    def test_code_3700_preserved(self):
        c, fake = self._client_with_account_response(
            {"code": 3700, "msg": "Invalid API-key.", "data": None})
        with mock.patch.object(c._session, "get", side_effect=fake):
            with pytest.raises(btr.BinanceTRError) as ei:
                c.get_spot_account()
        assert ei.value.exchange_code == 3700
        assert ei.value.exchange_message == "Invalid API-key."

    def test_http_4xx_json_code_msg_preserved(self):
        c, fake = self._client_with_account_response(
            {"code": -1022, "msg": "Signature invalid"}, status=400)
        with mock.patch.object(c._session, "get", side_effect=fake):
            with pytest.raises(btr.BinanceTRError) as ei:
                c.get_spot_account()
        assert ei.value.exchange_code == -1022
        assert ei.value.http_status == 400

    def test_error_never_contains_secret_or_signature(self):
        c = btr.BinanceTRClient("SUPERKEY123456", "SUPERSEC654321")

        def fake_get(url, headers=None, timeout=None):
            if btr.PATH_TIME in url:
                return _resp({"code": 0, "timestamp": 1})
            return _resp({"code": 3702, "msg": "denied"}, 403)

        with mock.patch.object(c._session, "get", side_effect=fake_get):
            with pytest.raises(btr.BinanceTRError) as ei:
                c.get_spot_account()
        text = str(ei.value)
        assert "SUPERKEY" not in text and "SUPERSEC" not in text
        assert "signature=" not in text

    def test_sanitize_message_strips_and_caps(self):
        assert btr.sanitize_message("a\nb\x00c") == "abc"
        assert len(btr.sanitize_message("x" * 999)) == 200

    def test_dashboard_api_preserves_exchange_code(self):
        import dashboard_api as dapi
        err = dapi._tr_error_to_safe(btr.BinanceTRError(
            "EXCHANGE_ERROR", http_status=200, path=btr.PATH_SPOT_ACCOUNT,
            exchange_code=3700, exchange_message="Invalid API-key."))
        assert err.exchange_code == 3700
        assert err.exchange_message == "Invalid API-key."


# ── 6/7) env precedence ─────────────────────────────────────────────────────

class TestEnvPrecedence:
    def _write_env(self, tmp_path, content):
        f = tmp_path / ".env"
        f.write_text(content, encoding="utf-8")
        return f

    def test_local_project_env_overrides_stale_os_env(self, tmp_path,
                                                      monkeypatch):
        env_file = self._write_env(
            tmp_path,
            "BINANCE_TR_API_KEY=fresh-key\n"
            "BINANCE_TR_API_SECRET=fresh-sec\n"
            "OTHER_VAR=fromfile\n")
        monkeypatch.setattr(local_env, "ENV_FILE", env_file)
        monkeypatch.setattr(local_env, "is_replit", lambda: False)
        monkeypatch.setenv("BINANCE_TR_API_KEY", "stale-os-key")
        monkeypatch.setenv("OTHER_VAR", "fromos")
        local_env.reset_for_tests()
        try:
            sources = local_env.load_project_env()
            # credential: .env kazanır (stale OS değeri geçersiz)
            assert os.environ["BINANCE_TR_API_KEY"] == "fresh-key"
            assert sources["BINANCE_TR_API_KEY"] == "project_env"
            # credential dışı: process env korunur
            assert os.environ["OTHER_VAR"] == "fromos"
            assert sources["OTHER_VAR"] == "process_env"
        finally:
            local_env.reset_for_tests()

    def test_replit_process_env_wins_env_file_ignored(self, tmp_path,
                                                      monkeypatch):
        env_file = self._write_env(
            tmp_path, "BINANCE_TR_API_KEY=file-key\n")
        monkeypatch.setattr(local_env, "ENV_FILE", env_file)
        monkeypatch.setattr(local_env, "is_replit", lambda: True)
        monkeypatch.setenv("BINANCE_TR_API_KEY", "replit-secret-key")
        local_env.reset_for_tests()
        try:
            sources = local_env.load_project_env()
            assert os.environ["BINANCE_TR_API_KEY"] == "replit-secret-key"
            assert sources["BINANCE_TR_API_KEY"] == "process_env"
        finally:
            local_env.reset_for_tests()

    def test_global_alias_env_overrides_stale_os_env(self, tmp_path,
                                                     monkeypatch):
        # Global Spot alias adları (karışık büyük/küçük harf) da
        # Windows'ta .env > stale OS env kuralına tabidir.
        env_file = self._write_env(
            tmp_path,
            "BINANCE_GLOBAL_API_Key=fresh-gkey\n"
            "BINANCE_GLOBAL_Secret_Key=fresh-gsec\n")
        monkeypatch.setattr(local_env, "ENV_FILE", env_file)
        monkeypatch.setattr(local_env, "is_replit", lambda: False)
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "stale-os-gkey")
        local_env.reset_for_tests()
        try:
            sources = local_env.load_project_env()
            assert os.environ["BINANCE_GLOBAL_API_Key"] == "fresh-gkey"
            assert sources["BINANCE_GLOBAL_API_Key"] == "project_env"
        finally:
            local_env.reset_for_tests()

    def test_idempotent_single_load(self, tmp_path, monkeypatch):
        env_file = self._write_env(tmp_path, "BINANCE_TR_API_KEY=v1\n")
        monkeypatch.setattr(local_env, "ENV_FILE", env_file)
        monkeypatch.setattr(local_env, "is_replit", lambda: False)
        monkeypatch.delenv("BINANCE_TR_API_KEY", raising=False)
        local_env.reset_for_tests()
        try:
            local_env.load_project_env()
            env_file.write_text("BINANCE_TR_API_KEY=v2\n")
            local_env.load_project_env()  # ikinci çağrı NO-OP
            assert os.environ["BINANCE_TR_API_KEY"] == "v1"
        finally:
            local_env.reset_for_tests()

    def test_metadata_has_no_values(self, monkeypatch):
        monkeypatch.setenv("BINANCE_TR_API_KEY", "SHOULD-NOT-LEAK")
        meta = local_env.credential_metadata()
        blob = str(meta)
        assert "SHOULD-NOT-LEAK" not in blob
        assert meta["BINANCE_TR_API_KEY"]["present"] is True
        assert meta["BINANCE_TR_API_KEY"]["length"] == len("SHOULD-NOT-LEAK")


# ── 8) TLS: verify=False YOK; trust_env=False VAR ──────────────────────────

class TestTLSPolicy:
    def test_source_has_no_verify_false_or_custom_ca(self):
        src = (ROOT / "binance_tr_client.py").read_text(encoding="utf-8")
        assert "verify=False" not in src
        assert "certifi" not in src
        assert "REQUESTS_CA_BUNDLE" not in src

    def test_session_trust_env_false_default_verify(self):
        c = btr.BinanceTRClient("k", "s")
        assert c._session.trust_env is False
        assert c._session.verify is True

    def test_no_write_http_methods_in_source(self):
        src = (ROOT / "binance_tr_client.py").read_text(encoding="utf-8")
        for banned in (".post(", ".put(", ".delete(", "requests.post"):
            assert banned not in src

    def test_single_loader_no_setdefault_duplication(self):
        """app.py ve serve_windows.py kendi env loader'ını İÇERMEZ;
        yalnız local_env.load_project_env çağırır."""
        for name in ("app.py", "serve_windows.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            assert "_load_local_env" not in src
            assert "local_env.load_project_env()" in src
