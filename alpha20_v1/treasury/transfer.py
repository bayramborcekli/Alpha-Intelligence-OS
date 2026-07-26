"""
treasury/transfer.py — Transfer yaşam döngüsü durum makinesi.

Transfer durumları:
  PENDING → SUBMITTED → CONFIRMED → SETTLED   (normal akış)
                      ↘ FAILED                (onaylanamadı)
  PENDING → CANCELLED                         (iptal edildi)

Kurallar:
- Terminal durumlar (SETTLED, FAILED, CANCELLED) geçiş kabul etmez.
- Her geçiş yeni bir Transfer nesnesi üretir (değiştirilemezlik).
- Geçersiz geçiş girişimi TransitionError fırlatır.
- Exchange-independent: borsa adı geçmez.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .types import Transfer, TransferStatus, TransferType


# ── Geçiş grafiği ─────────────────────────────────────────────────────────────
VALID_TRANSITIONS: dict[TransferStatus, frozenset[TransferStatus]] = {
    TransferStatus.PENDING:   frozenset({TransferStatus.SUBMITTED, TransferStatus.CANCELLED}),
    TransferStatus.SUBMITTED: frozenset({TransferStatus.CONFIRMED, TransferStatus.FAILED}),
    TransferStatus.CONFIRMED: frozenset({TransferStatus.SETTLED,   TransferStatus.FAILED}),
    TransferStatus.SETTLED:   frozenset(),   # Terminal
    TransferStatus.FAILED:    frozenset(),   # Terminal
    TransferStatus.CANCELLED: frozenset(),   # Terminal
}

TERMINAL_STATUSES = frozenset({
    TransferStatus.SETTLED,
    TransferStatus.FAILED,
    TransferStatus.CANCELLED,
})


class TransitionError(Exception):
    """Geçersiz durum geçişi girişimi."""


# ══════════════════════════════════════════════════════════════════════════════
# Durum makinesi sorgu fonksiyonları
# ══════════════════════════════════════════════════════════════════════════════

def can_transition(from_status: TransferStatus, to_status: TransferStatus) -> bool:
    """İki durum arasında geçiş izinli mi?"""
    return to_status in VALID_TRANSITIONS.get(from_status, frozenset())


def is_terminal(status: TransferStatus) -> bool:
    """Durum terminal mi (artık geçiş kabul etmez)?"""
    return status in TERMINAL_STATUSES


def allowed_next_states(status: TransferStatus) -> frozenset[TransferStatus]:
    """Mevcut durumdan izin verilen geçiş hedefleri."""
    return VALID_TRANSITIONS.get(status, frozenset())


# ══════════════════════════════════════════════════════════════════════════════
# Geçiş uygulama (değiştirilemez — yeni nesne üretir)
# ══════════════════════════════════════════════════════════════════════════════

def transition(
    transfer: Transfer,
    new_status: TransferStatus,
    *,
    error: str | None = None,
    metadata_update: dict[str, Any] | None = None,
) -> Transfer:
    """
    Transfer'i yeni duruma geçir.

    Geçersiz geçiş → TransitionError.
    Terminal durum → TransitionError (terminal transferler değiştirilemez).

    Args:
        transfer:        Mevcut transfer.
        new_status:      Hedef durum.
        error:           Yalnızca FAILED geçişinde hata mesajı.
        metadata_update: Journal ID veya ek bilgi güncellemesi.

    Returns:
        Yeni durumdaki değiştirilemez Transfer nesnesi.
    """
    if not can_transition(transfer.status, new_status):
        raise TransitionError(
            f"Transfer {transfer.id}: "
            f"{transfer.status.value} → {new_status.value} geçişi geçersiz. "
            f"İzin verilenler: {[s.value for s in allowed_next_states(transfer.status)]}"
        )

    # Hata mesajı yalnızca FAILED durumunda taşınır
    effective_error = error if new_status == TransferStatus.FAILED else None

    # Metadata birleştir
    new_meta = dict(transfer.metadata)
    if metadata_update:
        new_meta.update(metadata_update)

    return Transfer(
        id=transfer.id,
        timestamp=transfer.timestamp,
        transfer_type=transfer.transfer_type,
        amount_usdt=transfer.amount_usdt,
        status=new_status,
        symbol=transfer.symbol,
        trade_id=transfer.trade_id,
        journal_id=transfer.journal_id,
        error=effective_error,
        metadata=new_meta,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Transfer oluşturma yardımcıları
# ══════════════════════════════════════════════════════════════════════════════

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_position_open_transfer(
    *,
    transfer_id: str,
    symbol: str,
    risk_usdt: Decimal,
    trade_id: str,
    timestamp: datetime | None = None,
) -> Transfer:
    """Pozisyon açma için PENDING durumunda Transfer oluştur."""
    return Transfer(
        id=transfer_id,
        timestamp=timestamp or _now_utc(),
        transfer_type=TransferType.POSITION_OPEN,
        amount_usdt=risk_usdt,
        status=TransferStatus.PENDING,
        symbol=symbol,
        trade_id=trade_id,
    )


def create_position_close_transfer(
    *,
    transfer_id: str,
    symbol: str,
    exit_value_usdt: Decimal,
    trade_id: str,
    timestamp: datetime | None = None,
) -> Transfer:
    """Pozisyon kapama için PENDING durumunda Transfer oluştur."""
    return Transfer(
        id=transfer_id,
        timestamp=timestamp or _now_utc(),
        transfer_type=TransferType.POSITION_CLOSE,
        amount_usdt=exit_value_usdt,
        status=TransferStatus.PENDING,
        symbol=symbol,
        trade_id=trade_id,
    )


def create_fee_transfer(
    *,
    transfer_id: str,
    symbol: str,
    fee_usdt: Decimal,
    trade_id: str | None = None,
    timestamp: datetime | None = None,
) -> Transfer:
    """Ücret ödemesi için PENDING durumunda Transfer oluştur."""
    return Transfer(
        id=transfer_id,
        timestamp=timestamp or _now_utc(),
        transfer_type=TransferType.FEE,
        amount_usdt=fee_usdt,
        status=TransferStatus.PENDING,
        symbol=symbol,
        trade_id=trade_id,
    )


def settle(transfer: Transfer, journal_id: str) -> Transfer:
    """
    Transferi hızlı yerleştir: PENDING → SUBMITTED → CONFIRMED → SETTLED.
    PAPER modunda tüm adımlar anında tamamlanır.

    Args:
        transfer:   Başlangıç PENDING transfer.
        journal_id: İlişkili journal kaydı ID'si.

    Returns:
        SETTLED durumunda yeni Transfer.
    """
    if transfer.status != TransferStatus.PENDING:
        raise TransitionError(
            f"settle(): transfer {transfer.id} PENDING değil "
            f"({transfer.status.value}), hızlı yerleştirme yapılamaz."
        )
    t = transition(transfer, TransferStatus.SUBMITTED)
    t = transition(t, TransferStatus.CONFIRMED)
    t = transition(t, TransferStatus.SETTLED, metadata_update={"journal_id": journal_id})
    # journal_id'yi Transfer'in journal_id alanına da koy
    return Transfer(
        id=t.id, timestamp=t.timestamp, transfer_type=t.transfer_type,
        amount_usdt=t.amount_usdt, status=t.status,
        symbol=t.symbol, trade_id=t.trade_id,
        journal_id=journal_id,
        error=t.error, metadata=t.metadata,
    )


def fail(transfer: Transfer, reason: str) -> Transfer:
    """
    Transferi başarısız olarak işaretle.
    PENDING veya SUBMITTED → FAILED.
    """
    if transfer.status not in (TransferStatus.PENDING, TransferStatus.SUBMITTED,
                                TransferStatus.CONFIRMED):
        raise TransitionError(
            f"fail(): transfer {transfer.id} FAILED yapılamaz "
            f"(mevcut durum: {transfer.status.value})."
        )
    # PENDING → SUBMITTED → FAILED veya direkt geçiş
    if transfer.status == TransferStatus.PENDING:
        t = transition(transfer, TransferStatus.SUBMITTED)
    else:
        t = transfer
    return transition(t, TransferStatus.FAILED, error=reason)
