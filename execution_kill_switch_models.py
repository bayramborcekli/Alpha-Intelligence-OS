"""Mission 2000 — Execution Foundation: Kill Switch değişmez modelleri.

Kapalı durum kümesi, kapalı gerekçe kümesi ve değişmez geçiş anlık
görüntüsü. Duvar saati yok, UUID yok, rastgelelik yok — zaman
YALNIZ mantıksal sıra numarasıdır.

Güvenlik: I/O yok, ağ yok, broker/exchange/execution importu yok.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from types import MappingProxyType

__all__ = ["KillSwitchState", "KillSwitchReason",
           "KillSwitchSnapshot"]

_ERROR_INVALID_INPUT = "INVALID_KILLSWITCH_INPUT"


@unique
class KillSwitchState(Enum):
    """İzinli durumlar — kapalı küme; LOCKED terminaldir
    (ENABLED'a yalnız LOCKED→DISABLED→ENABLED yoluyla dönülür)."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    LOCKED = "LOCKED"
    MAINTENANCE = "MAINTENANCE"


@unique
class KillSwitchReason(Enum):
    """Kapalı gerekçe kümesi — keyfi string yasak."""

    MANUAL = "MANUAL"
    RISK_LIMIT = "RISK_LIMIT"
    SYSTEM_FAILURE = "SYSTEM_FAILURE"
    BROKER_FAILURE = "BROKER_FAILURE"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    DEPLOYMENT = "DEPLOYMENT"
    REGULATORY = "REGULATORY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class KillSwitchSnapshot:
    """Değişmez geçiş anlık görüntüsü.

    timestamp mantıksal sıradır (duvar saati DEĞİLDİR) ve
    sequence_id ile birlikte deterministik olarak artar.
    """

    state: KillSwitchState
    reason: KillSwitchReason
    timestamp: int
    sequence_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.state, KillSwitchState) or \
                not isinstance(self.reason, KillSwitchReason) or \
                not isinstance(self.timestamp, int) or \
                isinstance(self.timestamp, bool) or \
                not isinstance(self.sequence_id, int) or \
                isinstance(self.sequence_id, bool) or \
                self.timestamp < 0 or self.sequence_id < 0:
            raise ValueError(_ERROR_INVALID_INPUT)


# Dondurulmuş deterministik geçiş tablosu (tek doğruluk kaynağı).
# LOCKED terminaldir: ENABLED/MAINTENANCE'a doğrudan geçemez.
_APPROVED_TRANSITIONS = MappingProxyType({
    KillSwitchState.ENABLED: frozenset({
        KillSwitchState.DISABLED, KillSwitchState.MAINTENANCE}),
    KillSwitchState.DISABLED: frozenset({
        KillSwitchState.ENABLED, KillSwitchState.LOCKED}),
    KillSwitchState.MAINTENANCE: frozenset({
        KillSwitchState.ENABLED}),
    KillSwitchState.LOCKED: frozenset({
        KillSwitchState.DISABLED}),
})
