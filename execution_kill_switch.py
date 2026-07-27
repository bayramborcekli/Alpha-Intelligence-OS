"""Mission 2000 — Execution Foundation: deterministik Kill Switch.

Bir emir Broker Adapter'a ulaşmadan önceki SON otoritedir. Hiçbir
çağıran (Execution Service, Risk Engine, Broker Adapter, gelecekteki
AI ajanları) onu geçersiz kılamaz — override API yoktur. Yürütme
YALNIZ Risk Engine = ALLOW VE Kill Switch = ENABLED iken mümkündür.

Kill Switch ticaret mantığını ASLA değerlendirmez; tek sorusu:
"Sistem emir yürütebilir mi?" Emir yürütmez/iptal etmez/değiştirmez;
broker/exchange/REST/WebSocket/dosya sistemi/ağ ile konuşmaz.

Determinizm: aynı geçiş dizisi → her zaman aynı son durum. Duvar
saati, UUID, rastgelelik yoktur; zaman mantıksal sıra numarasıdır.
Her geçiş yeni bir değişmez anlık görüntü üretir; geçmiş asla
değiştirilemez.

Kamu yüzeyi (dondurulmuş): KillSwitch — is_execution_allowed(),
enable(), disable(), lock(), maintenance(), current_state().
Gelecek misyonlar (2100 Paper/Shadow/Micro Live, 2300 HA, 2400
Observability, 2700 Audit) bu API'yi DEĞİŞTİRMEDEN entegre olur:
anlık görüntüler salt-okunur tüketilebilir, davranış sabittir.
"""

from __future__ import annotations

from typing import Tuple

from execution_kill_switch_models import (
    KillSwitchReason,
    KillSwitchSnapshot,
    KillSwitchState,
    _APPROVED_TRANSITIONS,
)

__all__ = ["KillSwitch"]

_ERROR_INVALID_INPUT = "INVALID_KILLSWITCH_INPUT"
_ERROR_INVALID_TRANSITION = "INVALID_KILLSWITCH_TRANSITION"


class KillSwitch:
    """Sistem geneli acil koruma katmanı — mutlak otorite."""

    __slots__ = ("_history",)

    def __init__(self) -> None:
        # Güvenli varsayılan: DISABLED (yürütme kapalı başlar)
        object.__setattr__(self, "_history", (
            KillSwitchSnapshot(state=KillSwitchState.DISABLED,
                               reason=KillSwitchReason.MANUAL,
                               timestamp=0, sequence_id=0),))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(_ERROR_INVALID_INPUT)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(_ERROR_INVALID_INPUT)

    def _snapshots(self) -> Tuple[KillSwitchSnapshot, ...]:
        return self._history

    def _transition(self, target: KillSwitchState,
                    reason: KillSwitchReason) -> KillSwitchSnapshot:
        if not isinstance(reason, KillSwitchReason):
            raise ValueError(_ERROR_INVALID_INPUT)
        current = self._history[-1]
        if target not in _APPROVED_TRANSITIONS[current.state]:
            raise ValueError(_ERROR_INVALID_TRANSITION)
        sequence = current.sequence_id + 1
        snapshot = KillSwitchSnapshot(
            state=target, reason=reason, timestamp=sequence,
            sequence_id=sequence)
        object.__setattr__(self, "_history",
                           self._history + (snapshot,))
        return snapshot

    def is_execution_allowed(self) -> bool:
        """Tek soru: sistem emir yürütebilir mi?"""
        return self._history[-1].state is KillSwitchState.ENABLED

    def current_state(self) -> KillSwitchState:
        return self._history[-1].state

    def enable(self, reason: KillSwitchReason =
               KillSwitchReason.MANUAL) -> KillSwitchSnapshot:
        return self._transition(KillSwitchState.ENABLED, reason)

    def disable(self, reason: KillSwitchReason =
                KillSwitchReason.MANUAL) -> KillSwitchSnapshot:
        return self._transition(KillSwitchState.DISABLED, reason)

    def lock(self, reason: KillSwitchReason =
             KillSwitchReason.MANUAL) -> KillSwitchSnapshot:
        return self._transition(KillSwitchState.LOCKED, reason)

    def maintenance(self, reason: KillSwitchReason =
                    KillSwitchReason.MANUAL) -> KillSwitchSnapshot:
        return self._transition(KillSwitchState.MAINTENANCE, reason)
