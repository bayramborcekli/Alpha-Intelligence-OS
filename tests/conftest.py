"""Test tabanı varsayılanları.

Spot-only mimari: Futures kaldırıldı; testler /fapi'ye asla çıkmaz.
"""
import pytest


@pytest.fixture(autouse=True)
def _sanitize_real_creds(monkeypatch):
    # Testler ASLA gerçek Global creds ile ağa çıkmamalı: ortamdaki
    # gerçek alias secret'ları test tabanından silinir (testler kendi
    # sahte anahtarlarını monkeypatch.setenv ile koyar).
    for key in ("BINANCE_GLOBAL_API_KEY", "BINANCE_GLOBAL_API_SECRET",
                "BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key",
                "BINANCE_API_Key", "BINANCE_Secret_Key"):
        monkeypatch.delenv(key, raising=False)
    yield
