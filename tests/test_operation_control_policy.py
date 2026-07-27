"""Mission 2200 — Agent 01: politika testleri.

Kapalı geçiş tabloları, fail-closed varsayılanlar ve otomatik
yürütme ön koşulları. Tablo dışı geçiş → KeyError (409 kaynağı).
"""

import pytest

import operation_control_policy as pol
from operation_control_models import (
    AutomationCommand as AC, AutomationState as AS,
    DataFreshness, ReconciliationState,
    SymbolAutomationState as SS, SymbolCommand as SC)


# ── Fail-closed varsayılanlar ───────────────────────────────────────

class TestDefaults:
    def test_execution_mode_paper(self):
        assert pol.DEFAULT_EXECUTION_MODE == "PAPER"

    def test_automation_stopped(self):
        assert pol.DEFAULT_AUTOMATION_STATE is AS.STOPPED

    def test_symbol_disabled(self):
        assert pol.DEFAULT_SYMBOL_STATE is SS.DISABLED

    def test_live_authorization_denied(self):
        assert pol.DEFAULT_LIVE_AUTHORIZATION == "DENIED"

    def test_close_all_live_denied(self):
        assert pol.DEFAULT_CLOSE_ALL_LIVE == "DENIED"

    def test_destructive_actions_closed(self):
        assert pol.DESTRUCTIVE_ACTIONS == (
            "GLOBAL_STOP_NEW_ENTRIES", "GLOBAL_REQUEST_CLOSE_ALL",
            "GLOBAL_KILL_SWITCH")

    def test_transitions_immutable(self):
        with pytest.raises(TypeError):
            pol.AUTOMATION_TRANSITIONS[AC.START] = ((), AS.RUNNING)

    def test_symbol_transitions_immutable(self):
        with pytest.raises(TypeError):
            pol.SYMBOL_TRANSITIONS[SC.ENABLE] = ((), SS.ENABLED)


# ── Otomasyon geçiş matrisi (tam kapsam) ────────────────────────────

# (durum, komut) → beklenen: hedef durum | "IDEMPOTENT" | None (=409)
AUTOMATION_MATRIX = {
    (AS.STOPPED, AC.START): AS.RUNNING,
    (AS.STOPPED, AC.PAUSE): None,
    (AS.STOPPED, AC.RESUME): None,
    (AS.STOPPED, AC.STOP): "IDEMPOTENT",
    (AS.STARTING, AC.START): None,
    (AS.STARTING, AC.PAUSE): AS.PAUSED,
    (AS.STARTING, AC.RESUME): None,
    (AS.STARTING, AC.STOP): AS.STOPPED,
    (AS.RUNNING, AC.START): "IDEMPOTENT",
    (AS.RUNNING, AC.PAUSE): AS.PAUSED,
    (AS.RUNNING, AC.RESUME): "IDEMPOTENT",
    (AS.RUNNING, AC.STOP): AS.STOPPED,
    (AS.PAUSING, AC.START): None,
    (AS.PAUSING, AC.PAUSE): None,
    (AS.PAUSING, AC.RESUME): AS.RUNNING,
    (AS.PAUSING, AC.STOP): AS.STOPPED,
    (AS.PAUSED, AC.START): None,
    (AS.PAUSED, AC.PAUSE): "IDEMPOTENT",
    (AS.PAUSED, AC.RESUME): AS.RUNNING,
    (AS.PAUSED, AC.STOP): AS.STOPPED,
    (AS.STOPPING, AC.START): None,
    (AS.STOPPING, AC.PAUSE): None,
    (AS.STOPPING, AC.RESUME): None,
    (AS.STOPPING, AC.STOP): AS.STOPPED,
    (AS.BLOCKED, AC.START): None,       # kill-switch baypası YOK
    (AS.BLOCKED, AC.PAUSE): None,
    (AS.BLOCKED, AC.RESUME): None,
    (AS.BLOCKED, AC.STOP): AS.STOPPED,
    (AS.ERROR, AC.START): None,
    (AS.ERROR, AC.PAUSE): None,
    (AS.ERROR, AC.RESUME): None,
    (AS.ERROR, AC.STOP): AS.STOPPED,
}


class TestAutomationTransitionMatrix:
    @pytest.mark.parametrize("state,command",
                             sorted(AUTOMATION_MATRIX,
                                    key=lambda k: (k[0].value,
                                                   k[1].value)))
    def test_cell(self, state, command):
        expected = AUTOMATION_MATRIX[(state, command)]
        if expected is None:
            with pytest.raises(KeyError):
                pol.resolve_transition(state, command)
        elif expected == "IDEMPOTENT":
            target, repeat = pol.resolve_transition(state, command)
            assert target is state and repeat is True
        else:
            target, repeat = pol.resolve_transition(state, command)
            assert target is expected and repeat is False

    def test_matrix_is_exhaustive(self):
        assert len(AUTOMATION_MATRIX) == len(AS) * len(AC)

    @pytest.mark.parametrize("state,command", [
        ("STOPPED", AC.START), (AS.STOPPED, "START"), (None, None)])
    def test_invalid_input_types(self, state, command):
        with pytest.raises(KeyError):
            pol.resolve_transition(state, command)


