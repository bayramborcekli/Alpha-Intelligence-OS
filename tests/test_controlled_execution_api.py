"""Mission 2100 — Agent 08: Birleşik Kontrollü Yürütme API testleri.

Kapsam: PAPER / SHADOW / MICRO_LIVE yönlendirme, karar eşlemesi,
doğrulama (örtük varsayılan YOK), yetkilendirme/risk/kill switch
zorlaması (baypas YOK), okuma işlemleri, değişmez yanıt zarfları,
sıfır borsa yazımı ve determinizm.
"""

import sys
from dataclasses import FrozenInstanceError
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlled_execution_api import (  # noqa: E402
    ControlledExecutionAPI)
from controlled_execution_api_errors import (  # noqa: E402
    ControlledExecutionAPIConfigurationError,
    ControlledExecutionAPIContractError,
    ControlledExecutionAPIModeError)
from controlled_execution_api_models import (  # noqa: E402
    ControlledExecutionAPIDecision, ControlledExecutionAudit,
    ControlledExecutionOperation, ControlledExecutionRequest,
    ControlledExecutionResponse, ControlledExecutionState,
    ControlledExecutionStatistics, ControlledExecutionStatus)
from controlled_execution_foundation import (  # noqa: E402
    ControlledExecutionFoundation)
from controlled_execution_models import (  # noqa: E402
    ControlledExecutionMode, ControlledExecutionPolicy)
from controlled_execution_policy import (  # noqa: E402
    ExtensionRegistry)
from controlled_execution_router import (  # noqa: E402
    ControlledExecutionRouter)
from execution_enums import (OrderSide, OrderType,  # noqa: E402
                             TimeInForce)
from execution_kill_switch_models import (  # noqa: E402
    KillSwitchReason, KillSwitchSnapshot, KillSwitchState)
from execution_models import ExecutionRequest  # noqa: E402
from execution_risk_models import (RiskDecision,  # noqa: E402
                                   RiskDecisionType)
from micro_live_authorization import (  # noqa: E402
    MicroLiveAuthorizationService)
from micro_live_models import (MicroLiveApproval,  # noqa: E402
                               MicroLiveLimits,
                               MicroLiveReferences,
                               MicroLiveRequest, MicroLiveScope,
                               MicroLiveSnapshot)
from paper_broker import PaperBroker  # noqa: E402
from paper_execution_models import (  # noqa: E402
    PaperExecutionReferences, PaperExecutionServiceResult)
from paper_execution_service import (  # noqa: E402
    PaperExecutionService, StaticRiskEvaluator)
from paper_models import PaperLedgerSnapshot  # noqa: E402
from shadow_models import (ShadowMarketObservation,  # noqa: E402
                           ShadowResult, ShadowSnapshot)
from shadow_mode import ShadowModeService  # noqa: E402

MODE = ControlledExecutionMode
OP = ControlledExecutionOperation
API_DECISION = ControlledExecutionAPIDecision

BROKER = PaperBroker(known_symbols=("BTCUSDT", "ETHUSDT"))
FOUNDATION = ControlledExecutionFoundation(ExtensionRegistry())

ALLOW = StaticRiskEvaluator(RiskDecision(
    decision=RiskDecisionType.ALLOW))
REJECT = StaticRiskEvaluator(RiskDecision(
    decision=RiskDecisionType.REJECT, code="LIMIT_BREACH"))
REDUCE = StaticRiskEvaluator(RiskDecision(
    decision=RiskDecisionType.REDUCE_SIZE,
    approved_quantity=D("1")))

MICRO_SERVICE = MicroLiveAuthorizationService(
    foundation=FOUNDATION)


def make_api(risk=ALLOW):
    return ControlledExecutionAPI(ControlledExecutionRouter(
        PaperExecutionService(broker=BROKER,
                              foundation=FOUNDATION,
                              risk_evaluator=risk),
        ShadowModeService(broker=BROKER, foundation=FOUNDATION,
                          risk_evaluator=risk),
        MICRO_SERVICE))


