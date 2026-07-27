"""Mission 2100 — Agent 01: Kontrollü Yürütme Temeli.

Dondurulmuş Yürütme Çekirdeği v1.0.0'ın ÜZERİNDE çalışan uzatma
katmanının temel bileşeni. Yalnız politika uygunluğu değerlendirir.

YAPMAZ: emir göndermez, emir simüle etmez, broker'a dokunmaz,
Execution Service/API çağırmaz, alım-satım riski hesaplamaz,
Kill Switch durumunu değiştirmez. Çekirdek modüllerini import
etmez; ters bağımlılık (çekirdek → Mission 2100) yasaktır.

Her belirsizlik KAPALI değerlendirilir (fail-closed): bilinmeyen
mod, eksik politika, eksik yetkilendirme, geçersiz yapılandırma
ve belirsiz durum → RED.
"""

from __future__ import annotations

from controlled_execution_errors import (
    ControlledExecutionConfigurationError,
    ControlledExecutionContractError)
from controlled_execution_models import (
    ControlledExecutionDecision, ControlledExecutionDecisionCode,
    ControlledExecutionMode, ControlledExecutionPolicy)
from controlled_execution_policy import (
    _ALLOWED_TRANSITIONS, _DEFAULT_MODE,
    _FUTURE_AUTHORIZED_TRANSITIONS, _MODE_SAFETY,
    ExtensionRegistry)

__all__ = ["ControlledExecutionFoundation"]

_CODE = ControlledExecutionDecisionCode

_REASON_UNKNOWN_MODE = "UNKNOWN_MODE"
_REASON_MISSING_POLICY = "MISSING_POLICY"
_REASON_POLICY_MODE_CONFLICT = "POLICY_MODE_CONFLICT"
_REASON_EXCHANGE_WRITE_DISABLED = "EXCHANGE_WRITE_DISABLED"
_REASON_MISSING_AUTHORIZATION = "MISSING_AUTHORIZATION"
_REASON_TRANSITION_NOT_ALLOWED = "TRANSITION_NOT_ALLOWED"
_REASON_REQUIRES_AUTHORIZATION_COMPONENT = (
    "REQUIRES_AUTHORIZATION_COMPONENT")


class ControlledExecutionFoundation:
    """Durumsuz politika değerlendiricisi — fail-closed."""

    __slots__ = ("_registry",)

    def __init__(self, registry: ExtensionRegistry) -> None:
        if not isinstance(registry, ExtensionRegistry):
            raise ControlledExecutionConfigurationError(
                "INVALID_EXTENSION_REGISTRY")
        self._registry = registry

    def default_mode(self) -> ControlledExecutionMode:
        """Varsayılan çalışma modu: PAPER (yazmayan)."""
        return _DEFAULT_MODE

    def extension_points(self) -> tuple:
        """Bildirilen genişleme noktaları (değişmez kopya)."""
        return self._registry.points

    def evaluate_policy(
            self, policy: object) -> ControlledExecutionDecision:
        """Politika uygunluğu — her belirsizlik RED.

        Karar sırası: eksik politika → geçersiz mod → mod güvenlik
        sözleşmesiyle çelişki → borsa yazma talebi (Agent 01'de
        DAİMA red) → eksik yetkilendirme → yazmayan mod izni.
        """
        if policy is None:
            return ControlledExecutionDecision(
                code=_CODE.INVALID_POLICY,
                reason=_REASON_MISSING_POLICY)
        if not isinstance(policy, ControlledExecutionPolicy):
            raise ControlledExecutionContractError(
                "INVALID_POLICY_TYPE")
        safety = _MODE_SAFETY.get(policy.mode)
        if safety is None:
            return ControlledExecutionDecision(
                code=_CODE.INVALID_MODE,
                reason=_REASON_UNKNOWN_MODE)
        if self._conflicts_with_safety(policy, safety):
            return ControlledExecutionDecision(
                code=_CODE.INVALID_POLICY, mode=policy.mode,
                reason=_REASON_POLICY_MODE_CONFLICT)
        if policy.exchange_write_allowed:
            # Agent 01 kapsamı: borsa yazması HER ZAMAN reddedilir
            return ControlledExecutionDecision(
                code=_CODE.DENY_EXCHANGE_WRITE, mode=policy.mode,
                reason=_REASON_EXCHANGE_WRITE_DISABLED)
        if safety.explicit_authorization_required and \
                policy.authorization_reference is None:
            return ControlledExecutionDecision(
                code=_CODE.REQUIRE_EXPLICIT_AUTHORIZATION,
                mode=policy.mode,
                reason=_REASON_MISSING_AUTHORIZATION)
        if safety.explicit_authorization_required:
            # Yetkilendirme bileşeni Agent 01'de YOK → fail-closed
            return ControlledExecutionDecision(
                code=_CODE.REQUIRE_EXPLICIT_AUTHORIZATION,
                mode=policy.mode,
                reason=_REASON_REQUIRES_AUTHORIZATION_COMPONENT)
        return ControlledExecutionDecision(
            code=_CODE.ALLOW_NON_WRITING_MODE, mode=policy.mode)

    def evaluate_transition(
            self, current: object,
            target: object) -> ControlledExecutionDecision:
        """Mod geçişi — matris dışı her geçiş RED."""
        if not isinstance(current, ControlledExecutionMode) or \
                not isinstance(target, ControlledExecutionMode):
            return ControlledExecutionDecision(
                code=_CODE.INVALID_MODE,
                reason=_REASON_UNKNOWN_MODE)
        pair = (current, target)
        if pair in _ALLOWED_TRANSITIONS:
            return ControlledExecutionDecision(
                code=_CODE.ALLOW_NON_WRITING_MODE, mode=target)
        if pair in _FUTURE_AUTHORIZED_TRANSITIONS:
            return ControlledExecutionDecision(
                code=_CODE.REQUIRE_EXPLICIT_AUTHORIZATION,
                mode=target,
                reason=_REASON_REQUIRES_AUTHORIZATION_COMPONENT)
        return ControlledExecutionDecision(
            code=_CODE.INVALID_TRANSITION, mode=current,
            reason=_REASON_TRANSITION_NOT_ALLOWED)

    @staticmethod
    def _conflicts_with_safety(policy, safety) -> bool:
        """Politika, modun kalıcı güvenlik sözleşmesini aşamaz."""
        if policy.simulated_fill_allowed and \
                not safety.simulated_fill_allowed:
            return True
        if policy.broker_read_allowed and \
                not safety.broker_read_allowed:
            return True
        if policy.exchange_write_allowed and \
                not safety.exchange_write_allowed:
            return True
        if safety.human_confirmation_required and \
                not policy.human_confirmation_required:
            return True
        if safety.explicit_authorization_required and \
                not policy.explicit_authorization_required:
            return True
        return False
