"""Mission 2100 — Agent 05: Gölge Modu Servisi.

Gerçek piyasayı YALNIZ gözlemleyerek AI kararı → kağıt yürütme →
canlı piyasa sonucu → performans analizi zincirini deterministik
olarak işletir. Bu servis HİÇBİR ZAMAN: canlı emir göndermez,
borsaya yazmaz, broker durumunu değiştirmez, Mission 2000'i,
Risk Motoru'nu veya Kill Switch'i atlamaz.

Sabit boru hattı sırası:
ExecutionRequest → çalışma zamanı doğrulaması → SHADOW doğrulaması
→ Risk Motoru → İzin Kapısı → Kill Switch → kağıt simülasyonu →
piyasa gözlemi → karşılaştırma → değişmez rapor.

Piyasa gözlemi çağıran-sahipli ShadowMarketObservation olarak
gelir (salt-okunur adaptörden); bu modül ağa ÇIKMAZ. Reddedilen
yollar kağıt broker'ı SIFIR kez çağırır; onaylı gönderim tam BİR
simülasyon çağrısı yapar. Servis durumsuzdur: mevcut değişmez
durumları alır, SONRAKİ değişmez durumları döner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from controlled_execution_foundation import (
    ControlledExecutionFoundation)
from controlled_execution_models import (
    ControlledExecutionDecisionCode, ControlledExecutionMode,
    ControlledExecutionPolicy)
from execution_kill_switch_models import (KillSwitchSnapshot,
                                          KillSwitchState)
from execution_models import ExecutionRequest
from execution_risk_models import RiskDecision, RiskDecisionType
from paper_broker import PaperBroker
from paper_errors import PaperDomainError
from paper_execution_mapper import PaperExecutionMapper
from paper_execution_models import PaperExecutionReferences
from paper_execution_service import PaperRiskEvaluator
from paper_models import PaperLedgerSnapshot
from shadow_comparator import ShadowComparator
from shadow_errors import (ShadowConfigurationError,
                           ShadowContractError, ShadowRiskError,
                           ShadowStateError)
from shadow_models import (ShadowAudit, ShadowDecision,
                           ShadowDecisionCode, ShadowExecution,
                           ShadowHeartbeat,
                           ShadowMarketObservation,
                           ShadowOperation, ShadowOrder,
                           ShadowResult, ShadowSnapshot,
                           ShadowStage, ShadowStatistics)

__all__ = ["ShadowModeService"]

_ERROR_INVALID_FIELD = "INVALID_SHADOW_FIELD"

# Foundation'ın politika/mod kaynaklı red kodları
_POLICY_DENIAL_CODES = frozenset({
    ControlledExecutionDecisionCode.INVALID_POLICY,
    ControlledExecutionDecisionCode.INVALID_MODE})


def _fail(fieldname: str) -> None:
    raise ShadowContractError(
        f"{_ERROR_INVALID_FIELD}:{fieldname}")


@dataclass(frozen=True, slots=True)
class ShadowModeService:
    """Durumsuz gölge modu servisi — fail-closed, salt-gözlem.

    Kağıt simülasyonu Agent 03 PaperBroker ile yapılır (yeni
    değişmez defter döner; mevcut durum DEĞİŞTİRİLMEZ). Gerçek
    borsa erişimi, ağ, iş parçacığı, kimlik/zaman/rastgelelik
    üretimi YOKTUR."""

    broker: PaperBroker
    foundation: ControlledExecutionFoundation
    risk_evaluator: PaperRiskEvaluator
    comparator: ShadowComparator = field(
        default_factory=ShadowComparator)
    mapper: PaperExecutionMapper = field(
        default_factory=PaperExecutionMapper)

    def __post_init__(self) -> None:
        if not isinstance(self.broker, PaperBroker):
            raise ShadowConfigurationError(
                "SHADOW_CONFIGURATION:INVALID_BROKER")
        if not isinstance(self.foundation,
                          ControlledExecutionFoundation):
            raise ShadowConfigurationError(
                "SHADOW_CONFIGURATION:INVALID_FOUNDATION")
        if not isinstance(self.risk_evaluator,
                          PaperRiskEvaluator):
            raise ShadowConfigurationError(
                "SHADOW_CONFIGURATION:INVALID_RISK_EVALUATOR")
        if not isinstance(self.comparator, ShadowComparator):
            raise ShadowConfigurationError(
                "SHADOW_CONFIGURATION:INVALID_COMPARATOR")
        if not isinstance(self.mapper, PaperExecutionMapper):
            raise ShadowConfigurationError(
                "SHADOW_CONFIGURATION:INVALID_MAPPER")

    # ── Gölge işlemleri ──────────────────────────────────────────

    def submit_shadow(
            self, ledger: PaperLedgerSnapshot,
            shadow: ShadowSnapshot, request: ExecutionRequest,
            order_reference: str, policy: object,
            kill_switch: object,
            observation: ShadowMarketObservation,
            references: PaperExecutionReferences
            ) -> ShadowResult:
        """Sabit boru hattı ile gölge gönderimi.

        Reddedilen her yol kağıt broker'ı SIFIR kez çağırır ve
        her iki durumu da DEĞİŞTİRMEZ; onaylı yol tam BİR
        simülasyon çağrısı yapar. Canlı emir YOKTUR."""
        self._require_common(ledger, shadow, order_reference,
                             references)
        if not isinstance(request, ExecutionRequest):
            _fail("request")
        if not isinstance(observation, ShadowMarketObservation):
            _fail("observation")
        if observation.symbol != request.symbol:
            _fail("observation")
        self._validated_order_input(request)
        operation = ShadowOperation.SUBMIT_SHADOW
        records = (self._record(references, ShadowStage
                                .REQUEST_VALIDATED,
                                order_reference),)
        records = records + (self._record(
            references, ShadowStage.MODE_VALIDATED,
            order_reference),)
        if not self._shadow_mode(policy):
            return self._denied(
                operation, ShadowDecisionCode.MODE_DENIED,
                shadow, ledger, order_reference, references,
                records)
        if not self._shadow_policy_valid(policy):
            return self._denied(
                operation, ShadowDecisionCode.POLICY_DENIED,
                shadow, ledger, order_reference, references,
                records)
        risk = self._evaluate_risk(request)
        records = records + (self._record(
            references, ShadowStage.RISK_EVALUATED,
            order_reference),)
        if risk.decision is RiskDecisionType.REJECT:
            return self._denied(
                operation, ShadowDecisionCode.RISK_REJECTED,
                shadow, ledger, order_reference, references,
                records)
        if risk.decision is RiskDecisionType.REDUCE_SIZE:
            return self._recommendation(
                operation, shadow, ledger, order_reference,
                references, records, risk.approved_quantity)
        if risk.decision is (RiskDecisionType
                             .REQUIRE_CONFIRMATION):
            return self._denied(
                operation, ShadowDecisionCode
                .RISK_CONFIRMATION_REQUIRED, shadow, ledger,
                order_reference, references, records)
        gate = self.foundation.evaluate_policy(policy)
        records = records + (self._record(
            references, ShadowStage.PERMISSION_EVALUATED,
            order_reference),)
        if not gate.allowed:
            code = ShadowDecisionCode.PERMISSION_DENIED
            if gate.code in _POLICY_DENIAL_CODES:
                code = ShadowDecisionCode.POLICY_DENIED
            return self._denied(operation, code, shadow, ledger,
                                order_reference, references,
                                records)
        records = records + (self._record(
            references, ShadowStage.KILL_SWITCH_CHECKED,
            order_reference),)
        if not self._kill_switch_enabled(kill_switch):
            return self._denied(
                operation, ShadowDecisionCode
                .KILL_SWITCH_DENIED, shadow, ledger,
                order_reference, references, records)
        symbol, side, quantity, price = \
            self._validated_order_input(request)
        next_ledger = self._invoke_simulation(
            ledger, order_reference, symbol, side, quantity,
            price)
        records = records + (self._record(
            references, ShadowStage.PAPER_SIMULATED,
            order_reference),)
        shadow_order = ShadowOrder(
            order_reference=order_reference, symbol=symbol,
            side=side, quantity=quantity, price=price,
            logical_sequence=references.logical_sequence)
        shadow_executions = self._shadow_executions_from(
            next_ledger, order_reference, references)
        records = records + (self._record(
            references, ShadowStage.MARKET_OBSERVED,
            order_reference),)
        records = records + (self._record(
            references, ShadowStage.COMPARISON_COMPLETED,
            order_reference),)
        first_execution = None
        if shadow_executions:
            first_execution = shadow_executions[0]
        comparison = self.comparator.compare(
            shadow_order, first_execution, observation,
            references.request_reference,
            observation.observation_reference,
            audit=records,
            logical_sequence=references.logical_sequence)
        next_shadow = ShadowSnapshot(
            snapshot_reference=(
                references.current_ledger_reference),
            orders=shadow.orders + (shadow_order,),
            executions=shadow.executions + shadow_executions,
            comparisons=shadow.comparisons + (comparison,),
            denied_count=shadow.denied_count,
            cancel_request_count=shadow.cancel_request_count,
            logical_sequence=references.logical_sequence)
        return ShadowResult(
            operation=operation,
            decision=ShadowDecision.SIMULATED,
            decision_code=ShadowDecisionCode.ORDER_SIMULATED,
            shadow=next_shadow,
            ledger=next_ledger,
            order_reference=order_reference,
            comparison=comparison,
            audit=records,
            logical_sequence=references.logical_sequence)

    def cancel_shadow(
            self, ledger: PaperLedgerSnapshot,
            shadow: ShadowSnapshot, order_reference: str,
            policy: object, kill_switch: object,
            references: PaperExecutionReferences
            ) -> ShadowResult:
        """Gölge iptali — simülasyon iptal talebi.

        Risk aşaması iptalde BİLİNÇLİ yoktur (miktar/nosyonel
        taşımaz; istek uydurmak yasaktır — Agent 04 ile aynı
        sözleşme). IMMEDIATE_FULL_FILL altında açık emir
        kalmadığından kağıt broker iptali deterministik reddeder;
        red steril SHADOW_STATE hatasına sarılır."""
        self._require_common(ledger, shadow, order_reference,
                             references)
        operation = ShadowOperation.CANCEL_SHADOW
        records = (self._record(references, ShadowStage
                                .REQUEST_VALIDATED,
                                order_reference),)
        records = records + (self._record(
            references, ShadowStage.MODE_VALIDATED,
            order_reference),)
        if not self._shadow_mode(policy):
            return self._denied(
                operation, ShadowDecisionCode.MODE_DENIED,
                shadow, ledger, order_reference, references,
                records)
        if not self._shadow_policy_valid(policy):
            return self._denied(
                operation, ShadowDecisionCode.POLICY_DENIED,
                shadow, ledger, order_reference, references,
                records)
        gate = self.foundation.evaluate_policy(policy)
        records = records + (self._record(
            references, ShadowStage.PERMISSION_EVALUATED,
            order_reference),)
        if not gate.allowed:
            code = ShadowDecisionCode.PERMISSION_DENIED
            if gate.code in _POLICY_DENIAL_CODES:
                code = ShadowDecisionCode.POLICY_DENIED
            return self._denied(operation, code, shadow, ledger,
                                order_reference, references,
                                records)
        records = records + (self._record(
            references, ShadowStage.KILL_SWITCH_CHECKED,
            order_reference),)
        if not self._kill_switch_enabled(kill_switch):
            return self._denied(
                operation, ShadowDecisionCode
                .KILL_SWITCH_DENIED, shadow, ledger,
                order_reference, references, records)
        next_ledger = self._invoke_cancel_simulation(
            ledger, order_reference)
        records = records + (self._record(
            references, ShadowStage.PAPER_SIMULATED,
            order_reference),)
        next_shadow = ShadowSnapshot(
            snapshot_reference=(
                references.current_ledger_reference),
            orders=shadow.orders,
            executions=shadow.executions,
            comparisons=shadow.comparisons,
            denied_count=shadow.denied_count,
            cancel_request_count=(
                shadow.cancel_request_count + 1),
            logical_sequence=references.logical_sequence)
        return ShadowResult(
            operation=operation,
            decision=ShadowDecision.SIMULATED,
            decision_code=ShadowDecisionCode.CANCEL_SIMULATED,
            shadow=next_shadow,
            ledger=next_ledger,
            order_reference=order_reference,
            audit=records,
            logical_sequence=references.logical_sequence)

    def compare_execution(
            self, shadow: ShadowSnapshot, order_reference: str,
            observation: ShadowMarketObservation,
            references: PaperExecutionReferences,
            ledger: PaperLedgerSnapshot) -> ShadowResult:
        """Kayıtlı gölge emrini yeni piyasa gözlemiyle
        karşılaştırır — yalnız gözlem, broker çağrısı SIFIR."""
        self._require_common(ledger, shadow, order_reference,
                             references)
        if not isinstance(observation, ShadowMarketObservation):
            _fail("observation")
        operation = ShadowOperation.COMPARE_EXECUTION
        records = (self._record(references, ShadowStage
                                .REQUEST_VALIDATED,
                                order_reference),)
        order = shadow.order_for(order_reference)
        if order is None:
            raise ShadowStateError("SHADOW_STATE:UNKNOWN_ORDER")
        executions = shadow.executions_for(order_reference)
        first_execution = None
        if executions:
            first_execution = executions[0]
        records = records + (self._record(
            references, ShadowStage.MARKET_OBSERVED,
            order_reference),)
        records = records + (self._record(
            references, ShadowStage.COMPARISON_COMPLETED,
            order_reference),)
        comparison = self.comparator.compare(
            order, first_execution, observation,
            references.request_reference,
            observation.observation_reference,
            audit=records,
            logical_sequence=references.logical_sequence)
        next_shadow = ShadowSnapshot(
            snapshot_reference=(
                references.current_ledger_reference),
            orders=shadow.orders,
            executions=shadow.executions,
            comparisons=shadow.comparisons + (comparison,),
            denied_count=shadow.denied_count,
            cancel_request_count=shadow.cancel_request_count,
            logical_sequence=references.logical_sequence)
        return ShadowResult(
            operation=operation,
            decision=ShadowDecision.SIMULATED,
            decision_code=(ShadowDecisionCode
                           .COMPARISON_COMPLETED),
            shadow=next_shadow,
            ledger=ledger,
            order_reference=order_reference,
            comparison=comparison,
            audit=records,
            logical_sequence=references.logical_sequence)

    # ── Yan etkisiz okuma işlemleri ──────────────────────────────

    def statistics(self, shadow: ShadowSnapshot
                   ) -> ShadowStatistics:
        """Anlık görüntüden türetilmiş sayaçlar."""
        self._require_shadow(shadow)
        return shadow.statistics()

    def heartbeat(self, shadow: ShadowSnapshot
                  ) -> ShadowHeartbeat:
        """Deterministik kalp atışı — iç tutarlılık denetimi."""
        self._require_shadow(shadow)
        return ShadowHeartbeat(
            alive=True,
            order_count=len(shadow.orders),
            execution_count=len(shadow.executions),
            comparison_count=len(shadow.comparisons),
            logical_sequence=shadow.logical_sequence)

    # ── İç yardımcılar (deterministik, yan etkisiz) ──────────────

    @staticmethod
    def _require_shadow(shadow: object) -> None:
        if not isinstance(shadow, ShadowSnapshot):
            _fail("shadow")

    def _require_common(self, ledger: object, shadow: object,
                        order_reference: object,
                        references: object) -> None:
        if not isinstance(ledger, PaperLedgerSnapshot):
            _fail("ledger")
        self._require_shadow(shadow)
        if not isinstance(order_reference, str) or \
                not order_reference.strip():
            _fail("order_reference")
        if not isinstance(references, PaperExecutionReferences):
            _fail("references")

    def _validated_order_input(self, request: ExecutionRequest
                               ) -> tuple:
        """Kanonik istek doğrulaması — üst katman sözleşme
        hatası steril gölge sözleşme hatasına sarılır."""
        try:
            return self.mapper.order_input_for(request)
        except ShadowContractError:
            raise
        except Exception as error:
            raise ShadowContractError(
                f"{_ERROR_INVALID_FIELD}:request") from error

    @staticmethod
    def _shadow_mode(policy: object) -> bool:
        """Yalnız SHADOW; dönüştürme/geri düşme/yükseltme YOK."""
        if not isinstance(policy, ControlledExecutionPolicy):
            return False
        return policy.mode is ControlledExecutionMode.SHADOW

    @staticmethod
    def _shadow_policy_valid(policy: ControlledExecutionPolicy
                             ) -> bool:
        """SHADOW politika doğrulaması (risk'ten ÖNCE): borsa
        yazma talebi DAİMA reddedilir — gölge salt-okunurdur."""
        return not policy.exchange_write_allowed

    @staticmethod
    def _kill_switch_enabled(kill_switch: object) -> bool:
        """ENABLED dışındaki her kill switch durumu RED."""
        if not isinstance(kill_switch, KillSwitchSnapshot):
            return False
        return kill_switch.state is KillSwitchState.ENABLED

    def _evaluate_risk(self, request: ExecutionRequest
                       ) -> RiskDecision:
        try:
            decision = self.risk_evaluator.evaluate(request)
        except ShadowContractError:
            raise
        except Exception as error:
            raise ShadowRiskError(
                "SHADOW_RISK:EVALUATOR_FAILURE") from error
        if not isinstance(decision, RiskDecision):
            raise ShadowRiskError(
                "SHADOW_RISK:INVALID_RISK_DECISION")
        return decision

    def _invoke_simulation(self, ledger, order_reference,
                           symbol, side, quantity, price
                           ) -> PaperLedgerSnapshot:
        try:
            return self.broker.submit(
                ledger, order_reference, symbol, side, quantity,
                price)
        except PaperDomainError as error:
            raise ShadowStateError(
                f"SHADOW_STATE:{error}") from error
        except Exception as error:
            raise ShadowStateError(
                "SHADOW_STATE:INTERNAL_FAILURE") from error

    def _invoke_cancel_simulation(self, ledger, order_reference
                                  ) -> PaperLedgerSnapshot:
        try:
            return self.broker.cancel(ledger, order_reference)
        except PaperDomainError as error:
            raise ShadowStateError(
                f"SHADOW_STATE:{error}") from error
        except Exception as error:
            raise ShadowStateError(
                "SHADOW_STATE:INTERNAL_FAILURE") from error

    @staticmethod
    def _shadow_executions_from(ledger: PaperLedgerSnapshot,
                                order_reference: str,
                                references:
                                PaperExecutionReferences
                                ) -> Tuple[ShadowExecution, ...]:
        """Kağıt gerçekleşmelerini gölge kayıtlarına türetir —
        kimlikler kağıt defterden AKTARILIR, üretilmez."""
        return tuple(
            ShadowExecution(
                execution_reference=(
                    execution.execution_reference),
                order_reference=execution.order_reference,
                symbol=execution.symbol,
                side=execution.side,
                quantity=execution.quantity,
                price=execution.price,
                logical_sequence=references.logical_sequence)
            for execution in ledger.executions
            if execution.order_reference == order_reference)

    @staticmethod
    def _record(references: PaperExecutionReferences,
                stage: ShadowStage,
                order_reference: str) -> ShadowAudit:
        """Deterministik denetim kaydı — kimlik türetilir,
        ÜRETİLMEZ."""
        return ShadowAudit(
            audit_reference=(f"{references.request_reference}:"
                             f"{stage.value}"),
            stage=stage,
            event_code=stage.value,
            subject_reference=order_reference,
            logical_sequence=references.logical_sequence)

    def _denied(self, operation, code, shadow, ledger,
                order_reference, references, records
                ) -> ShadowResult:
        """Reddedilen yol: broker çağrısı SIFIR, durumlar aynı;
        red sayacı SONRAKİ görüntüde artar."""
        next_shadow = ShadowSnapshot(
            snapshot_reference=shadow.snapshot_reference,
            orders=shadow.orders,
            executions=shadow.executions,
            comparisons=shadow.comparisons,
            denied_count=shadow.denied_count + 1,
            cancel_request_count=shadow.cancel_request_count,
            logical_sequence=references.logical_sequence)
        return ShadowResult(
            operation=operation,
            decision=ShadowDecision.DENIED,
            decision_code=code,
            shadow=next_shadow,
            ledger=ledger,
            order_reference=order_reference,
            audit=records,
            logical_sequence=references.logical_sequence)

    def _recommendation(self, operation, shadow, ledger,
                        order_reference, references, records,
                        approved_quantity) -> ShadowResult:
        """REDUCE_SIZE: yalnız öneri — otomatik boyutlandırma ve
        broker çağrısı YOK."""
        return ShadowResult(
            operation=operation,
            decision=ShadowDecision.RECOMMENDATION_ONLY,
            decision_code=ShadowDecisionCode.RISK_REDUCE_SIZE,
            shadow=shadow,
            ledger=ledger,
            order_reference=order_reference,
            recommended_quantity=approved_quantity,
            audit=records,
            logical_sequence=references.logical_sequence)
