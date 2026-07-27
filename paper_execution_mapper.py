"""Mission 2100 — Agent 04: Kağıt yürütme eşleyicisi.

Deterministik, yan etkisiz, broker-bağımsız ve çerçeve-bağımsız
eşleme katmanı:

- ExecutionRequest → PaperBroker emir girdisi
- PaperOrder / PaperExecution → kanonik ExecutionResult
- PaperLedgerSnapshot → RuntimeAccountSnapshot uyumlu çıktı

Alanların örtüştüğü her yerde eşleme kayıpsızdır; hiçbir alan
uydurulmaz, kimlik/zaman üretilmez.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple

from execution_enums import ExecutionStatus, OrderSide
from execution_models import (ExecutionRequest, ExecutionResult,
                              Fill, Order)
from paper_execution_errors import PaperExecutionContractError
from paper_models import (PaperExecution, PaperLedgerSnapshot,
                          PaperOrder)
from runtime_models import (RuntimeAccountSnapshot,
                            RuntimeBalance, RuntimePosition)

__all__ = ["PaperExecutionMapper"]

_ERROR_INVALID_FIELD = "INVALID_PAPER_EXECUTION_FIELD"

_ZERO = Decimal("0")


def _fail(field: str) -> None:
    raise PaperExecutionContractError(
        f"{_ERROR_INVALID_FIELD}:{field}")


@dataclass(frozen=True, slots=True)
class PaperExecutionMapper:
    """Durumsuz eşleyici — yapılandırma alanı taşımaz."""

    def order_input_for(self, request: ExecutionRequest
                        ) -> Tuple[str, OrderSide, Decimal,
                                   Decimal]:
        """Kanonik istek → kağıt emir girdisi (sembol, yön,
        miktar, fiyat). Fiyat zorunludur: gerçekleşme fiyatı
        gönderilen fiyatın TA KENDİSİDİR."""
        if not isinstance(request, ExecutionRequest):
            _fail("request")
        if request.price is None:
            _fail("price")
        if isinstance(request.price, bool) or \
                not isinstance(request.price, Decimal) or \
                not request.price.is_finite() or \
                request.price <= _ZERO:
            _fail("price")
        if isinstance(request.quantity, bool) or \
                not isinstance(request.quantity, Decimal) or \
                not request.quantity.is_finite() or \
                request.quantity <= _ZERO:
            _fail("quantity")
        return (request.symbol, request.side, request.quantity,
                request.price)

    def execution_result_for(
            self, request: ExecutionRequest, order: PaperOrder,
            executions: Tuple[PaperExecution, ...]
            ) -> ExecutionResult:
        """Kağıt emir + gerçekleşmeler → kanonik ExecutionResult.

        Kayıpsız: emir kimliği, durum, miktar, fiyat ve komisyon
        alanları birebir taşınır; meta veri üretilmez."""
        if not isinstance(request, ExecutionRequest):
            _fail("request")
        if not isinstance(order, PaperOrder):
            _fail("order")
        if not isinstance(executions, tuple):
            _fail("executions")
        fills = ()
        for execution in executions:
            if not isinstance(execution, PaperExecution):
                _fail("executions")
            if execution.order_reference != \
                    order.order_reference:
                _fail("executions")
            fills = fills + (Fill(
                symbol=execution.symbol,
                side=execution.side,
                quantity=execution.quantity,
                price=execution.price,
                fee=execution.commission.amount,
                fee_asset=execution.commission.asset,
                trade_id=execution.execution_reference),)
        canonical_order = Order(
            symbol=order.symbol,
            side=order.side,
            order_type=request.order_type,
            quantity=order.quantity,
            time_in_force=request.time_in_force,
            state=order.state,
            price=order.price,
            filled_quantity=order.quantity,
            order_id=order.order_reference)
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            order=canonical_order,
            fills=fills,
            code=None,
            metadata=request.metadata)

    def account_snapshot_for(
            self, snapshot: PaperLedgerSnapshot,
            account_reference: str) -> RuntimeAccountSnapshot:
        """Defter anlık görüntüsü → RuntimeAccountSnapshot.

        Bakiye: serbest = nakit, kilitli = rezerve nakit.
        Pozisyon giriş fiyatı türetilmiş ortalama fiyattır."""
        if not isinstance(snapshot, PaperLedgerSnapshot):
            _fail("snapshot")
        if not isinstance(account_reference, str) or \
                not account_reference.strip():
            _fail("account_reference")
        positions = ()
        for position in snapshot.positions:
            positions = positions + (RuntimePosition(
                symbol=position.symbol,
                side=position.side,
                quantity=position.quantity,
                entry_price=position.average_price),)
        balances = (RuntimeBalance(
            asset=snapshot.quote_asset,
            free=snapshot.cash,
            locked=snapshot.reserved_cash),)
        return RuntimeAccountSnapshot(
            account_reference=account_reference,
            balances=balances,
            positions=positions,
            logical_sequence=snapshot.sequence)
