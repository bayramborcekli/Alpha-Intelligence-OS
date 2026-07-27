"""Mission 2100 — Agent 05: Gölge karşılaştırıcı.

AI kararı → kağıt yürütme → canlı piyasa gözlemi zincirini
deterministik olarak karşılaştırır. YALNIZ gözlem: skor
manipülasyonu, AI optimizasyonu veya geri besleme YOKTUR.

Delta sözleşmesi (tamamı Decimal, hesaplanamayan None):
- gözlenen fiyat = last_trade_price, yoksa price (öncelik sırası)
- price_delta = gözlenen - beklenen (beklenen = gerçekleşme
  fiyatı, gerçekleşme yoksa emir fiyatı)
- piyasa dolum olanağı: BUY için emir fiyatı ≥ best_ask, SELL
  için emir fiyatı ≤ best_bid; ilgili taraf bilinmiyorsa None
- fill_delta = beklenen dolum miktarı - piyasa dolum miktarı
- pnl_delta = yön işaretli (gözlenen - beklenen) × miktar
- latency = gözlem mantıksal sırası - emir mantıksal sırası;
  negatifse None (duvar saati YOKTUR, uydurma yasak)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from execution_enums import OrderSide
from shadow_errors import ShadowContractError
from shadow_models import (ShadowAudit, ShadowComparison,
                           ShadowDecision, ShadowExecution,
                           ShadowMarketObservation, ShadowOrder)

__all__ = ["ShadowComparator"]

_ERROR_INVALID_FIELD = "INVALID_SHADOW_FIELD"

_ZERO = Decimal("0")


def _fail(field: str) -> None:
    raise ShadowContractError(f"{_ERROR_INVALID_FIELD}:{field}")


@dataclass(frozen=True, slots=True)
class ShadowComparator:
    """Durumsuz, deterministik karşılaştırıcı — yan etkisiz."""

    def compare(
            self, order: ShadowOrder,
            execution: Optional[ShadowExecution],
            observation: ShadowMarketObservation,
            request_reference: str, market_reference: str,
            audit: Tuple[ShadowAudit, ...] = (),
            logical_sequence: int = 0) -> ShadowComparison:
        """Değişmez karşılaştırma raporu üretir.

        Emir ve gözlem sembolleri EŞLEŞMEK zorundadır; kimlikler
        çağıran-sahiplidir, burada üretilmez."""
        if not isinstance(order, ShadowOrder):
            _fail("order")
        if execution is not None and not isinstance(
                execution, ShadowExecution):
            _fail("execution")
        if not isinstance(observation, ShadowMarketObservation):
            _fail("observation")
        if observation.symbol != order.symbol:
            _fail("observation")
        if execution is not None and (
                execution.order_reference
                != order.order_reference
                or execution.symbol != order.symbol
                or execution.side is not order.side):
            _fail("execution")
        expected_price = self._expected_price(order, execution)
        observed_price = self._observed_price(observation)
        price_delta = self._price_delta(expected_price,
                                        observed_price)
        fill_delta = self._fill_delta(order, execution,
                                      observation)
        pnl_delta = self._pnl_delta(order, expected_price,
                                    observed_price)
        latency = self._latency(order, observation)
        return ShadowComparison(
            request_reference=request_reference,
            paper_reference=order.order_reference,
            market_reference=market_reference,
            price_delta=price_delta,
            fill_delta=fill_delta,
            pnl_delta=pnl_delta,
            latency=latency,
            decision=ShadowDecision.SIMULATED,
            audit=audit,
            logical_sequence=logical_sequence)

    # ── Deterministik delta yardımcıları ─────────────────────────

    @staticmethod
    def _expected_price(order: ShadowOrder,
                        execution: Optional[ShadowExecution]
                        ) -> Decimal:
        if execution is not None:
            return execution.price
        return order.price

    @staticmethod
    def _observed_price(observation: ShadowMarketObservation
                        ) -> Optional[Decimal]:
        if observation.last_trade_price is not None:
            return observation.last_trade_price
        return observation.price

    @staticmethod
    def _price_delta(expected: Decimal,
                     observed: Optional[Decimal]
                     ) -> Optional[Decimal]:
        if observed is None:
            return None
        return observed - expected

    @staticmethod
    def _market_fill_quantity(order: ShadowOrder,
                              observation:
                              ShadowMarketObservation
                              ) -> Optional[Decimal]:
        """Piyasa dolum OLANAĞI — emir gönderilmez, yalnız
        limit fiyatın defterle kesişimi değerlendirilir."""
        if order.side is OrderSide.BUY:
            if observation.best_ask is None:
                return None
            if order.price >= observation.best_ask:
                return order.quantity
            return _ZERO
        if observation.best_bid is None:
            return None
        if order.price <= observation.best_bid:
            return order.quantity
        return _ZERO

    def _fill_delta(self, order: ShadowOrder,
                    execution: Optional[ShadowExecution],
                    observation: ShadowMarketObservation
                    ) -> Optional[Decimal]:
        market_quantity = self._market_fill_quantity(
            order, observation)
        if market_quantity is None:
            return None
        expected_quantity = _ZERO
        if execution is not None:
            expected_quantity = execution.quantity
        return expected_quantity - market_quantity

    @staticmethod
    def _pnl_delta(order: ShadowOrder, expected: Decimal,
                   observed: Optional[Decimal]
                   ) -> Optional[Decimal]:
        if observed is None:
            return None
        difference = observed - expected
        if order.side is OrderSide.SELL:
            difference = -difference
        return difference * order.quantity

    @staticmethod
    def _latency(order: ShadowOrder,
                 observation: ShadowMarketObservation
                 ) -> Optional[int]:
        difference = (observation.logical_sequence
                      - order.logical_sequence)
        if difference < 0:
            return None
        return difference
