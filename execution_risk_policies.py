"""Mission 2000 — Execution Foundation: deterministik risk politikaları.

Risk Engine'in iç politika adımları. Tüm fonksiyonlar saftır:
aynı girdi her zaman aynı çıktıyı üretir; yan etki, I/O, zaman,
rastgelelik yoktur. Broker'a özgü mantık ve sembol denetimi YOKTUR —
yalnız `BrokerProfile` yetenekleri ve `Instrument` alanları okunur.

GİZLİ KURAL YOKTUR — dondurulmuş kural tablosu:
- Geçersiz girdi türü → sterile ValueError INVALID_RISK_INPUT
- Ekonomik geçersizlik (miktar ≤ 0; verilmiş fiyat ≤ 0) →
  sterile ValueError INVALID_RISK_INPUT
- Maruziyet YÖN-FARKINDADIR: BUY net maruziyeti artırır, SELL
  azaltır; long kapatan SELL riski düşürür ve reddedilmez;
  ihlal ölçütü |önerilen net| > max_exposure VE |önerilen net| ≥
  |mevcut net| (riski düşüren emir asla maruziyetten reddedilmez)
- Notional bilinmiyor (fiyat null) → REQUIRE_CONFIRMATION
  UNKNOWN_NOTIONAL (bilinmeyen asla 0 sayılmaz)
- Yetersiz sermaye → REJECT INSUFFICIENT_CAPITAL
- MARKET desteklenmiyor → REJECT MARKET_NOT_SUPPORTED
- Açığa satış desteklenmiyor → REJECT SHORT_NOT_SUPPORTED
- Kesirli miktar desteklenmiyor → REJECT FRACTIONAL_NOT_SUPPORTED
- Enstrüman/sembol uyuşmazlığı → REJECT INSTRUMENT_MISMATCH
- step_size ihlali → REJECT STEP_SIZE_VIOLATION
- tick_size ihlali → REJECT TICK_SIZE_VIOLATION
- Maruziyet aşımı → REDUCE_SIZE EXPOSURE_EXCEEDED
  (indirgenmiş miktar ≤ 0 ise REJECT EXPOSURE_EXCEEDED)
- Günlük zarar aşımı → REJECT DAILY_LOSS_EXCEEDED
- Pozisyon boyutu aşımı → REDUCE_SIZE MAX_POSITION_SIZE
- Limitler içinde → ALLOW (kod null)

Bu modülün kamu yüzeyi YOKTUR (yalnız Risk Engine kullanır).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Optional

from execution_enums import OrderSide, OrderType, PositionSide
from execution_models import ExecutionRequest
from execution_risk_models import (
    BrokerProfile,
    Instrument,
    Portfolio,
    RiskLimits,
)

__all__: list = []

_ERROR_INVALID_INPUT = "INVALID_RISK_INPUT"

CODE_UNKNOWN_NOTIONAL = "UNKNOWN_NOTIONAL"
CODE_INSUFFICIENT_CAPITAL = "INSUFFICIENT_CAPITAL"
CODE_MARKET_NOT_SUPPORTED = "MARKET_NOT_SUPPORTED"
CODE_SHORT_NOT_SUPPORTED = "SHORT_NOT_SUPPORTED"
CODE_FRACTIONAL_NOT_SUPPORTED = "FRACTIONAL_NOT_SUPPORTED"
CODE_INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
CODE_STEP_SIZE_VIOLATION = "STEP_SIZE_VIOLATION"
CODE_TICK_SIZE_VIOLATION = "TICK_SIZE_VIOLATION"
CODE_EXPOSURE_EXCEEDED = "EXPOSURE_EXCEEDED"
CODE_DAILY_LOSS_EXCEEDED = "DAILY_LOSS_EXCEEDED"
CODE_MAX_POSITION_SIZE = "MAX_POSITION_SIZE"

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _validate_input(request, portfolio, instrument, broker_profile,
                    limits) -> None:
    """Adım 1 — girdi doğrulama (türler + ekonomik geçerlilik).

    Sterile hata: miktar > 0 zorunlu; fiyat verilmişse > 0 zorunlu.
    """
    if not isinstance(request, ExecutionRequest) or \
            not isinstance(portfolio, Portfolio) or \
            not isinstance(instrument, Instrument) or \
            not isinstance(broker_profile, BrokerProfile) or \
            not isinstance(limits, RiskLimits):
        raise ValueError(_ERROR_INVALID_INPUT)
    if request.quantity <= _ZERO:
        raise ValueError(_ERROR_INVALID_INPUT)
    if request.price is not None and request.price <= _ZERO:
        raise ValueError(_ERROR_INVALID_INPUT)


def _order_notional(request: ExecutionRequest) -> Optional[Decimal]:
    """Emir notional'ı — fiyat bilinmiyorsa null (asla 0 değil)."""
    if request.price is None:
        return None
    return request.quantity * request.price


def _validate_capital(request: ExecutionRequest,
                      portfolio: Portfolio) -> Optional[str]:
    """Adım 2 — sermaye doğrulama."""
    notional = _order_notional(request)
    if notional is None:
        return CODE_UNKNOWN_NOTIONAL
    if request.side is OrderSide.BUY and \
            notional > portfolio.capital.available_capital:
        return CODE_INSUFFICIENT_CAPITAL
    return None


