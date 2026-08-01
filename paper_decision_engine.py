"""ADR-016 — rejim-duyarlı, maliyet-sonrası Paper karar motoru.

Bu modül saf/deterministiktir: ağ, borsa yazma isteği, dosya yazımı veya
canlı emir yolu içermez. 5 dakikalık gerçek mumlardan 15m ve 1h serileri
türetilir; güncel rejime uygun girişler walk-forward gözlemleriyle kalibre
edilir. Yetersiz örneklemde güvenli sonuç NO_TRADE'dir.
"""
from __future__ import annotations

from bisect import bisect_right
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Iterable


D = Decimal
ZERO = D("0")
ONE = D("1")
HUNDRED = D("100")

REGIME_TREND = "TREND"
REGIME_RANGE = "RANGE"
REGIME_NO_TRADE = "UNSTABLE_NO_TRADE"

STRATEGY_TREND = "TREND_PULLBACK"
STRATEGY_RANGE = "RANGE_REBOUND"
STRATEGY_VERSION = "RECOVERY_FOCUSED_V1"

TREND_ROUTE_CONTINUATION = "TREND_CONTINUATION"
TREND_ROUTE_PULLBACK_RECOVERY = "PULLBACK_RECOVERY"
MAX_HOURLY_PULLBACK_BELOW_EMA20_PCT = D("0.60")

DECISION_CHAIN = ("REGIME", "STRATEGY_FIT", "NET_EV", "RANK",
                  "PAPER_ENTRY")


class DecisionDataError(ValueError):
    """Mum dizisi karar üretmeye uygun değil."""


def _decimal(value: Any) -> Decimal:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DecisionDataError("sayısal alan geçersiz") from exc
    if not out.is_finite():
        raise DecisionDataError("sayısal alan sonlu değil")
    return out


def _text(value: Decimal | None, places: str = "0.0001") -> str | None:
    if value is None:
        return None
    return format(value.quantize(D(places)), "f")


def _mean(values: Iterable[Decimal]) -> Decimal:
    vals = list(values)
    return sum(vals, ZERO) / D(len(vals)) if vals else ZERO


def _std(values: Iterable[Decimal]) -> Decimal:
    vals = list(values)
    if len(vals) < 2:
        return ZERO
    avg = _mean(vals)
    variance = sum(((v - avg) ** 2 for v in vals), ZERO) / D(len(vals))
    with localcontext() as ctx:
        ctx.prec = 28
        return variance.sqrt()


def _ema(values: list[Decimal], period: int) -> Decimal:
    if not values:
        return ZERO
    weight = D(2) / D(period + 1)
    out = values[0]
    for value in values[1:]:
        out = value * weight + out * (ONE - weight)
    return out


def _parse_5m(raw_klines: list[list]) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    prior_ts: int | None = None
    for raw in raw_klines:
        if not isinstance(raw, (list, tuple)) or len(raw) < 6:
            raise DecisionDataError("mum alanları eksik")
        try:
            ts = int(raw[0])
        except (TypeError, ValueError) as exc:
            raise DecisionDataError("mum zamanı geçersiz") from exc
        if prior_ts is not None and ts <= prior_ts:
            raise DecisionDataError("mum zamanları sıralı değil")
        prior_ts = ts
        opn, high, low, close, volume = (
            _decimal(raw[1]), _decimal(raw[2]), _decimal(raw[3]),
            _decimal(raw[4]), _decimal(raw[5]))
        if min(opn, high, low, close) <= ZERO or volume < ZERO:
            raise DecisionDataError("mum fiyatı/hacmi geçersiz")
        if high < max(opn, close) or low > min(opn, close) or high < low:
            raise DecisionDataError("mum OHLC bütünlüğü geçersiz")
        try:
            close_ts = int(raw[6]) if len(raw) > 6 else ts + 300_000 - 1
        except (TypeError, ValueError):
            close_ts = ts + 300_000 - 1
        candles.append({
            "ts": ts, "close_ts": close_ts, "open": opn, "high": high,
            "low": low, "close": close, "volume": volume,
        })
    return candles


