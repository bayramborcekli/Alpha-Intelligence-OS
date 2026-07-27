"""Mission 2100 — Agent 06: Micro Live politika testleri.

Kapalı geçiş matrisi ve deterministik politika kuralları."""

from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from controlled_execution_models import (ControlledExecutionMode,
                                         ControlledExecutionPolicy)
from execution_enums import OrderSide, OrderType
from execution_kill_switch_models import (KillSwitchReason,
                                          KillSwitchSnapshot,
                                          KillSwitchState)
from execution_risk_models import RiskDecision, RiskDecisionType
from micro_live_models import (MicroLiveApproval,
                               MicroLiveAuthorization,
                               MicroLiveAuthorizationState,
                               MicroLiveLimits, MicroLiveRequest,
                               MicroLiveScope)
from micro_live_policy import (MicroLiveAuthorizationPolicy,
                               MicroLiveTransitionPolicy)

S = MicroLiveAuthorizationState
TRANSITIONS = MicroLiveTransitionPolicy()
RULES = MicroLiveAuthorizationPolicy()

_ALLOWED = frozenset({
    (S.NONE, S.PENDING),
    (S.PENDING, S.APPROVED),
    (S.PENDING, S.DENIED),
    (S.PENDING, S.EXPIRED),
    (S.APPROVED, S.REVOKED),
    (S.APPROVED, S.EXPIRED)})


def _scope(**kw):
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


def _kill(state=KillSwitchState.ENABLED):
    return KillSwitchSnapshot(state=state,
                              reason=KillSwitchReason.MANUAL,
                              timestamp=1, sequence_id=1)


def _record(state=S.PENDING, approval=None):
    return MicroLiveAuthorization(
        authorization_reference="auth-1", request=_request(),
        limits=_limits(), state=state, approval=approval,
        logical_sequence=2)


# ── Geçiş matrisi ────────────────────────────────────────────────────

class TestTransitionMatrix:
    @pytest.mark.parametrize("current", list(S))
    @pytest.mark.parametrize("target", list(S))
    def test_full_matrix_membership(self, current, target):
        expected = (current, target) in _ALLOWED
        assert TRANSITIONS.transition_allowed(
            current, target) is expected

    @pytest.mark.parametrize("pair", sorted(
        _ALLOWED, key=lambda p: (p[0].value, p[1].value)))
    def test_allowed_pairs(self, pair):
        assert TRANSITIONS.transition_allowed(*pair) is True

    @pytest.mark.parametrize("pair", [
        (S.NONE, S.APPROVED), (S.DENIED, S.APPROVED),
        (S.EXPIRED, S.APPROVED), (S.REVOKED, S.APPROVED),
        (S.EXPIRED, S.PENDING), (S.REVOKED, S.PENDING),
        (S.DENIED, S.PENDING), (S.APPROVED, S.PENDING),
        (S.APPROVED, S.DENIED), (S.NONE, S.DENIED),
        (S.NONE, S.EXPIRED), (S.NONE, S.REVOKED),
        (S.PENDING, S.REVOKED), (S.DENIED, S.REVOKED),
        (S.EXPIRED, S.REVOKED), (S.REVOKED, S.EXPIRED)])
    def test_denied_pairs(self, pair):
        assert TRANSITIONS.transition_allowed(*pair) is False

    @pytest.mark.parametrize("state", list(S))
    def test_no_self_transition(self, state):
        assert TRANSITIONS.transition_allowed(
            state, state) is False

    @pytest.mark.parametrize("bad", [None, "PENDING", 1, (),
                                     object()])
    def test_non_enum_current_rejected(self, bad):
        assert TRANSITIONS.transition_allowed(
            bad, S.PENDING) is False

    @pytest.mark.parametrize("bad", [None, "APPROVED", 1, (),
                                     object()])
    def test_non_enum_target_rejected(self, bad):
        assert TRANSITIONS.transition_allowed(
            S.PENDING, bad) is False

    @pytest.mark.parametrize("state,targets", [
        (S.NONE, {S.PENDING}),
        (S.PENDING, {S.APPROVED, S.DENIED, S.EXPIRED}),
        (S.APPROVED, {S.REVOKED, S.EXPIRED}),
        (S.DENIED, set()), (S.EXPIRED, set()),
        (S.REVOKED, set())])
    def test_allowed_targets(self, state, targets):
        assert TRANSITIONS.allowed_targets(state) == \
            frozenset(targets)

    def test_allowed_targets_non_enum_empty(self):
        assert TRANSITIONS.allowed_targets("NONE") == frozenset()

    @pytest.mark.parametrize("state,terminal", [
        (S.NONE, False), (S.PENDING, False),
        (S.APPROVED, False), (S.DENIED, True),
        (S.EXPIRED, True), (S.REVOKED, True)])
    def test_terminal_states(self, state, terminal):
        assert TRANSITIONS.terminal(state) is terminal

    @pytest.mark.parametrize("bad", [None, "DENIED", 1])
    def test_terminal_non_enum_false(self, bad):
        assert TRANSITIONS.terminal(bad) is False

    def test_no_transition_reaches_approved_implicitly(self):
        sources = {source for source, target in _ALLOWED
                   if target is S.APPROVED}
        assert sources == {S.PENDING}

    @pytest.mark.parametrize("terminal_state",
                             [S.DENIED, S.EXPIRED, S.REVOKED])
    @pytest.mark.parametrize("target", list(S))
    def test_terminal_states_have_no_exit(self, terminal_state,
                                          target):
        assert TRANSITIONS.transition_allowed(
            terminal_state, target) is False


