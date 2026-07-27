"""Mission 2100 — Agent 06: Micro Live yetkilendirme servisi
testleri.

Bu servis emir vermez, borsa/broker'a bağlanmaz; yalnız
gelecekteki bir micro-live isteğini yetkilendirir/reddeder."""

from __future__ import annotations

import dataclasses
import os
import sys
from decimal import Decimal as D

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from controlled_execution_foundation import (
    ControlledExecutionFoundation)
from controlled_execution_models import (ControlledExecutionMode,
                                         ControlledExecutionPolicy)
from controlled_execution_policy import ExtensionRegistry
from execution_enums import OrderSide, OrderType
from execution_kill_switch_models import (KillSwitchReason,
                                          KillSwitchSnapshot,
                                          KillSwitchState)
from execution_risk_models import RiskDecision, RiskDecisionType
from micro_live_authorization import MicroLiveAuthorizationService
from micro_live_errors import (MicroLiveConfigurationError,
                               MicroLiveContractError,
                               MicroLiveStateError)
from micro_live_models import (MicroLiveApproval,
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
                               MicroLiveStatistics)
from micro_live_policy import (MicroLiveAuthorizationPolicy,
                               MicroLiveTransitionPolicy)

S = MicroLiveAuthorizationState
CODE = MicroLiveDecisionCode
FOUNDATION = ControlledExecutionFoundation(ExtensionRegistry())
SERVICE = MicroLiveAuthorizationService(foundation=FOUNDATION)
RISK_ALLOW = RiskDecision(decision=RiskDecisionType.ALLOW)
KS_ENABLED = KillSwitchSnapshot(
    state=KillSwitchState.ENABLED,
    reason=KillSwitchReason.MANUAL, timestamp=1, sequence_id=1)


def _kill(state):
    return KillSwitchSnapshot(state=state,
                              reason=KillSwitchReason.MANUAL,
                              timestamp=1, sequence_id=1)


def _scope(**kw):
    from micro_live_models import MicroLiveScope
    base = dict(symbol="BTCUSDT", side=OrderSide.BUY,
                order_type=OrderType.LIMIT)
    base.update(kw)
    return MicroLiveScope(**base)


def _limits(**kw):
    base = dict(maximum_order_quantity=D("10"),
                maximum_notional=D("1000"),
                maximum_position_size=D("20"),
                maximum_open_orders=5,
                maximum_daily_executions=10,
                maximum_daily_loss=D("100"),
                maximum_exposure=D("2000"),
                maximum_leverage=D("1"))
    base.update(kw)
    return MicroLiveLimits(**base)


def _request(**kw):
    base = dict(authorization_reference="auth-1",
                symbol="BTCUSDT", side=OrderSide.BUY,
                order_type=OrderType.LIMIT, quantity=D("1"),
                maximum_notional=D("100"),
                execution_mode=(ControlledExecutionMode
                                .MICRO_LIVE),
                expiry_sequence=100, scope=_scope(),
                logical_sequence=1)
    base.update(kw)
    return MicroLiveRequest(**base)


def _approval(**kw):
    base = dict(approval_reference="app-1",
                approver_reference="owner-1",
                authorization_reference="auth-1",
                expiry_sequence=50, logical_sequence=2)
    base.update(kw)
    return MicroLiveApproval(**base)


def _policy(**kw):
    base = dict(mode=ControlledExecutionMode.MICRO_LIVE,
                broker_read_allowed=True,
                human_confirmation_required=True,
                explicit_authorization_required=True,
                authorization_reference="auth-1")
    base.update(kw)
    return ControlledExecutionPolicy(**base)


def _references(sequence=5, **kw):
    base = dict(request_reference="req-1",
                snapshot_reference="snap-next",
                logical_sequence=sequence)
    base.update(kw)
    return MicroLiveReferences(**base)


def _empty():
    return MicroLiveSnapshot(snapshot_reference="snap-0")


def _snapshot(state=S.PENDING, approval=None, **kw):
    record = MicroLiveAuthorization(
        authorization_reference="auth-1", request=_request(),
        limits=_limits(), state=state, approval=approval,
        logical_sequence=2)
    return MicroLiveSnapshot(snapshot_reference="snap-0",
                             authorizations=(record,), **kw)


_DEFAULT = object()


def _or(value, factory):
    return factory() if value is _DEFAULT else value


