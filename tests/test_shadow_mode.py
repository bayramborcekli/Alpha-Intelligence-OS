"""Mission 2100 — Agent 05: Gölge Modu Servisi testleri.

Boru hattı sırası, SHADOW-only zorlaması, risk/izin/kill switch
redleri, reddedilen yollarda SIFIR ve onaylı gönderimde TAM BİR
simülasyon çağrısı, sıfır borsa yazımı, değişmez raporlar,
istatistik ve kalp atışı.
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
from execution_enums import (OrderSide, OrderType, TimeInForce)
from execution_kill_switch_models import (KillSwitchReason,
                                          KillSwitchSnapshot,
                                          KillSwitchState)
from execution_models import ExecutionRequest
from execution_risk_models import RiskDecision, RiskDecisionType
from paper_broker import PaperBroker
from paper_execution_models import PaperExecutionReferences
from paper_execution_service import (PaperRiskEvaluator,
                                     StaticRiskEvaluator)
from paper_models import PaperLedgerSnapshot
from shadow_comparator import ShadowComparator
from shadow_errors import (ShadowConfigurationError,
                           ShadowContractError, ShadowError,
                           ShadowRiskError, ShadowStateError)
from shadow_mode import ShadowModeService
from shadow_models import (ShadowDecision, ShadowDecisionCode,
                           ShadowHeartbeat,
                           ShadowMarketObservation,
                           ShadowOperation, ShadowResult,
                           ShadowSnapshot, ShadowStatistics)

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

SHADOW_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.SHADOW,
    broker_read_allowed=True)
PAPER_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.PAPER,
    simulated_fill_allowed=True)
MICRO_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.MICRO_LIVE,
    exchange_write_allowed=True,
    broker_read_allowed=True,
    human_confirmation_required=True,
    explicit_authorization_required=True,
    authorization_reference="auth-1")
WRITE_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.SHADOW,
    broker_read_allowed=True,
    exchange_write_allowed=True)
CONFLICT_POLICY = ControlledExecutionPolicy(
    mode=ControlledExecutionMode.SHADOW,
    broker_read_allowed=True,
    simulated_fill_allowed=True)

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

FULL_STAGES = ("REQUEST_VALIDATED", "MODE_VALIDATED",
               "RISK_EVALUATED", "PERMISSION_EVALUATED",
               "KILL_SWITCH_CHECKED", "PAPER_SIMULATED",
               "MARKET_OBSERVED", "COMPARISON_COMPLETED")


def _service(risk=ALLOW, broker=BROKER):
    return ShadowModeService(broker=broker,
                             foundation=FOUNDATION,
                             risk_evaluator=risk)


def _ledger(cash="1000"):
    return PaperLedgerSnapshot(
        quote_asset="USDT", initial_cash=D(cash), cash=D(cash),
        reserved_cash=D("0"), realized_pnl=D("0"),
        commission_paid=D("0"))


def _shadow(reference="shadow-0"):
    return ShadowSnapshot(snapshot_reference=reference)


def _request(symbol="BTCUSDT", quantity="2", price="100",
             side=OrderSide.BUY):
    return ExecutionRequest(
        symbol=symbol, side=side, order_type=OrderType.LIMIT,
        quantity=D(quantity), time_in_force=TimeInForce.GTC,
        price=D(price))


def _observation(reference="obs-1", symbol="BTCUSDT",
                 last_trade_price="101", best_bid="99",
                 best_ask="101", sequence=9):
    return ShadowMarketObservation(
        observation_reference=reference, symbol=symbol,
        best_bid=None if best_bid is None else D(best_bid),
        best_ask=None if best_ask is None else D(best_ask),
        last_trade_price=(None if last_trade_price is None
                          else D(last_trade_price)),
        logical_sequence=sequence)


def _references(sequence=7):
    return PaperExecutionReferences(
        request_reference="req-1",
        previous_ledger_reference="ledger-0",
        current_ledger_reference="ledger-1",
        risk_decision_reference="risk-1",
        kill_switch_reference="ks-1",
        logical_sequence=sequence)


def _submit(ledger=None, shadow=None, request=None,
            order_reference="ord-1", policy=SHADOW_POLICY,
            kill_switch=KS_ENABLED, observation=None,
            references=None, service=None):
    return (service or _service()).submit_shadow(
        ledger if ledger is not None else _ledger(),
        shadow if shadow is not None else _shadow(),
        request if request is not None else _request(),
        order_reference, policy, kill_switch,
        observation if observation is not None
        else _observation(),
        references or _references())


def _cancel(policy=SHADOW_POLICY, kill_switch=KS_ENABLED,
            service=None, order_reference="ord-404"):
    return (service or _service()).cancel_shadow(
        _ledger(), _shadow(), order_reference, policy,
        kill_switch, _references())


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


# ── Kuruluş doğrulaması ──────────────────────────────────────────────

class TestConfiguration:
    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_invalid_broker_rejected(self, bad):
        with pytest.raises(ShadowConfigurationError):
            ShadowModeService(broker=bad, foundation=FOUNDATION,
                              risk_evaluator=ALLOW)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_invalid_foundation_rejected(self, bad):
        with pytest.raises(ShadowConfigurationError):
            ShadowModeService(broker=BROKER, foundation=bad,
                              risk_evaluator=ALLOW)

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_invalid_risk_evaluator_rejected(self, bad):
        with pytest.raises(ShadowConfigurationError):
            ShadowModeService(broker=BROKER,
                              foundation=FOUNDATION,
                              risk_evaluator=bad)

    @pytest.mark.parametrize("bad", ["x", 1, object()])
    def test_invalid_comparator_rejected(self, bad):
        with pytest.raises(ShadowConfigurationError):
            ShadowModeService(broker=BROKER,
                              foundation=FOUNDATION,
                              risk_evaluator=ALLOW,
                              comparator=bad)

    def test_service_frozen(self):
        with pytest.raises(Exception):
            _service().broker = None

    def test_service_no_dict(self):
        assert not hasattr(_service(), "__dict__")

    def test_default_comparator(self):
        assert isinstance(_service().comparator,
                          ShadowComparator)


# ── Sözleşme doğrulaması ─────────────────────────────────────────────

class TestContractValidation:
    @pytest.mark.parametrize("bad", [None, "x", 1, object(), ()])
    def test_bad_ledger_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _service().submit_shadow(
                bad, _shadow(), _request(), "ord-1",
                SHADOW_POLICY, KS_ENABLED, _observation(),
                _references())

    @pytest.mark.parametrize("bad", [None, "x", 1, object(), ()])
    def test_bad_shadow_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _service().submit_shadow(
                _ledger(), bad, _request(), "ord-1",
                SHADOW_POLICY, KS_ENABLED, _observation(),
                _references())

    @pytest.mark.parametrize("bad", [None, "x", 1, object(), ()])
    def test_bad_request_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _service().submit_shadow(
                _ledger(), _shadow(), bad, "ord-1",
                SHADOW_POLICY, KS_ENABLED, _observation(),
                _references())

    @pytest.mark.parametrize("bad", [None, "", "  ", 5, ()])
    def test_bad_order_reference_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _submit(order_reference=bad)

    @pytest.mark.parametrize("bad", [None, "x", 1, object(), ()])
    def test_bad_observation_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _service().submit_shadow(
                _ledger(), _shadow(), _request(), "ord-1",
                SHADOW_POLICY, KS_ENABLED, bad, _references())

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_references_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _service().submit_shadow(
                _ledger(), _shadow(), _request(), "ord-1",
                SHADOW_POLICY, KS_ENABLED, _observation(), bad)

    def test_observation_symbol_mismatch_rejected(self):
        with pytest.raises(ShadowContractError):
            _submit(observation=_observation(symbol="ETHUSDT"))

    def test_market_order_without_price_rejected(self):
        request = ExecutionRequest(
            symbol="BTCUSDT", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=D("1"),
            time_in_force=TimeInForce.IOC)
        with pytest.raises(ShadowContractError):
            _submit(request=request)

    def test_contract_errors_leave_broker_untouched(
            self, broker_calls):
        with pytest.raises(ShadowContractError):
            _service().submit_shadow(
                None, _shadow(), _request(), "ord-1",
                SHADOW_POLICY, KS_ENABLED, _observation(),
                _references())
        assert broker_calls == []

    def test_sterile_error_message(self):
        with pytest.raises(ShadowContractError) as exc:
            _submit(order_reference="")
        assert str(exc.value) == \
            "INVALID_SHADOW_FIELD:order_reference"


# ── SHADOW-only zorlaması ────────────────────────────────────────────

class TestShadowOnly:
    @pytest.mark.parametrize("policy", [
        PAPER_POLICY, MICRO_POLICY, None, "SHADOW", 5,
        object()])
    def test_non_shadow_mode_denied(self, policy, broker_calls):
        result = _submit(policy=policy)
        assert result.decision is ShadowDecision.DENIED
        assert result.decision_code is \
            ShadowDecisionCode.MODE_DENIED
        assert broker_calls == []

    @pytest.mark.parametrize("policy", [
        PAPER_POLICY, MICRO_POLICY, None, object()])
    def test_cancel_non_shadow_mode_denied(self, policy,
                                           broker_calls):
        result = _cancel(policy=policy)
        assert result.decision_code is \
            ShadowDecisionCode.MODE_DENIED
        assert broker_calls == []

    def test_exchange_write_policy_denied_before_risk(
            self, broker_calls):
        class Recording(PaperRiskEvaluator):
            invoked = 0

            def evaluate(self, request):
                Recording.invoked = Recording.invoked + 1
                return RiskDecision(
                    decision=RiskDecisionType.ALLOW)

        result = _submit(policy=WRITE_POLICY,
                         service=_service(Recording()))
        assert result.decision_code is \
            ShadowDecisionCode.POLICY_DENIED
        assert Recording.invoked == 0
        assert broker_calls == []

    def test_policy_denial_stages(self):
        result = _submit(policy=WRITE_POLICY)
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED")

    def test_mode_denial_stages(self):
        result = _submit(policy=PAPER_POLICY)
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED")

    def test_conflicting_shadow_policy_denied_at_gate(
            self, broker_calls):
        result = _submit(policy=CONFLICT_POLICY)
        assert result.decision is ShadowDecision.DENIED
        assert result.decision_code is \
            ShadowDecisionCode.POLICY_DENIED
        assert broker_calls == []

    def test_no_mode_escalation_or_fallback(self):
        result = _submit(policy=MICRO_POLICY)
        assert result.decision is ShadowDecision.DENIED
        assert result.shadow.orders == ()

    def test_denied_increments_denied_count(self):
        result = _submit(policy=PAPER_POLICY)
        assert result.shadow.denied_count == 1


# ── Risk Motoru ──────────────────────────────────────────────────────

class TestRiskEngine:
    def test_reject_denies_without_broker(self, broker_calls):
        result = _submit(service=_service(REJECT))
        assert result.decision is ShadowDecision.DENIED
        assert result.decision_code is \
            ShadowDecisionCode.RISK_REJECTED
        assert broker_calls == []

    def test_reject_stages(self):
        result = _submit(service=_service(REJECT))
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED",
            "RISK_EVALUATED")

    def test_reduce_size_recommendation_only(self,
                                             broker_calls):
        result = _submit(service=_service(REDUCE))
        assert result.decision is \
            ShadowDecision.RECOMMENDATION_ONLY
        assert result.decision_code is \
            ShadowDecisionCode.RISK_REDUCE_SIZE
        assert result.recommended_quantity == D("1")
        assert broker_calls == []

    def test_reduce_size_no_auto_resize(self):
        result = _submit(service=_service(REDUCE))
        assert result.shadow.orders == ()
        assert result.comparison is None

    def test_confirmation_required_denied(self, broker_calls):
        result = _submit(service=_service(CONFIRM))
        assert result.decision_code is \
            ShadowDecisionCode.RISK_CONFIRMATION_REQUIRED
        assert broker_calls == []

    def test_risk_evaluator_failure_wrapped(self):
        class Exploding(PaperRiskEvaluator):
            def evaluate(self, request):
                raise RuntimeError("iç risk ayrıntısı")

        with pytest.raises(ShadowRiskError) as exc:
            _submit(service=_service(Exploding()))
        assert str(exc.value) == "SHADOW_RISK:EVALUATOR_FAILURE"

    def test_invalid_risk_decision_wrapped(self):
        class Broken(PaperRiskEvaluator):
            def evaluate(self, request):
                return "ALLOW"

        with pytest.raises(ShadowRiskError):
            _submit(service=_service(Broken()))

    def test_risk_never_bypassed_on_submit(self):
        class Recording(PaperRiskEvaluator):
            invoked = 0

            def evaluate(self, request):
                Recording.invoked = Recording.invoked + 1
                return RiskDecision(
                    decision=RiskDecisionType.ALLOW)

        _submit(service=_service(Recording()))
        assert Recording.invoked == 1


# ── İzin kapısı ──────────────────────────────────────────────────────

class TestPermissionGate:
    def test_valid_shadow_policy_passes_gate(self):
        result = _submit()
        assert result.decision is ShadowDecision.SIMULATED

    def test_gate_conflict_stages(self):
        result = _submit(policy=CONFLICT_POLICY)
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED",
            "RISK_EVALUATED", "PERMISSION_EVALUATED")

    def test_cancel_gate_conflict_denied(self, broker_calls):
        result = _cancel(policy=CONFLICT_POLICY)
        assert result.decision_code is \
            ShadowDecisionCode.POLICY_DENIED
        assert broker_calls == []


# ── Kill Switch ──────────────────────────────────────────────────────

class TestKillSwitch:
    @pytest.mark.parametrize("kill_switch", [
        KS_DISABLED, KS_LOCKED, KS_MAINTENANCE, None, "ENABLED",
        5, object()])
    def test_non_enabled_denied(self, kill_switch,
                                broker_calls):
        result = _submit(kill_switch=kill_switch)
        assert result.decision is ShadowDecision.DENIED
        assert result.decision_code is \
            ShadowDecisionCode.KILL_SWITCH_DENIED
        assert broker_calls == []

    @pytest.mark.parametrize("kill_switch", [
        KS_DISABLED, KS_LOCKED, KS_MAINTENANCE, None])
    def test_cancel_non_enabled_denied(self, kill_switch,
                                       broker_calls):
        result = _cancel(kill_switch=kill_switch)
        assert result.decision_code is \
            ShadowDecisionCode.KILL_SWITCH_DENIED
        assert broker_calls == []

    def test_kill_switch_denial_stages(self):
        result = _submit(kill_switch=KS_DISABLED)
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MODE_VALIDATED",
            "RISK_EVALUATED", "PERMISSION_EVALUATED",
            "KILL_SWITCH_CHECKED")

    def test_kill_switch_never_bypassed(self):
        result = _submit(kill_switch=KS_LOCKED)
        assert result.shadow.orders == ()
        assert result.comparison is None


# ── Onaylı gölge gönderimi ───────────────────────────────────────────

class TestApprovedSubmit:
    def test_simulated_decision(self):
        result = _submit()
        assert result.operation is \
            ShadowOperation.SUBMIT_SHADOW
        assert result.decision is ShadowDecision.SIMULATED
        assert result.decision_code is \
            ShadowDecisionCode.ORDER_SIMULATED

    def test_exactly_one_simulation_call(self, broker_calls):
        _submit()
        assert broker_calls == ["submit"]

    def test_full_stage_sequence(self):
        result = _submit()
        assert result.audit_stage_codes() == FULL_STAGES

    def test_shadow_order_recorded(self):
        result = _submit()
        assert len(result.shadow.orders) == 1
        order = result.shadow.orders[0]
        assert order.order_reference == "ord-1"
        assert order.symbol == "BTCUSDT"
        assert order.quantity == D("2")
        assert order.price == D("100")

    def test_shadow_execution_recorded(self):
        result = _submit()
        assert len(result.shadow.executions) == 1
        execution = result.shadow.executions[0]
        assert execution.order_reference == "ord-1"
        assert execution.quantity == D("2")

    def test_comparison_produced(self):
        result = _submit()
        assert result.comparison is not None
        assert result.comparison.paper_reference == "ord-1"
        assert result.comparison.market_reference == "obs-1"
        assert result.comparison.price_delta == D("1")

    def test_comparison_in_snapshot(self):
        result = _submit()
        assert len(result.shadow.comparisons) == 1
        assert result.shadow.comparisons[0] == \
            result.comparison

    def test_ledger_advances(self):
        result = _submit()
        assert result.ledger.cash == D("800")
        assert len(result.ledger.orders) == 1

    def test_input_states_unchanged(self):
        ledger = _ledger()
        shadow = _shadow()
        _submit(ledger=ledger, shadow=shadow)
        assert ledger.cash == D("1000")
        assert shadow.orders == ()

    def test_result_immutable(self):
        result = _submit()
        with pytest.raises(Exception):
            result.decision = ShadowDecision.DENIED

    def test_deterministic_repeat(self):
        first = _submit()
        second = _submit()
        assert first.comparison == second.comparison
        assert first.audit_stage_codes() == \
            second.audit_stage_codes()

    def test_latency_from_logical_sequences(self):
        result = _submit(
            observation=_observation(sequence=12),
            references=_references(sequence=7))
        assert result.comparison.latency == 5

    def test_unknown_symbol_wrapped_sterile(self):
        with pytest.raises(ShadowStateError) as exc:
            _submit(request=_request(symbol="DOGEUSDT"),
                    observation=_observation(
                        symbol="DOGEUSDT"))
        assert str(exc.value).startswith("SHADOW_STATE:")

    def test_insufficient_cash_wrapped_sterile(self):
        with pytest.raises(ShadowStateError):
            _submit(ledger=_ledger(cash="10"))

    def test_unexpected_broker_failure_contained(
            self, monkeypatch):
        def exploding(self, *args, **kwargs):
            raise RuntimeError("iç broker ayrıntısı")

        monkeypatch.setattr(PaperBroker, "submit", exploding)
        with pytest.raises(ShadowStateError) as exc:
            _submit()
        assert "INTERNAL_FAILURE" in str(exc.value)
        assert "iç broker ayrıntısı" not in str(exc.value)

    def test_duplicate_reference_wrapped(self):
        first = _submit()
        with pytest.raises(ShadowStateError):
            _submit(ledger=first.ledger, shadow=first.shadow)


# ── Gölge iptali ─────────────────────────────────────────────────────

class TestCancelShadow:
    def test_cancel_risk_exemption_codified(self):
        """İptal riski BİLİNÇLİ muaf: istek uydurulamaz."""
        with pytest.raises(ShadowStateError):
            _cancel(service=_service(REJECT))

    def test_exactly_one_cancel_call(self, broker_calls):
        with pytest.raises(ShadowStateError):
            _cancel()
        assert broker_calls == ["cancel"]

    def test_unknown_order_cancel_sterile(self):
        with pytest.raises(ShadowStateError) as exc:
            _cancel()
        assert str(exc.value).startswith("SHADOW_STATE:")

    def test_unexpected_cancel_failure_contained(
            self, monkeypatch):
        def exploding(self, *args, **kwargs):
            raise ValueError("iç ayrıntı")

        monkeypatch.setattr(PaperBroker, "cancel", exploding)
        with pytest.raises(ShadowStateError) as exc:
            _cancel()
        assert "INTERNAL_FAILURE" in str(exc.value)

    def test_cancel_denied_paths_zero_calls(self, broker_calls):
        _cancel(policy=PAPER_POLICY)
        _cancel(kill_switch=KS_DISABLED)
        assert broker_calls == []


# ── compare_execution ────────────────────────────────────────────────

class TestCompareExecution:
    def _submitted(self):
        return _submit()

    def test_compare_known_order(self, broker_calls):
        submitted = self._submitted()
        broker_calls.clear()
        result = _service().compare_execution(
            submitted.shadow, "ord-1",
            _observation(reference="obs-2",
                         last_trade_price="105", sequence=20),
            _references(sequence=8), submitted.ledger)
        assert result.operation is \
            ShadowOperation.COMPARE_EXECUTION
        assert result.decision_code is \
            ShadowDecisionCode.COMPARISON_COMPLETED
        assert result.comparison.price_delta == D("5")
        assert broker_calls == []

    def test_compare_appends_comparison(self):
        submitted = self._submitted()
        result = _service().compare_execution(
            submitted.shadow, "ord-1", _observation(),
            _references(), submitted.ledger)
        assert len(result.shadow.comparisons) == 2

    def test_compare_unknown_order_sterile(self):
        with pytest.raises(ShadowStateError) as exc:
            _service().compare_execution(
                _shadow(), "ord-404", _observation(),
                _references(), _ledger())
        assert str(exc.value) == "SHADOW_STATE:UNKNOWN_ORDER"

    def test_compare_ledger_unchanged(self):
        submitted = self._submitted()
        result = _service().compare_execution(
            submitted.shadow, "ord-1", _observation(),
            _references(), submitted.ledger)
        assert result.ledger is submitted.ledger

    def test_compare_stages(self):
        submitted = self._submitted()
        result = _service().compare_execution(
            submitted.shadow, "ord-1", _observation(),
            _references(), submitted.ledger)
        assert result.audit_stage_codes() == (
            "REQUEST_VALIDATED", "MARKET_OBSERVED",
            "COMPARISON_COMPLETED")

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_observation_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _service().compare_execution(
                _shadow(), "ord-1", bad, _references(),
                _ledger())


# ── İstatistik ve kalp atışı ─────────────────────────────────────────

class TestStatisticsHeartbeat:
    def test_empty_statistics(self):
        stats = _service().statistics(_shadow())
        assert stats == ShadowStatistics()

    def test_statistics_after_submit(self):
        result = _submit()
        stats = _service().statistics(result.shadow)
        assert stats.total_orders == 1
        assert stats.total_executions == 1
        assert stats.total_comparisons == 1
        assert stats.total_denied == 0

    def test_statistics_after_denial(self):
        result = _submit(policy=PAPER_POLICY)
        stats = _service().statistics(result.shadow)
        assert stats.total_denied == 1
        assert stats.total_orders == 0

    def test_heartbeat_alive(self):
        beat = _service().heartbeat(_shadow())
        assert isinstance(beat, ShadowHeartbeat)
        assert beat.alive is True
        assert beat.order_count == 0

    def test_heartbeat_counts(self):
        result = _submit()
        beat = _service().heartbeat(result.shadow)
        assert beat.order_count == 1
        assert beat.execution_count == 1
        assert beat.comparison_count == 1

    @pytest.mark.parametrize("bad", [None, "x", 1, object()])
    def test_bad_snapshot_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _service().statistics(bad)
        with pytest.raises(ShadowContractError):
            _service().heartbeat(bad)


# ── Sıfır borsa yazımı sertifikası ───────────────────────────────────

class TestZeroExchangeWrite:
    def test_service_has_no_live_capabilities(self):
        forbidden = ("place", "transfer", "withdraw", "margin",
                     "listen_key", "modify_position")
        names = [name for name in dir(ShadowModeService)
                 if not name.startswith("__")]
        for name in names:
            for token in forbidden:
                assert token not in name.lower()

    def test_all_denial_paths_zero_broker(self, broker_calls):
        _submit(policy=PAPER_POLICY)
        _submit(policy=WRITE_POLICY)
        _submit(service=_service(REJECT))
        _submit(service=_service(REDUCE))
        _submit(service=_service(CONFIRM))
        _submit(policy=CONFLICT_POLICY)
        _submit(kill_switch=KS_DISABLED)
        assert broker_calls == []

    def test_observation_is_pure_data(self):
        observation = _observation()
        names = [name for name in dir(observation)
                 if not name.startswith("_")]
        methods = [name for name in names
                   if callable(getattr(observation, name))]
        assert methods == []

    def test_result_decision_closed(self):
        result = _submit()
        assert isinstance(result, ShadowResult)
        assert result.simulated is True
