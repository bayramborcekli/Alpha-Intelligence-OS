"""Mission 2200 — Agent 01: servis + denetim zinciri testleri.

Gerçek sertifikalı yığın (PaperBroker + ControlledExecutionAPI)
kullanılır — mock borsa katmanı yoktur. Kapatmalar DAİMA
kontrollü niyet olarak akar.
"""

from decimal import Decimal

import pytest

from controlled_execution_api import ControlledExecutionAPI
from controlled_execution_foundation import (
    ControlledExecutionFoundation)
from controlled_execution_models import (
    ControlledExecutionMode, ControlledExecutionPolicy)
from controlled_execution_policy import ExtensionRegistry
from controlled_execution_router import ControlledExecutionRouter
from execution_kill_switch_models import (
    KillSwitchReason, KillSwitchSnapshot, KillSwitchState)
from execution_risk_models import RiskDecision, RiskDecisionType
from micro_live_authorization import MicroLiveAuthorizationService
from operation_control_audit import (
    FORBIDDEN_AUDIT_TOKENS, OperationAuditTrail)
from operation_control_errors import (
    OperationControlAuditError, OperationControlValidationError)
from operation_control_models import (
    AutomationCommand as AC, AutomationState as AS,
    IdempotencyStatus, OperationActionStatus as OAS,
    OperationAuditRecord, ReconciliationState,
    SymbolAutomationState as SS, SymbolCommand as SC)
from operation_control_service import (
    CONFIRMATION_PHRASE, OperationControlService)
from paper_broker import PaperBroker
from paper_execution_service import (
    PaperExecutionService, StaticRiskEvaluator)
from paper_models import PaperLedgerSnapshot, PaperPosition
from shadow_mode import ShadowModeService
from tests.test_operation_control_models import valid_position


def build_service(clock=None):
    foundation = ControlledExecutionFoundation(ExtensionRegistry())
    broker = PaperBroker(known_symbols=("BTCUSDT", "ETHUSDT"))
    risk = StaticRiskEvaluator(RiskDecision(
        decision=RiskDecisionType.ALLOW))
    api = ControlledExecutionAPI(ControlledExecutionRouter(
        PaperExecutionService(broker=broker, foundation=foundation,
                              risk_evaluator=risk),
        ShadowModeService(broker=broker, foundation=foundation,
                          risk_evaluator=risk),
        MicroLiveAuthorizationService(foundation=foundation)))
    return OperationControlService(api, clock=clock or (lambda: 1))


def close_context(active_kill_switch=False):
    kill_switch = KillSwitchSnapshot(
        state=KillSwitchState.DISABLED if active_kill_switch
        else KillSwitchState.ENABLED,
        reason=KillSwitchReason.MANUAL, timestamp=1, sequence_id=1)
    policy = ControlledExecutionPolicy(
        mode=ControlledExecutionMode.PAPER,
        simulated_fill_allowed=True)
    return policy, kill_switch


def ledger_for(positions):
    paper = tuple(
        PaperPosition(symbol=p.symbol, quantity=p.quantity,
                      cost_basis=p.entry_price * p.quantity)
        for p in positions
        if p.side.upper() in ("BUY", "LONG")
        and p.quantity is not None and p.entry_price is not None
        and p.quantity > 0)
    budget = sum((p.quantity * p.current_price * 2
                  for p in positions
                  if p.quantity is not None
                  and p.current_price is not None
                  and p.quantity > 0), Decimal("0"))
    cost = sum((pp.cost_basis for pp in paper), Decimal("0"))
    return PaperLedgerSnapshot(
        quote_asset="USDT", initial_cash=cost + budget,
        cash=budget, reserved_cash=Decimal("0"),
        realized_pnl=Decimal("0"),
        commission_paid=Decimal("0"), positions=paper)


def open_position(**over):
    base = dict(position_id="BTCUSDT", symbol="BTCUSDT",
                side="BUY", entry_price=Decimal("100"),
                current_price=Decimal("110"),
                quantity=Decimal("1"))
    base.update(over)
    return valid_position(**base)


