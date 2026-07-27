"""Mission 2100 — Agent 06: Micro Live yetkilendirme politikası.

Kapalı geçiş matrisi ve deterministik politika kuralları. Bu
katman emir vermez, borsaya bağlanmaz; yalnız gelecekteki bir
micro-live isteğinin yetkilendirilip yetkilendirilemeyeceğini
belirleyen KURALLARI taşır.

Geçiş matrisi (izinli):
NONE → PENDING, PENDING → APPROVED / DENIED / EXPIRED,
APPROVED → REVOKED / EXPIRED. DİĞER TÜM GEÇİŞLER RED.

Örtük geçiş, otomatik onay, geri-düşme onayı, kalıcı yetki YOK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from controlled_execution_models import (ControlledExecutionMode,
                                         ControlledExecutionPolicy)
from execution_kill_switch_models import (KillSwitchSnapshot,
                                          KillSwitchState)
from execution_risk_models import RiskDecision, RiskDecisionType
from micro_live_models import (MicroLiveApproval,
                               MicroLiveAuthorization,
                               MicroLiveAuthorizationState,
                               MicroLiveLimits, MicroLiveRequest,
                               MicroLiveScope)

__all__ = ["MicroLiveTransitionPolicy",
           "MicroLiveAuthorizationPolicy"]

_STATE = MicroLiveAuthorizationState

# Kapalı geçiş matrisi — matris dışı HER geçiş reddedilir.
_ALLOWED_TRANSITIONS = frozenset({
    (_STATE.NONE, _STATE.PENDING),
    (_STATE.PENDING, _STATE.APPROVED),
    (_STATE.PENDING, _STATE.DENIED),
    (_STATE.PENDING, _STATE.EXPIRED),
    (_STATE.APPROVED, _STATE.REVOKED),
    (_STATE.APPROVED, _STATE.EXPIRED)})


@dataclass(frozen=True, slots=True)
class MicroLiveTransitionPolicy:
    """Kapalı geçiş matrisi — deterministik, durumsuz.

    DENIED / EXPIRED / REVOKED terminaldir; hiçbir durumdan
    doğrudan APPROVED'a örtük yol YOKTUR."""

    @staticmethod
    def transition_allowed(current: object,
                           target: object) -> bool:
        """Matris üyeliği — bilinmeyen tür DAİMA red."""
        if not isinstance(current,
                          MicroLiveAuthorizationState) or \
                not isinstance(target,
                               MicroLiveAuthorizationState):
            return False
        return (current, target) in _ALLOWED_TRANSITIONS

    @staticmethod
    def allowed_targets(current: object) -> frozenset:
        """Bir durumdan çıkabilen hedefler (değişmez küme)."""
        if not isinstance(current, MicroLiveAuthorizationState):
            return frozenset()
        return frozenset(
            target for source, target in _ALLOWED_TRANSITIONS
            if source is current)

    @staticmethod
    def terminal(state: object) -> bool:
        """Terminal durumlar — çıkış geçişi olmayanlar."""
        if not isinstance(state, MicroLiveAuthorizationState):
            return False
        return state in (_STATE.DENIED, _STATE.EXPIRED,
                         _STATE.REVOKED)


@dataclass(frozen=True, slots=True)
class MicroLiveAuthorizationPolicy:
    """Deterministik yetkilendirme kuralları — fail-closed.

    Bu politika katmanı borsa yazmayı HİÇBİR koşulda geçerli
    saymaz: yetkilendirme sınırı yazma YETKİSİ üretmez, yalnız
    gelecekteki isteğin sınırlarını belirler."""

    @staticmethod
    def mode_valid(policy: object) -> bool:
        """Yalnız MICRO_LIVE; dönüştürme/geri düşme YOK."""
        if not isinstance(policy, ControlledExecutionPolicy):
            return False
        return policy.mode is ControlledExecutionMode.MICRO_LIVE

    @staticmethod
    def policy_valid(policy: ControlledExecutionPolicy) -> bool:
        """MICRO_LIVE politika doğrulaması.

        Borsa yazma talebi bu katmanda DAİMA geçersizdir; açık
        yetkilendirme zorunluluğu ve yetki referansı şarttır."""
        if policy.exchange_write_allowed:
            return False
        if not policy.explicit_authorization_required:
            return False
        return policy.authorization_reference is not None

    @staticmethod
    def policy_reference_match(
            policy: ControlledExecutionPolicy,
            authorization_reference: str) -> bool:
        """Politika, değerlendirilen yetkiyi açıkça adreslemeli."""
        return policy.authorization_reference == \
            authorization_reference

    @staticmethod
    def scope_valid(request: MicroLiveRequest,
                    scope: MicroLiveScope) -> bool:
        """İstek, onay kapsamının DIŞINA çıkamaz."""
        return (request.symbol == scope.symbol
                and request.side is scope.side
                and request.order_type is scope.order_type)

    @staticmethod
    def within_limits(request: MicroLiveRequest,
                      limits: MicroLiveLimits) -> bool:
        """Limit aşan istek yetkilendirilemez (fail-closed)."""
        if request.quantity > limits.maximum_order_quantity:
            return False
        if request.maximum_notional > limits.maximum_notional:
            return False
        return request.maximum_notional <= \
            limits.maximum_exposure

    @staticmethod
    def risk_passed(risk: object) -> bool:
        """Yalnız açık ALLOW; diğer her karar RED sayılır."""
        if not isinstance(risk, RiskDecision):
            return False
        return risk.decision is RiskDecisionType.ALLOW

    @staticmethod
    def kill_switch_enabled(kill_switch: object) -> bool:
        """ENABLED dışındaki her kill switch durumu RED."""
        if not isinstance(kill_switch, KillSwitchSnapshot):
            return False
        return kill_switch.state is KillSwitchState.ENABLED

    @staticmethod
    def approval_active(approval: Optional[MicroLiveApproval],
                        logical_sequence: int) -> bool:
        """Onay var VE süresi dolmamış — kalıcı onay yoktur."""
        if not isinstance(approval, MicroLiveApproval):
            return False
        return approval.expiry_sequence > logical_sequence

    @staticmethod
    def request_active(request: MicroLiveRequest,
                       logical_sequence: int) -> bool:
        """İstek son kullanma sırası geçilmemiş olmalı."""
        return request.expiry_sequence > logical_sequence

    @staticmethod
    def evaluable_state(record: MicroLiveAuthorization) -> bool:
        """Yalnız APPROVED değerlendirilebilir; REVOKED /
        DENIED / EXPIRED / PENDING yetki VERMEZ."""
        return record.state is _STATE.APPROVED