API = make_api()

KS_ENABLED = KillSwitchSnapshot(
    state=KillSwitchState.ENABLED,
    reason=KillSwitchReason.MANUAL, timestamp=1, sequence_id=1)
KS_DISABLED = KillSwitchSnapshot(
    state=KillSwitchState.DISABLED,
    reason=KillSwitchReason.MANUAL, timestamp=2, sequence_id=2)

PAPER_POLICY = ControlledExecutionPolicy(
    mode=MODE.PAPER, simulated_fill_allowed=True)
SHADOW_POLICY = ControlledExecutionPolicy(
    mode=MODE.SHADOW, broker_read_allowed=True)
MICRO_POLICY = ControlledExecutionPolicy(
    mode=MODE.MICRO_LIVE, broker_read_allowed=True,
    human_confirmation_required=True,
    explicit_authorization_required=True,
    authorization_reference="auth-1")


def _ledger(cash="1000"):
    return PaperLedgerSnapshot(
        quote_asset="USDT", initial_cash=D(cash), cash=D(cash),
        reserved_cash=D("0"), realized_pnl=D("0"),
        commission_paid=D("0"))


def _execution(quantity="2", price="100"):
    return ExecutionRequest(
        symbol="BTCUSDT", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, quantity=D(quantity),
        time_in_force=TimeInForce.GTC, price=D(price))


def _observation():
    return ShadowMarketObservation(
        observation_reference="obs-1", symbol="BTCUSDT",
        best_bid=D("99"), best_ask=D("101"),
        last_trade_price=D("100"), logical_sequence=4)


def _paper_references(sequence=7):
    return PaperExecutionReferences(
        request_reference="req-1",
        previous_ledger_reference="ledger-0",
        current_ledger_reference="ledger-1",
        logical_sequence=sequence)


def _micro_request(reference="auth-1"):
    return MicroLiveRequest(
        authorization_reference=reference, symbol="BTCUSDT",
        side=OrderSide.BUY, order_type=OrderType.LIMIT,
        quantity=D("1"), maximum_notional=D("100"),
        execution_mode=MODE.MICRO_LIVE, expiry_sequence=100,
        scope=MicroLiveScope(symbol="BTCUSDT",
                             side=OrderSide.BUY,
                             order_type=OrderType.LIMIT),
        logical_sequence=1)


def _micro_limits():
    return MicroLiveLimits(
        maximum_order_quantity=D("10"),
        maximum_notional=D("1000"),
        maximum_position_size=D("20"), maximum_open_orders=5,
        maximum_daily_executions=10, maximum_daily_loss=D("100"),
        maximum_exposure=D("2000"), maximum_leverage=D("1"))


def _micro_references(sequence=5, snapshot="snap-next"):
    return MicroLiveReferences(
        request_reference="req-1",
        snapshot_reference=snapshot,
        logical_sequence=sequence)


def _request(mode=MODE.PAPER, operation=OP.SUBMIT, **overrides):
    values = dict(mode=mode, operation=operation,
                  request_reference="api-req-1",
                  logical_sequence=9)
    if operation in (OP.SUBMIT, OP.CANCEL):
        if mode is MODE.PAPER:
            values.update(policy=PAPER_POLICY,
                          kill_switch=KS_ENABLED,
                          order_reference="ord-1",
                          execution=_execution())
        elif mode is MODE.SHADOW:
            values.update(policy=SHADOW_POLICY,
                          kill_switch=KS_ENABLED,
                          order_reference="ord-1",
                          execution=_execution(),
                          observation=_observation())
        else:
            values.update(policy=MICRO_POLICY,
                          kill_switch=KS_ENABLED,
                          micro_live_request=_micro_request(),
                          micro_live_limits=_micro_limits())
            if operation is OP.CANCEL:
                values.update(order_reference="auth-1")
    values.update(overrides)
    return ControlledExecutionRequest(**values)


