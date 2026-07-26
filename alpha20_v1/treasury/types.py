"""
treasury/types.py — Treasury domain'inin tüm temel tipleri.

Tasarım ilkeleri:
- Tüm veri sınıfları frozen=True (değiştirilemez).
- Enum'lar string değerli — JSON serileşmesi için.
- Tüm parasal alanlar Decimal tipinde.
- Zaman damgaları timezone-aware (UTC).

Exchange-independent: Binance, FTX veya herhangi bir borsa adı geçmez.
PAPER modu: Tüm tipler simüle işlemler için tasarlanmıştır.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


# ══════════════════════════════════════════════════════════════════════════════
# Enum'lar
# ══════════════════════════════════════════════════════════════════════════════

class AccountType(str, Enum):
    """
    Çift kayıtlı muhasebe hesap tipleri.
    Hesap adı: "<AccountType>[:<SYMBOL>]" formatında kullanılır.
    Örnek: "PAPER_POSITION:BTCUSDT"
    """
    PAPER_CASH            = "PAPER_CASH"             # Nakit (USDT) — Varlık
    PAPER_POSITION        = "PAPER_POSITION"         # Açık pozisyon teminatı — Varlık
    PAPER_REALIZED_PNL    = "PAPER_REALIZED_PNL"     # Gerçekleşmiş K/Z — Özkaynak
    PAPER_UNREALIZED_PNL  = "PAPER_UNREALIZED_PNL"   # Gerçekleşmemiş K/Z — Özkaynak
    PAPER_FEE_EXPENSE     = "PAPER_FEE_EXPENSE"      # İşlem ücreti — Gider
    PAPER_FUNDING_EXPENSE = "PAPER_FUNDING_EXPENSE"  # Fonlama ödemesi — Gider
    PAPER_FUNDING_INCOME  = "PAPER_FUNDING_INCOME"   # Fonlama tahsilatı — Gelir
    # Backward compat: eski PAPER_FUNDING adı, expense anlamında kullanılırdı
    PAPER_FUNDING         = "PAPER_FUNDING_EXPENSE"  # Alias — kullanımdan kalkıyor


class EntryType(str, Enum):
    """Çift kayıt defteri giriş tipi."""
    DEBIT  = "DR"   # Borç — Varlıkları artırır, pasifi azaltır
    CREDIT = "CR"   # Alacak — Pasifi artırır, varlıkları azaltır


class TradeSide(str, Enum):
    """İşlem yönü."""
    LONG  = "LONG"
    SHORT = "SHORT"


class TradeResult(str, Enum):
    """Kapalı işlem sonucu."""
    WIN  = "WIN"
    LOSS = "LOSS"
    BREAK_EVEN = "BREAK_EVEN"


class TransferType(str, Enum):
    """Transfer / işlem olayı türü."""
    POSITION_OPEN    = "POSITION_OPEN"    # Pozisyon açma
    POSITION_CLOSE   = "POSITION_CLOSE"   # Pozisyon kapama
    FEE              = "FEE"              # İşlem ücreti
    FUNDING_PAYMENT  = "FUNDING_PAYMENT"  # Fonlama ödemesi (gider)
    FUNDING_INCOME   = "FUNDING_INCOME"   # Fonlama tahsilatı (gelir)
    DEPOSIT          = "DEPOSIT"          # Bakiye yükleme (başlangıç)
    ADJUSTMENT       = "ADJUSTMENT"       # Manuel düzeltme


class TransferStatus(str, Enum):
    """
    Transfer yaşam döngüsü durumları.

    Geçiş grafiği:
      PENDING → SUBMITTED → CONFIRMED → SETTLED (terminal)
                          ↘ FAILED  (terminal)
      PENDING → CANCELLED  (terminal)
    """
    PENDING   = "PENDING"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    SETTLED   = "SETTLED"    # Terminal
    FAILED    = "FAILED"     # Terminal
    CANCELLED = "CANCELLED"  # Terminal


class FeeType(str, Enum):
    """İşlem ücreti türü."""
    TAKER      = "TAKER"       # Piyasa emri ücreti
    MAKER      = "MAKER"       # Limit emir ücreti
    FUNDING    = "FUNDING"     # Perp futures fonlama ücreti
    WITHDRAWAL = "WITHDRAWAL"  # Para çekme ücreti


class CostBasisMethod(str, Enum):
    """Maliyet esası hesaplama yöntemi."""
    WAVG = "WAVG"   # Ağırlıklı ortalama — bu sistemde kullanılır
    FIFO = "FIFO"   # İlk giren ilk çıkar (gelecek için ayrılmıştır)


# ══════════════════════════════════════════════════════════════════════════════
# Muhasebe tipleri (değiştirilemez)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LedgerLine:
    """
    Çift kayıt defterinin tek bir satırı.

    Değişmezlik (Invariant):
    - amount > 0 (yön entry_type ile belirlenir)
    - currency standart ISO kodu olmalı (örn. "USDT")
    - account, "<AccountType>[:<SYMBOL>]" formatında olmalı
    """
    account:    str         # Örnek: "PAPER_CASH", "PAPER_POSITION:BTCUSDT"
    entry_type: EntryType   # DR veya CR
    amount:     Decimal     # Her zaman pozitif
    currency:   str = "USDT"

    def __post_init__(self) -> None:
        if self.amount <= Decimal("0"):
            raise ValueError(
                f"LedgerLine.amount pozitif olmalı, {self.amount} verildi "
                f"(hesap: {self.account}, tür: {self.entry_type})"
            )


@dataclass(frozen=True)
class JournalEntry:
    """
    Çift kayıt defteri journal kaydı.

    Değişmezlik (Invariant):
    - lines boş olamaz.
    - Σ(DR amount) == Σ(CR amount) — denge koşulu.
    - timestamp timezone-aware olmalı.

    Denge koşulu validate() ile kontrol edilir; constructor içinde enforce edilmez
    (nesne inşası sırasında henüz tamamlanmamış olabilir).
    """
    id:            str
    timestamp:     datetime
    description:   str
    transfer_type: TransferType
    lines:         tuple[LedgerLine, ...]
    metadata:      dict[str, Any] = field(default_factory=dict)

    def debit_total(self) -> Decimal:
        return sum(
            (ln.amount for ln in self.lines if ln.entry_type == EntryType.DEBIT),
            Decimal("0"),
        )

    def credit_total(self) -> Decimal:
        return sum(
            (ln.amount for ln in self.lines if ln.entry_type == EntryType.CREDIT),
            Decimal("0"),
        )

    def is_balanced(self, tolerance: Decimal = Decimal("0.00000001")) -> bool:
        """Borç ve alacak toplamlarının farkı tolerans içinde mi?"""
        return abs(self.debit_total() - self.credit_total()) <= tolerance


# ══════════════════════════════════════════════════════════════════════════════
# İşlem (Trade) tipleri
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TradeRecord:
    """
    Tek bir PAPER işleminin muhasebe kaydı.

    entry_price ve exit_price 8 basamak hassasiyette saklanır.
    realized_pnl = None ise pozisyon henüz açık demektir.
    fee_usdt maliyet esasına dahildir.
    """
    id:               str
    symbol:           str
    side:             TradeSide
    quantity:         Decimal     # Coin miktarı (8 dp)
    entry_price:      Decimal     # Giriş fiyatı (8 dp)
    cost_basis_usdt:  Decimal     # Toplam giriş maliyeti — ücret dahil (8 dp)
    fee_usdt:         Decimal     # Ödenen toplam ücret (8 dp)
    opened_at:        datetime
    exit_price:       Decimal | None = None
    realized_pnl:     Decimal | None = None   # None → açık pozisyon
    result:           TradeResult | None = None
    closed_at:        datetime | None = None
    journal_ids:      tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    @property
    def avg_cost_per_unit(self) -> Decimal:
        """Birim başına ortalama maliyet (ücret dahil)."""
        from .precision import safe_divide
        return safe_divide(self.cost_basis_usdt, self.quantity)

    @property
    def notional_usdt(self) -> Decimal:
        """Giriş fiyatına göre nominal değer."""
        return self.quantity * self.entry_price


@dataclass(frozen=True)
class CostBasisLot:
    """
    Ağırlıklı ortalama maliyet hesabı için bir pozisyon lotu.
    Birden fazla lot birleştirilerek WAVG hesabı yapılır.

    Değişmezlik:
    - Tüm lotlar aynı symbol ve side'a ait olmalı (WAVG invariant).
    """
    symbol:         str
    side:           TradeSide
    quantity:       Decimal     # Bu lotta tutulan miktar
    total_cost_usdt: Decimal    # Bu lota ödenen toplam USDT (ücret dahil)
    opened_at:      datetime
    trade_id:       str

    @property
    def avg_cost_per_unit(self) -> Decimal:
        """Lot başına birim maliyet."""
        from .precision import safe_divide
        return safe_divide(self.total_cost_usdt, self.quantity)


# ══════════════════════════════════════════════════════════════════════════════
# Ücret (Fee) tipleri
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FeeRecord:
    """
    Tek bir ücret olayı.
    amount_usdt her zaman pozitif; gider olarak kaydedilir.
    """
    id:          str
    timestamp:   datetime
    symbol:      str
    fee_type:    FeeType
    amount_usdt: Decimal    # Pozitif — ödenen ücret
    rate:        Decimal    # Oran (örn. 0.00040000 = 0.04%)
    trade_id:    str | None = None

    def __post_init__(self) -> None:
        if self.amount_usdt < Decimal("0"):
            raise ValueError(
                f"FeeRecord.amount_usdt negatif olamaz: {self.amount_usdt}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Transfer yaşam döngüsü tipleri
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Transfer:
    """
    Bir muhasebe transferinin değiştirilemez anlık görüntüsü.
    Durum geçişleri transfer.py içindeki state machine ile yönetilir.
    Yerleşik (SETTLED/FAILED/CANCELLED) transfer değiştirilemez.
    """
    id:            str
    timestamp:     datetime
    transfer_type: TransferType
    amount_usdt:   Decimal
    status:        TransferStatus
    symbol:        str | None = None
    trade_id:      str | None = None
    journal_id:    str | None = None
    error:         str | None = None   # Yalnızca FAILED durumunda
    metadata:      dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            TransferStatus.SETTLED,
            TransferStatus.FAILED,
            TransferStatus.CANCELLED,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Valuation tipleri
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PositionValuation:
    """
    Tek bir açık pozisyonun anlık değerlemesi.
    Tüm tutarlar USDT cinsinden.

    NAV katkısı modeli:
      LONG:  nav_contribution = mark_to_market_usdt (qty × current_price)
      SHORT: nav_contribution = collateral_usdt + unrealized_pnl
             NOT: SHORT mark_to_market_usdt portföy NAV'ına doğrudan eklenmez;
             bu değer yalnızca ham piyasa verisi olarak saklanır.
    """
    symbol:              str
    side:                TradeSide
    quantity:            Decimal
    avg_cost_per_unit:   Decimal       # Birim başına giriş maliyeti
    current_price:       Decimal       # Anlık piyasa fiyatı
    mark_to_market_usdt: Decimal       # qty × current_price (ham piyasa değeri)
    collateral_usdt:     Decimal       # avg_cost × qty (teminat/maliyet esası)
    unrealized_pnl:      Decimal       # Gerçekleşmemiş K/Z (negatif olabilir)
    unrealized_pnl_pct:  Decimal       # Yüzde değişim

    @property
    def is_profitable(self) -> bool:
        return self.unrealized_pnl > Decimal("0")

    @property
    def nav_contribution(self) -> Decimal:
        """
        Bu pozisyonun portföy NAV'ına katkısı.

        LONG:  current piyasa değeri (mark_to_market_usdt)
        SHORT: teminat + gerçekleşmemiş K/Z
               = collateral + (avg_cost - current) × qty

        Mantık: SHORT açılışta nakit azaldı (teminat ayrıldı). Kapanışta
        teminat + K/Z geri döner. NAV'daki anlık değer = geri dönecek olan.
        """
        if self.side == TradeSide.LONG:
            return self.mark_to_market_usdt
        else:
            return self.collateral_usdt + self.unrealized_pnl


@dataclass(frozen=True)
class PortfolioValuation:
    """
    Tüm portföyün anlık değerlemesi.

    NAV hesaplama modeli:
      NAV = nakit + LONG pozisyon değeri + SHORT özkaynak
      long_position_value = Σ(LONG mark_to_market_usdt)
      short_equity        = Σ(SHORT collateral + unrealized_pnl)
      total_position_value = long_position_value + short_equity (compat)
    """
    cash_usdt:             Decimal
    positions:             tuple[PositionValuation, ...]
    long_position_value:   Decimal      # Σ(LONG mark_to_market_usdt)
    short_equity:          Decimal      # Σ(SHORT collateral + unrealized_pnl)
    total_position_value:  Decimal      # long + short (backward compat)
    nav_usdt:              Decimal      # cash + long_position_value + short_equity
    total_unrealized_pnl:  Decimal      # Σ(unrealized_pnl)
    timestamp:             datetime


# ══════════════════════════════════════════════════════════════════════════════
# Mutabakat (Reconciliation) tipleri
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CheckResult:
    """Tek bir mutabakat kontrolünün sonucu."""
    name:     str
    passed:   bool
    expected: str
    actual:   str
    message:  str


@dataclass(frozen=True)
class ReconciliationResult:
    """Tüm mutabakat kontrollerinin özeti."""
    passed:    bool
    checks:    tuple[CheckResult, ...]
    timestamp: datetime

    @property
    def failed_checks(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if not c.passed)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)