def _request_op(snapshot=_DEFAULT, request=_DEFAULT,
                limits=_DEFAULT, policy=_DEFAULT,
                kill_switch=KS_ENABLED, references=_DEFAULT):
    return SERVICE.request_authorization(
        _or(snapshot, _empty), _or(request, _request),
        _or(limits, _limits), _or(policy, _policy),
        kill_switch, _or(references, _references))


def _approve_op(snapshot=_DEFAULT, approval=_DEFAULT,
                risk=RISK_ALLOW, policy=_DEFAULT,
                kill_switch=KS_ENABLED, references=_DEFAULT,
                reference="auth-1"):
    return SERVICE.approve(
        _or(snapshot, _snapshot), reference,
        _or(approval, _approval), risk, _or(policy, _policy),
        kill_switch, _or(references, _references))


def _evaluate_op(snapshot=_DEFAULT, risk=RISK_ALLOW,
                 policy=_DEFAULT, kill_switch=KS_ENABLED,
                 references=_DEFAULT, reference="auth-1"):
    return SERVICE.evaluate(
        _or(snapshot,
            lambda: _snapshot(S.APPROVED, _approval())),
        reference, risk, _or(policy, _policy), kill_switch,
        _or(references, _references))


# ── Servis kuruluşu ──────────────────────────────────────────────────

class TestServiceConstruction:
    def test_frozen_slots(self):
        params = (MicroLiveAuthorizationService
                  .__dataclass_params__)
        assert params.frozen
        assert "__slots__" in vars(
            MicroLiveAuthorizationService)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_invalid_foundation_rejected(self, bad):
        with pytest.raises(MicroLiveConfigurationError):
            MicroLiveAuthorizationService(foundation=bad)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_invalid_transition_policy_rejected(self, bad):
        with pytest.raises(MicroLiveConfigurationError):
            MicroLiveAuthorizationService(
                foundation=FOUNDATION, transition_policy=bad)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_invalid_policy_rules_rejected(self, bad):
        with pytest.raises(MicroLiveConfigurationError):
            MicroLiveAuthorizationService(
                foundation=FOUNDATION, policy_rules=bad)

    def test_explicit_components_accepted(self):
        service = MicroLiveAuthorizationService(
            foundation=FOUNDATION,
            transition_policy=MicroLiveTransitionPolicy(),
            policy_rules=MicroLiveAuthorizationPolicy())
        assert isinstance(service,
                          MicroLiveAuthorizationService)

    def test_service_immutable(self):
        with pytest.raises(Exception):
            SERVICE.foundation = None


# ── request_authorization ────────────────────────────────────────────