def _state(mode=MODE.PAPER, **overrides):
    values = {}
    if mode in (MODE.PAPER, MODE.SHADOW):
        values.update(ledger=_ledger(),
                      paper_references=_paper_references())
    if mode is MODE.SHADOW:
        values.update(shadow=ShadowSnapshot(
            snapshot_reference="shadow-0"))
    if mode is MODE.MICRO_LIVE:
        values.update(
            micro_live=MicroLiveSnapshot(
                snapshot_reference="snap-0"),
            micro_live_references=_micro_references())
    values.update(overrides)
    return ControlledExecutionState(**values)


READ_METHODS = ("status", "positions", "orders", "executions",
                "statistics", "heartbeat")
ALL_METHODS = ("submit", "cancel") + READ_METHODS
METHOD_OPERATION = {
    "submit": OP.SUBMIT, "cancel": OP.CANCEL,
    "status": OP.STATUS, "positions": OP.POSITIONS,
    "orders": OP.ORDERS, "executions": OP.EXECUTIONS,
    "statistics": OP.STATISTICS, "heartbeat": OP.HEARTBEAT}


class TestPaperRouting:
    def test_submit_executed(self):
        response = API.submit(_request(), _state())
        assert response.decision is API_DECISION.ACCEPTED
        assert response.decision_code == "ORDER_EXECUTED"
        assert isinstance(response.payload,
                          PaperExecutionServiceResult)
        assert response.ledger_reference == "ledger-1"

    def test_submit_risk_rejected(self):
        response = make_api(REJECT).submit(_request(), _state())
        assert response.decision is API_DECISION.DENIED
        assert response.decision_code == "RISK_REJECTED"

    def test_submit_risk_reduce_recommendation(self):
        response = make_api(REDUCE).submit(_request(), _state())
        assert response.decision is \
            API_DECISION.RECOMMENDATION_ONLY
        assert response.decision_code == "RISK_REDUCE_SIZE"

    def test_submit_kill_switch_denied(self):
        response = API.submit(
            _request(kill_switch=KS_DISABLED), _state())
        assert response.decision is API_DECISION.DENIED
        assert response.decision_code == "KILL_SWITCH_DENIED"

    def test_submit_wrong_mode_policy_denied(self):
        response = API.submit(
            _request(policy=SHADOW_POLICY), _state())
        assert response.decision is API_DECISION.DENIED

    def test_cancel_unknown_order_fails_closed(self):
        # Bilinmeyen emir iptali sessizce yutulmaz: alt servisin
        # steril durum hatası aynen yükselir (fail-closed).
        with pytest.raises(Exception) as info:
            API.cancel(
                _request(operation=OP.CANCEL, execution=None),
                _state())
        assert "UNKNOWN_ORDER" in str(info.value)

    def test_submit_ledger_not_mutated(self):
        ledger = _ledger()
        API.submit(_request(),
                   _state(ledger=ledger))
        assert ledger.cash == D("1000")

    def test_submit_deterministic(self):
        first = API.submit(_request(), _state())
        second = API.submit(_request(), _state())
        assert first == second


