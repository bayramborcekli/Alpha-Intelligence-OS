"""Mission 1500.1 / Agent 06 — Intelligence Servis Katmanı testleri."""

import json
from datetime import datetime, timezone

from intelligence_service import IntelligenceService

UTC = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
ISO = "2026-07-26T11:59:58+00:00"


def _meta(freshness="FRESH", age=2.0):
    return {"source": "binance", "retrieved_at": ISO,
            "age_seconds": age, "freshness": freshness,
            "latency_ms": 10}


def _ga(ok=True, freshness="FRESH"):
    return {"ok": ok, "meta": _meta(freshness),
            "account": {"usdt_margin_balance": "1000",
                        "usdt_available_balance": "800",
                        "unrealized_pnl": "12.5"}} if ok else \
        {"ok": False, "meta": {"freshness": "OFFLINE"},
         "error": {"code": "EXCHANGE_TIMEOUT",
                   "message": "Borsa yanıt vermedi."}}


def _gp(ok=True, freshness="FRESH"):
    return {"ok": ok, "meta": _meta(freshness),
            "positions": [{"symbol": "BTCUSDT", "direction": "LONG",
                           "position_amt": "0.5", "mark_price": "400",
                           "unrealized_pnl": "12.5"}]} if ok else \
        {"ok": False, "error": {"code": "X", "message": "Hata."}}


def _rs(ok=True):
    return {"ok": ok, "meta": _meta(), "risk_score": 90,
            "classification": "Mükemmel", "score_components": [],
            "single_position_pct": "10.00",
            "exposure_pct_of_margin": "20.00"} if ok else {"ok": False}


def _al(ok=True):
    return {"ok": ok, "alerts": []} if ok else {"ok": False}


def _svc(ga=None, gp=None, rs=None, al=None):
    return IntelligenceService(
        account_provider=lambda: ga if ga is not None else _ga(),
        positions_provider=lambda: gp if gp is not None else _gp(),
        risk_provider=lambda: rs if rs is not None else _rs(),
        alerts_provider=lambda: al if al is not None else _al())


class TestMockability:
    def test_all_dependencies_injectable(self):
        s = _svc()
        snap = s.get_snapshot()
        assert snap["account"]["usdt_margin_balance"] == "1000"
        assert len(snap["positions"]) == 1
        assert snap["risk_summary"]["risk_score"] == 90

    def test_provider_exception_isolated(self):
        def boom():
            raise RuntimeError("api_key=SECRET123 leak")
        s = IntelligenceService(account_provider=boom,
                                positions_provider=lambda: _gp(),
                                risk_provider=lambda: _rs(),
                                alerts_provider=lambda: _al())
        out = s.get_summary(UTC)
        assert out["ok"] is True                     # servis çökmez
        blob = json.dumps(out, ensure_ascii=False)
        assert "SECRET123" not in blob               # ham hata sızmaz
        assert out["status"] == "PARTIAL"


class TestPartialFailure:
    def test_partial_provider_failure_visible(self):
        s = _svc(ga=_ga(ok=False))
        out = s.get_summary(UTC)
        assert out["status"] == "PARTIAL" and out["partial"] is True
        # Sterilize hata açıkça gösterilir, secret içermez
        assert out["source_errors"]["global_account"]["code"] == \
            "EXCHANGE_TIMEOUT"
        fr = {f["source"]: f["status"] for f in out["freshness"]}
        assert fr["global_account"] == "UNAVAILABLE"
        assert fr["global_positions"] == "OK"

    def test_all_down(self):
        s = _svc(ga=_ga(ok=False), gp=_gp(ok=False), rs=_rs(ok=False),
                 al=_al(ok=False))
        out = s.get_summary(UTC)
        assert out["status"] == "UNAVAILABLE"
        st = s.get_status()
        assert st["status"] == "UNAVAILABLE" and st["partial"] is True


class TestEmptyPortfolio:
    def test_empty_positions(self):
        gp = {"ok": True, "meta": _meta(), "positions": []}
        out = _svc(gp=gp).get_summary(UTC)
        assert out["portfolio_summary"]["open_position_count"] == 0
        codes = [i["code"] for i in out["insights"]]
        assert "NO_OPEN_POSITIONS" in codes