class TestRequestAuthorization:
    def test_accepted_pending(self):
        result = _request_op()
        assert result.decision is MicroLiveDecision.ACCEPTED
        assert result.decision_code is \
            CODE.AUTHORIZATION_REQUESTED
        assert result.operation is \
            MicroLiveOperation.REQUEST_AUTHORIZATION
        record = result.snapshot.authorization_for("auth-1")
        assert record.state is S.PENDING
        assert record.approval is None

    def test_no_automatic_approval(self):
        result = _request_op()
        record = result.snapshot.authorization_for("auth-1")
        assert record.state is not S.APPROVED

    def test_input_snapshot_unchanged(self):
        snapshot = _empty()
        _request_op(snapshot=snapshot)
        assert snapshot.authorizations == ()

    def test_new_snapshot_reference(self):
        result = _request_op()
        assert result.snapshot.snapshot_reference == "snap-next"
        assert result.snapshot.logical_sequence == 5

    def test_audit_records_ordered(self):
        result = _request_op()
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED",
            "KILL_SWITCH_CHECKED", "TRANSITION_VALIDATED",
            "LIMITS_VALIDATED", "AUTHORIZATION_RECORDED")

    def test_duplicate_reference_rejected(self):
        with pytest.raises(MicroLiveStateError) as exc:
            _request_op(snapshot=_snapshot())
        assert str(exc.value) == \
            "MICRO_LIVE_STATE:DUPLICATE_AUTHORIZATION"

    @pytest.mark.parametrize("mode", [
        ControlledExecutionMode.PAPER,
        ControlledExecutionMode.SHADOW])
    def test_non_micro_live_request_mode_denied(self, mode):
        result = _request_op(
            request=_request(execution_mode=mode))
        assert result.decision is MicroLiveDecision.DENIED
        assert result.decision_code is CODE.MODE_DENIED

    @pytest.mark.parametrize("mode", [
        ControlledExecutionMode.PAPER,
        ControlledExecutionMode.SHADOW])
    def test_non_micro_live_policy_mode_denied(self, mode):
        result = _request_op(
            policy=ControlledExecutionPolicy(mode=mode))
        assert result.decision_code is CODE.MODE_DENIED

    @pytest.mark.parametrize("bad_policy", [None, "x", 1, ()])
    def test_non_policy_object_mode_denied(self, bad_policy):
        result = _request_op(policy=bad_policy)
        assert result.decision_code is CODE.MODE_DENIED

    def test_exchange_write_policy_denied(self):
        result = _request_op(
            policy=_policy(exchange_write_allowed=True))
        assert result.decision_code is CODE.POLICY_DENIED

    def test_missing_authorization_reference_policy_denied(
            self):
        result = _request_op(
            policy=_policy(authorization_reference=None))
        assert result.decision_code is CODE.POLICY_DENIED

    def test_no_explicit_authorization_policy_denied(self):
        result = _request_op(policy=_policy(
            explicit_authorization_required=False))
        assert result.decision_code is CODE.POLICY_DENIED

    def test_policy_reference_mismatch_denied(self):
        result = _request_op(policy=_policy(
            authorization_reference="auth-other"))
        assert result.decision_code is CODE.POLICY_DENIED

    @pytest.mark.parametrize("state", [
        KillSwitchState.DISABLED, KillSwitchState.LOCKED,
        KillSwitchState.MAINTENANCE])
    def test_kill_switch_denied(self, state):
        result = _request_op(kill_switch=_kill(state))
        assert result.decision_code is CODE.KILL_SWITCH_DENIED

    @pytest.mark.parametrize("bad", [None, "ENABLED", 1])
    def test_non_snapshot_kill_switch_denied(self, bad):
        result = _request_op(kill_switch=bad)
        assert result.decision_code is CODE.KILL_SWITCH_DENIED

    def test_scope_symbol_mismatch_denied(self):
        request = _request(scope=_scope(symbol="ETHUSDT"))
        result = _request_op(request=request)
        assert result.decision_code is CODE.SCOPE_DENIED

    def test_scope_side_mismatch_denied(self):
        request = _request(scope=_scope(side=OrderSide.SELL))
        result = _request_op(request=request)
        assert result.decision_code is CODE.SCOPE_DENIED

    def test_scope_order_type_mismatch_denied(self):
        request = _request(
            scope=_scope(order_type=OrderType.MARKET))
        result = _request_op(request=request)
        assert result.decision_code is CODE.SCOPE_DENIED

    def test_quantity_over_limit_denied(self):
        result = _request_op(request=_request(
            quantity=D("11")))
        assert result.decision_code is CODE.LIMIT_DENIED

    def test_notional_over_limit_denied(self):
        result = _request_op(request=_request(
            maximum_notional=D("1500")),
            limits=_limits(maximum_notional=D("1000")))
        assert result.decision_code is CODE.LIMIT_DENIED

    def test_notional_over_exposure_denied(self):
        result = _request_op(
            limits=_limits(maximum_exposure=D("50")))
        assert result.decision_code is CODE.LIMIT_DENIED

    def test_denied_increments_counter(self):
        result = _request_op(kill_switch=_kill(
            KillSwitchState.DISABLED))
        assert result.snapshot.denied_count == 1
        assert result.snapshot.authorizations == ()

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_request_rejected(self, bad):
        with pytest.raises(MicroLiveContractError):
            _request_op(request=bad)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_limits_rejected(self, bad):
        with pytest.raises(MicroLiveContractError):
            _request_op(limits=bad)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_snapshot_rejected(self, bad):
        with pytest.raises(MicroLiveContractError):
            _request_op(snapshot=bad)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_references_rejected(self, bad):
        with pytest.raises(MicroLiveContractError):
            _request_op(references=bad)


# ── approve ──────────────────────────────────────────────────────────

