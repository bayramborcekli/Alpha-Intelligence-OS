"""Mission 2200 Agent 02 — çalışma alanı görünüm kurucusu testleri."""
from decimal import Decimal

import pytest

from operation_control_models import (
    DataFreshness, OperationAuditRecord, PositionView, ProductView,
    ReconciliationState, SignalView, SymbolAutomationState)
import operation_workspace_service as ws

D = Decimal
NOW = 1_000_000


def position(**over):
    base = dict(
        position_id="BTCUSDT", symbol="BTCUSDT", market="FUTURES",
        side="LONG", position_status="OPEN", strategy="alpha20_v1",
        entry_price=D("100"), current_price=D("110"),
        quantity=D("2"), notional_value=D("220"),
        realized_pnl=D("1"), unrealized_pnl=D("20"),
        pnl_percent=D("10"), fees=D("0.1"), stop_loss=D("95"),
        take_profit=D("120"), max_favorable_excursion=None,
        max_adverse_excursion=None, opened_at="2026-01-01T00:00:00Z",
        reconciliation_state=ReconciliationState.UNKNOWN,
        last_reconciled_at="UNKNOWN", execution_mode="PAPER")
    base.update(over)
    return PositionView(**base)


def product(**over):
    base = dict(
        symbol="BTCUSDT", market="FUTURES", strategy="alpha20_v1",
        automation_state=SymbolAutomationState.ENABLED,
        signal_state="UNKNOWN", execution_mode="PAPER",
        direction="LONG", entry_eligible=True,
        last_signal_at="UNKNOWN", last_decision="UNKNOWN",
        last_rejection_reason="-")
    base.update(over)
    return ProductView(**base)


def signal(**over):
    base = dict(
        signal_time="2026-01-01T00:00:00Z", symbol="BTCUSDT",
        strategy="alpha20_v1", direction="LONG",
        confidence=D("91"), decision="EXECUTE",
        risk_outcome="ALLOW", permission_outcome="GRANTED",
        rejection_code="-", execution_result="EXECUTED",
        correlation_id="c1", kind="PROPOSAL")
    base.update(over)
    return SignalView(**base)


def audit(**over):
    base = dict(
        timestamp=NOW, actor="op", action="AUTOMATION:START",
        target="global", previous_state="STOPPED",
        requested_state="RUNNING", result="COMPLETED",
        reason="-", correlation_id="corr1",
        idempotency_key="k1", error_code=None)
    base.update(over)
    return OperationAuditRecord(**base)


TRADES = [{"realized_pnl": "10", "fees": "1", "closed_at": NOW - 100,
           "opened_at": NOW - 200, "symbol": "BTCUSDT"}]


# ── Portföy ────────────────────────────────────────────────────────

