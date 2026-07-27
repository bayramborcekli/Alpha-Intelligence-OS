"""Mission 2100 — Agent 07: Deterministik emir yaşam döngüsü servisi.

Durumsuz, saf servis: her geçiş çağıran-sahipli olay referansı ve
mantıksal sıra ile YENİ bir OrderLifecycle üretir. Emir vermez,
borsaya/broker'a BAĞLANMAZ, broker durumu değiştirmez, işlem
YÜRÜTMEZ. Gizli mutasyon YOKTUR.

Geçiş matrisi kapalıdır; matris dışı her istek steril
LifecycleTransitionError ile REDDEDİLİR (fail-closed). QUEUE ve
FAIL bilinçli ek işlemlerdir: QUEUED ve FAILED durumları
spesifikasyonda tanımlıdır ve deterministik bir geçişle
ulaşılabilir olmak ZORUNDADIR.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
from typing import Optional, Tuple

from lifecycle_models import (LifecycleAudit, LifecycleEvent,
                              LifecycleOperation, OrderLifecycle,
                              OrderLifecycleState)
from reconciliation_errors import (LifecycleContractError,
                                   LifecycleTransitionError)

__all__ = ["OrderLifecycleService", "TRANSITION_MATRIX",
           "TERMINAL_STATES"]

_S = OrderLifecycleState
_O = LifecycleOperation

_ERROR_FIELD = "INVALID_LIFECYCLE_FIELD"
_ERROR_TRANSITION = "INVALID_LIFECYCLE_TRANSITION"

# Kapalı geçiş matrisi: işlem -> (izinli kaynak durumlar, hedef).
TRANSITION_MATRIX = MappingProxyType({
    _O.VALIDATE: ((_S.NEW,), _S.VALIDATED),
    _O.ACCEPT: ((_S.VALIDATED,), _S.ACCEPTED),
    _O.QUEUE: ((_S.ACCEPTED,), _S.QUEUED),
    _O.SUBMIT: ((_S.ACCEPTED, _S.QUEUED), _S.SUBMITTED),
    _O.FILL: ((_S.SUBMITTED,), _S.FILLED),
    _O.CANCEL: ((_S.ACCEPTED, _S.QUEUED, _S.SUBMITTED),
                _S.CANCELLED),
    _O.REJECT: ((_S.NEW, _S.VALIDATED, _S.ACCEPTED, _S.QUEUED),
                _S.REJECTED),
    _O.FAIL: ((_S.SUBMITTED,), _S.FAILED),
    _O.CLOSE: ((_S.FILLED, _S.CANCELLED, _S.REJECTED, _S.FAILED),
               _S.CLOSED),
})

TERMINAL_STATES = (_S.CLOSED,)


def _fail_field(field: str) -> None:
    raise LifecycleContractError(f"{_ERROR_FIELD}:{field}")


def _require_lifecycle(value: object) -> None:
    if not isinstance(value, OrderLifecycle):
        _fail_field("lifecycle")


def _require_reference(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail_field(field)


def _require_sequence(value: object,
                      lifecycle: OrderLifecycle) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or \
            value < 0:
        _fail_field("logical_sequence")
    if value <= lifecycle.logical_sequence:
        raise LifecycleTransitionError(
            f"{_ERROR_TRANSITION}:NON_MONOTONIC_SEQUENCE")


def _require_positive_decimal(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        _fail_field(field)
    if not value.is_finite() or value <= Decimal("0"):
        _fail_field(field)


class OrderLifecycleService:
    """Durumsuz, deterministik yaşam döngüsü geçiş servisi."""

    def _transition(self, lifecycle: OrderLifecycle,
                    operation: LifecycleOperation,
                    event_reference: str,
                    logical_sequence: int,
                    filled_quantity: Optional[Decimal] = None,
                    filled_price: Optional[Decimal] = None
                    ) -> OrderLifecycle:
        _require_lifecycle(lifecycle)
        _require_reference(event_reference, "event_reference")
        _require_sequence(logical_sequence, lifecycle)
        allowed, target = TRANSITION_MATRIX[operation]
        if lifecycle.state not in allowed:
            raise LifecycleTransitionError(
                f"{_ERROR_TRANSITION}:{lifecycle.state.value}"
                f":{operation.value}")
        event = LifecycleEvent(
            event_reference=event_reference,
            order_reference=lifecycle.order_reference,
            operation=operation,
            from_state=lifecycle.state,
            to_state=target,
            logical_sequence=logical_sequence)
        audit = LifecycleAudit(
            audit_code=f"LIFECYCLE_{operation.value}",
            order_reference=lifecycle.order_reference,
            logical_sequence=logical_sequence)
        if operation is _O.FILL:
            return replace(
                lifecycle, state=target,
                filled_quantity=filled_quantity,
                filled_price=filled_price,
                events=lifecycle.events + (event,),
                audit=lifecycle.audit + (audit,),
                logical_sequence=logical_sequence)
        return replace(
            lifecycle, state=target,
            events=lifecycle.events + (event,),
            audit=lifecycle.audit + (audit,),
            logical_sequence=logical_sequence)

    def validate(self, lifecycle: OrderLifecycle,
                 event_reference: str,
                 logical_sequence: int) -> OrderLifecycle:
        return self._transition(lifecycle, _O.VALIDATE,
                                event_reference, logical_sequence)

    def accept(self, lifecycle: OrderLifecycle,
               event_reference: str,
               logical_sequence: int) -> OrderLifecycle:
        return self._transition(lifecycle, _O.ACCEPT,
                                event_reference, logical_sequence)

    def queue(self, lifecycle: OrderLifecycle,
              event_reference: str,
              logical_sequence: int) -> OrderLifecycle:
        return self._transition(lifecycle, _O.QUEUE,
                                event_reference, logical_sequence)

    def submit(self, lifecycle: OrderLifecycle,
               event_reference: str,
               logical_sequence: int) -> OrderLifecycle:
        return self._transition(lifecycle, _O.SUBMIT,
                                event_reference, logical_sequence)

    def fill(self, lifecycle: OrderLifecycle,
             event_reference: str, logical_sequence: int,
             fill_quantity: Decimal,
             fill_price: Decimal) -> OrderLifecycle:
        """YALNIZ tam dolum — kısmi dolum bu katmanda REDDEDİLİR
        (Paper broker IMMEDIATE_FULL_FILL sözleşmesiyle hizalı)."""
        _require_lifecycle(lifecycle)
        _require_positive_decimal(fill_quantity, "fill_quantity")
        _require_positive_decimal(fill_price, "fill_price")
        if fill_quantity != lifecycle.quantity:
            raise LifecycleTransitionError(
                f"{_ERROR_TRANSITION}:PARTIAL_FILL_UNSUPPORTED")
        return self._transition(lifecycle, _O.FILL,
                                event_reference, logical_sequence,
                                filled_quantity=fill_quantity,
                                filled_price=fill_price)

    def cancel(self, lifecycle: OrderLifecycle,
               event_reference: str,
               logical_sequence: int) -> OrderLifecycle:
        return self._transition(lifecycle, _O.CANCEL,
                                event_reference, logical_sequence)

    def reject(self, lifecycle: OrderLifecycle,
               event_reference: str,
               logical_sequence: int) -> OrderLifecycle:
        return self._transition(lifecycle, _O.REJECT,
                                event_reference, logical_sequence)

    def fail(self, lifecycle: OrderLifecycle,
             event_reference: str,
             logical_sequence: int) -> OrderLifecycle:
        return self._transition(lifecycle, _O.FAIL,
                                event_reference, logical_sequence)

    def close(self, lifecycle: OrderLifecycle,
              event_reference: str,
              logical_sequence: int) -> OrderLifecycle:
        return self._transition(lifecycle, _O.CLOSE,
                                event_reference, logical_sequence)
