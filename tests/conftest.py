"""Test tabanı varsayılanları.

Futures panosu üretimde VARSAYILAN olarak devre dışıdır (Spot-only).
Eski görev testleri Futures davranışını mock'lu olarak doğrular; bu
fixture test ortamında bayrağı açar. Devre dışı davranışını sınayan
testler bayrağı kendi içinde silip/kapatıp doğrular.
"""
import pytest


@pytest.fixture(autouse=True)
def _futures_enabled_for_legacy_tests(monkeypatch):
    monkeypatch.setenv("ALPHA_FUTURES_ENABLED", "1")
    # Testler ASLA gerçek Global creds ile ağa çıkmamalı: ortamdaki
    # gerçek alias secret'ları test tabanından silinir (testler kendi
    # sahte anahtarlarını monkeypatch.setenv ile koyar).
    for key in ("BINANCE_GLOBAL_API_KEY", "BINANCE_GLOBAL_API_SECRET",
                "BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key"):
        monkeypatch.delenv(key, raising=False)
    yield
