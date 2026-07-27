"""Mission 2200 Agent 02 — Performans metrikleri (saf modül).

Kapalı işlem kayıtlarından ve özkaynak eğrisinden performans
metrikleri hesaplar. Kurallar:

- Yalnız stdlib; framework yok, G/Ç yok, borsa çağrısı yok.
- Tüm parasal aritmetik Decimal'dir; float ASLA kullanılmaz.
- Yetersiz veri → None (UI'da UNKNOWN). Sahte sıfır üretilmez:
  "0 işlem → %0 kazanma oranı" YANLIŞTIR; doğrusu UNKNOWN'dur.
- Bozuk kayıtlar sessizce atlanmaz; kayıt sayacı üzerinden
  görünür (`dropped_records`).

Sözleşme — işlem kaydı (Mapping):
    realized_pnl : Decimal'e çevrilebilir (zorunlu)
    fees         : Decimal'e çevrilebilir (opsiyonel)
    opened_at    : epoch saniye int (opsiyonel, hold süresi için)
    closed_at    : epoch saniye int (zorunlu; pencere filtreleri)
    symbol       : metin (opsiyonel)

Sözleşme — özkaynak noktası (Mapping):
    at     : epoch saniye int
    equity : Decimal'e çevrilebilir, > 0
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Mapping, Optional, Sequence, Tuple

__all__ = [
    "TradeRecord", "EquityPoint", "PerformanceMetrics",
    "parse_trades", "parse_equity_points", "compute_metrics",
    "DAY_SECONDS", "WEEK_SECONDS", "MONTH_SECONDS",
    "utc_day_profit",
]

DAY_SECONDS = 86400
WEEK_SECONDS = 7 * DAY_SECONDS
MONTH_SECONDS = 30 * DAY_SECONDS

_QUANT_PCT = Decimal("0.01")
_QUANT_MONEY = Decimal("0.00000001")
_QUANT_RATIO = Decimal("0.0001")


def _to_decimal(value: object) -> Optional[Decimal]:
    """Float bilinçli olarak reddedilir (temsil hatası taşır)."""
    if isinstance(value, bool) or isinstance(value, float):
        return None
    if isinstance(value, Decimal):
        return None if not value.is_finite() else value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
    return None


def _to_epoch(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


@dataclass(frozen=True)
class TradeRecord:
    """Doğrulanmış kapalı işlem kaydı."""
    realized_pnl: Decimal
    fees: Decimal
    closed_at: int
    opened_at: Optional[int]
    symbol: str

    @property
    def net_pnl(self) -> Decimal:
        return self.realized_pnl - self.fees

    @property
    def hold_seconds(self) -> Optional[int]:
        if self.opened_at is None or self.opened_at > self.closed_at:
            return None
        return self.closed_at - self.opened_at


@dataclass(frozen=True)
class EquityPoint:
    at: int
    equity: Decimal


def parse_trades(raw: object) -> Tuple[Tuple[TradeRecord, ...], int]:
    """Ham kayıt listesini doğrula. Dönen: (kayıtlar, düşen sayısı)."""
    if not isinstance(raw, (list, tuple)):
        return (), 0
    records = []
    dropped = 0
    for item in raw:
        if not isinstance(item, Mapping):
            dropped += 1
            continue
        pnl = _to_decimal(item.get("realized_pnl"))
        closed_at = _to_epoch(item.get("closed_at"))
        if pnl is None or closed_at is None:
            dropped += 1
            continue
        fees = _to_decimal(item.get("fees"))
        symbol = item.get("symbol")
        records.append(TradeRecord(
            realized_pnl=pnl,
            fees=fees if fees is not None and fees >= 0 else Decimal("0"),
            closed_at=closed_at,
            opened_at=_to_epoch(item.get("opened_at")),
            symbol=symbol.strip().upper()
            if isinstance(symbol, str) and symbol.strip() else "UNKNOWN",
        ))
    records.sort(key=lambda r: r.closed_at)
    return tuple(records), dropped


def parse_equity_points(raw: object) -> Tuple[EquityPoint, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    points = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        at = _to_epoch(item.get("at"))
        equity = _to_decimal(item.get("equity"))
        if at is None or equity is None or equity <= 0:
            continue
        points.append(EquityPoint(at=at, equity=equity))
    points.sort(key=lambda p: p.at)
    return tuple(points)


@dataclass(frozen=True)
class PerformanceMetrics:
    """None alanlar UI'da UNKNOWN olarak gösterilir — asla 0 değil."""
    trade_count: int
    win_count: int
    loss_count: int
    flat_count: int
    dropped_records: int
    win_rate_pct: Optional[Decimal]
    loss_rate_pct: Optional[Decimal]
    average_win: Optional[Decimal]
    average_loss: Optional[Decimal]
    profit_factor: Optional[Decimal]
    sharpe: Optional[Decimal]
    max_drawdown_pct: Optional[Decimal]
    average_hold_seconds: Optional[int]
    daily_profit: Optional[Decimal]
    weekly_profit: Optional[Decimal]
    monthly_profit: Optional[Decimal]
    equity_curve: Tuple[EquityPoint, ...]


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _sqrt(value: Decimal) -> Optional[Decimal]:
    if value < 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        return value.sqrt()


