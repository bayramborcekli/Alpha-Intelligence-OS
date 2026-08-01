"""ADR-024: deterministik 4 saatlik Spot trend/kırılım araştırma motoru.

Bu modül borsaya bağlanmaz ve dosya yazmaz. Girdi olarak Binance kline
satırlarını alır; bütün fiyat, maliyet ve performans matematiğini ``Decimal``
ile yapar. Sinyal kapanan mumdan üretilir, giriş/normal çıkış bir sonraki mum
açılışında gerçekleşir. Stop aynı mumda tetiklenirse muhafazakâr olarak önce
stop değerlendirilir.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence


ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


def dec(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("decimal value is missing")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid decimal value") from exc
    if not result.is_finite():
        raise ValueError("decimal value is not finite")
    return result


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: int

    @classmethod
    def from_binance(cls, row: Sequence[Any]) -> "Candle":
        if len(row) < 7:
            raise ValueError("kline row is incomplete")
        candle = cls(
            open_time=int(row[0]), open=dec(row[1]), high=dec(row[2]),
            low=dec(row[3]), close=dec(row[4]), volume=dec(row[5]),
            close_time=int(row[6]),
        )
        if min(candle.open, candle.high, candle.low, candle.close) <= ZERO:
            raise ValueError("kline price must be positive")
        if candle.low > candle.high or candle.open_time >= candle.close_time:
            raise ValueError("kline bounds are invalid")
        return candle


@dataclass(frozen=True)
class StrategyParams:
    ema_period: int = 200
    channel_period: int = 40
    exit_channel_period: int = 20
    atr_period: int = 14
    atr_stop_multiplier: Decimal = Decimal("2.5")
    atr_trail_multiplier: Decimal = Decimal("3.0")
    ema_slope_bars: int = 8

    def public(self) -> dict[str, Any]:
        raw = asdict(self)
        for key, value in tuple(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)
        return raw


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_time: int
    exit_time: int
    entry: Decimal
    exit: Decimal
    net_return_pct: Decimal
    reason: str


def ema(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    if period < 2:
        raise ValueError("EMA period must be at least 2")
    out: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period], ZERO) / Decimal(period)
    out[period - 1] = seed
    alpha = Decimal("2") / Decimal(period + 1)
    previous = seed
    for index in range(period, len(values)):
        previous = values[index] * alpha + previous * (ONE - alpha)
        out[index] = previous
    return out


def atr(candles: Sequence[Candle], period: int) -> list[Decimal | None]:
    if period < 2:
        raise ValueError("ATR period must be at least 2")
    ranges: list[Decimal] = []
    for index, candle in enumerate(candles):
        if index == 0:
            ranges.append(candle.high - candle.low)
            continue
        previous_close = candles[index - 1].close
        ranges.append(max(candle.high - candle.low,
                          abs(candle.high - previous_close),
                          abs(candle.low - previous_close)))
    out: list[Decimal | None] = [None] * len(candles)
    if len(ranges) < period:
        return out
    current = sum(ranges[:period], ZERO) / Decimal(period)
    out[period - 1] = current
    for index in range(period, len(ranges)):
        current = ((current * Decimal(period - 1)) + ranges[index]) / \
            Decimal(period)
        out[index] = current
    return out


def _close_trade(symbol: str, entry_candle: Candle, exit_candle: Candle,
                 entry: Decimal, exit_price: Decimal, cost_pct: Decimal,
                 reason: str) -> Trade:
    raw_pct = (exit_price / entry - ONE) * HUNDRED
    return Trade(symbol=symbol, entry_time=entry_candle.open_time,
                 exit_time=exit_candle.open_time, entry=entry,
                 exit=exit_price, net_return_pct=raw_pct - cost_pct,
                 reason=reason)


def backtest_window(symbol: str, candles: Sequence[Candle],
                    params: StrategyParams, cost_pct: Decimal,
                    start: int, end: int) -> list[Trade]:
    """``[start, end)`` girişleri için tek-pozisyon long backtest çalıştırır."""
    if cost_pct < Decimal("0.30"):
        raise ValueError("round-trip cost must be at least 0.30 percent")
    if not 0 <= start < end <= len(candles):
        raise ValueError("invalid backtest window")
    closes = [row.close for row in candles]
    ema_values = ema(closes, params.ema_period)
    atr_values = atr(candles, params.atr_period)
    warmup = max(params.ema_period + params.ema_slope_bars,
                 params.channel_period, params.exit_channel_period,
                 params.atr_period)
    trades: list[Trade] = []
    pending_entry_atr: Decimal | None = None
    pending_exit_reason: str | None = None
    entry_candle: Candle | None = None
    entry_price: Decimal | None = None
    stop: Decimal | None = None
    peak: Decimal | None = None

    for index in range(max(1, start), end):
        candle = candles[index]
        if entry_candle is not None and pending_exit_reason is not None:
            trades.append(_close_trade(
                symbol, entry_candle, candle, entry_price or ZERO,
                candle.open, cost_pct, pending_exit_reason))
            entry_candle = entry_price = stop = peak = None
            pending_exit_reason = None

        if entry_candle is None and pending_entry_atr is not None:
            entry_candle = candle
            entry_price = candle.open
            peak = candle.open
            stop = candle.open - pending_entry_atr * \
                params.atr_stop_multiplier
            pending_entry_atr = None

        if entry_candle is not None:
            assert entry_price is not None and stop is not None and peak is not None
            if candle.low <= stop:
                exit_price = min(candle.open, stop)
                trades.append(_close_trade(
                    symbol, entry_candle, candle, entry_price, exit_price,
                    cost_pct, "ATR_STOP"))
                entry_candle = entry_price = stop = peak = None
                pending_exit_reason = None
                continue
            ema_now = ema_values[index]
            exit_from = max(0, index - params.exit_channel_period)
            prior_exit_low = min(row.low for row in candles[exit_from:index])
            if candle.close < prior_exit_low:
                pending_exit_reason = "CHANNEL_EXIT"
            elif ema_now is not None and candle.close < ema_now:
                pending_exit_reason = "TREND_EXIT"
            peak = max(peak, candle.high)
            atr_now = atr_values[index]
            if atr_now is not None:
                stop = max(stop, peak - atr_now * params.atr_trail_multiplier)
            continue

        signal_index = index
        if signal_index < warmup or signal_index + 1 >= end:
            continue
        ema_now = ema_values[signal_index]
        ema_then = ema_values[signal_index - params.ema_slope_bars]
        atr_now = atr_values[signal_index]
        if ema_now is None or ema_then is None or atr_now is None:
            continue
        channel_from = signal_index - params.channel_period
        prior_high = max(row.high for row in
                         candles[channel_from:signal_index])
        if (candle.close > prior_high and candle.close > ema_now and
                ema_now > ema_then):
            pending_entry_atr = atr_now

    if entry_candle is not None:
        final = candles[end - 1]
        trades.append(_close_trade(
            symbol, entry_candle, final, entry_price or ZERO, final.close,
            cost_pct, "WINDOW_END"))
    return trades


def metrics(trades: Iterable[Trade]) -> dict[str, Any]:
    rows = list(trades)
    gains = sum((row.net_return_pct for row in rows
                 if row.net_return_pct > ZERO), ZERO)
    losses = -sum((row.net_return_pct for row in rows
                   if row.net_return_pct < ZERO), ZERO)
    net = gains - losses
    factor = gains / losses if losses > ZERO else None
    wins = sum(row.net_return_pct > ZERO for row in rows)
    return {
        "trades": len(rows),
        "wins": wins,
        "losses": len(rows) - wins,
        "net_return_pct": str(net),
        "gross_gain_pct": str(gains),
        "gross_loss_pct": str(losses),
        "profit_factor": str(factor) if factor is not None else None,
        "profit_factor_state": ("CALCULATED" if factor is not None else
                                "NO_LOSSES" if gains > ZERO else
                                "NO_TRADES"),
    }


def default_parameter_grid() -> tuple[StrategyParams, ...]:
    return tuple(
        StrategyParams(ema_period=ema_period, channel_period=channel,
                       exit_channel_period=exit_channel,
                       atr_stop_multiplier=stop,
                       atr_trail_multiplier=trail)
        for ema_period in (100, 200)
        for channel in (20, 40, 55)
        for exit_channel in (10, 20)
        for stop in (Decimal("2.0"), Decimal("2.5"), Decimal("3.0"))
        for trail in (Decimal("2.5"), Decimal("3.0"), Decimal("3.5"))
    )


def _split(length: int) -> tuple[int, int]:
    train_end = length * 60 // 100
    validation_end = length * 80 // 100
    return train_end, validation_end


def _stage(data: dict[str, Sequence[Candle]], params: StrategyParams,
           cost_pct: Decimal, stage: str) -> dict[str, Any]:
    all_trades: list[Trade] = []
    per_symbol: dict[str, Any] = {}
    for symbol, candles in sorted(data.items()):
        train_end, validation_end = _split(len(candles))
        if stage == "train":
            start, end = 0, train_end
        elif stage == "validation":
            start, end = train_end, validation_end
        elif stage == "holdout":
            start, end = validation_end, len(candles)
        else:
            raise ValueError("unknown stage")
        rows = backtest_window(symbol, candles, params, cost_pct, start, end)
        all_trades.extend(rows)
        per_symbol[symbol] = metrics(rows)
    result = metrics(all_trades)
    result["per_symbol"] = per_symbol
    return result


def _rank(stage: dict[str, Any]) -> tuple[Decimal, Decimal, int]:
    pf = dec(stage["profit_factor"]) if stage["profit_factor"] is not None \
        else (Decimal("999") if stage["trades"] else ZERO)
    return pf, dec(stage["net_return_pct"]), int(stage["trades"])


def evaluate(data: dict[str, Sequence[Candle]], *,
             cost_pct: Decimal = Decimal("0.30"),
             grid: Sequence[StrategyParams] | None = None,
             minimum_train_trades: int = 20,
             minimum_stage_trades: int = 8) -> dict[str, Any]:
    if not data or any(len(rows) < 500 for rows in data.values()):
        raise ValueError("each symbol needs at least 500 chronological candles")
    candidates: list[tuple[tuple[Decimal, Decimal, int], StrategyParams,
                           dict[str, Any]]] = []
    for params in grid or default_parameter_grid():
        train = _stage(data, params, cost_pct, "train")
        qualified = int(train["trades"]) >= minimum_train_trades and \
            dec(train["net_return_pct"]) > ZERO
        rank = _rank(train) if qualified else (ZERO, dec(
            train["net_return_pct"]), int(train["trades"]))
        candidates.append((rank, params, train))
    candidates.sort(key=lambda row: row[0], reverse=True)
    _, selected, train = candidates[0]
    validation = _stage(data, selected, cost_pct, "validation")
    holdout = _stage(data, selected, cost_pct, "holdout")

    def passed(stage: dict[str, Any]) -> bool:
        factor = stage["profit_factor"]
        pf_ok = ((factor is None and stage["profit_factor_state"] ==
                  "NO_LOSSES" and stage["trades"] > 0) or
                 (factor is not None and dec(factor) >= Decimal("1.20")))
        return (stage["trades"] >= minimum_stage_trades and
                dec(stage["net_return_pct"]) > ZERO and pf_ok)

    validation_pass = passed(validation)
    holdout_pass = passed(holdout)
    status = "PASS" if validation_pass and holdout_pass else "REJECTED"
    return {
        "ok": True,
        "strategy_version": "PAPER_PROFIT_V1_CANDIDATE",
        "status": status,
        "activation": ("PAPER_READY" if status == "PASS" else
                       "BLOCKED_FAILED_EVIDENCE"),
        "timeframe": "4h",
        "direction": "LONG_ONLY",
        "entry_family": "TREND_FILTERED_DONCHIAN_BREAKOUT",
        "exit_family": "ATR_TRAILING_AND_CHANNEL_TREND_EXIT",
        "round_trip_cost_pct": str(cost_pct),
        "selected_on": "TRAIN_ONLY",
        "holdout_used_for_selection": False,
        "parameters": selected.public(),
        "train": train,
        "validation": validation,
        "holdout": holdout,
        "gates": {
            "minimum_stage_trades": minimum_stage_trades,
            "validation_net_positive_and_pf_1_20": validation_pass,
            "holdout_net_positive_and_pf_1_20": holdout_pass,
            "legacy_pf_0_48_excluded": True,
            "forced_trade_frequency": False,
            "forced_ten_minute_exit": False,
            "live_orders": "DISABLED",
            "exchange_write_requests": 0,
        },
        "candidates_tested": len(candidates),
    }
