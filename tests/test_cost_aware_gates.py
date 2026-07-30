"""Maliyet-sonrası (net) TP/SL sözleşmesi.

net_tp = tp - roundtrip, net_sl = sl + roundtrip.
Giriş REDDEDİLİR: net_tp<=0, net_rr<1.20, net edge < roundtrip×1.5.
CORE varsayılan yapısı (TP .45 / SL .30) bu kapılardan GEÇEMEZ —
bilinçli: sürdürülemez yapı yeni pozisyon açamaz; challenger'lar
COST_STRUCTURE ızgarasıyla shadow/paper'da kanıtlanır.
LIVE ORDERS DISABLED değişmez.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_learning as dl  # noqa: E402
import dual_model as dm  # noqa: E402
import strategy_lab as sl  # noqa: E402

CFG = dm.get_config(None)


def _viable_cfg(tp=1.0, sl=0.40):
    cfg = dm.get_config(None)
    for sec in ("core", "opportunity"):
        cfg[sec]["tp_pct"] = tp
        cfg[sec]["sl_pct"] = sl
    return cfg


ROW = {"spread_pct": 0.02, "volume_usdt": 100e6, "trade_count": 300000}
SIG = {"confidence": 80, "expected_gross_edge_pct": 0.9,
       "side": "LONG", "last": 100.0}


class TestCostProfile:
    def test_core_default_math(self):
        cp = dm.cost_profile(CFG["core"])  # slip = max_slippage .03
        assert cp["round_trip_cost_pct"] == pytest.approx(0.23)
        assert cp["net_tp_pct"] == pytest.approx(0.22)
        assert cp["net_sl_pct"] == pytest.approx(0.53)
        assert cp["net_reward_risk"] == pytest.approx(0.4151, abs=1e-3)
        assert cp["break_even_win_rate_pct"] == pytest.approx(
            70.67, abs=0.1)

    def test_net_tp_negative_break_even_none(self):
        cp = dm.cost_profile({"tp_pct": 0.1, "sl_pct": 0.3,
                              "max_slippage_pct": 0.03})
        assert cp["net_tp_pct"] < 0
        assert cp["break_even_win_rate_pct"] is None


class TestEntryGates:
    def test_default_core_structure_rejected(self):
        # Varsayılan CORE yapısı: net RR ~0.42 < 1.20 → giriş YOK.
        ok, reason, _ = dm.execution_quality_gate(
            ROW, SIG, dm.MODEL_CORE, CFG)
        assert not ok and reason == "NET_REWARD_RISK_TOO_LOW"

    def test_net_tp_non_positive(self):
        cfg = _viable_cfg(tp=0.20, sl=0.40)
        ok, reason, _ = dm.execution_quality_gate(
            ROW, SIG, dm.MODEL_CORE, cfg)
        assert not ok and reason == "NET_TP_NON_POSITIVE"

    def test_viable_structure_passes(self):
        # TP 1.0 / SL 0.40, slip .015 → cost .215; net .785/.615
        # rr 1.276; net edge .685 >= .3225.
        ok, reason, net = dm.execution_quality_gate(
            ROW, SIG, dm.MODEL_CORE, _viable_cfg())
        assert ok and reason is None and net > 0

    def test_edge_below_cost_multiple(self):
        sig = {**SIG, "expected_gross_edge_pct": 0.30}
        # net edge .085 > 0 ama < cost(.215)×1.5=.3225 → red.
        ok, reason, _ = dm.execution_quality_gate(
            ROW, sig, dm.MODEL_CORE, _viable_cfg())
        assert not ok and reason == "EDGE_BELOW_COST_MULTIPLE"

    def test_reason_codes_registered(self):
        for c in ("NET_TP_NON_POSITIVE", "NET_REWARD_RISK_TOO_LOW",
                  "EDGE_BELOW_COST_MULTIPLE"):
            assert c in dm.REASON_CODES


class TestSnapshotCostProfiles:
    def test_snapshot_has_both_models(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dm, "RUNTIME_PATH",
                            tmp_path / "runtime.json", raising=False)
        snap = dm.snapshot()
        cps = snap["cost_profiles"]
        for key in ("core", "opportunity"):
            cp = cps[key]
            for f in ("gross_tp_pct", "gross_sl_pct",
                      "round_trip_cost_pct", "net_tp_pct",
                      "net_sl_pct", "net_reward_risk",
                      "break_even_win_rate_pct"):
                assert f in cp


class TestCostStructureCandidates:
    def _gen(self, model):
        ml = {"generation": 0, "candidates": {},
              "rejected_fingerprints": [], "promotion_history": []}
        import random
        return sl.generate_candidates(model, ml, None, None, None,
                                      rng=random.Random(7))

    @pytest.mark.parametrize("model,tps,sls", [
        (dl.MODEL_CORE, {0.80, 1.00, 1.20}, {0.35, 0.40, 0.50}),
        (dl.MODEL_OPP, {1.00, 1.25, 1.50}, {0.45, 0.55, 0.65}),
    ])
    def test_grid_only_net_rr_ge_target(self, model, tps, sls):
        cands = [c for c in self._gen(model)
                 if c["created_reason"] == "COST_STRUCTURE"]
        assert 1 <= len(cands) <= sl.MAX_COST_CANDIDATES_PER_GEN
        for c in cands:
            p = c["parameters"]
            assert p["tp_pct"] in tps and p["sl_pct"] in sls
            assert c["cost_profile"]["net_reward_risk"] >= \
                sl.MIN_NET_REWARD_RISK
            # champion'a dokunulmaz — aday shadow hattında başlar
            assert c["stage"] == "STAGE0_STATIC"

    def test_core_unviable_combo_never_generated(self):
        # TP .80 kombinasyonları CORE'da rr<1.20 → asla üretilmez.
        seen = set()
        ml = {"generation": 0, "candidates": {},
              "rejected_fingerprints": [], "promotion_history": []}
        import random
        for g in range(12):  # ızgara parmak izi eleme ile tükenir
            out = sl.generate_candidates(
                dl.MODEL_CORE, ml, None, None, None,
                rng=random.Random(g))
            for c in out:
                if c["created_reason"] == "COST_STRUCTURE":
                    seen.add((c["parameters"]["tp_pct"],
                              c["parameters"]["sl_pct"]))
                    ml["candidates"][c["strategy_candidate_id"]] = c
            ml["generation"] += 1
        assert all(tp != 0.80 for tp, _ in seen)
        assert (1.00, 0.50) not in seen  # rr 1.054 < 1.20


class TestStage4RealizedGate:
    def _cand(self):
        return {"strategy_candidate_id": "X", "parameters":
                {"tp_pct": 1.0, "sl_pct": 0.4},
                "created_reason": "COST_STRUCTURE",
                "stage": "STAGE4_PAPER_SHADOW",
                "created_at": "2026-01-01T00:00:00+00:00"}

    def _run(self, monkeypatch, metrics):
        monkeypatch.setattr(sl, "_stage_metrics", lambda *a, **k: {
            "method": "GATE_SUBSET_REPLAY", "sample": 80,
            "exit_params_replayable": False, "sufficient": True,
            "metrics": metrics, "note": ""})
        monkeypatch.setattr(sl.dl, "compute_model_metrics",
                            lambda *a, **k: {
                                "expectancy_per_trade": -0.1,
                                "closed_trades": 80})
        return sl.run_stage(self._cand(), dl.MODEL_CORE, [],
                            None, {}, {})

    def test_pf_not_above_1_fails(self, monkeypatch):
        res = self._run(monkeypatch, {
            "expectancy_per_trade": 0.05, "profit_factor": 0.9,
            "win_loss_ratio": 1.5})
        assert res["ok"] is False
        assert res["reason"] == "COST_TARGET_NOT_MET"
        assert res["cost_gate"] == "PROFIT_FACTOR_NOT_ABOVE_1"

    def test_realized_rr_below_target_fails(self, monkeypatch):
        res = self._run(monkeypatch, {
            "expectancy_per_trade": 0.05, "profit_factor": 1.4,
            "win_loss_ratio": 1.05})
        assert res["ok"] is False
        assert res["cost_gate"] == "REALIZED_NET_RR_BELOW_TARGET"

    def test_targets_met_passes(self, monkeypatch):
        res = self._run(monkeypatch, {
            "expectancy_per_trade": 0.05, "profit_factor": 1.4,
            "win_loss_ratio": 1.35})
        assert res["ok"] is True and res["cost_gate"] == "OK"