class TestShadowRouting:
    def test_submit_simulated(self):
        response = API.submit(_request(mode=MODE.SHADOW),
                              _state(mode=MODE.SHADOW))
        assert response.decision is API_DECISION.ACCEPTED
        assert response.decision_code == "ORDER_SIMULATED"
        assert isinstance(response.payload, ShadowResult)

    def test_submit_risk_rejected(self):
        response = make_api(REJECT).submit(
            _request(mode=MODE.SHADOW),
            _state(mode=MODE.SHADOW))
        assert response.decision is API_DECISION.DENIED
        assert response.decision_code == "RISK_REJECTED"

    def test_submit_kill_switch_denied(self):
        response = API.submit(
            _request(mode=MODE.SHADOW,
                     kill_switch=KS_DISABLED),
            _state(mode=MODE.SHADOW))
        assert response.decision_code == "KILL_SWITCH_DENIED"

    def test_cancel_unknown_order_fails_closed(self):
        # Tam-dolum modelinde açık emir kalmaz: bilinmeyen emir
        # iptali alt servisin steril hatasıyla yükselir.
        with pytest.raises(Exception) as info:
            API.cancel(
                _request(mode=MODE.SHADOW, operation=OP.CANCEL,
                         execution=None, observation=None),
                _state(mode=MODE.SHADOW))
        assert "SHADOW_STATE:" in str(info.value)

    def test_cancel_kill_switch_denied_response(self):
        response = API.cancel(
            _request(mode=MODE.SHADOW, operation=OP.CANCEL,
                     execution=None, observation=None,
                     kill_switch=KS_DISABLED),
            _state(mode=MODE.SHADOW))
        assert response.decision is API_DECISION.DENIED
        assert response.decision_code == "KILL_SWITCH_DENIED"
        assert isinstance(response.payload, ShadowResult)

    def test_reduce_maps_to_recommendation(self):
        response = make_api(REDUCE).submit(
            _request(mode=MODE.SHADOW),
            _state(mode=MODE.SHADOW))
        assert response.decision is \
            API_DECISION.RECOMMENDATION_ONLY