class TestApprove:
    def test_approved(self):
        result = _approve_op()
        assert result.decision is MicroLiveDecision.ACCEPTED
        assert result.decision_code is \
            CODE.AUTHORIZATION_APPROVED
        record = result.snapshot.authorization_for("auth-1")
        assert record.state is S.APPROVED
        assert record.approval == _approval()

    def test_audit_records_ordered(self):
        result = _approve_op()
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "TRANSITION_VALIDATED",
            "MODE_VALIDATED", "RISK_EVALUATED",
            "PERMISSION_EVALUATED", "KILL_SWITCH_CHECKED",
            "LIMITS_VALIDATED", "AUTHORIZATION_RECORDED")

    def test_input_snapshot_unchanged(self):
        snapshot = _snapshot()
        _approve_op(snapshot=snapshot)
        assert snapshot.authorization_for(
            "auth-1").state is S.PENDING

    @pytest.mark.parametrize("state,approval", [
        (S.APPROVED, "yes"), (S.DENIED, None),
        (S.EXPIRED, None), (S.REVOKED, "yes")])
    def test_non_pending_transition_denied(self, state,
                                           approval):
        snapshot = _snapshot(
            state, _approval() if approval else None)
        result = _approve_op(snapshot=snapshot)
        assert result.decision is MicroLiveDecision.DENIED
        assert result.decision_code is CODE.TRANSITION_DENIED

    def test_unknown_authorization_rejected(self):
        with pytest.raises(MicroLiveStateError) as exc:
            _approve_op(snapshot=_empty())
        assert str(exc.value) == \
            "MICRO_LIVE_STATE:UNKNOWN_AUTHORIZATION"

    @pytest.mark.parametrize("decision", [
        RiskDecisionType.REJECT, RiskDecisionType.REDUCE_SIZE,
        RiskDecisionType.REQUIRE_CONFIRMATION])
    def test_risk_not_allow_denied(self, decision):
        result = _approve_op(
            risk=RiskDecision(decision=decision))
        assert result.decision_code is CODE.RISK_DENIED

    @pytest.mark.parametrize("bad", [None, "ALLOW", 1, ()])
    def test_non_risk_decision_denied(self, bad):
        result = _approve_op(risk=bad)
        assert result.decision_code is CODE.RISK_DENIED

    @pytest.mark.parametrize("state", [
        KillSwitchState.DISABLED, KillSwitchState.LOCKED,
        KillSwitchState.MAINTENANCE])
    def test_kill_switch_denied(self, state):
        result = _approve_op(kill_switch=_kill(state))
        assert result.decision_code is CODE.KILL_SWITCH_DENIED

    @pytest.mark.parametrize("mode", [
        ControlledExecutionMode.PAPER,
        ControlledExecutionMode.SHADOW])
    def test_wrong_mode_denied(self, mode):
        result = _approve_op(
            policy=ControlledExecutionPolicy(mode=mode))
        assert result.decision_code is CODE.MODE_DENIED

    def test_exchange_write_policy_denied(self):
        result = _approve_op(
            policy=_policy(exchange_write_allowed=True))
        assert result.decision_code is CODE.POLICY_DENIED

    def test_policy_reference_mismatch_denied(self):
        # Politika başka bir yetkiye bağlıysa onay verilemez.
        result = _approve_op(policy=_policy(
            authorization_reference="auth-other"))
        assert result.decision_code is CODE.POLICY_DENIED
        assert result.snapshot.authorization_for(
            "auth-1").state is S.PENDING

    def test_missing_policy_reference_denied(self):
        result = _approve_op(policy=_policy(
            authorization_reference=None))
        assert result.decision_code is CODE.POLICY_DENIED

    def test_gate_conflict_permission_denied(self):
        # MICRO_LIVE güvenlik sözleşmesi insan onayı ister;
        # onaysız politika kapıda INVALID_POLICY üretir.
        result = _approve_op(policy=_policy(
            human_confirmation_required=False))
        assert result.decision_code is CODE.PERMISSION_DENIED

    def test_expired_approval_denied(self):
        result = _approve_op(
            references=_references(sequence=60))
        assert result.decision_code is CODE.POLICY_DENIED

    def test_expired_request_denied(self):
        approval = _approval(expiry_sequence=300)
        result = _approve_op(
            approval=approval,
            references=_references(sequence=150))
        assert result.decision_code is CODE.POLICY_DENIED

    def test_approval_reference_mismatch_rejected(self):
        with pytest.raises(MicroLiveContractError):
            _approve_op(approval=_approval(
                authorization_reference="auth-2"))

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_approval_rejected(self, bad):
        with pytest.raises(MicroLiveContractError):
            _approve_op(approval=bad)

    def test_denied_increments_counter(self):
        result = _approve_op(risk=RiskDecision(
            decision=RiskDecisionType.REJECT))
        assert result.snapshot.denied_count == 1
        assert result.snapshot.authorization_for(
            "auth-1").state is S.PENDING


