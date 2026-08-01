"""ADR-016 ile dual-model Paper yaşam döngüsü entegrasyonu."""

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_model as dm  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
    monkeypatch.setattr(dm, "LEGACY_STATE_PATH", tmp_path / "state.json")
    dm._ADR16_KLINE_CACHE.clear()


def _decision(symbol="BTCUSDT", model=None):
    return {
        "symbol": symbol,
        "model": model or dm.MODEL_CORE,
        "eligible": True,
        "side": "LONG",
        "profile": "ADR016_REGIME_NET_EV",
        "decision_chain": [
            "REGIME", "STRATEGY_FIT", "NET_EV", "RANK", "PAPER_ENTRY"],
        "timeframes": {"direction": "1h", "confirmation": "15m",
                       "entry_timing": "5m"},
        "regime": "TREND",
        "strategy": "TREND_PULLBACK",
        "reason_code": None,
        "sample_size": 40,
        "probabilities": {"tp": "0.750000", "sl": "0.125000",
                          "time": "0.125000"},
        "expected_gross_return_pct": "0.4000",
        "round_trip_cost_pct": "0.2150",
        "net_ev_pct": "0.1850",
        "rank_score": "0.1850",
        "confidence": "75.00",
        "last": "100.00000000",
        "entry_route": "TREND_PULLBACK",
    }


def _row(spread=0.02):
    return {"spread_pct": spread, "volume_usdt": 100_000_000,
            "trade_count": 300_000}


def test_windows_config_enables_adr016_explicitly():
    config = json.loads((ROOT / "alpha20_v1" / "config.json").read_text(
        encoding="utf-8"))
    engine = config["dual_model"]["decision_engine"]
    assert engine == {
        "enabled": True,
        "profile": "ADR016_REGIME_NET_EV",
        "kline_interval": "5m",
        "kline_limit": 1000,
        "kline_cache_seconds": 120,
        "minimum_calibration_samples": 20,
        "net_ev_confidence_z": 1.645,
        "max_new_positions_per_cycle": 1,
        "entry_stagger_seconds": 300,
        "patient_exit": {
            "enabled": True,
            "trend_min_hold_minutes": 10,
            "trend_max_hold_minutes": 45,
            "soft_stop_confirmation_seconds": 300,
            "hard_stop_multiplier": 2.0,
            "profit_activation_buffer_pct": 0.05,
            "profit_floor_buffer_pct": 0.03,
        },
    }
    assert dm.DEFAULTS["decision_engine"]["enabled"] is False


def test_unfinished_5m_candle_is_excluded_and_cache_is_bounded(
        monkeypatch):
    calls = {"count": 0}
    rows = [
        [0, "100", "101", "99", "100", "10", 900_000],
        [900_001, "100", "101", "99", "100", "10", 1_100_000],
    ]

    def fetch(*_args, **_kwargs):
        calls["count"] += 1
        return rows

    monkeypatch.setattr(dm, "fetch_spot_klines", fetch)
    cfg = {"kline_interval": "5m", "kline_limit": 1000,
           "kline_cache_seconds": 120}
    first = dm.fetch_adr016_klines("BTCUSDT", cfg, now=1000)
    second = dm.fetch_adr016_klines("BTCUSDT", cfg, now=1100)
    third = dm.fetch_adr016_klines("BTCUSDT", cfg, now=1121)
    assert len(first) == 1
    assert second is first
    assert len(third) == 2
    assert calls["count"] == 2


def test_adr016_candidate_passes_through_existing_hard_gate(monkeypatch):
    monkeypatch.setattr(dm._adr16, "evaluate_candidate",
                        lambda *a, **k: _decision())
    cfg = dm.get_config({"dual_model": {"decision_engine": {
        "enabled": True}}})
    decision, signal, ok, reason, net = dm.evaluate_adr016_candidate(
        _row(), "BTCUSDT", [], dm.MODEL_CORE, cfg)
    assert ok is True and reason is None and net > 0
    assert signal["profile"] == "ADR016_REGIME_NET_EV"
    assert signal["decision_engine"] is decision


