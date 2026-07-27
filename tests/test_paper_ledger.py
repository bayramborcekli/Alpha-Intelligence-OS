"""Mission 2100 — Agent 03: Kağıt defter testleri.

Kapsam: model doğrulaması, değişmezlik, çift kayıt tutarlılığı,
nakit korunumu, pozisyon/ortalama fiyat/gerçekleşen K/Z matematiği,
rezerv işlemleri, geçmiş sınırı, denetim ve anlık görüntü zinciri.
"""

from __future__ import annotations

import os
import sys
from dataclasses import FrozenInstanceError, fields
from decimal import Decimal

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import paper_ledger
from execution_enums import OrderSide, OrderState, PositionSide
from paper_errors import (PaperContractError, PaperDomainError,
                          PaperLedgerError, PaperOrderError)
from paper_ledger import MAXIMUM_HISTORY_LENGTH, PaperLedger
from paper_models import (PaperBalance, PaperCommission,
                          PaperExecution, PaperFillPolicy,
                          PaperLedgerSnapshot, PaperOrder,
                          PaperPosition, PaperStatistics)

D = Decimal
LEDGER = PaperLedger()


def _snapshot(cash="1000", **over):
    base = dict(quote_asset="USDT", initial_cash=D(cash),
                cash=D(cash), reserved_cash=D("0"),
                realized_pnl=D("0"), commission_paid=D("0"))
    base.update(over)
    return PaperLedgerSnapshot(**base)


def _commission(amount="0"):
    return PaperCommission(amount=D(amount), asset="USDT")


def _order(ref="ord-1", side=OrderSide.BUY, qty="1",
           price="100", state=OrderState.FILLED, symbol="BTCUSDT"):
    return PaperOrder(order_reference=ref, symbol=symbol,
                      side=side, quantity=D(qty), price=D(price),
                      state=state)


def _execution(ref="ord-1", side=OrderSide.BUY, qty="1",
               price="100", fee="0", symbol="BTCUSDT"):
    return PaperExecution(
        execution_reference=f"{ref}:1", order_reference=ref,
        symbol=symbol, side=side, quantity=D(qty),
        price=D(price), commission=_commission(fee))


def _buy(snapshot, ref="ord-1", qty="1", price="100", fee="0",
         symbol="BTCUSDT"):
    return LEDGER.apply_fill(
        snapshot,
        _order(ref=ref, side=OrderSide.BUY, qty=qty, price=price,
               symbol=symbol),
        _execution(ref=ref, side=OrderSide.BUY, qty=qty,
                   price=price, fee=fee, symbol=symbol))


def _sell(snapshot, ref="ord-2", qty="1", price="100", fee="0",
          symbol="BTCUSDT"):
    return LEDGER.apply_fill(
        snapshot,
        _order(ref=ref, side=OrderSide.SELL, qty=qty,
               price=price, symbol=symbol),
        _execution(ref=ref, side=OrderSide.SELL, qty=qty,
                   price=price, fee=fee, symbol=symbol))


# ── Model doğrulaması ───────────────────────────────────────────────

