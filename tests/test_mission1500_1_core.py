"""Mission 1500.1 / Agent 03 — Deterministik Intelligence çekirdeği testleri."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

import intelligence_api as ia
from intelligence_models import (
    ConfidenceLevel, DataFreshness, IntelligenceStatus, to_json,
)

UTC = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)

ACCOUNT = {"usdt_margin_balance": "1000", "usdt_available_balance": "800",
           "unrealized_pnl": "12.5"}
POSITIONS = [
    {"symbol": "BTCUSDT", "direction": "LONG", "position_amt": "0.5",
     "mark_price": "400"},
    {"symbol": "ETHUSDT", "direction": "SHORT", "position_amt": "-2",
     "mark_price": "50"},
    {"symbol": "XRPUSDT", "direction": "FLAT", "position_amt": "0",
     "mark_price": "1"},
]
RISK = {"ok": True, "risk_score": 82, "classification": "İyi",
        "alerts": [{"code": "HIGH_EXPOSURE"}]}
FRESH = [DataFreshness(status="OK", observed_at=UTC,
                       age_seconds=Decimal("3"), source="global_account")]


def _summary(**over):
    kw = dict(account=ACCOUNT, positions=POSITIONS, risk_summary=RISK,
              freshness_list=FRESH, generated_at=UTC)
    kw.update(over)
    return ia.build_intelligence_summary(**kw)


class TestDeterminism:
    def test_same_input_same_output(self):
        assert to_json(_summary()) == to_json(_summary())

    def test_no_randomness_imports(self):
        import inspect
        src = inspect.getsource(ia)
        for banned in ("random", "uuid", "requests", "dashboard_api",
                       "flask", "openai", "anthropic"):
            assert banned not in src, banned

    def test_insights_sorted_by_code(self):
        codes = [i.code for i in _summary().insights]
        assert codes == sorted(codes)


class TestMissingData:
    def test_all_missing_unavailable(self):
        s = _summary(account=None, positions=None, risk_summary=None,
                     freshness_list=None)
        assert s.status is IntelligenceStatus.UNAVAILABLE
        # Hiçbir finansal değer uydurulmaz
        assert s.portfolio_summary["usdt_margin_balance"] is None
        assert s.risk_summary["risk_score"] is None
        assert all(i.confidence is ConfidenceLevel.INSUFFICIENT_DATA
                   for i in s.insights)
        assert s.warnings   # eksik veri açıkça uyarı olarak listelenir

    def test_partial_missing(self):
        s = _summary(risk_summary=None)
        assert s.status is IntelligenceStatus.PARTIAL
        assert any(i.code == "RISK_ENGINE_UNAVAILABLE" for i in s.insights)

    def test_null_score_not_fabricated(self):
        s = _summary(risk_summary={"ok": True, "risk_score": None,
                                   "classification": None, "alerts": []})
        assert s.risk_summary["risk_score"] is None
        assert any(i.code == "RISK_SCORE_UNKNOWN" and
                   i.confidence is ConfidenceLevel.INSUFFICIENT_DATA
                   for i in s.insights)


class TestZeroPortfolio:
    def test_no_positions(self):
        s = _summary(positions=[])
        codes = [i.code for i in s.insights]
        assert "NO_OPEN_POSITIONS" in codes
        assert "SINGLE_ASSET_CONCENTRATION" not in codes
        assert s.portfolio_summary["open_position_count"] == 0

    def test_zero_margin_no_division_error(self):
        s = _summary(account={"usdt_margin_balance": "0",
                              "usdt_available_balance": "0",
                              "unrealized_pnl": "0"})
        assert any(i.code == "CASH_RATIO_UNKNOWN" for i in s.insights)


class TestConcentration:
    def test_single_asset_high(self):
        pos = [{"symbol": "BTCUSDT", "direction": "LONG",
                "position_amt": "1", "mark_price": "300"}]
        ins = ia.analyze_positions(pos)
        c = next(i for i in ins if i.code == "SINGLE_ASSET_CONCENTRATION")
        assert "%100.00" in c.observation
        assert "yüksek" in c.impact.lower()
        ls = next(i for i in ins if i.code == "LONG_SHORT_EXPOSURE")
        assert "tek yönlü" in ls.impact

    def test_balanced_below_threshold(self):
        ins = ia.analyze_positions(POSITIONS)
        c = next(i for i in ins if i.code == "SINGLE_ASSET_CONCENTRATION")
        assert "%66.67" in c.observation   # 200/300 Decimal doğruluğu


class TestPnl:
    def test_negative(self):
        ins = ia.analyze_portfolio({**ACCOUNT, "unrealized_pnl": "-5.25"})
        p = next(i for i in ins if i.code == "UNREALIZED_PNL_STATUS")
        assert "zarar" in p.impact and "-5.25" in p.observation

    def test_positive(self):
        ins = ia.analyze_portfolio(ACCOUNT)
        p = next(i for i in ins if i.code == "UNREALIZED_PNL_STATUS")
        assert "kâr" in p.impact

    def test_unknown_pnl_not_zeroed(self):
        ins = ia.analyze_portfolio({"usdt_margin_balance": "100",
                                    "usdt_available_balance": "50"})
        p = next(i for i in ins if i.code == "UNREALIZED_PNL_UNKNOWN")
        assert p.confidence is ConfidenceLevel.INSUFFICIENT_DATA


class TestFreshness:
    def test_stale_source_flagged(self):
        fl = [DataFreshness(status="STALE", observed_at=UTC,
                            age_seconds=Decimal("900"),
                            source="global_positions")]
        ins = ia.analyze_freshness(fl)
        assert len(ins) == 1
        assert ins[0].code == "FRESHNESS_GLOBAL_POSITIONS"
        assert ins[0].confidence is ConfidenceLevel.MEDIUM
        s = _summary(freshness_list=fl)
        assert s.status is IntelligenceStatus.STALE

    def test_fresh_sources_silent(self):
        assert ia.analyze_freshness(FRESH) == []

    def test_wrong_type_rejected(self):
        with pytest.raises(TypeError):
            ia.analyze_freshness([{"status": "OK"}])


class TestPartialPositionData:
    def test_unknown_leg_excluded_not_zeroed(self):
        pos = [{"symbol": "BTCUSDT", "direction": "LONG",
                "position_amt": "1", "mark_price": "100"},
               {"symbol": "ETHUSDT", "direction": "SHORT",
                "position_amt": "2", "mark_price": None}]   # bilinmeyen
        ins = ia.analyze_positions(pos)
        codes = [i.code for i in ins]
        assert "POSITION_VALUE_UNKNOWN" in codes
        u = next(i for i in ins if i.code == "POSITION_VALUE_UNKNOWN")
        assert u.confidence is ConfidenceLevel.INSUFFICIENT_DATA
        assert "ETHUSDT" in u.observation
        # Bilinen bacak 0'a zorlanmış olsaydı long %33 olurdu; dışlandığı
        # için bilinen maruziyet %100 long raporlanır (kısmi olduğu açık).
        ls = next(i for i in ins if i.code == "LONG_SHORT_EXPOSURE")
        assert "%100.00" in ls.observation

    def test_all_unknown_degrades_deterministically(self):
        pos = [{"symbol": "BTCUSDT", "direction": "LONG",
                "position_amt": None, "mark_price": None}]
        ins = ia.analyze_positions(pos)
        codes = [i.code for i in ins]
        assert "POSITION_VALUE_UNKNOWN" in codes
        assert "SINGLE_ASSET_CONCENTRATION" not in codes
        assert "LONG_SHORT_EXPOSURE" not in codes
        assert [i.code for i in ins] == sorted(codes)

    def test_risk_score_passed_through_unmodified(self):
        ins = ia.analyze_risk({"ok": True, "risk_score": "82",
                               "classification": "İyi", "alerts": []})
        h = next(i for i in ins if i.code == "RISK_HEALTH_EXPLAIN")
        assert h.evidence[0].value == "82"    # dönüştürme yok


class TestDecimalAccuracy:
    def test_cash_ratio_decimal(self):
        ins = ia.analyze_portfolio(ACCOUNT)
        c = next(i for i in ins if i.code == "CASH_STABLECOIN_RATIO")
        assert "%80.00" in c.observation
        ev = {e.field: e.value for e in c.evidence}
        assert ev["usdt_margin_balance"] == Decimal("1000")
        assert isinstance(ev["usdt_margin_balance"], Decimal)

    def test_json_decimal_as_string(self):
        import json
        p = json.loads(to_json(_summary()))
        assert p["portfolio_summary"]["usdt_margin_balance"] == "1000"
        assert isinstance(p["portfolio_summary"]["unrealized_pnl"], str)

    def test_risk_score_consumed_not_recomputed(self):
        # Motorun skoru NE OLURSA OLSUN aynen yansıtılır (yeniden hesap yok)
        s = _summary(risk_summary={"ok": True, "risk_score": 7,
                                   "classification": "Kritik", "alerts": []})
        assert s.risk_summary["risk_score"] == 7
        assert s.risk_summary["classification"] == "Kritik"


class TestAdvisorySafety:
    def test_all_advisory(self):
        s = _summary()
        assert s.advisory_only is True
        assert all(i.advisory_only is True for i in s.insights)

    def test_no_forbidden_fields_in_json(self):
        to_json(_summary())   # yasaklı alan taraması içeride — hata yoksa OK

    def test_alerts_explained(self):
        s = _summary()
        a = next(i for i in s.insights if i.code == "ACTIVE_RISK_ALERTS")
        assert "HIGH_EXPOSURE" in a.observation