class TestNoRiskData:
    def test_risk_missing(self):
        out = _svc(rs=_rs(ok=False)).get_summary(UTC)
        assert out["risk_summary"]["risk_score"] is None   # uydurulmaz
        assert any(i["code"] == "RISK_EXPLAIN_UNAVAILABLE"
                   for i in out["risk_explanations"])
        assert out["status"] == "PARTIAL"


class TestFreshStale:
    def test_stale_source(self):
        out = _svc(ga=_ga(freshness="STALE")).get_summary(UTC)
        assert out["status"] == "STALE"
        fr = {f["source"]: f["status"] for f in out["freshness"]}
        assert fr["global_account"] == "STALE"

    def test_fresh_all_ok(self):
        st = _svc().get_status()
        assert st["status"] == "OK" and st["partial"] is False
        assert {x["source"] for x in st["sources"]} == \
            {"global_account", "global_positions", "risk_engine",
             "risk_engine_alerts"}


class TestSchema:
    def test_summary_schema(self):
        out = _svc().get_summary(UTC)
        for key in ("ok", "read_only", "advisory_only", "status",
                    "generated_at", "portfolio_summary", "risk_summary",
                    "insights", "recommendations", "risk_explanations",
                    "warnings", "freshness", "source_errors", "partial"):
            assert key in out, key
        assert out["advisory_only"] is True
        assert out["read_only"] is True
        json.dumps(out)                              # JSON-hazır

    def test_deterministic_with_fixed_time(self):
        a = json.dumps(_svc().get_summary(UTC), sort_keys=True)
        b = json.dumps(_svc().get_summary(UTC), sort_keys=True)
        assert a == b

    def test_helper_methods(self):
        s = _svc()
        assert isinstance(s.get_insights(UTC), list)
        recs = s.get_recommendations(UTC)
        assert isinstance(recs, list)
        assert all(r["advisory_only"] is True for r in recs)

    def test_no_write_or_secret_surface(self):
        import inspect
        import intelligence_service as isvc
        src = inspect.getsource(isvc)
        for banned in ("post(", "put(", "delete(", "API_KEY", "SECRET",
                       "_signed", "hmac"):
            assert banned not in src, banned


class TestReviewFixes:
    def test_risk_summary_without_meta_counts_ok(self):
        # risk_api.summary() meta.freshness içermez; ok yanıt OK sayılmalı
        rs = {"ok": True, "as_of": "2026-07-26T11:59:59+00:00",
              "risk_score": 90, "classification": "Mükemmel",
              "score_components": [], "single_position_pct": "10.00",
              "exposure_pct_of_margin": "20.00"}
        out = _svc(rs=rs).get_summary(UTC)
        fr = {f["source"]: f["status"] for f in out["freshness"]}
        assert fr["risk_engine"] == "OK"
        assert out["status"] == "OK"

    def test_alerts_source_first_class(self):
        s = _svc(al={"ok": False, "error": {"code": "X",
                                            "message": "Hata."}})
        st = s.get_status()
        src = {x["source"]: x["status"] for x in st["sources"]}
        assert src["risk_engine_alerts"] == "UNAVAILABLE"
        assert "risk_engine_alerts" in st["errors"]

    def test_status_consistent_between_apis(self):
        for kwargs in ({}, {"ga": _ga(ok=False)},
                       {"ga": _ga(freshness="STALE")},
                       {"ga": _ga(ok=False), "gp": _gp(ok=False),
                        "rs": _rs(ok=False), "al": _al(ok=False)}):
            s = _svc(**kwargs)
            assert s.get_summary(UTC)["status"] ==                 s.get_status()["status"], kwargs

    def test_provider_exception_sterile_error_recorded(self):
        def boom():
            raise RuntimeError("token=SECRET leak")
        s = IntelligenceService(account_provider=boom,
                                positions_provider=lambda: _gp(),
                                risk_provider=lambda: _rs(),
                                alerts_provider=lambda: _al())
        st = s.get_status()
        err = st["errors"]["global_account"]
        assert err["code"] == "PROVIDER_ERROR"
        assert "SECRET" not in json.dumps(st)

    def test_empty_positions_not_partial(self):
        gp = {"ok": True, "meta": _meta(), "positions": []}
        s = _svc(gp=gp)
        assert s.get_summary(UTC)["status"] == "OK"
        assert s.get_status()["status"] == "OK"

    def test_helpers_reuse_precomputed_summary(self):
        s = _svc()
        summary = s.get_summary(UTC)
        assert s.get_insights(summary=summary) == summary["insights"]
        assert s.get_recommendations(summary=summary) ==             summary["recommendations"]


