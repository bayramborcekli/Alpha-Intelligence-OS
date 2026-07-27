"""Mission 2100 — Agent 04: Kağıt Yürütme Servisi.

Dondurulmuş Yürütme Çekirdeği ile deterministik Kağıt Broker ve
Defter'i bütünleştirir. Kalıcı boru hattı sırası:

istek doğrulama → mod doğrulama → PAPER politika doğrulaması →
risk değerlendirme → izin kapısı → kill switch → kağıt eşleme →
PaperBroker submit/cancel → defter geçişi → kanonik sonuç →
değişmez denetim sonucu.

Yalnız PAPER modu kabul edilir; SHADOW / MICRO_LIVE / bilinmeyen /
eksik mod REDDEDİLİR. Mod dönüştürme, geri düşme veya örtük
yükseltme YOKTUR. Reddedilen yollar PaperBroker'ı SIFIR kez,
onaylı yollar TAM BİR kez çağırır. Servis durumsuzdur: mevcut
değişmez durumu alır, bir SONRAKİ değişmez durumu döner.

Güvenlik: gerçek broker yok, ağ yok, iş parçacığı yok, kimlik /
zaman / rastgelelik üretimi yok; ham iç istisna sınırı geçemez.
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
from paper_execution_errors import (
    PaperExecutionConfigurationError,
    PaperExecutionContractError, PaperExecutionRiskError,
    PaperExecutionStateError)
from paper_execution_mapper import PaperExecutionMapper
from paper_execution_models import (PaperAuditStage,
                                    PaperExecutionDecision,
                                    PaperExecutionDecisionCode,
                                    PaperExecutionOperation,
                                    PaperExecutionReferences,
                                    PaperExecutionServiceResult)
from paper_models import (PaperLedgerSnapshot, PaperStatistics)
from runtime_enums import AuditSeverity, HeartbeatStatus
from runtime_models import (RuntimeAccountSnapshot,
                            RuntimeAuditRecord)

__all__ = ["PaperExecutionService", "PaperRiskEvaluator",
           "StaticRiskEvaluator"]

_ERROR_INVALID_FIELD = "INVALID_PAPER_EXECUTION_FIELD"

# Foundation'ın politika/mod kaynaklı red kodları
_POLICY_DENIAL_CODES = frozenset({
    ControlledExecutionDecisionCode.INVALID_POLICY,
    ControlledExecutionDecisionCode.INVALID_MODE})


def _fail(fieldname: str) -> None:
    raise PaperExecutionContractError(
        f"{_ERROR_INVALID_FIELD}:{fieldname}")


class PaperRiskEvaluator:
    """Risk değerlendirici arayüzü — deterministik, yan etkisiz."""

    def evaluate(self, request: ExecutionRequest
                 ) -> RiskDecision:
        """Alt sınıf uygular; arayüz doğrudan kullanılamaz."""
        raise NotImplementedError(
            "PAPER_RISK_EVALUATOR_ABSTRACT")


@dataclass(frozen=True, slots=True)
class StaticRiskEvaluator(PaperRiskEvaluator):
    """Sabit karar döndüren deterministik değerlendirici."""

    decision: RiskDecision

    def __post_init__(self) -> None:
        if not isinstance(self.decision, RiskDecision):
            raise PaperExecutionConfigurationError(
                "PAPER_EXECUTION_CONFIGURATION:"
                "INVALID_RISK_DECISION")

    def evaluate(self, request: ExecutionRequest
                 ) -> RiskDecision:
        if not isinstance(request, ExecutionRequest):
            _fail("request")
        return self.decision


@dataclass(frozen=True, slots=True)
class PaperExecutionService:
    """Durumsuz kağıt yürütme servisi — fail-closed.

    Durum sahipliği PaperBroker/PaperLedgerSnapshot'tadır; servis
    tekil değildir, önbellek ve modül-düzeyi durum taşımaz.
    """

    broker: PaperBroker
    foundation: ControlledExecutionFoundation
    risk_evaluator: PaperRiskEvaluator
    mapper: PaperExecutionMapper = field(
        default_factory=PaperExecutionMapper)

    def __post_init__(self) -> None:
        if not isinstance(self.broker, PaperBroker):
            raise PaperExecutionConfigurationError(
                "PAPER_EXECUTION_CONFIGURATION:INVALID_BROKER")
        if not isinstance(self.foundation,
                          ControlledExecutionFoundation):
            raise PaperExecutionConfigurationError(
                "PAPER_EXECUTION_CONFIGURATION:"
                "INVALID_FOUNDATION")
        if not isinstance(self.risk_evaluator,
                          PaperRiskEvaluator):
            raise PaperExecutionConfigurationError(
                "PAPER_EXECUTION_CONFIGURATION:"
                "INVALID_RISK_EVALUATOR")
        if not isinstance(self.mapper, PaperExecutionMapper):
            raise PaperExecutionConfigurationError(
                "PAPER_EXECUTION_CONFIGURATION:INVALID_MAPPER")

    # ── Yazan işlemler ───────────────────────────────────────────

    def submit_order(
            self, snapshot: PaperLedgerSnapshot,
            request: ExecutionRequest, order_reference: str,
            policy: object, kill_switch: object,
            references: PaperExecutionReferences
            ) -> PaperExecutionServiceResult:
        """Kalıcı boru hattı ile emir gönderimi.

        Reddedilen her yol PaperBroker'ı SIFIR kez çağırır ve
        defteri DEĞİŞTİRMEZ; onaylı yol tam BİR submit çağrısı
        yapar (yeniden deneme/yeniden gönderim yoktur)."""
        self._require_common(snapshot, order_reference,
                             references)
        if not isinstance(request, ExecutionRequest):
            _fail("request")
        self.mapper.order_input_for(request)
        records = (self._record(references, PaperAuditStage
                                .REQUEST_VALIDATED,
                                order_reference),)
        operation = PaperExecutionOperation.SUBMIT_ORDER
        records = records + (self._record(
            references, PaperAuditStage.MODE_VALIDATED,
            order_reference),)
        if not self._paper_mode(policy):
            return self._denied(
                operation, PaperExecutionDecisionCode
                .MODE_DENIED, snapshot, order_reference,
                references, records)
        if not self._paper_policy_valid(policy):
            return self._denied(
                operation, PaperExecutionDecisionCode
                .POLICY_DENIED, snapshot, order_reference,
                references, records)
        risk = self._evaluate_risk(request)
        records = records + (self._record(
            references, PaperAuditStage.RISK_EVALUATED,
            order_reference),)
        if risk.decision is RiskDecisionType.REJECT:
            return self._denied(
                operation, PaperExecutionDecisionCode
                .RISK_REJECTED, snapshot, order_reference,
                references, records)
        if risk.decision is RiskDecisionType.REDUCE_SIZE:
            return self._recommendation(
                operation, snapshot, order_reference,
                references, records, risk.approved_quantity)
        if risk.decision is (RiskDecisionType
                             .REQUIRE_CONFIRMATION):
            return self._denied(
                operation, PaperExecutionDecisionCode
                .RISK_CONFIRMATION_REQUIRED, snapshot,
                order_reference, references, records)
        gate = self.foundation.evaluate_policy(policy)
        records = records + (self._record(
            references, PaperAuditStage.PERMISSION_EVALUATED,
            order_reference),)
        if not gate.allowed:
            code = PaperExecutionDecisionCode.PERMISSION_DENIED
            if gate.code in _POLICY_DENIAL_CODES:
                code = PaperExecutionDecisionCode.POLICY_DENIED
            return self._denied(operation, code, snapshot,
                                order_reference, references,
                                records)
        records = records + (self._record(
            references, PaperAuditStage.KILL_SWITCH_CHECKED,
            order_reference),)
        if not self._kill_switch_enabled(kill_switch):
            return self._denied(
                operation, PaperExecutionDecisionCode
                .KILL_SWITCH_DENIED, snapshot, order_reference,
                references, records)
        symbol, side, quantity, price = \
            self.mapper.order_input_for(request)
        next_snapshot = self._invoke_submit(
            snapshot, order_reference, symbol, side, quantity,
            price)
        records = records + (self._record(
            references, PaperAuditStage.PAPER_BROKER_INVOKED,
            order_reference),)
        records = records + (self._record(
            references, PaperAuditStage.LEDGER_UPDATED,
            order_reference),)
        order = next_snapshot.order_for(order_reference)
        executions = tuple(
            execution for execution in next_snapshot.executions
            if execution.order_reference == order_reference)
        execution_result = self.mapper.execution_result_for(
            request, order, executions)
        records = records + (self._record(
            references, PaperAuditStage.RESULT_MAPPED,
            order_reference),)
        return PaperExecutionServiceResult(
            operation=operation,
            decision=PaperExecutionDecision.EXECUTED,
            decision_code=(PaperExecutionDecisionCode
                           .ORDER_EXECUTED),
            previous_ledger_reference=(
                references.previous_ledger_reference),
            current_ledger_reference=(
                references.current_ledger_reference),
            ledger=next_snapshot,
            order_reference=order_reference,
            execution_result=execution_result,
            execution_result_reference=(
                references.execution_result_reference),
            execution_references=tuple(
                execution.execution_reference
                for execution in executions),
            risk_decision_reference=(
                references.risk_decision_reference),
            kill_switch_reference=(
                references.kill_switch_reference),
            audit_records=records,
            logical_sequence=references.logical_sequence)

    def cancel_order(
            self, snapshot: PaperLedgerSnapshot,
            order_reference: str, policy: object,
            kill_switch: object,
            references: PaperExecutionReferences
            ) -> PaperExecutionServiceResult:
        """İptal boru hattı — onaylı yol tam BİR cancel çağrısı.

        Risk aşaması iptalde BİLİNÇLİ olarak yoktur: iptal yeni
        pozisyon/nakit riski üretmez ve kanonik ExecutionRequest
        taşımaz; istek uydurmak yasaktır. Bu muafiyet sözleşmeye
        testle sabitlenmiştir. IMMEDIATE_FULL_FILL altında açık
        emir kalmadığından broker iptali deterministik reddeder;
        red steril durum hatası olarak KAPSANIR (ham istisna
        sızmaz)."""
        self._require_common(snapshot, order_reference,
                             references)
        records = (self._record(references, PaperAuditStage
                                .REQUEST_VALIDATED,
                                order_reference),)
        operation = PaperExecutionOperation.CANCEL_ORDER
        records = records + (self._record(
            references, PaperAuditStage.MODE_VALIDATED,
            order_reference),)
        if not self._paper_mode(policy):
            return self._denied(
                operation, PaperExecutionDecisionCode
                .MODE_DENIED, snapshot, order_reference,
                references, records)
        if not self._paper_policy_valid(policy):
            return self._denied(
                operation, PaperExecutionDecisionCode
                .POLICY_DENIED, snapshot, order_reference,
                references, records)
        gate = self.foundation.evaluate_policy(policy)
        records = records + (self._record(
            references, PaperAuditStage.PERMISSION_EVALUATED,
            order_reference),)
        if not gate.allowed:
            code = PaperExecutionDecisionCode.PERMISSION_DENIED
            if gate.code in _POLICY_DENIAL_CODES:
                code = PaperExecutionDecisionCode.POLICY_DENIED
            return self._denied(operation, code, snapshot,
                                order_reference, references,
                                records)
        records = records + (self._record(
            references, PaperAuditStage.KILL_SWITCH_CHECKED,
            order_reference),)
        if not self._kill_switch_enabled(kill_switch):
            return self._denied(
                operation, PaperExecutionDecisionCode
                .KILL_SWITCH_DENIED, snapshot, order_reference,
                references, records)
        next_snapshot = self._invoke_cancel(snapshot,
                                            order_reference)
        records = records + (self._record(
            references, PaperAuditStage.PAPER_BROKER_INVOKED,
            order_reference),)
        records = records + (self._record(
            references, PaperAuditStage.LEDGER_UPDATED,
            order_reference),)
        records = records + (self._record(
            references, PaperAuditStage.RESULT_MAPPED,
            order_reference),)
        return PaperExecutionServiceResult(
            operation=operation,
            decision=PaperExecutionDecision.EXECUTED,
            decision_code=(PaperExecutionDecisionCode
                           .ORDER_CANCELLED),
            previous_ledger_reference=(
                references.previous_ledger_reference),
            current_ledger_reference=(
                references.current_ledger_reference),
            ledger=next_snapshot,
            order_reference=order_reference,
            kill_switch_reference=(
                references.kill_switch_reference),
            audit_records=records,
            logical_sequence=references.logical_sequence)

    # ── Yan etkisiz okuma işlemleri ──────────────────────────────

    def get_account_snapshot(
            self, snapshot: PaperLedgerSnapshot,
            account_reference: str) -> RuntimeAccountSnapshot:
        """RuntimeAccountSnapshot uyumlu deterministik görünüm."""
        self._require_snapshot(snapshot)
        return self.mapper.account_snapshot_for(
            snapshot, account_reference)

    def get_orders(self, snapshot: PaperLedgerSnapshot) -> tuple:
        """Emir geçmişi (değişmez)."""
        self._require_snapshot(snapshot)
        return self.broker.orders(snapshot)

    def get_executions(self, snapshot: PaperLedgerSnapshot
                       ) -> tuple:
        """Gerçekleşme geçmişi (değişmez)."""
        self._require_snapshot(snapshot)
        return self.broker.executions(snapshot)

    def get_positions(self, snapshot: PaperLedgerSnapshot
                      ) -> tuple:
        """Açık pozisyonlar (değişmez)."""
        self._require_snapshot(snapshot)
        return self.broker.positions(snapshot)

    def get_statistics(self, snapshot: PaperLedgerSnapshot
                       ) -> PaperStatistics:
        """Anlık görüntüden türetilmiş istatistikler."""
        self._require_snapshot(snapshot)
        return snapshot.statistics()

    def heartbeat(self, snapshot: PaperLedgerSnapshot
                  ) -> HeartbeatStatus:
        """Defter denetimine dayalı kalp atışı durumu."""
        self._require_snapshot(snapshot)
        return self.broker.heartbeat(snapshot)

    # ── İç yardımcılar (deterministik, yan etkisiz) ──────────────

    def _require_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, PaperLedgerSnapshot):
            _fail("snapshot")

    def _require_common(
            self, snapshot: object, order_reference: object,
            references: object) -> None:
        self._require_snapshot(snapshot)
        if not isinstance(order_reference, str) or \
                not order_reference.strip():
            _fail("order_reference")
        if not isinstance(references, PaperExecutionReferences):
            _fail("references")

    @staticmethod
    def _paper_mode(policy: object) -> bool:
        """Yalnız PAPER; dönüştürme/geri düşme/yükseltme YOK."""
        if not isinstance(policy, ControlledExecutionPolicy):
            return False
        return policy.mode is ControlledExecutionMode.PAPER

    @staticmethod
    def _paper_policy_valid(policy: ControlledExecutionPolicy
                            ) -> bool:
        """PAPER politika doğrulaması (risk'ten ÖNCE çalışır):
        borsa yazma talebi ve simüle dolum yasağı REDDEDİLİR."""
        if policy.exchange_write_allowed:
            return False
        return policy.simulated_fill_allowed

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
        except PaperExecutionContractError:
            raise
        except Exception as error:
            raise PaperExecutionRiskError(
                "PAPER_EXECUTION_RISK:EVALUATOR_FAILURE"
                ) from error
        if not isinstance(decision, RiskDecision):
            raise PaperExecutionRiskError(
                "PAPER_EXECUTION_RISK:INVALID_RISK_DECISION")
        return decision

    def _invoke_submit(self, snapshot, order_reference, symbol,
                       side, quantity, price
                       ) -> PaperLedgerSnapshot:
        try:
            return self.broker.submit(
                snapshot, order_reference, symbol, side,
                quantity, price)
        except PaperDomainError as error:
            raise PaperExecutionStateError(
                f"PAPER_EXECUTION_STATE:{error}") from error
        except Exception as error:
            raise PaperExecutionStateError(
                "PAPER_EXECUTION_STATE:INTERNAL_FAILURE"
                ) from error

    def _invoke_cancel(self, snapshot, order_reference
                       ) -> PaperLedgerSnapshot:
        try:
            return self.broker.cancel(snapshot, order_reference)
        except PaperDomainError as error:
            raise PaperExecutionStateError(
                f"PAPER_EXECUTION_STATE:{error}") from error
        except Exception as error:
            raise PaperExecutionStateError(
                "PAPER_EXECUTION_STATE:INTERNAL_FAILURE"
                ) from error

    @staticmethod
    def _record(references: PaperExecutionReferences,
                stage: PaperAuditStage,
                order_reference: str) -> RuntimeAuditRecord:
        """Deterministik denetim kaydı — kimlik türetilir,
        ÜRETİLMEZ (çağıran-sahipli isteğe bağlıdır)."""
        return RuntimeAuditRecord(
            audit_reference=(f"{references.request_reference}:"
                             f"{stage.value}"),
            severity=AuditSeverity.INFO,
            event_code=stage.value,
            subject_reference=order_reference,
            logical_sequence=references.logical_sequence)

    def _denied(self, operation, code, snapshot,
                order_reference, references, records
                ) -> PaperExecutionServiceResult:
        """Reddedilen yol: broker çağrısı SIFIR, defter aynı."""
        return PaperExecutionServiceResult(
            operation=operation,
            decision=PaperExecutionDecision.DENIED,
            decision_code=code,
            previous_ledger_reference=(
                references.previous_ledger_reference),
            current_ledger_reference=(
                references.previous_ledger_reference),
            ledger=snapshot,
            order_reference=order_reference,
            risk_decision_reference=(
                references.risk_decision_reference),
            kill_switch_reference=(
                references.kill_switch_reference),
            audit_records=records,
            logical_sequence=references.logical_sequence)

    def _recommendation(self, operation, snapshot,
                        order_reference, references, records,
                        approved_quantity
                        ) -> PaperExecutionServiceResult:
        """REDUCE_SIZE: yalnız öneri — otomatik boyutlandırma
        ve broker çağrısı YOK."""
        return PaperExecutionServiceResult(
            operation=operation,
            decision=(PaperExecutionDecision
                      .RECOMMENDATION_ONLY),
            decision_code=(PaperExecutionDecisionCode
                           .RISK_REDUCE_SIZE),
            previous_ledger_reference=(
                references.previous_ledger_reference),
            current_ledger_reference=(
                references.previous_ledger_reference),
            ledger=snapshot,
            order_reference=order_reference,
            risk_decision_reference=(
                references.risk_decision_reference),
            kill_switch_reference=(
                references.kill_switch_reference),
            recommended_quantity=approved_quantity,
            audit_records=records,
            logical_sequence=references.logical_sequence)
