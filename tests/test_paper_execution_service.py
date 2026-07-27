"""Mission 2100 — Agent 04: Kağıt Yürütme Servisi testleri.

Boru hattı sırası, PAPER-only zorlaması, risk/izin/kill switch
yolları, reddedilen yollarda SIFIR ve onaylı yollarda TAM BİR
broker çağrısı, defter geçişleri, değişmez sonuçlar ve denetim.
"""

from __future__ import annotations

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
from execution_enums import (OrderSide, OrderState, OrderType,
                             TimeInForce)
from execution_kill_switch_models import (KillSwitchReason,
                                          KillSwitchSnapshot,
                                          KillSwitchState)
from execution_models import ExecutionRequest
from execution_risk_models import RiskDecision, RiskDecisionType
from paper_broker import PaperBroker
from paper_execution_errors import (
    PaperExecutionConfigurationError,
    PaperExecutionContractError, PaperExecutionError,
    PaperExecutionRiskError, PaperExecutionStateError)
from paper_execution_mapper import PaperExecutionMapper
from paper_execution_models import (PaperAuditStage,
                                    PaperExecutionDecision,
                                    PaperExecutionDecisionCode,
                                    PaperExecutionOperation,
                                    PaperExecutionReferences,
                                    PaperExecutionServiceResult)
from paper_execution_service import (PaperExecutionService,
                                     PaperRiskEvaluator,
                                     StaticRiskEvaluator)
from paper_models import PaperLedgerSnapshot
from runtime_enums import HeartbeatStatus

BROKER = PaperBroker(known_symbols=("BTCUSDT", "ETHUSDT"))
FOUNDATION = ControlledExecutionFoundation(ExtensionRegistry())

ALLOW = StaticRiskEvaluator(RiskDecision(
    decision=RiskDecisionType.ALLOW))
REJECT = StaticRiskEvaluator(RiskDecision(
    decision=RiskDecisionType.REJECT, code="LIMIT_BREACH"))
REDUCE = StaticRiskEvaluator(RiskDecision(
    decision=RiskDecisionType.REDUCE_SIZE,
    approved_quantity=D("1")))
CONFIRM = StaticRiskEvaluator(RiskDecision(
    decision=RiskDecisionType.REQUIRE_CONFIRMATION))

PAPER_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.PAPER,
    simulated_fill_allowed=True)
SHADOW_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.SHADOW,
    broker_read_allowed=True)
MICRO_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.MICRO_LIVE,
    exchange_write_allowed=True,
    broker_read_allowed=True,
    human_confirmation_required=True,
    explicit_authorization_required=True,
    authorization_reference="auth-1")
WRITE_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.PAPER,
    simulated_fill_allowed=True,
    exchange_write_allowed=True)
NO_FILL_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.PAPER,
    simulated_fill_allowed=False)
CONFLICT_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.PAPER,
    simulated_fill_allowed=True,
    broker_read_allowed=True)

KS_ENABLED = KillSwitchSnapshot(
    state=KillSwitchState.ENABLED,
    reason=KillSwitchReason.MANUAL, timestamp=1, sequence_id=1)
KS_DISABLED = KillSwitchSnapshot(
    state=KillSwitchState.DISABLED,
    reason=KillSwitchReason.MANUAL, timestamp=2, sequence_id=2)
KS_LOCKED = KillSwitchSnapshot(
    state=KillSwitchState.LOCKED,
    reason=KillSwitchReason.RISK_LIMIT, timestamp=3,
    sequence_id=3)
KS_MAINTENANCE = KillSwitchSnapshot(
    state=KillSwitchState.MAINTENANCE,
    reason=KillSwitchReason.DEPLOYMENT, timestamp=4,
    sequence_id=4)

ALL_STAGES = ("REQUEST_VALIDATED", "MODE_VALIDATED",
              "RISK_EVALUATED", "PERMISSION_EVALUATED",
              "KILL_SWITCH_CHECKED", "PAPER_BROKER_INVOKED",
              "LEDGER_UPDATED", "RESULT_MAPPED")


def _service(risk=ALLOW, broker=BROKER):
    return PaperExecutionService(broker=broker,
                                 foundation=FOUNDATION,
                                 risk_evaluator=risk)


