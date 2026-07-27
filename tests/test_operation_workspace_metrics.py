"""Mission 2200 Agent 02 — performans metrik modülü testleri."""
from decimal import Decimal

import pytest

import operation_workspace_metrics as m

NOW = 1_000_000


def trade(pnl="10", fees="1", closed=NOW - 100, opened=NOW - 200,
          symbol="BTCUSDT"):
    return {"realized_pnl": pnl, "fees": fees, "closed_at": closed,
            "opened_at": opened, "symbol": symbol}


def eq(at, equity):
    return {"at": at, "equity": equity}


# ── parse_trades ───────────────────────────────────────────────────

class TestParseTrades:
    def test_valid_single(self):
        trades, dropped = m.parse_trades([trade()])
        assert len(trades) == 1 and dropped == 0
        t = trades[0]
        assert t.realized_pnl == Decimal("10")
        assert t.fees == Decimal("1")
        assert t.net_pnl == Decimal("9")
        assert t.symbol == "BTCUSDT"
        assert t.hold_seconds == 100

    @pytest.mark.parametrize("raw", [
        None, {}, "x", 5, True, object()])
    def test_non_list_input(self, raw):
        assert m.parse_trades(raw) == ((), 0)

    @pytest.mark.parametrize("bad", [
        "not-a-mapping", 7, None, [], True])
    def test_non_mapping_row_dropped(self, bad):
        trades, dropped = m.parse_trades([bad, trade()])
        assert len(trades) == 1 and dropped == 1

    @pytest.mark.parametrize("pnl", [
        None, "", "abc", 1.5, True, float("nan")])
    def test_bad_pnl_dropped(self, pnl):
        trades, dropped = m.parse_trades([trade(pnl=pnl)])
        assert trades == () and dropped == 1

    @pytest.mark.parametrize("closed", [
        None, "", "abc", -1, 1.5, True])
    def test_bad_closed_at_dropped(self, closed):
        trades, dropped = m.parse_trades([trade(closed=closed)])
        assert trades == () and dropped == 1

    @pytest.mark.parametrize("fees,expected", [
        ("2", Decimal("2")), (None, Decimal("0")),
        ("abc", Decimal("0")), ("-3", Decimal("0")),
        (1.5, Decimal("0"))])
    def test_fees_fallback_to_zero(self, fees, expected):
        trades, _ = m.parse_trades([trade(fees=fees)])
        assert trades[0].fees == expected

    @pytest.mark.parametrize("symbol,expected", [
        ("ethusdt", "ETHUSDT"), ("  sol  ", "SOL"),
        ("", "UNKNOWN"), (None, "UNKNOWN"), (5, "UNKNOWN")])
    def test_symbol_normalization(self, symbol, expected):
        trades, _ = m.parse_trades([trade(symbol=symbol)])
        assert trades[0].symbol == expected

    def test_sorted_by_closed_at(self):
        trades, _ = m.parse_trades([
            trade(closed=NOW - 1), trade(closed=NOW - 500)])
        assert trades[0].closed_at < trades[1].closed_at

    @pytest.mark.parametrize("opened,expected", [
        (NOW - 200, 100), (None, None), ("x", None),
        (NOW + 5, None), (True, None)])
    def test_hold_seconds(self, opened, expected):
        trades, _ = m.parse_trades([trade(opened=opened)])
        assert trades[0].hold_seconds == expected

    def test_int_pnl_accepted(self):
        trades, _ = m.parse_trades([trade(pnl=7)])
        assert trades[0].realized_pnl == Decimal("7")

    def test_decimal_pnl_accepted(self):
        trades, _ = m.parse_trades([trade(pnl=Decimal("7.5"))])
        assert trades[0].realized_pnl == Decimal("7.5")


# ── parse_equity_points ────────────────────────────────────────────

class TestParseEquityPoints:
    def test_valid(self):
        points = m.parse_equity_points([eq(1, "100"), eq(2, "110")])
        assert len(points) == 2
        assert points[0].equity == Decimal("100")

    @pytest.mark.parametrize("raw", [None, {}, "x", 5, True])
    def test_non_list(self, raw):
        assert m.parse_equity_points(raw) == ()

    @pytest.mark.parametrize("point", [
        eq(None, "100"), eq("x", "100"), eq(-1, "100"),
        eq(1, None), eq(1, "0"), eq(1, "-5"), eq(1, "abc"),
        eq(1, 1.5), "not-mapping", 5])
    def test_invalid_point_skipped(self, point):
        assert m.parse_equity_points([point]) == ()

    def test_sorted_by_time(self):
        points = m.parse_equity_points([eq(9, "1"), eq(3, "2")])
        assert [p.at for p in points] == [3, 9]


# ── period_profit ──────────────────────────────────────────────────