SYMBOL_MATRIX = {
    (SS.DISABLED, SC.ENABLE): SS.ENABLED,
    (SS.DISABLED, SC.PAUSE): None,
    (SS.DISABLED, SC.RESUME): None,
    (SS.DISABLED, SC.STOP): SS.STOPPED,
    (SS.ENABLED, SC.ENABLE): "IDEMPOTENT",
    (SS.ENABLED, SC.PAUSE): SS.PAUSED,
    (SS.ENABLED, SC.RESUME): "IDEMPOTENT",
    (SS.ENABLED, SC.STOP): SS.STOPPED,
    (SS.PAUSED, SC.ENABLE): None,
    (SS.PAUSED, SC.PAUSE): "IDEMPOTENT",
    (SS.PAUSED, SC.RESUME): SS.ENABLED,
    (SS.PAUSED, SC.STOP): SS.STOPPED,
    (SS.STOPPED, SC.ENABLE): SS.ENABLED,
    (SS.STOPPED, SC.PAUSE): None,
    (SS.STOPPED, SC.RESUME): None,
    (SS.STOPPED, SC.STOP): "IDEMPOTENT",
}


class TestSymbolTransitionMatrix:
    @pytest.mark.parametrize("state,command",
                             sorted(SYMBOL_MATRIX,
                                    key=lambda k: (k[0].value,
                                                   k[1].value)))
    def test_cell(self, state, command):
        expected = SYMBOL_MATRIX[(state, command)]
        if expected is None:
            with pytest.raises(KeyError):
                pol.resolve_symbol_transition(state, command)
        elif expected == "IDEMPOTENT":
            target, repeat = pol.resolve_symbol_transition(
                state, command)
            assert target is state and repeat is True
        else:
            target, repeat = pol.resolve_symbol_transition(
                state, command)
            assert target is expected and repeat is False

    def test_matrix_is_exhaustive(self):
        assert len(SYMBOL_MATRIX) == len(SS) * len(SC)

    def test_invalid_input_types(self):
        with pytest.raises(KeyError):
            pol.resolve_symbol_transition("ENABLED", SC.ENABLE)


# ── Güvenlik bağımlılıkları ────────────────────────────────────────

def healthy_deps():
    return {name: "OK" for name in
            pol.REQUIRED_SAFETY_DEPENDENCIES}


class TestSafetyDependencies:
    def test_required_set(self):
        assert pol.REQUIRED_SAFETY_DEPENDENCIES == (
            "permission_gate", "risk_engine", "kill_switch",
            "ledger", "lifecycle", "reconciliation")

    def test_all_healthy(self):
        assert pol.evaluate_safety_dependencies(healthy_deps()) == ()

    @pytest.mark.parametrize("value", ["OK", "PASS", "READY",
                                       "ok", "ready"])
    def test_healthy_values(self, value):
        deps = {name: value for name in
                pol.REQUIRED_SAFETY_DEPENDENCIES}
        assert pol.evaluate_safety_dependencies(deps) == ()

    @pytest.mark.parametrize("missing",
                             pol.REQUIRED_SAFETY_DEPENDENCIES)
    def test_each_missing_flagged(self, missing):
        deps = healthy_deps()
        del deps[missing]
        assert pol.evaluate_safety_dependencies(deps) == (
            f"DEPENDENCY_UNAVAILABLE:{missing}",)

    @pytest.mark.parametrize("bad", ["UNKNOWN", "STALE", "FAIL",
                                     "", None, 1, True])
    def test_unhealthy_value_flagged(self, bad):
        deps = healthy_deps()
        deps["ledger"] = bad
        assert pol.evaluate_safety_dependencies(deps) == (
            "DEPENDENCY_UNAVAILABLE:ledger",)

    @pytest.mark.parametrize("junk", [None, "x", 5, ["a"]])
    def test_non_mapping_all_flagged(self, junk):
        findings = pol.evaluate_safety_dependencies(junk)
        assert len(findings) == 6


# ── Otomatik yürütme ön koşulları ──────────────────────────────────