def _snapshot(cash="1000"):
    return PaperLedgerSnapshot(
        quote_asset="USDT", initial_cash=D(cash), cash=D(cash),
        reserved_cash=D("0"), realized_pnl=D("0"),
        commission_paid=D("0"))


def _request(symbol="BTCUSDT", quantity="2", price="100",
             side=OrderSide.BUY):
    return ExecutionRequest(
        symbol=symbol, side=side, order_type=OrderType.LIMIT,
        quantity=D(quantity), time_in_force=TimeInForce.GTC,
        price=D(price))


def _references(sequence=7):
    return PaperExecutionReferences(
        request_reference="req-1",
        previous_ledger_reference="ledger-0",
        current_ledger_reference="ledger-1",
        risk_decision_reference="risk-1",
        kill_switch_reference="ks-1",
        execution_result_reference="res-1",
        logical_sequence=sequence)


def _submit(service=None, snapshot=None, request=None,
            order_reference="ord-1", policy=PAPER_POLICY,
            kill_switch=KS_ENABLED, references=None):
    service = service or _service()
    return service.submit_order(
        snapshot if snapshot is not None else _snapshot(),
        request if request is not None else _request(),
        order_reference, policy, kill_switch,
        references or _references())


@pytest.fixture
def broker_calls(monkeypatch):
    calls = []
    original_submit = PaperBroker.submit
    original_cancel = PaperBroker.cancel

    def counting_submit(self, *args, **kwargs):
        calls.append("submit")
        return original_submit(self, *args, **kwargs)

    def counting_cancel(self, *args, **kwargs):
        calls.append("cancel")
        return original_cancel(self, *args, **kwargs)

    monkeypatch.setattr(PaperBroker, "submit", counting_submit)
    monkeypatch.setattr(PaperBroker, "cancel", counting_cancel)
    return calls


# ── Yapılandırma ─────────────────────────────────────────────────────

class TestConfiguration:
    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_invalid_broker_rejected(self, bad):
        with pytest.raises(PaperExecutionConfigurationError):
            PaperExecutionService(broker=bad,
                                  foundation=FOUNDATION,
                                  risk_evaluator=ALLOW)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_invalid_foundation_rejected(self, bad):
        with pytest.raises(PaperExecutionConfigurationError):
            PaperExecutionService(broker=BROKER,
                                  foundation=bad,
                                  risk_evaluator=ALLOW)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_invalid_evaluator_rejected(self, bad):
        with pytest.raises(PaperExecutionConfigurationError):
            PaperExecutionService(broker=BROKER,
                                  foundation=FOUNDATION,
                                  risk_evaluator=bad)

    @pytest.mark.parametrize("bad", ["x", 1, object()])
    def test_invalid_mapper_rejected(self, bad):
        with pytest.raises(PaperExecutionConfigurationError):
            PaperExecutionService(broker=BROKER,
                                  foundation=FOUNDATION,
                                  risk_evaluator=ALLOW,
                                  mapper=bad)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_static_evaluator_requires_decision(self, bad):
        with pytest.raises(PaperExecutionConfigurationError):
            StaticRiskEvaluator(bad)

    def test_abstract_evaluator_unusable(self):
        with pytest.raises(NotImplementedError):
            PaperRiskEvaluator().evaluate(_request())

    def test_service_is_frozen(self):
        service = _service()
        with pytest.raises(Exception):
            service.broker = BROKER


# ── Sözleşme doğrulaması ─────────────────────────────────────────────

