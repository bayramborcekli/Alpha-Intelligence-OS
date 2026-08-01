"""ADR-016 saf karar motoru kabul testleri."""

from decimal import Decimal

import paper_decision_engine as de


def _bar(ts, close, *, spread="0.001", volume="1000"):
    price = Decimal(str(close))
    pad = price * Decimal(spread)
    return [ts, str(price - pad / 2), str(price + pad),
            str(price - pad), str(price), volume, ts + 300_000 - 1]


def _trend_5m(count=800):
    price = Decimal("100")
    out = []
    for index in range(count):
        price *= Decimal("1.0002")
        out.append(_bar(index * 300_000, price))
    return out


def _walk_forward_positive_5m(count=1000):
    # Yavaş ana trend + kısa geri çekilme + doğrulanmış toparlanma.
    # Sonuçlar modülün gerçek walk-forward bariyer hesaplarından gelir.
    pattern = ([Decimal("1.0002")] * 6 +
               [Decimal("0.9997")] * 2 +
               [Decimal("1.0004")] +
               [Decimal("1.0018")] * 3)
    price = Decimal("100")
    out = []
    for index in range(count):
        price *= pattern[index % len(pattern)]
        out.append(_bar(index * 300_000, price, spread="0.0008"))
    return out


def _robust_positive_5m(count=991):
    """Güçlü geçmiş avantajı olan, son teyide kadar trend adayı."""
    pattern = ([Decimal("1.0016")] * 4 + [Decimal("0.9970")])
    price = Decimal("100")
    out = []
    for index in range(count):
        price *= pattern[index % len(pattern)]
        out.append(_bar(index * 300_000, price, spread="0.0008"))
    return out


def _agg_bars(count, step, *, jump_last=False):
    price = Decimal("100")
    out = []
    for index in range(count):
        price *= Decimal(str(step))
        if jump_last and index == count - 1:
            price *= Decimal("1.05")
        out.append({"close": str(price), "high": str(price * D("1.001")),
                    "low": str(price * D("0.999"))})
    return out


D = Decimal


def test_aggregates_real_5m_bars_to_15m_and_1h():
    raw = [_bar(i * 300_000, 100 + i) for i in range(12)]
    fifteen = de.aggregate_klines(raw, 15)
    hourly = de.aggregate_klines(raw, 60)
    assert len(fifteen) == 4
    assert len(hourly) == 1
    assert Decimal(fifteen[0]["open"]) == Decimal(raw[0][1])
    assert fifteen[0]["close"] == "102.0000"
    assert hourly[0]["close"] == "111.0000"


def test_classifies_confirmed_bull_trend():
    hourly = _agg_bars(60, "1.001")
    fifteen = _agg_bars(100, "1.0003")
    result = de.classify_regime(hourly, fifteen)
    assert result["regime"] == de.REGIME_TREND
    assert result["reason_code"] is None


def test_controlled_hourly_pullback_recovery_stays_in_trend():
    """ADR-019: yapı bozulmadıysa sınırlı dip + 15m toparlanma trenddir."""
    hourly = _agg_bars(60, "1.001")
    ema20 = de._ema([Decimal(row["close"]) for row in hourly], 20)
    recovered = ema20 * Decimal("0.9975")
    hourly[-1]["close"] = str(recovered)
    hourly[-1]["high"] = str(recovered * D("1.001"))
    hourly[-1]["low"] = str(recovered * D("0.999"))
    fifteen = _agg_bars(100, "1.0003")
    fifteen[-1]["close"] = str(Decimal(fifteen[-2]["close"]) * D("1.001"))

    result = de.classify_regime(hourly, fifteen)

    assert result["regime"] == de.REGIME_TREND
    assert result["reason_code"] is None
    assert result["evidence"]["trend_route"] == \
        de.TREND_ROUTE_PULLBACK_RECOVERY
    depth = D(result["evidence"]["hourly_pullback_below_ema20_pct"])
    assert D("0") < depth <= de.MAX_HOURLY_PULLBACK_BELOW_EMA20_PCT


