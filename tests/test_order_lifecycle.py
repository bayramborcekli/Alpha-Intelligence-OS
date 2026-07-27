"""Mission 2100 — Agent 07: Emir yaşam döngüsü testleri.

Kapsam: tam geçiş matrisi (9 işlem × 10 durum), sözleşme
doğrulaması, monotonik mantıksal sıra, tam-dolum kuralı, olay ve
denetim birikimi, model değişmezliği ve determinizm.
"""

import sys
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution_enums import OrderSide  # noqa: E402
from lifecycle_models import (LifecycleAudit,  # noqa: E402
                              LifecycleEvent, LifecycleOperation,
                              OrderLifecycle, OrderLifecycleState)
from order_lifecycle import (TERMINAL_STATES,  # noqa: E402
                             TRANSITION_MATRIX,
                             OrderLifecycleService)
from reconciliation_errors import (  # noqa: E402
    LifecycleContractError, LifecycleTransitionError,
    ReconciliationError)

S = OrderLifecycleState
O = LifecycleOperation  # noqa: E741

SERVICE = OrderLifecycleService()

ALL_STATES = tuple(S)

# (metot adı, işlem, izinli kaynaklar, hedef)
OPERATIONS = (
    ("validate", O.VALIDATE, (S.NEW,), S.VALIDATED),
    ("accept", O.ACCEPT, (S.VALIDATED,), S.ACCEPTED),
    ("queue", O.QUEUE, (S.ACCEPTED,), S.QUEUED),
    ("submit", O.SUBMIT, (S.ACCEPTED, S.QUEUED), S.SUBMITTED),
    ("fill", O.FILL, (S.SUBMITTED,), S.FILLED),
    ("cancel", O.CANCEL, (S.ACCEPTED, S.QUEUED, S.SUBMITTED),
     S.CANCELLED),
    ("reject", O.REJECT, (S.NEW, S.VALIDATED, S.ACCEPTED,
                          S.QUEUED), S.REJECTED),
    ("fail", O.FAIL, (S.SUBMITTED,), S.FAILED),
    ("close", O.CLOSE, (S.FILLED, S.CANCELLED, S.REJECTED,
                        S.FAILED), S.CLOSED),
)


def make_lifecycle(state=S.NEW, sequence=10):
    return OrderLifecycle(
        order_reference="ORD-1", symbol="BTCUSDT",
        side=OrderSide.BUY, quantity=Decimal("1.5"),
        price=Decimal("100"), state=state,
        logical_sequence=sequence)


def invoke(method, lifecycle, reference="EVT-1", sequence=11):
    if method == "fill":
        quantity = Decimal("1.5")
        if isinstance(lifecycle, OrderLifecycle):
            quantity = lifecycle.quantity
        return SERVICE.fill(lifecycle, reference, sequence,
                            fill_quantity=quantity,
                            fill_price=Decimal("101"))
    return getattr(SERVICE, method)(lifecycle, reference,
                                    sequence)


MATRIX_CASES = []
for _method, _op, _allowed, _target in OPERATIONS:
    for _state in ALL_STATES:
        MATRIX_CASES.append(
            (_method, _op, _state, _state in _allowed, _target))


class TestTransitionMatrix:
    @pytest.mark.parametrize(
        "method,operation,state,valid,target", MATRIX_CASES)
    def test_full_matrix(self, method, operation, state, valid,
                         target):
        lifecycle = make_lifecycle(state=state)
        if valid:
            result = invoke(method, lifecycle)
            assert result.state is target
            assert result.events[-1].operation is operation
            assert result.events[-1].from_state is state
            assert result.events[-1].to_state is target
        else:
            with pytest.raises(LifecycleTransitionError) as info:
                invoke(method, lifecycle)
            assert str(info.value) == (
                f"INVALID_LIFECYCLE_TRANSITION:{state.value}"
                f":{operation.value}")

    @pytest.mark.parametrize("method,operation,allowed,target",
                             OPERATIONS)
    def test_matrix_constant_matches_operations(
            self, method, operation, allowed, target):
        matrix_allowed, matrix_target = TRANSITION_MATRIX[
            operation]
        assert matrix_allowed == allowed
        assert matrix_target is target

    def test_matrix_covers_every_operation(self):
        assert set(TRANSITION_MATRIX.keys()) == set(O)

    def test_closed_is_only_terminal_state(self):
        reachable_sources = set()
        for allowed, _target in TRANSITION_MATRIX.values():
            reachable_sources.update(allowed)
        assert set(ALL_STATES) - reachable_sources == {S.CLOSED}
        assert TERMINAL_STATES == (S.CLOSED,)

    @pytest.mark.parametrize("state", ALL_STATES)
    def test_every_state_reachable_or_initial(self, state):
        if state is S.NEW:
            return
        targets = set()
        for _allowed, target in TRANSITION_MATRIX.values():
            targets.add(target)
        assert state in targets

    def test_matrix_is_read_only(self):
        with pytest.raises(TypeError):
            TRANSITION_MATRIX[O.VALIDATE] = ((), S.NEW)


