"""Mission 2000 — Agent 08: Kanonik Yürütme API'si.

Yürütme alt sistemine giren TEK kamu yazma giriş noktası.
HTTP/REST/WebSocket UYGULAMAZ — yalnız kanonik yürütme
sözleşmesini açığa çıkarır. Taşıma katmanı (varsa) gelecekte bu
sınırın ÜZERİNE inşa edilir; bu modül ağ, dosya sistemi, ortam
veya secret bilmez.

Sorumluluk: API modeli doğrulama → istek eşleme → Execution
Service'i tam BİR kez çağırma → yanıt eşleme → hata
normalizasyonu. İş mantığı YOKTUR: risk hesaplamaz, emir yeniden
boyutlandırmaz, Kill Switch'e dokunmaz, broker/Binance
incelemez, kimlik üretmez, retry/persist/cache yapmaz.

Doğrulama başarısızsa Execution Service ÇAĞRILMAZ; sonuç
deterministik VALIDATION_FAILED yanıtıdır. Aynı istek + aynı
servis yanıtı → aynı API yanıtı (rastgelelik/UUID/duvar saati
yoktur).
"""

from __future__ import annotations

from typing import Optional

from execution_api_mapper import ExecutionApiMapper
from execution_api_models import (
    ExecutionApiRequest,
    ExecutionApiResponse,
    ExecutionApiStatus,
)
from execution_service import ExecutionService
from execution_service_models import ExecutionServiceResult

__all__ = ["ExecutionApi", "ExecutionApiError",
           "ExecutionApiContractError",
           "ExecutionApiConfigurationError"]

_ERROR_CONTRACT = "INVALID_API_INPUT"
_ERROR_CONFIGURATION = "INVALID_API_DEPENDENCY"

_CODE_MISSING_IDEMPOTENCY = "MISSING_IDEMPOTENCY_KEY"
_CODE_SERVICE_FAILURE = "SERVICE_FAILURE"


class ExecutionApiError(Exception):
    """API istisnalarının tabanı — steril mesajlar taşır."""


class ExecutionApiContractError(ExecutionApiError):
    """Programlama/sözleşme ihlali (operasyonel sonuç DEĞİL)."""


class ExecutionApiConfigurationError(ExecutionApiError):
    """Geçersiz bağımlılık enjeksiyonu."""


class ExecutionApi:
    """Kanonik yürütme sınırı — tek kamu yazma metodu `execute`.

    Durumsuz; yalnız değişmez enjekte Execution Service ve
    durumsuz eşleyici taşır. Yalnız Execution Service'i bilir:
    Risk Engine, Kill Switch, BrokerAdapter veya broker iç
    ayrıntısına erişimi yoktur.
    """

    __slots__ = ("_service", "_mapper")

    def __init__(self, service: ExecutionService):
        if not isinstance(service, ExecutionService):
            raise ExecutionApiConfigurationError(
                _ERROR_CONFIGURATION)
        object.__setattr__(self, "_service", service)
        object.__setattr__(self, "_mapper", ExecutionApiMapper())

    async def execute(self, request: ExecutionApiRequest
                      ) -> ExecutionApiResponse:
        """Tek kamu yazma giriş noktası (asenkron).

        Doğrulama → eşleme → servis (tam BİR kez) → yanıt eşleme.
        Operasyonel sonuçlar istisna değil, yanıttır; bağımlılık
        istisnaları steril kodlara normalize edilir.
        """
        if not isinstance(request, ExecutionApiRequest):
            raise ExecutionApiContractError(_ERROR_CONTRACT)

        # Doğrulama — başarısızsa servis ÇAĞRILMAZ
        code = self._validate(request)
        if code is not None:
            return ExecutionApiResponse(
                status=ExecutionApiStatus.VALIDATION_FAILED,
                code=code)

        service_request = self._mapper.to_service_request(request)

        try:
            result = await self._service.execute(service_request)
        except Exception:
            # Native/servis istisnası sızmaz — steril normalize
            return ExecutionApiResponse(
                status=ExecutionApiStatus.UNKNOWN_FAILURE,
                code=_CODE_SERVICE_FAILURE)
        if not isinstance(result, ExecutionServiceResult):
            return ExecutionApiResponse(
                status=ExecutionApiStatus.UNKNOWN_FAILURE,
                code=_CODE_SERVICE_FAILURE)

        return self._mapper.to_api_response(result)

    @staticmethod
    def _validate(request: ExecutionApiRequest) -> Optional[str]:
        """Gönderim öncesi API doğrulaması — steril kod döndürür.

        Tip/biçim kuralları modelde zorlanır; burada gönderim için
        zorunlu alanların varlığı doğrulanır.
        """
        key = request.idempotency_key
        if key is None or not key.strip():
            return _CODE_MISSING_IDEMPOTENCY
        return None