class TestSpotOnlyDefaults:
    """Task 55: varsayılan (tombstone) sağlayıcılarla doğru çalışma.

    Spot-only mimaride global_account/global_positions kalıcı olarak
    NOT_AVAILABLE döner; bu kalıcı yokluk geçici arıza gibi raporlanmaz.
    """

    def _svc(self, rs=None, al=None):
        return IntelligenceService(
            risk_provider=lambda: rs if rs is not None else _rs(),
            alerts_provider=lambda: al if al is not None else _al())

    def test_summary_no_exception_and_ok(self):
        out = self._svc().get_summary(UTC)
        assert out["ok"] is True
        # Kaldırılmış kaynaklar durumu PARTIAL'a düşürmez
        assert out["status"] == "OK" and out["partial"] is False
        json.dumps(out)                              # JSON-hazır

    def test_removed_sources_not_in_freshness_or_errors(self):
        out = self._svc().get_summary(UTC)
        srcs = {f["source"] for f in out["freshness"]}
        assert "global_account" not in srcs
        assert "global_positions" not in srcs
        assert "global_account" not in out["source_errors"]
        assert "global_positions" not in out["source_errors"]

    def test_healthy_risk_yields_no_action_needed(self):
        # Futures girdileri null iken DATA_REFRESH değil NO_ACTION_NEEDED
        out = self._svc().get_summary(UTC)
        codes = [r["code"] for r in out["recommendations"]]
        assert "DATA_REFRESH" not in codes
        assert "NO_ACTION_NEEDED" in codes

    def test_status_endpoint_consistent(self):
        s = self._svc()
        assert s.get_summary(UTC)["status"] == s.get_status()["status"]
        st = s.get_status()
        assert st["status"] == "OK" and st["partial"] is False
        assert {x["source"] for x in st["sources"]} == \
            {"risk_engine", "risk_engine_alerts"}

    def test_risk_down_all_null_unavailable_no_exception(self):
        # Tüm girdiler null: risk de yoksa UNAVAILABLE + DATA_REFRESH
        s = self._svc(rs={"ok": False}, al={"ok": False})
        out = s.get_summary(UTC)
        assert out["status"] == "UNAVAILABLE"
        codes = [r["code"] for r in out["recommendations"]]
        assert "DATA_REFRESH" in codes and "NO_ACTION_NEEDED" not in codes
        assert s.get_status()["status"] == "UNAVAILABLE"

    def test_transient_failure_still_reported(self):
        # NOT_AVAILABLE dışındaki hatalar tombstone SAYILMAZ:
        # geçici arıza görünür kalır ve durumu düşürür.
        s = IntelligenceService(
            account_provider=lambda: _ga(ok=False),
            positions_provider=lambda: _gp(),
            risk_provider=lambda: _rs(),
            alerts_provider=lambda: _al())
        out = s.get_summary(UTC)
        assert out["status"] == "PARTIAL"
        assert out["source_errors"]["global_account"]["code"] == \
            "EXCHANGE_TIMEOUT"

    def test_core_summary_none_inputs_no_exception(self):
        # build_intelligence_summary null hesap/pozisyonla hata fırlatmaz
        import intelligence_api as icore
        summary = icore.build_intelligence_summary(
            account=None, positions=None, risk_summary=None,
            freshness_list=[], generated_at=UTC)
        assert summary is not None
