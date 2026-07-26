"""Mission 1500.1 / Agent 04 — Risk Açıklama Motoru testleri."""

from datetime import datetime, timezone

import intelligence_models as im
import risk_explainer as rx
from intelligence_models import ConfidenceLevel, to_json

UTC = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)

ALL_FACTORS = ["margin_usage", "exposure", "concentration",
               "available_balance", "open_orders", "drawdown"]

FULL = {
    "ok": True, "risk_score": 26, "classification": "Kritik",
    "score_components": [
        {"factor": f, "penalty": p, "detail": f"{f} ölçümü"}
        for f, p in zip(ALL_FACTORS, (30, 12, 15, 15, 5, 15))],
    "alerts": [
        {"code": "HIGH_MARGIN_USAGE", "severity": "HIGH",
         "explanation": "Marj kullanımı %85."},
        {"code": "NEGATIVE_UNREALIZED_PNL", "severity": "INFO",
         "explanation": "PnL -3.00 USDT."}],
}

HEALTHY = {"ok": True, "risk_score": 100, "classification": "Mükemmel",
           "score_components": [], "alerts": []}

# Emir dili + kazanç garantisi iddiaları ("garanti değildir" gibi
# açık RED ifadeleri serbesttir).
FORBIDDEN_PHRASES = ("alın", "satın", "pozisyon açın", "pozisyon aç",
                     "hemen al", "hemen sat", "kazanç garantisi",
                     "garanti eder", "garantilidir", "kesinlikle kazan")


def _texts(insights):
    for i in insights:
        yield from (i.title, i.observation, i.reason, i.impact,
                    i.recommendation)


class TestCoverage:
    def test_every_risk_category_explained(self):
        ins = rx.explain_risk(FULL, UTC)
        codes = {i.code for i in ins}
        for f in ALL_FACTORS:
            assert f"RISK_FACTOR_{f.upper()}" in codes, f
        assert "RISK_ALERT_HIGH_MARGIN_USAGE" in codes
        assert "RISK_ALERT_NEGATIVE_UNREALIZED_PNL" in codes

    def test_score_trace_traceable(self):
        ins = rx.explain_risk(FULL, UTC)
        trace = next(i for i in ins if i.code == "RISK_SCORE_TRACE")
        assert "26/100" in trace.observation
        assert "92 puan" in trace.observation          # 30+12+15+15+5+15
        for f in ALL_FACTORS:
            assert f in trace.observation
        assert trace.confidence is ConfidenceLevel.HIGH

    def test_structure_complete(self):
        for i in rx.explain_risk(FULL, UTC):
            # Gözlem/Gerekçe/Etki/Öneri/Güven/Kanıt yapısı
            assert i.observation and i.reason and i.impact
            assert i.recommendation and i.confidence
            assert i.advisory_only is True


class TestUnknownScore:
    def test_null_score(self):
        ins = rx.explain_risk({"ok": True, "risk_score": None,
                               "classification": None,
                               "score_components": []})
        assert len(ins) == 1
        assert ins[0].code == "RISK_SCORE_UNKNOWN_EXPLAIN"
        assert ins[0].confidence is ConfidenceLevel.INSUFFICIENT_DATA

    def test_engine_unavailable(self):
        ins = rx.explain_risk(None)
        assert ins[0].code == "RISK_EXPLAIN_UNAVAILABLE"
        ins2 = rx.explain_risk({"ok": False})
        assert ins2[0].code == "RISK_EXPLAIN_UNAVAILABLE"

    def test_unknown_factor_not_fabricated(self):
        assert rx.explain_component({"factor": "bilinmeyen_faktor",
                                     "penalty": 5}) is None
        out = rx.explain_alerts([{"code": "UNKNOWN_ALERT"}])
        assert out == []


class TestMultipleFactors:
    def test_deterministic_order_and_output(self):
        a = [to_json(i) for i in rx.explain_risk(FULL, UTC)]
        b = [to_json(i) for i in rx.explain_risk(FULL, UTC)]
        assert a == b
        comp = [i.code for i in rx.explain_risk(FULL, UTC)
                if i.code.startswith("RISK_FACTOR_")]
        assert comp == sorted(comp)                    # faktörler sıralı

    def test_single_factor(self):
        one = {"ok": True, "risk_score": 85, "classification": "İyi",
               "score_components": [{"factor": "concentration",
                                     "penalty": 15,
                                     "detail": "Tek pozisyon %45"}]}
        ins = rx.explain_risk(one, UTC)
        c = next(i for i in ins if i.code == "RISK_FACTOR_CONCENTRATION")
        assert "yoğunluk eşiğinin üzerindedir" in c.reason
        assert "orantısız etkileyebilir" in c.impact
        assert "yeniden değerlendirilebilir" in c.recommendation


class TestHealthy:
    def test_healthy_portfolio(self):
        ins = rx.explain_risk(HEALTHY, UTC)
        assert len(ins) == 1
        t = ins[0]
        assert t.code == "RISK_SCORE_TRACE"
        assert "100/100" in t.observation
        assert "Hiçbir ceza faktörü tetiklenmedi" in t.observation
        assert "garanti değildir" in t.impact          # kesinlik iddiası yok


class TestEvidence:
    def test_evidence_fields_valid(self):
        for i in rx.explain_risk(FULL, UTC):
            for e in i.evidence:
                assert e.source == "risk_engine"
                assert e.field in im.ALLOWED_EVIDENCE_FIELDS["risk_engine"]

    def test_score_evidence_unmodified(self):
        ins = rx.explain_risk(FULL, UTC)
        trace = next(i for i in ins if i.code == "RISK_SCORE_TRACE")
        ev = {e.field: e.value for e in trace.evidence}
        assert ev["score"] == 26                       # aynen aktarım
        assert ev["classification"] == "Kritik"


class TestLanguage:
    def test_no_order_language_or_guarantees(self):
        for scenario in (FULL, HEALTHY):
            for text in _texts(rx.explain_risk(scenario, UTC)):
                low = text.lower()
                for phrase in FORBIDDEN_PHRASES:
                    assert phrase not in low, (phrase, text)

    def test_turkish_default(self):
        ins = rx.explain_risk(FULL, UTC)
        joined = " ".join(_texts(ins))
        assert "ölçüldü" in joined and "önerilebilir" not in ""  # Türkçe
        assert "operatör" in joined.lower()

    def test_no_external_modules(self):
        import inspect
        src = inspect.getsource(rx)
        for banned in ("random", "requests", "flask", "openai",
                       "anthropic", "urllib"):
            assert banned not in src, banned