# ── deny / expire / revoke (fail-safe işlemler) ──────────────────────

class TestDeny:
    def test_denied_transition(self):
        result = SERVICE.deny(_snapshot(), "auth-1",
                              _references())
        assert result.decision is MicroLiveDecision.ACCEPTED
        assert result.decision_code is \
            CODE.AUTHORIZATION_DENIED
        assert result.snapshot.authorization_for(
            "auth-1").state is S.DENIED

    @pytest.mark.parametrize("state,approval", [
        (S.APPROVED, "yes"), (S.DENIED, None),
        (S.EXPIRED, None), (S.REVOKED, "yes")])
    def test_non_pending_transition_denied(self, state,
                                           approval):
        snapshot = _snapshot(
            state, _approval() if approval else None)
        result = SERVICE.deny(snapshot, "auth-1",
                              _references())
        assert result.decision_code is CODE.TRANSITION_DENIED

    def test_unknown_authorization_rejected(self):
        with pytest.raises(MicroLiveStateError):
            SERVICE.deny(_empty(), "auth-1", _references())

    def test_operation_and_audit(self):
        result = SERVICE.deny(_snapshot(), "auth-1",
                              _references())
        assert result.operation is MicroLiveOperation.DENY
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "TRANSITION_VALIDATED",
            "AUTHORIZATION_RECORDED")


class TestExpire:
    def test_pending_expired_at_expiry(self):
        result = SERVICE.expire(
            _snapshot(), "auth-1", _references(sequence=100))
        assert result.decision is MicroLiveDecision.ACCEPTED
        assert result.decision_code is \
            CODE.AUTHORIZATION_EXPIRED
        assert result.snapshot.authorization_for(
            "auth-1").state is S.EXPIRED

    def test_pending_premature_expiry_denied(self):
        result = SERVICE.expire(
            _snapshot(), "auth-1", _references(sequence=99))
        assert result.decision_code is CODE.TRANSITION_DENIED

    def test_approved_expired_at_approval_expiry(self):
        snapshot = _snapshot(S.APPROVED, _approval())
        result = SERVICE.expire(snapshot, "auth-1",
                                _references(sequence=50))
        record = result.snapshot.authorization_for("auth-1")
        assert record.state is S.EXPIRED
        assert record.approval == _approval()

    def test_approved_premature_expiry_denied(self):
        snapshot = _snapshot(S.APPROVED, _approval())
        result = SERVICE.expire(snapshot, "auth-1",
                                _references(sequence=49))
        assert result.decision_code is CODE.TRANSITION_DENIED

    @pytest.mark.parametrize("state,approval", [
        (S.DENIED, None), (S.EXPIRED, None),
        (S.REVOKED, "yes")])
    def test_terminal_transition_denied(self, state, approval):
        snapshot = _snapshot(
            state, _approval() if approval else None)
        result = SERVICE.expire(snapshot, "auth-1",
                                _references(sequence=500))
        assert result.decision_code is CODE.TRANSITION_DENIED

    def test_unknown_authorization_rejected(self):
        with pytest.raises(MicroLiveStateError):
            SERVICE.expire(_empty(), "auth-1", _references())


class TestRevoke:
    def test_approved_revoked(self):
        snapshot = _snapshot(S.APPROVED, _approval())
        result = SERVICE.revoke(snapshot, "auth-1",
                                _references())
        assert result.decision is MicroLiveDecision.ACCEPTED
        assert result.decision_code is \
            CODE.AUTHORIZATION_REVOKED
        record = result.snapshot.authorization_for("auth-1")
        assert record.state is S.REVOKED
        assert record.approval == _approval()

    @pytest.mark.parametrize("state", [S.PENDING, S.DENIED,
                                       S.EXPIRED])
    def test_non_approved_transition_denied(self, state):
        result = SERVICE.revoke(_snapshot(state), "auth-1",
                                _references())
        assert result.decision_code is CODE.TRANSITION_DENIED

    def test_revoked_not_re_revocable(self):
        snapshot = _snapshot(S.REVOKED, _approval())
        result = SERVICE.revoke(snapshot, "auth-1",
                                _references())
        assert result.decision_code is CODE.TRANSITION_DENIED

    def test_unknown_authorization_rejected(self):
        with pytest.raises(MicroLiveStateError):
            SERVICE.revoke(_empty(), "auth-1", _references())


