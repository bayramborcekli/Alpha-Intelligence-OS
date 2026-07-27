"""Mission 2100 — Agent 08: Kontrollü Yürütme API modelleri.

Tamamı değişmez (frozen+slots) veri sınıfları ve kapalı enum'lar.
Bu katman PAPER / SHADOW / MICRO_LIVE için TEK giriş noktasının
veri zarflarıdır: emri kendisi YÜRÜTMEZ, borsaya/broker'a
BAĞLANMAZ, Risk Motoru / Kill Switch / Yetkilendirme atlanamaz —
tümü alt servislerde zorlanır ve API bunları BAYPAS EDEMEZ.

Kimlik / zaman / UUID / rastgelelik ÜRETİLMEZ — tüm kimlikler ve
mantıksal sıralar çağıran-sahiplidir. Örtük varsayılan YOKTUR:
işlem için zorunlu alan eksikse istek sözleşme hatasıyla
REDDEDİLİR (fail-closed).

NOT: Spesifikasyon `controlled_execution_models.py` adını ister;
o dosya Agent 01 (Foundation) tarafından sahiplenilmiştir ve
Agents 01–07 DEĞİŞTİRİLEMEZ. Bu modül bilinçli olarak `_api_`
ekiyle adlandırılmıştır (raporda gerekçeli).

Bilinçli ek modeller: ControlledExecutionOperation /
ControlledExecutionAPIDecision (kapalı kümeler) ve
ControlledExecutionState (mod-özel çağıran-sahipli durum zarfı —
API durumsuz kalır).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Optional, Tuple

from controlled_execution_models import (ControlledExecutionMode,
                                         ControlledExecutionPolicy)
from controlled_execution_api_errors import (
    ControlledExecutionAPIContractError)
from execution_kill_switch_models import KillSwitchSnapshot
from execution_models import ExecutionRequest
from micro_live_models import (MicroLiveLimits, MicroLiveRequest,
                               MicroLiveSnapshot,
                               MicroLiveReferences)
from paper_execution_models import PaperExecutionReferences
from paper_models import PaperLedgerSnapshot
from shadow_models import (ShadowMarketObservation,
                           ShadowSnapshot)

__all__ = ["ControlledExecutionOperation",
           "ControlledExecutionAPIDecision",
           "ControlledExecutionRequest",
           "ControlledExecutionState",
           "ControlledExecutionAudit",
           "ControlledExecutionStatus",
           "ControlledExecutionStatistics",
           "ControlledExecutionResponse"]

_ERROR_INVALID = "INVALID_API_FIELD"


def _fail(field: str) -> None:
    raise ControlledExecutionAPIContractError(
        f"{_ERROR_INVALID}:{field}")


def _require_reference(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(field)


def _require_optional_reference(value: object,
                                field: str) -> None:
    if value is None:
        return
    _require_reference(value, field)


def _require_int(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or \
            value < 0:
        _fail(field)


def _require_bool(value: object, field: str) -> None:
    if not isinstance(value, bool):
        _fail(field)


def _require_enum(value: object, enum_type: type,
                  field: str) -> None:
    if not isinstance(value, enum_type):
        _fail(field)


def _require_optional_type(value: object, expected: type,
                           field: str) -> None:
    if value is None:
        return
    if not isinstance(value, expected):
        _fail(field)


def _require_tuple_of(value: object, element_type: type,
                      field: str) -> None:
    if not isinstance(value, tuple):
        _fail(field)
    for element in value:
        if not isinstance(element, element_type):
            _fail(field)


@unique
class ControlledExecutionOperation(Enum):
    """Kapalı API işlem kümesi — tek giriş noktası."""

    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"
    STATUS = "STATUS"
    POSITIONS = "POSITIONS"
    ORDERS = "ORDERS"
    EXECUTIONS = "EXECUTIONS"
    STATISTICS = "STATISTICS"
    HEARTBEAT = "HEARTBEAT"


@unique
class ControlledExecutionAPIDecision(Enum):
    """Kapalı üst karar kümesi — API asla GERÇEK yürütmez."""

    ACCEPTED = "ACCEPTED"
    DENIED = "DENIED"
    RECOMMENDATION_ONLY = "RECOMMENDATION_ONLY"
    REPORTED = "REPORTED"


@dataclass(frozen=True, slots=True)
class ControlledExecutionRequest:
    """Değişmez birleşik API isteği.

    Mod-özel yükler isteğe bağlı alanlardır; işlem için zorunlu
    alanın eksikliği API katmanında MISSING_API_FIELD ile
    REDDEDİLİR — örtük varsayılan YOKTUR."""

    mode: ControlledExecutionMode
    operation: ControlledExecutionOperation
    request_reference: str
    logical_sequence: int
    policy: Optional[ControlledExecutionPolicy] = None
    kill_switch: Optional[KillSwitchSnapshot] = None
    execution: Optional[ExecutionRequest] = None
    order_reference: Optional[str] = None
    observation: Optional[ShadowMarketObservation] = None
    micro_live_request: Optional[MicroLiveRequest] = None
    micro_live_limits: Optional[MicroLiveLimits] = None

    def __post_init__(self) -> None:
        _require_enum(self.mode, ControlledExecutionMode, "mode")
        _require_enum(self.operation,
                      ControlledExecutionOperation, "operation")
        _require_reference(self.request_reference,
                           "request_reference")
        _require_int(self.logical_sequence, "logical_sequence")
        _require_optional_type(self.policy,
                               ControlledExecutionPolicy,
                               "policy")
        _require_optional_type(self.kill_switch,
                               KillSwitchSnapshot, "kill_switch")
        _require_optional_type(self.execution, ExecutionRequest,
                               "execution")
        _require_optional_reference(self.order_reference,
                                    "order_reference")
        _require_optional_type(self.observation,
                               ShadowMarketObservation,
                               "observation")
        _require_optional_type(self.micro_live_request,
                               MicroLiveRequest,
                               "micro_live_request")
        _require_optional_type(self.micro_live_limits,
                               MicroLiveLimits,
                               "micro_live_limits")


@dataclass(frozen=True, slots=True)
class ControlledExecutionState:
    """Mod-özel çağıran-sahipli durum zarfı (bilinçli ek model).

    API durumsuzdur: defter / gölge / yetki anlık görüntüleri ve
    referans kümeleri HER çağrıda çağıran tarafından verilir."""

    ledger: Optional[PaperLedgerSnapshot] = None
    shadow: Optional[ShadowSnapshot] = None
    micro_live: Optional[MicroLiveSnapshot] = None
    paper_references: Optional[PaperExecutionReferences] = None
    micro_live_references: Optional[MicroLiveReferences] = None

    def __post_init__(self) -> None:
        _require_optional_type(self.ledger, PaperLedgerSnapshot,
                               "ledger")
        _require_optional_type(self.shadow, ShadowSnapshot,
                               "shadow")
        _require_optional_type(self.micro_live,
                               MicroLiveSnapshot, "micro_live")
        _require_optional_type(self.paper_references,
                               PaperExecutionReferences,
                               "paper_references")
        _require_optional_type(self.micro_live_references,
                               MicroLiveReferences,
                               "micro_live_references")


@dataclass(frozen=True, slots=True)
class ControlledExecutionAudit:
    """Değişmez API denetim kaydı (steril kod)."""

    audit_code: str
    request_reference: str
    logical_sequence: int

    def __post_init__(self) -> None:
        _require_reference(self.audit_code, "audit_code")
        _require_reference(self.request_reference,
                           "request_reference")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class ControlledExecutionStatus:
    """Mod başına türetilmiş, değişmez durum görüntüsü."""

    mode: ControlledExecutionMode
    alive: bool
    order_count: int = 0
    execution_count: int = 0
    authorization_count: int = 0
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_enum(self.mode, ControlledExecutionMode, "mode")
        _require_bool(self.alive, "alive")
        _require_int(self.order_count, "order_count")
        _require_int(self.execution_count, "execution_count")
        _require_int(self.authorization_count,
                     "authorization_count")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class ControlledExecutionStatistics:
    """Mod başına türetilmiş, değişmez sayaçlar."""

    mode: ControlledExecutionMode
    total_orders: int = 0
    total_executions: int = 0
    total_denied: int = 0
    total_cancels: int = 0
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_enum(self.mode, ControlledExecutionMode, "mode")
        _require_int(self.total_orders, "total_orders")
        _require_int(self.total_executions, "total_executions")
        _require_int(self.total_denied, "total_denied")
        _require_int(self.total_cancels, "total_cancels")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class ControlledExecutionResponse:
    """Değişmez birleşik API yanıtı — mutasyon YOK.

    `payload` alt servisin DEĞİŞMEZ sonucunu (ya da salt-okunur
    demetini) şeffaflık için taşır; API sonucu yeniden yazmaz."""

    mode: ControlledExecutionMode
    operation: ControlledExecutionOperation
    decision: ControlledExecutionAPIDecision
    decision_code: str
    request_reference: str
    logical_sequence: int
    execution_reference: Optional[str] = None
    ledger_reference: Optional[str] = None
    audit: Tuple[ControlledExecutionAudit, ...] = ()
    statistics: Optional[ControlledExecutionStatistics] = None
    status: Optional[ControlledExecutionStatus] = None
    payload: object = None

    def __post_init__(self) -> None:
        _require_enum(self.mode, ControlledExecutionMode, "mode")
        _require_enum(self.operation,
                      ControlledExecutionOperation, "operation")
        _require_enum(self.decision,
                      ControlledExecutionAPIDecision, "decision")
        _require_reference(self.decision_code, "decision_code")
        _require_reference(self.request_reference,
                           "request_reference")
        _require_int(self.logical_sequence, "logical_sequence")
        _require_optional_reference(self.execution_reference,
                                    "execution_reference")
        _require_optional_reference(self.ledger_reference,
                                    "ledger_reference")
        _require_tuple_of(self.audit, ControlledExecutionAudit,
                          "audit")
        _require_optional_type(self.statistics,
                               ControlledExecutionStatistics,
                               "statistics")
        _require_optional_type(self.status,
                               ControlledExecutionStatus,
                               "status")
