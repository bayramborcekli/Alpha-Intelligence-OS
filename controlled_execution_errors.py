"""Mission 2100 — Agent 01: Kontrollü Yürütme istisna hiyerarşisi.

Kapalı hiyerarşi: ControlledExecutionError kökü altında sözleşme ve
yapılandırma hataları. Steril mesajlar; native detay sızmaz.
Yürütme Çekirdeği v1.0.0'a (dondurulmuş) dokunulmaz.
"""

from __future__ import annotations

__all__ = ["ControlledExecutionError",
           "ControlledExecutionContractError",
           "ControlledExecutionConfigurationError"]


class ControlledExecutionError(Exception):
    """Kontrollü Yürütme katmanının kök istisnası."""


class ControlledExecutionContractError(ControlledExecutionError):
    """Çağıran sözleşme ihlali (yanlış tip/geçersiz kullanım)."""


class ControlledExecutionConfigurationError(ControlledExecutionError):
    """Katman kurulum/yapılandırma hatası."""
