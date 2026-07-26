"""Sprint 3 — Paper Validation Engine testleri."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import validation  # noqa: E402
from validation import (  # noqa: E402
    build_equity_curve,
    compute_performance_metrics,
    format_session_report,
    generate_session_report,
    health_ok,
    persist_equity_curve,
    run_health_checks,
    run_validation,
)

START = 10000.0


@pytest.fixture(autouse=True)
def _isolate_history(tmp_path, monkeypatch):
    """Gerçek trade_history.json'a bağımlılığı kes — tutarlı sahte history sun."""
    hist = tmp_path / "_default_history.json"
    hist.write_text(json.dumps([{"pnl": 0}] * 100))  # her state sayısını karşılar
    monkeypatch.setattr(validation, "TRADE_HISTORY_PATH", hist)


def trade(pnl, symbol="BTCUSDT", fee=1.0, **extra):
    t = {"symbol": symbol, "pnl": pnl, "fee_usdt": fee,
         "close_reason": "TAKE_PROFIT" if pnl > 0 else "STOP_LOSS",
         "closed_at": "2026-07-26T20:00:00+00:00"}
    t.update(extra)
    return t


def base_config(**overrides):
    cfg = {"mode": "PAPER", "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
           "starting_balance_usdt": START}
    cfg.update(overrides)
    return cfg


def base_state(trades=None, balance=None, position=None):
    trades = trades or []
    net = sum(t["pnl"] for t in trades)
    return {
        "balance": balance if balance is not None else START + net,
        "day": "2026-07-26",
        "day_start_balance": START,
        "consecutive_losses": 0,
        "position": position,
        "trades": trades,
    }


# ── 1. Performans metrikleri ──────────────────────────────────────────────────

class TestPerformanceMetrics:
    def test_empty_trades(self):
        m = compute_performance_metrics([], START)
        assert m["total_trades"] == 0
        assert m["win_rate_pct"] == 0.0
        assert m["net_pnl"] == 0
        assert m["profit_factor"] is None
        assert m["max_drawdown_pct"] == 0

    def test_basic_metrics(self):
        trades = [trade(100), trade(-50), trade(200), trade(-50)]
        m = compute_performance_metrics(trades, START)
        assert m["total_trades"] == 4
        assert m["wins"] == 2 and m["losses"] == 2
        assert m["win_rate_pct"] == 50.0
        assert m["net_pnl"] == 200
        assert m["profit_factor"] == pytest.approx(300 / 100)
        assert m["avg_win"] == 150
        assert m["avg_loss"] == -50
        assert m["total_fees"] == 4.0
        assert m["return_pct"] == pytest.approx(2.0)

    def test_expectancy(self):
        trades = [trade(100), trade(-50)]
        m = compute_performance_metrics(trades, START)
        assert m["expectancy"] == pytest.approx(0.5 * 100 + 0.5 * -50)

    def test_max_drawdown(self):
        # 10000 → 10100 (peak) → 9900 → dd = 200/10100
        trades = [trade(100), trade(-200)]
        m = compute_performance_metrics(trades, START)
        assert m["max_drawdown_pct"] == pytest.approx(200 / 10100 * 100, rel=1e-4)

    def test_streaks(self):
        trades = [trade(1), trade(1), trade(1), trade(-1), trade(-1), trade(1)]
        m = compute_performance_metrics(trades, START)
        assert m["max_win_streak"] == 3
        assert m["max_loss_streak"] == 2

    def test_all_losses_no_profit_factor_div_zero(self):
        m = compute_performance_metrics([trade(-10), trade(-20)], START)
        assert m["profit_factor"] == 0 or m["profit_factor"] is not None
        assert m["gross_win"] == 0


# ── 2. Equity eğrisi ──────────────────────────────────────────────────────────

