"""Mission 2000 — Execution Foundation: kanonik broker hata sınıflandırması.

Kapalı hata kodu kümesi + değişmez hata detayı + minimal kapalı
istisna hiyerarşisi. Sıradan broker sonuçları (ret, bulunamadı,
desteklenmiyor vb.) İSTİSNA DEĞİLDİR — `BrokerOperationResult`
içinde taşınır. İstisnalar yalnız yerel sözleşme/yapılandırma/
normalizasyon ihlalleri içindir.

Hata detayları asla içeremez: API anahtarı, secret, imza,
authorization başlığı, ham istek/yanıt gövdesi, dış tüketiciye
yönelik stack trace.

Güvenlik: I/O yok, ağ yok, zaman/UUID/rastgelelik yok.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Optional

__all__ = [
    "BrokerErrorCode", "BrokerErrorDetail", "BrokerAdapterError",
    "BrokerContractError", "BrokerConfigurationError",
    "BrokerNormalizationError",
]

_ERROR_INVALID_FIELD = "INVALID_BROKER_MODEL_FIELD"


@unique
class BrokerErrorCode(Enum):
    """Kapalı kanonik hata kodu kümesi — keyfi kod yasak."""

    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_CONTEXT = "INVALID_CONTEXT"
    MISSING_IDEMPOTENCY_KEY = "MISSING_IDEMPOTENCY_KEY"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    UNSUPPORTED_ASSET = "UNSUPPORTED_ASSET"
    UNSUPPORTED_ORDER_TYPE = "UNSUPPORTED_ORDER_TYPE"
    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
    AUTHORIZATION_FAILURE = "AUTHORIZATION_FAILURE"
    RATE_LIMITED = "RATE_LIMITED"
    MARKET_CLOSED = "MARKET_CLOSED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    INVALID_INSTRUMENT = "INVALID_INSTRUMENT"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    MALFORMED_BROKER_RESPONSE = "MALFORMED_BROKER_RESPONSE"
    UNKNOWN_BROKER_FAILURE = "UNKNOWN_BROKER_FAILURE"


@dataclass(frozen=True, slots=True)
class BrokerErrorDetail:
    """Değişmez, sterile hata detayı.

    message serbest metin DEĞİL, kısa sterile açıklamadır; ham
    yanıt/istek gövdesi, secret, imza veya stack trace taşımaz.
    retryable bilinmiyorsa None kalır (asla uydurulmaz).
    """

    code: BrokerErrorCode
    message: Optional[str] = None
    retryable: Optional[bool] = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, BrokerErrorCode) or \
                not (self.message is None or
                     isinstance(self.message, str)) or \
                not (self.retryable is None or
                     isinstance(self.retryable, bool)):
            raise ValueError(_ERROR_INVALID_FIELD)


class BrokerAdapterError(Exception):
    """Kök yerel istisna — yalnız sözleşme düzeyi ihlaller."""


class BrokerContractError(BrokerAdapterError):
    """Çağıran sözleşmeyi ihlal etti (tür/idempotency/bağlam)."""


class BrokerConfigurationError(BrokerAdapterError):
    """Adaptör yapılandırması geçersiz."""


class BrokerNormalizationError(BrokerAdapterError):
    """Native yanıt kanonik modele normalize edilemedi."""
