"""Mission 1500.1 / Agent 02 — Intelligence veri sözleşmesi testleri.

Kapsam: yalnızca modeller (route/UI/exchange/geçmiş yok).
"""

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import intelligence_models as im
from intelligence_models import (
    ALLOWED_EVIDENCE_FIELDS, ConfidenceLevel, DataFreshness,
    IntelligenceEvidence, IntelligenceInsight, IntelligenceStatus,
    IntelligenceSummary, to_json,
)

UTC_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)


def _evidence(value=Decimal("123.456789012345678901")):
    return IntelligenceEvidence(
        source="global_account", field="usdt_margin_balance",
        value=value, unit="USDT", observed_at=UTC_NOW)


def _freshness(status=IntelligenceStatus.OK):
    return DataFreshness(status=status, observed_at=UTC_NOW,
                         age_seconds=Decimal("2.5"),
                         source="global_account", detail="Bağlı")


def _insight(**over):
    kw = dict(code="HIGH_MARGIN_USAGE_EXPLAIN", category="MARGIN",
              title="Marj kullanımı yüksek",
              observation="Marj kullanımı %75 ölçüldü.",
              reason="Kullanılabilir bakiye marjın %25'i.",
              impact="Ani hareketlerde tampon daralır.",
              recommendation="Maruziyeti gözden geçirmeniz önerilir.",
              confidence=ConfidenceLevel.HIGH,
              evidence=(_evidence(),), freshness=_freshness())
    kw.update(over)
    return IntelligenceInsight(**kw)


def _summary(**over):
    kw = dict(status=IntelligenceStatus.OK, generated_at=UTC_NOW,
              portfolio_summary={"usdt_margin_balance": Decimal("1000.10")},
              risk_summary={"score": 82, "classification": "İyi"},
              insights=(_insight(),),
              recommendations=("Konsantrasyonu izleyin.",),
              warnings=("Veri kısmi olabilir.",),
              freshness=(_freshness(),))
    kw.update(over)
    return IntelligenceSummary(**kw)


class TestEnums:
    def test_confidence_allowed_only(self):
        for v in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT_DATA"):
            assert ConfidenceLevel(v).value == v
        with pytest.raises(ValueError):
            ConfidenceLevel("CERTAIN")
        with pytest.raises(ValueError):
            _insight(confidence="belki")

    def test_status_allowed_only(self):
        for v in ("OK", "PARTIAL", "STALE", "UNAVAILABLE"):
            assert IntelligenceStatus(v).value == v
        with pytest.raises(ValueError):
            IntelligenceStatus("DOWN")
        with pytest.raises(ValueError):
            _summary(status="serbest metin")


class TestDecimal:
    def test_precision_preserved(self):
        d = _evidence().to_dict()
        assert d["value"] == "123.456789012345678901"   # kesinlik kaybı yok
        assert Decimal(d["value"]) == Decimal("123.456789012345678901")

    def test_never_float(self):
        j = to_json(_summary())
        parsed = json.loads(j)
        assert isinstance(
            parsed["portfolio_summary"]["usdt_margin_balance"], str)
        assert isinstance(parsed["insights"][0]["evidence"][0]["value"], str)
        with pytest.raises(TypeError):
            _evidence(value=1000.10)             # float reddedilir
        with pytest.raises(TypeError):
            _summary(portfolio_summary={"usdt_margin_balance": 1.5})
        with pytest.raises(TypeError):
            im._ser(0.1)

    def test_unknown_not_zeroed(self):
        d = _evidence(value=None).to_dict()
        assert d["value"] is None and d["value"] != 0
        f = DataFreshness(status="UNAVAILABLE", observed_at=None,
                          age_seconds=None, source="tr_account").to_dict()
        assert f["observed_at"] is None and f["age_seconds"] is None

    def test_non_finite_decimal_becomes_null(self):
        assert im._ser(Decimal("NaN")) is None
        assert im._ser(Decimal("Infinity")) is None


