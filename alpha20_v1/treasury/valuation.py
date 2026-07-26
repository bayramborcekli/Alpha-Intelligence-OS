"""
treasury/valuation.py — Pozisyon ve portföy değerleme (mark-to-market).

Tasarım kuralları:
- Tüm değerlemeler anlık fiyata (current_price) dayanır.
- NAV = Nakit + Σ(açık pozisyon mark-to-market değerleri).
- Gerçekleşmemiş K/Z: yön duyarlı (LONG/SHORT).
- Değerleme nesneleri değiştirilemez (frozen dataclass).
- Exchange-independent: fiyat dışarıdan enjekte edilir.

LONG  unrealized = (current_price - avg_cost) × quantity
SHORT unrealized = (avg_cost - current_price) × quantity
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from .precision import q_amount, q_rate, safe_divide, ZERO, to_decimal
from .types import PortfolioValuation, PositionValuation, TradeSide


class ValuationError(Exception):
    """Geçersiz değerleme girişi."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# Tek pozisyon değerleme
# ══════════════════════════════════════════════════════════════════════════════

def valuate_position(
    *,
    symbol: str,
    side: TradeSide,
    quantity: Decimal,
    avg_cost_per_unit: Decimal,
    current_price: Decimal,
) -> PositionValuation:
    """
    Açık pozisyonun anlık değerini hesapla.

    Args:
        symbol:            İşlem çifti (örn. "BTCUSDT").
        side:              LONG veya SHORT.
        quantity:          Pozisyon miktarı (> 0).
        avg_cost_per_unit: Ağırlıklı ortalama birim giriş maliyeti.
        current_price:     Anlık piyasa fiyatı.

    Returns:
        PositionValuation (değiştirilemez).

    Değişmezlikler:
    - quantity > 0
    - avg_cost_per_unit > 0
    - current_price > 0
    """
    if quantity <= ZERO:
        raise ValuationError(
            f"valuate_position ({symbol}): miktar pozitif olmalı, {quantity} verildi."
        )
    if avg_cost_per_unit <= ZERO:
        raise ValuationError(
            f"valuate_position ({symbol}): avg_cost_per_unit pozitif olmalı, "
            f"{avg_cost_per_unit} verildi."
        )
    if current_price <= ZERO:
        raise ValuationError(
            f"valuate_position ({symbol}): current_price pozitif olmalı, "
            f"{current_price} verildi."
        )

    mark_to_market = q_amount(quantity * current_price)

    if side == TradeSide.LONG:
        unrealized_pnl = q_amount((current_price - avg_cost_per_unit) * quantity)
    else:
        unrealized_pnl = q_amount((avg_cost_per_unit - current_price) * quantity)

    cost_basis_total = q_amount(avg_cost_per_unit * quantity)
    unrealized_pnl_pct = q_rate(
        safe_divide(unrealized_pnl, cost_basis_total) * Decimal("100")
        if cost_basis_total > ZERO else ZERO
    )

    return PositionValuation(
        symbol=symbol,
        side=side,
        quantity=quantity,
        avg_cost_per_unit=avg_cost_per_unit,
        current_price=current_price,
        mark_to_market_usdt=mark_to_market,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_pct=unrealized_pnl_pct,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Portföy değerleme
# ══════════════════════════════════════════════════════════════════════════════

def valuate_portfolio(
    *,
    cash_usdt: Decimal,
    open_positions: Sequence[dict],
    timestamp: datetime | None = None,
) -> PortfolioValuation:
    """
    Tüm portföyün anlık değerlemesini hesapla.

    Args:
        cash_usdt:      Mevcut nakit bakiyesi (USDT).
        open_positions: Her biri şu alanları içeren dict listesi:
                          - symbol: str
                          - side: TradeSide (veya "LONG"/"SHORT" str)
                          - quantity: Decimal | float
                          - avg_cost_per_unit: Decimal | float
                          - current_price: Decimal | float
        timestamp:      Değerleme zamanı (None → şimdi).

    Returns:
        PortfolioValuation (değiştirilemez).

    Değişmezlik:
    - cash_usdt >= 0.
    - NAV = cash + Σ(mark_to_market_usdt).
    """
    if cash_usdt < ZERO:
        raise ValuationError(
            f"valuate_portfolio: nakit negatif olamaz, {cash_usdt} verildi."
        )

    position_vals: list[PositionValuation] = []
    for pos in open_positions:
        side = pos["side"]
        if isinstance(side, str):
            side = TradeSide(side)
        pv = valuate_position(
            symbol=pos["symbol"],
            side=side,
            quantity=to_decimal(pos["quantity"]),
            avg_cost_per_unit=to_decimal(pos["avg_cost_per_unit"]),
            current_price=to_decimal(pos["current_price"]),
        )
        position_vals.append(pv)

    total_position_value = q_amount(
        sum((pv.mark_to_market_usdt for pv in position_vals), ZERO)
    )
    total_unrealized_pnl = q_amount(
        sum((pv.unrealized_pnl for pv in position_vals), ZERO)
    )
    nav = q_amount(cash_usdt + total_position_value)

    return PortfolioValuation(
        cash_usdt=q_amount(cash_usdt),
        positions=tuple(position_vals),
        total_position_value=total_position_value,
        nav_usdt=nav,
        total_unrealized_pnl=total_unrealized_pnl,
        timestamp=timestamp or _now_utc(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Yardımcı hesaplamalar
# ══════════════════════════════════════════════════════════════════════════════

def compute_drawdown_pct(
    current_nav: Decimal,
    peak_nav: Decimal,
) -> Decimal:
    """
    Peak NAV'a göre drawdown yüzdesini hesapla.

    drawdown = (peak - current) / peak × 100

    Pozitif → kayıp var, sıfır → peak'te.
    Peak sıfırsa sıfır döner.
    """
    if peak_nav <= ZERO:
        return ZERO
    dd = safe_divide(peak_nav - current_nav, peak_nav) * Decimal("100")
    return q_rate(max(ZERO, dd))


def compute_daily_pnl_pct(
    current_balance: Decimal,
    day_start_balance: Decimal,
) -> Decimal:
    """
    Günlük K/Z yüzdesini hesapla.

    pct = (current - day_start) / day_start × 100

    Pozitif → kâr, negatif → zarar.
    """
    if day_start_balance <= ZERO:
        return ZERO
    pct = safe_divide(current_balance - day_start_balance, day_start_balance) * Decimal("100")
    return q_rate(pct)


def compute_position_weight(
    position_value_usdt: Decimal,
    nav_usdt: Decimal,
) -> Decimal:
    """
    Pozisyonun portföy NAV içindeki ağırlığı (%).
    """
    return q_rate(safe_divide(position_value_usdt, nav_usdt) * Decimal("100"))