class TestMicroLiveRouting:
    def test_submit_requests_authorization(self):
        response = API.submit(_request(mode=MODE.MICRO_LIVE),
                              _state(mode=MODE.MICRO_LIVE))
        assert response.decision is API_DECISION.ACCEPTED
        assert response.decision_code == \
            "AUTHORIZATION_REQUESTED"
        assert response.execution_reference == "auth-1"
        assert response.ledger_reference is None

    def test_submit_no_automatic_approval(self):
        response = API.submit(_request(mode=MODE.MICRO_LIVE),
                              _state(mode=MODE.MICRO_LIVE))
        record = response.payload.snapshot.authorization_for(
            "auth-1")
        assert record.state.value == "PENDING"

    def test_cancel_revokes_approved_authorization(self):
        pending = API.submit(
            _request(mode=MODE.MICRO_LIVE),
            _state(mode=MODE.MICRO_LIVE)).payload.snapshot
        approved = MICRO_SERVICE.approve(
            pending, "auth-1",
            MicroLiveApproval(approval_reference="app-1",
                              approver_reference="owner-1",
                              authorization_reference="auth-1",
                              expiry_sequence=50,
                              logical_sequence=2),
            RiskDecision(decision=RiskDecisionType.ALLOW),
            MICRO_POLICY, KS_ENABLED,
            _micro_references(6, "snap-2")).snapshot
        response = API.cancel(
            _request(mode=MODE.MICRO_LIVE,
                     operation=OP.CANCEL),
            _state(mode=MODE.MICRO_LIVE, micro_live=approved,
                   micro_live_references=_micro_references(
                       7, "snap-3")))
        assert response.decision is API_DECISION.ACCEPTED
        assert response.decision_code == \
            "AUTHORIZATION_REVOKED"

    def test_cancel_pending_transition_denied(self):
        pending = API.submit(
            _request(mode=MODE.MICRO_LIVE),
            _state(mode=MODE.MICRO_LIVE)).payload.snapshot
        response = API.cancel(
            _request(mode=MODE.MICRO_LIVE,
                     operation=OP.CANCEL),
            _state(mode=MODE.MICRO_LIVE, micro_live=pending,
                   micro_live_references=_micro_references(
                       8, "snap-4")))
        assert response.decision is API_DECISION.DENIED
        assert response.decision_code == "TRANSITION_DENIED"

    def test_cancel_fail_safe_without_gates(self):
        # A06 sözleşmesi: revoke fail-safe'tir; acil yetki
        # iptali policy/kill switch kapısına BAĞLANAMAZ.
        pending = API.submit(
            _request(mode=MODE.MICRO_LIVE),
            _state(mode=MODE.MICRO_LIVE)).payload.snapshot
        approved = MICRO_SERVICE.approve(
            pending, "auth-1",
            MicroLiveApproval(approval_reference="app-2",
                              approver_reference="owner-1",
                              authorization_reference="auth-1",
                              expiry_sequence=50,
                              logical_sequence=2),
            RiskDecision(decision=RiskDecisionType.ALLOW),
            MICRO_POLICY, KS_ENABLED,
            _micro_references(6, "snap-2")).snapshot
        response = API.cancel(
            _request(mode=MODE.MICRO_LIVE,
                     operation=OP.CANCEL, policy=None,
                     kill_switch=None),
            _state(mode=MODE.MICRO_LIVE, micro_live=approved,
                   micro_live_references=_micro_references(
                       7, "snap-3")))
        assert response.decision is API_DECISION.ACCEPTED
        assert response.decision_code == \
            "AUTHORIZATION_REVOKED"

    def test_cancel_fail_safe_with_disabled_kill_switch(self):
        pending = API.submit(
            _request(mode=MODE.MICRO_LIVE),
            _state(mode=MODE.MICRO_LIVE)).payload.snapshot
        approved = MICRO_SERVICE.approve(
            pending, "auth-1",
            MicroLiveApproval(approval_reference="app-3",
                              approver_reference="owner-1",
                              authorization_reference="auth-1",
                              expiry_sequence=50,
                              logical_sequence=2),
            RiskDecision(decision=RiskDecisionType.ALLOW),
            MICRO_POLICY, KS_ENABLED,
            _micro_references(6, "snap-2")).snapshot
        response = API.cancel(
            _request(mode=MODE.MICRO_LIVE,
                     operation=OP.CANCEL,
                     kill_switch=KS_DISABLED),
            _state(mode=MODE.MICRO_LIVE, micro_live=approved,
                   micro_live_references=_micro_references(
                       7, "snap-3")))
        assert response.decision_code == \
            "AUTHORIZATION_REVOKED"

    @pytest.mark.parametrize("mode", [MODE.PAPER, MODE.SHADOW])
    def test_paper_shadow_cancel_still_gated(self, mode):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            API.cancel(
                _request(mode=mode, operation=OP.CANCEL,
                         execution=None, observation=None,
                         policy=None),
                _state(mode=mode))
        assert str(info.value) == "MISSING_API_FIELD:policy"

    def test_submit_wrong_mode_in_payload_denied(self):
        bad = MicroLiveRequest(
            authorization_reference="auth-9",
            symbol="BTCUSDT", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=D("1"),
            maximum_notional=D("100"),
            execution_mode=MODE.PAPER, expiry_sequence=100,
            scope=MicroLiveScope(symbol="BTCUSDT",
                                 side=OrderSide.BUY,
                                 order_type=OrderType.LIMIT),
            logical_sequence=1)
        response = API.submit(
            _request(mode=MODE.MICRO_LIVE,
                     micro_live_request=bad),
            _state(mode=MODE.MICRO_LIVE))
        assert response.decision is API_DECISION.DENIED
        assert response.decision_code == "MODE_DENIED"


class TestNoExchangeWrite:
    @pytest.fixture
    def broker_calls(self, monkeypatch):
        calls = []
        original_submit = PaperBroker.submit
        original_cancel = PaperBroker.cancel

        def counting_submit(self, *args, **kwargs):
            calls.append("submit")
            return original_submit(self, *args, **kwargs)

        def counting_cancel(self, *args, **kwargs):
            calls.append("cancel")
            return original_cancel(self, *args, **kwargs)

        monkeypatch.setattr(PaperBroker, "submit",
                            counting_submit)
        monkeypatch.setattr(PaperBroker, "cancel",
                            counting_cancel)
        return calls

    def test_denied_submit_never_reaches_broker(
            self, broker_calls):
        make_api(REJECT).submit(_request(), _state())
        assert broker_calls == []

    def test_kill_switch_denial_never_reaches_broker(
            self, broker_calls):
        API.submit(_request(kill_switch=KS_DISABLED), _state())
        assert broker_calls == []

    def test_micro_live_never_reaches_broker(
            self, broker_calls):
        API.submit(_request(mode=MODE.MICRO_LIVE),
                   _state(mode=MODE.MICRO_LIVE))
        assert broker_calls == []

    def test_reads_never_reach_broker(self, broker_calls):
        for method in READ_METHODS:
            getattr(API, method)(
                _request(operation=METHOD_OPERATION[method]),
                _state())
        assert broker_calls == []