class TestContractValidation:
    @pytest.mark.parametrize("bad", [None, "x", 1, object(), ()])
    def test_bad_snapshot_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            _service().submit_order(bad, _request(), "ord-1",
                                    PAPER_POLICY, KS_ENABLED,
                                    _references())

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_request_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            _service().submit_order(
                _snapshot(), bad, "ord-1", PAPER_POLICY,
                KS_ENABLED, _references())

    @pytest.mark.parametrize("bad", [None, "", "  ", 1, True,
                                     object()])
    def test_bad_order_reference_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            _submit(order_reference=bad)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_references_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            _service().submit_order(_snapshot(), _request(),
                                    "ord-1", PAPER_POLICY,
                                    KS_ENABLED, bad)

    def test_missing_price_rejected(self):
        request = ExecutionRequest(
            symbol="BTCUSDT", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=D("1"),
            time_in_force=TimeInForce.GTC, price=None)
        with pytest.raises(PaperExecutionContractError):
            _submit(request=request)

    @pytest.mark.parametrize("bad", [None, "x", 1, object(), ()])
    def test_cancel_bad_snapshot_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            _service().cancel_order(bad, "ord-1", PAPER_POLICY,
                                    KS_ENABLED, _references())

    @pytest.mark.parametrize("bad", [None, "", 1, object()])
    def test_cancel_bad_reference_rejected(self, bad):
        with pytest.raises(PaperExecutionContractError):
            _service().cancel_order(_snapshot(), bad,
                                    PAPER_POLICY, KS_ENABLED,
                                    _references())

    def test_contract_errors_leave_broker_untouched(
            self, broker_calls):
        with pytest.raises(PaperExecutionContractError):
            _service().submit_order(None, _request(), "ord-1",
                                    PAPER_POLICY, KS_ENABLED,
                                    _references())
        assert broker_calls == []


# ── PAPER-only zorlaması ─────────────────────────────────────────────

class TestPaperOnlyEnforcement:
    @pytest.mark.parametrize("policy", [SHADOW_POLICY,
                                        MICRO_POLICY])
    def test_non_paper_mode_denied(self, policy, broker_calls):
        result = _submit(policy=policy)
        assert result.decision is PaperExecutionDecision.DENIED
        assert result.decision_code is \
            PaperExecutionDecisionCode.MODE_DENIED
        assert broker_calls == []

    @pytest.mark.parametrize("policy", [None, "PAPER", 1,
                                        object()])
    def test_missing_or_unknown_policy_denied(self, policy,
                                              broker_calls):
        result = _submit(policy=policy)
        assert result.decision_code is \
            PaperExecutionDecisionCode.MODE_DENIED
        assert broker_calls == []

    def test_no_fallback_ledger_unchanged(self):
        snapshot = _snapshot()
        result = _submit(snapshot=snapshot,
                         policy=SHADOW_POLICY)
        assert result.ledger is snapshot

    @pytest.mark.parametrize("policy", [SHADOW_POLICY,
                                        MICRO_POLICY, None])
    def test_cancel_non_paper_denied(self, policy,
                                     broker_calls):
        result = _service().cancel_order(
            _snapshot(), "ord-1", policy, KS_ENABLED,
            _references())
        assert result.decision_code is \
            PaperExecutionDecisionCode.MODE_DENIED
        assert broker_calls == []

    def test_mode_denial_audit_stages(self):
        result = _submit(policy=SHADOW_POLICY)
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED")


# ── Risk yolları ─────────────────────────────────────────────────────

