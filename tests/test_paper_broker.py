"""Mission 2100 — Agent 03: Kağıt broker testleri.

Kapsam: durumsuzluk, deterministik anında tam dolum, emir kuralları
(tekrar/bilinmeyen sembol/geçersiz yön/durum), fiyat modeli (kesin
gönderilen fiyat), komisyon arayüzü, görünüm metotları ve kalp atışı.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import paper_broker
from execution_enums import OrderSide, OrderState
from paper_broker import (PaperBroker, PaperCommissionModel,
                          ZeroCommissionModel, ZERO_COMMISSION)
from paper_errors import (PaperContractError, PaperLedgerError,
                          PaperOrderError)
from paper_ledger import PaperLedger
from paper_models import (PaperBalance, PaperCommission,
                          PaperFillPolicy)
from runtime_enums import HeartbeatStatus

D = Decimal
LEDGER = PaperLedger()
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def _broker(**over):
    base = dict(known_symbols=SYMBOLS)
    base.update(over)
    return PaperBroker(**base)


def _snapshot(cash="1000"):
    return LEDGER.initial("USDT", D(cash))


def _submit(broker=None, snapshot=None, ref="ord-1",
            symbol="BTCUSDT", side=OrderSide.BUY, qty="1",
            price="100"):
    broker = broker if broker is not None else _broker()
    snapshot = snapshot if snapshot is not None else _snapshot()
    return broker.submit(snapshot, ref, symbol, side, D(qty),
                         D(price))


class FlatFeeModel(PaperCommissionModel):
    """Test modeli: sabit 1 birim komisyon."""

    def commission_for(self, symbol, side, quantity, price,
                       quote_asset):
        return PaperCommission(amount=D("1"),
                               asset=quote_asset)


class BrokenFeeModel(PaperCommissionModel):
    """Test modeli: sözleşme dışı dönüş."""

    def commission_for(self, symbol, side, quantity, price,
                       quote_asset):
        return "not-a-commission"


# ── Broker kurulumu ─────────────────────────────────────────────────

class TestBrokerConstruction:
    def test_valid_construction(self):
        broker = _broker()
        assert broker.known_symbols == SYMBOLS
        assert broker.commission_model is ZERO_COMMISSION
        assert broker.fill_policy is \
            PaperFillPolicy.IMMEDIATE_FULL_FILL

    @pytest.mark.parametrize("symbols", [
        (), ["BTCUSDT"], ("",), ("  ",), (1,), None,
        ("BTCUSDT", "BTCUSDT")])
    def test_invalid_symbols_rejected(self, symbols):
        with pytest.raises(PaperContractError):
            _broker(known_symbols=symbols)

    @pytest.mark.parametrize("model", [None, "zero", 1])
    def test_invalid_commission_model_rejected(self, model):
        with pytest.raises(PaperContractError):
            _broker(commission_model=model)

    def test_non_immediate_policy_rejected(self):
        with pytest.raises(PaperContractError):
            _broker(fill_policy="IMMEDIATE_FULL_FILL")

    def test_broker_is_frozen(self):
        broker = _broker()
        with pytest.raises(Exception):
            broker.known_symbols = ("X",)

    def test_interface_is_abstract(self):
        with pytest.raises(NotImplementedError):
            PaperCommissionModel().commission_for(
                "BTCUSDT", OrderSide.BUY, D("1"), D("1"),
                "USDT")

    def test_zero_commission_default(self):
        commission = ZERO_COMMISSION.commission_for(
            "BTCUSDT", OrderSide.BUY, D("1"), D("100"), "USDT")
        assert commission.amount == D("0")
        assert commission.asset == "USDT"

    def test_zero_commission_is_model(self):
        assert isinstance(ZERO_COMMISSION, ZeroCommissionModel)
        assert isinstance(ZERO_COMMISSION,
                          PaperCommissionModel)


# ── Gönderim ve anında dolum ────────────────────────────────────────

class TestSubmit:
    def test_immediate_full_fill(self):
        snap = _submit()
        order = snap.order_for("ord-1")
        assert order.state is OrderState.FILLED
        assert len(snap.executions) == 1
        assert snap.executions[0].quantity == D("1")

    def test_execution_price_is_submitted_price(self):
        snap = _submit(price="123.45")
        assert snap.executions[0].price == D("123.45")
        assert snap.orders[0].price == D("123.45")

    def test_no_partial_fill(self):
        snap = _submit(qty="5", price="100")
        assert snap.executions[0].quantity == D("5")
        assert snap.order_for("ord-1").state is \
            OrderState.FILLED

    def test_cash_updated(self):
        snap = _submit(qty="2", price="100")
        assert snap.cash == D("800")
        assert snap.audit()

    def test_deterministic_execution_reference(self):
        snap = _submit(ref="ord-7")
        assert snap.executions[0].execution_reference == \
            "ord-7:1"

    def test_stateless_broker_original_untouched(self):
        broker = _broker()
        original = _snapshot()
        broker.submit(original, "ord-1", "BTCUSDT",
                      OrderSide.BUY, D("1"), D("100"))
        assert original.orders == ()
        assert original.cash == D("1000")

    def test_same_inputs_same_result(self):
        broker = _broker()
        snap_a = broker.submit(_snapshot(), "ord-1", "BTCUSDT",
                               OrderSide.BUY, D("1"), D("100"))
        snap_b = broker.submit(_snapshot(), "ord-1", "BTCUSDT",
                               OrderSide.BUY, D("1"), D("100"))
        assert snap_a == snap_b

    def test_buy_then_sell_roundtrip(self):
        broker = _broker()
        snap = broker.submit(_snapshot(), "ord-1", "BTCUSDT",
                             OrderSide.BUY, D("2"), D("100"))
        snap = broker.submit(snap, "ord-2", "BTCUSDT",
                             OrderSide.SELL, D("2"), D("110"))
        assert snap.realized_pnl == D("20")
        assert snap.cash == D("1020")
        assert snap.positions == ()
        assert snap.audit()

    def test_commission_model_applied(self):
        broker = _broker(commission_model=FlatFeeModel())
        snap = broker.submit(_snapshot(), "ord-1", "BTCUSDT",
                             OrderSide.BUY, D("1"), D("100"))
        assert snap.cash == D("899")
        assert snap.commission_paid == D("1")
        assert snap.audit()

    def test_broken_commission_model_rejected(self):
        broker = _broker(commission_model=BrokenFeeModel())
        with pytest.raises(PaperContractError):
            broker.submit(_snapshot(), "ord-1", "BTCUSDT",
                          OrderSide.BUY, D("1"), D("100"))

    def test_logical_sequence_assigned(self):
        snap = _submit()
        assert snap.orders[0].logical_sequence == 1
        assert snap.executions[0].logical_sequence == 1
        assert snap.sequence == 1

    def test_insufficient_cash_propagates(self):
        with pytest.raises(PaperLedgerError):
            _submit(snapshot=_snapshot(cash="10"), qty="1",
                    price="100")


# ── Emir kuralları ──────────────────────────────────────────────────

class TestOrderRules:
    @pytest.mark.parametrize("qty", [
        D("0"), D("-1"), D("NaN"), D("Infinity")])
    def test_invalid_quantity_rejected(self, qty):
        with pytest.raises(PaperOrderError) as excinfo:
            _broker().submit(_snapshot(), "ord-1", "BTCUSDT",
                             OrderSide.BUY, qty, D("100"))
        assert "INVALID_QUANTITY" in str(excinfo.value)

    @pytest.mark.parametrize("qty", [1, True, 1.5, "1", None])
    def test_non_decimal_quantity_rejected(self, qty):
        with pytest.raises(PaperOrderError):
            _broker().submit(_snapshot(), "ord-1", "BTCUSDT",
                             OrderSide.BUY, qty, D("100"))

    @pytest.mark.parametrize("price", [
        D("0"), D("-1"), D("NaN"), D("Infinity"), 100, True,
        1.5, "100", None])
    def test_invalid_price_rejected(self, price):
        with pytest.raises(PaperOrderError):
            _broker().submit(_snapshot(), "ord-1", "BTCUSDT",
                             OrderSide.BUY, D("1"), price)

    def test_duplicate_order_id_rejected(self):
        broker = _broker()
        snap = broker.submit(_snapshot(), "ord-1", "BTCUSDT",
                             OrderSide.BUY, D("1"), D("100"))
        with pytest.raises(PaperOrderError) as excinfo:
            broker.submit(snap, "ord-1", "BTCUSDT",
                          OrderSide.BUY, D("1"), D("100"))
        assert "DUPLICATE_ORDER_ID" in str(excinfo.value)

    @pytest.mark.parametrize("symbol", [
        "DOGEUSDT", "", "  ", None, 1])
    def test_unknown_symbol_rejected(self, symbol):
        with pytest.raises(PaperOrderError) as excinfo:
            _broker().submit(_snapshot(), "ord-1", symbol,
                             OrderSide.BUY, D("1"), D("100"))
        assert "UNKNOWN_SYMBOL" in str(excinfo.value)

    @pytest.mark.parametrize("side", [
        "BUY", None, 1, OrderState.FILLED])
    def test_invalid_side_rejected(self, side):
        with pytest.raises(PaperOrderError) as excinfo:
            _broker().submit(_snapshot(), "ord-1", "BTCUSDT",
                             side, D("1"), D("100"))
        assert "INVALID_SIDE" in str(excinfo.value)

    @pytest.mark.parametrize("reference", ["", "   ", None, 1])
    def test_invalid_order_reference_rejected(self, reference):
        with pytest.raises(PaperOrderError):
            _broker().submit(_snapshot(), reference, "BTCUSDT",
                             OrderSide.BUY, D("1"), D("100"))

    def test_invalid_snapshot_rejected(self):
        with pytest.raises(PaperContractError):
            _broker().submit("snapshot", "ord-1", "BTCUSDT",
                             OrderSide.BUY, D("1"), D("100"))

    def test_rejection_leaves_no_trace(self):
        broker = _broker()
        snap = _snapshot()
        with pytest.raises(PaperOrderError):
            broker.submit(snap, "ord-1", "DOGEUSDT",
                          OrderSide.BUY, D("1"), D("100"))
        assert snap.orders == ()
        assert snap.cash == D("1000")

    def test_sterile_rejection_prefix(self):
        with pytest.raises(PaperOrderError) as excinfo:
            _broker().submit(_snapshot(), "", "BTCUSDT",
                             OrderSide.BUY, D("1"), D("100"))
        assert str(excinfo.value).startswith(
            "PAPER_ORDER_REJECTED:")


# ── İptal ───────────────────────────────────────────────────────────

class TestCancel:
    def test_cancel_unknown_order(self):
        with pytest.raises(PaperOrderError) as excinfo:
            _broker().cancel(_snapshot(), "ord-x")
        assert "UNKNOWN_ORDER" in str(excinfo.value)

    def test_cancel_filled_order_invalid_state(self):
        snap = _submit()
        with pytest.raises(PaperOrderError) as excinfo:
            _broker().cancel(snap, "ord-1")
        assert "INVALID_STATE" in str(excinfo.value)

    @pytest.mark.parametrize("reference", ["", "  ", None, 1])
    def test_cancel_invalid_reference(self, reference):
        with pytest.raises(PaperOrderError):
            _broker().cancel(_snapshot(), reference)

    def test_cancel_invalid_snapshot(self):
        with pytest.raises(PaperContractError):
            _broker().cancel(None, "ord-1")

    def test_cancel_never_mutates(self):
        snap = _submit()
        with pytest.raises(PaperOrderError):
            _broker().cancel(snap, "ord-1")
        assert snap.order_for("ord-1").state is \
            OrderState.FILLED


# ── Görünümler ve kalp atışı ────────────────────────────────────────

class TestViews:
    def test_balance_view(self):
        balance = _broker().balance(_submit(qty="2",
                                            price="100"))
        assert isinstance(balance, PaperBalance)
        assert balance.asset == "USDT"
        assert balance.free == D("800")
        assert balance.reserved == D("0")
        assert balance.total == D("800")

    def test_positions_view(self):
        positions = _broker().positions(_submit())
        assert len(positions) == 1
        assert positions[0].symbol == "BTCUSDT"

    def test_orders_view(self):
        orders = _broker().orders(_submit())
        assert [o.order_reference for o in orders] == ["ord-1"]

    def test_executions_view(self):
        executions = _broker().executions(_submit())
        assert len(executions) == 1

    def test_empty_views(self):
        broker = _broker()
        snap = _snapshot()
        assert broker.positions(snap) == ()
        assert broker.orders(snap) == ()
        assert broker.executions(snap) == ()

    @pytest.mark.parametrize("method", [
        "balance", "positions", "orders", "executions",
        "heartbeat"])
    def test_views_reject_invalid_snapshot(self, method):
        with pytest.raises(PaperContractError):
            getattr(_broker(), method)("snapshot")

    def test_heartbeat_ok(self):
        assert _broker().heartbeat(_submit()) is \
            HeartbeatStatus.OK

    def test_heartbeat_error_on_broken_ledger(self):
        from paper_models import PaperLedgerSnapshot
        broken = PaperLedgerSnapshot(
            quote_asset="USDT", initial_cash=D("1000"),
            cash=D("999"), reserved_cash=D("0"),
            realized_pnl=D("0"), commission_paid=D("0"))
        assert _broker().heartbeat(broken) is \
            HeartbeatStatus.ERROR

    def test_public_exports(self):
        assert paper_broker.__all__ == [
            "PaperBroker", "PaperCommissionModel",
            "ZeroCommissionModel", "ZERO_COMMISSION"]