class TestPeriodProfit:
    def _trades(self, *specs):
        raw = [trade(pnl=p, fees="0", closed=c) for p, c in specs]
        return m.parse_trades(raw)[0]

    def test_no_trades_in_window_is_unknown(self):
        trades = self._trades(("10", NOW - m.WEEK_SECONDS * 2))
        assert m.period_profit(trades, NOW, m.DAY_SECONDS) is None

    def test_empty_is_unknown_not_zero(self):
        assert m.period_profit((), NOW, m.DAY_SECONDS) is None

    def test_sums_inside_window(self):
        trades = self._trades(("10", NOW - 100), ("-4", NOW - 200))
        assert m.period_profit(
            trades, NOW, m.DAY_SECONDS) == Decimal("6")

    def test_excludes_outside_window(self):
        trades = self._trades(("10", NOW - 100),
                              ("99", NOW - m.DAY_SECONDS - 1))
        assert m.period_profit(
            trades, NOW, m.DAY_SECONDS) == Decimal("10")

    def test_boundary_inclusive(self):
        trades = self._trades(("5", NOW - m.DAY_SECONDS))
        assert m.period_profit(
            trades, NOW, m.DAY_SECONDS) == Decimal("5")

    def test_future_trade_excluded(self):
        trades = self._trades(("5", NOW + 10))
        assert m.period_profit(trades, NOW, m.DAY_SECONDS) is None

    @pytest.mark.parametrize("now", [0, -5, "x", None, 1.5])
    def test_bad_now(self, now):
        trades = self._trades(("5", 100))
        assert m.period_profit(trades, now, m.DAY_SECONDS) is None

    def test_bad_window(self):
        trades = self._trades(("5", NOW - 1))
        assert m.period_profit(trades, NOW, 0) is None
        assert m.period_profit(trades, NOW, -1) is None

    def test_net_pnl_used(self):
        raw = [trade(pnl="10", fees="3", closed=NOW - 1)]
        trades, _ = m.parse_trades(raw)
        assert m.period_profit(
            trades, NOW, m.DAY_SECONDS) == Decimal("7")


# ── sharpe_ratio ───────────────────────────────────────────────────

class TestSharpe:
    def test_insufficient_returns_none(self):
        assert m.sharpe_ratio([]) is None
        assert m.sharpe_ratio([Decimal("0.1")]) is None

    def test_zero_std_returns_none(self):
        assert m.sharpe_ratio([Decimal("0.1"), Decimal("0.1")]) is None

    def test_positive(self):
        result = m.sharpe_ratio([Decimal("0.1"), Decimal("0.2"),
                                 Decimal("0.15")])
        assert result is not None and result > 0

    def test_negative(self):
        result = m.sharpe_ratio([Decimal("-0.1"), Decimal("-0.2"),
                                 Decimal("-0.15")])
        assert result is not None and result < 0

    def test_symmetric_zero_mean(self):
        assert m.sharpe_ratio(
            [Decimal("0.1"), Decimal("-0.1")]) == Decimal("0.0000")

    def test_non_decimal_ignored(self):
        assert m.sharpe_ratio(["x", 0.5]) is None


# ── equity_returns / max_drawdown ──────────────────────────────────

class TestEquityCurve:
    def test_returns(self):
        points = m.parse_equity_points(
            [eq(1, "100"), eq(2, "110"), eq(3, "99")])
        rets = m.equity_returns(points)
        assert rets[0] == Decimal("0.1")
        assert len(rets) == 2

    def test_returns_empty(self):
        assert m.equity_returns(()) == ()

    def test_drawdown_none_for_short(self):
        assert m.max_drawdown_pct(()) is None
        assert m.max_drawdown_pct(
            m.parse_equity_points([eq(1, "100")])) is None

    def test_drawdown_value(self):
        points = m.parse_equity_points(
            [eq(1, "100"), eq(2, "110"), eq(3, "99")])
        assert m.max_drawdown_pct(points) == Decimal("10.00")

    def test_drawdown_monotonic_up_is_zero(self):
        points = m.parse_equity_points(
            [eq(1, "100"), eq(2, "110"), eq(3, "120")])
        assert m.max_drawdown_pct(points) == Decimal("0.00")

    def test_drawdown_recovers_still_reports_worst(self):
        points = m.parse_equity_points(
            [eq(1, "100"), eq(2, "50"), eq(3, "200")])
        assert m.max_drawdown_pct(points) == Decimal("50.00")


# ── compute_metrics ────────────────────────────────────────────────