class TestReadOperations:
    @pytest.mark.parametrize("mode",
                             [MODE.PAPER, MODE.SHADOW])
    @pytest.mark.parametrize("method,code", [
        ("positions", "POSITIONS_REPORTED"),
        ("orders", "ORDERS_REPORTED"),
        ("executions", "EXECUTIONS_REPORTED")])
    def test_ledger_reads(self, mode, method, code):
        response = getattr(API, method)(
            _request(mode=mode,
                     operation=METHOD_OPERATION[method]),
            _state(mode=mode))
        assert response.decision is API_DECISION.REPORTED
        assert response.decision_code == code
        assert response.payload == ()

    @pytest.mark.parametrize("method", ["positions", "orders",
                                        "executions"])
    def test_micro_live_reads_fail_closed(self, method):
        with pytest.raises(
                ControlledExecutionAPIModeError) as info:
            getattr(API, method)(
                _request(mode=MODE.MICRO_LIVE,
                         operation=METHOD_OPERATION[method]),
                _state(mode=MODE.MICRO_LIVE))
        assert "UNSUPPORTED_OPERATION:MICRO_LIVE" in \
            str(info.value)

    @pytest.mark.parametrize("mode", list(MODE))
    def test_statistics_reported(self, mode):
        response = API.statistics(
            _request(mode=mode, operation=OP.STATISTICS),
            _state(mode=mode))
        assert response.decision is API_DECISION.REPORTED
        assert isinstance(response.statistics,
                          ControlledExecutionStatistics)
        assert response.statistics.mode is mode

    @pytest.mark.parametrize("mode", list(MODE))
    def test_status_reported(self, mode):
        response = API.status(
            _request(mode=mode, operation=OP.STATUS),
            _state(mode=mode))
        assert isinstance(response.status,
                          ControlledExecutionStatus)
        assert response.status.mode is mode
        assert response.status.alive is True

    @pytest.mark.parametrize("mode", list(MODE))
    def test_heartbeat_reported(self, mode):
        response = API.heartbeat(
            _request(mode=mode, operation=OP.HEARTBEAT),
            _state(mode=mode))
        assert response.decision_code == "HEARTBEAT_REPORTED"
        assert response.payload is not None

    def test_statistics_zero_counts_on_fresh_state(self):
        response = API.statistics(
            _request(operation=OP.STATISTICS), _state())
        assert response.statistics.total_orders == 0
        assert response.statistics.total_executions == 0