class TestRiskPaths:
    def test_allow_continues_to_execution(self, broker_calls):
        result = _submit(service=_service(ALLOW))
        assert result.executed
        assert broker_calls == ["submit"]

    def test_reject_denies(self, broker_calls):
        result = _submit(service=_service(REJECT))
        assert result.decision is PaperExecutionDecision.DENIED
        assert result.decision_code is \
            PaperExecutionDecisionCode.RISK_REJECTED
        assert broker_calls == []

    def test_reject_audit_stages(self):
        result = _submit(service=_service(REJECT))
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED",
            "RISK_EVALUATED")

    def test_reduce_size_recommendation_only(self,
                                             broker_calls):
        result = _submit(service=_service(REDUCE))
        assert result.decision is \
            PaperExecutionDecision.RECOMMENDATION_ONLY
        assert result.decision_code is \
            PaperExecutionDecisionCode.RISK_REDUCE_SIZE
        assert result.recommended_quantity == D("1")
        assert broker_calls == []

    def test_reduce_size_no_automatic_resizing(self):
        snapshot = _snapshot()
        result = _submit(snapshot=snapshot,
                         service=_service(REDUCE))
        assert result.ledger is snapshot
        assert result.ledger.orders == ()

    def test_require_confirmation_denied(self, broker_calls):
        result = _submit(service=_service(CONFIRM))
        assert result.decision is PaperExecutionDecision.DENIED
        assert result.decision_code is \
            PaperExecutionDecisionCode.RISK_CONFIRMATION_REQUIRED
        assert broker_calls == []

    def test_evaluator_failure_contained(self, broker_calls):
        class Failing(PaperRiskEvaluator):
            def evaluate(self, request):
                raise RuntimeError("iç ayrıntı")

        with pytest.raises(PaperExecutionRiskError) as exc:
            _submit(service=_service(Failing()))
        assert "PAPER_EXECUTION_RISK:EVALUATOR_FAILURE" in \
            str(exc.value)
        assert "iç ayrıntı" not in str(exc.value)
        assert broker_calls == []

    def test_invalid_risk_decision_contained(self,
                                             broker_calls):
        class Wrong(PaperRiskEvaluator):
            def evaluate(self, request):
                return "ALLOW"

        with pytest.raises(PaperExecutionRiskError) as exc:
            _submit(service=_service(Wrong()))
        assert "INVALID_RISK_DECISION" in str(exc.value)
        assert broker_calls == []

    def test_risk_receives_canonical_request(self):
        class Capturing(PaperRiskEvaluator):
            captured = ()

            def evaluate(self, request):
                Capturing.captured = Capturing.captured + \
                    (request,)
                return RiskDecision(
                    decision=RiskDecisionType.ALLOW)

        request = _request()
        _submit(request=request, service=_service(Capturing()))
        assert Capturing.captured == (request,)


# ── PAPER politika doğrulaması (risk'ten ÖNCE) ──────────────────────

class TestPaperPolicyValidation:
    @pytest.mark.parametrize("policy", [WRITE_POLICY,
                                        NO_FILL_POLICY])
    def test_invalid_paper_policy_denied(self, policy,
                                         broker_calls):
        result = _submit(policy=policy)
        assert result.decision is PaperExecutionDecision.DENIED
        assert result.decision_code is \
            PaperExecutionDecisionCode.POLICY_DENIED
        assert broker_calls == []

    def test_policy_validated_before_risk(self):
        class Recording(PaperRiskEvaluator):
            invoked = 0

            def evaluate(self, request):
                Recording.invoked = Recording.invoked + 1
                return RiskDecision(
                    decision=RiskDecisionType.ALLOW)

        _submit(policy=WRITE_POLICY,
                service=_service(Recording()))
        assert Recording.invoked == 0

    def test_policy_denial_stages_before_risk(self):
        result = _submit(policy=WRITE_POLICY)
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED")

    @pytest.mark.parametrize("policy", [WRITE_POLICY,
                                        NO_FILL_POLICY])
    def test_cancel_invalid_paper_policy_denied(
            self, policy, broker_calls):
        result = _service().cancel_order(
            _snapshot(), "ord-1", policy, KS_ENABLED,
            _references())
        assert result.decision_code is \
            PaperExecutionDecisionCode.POLICY_DENIED
        assert broker_calls == []


# ── İzin kapısı ──────────────────────────────────────────────────────

class TestPermissionGate:
    def test_policy_conflict_denied(self, broker_calls):
        result = _submit(policy=CONFLICT_POLICY)
        assert result.decision_code is \
            PaperExecutionDecisionCode.POLICY_DENIED
        assert broker_calls == []

    def test_policy_denial_audit_stages(self):
        result = _submit(policy=CONFLICT_POLICY)
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED",
            "RISK_EVALUATED", "PERMISSION_EVALUATED")

    def test_permission_gate_runs_after_risk(self,
                                             broker_calls):
        result = _submit(service=_service(REJECT),
                         policy=CONFLICT_POLICY)
        assert result.decision_code is \
            PaperExecutionDecisionCode.RISK_REJECTED
        assert broker_calls == []

    def test_cancel_policy_conflict_denied(self, broker_calls):
        result = _service().cancel_order(
            _snapshot(), "ord-1", CONFLICT_POLICY, KS_ENABLED,
            _references())
        assert result.decision_code is \
            PaperExecutionDecisionCode.POLICY_DENIED
        assert broker_calls == []


