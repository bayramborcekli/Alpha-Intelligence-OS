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