class TestAdvisoryOnly:
    def test_insight_cannot_be_false(self):
        with pytest.raises(ValueError):
            _insight(advisory_only=False)

    def test_summary_cannot_be_false(self):
        with pytest.raises(ValueError):
            _summary(advisory_only=False)

    def test_always_true_in_json(self):
        p = json.loads(to_json(_summary()))
        assert p["advisory_only"] is True
        assert all(i["advisory_only"] is True for i in p["insights"])


class TestForbiddenFields:
    def test_no_trade_instruction_fields_in_json(self):
        p = json.loads(to_json(_summary()))

        def keys(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield k.lower()
                    yield from keys(v)
            elif isinstance(o, list):
                for v in o:
                    yield from keys(v)
        ks = set(keys(p))
        assert not ks & im.FORBIDDEN_TRADE_FIELDS
        assert not ks & im.FORBIDDEN_SECRET_FIELDS

    def test_trade_field_in_summary_dict_rejected(self):
        with pytest.raises(ValueError):
            _summary(risk_summary={"order_action": "BUY"})
        with pytest.raises(ValueError):
            _summary(portfolio_summary={"api_key": "x"})

    def test_evidence_source_whitelist(self):
        with pytest.raises(ValueError):
            IntelligenceEvidence(source="binance_raw", field="anything",
                                 value=None, unit=None, observed_at=None)
        with pytest.raises(ValueError):
            IntelligenceEvidence(source="global_account", field="api_key",
                                 value=None, unit=None, observed_at=None)
        # İzinli alanlar keşifle doğrulanmış modele karşılık gelir
        assert "usdt_margin_balance" in \
            ALLOWED_EVIDENCE_FIELDS["global_account"]


class TestTimestamps:
    def test_utc_iso8601(self):
        p = json.loads(to_json(_summary()))
        assert p["generated_at"] == "2026-07-26T12:00:00+00:00"

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError):
            _summary(generated_at=datetime(2026, 7, 26, 12, 0, 0))
        with pytest.raises(ValueError):
            DataFreshness(status="OK",
                          observed_at=datetime(2026, 1, 1),
                          age_seconds=None, source="x_src")

    def test_non_utc_normalized_to_utc(self):
        from datetime import timedelta, timezone as tz
        ist = tz(timedelta(hours=3))
        s = _summary(generated_at=datetime(2026, 7, 26, 15, 0, tzinfo=ist))
        assert s.to_dict()["generated_at"] == "2026-07-26T12:00:00+00:00"


class TestDeterminism:
    def test_same_input_same_json(self):
        assert to_json(_summary()) == to_json(_summary())

    def test_schema_stable_sorted_keys(self):
        j = to_json(_summary())
        p = json.loads(j)
        assert list(p.keys()) == sorted(p.keys())
        assert set(p.keys()) == {
            "status", "generated_at", "portfolio_summary", "risk_summary",
            "insights", "recommendations", "warnings", "freshness",
            "advisory_only"}

    def test_turkish_characters_preserved(self):
        j = to_json(_insight(title="Düşüş ve marj — ölçüldü ğüşiöçİ"))
        assert "ğüşiöçİ" in j          # ensure_ascii=False
        assert json.loads(j)["title"].endswith("ğüşiöçİ")


class TestSafety:
    def test_empty_evidence_safe(self):
        i = _insight(evidence=(), freshness=None)
        d = i.to_dict()
        assert d["evidence"] == [] and d["freshness"] is None
        to_json(i)

    def test_invalid_category_rejected(self):
        with pytest.raises(ValueError):
            _insight(category="TRADE_SIGNAL")

    def test_models_are_immutable(self):
        s = _summary()
        with pytest.raises(Exception):
            s.status = IntelligenceStatus.STALE

    def test_no_markup_plain_text(self):
        # HTML girdisi düz metin olarak saklanır (Markup/SafeString yok)
        i = _insight(title="<b>başlık</b>")
        assert isinstance(i.title, str)
        assert json.loads(to_json(i))["title"] == "<b>başlık</b>"

    def test_module_has_no_exchange_or_route_imports(self):
        import inspect
        src = inspect.getsource(im)
        for banned in ("requests", "flask", "dashboard_api", "app.",
                       "urllib"):
            assert banned not in src, banned
