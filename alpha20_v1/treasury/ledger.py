"""
treasury/ledger.py — Çift kayıtlı muhasebe defter mantığı.

Her muhasebe olayı dengeli bir journal kaydı üretir:
  Σ(Borç tutarları) == Σ(Alacak tutarları)

Hesap yapısı:
  PAPER_CASH              → Nakit USDT varlığı
  PAPER_POSITION:{SYMBOL} → Açık pozisyon maliyeti
  PAPER_REALIZED_PNL      → Gerçekleşmiş K/Z toplamı
  PAPER_FEE_EXPENSE       → Kümülatif ücret giderleri
  PAPER_FUNDING           → Fonlama giderleri

Exchange-independent: Borsa API'sine bağımlılık yok.
PAPER: Gerçek emir gönderilmez.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .precision import q_amount, ZERO, to_decimal
from .types import (
    AccountType, EntryType, JournalEntry, LedgerLine,
    TradeSide, TransferType,
)


# ── Hesap adı oluşturucular ───────────────────────────────────────────────────

def account_cash() -> str:
    return AccountType.PAPER_CASH.value


def account_position(symbol: str) -> str:
    return f"{AccountType.PAPER_POSITION.value}:{symbol.upper()}"


def account_realized_pnl() -> str:
    return AccountType.PAPER_REALIZED_PNL.value


def account_fee_expense() -> str:
    return AccountType.PAPER_FEE_EXPENSE.value


def account_funding() -> str:
    return AccountType.PAPER_FUNDING.value


# ── Journal ID üretici ────────────────────────────────────────────────────────

def new_journal_id() -> str:
    return f"J-{uuid.uuid4().hex[:12].upper()}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# Journal doğrulama
# ══════════════════════════════════════════════════════════════════════════════

class LedgerImbalanceError(Exception):
    """Çift kayıt dengesi bozulduğunda fırlatılır."""


def validate_journal(entry: JournalEntry, tolerance: Decimal = Decimal("0.00000001")) -> None:
    """
    Journal kaydının dengeli olduğunu doğrula.
    Denge bozuksa LedgerImbalanceError fırlatır.

    Kural: Σ(DR tutarları) == Σ(CR tutarları)  ± tolerans
    """
    if not entry.lines:
        raise LedgerImbalanceError(
            f"Journal {entry.id}: satır yok — boş journal geçersiz."
        )
    dr = entry.debit_total()
    cr = entry.credit_total()
    diff = abs(dr - cr)
    if diff > tolerance:
        raise LedgerImbalanceError(
            f"Journal {entry.id} dengeli değil: "
            f"DR={dr:.8f}, CR={cr:.8f}, fark={diff:.8f} "
            f"(tolerans={tolerance:.8f})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Standart journal şablonları
# ══════════════════════════════════════════════════════════════════════════════

def build_position_open_journal(
    *,
    symbol: str,
    side: TradeSide,
    risk_usdt: Decimal,
    timestamp: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> JournalEntry:
    """
    Pozisyon açma journal kaydı.

    Risk miktarı (risk_usdt) nakitten pozisyon hesabına aktarılır:
      DR  PAPER_POSITION:{SYMBOL}   risk_usdt
          CR  PAPER_CASH            risk_usdt

    Değişmezlik:
    - risk_usdt > 0 olmalı.
    - Denge: DR == CR == risk_usdt.
    """
    amount = q_amount(risk_usdt)
    if amount <= ZERO:
        raise ValueError(f"build_position_open_journal: risk_usdt pozitif olmalı, {risk_usdt} verildi.")

    lines = (
        LedgerLine(account=account_position(symbol), entry_type=EntryType.DEBIT,  amount=amount),
        LedgerLine(account=account_cash(),            entry_type=EntryType.CREDIT, amount=amount),
    )
    entry = JournalEntry(
        id=new_journal_id(),
        timestamp=timestamp or _now_utc(),
        description=f"Pozisyon açma: {symbol} {side.value} risk={amount:.8f} USDT",
        transfer_type=TransferType.POSITION_OPEN,
        lines=lines,
        metadata=metadata or {},
    )
    validate_journal(entry)
    return entry


def build_position_close_journal(
    *,
    symbol: str,
    side: TradeSide,
    cost_basis_usdt: Decimal,
    exit_value_usdt: Decimal,
    timestamp: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> JournalEntry:
    """
    Pozisyon kapama journal kaydı.

    Kâr durumunda:
      DR  PAPER_CASH                    exit_value_usdt
          CR  PAPER_POSITION:{SYMBOL}   cost_basis_usdt
          CR  PAPER_REALIZED_PNL        (exit_value - cost_basis)

    Zarar durumunda:
      DR  PAPER_CASH                    exit_value_usdt
      DR  PAPER_REALIZED_PNL            (cost_basis - exit_value)
          CR  PAPER_POSITION:{SYMBOL}   cost_basis_usdt

    Başabaş durumunda:
      DR  PAPER_CASH                    exit_value_usdt
          CR  PAPER_POSITION:{SYMBOL}   exit_value_usdt

    Değişmezlik:
    - cost_basis_usdt > 0 olmalı.
    - exit_value_usdt >= 0 olmalı.
    - Denge her üç senaryoda da sağlanmalı.
    """
    cost  = q_amount(cost_basis_usdt)
    exit_ = q_amount(exit_value_usdt)
    pnl   = q_amount(exit_ - cost)

    if cost <= ZERO:
        raise ValueError(f"build_position_close_journal: cost_basis_usdt pozitif olmalı, {cost_basis_usdt} verildi.")
    if exit_ < ZERO:
        raise ValueError(f"build_position_close_journal: exit_value_usdt negatif olamaz, {exit_value_usdt} verildi.")

    lines: list[LedgerLine] = []

    if pnl > ZERO:
        # Kâr: nakite geri dön + K/Z alacağı
        lines = [
            LedgerLine(account=account_cash(),            entry_type=EntryType.DEBIT,  amount=exit_),
            LedgerLine(account=account_position(symbol),  entry_type=EntryType.CREDIT, amount=cost),
            LedgerLine(account=account_realized_pnl(),    entry_type=EntryType.CREDIT, amount=pnl),
        ]
    elif pnl < ZERO:
        # Zarar: K/Z borç kaydı
        loss = q_amount(abs(pnl))
        lines = [
            LedgerLine(account=account_cash(),            entry_type=EntryType.DEBIT,  amount=exit_),
            LedgerLine(account=account_realized_pnl(),    entry_type=EntryType.DEBIT,  amount=loss),
            LedgerLine(account=account_position(symbol),  entry_type=EntryType.CREDIT, amount=cost),
        ]
    else:
        # Başabaş
        lines = [
            LedgerLine(account=account_cash(),            entry_type=EntryType.DEBIT,  amount=exit_),
            LedgerLine(account=account_position(symbol),  entry_type=EntryType.CREDIT, amount=exit_),
        ]

    entry = JournalEntry(
        id=new_journal_id(),
        timestamp=timestamp or _now_utc(),
        description=(
            f"Pozisyon kapama: {symbol} {side.value} "
            f"maliyet={cost:.8f} çıkış={exit_:.8f} K/Z={pnl:+.8f} USDT"
        ),
        transfer_type=TransferType.POSITION_CLOSE,
        lines=tuple(lines),
        metadata=metadata or {},
    )
    validate_journal(entry)
    return entry


def build_fee_journal(
    *,
    symbol: str,
    fee_usdt: Decimal,
    timestamp: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> JournalEntry:
    """
    Ücret ödeme journal kaydı.

      DR  PAPER_FEE_EXPENSE   fee_usdt
          CR  PAPER_CASH      fee_usdt

    Değişmezlik:
    - fee_usdt > 0 olmalı.
    """
    amount = q_amount(fee_usdt)
    if amount <= ZERO:
        raise ValueError(f"build_fee_journal: fee_usdt pozitif olmalı, {fee_usdt} verildi.")

    lines = (
        LedgerLine(account=account_fee_expense(), entry_type=EntryType.DEBIT,  amount=amount),
        LedgerLine(account=account_cash(),         entry_type=EntryType.CREDIT, amount=amount),
    )
    entry = JournalEntry(
        id=new_journal_id(),
        timestamp=timestamp or _now_utc(),
        description=f"İşlem ücreti: {symbol} {amount:.8f} USDT",
        transfer_type=TransferType.FEE,
        lines=lines,
        metadata=metadata or {},
    )
    validate_journal(entry)
    return entry


def build_funding_journal(
    *,
    symbol: str,
    funding_usdt: Decimal,
    timestamp: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> JournalEntry:
    """
    Fonlama ödemesi journal kaydı (perp futures).

    Ödeme (negatif funding rate):
      DR  PAPER_FUNDING   |funding_usdt|
          CR  PAPER_CASH  |funding_usdt|

    Tahsilat (pozitif funding rate) aynı muhasebe yapısını kullanır;
    yön metadata üzerinden ayırt edilir.
    """
    amount = q_amount(abs(to_decimal(funding_usdt)))
    if amount <= ZERO:
        raise ValueError(f"build_funding_journal: funding_usdt sıfır olamaz.")

    lines = (
        LedgerLine(account=account_funding(), entry_type=EntryType.DEBIT,  amount=amount),
        LedgerLine(account=account_cash(),    entry_type=EntryType.CREDIT, amount=amount),
    )
    entry = JournalEntry(
        id=new_journal_id(),
        timestamp=timestamp or _now_utc(),
        description=f"Fonlama ödemesi: {symbol} {amount:.8f} USDT",
        transfer_type=TransferType.FUNDING_PAYMENT,
        lines=lines,
        metadata=metadata or {},
    )
    validate_journal(entry)
    return entry


# ══════════════════════════════════════════════════════════════════════════════
# Hesap bakiye hesaplama
# ══════════════════════════════════════════════════════════════════════════════

def compute_account_balance(
    journals: list[JournalEntry], account: str
) -> Decimal:
    """
    Verilen hesap için tüm journal kayıtlarından net bakiye hesapla.

    Nakit ve pozisyon hesapları için:
      Net = Σ(DR tutarları) - Σ(CR tutarları)

    Muhasebe convention'ına göre:
    - PAPER_CASH normalde CR ağırlıklı → Net < 0 (pasif perspektif)
    - Bu fonksiyon ham net değeri döndürür; yorum çağırana aittir.
    """
    balance = ZERO
    for journal in journals:
        for line in journal.lines:
            if line.account == account:
                if line.entry_type == EntryType.DEBIT:
                    balance += line.amount
                else:
                    balance -= line.amount
    return balance


def compute_cash_from_journals(journals: list[JournalEntry]) -> Decimal:
    """
    PAPER_CASH hesabının journal'lardan hesaplanan net bakiyesi.

    Nakit hesabı için DR net perspektif kullanılır (varlık hesabı):
      Cash = Σ(DR tutarları) - Σ(CR tutarları)

    Yorumlama:
    - Pozitif → bu journal grubundan nakit net girişi
    - Negatif → bu journal grubundan nakit net çıkışı
    """
    return compute_account_balance(journals, account_cash())


def compute_realized_pnl_from_journals(journals: list[JournalEntry]) -> Decimal:
    """
    Gerçekleşmiş K/Z hesabının journal'lardan hesaplanan net tutarı.

    Net K/Z = Σ(CR tutarları) - Σ(DR tutarları)
    - Pozitif → kâr
    - Negatif → zarar
    """
    return -compute_account_balance(journals, account_realized_pnl())


def compute_total_fees_from_journals(journals: list[JournalEntry]) -> Decimal:
    """Ücret giderlerinin kümülatif toplamı (DR ağırlıklı, pozitif)."""
    total = ZERO
    for journal in journals:
        for line in journal.lines:
            if line.account == account_fee_expense() and line.entry_type == EntryType.DEBIT:
                total += line.amount
    return total


def get_ledger_summary(journals: list[JournalEntry]) -> dict:
    """Journal listesinden muhasebe özeti döndür."""
    return {
        "journal_count":   len(journals),
        "cash_balance":    compute_cash_from_journals(journals),
        "realized_pnl":    compute_realized_pnl_from_journals(journals),
        "total_fees":      compute_total_fees_from_journals(journals),
        "all_balanced":    all(j.is_balanced() for j in journals),
    }