# ── Kill switch ──────────────────────────────────────────────────────

class TestKillSwitch:
    @pytest.mark.parametrize("kill_switch", [
        KS_DISABLED, KS_LOCKED, KS_MAINTENANCE])
    def test_non_enabled_denied(self, kill_switch,
                                broker_calls):
        result = _submit(kill_switch=kill_switch)
        assert result.decision is PaperExecutionDecision.DENIED
        assert result.decision_code is \
            PaperExecutionDecisionCode.KILL_SWITCH_DENIED
        assert broker_calls == []

    @pytest.mark.parametrize("kill_switch", [None, "ENABLED",
                                             1, object()])
    def test_missing_snapshot_denied(self, kill_switch,
                                     broker_calls):
        result = _submit(kill_switch=kill_switch)
        assert result.decision_code is \
            PaperExecutionDecisionCode.KILL_SWITCH_DENIED
        assert broker_calls == []

    def test_kill_switch_denial_audit_stages(self):
        result = _submit(kill_switch=KS_DISABLED)
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED",
            "RISK_EVALUATED", "PERMISSION_EVALUATED",
            "KILL_SWITCH_CHECKED")

    @pytest.mark.parametrize("kill_switch", [KS_DISABLED,
                                             KS_LOCKED, None])
    def test_cancel_non_enabled_denied(self, kill_switch,
                                       broker_calls):
        result = _service().cancel_order(
            _snapshot(), "ord-1", PAPER_POLICY, kill_switch,
            _references())
        assert result.decision_code is \
            PaperExecutionDecisionCode.KILL_SWITCH_DENIED
        assert broker_calls == []


# ── Onaylı gönderim yolu ─────────────────────────────────────────────