class TestPortfolio:
    def test_empty_everything(self):
        view = ws.build_portfolio_view([], None, None, NOW)
        assert view.portfolio_value is None
        assert view.cash is None
        assert view.exposure is None
        assert view.open_risk is None
        assert view.largest_winner is None
        assert view.open_position_count == 0
        assert view.source_freshness == "UNKNOWN"

    def test_account_fields(self):
        view = ws.build_portfolio_view(
            [], {"portfolio_value": "1000", "cash": "400",
                 "equity": "1000", "drawdown_pct": "3"}, None, NOW)
        assert view.portfolio_value == D("1000")
        assert view.cash == D("400")
        assert view.equity == D("1000")
        assert view.drawdown_pct == D("3")

    def test_exposure_from_positions(self):
        view = ws.build_portfolio_view(
            [position(), position(symbol="ETHUSDT",
                                  position_id="ETHUSDT",
                                  notional_value=D("80"))],
            None, None, NOW)
        assert view.exposure == D("300")
        assert view.open_position_count == 2

    def test_exposure_unknown_when_no_notional(self):
        view = ws.build_portfolio_view(
            [position(notional_value=None)], None, None, NOW)
        assert view.exposure is None

    def test_open_risk_from_stops(self):
        view = ws.build_portfolio_view(
            [position()], None, None, NOW)
        # |100-95| * 2 = 10
        assert view.open_risk == D("10")

    def test_open_risk_unknown_if_any_stop_missing(self):
        view = ws.build_portfolio_view(
            [position(), position(symbol="ETHUSDT",
                                  position_id="ETHUSDT",
                                  stop_loss=None)],
            None, None, NOW)
        assert view.open_risk is None

    def test_largest_winner_loser(self):
        view = ws.build_portfolio_view(
            [position(realized_pnl=D("0"), unrealized_pnl=D("20")),
             position(symbol="ETHUSDT", position_id="ETHUSDT",
                      realized_pnl=D("0"),
                      unrealized_pnl=D("-7"))],
            None, None, NOW)
        assert view.largest_winner == D("20")
        assert view.largest_loser == D("-7")

    def test_pnl_windows_from_trades(self):
        view = ws.build_portfolio_view([], None, TRADES, NOW)
        assert view.daily_pnl == D("9.00000000")
        assert view.weekly_pnl == D("9.00000000")
        assert view.monthly_pnl == D("9.00000000")

    def test_no_trades_pnl_unknown(self):
        view = ws.build_portfolio_view([], None, [], NOW)
        assert view.daily_pnl is None

    def test_freshness_passthrough(self):
        view = ws.build_portfolio_view([], None, None, NOW,
                                       freshness="FRESH")
        assert view.source_freshness == "FRESH"

    @pytest.mark.parametrize("bad", ["", None, 5])
    def test_bad_freshness_falls_to_unknown(self, bad):
        view = ws.build_portfolio_view([], None, None, NOW,
                                       freshness=bad)
        assert view.source_freshness == "UNKNOWN"

    @pytest.mark.parametrize("account", ["x", 5, [], True])
    def test_non_mapping_account(self, account):
        view = ws.build_portfolio_view([], account, None, NOW)
        assert view.cash is None


# ── Performans görünümü ────────────────────────────────────────────

class TestPerformanceView:
    def test_maps_metrics(self):
        view = ws.build_performance_view(TRADES, [], NOW)
        assert view.trade_count == 1
        assert view.win_count == 1
        assert view.daily_profit == D("9.00000000")

    def test_empty_is_unknown(self):
        view = ws.build_performance_view([], [], NOW)
        assert view.win_rate_pct is None
        assert view.sharpe is None

    def test_curve_tuples(self):
        view = ws.build_performance_view(
            [], [{"at": 1, "equity": "100"},
                 {"at": 2, "equity": "90"}], NOW)
        assert view.equity_curve == ((1, D("100")), (2, D("90")))
        assert view.max_drawdown_pct == D("10.00")


# ── Broker sağlığı ─────────────────────────────────────────────────

class TestBrokerHealth:
    def test_empty_probe_all_unknown(self):
        view = ws.build_broker_health_view({}, NOW)
        assert view.heartbeat_state == "UNKNOWN"
        assert view.latency_ms is None
        assert view.api_status == "UNKNOWN"
        assert view.reconnect_count is None
        assert view.data_age_seconds is None

    @pytest.mark.parametrize("raw", [None, "x", 5, [], True])
    def test_non_mapping_probe(self, raw):
        assert ws.build_broker_health_view(
            raw, NOW).api_status == "UNKNOWN"

    def test_fresh_heartbeat_ok(self):
        view = ws.build_broker_health_view(
            {"heartbeat_at": NOW - 10}, NOW)
        assert view.heartbeat_state == "OK"
        assert view.data_age_seconds == 10

    def test_old_heartbeat_stale(self):
        view = ws.build_broker_health_view(
            {"heartbeat_at": NOW - 300}, NOW)
        assert view.heartbeat_state == "STALE"

    def test_states_normalized(self):
        view = ws.build_broker_health_view(
            {"api_status": "ok",
             "synchronization_state": " synced "}, NOW)
        assert view.api_status == "OK"
        assert view.synchronization_state == "SYNCED"

    def test_unlisted_state_falls_to_unknown(self):
        view = ws.build_broker_health_view(
            {"api_status": "CONNECTED"}, NOW)
        assert view.api_status == "UNKNOWN"

    def test_latency(self):
        view = ws.build_broker_health_view({"latency_ms": 42}, NOW)
        assert view.latency_ms == 42

    @pytest.mark.parametrize("bad", [-1, "42", 1.5, True, None])
    def test_bad_latency_unknown(self, bad):
        view = ws.build_broker_health_view({"latency_ms": bad}, NOW)
        assert view.latency_ms is None