class TestPaperModelValidation:
    @pytest.mark.parametrize("overrides", [
        dict(quote_asset=""), dict(quote_asset=None),
        dict(initial_cash=D("-1")), dict(cash=D("-1")),
        dict(cash=D("NaN")), dict(cash=D("Infinity")),
        dict(initial_cash=1), dict(initial_cash=True),
        dict(reserved_cash="0"),
        dict(reserved_cash=D("-1")),
        dict(realized_pnl=D("NaN")),
        dict(realized_pnl=D("Infinity")),
        dict(realized_pnl=1), dict(realized_pnl=True),
        dict(commission_paid=D("-1")),
        dict(positions=[]), dict(positions=("x",)),
        dict(orders=[]), dict(orders=(1,)),
        dict(executions=[]), dict(executions=(1,)),
        dict(sequence=-1), dict(sequence=True),
        dict(sequence=D("1"))])
    def test_snapshot_rejects(self, overrides):
        with pytest.raises(PaperContractError):
            _snapshot(**overrides)

    def test_negative_realized_pnl_allowed(self):
        snap = _snapshot(realized_pnl=D("-5"),
                         commission_paid=D("0"),
                         cash=D("995"))
        assert snap.realized_pnl == D("-5")

    @pytest.mark.parametrize("overrides", [
        dict(symbol=""), dict(symbol=None),
        dict(quantity=D("0")), dict(quantity=D("-1")),
        dict(quantity=D("NaN")), dict(quantity=1),
        dict(quantity=True), dict(cost_basis=D("-1")),
        dict(cost_basis=D("NaN")), dict(cost_basis=1)])
    def test_position_rejects(self, overrides):
        base = dict(symbol="BTCUSDT", quantity=D("1"),
                    cost_basis=D("100"))
        base.update(overrides)
        with pytest.raises(PaperContractError):
            PaperPosition(**base)

    @pytest.mark.parametrize("overrides", [
        dict(order_reference=""), dict(symbol=""),
        dict(side="BUY"), dict(side=None),
        dict(quantity=D("0")), dict(quantity=D("-1")),
        dict(quantity=D("NaN")), dict(quantity=D("Infinity")),
        dict(quantity=1.5), dict(quantity=True),
        dict(price=D("0")), dict(price=D("-1")),
        dict(price=D("NaN")), dict(price=1.5),
        dict(state="FILLED"), dict(state=None),
        dict(fill_policy="IMMEDIATE_FULL_FILL"),
        dict(logical_sequence=-1)])
    def test_order_rejects(self, overrides):
        base = dict(order_reference="ord-1", symbol="BTCUSDT",
                    side=OrderSide.BUY, quantity=D("1"),
                    price=D("100"), state=OrderState.FILLED)
        base.update(overrides)
        with pytest.raises(PaperContractError):
            PaperOrder(**base)

    @pytest.mark.parametrize("overrides", [
        dict(execution_reference=""),
        dict(order_reference=""), dict(symbol=""),
        dict(side="BUY"), dict(quantity=D("0")),
        dict(quantity=D("-1")), dict(price=D("0")),
        dict(price=D("-1")), dict(price=D("NaN")),
        dict(commission=None), dict(commission="0")])
    def test_execution_rejects(self, overrides):
        base = dict(execution_reference="exe-1",
                    order_reference="ord-1", symbol="BTCUSDT",
                    side=OrderSide.BUY, quantity=D("1"),
                    price=D("100"), commission=_commission())
        base.update(overrides)
        with pytest.raises(PaperContractError):
            PaperExecution(**base)

    @pytest.mark.parametrize("overrides", [
        dict(amount=D("-1")), dict(amount=D("NaN")),
        dict(amount=1), dict(amount=True), dict(asset="")])
    def test_commission_rejects(self, overrides):
        base = dict(amount=D("0"), asset="USDT")
        base.update(overrides)
        with pytest.raises(PaperContractError):
            PaperCommission(**base)

    @pytest.mark.parametrize("overrides", [
        dict(asset=""), dict(free=D("-1")),
        dict(reserved=D("-1")), dict(free=1), dict(free=True)])
    def test_balance_rejects(self, overrides):
        base = dict(asset="USDT", free=D("1"), reserved=D("0"))
        base.update(overrides)
        with pytest.raises(PaperContractError):
            PaperBalance(**base)

    def test_duplicate_position_symbol_rejected(self):
        pos = PaperPosition(symbol="BTCUSDT", quantity=D("1"),
                            cost_basis=D("100"))
        with pytest.raises(PaperContractError):
            _snapshot(positions=(pos, pos))

    def test_duplicate_order_reference_rejected(self):
        with pytest.raises(PaperContractError):
            _snapshot(cash="800",
                      orders=(_order(), _order()))

    def test_sterile_error_code_prefix(self):
        with pytest.raises(PaperContractError) as excinfo:
            _snapshot(quote_asset="")
        assert str(excinfo.value).startswith(
            "INVALID_PAPER_MODEL_FIELD:")

    def test_error_hierarchy(self):
        assert issubclass(PaperContractError, PaperDomainError)
        assert issubclass(PaperOrderError, PaperDomainError)
        assert issubclass(PaperLedgerError, PaperDomainError)
        assert issubclass(PaperDomainError, Exception)


# ── Değişmezlik ─────────────────────────────────────────────────────

