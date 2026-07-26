"""Mission 1500.1 / Agent 05 — Tavsiye Motoru testleri."""

import json
from datetime import datetime, timezone
from decimal import Decimal

import recommendation_api as rec
from intelligence_models import DataFreshness

UTC = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)

ACCOUNT_LOW_CASH = {"usdt_margin_balance": "1000",
                    "usdt_available_balance": "100"}
ACCOUNT_HEALTHY = {"usdt_margin_balance": "1000",
                   "usdt_available_balance": "800"}
RISK_BAD = {"ok": True, "single_position_pct": "70.00",
            "exposure_pct_of_margin": "200.00"}
RISK_GOOD = {"ok": True, "single_position_pct": "10.00",
             "exposure_pct_of_margin": "30.00"}
ALERTS = [{"code": "HIGH_MARGIN_USAGE"}, {"code": "HIGH_EXPOSURE"}]
POS_LOSING = [{"symbol": "BTCUSDT", "direction": "LONG",
               "unrealized_pnl": "-5"},
              {"symbol": "ETHUSDT", "direction": "SHORT",
               "unrealized_pnl": "3"}]
def _f(source, status="OK", age="2"):
    return DataFreshness(status=status, observed_at=UTC,
                         age_seconds=Decimal(age), source=source)

FRESH_OK = [_f("global_account"), _f("global_positions"),
            _f("risk_engine")]

FORBIDDEN = ("alın", "satın", "pozisyon açın", "kaldıraç kullanın",
             "hemen al", "hemen sat", "sinyal", "hedef fiyat",
             "kazanç garantisi")
FORBIDDEN_KEYS = {"order_action", "side", "quantity_to_trade",
                  "target_price", "leverage_instruction", "quantity",
                  "price", "leverage", "symbol_to_trade"}


def _build(**over):
    kw = dict(account=ACCOUNT_LOW_CASH, positions=POS_LOSING,
              risk_summary=RISK_BAD, alerts=ALERTS,
              freshness_list=FRESH_OK, generated_at=UTC)
    kw.update(over)
    return rec.build_recommendations(**kw)


class TestPriority:
    def test_ordered_by_priority(self):
        r = _build()
        codes = [x["code"] for x in r["recommendations"]]
        assert codes == ["RISK_ALERT_REVIEW", "CONCENTRATION_REVIEW",
                         "EXPOSURE_REVIEW", "CASH_RATIO_REVIEW",
                         "POSITION_REVIEW"]
        prios = [x["priority"] for x in r["recommendations"]]
        assert prios == sorted(prios)
        assert r["recommendations"][0]["severity"] == "Yüksek"

    def test_deterministic(self):
        assert json.dumps(_build(), sort_keys=True) == \
            json.dumps(_build(), sort_keys=True)


class TestDedup:
    def test_duplicate_alerts_merged(self):
        r = _build(alerts=[{"code": "HIGH_EXPOSURE"},
                           {"code": "HIGH_EXPOSURE"},
                           {"code": "HIGH_MARGIN_USAGE"}])
        alert_recs = [x for x in r["recommendations"]
                      if x["code"] == "RISK_ALERT_REVIEW"]
        assert len(alert_recs) == 1                       # tek öneri
        assert "2 aktif uyarı" in alert_recs[0]["observation"]

    def test_each_code_once(self):
        codes = [x["code"] for x in _build()["recommendations"]]
        assert len(codes) == len(set(codes))


class TestInsufficientData:
    def test_all_missing(self):
        r = rec.build_recommendations(generated_at=UTC)
        codes = [x["code"] for x in r["recommendations"]]
        assert codes == ["DATA_REFRESH"]
        d = r["recommendations"][0]
        assert d["confidence"] == "INSUFFICIENT_DATA"
        assert "NO_ACTION_NEEDED" not in codes            # veri yokken verilmez

    def test_unavailable_source(self):
        fl = [DataFreshness(status="UNAVAILABLE", observed_at=None,
                            age_seconds=None, source="tr_account")]
        r = _build(freshness_list=fl)
        codes = [x["code"] for x in r["recommendations"]]
        assert "DATA_REFRESH" in codes
        # Güven veriden türetilir: kaynak erişilemezken HIGH olamaz
        assert all(x["confidence"] != "HIGH"
                   for x in r["recommendations"])

    def test_stale_downgrades_confidence(self):
        fl = [_f("global_account"), _f("global_positions"),
              _f("risk_engine", status="STALE", age="900")]
        r = _build(freshness_list=fl)
        codes = [x["code"] for x in r["recommendations"]]
        assert "STALE_DATA_WARNING" in codes
        conc = next(x for x in r["recommendations"]
                    if x["code"] == "CONCENTRATION_REVIEW")
        assert conc["confidence"] == "MEDIUM"


