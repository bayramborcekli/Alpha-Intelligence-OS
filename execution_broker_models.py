"""Mission 2000 — Execution Foundation: değişmez broker sınır modelleri.

BrokerAdapter sınırında taşınan kanonik sözleşmeler. Alan modelleri
(ExecutionRequest, Order, Position, Fill, Instrument, BrokerProfile)
BURADA TANIMLANMAZ — kanonik sahiplerinden (Agent 02/03) import
edilir; yapısal kopya yasaktır.

Tüm modeller frozen=True, slots=True, hashlenebilir, mutable
varsayılansız, açık tipli, deterministiktir. Finansal alanlar
YALNIZ Decimal (float yasak). Bilinmeyen değer None kalır — asla
boş string, sıfır veya uydurma kimlik olmaz.

Native payload izolasyonu: hiçbir model raw_response/native_payload/
exchange_json/sdk_object/http_response benzeri alan taşımaz.

Güvenlik: I/O yok, ağ yok, zaman/UUID/rastgelelik yok.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, unique
from typing import Optional, Tuple

from execution_broker_errors import BrokerErrorDetail
from execution_models import Fill, Order, Position

__all__ = [
    "ExecutionMode", "BrokerOperationStatus", "BrokerHealthState",
    "BrokerRequestContext", "BrokerOperationResult", "BrokerHealth",
    "BrokerBalance", "CancelOrderRequest", "OrderQuery",
    "OpenOrdersQuery", "PositionsQuery", "BalancesQuery",
]

_ERROR_INVALID_FIELD = "INVALID_BROKER_MODEL_FIELD"


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError(_ERROR_INVALID_FIELD)


def _is_optional_str(value: object) -> bool:
    return value is None or isinstance(value, str)


def _is_optional_decimal(value: object) -> bool:
    return value is None or isinstance(value, Decimal)


def _is_optional_bool(value: object) -> bool:
    return value is None or isinstance(value, bool)


def _is_optional_int(value: object) -> bool:
    return value is None or (isinstance(value, int)
                             and not isinstance(value, bool))


@unique
class ExecutionMode(Enum):
    """Kapalı yürütme modu kümesi — adaptör modu TAŞIR,
    yetkilendirmez (mod yetkisi üst orkestrasyona aittir)."""

    PAPER = "PAPER"
    SHADOW = "SHADOW"
    MICRO_LIVE = "MICRO_LIVE"
    LIVE = "LIVE"


@unique
class BrokerOperationStatus(Enum):
    """Onaylı normalize sonuç durumları — kapalı küme."""

    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    NOT_FOUND = "NOT_FOUND"
    UNSUPPORTED = "UNSUPPORTED"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    UNKNOWN = "UNKNOWN"


@unique
class BrokerHealthState(Enum):
    """Onaylı sağlık durumları — kapalı küme."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class BrokerRequestContext:
    """Deterministik, ÇAĞIRAN tarafından sağlanan bağlam.

    Hiçbir alan adaptör içinde üretilmez: UUID yok, duvar saati
    yok, rastgele kimlik yok, örtük idempotency anahtarı yok.
    Bilinmeyen opsiyonel değerler None kalır.
    """

    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    account_id: Optional[str] = None
    portfolio_id: Optional[str] = None
    strategy_id: Optional[str] = None
    execution_mode: Optional[ExecutionMode] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        for name in ("request_id", "correlation_id",
                     "idempotency_key", "account_id",
                     "portfolio_id", "strategy_id"):
            _require(_is_optional_str(getattr(self, name)))
        _require(self.execution_mode is None or
                 isinstance(self.execution_mode, ExecutionMode))
        _require(_is_optional_int(self.logical_sequence))
        _require(self.logical_sequence is None or
                 self.logical_sequence >= 0)


@dataclass(frozen=True, slots=True)
class BrokerHealth:
    """Adaptör kullanılabilirliği — ticaret yapılmadan raporlanır.

    Duvar saati yok: logical_sequence yalnız mantıksal sıradır.
    """

    state: BrokerHealthState
    logical_sequence: Optional[int] = None
    reason_code: Optional[str] = None
    message: Optional[str] = None
    read_available: Optional[bool] = None
    write_available: Optional[bool] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.state, BrokerHealthState))
        _require(_is_optional_int(self.logical_sequence))
        _require(_is_optional_str(self.reason_code))
        _require(_is_optional_str(self.message))
        _require(_is_optional_bool(self.read_available))
        _require(_is_optional_bool(self.write_available))


