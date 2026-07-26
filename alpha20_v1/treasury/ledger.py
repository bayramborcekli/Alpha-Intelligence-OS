"""
treasury/ledger.py — Çift kayıtlı muhasebe defter mantığı.

Her muhasebe olayı dengeli bir journal kaydı üretir:
  Σ(Borç tutarları) == Σ(Alacak tutarları)

Hesap yapısı:
  PAPER_CASH               → Nakit USDT varlığı
  PAPER_POSITION:{SYMBOL}  → Açık pozisyon teminatı (LONG ve SHORT)
  PAPER_REALIZED_PNL       → Gerçekleşmiş K/Z toplamı
  PAPER_FEE_EXPENSE        → Kümülatif ücret giderleri
  PAPER_FUNDING_EXPENSE    → Fonlama ödemesi gideri (perp futures — borçlu)
  PAPER_FUNDING_INCOME     → Fonlama tahsilatı geliri (perp futures — alacaklı)

SHORT pozisyon muhasebesi (PAPER basitleştirilmiş model):
  Açılış: Teminat (cost_basis) nakitten ayrılır → DR POSITION, CR CASH
  Kapanış: Teminat + K/Z net olarak nakde döner.
    - Kâr: DR CASH (cost+pnl), CR POSITION cost, CR PNL pnl
    - Zarar: DR CASH (cost-zarar), DR PNL zarar,  CR POSITION cost
  Not: exit_value_usdt parametresi SHORT için exit_nominal (qty×exit_price).
       Nakit etkisi fonksiyon içinde side-aware hesaplanır.

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


def account_funding_expense() -> str:
    """Fonlama ödemesi gider hesabı (trader borçlu)."""
    return AccountType.PAPER_FUNDING_EXPENSE.value


def account_funding_income() -> str:
    """Fonlama tahsilatı gelir hesabı (trader alacaklı)."""
    return AccountType.PAPER_FUNDING_INCOME.value


def account_funding() -> str:
    """Backward compat: eski PAPER_FUNDING → expense hesabına yönlendirir."""
    return account_funding_expense()


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
    Pozisyon açma journal kaydı — LONG ve SHORT için aynı muhasebe yapısı.

    LONG ve SHORT için teminat (risk_usdt) nakitten pozisyon hesabına aktarılır:
      DR  PAPER_POSITION:{SYMBOL}   risk_usdt
          CR  PAPER_CASH            risk_usdt

    LONG:  risk_usdt = qty × entry_price (pozisyon nominal değeri)
    SHORT: risk_usdt = teminat miktarı (margin / collateral)

    Değişmezlik:
    - risk_usdt > 0 olmalı.
    - Denge: DR == CR == risk_usdt.
    """
    amount = q_amount(risk_usdt)
    if amount <= ZERO:
        raise ValueError(
            f"build_position_open_journal: risk_usdt pozitif olmalı, {risk_usdt} verildi."
        )

    lines = (
        LedgerLine(account=account_position(symbol), entry_type=EntryType.DEBIT,  amount=amount),
        LedgerLine(account=account_cash(),            entry_type=EntryType.CREDIT, amount=amount),
    )
    entry = JournalEntry(
        id=new_journal_id(),
        timestamp=timestamp or _now_utc(),
        description=f"Pozisyon açma: {symbol} {side.value} teminat={amount:.8f} USDT",
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
    Pozisyon kapama journal kaydı — side-aware K/Z hesaplama.

    Parametre semantiği:
      cost_basis_usdt : Açılışta ayrılan teminat (= qty × avg_cost)
      exit_value_usdt : LONG → qty × exit_price (nakite giren)
                        SHORT → qty × exit_price (exit nominal, kapama maliyeti)

    LONG K/Z: pnl = exit_value - cost_basis  (fiyat artarsa kâr)
    SHORT K/Z: pnl = cost_basis - exit_value (fiyat düşerse kâr)

    Nakit etkisi:
      LONG:  cash_dr = exit_value  (exit tutarı nakde dönüşür)
      SHORT: cash_dr = cost_basis + pnl = cost_basis + (cost_basis - exit_value)
             (teminat geri + net K/Z; net kâr olduğunda artı, zarar ≤ teminat olmalı)

    Journal yapısı (her iki yön için):
      Kâr:     DR CASH cash_dr / CR POSITION cost / CR PNL pnl
      Zarar:   DR CASH cash_dr / DR PNL loss      / CR POSITION cost
      Başabaş: DR CASH cash_dr / CR POSITION cash_dr

    Değişmezlik:
    - cost_basis_usdt > 0 olmalı.
    - exit_value_usdt >= 0 olmalı.
    - SHORT zarar teminatı aşamaz (PAPER margin call desteklenmiyor).
    - Denge her üç senaryoda sağlanır.
    """
    cost  = q_amount(cost_basis_usdt)
    exit_ = q_amount(exit_value_usdt)

    if cost <= ZERO:
        raise ValueError(
            f"build_position_close_journal: cost_basis_usdt pozitif olmalı, "
            f"{cost_basis_usdt} verildi."
        )
    if exit_ < ZERO:
        raise ValueError(
            f"build_position_close_journal: exit_value_usdt negatif olamaz, "
            f"{exit_value_usdt} verildi."
        )

    # Yön-duyarlı K/Z ve nakit etkisi
    if side == TradeSide.LONG:
        signed_pnl = q_amount(exit_ - cost)
        cash_dr    = exit_
    else:  # SHORT: fiyat düşünce kâr
        signed_pnl = q_amount(cost - exit_)
        cash_dr    = q_amount(cost + signed_pnl)  # = 2×cost - exit
        if cash_dr < ZERO:
            raise ValueError(
                f"build_position_close_journal: SHORT zarar teminatı ({cost:.8f}) aşıyor — "
                f"margin call PAPER modda desteklenmiyor. "
                f"exit={exit_:.8f}, loss={q_amount(-signed_pnl):.8f}"
            )

    lines: list[LedgerLine] = []

    if signed_pnl > ZERO:
        # Kâr
        lines = [
            LedgerLine(account=account_cash(),           entry_type=EntryType.DEBIT,  amount=cash_dr),
            LedgerLine(account=account_position(symbol), entry_type=EntryType.CREDIT, amount=cost),
            LedgerLine(account=account_realized_pnl(),   entry_type=EntryType.CREDIT, amount=signed_pnl),
        ]
    elif signed_pnl < ZERO:
        # Zarar
        loss = q_amount(abs(signed_pnl))
        lines = [
            LedgerLine(account=account_cash(),           entry_type=EntryType.DEBIT,  amount=cash_dr),
            LedgerLine(account=account_realized_pnl(),   entry_type=EntryType.DEBIT,  amount=loss),
            LedgerLine(account=account_position(symbol), entry_type=EntryType.CREDIT, amount=cost),
        ]
    else:
        # Başabaş
        lines = [
            LedgerLine(account=account_cash(),           entry_type=EntryType.DEBIT,  amount=cash_dr),
            LedgerLine(account=account_position(symbol), entry_type=EntryType.CREDIT, amount=cash_dr),
        ]

    entry = JournalEntry(
        id=new_journal_id(),
        timestamp=timestamp or _now_utc(),
        description=(
            f"Pozisyon kapama: {symbol} {side.value} "
            f"maliyet={cost:.8f} exit_nominal={exit_:.8f} K/Z={signed_pnl:+.8f} USDT"
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
        raise ValueError(
            f"build_fee_journal: fee_usdt pozitif olmalı, {fee_usdt} verildi."
        )

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
    is_income: bool = False,
    timestamp: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> JournalEntry:
    """
    Fonlama journal kaydı (perp futures).

    Ödeme (is_income=False, varsayılan) — trader borçlu (short taraf):
      DR  PAPER_FUNDING_EXPENSE   |funding_usdt|
          CR  PAPER_CASH          |funding_usdt|
      Nakit etkisi: azalır (gider).

    Tahsilat (is_income=True) — trader alacaklı (long taraf veya negatif rate):
      DR  PAPER_CASH              |funding_usdt|
          CR  PAPER_FUNDING_INCOME |funding_usdt|
      Nakit etkisi: artar (gelir).

    Değişmezlik:
    - funding_usdt > 0 olmalı (yön is_income ile belirlenir).
    """
    amount = q_amount(abs(to_decimal(funding_usdt)))
    if amount <= ZERO:
        raise ValueError(
            f"build_funding_journal: funding_usdt sıfır olamaz."
        )

    if is_income:
        lines: tuple[LedgerLine, ...] = (
            LedgerLine(account=account_cash(),            entry_type=EntryType.DEBIT,  amount=amount),
            LedgerLine(account=account_funding_income(),  entry_type=EntryType.CREDIT, amount=amount),
        )
        transfer_type = TransferType.FUNDING_INCOME
        direction_label = "tahsilat"
    else:
        lines = (
            LedgerLine(account=account_funding_expense(), entry_type=EntryType.DEBIT,  amount=amount),
            LedgerLine(account=account_cash(),            entry_type=EntryType.CREDIT, amount=amount),
        )
        transfer_type = TransferType.FUNDING_PAYMENT
        direction_label = "ödeme"

    entry = JournalEntry(
        id=new_journal_id(),
        timestamp=timestamp or _now_utc(),
        description=f"Fonlama {direction_label}: {symbol} {amount:.8f} USDT",
        transfer_type=transfer_type,
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

    Net = Σ(DR tutarları) - Σ(CR tutarları)

    Varlık hesapları (CASH, POSITION) için:
    - DR artı → nakit girişi / pozisyon büyümesi
    - CR eksi → nakit çıkışı / pozisyon kapanması

    Bu fonksiyon ham net değeri döndürür; yorum çağırana aittir.
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
    PAPER_CASH hesabının journal'lardan hesaplanan net değişimi.

    Net = Σ(DR tutarları) - Σ(CR tutarları)

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
        "journal_count": len(journals),
        "cash_balance":  compute_cash_from_journals(journals),
        "realized_pnl":  compute_realized_pnl_from_journals(journals),
        "total_fees":    compute_total_fees_from_journals(journals),
        "all_balanced":  all(j.is_balanced() for j in journals),
    }