DESTR = dict(reason="test reason",
             confirm_phrase=CONFIRMATION_PHRASE)


@pytest.fixture()
def svc():
    return build_service()


# ── Kuruluş varsayılanları ──────────────────────────────────────────

class TestConstruction:
    def test_defaults_fail_closed(self, svc):
        assert svc.automation_state is AS.STOPPED
        assert svc.stop_new_entries is False
        assert svc.last_error_code == "-"
        assert len(svc.audit) == 0

    def test_requires_certified_api(self):
        with pytest.raises(OperationControlValidationError):
            OperationControlService(object())

    @pytest.mark.parametrize("symbol", ["BTCUSDT", "", None, 5])
    def test_unregistered_symbol_disabled(self, svc, symbol):
        assert svc.symbol_state(symbol) is SS.DISABLED

    def test_symbol_states_copy(self, svc):
        states = svc.symbol_states()
        states["X"] = SS.ENABLED
        assert svc.symbol_state("X") is SS.DISABLED


# ── Otomasyon komutları ────────────────────────────────────────────

class TestAutomationCommands:
    def test_start(self, svc):
        result = svc.execute_automation_command(AC.START, "op")
        assert result.status is OAS.COMPLETED
        assert result.lifecycle_status == "APPLIED"
        assert svc.automation_state is AS.RUNNING

    def test_start_pause_resume_stop_cycle(self, svc):
        for command, state in ((AC.START, AS.RUNNING),
                               (AC.PAUSE, AS.PAUSED),
                               (AC.RESUME, AS.RUNNING),
                               (AC.STOP, AS.STOPPED)):
            svc.execute_automation_command(command, "op")
            assert svc.automation_state is state

    @pytest.mark.parametrize("command", [AC.PAUSE, AC.RESUME])
    def test_invalid_from_stopped(self, svc, command):
        result = svc.execute_automation_command(command, "op")
        assert result.status is OAS.DENIED
        assert result.error_code == "INVALID_TRANSITION"
        assert svc.automation_state is AS.STOPPED

    def test_idempotent_repeat_stop(self, svc):
        result = svc.execute_automation_command(AC.STOP, "op")
        assert result.status is OAS.COMPLETED
        assert result.lifecycle_status == "IDEMPOTENT_REPEAT"

    def test_replay_same_key(self, svc):
        first = svc.execute_automation_command(
            AC.START, "op", idempotency_key="k1")
        replay = svc.execute_automation_command(
            AC.START, "op", idempotency_key="k1")
        assert replay.idempotency_status is \
            IdempotencyStatus.REPLAYED
        assert replay.action_id == first.action_id
        assert svc.automation_state is AS.RUNNING

    def test_conflict_same_key_other_command(self, svc):
        svc.execute_automation_command(AC.START, "op",
                                       idempotency_key="k1")
        result = svc.execute_automation_command(
            AC.STOP, "op", idempotency_key="k1")
        assert result.status is OAS.DENIED
        assert result.error_code == "IDEMPOTENCY_CONFLICT"
        assert svc.automation_state is AS.RUNNING

    @pytest.mark.parametrize("actor", ["", "  ", None, 5])
    def test_actor_required(self, svc, actor):
        with pytest.raises(OperationControlValidationError):
            svc.execute_automation_command(AC.START, actor)

    @pytest.mark.parametrize("command", ["START", None, 1])
    def test_command_enum_required(self, svc, command):
        with pytest.raises(OperationControlValidationError):
            svc.execute_automation_command(command, "op")

    def test_audit_recorded(self, svc):
        svc.execute_automation_command(AC.START, "op")
        records = svc.audit.records()
        assert len(records) == 1
        assert records[0].action == "AUTOMATION:START"
        assert records[0].result == "COMPLETED"

    def test_blocked_start_denied(self, svc):
        svc.execute_automation_command(AC.START, "op")
        svc.mark_blocked("op", "kill switch engaged")
        assert svc.automation_state is AS.BLOCKED
        result = svc.execute_automation_command(AC.START, "op")
        assert result.status is OAS.DENIED
        assert result.error_code == "INVALID_TRANSITION"

    def test_blocked_stop_allowed(self, svc):
        svc.execute_automation_command(AC.START, "op")
        svc.mark_blocked("op", "x")
        result = svc.execute_automation_command(AC.STOP, "op")
        assert result.status is OAS.COMPLETED
        assert svc.automation_state is AS.STOPPED

    def test_mark_blocked_noop_from_stopped(self, svc):
        svc.mark_blocked("op", "x")
        assert svc.automation_state is AS.STOPPED
        assert len(svc.audit) == 0


