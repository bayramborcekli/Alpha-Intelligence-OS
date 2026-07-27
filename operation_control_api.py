"""Mission 2200 — Agent 01: Operasyon Kontrol API zarfı.

Framework'ten bağımsız (Flask importu YOK) sunum katmanı:
görünüm modellerini steril JSON-uyumlu zarflara serileştirir
ve HTTP durum kodlarını kural tablosuyla eşler.

Zarf sözleşmesi (her yanıt):
    ok, data, error_code, message, correlation_id,
    generated_at, data_freshness, execution_mode
Durum değiştiren yanıtlara ek olarak:
    action_id, idempotency_status, audit_recorded,
    lifecycle_status

Ham istisna metni, sır, kimlik bilgisi asla zarfa girmez.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple

from operation_control_models import (
    IdempotencyStatus, OperationActionResult,
    OperationActionStatus, OperationAuditRecord,
    OperationSnapshot)

__all__ = [
    "ENVELOPE_KEYS",
    "ACTION_KEYS",
    "serialize_value",
    "serialize_view",
    "read_envelope",
    "error_envelope",
    "action_envelope",
    "status_code_for_action",
]

ENVELOPE_KEYS: Tuple[str, ...] = (
    "ok", "data", "error_code", "message", "correlation_id",
    "generated_at", "data_freshness", "execution_mode")

ACTION_KEYS: Tuple[str, ...] = ENVELOPE_KEYS + (
    "action_id", "idempotency_status", "audit_recorded",
    "lifecycle_status")

# Eylem sonucu → HTTP durum kodu (kapalı tablo).
_ACTION_HTTP = {
    OperationActionStatus.COMPLETED: 200,
    OperationActionStatus.ACCEPTED: 202,
    OperationActionStatus.PARTIAL: 202,
    OperationActionStatus.FAILED: 422,
    OperationActionStatus.UNSUPPORTED: 422,
}

# Steril hata kodu → HTTP eşlemesi (öncelikli).
_ERROR_HTTP = {
    "INVALID_TRANSITION": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "POLICY_DENIED": 403,
    "KILL_SWITCH": 423,
    "DEPENDENCY_UNAVAILABLE": 503,
    "POSITION_DATA_INCOMPLETE": 422,
    "UNKNOWN_TARGET": 404,
    "MALFORMED_REQUEST": 400,
    "UNSUPPORTED_CAPABILITY": 422,
    "KILL_SWITCH_ACTIVE": 423,
    # Sertifikalı yürütme katmanının steril karar kodları:
    "KILL_SWITCH_DENIED": 423,
    "PERMISSION_DENIED": 403,
    "POLICY_DENIED_EXECUTION": 403,
    "RISK_DENIED": 422,
    "RISK_REJECTED": 422,
    "EXECUTION_REJECTED": 422,
}


def serialize_value(value: object) -> object:
    """Decimal → str; Enum → value; tuple → list (derin)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [serialize_value(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if hasattr(value, "__dataclass_fields__"):
        return serialize_view(value)
    # Bilinmeyen tip zarfı kirletemez.
    return "UNKNOWN"


def serialize_view(view: object) -> dict:
    """Frozen görünüm veri sınıfını sözlüğe serileştir."""
    return {f.name: serialize_value(getattr(view, f.name))
            for f in fields(view)}


def _freshness_of(snapshot: Optional[OperationSnapshot]
                  ) -> str:
    if snapshot is None:
        return "UNKNOWN"
    return snapshot.status.data_freshness.value


def _mode_of(snapshot: Optional[OperationSnapshot]) -> str:
    if snapshot is None:
        return "UNKNOWN"
    return snapshot.status.execution_mode


def read_envelope(data: object,
                  snapshot: Optional[OperationSnapshot],
                  correlation_id: str,
                  generated_at: int) -> Tuple[dict, int]:
    """Başarılı okuma zarfı (HTTP 200)."""
    return ({
        "ok": True,
        "data": serialize_value(data)
        if not isinstance(data, (dict, list)) else data,
        "error_code": None,
        "message": "OK",
        "correlation_id": correlation_id,
        "generated_at": generated_at,
        "data_freshness": _freshness_of(snapshot),
        "execution_mode": _mode_of(snapshot),
    }, 200)


def error_envelope(error_code: str, message: str,
                   correlation_id: str, generated_at: int,
                   snapshot: Optional[OperationSnapshot] = None,
                   http_status: Optional[int] = None
                   ) -> Tuple[dict, int]:
    """Steril hata zarfı — ham istisna metni taşınmaz."""
    status = http_status if isinstance(http_status, int) \
        else status_code_for_error(error_code)
    return ({
        "ok": False,
        "data": None,
        "error_code": error_code,
        "message": message,
        "correlation_id": correlation_id,
        "generated_at": generated_at,
        "data_freshness": _freshness_of(snapshot),
        "execution_mode": _mode_of(snapshot),
    }, status)


def status_code_for_error(error_code: object) -> int:
    """Kod öneki tablodan çözülür; bilinmeyen kod 500."""
    if not isinstance(error_code, str) or not error_code:
        return 500
    prefix = error_code.split(":", 1)[0]
    return _ERROR_HTTP.get(prefix, 500)


def status_code_for_action(result: OperationActionResult
                           ) -> int:
    """Eylem sonucunu HTTP koduna eşle (fail-closed 500 yok:
    DENIED kodun kendisinden çözülür)."""
    if result.status is OperationActionStatus.DENIED:
        return status_code_for_error(result.error_code)
    return _ACTION_HTTP.get(result.status, 500)


def action_envelope(result: OperationActionResult,
                    snapshot: Optional[OperationSnapshot],
                    generated_at: int,
                    message: Optional[str] = None
                    ) -> Tuple[dict, int]:
    """Durum değiştiren eylem zarfı + HTTP kodu."""
    ok = result.status in (OperationActionStatus.COMPLETED,
                           OperationActionStatus.ACCEPTED)
    payload = {
        "ok": ok,
        "data": {
            "previous_state": result.previous_state,
            "current_state": result.current_state,
            "status": result.status.value,
            "detail_codes": list(result.detail_codes),
        },
        "error_code": result.error_code,
        "message": message if isinstance(message, str)
        and message else result.status.value,
        "correlation_id": result.correlation_id,
        "generated_at": generated_at,
        "data_freshness": _freshness_of(snapshot),
        "execution_mode": _mode_of(snapshot),
        "action_id": result.action_id,
        "idempotency_status":
            result.idempotency_status.value,
        "audit_recorded": result.audit_recorded,
        "lifecycle_status": result.lifecycle_status,
    }
    return payload, status_code_for_action(result)


def serialize_audit(records: Tuple[OperationAuditRecord, ...]
                    ) -> list:
    """Denetim kayıtlarını listeye serileştir."""
    return [serialize_view(record) for record in records]