# ── Mod ve politika kuralları ────────────────────────────────────────

class TestModeAndPolicyRules:
    def test_micro_live_mode_valid(self):
        assert RULES.mode_valid(_policy()) is True

    @pytest.mark.parametrize("mode", [
        ControlledExecutionMode.PAPER,
        ControlledExecutionMode.SHADOW])
    def test_other_modes_invalid(self, mode):
        policy = ControlledExecutionPolicy(mode=mode)
        assert RULES.mode_valid(policy) is False

    @pytest.mark.parametrize("bad", [None, "MICRO_LIVE", 1, (),
                                     object()])
    def test_non_policy_mode_invalid(self, bad):
        assert RULES.mode_valid(bad) is False

    def test_valid_policy_accepted(self):
        assert RULES.policy_valid(_policy()) is True

    def test_exchange_write_always_invalid(self):
        policy = _policy(exchange_write_allowed=True)
        assert RULES.policy_valid(policy) is False

    def test_missing_explicit_authorization_invalid(self):
        policy = _policy(explicit_authorization_required=False)
        assert RULES.policy_valid(policy) is False

    def test_missing_authorization_reference_invalid(self):
        policy = _policy(authorization_reference=None)
        assert RULES.policy_valid(policy) is False

    def test_policy_reference_match(self):
        assert RULES.policy_reference_match(
            _policy(), "auth-1") is True

    def test_policy_reference_mismatch(self):
        assert RULES.policy_reference_match(
            _policy(), "auth-2") is False


# ── Kapsam kuralları ─────────────────────────────────────────────────

class TestScopeRules:
    def test_matching_scope_valid(self):
        request = _request()
        assert RULES.scope_valid(request,
                                 request.scope) is True

    def test_symbol_mismatch_invalid(self):
        assert RULES.scope_valid(
            _request(), _scope(symbol="ETHUSDT")) is False

    def test_side_mismatch_invalid(self):
        assert RULES.scope_valid(
            _request(), _scope(side=OrderSide.SELL)) is False

    def test_order_type_mismatch_invalid(self):
        assert RULES.scope_valid(
            _request(),
            _scope(order_type=OrderType.MARKET)) is False


# ── Limit kuralları ──────────────────────────────────────────────────

class TestLimitRules:
    def test_within_limits(self):
        assert RULES.within_limits(_request(),
                                   _limits()) is True

    def test_quantity_at_limit_allowed(self):
        request = _request(quantity=D("10"))
        assert RULES.within_limits(request, _limits()) is True

    def test_quantity_above_limit_denied(self):
        request = _request(quantity=D("10.00000001"))
        assert RULES.within_limits(request, _limits()) is False

    def test_notional_at_limit_allowed(self):
        request = _request(maximum_notional=D("1000"))
        assert RULES.within_limits(request, _limits()) is True

    def test_notional_above_limit_denied(self):
        request = _request(maximum_notional=D("1000.01"))
        assert RULES.within_limits(request, _limits()) is False

    def test_notional_above_exposure_denied(self):
        limits = _limits(maximum_exposure=D("50"))
        assert RULES.within_limits(_request(),
                                   limits) is False

    def test_decimal_precision_exact(self):
        limits = _limits(maximum_order_quantity=D("0.1"))
        request = _request(quantity=D("0.1"))
        assert RULES.within_limits(request, limits) is True