class TestSequenceMonotonicity:
    @pytest.mark.parametrize("method",
                             [c[0] for c in OPERATIONS])
    @pytest.mark.parametrize("sequence", [10, 9, 0])
    def test_non_monotonic_sequence_rejected(self, method,
                                             sequence):
        _m, _o, allowed, _t = next(
            c[1:] for c in OPERATIONS if c[0] == method), None, \
            None, None
        operation_row = next(c for c in OPERATIONS
                             if c[0] == method)
        lifecycle = make_lifecycle(state=operation_row[2][0],
                                   sequence=10)
        with pytest.raises(LifecycleTransitionError) as info:
            invoke(method, lifecycle, sequence=sequence)
        assert "NON_MONOTONIC_SEQUENCE" in str(info.value)

    @pytest.mark.parametrize("method",
                             [c[0] for c in OPERATIONS])
    @pytest.mark.parametrize("sequence",
                             [None, "11", 1.5, True, -1])
    def test_invalid_sequence_type_rejected(self, method,
                                            sequence):
        operation_row = next(c for c in OPERATIONS
                             if c[0] == method)
        lifecycle = make_lifecycle(state=operation_row[2][0])
        with pytest.raises(ReconciliationError):
            invoke(method, lifecycle, sequence=sequence)

    def test_result_carries_new_sequence(self):
        lifecycle = make_lifecycle()
        result = SERVICE.validate(lifecycle, "EVT-1", 42)
        assert result.logical_sequence == 42


class TestContractValidation:
    @pytest.mark.parametrize("method",
                             [c[0] for c in OPERATIONS])
    def test_non_lifecycle_rejected(self, method):
        with pytest.raises(LifecycleContractError) as info:
            invoke(method, "not-a-lifecycle")
        assert str(info.value) == \
            "INVALID_LIFECYCLE_FIELD:lifecycle"

    @pytest.mark.parametrize("method",
                             [c[0] for c in OPERATIONS])
    @pytest.mark.parametrize("reference", [None, "", "  ", 7])
    def test_invalid_event_reference_rejected(self, method,
                                              reference):
        operation_row = next(c for c in OPERATIONS
                             if c[0] == method)
        lifecycle = make_lifecycle(state=operation_row[2][0])
        with pytest.raises(LifecycleContractError) as info:
            invoke(method, lifecycle, reference=reference)
        assert str(info.value) == \
            "INVALID_LIFECYCLE_FIELD:event_reference"


class TestFill:
    def test_full_fill_sets_filled_fields(self):
        lifecycle = make_lifecycle(state=S.SUBMITTED)
        result = SERVICE.fill(lifecycle, "EVT-F", 11,
                              fill_quantity=Decimal("1.5"),
                              fill_price=Decimal("99.5"))
        assert result.state is S.FILLED
        assert result.filled_quantity == Decimal("1.5")
        assert result.filled_price == Decimal("99.5")

    @pytest.mark.parametrize("quantity",
                             [Decimal("1.4"), Decimal("1.6"),
                              Decimal("0.1"), Decimal("3")])
    def test_partial_fill_rejected(self, quantity):
        lifecycle = make_lifecycle(state=S.SUBMITTED)
        with pytest.raises(LifecycleTransitionError) as info:
            SERVICE.fill(lifecycle, "EVT-F", 11,
                         fill_quantity=quantity,
                         fill_price=Decimal("100"))
        assert "PARTIAL_FILL_UNSUPPORTED" in str(info.value)

    @pytest.mark.parametrize(
        "quantity", [None, "1.5", 1.5, Decimal("0"),
                     Decimal("-1"), Decimal("NaN"), True])
    def test_invalid_fill_quantity_rejected(self, quantity):
        lifecycle = make_lifecycle(state=S.SUBMITTED)
        with pytest.raises(LifecycleContractError):
            SERVICE.fill(lifecycle, "EVT-F", 11,
                         fill_quantity=quantity,
                         fill_price=Decimal("100"))

    @pytest.mark.parametrize(
        "price", [None, "100", 100.0, Decimal("0"),
                  Decimal("-5"), Decimal("Infinity"), False])
    def test_invalid_fill_price_rejected(self, price):
        lifecycle = make_lifecycle(state=S.SUBMITTED)
        with pytest.raises(LifecycleContractError):
            SERVICE.fill(lifecycle, "EVT-F", 11,
                         fill_quantity=Decimal("1.5"),
                         fill_price=price)

    def test_fill_uses_decimal_exact_equality(self):
        lifecycle = make_lifecycle(state=S.SUBMITTED)
        result = SERVICE.fill(lifecycle, "EVT-F", 11,
                              fill_quantity=Decimal("1.50"),
                              fill_price=Decimal("100"))
        assert result.state is S.FILLED


