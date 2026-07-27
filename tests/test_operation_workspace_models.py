"""Mission 2200 Agent 02 — çalışma alanı görünüm modelleri testleri."""
from decimal import Decimal

import pytest

from operation_control_errors import OperationControlValidationError
import operation_workspace_models as wm

D = Decimal


def portfolio(**over):
    base = dict(
        portfolio_value=D("1000"), cash=D("500"), equity=D("1000"),
        daily_pnl=D("5"), weekly_pnl=D("10"), monthly_pnl=D("20"),
        open_risk=D("30"), exposure=D("200"), drawdown_pct=D("2"),
        largest_winner=D("9"), largest_loser=D("-4"),
        open_position_count=2, source_freshness="FRESH")
    base.update(over)
    return wm.PortfolioView(**base)


def performance(**over):
    base = dict(
        trade_count=2, win_count=1, loss_count=1, dropped_records=0,
        win_rate_pct=D("50"), loss_rate_pct=D("50"),
        average_win=D("9"), average_loss=D("-5"),
        profit_factor=D("1.8"), sharpe=D("0.5"),
        max_drawdown_pct=D("10"), average_hold_seconds=100,
        daily_profit=D("4"), weekly_profit=D("4"),
        monthly_profit=D("4"), equity_curve=((1, D("100")),))
    base.update(over)
    return wm.PerformanceView(**base)


def broker(**over):
    base = dict(
        heartbeat_state="OK", heartbeat_at=100, latency_ms=42,
        api_status="OK", rate_limit_state="OK", reconnect_count=None,
        synchronization_state="SYNCED",
        authentication_state="AUTHENTICATED",
        permission_state="READ_ONLY", data_age_seconds=5)
    base.update(over)
    return wm.BrokerHealthView(**base)


def strategy(**over):
    base = dict(
        symbol="BTCUSDT", strategy="alpha20_v1", state="ENABLED",
        direction="LONG", confidence_pct=D("91"),
        pnl_today=D("2.3"), entry_eligible=True,
        last_signal_at="2026-01-01T00:00:00Z",
        open_position_count=1)
    base.update(over)
    return wm.StrategyView(**base)


def journal(**over):
    base = dict(
        event_time="2026-01-01T00:00:00Z", kind="FILLED",
        symbol="BTCUSDT", detail="EXECUTE", status="OK",
        correlation_id="abc")
    base.update(over)
    return wm.JournalEventView(**base)


# ── PortfolioView ──────────────────────────────────────────────────

class TestPortfolioView:
    def test_valid(self):
        assert portfolio().open_position_count == 2

    def test_all_unknown_allowed(self):
        view = portfolio(
            portfolio_value=None, cash=None, equity=None,
            daily_pnl=None, weekly_pnl=None, monthly_pnl=None,
            open_risk=None, exposure=None, drawdown_pct=None,
            largest_winner=None, largest_loser=None,
            source_freshness="UNKNOWN")
        assert view.cash is None

    @pytest.mark.parametrize("field", [
        "portfolio_value", "cash", "equity", "daily_pnl",
        "weekly_pnl", "monthly_pnl", "open_risk", "exposure",
        "drawdown_pct", "largest_winner", "largest_loser"])
    @pytest.mark.parametrize("bad", [1.5, "10", 7, True])
    def test_money_fields_reject_non_decimal(self, field, bad):
        with pytest.raises(OperationControlValidationError):
            portfolio(**{field: bad})

    @pytest.mark.parametrize("bad", [-1, None, "2", 1.5, True])
    def test_count_rejects(self, bad):
        with pytest.raises(OperationControlValidationError):
            portfolio(open_position_count=bad)

    @pytest.mark.parametrize("bad", ["", None, 5])
    def test_freshness_rejects(self, bad):
        with pytest.raises(OperationControlValidationError):
            portfolio(source_freshness=bad)

    def test_nan_rejected(self):
        with pytest.raises(OperationControlValidationError):
            portfolio(cash=D("NaN"))

    def test_frozen(self):
        with pytest.raises(Exception):
            object.__getattribute__(portfolio(), "__dict__")
            portfolio().cash = D("1")  # type: ignore


# ── PerformanceView ────────────────────────────────────────────────