class TestApprovedSubmit:
    def test_exactly_one_broker_call(self, broker_calls):
        _submit()
        assert broker_calls == ["submit"]

    def test_ledger_transition(self):
        result = _submit()
        assert result.ledger.cash == D("800")
        assert result.ledger.position_for(
            "BTCUSDT").quantity == D("2")
        assert result.ledger.audit()

    def test_input_snapshot_untouched(self):
        snapshot = _snapshot()
        _submit(snapshot=snapshot)
        assert snapshot.cash == D("1000")
        assert snapshot.orders == ()

    def test_full_audit_stage_order(self):
        result = _submit()
        assert result.audit_stage_codes() == ALL_STAGES

    def test_result_fields(self):
        result = _submit()
        assert result.operation is \
            PaperExecutionOperation.SUBMIT_ORDER
        assert result.decision is \
            PaperExecutionDecision.EXECUTED
        assert result.decision_code is \
            PaperExecutionDecisionCode.ORDER_EXECUTED
        assert result.order_reference == "ord-1"

    def test_reference_preservation(self):
        result = _submit()
        assert result.previous_ledger_reference == "ledger-0"
        assert result.current_ledger_reference == "ledger-1"
        assert result.risk_decision_reference == "risk-1"
        assert result.kill_switch_reference == "ks-1"
        assert result.execution_result_reference == "res-1"
        assert result.logical_sequence == 7

    def test_execution_references_from_ledger(self):
        result = _submit()
        assert result.execution_references == (
            result.ledger.executions[0].execution_reference,)

    def test_canonical_execution_result(self):
        result = _submit()
        canonical = result.execution_result
        assert canonical.order.order_id == "ord-1"
        assert canonical.order.state is OrderState.FILLED
        assert canonical.fills[0].price == D("100")
        assert canonical.fills[0].quantity == D("2")

    def test_execution_price_is_submitted_price(self):
        result = _submit(request=_request(price="123.45"))
        assert result.ledger.executions[0].price == D("123.45")

    def test_result_immutable(self):
        result = _submit()
        with pytest.raises(Exception):
            result.decision = PaperExecutionDecision.DENIED

    def test_audit_records_metadata(self):
        result = _submit()
        record = result.audit_records[0]
        assert record.audit_reference == \
            "req-1:REQUEST_VALIDATED"
        assert record.subject_reference == "ord-1"
        assert record.logical_sequence == 7

    def test_deterministic_submission(self):
        assert _submit() == _submit()

    def test_sell_close_realizes_pnl(self):
        opened = _submit().ledger
        result = _submit(
            snapshot=opened,
            request=_request(side=OrderSide.SELL,
                             price="110"),
            order_reference="ord-2")
        assert result.ledger.realized_pnl == D("20")
        assert result.ledger.audit()

    def test_duplicate_request_rejected(self, broker_calls):
        opened = _submit().ledger
        with pytest.raises(PaperExecutionStateError) as exc:
            _submit(snapshot=opened)
        assert "DUPLICATE_ORDER_ID" in str(exc.value)
        assert broker_calls == ["submit", "submit"]

    def test_unknown_symbol_contained(self, broker_calls):
        with pytest.raises(PaperExecutionStateError) as exc:
            _submit(request=_request(symbol="DOGEUSDT"))
        assert "PAPER_EXECUTION_STATE:" in str(exc.value)
        assert "UNKNOWN_SYMBOL" in str(exc.value)
        assert broker_calls == ["submit"]

    def test_insufficient_cash_contained(self):
        with pytest.raises(PaperExecutionStateError) as exc:
            _submit(request=_request(quantity="100",
                                     price="100"))
        assert "INSUFFICIENT_CASH" in str(exc.value)

    def test_no_raw_paper_error_crosses_boundary(self):
        try:
            _submit(request=_request(symbol="DOGEUSDT"))
            raise AssertionError("hata bekleniyordu")
        except PaperExecutionError:
            pass

    def test_no_retry_after_rejection(self, broker_calls):
        with pytest.raises(PaperExecutionStateError):
            _submit(request=_request(symbol="DOGEUSDT"))
        assert broker_calls == ["submit"]

    def test_unexpected_broker_failure_contained(
            self, monkeypatch):
        def exploding(self, *args, **kwargs):
            raise RuntimeError("iç broker ayrıntısı")

        monkeypatch.setattr(PaperBroker, "submit", exploding)
        with pytest.raises(PaperExecutionStateError) as exc:
            _submit()
        assert "INTERNAL_FAILURE" in str(exc.value)
        assert "iç broker ayrıntısı" not in str(exc.value)

    def test_unexpected_cancel_failure_contained(
            self, monkeypatch):
        def exploding(self, *args, **kwargs):
            raise ValueError("iç ayrıntı")

        monkeypatch.setattr(PaperBroker, "cancel", exploding)
        with pytest.raises(PaperExecutionStateError) as exc:
            _service().cancel_order(
                _snapshot(), "ord-1", PAPER_POLICY,
                KS_ENABLED, _references())
        assert "INTERNAL_FAILURE" in str(exc.value)


# ── İptal yolu ───────────────────────────────────────────────────────

class TestCancelPath:
    def test_unknown_order_contained(self, broker_calls):
        with pytest.raises(PaperExecutionStateError) as exc:
            _service().cancel_order(
                _snapshot(), "ord-404", PAPER_POLICY,
                KS_ENABLED, _references())
        assert "UNKNOWN_ORDER" in str(exc.value)
        assert broker_calls == ["cancel"]

    def test_filled_order_cancel_contained(self,
                                           broker_calls):
        opened = _submit().ledger
        broker_calls.clear()
        with pytest.raises(PaperExecutionStateError) as exc:
            _service().cancel_order(
                opened, "ord-1", PAPER_POLICY, KS_ENABLED,
                _references())
        assert "INVALID_STATE" in str(exc.value)
        assert broker_calls == ["cancel"]

    def test_cancel_risk_exemption_codified(self):
        """İptal riski BİLİNÇLİ muaf: istek uydurulamaz."""
        with pytest.raises(PaperExecutionStateError):
            _service(REJECT).cancel_order(
                _snapshot(), "ord-404", PAPER_POLICY,
                KS_ENABLED, _references())

    def test_exactly_one_cancel_call(self, broker_calls):
        with pytest.raises(PaperExecutionStateError):
            _service().cancel_order(
                _snapshot(), "ord-404", PAPER_POLICY,
                KS_ENABLED, _references())
        assert broker_calls.count("cancel") == 1