class TestEventAndAuditAccumulation:
    def chain_to_closed(self):
        lifecycle = make_lifecycle(sequence=0)
        lifecycle = SERVICE.validate(lifecycle, "E1", 1)
        lifecycle = SERVICE.accept(lifecycle, "E2", 2)
        lifecycle = SERVICE.queue(lifecycle, "E3", 3)
        lifecycle = SERVICE.submit(lifecycle, "E4", 4)
        lifecycle = SERVICE.fill(lifecycle, "E5", 5,
                                 fill_quantity=Decimal("1.5"),
                                 fill_price=Decimal("100"))
        return SERVICE.close(lifecycle, "E6", 6)

    def test_every_transition_logged(self):
        final = self.chain_to_closed()
        assert len(final.events) == 6
        assert len(final.audit) == 6

    def test_event_order_preserved(self):
        final = self.chain_to_closed()
        operations = [event.operation for event in final.events]
        assert operations == [O.VALIDATE, O.ACCEPT, O.QUEUE,
                              O.SUBMIT, O.FILL, O.CLOSE]

    def test_event_sequences_strictly_increase(self):
        final = self.chain_to_closed()
        sequences = [event.logical_sequence
                     for event in final.events]
        assert sequences == sorted(set(sequences))

    def test_audit_codes_sterile(self):
        final = self.chain_to_closed()
        codes = [entry.audit_code for entry in final.audit]
        assert codes == ["LIFECYCLE_VALIDATE",
                         "LIFECYCLE_ACCEPT", "LIFECYCLE_QUEUE",
                         "LIFECYCLE_SUBMIT", "LIFECYCLE_FILL",
                         "LIFECYCLE_CLOSE"]

    def test_original_lifecycle_not_mutated(self):
        lifecycle = make_lifecycle()
        SERVICE.validate(lifecycle, "EVT-1", 11)
        assert lifecycle.state is S.NEW
        assert lifecycle.events == ()
        assert lifecycle.audit == ()
        assert lifecycle.logical_sequence == 10

    def test_events_chain_from_states(self):
        final = self.chain_to_closed()
        for index in range(1, len(final.events)):
            assert final.events[index].from_state is \
                final.events[index - 1].to_state

    def test_reject_then_close_path(self):
        lifecycle = make_lifecycle()
        lifecycle = SERVICE.reject(lifecycle, "E1", 11)
        result = SERVICE.close(lifecycle, "E2", 12)
        assert result.state is S.CLOSED

    def test_cancel_then_close_path(self):
        lifecycle = make_lifecycle(state=S.SUBMITTED)
        lifecycle = SERVICE.cancel(lifecycle, "E1", 11)
        result = SERVICE.close(lifecycle, "E2", 12)
        assert result.state is S.CLOSED

    def test_fail_then_close_path(self):
        lifecycle = make_lifecycle(state=S.SUBMITTED)
        lifecycle = SERVICE.fail(lifecycle, "E1", 11)
        result = SERVICE.close(lifecycle, "E2", 12)
        assert result.state is S.CLOSED


class TestDeterminism:
    @pytest.mark.parametrize("method",
                             [c[0] for c in OPERATIONS])
    def test_same_inputs_same_output(self, method):
        operation_row = next(c for c in OPERATIONS
                             if c[0] == method)
        first = invoke(method,
                       make_lifecycle(state=operation_row[2][0]))
        second = invoke(method,
                        make_lifecycle(state=operation_row[2][0]))
        assert first == second

    def test_service_is_stateless(self):
        lifecycle = make_lifecycle()
        SERVICE.validate(lifecycle, "EVT-1", 11)
        assert not vars(SERVICE) if hasattr(SERVICE, "__dict__") \
            else True
        again = SERVICE.validate(lifecycle, "EVT-1", 11)
        assert again.state is S.VALIDATED


