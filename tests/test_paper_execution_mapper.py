"""Mission 2100 — Agent 04: Kağıt yürütme eşleyici testleri."""

from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from execution_enums import (ExecutionStatus, OrderSide,
                             OrderState, OrderType, TimeInForce)
from execution_models import ExecutionMetadata, ExecutionRequest
from paper_execution_errors import PaperExecutionContractError
from paper_execution_mapper import PaperExecutionMapper
from paper_models import (PaperCommission, PaperExecution,
                          PaperLedgerSnapshot, PaperOrder,
                          PaperPosition)
from runtime_models import RuntimeAccountSnapshot

MAPPER = PaperExecutionMapper()


def _request(symbol="BTCUSDT", side=OrderSide.BUY,
             order_type=OrderType.LIMIT, quantity=D("2"),
             tif=TimeInForce.GTC, price=D("100"),
             metadata=None):
    return ExecutionRequest(symbol=symbol, side=side,
                            order_type=order_type,
                            quantity=quantity,
                            time_in_force=tif, price=price,
                            metadata=metadata)


def _paper_order(ref="ord-1", symbol="BTCUSDT",
                 side=OrderSide.BUY, qty="2", price="100",
                 state=OrderState.FILLED):
    return PaperOrder(order_reference=ref, symbol=symbol,
                      side=side, quantity=D(qty),
                      price=D(price), state=state)


def _paper_execution(ref="exe-1", order_ref="ord-1",
                     symbol="BTCUSDT", side=OrderSide.BUY,
                     qty="2", price="100", fee="0",
                     fee_asset="USDT"):
    return PaperExecution(
        execution_reference=ref, order_reference=order_ref,
        symbol=symbol, side=side, quantity=D(qty),
        price=D(price),
        commission=PaperCommission(amount=D(fee),
                                   asset=fee_asset))


def _snapshot(cash="1000", positions=()):
    return PaperLedgerSnapshot(
        quote_asset="USDT", initial_cash=D("1000"),
        cash=D(cash), reserved_cash=D("0"),
        realized_pnl=D("0"), commission_paid=D("0"),
        positions=positions, sequence=3)


class TestOrderInputMapping:
    def test_returns_exact_submitted_fields(self):
        result = MAPPER.order_input_for(_request())
        assert result == ("BTCUSDT", OrderSide.BUY, D("2"),
                          D("100"))

    @pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT",
                                        "SOLUSDT", "BNBUSDT"])
    def test_symbol_lossless(self, symbol):
        assert MAPPER.order_input_for(
            _request(symbol=symbol))[0] == symbol

    @pytest.mark.parametrize("side", [OrderSide.BUY,
                                      OrderSide.SELL])
    def test_side_lossless(self, side):
        assert MAPPER.order_input_for(
            _request(side=side))[1] is side

    @pytest.mark.parametrize("value", ["1", "0.5", "2.25",
                                       "1000", "0.00000001"])
    def test_quantity_lossless(self, value):
        assert MAPPER.order_input_for(
            _request(quantity=D(value)))[2] == D(value)

    @pytest.mark.parametrize("value", ["1", "0.5", "99.99",
                                       "123456.789"])
    def test_price_exact_no_adjustment(self, value):
        assert MAPPER.order_input_for(
            _request(price=D(value)))[3] == D(value)

    @pytest.mark.parametrize("bad", [None, object(), "req",
                                     1, ()])
    def test_non_request_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError) as exc:
            MAPPER.order_input_for(bad)
        assert "INVALID_PAPER_EXECUTION_FIELD:request" in \
            str(exc.value)

    def test_missing_price_rejected(self):
        with pytest.raises(PaperExecutionContractError) as exc:
            MAPPER.order_input_for(_request(price=None))
        assert "INVALID_PAPER_EXECUTION_FIELD:price" in \
            str(exc.value)

    @pytest.mark.parametrize("price", [D("0"), D("-1"),
                                       D("NaN"),
                                       D("Infinity"),
                                       D("-Infinity")])
    def test_invalid_price_rejected(self, price):
        with pytest.raises(PaperExecutionContractError) as exc:
            MAPPER.order_input_for(_request(price=price))
        assert ":price" in str(exc.value)

    @pytest.mark.parametrize("quantity", [D("0"), D("-2"),
                                          D("NaN"),
                                          D("Infinity")])
    def test_invalid_quantity_rejected(self, quantity):
        with pytest.raises(PaperExecutionContractError) as exc:
            MAPPER.order_input_for(
                _request(quantity=quantity))
        assert ":quantity" in str(exc.value)

    def test_deterministic(self):
        assert MAPPER.order_input_for(_request()) == \
            MAPPER.order_input_for(_request())