class TestPerformanceView:
    def test_valid(self):
        assert performance().trade_count == 2

    @pytest.mark.parametrize("field", [
        "trade_count", "win_count", "loss_count", "dropped_records"])
    @pytest.mark.parametrize("bad", [-1, None, "2", 1.5, True])
    def test_counts_reject(self, field, bad):
        with pytest.raises(OperationControlValidationError):
            performance(**{field: bad})

    @pytest.mark.parametrize("field", [
        "win_rate_pct", "loss_rate_pct", "average_win",
        "average_loss", "profit_factor", "sharpe",
        "max_drawdown_pct", "daily_profit", "weekly_profit",
        "monthly_profit"])
    def test_optional_decimal_none_ok(self, field):
        assert getattr(performance(**{field: None}), field) is None

    @pytest.mark.parametrize("field", [
        "win_rate_pct", "profit_factor", "sharpe", "daily_profit"])
    @pytest.mark.parametrize("bad", [1.5, "1", 3, True])
    def test_optional_decimal_rejects(self, field, bad):
        with pytest.raises(OperationControlValidationError):
            performance(**{field: bad})

    @pytest.mark.parametrize("bad", [-5, "x", 1.5, True])
    def test_hold_rejects(self, bad):
        with pytest.raises(OperationControlValidationError):
            performance(average_hold_seconds=bad)

    @pytest.mark.parametrize("bad_curve", [
        [(1, D("1"))],                 # list, tuple değil
        ((1,),),                       # eksik öğe
        (("x", D("1")),),              # zaman metin
        ((1, "100"),),                 # equity metin
        ((True, D("1")),),             # bool zaman
        ((1, 100.5),),                 # float equity
    ])
    def test_curve_rejects(self, bad_curve):
        with pytest.raises(OperationControlValidationError):
            performance(equity_curve=bad_curve)

    def test_empty_curve_ok(self):
        assert performance(equity_curve=()).equity_curve == ()


# ── BrokerHealthView ───────────────────────────────────────────────

class TestBrokerHealthView:
    def test_valid(self):
        assert broker().latency_ms == 42

    @pytest.mark.parametrize("field", [
        "heartbeat_state", "api_status", "rate_limit_state",
        "synchronization_state", "authentication_state",
        "permission_state"])
    def test_state_outside_set_rejected(self, field):
        with pytest.raises(OperationControlValidationError):
            broker(**{field: "CONNECTED"})  # 'Connected' yasak!

    @pytest.mark.parametrize("field", [
        "heartbeat_state", "api_status"])
    @pytest.mark.parametrize("bad", ["", None, 5])
    def test_state_type_rejected(self, field, bad):
        with pytest.raises(OperationControlValidationError):
            broker(**{field: bad})

    @pytest.mark.parametrize("field", [
        "heartbeat_at", "latency_ms", "reconnect_count",
        "data_age_seconds"])
    def test_optional_ints_none_ok(self, field):
        assert getattr(broker(**{field: None}), field) is None

    @pytest.mark.parametrize("field", ["latency_ms", "reconnect_count"])
    @pytest.mark.parametrize("bad", [-1, "5", 1.5, True])
    def test_optional_ints_reject(self, field, bad):
        with pytest.raises(OperationControlValidationError):
            broker(**{field: bad})

    @pytest.mark.parametrize("state", sorted(wm.BROKER_STATES))
    def test_all_allowed_states_accepted(self, state):
        assert broker(api_status=state).api_status == state


# ── StrategyView ───────────────────────────────────────────────────

class TestStrategyView:
    def test_valid(self):
        assert strategy().symbol == "BTCUSDT"

    @pytest.mark.parametrize("field", [
        "symbol", "strategy", "state", "direction", "last_signal_at"])
    @pytest.mark.parametrize("bad", ["", None, 5])
    def test_text_rejects(self, field, bad):
        with pytest.raises(OperationControlValidationError):
            strategy(**{field: bad})

    @pytest.mark.parametrize("field", ["confidence_pct", "pnl_today"])
    def test_optional_none_ok(self, field):
        assert getattr(strategy(**{field: None}), field) is None

    @pytest.mark.parametrize("field", ["confidence_pct", "pnl_today"])
    @pytest.mark.parametrize("bad", [1.5, "5", 3])
    def test_optional_rejects(self, field, bad):
        with pytest.raises(OperationControlValidationError):
            strategy(**{field: bad})

    @pytest.mark.parametrize("bad", ["yes", 1, None])
    def test_eligible_rejects_non_bool(self, bad):
        with pytest.raises(OperationControlValidationError):
            strategy(entry_eligible=bad)

    @pytest.mark.parametrize("bad", [-1, None, "1", 1.5, True])
    def test_count_rejects(self, bad):
        with pytest.raises(OperationControlValidationError):
            strategy(open_position_count=bad)


# ── JournalEventView ───────────────────────────────────────────────

class TestJournalEventView:
    def test_valid(self):
        assert journal().kind == "FILLED"

    @pytest.mark.parametrize("kind", sorted(wm.JOURNAL_KINDS))
    def test_all_kinds_accepted(self, kind):
        assert journal(kind=kind).kind == kind

    @pytest.mark.parametrize("bad", [
        "HACKED", "filled", "unknown-kind", "SIGNAL"])
    def test_kind_outside_set_rejected(self, bad):
        with pytest.raises(OperationControlValidationError):
            journal(kind=bad)

    @pytest.mark.parametrize("field", [
        "event_time", "symbol", "detail", "status", "correlation_id"])
    @pytest.mark.parametrize("bad", ["", None, 7])
    def test_text_rejects(self, field, bad):
        with pytest.raises(OperationControlValidationError):
            journal(**{field: bad})

    def test_error_code_is_sterile(self):
        try:
            journal(kind="EVIL")
        except OperationControlValidationError as exc:
            assert "INVALID_WORKSPACE_FIELD:kind" in str(exc)
            assert "EVIL" not in str(exc)