class TestComputeMetrics:
    def test_empty_everything_is_unknown(self):
        pm = m.compute_metrics([], [], NOW)
        assert pm.trade_count == 0
        assert pm.win_rate_pct is None
        assert pm.loss_rate_pct is None
        assert pm.average_win is None
        assert pm.average_loss is None
        assert pm.profit_factor is None
        assert pm.sharpe is None
        assert pm.max_drawdown_pct is None
        assert pm.average_hold_seconds is None
        assert pm.daily_profit is None
        assert pm.weekly_profit is None
        assert pm.monthly_profit is None
        assert pm.equity_curve == ()

    def test_counts(self):
        pm = m.compute_metrics([
            trade(pnl="10"), trade(pnl="-5"),
            trade(pnl="1", fees="1")], [], NOW)
        assert pm.trade_count == 3
        assert pm.win_count == 1
        assert pm.loss_count == 1
        assert pm.flat_count == 1

    def test_win_rate(self):
        pm = m.compute_metrics(
            [trade(pnl="10"), trade(pnl="-5")], [], NOW)
        assert pm.win_rate_pct == Decimal("50.00")
        assert pm.loss_rate_pct == Decimal("50.00")

    def test_average_win_loss_net_of_fees(self):
        pm = m.compute_metrics([
            trade(pnl="10", fees="1"),
            trade(pnl="-5", fees="0.5")], [], NOW)
        assert pm.average_win == Decimal("9.00000000")
        assert pm.average_loss == Decimal("-5.50000000")

    def test_profit_factor(self):
        pm = m.compute_metrics([
            trade(pnl="10", fees="0"),
            trade(pnl="-5", fees="0")], [], NOW)
        assert pm.profit_factor == Decimal("2.0000")

    def test_profit_factor_none_without_losses(self):
        pm = m.compute_metrics([trade(pnl="10")], [], NOW)
        assert pm.profit_factor is None

    def test_profit_factor_none_without_wins(self):
        pm = m.compute_metrics([trade(pnl="-10")], [], NOW)
        assert pm.profit_factor is None

    def test_dropped_records_visible(self):
        pm = m.compute_metrics([trade(), "junk", {}], [], NOW)
        assert pm.dropped_records == 2

    def test_average_hold(self):
        pm = m.compute_metrics([
            trade(opened=NOW - 300, closed=NOW - 100),
            trade(opened=NOW - 150, closed=NOW - 50)], [], NOW)
        assert pm.average_hold_seconds == 150

    def test_hold_unknown_when_no_open_times(self):
        pm = m.compute_metrics([trade(opened=None)], [], NOW)
        assert pm.average_hold_seconds is None

    def test_equity_curve_passthrough(self):
        pm = m.compute_metrics([], [eq(1, "100"), eq(2, "90")], NOW)
        assert len(pm.equity_curve) == 2
        assert pm.max_drawdown_pct == Decimal("10.00")

    def test_period_profits(self):
        pm = m.compute_metrics([
            trade(pnl="5", fees="0", closed=NOW - 100),
            trade(pnl="7", fees="0",
                  closed=NOW - m.DAY_SECONDS - 100),
            trade(pnl="11", fees="0",
                  closed=NOW - m.WEEK_SECONDS - 100)], [], NOW)
        assert pm.daily_profit == Decimal("5.00000000")
        assert pm.weekly_profit == Decimal("12.00000000")
        assert pm.monthly_profit == Decimal("23.00000000")

    @pytest.mark.parametrize("trades_raw", [None, "x", 42, {}])
    def test_garbage_trades_tolerated(self, trades_raw):
        pm = m.compute_metrics(trades_raw, [], NOW)
        assert pm.trade_count == 0

    @pytest.mark.parametrize("equity_raw", [None, "x", 42, {}])
    def test_garbage_equity_tolerated(self, equity_raw):
        pm = m.compute_metrics([], equity_raw, NOW)
        assert pm.equity_curve == ()

    def test_no_floats_anywhere(self):
        pm = m.compute_metrics(
            [trade(), trade(pnl="-2")],
            [eq(1, "100"), eq(2, "90")], NOW)
        for name in ("win_rate_pct", "loss_rate_pct", "average_win",
                     "average_loss", "profit_factor",
                     "max_drawdown_pct", "daily_profit"):
            value = getattr(pm, name)
            assert value is None or isinstance(value, Decimal), name

    @pytest.mark.parametrize("wins,losses,expected_rate", [
        (1, 0, "100.00"), (0, 1, "0.00"), (1, 1, "50.00"),
        (3, 1, "75.00"), (1, 3, "25.00"), (2, 3, "40.00"),
        (9, 1, "90.00"), (1, 9, "10.00"),
    ])
    def test_win_rate_table(self, wins, losses, expected_rate):
        raws = ([trade(pnl="5") for _ in range(wins)] +
                [trade(pnl="-5") for _ in range(losses)])
        pm = m.compute_metrics(raws, [], NOW)
        assert pm.win_rate_pct == Decimal(expected_rate)

    @pytest.mark.parametrize("gains,losses,expected_pf", [
        ("10", "-5", "2.0000"), ("5", "-10", "0.5000"),
        ("7", "-7", "1.0000"), ("1", "-4", "0.2500"),
    ])
    def test_profit_factor_table(self, gains, losses, expected_pf):
        pm = m.compute_metrics(
            [trade(pnl=gains, fees="0"), trade(pnl=losses, fees="0")],
            [], NOW)
        assert pm.profit_factor == Decimal(expected_pf)

    @pytest.mark.parametrize("window", [
        m.DAY_SECONDS, m.WEEK_SECONDS, m.MONTH_SECONDS])
    def test_window_constants_positive(self, window):
        assert isinstance(window, int) and window > 0

    def test_window_ordering(self):
        assert m.DAY_SECONDS < m.WEEK_SECONDS < m.MONTH_SECONDS

    def test_float_inputs_rejected_not_converted(self):
        pm = m.compute_metrics(
            [trade(pnl=1.23)], [eq(1, 100.5)], NOW)
        assert pm.trade_count == 0
        assert pm.dropped_records == 1
        assert pm.equity_curve == ()