class TestImmutability:
    def _instances(self):
        return [
            _snapshot(),
            PaperPosition(symbol="BTCUSDT", quantity=D("1"),
                          cost_basis=D("100")),
            _order(), _execution(), _commission(),
            PaperBalance(asset="USDT", free=D("1"),
                         reserved=D("0")),
            PaperStatistics(orders_submitted=0, orders_filled=0,
                            executions_recorded=0,
                            gross_notional=D("0"),
                            commission_paid=D("0"),
                            realized_pnl=D("0")),
        ]

    @pytest.mark.parametrize("index", range(7))
    def test_frozen(self, index):
        instance = self._instances()[index]
        with pytest.raises(FrozenInstanceError):
            setattr(instance, fields(instance)[0].name, "x")

    @pytest.mark.parametrize("index", range(7))
    def test_slots(self, index):
        assert not hasattr(self._instances()[index], "__dict__")

    def test_apply_fill_returns_new_snapshot(self):
        original = _snapshot()
        result = _buy(original)
        assert result is not original
        assert original.cash == D("1000")
        assert original.orders == ()

    def test_snapshot_chain_sequences_increase(self):
        s0 = _snapshot()
        s1 = _buy(s0)
        s2 = _sell(s1)
        assert (s0.sequence, s1.sequence, s2.sequence) == \
            (0, 1, 2)

    def test_fill_policy_closed(self):
        assert [m.name for m in PaperFillPolicy] == \
            ["IMMEDIATE_FULL_FILL"]
        with pytest.raises(ValueError):
            PaperFillPolicy("PARTIAL_FILL")


# ── Nakit ve çift kayıt ─────────────────────────────────────────────

class TestCashConservation:
    def test_buy_reduces_cash_exactly(self):
        snap = _buy(_snapshot(), qty="2", price="100")
        assert snap.cash == D("800")

    def test_buy_with_fee(self):
        snap = _buy(_snapshot(), qty="1", price="100", fee="1")
        assert snap.cash == D("899")
        assert snap.commission_paid == D("1")

    def test_sell_adds_cash(self):
        snap = _sell(_buy(_snapshot(), qty="1", price="100"),
                     qty="1", price="150")
        assert snap.cash == D("1050")
        assert snap.realized_pnl == D("50")

    def test_audit_holds_after_every_step(self):
        snap = _snapshot()
        assert snap.audit()
        snap = _buy(snap, qty="3", price="100", fee="2")
        assert snap.audit()
        snap = _sell(snap, qty="1", price="120", fee="1")
        assert snap.audit()
        snap = _sell(snap, ref="ord-3", qty="2", price="90")
        assert snap.audit()

    def test_audit_holds_with_inexact_division(self):
        # 3 parçalı ortalama: bölünme kesin olmasa da
        # çift kayıt değişmezi YAPI GEREĞİ korunur
        snap = _buy(_snapshot(), qty="3", price="10")
        snap = _sell(snap, qty="1", price="11")
        assert snap.audit()
        snap = _sell(snap, ref="ord-3", qty="1", price="9")
        assert snap.audit()

    def test_cash_never_negative(self):
        with pytest.raises(PaperLedgerError):
            _buy(_snapshot(cash="99"), qty="1", price="100")

    def test_insufficient_cash_includes_fee(self):
        with pytest.raises(PaperLedgerError):
            _buy(_snapshot(cash="100"), qty="1", price="100",
                 fee="1")

    def test_exact_cash_accepted(self):
        snap = _buy(_snapshot(cash="100"), qty="1", price="100")
        assert snap.cash == D("0")
        assert snap.audit()

    def test_total_value_conserved_zero_fee(self):
        snap = _buy(_snapshot(), qty="4", price="50")
        # nakit + maliyet esası = başlangıç nakit
        assert snap.cash + snap.cost_basis_total == D("1000")

    def test_ledger_violation_sterile_code(self):
        with pytest.raises(PaperLedgerError) as excinfo:
            _buy(_snapshot(cash="1"), qty="1", price="100")
        assert str(excinfo.value).startswith(
            "PAPER_LEDGER_VIOLATION:")


# ── Pozisyon ve ortalama fiyat ──────────────────────────────────────