def test_pullback_without_fifteen_minute_recovery_remains_no_trade():
    hourly = _agg_bars(60, "1.001")
    ema20 = de._ema([Decimal(row["close"]) for row in hourly], 20)
    pulled = ema20 * Decimal("0.9975")
    hourly[-1]["close"] = str(pulled)
    hourly[-1]["high"] = str(pulled * D("1.001"))
    hourly[-1]["low"] = str(pulled * D("0.999"))
    fifteen = _agg_bars(100, "1.0003")
    fifteen[-1]["close"] = str(Decimal(fifteen[-2]["close"]) * D("0.999"))

    result = de.classify_regime(hourly, fifteen)

    assert result["regime"] == de.REGIME_NO_TRADE
    assert result["reason_code"] == "REGIME_UNSTABLE"


def test_decision_is_versioned_for_clean_evidence_cohort():
    decision = de._base_decision("BTCUSDT", "ALPHA_CORE_SCALP")
    assert decision["strategy_version"] == "RECOVERY_FOCUSED_V1"
    assert decision["strategy_candidate"] == "RECOVERY_FOCUSED"


def test_volatility_spike_is_no_trade():
    hourly = _agg_bars(60, "1.001", jump_last=True)
    fifteen = _agg_bars(100, "1.0003")
    result = de.classify_regime(hourly, fifteen)
    assert result["regime"] == de.REGIME_NO_TRADE
    assert result["reason_code"] == "REGIME_UNSTABLE"


def test_trend_strategy_uses_5m_only_for_timing():
    candles = de._parse_5m(_trend_5m(45))
    result = de.strategy_fit(candles, de.REGIME_TREND)
    assert result["fit"] is True
    assert result["strategy"] == de.STRATEGY_TREND


def test_calibration_uses_observed_probabilities_and_costs():
    outcomes = (
        [{"result": "TP", "gross_return_pct": "0.45"}] * 30 +
        [{"result": "SL", "gross_return_pct": "-0.30"}] * 5 +
        [{"result": "TIME", "gross_return_pct": "0.10"}] * 5)
    result = de.calibrated_net_ev(outcomes, "0.22")
    assert result["sample_size"] == 40
    assert result["probabilities"] == {
        "tp": "0.750000", "sl": "0.125000", "time": "0.125000"}
    assert Decimal(result["net_ev_pct"]) > 0
    assert Decimal(result["net_ev_lower_bound_pct"]) > 0


def test_low_sample_is_fail_closed(monkeypatch):
    monkeypatch.setattr(de, "_walk_forward", lambda *a, **k: [
        {"result": "TP", "gross_return_pct": "0.45"}] * 10)
    result = de.evaluate_candidate(
        "BTCUSDT", "ALPHA_CORE_SCALP", _trend_5m(),
        {"spread_pct": "0.02"},
        {"tp_pct": "0.45", "sl_pct": "0.30",
         "max_hold_minutes": 15}, min_samples=20)
    assert result["eligible"] is False
    assert result["reason_code"] == "INSUFFICIENT_CALIBRATION"
    assert result["sample_size"] == 10


def test_positive_calibrated_net_ev_becomes_eligible(monkeypatch):
    outcomes = (
        [{"result": "TP", "gross_return_pct": "0.45"}] * 30 +
        [{"result": "SL", "gross_return_pct": "-0.30"}] * 5 +
        [{"result": "TIME", "gross_return_pct": "0.10"}] * 5)
    monkeypatch.setattr(de, "_walk_forward", lambda *a, **k: outcomes)
    result = de.evaluate_candidate(
        "BTCUSDT", "ALPHA_CORE_SCALP", _trend_5m(),
        {"spread_pct": "0.02"},
        {"tp_pct": "0.45", "sl_pct": "0.30",
         "max_hold_minutes": 15}, min_samples=20)
    assert result["eligible"] is True
    assert result["regime"] == de.REGIME_TREND
    assert result["strategy"] == de.STRATEGY_TREND
    assert Decimal(result["net_ev_pct"]) > 0
    assert result["decision_chain"] == list(de.DECISION_CHAIN)