# ── Strateji satırları ─────────────────────────────────────────────

class TestStrategyRows:
    def test_empty(self):
        assert ws.build_strategy_rows([], [], []) == ()

    def test_row_from_product(self):
        rows = ws.build_strategy_rows([product()], [], [])
        assert rows[0].symbol == "BTCUSDT"
        assert rows[0].state == "ENABLED"
        assert rows[0].pnl_today is None
        assert rows[0].confidence_pct is None

    def test_pnl_from_positions(self):
        rows = ws.build_strategy_rows(
            [product()], [position()], [])
        assert rows[0].pnl_today == D("21")   # 1 + 20
        assert rows[0].open_position_count == 1

    def test_pnl_ignores_other_symbols(self):
        rows = ws.build_strategy_rows(
            [product()], [position(symbol="ETHUSDT",
                                   position_id="ETHUSDT")], [])
        assert rows[0].pnl_today is None
        assert rows[0].open_position_count == 0

    def test_confidence_from_latest_signal(self):
        rows = ws.build_strategy_rows(
            [product()], [], [signal(confidence=D("77")),
                              signal(confidence=D("55"))])
        assert rows[0].confidence_pct == D("77")

    def test_unknown_pnl_positions(self):
        rows = ws.build_strategy_rows(
            [product()], [position(realized_pnl=None,
                                   unrealized_pnl=None)], [])
        assert rows[0].pnl_today is None
        assert rows[0].open_position_count == 1


# ── Günlük ─────────────────────────────────────────────────────────

class TestJournal:
    def test_empty(self):
        assert ws.build_journal_events([], []) == ()

    def test_signal_mapped_to_kind(self):
        events = ws.build_journal_events([signal()], [])
        assert events[0].kind == "FILLED"
        assert events[0].correlation_id == "c1"

    @pytest.mark.parametrize("execution,expected", [
        ("EXECUTED", "FILLED"), ("SUBMITTED", "SUBMITTED"),
        ("REJECTED", "REJECTED"), ("-", "SIGNAL_GENERATED")])
    def test_kind_mapping(self, execution, expected):
        events = ws.build_journal_events(
            [signal(execution_result=execution)], [])
        assert events[0].kind == expected

    def test_audit_becomes_operator_action(self):
        events = ws.build_journal_events([], [audit()])
        assert events[0].kind == "OPERATOR_ACTION"
        assert events[0].detail == "AUTOMATION:START"
        assert events[0].status == "COMPLETED"

    def test_limit_applied(self):
        signals = [signal(correlation_id=f"c{i}")
                   for i in range(10)]
        events = ws.build_journal_events(signals, [], limit=3)
        assert len(events) == 3

    @pytest.mark.parametrize("limit", [0, -5, "x", None, 1.5, True])
    def test_bad_limit_falls_back(self, limit):
        events = ws.build_journal_events([signal()], [],
                                         limit=limit)
        assert len(events) == 1

    def test_no_raw_exception_text(self):
        events = ws.build_journal_events([signal()], [audit()])
        for event in events:
            assert "Traceback" not in event.detail
