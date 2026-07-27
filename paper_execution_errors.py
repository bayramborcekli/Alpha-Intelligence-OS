"""Mission 2100 — Agent 04: Kağıt yürütme hata taksonomisi.

Kapalı küme: kök PaperExecutionError ve altı alt sınıf. Servis
sınırından HAM iç istisna geçemez; her hata steril kod taşır.
Kod biçimleri:

- PaperExecutionContractError:
  ``INVALID_PAPER_EXECUTION_FIELD:<alan>``
- PaperExecutionConfigurationError:
  ``PAPER_EXECUTION_CONFIGURATION:<kod>``
- PaperExecutionModeError:
  ``PAPER_EXECUTION_MODE_DENIED:<kod>``
- PaperExecutionRiskError:
  ``PAPER_EXECUTION_RISK:<kod>``
- PaperExecutionPermissionError:
  ``PAPER_EXECUTION_PERMISSION:<kod>``
- PaperExecutionStateError:
  ``PAPER_EXECUTION_STATE:<kod>``

Güvenlik: ağ yok, dosya sistemi yok, zaman/UUID/rastgelelik yok.
"""

from __future__ import annotations

__all__ = ["PaperExecutionError",
           "PaperExecutionContractError",
           "PaperExecutionConfigurationError",
           "PaperExecutionModeError",
           "PaperExecutionRiskError",
           "PaperExecutionPermissionError",
           "PaperExecutionStateError"]


class PaperExecutionError(Exception):
    """Kağıt yürütme alanının kök hatası — steril kod taşır."""


class PaperExecutionContractError(PaperExecutionError):
    """Sözleşme ihlali: INVALID_PAPER_EXECUTION_FIELD:<alan>."""


class PaperExecutionConfigurationError(PaperExecutionError):
    """Servis kurulum hatası: PAPER_EXECUTION_CONFIGURATION:<kod>."""


class PaperExecutionModeError(PaperExecutionError):
    """Mod ihlali: PAPER_EXECUTION_MODE_DENIED:<kod>."""


class PaperExecutionRiskError(PaperExecutionError):
    """Risk değerlendirme hatası: PAPER_EXECUTION_RISK:<kod>."""


class PaperExecutionPermissionError(PaperExecutionError):
    """İzin kapısı ihlali: PAPER_EXECUTION_PERMISSION:<kod>."""


class PaperExecutionStateError(PaperExecutionError):
    """Kapsanan alan/durum hatası: PAPER_EXECUTION_STATE:<kod>."""
