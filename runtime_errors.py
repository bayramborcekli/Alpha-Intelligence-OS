"""Mission 2100 — Agent 02: Çalışma zamanı alan istisna hiyerarşisi.

Kapalı hiyerarşi: RuntimeDomainError kökü altında sözleşme ve
yapılandırma hataları. Steril mesajlar; native detay sızmaz.
Yürütme Çekirdeği v1.0.0 (dondurulmuş) ve Agent 01 katmanına
dokunulmaz.
"""

from __future__ import annotations

__all__ = ["RuntimeDomainError",
           "RuntimeContractError",
           "RuntimeConfigurationError"]


class RuntimeDomainError(Exception):
    """Çalışma zamanı alan katmanının kök istisnası."""


class RuntimeContractError(RuntimeDomainError):
    """Çağıran sözleşme ihlali (yanlış tip / geçersiz alan)."""


class RuntimeConfigurationError(RuntimeDomainError):
    """Katman kurulum/yapılandırma hatası."""
