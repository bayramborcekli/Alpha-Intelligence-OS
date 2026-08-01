"""ADR-024 strateji, kanıt kapısı ve Windows teslim testleri."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

import paper_profit_api as api
from paper_profit_strategy import (
    Candle, StrategyParams, backtest_window, evaluate,
)


ROOT = Path(__file__).resolve().parents[1]


def _candle(index: int, *, opened: str, high: str, low: str,
            close: str) -> Candle:
    start = index * 14_400_000
    return Candle(start, Decimal(opened), Decimal(high), Decimal(low),
                  Decimal(close), Decimal("1000"), start + 14_399_999)


def _params() -> StrategyParams:
    return StrategyParams(
        ema_period=2, channel_period=2, exit_channel_period=2,
        atr_period=2, atr_stop_multiplier=Decimal("2"),
        atr_trail_multiplier=Decimal("3"), ema_slope_bars=1)


def test_breakout_enters_only_at_next_bar_open():
    rows = [
        _candle(0, opened="100", high="101", low="99", close="100"),
        _candle(1, opened="100", high="101", low="99", close="100"),
        _candle(2, opened="100", high="101", low="99", close="100"),
        _candle(3, opened="100", high="104", low="99", close="103"),
        _candle(4, opened="110", high="112", low="109", close="111"),
        _candle(5, opened="111", high="113", low="110", close="112"),
    ]
    trades = backtest_window("TESTUSDT", rows, _params(),
                             Decimal("0.30"), 0, len(rows))
    assert len(trades) == 1
    assert trades[0].entry_time == rows[4].open_time
    assert trades[0].entry == Decimal("110")
    assert trades[0].entry != rows[3].close


def test_cost_below_point_thirty_is_rejected():
    rows = [_candle(i, opened="100", high="101", low="99", close="100")
            for i in range(6)]
    with pytest.raises(ValueError, match="at least 0.30"):
        backtest_window("TESTUSDT", rows, _params(), Decimal("0.29"),
                        0, len(rows))


def test_train_selection_never_reads_holdout_and_pass_requires_both_stages():
    data = {}
    for symbol_index in range(5):
        rows = []
        for index in range(600):
            base = Decimal(100 + symbol_index) + Decimal(index) / Decimal("10")
            rows.append(_candle(
                index, opened=str(base), high=str(base + Decimal("0.15")),
                low=str(base - Decimal("0.05")),
                close=str(base + Decimal("0.10"))))
        data[f"S{symbol_index}USDT"] = rows
    result = evaluate(data, grid=(_params(),), minimum_train_trades=1,
                      minimum_stage_trades=1)
    assert result["status"] == "PASS"
    assert result["selected_on"] == "TRAIN_ONLY"
    assert result["holdout_used_for_selection"] is False
    assert result["round_trip_cost_pct"] == "0.30"
    assert result["validation"]["trades"] >= 1
    assert result["holdout"]["trades"] >= 1
    assert result["gates"]["live_orders"] == "DISABLED"
    assert result["gates"]["exchange_write_requests"] == 0


def test_evidence_api_is_fail_closed_until_real_report_exists(tmp_path):
    missing = api.snapshot(tmp_path / "missing.json")
    assert missing["status"] == "NOT_RUN"
    assert missing["activation"] == "BLOCKED_NO_EVIDENCE"
    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")
    assert api.snapshot(broken)["status"] == "DATA_UNAVAILABLE"


def test_evidence_api_accepts_only_adr024_contract(tmp_path):
    report = tmp_path / "evidence.json"
    report.write_text(json.dumps({
        "source": "BINANCE_SPOT_PUBLIC_GET",
        "timeframe": "4h",
        "strategy_version": "PAPER_PROFIT_V1_CANDIDATE",
        "round_trip_cost_pct": "0.30",
        "holdout_used_for_selection": False,
        "validation": {}, "holdout": {},
        "gates": {"live_orders": "DISABLED",
                  "exchange_write_requests": 0},
        "status": "REJECTED",
    }), encoding="utf-8")
    result = api.snapshot(report)
    assert result["ok"] is True
    assert result["status"] == "REJECTED"
    assert result["live_orders"] == "DISABLED"


def test_windows_startup_does_not_install_superseded_autopilot():
    source = (ROOT / "serve_windows.py").read_text(encoding="utf-8")
    assert "paper_autopilot.install" not in source
    assert "ADR-024 ACTIVE" in source
    cmd = (ROOT / "RUN_PAPER_PROFIT_WINDOWS.cmd").read_text(
        encoding="utf-8")
    assert "paper_profit_research.py" in cmd
    assert "http://127.0.0.1:5000/home" in cmd
    assert 'if not exist "%~dp0paper_profit_strategy.py"' in cmd
    assert "pause >nul" in cmd


def test_api_and_ui_show_new_evidence_not_autopilot():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "trading_home.html").read_text(
        encoding="utf-8")
    js = (ROOT / "static" / "js" / "trading_home.js").read_text(
        encoding="utf-8")
    assert '@app.get("/api/paper-profit/evidence")' in app_source
    assert "PAPER PROFIT V1 — 4 SAATLİK KANIT" in html
    assert "Volatiliteye uyumlu zarar kesme" in html
    assert 'fetch("/api/paper-profit/evidence"' in js
    assert "PAPER AUTOPILOT — ALIŞ/SATIŞ KANITI" not in html
