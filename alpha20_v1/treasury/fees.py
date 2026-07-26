"""
treasury/fees.py — İşlem ücreti muhasebesi.

Tasarım kuralları:
- Tüm ücretler pozitif tutarda kaydedilir (gider perspektifi).
- Ücret hesaplama: notional × rate.
- Ücretler maliyet esasını artırır (cost_basis.py entegrasyonu).
- FeeAccumulator thread-safe değildir; tek iş parçacığından çağrılmalı.
- Exchange-independent: borsa adı geçmez.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from .precision import q_amount, q_rate, ZERO, safe_divide
from .types import FeeRecord, FeeType


# ── Referans ücret oranları ───────────────────────────────────────────────────
# Bu oranlar varsayılan olarak kullanılır; gerçek borsa ücretleri değil.
STANDARD_RATES: dict[FeeType, Decimal] = {
    FeeType.TAKER:    Decimal("0.00040000"),   # %0.04
    FeeType.MAKER:    Decimal("0.00020000"),   # %0.02
    FeeType.FUNDING:  Decimal("0.00010000"),   # %0.01 (sekiz saatte bir — örnek)
    FeeType.WITHDRAWAL: Decimal("0.00050000"), # %0.05
}


class FeeComputationError(Exception):
    """Geçersiz ücret hesabı."""


# ══════════════════════════════════════════════════════════════════════════════
# Ücret hesaplama fonksiyonları
# ══════════════════════════════════════════════════════════════════════════════

def compute_fee_usdt(
    notional_usdt: Decimal,
    rate: Decimal,
) -> Decimal:
    """
    USDT cinsinden ücret hesapla.

    fee = notional × rate   (8 basamak hassasiyet)

    Değişmezlik:
    - notional_usdt >= 0 olmalı.
    - rate >= 0 olmalı.
    - Sonuç her zaman >= 0.

    >>> compute_fee_usdt(Decimal("10000"), Decimal("0.00040000"))
    Decimal('4.00000000')
    """
    if notional_usdt < ZERO:
        raise FeeComputationError(
            f"compute_fee_usdt: notional negatif olamaz, {notional_usdt} verildi."
        )
    if rate < ZERO:
        raise FeeComputationError(
            f"compute_fee_usdt: rate negatif olamaz, {rate} verildi."
        )
    return q_amount(notional_usdt * rate)


def compute_fee_for_trade(
    quantity: Decimal,
    price: Decimal,
    fee_type: FeeType = FeeType.TAKER,
    rate_override: Decimal | None = None,
) -> Decimal:
    """
    Bir işlem için ücret hesapla.

    fee = quantity × price × rate

    Args:
        quantity:      Coin miktarı.
        price:         İşlem fiyatı.
        fee_type:      Ücret türü (STANDARD_RATES'ten oran seçilir).
        rate_override: Özel oran; verilmezse STANDARD_RATES kullanılır.
    """
    rate     = rate_override if rate_override is not None else STANDARD_RATES[fee_type]
    notional = q_amount(quantity * price)
    return compute_fee_usdt(notional, rate)


def standard_rate(fee_type: FeeType) -> Decimal:
    """Ücret türüne göre standart oranı döndür."""
    return STANDARD_RATES.get(fee_type, ZERO)


# ══════════════════════════════════════════════════════════════════════════════
# FeeAccumulator — Ücret birikimi ve sorgulama
# ══════════════════════════════════════════════════════════════════════════════

class FeeAccumulator:
    """
    Birden fazla FeeRecord'u toplar ve sorgular.

    Kullanım:
        acc = FeeAccumulator()
        acc.add(FeeRecord(...))
        total = acc.total_fees()
    """

    def __init__(self, initial: Sequence[FeeRecord] | None = None) -> None:
        self._records: list[FeeRecord] = list(initial or [])

    def add(self, record: FeeRecord) -> None:
        """Yeni ücret kaydı ekle."""
        if record.amount_usdt < ZERO:
            raise FeeComputationError(
                f"FeeAccumulator.add: negatif ücret eklenemez ({record.amount_usdt})."
            )
        self._records.append(record)

    @property
    def records(self) -> list[FeeRecord]:
        return list(self._records)

    def total_fees(self) -> Decimal:
        """Kümülatif toplam ücret (USDT)."""
        return q_amount(sum((r.amount_usdt for r in self._records), ZERO))

    def fees_by_type(self) -> dict[FeeType, Decimal]:
        """Ücret türü başına toplam."""
        result: dict[FeeType, Decimal] = {}
        for r in self._records:
            result[r.fee_type] = q_amount(result.get(r.fee_type, ZERO) + r.amount_usdt)
        return result

    def fees_for_symbol(self, symbol: str) -> Decimal:
        """Belirli bir sembol için toplam ücret."""
        return q_amount(
            sum((r.amount_usdt for r in self._records if r.symbol == symbol), ZERO)
        )

    def fees_for_trade(self, trade_id: str) -> Decimal:
        """Belirli bir işlem için toplam ücret."""
        return q_amount(
            sum(
                (r.amount_usdt for r in self._records if r.trade_id == trade_id),
                ZERO,
            )
        )

    def average_rate(self) -> Decimal:
        """Tüm kayıtların ağırlıklı ortalama ücreti."""
        total_notional = ZERO
        total_fee = ZERO
        for r in self._records:
            # Notionali rate üzerinden geri hesapla: notional = fee / rate
            if r.rate > ZERO:
                notional    = q_amount(safe_divide(r.amount_usdt, r.rate))
                total_notional += notional
                total_fee     += r.amount_usdt
        return q_rate(safe_divide(total_fee, total_notional))

    def count(self) -> int:
        return len(self._records)

    def __len__(self) -> int:
        return self.count()

    def __repr__(self) -> str:
        return (
            f"FeeAccumulator(records={len(self._records)}, "
            f"total={self.total_fees():.8f} USDT)"
        )