class TestEquityCurve:
    def test_curve_starts_at_starting_balance(self):
        curve = build_equity_curve([], START)
        assert len(curve) == 1
        assert curve[0]["equity"] == START
        assert curve[0]["trade_index"] == 0

    def test_curve_accumulates(self):
        curve = build_equity_curve([trade(100), trade(-30)], START)
        assert [p["equity"] for p in curve] == [START, START + 100, START + 70]
        assert curve[-1]["trade_index"] == 2
        assert curve[1]["symbol"] == "BTCUSDT"

    def test_persistence(self, tmp_path):
        path = tmp_path / "equity.json"
        curve = persist_equity_curve([trade(50)], START, path=path)
        on_disk = json.loads(path.read_text())
        assert on_disk == curve
        assert on_disk[-1]["equity"] == START + 50

    def test_final_equity_matches_metrics_net_pnl(self):
        trades = [trade(100), trade(-40), trade(15)]
        curve = build_equity_curve(trades, START)
        m = compute_performance_metrics(trades, START)
        assert curve[-1]["equity"] == pytest.approx(START + m["net_pnl"])


# ── 3. Oturum raporu ──────────────────────────────────────────────────────────

class TestSessionReport:
    def test_report_written(self, tmp_path):
        path = tmp_path / "report.json"
        rep = generate_session_report(
            base_state([trade(100)]), base_config(), path=path)
        on_disk = json.loads(path.read_text())
        assert on_disk["current_balance"] == START + 100
        assert on_disk["metrics"]["total_trades"] == 1
        assert rep["mode"] == "PAPER"

    def test_report_no_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validation, "SESSION_REPORT_PATH",
                            tmp_path / "nope.json")
        generate_session_report(base_state(), base_config(), write=False)
        assert not (tmp_path / "nope.json").exists()

    def test_format_contains_key_fields(self):
        rep = generate_session_report(
            base_state([trade(100), trade(-50)]), base_config(), write=False)
        text = format_session_report(rep)
        assert "PAPER OTURUM RAPORU" in text
        assert "Kazanma oranı: 50.0%" in text
        assert "Açık pozisyon: YOK" in text

    def test_format_shows_open_position(self):
        pos = {"symbol": "BTCUSDT", "side": "LONG", "entry": 65000,
               "stop": 64850, "target": 65300, "quantity": 0.33}
        rep = generate_session_report(
            base_state(position=pos), base_config(), write=False)
        assert "BTCUSDT LONG" in format_session_report(rep)


# ── 4. Sağlık monitörü ────────────────────────────────────────────────────────