# ── Risk ve kill switch kuralları ────────────────────────────────────

class TestRiskAndKillSwitchRules:
    def test_allow_passes(self):
        risk = RiskDecision(decision=RiskDecisionType.ALLOW)
        assert RULES.risk_passed(risk) is True

    @pytest.mark.parametrize("decision", [
        RiskDecisionType.REJECT, RiskDecisionType.REDUCE_SIZE,
        RiskDecisionType.REQUIRE_CONFIRMATION])
    def test_non_allow_fails(self, decision):
        risk = RiskDecision(decision=decision)
        assert RULES.risk_passed(risk) is False

    @pytest.mark.parametrize("bad", [None, "ALLOW", 1, (),
                                     object(), True])
    def test_non_risk_decision_fails(self, bad):
        assert RULES.risk_passed(bad) is False

    def test_enabled_kill_switch_passes(self):
        assert RULES.kill_switch_enabled(_kill()) is True

    @pytest.mark.parametrize("state", [
        KillSwitchState.DISABLED, KillSwitchState.LOCKED,
        KillSwitchState.MAINTENANCE])
    def test_non_enabled_kill_switch_fails(self, state):
        assert RULES.kill_switch_enabled(_kill(state)) is False

    @pytest.mark.parametrize("bad", [None, "ENABLED", 1, (),
                                     object()])
    def test_non_snapshot_kill_switch_fails(self, bad):
        assert RULES.kill_switch_enabled(bad) is False


# ── Süre ve durum kuralları ──────────────────────────────────────────

class TestExpiryAndStateRules:
    def test_active_approval(self):
        assert RULES.approval_active(_approval(), 10) is True

    def test_approval_at_expiry_inactive(self):
        assert RULES.approval_active(_approval(), 50) is False

    def test_approval_past_expiry_inactive(self):
        assert RULES.approval_active(_approval(), 51) is False

    @pytest.mark.parametrize("bad", [None, "approval", 1, ()])
    def test_missing_approval_inactive(self, bad):
        assert RULES.approval_active(bad, 0) is False

    def test_active_request(self):
        assert RULES.request_active(_request(), 99) is True

    def test_request_at_expiry_inactive(self):
        assert RULES.request_active(_request(), 100) is False

    def test_request_past_expiry_inactive(self):
        assert RULES.request_active(_request(), 200) is False

    def test_approved_evaluable(self):
        record = _record(S.APPROVED, _approval())
        assert RULES.evaluable_state(record) is True

    @pytest.mark.parametrize("state,approval", [
        (S.PENDING, None), (S.DENIED, None),
        (S.EXPIRED, None), (S.REVOKED, "approval")])
    def test_non_approved_not_evaluable(self, state, approval):
        record = _record(
            state, _approval() if approval else None)
        assert RULES.evaluable_state(record) is False


# ── Politika sınıfı sözleşmeleri ─────────────────────────────────────

class TestPolicyClassContracts:
    @pytest.mark.parametrize("policy_class", [
        MicroLiveTransitionPolicy, MicroLiveAuthorizationPolicy])
    def test_frozen_slots(self, policy_class):
        assert policy_class.__dataclass_params__.frozen
        assert "__slots__" in vars(policy_class)

    @pytest.mark.parametrize("policy_class", [
        MicroLiveTransitionPolicy, MicroLiveAuthorizationPolicy])
    def test_stateless_no_fields(self, policy_class):
        assert policy_class.__dataclass_fields__ == {}

    def test_transition_policy_immutable(self):
        with pytest.raises(Exception):
            object.__getattribute__(TRANSITIONS, "x")

    def test_deterministic_repeated_calls(self):
        first = TRANSITIONS.transition_allowed(S.PENDING,
                                               S.APPROVED)
        second = TRANSITIONS.transition_allowed(S.PENDING,
                                                S.APPROVED)
        assert first is second is True

    def test_rules_deterministic(self):
        request = _request()
        results = tuple(
            RULES.within_limits(request, _limits())
            for _ in range(3))
        assert results == (True, True, True)