def period_profit(trades: Sequence[TradeRecord], now: int,
                  window_seconds: int) -> Optional[Decimal]:
    """Pencere içinde kapanan işlem yoksa None (UNKNOWN) döner —
    'işlem yok' ile 'kâr 0' aynı şey DEĞİLDİR; pencere içinde işlem
    varsa toplam net PnL döner."""
    if not isinstance(now, int) or now <= 0 or window_seconds <= 0:
        return None
    inside = [t.net_pnl for t in trades
              if now - window_seconds <= t.closed_at <= now]
    if not inside:
        return None
    return sum(inside, Decimal("0")).quantize(_QUANT_MONEY)


def utc_day_profit(trades: Sequence[TradeRecord],
                   now: int) -> Optional[Decimal]:
    """'Bugünkü Kazanç': UTC gün başlangıcından (00:00) şu ana
    kadar kapanan işlemlerin net PnL toplamı. Kayan 24 saatlik
    pencereden farkı: gün dönümünde sıfırdan saymaya başlar —
    dünkü işlemler bugünün kazancına sızmaz.

    Bugün kapanan işlem yoksa None (UNKNOWN) döner; 'işlem yok'
    ile 'kâr 0' aynı şey DEĞİLDİR."""
    if not isinstance(now, int) or isinstance(now, bool) or now <= 0:
        return None
    day_start = (now // DAY_SECONDS) * DAY_SECONDS
    inside = [t.net_pnl for t in trades
              if day_start <= t.closed_at <= now]
    if not inside:
        return None
    return sum(inside, Decimal("0")).quantize(_QUANT_MONEY)


def sharpe_ratio(returns: Sequence[Decimal]) -> Optional[Decimal]:
    """Basit Sharpe (rf=0): mean/std (örneklem std, n-1).
    <2 getiri veya sıfır sapma → None."""
    clean = [r for r in returns if isinstance(r, Decimal)
             and r.is_finite()]
    if len(clean) < 2:
        return None
    mean = _mean(clean)
    variance = sum(((r - mean) ** 2 for r in clean),
                   Decimal("0")) / Decimal(len(clean) - 1)
    std = _sqrt(variance)
    if std is None or std == 0:
        return None
    return (mean / std).quantize(_QUANT_RATIO)


def equity_returns(points: Sequence[EquityPoint]
                   ) -> Tuple[Decimal, ...]:
    out = []
    for prev, cur in zip(points, points[1:]):
        if prev.equity > 0:
            out.append((cur.equity - prev.equity) / prev.equity)
    return tuple(out)


def max_drawdown_pct(points: Sequence[EquityPoint]
                     ) -> Optional[Decimal]:
    if len(points) < 2:
        return None
    peak = points[0].equity
    worst = Decimal("0")
    for point in points:
        if point.equity > peak:
            peak = point.equity
        if peak > 0:
            dd = (peak - point.equity) / peak * Decimal("100")
            if dd > worst:
                worst = dd
    return worst.quantize(_QUANT_PCT)


def compute_metrics(trades_raw: object, equity_raw: object,
                    now: int) -> PerformanceMetrics:
    trades, dropped = parse_trades(trades_raw)
    points = parse_equity_points(equity_raw)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    flats = [t for t in trades if t.net_pnl == 0]
    count = len(trades)

    win_rate = loss_rate = None
    if count > 0:
        win_rate = (Decimal(len(wins)) / Decimal(count)
                    * Decimal("100")).quantize(_QUANT_PCT)
        loss_rate = (Decimal(len(losses)) / Decimal(count)
                     * Decimal("100")).quantize(_QUANT_PCT)

    average_win = (_mean([t.net_pnl for t in wins])
                   .quantize(_QUANT_MONEY) if wins else None)
    average_loss = (_mean([t.net_pnl for t in losses])
                    .quantize(_QUANT_MONEY) if losses else None)

    profit_factor = None
    gross_win = sum((t.net_pnl for t in wins), Decimal("0"))
    gross_loss = sum((t.net_pnl for t in losses), Decimal("0"))
    if gross_loss < 0 and gross_win > 0:
        profit_factor = (gross_win / -gross_loss).quantize(
            _QUANT_RATIO)

    holds = [t.hold_seconds for t in trades
             if t.hold_seconds is not None]
    average_hold = (sum(holds) // len(holds)) if holds else None

    return PerformanceMetrics(
        trade_count=count,
        win_count=len(wins),
        loss_count=len(losses),
        flat_count=len(flats),
        dropped_records=dropped,
        win_rate_pct=win_rate,
        loss_rate_pct=loss_rate,
        average_win=average_win,
        average_loss=average_loss,
        profit_factor=profit_factor,
        sharpe=sharpe_ratio(equity_returns(points)),
        max_drawdown_pct=max_drawdown_pct(points),
        average_hold_seconds=average_hold,
        daily_profit=utc_day_profit(trades, now),
        weekly_profit=period_profit(trades, now, WEEK_SECONDS),
        monthly_profit=period_profit(trades, now, MONTH_SECONDS),
        equity_curve=points,
    )