class TestHealthMonitor:
    def test_healthy_state(self):
        checks = run_health_checks(base_state([trade(100)]), base_config())
        assert health_ok(checks), [c for c in checks if not c["ok"]]

    def test_non_paper_mode_fails(self):
        checks = run_health_checks(base_state(), base_config(mode="LIVE"))
        assert not health_ok(checks)
        assert any(c["name"] == "paper_mode" and not c["ok"] for c in checks)

    def test_balance_mismatch_detected(self):
        st = base_state([trade(100)], balance=START + 999)  # tutarsız
        checks = run_health_checks(st, base_config())
        bad = next(c for c in checks if c["name"] == "balance_consistency")
        assert not bad["ok"]

    def test_missing_state_fields_detected(self):
        checks = run_health_checks({"balance": 1}, base_config())
        bad = next(c for c in checks if c["name"] == "state_schema")
        assert not bad["ok"]

    def test_invalid_open_position_detected(self):
        pos = {"symbol": "BTCUSDT", "side": "LONG", "entry": 65000,
               "stop": None, "target": 65300, "quantity": 0.5}
        checks = run_health_checks(base_state(position=pos), base_config())
        bad = next(c for c in checks if c["name"] == "open_position_valid")
        assert not bad["ok"]

    def test_valid_open_position_passes(self):
        pos = {"symbol": "BTCUSDT", "side": "SHORT", "entry": 65000,
               "stop": 65150, "target": 64700, "quantity": 0.5}
        checks = run_health_checks(base_state(position=pos), base_config())
        good = next(c for c in checks if c["name"] == "open_position_valid")
        assert good["ok"]

    def test_incomplete_trade_record_detected(self):
        st = base_state()
        st["trades"] = [{"symbol": "BTCUSDT", "pnl": 5}]  # fee/close_reason yok
        st["balance"] = START + 5
        checks = run_health_checks(st, base_config())
        bad = next(c for c in checks if c["name"] == "trade_records_complete")
        assert not bad["ok"]

    def test_negative_balance_detected(self):
        st = base_state(balance=-10)
        checks = run_health_checks(st, base_config())
        bad = next(c for c in checks if c["name"] == "balance_positive")
        assert not bad["ok"]

    def test_truncated_history_detected(self, tmp_path, monkeypatch):
        """History geçerli liste ama state'ten kısa → sağlıksız."""
        hist = tmp_path / "trade_history.json"
        hist.write_text(json.dumps([trade(1)]))  # 1 kayıt
        monkeypatch.setattr(validation, "TRADE_HISTORY_PATH", hist)
        st = base_state([trade(1), trade(2)])    # state'te 2 kayıt
        checks = run_health_checks(st, base_config())
        bad = next(c for c in checks if c["name"] == "trade_history_consistent")
        assert not bad["ok"]

    def test_malformed_history_detected(self, tmp_path, monkeypatch):
        hist = tmp_path / "trade_history.json"
        hist.write_text('{"not": "a list"}')
        monkeypatch.setattr(validation, "TRADE_HISTORY_PATH", hist)
        checks = run_health_checks(base_state([trade(1)]), base_config())
        bad = next(c for c in checks if c["name"] == "trade_history_consistent")
        assert not bad["ok"]

    def test_cumulative_history_passes(self, tmp_path, monkeypatch):
        """History state'ten uzun olabilir (reset sonrası) → sağlıklı."""
        hist = tmp_path / "trade_history.json"
        hist.write_text(json.dumps([trade(1), trade(2), trade(3)]))
        monkeypatch.setattr(validation, "TRADE_HISTORY_PATH", hist)
        checks = run_health_checks(base_state([trade(1)]), base_config())
        good = next(c for c in checks if c["name"] == "trade_history_consistent")
        assert good["ok"]

    def test_missing_history_with_nonlegacy_trades_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validation, "TRADE_HISTORY_PATH", tmp_path / "yok.json")
        checks = run_health_checks(base_state([trade(5)]), base_config())
        bad = next(c for c in checks if c["name"] == "trade_history_consistent")
        assert not bad["ok"]

    def test_missing_history_legacy_only_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validation, "TRADE_HISTORY_PATH", tmp_path / "yok.json")
        st = base_state([trade(5, legacy_record=True)])
        checks = run_health_checks(st, base_config())
        good = next(c for c in checks if c["name"] == "trade_history_consistent")
        assert good["ok"]

    def test_high_network_errors_detected(self):
        st = base_state()
        st["network_errors"] = 100
        checks = run_health_checks(st, base_config())
        bad = next(c for c in checks if c["name"] == "network_errors_low")
        assert not bad["ok"]


# ── 5. --validate modu ────────────────────────────────────────────────────────

class TestValidationMode:
    def test_run_validation_healthy(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(validation, "EQUITY_CURVE_PATH", tmp_path / "eq.json")
        monkeypatch.setattr(validation, "SESSION_REPORT_PATH", tmp_path / "rep.json")
        ok, out = run_validation(
            state=base_state([trade(100), trade(-50)]), config=base_config())
        assert ok
        assert out["equity_points"] == 3
        assert (tmp_path / "eq.json").exists()
        assert (tmp_path / "rep.json").exists()
        printed = capsys.readouterr().out
        assert "SAĞLIK KONTROLLERİ" in printed
        assert "SAĞLIKLI" in printed

    def test_run_validation_unhealthy(self, capsys):
        st = base_state([trade(100)], balance=START + 999)
        ok, out = run_validation(state=st, config=base_config(), write_files=False)
        assert not ok
        assert "SORUN VAR" in capsys.readouterr().out

    def test_run_validation_no_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validation, "EQUITY_CURVE_PATH", tmp_path / "eq.json")
        monkeypatch.setattr(validation, "SESSION_REPORT_PATH", tmp_path / "rep.json")
        ok, _ = run_validation(state=base_state(), config=base_config(),
                               write_files=False)
        assert ok
        assert not (tmp_path / "eq.json").exists()
        assert not (tmp_path / "rep.json").exists()

    def test_cli_flag_exists(self):
        import alpha20
        src = (ROOT / "alpha20_v1" / "alpha20.py").read_text()
        assert "--validate" in src