# ── Sembol komutları ───────────────────────────────────────────────

class TestSymbolCommands:
    def test_enable(self, svc):
        result = svc.execute_symbol_command("btcusdt", SC.ENABLE,
                                            "op")
        assert result.status is OAS.COMPLETED
        assert svc.symbol_state("BTCUSDT") is SS.ENABLED

    def test_symbol_isolation(self, svc):
        svc.execute_symbol_command("BTCUSDT", SC.ENABLE, "op")
        svc.execute_symbol_command("ETHUSDT", SC.ENABLE, "op")
        svc.execute_symbol_command("BTCUSDT", SC.PAUSE, "op")
        assert svc.symbol_state("BTCUSDT") is SS.PAUSED
        assert svc.symbol_state("ETHUSDT") is SS.ENABLED

    def test_idempotent_repeat(self, svc):
        svc.execute_symbol_command("BTCUSDT", SC.ENABLE, "op")
        result = svc.execute_symbol_command("BTCUSDT", SC.ENABLE,
                                            "op")
        assert result.lifecycle_status == "IDEMPOTENT_REPEAT"

    def test_invalid_transition(self, svc):
        result = svc.execute_symbol_command("BTCUSDT", SC.RESUME,
                                            "op")
        assert result.status is OAS.DENIED
        assert result.error_code == "INVALID_TRANSITION"

    def test_replay(self, svc):
        svc.execute_symbol_command("BTCUSDT", SC.ENABLE, "op",
                                   idempotency_key="s1")
        replay = svc.execute_symbol_command(
            "BTCUSDT", SC.ENABLE, "op", idempotency_key="s1")
        assert replay.idempotency_status is \
            IdempotencyStatus.REPLAYED

    def test_conflict_other_symbol_same_key(self, svc):
        svc.execute_symbol_command("BTCUSDT", SC.ENABLE, "op",
                                   idempotency_key="s1")
        result = svc.execute_symbol_command(
            "ETHUSDT", SC.ENABLE, "op", idempotency_key="s1")
        assert result.error_code == "IDEMPOTENCY_CONFLICT"
        assert svc.symbol_state("ETHUSDT") is SS.DISABLED

    @pytest.mark.parametrize("symbol", ["", " ", None, 5])
    def test_symbol_required(self, svc, symbol):
        with pytest.raises(OperationControlValidationError):
            svc.execute_symbol_command(symbol, SC.ENABLE, "op")

    def test_command_enum_required(self, svc):
        with pytest.raises(OperationControlValidationError):
            svc.execute_symbol_command("BTCUSDT", "ENABLE", "op")


# ── Yıkıcı eylem koruması ──────────────────────────────────────────

GUARD_CASES = [
    (dict(reason="", confirm_phrase=CONFIRMATION_PHRASE),
     "POLICY_DENIED:reason_required"),
    (dict(reason=None, confirm_phrase=CONFIRMATION_PHRASE),
     "POLICY_DENIED:reason_required"),
    (dict(reason="r", confirm_phrase="onayliyorum"),
     "POLICY_DENIED:confirmation_required"),
    (dict(reason="r", confirm_phrase=""),
     "POLICY_DENIED:confirmation_required"),
    # Boş anahtar idempotency denetiminde CONFLICT olarak yakalanır
    # (guard'a ulaşmadan) — yine fail-closed RED.
    (dict(reason="r", confirm_phrase=CONFIRMATION_PHRASE,
          idempotency_key=""),
     "IDEMPOTENCY_CONFLICT"),
]


