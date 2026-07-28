"""Task 59 — Fiyatlanamayan varlık listesi sessizce kaybolmasın (regresyon).

Task 56 ile eklenen şeffaflık alanları:
- dashboard_api.global_spot_account → unpriced_holdings / unpriced_count
- risk_api.exposure() / summary(persist=False) → unpriced_assets
Bu testler ileride bir refactor'un bu listeleri sessizce boşaltmasını önler.
Ağ isteği asla çıkmaz (mock).
"""

from unittest import mock

import pytest

import dashboard_api as dapi
import risk_api


# ── dashboard_api katmanı: mock borsa yanıtları ─────────────────────────────

ACC_WITH_OBSCURE = {"canTrade": True, "balances": [
    {"asset": "USDT", "free": "100", "locked": "0"},
    {"asset": "BTC", "free": "0.01", "locked": "0"},
    {"asset": "OBSCURE", "free": "5", "locked": "2"},
]}
ACC_PRICED_ONLY = {"canTrade": True, "balances": [
    {"asset": "USDT", "free": "100", "locked": "0"},
    {"asset": "BTC", "free": "0.01", "locked": "0"},
]}
TICKER = [{"symbol": "BTCUSDT", "price": "50000"}]


def _mock_spot(monkeypatch, account=ACC_WITH_OBSCURE, ticker=TICKER):
    dapi.invalidate_caches()

    def fake_signed(base, path, allowlist, key, secret,
                    params=None, timeout=10):
        if ("GET", path) not in allowlist:
            raise RuntimeError("allowlist ihlali")
        if path == "/api/v3/account":
            return account
        raise dapi.SafeExchangeError("EXCHANGE_UNAVAILABLE", "mock yok")

    def fake_public(base, path, allowlist, params=None, timeout=10):
        if ("GET", path) not in allowlist:
            raise RuntimeError("allowlist ihlali")
        return ticker

    monkeypatch.setattr(dapi, "_signed_get", fake_signed)
    monkeypatch.setattr(dapi, "_public_get", fake_public)
    for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET"):
        monkeypatch.setenv(k, "x" * 20)


@pytest.fixture(autouse=True)
def _clean_cache():
    dapi.invalidate_caches()
    yield
    dapi.invalidate_caches()


class TestGlobalSpotUnpricedFields:
    def test_missing_ticker_symbol_fills_unpriced(self, monkeypatch):
        _mock_spot(monkeypatch)
        model = dapi.global_spot_account()
        assert model["ok"] is True
        assert model["valuation"] == "PARTIAL"
        assert model["unpriced_count"] == 1
        assert model["unpriced_holdings"] == [
            {"asset": "OBSCURE", "amount": "7", "value_usdt": None}]

    def test_unpriced_fields_always_present_in_model(self, monkeypatch):
        # Alanlar refactor'da sessizce silinmesin: FULL fiyatlamada bile
        # anahtarlar mevcut olmalı.
        _mock_spot(monkeypatch, account=ACC_PRICED_ONLY)
        model = dapi.global_spot_account()
        assert "unpriced_holdings" in model
        assert "unpriced_count" in model

    def test_full_valuation_empty_unpriced(self, monkeypatch):
        _mock_spot(monkeypatch, account=ACC_PRICED_ONLY)
        model = dapi.global_spot_account()
        assert model["valuation"] == "FULL"
        assert model["unpriced_holdings"] == []
        assert model["unpriced_count"] == 0


# ── risk_api katmanı: global_spot_account modeli mock'lanır ────────────────

SPOT_PARTIAL = {
    "ok": True,
    "total_spot_value_usdt": "600",
    "valuation": "PARTIAL",
    "usdt_free": "100",
    "usdt_locked": "0",
    "top_holdings": [
        {"asset": "BTC", "amount": "0.01", "value_usdt": "500"},
        {"asset": "USDT", "amount": "100", "value_usdt": "100"},
        {"asset": "OBSCURE", "amount": "7", "value_usdt": None},
    ],
    "unpriced_holdings": [
        {"asset": "OBSCURE", "amount": "7", "value_usdt": None}],
    "unpriced_count": 1,
}

SPOT_FULL = {
    "ok": True,
    "total_spot_value_usdt": "600",
    "valuation": "FULL",
    "usdt_free": "100",
    "usdt_locked": "0",
    "top_holdings": [
        {"asset": "BTC", "amount": "0.01", "value_usdt": "500"},
        {"asset": "USDT", "amount": "100", "value_usdt": "100"},
    ],
    "unpriced_holdings": [],
    "unpriced_count": 0,
}

TR_FAIL = {"ok": False}


def _patched(spot):
    return (mock.patch.object(dapi, "global_spot_account",
                              return_value=spot),
            mock.patch.object(dapi, "tr_account", return_value=TR_FAIL))


class TestRiskExposureUnpriced:
    def test_partial_lists_unpriced_assets(self):
        p1, p2 = _patched(SPOT_PARTIAL)
        with p1, p2:
            e = risk_api.exposure()
        assert e["ok"] is True
        assert e["spot_valuation"] == "PARTIAL"
        assert e["unpriced_assets"] == [
            {"asset": "OBSCURE", "quantity": "7", "value_usdt": None}]

    def test_full_valuation_empty_list(self):
        p1, p2 = _patched(SPOT_FULL)
        with p1, p2:
            e = risk_api.exposure()
        assert e["spot_valuation"] == "FULL"
        assert e["unpriced_assets"] == []


class TestRiskSummaryUnpriced:
    def test_summary_carries_unpriced_assets(self):
        p1, p2 = _patched(SPOT_PARTIAL)
        with p1, p2:
            s = risk_api.summary(persist=False)
        assert s["ok"] is True
        assert s["unpriced_assets"] == [
            {"asset": "OBSCURE", "quantity": "7", "value_usdt": None}]

    def test_summary_full_valuation_empty_list(self):
        p1, p2 = _patched(SPOT_FULL)
        with p1, p2:
            s = risk_api.summary(persist=False)
        assert s["unpriced_assets"] == []