class TestExecutionResultMapping:
    def test_status_success(self):
        result = MAPPER.execution_result_for(
            _request(), _paper_order(), (_paper_execution(),))
        assert result.status is ExecutionStatus.SUCCESS

    def test_order_identity_lossless(self):
        result = MAPPER.execution_result_for(
            _request(), _paper_order(ref="ord-9"),
            (_paper_execution(order_ref="ord-9"),))
        assert result.order.order_id == "ord-9"

    def test_order_state_lossless(self):
        result = MAPPER.execution_result_for(
            _request(), _paper_order(), (_paper_execution(),))
        assert result.order.state is OrderState.FILLED

    def test_order_fields_lossless(self):
        result = MAPPER.execution_result_for(
            _request(), _paper_order(), (_paper_execution(),))
        order = result.order
        assert order.symbol == "BTCUSDT"
        assert order.side is OrderSide.BUY
        assert order.quantity == D("2")
        assert order.price == D("100")
        assert order.filled_quantity == D("2")

    @pytest.mark.parametrize("order_type", [
        OrderType.LIMIT, OrderType.MARKET,
        OrderType.STOP_LIMIT])
    def test_order_type_from_request(self, order_type):
        result = MAPPER.execution_result_for(
            _request(order_type=order_type), _paper_order(),
            (_paper_execution(),))
        assert result.order.order_type is order_type

    @pytest.mark.parametrize("tif", [TimeInForce.GTC,
                                     TimeInForce.IOC,
                                     TimeInForce.FOK])
    def test_time_in_force_from_request(self, tif):
        result = MAPPER.execution_result_for(
            _request(tif=tif), _paper_order(),
            (_paper_execution(),))
        assert result.order.time_in_force is tif

    def test_fill_fields_lossless(self):
        result = MAPPER.execution_result_for(
            _request(), _paper_order(),
            (_paper_execution(fee="0.25", fee_asset="USDT"),))
        fill = result.fills[0]
        assert fill.symbol == "BTCUSDT"
        assert fill.side is OrderSide.BUY
        assert fill.quantity == D("2")
        assert fill.price == D("100")
        assert fill.fee == D("0.25")
        assert fill.fee_asset == "USDT"
        assert fill.trade_id == "exe-1"

    def test_empty_executions_allowed(self):
        result = MAPPER.execution_result_for(
            _request(), _paper_order(), ())
        assert result.fills == ()

    def test_metadata_passthrough(self):
        metadata = ExecutionMetadata(correlation_id="corr-1")
        result = MAPPER.execution_result_for(
            _request(metadata=metadata), _paper_order(),
            (_paper_execution(),))
        assert result.metadata is metadata

    def test_metadata_absent_stays_none(self):
        result = MAPPER.execution_result_for(
            _request(), _paper_order(), (_paper_execution(),))
        assert result.metadata is None

    def test_no_error_code_on_success(self):
        result = MAPPER.execution_result_for(
            _request(), _paper_order(), (_paper_execution(),))
        assert result.code is None

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_request_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            MAPPER.execution_result_for(
                bad, _paper_order(), ())

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_order_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            MAPPER.execution_result_for(_request(), bad, ())

    @pytest.mark.parametrize("bad", [None, "x", 1,
                                     [(), ()], {}])
    def test_non_tuple_executions_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            MAPPER.execution_result_for(
                _request(), _paper_order(), bad)

    def test_foreign_execution_rejected(self):
        with pytest.raises(PaperExecutionContractError) as exc:
            MAPPER.execution_result_for(
                _request(), _paper_order(ref="ord-1"),
                (_paper_execution(order_ref="ord-2"),))
        assert ":executions" in str(exc.value)

    def test_non_execution_element_rejected(self):
        with pytest.raises(PaperExecutionContractError):
            MAPPER.execution_result_for(
                _request(), _paper_order(), (object(),))

    def test_deterministic(self):
        first = MAPPER.execution_result_for(
            _request(), _paper_order(), (_paper_execution(),))
        second = MAPPER.execution_result_for(
            _request(), _paper_order(), (_paper_execution(),))
        assert first == second


