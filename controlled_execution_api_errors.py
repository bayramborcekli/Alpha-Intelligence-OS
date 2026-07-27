"""Mission 2100 — Agent 08: Kontrollü Yürütme API hata taksonomisi.

Kapalı, steril hata kümesi. Hata mesajları YALNIZ steril kodlar
taşır; iç durum, bakiye, kimlik veya piyasa ayrıntısı sızdırmaz.

NOT: Spesifikasyon `controlled_execution_errors.py` adını ister;
o dosya Agent 01 (Controlled Execution Foundation) tarafından
sahiplenilmiştir ve Agents 01–07 DEĞİŞTİRİLEMEZ. Bu modül bilinçli
olarak `_api_` ekiyle adlandırılmıştır (raporda gerekçeli).
"""

from __future__ import annotations

__all__ = ["ControlledExecutionAPIError",
           "ControlledExecutionAPIContractError",
           "ControlledExecutionAPIConfigurationError",
           "ControlledExecutionAPIModeError",
           "ControlledExecutionAPIRoutingError"]


class ControlledExecutionAPIError(Exception):
    """Kontrollü Yürütme API alan hatalarının kapalı kökü."""


class ControlledExecutionAPIContractError(
        ControlledExecutionAPIError):
    """Sözleşme ihlali — INVALID_API_FIELD:<alan> ya da
    MISSING_API_FIELD:<alan> (örtük varsayılan YOK)."""


class ControlledExecutionAPIConfigurationError(
        ControlledExecutionAPIError):
    """API/yönlendirici kuruluş hatası — API_CONFIGURATION:<kod>."""


class ControlledExecutionAPIModeError(ControlledExecutionAPIError):
    """Mod/işlem ihlali — API_MODE:<kod> (steril, fail-closed)."""


class ControlledExecutionAPIRoutingError(
        ControlledExecutionAPIError):
    """Yönlendirme ihlali — API_ROUTING:<kod>."""
