"""Mission 2100 — Agent 03: Deterministik kağıt broker.

Gerçek broker YOK, ağ YOK, Binance YOK, Execution API/Service YOK.
Durumsuz: tüm işlemler anlık görüntü alır, YENİ anlık görüntü döner.
Dolum politikası tek: IMMEDIATE_FULL_FILL. Gerçekleşme fiyatı =
gönderilen fiyat (kayma/gecikme/spread/rastgelelik YOK).

Komisyon modeli arayüzü: PaperCommissionModel; varsayılan uygulama
ZERO_COMMISSION. Gelecek modeller ertelendi.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple

from execution_enums import OrderSide, OrderState
from paper_errors import PaperContractError, PaperOrderError
from paper_ledger import PaperLedger
from paper_models import (PaperBalance, PaperCommission,
                          PaperExecution, PaperFillPolicy,
                          PaperLedgerSnapshot, PaperOrder)
from runtime_enums import HeartbeatStatus

__all__ = ["PaperBroker", "PaperCommissionModel",
           "ZeroCommissionModel", "ZERO_COMMISSION"]

_ERROR_REJECTED = "PAPER_ORDER_REJECTED"
_ERROR_INVALID_FIELD = "INVALID_PAPER_MODEL_FIELD"

_ZERO = Decimal("0")


def _reject(code: str) -> None:
    raise PaperOrderError(f"{_ERROR_REJECTED}:{code}")


class PaperCommissionModel:
    """Komisyon modeli arayüzü — deterministik, yan etkisiz."""

    def commission_for(self, symbol: str, side: OrderSide,
                       quantity: Decimal, price: Decimal,
                       quote_asset: str) -> PaperCommission:
        """Alt sınıf uygular; arayüz doğrudan kullanılamaz."""
        raise NotImplementedError(
            "PAPER_COMMISSION_MODEL_ABSTRACT")


@dataclass(frozen=True, slots=True)
class ZeroCommissionModel(PaperCommissionModel):
    """Varsayılan model: ZERO_COMMISSION — her emirde sıfır."""

    def commission_for(self, symbol: str, side: OrderSide,
                       quantity: Decimal, price: Decimal,
                       quote_asset: str) -> PaperCommission:
        return PaperCommission(amount=_ZERO, asset=quote_asset)


ZERO_COMMISSION = ZeroCommissionModel()


@dataclass(frozen=True, slots=True)
class PaperBroker:
    """Durumsuz, deterministik kağıt broker.

    Yapılandırma değişmezdir: bilinen semboller + komisyon modeli.
    İş parçacığı, zamanlayıcı, async görev YOKTUR.
    """

    known_symbols: Tuple[str, ...]
    commission_model: PaperCommissionModel = ZERO_COMMISSION
    fill_policy: PaperFillPolicy = (
        PaperFillPolicy.IMMEDIATE_FULL_FILL)

    def __post_init__(self) -> None:
        if not isinstance(self.known_symbols, tuple) or \
                not self.known_symbols:
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:known_symbols")
        for symbol in self.known_symbols:
            if not isinstance(symbol, str) or \
                    not symbol.strip():
                raise PaperContractError(
                    f"{_ERROR_INVALID_FIELD}:known_symbols")
        if len(set(self.known_symbols)) != \
                len(self.known_symbols):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:known_symbols")
        if not isinstance(self.commission_model,
                          PaperCommissionModel):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:commission_model")
        if self.fill_policy is not \
                PaperFillPolicy.IMMEDIATE_FULL_FILL:
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:fill_policy")

    def submit(self, snapshot: PaperLedgerSnapshot,
               order_reference: str, symbol: str,
               side: OrderSide, quantity: Decimal,
               price: Decimal) -> PaperLedgerSnapshot:
        """Emri doğrular, ANINDA tam dolar, yeni defter döner.

        Gerçekleşme fiyatı gönderilen fiyatın TA KENDİSİDİR.
        """
        if not isinstance(snapshot, PaperLedgerSnapshot):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:snapshot")
        if not isinstance(order_reference, str) or \
                not order_reference.strip():
            _reject("INVALID_ORDER_REFERENCE")
        if snapshot.order_for(order_reference) is not None:
            _reject("DUPLICATE_ORDER_ID")
        if not isinstance(symbol, str) or not symbol.strip():
            _reject("UNKNOWN_SYMBOL")
        if symbol not in self.known_symbols:
            _reject("UNKNOWN_SYMBOL")
        if not isinstance(side, OrderSide):
            _reject("INVALID_SIDE")
        if isinstance(quantity, bool) or \
                not isinstance(quantity, Decimal) or \
                not quantity.is_finite() or quantity <= _ZERO:
            _reject("INVALID_QUANTITY")
        if isinstance(price, bool) or \
                not isinstance(price, Decimal) or \
                not price.is_finite() or price <= _ZERO:
            _reject("INVALID_PRICE")

        commission = self.commission_model.commission_for(
            symbol, side, quantity, price,
            snapshot.quote_asset)
        if not isinstance(commission, PaperCommission):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:commission")

        next_sequence = snapshot.sequence + 1
        order = PaperOrder(
            order_reference=order_reference, symbol=symbol,
            side=side, quantity=quantity, price=price,
            state=OrderState.FILLED,
            fill_policy=self.fill_policy,
            logical_sequence=next_sequence)
        execution = PaperExecution(
            execution_reference=f"{order_reference}:1",
            order_reference=order_reference, symbol=symbol,
            side=side, quantity=quantity, price=price,
            commission=commission,
            logical_sequence=next_sequence)
        return PaperLedger().apply_fill(snapshot, order,
                                        execution)

    def cancel(self, snapshot: PaperLedgerSnapshot,
               order_reference: str) -> PaperLedgerSnapshot:
        """IMMEDIATE_FULL_FILL altında açık emir kalmaz.

        Bilinmeyen emir → UNKNOWN_ORDER; dolmuş emir →
        INVALID_STATE. Deterministik red; sessiz başarı YOK.
        """
        if not isinstance(snapshot, PaperLedgerSnapshot):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:snapshot")
        if not isinstance(order_reference, str) or \
                not order_reference.strip():
            _reject("INVALID_ORDER_REFERENCE")
        order = snapshot.order_for(order_reference)
        if order is None:
            _reject("UNKNOWN_ORDER")
        _reject("INVALID_STATE")
        return snapshot

    def balance(self, snapshot: PaperLedgerSnapshot
                ) -> PaperBalance:
        """Nakit bakiye görünümü."""
        if not isinstance(snapshot, PaperLedgerSnapshot):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:snapshot")
        return PaperBalance(asset=snapshot.quote_asset,
                            free=snapshot.cash,
                            reserved=snapshot.reserved_cash)

    def positions(self, snapshot: PaperLedgerSnapshot) -> tuple:
        """Açık pozisyonlar."""
        if not isinstance(snapshot, PaperLedgerSnapshot):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:snapshot")
        return snapshot.positions

    def orders(self, snapshot: PaperLedgerSnapshot) -> tuple:
        """Emir geçmişi."""
        if not isinstance(snapshot, PaperLedgerSnapshot):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:snapshot")
        return snapshot.orders

    def executions(self, snapshot: PaperLedgerSnapshot) -> tuple:
        """Gerçekleşme geçmişi."""
        if not isinstance(snapshot, PaperLedgerSnapshot):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:snapshot")
        return snapshot.executions

    def heartbeat(self, snapshot: PaperLedgerSnapshot
                  ) -> HeartbeatStatus:
        """Defter denetimi geçerse OK, aksi halde ERROR."""
        if not isinstance(snapshot, PaperLedgerSnapshot):
            raise PaperContractError(
                f"{_ERROR_INVALID_FIELD}:snapshot")
        if snapshot.audit():
            return HeartbeatStatus.OK
        return HeartbeatStatus.ERROR