def allowed_kwargs():
    return dict(
        automation_state=AS.RUNNING, execution_mode="PAPER",
        symbol_state=SS.ENABLED, stop_new_entries=False,
        kill_switch_active=False, dependencies=healthy_deps(),
        reconciliation_state=ReconciliationState.MATCHED,
        data_freshness=DataFreshness.FRESH, candidate_exists=True,
        intent_normalized=True, authorization_valid=True,
        permission_pass=True, risk_pass=True, cooldown_pass=True,
        idempotency_pass=True)


class TestCanExecuteAutomatically:
    def test_all_pass(self):
        assert pol.can_execute_automatically(**allowed_kwargs()) == ()

    @pytest.mark.parametrize("override,expected_code", [
        ({"automation_state": AS.PAUSED}, "AUTOMATION_NOT_RUNNING"),
        ({"automation_state": AS.BLOCKED}, "AUTOMATION_NOT_RUNNING"),
        ({"automation_state": AS.STOPPED}, "AUTOMATION_NOT_RUNNING"),
        ({"execution_mode": "LIVE"}, "EXECUTION_MODE_NOT_PERMITTED"),
        ({"execution_mode": ""}, "EXECUTION_MODE_NOT_PERMITTED"),
        ({"symbol_state": SS.DISABLED}, "SYMBOL_AUTOMATION_DISABLED"),
        ({"symbol_state": SS.PAUSED}, "SYMBOL_AUTOMATION_DISABLED"),
        ({"symbol_state": SS.STOPPED}, "SYMBOL_AUTOMATION_DISABLED"),
        ({"stop_new_entries": True}, "NEW_ENTRIES_STOPPED"),
        ({"kill_switch_active": True}, "KILL_SWITCH_ACTIVE"),
        ({"reconciliation_state": ReconciliationState.UNKNOWN},
         "RECONCILIATION_BLOCKING"),
        ({"reconciliation_state": ReconciliationState.STALE},
         "RECONCILIATION_BLOCKING"),
        ({"reconciliation_state": ReconciliationState.MISMATCH},
         "RECONCILIATION_BLOCKING"),
        ({"reconciliation_state": ReconciliationState.ERROR},
         "RECONCILIATION_BLOCKING"),
        ({"data_freshness": DataFreshness.STALE}, "DATA_NOT_FRESH"),
        ({"data_freshness": DataFreshness.UNKNOWN}, "DATA_NOT_FRESH"),
        ({"candidate_exists": False}, "NO_ELIGIBLE_CANDIDATE"),
        ({"intent_normalized": False}, "INTENT_NOT_NORMALIZED"),
        ({"authorization_valid": False}, "AUTHORIZATION_INVALID"),
        ({"permission_pass": False}, "PERMISSION_GATE_FAIL"),
        ({"risk_pass": False}, "RISK_ENGINE_FAIL"),
        ({"cooldown_pass": False}, "COOLDOWN_FAIL"),
        ({"idempotency_pass": False}, "IDEMPOTENCY_FAIL"),
    ])
    def test_single_failure_blocks(self, override, expected_code):
        kwargs = allowed_kwargs()
        kwargs.update(override)
        assert pol.can_execute_automatically(**kwargs) == \
            (expected_code,)

    @pytest.mark.parametrize("fieldname", [
        "candidate_exists", "intent_normalized",
        "authorization_valid", "permission_pass", "risk_pass",
        "cooldown_pass", "idempotency_pass"])
    def test_unknown_truthy_not_accepted(self, fieldname):
        """Fail-open yolu yok: bool True dışındaki her değer engel."""
        kwargs = allowed_kwargs()
        kwargs[fieldname] = "yes"
        assert len(pol.can_execute_automatically(**kwargs)) == 1

    def test_missing_dependency_blocks(self):
        kwargs = allowed_kwargs()
        kwargs["dependencies"] = {}
        findings = pol.can_execute_automatically(**kwargs)
        assert len(findings) == 6
        assert all(f.startswith("DEPENDENCY_UNAVAILABLE:")
                   for f in findings)

    def test_everything_wrong_reports_all(self):
        findings = pol.can_execute_automatically(
            automation_state=AS.STOPPED, execution_mode="LIVE",
            symbol_state=SS.DISABLED, stop_new_entries=True,
            kill_switch_active=True, dependencies={},
            reconciliation_state=ReconciliationState.UNKNOWN,
            data_freshness=DataFreshness.UNKNOWN,
            candidate_exists=False, intent_normalized=False,
            authorization_valid=False, permission_pass=False,
            risk_pass=False, cooldown_pass=False,
            idempotency_pass=False)
        assert len(findings) == 20  # 14 koşul + 6 bağımlılık

    def test_new_entries_helper(self):
        assert pol.automation_allows_new_entries(AS.RUNNING) is True
        for state in AS:
            if state is not AS.RUNNING:
                assert pol.automation_allows_new_entries(state) \
                    is False