class TestValidation:
    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_non_request_rejected(self, method):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            getattr(API, method)("not-a-request", _state())
        assert str(info.value) == "INVALID_API_FIELD:request"

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_operation_mismatch_rejected(self, method):
        wrong = OP.SUBMIT if method != "submit" else OP.CANCEL
        request = _request(operation=wrong) if \
            wrong is not OP.SUBMIT else _request()
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            getattr(API, method)(request, _state())
        assert str(info.value) == "INVALID_API_FIELD:operation"

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_non_state_rejected(self, method):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            getattr(API, method)(
                _request(operation=METHOD_OPERATION[method]),
                "not-a-state")
        assert str(info.value) == "INVALID_API_FIELD:state"

    @pytest.mark.parametrize("field", ["policy", "kill_switch",
                                       "order_reference",
                                       "execution"])
    def test_paper_submit_missing_field(self, field):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            API.submit(_request(**{field: None}), _state())
        assert str(info.value) == f"MISSING_API_FIELD:{field}"

    @pytest.mark.parametrize("field", ["observation"])
    def test_shadow_submit_missing_field(self, field):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            API.submit(
                _request(mode=MODE.SHADOW, **{field: None}),
                _state(mode=MODE.SHADOW))
        assert str(info.value) == f"MISSING_API_FIELD:{field}"

    @pytest.mark.parametrize("field", ["micro_live_request",
                                       "micro_live_limits"])
    def test_micro_submit_missing_field(self, field):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            API.submit(
                _request(mode=MODE.MICRO_LIVE,
                         **{field: None}),
                _state(mode=MODE.MICRO_LIVE))
        assert str(info.value) == f"MISSING_API_FIELD:{field}"

    @pytest.mark.parametrize("field", ["ledger",
                                       "paper_references"])
    def test_paper_submit_missing_state(self, field):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            API.submit(_request(), _state(**{field: None}))
        assert str(info.value) == f"MISSING_API_FIELD:{field}"

    def test_shadow_submit_missing_shadow_state(self):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            API.submit(_request(mode=MODE.SHADOW),
                       _state(mode=MODE.SHADOW, shadow=None))
        assert str(info.value) == "MISSING_API_FIELD:shadow"

    @pytest.mark.parametrize("field", [
        "micro_live", "micro_live_references"])
    def test_micro_submit_missing_state(self, field):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            API.submit(_request(mode=MODE.MICRO_LIVE),
                       _state(mode=MODE.MICRO_LIVE,
                              **{field: None}))
        assert str(info.value) == f"MISSING_API_FIELD:{field}"

    def test_micro_cancel_missing_order_reference(self):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            API.cancel(
                _request(mode=MODE.MICRO_LIVE,
                         operation=OP.CANCEL,
                         order_reference=None),
                _state(mode=MODE.MICRO_LIVE))
        assert str(info.value) == \
            "MISSING_API_FIELD:order_reference"

    @pytest.mark.parametrize("method", ["positions", "orders",
                                        "executions",
                                        "heartbeat", "status",
                                        "statistics"])
    def test_paper_read_missing_ledger(self, method):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            getattr(API, method)(
                _request(operation=METHOD_OPERATION[method]),
                _state(ledger=None))
        assert str(info.value) == "MISSING_API_FIELD:ledger"

    def test_api_requires_router(self):
        with pytest.raises(
                ControlledExecutionAPIConfigurationError):
            ControlledExecutionAPI("not-a-router")

    def test_api_immutable(self):
        with pytest.raises(
                ControlledExecutionAPIConfigurationError):
            API.new_attribute = 1


class TestResponseEnvelope:
    def test_write_audit_trail(self):
        response = API.submit(_request(), _state())
        codes = [entry.audit_code for entry in response.audit]
        assert codes == ["API_REQUEST_VALIDATED",
                         "API_MODE_ROUTED:PAPER",
                         "API_RESULT_MAPPED"]

    def test_read_audit_trail(self):
        response = API.orders(
            _request(operation=OP.ORDERS), _state())
        codes = [entry.audit_code for entry in response.audit]
        assert codes == ["API_REQUEST_VALIDATED",
                         "API_MODE_ROUTED:PAPER",
                         "API_READ_COMPLETED"]

    def test_response_carries_request_identity(self):
        response = API.submit(_request(), _state())
        assert response.request_reference == "api-req-1"
        assert response.logical_sequence == 9

    def test_response_frozen(self):
        response = API.submit(_request(), _state())
        with pytest.raises(FrozenInstanceError):
            response.decision = API_DECISION.DENIED

    def test_payload_result_frozen(self):
        response = API.submit(_request(), _state())
        with pytest.raises(FrozenInstanceError):
            response.payload.decision = None


REQUEST_FIELDS = ["mode", "operation", "request_reference",
                  "logical_sequence", "policy", "kill_switch",
                  "execution", "order_reference"]
STATE_FIELDS = ["ledger", "shadow", "micro_live",
                "paper_references", "micro_live_references"]


