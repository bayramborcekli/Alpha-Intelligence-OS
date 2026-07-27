"""Mission 2000 — Execution Foundation: kanonik BrokerAdapter arayüzü.

Her gelecek broker uygulamasının (Binance Spot/Futures, Interactive
Brokers, Midas, Bybit, OKX, Kraken, paper broker'lar) arkasında
duracağı SINIRDIR. Bu ajan YALNIZ sözleşme tanımlar: gerçek broker,
ağ, kimlik doğrulama, imzalama, REST, WebSocket, retry, emir
yürütme veya kalıcılık YOKTUR.

Sınır kuralları:
- Strateji/portföy mantığı değerlendirmez, risk hesaplamaz,
  Kill Switch'i kontrol etmez, yürütmeyi YETKİLENDİRMEZ.
- Broker SDK nesnesi, ham REST/WebSocket yanıtı, tipsiz sözlük,
  keyfi JSON, ham byte veya native istisna sınırı GEÇEMEZ.
- Yetenek kararları VERİ odaklıdır (kanonik BrokerProfile);
  broker-adı dallanması yasaktır.
- Idempotency kimliğini asla adaptör üretmez; yazma operasyonları
  çağıranın idempotency anahtarını ZORUNLU kılar ve arayüz bunu
  herhangi bir I/O'dan ÖNCE reddeder.
- Retry politikası adaptöre ait değildir: otomatik retry, backoff,
  sleep, zamanlayıcı yasak — somut adaptörler yalnız hatayı doğru
  sınıflandırır (TEMPORARY_FAILURE / PERMANENT_FAILURE /
  RATE_LIMITED).
- İptal semantiği bool'a indirgenmez; kanonik normalize sonuçla
  ayrıştırılır (kabul edildi / zaten iptal / zaten dolu /
  bulunamadı / desteklenmiyor / geçici hata).

Okuma/yazma ayrımı sözleşmeseldir: gelecekteki izin/güvenlik
katmanları yazma metotlarını broker bilgisi olmadan tanır
(`_READ_OPERATIONS` / `_WRITE_OPERATIONS`).

Kamu yüzeyi: yalnız `BrokerAdapter`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from execution_broker_errors import BrokerContractError
from execution_broker_models import (
    BalancesQuery,
    BrokerHealth,
    BrokerOperationResult,
    BrokerRequestContext,
    CancelOrderRequest,
    OpenOrdersQuery,
    OrderQuery,
    PositionsQuery,
)
from execution_models import ExecutionRequest
from execution_risk_models import BrokerProfile

__all__ = ["BrokerAdapter"]

_ERROR_INVALID_REQUEST = "INVALID_REQUEST"
_ERROR_INVALID_CONTEXT = "INVALID_CONTEXT"
_ERROR_MISSING_IDEMPOTENCY_KEY = "MISSING_IDEMPOTENCY_KEY"

# Sözleşmesel okuma/yazma sınıflandırması (broker-bağımsız)
_READ_OPERATIONS = frozenset({
    "profile", "health_check", "get_order", "list_open_orders",
    "get_positions", "get_balances"})
_WRITE_OPERATIONS = frozenset({"submit_order", "cancel_order"})


def _validate_context(context: object) -> BrokerRequestContext:
    if not isinstance(context, BrokerRequestContext):
        raise BrokerContractError(_ERROR_INVALID_CONTEXT)
    return context


def _validate_write_context(context: object) -> BrokerRequestContext:
    """Yazma operasyonu bağlamı: idempotency anahtarı ZORUNLU.

    Adaptör anahtar üretmez; eksik/boş anahtar I/O'dan önce
    reddedilir.
    """
    validated = _validate_context(context)
    key = validated.idempotency_key
    if not isinstance(key, str) or not key.strip():
        raise BrokerContractError(_ERROR_MISSING_IDEMPOTENCY_KEY)
    return validated


def _validate_request(request: object, expected: type) -> None:
    if not isinstance(request, expected):
        raise BrokerContractError(_ERROR_INVALID_REQUEST)


class BrokerAdapter(ABC):
    """Soyut asenkron broker sınırı — tüm adaptörlerin sözleşmesi.

    Kamu metotları paylaşılan değişmez doğrulamayı uygular ve soyut
    `_do_*` kancalarına devreder; somut adaptörler yalnız kancaları
    uygular ve native sonuçları kanonik modellere normalize eder.
    Kamu değiştirilebilir durum yoktur.
    """

    __slots__ = ()

    # ── Okuma operasyonları ─────────────────────────────────────────

    async def profile(self) -> BrokerProfile:
        """Yetenek keşfi — kanonik BrokerProfile döner."""
        return await self._do_profile()

    async def health_check(self) -> BrokerHealth:
        """Adaptör kullanılabilirliği — ticaret yapmadan."""
        return await self._do_health_check()

    async def get_order(self, query: OrderQuery,
                        context: BrokerRequestContext
                        ) -> BrokerOperationResult:
        _validate_request(query, OrderQuery)
        _validate_context(context)
        return await self._do_get_order(query, context)

    async def list_open_orders(self, query: OpenOrdersQuery,
                               context: BrokerRequestContext
                               ) -> BrokerOperationResult:
        _validate_request(query, OpenOrdersQuery)
        _validate_context(context)
        return await self._do_list_open_orders(query, context)

    async def get_positions(self, query: PositionsQuery,
                            context: BrokerRequestContext
                            ) -> BrokerOperationResult:
        _validate_request(query, PositionsQuery)
        _validate_context(context)
        return await self._do_get_positions(query, context)

    async def get_balances(self, query: BalancesQuery,
                           context: BrokerRequestContext
                           ) -> BrokerOperationResult:
        _validate_request(query, BalancesQuery)
        _validate_context(context)
        return await self._do_get_balances(query, context)

    # ── Yazma operasyonları (idempotency anahtarı zorunlu) ─────────

    async def submit_order(self, request: ExecutionRequest,
                           context: BrokerRequestContext
                           ) -> BrokerOperationResult:
        _validate_request(request, ExecutionRequest)
        _validate_write_context(context)
        return await self._do_submit_order(request, context)

    async def cancel_order(self, request: CancelOrderRequest,
                           context: BrokerRequestContext
                           ) -> BrokerOperationResult:
        _validate_request(request, CancelOrderRequest)
        _validate_write_context(context)
        return await self._do_cancel_order(request, context)

    # ── Soyut kancalar (somut adaptörler uygular) ───────────────────

    @abstractmethod
    async def _do_profile(self) -> BrokerProfile:
        ...

    @abstractmethod
    async def _do_health_check(self) -> BrokerHealth:
        ...

    @abstractmethod
    async def _do_get_order(self, query: OrderQuery,
                            context: BrokerRequestContext
                            ) -> BrokerOperationResult:
        ...

    @abstractmethod
    async def _do_list_open_orders(self, query: OpenOrdersQuery,
                                   context: BrokerRequestContext
                                   ) -> BrokerOperationResult:
        ...

    @abstractmethod
    async def _do_get_positions(self, query: PositionsQuery,
                                context: BrokerRequestContext
                                ) -> BrokerOperationResult:
        ...

    @abstractmethod
    async def _do_get_balances(self, query: BalancesQuery,
                               context: BrokerRequestContext
                               ) -> BrokerOperationResult:
        ...

    @abstractmethod
    async def _do_submit_order(self, request: ExecutionRequest,
                               context: BrokerRequestContext
                               ) -> BrokerOperationResult:
        ...

    @abstractmethod
    async def _do_cancel_order(self, request: CancelOrderRequest,
                               context: BrokerRequestContext
                               ) -> BrokerOperationResult:
        ...
