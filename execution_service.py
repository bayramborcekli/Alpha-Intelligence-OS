"""Mission 2000 — Agent 07: Kanonik Yürütme Servisi.

TEK kanonik yürütme orkestrasyon yolu. Servis YALNIZ koordine
eder; hiçbir bağımlılığın sorumluluğunu kopyalamaz: risk hesabı
Risk Engine'de, izin birleşimi ExecutionPermissionGate'te, broker
etkileşimi BrokerAdapter'dadır.

Dondurulmuş gönderim sırası:
    1. Servis girdisini doğrula
    2. Kanonik broker adaptörünü çöz (enjekte resolver)
    3. Risk Engine'i değerlendir (deneme başına tam BİR kez)
    4. Yürütme İzin Kapısı'nı değerlendir
    5. Kill Switch'i kontrol et (kapı içinde, güncel okuma)
    6. Broker istek bağlamını kur (kimlikler DEĞİŞMEDEN)
    7. BrokerAdapter'ı çağır (onay sonrası, deneme başına en çok
       BİR kez; finally/hata-kurtarma/döngü içinden asla)
    8. Servis sonucunu normalize et
    9. Değişmez yürütme çıktısını döndür

Zaman-kontrol sınırı (yerel, tek-düğüm): risk değerlendirmesi →
Kill Switch izni → BrokerAdapter çağrısı sırası aynı süreç içinde
ardışıktır; kontrol ile kullanım arasında Kill Switch durumu başka
bir yerel çağrıyla değişebilir. Bu servis dağıtık atomiklik İDDİA
ETMEZ; süreçler-arası tekillik garantisi kalıcılık ve broker
desteği gerektirir ve ertelenmiştir (Mission 2300). Verilen
garanti: başarılı kapılardan sonra servis çağrısı başına tek
BrokerAdapter çağrısı, iç retry yok, çağıran-sahipli idempotency
değişmeden iletilir.

Servis durumsuzdur (değişmez enjekte bağımlılıklar dışında):
emir önbelleği, istek haritası, kilit, kuyruk, arka plan görevi
yoktur. Ağ/dosya sistemi/ortam/secret erişimi yoktur.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Tuple

from execution_broker_adapter import BrokerAdapter
from execution_broker_errors import (
    BrokerAdapterError,
    BrokerErrorCode,
)
from execution_broker_models import (
    BrokerOperationResult,
    BrokerOperationStatus,
    BrokerRequestContext,
)
from execution_kill_switch import KillSwitch
from execution_permission_gate import (
    ExecutionPermission,
    ExecutionPermissionGate,
)
from execution_risk_engine import RiskEngine
from execution_risk_models import (
    BrokerProfile, RiskDecision, RiskDecisionType)
from execution_service_models import (
    ExecutionServiceRequest,
    ExecutionServiceResult,
    ExecutionServiceStatus,
    ExecutionTrace,
    ExecutionTraceStep,
)

__all__ = ["BrokerAdapterResolver", "ExecutionService",
           "ExecutionServiceError", "ExecutionServiceContractError",
           "ExecutionServiceConfigurationError"]

_ERROR_CONTRACT = "INVALID_SERVICE_INPUT"
_ERROR_CONFIGURATION = "INVALID_SERVICE_DEPENDENCY"

_CODE_MISSING_IDEMPOTENCY = "MISSING_IDEMPOTENCY_KEY"
_CODE_UNKNOWN_BROKER = "UNKNOWN_BROKER"
_CODE_RISK_ENGINE_FAILURE = "RISK_ENGINE_FAILURE"
_CODE_BROKER_PROFILE_FAILURE = "BROKER_PROFILE_FAILURE"
_CODE_PERMISSION_GATE_FAILURE = "PERMISSION_GATE_FAILURE"
_CODE_BROKER_FAILURE = "BROKER_FAILURE"

_STEP = ExecutionTraceStep
_STATUS = ExecutionServiceStatus

# RiskDecisionType (ALLOW dışı) → servis sonucu
_RISK_DENIAL_STATUS = {
    RiskDecisionType.REJECT: _STATUS.REJECTED_BY_RISK,
    RiskDecisionType.REQUIRE_CONFIRMATION:
        _STATUS.REQUIRES_CONFIRMATION,
    RiskDecisionType.REDUCE_SIZE: _STATUS.SIZE_REDUCTION_REQUIRED,
}

# BrokerOperationStatus → servis sonucu (kanonik sonuç kayıpsız
# korunur; broker'a özgü durum eşlemesi veya Binance kodu YOKTUR)
_BROKER_STATUS_MAP = {
    BrokerOperationStatus.SUCCESS: _STATUS.SUBMITTED,
    BrokerOperationStatus.REJECTED: _STATUS.BROKER_REJECTED,
    BrokerOperationStatus.NOT_FOUND: _STATUS.UNKNOWN_FAILURE,
    BrokerOperationStatus.UNSUPPORTED:
        _STATUS.BROKER_PERMANENT_FAILURE,
    BrokerOperationStatus.TEMPORARY_FAILURE:
        _STATUS.BROKER_TEMPORARY_FAILURE,
    BrokerOperationStatus.PERMANENT_FAILURE:
        _STATUS.BROKER_PERMANENT_FAILURE,
    BrokerOperationStatus.UNKNOWN: _STATUS.UNKNOWN_FAILURE,
}


class ExecutionServiceError(Exception):
    """Servis istisnalarının tabanı — steril mesajlar taşır."""


class ExecutionServiceContractError(ExecutionServiceError):
    """Programlama/sözleşme ihlali (operasyonel sonuç DEĞİL)."""


class ExecutionServiceConfigurationError(ExecutionServiceError):
    """Geçersiz bağımlılık enjeksiyonu."""


class BrokerAdapterResolver(ABC):
    """Minimal soyut, deterministik, salt-okur çözümleyici.

    Enjekte edilir; global kayıt, service locator, dinamik import,
    yansıma tabanlı keşif, ortam okuması veya eklenti taraması
    YOKTUR. Bilinmeyen broker_id için KeyError/LookupError
    yükseltebilir — servis bunu deterministik, gönderilmemiş
    sonuca normalize eder (başka broker'a geri dönüş YOKTUR).
    """

    __slots__ = ()

    @abstractmethod
    def resolve(self, broker_id: str) -> BrokerAdapter:
        """broker_id → BrokerAdapter (deterministik)."""

    @abstractmethod
    def profile(self, broker_id: str) -> BrokerProfile:
        """broker_id → kanonik statik yetenek profili.

        Reddedilen yolların adaptör çalışma zamanına HİÇ
        dokunmaması için yetenekler resolver'dan okunur; adaptöre
        yalnız onaylı gönderim anında erişilir."""


class ExecutionService:
    """Kanonik yürütme orkestratörü — tek giriş noktası
    `execute`. Durumsuz; yalnız değişmez enjekte bağımlılıklar."""

    __slots__ = ("_risk_engine", "_kill_switch", "_resolver",
                 "_gate")

    def __init__(self, risk_engine: RiskEngine,
                 kill_switch: KillSwitch,
                 resolver: BrokerAdapterResolver):
        if not isinstance(risk_engine, RiskEngine):
            raise ExecutionServiceConfigurationError(
                _ERROR_CONFIGURATION)
        if not isinstance(kill_switch, KillSwitch):
            raise ExecutionServiceConfigurationError(
                _ERROR_CONFIGURATION)
        if not isinstance(resolver, BrokerAdapterResolver):
            raise ExecutionServiceConfigurationError(
                _ERROR_CONFIGURATION)
        object.__setattr__(self, "_risk_engine", risk_engine)
        object.__setattr__(self, "_kill_switch", kill_switch)
        object.__setattr__(self, "_resolver", resolver)
        object.__setattr__(self, "_gate", ExecutionPermissionGate())

    async def execute(self, request: ExecutionServiceRequest
                      ) -> ExecutionServiceResult:
        """Tek kanonik yürütme giriş noktası (asenkron)."""
        if not isinstance(request, ExecutionServiceRequest):
            raise ExecutionServiceContractError(_ERROR_CONTRACT)

        steps: list = []

        # 1 — girdi doğrulama (gönderim için idempotency zorunlu)
        key = request.idempotency_key
        if key is None or not key.strip():
            return self._result(_STATUS.INVALID_REQUEST, steps,
                                code=_CODE_MISSING_IDEMPOTENCY)
        steps.append(_STEP.INPUT_VALIDATED)

        # 2 — broker çözümleme (bilinmeyen broker → deterministik
        # gönderilmemiş sonuç; geri dönüş broker'ı YOK)
        try:
            adapter = self._resolver.resolve(request.broker_id)
        except Exception:
            return self._result(_STATUS.NOT_SUBMITTED, steps,
                                code=_CODE_UNKNOWN_BROKER)
        if not isinstance(adapter, BrokerAdapter):
            return self._result(_STATUS.NOT_SUBMITTED, steps,
                                code=_CODE_UNKNOWN_BROKER)
        steps.append(_STEP.BROKER_RESOLVED)

        # 3 — risk değerlendirmesi (deneme başına tam BİR kez;
        # yetenekler resolver'daki kanonik statik profil üzerinden
        # okunur — reddedilen yollarda adaptör çağrısı SIFIRDIR)
        try:
            profile = self._resolver.profile(request.broker_id)
        except Exception:
            return self._result(_STATUS.UNKNOWN_FAILURE, steps,
                                code=_CODE_BROKER_PROFILE_FAILURE)
        if not isinstance(profile, BrokerProfile):
            return self._result(_STATUS.UNKNOWN_FAILURE, steps,
                                code=_CODE_BROKER_PROFILE_FAILURE)
        try:
            risk_decision = self._risk_engine.validate(
                request.execution_request, request.portfolio,
                request.instrument, profile)
        except Exception:
            return self._result(_STATUS.UNKNOWN_FAILURE, steps,
                                code=_CODE_RISK_ENGINE_FAILURE)
        steps.append(_STEP.RISK_EVALUATED)

        if risk_decision.decision is not RiskDecisionType.ALLOW:
            steps.append(_STEP.RISK_DENIED)
            steps.append(_STEP.EXECUTION_DENIED)
            # REDUCE_SIZE: örtük yeniden boyutlandırma YOK —
            # önerilen miktar korunur, çağıran açıkça onaylanmış
            # YENİ istek + YENİ idempotency anahtarı oluşturmalıdır
            recommended = None
            if risk_decision.decision is \
                    RiskDecisionType.REDUCE_SIZE:
                recommended = risk_decision.approved_quantity
            return self._result(
                _RISK_DENIAL_STATUS[risk_decision.decision], steps,
                code=risk_decision.code,
                risk_decision=risk_decision,
                recommended_quantity=recommended)
        steps.append(_STEP.RISK_ALLOWED)

        # 4/5 — izin kapısı: güncel Kill Switch izni broker yazma
        # çağrısından hemen önce okunur (önbellek YOK)
        try:
            permission = self._gate.evaluate(risk_decision,
                                             self._kill_switch)
        except Exception:
            return self._result(
                _STATUS.UNKNOWN_FAILURE, steps,
                code=_CODE_PERMISSION_GATE_FAILURE,
                risk_decision=risk_decision)
        steps.append(_STEP.KILL_SWITCH_CHECKED)
        if not permission.permitted:
            steps.append(_STEP.EXECUTION_DENIED)
            return self._result(
                _STATUS.BLOCKED_BY_KILL_SWITCH, steps,
                code=permission.code, risk_decision=risk_decision,
                permission=permission)
        steps.append(_STEP.EXECUTION_PERMITTED)

        # 6 — broker istek bağlamı: çağıran-sahipli kimlikler
        # DEĞİŞMEDEN iletilir; servis hiçbir kimlik üretmez
        context = BrokerRequestContext(
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            idempotency_key=key,
            account_id=request.account_id,
            portfolio_id=request.portfolio_id,
            strategy_id=request.strategy_id,
            execution_mode=request.execution_mode,
            logical_sequence=request.logical_sequence)

        # 7 — broker çağrısı: deneme başına EN ÇOK bir kez;
        # retry/finally/kurtarma yolu YOK
        steps.append(_STEP.BROKER_SUBMISSION_STARTED)
        try:
            broker_result = await adapter.submit_order(
                request.execution_request, context)
        except BrokerAdapterError:
            steps.append(_STEP.BROKER_SUBMISSION_FAILED)
            return self._result(
                _STATUS.UNKNOWN_FAILURE, steps,
                code=_CODE_BROKER_FAILURE,
                risk_decision=risk_decision, permission=permission)
        except Exception:
            steps.append(_STEP.BROKER_SUBMISSION_FAILED)
            return self._result(
                _STATUS.UNKNOWN_FAILURE, steps,
                code=_CODE_BROKER_FAILURE,
                risk_decision=risk_decision, permission=permission)
        if not isinstance(broker_result, BrokerOperationResult):
            steps.append(_STEP.BROKER_SUBMISSION_FAILED)
            return self._result(
                _STATUS.UNKNOWN_FAILURE, steps,
                code=_CODE_BROKER_FAILURE,
                risk_decision=risk_decision, permission=permission)
        steps.append(_STEP.BROKER_SUBMISSION_COMPLETED)

        # 8/9 — sonuç normalizasyonu (kanonik sonuç kayıpsız)
        steps.append(_STEP.RESULT_NORMALIZED)
        status = _BROKER_STATUS_MAP[broker_result.status]
        if (status is _STATUS.BROKER_TEMPORARY_FAILURE
                and broker_result.error is not None
                and broker_result.error.code is
                BrokerErrorCode.BROKER_UNAVAILABLE):
            status = _STATUS.BROKER_UNAVAILABLE
        code = None
        if broker_result.error is not None:
            code = broker_result.error.code.value
        return self._result(status, steps, code=code,
                            risk_decision=risk_decision,
                            permission=permission,
                            broker_result=broker_result)

    @staticmethod
    def _result(status: ExecutionServiceStatus, steps: list,
                code: Optional[str] = None,
                risk_decision: Optional[RiskDecision] = None,
                permission: Optional[ExecutionPermission] = None,
                broker_result: Optional[BrokerOperationResult]
                = None,
                recommended_quantity=None
                ) -> ExecutionServiceResult:
        trace = ExecutionTrace(steps=tuple(steps))
        return ExecutionServiceResult(
            status=status, trace=trace, code=code,
            risk_decision=risk_decision, permission=permission,
            broker_result=broker_result,
            recommended_quantity=recommended_quantity)