@dataclass(frozen=True, slots=True)
class BrokerBalance:
    """Bakiye sözleşmesi — bilinmeyen None kalır, ASLA 0 sayılmaz.

    Değişmezler: total negatif olamaz (açık hesap semantiği
    tanıtılana dek); reserved negatif olamaz; her ikisi biliniyorsa
    available > total olamaz; currency boş olamaz. Para birimi
    dönüşümü YOKTUR.
    """

    currency: str
    total: Optional[Decimal] = None
    available: Optional[Decimal] = None
    reserved: Optional[Decimal] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.currency, str) and
                 bool(self.currency.strip()))
        _require(_is_optional_decimal(self.total))
        _require(_is_optional_decimal(self.available))
        _require(_is_optional_decimal(self.reserved))
        if self.total is not None:
            _require(self.total >= Decimal("0"))
        if self.reserved is not None:
            _require(self.reserved >= Decimal("0"))
        if self.total is not None and self.available is not None:
            _require(self.available <= self.total)


@dataclass(frozen=True, slots=True)
class CancelOrderRequest:
    """İptal isteği — order_id VEYA client_order_id zorunlu."""

    symbol: str
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.symbol, str) and bool(self.symbol))
        _require(_is_optional_str(self.order_id))
        _require(_is_optional_str(self.client_order_id))
        _require(self.order_id is not None or
                 self.client_order_id is not None)


@dataclass(frozen=True, slots=True)
class OrderQuery:
    """Tek emir sorgusu — order_id VEYA client_order_id zorunlu."""

    symbol: str
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.symbol, str) and bool(self.symbol))
        _require(_is_optional_str(self.order_id))
        _require(_is_optional_str(self.client_order_id))
        _require(self.order_id is not None or
                 self.client_order_id is not None)


@dataclass(frozen=True, slots=True)
class OpenOrdersQuery:
    """Açık emir listesi sorgusu (symbol None = tümü)."""

    symbol: Optional[str] = None

    def __post_init__(self) -> None:
        _require(_is_optional_str(self.symbol))


@dataclass(frozen=True, slots=True)
class PositionsQuery:
    """Pozisyon sorgusu (symbol None = tümü)."""

    symbol: Optional[str] = None

    def __post_init__(self) -> None:
        _require(_is_optional_str(self.symbol))


@dataclass(frozen=True, slots=True)
class BalancesQuery:
    """Bakiye sorgusu (currency None = tümü)."""

    currency: Optional[str] = None

    def __post_init__(self) -> None:
        _require(_is_optional_str(self.currency))


@dataclass(frozen=True, slots=True)
class BrokerOperationResult:
    """Kanonik normalize operasyon sonucu.

    Başarı/başarısızlık AÇIKTIR: sıradan broker sonuçları (ret,
    bulunamadı, desteklenmiyor, yetersiz bakiye, kapalı piyasa)
    istisna değil, durum+hata detayı olarak taşınır. Native durum
    bu sınırı asla geçemez. Ham payload alanı YOKTUR.
    """

    status: BrokerOperationStatus
    error: Optional[BrokerErrorDetail] = None
    order: Optional[Order] = None
    orders: Optional[Tuple[Order, ...]] = None
    positions: Optional[Tuple[Position, ...]] = None
    fills: Optional[Tuple[Fill, ...]] = None
    balances: Optional[Tuple[BrokerBalance, ...]] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.status, BrokerOperationStatus))
        _require(self.error is None or
                 isinstance(self.error, BrokerErrorDetail))
        _require(self.order is None or
                 isinstance(self.order, Order))
        for name, expected in (("orders", Order),
                               ("positions", Position),
                               ("fills", Fill),
                               ("balances", BrokerBalance)):
            value = getattr(self, name)
            if value is None:
                continue
            _require(isinstance(value, tuple))
            for item in value:
                _require(isinstance(item, expected))
        if self.status is not BrokerOperationStatus.SUCCESS:
            _require(self.error is not None)