def _held_long_quantity(portfolio: Portfolio, symbol: str) -> Decimal:
    total = _ZERO
    for position in portfolio.positions:
        if position.symbol == symbol and \
                position.side is PositionSide.LONG:
            total += position.quantity
    return total


def _is_fractional(quantity: Decimal,
                   step_size: Optional[Decimal]) -> bool:
    if step_size is not None and step_size != _ZERO:
        return (quantity % step_size) != _ZERO
    return (quantity % _ONE) != _ZERO


def _validate_broker_capabilities(
        request: ExecutionRequest, portfolio: Portfolio,
        instrument: Instrument,
        broker_profile: BrokerProfile) -> Optional[str]:
    """Adım 3 — broker YETENEK doğrulama (broker adı bilinmez)."""
    if request.order_type is OrderType.MARKET and \
            not broker_profile.supports_market_orders:
        return CODE_MARKET_NOT_SUPPORTED
    if request.side is OrderSide.SELL:
        held = _held_long_quantity(portfolio, request.symbol)
        if request.quantity > held and \
                not broker_profile.supports_short:
            return CODE_SHORT_NOT_SUPPORTED
    if _is_fractional(request.quantity, instrument.step_size) and \
            not broker_profile.supports_fractional:
        return CODE_FRACTIONAL_NOT_SUPPORTED
    return None


def _validate_instrument(request: ExecutionRequest,
                         instrument: Instrument) -> Optional[str]:
    """Adım 4 — enstrüman doğrulama (sembol-bağımsız kurallar)."""
    if request.symbol != instrument.symbol:
        return CODE_INSTRUMENT_MISMATCH
    if instrument.step_size is not None and \
            instrument.step_size != _ZERO and \
            (request.quantity % instrument.step_size) != _ZERO:
        return CODE_STEP_SIZE_VIOLATION
    if request.price is not None and \
            instrument.tick_size is not None and \
            instrument.tick_size != _ZERO and \
            (request.price % instrument.tick_size) != _ZERO:
        return CODE_TICK_SIZE_VIOLATION
    return None


def _net_symbol_exposure(portfolio: Portfolio, symbol: str,
                         price: Decimal) -> Decimal:
    """İşaretli net sembol maruziyeti (LONG +, SHORT −)."""
    total = _ZERO
    for position in portfolio.positions:
        if position.symbol != symbol:
            continue
        notional = position.quantity * price
        if position.side is PositionSide.SHORT:
            total -= notional
        elif position.side is PositionSide.LONG:
            total += notional
    return total


def _validate_exposure(request: ExecutionRequest,
                       portfolio: Portfolio, instrument: Instrument,
                       limits: RiskLimits):
    """Adım 5 — maruziyet doğrulama (yön-farkında).

    Net maruziyet işaretlidir: BUY artırır, SELL azaltır (long
    kapatan SELL riski DÜŞÜRÜR ve reddedilmez). İhlal ölçütü
    |önerilen net| > max_exposure. Aşım → (EXPOSURE_EXCEEDED,
    indirgenmiş miktar). Limit tanımsız veya fiyat bilinmiyorsa
    geçer (None, None).
    """
    if limits.max_exposure is None or request.price is None:
        return None, None
    existing = _net_symbol_exposure(portfolio, request.symbol,
                                    request.price)
    delta = request.quantity * request.price
    if request.side is OrderSide.SELL:
        delta = -delta
    proposed = existing + delta
    if abs(proposed) <= limits.max_exposure or \
            abs(proposed) < abs(existing):
        return None, None
    if request.side is OrderSide.BUY:
        headroom = limits.max_exposure - existing
    else:
        headroom = limits.max_exposure + existing
    if headroom <= _ZERO:
        return CODE_EXPOSURE_EXCEEDED, _ZERO
    reduced = _quantize_step(headroom / request.price,
                             instrument.step_size)
    return CODE_EXPOSURE_EXCEEDED, reduced


def _validate_daily_loss(portfolio: Portfolio,
                         limits: RiskLimits) -> Optional[str]:
    """Adım 6 — günlük zarar koruması (bilinmeyen PnL → kural atlanır)."""
    pnl = portfolio.capital.daily_realized_pnl
    if limits.max_daily_loss is None or pnl is None:
        return None
    if pnl < _ZERO and -pnl >= limits.max_daily_loss:
        return CODE_DAILY_LOSS_EXCEEDED
    return None


def _quantize_step(quantity: Decimal,
                   step_size: Optional[Decimal]) -> Decimal:
    """Miktarı step_size'a AŞAĞI yuvarlar (deterministik)."""
    if step_size is None or step_size == _ZERO:
        return quantity
    steps = (quantity / step_size).to_integral_value(
        rounding=ROUND_DOWN)
    return steps * step_size


def _apply_position_sizing(request: ExecutionRequest,
                           limits: RiskLimits):
    """Adım 7 — pozisyon boyutlama."""
    if limits.max_position_size is not None and \
            request.quantity > limits.max_position_size:
        return CODE_MAX_POSITION_SIZE, limits.max_position_size
    return None, None