# ── Yan etkisiz okuma işlemleri ──────────────────────────────────────

class TestReadOperations:
    def test_account_snapshot_view(self):
        opened = _submit().ledger
        mapped = _service().get_account_snapshot(opened,
                                                 "acct-1")
        assert mapped.balances[0].free == D("800")
        assert mapped.positions[0].symbol == "BTCUSDT"

    def test_get_orders(self):
        opened = _submit().ledger
        assert _service().get_orders(opened) == opened.orders

    def test_get_executions(self):
        opened = _submit().ledger
        assert _service().get_executions(opened) == \
            opened.executions

    def test_get_positions(self):
        opened = _submit().ledger
        assert _service().get_positions(opened) == \
            opened.positions

    def test_get_statistics(self):
        opened = _submit().ledger
        statistics = _service().get_statistics(opened)
        assert statistics.orders_submitted == 1
        assert statistics.gross_notional == D("200")

    def test_heartbeat_ok(self):
        assert _service().heartbeat(_snapshot()) is \
            HeartbeatStatus.OK

    def test_heartbeat_error_on_broken_ledger(self):
        broken = PaperLedgerSnapshot(
            quote_asset="USDT", initial_cash=D("1000"),
            cash=D("999"), reserved_cash=D("0"),
            realized_pnl=D("0"), commission_paid=D("0"))
        assert _service().heartbeat(broken) is \
            HeartbeatStatus.ERROR

    @pytest.mark.parametrize("method", [
        "get_orders", "get_executions", "get_positions",
        "get_statistics", "heartbeat"])
    def test_reads_reject_bad_snapshot(self, method):
        with pytest.raises(PaperExecutionContractError):
            getattr(_service(), method)(object())

    @pytest.mark.parametrize("method", [
        "get_orders", "get_executions", "get_positions"])
    def test_reads_are_side_effect_free(self, method,
                                        broker_calls):
        snapshot = _snapshot()
        getattr(_service(), method)(snapshot)
        assert snapshot == _snapshot()
        assert broker_calls == []


# ── Sonuç ve referans modelleri ──────────────────────────────────────

class TestReferencesModel:
    def test_valid_references(self):
        references = _references()
        assert references.request_reference == "req-1"
        assert references.logical_sequence == 7

    @pytest.mark.parametrize("overrides", [
        dict(request_reference=""),
        dict(request_reference=None),
        dict(request_reference=1),
        dict(previous_ledger_reference=""),
        dict(previous_ledger_reference=None),
        dict(current_ledger_reference=""),
        dict(current_ledger_reference=None),
        dict(risk_decision_reference=""),
        dict(risk_decision_reference=1),
        dict(kill_switch_reference=""),
        dict(execution_result_reference=""),
        dict(logical_sequence=-1),
        dict(logical_sequence=True),
        dict(logical_sequence="7"),
        dict(logical_sequence=None)])
    def test_invalid_references_rejected(self, overrides):
        base = dict(request_reference="req-1",
                    previous_ledger_reference="ledger-0",
                    current_ledger_reference="ledger-1")
        base.update(overrides)
        with pytest.raises(PaperExecutionContractError):
            PaperExecutionReferences(**base)

    def test_references_frozen(self):
        references = _references()
        with pytest.raises(Exception):
            references.request_reference = "x"

    def test_references_hashable(self):
        assert hash(_references()) == hash(_references())


