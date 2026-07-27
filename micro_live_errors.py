"""Mission 2100 — Agent 06: Micro Live yetkilendirme hata taksonomisi.

Kapalı, steril hata kümesi. Hata mesajları YALNIZ steril kodlar
taşır; iç durum, bakiye, kimlik veya piyasa ayrıntısı sızdırmaz.
Ham iç istisnalar servis sınırını GEÇEMEZ; bu taksonomiye sarılır.
"""

from __future__ import annotations

__all__ = ["MicroLiveError", "MicroLiveContractError",
           "MicroLiveConfigurationError", "MicroLiveModeError",
           "MicroLivePolicyError", "MicroLiveTransitionError",
           "MicroLiveStateError"]


class MicroLiveError(Exception):
    """Micro Live yetkilendirme alan hatalarının kapalı kökü."""


class MicroLiveContractError(MicroLiveError):
    """Sözleşme ihlali — INVALID_MICRO_LIVE_FIELD:<alan>."""


class MicroLiveConfigurationError(MicroLiveError):
    """Servis kuruluş hatası — MICRO_LIVE_CONFIGURATION:<kod>."""


class MicroLiveModeError(MicroLiveError):
    """Mod ihlali — MICRO_LIVE_MODE:<kod>."""


class MicroLivePolicyError(MicroLiveError):
    """Politika ihlali — MICRO_LIVE_POLICY:<kod>."""


class MicroLiveTransitionError(MicroLiveError):
    """Geçiş matrisi ihlali — MICRO_LIVE_TRANSITION:<kod>."""


class MicroLiveStateError(MicroLiveError):
    """Durum ihlali — MICRO_LIVE_STATE:<kod> (steril)."""