class TestAccountSnapshotMapping:
    def test_returns_runtime_account_snapshot(self):
        mapped = MAPPER.account_snapshot_for(_snapshot(),
                                             "acct-1")
        assert isinstance(mapped, RuntimeAccountSnapshot)
        assert mapped.account_reference == "acct-1"

    def test_balance_mapping(self):
        snap = PaperLedgerSnapshot(
            quote_asset="USDT", initial_cash=D("1000"),
            cash=D("700"), reserved_cash=D("100"),
            realized_pnl=D("0"), commission_paid=D("0"),
            positions=(PaperPosition(
                symbol="BTCUSDT", quantity=D("2"),
                cost_basis=D("200")),), sequence=5)
        mapped = MAPPER.account_snapshot_for(snap, "acct-1")
        balance = mapped.balances[0]
        assert balance.asset == "USDT"
        assert balance.free == D("700")
        assert balance.locked == D("100")

    def test_position_mapping_with_average_price(self):
        snap = _snapshot(positions=(PaperPosition(
            symbol="BTCUSDT", quantity=D("4"),
            cost_basis=D("400")),))
        mapped = MAPPER.account_snapshot_for(snap, "acct-1")
        position = mapped.positions[0]
        assert position.symbol == "BTCUSDT"
        assert position.quantity == D("4")
        assert position.entry_price == D("100")

    def test_multiple_positions_mapped(self):
        snap = _snapshot(positions=(
            PaperPosition(symbol="BTCUSDT", quantity=D("1"),
                          cost_basis=D("100")),
            PaperPosition(symbol="ETHUSDT", quantity=D("2"),
                          cost_basis=D("50"))))
        mapped = MAPPER.account_snapshot_for(snap, "acct-1")
        assert len(mapped.positions) == 2

    def test_empty_positions(self):
        mapped = MAPPER.account_snapshot_for(_snapshot(),
                                             "acct-1")
        assert mapped.positions == ()

    def test_sequence_carried(self):
        mapped = MAPPER.account_snapshot_for(_snapshot(),
                                             "acct-1")
        assert mapped.logical_sequence == 3

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_snapshot_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            MAPPER.account_snapshot_for(bad, "acct-1")

    @pytest.mark.parametrize("bad", [None, "", "   ", 1,
                                     object()])
    def test_bad_account_reference_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            MAPPER.account_snapshot_for(_snapshot(), bad)

    def test_side_effect_free(self):
        snap = _snapshot()
        before = snap
        MAPPER.account_snapshot_for(snap, "acct-1")
        assert snap == before and snap.positions == ()

    def test_deterministic(self):
        assert MAPPER.account_snapshot_for(
            _snapshot(), "acct-1") == \
            MAPPER.account_snapshot_for(_snapshot(), "acct-1")


class TestMapperContract:
    def test_mapper_is_frozen(self):
        with pytest.raises(Exception):
            object.__getattribute__(MAPPER, "__dict__")

    def test_mapper_hashable_and_stateless(self):
        assert hash(PaperExecutionMapper()) == \
            hash(PaperExecutionMapper())
        assert PaperExecutionMapper() == PaperExecutionMapper()