class TestFailSafeOperations:
    """Güvenliği azaltan işlemler kapılara tabi değildir."""

    def test_deny_works_without_gates(self):
        # deny() mod/politika/kill switch parametresi ALMAZ.
        result = SERVICE.deny(_snapshot(), "auth-1",
                              _references())
        assert result.decision is MicroLiveDecision.ACCEPTED

    def test_revoke_works_without_gates(self):
        snapshot = _snapshot(S.APPROVED, _approval())
        result = SERVICE.revoke(snapshot, "auth-1",
                                _references())
        assert result.decision is MicroLiveDecision.ACCEPTED

    def test_expire_works_without_gates(self):
        result = SERVICE.expire(
            _snapshot(), "auth-1", _references(sequence=100))
        assert result.decision is MicroLiveDecision.ACCEPTED


# ── evaluate ─────────────────────────────────────────────────────────

class TestEvaluate:
    def test_authorized(self):
        result = _evaluate_op()
        assert result.decision is MicroLiveDecision.AUTHORIZED
        assert result.decision_code is \
            CODE.EVALUATION_AUTHORIZED
        assert result.authorized is True

    def test_read_only_snapshot_identical(self):
        snapshot = _snapshot(S.APPROVED, _approval())
        result = _evaluate_op(snapshot=snapshot)
        assert result.snapshot is snapshot
        assert result.snapshot.denied_count == 0

    def test_audit_records_ordered(self):
        result = _evaluate_op()
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "RISK_EVALUATED",
            "KILL_SWITCH_CHECKED", "MODE_VALIDATED",
            "PERMISSION_EVALUATED", "LIMITS_VALIDATED",
            "EVALUATION_COMPLETED")

    @pytest.mark.parametrize("state,approval", [
        (S.PENDING, None), (S.DENIED, None),
        (S.EXPIRED, None), (S.REVOKED, "yes")])
    def test_non_approved_not_authorized(self, state,
                                         approval):
        snapshot = _snapshot(
            state, _approval() if approval else None)
        result = _evaluate_op(snapshot=snapshot)
        assert result.decision is \
            MicroLiveDecision.NOT_AUTHORIZED
        assert result.decision_code is \
            CODE.NOT_AUTHORIZED_STATE

    def test_expired_approval_not_authorized(self):
        result = _evaluate_op(
            references=_references(sequence=50))
        assert result.decision_code is \
            CODE.NOT_AUTHORIZED_EXPIRED

    def test_expired_request_not_authorized(self):
        approval = _approval(expiry_sequence=300)
        snapshot = _snapshot(S.APPROVED, approval)
        result = _evaluate_op(
            snapshot=snapshot,
            references=_references(sequence=120))
        assert result.decision_code is \
            CODE.NOT_AUTHORIZED_EXPIRED

    @pytest.mark.parametrize("decision", [
        RiskDecisionType.REJECT, RiskDecisionType.REDUCE_SIZE,
        RiskDecisionType.REQUIRE_CONFIRMATION])
    def test_risk_not_allow_not_authorized(self, decision):
        result = _evaluate_op(
            risk=RiskDecision(decision=decision))
        assert result.decision is \
            MicroLiveDecision.NOT_AUTHORIZED
        assert result.decision_code is CODE.RISK_DENIED

    @pytest.mark.parametrize("state", [
        KillSwitchState.DISABLED, KillSwitchState.LOCKED,
        KillSwitchState.MAINTENANCE])
    def test_kill_switch_not_authorized(self, state):
        result = _evaluate_op(kill_switch=_kill(state))
        assert result.decision_code is CODE.KILL_SWITCH_DENIED

    @pytest.mark.parametrize("mode", [
        ControlledExecutionMode.PAPER,
        ControlledExecutionMode.SHADOW])
    def test_wrong_mode_not_authorized(self, mode):
        result = _evaluate_op(
            policy=ControlledExecutionPolicy(mode=mode))
        assert result.decision_code is CODE.MODE_DENIED

    def test_exchange_write_policy_not_authorized(self):
        result = _evaluate_op(
            policy=_policy(exchange_write_allowed=True))
        assert result.decision_code is CODE.POLICY_DENIED

    def test_policy_reference_mismatch_not_authorized(self):
        result = _evaluate_op(policy=_policy(
            authorization_reference="auth-2"))
        assert result.decision_code is CODE.POLICY_DENIED

    def test_gate_conflict_not_authorized(self):
        result = _evaluate_op(policy=_policy(
            human_confirmation_required=False))
        assert result.decision_code is CODE.PERMISSION_DENIED

    def test_unknown_authorization_rejected(self):
        with pytest.raises(MicroLiveStateError):
            _evaluate_op(snapshot=_empty())

    def test_not_authorized_is_not_execution_permission(self):
        result = _evaluate_op(kill_switch=_kill(
            KillSwitchState.DISABLED))
        assert result.authorized is False

    def test_deterministic(self):
        first = _evaluate_op()
        second = _evaluate_op()
        assert first == second


