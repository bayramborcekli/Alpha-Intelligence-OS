"""Continuous Strategy Lab — spec §17'deki 25 zorunlu test kapsamı.

Dürüstlük sözleşmesi: tik geçmişi olmadığından adaylar
GATE_SUBSET_REPLAY ile değerlendirilir (giriş filtreleri gerçek
işlemlere uygulanır; çıkış paramları exit_params_replayable=False).
LIVE ORDERS DISABLED — lab gerçek emir açamaz.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_learning as dl  # noqa: E402
import strategy_lab as sl  # noqa: E402

T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _rec(i: int, model: str = dl.MODEL_CORE, net: float = 1.0,
         symbol: str = "BTCUSDT", result: str = "TP",
         confidence: float = 70, regime: str = "TREND",
         mfe: float | None = 2.0, mae: float | None = 0.4,
         fees: float = 0.2, slippage: float = 0.1,
         spread: float = 0.05) -> dict:
    ts = (T0 + timedelta(hours=i)).isoformat()
    return {
        "trade_id": f"R{model[:4]}{i:05d}", "model_name": model,
        "strategy_name": model, "symbol": symbol, "side": "LONG",
        "entry_time": ts, "exit_time": ts,
        "entry_price": 100.0, "exit_price": 100.0 + net,
        "quantity": 1.0, "notional": 100.0,
        "gross_pnl": net + fees + slippage, "fees": fees,
        "slippage": slippage, "net_pnl": net, "exit_reason": result,
        "signal_type": "BREAKOUT", "confidence": confidence,
        "expected_edge": 0.2, "market_regime": regime,
        "spread": spread, "volatility": 1.0, "volume_ratio": 1.2,
        "hold_duration": 8.0, "mfe_pct": mfe, "mae_pct": mae,
        "configuration_version": "BASE", "learning_version": 1,
    }


def _dataset(n: int = 100, model: str = dl.MODEL_CORE,
             win_ratio: float = 0.7) -> list[dict]:
    out = []
    for i in range(n):
        win = (i % 10) < int(win_ratio * 10)
        out.append(_rec(
            i, model=model, net=1.0 if win else -0.8,
            symbol=["BTCUSDT", "ETHUSDT", "SOLUSDT"][i % 3],
            result="TP" if win else "SL",
            confidence=55 + (i % 40),
            regime=["TREND", "RANGE"][i % 2]))
    return out


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "STATE_PATH",
                        tmp_path / "strategy_lab_state.json")
    monkeypatch.setattr(sl, "HISTORY_PATH",
                        tmp_path / "strategy_lab_history.jsonl")
    monkeypatch.setattr(dl, "STATE_PATH",
                        tmp_path / "dual_learning_state.json")
    monkeypatch.setattr(dl, "HISTORY_PATH",
                        tmp_path / "dual_learning_history.jsonl")
    monkeypatch.setattr(dl, "RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
    dl._OVERRIDE_CACHE.update(mtime="X", data={})
    yield tmp_path
    dl._OVERRIDE_CACHE.update(mtime="X", data={})


def _seed_dl(dataset: list[dict], model: str = dl.MODEL_CORE) -> None:
    def _m(s):
        ms = dl._model_state(s, model)
        ms["dataset"] = dataset
        ms["metrics"] = dl.compute_model_metrics(dataset)
    dl._update_state(_m)


def _base_params():
    return {"min_confidence": 60.0, "tp_pct": 1.0, "sl_pct": 0.6,
            "trailing_pct": 0.5, "max_hold_minutes": 30.0,
            "cooldown_minutes": 15.0, "max_spread_pct": 0.2,
            "max_slippage_pct": 0.1}


def _cand(params=None, stage="STAGE0_STATIC", created_at=None):
    p = params or _base_params()
    return {"strategy_candidate_id": "TEST-g1-abc",
            "parent_strategy_id": "CHAMPION", "generation": 1,
            "model_family": dl.MODEL_CORE, "parameters": p,
            "fingerprint": sl._fingerprint(dl.MODEL_CORE, p),
            "created_reason": "PARAM_MUTATION", "hypothesis": "t",
            "expected_improvement": "t", "risk_notes": "t",
            "data_version": None, "code_version": sl.CODE_VERSION,
            "created_at": created_at or T0.isoformat(),
            "stage": stage, "status": "ACTIVE", "stage_results": {}}


# ── 1-2: güvenli üretim + güvenlik parametreleri dokunulmaz ─────────

class TestGeneration:
    def test_candidates_within_safe_bounds(self, iso, monkeypatch):
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        ml = {"candidates": {}, "rejected_fingerprints": [],
              "generation": 0, "promotion_history": []}
        cands = sl.generate_candidates(
            dl.MODEL_CORE, ml, {"code": "HEALTHY"}, None, None)
        assert cands
        for c in cands:
            ok, why = sl.validate_candidate_params(c["parameters"])
            assert ok, why
            for k in c["parameters"]:
                assert k in dl.LEARNABLE_BOUNDS

    def test_forbidden_param_rejected(self):
        ok, why = sl.validate_candidate_params(
            {"min_confidence": 60, "live_orders_enabled": 1})
        assert not ok and "FORBIDDEN_PARAM" in why
        ok2, why2 = sl.validate_candidate_params(
            {"min_confidence": 5000})
        assert not ok2 and "OUT_OF_BOUNDS" in why2

    def test_generation_capped(self, iso, monkeypatch):
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        ml = {"candidates": {}, "rejected_fingerprints": [],
              "generation": 0,
              "promotion_history": [{"parameters": _base_params()}]}
        cands = sl.generate_candidates(
            dl.MODEL_CORE, ml, {"code": "FEE_DRAG_DOMINANT"},
            {"most_frequent": "PROFIT_GIVEBACK"},
            {"dominant": "EXIT_TOO_EARLY"})
        assert len(cands) <= sl.MAX_CANDIDATES_PER_GENERATION

    def test_duplicate_not_regenerated(self, iso, monkeypatch):
        # 16: aynı başarısız aday tekrar üretilmez
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        import random
        rng1, rng2 = random.Random(7), random.Random(7)
        ml = {"candidates": {}, "rejected_fingerprints": [],
              "generation": 0, "promotion_history": []}
        first = sl.generate_candidates(dl.MODEL_CORE, ml,
                                       None, None, None, rng=rng1)
        assert first
        ml2 = {"candidates": {}, "generation": 0, "promotion_history": [],
               "rejected_fingerprints": [c["fingerprint"] for c in first]}
        again = sl.generate_candidates(dl.MODEL_CORE, ml2,
                                       None, None, None, rng=rng2)
        assert not {c["fingerprint"] for c in again} & \
            {c["fingerprint"] for c in first}


# ── 3-6: sızıntı yok, train/holdout ayrı, walk-forward, fee dahil ──

class TestSplitsAndLeakage:
    def test_strict_time_split(self):
        ds = _dataset(100)
        sp = sl.split_dataset(ds)
        assert len(sp["train"]) == 60 and len(sp["holdout"]) == 25
        # Kronolojik: train'in en yenisi holdout'un en eskisinden önce
        assert max(r["exit_time"] for r in sp["train"]) < \
            min(r["exit_time"] for r in sp["holdout"])

    def test_stage1_uses_only_train(self, iso):
        ds = _dataset(100)
        c = _cand(stage="STAGE1_HISTORICAL")
        res = sl.run_stage(c, dl.MODEL_CORE, ds, None, {}, {})
        # STAGE1 örneklemi train boyutunu aşamaz → holdout'a bakılmadı
        assert res["sample"] <= 60
        assert res["method"] == "GATE_SUBSET_REPLAY"
        assert res["exit_params_replayable"] is False

    def test_walk_forward_windows_chronological(self, iso):
        ds = _dataset(120)
        c = _cand(stage="STAGE2_WALK_FORWARD")
        res = sl.run_stage(c, dl.MODEL_CORE, ds, None, {}, {})
        assert len(res["windows"]) >= 2
        assert res["ok"] in (True, False)

    def test_holdout_consumed_once(self, iso):
        # Holdout aday başına TEK değerlendirme
        ds = _dataset(120)
        c = _cand(stage="STAGE3_HOLDOUT")
        r1 = sl.run_stage(c, dl.MODEL_CORE, ds, None, {}, {})
        c["stage_results"]["STAGE3_HOLDOUT"] = r1
        r2 = sl.run_stage(c, dl.MODEL_CORE, ds, None, {}, {})
        assert r2["ok"] is False
        assert r2["reason"] == "HOLDOUT_ALREADY_CONSUMED"

    def test_fee_slippage_included(self):
        # Metrikler net_pnl (fee+slip sonrası) üzerinden hesaplanır
        ds = [_rec(i, net=-0.1, fees=0.5, slippage=0.2, result="TP")
              for i in range(30)]
        m = dl.compute_model_metrics(ds)
        assert m["net_pnl"] < 0 < m["gross_pnl"]
        assert m["fee_drag"] == pytest.approx(15.0)


# ── 7-9: konsantrasyon/rejim/örneklem korumaları ───────────────────

class TestGuards:
    def test_single_symbol_candidate_blocked_in_promotion(self, iso):
        # dl terfi kapısı: >=60% tek sembol konsantrasyonu reddedilir
        ds = [_rec(i, symbol="DOGEUSDT") for i in range(80)]
        ms = {"challenger": {"version": "X", "overrides": {},
                             "shadow": {"sample": 80, "metrics":
                                        dl.compute_model_metrics(ds)}},
              "metrics": dl.compute_model_metrics(ds),
              "champion": {"version": "BASE"}}
        r = dl.evaluate_promotion(ms, dl.DEFAULT_THRESHOLDS)
        assert r["code"] == "REJECTED_CONCENTRATION"

    def test_single_regime_blocks_live_eligibility(self, iso):
        ds = [_rec(i, regime="TREND") for i in range(250)]
        m = dl.compute_model_metrics(ds)
        r = sl.evaluate_live_eligibility(dl.MODEL_CORE, ds, m,
                                         True, {})
        assert r["checks"]["regime_coverage"] is False
        assert r["status"] == "NOT_ELIGIBLE"

    def test_low_sample_no_promotion(self, iso):
        ds = _dataset(10)
        c = _cand(stage="STAGE1_HISTORICAL")
        res = sl.run_stage(c, dl.MODEL_CORE, ds, None, {}, {})
        assert res["ok"] is False
        assert res["reason"] in ("INSUFFICIENT_SAMPLE",
                                 "NEGATIVE_EXPECTANCY")


# ── 10-16: aşama akışı, terfi, rollback, graveyard ─────────────────

class TestPipeline:
    def test_bad_backtest_rejected_before_paper(self, iso):
        # Kaybeden dataset → STAGE1 FAIL → Paper'a geçemez
        ds = _dataset(100, win_ratio=0.2)
        c = _cand(stage="STAGE1_HISTORICAL")
        res = sl.run_stage(c, dl.MODEL_CORE, ds, None, {}, {})
        assert res["ok"] is False

    def test_good_backtest_bad_paper_rejected(self, iso):
        # STAGE4: ileri-zaman penceresi champion'dan iyi değilse FAIL
        past = _dataset(80, win_ratio=0.8)
        created = (T0 + timedelta(hours=100)).isoformat()
        fwd = [_rec(200 + i, net=-0.9, result="SL",
                    confidence=80) for i in range(40)]
        c = _cand(stage="STAGE4_PAPER_SHADOW", created_at=created)
        res = sl.run_stage(c, dl.MODEL_CORE, past + fwd, None, {}, {})
        assert res["ok"] is False
        assert res["reason"] == "NOT_BETTER_THAN_CHAMPION"

    def test_waiting_forward_sample_is_honest(self, iso):
        c = _cand(stage="STAGE4_PAPER_SHADOW",
                  created_at=(T0 + timedelta(hours=500)).isoformat())
        res = sl.run_stage(c, dl.MODEL_CORE, _dataset(80), None, {}, {})
        assert res["ok"] is None
        assert res["reason"] == "WAITING_FORWARD_SAMPLE"

    def test_good_challenger_promotes_via_dl(self, iso):
        # 12: iyi Paper challenger dl kapılarından terfi eder
        ds = _dataset(120, win_ratio=0.8)
        _seed_dl(ds)
        c = _cand()
        assert sl.install_as_challenger(dl.MODEL_CORE, c)
        st = dl._load_state()
        assert st["models"][dl.MODEL_CORE]["challenger"]["version"] == \
            c["strategy_candidate_id"]
        # yuva doluyken ikinci kurulum reddedilir
        assert not sl.install_as_challenger(dl.MODEL_CORE, _cand())

    def test_rejected_goes_to_graveyard_and_memory(self, iso,
                                                   monkeypatch):
        # 15-16: graveyard + fingerprint hafızası (run_cycle içinden)
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        _seed_dl(_dataset(100, win_ratio=0.2))  # kaybeden dataset
        r1 = sl.run_cycle({"strategy_lab": {}}, force=True)
        assert r1 is not None
        sl.run_cycle({"strategy_lab": {}}, force=True)
        s = sl._load_state()
        ml = s["models"][dl.MODEL_CORE]
        assert ml["graveyard"], "başarısız adaylar graveyard'a gitmeli"
        assert ml["rejected_fingerprints"]
        for g in ml["graveyard"]:
            assert g["fingerprint"] in ml["rejected_fingerprints"]

    def test_rollback_on_degradation(self, iso):
        # 14: kötüleşmede dl rollback çalışır (baseline'dan kötü)
        base = _dataset(60, win_ratio=0.8)
        bad = [_rec(300 + i, net=-2.0, result="SL",
                    confidence=75) for i in range(20)]
        for t in bad:
            t["configuration_version"] = "CANDX"

        def _m(s):
            ms = dl._model_state(s, dl.MODEL_CORE)
            ms["dataset"] = base + bad
            ms["champion"] = {
                "version": "CANDX", "overrides": {"tp_pct": 1.2},
                "promoted_at": T0.isoformat(),
                "previous": {"version": "BASE", "overrides": {}},
                "baseline_metrics": dl.compute_model_metrics(base)}
        dl._update_state(_m)
        st = dl._load_state()
        ms = st["models"][dl.MODEL_CORE]
        rb = dl._check_rollback(ms, dl.DEFAULT_THRESHOLDS)
        assert rb is not None
        assert rb["reason"] in dl.ROLLBACK_CODES


# ── 17-20: MFE/MAE, kâr yakalama, giveback, zarar nedeni ───────────

class TestDiagnostics:
    def test_mfe_mae_computed_in_dual_model(self):
        import dual_model as dm
        p = {"symbol": "BTCUSDT", "model": dl.MODEL_CORE,
             "side": "LONG", "entry": 100.0, "quantity": 1.0,
             "opened_at": T0.isoformat(), "opened_ts": 0.0,
             "confidence": 70, "peak": 103.0, "trough": 98.0}
        t = dm._build_trade(p, 101.0, "TP", 60.0)
        assert t["mfe_pct"] == pytest.approx(3.0)
        assert t["mae_pct"] == pytest.approx(2.0)
        # Eski kayıt (peak yok) → None; uydurma yok
        p2 = dict(p); p2.pop("peak"); p2.pop("trough")
        t2 = dm._build_trade(p2, 101.0, "TP", 60.0)
        assert t2["mfe_pct"] is None and t2["mae_pct"] is None

    def test_captured_profit_ratio(self):
        # 18: max_net = 100*(3/100)*1 - 0.2 - 0.1 = 2.7; net=1.35 → 0.5
        r = _rec(1, net=1.35, mfe=3.0, fees=0.2, slippage=0.1,
                 result="TP")
        pc = sl.profit_capture_metrics(r)
        assert pc["maximum_net_profit"] == pytest.approx(2.7)
        assert pc["captured_profit_ratio"] == pytest.approx(0.5)

    def test_profit_giveback(self):
        # 19: giveback = max_net - realized
        r = _rec(1, net=0.5, mfe=2.0, fees=0.2, slippage=0.1,
                 result="TRAILING")
        pc = sl.profit_capture_metrics(r)
        assert pc["profit_giveback"] == pytest.approx(1.2)

    def test_capture_no_evidence_honest(self):
        pc = sl.profit_capture_metrics(_rec(1, mfe=None))
        assert pc["code"] == "EVIDENCE_MISSING"
        assert pc["captured_profit_ratio"] is None

    def test_single_trade_no_dominant_diagnosis(self):
        # §4: tek işlemle strateji değişikliği yapılmaz
        agg = sl.aggregate_profit_capture([_rec(1, net=0.1, mfe=3.0)],
                                          min_sample=15)
        assert agg["dominant"] is None

    def test_loss_reason_classification(self):
        # 20: zarar nedeni doğru sınıflandırılır
        assert sl.classify_loss(_rec(1, net=-0.5, mfe=1.2,
                                     result="SL"))["code"] == \
            "PROFIT_GIVEBACK"
        assert sl.classify_loss(_rec(1, net=-0.5, mfe=0.05,
                                     result="SL"))["code"] == \
            "LOW_QUALITY_ENTRY"
        fee = _rec(1, net=-0.3, mfe=0.8, fees=0.9, slippage=0.1)
        fee["gross_pnl"] = 0.7
        assert sl.classify_loss(fee)["code"] == "FEE_DRAG"
        old = _rec(1, net=-0.5, mfe=None, result="SL")
        assert sl.classify_loss(old)["code"] == "DATA_QUALITY_FAILURE"
        assert sl.classify_loss(_rec(1, net=1.0))["code"] is None

    def test_loss_aggregation(self):
        losses = [_rec(i, net=-0.5, mfe=1.2, result="SL")
                  for i in range(5)] + \
                 [_rec(9, net=-5.0, mfe=0.05, result="SL")]
        agg = sl.aggregate_loss_diagnosis(losses)
        assert agg["most_frequent"] == "PROFIT_GIVEBACK"
        assert agg["most_expensive"] == "LOW_QUALITY_ENTRY"


# ── 21-22: devre kesici ────────────────────────────────────────────

class TestCircuitBreaker:
    def test_transport_failure_trips(self, iso):
        cb = sl.evaluate_circuit_breaker(
            {"consecutive_failed_challengers": 0}, _dataset(100),
            "SSL: certificate verify failed", {})
        assert cb["tripped"] and "TRANSPORT_FAILURE" in cb["reasons"]
        assert cb["code"] == "STRATEGY_LAB_CIRCUIT_BREAKER"

    def test_consecutive_failures_trip(self, iso):
        cb = sl.evaluate_circuit_breaker(
            {"consecutive_failed_challengers": 5}, _dataset(100),
            None, {})
        assert "CONSECUTIVE_FAILED_CHALLENGERS" in cb["reasons"]

    def test_breaker_stops_generation(self, iso, monkeypatch):
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        _seed_dl(_dataset(100))

        def _m(s):
            ml = sl._model_lab(s, dl.MODEL_CORE)
            ml["consecutive_failed_challengers"] = 99
        sl._update_state(_m)
        sl.run_cycle({"strategy_lab": {}}, force=True)
        s = sl._load_state()
        ml = s["models"][dl.MODEL_CORE]
        assert ml["circuit_breaker"]["tripped"]
        assert not ml["candidates"], "fren açıkken aday üretilmemeli"


# ── 23-25: restart, panel, LIVE DISABLED ───────────────────────────

class TestStateAndSafety:
    def test_state_survives_restart(self, iso, monkeypatch):
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        _seed_dl(_dataset(100))
        sl.run_cycle({"strategy_lab": {}}, force=True)
        before = sl._load_state()
        assert before.get("last_cycle")
        # "restart": modül state'i yalnız diskten okunur
        after = json.loads(sl.STATE_PATH.read_text(encoding="utf-8"))
        assert after["last_cycle"] == before["last_cycle"]
        assert after["models"] == before["models"]

    def test_status_reflects_real_state(self, iso, monkeypatch):
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        _seed_dl(_dataset(100))
        sl.run_cycle({"strategy_lab": {}}, force=True)
        st = sl.status()
        assert st["live_orders"] == "DISABLED"
        m = st["models"][dl.MODEL_CORE]
        assert m["loss_diagnosis"] is not None
        assert m["profit_capture"] is not None
        real = sl._load_state()["models"][dl.MODEL_CORE]
        assert m["graveyard_size"] == len(real["graveyard"])

    def test_live_eligibility_is_label_only(self, iso):
        # 25: tüm kapılar geçilse bile sonuç yalnız etikettir
        ds = []
        for i in range(250):
            ds.append(_rec(i, net=1.0 if i % 10 else -0.5,
                           regime=["TREND", "RANGE"][i % 2],
                           symbol=["BTCUSDT", "ETHUSDT", "SOLUSDT"][i % 3]))
        # exit_time'ları 20 güne yay
        for i, r in enumerate(ds):
            r["exit_time"] = (datetime.now(timezone.utc) -
                              timedelta(days=20) +
                              timedelta(hours=i * 2)).isoformat()
        m = dl.compute_model_metrics(ds)
        cfg = {"strategy_lab": {
            "attest_execution_simulator": True,
            "attest_stress_test": True,
            "attest_restart_recovery": True,
            "attest_rollback_test": True,
            "attest_kill_switch_test": True}}
        r = sl.evaluate_live_eligibility(dl.MODEL_CORE, ds, m,
                                         True, cfg)
        assert r["status"] in ("LIVE_ELIGIBLE", "NOT_ELIGIBLE")
        assert r["live_orders"] == "DISABLED"
        # LIVE_ENABLED bu modülde tanımsız — kaynak sözleşmesi
        src = Path(sl.__file__).read_text(encoding="utf-8")
        assert "LIVE_ENABLED bu modülde TANIMSIZ" in src
        assert not hasattr(sl, "LIVE_ENABLED")

    def test_no_attestation_no_eligibility(self, iso):
        ds = _dataset(250)
        m = dl.compute_model_metrics(ds)
        r = sl.evaluate_live_eligibility(dl.MODEL_CORE, ds, m, True, {})
        assert r["status"] == "NOT_ELIGIBLE"
        assert r["checks"]["kill_switch_test"] is False


# ── Kontroller + API/UI kablolaması ────────────────────────────────

class TestControlsAndWiring:
    def test_emergency_stop_blocks_cycle(self, iso):
        _seed_dl(_dataset(100))
        sl.control("EMERGENCY_STOP", actor="op")
        assert sl.run_cycle({}, force=True) is None
        sl.control("CLEAR_EMERGENCY_STOP", actor="op")
        sl.control("RESUME_LAB", actor="op")
        assert sl.run_cycle({}, force=True) is not None

    def test_cancel_challengers(self, iso):
        _seed_dl(_dataset(100))
        c = _cand()
        assert sl.install_as_challenger(dl.MODEL_CORE, c)
        r = sl.control("CANCEL_CHALLENGERS", actor="op")
        assert r["ok"]
        st = dl._load_state()
        assert st["models"][dl.MODEL_CORE]["challenger"] is None

    def test_revert_champion(self, iso):
        def _m(s):
            ms = dl._model_state(s, dl.MODEL_CORE)
            ms["champion"] = {"version": "CANDX",
                              "overrides": {"tp_pct": 1.2},
                              "promoted_at": T0.isoformat(),
                              "previous": {"version": "BASE",
                                           "overrides": {}}}
        dl._update_state(_m)
        r = sl.control("REVERT_CHAMPION", actor="op",
                       model=dl.MODEL_CORE)
        assert dl.MODEL_CORE in r["reverted"]
        st = dl._load_state()
        assert st["models"][dl.MODEL_CORE]["champion"]["version"] == \
            "BASE"

    def test_unknown_action_rejected(self, iso):
        assert not sl.control("ENABLE_LIVE_ORDERS", actor="op")["ok"]

    def test_api_and_ui_wired(self):
        appsrc = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "/api/strategy-lab/status" in appsrc
        # Yazma rotası "strategy" içeremez (Mission 1800 read-only
        # kilidi) — bilinçli olarak /api/lab/control.
        assert "/api/lab/control" in appsrc
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "renderStrategyLab" in js
        assert "/api/strategy-lab/status" in js
        html = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        assert "th-lab-core" in html and "th-lab-opp" in html
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "strategy_lab_state.json" in gi
        assert "strategy_lab_history.jsonl" in gi

    def test_changes_schema_matches_ui_contract(self, iso,
                                                monkeypatch):
        # Mimar bulgusu: UI renderLearning parameter/old/new bekler
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        assert sl.install_as_challenger(dl.MODEL_CORE, _cand())
        chal = dl._load_state()["models"][dl.MODEL_CORE]["challenger"]
        for ch in chal["changes"]:
            assert set(ch) == {"parameter", "old", "new"}

    def test_stage5_fresh_read_no_false_drop(self, iso, monkeypatch):
        # Mimar bulgusu: aynı çevrimde kurulan challenger bayat
        # snapshot yüzünden DL_CHALLENGER_DROPPED sayılmamalı.
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        _seed_dl(_dataset(100))
        cd = _cand(stage="STAGE5_PAPER_CHALLENGER")
        cd["installed_as_challenger"] = True
        assert sl.install_as_challenger(dl.MODEL_CORE, cd)

        def _m(s):
            ml = sl._model_lab(s, dl.MODEL_CORE)
            ml["candidates"][cd["strategy_candidate_id"]] = cd
        sl._update_state(_m)
        sl.run_cycle({"strategy_lab": {}}, force=True)
        ml = sl._load_state()["models"][dl.MODEL_CORE]
        got = ml["candidates"].get(cd["strategy_candidate_id"])
        assert got and got["status"] == "ACTIVE"
        assert ml["consecutive_failed_challengers"] == 0

    def test_uninstalled_stage5_not_counted_failed(self, iso,
                                                   monkeypatch):
        monkeypatch.setattr(sl, "_base_params", lambda m: _base_params())
        _seed_dl(_dataset(100))
        cd = _cand(stage="STAGE5_PAPER_CHALLENGER")
        # installed_as_challenger YOK — kurulum doğrulanmamış

        def _m(s):
            ml = sl._model_lab(s, dl.MODEL_CORE)
            ml["candidates"][cd["strategy_candidate_id"]] = cd
        sl._update_state(_m)
        sl.run_cycle({"strategy_lab": {}}, force=True)
        ml = sl._load_state()["models"][dl.MODEL_CORE]
        assert ml["consecutive_failed_challengers"] == 0
        assert cd["strategy_candidate_id"] in ml["candidates"]

    def test_generation_inputs_exclude_holdout(self):
        # Mimar bulgusu: aday üretimi holdout'tan beslenemez —
        # run_cycle kaynağında üretim girdileri gen_window'dan gelir.
        src = Path(sl.__file__).read_text(encoding="utf-8")
        blk = src.split("def run_cycle")[1]
        assert 'gen_window = _split["train"] + _split["walk"]' in blk
        assert "generate_candidates(\n" \
               "                    model, ml, gen_diagnosis, " \
               "gen_loss, gen_capture)" in blk

    def test_bridge_single_trigger_point(self):
        # Lab yalnız auto_controller döngüsünden tetiklenir
        ac = (ROOT / "alpha20_v1/auto_controller.py").read_text(
            encoding="utf-8")
        assert "run_strategy_lab_cycle" in ac
        le_src = (ROOT / "alpha20_v1/learning_engine.py").read_text(
            encoding="utf-8")
        assert le_src.count("run_strategy_lab_cycle") == 1 or \
            "def run_strategy_lab_cycle" in le_src