class TestResultModel:
    def _result(self, **overrides):
        base = dict(
            operation=PaperExecutionOperation.SUBMIT_ORDER,
            decision=PaperExecutionDecision.DENIED,
            decision_code=(PaperExecutionDecisionCode
                           .MODE_DENIED),
            previous_ledger_reference="ledger-0",
            current_ledger_reference="ledger-0",
            ledger=_snapshot())
        base.update(overrides)
        return PaperExecutionServiceResult(**base)

    def test_valid_denied_result(self):
        result = self._result()
        assert not result.executed

    @pytest.mark.parametrize("overrides", [
        dict(operation=None), dict(operation="SUBMIT_ORDER"),
        dict(decision=None), dict(decision="DENIED"),
        dict(decision_code=None),
        dict(decision_code="MODE_DENIED"),
        dict(previous_ledger_reference=""),
        dict(previous_ledger_reference=None),
        dict(current_ledger_reference=""),
        dict(ledger=None), dict(ledger="ledger"),
        dict(order_reference=""),
        dict(order_reference=1),
        dict(execution_result="result"),
        dict(execution_result_reference=""),
        dict(execution_references=[]),
        dict(execution_references=("a", 1)),
        dict(risk_decision_reference=""),
        dict(kill_switch_reference=""),
        dict(recommended_quantity="1"),
        dict(recommended_quantity=True),
        dict(recommended_quantity=D("0")),
        dict(recommended_quantity=D("-1")),
        dict(recommended_quantity=D("NaN")),
        dict(audit_records=[]),
        dict(audit_records=("kayıt",)),
        dict(logical_sequence=-1),
        dict(logical_sequence=True),
        dict(logical_sequence="1")])
    def test_invalid_fields_rejected(self, overrides):
        with pytest.raises(PaperExecutionContractError):
            self._result(**overrides)

    @pytest.mark.parametrize("decision,code", [
        (PaperExecutionDecision.EXECUTED,
         PaperExecutionDecisionCode.MODE_DENIED),
        (PaperExecutionDecision.EXECUTED,
         PaperExecutionDecisionCode.RISK_REDUCE_SIZE),
        (PaperExecutionDecision.DENIED,
         PaperExecutionDecisionCode.ORDER_EXECUTED),
        (PaperExecutionDecision.DENIED,
         PaperExecutionDecisionCode.RISK_REDUCE_SIZE),
        (PaperExecutionDecision.RECOMMENDATION_ONLY,
         PaperExecutionDecisionCode.ORDER_EXECUTED),
        (PaperExecutionDecision.RECOMMENDATION_ONLY,
         PaperExecutionDecisionCode.RISK_REJECTED)])
    def test_decision_code_coupling_enforced(self, decision,
                                             code):
        with pytest.raises(PaperExecutionContractError):
            self._result(decision=decision,
                         decision_code=code)

    @pytest.mark.parametrize("decision,code", [
        (PaperExecutionDecision.EXECUTED,
         PaperExecutionDecisionCode.ORDER_EXECUTED),
        (PaperExecutionDecision.EXECUTED,
         PaperExecutionDecisionCode.ORDER_CANCELLED),
        (PaperExecutionDecision.RECOMMENDATION_ONLY,
         PaperExecutionDecisionCode.RISK_REDUCE_SIZE),
        (PaperExecutionDecision.DENIED,
         PaperExecutionDecisionCode.KILL_SWITCH_DENIED),
        (PaperExecutionDecision.DENIED,
         PaperExecutionDecisionCode.RISK_REJECTED),
        (PaperExecutionDecision.DENIED,
         PaperExecutionDecisionCode.PERMISSION_DENIED)])
    def test_valid_couplings_accepted(self, decision, code):
        assert self._result(decision=decision,
                            decision_code=code)

    def test_result_frozen(self):
        result = self._result()
        with pytest.raises(Exception):
            result.ledger = _snapshot()

    def test_audit_stage_codes_helper(self):
        assert self._result().audit_stage_codes() == ()


class TestEnumClosure:
    def test_operations_closed(self):
        assert [m.name for m in PaperExecutionOperation] == \
            ["SUBMIT_ORDER", "CANCEL_ORDER"]

    def test_decisions_closed(self):
        assert [m.name for m in PaperExecutionDecision] == \
            ["EXECUTED", "DENIED", "RECOMMENDATION_ONLY"]

    def test_decision_codes_closed(self):
        assert [m.name for m in PaperExecutionDecisionCode] == \
            ["ORDER_EXECUTED", "ORDER_CANCELLED",
             "MODE_DENIED", "POLICY_DENIED",
             "PERMISSION_DENIED", "RISK_REJECTED",
             "RISK_REDUCE_SIZE",
             "RISK_CONFIRMATION_REQUIRED",
             "KILL_SWITCH_DENIED"]

    def test_audit_stages_closed_and_ordered(self):
        assert tuple(m.name for m in PaperAuditStage) == \
            ALL_STAGES
