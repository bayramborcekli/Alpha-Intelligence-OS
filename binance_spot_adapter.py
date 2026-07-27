"""Mission 2000 — Agent 06: Binance Spot referans adaptörü.

İLK somut BrokerAdapter. Amacı işlem yapmak DEĞİL; BrokerAdapter
sözleşmesinin gerçek broker uygulamaları için yeterli olduğunu
kanıtlamak ve gelecekteki tüm broker'lar için REFERANS uygulama
olmaktır.

Katmanlama: BrokerAdapter'ın üzerindeki hiçbir katman Binance'i
bilmez; Binance bilgisi YALNIZ bu adaptör ailesinde yaşar.

Ağ YOK: gerçek HTTP/WebSocket açılmaz. Deterministik `Transport`
arayüzleri tanımlanır (RESTTransport, WebSocketTransport) — somut
taşıma katmanı gelecek misyonun işidir; testler sahte taşıma kullanır.

Kimlik doğrulama YOK: `SigningProvider` ve `CredentialProvider`
YALNIZ arayüz olarak tanımlanır; uygulama gelecek misyondadır.
Secret, API anahtarı, imzalama bu modülde yoktur.

Native payload izolasyonu: taşıma katmanının döndürdüğü ham Binance
payload'ları bu modülün İÇİNDE kalır; sınırdan yalnız kanonik
modeller (BrokerOperationResult/BrokerHealth/BrokerProfile) çıkar.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Mapping, Optional

from binance_capabilities import binance_spot_profile
from binance_normalizer import (
    error_result,
    normalize_balance,
    normalize_error,
    normalize_order,
)
from execution_broker_errors import (
    BrokerConfigurationError,
    BrokerErrorCode,
    BrokerNormalizationError,
)
from execution_broker_adapter import BrokerAdapter
from execution_broker_models import (
    BrokerHealth,
    BrokerHealthState,
    BrokerOperationResult,
    BrokerOperationStatus,
)

__all__ = ["BinanceSpotAdapter", "Transport", "RESTTransport",
           "WebSocketTransport", "TransportFailure",
           "SigningProvider", "CredentialProvider"]

_ERROR_INVALID_TRANSPORT = "INVALID_TRANSPORT"


class TransportFailure(Exception):
    """Deterministik taşıma hatası sinyali.

    kind: "TIMEOUT" | "NETWORK" | "UNAVAILABLE" — normalize edilir,
    asla sınırdan sızmaz.
    """

    __slots__ = ("kind",)

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class Transport(ABC):
    """Deterministik taşıma arayüzü — gerçek ağ YOK.

    Native Binance payload'ı (Mapping) döner; bu payload adaptör
    dışına asla çıkmaz.
    """

    __slots__ = ()

    @abstractmethod
    async def request(self, operation: str,
                      params: Mapping) -> Mapping:
        """Tek deterministik istek; hata için TransportFailure."""


class RESTTransport(Transport):
    """REST taşıma arayüzü (soyut) — uygulama gelecek misyonda."""

    __slots__ = ()


class WebSocketTransport(Transport):
    """WebSocket taşıma arayüzü (soyut) — uygulama gelecek
    misyonda. Bu misyonda soket açılmaz."""

    __slots__ = ()


class SigningProvider(ABC):
    """İmzalama arayüzü — YALNIZ sözleşme, uygulama YOK.

    Gerçek HMAC imzalama gelecek misyonun işidir; bu modül hiçbir
    secret'a dokunmaz.
    """

    __slots__ = ()

    @abstractmethod
    def sign_payload(self, canonical_payload: str) -> str:
        """Kanonik payload'ın imzasını döndürür (gelecek misyon)."""


class CredentialProvider(ABC):
    """Kimlik bilgisi arayüzü — YALNIZ sözleşme, uygulama YOK."""

    __slots__ = ()

    @abstractmethod
    def api_key_reference(self) -> str:
        """Anahtarın kendisini DEĞİL, opak referansını döndürür."""


def _normalized_failure(payload: Mapping) -> Optional[
        BrokerOperationResult]:
    """Native hata payload'ını kanonik sonuca çevirir; hata yoksa
    None döner."""
    code = payload.get("code")
    if code is None:
        return None
    return error_result(normalize_error(code))