# ── statistics / heartbeat / durumsuzluk ─────────────────────────────

class TestReadOnlyOperations:
    def test_statistics_empty(self):
        stats = SERVICE.statistics(_empty())
        assert stats == MicroLiveStatistics()

    def test_statistics_counts(self):
        snapshot = _snapshot(S.APPROVED, _approval(),
                             denied_count=3,
                             logical_sequence=9)
        stats = SERVICE.statistics(snapshot)
        assert stats.total_authorizations == 1
        assert stats.approved_count == 1
        assert stats.pending_count == 0
        assert stats.total_denied == 3
        assert stats.logical_sequence == 9

    def test_heartbeat(self):
        beat = SERVICE.heartbeat(_snapshot())
        assert beat == MicroLiveHeartbeat(
            alive=True, authorization_count=1,
            pending_count=1, approved_count=0,
            logical_sequence=0)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_statistics_bad_snapshot_rejected(self, bad):
        with pytest.raises(MicroLiveContractError):
            SERVICE.statistics(bad)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_heartbeat_bad_snapshot_rejected(self, bad):
        with pytest.raises(MicroLiveContractError):
            SERVICE.heartbeat(bad)


class TestStatelessBehavior:
    def test_repeated_request_same_result(self):
        assert _request_op() == _request_op()

    def test_repeated_approve_same_result(self):
        assert _approve_op() == _approve_op()

    def test_service_holds_no_snapshot(self):
        fields = set(MicroLiveAuthorizationService
                     .__dataclass_fields__)
        assert fields == {"foundation", "transition_policy",
                          "policy_rules"}

    def test_two_service_instances_equal(self):
        other = MicroLiveAuthorizationService(
            foundation=FOUNDATION)
        assert other == SERVICE

    def test_denial_does_not_leak_between_calls(self):
        _request_op(kill_switch=_kill(
            KillSwitchState.DISABLED))
        result = _request_op()
        assert result.snapshot.denied_count == 0


# ── Değişmezlik ──────────────────────────────────────────────────────

class TestImmutability:
    @pytest.mark.parametrize("model", [
        _request(), _approval(), _limits(), _scope(),
        _references(), _empty(), _snapshot(),
        RISK_ALLOW])
    def test_models_frozen(self, model):
        fieldname = tuple(dataclasses.asdict(model))[0] if \
            dataclasses.is_dataclass(model) else "x"
        with pytest.raises(Exception):
            setattr(model, fieldname, None)

    def test_result_frozen(self):
        result = _request_op()
        with pytest.raises(Exception):
            result.decision = MicroLiveDecision.DENIED

    def test_authorization_record_frozen(self):
        record = _snapshot().authorizations[0]
        with pytest.raises(Exception):
            record.state = S.APPROVED

    def test_limits_are_immutable_after_grant(self):
        result = _approve_op()
        record = result.snapshot.authorization_for("auth-1")
        assert record.limits == _limits()
        with pytest.raises(Exception):
            record.limits.maximum_notional = D("999999")

    def test_transitions_produce_new_records(self):
        snapshot = _snapshot()
        result = SERVICE.deny(snapshot, "auth-1",
                              _references())
        assert result.snapshot.authorizations[0] is not \
            snapshot.authorizations[0]