class TestDestructiveGuard:
    @pytest.mark.parametrize("over,code", GUARD_CASES)
    def test_stop_new_entries_guard(self, svc, over, code):
        kwargs = dict(DESTR, idempotency_key="g1")
        kwargs.update(over)
        result = svc.stop_new_entries_action("op", **kwargs)
        assert result.status is OAS.DENIED
        assert result.error_code == code
        assert svc.stop_new_entries is False

    @pytest.mark.parametrize("over,code", GUARD_CASES)
    def test_close_all_guard(self, svc, over, code):
        kwargs = dict(DESTR, idempotency_key="g2")
        kwargs.update(over)
        policy, ks = close_context()
        result = svc.request_close_all(
            (open_position(),), ledger_for((open_position(),)),
            policy, ks, "op", **kwargs)
        assert result.status is OAS.DENIED
        assert result.error_code == code

    @pytest.mark.parametrize("over,code", GUARD_CASES)
    def test_kill_switch_engage_guard(self, svc, over, code):
        kwargs = dict(DESTR, idempotency_key="g3")
        kwargs.update(over)
        result = svc.record_kill_switch("op", True, **kwargs)
        assert result.status is OAS.DENIED
        assert result.error_code == code

    @pytest.mark.parametrize("over,code", GUARD_CASES)
    def test_kill_switch_disengage_guard(self, svc, over, code):
        """Kapatmak ticareti yeniden açar — o da guard ister."""
        kwargs = dict(DESTR, idempotency_key="g4")
        kwargs.update(over)
        result = svc.record_kill_switch("op", False, **kwargs)
        assert result.status is OAS.DENIED
        assert result.error_code == code

    def test_kill_switch_disengage_with_guard_ok(self, svc):
        result = svc.record_kill_switch(
            "op", False, idempotency_key="g4", **DESTR)
        assert result.status is OAS.COMPLETED


class TestInputScreening:
    """Yasak belirteçli metin, mutasyondan ÖNCE reddedilir —
    denetim zinciri asla mutasyon sonrası patlamaz."""

    @pytest.mark.parametrize("bad", [
        "my api_key here", "Bearer abc", "token=xyz",
        "x-mbx-apikey leak", "see Traceback below"])
    def test_reason_screened_before_mutation(self, svc, bad):
        with pytest.raises(OperationControlValidationError):
            svc.stop_new_entries_action(
                "op", reason=bad,
                confirm_phrase=CONFIRMATION_PHRASE,
                idempotency_key="scr1")
        assert svc.stop_new_entries is False
        assert len(svc.audit) == 0

    def test_idempotency_key_screened(self, svc):
        with pytest.raises(OperationControlValidationError):
            svc.execute_automation_command(
                AC.START, "op", idempotency_key="secret-key")
        assert svc.automation_state is AS.STOPPED

    def test_actor_screened(self, svc):
        with pytest.raises(OperationControlValidationError):
            svc.execute_automation_command(AC.START,
                                           "op-password")

    def test_kill_switch_reason_screened(self, svc):
        with pytest.raises(OperationControlValidationError):
            svc.record_kill_switch(
                "op", True, reason="api-key rotation",
                confirm_phrase=CONFIRMATION_PHRASE,
                idempotency_key="scr2")


# ── Yeni girişleri durdur ──────────────────────────────────────────

class TestStopNewEntries:
    def test_happy_path(self, svc):
        result = svc.stop_new_entries_action(
            "op", idempotency_key="s1", **DESTR)
        assert result.status is OAS.COMPLETED
        assert svc.stop_new_entries is True
        assert result.current_state == "STOPPED"

    def test_replay(self, svc):
        svc.stop_new_entries_action("op", idempotency_key="s1",
                                    **DESTR)
        replay = svc.stop_new_entries_action(
            "op", idempotency_key="s1", **DESTR)
        assert replay.idempotency_status is \
            IdempotencyStatus.REPLAYED


