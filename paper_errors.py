"""Mission 2100 — Agent 03: Kağıt alan istisna hiyerarşisi.

Kapalı hiyerarşi: PaperDomainError kökü altında sözleşme, emir ve
defter hataları. Steril kodlar; native detay sızmaz. Gerçek broker,
ağ, yürütme çekirdeği YOK — yalnız deterministik kağıt alanı.
"""

from __future__ import annotations

__all__ = ["PaperDomainError", "PaperContractError",
           "PaperOrderError", "PaperLedgerError"]


class PaperDomainError(Exception):
    """Kağıt alan katmanının kök istisnası."""


class PaperContractError(PaperDomainError):
    """Çağıran sözleşme ihlali (yanlış tip / geçersiz alan)."""


class PaperOrderError(PaperDomainError):
    """Emir kuralı reddi (steril PAPER_ORDER_REJECTED kodu)."""


class PaperLedgerError(PaperDomainError):
    """Defter tutarlılık/sınır ihlali (steril kod)."""
