"""Hold Intelligence (PHI) gölge katmanı testleri.

Spec test listesi:
 1 Trend güçlenmesi doğru algılanıyor
 2 Trend zayıflaması doğru algılanıyor
 3 PHI deterministik
 4 Giveback doğru hesaplanıyor
 5 Captured ratio doğru
 6 Hold state doğru
 7 Exit state doğru
 8 Trend decay reason code doğru
 9 Shadow gerçek işlemi değiştirmiyor
10 Champion değişmiyor
11 LIVE ORDERS DISABLED
12 Restart sonrası state korunuyor
13 Strategy Lab çalışıyor
14 Look-ahead yok
15 Veri eksikse fail-closed
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))
sys.path.insert(0, str(ROOT))

import hold_intelligence as hi  # noqa: E402
import dual_model as dm  # noqa: E402


def _klines(trend="up", n=60, base=100.0, vol=100.0):
    """Deterministik sentetik 1m klines."""
    out = []
    price = base
    for i in range(n):
        if trend == "up":
            step = 0.15 + (0.05 if i > n - 15 else 0.0)
        elif trend == "down":
            step = -0.15 - (0.05 if i > n - 15 else 0.0)
        elif trend == "fade":   # önce yukarı, son 15 mum aşağı
            step = 0.2 if i < n - 15 else -0.25
        else:                    # flat
            step = 0.01 if i % 2 == 0 else -0.01
        price = price * (1 + step / 100)
        v = vol * (1.5 if trend == "up" and i > n - 10 else 1.0)
        out.append([0, price * 0.999, price * 1.002, price * 0.998,
                    price, v])
    return out


class TestTrendHealth:
    def test_strengthening_detected(self):            # spec 1
        th = hi.trend_health(_klines("up"))
        assert th is not None
        assert th["state"] in ("STRENGTHENING", "STABLE")
        assert th["score"] >= 50

    def test_weakening_detected(self):                 # spec 2
        th = hi.trend_health(_klines("fade"))
        assert th is not None
        assert th["state"] in ("WEAKENING", "BREAKING", "DEAD")
        assert th["score"] < 50

    def test_single_indicator_cannot_decide(self):
        th = hi.trend_health(_klines("up"))
        assert len(th["components"]) >= 7  # çok göstergeli oylama

    def test_insufficient_data_fail_closed(self):      # spec 15
        assert hi.trend_health([]) is None
        assert hi.trend_health(_klines("up", n=20)) is None
        assert hi.trend_health([[0, "x"]] * 50) is None


class TestRegime:
    def test_regimes_valid(self):
        r = hi.classify_regime(_klines("up"))
        assert r in hi.REGIMES
        assert hi.classify_regime([]) is None          # fail-closed

    def test_low_volatility(self):
        r = hi.classify_regime(_klines("flat"))
        assert r in ("LOW_VOLATILITY", "RANGE", "WEAK_TREND")


class TestPhi:
    def test_deterministic(self):                      # spec 3
        kl = _klines("up")
        th = hi.trend_health(kl)
        decay = hi.trend_decay_reasons(th)
        ss = {"tcp": 60.0, "pfs": 55.0}
        track = {"max_net_pnl": 0.5, "net_pnl": 0.4,
                 "max_net_at_sec": 120.0, "new_high_count": 3}
        pq = hi.profit_quality(track, 200.0)
        a = hi.compute_phi(ss, th, pq, decay)
        b = hi.compute_phi(ss, th, pq, decay)
        assert a == b and a is not None and 0 <= a <= 100

    def test_no_trend_health_none(self):               # spec 15
        assert hi.compute_phi({"tcp": 60}, None, None, []) is None

    def test_decay_penalizes(self):
        th = hi.trend_health(_klines("up"))
        clean = hi.compute_phi(None, th, None, [])
        dirty = hi.compute_phi(None, th, None,
                               ["EMA_COLLAPSING", "VWAP_LOST",
                                "MOMENTUM_LOST"])
        assert dirty < clean


class TestProfitQuality:
    def test_giveback_correct(self):                   # spec 4
        track = {"max_net_pnl": 1.0, "net_pnl": 0.4,
                 "max_net_at_sec": 60.0, "new_high_count": 2}
        pq = hi.profit_quality(track, 120.0)
        assert pq["giveback_pnl"] == pytest.approx(0.6)
        assert pq["giveback_ratio"] == pytest.approx(0.6)
        assert pq["time_since_peak_sec"] == pytest.approx(60.0)

    def test_captured_ratio_correct(self):             # spec 5
        track = {"max_net_pnl": 2.0, "net_pnl": 1.5,
                 "max_net_at_sec": 30.0, "new_high_count": 1}
        pq = hi.profit_quality(track, 60.0)
        assert pq["captured_ratio"] == pytest.approx(0.75)

    def test_never_profitable_no_fabrication(self):
        track = {"max_net_pnl": -0.2, "net_pnl": -0.3,
                 "max_net_at_sec": 10.0, "new_high_count": 0}
        pq = hi.profit_quality(track, 60.0)
        assert pq["captured_ratio"] is None
        assert pq["giveback_ratio"] is None

    def test_missing_track_fail_closed(self):          # spec 15
        assert hi.profit_quality(None) is None
        assert hi.profit_quality({"net_pnl": 1.0}) is None


class TestHoldState:
    def test_hold_states(self):                        # spec 6
        th = {"state": "STRENGTHENING", "score": 80}
        assert hi.hold_state(80.0, th, None, 0.5) == "HOLD_STRONG"
        assert hi.hold_state(60.0, th, None, 0.5) == "HOLD_NORMAL"
        assert hi.hold_state(48.0, th, None, 0.5) == "HOLD_WEAK"
        assert hi.hold_state(40.0, th, None, 0.5) == "EXIT_WATCH"

    def test_exit_states(self):                        # spec 7
        dead = {"state": "DEAD", "score": 10}
        brk = {"state": "BREAKING", "score": 30}
        assert hi.hold_state(50.0, dead, None, 0.5) == "EXIT_NOW"
        assert hi.hold_state(50.0, brk, None, 0.5) == "EXIT_READY"
        # giveback limiti aşımı + zayıflayan trend → EXIT_NOW
        weak = {"state": "WEAKENING", "score": 40}
        pq = {"giveback_ratio": 0.7}
        assert hi.hold_state(50.0, weak, pq, 0.5) == "EXIT_NOW"

    def test_fail_closed(self):                        # spec 15
        assert hi.hold_state(None, {"state": "STABLE"}, None, 0.5) \
            is None


class TestDecayReasons:
    def test_codes_valid_and_correct(self):            # spec 8
        th = hi.trend_health(_klines("fade"))
        codes = hi.trend_decay_reasons(th)
        assert codes and all(c in hi.DECAY_CODES for c in codes)

    def test_data_quality_when_missing(self):          # spec 15
        assert hi.trend_decay_reasons(None) == ["DATA_QUALITY"]

    def test_btc_reversal(self):
        th = hi.trend_health(_klines("up"), btc_change_pct=-1.2)
        assert "BTC_REVERSAL" in hi.trend_decay_reasons(th)

    def test_liquidity_lost(self):
        th = hi.trend_health(_klines("up"))
        assert "LIQUIDITY_LOST" in hi.trend_decay_reasons(
            th, liquidity_ok=False)


class TestAdaptiveHold:
    def test_strong_trend_more_tolerance(self):
        strong = hi.adaptive_giveback_limit(
            {"state": "STRENGTHENING", "score": 80},
            "STRONG_TREND", 70.0)
        weak = hi.adaptive_giveback_limit(
            {"state": "WEAKENING", "score": 30}, "RANGE", 30.0)
        assert strong > weak
        assert 0.10 <= weak <= 0.80 and 0.10 <= strong <= 0.80

    def test_fail_closed(self):                        # spec 15
        assert hi.adaptive_giveback_limit(None, "RANGE", 50.0) is None
        assert hi.adaptive_giveback_limit(
            {"state": "STABLE", "score": 55}, None, 50.0) is None


class TestVariants:
    def test_decisions(self):
        pq = {"giveback_ratio": 0.0}
        d = hi.variant_decisions("HOLD_STRONG", pq, 0.5)
        assert d == {"balanced": "HOLD", "conservative": "HOLD",
                     "aggressive": "HOLD"}
        d = hi.variant_decisions("EXIT_WATCH", pq, 0.5)
        assert d["conservative"] == "EXIT" and d["balanced"] == "HOLD"
        d = hi.variant_decisions("EXIT_READY", pq, 0.5)
        assert d["balanced"] == "EXIT" and d["aggressive"] == "HOLD"
        d = hi.variant_decisions("EXIT_NOW", pq, 0.5)
        assert d["aggressive"] == "EXIT"

    def test_none_state(self):
        d = hi.variant_decisions(None, None, None)
        assert all(v is None for v in d.values())


class TestTrackAndLookAhead:
    def test_update_track_and_new_highs(self):
        p = {"entry": 100.0, "quantity": 1.0, "opened_ts": 1000.0}
        hi.update_track(p, 101.0, 1060.0)
        tr1 = dict(p["hold_track"])
        hi.update_track(p, 100.5, 1120.0)
        tr2 = p["hold_track"]
        # zirve 101'de dondu; 100.5 yeni zirve DEĞİL (look-ahead yok)
        assert tr2["max_net_pnl"] == tr1["max_net_pnl"]
        assert tr2["max_net_at_sec"] == pytest.approx(60.0)
        assert tr2["net_pnl"] < tr1["net_pnl"]

    def test_no_look_ahead_variant_freeze(self):       # spec 14
        p = {"entry": 100.0, "quantity": 1.0, "opened_ts": 0.0}
        hi.update_track(p, 102.0, 60.0)
        hi.apply_variant_exits(p, {"balanced": "EXIT",
                                   "conservative": "HOLD",
                                   "aggressive": "HOLD"}, 60.0)
        frozen = p["hold_track"]["variant_exits"]["balanced"]["net_pnl"]
        # sonra fiyat yükselse de gölge çıkış SABİT kalır
        hi.update_track(p, 110.0, 120.0)
        hi.apply_variant_exits(p, {"balanced": "EXIT",
                                   "conservative": "HOLD",
                                   "aggressive": "HOLD"}, 120.0)
        assert p["hold_track"]["variant_exits"]["balanced"][
            "net_pnl"] == frozen

    def test_track_fail_closed(self):                  # spec 15
        assert hi.update_track({"entry": None}, 100.0, 0.0) is None


class TestHoldReview:
    def test_review_math(self):
        t = {"net_pnl": 0.5, "hold_track": {
            "max_net_pnl": 1.0,
            "variant_exits": {"balanced": {"net_pnl": 0.9,
                                           "at_sec": 100}}}}
        rv = hi.hold_review(t)
        assert rv["verdict"] == "GAVE_BACK"
        assert rv["captured_ratio"] == pytest.approx(0.5)
        assert rv["missed_net_pnl"] == pytest.approx(0.5)
        assert rv["early_exit"] == "NOT_PROVABLE"   # look-ahead yok
        v = rv["variants"]
        assert v["balanced"]["delta_vs_real"] == pytest.approx(0.4)
        # EXIT demeyen varyant gerçek sonucu alır
        assert v["aggressive"]["net_pnl"] == pytest.approx(0.5)

    def test_never_profitable(self):
        t = {"net_pnl": -0.3, "hold_track": {"max_net_pnl": -0.1}}
        assert hi.hold_review(t)["verdict"] == "NEVER_PROFITABLE"

    def test_missing_track_data_quality(self):         # spec 15
        assert hi.hold_review({"net_pnl": 0.1})["verdict"] == \
            "DATA_QUALITY"


class TestShadowAndMemoryFiles:
    def test_shadow_lock_and_trim(self, tmp_path, monkeypatch):
        p = tmp_path / "hold_shadow.jsonl"
        assert hi.append_shadow({"a": 1}, p)
        assert (tmp_path / "hold_shadow.jsonl.lock").exists()
        monkeypatch.setattr(hi, "SHADOW_MAX_BYTES", 100)
        monkeypatch.setattr(hi, "SHADOW_KEEP_LINES", 2)
        for i in range(30):
            assert hi.append_shadow({"i": i}, p)
        rows = hi.read_shadow(100, p)
        assert rows[-1]["i"] == 29 and len(rows) < 30

    def test_memory_restart_persistence(self, tmp_path, monkeypatch):
        # spec 12: restart sonrası state korunuyor (dosya tabanlı)
        monkeypatch.setattr(hi, "MEMORY_PATH",
                            tmp_path / "hold_memory.json")
        t = {"symbol": "AAAUSDT", "net_pnl": 0.5, "hold_minutes": 12.0,
             "hold_track": {"max_net_pnl": 1.0}}
        rv = hi.hold_review(t)
        assert hi.record_closed_trade(t, rv, "STRONG_TREND")
        # "restart": modül state'i değil dosya okunur
        mem = hi.read_memory()
        assert mem["symbols"]["AAAUSDT"]["n"] == 1
        assert mem["regimes"]["STRONG_TREND"]["n"] == 1
        assert mem["symbols"]["AAAUSDT"]["captured_ratio_sum"] == \
            pytest.approx(0.5)


class TestRealBehaviorUnchanged:
    def test_monitor_exit_rules_unchanged(self):       # spec 9
        src = (ROOT / "alpha20_v1/dual_model.py").read_text(
            encoding="utf-8")
        # Gerçek çıkış kuralları aynen durur
        for rule in ('if price >= p["tp"]:', 'elif price <= p["sl"]:',
                     'result = "TRAILING"', 'result = "TIME_EXIT"'):
            assert rule in src
        # PHI/hold_state gerçek karara bağlanamaz
        import re
        for m in re.finditer(r"phi|hold_state|hold_shadow", src):
            line = src[src.rfind("\n", 0, m.start()) + 1:
                       src.find("\n", m.end())]
            assert "result =" not in line

    def test_shadow_failure_does_not_break_monitor(self, monkeypatch):
        # update_track patlarsa gerçek monitör akışı sürer
        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            raise RuntimeError("gölge patladı")

        monkeypatch.setattr(hi, "update_track", _boom)
        state = {"positions": {"XUSDT": {
            "symbol": "XUSDT", "model": dm.MODEL_CORE, "side": "LONG",
            "entry": 100.0, "quantity": 1.0, "notional_usdt": 100.0,
            "tp": 101.0, "sl": 99.0, "trailing_pct": 0.5, "peak": 100.0,
            "trough": 100.0, "max_hold_minutes": 60,
            "opened_at": "2026-07-30T00:00:00+00:00",
            "opened_ts": 0.0, "confidence": 70, "net_edge_pct": 0.5,
            "execution_mode": "PAPER", "early_marks": {}}},
            "trades": [], "cooldowns": {}}

        def _load():
            return state

        def _upd(mut):
            mut(state)

        monkeypatch.setattr(dm, "_load_runtime", _load)
        monkeypatch.setattr(dm, "_update_runtime", _upd)
        monkeypatch.setattr(dm.log, "error", lambda *a, **k: None)
        cfg = dm.get_config()
        closed = dm.monitor_positions(lambda s: 101.5, cfg, now=60.0)
        assert calls["n"] >= 1          # gölge çağrıldı ve patladı
        assert closed and closed[0]["result"] == "TP"  # gerçek akış TAM

    def test_champion_untouched(self):                 # spec 10
        src = (ROOT / "alpha20_v1/hold_intelligence.py").read_text(
            encoding="utf-8")
        # hold_intelligence hiçbir config/champion mekanizmasına
        # YAZMAZ: terfi/challenger kurulum yolları çağrılmaz,
        # smart_config'e dokunulmaz.
        for forbidden in ("smart_config", "install_as_challenger",
                          "promote", "set_config", "_update_config",
                          "import dual_learning"):
            assert forbidden not in src, forbidden
        rep = hi.build_report([])
        assert rep["contract"]["champion_changed"] is False

    def test_live_orders_disabled(self):               # spec 11
        rep = hi.build_report([])
        assert rep["contract"]["live_orders"] == "DISABLED"
        assert rep["contract"]["phi_changes_real_trades"] is False
        assert rep["contract"]["champion_changed"] is False
        src = (ROOT / "alpha20_v1/hold_intelligence.py").read_text(
            encoding="utf-8")
        assert "create_order" not in src and "order/place" not in src


class TestCycleAndReport:
    def test_evaluate_cycle_shadow_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hi, "SHADOW_PATH",
                            tmp_path / "hs.jsonl")
        p = {"symbol": "AAAUSDT", "model": "ALPHA_CORE_SCALP",
             "entry": 100.0, "quantity": 1.0, "opened_ts": 0.0,
             "tp": 101.0, "sl": 99.0,
             "shadow_scores": {"tcp": 60.0, "pfs": 55.0,
                               "local_top": {"breakout_bars_above": 0}}}
        hi.update_track(p, 100.8, 120.0)
        before = {k: p[k] for k in ("tp", "sl", "entry", "quantity")}
        snap = hi.evaluate_position_cycle(
            p, _klines("up"), 0.5, 120.0)
        assert snap is not None and snap["phi"] is not None
        assert snap["hold_state"] in hi.HOLD_STATES
        assert snap["regime"] in hi.REGIMES
        # spec 9: gerçek alanlara DOKUNULMADI
        assert {k: p[k] for k in before} == before
        rows = hi.read_shadow(10, tmp_path / "hs.jsonl")
        assert rows and rows[-1]["symbol"] == "AAAUSDT"

    def test_evaluate_cycle_no_klines_fail_closed(self, tmp_path,
                                                  monkeypatch):
        monkeypatch.setattr(hi, "SHADOW_PATH", tmp_path / "hs.jsonl")
        p = {"symbol": "AAAUSDT", "entry": 100.0, "quantity": 1.0,
             "opened_ts": 0.0}
        snap = hi.evaluate_position_cycle(p, [], None, 60.0)
        assert snap["phi"] is None and snap["hold_state"] is None
        assert snap["decay"] == ["DATA_QUALITY"]

    def test_build_report_contract(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hi, "SHADOW_PATH", tmp_path / "hs.jsonl")
        monkeypatch.setattr(hi, "MEMORY_PATH", tmp_path / "hm.json")
        t = {"symbol": "AAAUSDT", "net_pnl": 0.5, "trade_id": "x1",
             "hold_minutes": 10.0,
             "hold_track": {"max_net_pnl": 1.0, "variant_exits": {
                 "conservative": {"net_pnl": 0.9, "at_sec": 60}}}}
        t["hold_review"] = hi.on_trade_closed(t)
        rep = hi.build_report([t])
        assert rep["coverage"]["with_hold_review"] == 1
        rv = rep["real_vs_variants"]
        assert rv["real_net_pnl"] == pytest.approx(0.5)
        assert rv["variants"]["conservative"]["net_pnl"] == \
            pytest.approx(0.9)
        assert rv["variants"]["conservative"]["improved"] == 1
        assert rep["strategy_lab"]["scores_role"] == \
            "SHADOW_CHALLENGER_ONLY"

    def test_report_endpoint(self):                    # spec 13 dahil
        import app as app_mod
        app_mod.app.config["TESTING"] = True
        with app_mod.app.test_client() as client:
            with client.session_transaction() as s:
                s["logged_in"] = True
                s["username"] = "t"
            r = client.get("/api/hold-intelligence/report")
            r2 = client.get("/api/strategy-lab/status")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] and d["data"]["contract"][
            "live_orders"] == "DISABLED"
        # spec 13: Strategy Lab çalışıyor
        assert r2.status_code == 200 and r2.get_json()["ok"]
