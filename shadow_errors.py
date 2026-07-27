"""Mission 2100 — Agent 05: Gölge modu hata taksonomisi.

Kapalı, steril hata kümesi. Hata mesajları YALNIZ steril kodlar
taşır; iç durum, bakiye, kimlik veya piyasa ayrıntısı sızdırmaz.
Ham iç istisnalar servis sınırını GEÇEMEZ; bu taksonomiye sarılır.
"""

from __future__ import annotations

__all__ = ["ShadowError", "ShadowContractError",
           "ShadowConfigurationError", "ShadowModeError",
           "ShadowRiskError", "ShadowPermissionError",
           "ShadowStateError"]


class ShadowError(Exception):
    """Gölge modu alan hatalarının kapalı kökü."""


class ShadowContractError(ShadowError):
    """Sözleşme ihlali — INVALID_SHADOW_FIELD:<alan>."""


class ShadowConfigurationError(ShadowError):
    """Servis kuruluş hatası — SHADOW_CONFIGURATION:<kod>."""


class ShadowModeError(ShadowError):
    """Mod ihlali — SHADOW_MODE:<kod>."""


class ShadowRiskError(ShadowError):
    """Risk değerlendirici arızası — SHADOW_RISK:<kod>."""


class ShadowPermissionError(ShadowError):
    """İzin kapısı arızası — SHADOW_PERMISSION:<kod>."""


class ShadowStateError(ShadowError):
    """Durum ihlali — SHADOW_STATE:<kod> (steril, ham istisnasız)."""
