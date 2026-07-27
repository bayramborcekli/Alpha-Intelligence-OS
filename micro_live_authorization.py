"""Mission 2100 — Agent 06: Micro Live Yetkilendirme Servisi.

MICRO_LIVE yürütmesi için AÇIK yetkilendirme sınırı. Bu servis
GELECEKTEKİ bir micro-live isteğini yalnız yetkilendirir ya da
reddeder. HİÇBİR ZAMAN: emir vermez, borsa/broker'a bağlanmaz,
borsaya yazmaz, özel işlem uçlarına erişmez, bakiye/pozisyon
değiştirmez, mutabakat yapmaz, sınırsız canlı modu ETKİNLEŞTİRMEZ.

Onay şartları (spesifikasyon §7): Risk Motoru PASS + Kill Switch
ENABLED + İzin Kapısı PASS + ControlledExecutionMode=MICRO_LIVE +
açık onay + süresi dolmamış + iptal edilmemiş + limitler içinde.
Aksi her durumda DENY (fail-closed).

Servis durumsuzdur: mevcut değişmez durumu alır, SONRAKİ değişmez
durumu döner. Kimlik/zaman/rastgelelik ÜRETİLMEZ.

Güvenliği AZALTAN işlemler (deny / expire / revoke) mod, politika
ve kill switch kapılarına TABİ DEĞİLDİR: yetkiyi geri çekmek her
koşulda mümkündür (bilinçli fail-safe kuralı; yalnız geçiş
matrisi ve zaman önkoşulları uygulanır).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from controlled_execution_foundation import (
    ControlledExecutionFoundation)
from controlled_execution_models import (
    ControlledExecutionDecisionCode, ControlledExecutionMode)
from micro_live_errors import (MicroLiveConfigurationError,
                               MicroLiveContractError,
                               MicroLiveStateError)
from micro_live_models import (MicroLiveApproval,
                               MicroLiveAudit,
                               MicroLiveAuthorization,
                               MicroLiveAuthorizationState,
                               MicroLiveDecision,
                               MicroLiveDecisionCode,
                               MicroLiveHeartbeat,
                               MicroLiveLimits,
                               MicroLiveOperation,
                               MicroLiveReferences,
                               MicroLiveRequest,
                               MicroLiveResult,
                               MicroLiveSnapshot,
                               MicroLiveStage,
                               MicroLiveStatistics)
from micro_live_policy import (MicroLiveAuthorizationPolicy,
                               MicroLiveTransitionPolicy)

__all__ = ["MicroLiveAuthorizationService"]

_ERROR_INVALID_FIELD = "INVALID_MICRO_LIVE_FIELD"

_STATE = MicroLiveAuthorizationState

# İzin kapısında kabul edilen kodlar: MICRO_LIVE için Agent 01
# kapısı açık yetkilendirme bileşeni TALEP eder — bu servis o
# bileşenin kendisidir; bu nedenle REQUIRE_EXPLICIT_AUTHORIZATION
# kapı GEÇİŞİ sayılır (borsa yazma talebi yine DAİMA reddedilir).
_GATE_PASS_CODES = frozenset({
    ControlledExecutionDecisionCode.ALLOW_NON_WRITING_MODE,
    ControlledExecutionDecisionCode
    .REQUIRE_EXPLICIT_AUTHORIZATION})


def _fail(fieldname: str) -> None:
    raise MicroLiveContractError(
        f"{_ERROR_INVALID_FIELD}:{fieldname}")


@dataclass(frozen=True, slots=True)
class MicroLiveAuthorizationService:
    """Durumsuz yetkilendirme servisi — fail-closed.

    Broker/borsa bağımlılığı YOKTUR; çerçeveden bağımsızdır.
    Tüm kararlar deterministiktir ve kapalı geçiş matrisine,
    kapalı karar koduna bağlıdır."""

    foundation: ControlledExecutionFoundation
    transition_policy: MicroLiveTransitionPolicy = field(
        default_factory=MicroLiveTransitionPolicy)
    policy_rules: MicroLiveAuthorizationPolicy = field(
        default_factory=MicroLiveAuthorizationPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.foundation,
                          ControlledExecutionFoundation):
            raise MicroLiveConfigurationError(
                "MICRO_LIVE_CONFIGURATION:INVALID_FOUNDATION")
        if not isinstance(self.transition_policy,
                          MicroLiveTransitionPolicy):
            raise MicroLiveConfigurationError(
                "MICRO_LIVE_CONFIGURATION:"
                "INVALID_TRANSITION_POLICY")
        if not isinstance(self.policy_rules,
                          MicroLiveAuthorizationPolicy):
            raise MicroLiveConfigurationError(
                "MICRO_LIVE_CONFIGURATION:INVALID_POLICY_RULES")

    # ── Yazan işlemler ───────────────────────────────────────────

    def request_authorization(
            self, snapshot: MicroLiveSnapshot,
            request: MicroLiveRequest, limits: MicroLiveLimits,
            policy: object, kill_switch: object,
            references: MicroLiveReferences) -> MicroLiveResult:
        """NONE → PENDING geçişi ile yetkilendirme isteği.

        Otomatik onay YOKTUR: sonuç daima PENDING kaydıdır.
        Kapsam dışı veya limit aşan istek kayıt EDİLMEDEN
        reddedilir."""
        self._require_snapshot(snapshot)
        self._require_references(references)
        if not isinstance(request, MicroLiveRequest):
            _fail("request")
        if not isinstance(limits, MicroLiveLimits):
            _fail("limits")
        operation = MicroLiveOperation.REQUEST_AUTHORIZATION
        reference = request.authorization_reference
        if snapshot.authorization_for(reference) is not None:
            raise MicroLiveStateError(
                "MICRO_LIVE_STATE:DUPLICATE_AUTHORIZATION")
        records = (self._record(
            references, MicroLiveStage.REQUEST_VALIDATED,
            reference),)
        records = records + (self._record(
            references, MicroLiveStage.MODE_VALIDATED,
            reference),)
        if request.execution_mode is not \
                ControlledExecutionMode.MICRO_LIVE:
            return self._denied(
                operation, MicroLiveDecisionCode.MODE_DENIED,
                snapshot, reference, references, records)
        if not self.policy_rules.mode_valid(policy):
            return self._denied(
                operation, MicroLiveDecisionCode.MODE_DENIED,
                snapshot, reference, references, records)
        if not self.policy_rules.policy_valid(policy) or \
                not self.policy_rules.policy_reference_match(
                    policy, reference):
            return self._denied(
                operation, MicroLiveDecisionCode.POLICY_DENIED,
                snapshot, reference, references, records)
        records = records + (self._record(
            references, MicroLiveStage.KILL_SWITCH_CHECKED,
            reference),)
        if not self.policy_rules.kill_switch_enabled(
                kill_switch):
            return self._denied(
                operation,
                MicroLiveDecisionCode.KILL_SWITCH_DENIED,
                snapshot, reference, references, records)
        records = records + (self._record(
            references, MicroLiveStage.TRANSITION_VALIDATED,
            reference),)
        if not self.transition_policy.transition_allowed(
                _STATE.NONE, _STATE.PENDING):
            return self._denied(
                operation,
                MicroLiveDecisionCode.TRANSITION_DENIED,
                snapshot, reference, references, records)
        records = records + (self._record(
            references, MicroLiveStage.LIMITS_VALIDATED,
            reference),)
        if not self.policy_rules.scope_valid(request,
                                             request.scope):
            return self._denied(
                operation, MicroLiveDecisionCode.SCOPE_DENIED,
                snapshot, reference, references, records)
        if not self.policy_rules.within_limits(request, limits):
            return self._denied(
                operation, MicroLiveDecisionCode.LIMIT_DENIED,
                snapshot, reference, references, records)
        record = MicroLiveAuthorization(
            authorization_reference=reference,
            request=request, limits=limits,
            state=_STATE.PENDING,
            logical_sequence=references.logical_sequence)
        records = records + (self._record(
            references, MicroLiveStage.AUTHORIZATION_RECORDED,
            reference),)
        next_snapshot = MicroLiveSnapshot(
            snapshot_reference=references.snapshot_reference,
            authorizations=(snapshot.authorizations
                            + (record,)),
            denied_count=snapshot.denied_count,
            logical_sequence=references.logical_sequence)
        return MicroLiveResult(
            operation=operation,
            decision=MicroLiveDecision.ACCEPTED,
            decision_code=(MicroLiveDecisionCode
                           .AUTHORIZATION_REQUESTED),
            snapshot=next_snapshot,
            authorization_reference=reference,
            audit=records,
            logical_sequence=references.logical_sequence)

    def approve(self, snapshot: MicroLiveSnapshot,
                authorization_reference: str,
                approval: MicroLiveApproval, risk: object,
                policy: object, kill_switch: object,
                references: MicroLiveReferences
                ) -> MicroLiveResult:
        """PENDING → APPROVED — yalnız AÇIK onayla.

        Onay şartları (tamamı zorunlu): Risk PASS, Kill Switch
        ENABLED, İzin Kapısı PASS, mod MICRO_LIVE, geçiş matrisi
        izinli, onay süresi ve istek süresi dolmamış, limitler
        içinde. Aksi her durum DENY."""
        record = self._require_known(snapshot,
                                     authorization_reference,
                                     references)
        if not isinstance(approval, MicroLiveApproval):
            _fail("approval")
        if approval.authorization_reference != \
                authorization_reference:
            _fail("approval")
        operation = MicroLiveOperation.APPROVE
        records = (self._record(
            references, MicroLiveStage.REQUEST_VALIDATED,
            authorization_reference),)
        records = records + (self._record(
            references, MicroLiveStage.TRANSITION_VALIDATED,
            authorization_reference),)
        if not self.transition_policy.transition_allowed(
                record.state, _STATE.APPROVED):
            return self._denied(
                operation,
                MicroLiveDecisionCode.TRANSITION_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.MODE_VALIDATED,
            authorization_reference),)
        if not self.policy_rules.mode_valid(policy):
            return self._denied(
                operation, MicroLiveDecisionCode.MODE_DENIED,
                snapshot, authorization_reference, references,
                records)
        if not self.policy_rules.policy_valid(policy) or \
                not self.policy_rules.policy_reference_match(
                    policy, authorization_reference):
            return self._denied(
                operation, MicroLiveDecisionCode.POLICY_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.RISK_EVALUATED,
            authorization_reference),)
        if not self.policy_rules.risk_passed(risk):
            return self._denied(
                operation, MicroLiveDecisionCode.RISK_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.PERMISSION_EVALUATED,
            authorization_reference),)
        if not self._gate_passed(policy):
            return self._denied(
                operation,
                MicroLiveDecisionCode.PERMISSION_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.KILL_SWITCH_CHECKED,
            authorization_reference),)
        if not self.policy_rules.kill_switch_enabled(
                kill_switch):
            return self._denied(
                operation,
                MicroLiveDecisionCode.KILL_SWITCH_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.LIMITS_VALIDATED,
            authorization_reference),)
        if not self.policy_rules.approval_active(
                approval, references.logical_sequence) or \
                not self.policy_rules.request_active(
                    record.request,
                    references.logical_sequence):
            return self._denied(
                operation, MicroLiveDecisionCode.POLICY_DENIED,
                snapshot, authorization_reference, references,
                records)
        if not self.policy_rules.within_limits(record.request,
                                               record.limits):
            return self._denied(
                operation, MicroLiveDecisionCode.LIMIT_DENIED,
                snapshot, authorization_reference, references,
                records)
        approved = MicroLiveAuthorization(
            authorization_reference=authorization_reference,
            request=record.request, limits=record.limits,
            state=_STATE.APPROVED, approval=approval,
            logical_sequence=references.logical_sequence)
        records = records + (self._record(
            references, MicroLiveStage.AUTHORIZATION_RECORDED,
            authorization_reference),)
        return self._transitioned(
            operation,
            MicroLiveDecisionCode.AUTHORIZATION_APPROVED,
            snapshot, record, approved, references, records)

    def deny(self, snapshot: MicroLiveSnapshot,
             authorization_reference: str,
             references: MicroLiveReferences
             ) -> MicroLiveResult:
        """PENDING → DENIED — fail-safe: kapılara tabi değildir.

        Yetkiyi GERİ ÇEKEN işlem her koşulda mümkündür; yalnız
        geçiş matrisi uygulanır."""
        record = self._require_known(snapshot,
                                     authorization_reference,
                                     references)
        operation = MicroLiveOperation.DENY
        records = self._safety_records(references,
                                       authorization_reference)
        if not self.transition_policy.transition_allowed(
                record.state, _STATE.DENIED):
            return self._denied(
                operation,
                MicroLiveDecisionCode.TRANSITION_DENIED,
                snapshot, authorization_reference, references,
                records)
        denied = MicroLiveAuthorization(
            authorization_reference=authorization_reference,
            request=record.request, limits=record.limits,
            state=_STATE.DENIED,
            logical_sequence=references.logical_sequence)
        records = records + (self._record(
            references, MicroLiveStage.AUTHORIZATION_RECORDED,
            authorization_reference),)
        return self._transitioned(
            operation,
            MicroLiveDecisionCode.AUTHORIZATION_DENIED,
            snapshot, record, denied, references, records)

    def expire(self, snapshot: MicroLiveSnapshot,
               authorization_reference: str,
               references: MicroLiveReferences
               ) -> MicroLiveResult:
        """PENDING/APPROVED → EXPIRED — zaman önkoşullu.

        Süre, mantıksal sıra tabanlıdır: ilgili son kullanma
        sırasına ULAŞILMADAN süre bitirme reddedilir (erken
        süre bitirme örtük iptal olurdu; iptal için revoke/deny
        vardır)."""
        record = self._require_known(snapshot,
                                     authorization_reference,
                                     references)
        operation = MicroLiveOperation.EXPIRE
        records = self._safety_records(references,
                                       authorization_reference)
        if not self.transition_policy.transition_allowed(
                record.state, _STATE.EXPIRED):
            return self._denied(
                operation,
                MicroLiveDecisionCode.TRANSITION_DENIED,
                snapshot, authorization_reference, references,
                records)
        if references.logical_sequence < \
                self._expiry_sequence_of(record):
            return self._denied(
                operation,
                MicroLiveDecisionCode.TRANSITION_DENIED,
                snapshot, authorization_reference, references,
                records)
        expired = MicroLiveAuthorization(
            authorization_reference=authorization_reference,
            request=record.request, limits=record.limits,
            state=_STATE.EXPIRED, approval=record.approval,
            logical_sequence=references.logical_sequence)
        records = records + (self._record(
            references, MicroLiveStage.AUTHORIZATION_RECORDED,
            authorization_reference),)
        return self._transitioned(
            operation,
            MicroLiveDecisionCode.AUTHORIZATION_EXPIRED,
            snapshot, record, expired, references, records)

    def revoke(self, snapshot: MicroLiveSnapshot,
               authorization_reference: str,
               references: MicroLiveReferences
               ) -> MicroLiveResult:
        """APPROVED → REVOKED — fail-safe: kapılara tabi değil.

        Onaylı yetkiyi geri çekmek her koşulda mümkündür."""
        record = self._require_known(snapshot,
                                     authorization_reference,
                                     references)
        operation = MicroLiveOperation.REVOKE
        records = self._safety_records(references,
                                       authorization_reference)
        if not self.transition_policy.transition_allowed(
                record.state, _STATE.REVOKED):
            return self._denied(
                operation,
                MicroLiveDecisionCode.TRANSITION_DENIED,
                snapshot, authorization_reference, references,
                records)
        revoked = MicroLiveAuthorization(
            authorization_reference=authorization_reference,
            request=record.request, limits=record.limits,
            state=_STATE.REVOKED, approval=record.approval,
            logical_sequence=references.logical_sequence)
        records = records + (self._record(
            references, MicroLiveStage.AUTHORIZATION_RECORDED,
            authorization_reference),)
        return self._transitioned(
            operation,
            MicroLiveDecisionCode.AUTHORIZATION_REVOKED,
            snapshot, record, revoked, references, records)

    # ── Salt-okunur işlemler ─────────────────────────────────────

    def evaluate(self, snapshot: MicroLiveSnapshot,
                 authorization_reference: str, risk: object,
                 policy: object, kill_switch: object,
                 references: MicroLiveReferences
                 ) -> MicroLiveResult:
        """Salt-okunur değerlendirme — durum DEĞİŞMEZ.

        AUTHORIZED yalnız: durum APPROVED + süresi dolmamış +
        Risk PASS + Kill Switch ENABLED + mod MICRO_LIVE +
        politika geçerli ve bu yetkiyi adresliyor + İzin Kapısı
        PASS + kapsam ve limitler içinde. Aksi NOT_AUTHORIZED.
        Bu sonuç bir yürütme EYLEMİ değildir."""
        record = self._require_known(snapshot,
                                     authorization_reference,
                                     references)
        operation = MicroLiveOperation.EVALUATE
        records = (self._record(
            references, MicroLiveStage.REQUEST_VALIDATED,
            authorization_reference),)
        if not self.policy_rules.evaluable_state(record):
            return self._not_authorized(
                operation,
                MicroLiveDecisionCode.NOT_AUTHORIZED_STATE,
                snapshot, authorization_reference, references,
                records)
        if not self.policy_rules.approval_active(
                record.approval,
                references.logical_sequence) or \
                not self.policy_rules.request_active(
                    record.request,
                    references.logical_sequence):
            return self._not_authorized(
                operation,
                MicroLiveDecisionCode.NOT_AUTHORIZED_EXPIRED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.RISK_EVALUATED,
            authorization_reference),)
        if not self.policy_rules.risk_passed(risk):
            return self._not_authorized(
                operation, MicroLiveDecisionCode.RISK_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.KILL_SWITCH_CHECKED,
            authorization_reference),)
        if not self.policy_rules.kill_switch_enabled(
                kill_switch):
            return self._not_authorized(
                operation,
                MicroLiveDecisionCode.KILL_SWITCH_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.MODE_VALIDATED,
            authorization_reference),)
        if not self.policy_rules.mode_valid(policy):
            return self._not_authorized(
                operation, MicroLiveDecisionCode.MODE_DENIED,
                snapshot, authorization_reference, references,
                records)
        if not self.policy_rules.policy_valid(policy) or \
                not self.policy_rules.policy_reference_match(
                    policy, authorization_reference):
            return self._not_authorized(
                operation, MicroLiveDecisionCode.POLICY_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.PERMISSION_EVALUATED,
            authorization_reference),)
        if not self._gate_passed(policy):
            return self._not_authorized(
                operation,
                MicroLiveDecisionCode.PERMISSION_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.LIMITS_VALIDATED,
            authorization_reference),)
        if not self.policy_rules.scope_valid(
                record.request, record.request.scope):
            return self._not_authorized(
                operation, MicroLiveDecisionCode.SCOPE_DENIED,
                snapshot, authorization_reference, references,
                records)
        if not self.policy_rules.within_limits(record.request,
                                               record.limits):
            return self._not_authorized(
                operation, MicroLiveDecisionCode.LIMIT_DENIED,
                snapshot, authorization_reference, references,
                records)
        records = records + (self._record(
            references, MicroLiveStage.EVALUATION_COMPLETED,
            authorization_reference),)
        return MicroLiveResult(
            operation=operation,
            decision=MicroLiveDecision.AUTHORIZED,
            decision_code=(MicroLiveDecisionCode
                           .EVALUATION_AUTHORIZED),
            snapshot=snapshot,
            authorization_reference=authorization_reference,
            audit=records,
            logical_sequence=references.logical_sequence)

    def statistics(self, snapshot: MicroLiveSnapshot
                   ) -> MicroLiveStatistics:
        """Anlık görüntüden türetilmiş sayaçlar."""
        self._require_snapshot(snapshot)
        return snapshot.statistics()

    def heartbeat(self, snapshot: MicroLiveSnapshot
                  ) -> MicroLiveHeartbeat:
        """Deterministik kalp atışı — iç tutarlılık denetimi."""
        self._require_snapshot(snapshot)
        return MicroLiveHeartbeat(
            alive=True,
            authorization_count=len(snapshot.authorizations),
            pending_count=snapshot.count_in_state(
                _STATE.PENDING),
            approved_count=snapshot.count_in_state(
                _STATE.APPROVED),
            logical_sequence=snapshot.logical_sequence)

    # ── İç yardımcılar (deterministik, yan etkisiz) ──────────────

    @staticmethod
    def _require_snapshot(snapshot: object) -> None:
        if not isinstance(snapshot, MicroLiveSnapshot):
            _fail("snapshot")

    @staticmethod
    def _require_references(references: object) -> None:
        if not isinstance(references, MicroLiveReferences):
            _fail("references")

    def _require_known(self, snapshot: object,
                       authorization_reference: object,
                       references: object
                       ) -> MicroLiveAuthorization:
        self._require_snapshot(snapshot)
        self._require_references(references)
        if not isinstance(authorization_reference, str) or \
                not authorization_reference.strip():
            _fail("authorization_reference")
        record = snapshot.authorization_for(
            authorization_reference)
        if record is None:
            raise MicroLiveStateError(
                "MICRO_LIVE_STATE:UNKNOWN_AUTHORIZATION")
        return record

    def _gate_passed(self, policy: object) -> bool:
        """İzin Kapısı — Agent 01 foundation değerlendirmesi.

        Ham iç istisnalar sınırı geçemez; belirsizlik REDDİR."""
        try:
            decision = self.foundation.evaluate_policy(policy)
        except Exception:
            return False
        if decision.code not in _GATE_PASS_CODES:
            return False
        return decision.mode is \
            ControlledExecutionMode.MICRO_LIVE

    @staticmethod
    def _expiry_sequence_of(record: MicroLiveAuthorization
                            ) -> int:
        """Duruma göre etkin son kullanma sırası."""
        if record.approval is not None:
            return record.approval.expiry_sequence
        return record.request.expiry_sequence

    def _safety_records(self, references: MicroLiveReferences,
                        authorization_reference: str
                        ) -> Tuple[MicroLiveAudit, ...]:
        """Fail-safe işlemlerin ortak denetim ön eki."""
        return (self._record(
            references, MicroLiveStage.REQUEST_VALIDATED,
            authorization_reference),
            self._record(
                references, MicroLiveStage.TRANSITION_VALIDATED,
                authorization_reference))

    @staticmethod
    def _record(references: MicroLiveReferences,
                stage: MicroLiveStage,
                authorization_reference: str) -> MicroLiveAudit:
        """Deterministik denetim kaydı — kimlik türetilir,
        ÜRETİLMEZ."""
        return MicroLiveAudit(
            audit_reference=(f"{references.request_reference}:"
                             f"{stage.value}"),
            stage=stage,
            event_code=stage.value,
            subject_reference=authorization_reference,
            logical_sequence=references.logical_sequence)

    @staticmethod
    def _replace(snapshot: MicroLiveSnapshot,
                 previous: MicroLiveAuthorization,
                 replacement: MicroLiveAuthorization
                 ) -> Tuple[MicroLiveAuthorization, ...]:
        """Kayıt değişimi — sıra korunur, kopya üretilmez."""
        return tuple(replacement
                     if record is previous else record
                     for record in snapshot.authorizations)

    def _transitioned(self, operation, code, snapshot, previous,
                      replacement, references, records
                      ) -> MicroLiveResult:
        """Kabul edilen geçiş: SONRAKİ değişmez görüntü döner."""
        next_snapshot = MicroLiveSnapshot(
            snapshot_reference=references.snapshot_reference,
            authorizations=self._replace(snapshot, previous,
                                         replacement),
            denied_count=snapshot.denied_count,
            logical_sequence=references.logical_sequence)
        return MicroLiveResult(
            operation=operation,
            decision=MicroLiveDecision.ACCEPTED,
            decision_code=code,
            snapshot=next_snapshot,
            authorization_reference=(
                replacement.authorization_reference),
            audit=records,
            logical_sequence=references.logical_sequence)

    @staticmethod
    def _denied(operation, code, snapshot,
                authorization_reference, references, records
                ) -> MicroLiveResult:
        """Reddedilen yazan yol: kayıt kümesi AYNI kalır; red
        sayacı SONRAKİ görüntüde artar."""
        next_snapshot = MicroLiveSnapshot(
            snapshot_reference=snapshot.snapshot_reference,
            authorizations=snapshot.authorizations,
            denied_count=snapshot.denied_count + 1,
            logical_sequence=references.logical_sequence)
        return MicroLiveResult(
            operation=operation,
            decision=MicroLiveDecision.DENIED,
            decision_code=code,
            snapshot=next_snapshot,
            authorization_reference=authorization_reference,
            audit=records,
            logical_sequence=references.logical_sequence)

    @staticmethod
    def _not_authorized(operation, code, snapshot,
                        authorization_reference, references,
                        records) -> MicroLiveResult:
        """Salt-okunur değerlendirme reddi: görüntü DEĞİŞMEZ."""
        return MicroLiveResult(
            operation=operation,
            decision=MicroLiveDecision.NOT_AUTHORIZED,
            decision_code=code,
            snapshot=snapshot,
            authorization_reference=authorization_reference,
            audit=records,
            logical_sequence=references.logical_sequence)
