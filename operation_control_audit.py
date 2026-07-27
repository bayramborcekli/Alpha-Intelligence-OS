"""Mission 2200 — Agent 01: Operasyon denetim zinciri.

Her operasyonel eylem gözlemlenebilir OLMALIDIR. Kayıtlar
yalnız eklenir (append-only), tuple olarak dışa verilir ve
sır/kimlik bilgisi içeren metinler REDDEDİLİR — denetim
kaydına sır sızdırmak sessizce budanmaz, açıkça hata olur.
"""

from __future__ import annotations

from typing import Tuple

from operation_control_errors import OperationControlAuditError
from operation_control_models import OperationAuditRecord

__all__ = ["FORBIDDEN_AUDIT_TOKENS", "OperationAuditTrail"]

# Denetim metinlerinde asla görünmemesi gereken parçalar.
FORBIDDEN_AUDIT_TOKENS: Tuple[str, ...] = (
    "api_key", "api-key", "apikey", "secret", "password",
    "authorization:", "bearer ", "token=", "x-mbx-apikey",
    "traceback",
)

_MAX_RECORDS = 5000


class OperationAuditTrail:
    """Bellek içi, yalnız-ekle denetim zinciri."""

    __slots__ = ("_records",)

    def __init__(self) -> None:
        self._records: list = []

    def append(self, record: OperationAuditRecord
               ) -> OperationAuditRecord:
        """Kaydı doğrula ve ekle; sır içeren kayıt REDDEDİLİR."""
        if not isinstance(record, OperationAuditRecord):
            raise OperationControlAuditError(
                "AUDIT_REJECTED:INVALID_RECORD")
        joined = " ".join((
            record.actor, record.action, record.target,
            record.previous_state, record.requested_state,
            record.result, record.reason,
            record.correlation_id,
            record.idempotency_key or "",
            record.error_code or "")).lower()
        for token in FORBIDDEN_AUDIT_TOKENS:
            if token in joined:
                raise OperationControlAuditError(
                    "AUDIT_REJECTED:SENSITIVE_CONTENT")
        self._records.append(record)
        if len(self._records) > _MAX_RECORDS:
            # En eski kayıtlar düşer; zincir sınırlı bellek
            # kullanır (yalnız görünüm katmanı).
            del self._records[0]
        return record

    def records(self) -> Tuple[OperationAuditRecord, ...]:
        """Değişmez kopya — dışarıdan mutasyon imkânsız."""
        return tuple(self._records)

    def tail(self, limit: int = 100
             ) -> Tuple[OperationAuditRecord, ...]:
        """Son ``limit`` kaydı (en yeni önce) döndür."""
        if not isinstance(limit, int) or isinstance(
                limit, bool) or limit <= 0:
            raise OperationControlAuditError(
                "AUDIT_REJECTED:INVALID_LIMIT")
        return tuple(reversed(self._records[-limit:]))

    def __len__(self) -> int:
        return len(self._records)