def _aggregate(candles: list[dict[str, Any]], minutes: int) \
        -> list[dict[str, Any]]:
    bucket_ms = minutes * 60_000
    out: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    active_bucket: int | None = None
    for candle in candles:
        bucket = candle["ts"] // bucket_ms
        if bucket != active_bucket:
            if active is not None:
                out.append(active)
            active_bucket = bucket
            active = {
                "ts": bucket * bucket_ms,
                "close_ts": (bucket + 1) * bucket_ms - 1,
                "open": candle["open"], "high": candle["high"],
                "low": candle["low"], "close": candle["close"],
                "volume": candle["volume"],
            }
        else:
            assert active is not None
            active["high"] = max(active["high"], candle["high"])
            active["low"] = min(active["low"], candle["low"])
            active["close"] = candle["close"]
            active["volume"] += candle["volume"]
    if active is not None:
        out.append(active)
    return out


def aggregate_klines(raw_klines: list[list], minutes: int) -> list[dict]:
    """Test/teşhis için JSON-uyumlu 15m veya 1h mumları döndürür."""
    if minutes not in (15, 60):
        raise ValueError("yalnız 15m ve 60m desteklenir")
    candles = _aggregate(_parse_5m(raw_klines), minutes)
    return [{
        "ts": c["ts"], "close_ts": c["close_ts"],
        "open": _text(c["open"]), "high": _text(c["high"]),
        "low": _text(c["low"]), "close": _text(c["close"]),
        "volume": _text(c["volume"]),
    } for c in candles]


def _returns(closes: list[Decimal]) -> list[Decimal]:
    out = []
    for prior, current in zip(closes, closes[1:]):
        out.append((current / prior - ONE) * HUNDRED if prior else ZERO)
    return out


def classify_regime(hourly: list[dict[str, Any]],
                    fifteen: list[dict[str, Any]]) -> dict[str, Any]:
    """1h ana yön + 15m doğrulamadan açıklanabilir rejim üretir."""
    if len(hourly) < 55 or len(fifteen) < 80:
        return {"regime": REGIME_NO_TRADE,
                "reason_code": "DATA_QUALITY", "evidence": {}}
    h_close = [_decimal(c["close"]) for c in hourly]
    q_close = [_decimal(c["close"]) for c in fifteen]
    h_ret = _returns(h_close)[-24:]
    volatility = _std(h_ret)
    last_return = abs(h_ret[-1]) if h_ret else ZERO
    spike_limit = max(D("1.20"), volatility * D(3))

    ema20 = _ema(h_close[-55:], 20)
    ema50 = _ema(h_close[-55:], 50)
    ema20_prior = _ema(h_close[-55:-3], 20)
    strength = abs(ema20 / ema50 - ONE) * HUNDRED if ema50 else ZERO
    slope = (ema20 / ema20_prior - ONE) * HUNDRED \
        if ema20_prior else ZERO
    q_fast = _ema(q_close[-40:], 12)
    q_slow = _ema(q_close[-40:], 26)
    fifteen_recovery = len(q_close) >= 2 and q_close[-1] > q_close[-2]
    recent_h = hourly[-24:]
    range_low = min(_decimal(c["low"]) for c in recent_h)
    range_high = max(_decimal(c["high"]) for c in recent_h)
    range_width = (range_high / range_low - ONE) * HUNDRED \
        if range_low else ZERO
    pullback_depth = max(
        ZERO, (ONE - h_close[-1] / ema20) * HUNDRED) if ema20 else ZERO
    trend_structure = ema20 > ema50 and slope > D("0.04")
    continuation = h_close[-1] >= ema20 and q_fast > q_slow
    pullback_recovery = (
        ZERO < pullback_depth <= MAX_HOURLY_PULLBACK_BELOW_EMA20_PCT
        and q_fast > q_slow
        and fifteen_recovery
    )
    trend_route = (
        TREND_ROUTE_CONTINUATION if continuation else
        TREND_ROUTE_PULLBACK_RECOVERY if pullback_recovery else None)

    evidence = {
        "hourly_ema_strength_pct": _text(strength),
        "hourly_ema_slope_pct": _text(slope),
        "hourly_realized_vol_pct": _text(volatility),
        "last_hour_abs_return_pct": _text(last_return),
        "range_width_24h_pct": _text(range_width),
        "fifteen_minute_confirmation": q_fast > q_slow,
        "fifteen_minute_recovery": fifteen_recovery,
        "hourly_pullback_below_ema20_pct": _text(pullback_depth),
        "trend_route": trend_route,
    }
    if last_return > spike_limit:
        return {"regime": REGIME_NO_TRADE,
                "reason_code": "REGIME_UNSTABLE", "evidence": evidence}
    # ADR-019: genel eşik gevşetmesi değildir. Uzun vadeli yükseliş yapısı
    # bozulmamışsa, EMA20'nin en fazla %0,60 altındaki sınırlı geri çekilme
    # yalnız 15m yön yeniden yukarı döndüğünde TREND olarak kalabilir. 5m
    # toparlanma teyidi ayrıca strategy_fit içinde zorunludur.
    if trend_structure and (continuation or pullback_recovery):
        return {"regime": REGIME_TREND, "reason_code": None,
                "evidence": evidence}
    if strength <= D("0.32") and range_width <= D("5.00"):
        return {"regime": REGIME_RANGE, "reason_code": None,
                "evidence": evidence}
    return {"regime": REGIME_NO_TRADE,
            "reason_code": "REGIME_UNSTABLE", "evidence": evidence}