def test_spread_safety_cannot_be_bypassed_by_positive_ev(monkeypatch):
    monkeypatch.setattr(dm._adr16, "evaluate_candidate",
                        lambda *a, **k: _decision())
    cfg = dm.get_config({"dual_model": {"decision_engine": {
        "enabled": True}}})
    decision, _signal, ok, reason, _net = dm.evaluate_adr016_candidate(
        _row(spread=9.0), "BTCUSDT", [], dm.MODEL_CORE, cfg)
    assert ok is False
    assert reason == "SPREAD_TOO_HIGH"
    assert decision["eligible"] is False


def test_cycle_selects_only_top_one_but_keeps_total_cap_ten():
    cfg = dm.get_config({"dual_model": {"decision_engine": {
        "enabled": True, "max_new_positions_per_cycle": 1}}})
    winners = [{"symbol": f"S{i}USDT"} for i in range(6)]
    selected, deferred = dm.select_ranked_cycle_candidates(winners, cfg)
    assert selected == winners[:1]
    assert deferred == winners[1:]
    assert cfg["total_max_open_positions"] == 10


def test_ownership_is_ranked_by_net_ev_then_sample():
    result = dm.resolve_ownership({dm.MODEL_CORE: [
        {"symbol": "AUSDT", "net_edge_pct": 0.2,
         "rank_score": 0.2, "sample_size": 20},
        {"symbol": "BUSDT", "net_edge_pct": 0.3,
         "rank_score": 0.3, "sample_size": 20}],
        dm.MODEL_OPP: []})
    assert [row["symbol"] for row in result["winners"]] == [
        "BUSDT", "AUSDT"]


def test_open_position_and_trade_preserve_decision_evidence():
    cfg = dm.get_config({"dual_model": {"decision_engine": {
        "enabled": True, "patient_exit": {"enabled": True}}}})
    decision = _decision()
    signal = {"side": "LONG", "confidence": 75, "last": 100,
              "profile": "ADR016_REGIME_NET_EV",
              "entry_route": "TREND_PULLBACK",
              "decision_engine": decision}
    assert dm.try_open_position(
        "BTCUSDT", dm.MODEL_CORE, signal, 0.185, cfg, now=1000)[0]
    position = dm._load_runtime()["positions"]["BTCUSDT"]
    assert position["decision_engine"]["sample_size"] == 40
    assert position["patient_exit"]["profile"] == \
        "ADR017_TREND_PATIENT_EXIT"
    trade = dm._build_trade(position, 101, "TP", 1060)
    assert trade["decision_engine"]["regime"] == "TREND"
    assert trade["profile"] == "ADR016_REGIME_NET_EV"


def test_snapshot_exposes_read_only_decision_status():
    decision = _decision()
    dm.record_adr016_decision(decision, {"reason_code": "NO_SIGNAL"},
                              opened=False, rank=1)
    config = {"dual_model": {"decision_engine": {
        "enabled": True, "minimum_calibration_samples": 20,
        "max_new_positions_per_cycle": 1,
        "entry_stagger_seconds": 300}}}
    snap = dm.snapshot(main_cfg=config)
    status = snap["decision_engine"]
    assert status["enabled"] is True
    assert status["decision_chain"][-1] == "PAPER_ENTRY"
    assert status["last_decisions"][0]["market_regime"] == "TREND"
    assert snap["live_orders"] == "DISABLED"


def test_source_contains_no_exchange_write_path():
    sources = "\n".join([
        (ROOT / "paper_decision_engine.py").read_text(encoding="utf-8"),
        (ROOT / "alpha20_v1" / "dual_model.py").read_text(
            encoding="utf-8"),
    ])
    for forbidden in ("/api/v3/order", "X-MBX-APIKEY", "signature="):
        assert forbidden not in sources
