"""Mission 2100 — Agent 03: Değişmez kağıt defteri.

Her durum geçişi YENİ bir PaperLedgerSnapshot üretir; mutasyon
YOKTUR. Çift kayıt tutarlılığı her geçişte yapı gereği korunur:
nakit deltası daima −(maliyet deltası) + gerçekleşen K/Z deltası
− komisyon deltasıdır — yuvarlama kayması olamaz, nakit kaybolmaz.

Kısıtlar: kısa/short pozisyon yok; nakit ve pozisyon yetersizliği
steril kodla reddedilir; koleksiyonlar sınırlıdır; özyineleme,
zamanlayıcı, kayma/gecikme simülasyonu YOKTUR.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from execution_enums import OrderSide, OrderState
from paper_errors import PaperLedgerError
from paper_models import (PaperExecution, PaperFillPolicy,
                          PaperLedgerSnapshot, PaperOrder,
                          PaperPosition)

__all__ = ["PaperLedger", "MAXIMUM_HISTORY_LENGTH"]

_ERROR_LEDGER = "PAPER_LEDGER_VIOLATION"

_ZERO = Decimal("0")

# Sınırlı koleksiyon: emir/gerçekleşme geçmişi üst sınırı
MAXIMUM_HISTORY_LENGTH = 10000


def _violation(code: str) -> None:
    raise PaperLedgerError(f"{_ERROR_LEDGER}:{code}")


def _replace_position(snapshot: PaperLedgerSnapshot,
                      symbol: str,
                      position: PaperPosition | None
                      ) -> tuple:
    remaining = tuple(p for p in snapshot.positions
                      if p.symbol != symbol)
    if position is None:
        return remaining
    return remaining + (position,)


@dataclass(frozen=True, slots=True)
class PaperLedger:
    """Durumsuz defter işlemcisi — tüm işlemler yeni kopya döner."""

    def initial(self, quote_asset: str,
                cash: Decimal) -> PaperLedgerSnapshot:
        """Başlangıç anlık görüntüsü — boş geçmiş, sıfır rezerv."""
        return PaperLedgerSnapshot(
            quote_asset=quote_asset, initial_cash=cash,
            cash=cash, reserved_cash=_ZERO,
            realized_pnl=_ZERO, commission_paid=_ZERO)

    def reserve(self, snapshot: PaperLedgerSnapshot,
                amount: Decimal) -> PaperLedgerSnapshot:
        """Nakit rezerve eder — toplam nakit değişmez."""
        if isinstance(amount, bool) or \
                not isinstance(amount, Decimal) or \
                not amount.is_finite() or amount <= _ZERO:
            _violation("INVALID_AMOUNT")
        if amount > snapshot.cash:
            _violation("INSUFFICIENT_CASH")
        return replace(
            snapshot, cash=snapshot.cash - amount,
            reserved_cash=snapshot.reserved_cash + amount,
            sequence=snapshot.sequence + 1)

    def release(self, snapshot: PaperLedgerSnapshot,
                amount: Decimal) -> PaperLedgerSnapshot:
        """Rezervi serbest bırakır — toplam nakit değişmez."""
        if isinstance(amount, bool) or \
                not isinstance(amount, Decimal) or \
                not amount.is_finite() or amount <= _ZERO:
            _violation("INVALID_AMOUNT")
        if amount > snapshot.reserved_cash:
            _violation("INSUFFICIENT_RESERVED_CASH")
        return replace(
            snapshot, cash=snapshot.cash + amount,
            reserved_cash=snapshot.reserved_cash - amount,
            sequence=snapshot.sequence + 1)

    def apply_fill(self, snapshot: PaperLedgerSnapshot,
                   order: PaperOrder,
                   execution: PaperExecution
                   ) -> PaperLedgerSnapshot:
        """Dolumu deftere işler — YENİ anlık görüntü döner.

        BUY: maliyet + komisyon nakitten düşer, cost_basis artar.
        SELL: hasılat − komisyon nakde eklenir; serbest kalan
        maliyet, gerçekleşen K/Z ile aynı büyüklükte düşülür.
        """
        if not isinstance(order, PaperOrder):
            _violation("INVALID_ORDER")
        if not isinstance(execution, PaperExecution):
            _violation("INVALID_EXECUTION")
        if len(snapshot.orders) >= MAXIMUM_HISTORY_LENGTH or \
                len(snapshot.executions) >= \
                MAXIMUM_HISTORY_LENGTH:
            _violation("HISTORY_BOUND_EXCEEDED")
        if execution.order_reference != order.order_reference:
            _violation("EXECUTION_ORDER_MISMATCH")
        if execution.symbol != order.symbol or \
                execution.side is not order.side or \
                execution.quantity != order.quantity or \
                execution.price != order.price:
            _violation("EXECUTION_ORDER_MISMATCH")
        if order.state is not OrderState.FILLED:
            _violation("INVALID_ORDER_STATE")
        if order.fill_policy is not \
                PaperFillPolicy.IMMEDIATE_FULL_FILL:
            _violation("INVALID_FILL_POLICY")
        if snapshot.order_for(order.order_reference) \
                is not None:
            _violation("DUPLICATE_ORDER_REFERENCE")

        fee = execution.commission.amount
        notional = execution.notional
        position = snapshot.position_for(execution.symbol)

        if execution.side is OrderSide.BUY:
            required = notional + fee
            if required > snapshot.cash:
                _violation("INSUFFICIENT_CASH")
            if position is None:
                new_position = PaperPosition(
                    symbol=execution.symbol,
                    quantity=execution.quantity,
                    cost_basis=notional)
            else:
                new_position = PaperPosition(
                    symbol=execution.symbol,
                    quantity=(position.quantity
                              + execution.quantity),
                    cost_basis=(position.cost_basis + notional))
            new_cash = snapshot.cash - required
            new_realized = snapshot.realized_pnl
        else:
            if position is None or \
                    execution.quantity > position.quantity:
                _violation("INSUFFICIENT_POSITION")
            if execution.quantity == position.quantity:
                released = position.cost_basis
                new_position = None
            else:
                released = (position.cost_basis
                            * execution.quantity
                            / position.quantity)
                new_position = PaperPosition(
                    symbol=execution.symbol,
                    quantity=(position.quantity
                              - execution.quantity),
                    cost_basis=(position.cost_basis - released))
            new_cash = snapshot.cash + notional - fee
            new_realized = (snapshot.realized_pnl
                            + notional - released)
            if new_cash < _ZERO:
                _violation("NEGATIVE_CASH")

        return replace(
            snapshot,
            cash=new_cash,
            realized_pnl=new_realized,
            commission_paid=snapshot.commission_paid + fee,
            positions=_replace_position(
                snapshot, execution.symbol, new_position),
            orders=snapshot.orders + (order,),
            executions=snapshot.executions + (execution,),
            sequence=snapshot.sequence + 1)
