"""Mission 2000 — Agent 07: Yürütme İzin Kapısı.

RiskDecision İLE KillSwitch durumunu birleştiren TEK otorite.
Yanıtladığı soru: "Bu yürütme BrokerAdapter'a ilerleyebilir mi?"

İzin YALNIZ şu birleşimde verilir:
    RiskDecision = ALLOW  VE  KillSwitch.is_execution_allowed() True

Diğer TÜM birleşimler broker gönderimini reddeder:
    ALLOW+DISABLED/LOCKED/MAINTENANCE → DENY
    REJECT/REDUCE_SIZE/REQUIRE_CONFIRMATION → DENY
    (REDUCE_SIZE için çağıran, açıkça onaylanmış YENİ bir istek ve
    YENİ bir idempotency anahtarı oluşturmalıdır — örtük yeniden
    boyutlandırma yoktur.)

Kapı ŞUNLARI YAPMAZ: RiskDecision'ı değiştirmez, emri yeniden
boyutlandırmaz, onay istemez, KillSwitch durumunu değiştirmez,
BrokerAdapter'ı çağırmaz. Salt-okur ve durumsuzdur.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from execution_kill_switch import KillSwitch
from execution_risk_models import RiskDecision, RiskDecisionType

__all__ = ["ExecutionPermission", "ExecutionPermissionGate"]

_ERROR_INPUT = "INVALID_PERMISSION_INPUT"

_CODE_RISK_REJECTED = "RISK_REJECTED"
_CODE_RISK_CONFIRMATION = "RISK_REQUIRES_CONFIRMATION"
_CODE_RISK_REDUCE = "RISK_SIZE_REDUCTION_REQUIRED"
_CODE_KILL_SWITCH = "KILL_SWITCH_DENIED"


@dataclass(frozen=True, slots=True)
class ExecutionPermission:
    """Değişmez izin kararı — kapının tek çıktısı."""

    permitted: bool
    risk_decision: RiskDecision
    kill_switch_allowed: Optional[bool] = None
    code: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.permitted, bool):
            raise ValueError(_ERROR_INPUT)
        if not isinstance(self.risk_decision, RiskDecision):
            raise ValueError(_ERROR_INPUT)
        if self.kill_switch_allowed is not None and not isinstance(
                self.kill_switch_allowed, bool):
            raise ValueError(_ERROR_INPUT)
        if self.code is not None and not (
                isinstance(self.code, str) and bool(self.code)):
            raise ValueError(_ERROR_INPUT)


class ExecutionPermissionGate:
    """Durumsuz, salt-okur izin otoritesi."""

    __slots__ = ()

    def evaluate(self, risk_decision: RiskDecision,
                 kill_switch: KillSwitch) -> ExecutionPermission:
        """Güncel KillSwitch iznini okur (her denemede taze) ve
        RiskDecision ile birleştirir. Yan etkisi yoktur."""
        if not isinstance(risk_decision, RiskDecision):
            raise ValueError(_ERROR_INPUT)
        if not isinstance(kill_switch, KillSwitch):
            raise ValueError(_ERROR_INPUT)

        decision = risk_decision.decision
        if decision is not RiskDecisionType.ALLOW:
            code = _CODE_RISK_REJECTED
            if decision is RiskDecisionType.REQUIRE_CONFIRMATION:
                code = _CODE_RISK_CONFIRMATION
            elif decision is RiskDecisionType.REDUCE_SIZE:
                code = _CODE_RISK_REDUCE
            return ExecutionPermission(
                permitted=False, risk_decision=risk_decision,
                kill_switch_allowed=None, code=code)

        allowed = kill_switch.is_execution_allowed()
        if allowed is not True:
            return ExecutionPermission(
                permitted=False, risk_decision=risk_decision,
                kill_switch_allowed=False, code=_CODE_KILL_SWITCH)

        return ExecutionPermission(
            permitted=True, risk_decision=risk_decision,
            kill_switch_allowed=True, code=None)