class TestModelContracts:
    @pytest.mark.parametrize("field,value", [
        ("mode", "PAPER"), ("operation", "SUBMIT"),
        ("request_reference", ""), ("request_reference", None),
        ("logical_sequence", -1), ("logical_sequence", True),
        ("policy", "policy"), ("kill_switch", "ks"),
        ("execution", "exe"), ("order_reference", ""),
        ("observation", "obs"), ("micro_live_request", "req"),
        ("micro_live_limits", "lim")])
    def test_request_contract(self, field, value):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            _request(**{field: value})
        assert str(info.value) == f"INVALID_API_FIELD:{field}"

    @pytest.mark.parametrize("field", STATE_FIELDS)
    def test_state_contract(self, field):
        with pytest.raises(
                ControlledExecutionAPIContractError) as info:
            ControlledExecutionState(**{field: "bad"})
        assert str(info.value) == f"INVALID_API_FIELD:{field}"

    @pytest.mark.parametrize("field,value", [
        ("audit_code", ""), ("request_reference", ""),
        ("logical_sequence", -1)])
    def test_audit_contract(self, field, value):
        values = dict(audit_code="API_REQUEST_VALIDATED",
                      request_reference="r", logical_sequence=1)
        values[field] = value
        with pytest.raises(ControlledExecutionAPIContractError):
            ControlledExecutionAudit(**values)

    @pytest.mark.parametrize("field,value", [
        ("mode", "PAPER"), ("alive", 1), ("order_count", -1),
        ("execution_count", None),
        ("authorization_count", "1"),
        ("logical_sequence", -5)])
    def test_status_contract(self, field, value):
        values = dict(mode=MODE.PAPER, alive=True)
        values[field] = value
        with pytest.raises(ControlledExecutionAPIContractError):
            ControlledExecutionStatus(**values)

    @pytest.mark.parametrize("field,value", [
        ("mode", None), ("total_orders", -1),
        ("total_executions", "0"), ("total_denied", 1.0),
        ("total_cancels", True), ("logical_sequence", -1)])
    def test_statistics_contract(self, field, value):
        values = dict(mode=MODE.PAPER)
        values[field] = value
        with pytest.raises(ControlledExecutionAPIContractError):
            ControlledExecutionStatistics(**values)

    @pytest.mark.parametrize("field,value", [
        ("decision_code", ""), ("request_reference", ""),
        ("logical_sequence", -1), ("execution_reference", ""),
        ("ledger_reference", ""), ("audit", [1]),
        ("statistics", "stats"), ("status", "status"),
        ("decision", "ACCEPTED"), ("mode", "PAPER"),
        ("operation", "SUBMIT")])
    def test_response_contract(self, field, value):
        values = dict(mode=MODE.PAPER, operation=OP.SUBMIT,
                      decision=API_DECISION.ACCEPTED,
                      decision_code="ORDER_EXECUTED",
                      request_reference="r",
                      logical_sequence=1)
        values[field] = value
        with pytest.raises(ControlledExecutionAPIContractError):
            ControlledExecutionResponse(**values)


IMMUTABLE_CASES = [
    (lambda: _request(), "mode"),
    (lambda: _request(), "operation"),
    (lambda: _request(), "execution"),
    (lambda: _request(), "logical_sequence"),
    (lambda: _state(), "ledger"),
    (lambda: _state(), "paper_references"),
    (lambda: ControlledExecutionAudit(
        audit_code="A", request_reference="r",
        logical_sequence=1), "audit_code"),
    (lambda: ControlledExecutionStatus(
        mode=MODE.PAPER, alive=True), "alive"),
    (lambda: ControlledExecutionStatistics(
        mode=MODE.PAPER), "total_orders")]


class TestImmutability:
    @pytest.mark.parametrize("factory,field", IMMUTABLE_CASES)
    def test_models_frozen(self, factory, field):
        instance = factory()
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field, "mutated")