def strategy_fit(candles: list[dict[str, Any]], regime: str) \
        -> dict[str, Any]:
    """5m yalnız giriş zamanlamasıdır; yönü tek başına belirlemez."""
    if len(candles) < 35:
        return {"fit": False, "strategy": None,
                "reason_code": "DATA_QUALITY", "evidence": {}}
    closes = [_decimal(c["close"]) for c in candles[-40:]]
    last, previous = closes[-1], closes[-2]
    move = (last / previous - ONE) * HUNDRED if previous else ZERO
    if regime == REGIME_TREND:
        ema9 = _ema(closes[-30:], 9)
        ema21 = _ema(closes[-30:], 21)
        distance = abs(last / ema21 - ONE) * HUNDRED if ema21 else D(999)
        fit = (ema9 >= ema21 and last >= ema21 * D("0.997")
               and last > previous and distance <= D("0.60")
               and move <= D("0.65"))
        return {"fit": fit, "strategy": STRATEGY_TREND,
                "reason_code": None if fit else "STRATEGY_NOT_CONFIRMED",
                "evidence": {"ema_distance_pct": _text(distance),
                             "last_move_pct": _text(move)}}
    if regime == REGIME_RANGE:
        base = closes[-22:-2]
        avg, std = _mean(base), _std(base)
        prior_z = (previous - avg) / std if std else ZERO
        fit = (std > ZERO and prior_z <= D("-0.85")
               and last > previous and last <= avg * D("1.002"))
        return {"fit": fit, "strategy": STRATEGY_RANGE,
                "reason_code": None if fit else "STRATEGY_NOT_CONFIRMED",
                "evidence": {"previous_z_score": _text(prior_z),
                             "last_move_pct": _text(move)}}
    return {"fit": False, "strategy": None,
            "reason_code": "REGIME_UNSTABLE", "evidence": {}}


def _barrier_outcome(candles: list[dict[str, Any]], index: int,
                     horizon: int, tp_pct: Decimal,
                     sl_pct: Decimal) -> dict[str, Any]:
    entry = candles[index]["close"]
    tp = entry * (ONE + tp_pct / HUNDRED)
    sl = entry * (ONE - sl_pct / HUNDRED)
    last_close = entry
    for future in candles[index + 1:index + horizon + 1]:
        last_close = future["close"]
        hit_sl = future["low"] <= sl
        hit_tp = future["high"] >= tp
        # Aynı mumda iki bariyer görülürse intrabar sıra bilinmez;
        # iyimserlik üretmemek için konservatif olarak SL yazılır.
        if hit_sl:
            return {"result": "SL", "gross_return_pct": -sl_pct}
        if hit_tp:
            return {"result": "TP", "gross_return_pct": tp_pct}
    gross = (last_close / entry - ONE) * HUNDRED if entry else ZERO
    return {"result": "TIME", "gross_return_pct": gross}


