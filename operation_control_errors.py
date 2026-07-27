"""Mission 2200 — Agent 01: Operasyon Kontrol Merkezi hataları.

Steril hata hiyerarşisi: hiçbir hata mesajı ham istisna metni,
sır, kimlik bilgisi veya iç yığın izi taşımaz. Tüm kodlar
KAPALI kümedendir ve `KOD:<alan>` biçimindedir.
"""

from __future__ import annotations

__all__ = [
    "OperationControlError",
    "OperationControlValidationError",
    "OperationControlTransitionError",
    "OperationControlIdempotencyError",
    "OperationControlPolicyError",
    "OperationControlDependencyError",
    "OperationControlAuditError",
    "OperationControlUnsupportedError",
]


class OperationControlError(Exception):
    """Operasyon kontrol katmanının steril temel hatası."""


class OperationControlValidationError(OperationControlError):
    """Geçersiz alan — `INVALID_OPERATION_FIELD:<alan>`."""


class OperationControlTransitionError(OperationControlError):
    """Geçersiz durum geçişi — `INVALID_TRANSITION:<detay>`."""


class OperationControlIdempotencyError(OperationControlError):
    """Idempotency çakışması — `IDEMPOTENCY_CONFLICT:<anahtar>`."""


class OperationControlPolicyError(OperationControlError):
    """Politika reddi (fail-closed) — `POLICY_DENIED:<neden>`."""


class OperationControlDependencyError(OperationControlError):
    """Güvenlik bağımlılığı eksik/bayat — `DEPENDENCY_UNAVAILABLE`."""


class OperationControlAuditError(OperationControlError):
    """Denetim kaydı ihlali — `AUDIT_REJECTED:<neden>`."""


class OperationControlUnsupportedError(OperationControlError):
    """Sertifikalı API desteklemiyor — `UNSUPPORTED_CAPABILITY`."""
