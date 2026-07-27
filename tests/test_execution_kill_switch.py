"""Mission 2000 — Agent 04 Kill Switch testleri.

Değişmez anlık görüntüler, geçiş kuralları, yasak geçişler, terminal
LOCKED davranışı, sıra determinizmi, kamu API dondurması, yasak
importlar (datetime/uuid/random/dosya sistemi/ağ/broker/execution),
yan etki yokluğu doğrulanır.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import itertools
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import execution_kill_switch
import execution_kill_switch_models
from execution_kill_switch import KillSwitch
from execution_kill_switch_models import (
    KillSwitchReason, KillSwitchSnapshot, KillSwitchState)

STATES = tuple(KillSwitchState)
REASONS = tuple(KillSwitchReason)

ALLOWED = {
    (KillSwitchState.ENABLED, KillSwitchState.DISABLED),
    (KillSwitchState.DISABLED, KillSwitchState.ENABLED),
    (KillSwitchState.DISABLED, KillSwitchState.LOCKED),
    (KillSwitchState.ENABLED, KillSwitchState.MAINTENANCE),
    (KillSwitchState.MAINTENANCE, KillSwitchState.ENABLED),
    (KillSwitchState.LOCKED, KillSwitchState.DISABLED),
}

METHOD_FOR = {
    KillSwitchState.ENABLED: "enable",
    KillSwitchState.DISABLED: "disable",
    KillSwitchState.LOCKED: "lock",
    KillSwitchState.MAINTENANCE: "maintenance",
}

PATH_TO = {
    KillSwitchState.DISABLED: (),
    KillSwitchState.ENABLED: ("enable",),
    KillSwitchState.LOCKED: ("lock",),
    KillSwitchState.MAINTENANCE: ("enable", "maintenance"),
}


def _switch_in(state: KillSwitchState) -> KillSwitch:
    switch = KillSwitch()
    for method in PATH_TO[state]:
        getattr(switch, method)()
    return switch


# ── Durum modeli ─────────────────────────────────────────────────────

class TestStateModel:
    def test_states_closed(self):
        assert tuple(s.name for s in KillSwitchState) == (
            "ENABLED", "DISABLED", "LOCKED", "MAINTENANCE")

    def test_reasons_closed(self):
        assert tuple(r.name for r in KillSwitchReason) == (
            "MANUAL", "RISK_LIMIT", "SYSTEM_FAILURE",
            "BROKER_FAILURE", "NETWORK_FAILURE", "DEPLOYMENT",
            "REGULATORY", "UNKNOWN")

    @pytest.mark.parametrize("state", STATES)
    def test_state_values_equal_names(self, state):
        assert state.value == state.name

    @pytest.mark.parametrize("reason", REASONS)
    def test_reason_values_equal_names(self, reason):
        assert reason.value == reason.name

    def test_initial_state_safe_disabled(self):
        switch = KillSwitch()
        assert switch.current_state() is KillSwitchState.DISABLED
        assert switch.is_execution_allowed() is False


# ── Değişmez anlık görüntüler ────────────────────────────────────────

class TestImmutableSnapshots:
    def _snapshot(self):
        return KillSwitchSnapshot(state=KillSwitchState.ENABLED,
                                  reason=KillSwitchReason.MANUAL,
                                  timestamp=1, sequence_id=1)

    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            self._snapshot().state = KillSwitchState.LOCKED

    def test_slots(self):
        assert not hasattr(self._snapshot(), "__dict__")

    def test_hashable_and_value_equal(self):
        assert isinstance(hash(self._snapshot()), int)
        assert self._snapshot() == self._snapshot()

    @pytest.mark.parametrize("field,bad", [
        ("state", "ENABLED"), ("state", None),
        ("reason", "MANUAL"), ("reason", None),
        ("timestamp", 1.0), ("timestamp", True),
        ("timestamp", -1), ("timestamp", None),
        ("sequence_id", 1.0), ("sequence_id", True),
        ("sequence_id", -5), ("sequence_id", "1"),
    ])
    def test_sterile_validation(self, field, bad):
        kwargs = dict(state=KillSwitchState.ENABLED,
                      reason=KillSwitchReason.MANUAL,
                      timestamp=1, sequence_id=1)
        kwargs[field] = bad
        with pytest.raises(ValueError,
                           match="INVALID_KILLSWITCH_INPUT"):
            KillSwitchSnapshot(**kwargs)

    def test_history_is_tuple_of_snapshots(self):
        switch = KillSwitch()
        switch.enable()
        history = switch._snapshots()
        assert isinstance(history, tuple)
        assert all(isinstance(s, KillSwitchSnapshot)
                   for s in history)

    def test_history_grows_never_mutates(self):
        switch = KillSwitch()
        before = switch._snapshots()
        switch.enable()
        after = switch._snapshots()
        assert before == after[:len(before)]
        assert len(after) == len(before) + 1

    def test_every_transition_new_snapshot(self):
        switch = KillSwitch()
        first = switch.enable()
        second = switch.disable()
        assert first is not second
        assert first.sequence_id + 1 == second.sequence_id

    def test_snapshot_records_reason(self):
        switch = KillSwitch()
        switch.enable()
        snapshot = switch.disable(KillSwitchReason.RISK_LIMIT)
        assert snapshot.reason is KillSwitchReason.RISK_LIMIT

    def test_logical_timestamp_equals_sequence(self):
        switch = KillSwitch()
        switch.enable()
        switch.maintenance()
        for snapshot in switch._snapshots():
            assert snapshot.timestamp == snapshot.sequence_id


# ── Geçiş tablosu ────────────────────────────────────────────────────

class TestTransitionTable:
    @pytest.mark.parametrize("source,target",
                             sorted(ALLOWED,
                                    key=lambda p: (p[0].name,
                                                   p[1].name)))
    def test_allowed_transitions(self, source, target):
        switch = _switch_in(source)
        snapshot = getattr(switch, METHOD_FOR[target])()
        assert snapshot.state is target
        assert switch.current_state() is target

    @pytest.mark.parametrize(
        "source,target",
        sorted(((s, t) for s, t in itertools.product(STATES, STATES)
                if (s, t) not in ALLOWED),
               key=lambda p: (p[0].name, p[1].name)))
    def test_forbidden_transitions(self, source, target):
        switch = _switch_in(source)
        with pytest.raises(ValueError,
                           match="INVALID_KILLSWITCH_TRANSITION"):
            getattr(switch, METHOD_FOR[target])()
        assert switch.current_state() is source

    def test_locked_terminal_no_direct_enable(self):
        switch = _switch_in(KillSwitchState.LOCKED)
        with pytest.raises(ValueError,
                           match="INVALID_KILLSWITCH_TRANSITION"):
            switch.enable()
        with pytest.raises(ValueError,
                           match="INVALID_KILLSWITCH_TRANSITION"):
            switch.maintenance()

    def test_locked_recovery_path(self):
        switch = _switch_in(KillSwitchState.LOCKED)
        switch.disable()
        switch.enable()
        assert switch.is_execution_allowed() is True

    def test_locked_to_enabled_only_via_two_step_recovery(self):
        # Kanonik değişmez: LOCKED'dan ENABLED'a TEK yol
        # LOCKED→DISABLED→ENABLED'dır; tek adım imkânsızdır.
        switch = _switch_in(KillSwitchState.LOCKED)
        for method in ("enable", "maintenance", "lock"):
            with pytest.raises(
                    ValueError,
                    match="INVALID_KILLSWITCH_TRANSITION"):
                getattr(switch, method)()
        assert switch.is_execution_allowed() is False
        switch.disable()
        assert switch.is_execution_allowed() is False
        switch.enable()
        assert switch.is_execution_allowed() is True
        states = [s.state for s in switch._snapshots()[-3:]]
        assert states == [KillSwitchState.LOCKED,
                          KillSwitchState.DISABLED,
                          KillSwitchState.ENABLED]

    def test_no_self_transitions(self):
        for state in STATES:
            switch = _switch_in(state)
            with pytest.raises(
                    ValueError,
                    match="INVALID_KILLSWITCH_TRANSITION"):
                getattr(switch, METHOD_FOR[state])()

    def test_failed_transition_no_snapshot(self):
        switch = _switch_in(KillSwitchState.LOCKED)
        before = switch._snapshots()
        with pytest.raises(ValueError):
            switch.enable()
        assert switch._snapshots() == before

    def test_transition_table_frozen_mapping(self):
        table = execution_kill_switch_models._APPROVED_TRANSITIONS
        with pytest.raises(TypeError):
            table[KillSwitchState.LOCKED] = frozenset()
        assert set(table.keys()) == set(STATES)
        for targets in table.values():
            assert isinstance(targets, frozenset)

    def test_transition_table_matches_spec(self):
        table = execution_kill_switch_models._APPROVED_TRANSITIONS
        derived = {(s, t) for s, targets in table.items()
                   for t in targets}
        assert derived == ALLOWED


# ── Yürütme yetkisi ──────────────────────────────────────────────────

class TestExecutionAuthority:
    @pytest.mark.parametrize("state,expected", [
        (KillSwitchState.ENABLED, True),
        (KillSwitchState.DISABLED, False),
        (KillSwitchState.LOCKED, False),
        (KillSwitchState.MAINTENANCE, False),
    ])
    def test_only_enabled_allows_execution(self, state, expected):
        assert _switch_in(state).is_execution_allowed() is expected

    def test_no_override_api(self):
        # Hiçbir kamu üyesi durumu geçiş tablosu dışında değiştiremez
        public = [name for name in dir(KillSwitch)
                  if not name.startswith("_")]
        assert sorted(public) == [
            "current_state", "disable", "enable",
            "is_execution_allowed", "lock", "maintenance"]

    def test_instance_attributes_locked(self):
        switch = KillSwitch()
        with pytest.raises(AttributeError):
            switch._history = ()
        with pytest.raises(AttributeError):
            switch.force_enabled = True
        with pytest.raises(AttributeError):
            del switch._history

    @pytest.mark.parametrize("bad", ["MANUAL", 1, None, object()])
    def test_arbitrary_reason_rejected(self, bad):
        switch = KillSwitch()
        with pytest.raises(ValueError,
                           match="INVALID_KILLSWITCH_INPUT"):
            switch.enable(bad)

    @pytest.mark.parametrize("reason", REASONS)
    def test_all_reasons_accepted(self, reason):
        switch = KillSwitch()
        assert switch.enable(reason).reason is reason


# ── Determinizm ──────────────────────────────────────────────────────

class TestDeterminism:
    SEQUENCES = [
        ("enable", "disable", "enable"),
        ("enable", "maintenance", "enable", "disable"),
        ("lock", "disable", "enable"),
        ("enable", "disable", "lock", "disable", "enable"),
    ]

    @pytest.mark.parametrize("sequence", SEQUENCES)
    def test_same_sequence_same_final_state(self, sequence):
        results = []
        for _ in range(3):
            switch = KillSwitch()
            for method in sequence:
                getattr(switch, method)()
            results.append((switch.current_state(),
                            switch._snapshots()))
        assert results[0] == results[1] == results[2]

    @pytest.mark.parametrize("sequence", SEQUENCES)
    def test_sequence_ids_strictly_monotonic(self, sequence):
        switch = KillSwitch()
        for method in sequence:
            getattr(switch, method)()
        ids = [s.sequence_id for s in switch._snapshots()]
        assert ids == list(range(len(ids)))

    def test_independent_instances_no_shared_state(self):
        first, second = KillSwitch(), KillSwitch()
        first.enable()
        assert second.current_state() is KillSwitchState.DISABLED
        assert len(second._snapshots()) == 1

    def test_read_methods_no_side_effects(self):
        switch = _switch_in(KillSwitchState.ENABLED)
        before = switch._snapshots()
        for _ in range(5):
            switch.is_execution_allowed()
            switch.current_state()
        assert switch._snapshots() == before


# ── Kamu API dondurması ve güvenlik ──────────────────────────────────

def _module_imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


class TestPublicApiAndSafety:
    def test_switch_public_surface(self):
        assert execution_kill_switch.__all__ == ["KillSwitch"]

    def test_models_public_surface(self):
        assert set(execution_kill_switch_models.__all__) == {
            "KillSwitchState", "KillSwitchReason",
            "KillSwitchSnapshot"}

    def test_no_additional_public_callables(self):
        for module in (execution_kill_switch,
                       execution_kill_switch_models):
            public = {name for name, value in vars(module).items()
                      if not name.startswith("_")
                      and (inspect.isfunction(value)
                           or inspect.isclass(value))
                      and getattr(value, "__module__", None)
                      == module.__name__}
            assert public <= set(module.__all__)

    @pytest.mark.parametrize("module", [
        execution_kill_switch, execution_kill_switch_models])
    def test_no_forbidden_imports(self, module):
        roots = {m.split(".")[0] for m in _module_imports(module)}
        forbidden = {"datetime", "time", "uuid", "random",
                     "secrets", "os", "sys", "io", "pathlib",
                     "socket", "ssl", "http", "requests", "httpx",
                     "urllib", "urllib3", "aiohttp", "websocket",
                     "websockets", "threading", "asyncio",
                     "subprocess", "sqlite3", "pickle", "shelve",
                     "ccxt", "binance",
                     "broker_adapter", "binance_spot_adapter",
                     "execution_api", "execution_service",
                     "execution_risk_engine", "execution_models"}
        assert not roots & forbidden

    @pytest.mark.parametrize("module", [
        execution_kill_switch, execution_kill_switch_models])
    def test_allowed_imports_only(self, module):
        allowed = {"__future__", "enum", "dataclasses", "types",
                   "typing", "execution_kill_switch_models"}
        assert _module_imports(module) <= allowed

    @pytest.mark.parametrize("token", [
        "datetime.now", "time.time", "uuid4", "uuid1",
        "random.", "urandom", "open(", "requests.",
        "http://", "https://"])
    def test_no_wallclock_uuid_randomness_io(self, token):
        for module in (execution_kill_switch,
                       execution_kill_switch_models):
            assert token not in inspect.getsource(module)

    @pytest.mark.parametrize("token", [
        "place_order", "submit_order", "cancel_order",
        "modify_order", "execute_trade", "BrokerAdapter",
        "Binance", "binance"])
    def test_no_execution_or_broker_capability(self, token):
        for module in (execution_kill_switch,
                       execution_kill_switch_models):
            assert token not in inspect.getsource(module)

    @pytest.mark.parametrize("module", [
        execution_kill_switch, execution_kill_switch_models])
    def test_no_dangerous_calls(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "open", "__import__",
                    "compile")

    def test_no_float_literals(self):
        for module in (execution_kill_switch,
                       execution_kill_switch_models):
            for node in ast.walk(ast.parse(
                    inspect.getsource(module))):
                if isinstance(node, ast.Constant):
                    assert not isinstance(node.value, float)

    def test_kill_switch_uses_slots(self):
        assert KillSwitch.__slots__ == ("_history",)
        assert not hasattr(KillSwitch(), "__dict__")

    @pytest.mark.parametrize("state", STATES)
    @pytest.mark.parametrize("reason", REASONS)
    def test_snapshot_valid_for_all_state_reason_pairs(
            self, state, reason):
        snapshot = KillSwitchSnapshot(state=state, reason=reason,
                                      timestamp=3, sequence_id=3)
        assert snapshot.state is state
        assert snapshot.reason is reason
        assert snapshot == KillSwitchSnapshot(
            state=state, reason=reason, timestamp=3, sequence_id=3)

    @pytest.mark.parametrize("source,target",
                             sorted(ALLOWED,
                                    key=lambda p: (p[0].name,
                                                   p[1].name)))
    def test_transition_appends_exactly_one_snapshot(
            self, source, target):
        switch = _switch_in(source)
        before = len(switch._snapshots())
        getattr(switch, METHOD_FOR[target])()
        assert len(switch._snapshots()) == before + 1
        assert switch._snapshots()[-1].state is target

    def test_snapshot_fields_closed(self):
        assert tuple(f.name for f in dataclasses.fields(
            KillSwitchSnapshot)) == (
            "state", "reason", "timestamp", "sequence_id")
