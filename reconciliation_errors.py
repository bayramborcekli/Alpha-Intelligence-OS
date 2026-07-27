"""Mission 2100 — Agent 07: Yaşam döngüsü & mutabakat hata taksonomisi.

Kapalı, steril hata kümesi. Hata mesajları YALNIZ steril kodlar
taşır; iç durum, bakiye, kimlik veya piyasa ayrıntısı sızdırmaz.
Ham iç istisnalar servis sınırını GEÇEMEZ; bu taksonomiye sarılır.
"""

from __future__ import annotations

__all__ = ["ReconciliationError", "LifecycleContractError",
           "LifecycleTransitionError", "LifecycleStateError",
           "ReconciliationContractError",
           "ReconciliationInputError"]


class ReconciliationError(Exception):
    """Yaşam döngüsü ve mutabakat alan hatalarının kapalı kökü."""


class LifecycleContractError(ReconciliationError):
    """Sözleşme ihlali — INVALID_LIFECYCLE_FIELD:<alan>."""


class LifecycleTransitionError(ReconciliationError):
    """Geçiş matrisi ihlali — INVALID_LIFECYCLE_TRANSITION:<kod>."""


class LifecycleStateError(ReconciliationError):
    """Durum ihlali — LIFECYCLE_STATE:<kod> (steril)."""


class ReconciliationContractError(ReconciliationError):
    """Mutabakat sözleşme ihlali — INVALID_RECONCILIATION_FIELD:<alan>."""


class ReconciliationInputError(ReconciliationError):
    """Mutabakat girdi ihlali — RECONCILIATION_INPUT:<kod>."""
