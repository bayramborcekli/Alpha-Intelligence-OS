"""Task 53 — Risk skoru ve maruziyet Spot bakiyeleriyle gerçek veri üretir.

risk_api.exposure()/concentration()/summary() artık Binance Global Spot
hesabını (global_spot_account) ve Binance TR bakiyelerini kullanır;
health_score marj yerine Spot bakiye oranlarıyla çalışır.
"""

from unittest import mock

import dashboard_api as dapi
import risk_api


SPOT_OK = {
    "ok": True,
    "total_spot_value_usdt": "1000",
    "valuation": "FULL",
    "usdt_free": "300",
    "usdt_locked": "0",
    "top_holdings": [
        {"asset": "BTC", "amount": "0.01", "value_usdt": "700"},
        {"asset": "USDT", "amount": "300", "value_usdt": "300"},
    ],
}

TR_OK = {"ok": True, "usdt_free": "50", "usdt_locked": "0",
         "try_free": "1000", "try_locked": "0"}

FAIL = {"ok": False}


def _patched(spot=SPOT_OK, tr=TR_OK):
    return (mock.patch.object(dapi, "global_spot_account",
                              return_value=spot),
            mock.patch.object(dapi, "tr_account", return_value=tr))


class TestExposureSpot:
    def test_total_from_spot_balances(self):
        p1, p2 = _patched()
        with p1, p2:
            e = risk_api.exposure()
        assert e["ok"] is True
        assert e["total_spot_value_usdt"] == "1000.00"
        assert e["gross_exposure_usdt"] == "1000.00"
        assert e["spot_valuation"] == "FULL"
        assert e["cash_available_usdt"] == "300.00"
        assert e["by_asset"][0]["asset"] == "BTC"
        assert e["by_asset"][0]["exposure_pct"] == "70.00"

    def test_tr_balances_listed_as_quantity_only(self):
        p1, p2 = _patched()
        with p1, p2:
            e = risk_api.exposure()
        tr = e["binance_tr_holdings"]
        assert tr["stablecoins"] == [{"asset": "USDT", "quantity": "50"}]
        assert tr["other_assets"] == [{"asset": "TRY", "quantity": "1000"}]

    def test_spot_failure_yields_null_not_zero(self):
        p1, p2 = _patched(spot=FAIL, tr=FAIL)
        with p1, p2:
            e = risk_api.exposure()
        assert e["ok"] is True
        assert e["total_spot_value_usdt"] is None
        assert e["gross_exposure_usdt"] is None


class TestConcentrationSpot:
    def test_largest_holding_share(self):
        p1, p2 = _patched()
        with p1, p2:
            c = risk_api.concentration()
        assert c["largest_position"]["symbol"] == "BTC"
        assert c["single_position_pct"] == "70.00"
        assert any(w["level"] == "Critical" for w in c["warnings"])

    def test_empty_when_spot_unavailable(self):
        p1, p2 = _patched(spot=FAIL, tr=FAIL)
        with p1, p2:
            c = risk_api.concentration()
        assert c["largest_position"] is None
        assert c["single_position_pct"] is None


class TestSummarySpot:
    def test_real_score_from_spot_ratio(self):
        p1, p2 = _patched()
        with p1, p2:
            s = risk_api.summary(persist=False)
        assert s["risk_score"] is not None
        assert s["classification"] is not None
        assert s["total_spot_value_usdt"] == "1000.00"
        assert s["available_usdt"] == "300.00"
        # riskli varlık oranı %70 → margin_usage karşılığı ceza
        factors = {c["factor"] for c in s["score_components"]}
        assert "margin_usage" in factors
        assert "concentration" in factors

    def test_partial_valuation_penalized(self):
        spot = dict(SPOT_OK, valuation="PARTIAL")
        p1, p2 = _patched(spot=spot)
        with p1, p2:
            s = risk_api.summary(persist=False)
        assert any(c["factor"] == "exposure"
                   for c in s["score_components"])

    def test_null_score_when_spot_unavailable(self):
        p1, p2 = _patched(spot=FAIL, tr=FAIL)
        with p1, p2:
            s = risk_api.summary(persist=False)
        assert s["risk_score"] is None
        assert s["classification"] is None