def calibrated_net_ev(outcomes: list[dict[str, Any]],
                      round_trip_cost_pct: Decimal | str | float,
                      confidence_z: Decimal | str | float = "1.645") -> dict:
    """Gözlenmiş sonuçlardan maliyet-sonrası EV ve güven alt sınırı.

    Ham ortalamanın sıfırın az üstünde olması yeterli değildir. Tek taraflı
    yaklaşık %95 güven alt sınırı (ortalama - z×standart hata), örneklem
    belirsizliğini giriş kararına taşır. Bu eşik özellikle maliyetlerin küçük
    brüt avantajı kolayca tükettiği kısa vadeli işlemleri fail-closed tutar.
    """
    cost = _decimal(round_trip_cost_pct)
    z_value = max(ZERO, _decimal(confidence_z))
    count = len(outcomes)
    if not count:
        return {"sample_size": 0, "probabilities": None,
                "expected_gross_return_pct": None, "net_ev_pct": None,
                "net_ev_standard_error_pct": None,
                "net_ev_lower_bound_pct": None}
    tp = sum(1 for o in outcomes if o["result"] == "TP")
    sl = sum(1 for o in outcomes if o["result"] == "SL")
    timed = count - tp - sl
    gross_values = [_decimal(o["gross_return_pct"]) for o in outcomes]
    gross = _mean(gross_values)
    net = gross - cost
    with localcontext() as ctx:
        ctx.prec = 28
        standard_error = (_std(gross_values) / D(count).sqrt()
                          if count > 1 else ZERO)
    lower_bound = net - z_value * standard_error
    return {
        "sample_size": count,
        "probabilities": {
            "tp": _text(D(tp) / D(count), "0.000001"),
            "sl": _text(D(sl) / D(count), "0.000001"),
            "time": _text(D(timed) / D(count), "0.000001"),
        },
        "expected_gross_return_pct": _text(gross),
        "round_trip_cost_pct": _text(cost),
        "net_ev_pct": _text(net),
        "net_ev_standard_error_pct": _text(standard_error),
        "net_ev_lower_bound_pct": _text(lower_bound),
        "net_ev_confidence_z": _text(z_value, "0.001"),
    }


def _prefix(bars: list[dict[str, Any]], closes: list[int],
            timestamp: int) -> list[dict[str, Any]]:
    return bars[:bisect_right(closes, timestamp)]


def _walk_forward(candles: list[dict[str, Any]],
                  hourly: list[dict[str, Any]],
                  fifteen: list[dict[str, Any]], regime: str,
                  horizon: int, tp_pct: Decimal,
                  sl_pct: Decimal, max_events: int = 80,
                  trend_route: str | None = None) -> list[dict]:
    outcomes: list[dict] = []
    h_close_ts = [int(c["close_ts"]) for c in hourly]
    q_close_ts = [int(c["close_ts"]) for c in fifteen]
    start = max(620, len(candles) - 650)
    stop = len(candles) - horizon - 1
    index = start
    while index < stop:
        ts = int(candles[index]["close_ts"])
        historical_regime = classify_regime(
            _prefix(hourly, h_close_ts, ts),
            _prefix(fifteen, q_close_ts, ts))
        historical_route = (historical_regime.get("evidence") or {}).get(
            "trend_route")
        if (historical_regime["regime"] == regime and
                (regime != REGIME_TREND or trend_route is None or
                 historical_route == trend_route)):
            fit = strategy_fit(candles[max(0, index - 45):index + 1],
                               regime)
            if fit["fit"]:
                outcomes.append(_barrier_outcome(
                    candles, index, horizon, tp_pct, sl_pct))
                index += horizon
                if len(outcomes) >= max_events:
                    break
        index += 1
    return outcomes


