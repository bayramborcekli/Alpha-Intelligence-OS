"""Mission 2000 — Agent 06: Binance → kanonik model normalizasyonu.

Native Binance payload'ları YALNIZ kanonik modellere çevrilir;
Binance JSON'u, SDK nesnesi, REST yanıtı veya Binance hata kodu bu
modülün ÇIKIŞINDAN asla sızmaz. Ham payload'lar somut adaptöre özel
kalır (native payload izolasyonu).

Durum eşlemesi (kapalı): NEW→SUBMITTED,
PARTIALLY_FILLED→PARTIALLY_FILLED, FILLED→FILLED,
CANCELED→CANCELLED, PENDING_CANCEL→SUBMITTED, REJECTED→REJECTED,
EXPIRED→EXPIRED. Bilinmeyen durum → BrokerNormalizationError
(asla uydurulmaz).

Hata eşlemesi (kapalı örnekler): -2010→ORDER_REJECTED,
-1013→INVALID_REQUEST, -2011/-2013→ORDER_NOT_FOUND,
-1021→INVALID_REQUEST, 429/418→RATE_LIMITED,
401→AUTHENTICATION_FAILURE, 403→AUTHORIZATION_FAILURE,
TIMEOUT→TIMEOUT, NETWORK→NETWORK_FAILURE,
bilinmeyen→UNKNOWN_BROKER_FAILURE.

Finansal alanlar YALNIZ Decimal'e ayrıştırılır. Bilinmeyen değer
None kalır — asla 0 sayılmaz. Zaman/UUID/rastgelelik üretilmez.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Mapping, Optional

from execution_broker_errors import (
    BrokerErrorCode,
    BrokerErrorDetail,
    BrokerNormalizationError,
)
from execution_broker_models import (
    BrokerBalance,
    BrokerOperationResult,
    BrokerOperationStatus,
)
from execution_enums import (
    OrderSide,
    OrderState,
    OrderType,
    TimeInForce,
)
from execution_models import Fill, Order

__all__ = ["normalize_order", "normalize_balance", "normalize_fill",
           "normalize_order_state", "normalize_error",
           "error_result"]

_ERROR_MALFORMED = "MALFORMED_BROKER_RESPONSE"

# Kapalı Binance→kanonik emir durumu eşlemesi
_STATE_MAP = MappingProxyType({
    "NEW": OrderState.SUBMITTED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
    "FILLED": OrderState.FILLED,
    "CANCELED": OrderState.CANCELLED,
    "PENDING_CANCEL": OrderState.SUBMITTED,
    "REJECTED": OrderState.REJECTED,
    "EXPIRED": OrderState.EXPIRED,
})

# Kapalı Binance hata kodu → kanonik hata kodu eşlemesi
_ERROR_MAP = MappingProxyType({
    -2010: BrokerErrorCode.ORDER_REJECTED,
    -1013: BrokerErrorCode.INVALID_REQUEST,
    -2011: BrokerErrorCode.ORDER_NOT_FOUND,
    -2013: BrokerErrorCode.ORDER_NOT_FOUND,
    -1021: BrokerErrorCode.INVALID_REQUEST,
    -1121: BrokerErrorCode.INVALID_INSTRUMENT,
    -2014: BrokerErrorCode.AUTHENTICATION_FAILURE,
    -2015: BrokerErrorCode.AUTHORIZATION_FAILURE,
    429: BrokerErrorCode.RATE_LIMITED,
    418: BrokerErrorCode.RATE_LIMITED,
    401: BrokerErrorCode.AUTHENTICATION_FAILURE,
    403: BrokerErrorCode.AUTHORIZATION_FAILURE,
    "TIMEOUT": BrokerErrorCode.TIMEOUT,
    "NETWORK": BrokerErrorCode.NETWORK_FAILURE,
    "UNAVAILABLE": BrokerErrorCode.BROKER_UNAVAILABLE,
})

# Kanonik hata kodu → normalize sonuç durumu
_STATUS_FOR_ERROR = MappingProxyType({
    BrokerErrorCode.ORDER_REJECTED: BrokerOperationStatus.REJECTED,
    BrokerErrorCode.INSUFFICIENT_FUNDS:
        BrokerOperationStatus.REJECTED,
    BrokerErrorCode.INVALID_REQUEST: BrokerOperationStatus.REJECTED,
    BrokerErrorCode.INVALID_INSTRUMENT:
        BrokerOperationStatus.REJECTED,
    BrokerErrorCode.MARKET_CLOSED: BrokerOperationStatus.REJECTED,
    BrokerErrorCode.ORDER_NOT_FOUND:
        BrokerOperationStatus.NOT_FOUND,
    BrokerErrorCode.UNSUPPORTED_OPERATION:
        BrokerOperationStatus.UNSUPPORTED,
    BrokerErrorCode.UNSUPPORTED_ASSET:
        BrokerOperationStatus.UNSUPPORTED,
    BrokerErrorCode.UNSUPPORTED_ORDER_TYPE:
        BrokerOperationStatus.UNSUPPORTED,
    BrokerErrorCode.RATE_LIMITED:
        BrokerOperationStatus.TEMPORARY_FAILURE,
    BrokerErrorCode.TIMEOUT:
        BrokerOperationStatus.TEMPORARY_FAILURE,
    BrokerErrorCode.NETWORK_FAILURE:
        BrokerOperationStatus.TEMPORARY_FAILURE,
    BrokerErrorCode.BROKER_UNAVAILABLE:
        BrokerOperationStatus.TEMPORARY_FAILURE,
    BrokerErrorCode.AUTHENTICATION_FAILURE:
        BrokerOperationStatus.PERMANENT_FAILURE,
    BrokerErrorCode.AUTHORIZATION_FAILURE:
        BrokerOperationStatus.PERMANENT_FAILURE,
    BrokerErrorCode.MALFORMED_BROKER_RESPONSE:
        BrokerOperationStatus.PERMANENT_FAILURE,
    BrokerErrorCode.UNKNOWN_BROKER_FAILURE:
        BrokerOperationStatus.UNKNOWN,
})

_RETRYABLE = frozenset({
    BrokerErrorCode.RATE_LIMITED, BrokerErrorCode.TIMEOUT,
    BrokerErrorCode.NETWORK_FAILURE,
    BrokerErrorCode.BROKER_UNAVAILABLE})

_NOT_RETRYABLE = frozenset({
    BrokerErrorCode.ORDER_REJECTED, BrokerErrorCode.INVALID_REQUEST,
    BrokerErrorCode.INVALID_INSTRUMENT,
    BrokerErrorCode.ORDER_NOT_FOUND,
    BrokerErrorCode.AUTHENTICATION_FAILURE,
    BrokerErrorCode.AUTHORIZATION_FAILURE,
    BrokerErrorCode.INSUFFICIENT_FUNDS,
    BrokerErrorCode.MALFORMED_BROKER_RESPONSE})


def _require(condition: bool) -> None:
    if not condition:
        raise BrokerNormalizationError(_ERROR_MALFORMED)


def _decimal(value: object) -> Decimal:
    _require(isinstance(value, str) and bool(value))
    try:
        return Decimal(value)
    except InvalidOperation:
        raise BrokerNormalizationError(_ERROR_MALFORMED)


def _optional_decimal(payload: Mapping, key: str
                      ) -> Optional[Decimal]:
    value = payload.get(key)
    if value is None:
        return None
    return _decimal(value)


def _enum_member(enum_cls, value: object):
    _require(isinstance(value, str))
    try:
        return enum_cls[value]
    except KeyError:
        raise BrokerNormalizationError(_ERROR_MALFORMED)


def normalize_order_state(native_status: object) -> OrderState:
    """Kapalı Binance durum eşlemesi — bilinmeyen asla uydurulmaz."""
    _require(isinstance(native_status, str))
    state = _STATE_MAP.get(native_status)
    if state is None:
        raise BrokerNormalizationError(_ERROR_MALFORMED)
    return state


def normalize_order(payload: Mapping) -> Order:
    """Native Binance emri → kanonik Order."""
    _require(isinstance(payload, Mapping))
    symbol = payload.get("symbol")
    _require(isinstance(symbol, str) and bool(symbol))
    order_id = payload.get("orderId")
    return Order(
        symbol=symbol,
        side=_enum_member(OrderSide, payload.get("side")),
        order_type=_enum_member(OrderType, payload.get("type")),
        quantity=_decimal(payload.get("origQty")),
        time_in_force=_enum_member(
            TimeInForce, payload.get("timeInForce")),
        state=normalize_order_state(payload.get("status")),
        price=_optional_decimal(payload, "price"),
        filled_quantity=_optional_decimal(payload, "executedQty"),
        order_id=None if order_id is None else str(order_id),
    )


def normalize_balance(payload: Mapping) -> BrokerBalance:
    """Native Binance bakiyesi → kanonik BrokerBalance.

    Bilinmeyen alan None kalır; total yalnız free+locked her ikisi
    biliniyorsa hesaplanır (asla 0 uydurulmaz).
    """
    _require(isinstance(payload, Mapping))
    asset = payload.get("asset")
    _require(isinstance(asset, str) and bool(asset))
    available = _optional_decimal(payload, "free")
    reserved = _optional_decimal(payload, "locked")
    total = None
    if available is not None and reserved is not None:
        total = available + reserved
    return BrokerBalance(currency=asset, total=total,
                         available=available, reserved=reserved)


def normalize_fill(payload: Mapping, symbol: str,
                   side: OrderSide) -> Fill:
    """Native Binance fill → kanonik Fill."""
    _require(isinstance(payload, Mapping))
    _require(isinstance(symbol, str) and bool(symbol))
    _require(isinstance(side, OrderSide))
    trade_id = payload.get("tradeId")
    return Fill(
        symbol=symbol, side=side,
        quantity=_decimal(payload.get("qty")),
        price=_decimal(payload.get("price")),
        fee=_optional_decimal(payload, "commission"),
        fee_asset=payload.get("commissionAsset"),
        trade_id=None if trade_id is None else str(trade_id),
    )


def normalize_error(native_code: object) -> BrokerErrorCode:
    """Binance hata kodu → kanonik kod; bilinmeyen →
    UNKNOWN_BROKER_FAILURE. Native kod sınırı asla geçmez."""
    if isinstance(native_code, bool):
        return BrokerErrorCode.UNKNOWN_BROKER_FAILURE
    return _ERROR_MAP.get(native_code,
                          BrokerErrorCode.UNKNOWN_BROKER_FAILURE)


def error_result(code: BrokerErrorCode) -> BrokerOperationResult:
    """Kanonik hata kodundan normalize sonuç üretir."""
    if not isinstance(code, BrokerErrorCode):
        raise BrokerNormalizationError(_ERROR_MALFORMED)
    retryable = None
    if code in _RETRYABLE:
        retryable = True
    elif code in _NOT_RETRYABLE:
        retryable = False
    return BrokerOperationResult(
        status=_STATUS_FOR_ERROR.get(
            code, BrokerOperationStatus.UNKNOWN),
        error=BrokerErrorDetail(code=code, retryable=retryable))
