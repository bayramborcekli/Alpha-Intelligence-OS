"""Mission 2100 — Agent 02: Çalışma zamanı kapalı enum kümeleri.

Yalnız `enum` importu. Kapalı kümeler: durum, ortam, kalp atışı,
yetkilendirme ve denetim şiddeti. Sınırsız canlı ortam YOKTUR.
"""

from __future__ import annotations

from enum import Enum, unique

__all__ = ["RuntimeState", "RuntimeEnvironment",
           "HeartbeatStatus", "AuthorizationState",
           "AuditSeverity"]


@unique
class RuntimeState(Enum):
    """Kapalı çalışma zamanı durumu."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


@unique
class RuntimeEnvironment(Enum):
    """Kapalı çalışma ortamı kümesi — sınırsız canlı ortam yoktur."""

    PAPER = "PAPER"
    SHADOW = "SHADOW"
    MICRO_LIVE = "MICRO_LIVE"


@unique
class HeartbeatStatus(Enum):
    """Kapalı kalp atışı durumu."""

    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


@unique
class AuthorizationState(Enum):
    """Kapalı yetkilendirme durumu."""

    NONE = "NONE"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


@unique
class AuditSeverity(Enum):
    """Kapalı denetim şiddeti."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