class TestPositionRules:
    def test_open_position(self):
        snap = _buy(_snapshot(), qty="2", price="100")
        position = snap.position_for("BTCUSDT")
        assert position.quantity == D("2")
        assert position.cost_basis == D("200")
        assert position.average_price == D("100")
        assert position.side is PositionSide.LONG

    def test_average_price_deterministic(self):
        snap = _buy(_snapshot(), qty="1", price="100")
        snap = _buy(snap, ref="ord-2", qty="1", price="200")
        assert snap.position_for("BTCUSDT").average_price == \
            D("150")

    def test_opening_updates_average_only(self):
        snap = _buy(_snapshot(), qty="1", price="100")
        snap = _buy(snap, ref="ord-2", qty="1", price="300")
        assert snap.realized_pnl == D("0")

    def test_closing_updates_realized_pnl(self):
        snap = _buy(_snapshot(), qty="2", price="100")
        snap = _sell(snap, qty="2", price="130")
        assert snap.realized_pnl == D("60")
        assert snap.position_for("BTCUSDT") is None

    def test_partial_close_releases_proportional_cost(self):
        snap = _buy(_snapshot(), qty="2", price="100")
        snap = _sell(snap, qty="1", price="150")
        position = snap.position_for("BTCUSDT")
        assert snap.realized_pnl == D("50")
        assert position.quantity == D("1")
        assert position.cost_basis == D("100")

    def test_loss_realized(self):
        snap = _buy(_snapshot(), qty="1", price="100")
        snap = _sell(snap, qty="1", price="80")
        assert snap.realized_pnl == D("-20")

    def test_full_close_removes_position(self):
        snap = _sell(_buy(_snapshot()), qty="1", price="100")
        assert snap.positions == ()

    def test_oversell_rejected(self):
        snap = _buy(_snapshot(), qty="1", price="100")
        with pytest.raises(PaperLedgerError):
            _sell(snap, qty="2", price="100")

    def test_sell_without_position_rejected(self):
        with pytest.raises(PaperLedgerError):
            _sell(_snapshot(), qty="1", price="100")

    def test_sell_other_symbol_rejected(self):
        snap = _buy(_snapshot())
        with pytest.raises(PaperLedgerError):
            _sell(snap, qty="1", price="100", symbol="ETHUSDT")

    def test_multi_symbol_positions_isolated(self):
        snap = _buy(_snapshot(), qty="1", price="100")
        snap = _buy(snap, ref="ord-2", qty="2", price="10",
                    symbol="ETHUSDT")
        assert snap.position_for("BTCUSDT").quantity == D("1")
        assert snap.position_for("ETHUSDT").quantity == D("2")
        assert snap.audit()

    def test_no_short_positions_exist(self):
        snap = _buy(_snapshot())
        for position in snap.positions:
            assert position.side is PositionSide.LONG
            assert position.quantity > D("0")

    def test_decimal_precision_preserved(self):
        snap = _buy(_snapshot(), qty="0.00000001",
                    price="50000")
        assert snap.position_for("BTCUSDT").cost_basis == \
            D("0.0005")


# ── Rezerv işlemleri ────────────────────────────────────────────────

class TestReserve:
    def test_reserve_moves_cash(self):
        snap = LEDGER.reserve(_snapshot(), D("100"))
        assert snap.cash == D("900")
        assert snap.reserved_cash == D("100")

    def test_release_returns_cash(self):
        snap = LEDGER.reserve(_snapshot(), D("100"))
        snap = LEDGER.release(snap, D("40"))
        assert snap.cash == D("940")
        assert snap.reserved_cash == D("60")

    def test_reserve_total_conserved(self):
        snap = LEDGER.reserve(_snapshot(), D("250"))
        assert snap.cash + snap.reserved_cash == D("1000")

    @pytest.mark.parametrize("amount", [
        D("0"), D("-1"), D("NaN"), D("Infinity"), 1, True,
        "100", None, 1.5])
    def test_reserve_invalid_amount(self, amount):
        with pytest.raises(PaperLedgerError):
            LEDGER.reserve(_snapshot(), amount)

    @pytest.mark.parametrize("amount", [
        D("0"), D("-1"), D("NaN"), 1, True, None])
    def test_release_invalid_amount(self, amount):
        with pytest.raises(PaperLedgerError):
            LEDGER.release(LEDGER.reserve(_snapshot(),
                                          D("10")), amount)

    def test_reserve_insufficient_cash(self):
        with pytest.raises(PaperLedgerError):
            LEDGER.reserve(_snapshot(cash="10"), D("11"))

    def test_release_insufficient_reserved(self):
        with pytest.raises(PaperLedgerError):
            LEDGER.release(_snapshot(), D("1"))


# ── Geçmiş, sınırlar, denetim ───────────────────────────────────────

