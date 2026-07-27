"""Mission 2100 — Agent 06: Micro Live yetkilendirme modelleri.

Tamamı değişmez (frozen+slots) veri sınıfları ve kapalı enum'lar.
Bu katman GELECEKTEKİ bir micro-live yürütme isteğini yalnız
YETKİLENDİRİR ya da REDDEDER: emir vermez, borsaya/broker'a
BAĞLANMAZ, bakiye/pozisyon değiştirmez, işlem YÜRÜTMEZ.

Kimlik / zaman / UUID / rastgelelik ÜRETİLMEZ — tüm kimlikler ve
mantıksal sıralar çağıran-sahiplidir. Finansal değerler YALNIZ
Decimal; eksik zorunlu alan sözleşme hatasıyla REDDEDİLİR
(fail-closed). Kalıcı yetkilendirme YOKTUR: her onay mantıksal
sıra tabanlı bir son kullanma taşımak ZORUNDADIR.

Bilinçli ek modeller (spesifikasyon listesi dışında, raporda
gerekçeli): MicroLiveReferences (çağıran-sahipli kimlik kümesi),
MicroLiveSnapshot (değişmez durum sahibi — servis durumsuz kalır),
MicroLiveStatistics (türetilmiş sayaçlar) ve MicroLiveResult
(karar ↔ kod kapalı eşleşmeli sonuç zarfı).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, unique
from typing import Optional, Tuple

from controlled_execution_models import ControlledExecutionMode
from execution_enums import OrderSide, OrderType
from micro_live_errors import MicroLiveContractError

__all__ = ["MicroLiveAuthorizationState", "MicroLiveOperation",
           "MicroLiveDecision", "MicroLiveDecisionCode",
           "MicroLiveStage", "MicroLiveScope", "MicroLiveLimits",
           "MicroLiveRequest", "MicroLiveApproval",
           "MicroLiveAudit", "MicroLiveAuthorization",
           "MicroLiveReferences", "MicroLiveSnapshot",
           "MicroLiveStatistics", "MicroLiveHeartbeat",
           "MicroLiveResult"]

_ERROR_INVALID_FIELD = "INVALID_MICRO_LIVE_FIELD"

_ZERO = Decimal("0")


def _fail(field: str) -> None:
    raise MicroLiveContractError(
        f"{_ERROR_INVALID_FIELD}:{field}")


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


def _require_enum(value: object, enum_type: type,
                  field: str) -> None:
    if not isinstance(value, enum_type):
        _fail(field)


def _require_decimal(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        _fail(field)
    if not value.is_finite() or value <= _ZERO:
        _fail(field)


def _require_tuple_of(value: object, element_type: type,
                      field: str) -> None:
    if not isinstance(value, tuple):
        _fail(field)
    for element in value:
        if not isinstance(element, element_type):
            _fail(field)


@unique
class MicroLiveAuthorizationState(Enum):
    """Kapalı yetkilendirme durum kümesi.

    Örtük geçiş, otomatik onay, geri-düşme onayı ve KALICI
    yetkilendirme YOKTUR."""

    NONE = "NONE"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@unique
class MicroLiveOperation(Enum):
    """Kapalı işlem kümesi — yürütme işlemi YOKTUR."""

    REQUEST_AUTHORIZATION = "REQUEST_AUTHORIZATION"
    APPROVE = "APPROVE"
    DENY = "DENY"
    EXPIRE = "EXPIRE"
    REVOKE = "REVOKE"
    EVALUATE = "EVALUATE"


@unique
class MicroLiveDecision(Enum):
    """Kapalı üst karar kümesi.

    ACCEPTED: durum geçişi kaydedildi. DENIED: geçiş/istek
    reddedildi. AUTHORIZED / NOT_AUTHORIZED: yalnız salt-okunur
    değerlendirme sonucu — yürütme İZNİ değildir, yürütme bu
    katmanda YOKTUR."""

    ACCEPTED = "ACCEPTED"
    DENIED = "DENIED"
    AUTHORIZED = "AUTHORIZED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


@unique
class MicroLiveDecisionCode(Enum):
    """Kapalı karar kodu kümesi — steril, deterministik."""

    AUTHORIZATION_REQUESTED = "AUTHORIZATION_REQUESTED"
    AUTHORIZATION_APPROVED = "AUTHORIZATION_APPROVED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    AUTHORIZATION_REVOKED = "AUTHORIZATION_REVOKED"
    EVALUATION_AUTHORIZED = "EVALUATION_AUTHORIZED"
    MODE_DENIED = "MODE_DENIED"
    POLICY_DENIED = "POLICY_DENIED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    KILL_SWITCH_DENIED = "KILL_SWITCH_DENIED"
    RISK_DENIED = "RISK_DENIED"
    TRANSITION_DENIED = "TRANSITION_DENIED"
    SCOPE_DENIED = "SCOPE_DENIED"
    LIMIT_DENIED = "LIMIT_DENIED"
    NOT_AUTHORIZED_STATE = "NOT_AUTHORIZED_STATE"
    NOT_AUTHORIZED_EXPIRED = "NOT_AUTHORIZED_EXPIRED"


@unique
class MicroLiveStage(Enum):
    """Sabit boru hattı denetim aşamaları — sıra değişmez."""

    REQUEST_VALIDATED = "REQUEST_VALIDATED"
    MODE_VALIDATED = "MODE_VALIDATED"
    TRANSITION_VALIDATED = "TRANSITION_VALIDATED"
    RISK_EVALUATED = "RISK_EVALUATED"
    PERMISSION_EVALUATED = "PERMISSION_EVALUATED"
    KILL_SWITCH_CHECKED = "KILL_SWITCH_CHECKED"
    LIMITS_VALIDATED = "LIMITS_VALIDATED"
    AUTHORIZATION_RECORDED = "AUTHORIZATION_RECORDED"
    EVALUATION_COMPLETED = "EVALUATION_COMPLETED"


# Karar ↔ kod eşleşmesi (kapalı tutarlılık kuralı)
_ACCEPTED_CODES = frozenset({
    MicroLiveDecisionCode.AUTHORIZATION_REQUESTED,
    MicroLiveDecisionCode.AUTHORIZATION_APPROVED,
    MicroLiveDecisionCode.AUTHORIZATION_DENIED,
    MicroLiveDecisionCode.AUTHORIZATION_EXPIRED,
    MicroLiveDecisionCode.AUTHORIZATION_REVOKED})
_DENIED_CODES = frozenset({
    MicroLiveDecisionCode.MODE_DENIED,
    MicroLiveDecisionCode.POLICY_DENIED,
    MicroLiveDecisionCode.PERMISSION_DENIED,
    MicroLiveDecisionCode.KILL_SWITCH_DENIED,
    MicroLiveDecisionCode.RISK_DENIED,
    MicroLiveDecisionCode.TRANSITION_DENIED,
    MicroLiveDecisionCode.SCOPE_DENIED,
    MicroLiveDecisionCode.LIMIT_DENIED})
_AUTHORIZED_CODES = frozenset({
    MicroLiveDecisionCode.EVALUATION_AUTHORIZED})
_NOT_AUTHORIZED_CODES = frozenset({
    MicroLiveDecisionCode.NOT_AUTHORIZED_STATE,
    MicroLiveDecisionCode.NOT_AUTHORIZED_EXPIRED,
    MicroLiveDecisionCode.MODE_DENIED,
    MicroLiveDecisionCode.POLICY_DENIED,
    MicroLiveDecisionCode.PERMISSION_DENIED,
    MicroLiveDecisionCode.KILL_SWITCH_DENIED,
    MicroLiveDecisionCode.RISK_DENIED,
    MicroLiveDecisionCode.SCOPE_DENIED,
    MicroLiveDecisionCode.LIMIT_DENIED})


@dataclass(frozen=True, slots=True)
class MicroLiveScope:
    """Onay kapsamı — yetki YALNIZ bu kapsam için geçerlidir.

    Kapsam dışı her istek reddedilir; kapsam genişletme YOKTUR."""

    symbol: str
    side: OrderSide
    order_type: OrderType

    def __post_init__(self) -> None:
        _require_reference(self.symbol, "symbol")
        _require_enum(self.side, OrderSide, "side")
        _require_enum(self.order_type, OrderType, "order_type")


@dataclass(frozen=True, slots=True)
class MicroLiveLimits:
    """Değişmez limit kümesi — tüm limitler zorunlu ve pozitif.

    Limitsiz (None) micro-live yetkisi YOKTUR."""

    maximum_order_quantity: Decimal
    maximum_notional: Decimal
    maximum_position_size: Decimal
    maximum_open_orders: int
    maximum_daily_executions: int
    maximum_daily_loss: Decimal
    maximum_exposure: Decimal
    maximum_leverage: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.maximum_order_quantity,
                         "maximum_order_quantity")
        _require_decimal(self.maximum_notional,
                         "maximum_notional")
        _require_decimal(self.maximum_position_size,
                         "maximum_position_size")
        _require_int(self.maximum_open_orders,
                     "maximum_open_orders")
        if self.maximum_open_orders == 0:
            _fail("maximum_open_orders")
        _require_int(self.maximum_daily_executions,
                     "maximum_daily_executions")
        if self.maximum_daily_executions == 0:
            _fail("maximum_daily_executions")
        _require_decimal(self.maximum_daily_loss,
                         "maximum_daily_loss")
        _require_decimal(self.maximum_exposure,
                         "maximum_exposure")
        _require_decimal(self.maximum_leverage,
                         "maximum_leverage")


@dataclass(frozen=True, slots=True)
class MicroLiveRequest:
    """Yetkilendirme isteği — tüm alanlar ZORUNLU.

    Eksik/geçersiz alan sözleşme hatasıyla reddedilir (DENY,
    fail-closed). Son kullanma, mantıksal sıra tabanlıdır ve
    mevcut sıradan BÜYÜK olmak zorundadır — kalıcı yetki yok."""

    authorization_reference: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    maximum_notional: Decimal
    execution_mode: ControlledExecutionMode
    expiry_sequence: int
    scope: MicroLiveScope
    logical_sequence: int

    def __post_init__(self) -> None:
        _require_reference(self.authorization_reference,
                           "authorization_reference")
        _require_reference(self.symbol, "symbol")
        _require_enum(self.side, OrderSide, "side")
        _require_enum(self.order_type, OrderType, "order_type")
        _require_decimal(self.quantity, "quantity")
        _require_decimal(self.maximum_notional,
                         "maximum_notional")
        _require_enum(self.execution_mode,
                      ControlledExecutionMode, "execution_mode")
        _require_int(self.expiry_sequence, "expiry_sequence")
        if not isinstance(self.scope, MicroLiveScope):
            _fail("scope")
        _require_int(self.logical_sequence, "logical_sequence")
        if self.expiry_sequence <= self.logical_sequence:
            _fail("expiry_sequence")


@dataclass(frozen=True, slots=True)
class MicroLiveApproval:
    """Açık insan onayı kaydı — otomatik onay YOKTUR.

    Onay, süresi mantıksal sıra tabanlı dolan geçici bir
    yetkidir; kalıcı onay temsil EDİLEMEZ."""

    approval_reference: str
    approver_reference: str
    authorization_reference: str
    expiry_sequence: int
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.approval_reference,
                           "approval_reference")
        _require_reference(self.approver_reference,
                           "approver_reference")
        _require_reference(self.authorization_reference,
                           "authorization_reference")
        _require_int(self.expiry_sequence, "expiry_sequence")
        _require_int(self.logical_sequence, "logical_sequence")
        if self.expiry_sequence <= self.logical_sequence:
            _fail("expiry_sequence")


@dataclass(frozen=True, slots=True)
class MicroLiveAudit:
    """Değişmez denetim kaydı — kimlik çağırandan TÜRETİLİR."""

    audit_reference: str
    stage: MicroLiveStage
    event_code: str
    subject_reference: Optional[str] = None
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.audit_reference,
                           "audit_reference")
        _require_enum(self.stage, MicroLiveStage, "stage")
        _require_reference(self.event_code, "event_code")
        _require_optional_reference(self.subject_reference,
                                    "subject_reference")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class MicroLiveAuthorization:
    """Değişmez yetkilendirme kaydı.

    NONE durumu SAKLANAMAZ (yokluk demektir). APPROVED ve
    REVOKED onay kaydı taşımak ZORUNDADIR; PENDING/DENIED onay
    taşıyamaz. Durum ilerledikçe YENİ kayıt üretilir."""

    authorization_reference: str
    request: MicroLiveRequest
    limits: MicroLiveLimits
    state: MicroLiveAuthorizationState
    approval: Optional[MicroLiveApproval] = None
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.authorization_reference,
                           "authorization_reference")
        if not isinstance(self.request, MicroLiveRequest):
            _fail("request")
        if not isinstance(self.limits, MicroLiveLimits):
            _fail("limits")
        _require_enum(self.state, MicroLiveAuthorizationState,
                      "state")
        if self.state is MicroLiveAuthorizationState.NONE:
            _fail("state")
        if self.approval is not None and not isinstance(
                self.approval, MicroLiveApproval):
            _fail("approval")
        _require_int(self.logical_sequence, "logical_sequence")
        if self.request.authorization_reference != \
                self.authorization_reference:
            _fail("request")
        if self.approval is not None and \
                self.approval.authorization_reference != \
                self.authorization_reference:
            _fail("approval")
        self._require_state_consistency()

    def _require_state_consistency(self) -> None:
        approved_like = self.state in (
            MicroLiveAuthorizationState.APPROVED,
            MicroLiveAuthorizationState.REVOKED)
        if approved_like and self.approval is None:
            _fail("approval")
        pending_like = self.state in (
            MicroLiveAuthorizationState.PENDING,
            MicroLiveAuthorizationState.DENIED)
        if pending_like and self.approval is not None:
            _fail("approval")


@dataclass(frozen=True, slots=True)
class MicroLiveReferences:
    """Çağıran-sahipli kimlik kümesi — servis kimlik ÜRETMEZ."""

    request_reference: str
    snapshot_reference: str
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.request_reference,
                           "request_reference")
        _require_reference(self.snapshot_reference,
                           "snapshot_reference")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class MicroLiveSnapshot:
    """Değişmez yetkilendirme durumu — servis DEĞİL, durum sahibi.

    Her yazan işlem YENİ bir anlık görüntü döner; mevcut görüntü
    asla değiştirilmez. Yetki referansları benzersizdir."""

    snapshot_reference: str
    authorizations: Tuple[MicroLiveAuthorization, ...] = ()
    denied_count: int = 0
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.snapshot_reference,
                           "snapshot_reference")
        _require_tuple_of(self.authorizations,
                          MicroLiveAuthorization,
                          "authorizations")
        _require_int(self.denied_count, "denied_count")
        _require_int(self.logical_sequence, "logical_sequence")
        references = tuple(
            record.authorization_reference
            for record in self.authorizations)
        if len(frozenset(references)) != len(references):
            _fail("authorizations")

    def authorization_for(self, authorization_reference: str
                          ) -> Optional[MicroLiveAuthorization]:
        """Referansla yetki arama — bulunamazsa None."""
        for record in self.authorizations:
            if record.authorization_reference == \
                    authorization_reference:
                return record
        return None

    def count_in_state(self, state: MicroLiveAuthorizationState
                       ) -> int:
        """Durum başına deterministik sayaç."""
        return len(tuple(record
                         for record in self.authorizations
                         if record.state is state))

    def statistics(self) -> "MicroLiveStatistics":
        """Türetilmiş sayaçlar — saklanan ayrı durum yoktur."""
        return MicroLiveStatistics(
            total_authorizations=len(self.authorizations),
            pending_count=self.count_in_state(
                MicroLiveAuthorizationState.PENDING),
            approved_count=self.count_in_state(
                MicroLiveAuthorizationState.APPROVED),
            denied_state_count=self.count_in_state(
                MicroLiveAuthorizationState.DENIED),
            expired_count=self.count_in_state(
                MicroLiveAuthorizationState.EXPIRED),
            revoked_count=self.count_in_state(
                MicroLiveAuthorizationState.REVOKED),
            total_denied=self.denied_count,
            logical_sequence=self.logical_sequence)


@dataclass(frozen=True, slots=True)
class MicroLiveStatistics:
    """Anlık görüntüden türetilmiş deterministik sayaçlar."""

    total_authorizations: int = 0
    pending_count: int = 0
    approved_count: int = 0
    denied_state_count: int = 0
    expired_count: int = 0
    revoked_count: int = 0
    total_denied: int = 0
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_int(self.total_authorizations,
                     "total_authorizations")
        _require_int(self.pending_count, "pending_count")
        _require_int(self.approved_count, "approved_count")
        _require_int(self.denied_state_count,
                     "denied_state_count")
        _require_int(self.expired_count, "expired_count")
        _require_int(self.revoked_count, "revoked_count")
        _require_int(self.total_denied, "total_denied")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class MicroLiveHeartbeat:
    """Deterministik kalp atışı — duvar saati/ağ içermez."""

    alive: bool
    authorization_count: int = 0
    pending_count: int = 0
    approved_count: int = 0
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.alive, bool):
            _fail("alive")
        _require_int(self.authorization_count,
                     "authorization_count")
        _require_int(self.pending_count, "pending_count")
        _require_int(self.approved_count, "approved_count")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class MicroLiveResult:
    """Değişmez işlem sonucu zarfı (bilinçli ek model).

    `snapshot` bir SONRAKİ yetkilendirme durumudur; reddedilen ve
    salt-okunur yollarda kayıt kümesi girdiyle AYNIDIR. Karar ↔
    kod kapalı eşleşme kuralına bağlıdır. AUTHORIZED yalnız
    değerlendirme sonucudur — yürütme İZNİ/eylemi DEĞİLDİR."""

    operation: MicroLiveOperation
    decision: MicroLiveDecision
    decision_code: MicroLiveDecisionCode
    snapshot: MicroLiveSnapshot
    authorization_reference: Optional[str] = None
    audit: Tuple[MicroLiveAudit, ...] = ()
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_enum(self.operation, MicroLiveOperation,
                      "operation")
        _require_enum(self.decision, MicroLiveDecision,
                      "decision")
        _require_enum(self.decision_code, MicroLiveDecisionCode,
                      "decision_code")
        if not isinstance(self.snapshot, MicroLiveSnapshot):
            _fail("snapshot")
        _require_optional_reference(
            self.authorization_reference,
            "authorization_reference")
        _require_tuple_of(self.audit, MicroLiveAudit, "audit")
        _require_int(self.logical_sequence, "logical_sequence")
        self._require_consistency()

    def _require_consistency(self) -> None:
        if self.decision is MicroLiveDecision.ACCEPTED:
            if self.decision_code not in _ACCEPTED_CODES:
                _fail("decision_code")
        elif self.decision is MicroLiveDecision.DENIED:
            if self.decision_code not in _DENIED_CODES:
                _fail("decision_code")
        elif self.decision is MicroLiveDecision.AUTHORIZED:
            if self.decision_code not in _AUTHORIZED_CODES:
                _fail("decision_code")
        elif self.decision_code not in _NOT_AUTHORIZED_CODES:
            _fail("decision_code")

    @property
    def authorized(self) -> bool:
        """Yalnız değerlendirme sonucu — yürütme eylemi değildir."""
        return self.decision is MicroLiveDecision.AUTHORIZED

    def audit_stage_codes(self) -> Tuple[str, ...]:
        """Ulaşılan aşamaların steril kodları (sıralı)."""
        return tuple(record.event_code for record in self.audit)
