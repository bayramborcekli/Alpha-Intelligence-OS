"""ADR-017 — trend Paper pozisyonunda sabırlı, risk-sınırlı çıkış."""

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


@pytest.fixture()
def cfg():
    return dm.get_config({"dual_model": {"decision_engine": {
        "enabled": True,
        "patient_exit": {
            "enabled": True,
            "trend_min_hold_minutes": 10,
            "trend_max_hold_minutes": 45,
            "soft_stop_confirmation_seconds": 300,
            "hard_stop_multiplier": 2.0,
            "profit_activation_buffer_pct": 0.05,
            "profit_floor_buffer_pct": 0.03,
        },
    }}})


def signal():
    return {
        "side": "LONG", "confidence": 80, "last": 100.0,
        "profile": "ADR016_REGIME_NET_EV",
        "entry_route": "TREND_PULLBACK",
        "decision_engine": {
            "regime": "TREND", "strategy": "TREND_PULLBACK",
            "round_trip_cost_pct": "0.2150", "sample_size": 40,
            "net_ev_pct": "0.1850",
        },
    }


def open_trend(cfg, symbol="BTCUSDT"):
    opened, reason = dm.try_open_position(
        symbol, dm.MODEL_CORE, signal(), 0.185, cfg, now=1000.0)
    assert opened, reason
    return dm._load_runtime()["positions"][symbol]


def test_policy_is_only_attached_to_adr016_trend(cfg):
    position = open_trend(cfg)
    policy = position["patient_exit"]
    assert policy["profile"] == "ADR017_TREND_PATIENT_EXIT"
    assert policy["minimum_hold_minutes"] == 10
    assert policy["maximum_hold_minutes"] == 45

    legacy_signal = {"side": "LONG", "confidence": 80, "last": 100.0}
    assert dm.try_open_position(
        "ETHUSDT", dm.MODEL_CORE, legacy_signal, 0.3, cfg, now=1000)[0]
    legacy = dm._load_runtime()["positions"]["ETHUSDT"]
    assert legacy["patient_exit"] is None


def test_small_soft_stop_touch_does_not_sell_immediately(cfg):
    open_trend(cfg)
    assert dm.monitor_positions(lambda _s: 99.69, cfg, now=1004) == []
    assert dm.monitor_positions(lambda _s: 99.69, cfg, now=1305) == []
    assert "BTCUSDT" in dm._load_runtime()["positions"]


def test_soft_stop_requires_minimum_hold_and_five_minute_confirmation(cfg):
    open_trend(cfg)
    assert dm.monitor_positions(lambda _s: 99.69, cfg, now=1004) == []
    closed = dm.monitor_positions(lambda _s: 99.69, cfg, now=1605)
    assert len(closed) == 1
    assert closed[0]["result"] == "SL"
    assert closed[0]["exit_policy_reason"] == "SOFT_STOP_CONFIRMED"


def test_recovery_above_soft_stop_resets_confirmation(cfg):
    open_trend(cfg)
    assert dm.monitor_positions(lambda _s: 99.69, cfg, now=1004) == []
    assert dm.monitor_positions(lambda _s: 99.80, cfg, now=1305) == []
    assert dm.monitor_positions(lambda _s: 99.69, cfg, now=1500) == []
    # Toplam pozisyon yaşı 10 dk'yı aşsa bile yeni stop-altı seri 5 dk değil.
    assert dm.monitor_positions(lambda _s: 99.69, cfg, now=1605) == []


def test_hard_stop_remains_immediate(cfg):
    open_trend(cfg)
    closed = dm.monitor_positions(lambda _s: 99.39, cfg, now=1004)
    assert len(closed) == 1
    assert closed[0]["result"] == "SL"
    assert closed[0]["exit_policy_reason"] == "HARD_STOP"


def test_profit_arms_then_rising_stress_closes_net_positive(cfg):
    open_trend(cfg)
    # TP %0.45; %0.44 zirve maliyet+buffer üstünde ve henüz TP altında.
    assert dm.monitor_positions(lambda _s: 100.44, cfg, now=1600) == []
    position = dm._load_runtime()["positions"]["BTCUSDT"]
    assert position["patient_exit"]["profit_armed"] is True
    closed = dm.monitor_positions(lambda _s: 100.24, cfg, now=1604)
    assert len(closed) == 1
    assert closed[0]["result"] == "TRAILING"
    assert closed[0]["exit_policy_reason"] == "COST_PROTECTED_TRAILING"
    assert closed[0]["net_pnl"] > 0


def test_trailing_never_forces_estimated_net_loss(cfg):
    open_trend(cfg)
    assert dm.monitor_positions(lambda _s: 100.44, cfg, now=1600) == []
    # Zirveden stres artıyor ama brüt %0.10 maliyetin altında.
    assert dm.monitor_positions(lambda _s: 100.10, cfg, now=1604) == []
    assert "BTCUSDT" in dm._load_runtime()["positions"]


def test_trend_time_exit_is_forty_five_minutes_not_fifteen(cfg):
    open_trend(cfg)
    assert dm.monitor_positions(lambda _s: 100.0, cfg, now=1901) == []
    assert dm.monitor_positions(lambda _s: 100.0, cfg, now=2801) == []
    closed = dm.monitor_positions(lambda _s: 100.0, cfg, now=3701)
    assert len(closed) == 1
    assert closed[0]["result"] == "TIME_EXIT"
    assert closed[0]["exit_policy_reason"] == "TREND_PATIENT_TIME_EXIT"


def test_take_profit_remains_immediate(cfg):
    open_trend(cfg)
    closed = dm.monitor_positions(lambda _s: 100.45, cfg, now=1004)
    assert len(closed) == 1
    assert closed[0]["result"] == "TP"
    assert closed[0]["exit_policy_reason"] == "TAKE_PROFIT"


def test_adr016_entries_are_staggered_five_minutes(cfg):
    open_trend(cfg, "BTCUSDT")
    second = signal()
    opened, reason = dm.try_open_position(
        "ETHUSDT", dm.MODEL_CORE, second, 0.185, cfg, now=1200.0)
    assert opened is False and reason == "COOLDOWN"
    opened, reason = dm.try_open_position(
        "ETHUSDT", dm.MODEL_CORE, second, 0.185, cfg, now=1301.0)
    assert opened is True and reason is None