def _base_decision(symbol: str, model: str) -> dict[str, Any]:
    return {
        "symbol": symbol, "model": model, "eligible": False,
        "side": None, "profile": "ADR016_REGIME_NET_EV",
        "strategy_version": STRATEGY_VERSION,
        "strategy_candidate": "RECOVERY_FOCUSED",
        "decision_chain": list(DECISION_CHAIN),
        "timeframes": {"direction": "1h", "confirmation": "15m",
                       "entry_timing": "5m"},
        "regime": REGIME_NO_TRADE, "strategy": None,
        "reason_code": "DATA_QUALITY", "sample_size": 0,
        "probabilities": None, "expected_gross_return_pct": None,
        "round_trip_cost_pct": None, "net_ev_pct": None,
        "net_ev_standard_error_pct": None,
        "net_ev_lower_bound_pct": None,
        "rank_score": None,
    }


def evaluate_candidate(symbol: str, model: str, raw_klines_5m: list[list],
                       row: dict[str, Any], model_cfg: dict[str, Any],
                       *, min_samples: int = 20) -> dict[str, Any]:
    """Tek sembol için açıklanabilir ADR-016 Paper kararı üretir."""
    decision = _base_decision(symbol, model)
    try:
        candles = _parse_5m(raw_klines_5m)
        if len(candles) < 720:
            return decision
        as_of = int(candles[-1]["close_ts"])
        fifteen = [bar for bar in _aggregate(candles, 15)
                    if int(bar["close_ts"]) <= as_of]
        hourly = [bar for bar in _aggregate(candles, 60)
                  if int(bar["close_ts"]) <= as_of]
        regime = classify_regime(hourly, fifteen)
        decision["regime"] = regime["regime"]
        decision["regime_evidence"] = regime["evidence"]
        if regime["reason_code"]:
            decision["reason_code"] = regime["reason_code"]
            return decision
        fit = strategy_fit(candles, regime["regime"])
        decision["strategy"] = fit["strategy"]
        decision["strategy_evidence"] = fit["evidence"]
        if not fit["fit"]:
            decision["reason_code"] = fit["reason_code"]
            return decision

        tp_pct = _decimal(model_cfg.get("tp_pct"))
        sl_pct = _decimal(model_cfg.get("sl_pct"))
        horizon = max(1, int(model_cfg.get("max_hold_minutes", 15)) // 5)
        spread = max(ZERO, _decimal(row.get("spread_pct", 0)))
        fee_pct = _decimal(model_cfg.get("round_trip_fee_pct", "0.20"))
        slippage = spread * D("0.75")
        cost = fee_pct + slippage
        outcomes = _walk_forward(candles, hourly, fifteen,
                                 regime["regime"], horizon,
                                 tp_pct, sl_pct,
                                 trend_route=(regime.get("evidence") or {}).get(
                                     "trend_route"))
        calibration = calibrated_net_ev(
            outcomes, cost, model_cfg.get("net_ev_confidence_z", "1.645"))
        decision.update(calibration)
        if calibration["sample_size"] < int(min_samples):
            decision["reason_code"] = "INSUFFICIENT_CALIBRATION"
            return decision
        net_ev = _decimal(calibration["net_ev_pct"])
        if net_ev <= ZERO:
            decision["reason_code"] = "NET_EV_NON_POSITIVE"
            return decision
        lower_bound = _decimal(calibration["net_ev_lower_bound_pct"])
        if lower_bound <= ZERO:
            decision["reason_code"] = "NET_EV_CONFIDENCE_LOW"
            return decision
        decision.update({
            "eligible": True, "side": "LONG", "reason_code": None,
            "rank_score": _text(net_ev),
            "confidence": _text(
                _decimal(calibration["probabilities"]["tp"]) * HUNDRED,
                "0.01"),
            "last": _text(candles[-1]["close"], "0.00000001"),
            "entry_route": fit["strategy"],
        })
        return decision
    except (DecisionDataError, InvalidOperation, TypeError, ValueError,
            ZeroDivisionError):
        return decision


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """EV, örneklem ve sembol ile kararlı/deterministik sıralama."""
    def key(candidate: dict[str, Any]):
        score = _decimal(candidate.get("rank_score") or "-999999")
        sample = int(candidate.get("sample_size") or 0)
        return (-score, -sample, str(candidate.get("symbol") or ""),
                str(candidate.get("model") or ""))
    return sorted(candidates, key=key)
