"""Kimi denetimi: runtime P0 ve gerçek piyasa girdisi regresyonları."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import alpha20  # noqa: E402
import auto_controller as ac  # noqa: E402
import dual_learning as dl  # noqa: E402
import dual_model as dm  # noqa: E402
import universe_manager as um  # noqa: E402


def _valid_runtime(**extra):
    return {
        "positions": {}, "trades": [], "rejections": [],
        "core_list": [], "opportunity_list": [], **extra,
    }


def test_dual_runtime_corruption_is_quarantined_and_mutation_stops(
        tmp_path, monkeypatch):
    path = tmp_path / "dual_model_runtime.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(dm, "RUNTIME_PATH", path)

    with pytest.raises(dm.RuntimeCorruptError, match="RUNTIME_CORRUPT"):
        dm._load_runtime()

    quarantine = path.with_suffix(".corrupt")
    assert not path.exists()
    assert quarantine.exists()
    with pytest.raises(dm.RuntimeCorruptError):
        dm._update_runtime(lambda rt: rt.update({"positions": {}}))
    assert not path.exists()


def test_dual_runtime_schema_corruption_is_quarantined(tmp_path, monkeypatch):
    path = tmp_path / "dual_model_runtime.json"
    path.write_text(json.dumps({"positions": []}), encoding="utf-8")
    monkeypatch.setattr(dm, "RUNTIME_PATH", path)

    with pytest.raises(dm.RuntimeCorruptError, match="positions"):
        dm._load_runtime()
    assert path.with_suffix(".corrupt").exists()


def test_dual_runtime_atomic_update_keeps_verified_backup(tmp_path, monkeypatch):
    path = tmp_path / "dual_model_runtime.json"
    path.write_text(json.dumps(_valid_runtime(version=1)), encoding="utf-8")
    monkeypatch.setattr(dm, "RUNTIME_PATH", path)

    dm._update_runtime(lambda rt: rt.update({"version": 2}))

    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2
    assert json.loads(path.with_suffix(".bak").read_text(
        encoding="utf-8"))["version"] == 1
    assert not list(tmp_path.glob("*.tmp"))
    assert dm.runtime_health()["status"] == "HEALTHY"


def test_snapshot_remains_read_only_visible_on_corruption(tmp_path, monkeypatch):
    path = tmp_path / "dual_model_runtime.json"
    path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(dm, "RUNTIME_PATH", path)

    snap = dm.snapshot()

    assert snap["live_orders"] == "DISABLED"
    assert snap["last_error"] == "RUNTIME_CORRUPT"
    assert snap["runtime_health"]["status"] == "RUNTIME_CORRUPT"
    assert snap["positions"] == []


def test_alpha20_reader_blocks_new_entry_on_corrupt_dual_runtime(
        tmp_path, monkeypatch):
    path = tmp_path / "dual_model_runtime.json"
    path.write_text(json.dumps({"positions": []}), encoding="utf-8")
    monkeypatch.setattr(alpha20, "DUAL_MODEL_RUNTIME_PATH", path)
    state = {
        "position": None, "consecutive_losses": 0,
        "day_start_balance": 10_000.0, "balance": 10_000.0,
    }
    config = {"max_consecutive_losses": 3, "daily_loss_limit_pct": 1.5}

    allowed, reason = alpha20.can_open(config, state, "BTCUSDT")

    assert allowed is False
    assert "RUNTIME_CORRUPT" in reason
    assert path.with_suffix(".corrupt").exists()


def test_dual_learning_stops_on_corrupt_runtime(tmp_path, monkeypatch):
    path = tmp_path / "dual_model_runtime.json"
    path.write_text(json.dumps({"trades": {}}), encoding="utf-8")
    monkeypatch.setattr(dl, "RUNTIME_PATH", path)

    with pytest.raises(RuntimeError, match="RUNTIME_CORRUPT"):
        dl._read_runtime_trades()
    assert path.with_suffix(".corrupt").exists()


def test_universe_runtime_corruption_stops_update(tmp_path, monkeypatch):
    path = tmp_path / "universe_runtime.json"
    path.write_text(json.dumps({"dynamic_symbols": {}}), encoding="utf-8")
    monkeypatch.setattr(um, "RUNTIME_STORE_PATH", path)

    with pytest.raises(RuntimeError, match="RUNTIME_CORRUPT"):
        um._update_runtime(lambda rt: rt.update({"dynamic_symbols": []}))
    assert path.with_suffix(".corrupt").exists()
    assert not path.exists()


def test_state_corruption_is_quarantined(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"balance": 100, "trades": {}}),
                    encoding="utf-8")
    monkeypatch.setattr(ac, "STATE_PATH", path)

    assert ac._load_state() is None
    assert path.with_suffix(".corrupt").exists()
    assert ac.get_status()["state_health"]["status"] == "RUNTIME_CORRUPT"


def test_state_atomic_save_keeps_backup(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    old = {"balance": 100.0, "trades": [], "position": None}
    new = {"balance": 101.0, "trades": [], "position": None}
    path.write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(ac, "STATE_PATH", path)

    ac._save_state(new)

    assert json.loads(path.read_text(encoding="utf-8")) == new
    assert json.loads(path.with_suffix(".bak").read_text(
        encoding="utf-8")) == old


def test_real_spot_snapshot_uses_quote_volume_and_spread(monkeypatch):
    monkeypatch.setattr(dm, "fetch_spot_tickers", lambda: [{
        "symbol": "BTCUSDT", "lastPrice": "100",
        "bidPrice": "99.9", "askPrice": "100.1",
        "quoteVolume": "123456789", "priceChangePercent": "2.5",
        "count": 12345,
    }])

    snap = ac._fetch_spot_market_snapshot()["BTCUSDT"]

    assert snap["quote_volume"] == 123456789.0
    assert snap["spread_pct"] == pytest.approx(0.2)
    assert snap["change_pct"] == 2.5


def test_closed_candle_freshness_uses_real_timestamp():
    now = time.time()
    fresh = pd.DataFrame([
        {"close_time": (now - 1800) * 1000},
        {"close_time": (now - 60) * 1000},
        {"close_time": (now + 840) * 1000},
    ])
    stale = fresh.copy()
    stale.loc[1, "close_time"] = (now - 7200) * 1000

    assert ac._closed_candle_timestamp_ok(fresh, "15m", now=now)
    assert not ac._closed_candle_timestamp_ok(stale, "15m", now=now)


def test_coin_score_is_independent_and_directional():
    ticker = {"change_pct": 4.0}
    assert ac._independent_coin_score("LONG", ticker) == 70.0
    assert ac._independent_coin_score("SHORT", ticker) == 30.0
    assert ac._independent_coin_score(None, ticker) == 50.0


def test_auto_controller_has_no_kimi_audit_placeholders():
    source = (ROOT / "alpha20_v1/auto_controller.py").read_text(
        encoding="utf-8")
    for forbidden in (
        "timestamp_ok=True", "prev_price=price",
        "details.get(\"volume_ratio\", 1) * 1e9",
        "liquidity_score=70.0",
        "coin_score=float(strategy_score)",
    ):
        assert forbidden not in source