class TestHistoryAndAudit:
    def test_history_appended(self):
        snap = _sell(_buy(_snapshot()), qty="1", price="100")
        assert len(snap.orders) == 2
        assert len(snap.executions) == 2

    def test_history_order_preserved(self):
        snap = _buy(_snapshot(), ref="ord-1")
        snap = _sell(snap, ref="ord-2", qty="1", price="100")
        assert [o.order_reference for o in snap.orders] == \
            ["ord-1", "ord-2"]

    def test_history_bound_enforced(self):
        orders = tuple(
            _order(ref=f"ord-{i}") for i in
            range(MAXIMUM_HISTORY_LENGTH))
        snap = _snapshot(cash="0", orders=orders)
        with pytest.raises(PaperLedgerError):
            _buy(snap, ref="ord-x", qty="1", price="1")

    def test_maximum_history_is_bounded_constant(self):
        assert isinstance(MAXIMUM_HISTORY_LENGTH, int)
        assert MAXIMUM_HISTORY_LENGTH > 0

    def test_execution_order_mismatch_rejected(self):
        with pytest.raises(PaperLedgerError):
            LEDGER.apply_fill(_snapshot(),
                              _order(ref="ord-1"),
                              _execution(ref="ord-2"))

    @pytest.mark.parametrize("execution", [
        _execution(symbol="ETHUSDT"),
        _execution(side=OrderSide.SELL),
        _execution(qty="2"),
        _execution(price="101")])
    def test_execution_field_mismatch_rejected(self, execution):
        with pytest.raises(PaperLedgerError) as excinfo:
            LEDGER.apply_fill(_snapshot(), _order(), execution)
        assert "EXECUTION_ORDER_MISMATCH" in str(excinfo.value)

    @pytest.mark.parametrize("state", [
        OrderState.CREATED, OrderState.SUBMITTED,
        OrderState.PARTIALLY_FILLED, OrderState.CANCELLED,
        OrderState.REJECTED])
    def test_non_filled_state_rejected(self, state):
        with pytest.raises(PaperLedgerError) as excinfo:
            LEDGER.apply_fill(_snapshot(), _order(state=state),
                              _execution())
        assert "INVALID_ORDER_STATE" in str(excinfo.value)

    def test_duplicate_order_reference_in_ledger_rejected(self):
        snap = _buy(_snapshot())
        with pytest.raises(PaperLedgerError) as excinfo:
            LEDGER.apply_fill(snap, _order(), _execution())
        assert "DUPLICATE_ORDER_REFERENCE" in str(excinfo.value)

    @pytest.mark.parametrize("order,execution", [
        (None, _execution()), ("order", _execution()),
        (_order(), None), (_order(), "execution")])
    def test_invalid_records_rejected(self, order, execution):
        with pytest.raises(PaperLedgerError):
            LEDGER.apply_fill(_snapshot(), order, execution)

    def test_initial_snapshot(self):
        snap = LEDGER.initial("USDT", D("500"))
        assert snap.cash == D("500")
        assert snap.initial_cash == D("500")
        assert snap.reserved_cash == D("0")
        assert snap.orders == ()
        assert snap.audit()

    def test_initial_rejects_invalid_cash(self):
        with pytest.raises(PaperContractError):
            LEDGER.initial("USDT", D("-1"))

    def test_audit_detects_manufactured_drift(self):
        snap = _snapshot(cash="999", initial_cash=D("1000"))
        assert not snap.audit()

    def test_unrealized_pnl_deterministic(self):
        snap = _buy(_snapshot(), qty="2", price="100")
        assert snap.unrealized_pnl(
            {"BTCUSDT": D("110")}) == D("20")
        assert snap.unrealized_pnl(
            {"BTCUSDT": D("90")}) == D("-20")

    def test_unrealized_pnl_missing_price_rejected(self):
        snap = _buy(_snapshot())
        with pytest.raises(PaperContractError):
            snap.unrealized_pnl({})

    @pytest.mark.parametrize("price", [
        D("0"), D("-1"), D("NaN"), 100, True, 1.5])
    def test_unrealized_pnl_invalid_price(self, price):
        snap = _buy(_snapshot())
        with pytest.raises(PaperContractError):
            snap.unrealized_pnl({"BTCUSDT": price})

    def test_statistics_derived(self):
        snap = _buy(_snapshot(), qty="1", price="100", fee="1")
        snap = _sell(snap, qty="1", price="120", fee="1")
        stats = snap.statistics()
        assert stats.orders_submitted == 2
        assert stats.orders_filled == 2
        assert stats.executions_recorded == 2
        assert stats.gross_notional == D("220")
        assert stats.commission_paid == D("2")
        assert stats.realized_pnl == D("20")

    def test_public_exports(self):
        assert paper_ledger.__all__ == [
            "PaperLedger", "MAXIMUM_HISTORY_LENGTH"]

    def test_determinism_same_inputs_same_snapshot(self):
        first = _sell(_buy(_snapshot()), qty="1", price="120")
        second = _sell(_buy(_snapshot()), qty="1", price="120")
        assert first == second
        assert hash(first) == hash(second)