# ── Kontrollü pozisyon kapatma ─────────────────────────────────────

class TestPositionClose:
    def _close(self, svc, position, key="c1",
               active_kill_switch=False, ledger="AUTO", **over):
        policy, ks = close_context(active_kill_switch)
        if ledger == "AUTO":
            ledger = ledger_for((position,))
        kwargs = dict(DESTR)
        kwargs.update(over)
        return svc.request_position_close(
            position, ledger, policy, ks, "op",
            kwargs["reason"], kwargs["confirm_phrase"], key)

    def test_long_close_accepted(self, svc):
        result = self._close(svc, open_position())
        assert result.status is OAS.ACCEPTED
        assert result.lifecycle_status == "CLOSE_REQUESTED"
        assert result.error_code is None

    def test_short_close_accepted(self, svc):
        position = open_position(side="SELL")
        result = self._close(svc, position)
        assert result.status is OAS.ACCEPTED

    def test_kill_switch_denied(self, svc):
        result = self._close(svc, open_position(),
                             active_kill_switch=True)
        assert result.status is OAS.DENIED
        assert result.error_code == "KILL_SWITCH_DENIED"

    def test_missing_ledger_denied(self, svc):
        result = self._close(svc, open_position(), ledger=None)
        assert result.status is OAS.DENIED
        assert result.error_code == "DEPENDENCY_UNAVAILABLE"

    @pytest.mark.parametrize("over,code", [
        (dict(quantity=None), "POSITION_DATA_INCOMPLETE"),
        (dict(quantity=Decimal("0")), "POSITION_DATA_INCOMPLETE"),
        (dict(side="WEIRD"), "POSITION_DATA_INCOMPLETE"),
        (dict(current_price=None), "DEPENDENCY_UNAVAILABLE"),
        (dict(current_price=Decimal("0")),
         "DEPENDENCY_UNAVAILABLE"),
    ])
    def test_incomplete_position_denied(self, svc, over, code):
        result = self._close(svc, open_position(**over))
        assert result.status is OAS.DENIED
        assert result.error_code == code

    def test_replay_does_not_double_submit(self, svc):
        first = self._close(svc, open_position(), key="c9")
        replay = self._close(svc, open_position(), key="c9")
        assert replay.idempotency_status is \
            IdempotencyStatus.REPLAYED
        assert replay.action_id == first.action_id

    def test_position_type_required(self, svc):
        policy, ks = close_context()
        with pytest.raises(OperationControlValidationError):
            svc.request_position_close(
                {"symbol": "BTCUSDT"}, None, policy, ks, "op",
                "r", CONFIRMATION_PHRASE, "cX")

    def test_audit_contains_denial_code(self, svc):
        self._close(svc, open_position(), ledger=None)
        record = svc.audit.records()[-1]
        assert record.error_code == "DEPENDENCY_UNAVAILABLE"
        assert record.result == "DENIED"


# ── Tümünü kapatma ─────────────────────────────────────────────────

class TestCloseAll:
    def _close_all(self, svc, positions, key="a1"):
        policy, ks = close_context()
        return svc.request_close_all(
            tuple(positions), ledger_for(positions), policy, ks,
            "op", DESTR["reason"], DESTR["confirm_phrase"], key)

    def test_empty_completed(self, svc):
        result = self._close_all(svc, ())
        assert result.status is OAS.COMPLETED
        assert result.detail_codes == ()

    def test_all_accepted(self, svc):
        result = self._close_all(svc, (
            open_position(),
            open_position(position_id="ETHUSDT",
                          symbol="ETHUSDT")))
        assert result.status is OAS.ACCEPTED
        assert len(result.detail_codes) == 2
        assert all(":ACCEPTED:" in d for d in result.detail_codes)

    def test_partial(self, svc):
        result = self._close_all(svc, (
            open_position(),
            open_position(position_id="ETHUSDT",
                          symbol="ETHUSDT", quantity=None)))
        assert result.status is OAS.PARTIAL
        assert result.error_code == "CLOSE_ALL_INCOMPLETE"
        joined = " ".join(result.detail_codes)
        assert ":ACCEPTED:" in joined and ":DENIED:" in joined

    def test_all_failed(self, svc):
        result = self._close_all(svc,
                                 (open_position(quantity=None),))
        assert result.status is OAS.FAILED

    def test_positions_tuple_required(self, svc):
        policy, ks = close_context()
        with pytest.raises(OperationControlValidationError):
            svc.request_close_all(
                [open_position()], None, policy, ks, "op",
                "r", CONFIRMATION_PHRASE, "aX")

    def test_replay(self, svc):
        self._close_all(svc, (open_position(),), key="a7")
        replay = self._close_all(svc, (open_position(),),
                                 key="a7")
        assert replay.idempotency_status is \
            IdempotencyStatus.REPLAYED