def test_small_positive_walk_forward_edge_is_rejected_without_confidence():
    result = de.evaluate_candidate(
        "BTCUSDT", "ALPHA_CORE_SCALP", _walk_forward_positive_5m(),
        {"spread_pct": "0.02"},
        {"tp_pct": "0.45", "sl_pct": "0.30",
         "max_hold_minutes": 15}, min_samples=20)
    assert result["eligible"] is False
    assert result["sample_size"] >= 20
    assert Decimal(result["net_ev_pct"]) > 0
    assert Decimal(result["net_ev_lower_bound_pct"]) <= 0
    assert result["reason_code"] == "NET_EV_CONFIDENCE_LOW"


def test_robust_positive_calibration_becomes_ranked_candidate(monkeypatch):
    outcomes = ([{"result": "TP", "gross_return_pct": "0.45"}] * 38 +
                [{"result": "SL", "gross_return_pct": "-0.30"}] * 2)
    monkeypatch.setattr(de, "_walk_forward", lambda *a, **k: outcomes)
    result = de.evaluate_candidate(
        "BTCUSDT", "ALPHA_CORE_SCALP", _trend_5m(),
        {"spread_pct": "0.02"},
        {"tp_pct": "0.45", "sl_pct": "0.30",
         "max_hold_minutes": 15}, min_samples=20)
    assert result["eligible"] is True
    assert Decimal(result["net_ev_lower_bound_pct"]) > 0


def test_false_breakout_blocks_the_next_five_minute_entry():
    """İlk trend teyidi bozulursa sonraki stagger slotu yeni alım yapmaz."""
    history = _robust_positive_5m()
    model_cfg = {
        "tp_pct": "0.45", "sl_pct": "0.30",
        "max_hold_minutes": 15, "round_trip_fee_pct": "0.20",
        "net_ev_confidence_z": "1.645",
    }
    before = de.evaluate_candidate(
        "ALTUSDT", "ALPHA_CORE_SCALP", history,
        {"spread_pct": "0.02"}, model_cfg, min_samples=20)
    assert before["eligible"] is True

    last = Decimal(history[-1][4]) * Decimal("0.9970")
    history.append(_bar(len(history) * 300_000, last,
                        spread="0.0008"))
    after = de.evaluate_candidate(
        "ALTUSDT", "ALPHA_CORE_SCALP", history,
        {"spread_pct": "0.02"}, model_cfg, min_samples=20)
    assert after["eligible"] is False
    assert after["reason_code"] == "STRATEGY_NOT_CONFIRMED"


def test_non_positive_net_ev_is_rejected(monkeypatch):
    outcomes = (
        [{"result": "TP", "gross_return_pct": "0.45"}] * 5 +
        [{"result": "SL", "gross_return_pct": "-0.30"}] * 30)
    monkeypatch.setattr(de, "_walk_forward", lambda *a, **k: outcomes)
    result = de.evaluate_candidate(
        "BTCUSDT", "ALPHA_CORE_SCALP", _trend_5m(),
        {"spread_pct": "0.02"},
        {"tp_pct": "0.45", "sl_pct": "0.30",
         "max_hold_minutes": 15}, min_samples=20)
    assert result["eligible"] is False
    assert result["reason_code"] == "NET_EV_NON_POSITIVE"


def test_candidate_ranking_is_ev_first_then_sample_then_symbol():
    ranked = de.rank_candidates([
        {"symbol": "ZUSDT", "model": "M", "rank_score": "0.10",
         "sample_size": 30},
        {"symbol": "AUSDT", "model": "M", "rank_score": "0.20",
         "sample_size": 20},
        {"symbol": "BUSDT", "model": "M", "rank_score": "0.20",
         "sample_size": 40},
    ])
    assert [row["symbol"] for row in ranked] == [
        "BUSDT", "AUSDT", "ZUSDT"]