def make_event(**overrides):
    values = dict(event_reference="EVT-1",
                  order_reference="ORD-1",
                  operation=O.VALIDATE, from_state=S.NEW,
                  to_state=S.VALIDATED, logical_sequence=1)
    values.update(overrides)
    return LifecycleEvent(**values)


def make_audit(**overrides):
    values = dict(audit_code="LIFECYCLE_VALIDATE",
                  order_reference="ORD-1", logical_sequence=1)
    values.update(overrides)
    return LifecycleAudit(**values)


class TestModelValidation:
    @pytest.mark.parametrize("field,value", [
        ("event_reference", ""), ("event_reference", None),
        ("event_reference", 5), ("order_reference", " "),
        ("order_reference", None), ("operation", "VALIDATE"),
        ("from_state", "NEW"), ("to_state", None),
        ("logical_sequence", -1), ("logical_sequence", True),
        ("logical_sequence", "1")])
    def test_event_contract(self, field, value):
        with pytest.raises(LifecycleContractError) as info:
            make_event(**{field: value})
        assert str(info.value) == \
            f"INVALID_LIFECYCLE_FIELD:{field}"

    @pytest.mark.parametrize("field,value", [
        ("audit_code", ""), ("audit_code", None),
        ("order_reference", ""), ("order_reference", 3),
        ("logical_sequence", -2), ("logical_sequence", None)])
    def test_audit_contract(self, field, value):
        with pytest.raises(LifecycleContractError) as info:
            make_audit(**{field: value})
        assert str(info.value) == \
            f"INVALID_LIFECYCLE_FIELD:{field}"

    @pytest.mark.parametrize("field,value", [
        ("order_reference", ""), ("order_reference", None),
        ("symbol", ""), ("symbol", 5), ("side", "BUY"),
        ("quantity", None), ("quantity", 1.5),
        ("quantity", Decimal("0")), ("quantity", Decimal("-1")),
        ("quantity", Decimal("NaN")), ("price", 100.0),
        ("price", Decimal("0")), ("state", "NEW"),
        ("filled_quantity", Decimal("-1")),
        ("filled_price", "100"), ("events", []),
        ("events", ("x",)), ("audit", [1]),
        ("logical_sequence", -1), ("logical_sequence", 1.0)])
    def test_lifecycle_contract(self, field, value):
        values = dict(order_reference="ORD-1", symbol="BTCUSDT",
                      side=OrderSide.BUY,
                      quantity=Decimal("1.5"))
        values[field] = value
        with pytest.raises(LifecycleContractError) as info:
            OrderLifecycle(**values)
        assert str(info.value) == \
            f"INVALID_LIFECYCLE_FIELD:{field}"

    def test_lifecycle_defaults(self):
        lifecycle = OrderLifecycle(
            order_reference="ORD-1", symbol="BTCUSDT",
            side=OrderSide.BUY, quantity=Decimal("1"))
        assert lifecycle.state is S.NEW
        assert lifecycle.price is None
        assert lifecycle.filled_quantity is None
        assert lifecycle.events == ()
        assert lifecycle.logical_sequence == 0


IMMUTABLE_CASES = [
    (make_lifecycle, "state"), (make_lifecycle, "quantity"),
    (make_lifecycle, "events"), (make_lifecycle, "audit"),
    (make_lifecycle, "logical_sequence"),
    (make_lifecycle, "symbol"), (make_lifecycle, "side"),
    (make_lifecycle, "price"),
    (make_lifecycle, "filled_quantity"),
    (make_event, "event_reference"), (make_event, "operation"),
    (make_event, "from_state"), (make_event, "to_state"),
    (make_event, "logical_sequence"),
    (make_audit, "audit_code"), (make_audit, "order_reference"),
    (make_audit, "logical_sequence")]


class TestImmutability:
    @pytest.mark.parametrize("factory,field", IMMUTABLE_CASES)
    def test_models_frozen(self, factory, field):
        instance = factory()
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field, "mutated")

    @pytest.mark.parametrize("factory,field", IMMUTABLE_CASES)
    def test_models_reject_deletion(self, factory, field):
        instance = factory()
        with pytest.raises((FrozenInstanceError,
                            AttributeError)):
            delattr(instance, field)
