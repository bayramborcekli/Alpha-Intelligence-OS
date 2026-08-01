"""alpha20_revize_v2: Windows entegrasyonu ve davranış regresyonları."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import alpha20  # noqa: E402
import auto_controller as ac  # noqa: E402
import decision_engine as de  # noqa: E402
import dual_model as dm  # noqa: E402
from app import app  # noqa: E402


@pytest.fixture
def dual_store(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
    monkeypatch.setattr(dm, "LEGACY_STATE_PATH", tmp_path / "state.json")
    return tmp_path


def _dual_cfg(min_hold_hours: float = 4.0) -> dict:
    return dm.get_config({"adaptive_system": {
        "min_hold_hours": min_hold_hours,
    }})


def _short_sig() -> dict:
    return {
        "side": "SHORT", "last": 100.0, "confidence": 90,
        "expected_gross_edge_pct": 1.0,
    }


def test_full_app_preserved_and_home_is_not_404(monkeypatch):
    assert (ROOT / "app.py").stat().st_size > 200_000
    monkeypatch.setenv("LOCAL_DEV_BYPASS", "true")
    response = app.test_client().get("/home")
    assert response.status_code != 404


def test_config_is_inside_active_alpha20_package():
    expected = ROOT / "alpha20_v1" / "config.json"
    assert alpha20.CONFIG_PATH == expected
    assert ac.CONFIG_PATH == expected
    cfg = json.loads(expected.read_text(encoding="utf-8"))
    assert cfg["mode"] == "PAPER"
    assert cfg["interval"] == "15m"
    assert cfg["trend_interval"] == "1h"
    assert cfg["scan_seconds"] == 60


def test_windows_start_chain_bootstraps_both_paper_loops():
    cmd = (ROOT / "start_alpha.cmd").read_text(encoding="utf-8")
    launcher = (ROOT / "launcher_windows.py").read_text(encoding="utf-8")
    server = (ROOT / "serve_windows.py").read_text(encoding="utf-8")
    assert "launcher_windows.py" in cmd
    assert "serve_windows.py" in launcher
    assert "_app.ac.start_controller_loop()" in server
    assert "_dm.start_dual_model_loop(_app._get_main_config)" in server
    assert "DUAL-MODEL LOOP STARTED (LIVE ORDERS DISABLED)" in server


def test_windows_file_lock_has_portable_fallback():
    for rel in ("alpha20_v1/dual_model.py",
                "alpha20_v1/universe_manager.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "except ImportError" in source
        assert "import portable_flock as fcntl" in source
    assert (ROOT / "portable_flock.py").is_file()


def test_legacy_short_pnl_direction_and_single_round_trip_fee():
    pnl = alpha20.compute_realized_pnl(100.0, 90.0, 1.0, "SHORT")
    assert pnl["gross_pnl"] == 10.0
    expected_fee = (100.0 + 90.0) * alpha20.FEE_RATE
    assert pnl["fee_usdt"] == pytest.approx(expected_fee)
    assert pnl["pnl"] == pytest.approx(10.0 - expected_fee)


def test_dual_short_open_is_directional(dual_store):
    cfg = _dual_cfg()
    ok, reason = dm.try_open_position(
        "BTCUSDT", dm.MODEL_CORE, _short_sig(), 0.8, cfg, now=time.time())
    assert ok, reason
    pos = dm._load_runtime()["positions"]["BTCUSDT"]
    assert pos["tp"] < pos["entry"] < pos["sl"]
    assert pos["side"] == "SHORT"


def test_dual_minimum_hold_blocks_profit_but_not_stop(dual_store):
    now = time.time()
    cfg = _dual_cfg()
    ok, _ = dm.try_open_position(
        "BTCUSDT", dm.MODEL_CORE, _short_sig(), 0.8, cfg, now=now)
    assert ok
    pos = dm._load_runtime()["positions"]["BTCUSDT"]

    assert dm.monitor_positions(lambda _: pos["tp"] - 0.01,
                                cfg, now=now + 60) == []
    assert "BTCUSDT" in dm._load_runtime()["positions"]

    closed = dm.monitor_positions(lambda _: pos["sl"] + 0.01,
                                  cfg, now=now + 120)
    assert closed and closed[0]["result"] == "SL"
    assert closed[0]["net_pnl"] < 0


def test_dual_short_profit_after_four_hours(dual_store):
    now = time.time()
    cfg = _dual_cfg()
    ok, _ = dm.try_open_position(
        "ETHUSDT", dm.MODEL_CORE, _short_sig(), 0.8, cfg, now=now)
    assert ok
    pos = dm._load_runtime()["positions"]["ETHUSDT"]
    closed = dm.monitor_positions(lambda _: pos["tp"] - 0.01,
                                  cfg, now=now + 4 * 3600 + 1)
    assert closed and closed[0]["result"] == "TP"
    assert closed[0]["gross_pnl"] > 0


def test_legacy_minimum_hold_blocks_take_profit(monkeypatch):
    now = datetime.now(timezone.utc)
    position = {
        "symbol": "BTCUSDT", "side": "LONG", "entry": 100.0,
        "stop": 90.0, "target": 110.0, "quantity": 1.0,
        "risk_usdt": 10.0, "opened_at": now.isoformat(),
        "min_hold_hours": 4.0,
    }
    state = {"position": position, "balance": 1000.0,
             "consecutive_losses": 0, "trades": []}
    candle = pd.DataFrame([{"high": 111.0, "low": 99.0}])
    monkeypatch.setattr(alpha20, "fetch_klines_safe",
                        lambda *args, **kwargs: candle)
    monkeypatch.setattr(alpha20, "append_trade_history",
                        lambda *args, **kwargs: None)
    alpha20.manage_position(state)
    assert state["position"] is not None

    state["position"]["opened_at"] = (
        now - timedelta(hours=4, seconds=1)).isoformat()
    alpha20.manage_position(state)
    assert state["position"] is None
    assert state["trades"][0]["close_reason"] == "TAKE_PROFIT"


def test_dual_fee_is_charged_once_per_side(dual_store):
    now = time.time()
    p = {
        "symbol": "BTCUSDT", "model": dm.MODEL_CORE, "side": "SHORT",
        "entry": 100.0, "quantity": 1.0,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "opened_ts": now - 3600, "confidence": 90,
    }
    trade = dm._build_trade(p, 90.0, "TP", now)
    assert trade["fees"] == pytest.approx((100.0 + 90.0) * dm.FEE_RATE)
    assert trade["gross_pnl"] == 10.0


@pytest.mark.parametrize("symbol", [
    "USDCUSDT", "FDUSDUSDT", "BTCUPUSDT", "ETHDOWNUSDT",
    "SOL3LUSDT", "XRP5SUSDT",
])
def test_stablecoin_and_leveraged_tokens_are_excluded(symbol):
    assert not dm._eligible_usdt({"symbol": symbol})


def test_configured_decision_threshold_is_active():
    cfg = json.loads((ROOT / "alpha20_v1" / "config.json").read_text(
        encoding="utf-8"))
    adaptive = cfg["adaptive_system"]
    configured_threshold = adaptive["final_decision_threshold"]
    assert configured_threshold == 78.0
    source = (ROOT / "alpha20_v1" / "auto_controller.py").read_text(
        encoding="utf-8")
    assert 'adaptive_cfg.get("final_decision_threshold"' in source
    assert "de.check_conditions(" in source
    assert de.DEFAULT_AUTO_THRESHOLD <= configured_threshold


def test_live_exchange_writes_remain_disabled():
    state = dm.snapshot()
    assert state["live_orders"] == "DISABLED"
    project = json.loads((ROOT / "governance" / "project_state.json").read_text(
        encoding="utf-8"))
    assert project["safety"]["live_orders"] == "DISABLED"
    assert project["safety"]["exchange_write_requests_allowed"] == 0
    assert project["safety"]["paper_only"] is True
