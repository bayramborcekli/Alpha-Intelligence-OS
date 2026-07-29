"""Windows SSL hatası tanılaması testleri (Task: kline SSL kesintisi).

Ağa hiç çıkmaz: requests.get monkeypatch ile sahtelenir.
Doğrulananlar:
- fetch_klines SSL hatasında Türkçe, kök nedeni ayırt eden mesaj üretir
- SSL doğrulaması asla kapatılmaz (verify=False çağrısı yok)
- Tek sembol SSL hatası fetch_klines_safe üzerinden None'a düşer
  (diğer semboller değerlendirilmeye devam edebilir)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "alpha20_v1"))

import alpha20  # noqa: E402


class TestDiagnoseSslError:
    def test_expired_cert_points_to_clock(self):
        msg = alpha20.diagnose_ssl_error(
            Exception("certificate verify failed: certificate has expired"))
        assert "saat" in msg.lower()

    def test_self_signed_points_to_proxy(self):
        msg = alpha20.diagnose_ssl_error(
            Exception("self signed certificate in certificate chain"))
        assert "proxy" in msg.lower() or "antivirüs" in msg.lower()

    def test_local_issuer_points_to_certifi(self):
        msg = alpha20.diagnose_ssl_error(
            Exception("unable to get local issuer certificate"))
        assert "certifi" in msg
        assert "INSTALL_WINDOWS.cmd" in msg

    def test_unknown_ssl_error_has_generic_guidance(self):
        msg = alpha20.diagnose_ssl_error(Exception("weird tls failure"))
        assert "INSTALL_WINDOWS.cmd" in msg
        assert "weird tls failure" in msg


class TestDiagnoseNetworkError:
    def test_dns_failure_points_to_dns(self):
        msg = alpha20.diagnose_network_error(
            requests.exceptions.ConnectionError(
                "HTTPSConnectionPool(host='fapi.binance.com', port=443): "
                "Max retries exceeded (Caused by NameResolutionError: "
                "getaddrinfo failed)"))
        assert "DNS" in msg
        assert "Ağ hatası" in msg

    def test_timeout_points_to_slow_network(self):
        msg = alpha20.diagnose_network_error(
            requests.exceptions.ConnectTimeout("Connection to fapi.binance.com timed out"))
        assert "zaman aşımı" in msg.lower()

    def test_read_timeout_points_to_slow_network(self):
        msg = alpha20.diagnose_network_error(
            requests.exceptions.ReadTimeout("Read timed out. (read timeout=15)"))
        assert "zaman aşımı" in msg.lower()

    def test_connection_refused_points_to_firewall(self):
        msg = alpha20.diagnose_network_error(
            requests.exceptions.ConnectionError(
                "[WinError 10061] No connection could be made because the "
                "target machine actively refused it"))
        assert "güvenlik duvarı" in msg.lower()

    def test_connection_reset_points_to_interruption(self):
        msg = alpha20.diagnose_network_error(
            requests.exceptions.ConnectionError("Connection reset by peer"))
        assert "sıfırlandı" in msg or "koptu" in msg

    def test_generic_connection_error_has_turkish_guidance(self):
        msg = alpha20.diagnose_network_error(
            requests.exceptions.ConnectionError("weird network failure"))
        assert "Ağ hatası" in msg
        assert "weird network failure" in msg


def _http_error(status: int) -> requests.exceptions.HTTPError:
    resp = requests.models.Response()
    resp.status_code = status
    return requests.exceptions.HTTPError(
        f"{status} Error for url", response=resp)


class TestDiagnoseHttpError:
    def test_429_points_to_rate_limit(self):
        msg = alpha20.diagnose_http_error(_http_error(429))
        assert "429" in msg
        assert "çok fazla istek" in msg.lower()
        assert "sıklığı" in msg or "bekle" in msg.lower()

    def test_418_points_to_ip_ban(self):
        msg = alpha20.diagnose_http_error(_http_error(418))
        assert "418" in msg
        assert "IP" in msg
        assert "yasak" in msg.lower() or "engellendi" in msg.lower()

    def test_5xx_points_to_binance_side(self):
        for status in (500, 502, 503):
            msg = alpha20.diagnose_http_error(_http_error(status))
            assert str(status) in msg
            assert "Binance" in msg
            assert "geçici" in msg.lower()

    def test_other_status_has_generic_turkish_guidance(self):
        msg = alpha20.diagnose_http_error(_http_error(403))
        assert "403" in msg
        assert "Binance hatası" in msg

    def test_missing_response_still_turkish(self):
        msg = alpha20.diagnose_http_error(
            requests.exceptions.HTTPError("no response attached"))
        assert "Binance hatası" in msg
        assert "no response attached" in msg


class TestFetchKlinesHttpHandling:
    def _mock_status(self, monkeypatch, status: int):
        class FakeResponse:
            status_code = status

            def raise_for_status(self):
                raise _http_error(status)

        monkeypatch.setattr(
            alpha20.requests, "get", lambda *a, **kw: FakeResponse())

    def test_429_raises_turkish_runtime_error(self, monkeypatch):
        self._mock_status(monkeypatch, 429)
        with pytest.raises(RuntimeError) as ei:
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert "çok fazla istek" in str(ei.value).lower()

    def test_418_raises_turkish_runtime_error(self, monkeypatch):
        self._mock_status(monkeypatch, 418)
        with pytest.raises(RuntimeError) as ei:
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert "IP" in str(ei.value)

    def test_503_raises_turkish_runtime_error(self, monkeypatch):
        self._mock_status(monkeypatch, 503)
        with pytest.raises(RuntimeError) as ei:
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert "geçici" in str(ei.value).lower()

    def test_fetch_klines_safe_returns_none_on_http_error(self, monkeypatch):
        self._mock_status(monkeypatch, 429)
        state: dict = {}
        assert alpha20.fetch_klines_safe("SOLUSDT", "15m", state=state) is None
        assert state["network_errors"] == 1


class TestMarketRegimeHttpHandling:
    def _mock_status(self, monkeypatch, status: int):
        import market_regime

        class FakeResponse:
            status_code = status

            def raise_for_status(self):
                raise _http_error(status)

        monkeypatch.setattr(
            market_regime.requests, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(market_regime.time, "sleep", lambda *_: None)

    def test_429_logs_turkish_message(self, monkeypatch, caplog):
        import market_regime
        self._mock_status(monkeypatch, 429)
        with caplog.at_level("WARNING", logger="market_regime"):
            assert market_regime._fetch_klines("SOLUSDT", "15m") is None
        assert any("çok fazla istek" in r.message.lower()
                   for r in caplog.records)

    def test_503_logs_turkish_message(self, monkeypatch, caplog):
        import market_regime
        self._mock_status(monkeypatch, 503)
        with caplog.at_level("WARNING", logger="market_regime"):
            assert market_regime._fetch_klines("SOLUSDT", "15m") is None
        assert any("geçici" in r.message.lower() for r in caplog.records)


class TestFetchKlinesNetworkHandling:
    def test_dns_error_raises_turkish_runtime_error(self, monkeypatch):
        def boom(*a, **kw):
            raise requests.exceptions.ConnectionError("getaddrinfo failed")
        monkeypatch.setattr(alpha20.requests, "get", boom)
        with pytest.raises(RuntimeError) as ei:
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert "DNS" in str(ei.value)

    def test_timeout_raises_turkish_runtime_error(self, monkeypatch):
        def boom(*a, **kw):
            raise requests.exceptions.ReadTimeout("Read timed out.")
        monkeypatch.setattr(alpha20.requests, "get", boom)
        with pytest.raises(RuntimeError) as ei:
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert "zaman aşımı" in str(ei.value).lower()

    def test_fetch_klines_safe_returns_none_on_network_error(self, monkeypatch):
        def boom(*a, **kw):
            raise requests.exceptions.ConnectionError("getaddrinfo failed")
        monkeypatch.setattr(alpha20.requests, "get", boom)
        state: dict = {}
        assert alpha20.fetch_klines_safe("SOLUSDT", "15m", state=state) is None
        assert state["network_errors"] == 1


class TestMarketRegimeNetworkHandling:
    def test_dns_error_logs_turkish_message(self, monkeypatch, caplog):
        import market_regime

        def boom(*a, **kw):
            raise requests.exceptions.ConnectionError("getaddrinfo failed")
        monkeypatch.setattr(market_regime.requests, "get", boom)
        monkeypatch.setattr(market_regime.time, "sleep", lambda *_: None)
        with caplog.at_level("WARNING", logger="market_regime"):
            assert market_regime._fetch_klines("SOLUSDT", "15m") is None
        assert any("DNS" in r.message for r in caplog.records)

    def test_timeout_logs_turkish_message(self, monkeypatch, caplog):
        import market_regime

        def boom(*a, **kw):
            raise requests.exceptions.ReadTimeout("Read timed out.")
        monkeypatch.setattr(market_regime.requests, "get", boom)
        monkeypatch.setattr(market_regime.time, "sleep", lambda *_: None)
        with caplog.at_level("WARNING", logger="market_regime"):
            assert market_regime._fetch_klines("SOLUSDT", "15m") is None
        assert any("zaman aşımı" in r.message.lower() for r in caplog.records)


class TestFetchKlinesSslHandling:
    def test_ssl_error_raises_turkish_runtime_error(self, monkeypatch):
        def boom(*a, **kw):
            raise requests.exceptions.SSLError(
                "certificate verify failed: unable to get local issuer certificate")
        monkeypatch.setattr(alpha20.requests, "get", boom)
        with pytest.raises(RuntimeError) as ei:
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert "SSL hatası" in str(ei.value)
        assert "certifi" in str(ei.value)

    def test_fetch_klines_safe_returns_none_on_ssl_error(self, monkeypatch):
        def boom(*a, **kw):
            raise requests.exceptions.SSLError("certificate verify failed")
        monkeypatch.setattr(alpha20.requests, "get", boom)
        state: dict = {}
        assert alpha20.fetch_klines_safe("SOLUSDT", "15m", state=state) is None
        assert state["network_errors"] == 1

    def test_no_verify_false_in_kline_modules(self):
        root = Path(__file__).resolve().parent.parent
        for rel in ("alpha20_v1/alpha20.py", "alpha20_v1/market_regime.py"):
            text = (root / rel).read_text(encoding="utf-8")
            assert "verify=False" not in text, f"{rel} SSL doğrulamasını kapatıyor!"


def _http_error_with_headers(status: int, headers: dict | None = None):
    resp = requests.models.Response()
    resp.status_code = status
    if headers:
        resp.headers.update(headers)
    return requests.exceptions.HTTPError(
        f"{status} Error for url", response=resp), resp


class TestRateLimitBackoff:
    """429/418 geri çekilme davranışı — ağa hiç çıkmadan (monkeypatch)."""

    def _mock_status(self, monkeypatch, status: int, headers: dict | None = None,
                     counter: list | None = None):
        class FakeResponse:
            status_code = status

            def __init__(self):
                self.headers = dict(headers or {})

            def raise_for_status(self):
                exc, _ = _http_error_with_headers(status, headers)
                raise exc

        def fake_get(*a, **kw):
            if counter is not None:
                counter.append(1)
            return FakeResponse()

        monkeypatch.setattr(alpha20.requests, "get", fake_get)

    def test_429_sets_backoff_and_blocks_next_request(self, monkeypatch):
        calls: list = []
        self._mock_status(monkeypatch, 429, counter=calls)
        with pytest.raises(RuntimeError):
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert alpha20.rate_limit_remaining() > 0
        # İkinci çağrı ağa hiç çıkmamalı
        with pytest.raises(RuntimeError) as ei:
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert len(calls) == 1
        assert "Geri çekilme aktif" in str(ei.value)

    def test_429_respects_retry_after_header(self, monkeypatch):
        self._mock_status(monkeypatch, 429, headers={"Retry-After": "7"})
        with pytest.raises(RuntimeError):
            alpha20.fetch_klines("SOLUSDT", "15m")
        remaining = alpha20.rate_limit_remaining()
        assert 0 < remaining <= 7.5

    def test_429_backoff_escalates_without_retry_after(self):
        w1 = alpha20.register_rate_limit(429)
        # sayaç korunarak ikinci 429
        alpha20._rate_limit_state["blocked_until"] = 0.0
        w2 = alpha20.register_rate_limit(429)
        assert w1 == alpha20.RATE_LIMIT_DEFAULT_BACKOFF
        assert w2 == alpha20.RATE_LIMIT_DEFAULT_BACKOFF * 2

    def test_429_backoff_is_capped(self):
        for _ in range(20):
            alpha20._rate_limit_state["blocked_until"] = 0.0
            wait = alpha20.register_rate_limit(429)
        assert wait == alpha20.RATE_LIMIT_MAX_BACKOFF

    def test_418_blocks_requests_with_turkish_message(self, monkeypatch):
        calls: list = []
        self._mock_status(monkeypatch, 418, counter=calls)
        with pytest.raises(RuntimeError):
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert alpha20.rate_limit_remaining() > 0
        assert "IP yasağı" in alpha20.rate_limit_reason()
        with pytest.raises(RuntimeError) as ei:
            alpha20.fetch_klines("SOLUSDT", "15m")
        assert len(calls) == 1
        assert "IP yasağı" in str(ei.value)

    def test_418_respects_retry_after_header(self):
        _, resp = _http_error_with_headers(418, {"Retry-After": "120"})
        wait = alpha20.register_rate_limit(418, resp)
        assert wait == 120

    def test_success_resets_consecutive_429_counter(self):
        alpha20.register_rate_limit(429)
        alpha20.note_rate_limit_success()
        assert alpha20._rate_limit_state["consecutive_429"] == 0

    def test_market_regime_429_stops_retries_and_sets_backoff(
            self, monkeypatch, caplog):
        import market_regime
        calls: list = []

        class FakeResponse:
            status_code = 429
            headers: dict = {}

            def raise_for_status(self):
                raise _http_error(429)

        def fake_get(*a, **kw):
            calls.append(1)
            return FakeResponse()

        monkeypatch.setattr(market_regime.requests, "get", fake_get)
        monkeypatch.setattr(market_regime.time, "sleep", lambda *_: None)
        with caplog.at_level("WARNING"):
            assert market_regime._fetch_klines("SOLUSDT", "15m") is None
        # 429 sonrası yeniden deneme YOK (yasağı büyütmemek için)
        assert len(calls) == 1
        assert alpha20.rate_limit_remaining() > 0
        assert any("çok fazla istek" in r.message.lower()
                   for r in caplog.records)

    def test_market_regime_respects_active_backoff(self, monkeypatch, caplog):
        import market_regime
        calls: list = []
        alpha20.register_rate_limit(418)

        def fake_get(*a, **kw):
            calls.append(1)
            raise AssertionError("Geri çekilme sırasında ağa çıkılmamalı")

        monkeypatch.setattr(market_regime.requests, "get", fake_get)
        with caplog.at_level("WARNING", logger="market_regime"):
            assert market_regime._fetch_klines("SOLUSDT", "15m") is None
        assert len(calls) == 0
        assert any("GERİ ÇEKİLME" in r.message or "kaldı" in r.message
                   for r in caplog.records)