# ── Kill-switch köprüsü ────────────────────────────────────────────

class TestKillSwitchBridge:
    def test_engage_blocks_automation(self, svc):
        svc.execute_automation_command(AC.START, "op")
        result = svc.record_kill_switch(
            "op", True, idempotency_key="k1", **DESTR)
        assert result.status is OAS.COMPLETED
        assert svc.automation_state is AS.BLOCKED

    def test_engaged_requires_bool(self, svc):
        with pytest.raises(OperationControlValidationError):
            svc.record_kill_switch("op", "yes",
                                   idempotency_key="k1", **DESTR)

    def test_disengage_does_not_unblock(self, svc):
        """Kill-switch kapatmak otomasyonu OTOMATİK başlatmaz."""
        svc.execute_automation_command(AC.START, "op")
        svc.record_kill_switch("op", True,
                               idempotency_key="k1", **DESTR)
        svc.record_kill_switch("op", False,
                               idempotency_key="k2", **DESTR)
        assert svc.automation_state is AS.BLOCKED

    def test_replay(self, svc):
        svc.record_kill_switch("op", True,
                               idempotency_key="k1", **DESTR)
        replay = svc.record_kill_switch(
            "op", True, idempotency_key="k1", **DESTR)
        assert replay.idempotency_status is \
            IdempotencyStatus.REPLAYED


# ── Denetim zinciri ────────────────────────────────────────────────

def audit_record(**over):
    base = dict(timestamp=1, actor="op", action="A", target="t",
                previous_state="X", requested_state="Y",
                result="OK", reason="r", correlation_id="c-1")
    base.update(over)
    return OperationAuditRecord(**base)


class TestAuditTrail:
    def test_append_and_len(self):
        trail = OperationAuditTrail()
        trail.append(audit_record())
        assert len(trail) == 1

    def test_records_immutable_copy(self):
        trail = OperationAuditTrail()
        trail.append(audit_record())
        assert isinstance(trail.records(), tuple)

    def test_invalid_record_rejected(self):
        trail = OperationAuditTrail()
        with pytest.raises(OperationControlAuditError):
            trail.append({"actor": "x"})

    @pytest.mark.parametrize("token", FORBIDDEN_AUDIT_TOKENS)
    def test_sensitive_content_rejected(self, token):
        trail = OperationAuditTrail()
        with pytest.raises(OperationControlAuditError):
            trail.append(audit_record(reason=f"x {token} y"))

    def test_tail_newest_first(self):
        trail = OperationAuditTrail()
        for i in range(5):
            trail.append(audit_record(correlation_id=f"c-{i}"))
        tail = trail.tail(2)
        assert [r.correlation_id for r in tail] == ["c-4", "c-3"]

    @pytest.mark.parametrize("limit", [0, -1, "5", True, None])
    def test_tail_invalid_limit(self, limit):
        trail = OperationAuditTrail()
        with pytest.raises(OperationControlAuditError):
            trail.tail(limit)

    def test_ring_bounded(self):
        trail = OperationAuditTrail()
        for i in range(5010):
            trail.append(audit_record(correlation_id=f"c-{i}"))
        assert len(trail) == 5000
        assert trail.records()[0].correlation_id == "c-10"
