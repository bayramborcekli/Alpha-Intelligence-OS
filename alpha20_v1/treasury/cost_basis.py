"""
treasury/cost_basis.py — Ağırlıklı ortalama maliyet esası (WAVG).

Tasarım kuralları:
- Maliyet esası her zaman USDT cinsinden hesaplanır.
- İşlem ücretleri maliyet esasına dahil edilir (etkin maliyet).
- Kısmi kapama: orantılı maliyet esası tüketilir.
- Negatif miktar veya negatif maliyet mümkün değildir.
- Exchange-independent: sembol formatına bağımlılık yok.

WAVG invariant:
  Tüm lotlar aynı sembol ve aynı yöne (side) ait olmalıdır.
  Karışık sembol veya yön durumunda CostBasisError fırlatılır.

WAVG formülü:
  new_avg = (Σ cost_i) / (Σ quantity_i)

Kısmi kapama sonrası gerçekleşmiş K/Z:
  LONG:  pnl = (exit_price - avg_cost) × qty_closed
  SHORT: pnl = (avg_cost  - exit_price) × qty_closed
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from .precision import q_amount, q_qty, q_price, q_rate, safe_divide, ZERO
from .types import CostBasisLot, TradeSide


# ── Standart ücret oranları (referans) ───────────────────────────────────────
DEFAULT_TAKER_RATE = Decimal("0.00040000")   # %0.04
DEFAULT_MAKER_RATE = Decimal("0.00020000")   # %0.02


class CostBasisError(Exception):
    """Geçersiz maliyet esası işlemi."""


# ══════════════════════════════════════════════════════════════════════════════
# WAVG invariant kontrolü
# ══════════════════════════════════════════════════════════════════════════════

def _validate_lot_homogeneity(lots: Sequence[CostBasisLot], context: str = "") -> None:
    """
    Tüm lotların aynı sembol ve yöne (side) ait olduğunu doğrula.

    Kural: WAVG yalnızca homojen lot listelerinde geçerlidir.
    Karışık sembol veya karışık side → CostBasisError.

    Args:
        lots:    Kontrol edilecek lot listesi.
        context: Hata mesajına eklenen bağlam bilgisi.
    """
    if len(lots) <= 1:
        return
    first = lots[0]
    for i, lot in enumerate(lots[1:], start=1):
        if lot.symbol != first.symbol:
            raise CostBasisError(
                f"{context}WAVG invariant ihlali: karışık sembol. "
                f"Beklenen '{first.symbol}', lot[{i}]='{lot.symbol}'. "
                f"Her sembol için ayrı lot listesi tutun."
            )
        if lot.side != first.side:
            raise CostBasisError(
                f"{context}WAVG invariant ihlali: karışık yön (side). "
                f"Beklenen '{first.side.value}', lot[{i}]='{lot.side.value}'. "
                f"LONG ve SHORT ayrı lot listelerinde tutulmalı."
            )


# ══════════════════════════════════════════════════════════════════════════════
# Temel hesaplamalar
# ══════════════════════════════════════════════════════════════════════════════

def compute_weighted_average(lots: Sequence[CostBasisLot]) -> Decimal:
    """
    Lot listesinin ağırlıklı ortalama birim maliyetini hesapla.

    avg = Σ(total_cost_usdt) / Σ(quantity)

    Değişmezlik:
    - Lots boş olmamalı.
    - Toplam miktar > 0 olmalı.
    - Tüm lotlar aynı sembol ve yöne (side) ait olmalı [WAVG invariant].

    Boş lots → 0 döner (hata vermez).
    Karışık sembol veya side → CostBasisError.
    """
    if not lots:
        return ZERO

    _validate_lot_homogeneity(lots, context="compute_weighted_average: ")

    total_cost = sum((lt.total_cost_usdt for lt in lots), ZERO)
    total_qty  = sum((lt.quantity for lt in lots), ZERO)

    return q_price(safe_divide(total_cost, total_qty))


def add_lot_and_recompute(
    existing_lots: list[CostBasisLot],
    new_lot: CostBasisLot,
) -> tuple[list[CostBasisLot], Decimal]:
    """
    Yeni lot ekleyip yeni ağırlıklı ortalama döndür.

    Returns:
        (updated_lots, new_weighted_avg)

    Değişmezlik:
    - new_lot.quantity > 0 olmalı.
    - new_lot.total_cost_usdt >= 0 olmalı.
    - new_lot, mevcut lotlarla aynı symbol ve side'a sahip olmalı [WAVG invariant].
    """
    if new_lot.quantity <= ZERO:
        raise CostBasisError(
            f"add_lot_and_recompute: yeni lot miktarı pozitif olmalı, "
            f"{new_lot.quantity} verildi."
        )
    if new_lot.total_cost_usdt < ZERO:
        raise CostBasisError(
            f"add_lot_and_recompute: lot maliyeti negatif olamaz, "
            f"{new_lot.total_cost_usdt} verildi."
        )

    # Mevcut lotlarla homojenlik kontrolü
    if existing_lots:
        first = existing_lots[0]
        if new_lot.symbol != first.symbol:
            raise CostBasisError(
                f"add_lot_and_recompute: WAVG invariant ihlali. "
                f"Mevcut sembol='{first.symbol}', yeni lot sembol='{new_lot.symbol}'."
            )
        if new_lot.side != first.side:
            raise CostBasisError(
                f"add_lot_and_recompute: WAVG invariant ihlali. "
                f"Mevcut side='{first.side.value}', yeni lot side='{new_lot.side.value}'."
            )

    updated = list(existing_lots) + [new_lot]
    return updated, compute_weighted_average(updated)


def consume_lots_wavg(
    lots: list[CostBasisLot],
    qty_to_close: Decimal,
) -> tuple[Decimal, list[CostBasisLot]]:
    """
    WAVG yöntemiyle kısmi veya tam kapatma işlemi.

    Tüm lotlardan ortalama maliyet alınır; tüketilen maliyet orantılı hesaplanır.
    Lot listesi tek bir birleşik lot gibi davranır (WAVG'nin doğası).

    Args:
        lots:         Mevcut açık lot listesi (aynı symbol ve side).
        qty_to_close: Kapatılacak miktar.

    Returns:
        (cost_of_closed_usdt, remaining_lots)
        - cost_of_closed_usdt: Kapatılan miktarın USDT maliyeti.
        - remaining_lots:      Kalan pozisyon (boş ise pozisyon kapandı).

    Hata durumları:
    - qty_to_close <= 0 → CostBasisError
    - qty_to_close > toplam miktar → CostBasisError
    - Karışık symbol/side → CostBasisError (WAVG invariant)
    """
    if qty_to_close <= ZERO:
        raise CostBasisError(
            f"consume_lots_wavg: kapatılacak miktar pozitif olmalı, {qty_to_close} verildi."
        )

    _validate_lot_homogeneity(lots, context="consume_lots_wavg: ")

    total_qty = sum((lt.quantity for lt in lots), ZERO)
    if qty_to_close > total_qty + Decimal("0.00000001"):  # tolerans
        raise CostBasisError(
            f"consume_lots_wavg: kapatılacak miktar ({qty_to_close}) "
            f"toplam miktarı ({total_qty}) aşıyor."
        )

    avg_cost      = compute_weighted_average(lots)
    cost_closed   = q_amount(avg_cost * qty_to_close)
    qty_remaining = q_qty(total_qty - qty_to_close)

    if qty_remaining <= ZERO:
        # Tam kapatma — lot listesi boşaltılıyor
        return cost_closed, []

    # Kalan toplam maliyet
    total_cost  = sum((lt.total_cost_usdt for lt in lots), ZERO)
    cost_remain = q_amount(total_cost - cost_closed)

    # Birleşik tek lot olarak döndür (WAVG convention)
    if lots:
        first = lots[0]
        remaining_lot = CostBasisLot(
            symbol=first.symbol,
            side=first.side,
            quantity=qty_remaining,
            total_cost_usdt=cost_remain,
            opened_at=first.opened_at,
            trade_id=first.trade_id,
        )
        return cost_closed, [remaining_lot]

    return cost_closed, []


# ══════════════════════════════════════════════════════════════════════════════
# K/Z hesaplama
# ══════════════════════════════════════════════════════════════════════════════

def compute_realized_pnl(
    avg_cost_per_unit: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    side: TradeSide,
) -> Decimal:
    """
    Gerçekleşmiş K/Z hesapla.

    LONG:  pnl = (exit_price - avg_cost) × quantity
    SHORT: pnl = (avg_cost - exit_price) × quantity

    Pozitif → kâr, negatif → zarar.
    """
    if side == TradeSide.LONG:
        pnl = (exit_price - avg_cost_per_unit) * quantity
    else:
        pnl = (avg_cost_per_unit - exit_price) * quantity
    return q_amount(pnl)


def compute_unrealized_pnl(
    avg_cost_per_unit: Decimal,
    current_price: Decimal,
    quantity: Decimal,
    side: TradeSide,
) -> Decimal:
    """
    Gerçekleşmemiş K/Z hesapla (mark-to-market).
    Formül compute_realized_pnl ile aynı; çıkış yerine anlık fiyat kullanılır.
    """
    return compute_realized_pnl(avg_cost_per_unit, current_price, quantity, side)


# ══════════════════════════════════════════════════════════════════════════════
# Ücretin maliyet esasına dahil edilmesi
# ══════════════════════════════════════════════════════════════════════════════

def apply_fee_to_cost_basis(
    avg_cost_per_unit: Decimal,
    fee_usdt: Decimal,
    quantity: Decimal,
) -> Decimal:
    """
    Ücret dahil birim başına etkin maliyet.

    etkin_avg = avg_cost + (fee_usdt / quantity)

    Kural: ücret her zaman maliyeti artırır (yatırımcı aleyhine).
    """
    if quantity <= ZERO:
        raise CostBasisError(
            f"apply_fee_to_cost_basis: miktar pozitif olmalı, {quantity} verildi."
        )
    fee_per_unit = safe_divide(fee_usdt, quantity)
    return q_price(avg_cost_per_unit + fee_per_unit)


def compute_fee_inclusive_cost(
    quantity: Decimal,
    entry_price: Decimal,
    fee_rate: Decimal = DEFAULT_TAKER_RATE,
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Ücret dahil toplam maliyet esasını hesapla.

    Args:
        quantity:    Satın alınan coin miktarı.
        entry_price: Giriş fiyatı.
        fee_rate:    Ücret oranı (örn. 0.00040000 = %0.04).

    Returns:
        (notional_usdt, fee_usdt, total_cost_usdt)
        - notional_usdt:   quantity × entry_price
        - fee_usdt:        notional × fee_rate
        - total_cost_usdt: notional + fee (maliyet esası)
    """
    notional   = q_amount(quantity * entry_price)
    fee        = q_amount(notional * fee_rate)
    total_cost = q_amount(notional + fee)
    return notional, fee, total_cost