class BinanceSpotAdapter(BrokerAdapter):
    """Binance Spot referans adaptörü — yalnız BrokerAdapter'dan
    kalıtır; ek taban sınıf, mixin, service locator YOK."""

    __slots__ = ("_transport",)

    def __init__(self, transport: Transport):
        if not isinstance(transport, Transport):
            raise BrokerConfigurationError(
                _ERROR_INVALID_TRANSPORT)
        object.__setattr__(self, "_transport", transport)

    # ── okuma ────────────────────────────────────────────────────

    async def _do_profile(self):
        # Yetenekler statiktir; ağ gerekmez
        return binance_spot_profile()

    async def _do_health_check(self):
        try:
            await self._transport.request("ping", {})
        except TransportFailure as failure:
            return BrokerHealth(
                state=BrokerHealthState.UNAVAILABLE,
                reason_code=normalize_error(failure.kind).value,
                read_available=False, write_available=False)
        except Exception:
            return BrokerHealth(
                state=BrokerHealthState.UNKNOWN,
                reason_code=(BrokerErrorCode
                             .UNKNOWN_BROKER_FAILURE.value),
                read_available=None, write_available=None)
        return BrokerHealth(state=BrokerHealthState.HEALTHY,
                            read_available=True,
                            write_available=True)

    async def _do_get_order(self, query, context):
        params = {"symbol": query.symbol}
        if query.order_id is not None:
            params["orderId"] = query.order_id
        if query.client_order_id is not None:
            params["origClientOrderId"] = query.client_order_id
        return await self._call(
            "get_order", params,
            lambda payload: BrokerOperationResult(
                status=BrokerOperationStatus.SUCCESS,
                order=normalize_order(payload)))

    async def _do_list_open_orders(self, query, context):
        params = {}
        if query.symbol is not None:
            params["symbol"] = query.symbol
        return await self._call(
            "open_orders", params,
            lambda payload: BrokerOperationResult(
                status=BrokerOperationStatus.SUCCESS,
                orders=tuple(
                    normalize_order(item)
                    for item in self._native_list(payload))))

    async def _do_get_positions(self, query, context):
        # Spot hesapta türev pozisyonu yoktur (supports_margin ve
        # supports_short profili False) — varlıklar bakiye olarak
        # raporlanır. Deterministik boş başarı; ağ çağrısı gerekmez.
        return BrokerOperationResult(
            status=BrokerOperationStatus.SUCCESS, positions=())

    async def _do_get_balances(self, query, context):
        params = {}
        if query.currency is not None:
            params["asset"] = query.currency
        return await self._call(
            "balances", params,
            lambda payload: BrokerOperationResult(
                status=BrokerOperationStatus.SUCCESS,
                balances=tuple(
                    normalize_balance(item)
                    for item in self._native_list(payload))))

    # ── yazma ────────────────────────────────────────────────────

    async def _do_submit_order(self, request, context):
        params = {
            "symbol": request.symbol,
            "side": request.side.value,
            "type": request.order_type.value,
            "timeInForce": request.time_in_force.value,
            "quantity": str(request.quantity),
            "newClientOrderId": context.idempotency_key,
        }
        if request.price is not None:
            params["price"] = str(request.price)
        return await self._call(
            "submit_order", params,
            lambda payload: BrokerOperationResult(
                status=BrokerOperationStatus.SUCCESS,
                order=normalize_order(payload)))

    async def _do_cancel_order(self, request, context):
        params = {"symbol": request.symbol}
        if request.order_id is not None:
            params["orderId"] = request.order_id
        if request.client_order_id is not None:
            params["origClientOrderId"] = request.client_order_id
        return await self._call(
            "cancel_order", params,
            lambda payload: BrokerOperationResult(
                status=BrokerOperationStatus.SUCCESS,
                order=normalize_order(payload)))

    # ── ortak normalize çağrı yolu ───────────────────────────────

    @staticmethod
    def _native_list(payload: Mapping) -> tuple:
        items = payload.get("items")
        if not isinstance(items, (list, tuple)):
            raise BrokerNormalizationError(
                "MALFORMED_BROKER_RESPONSE")
        return tuple(items)

    async def _call(self, operation: str, params: Mapping,
                    on_success) -> BrokerOperationResult:
        """Tek deneme (retry YOK); her sonuç kanonik sonuca iner;
        hiçbir native istisna veya payload sınırı geçmez."""
        try:
            payload = await self._transport.request(operation,
                                                    params)
        except TransportFailure as failure:
            return error_result(normalize_error(failure.kind))
        except Exception:
            return error_result(
                BrokerErrorCode.UNKNOWN_BROKER_FAILURE)
        if not isinstance(payload, Mapping):
            return error_result(
                BrokerErrorCode.MALFORMED_BROKER_RESPONSE)
        failure = _normalized_failure(payload)
        if failure is not None:
            return failure
        try:
            return on_success(payload)
        except BrokerNormalizationError:
            return error_result(
                BrokerErrorCode.MALFORMED_BROKER_RESPONSE)