class TestNoActionNeeded:
    def test_healthy(self):
        r = rec.build_recommendations(
            account=ACCOUNT_HEALTHY,
            positions=[{"symbol": "BTCUSDT", "direction": "LONG",
                        "unrealized_pnl": "5"}],
            risk_summary=RISK_GOOD, alerts=[], freshness_list=FRESH_OK,
            generated_at=UTC)
        codes = [x["code"] for x in r["recommendations"]]
        assert codes == ["NO_ACTION_NEEDED"]
        n = r["recommendations"][0]
        assert n["severity"] == "Bilgi"
        assert "garanti değildir" in n["impact"]

    def test_not_emitted_with_findings(self):
        assert "NO_ACTION_NEEDED" not in \
            [x["code"] for x in _build()["recommendations"]]


class TestAdvisoryOnly:
    def test_flags(self):
        r = _build()
        assert r["advisory_only"] is True and r["read_only"] is True
        for x in r["recommendations"]:
            assert x["advisory_only"] is True
            # Zorunlu alanlar: başlık, gözlem, gerekçe, etki, öneri,
            # güven, kanıt yapısı, oluşturulma zamanı, tazelik
            for f in ("title", "observation", "reason", "impact",
                      "recommendation", "confidence", "evidence",
                      "generated_at", "freshness"):
                assert f in x, f
        assert r["generated_at"] == "2026-07-26T12:00:00+00:00"


class TestNoOrderLanguage:
    def test_no_order_phrases(self):
        r = _build()
        blob = json.dumps(r, ensure_ascii=False).lower()
        for phrase in FORBIDDEN:
            assert phrase not in blob, phrase

    def test_no_order_parameter_keys(self):
        def keys(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield k.lower()
                    yield from keys(v)
            elif isinstance(o, list):
                for v in o:
                    yield from keys(v)
        assert not set(keys(_build())) & FORBIDDEN_KEYS

    def test_no_external_modules(self):
        import inspect
        src = inspect.getsource(rec)
        for banned in ("random", "requests", "flask", "openai",
                       "urllib", "dashboard_api"):
            assert banned not in src, banned


class TestPerSourceConfidence:
    def test_no_freshness_metadata_never_high(self):
        # Tazelik kanıtı olmadan HIGH güven verilmez
        r = _build(freshness_list=[])
        assert all(x["confidence"] == "INSUFFICIENT_DATA"
                   for x in r["recommendations"])

    def test_confidence_follows_own_source(self):
        # Yalnızca hesap kaynağı taze: hesaba dayalı öneri HIGH,
        # risk motoruna dayalılar INSUFFICIENT_DATA olmalı
        r = _build(freshness_list=[_f("global_account")])
        by = {x["code"]: x["confidence"] for x in r["recommendations"]}
        assert by["CASH_RATIO_REVIEW"] == "HIGH"
        assert by["CONCENTRATION_REVIEW"] == "INSUFFICIENT_DATA"

    def test_unknown_pnl_not_zeroed(self):
        pos = [{"symbol": "BTCUSDT", "direction": "LONG",
                "unrealized_pnl": None},
               {"symbol": "ETHUSDT", "direction": "SHORT",
                "unrealized_pnl": "-1"}]
        r = _build(positions=pos)
        pr = next(x for x in r["recommendations"]
                  if x["code"] == "POSITION_REVIEW")
        assert "BTCUSDT" not in pr["observation"]    # bilinmeyen dahil değil
        dr = next(x for x in r["recommendations"]
                  if x["code"] == "DATA_REFRESH")
        assert "BTCUSDT" in dr["observation"]
        assert dr["confidence"] == "INSUFFICIENT_DATA"
