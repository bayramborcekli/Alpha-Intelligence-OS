"""Mission 2100 — Agent 05: Gölge karşılaştırıcı testleri.

Fiyat/dolum/PnL deltaları, gecikme, değişmez rapor, sözleşme
doğrulama ve deterministik yeniden üretilebilirlik.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from execution_enums import OrderSide
from shadow_comparator import ShadowComparator
from shadow_errors import ShadowContractError
from shadow_models import (ShadowComparison, ShadowDecision,
                           ShadowExecution,
                           ShadowMarketObservation, ShadowOrder)

COMPARATOR = ShadowComparator()


def _order(reference="ord-1", symbol="BTCUSDT",
           side=OrderSide.BUY, quantity="2", price="100",
           sequence=5):
    return ShadowOrder(order_reference=reference, symbol=symbol,
                       side=side, quantity=D(quantity),
                       price=D(price),
                       logical_sequence=sequence)


def _execution(reference="exe-1", order_reference="ord-1",
               symbol="BTCUSDT", side=OrderSide.BUY,
               quantity="2", price="100", sequence=5):
    return ShadowExecution(
        execution_reference=reference,
        order_reference=order_reference, symbol=symbol,
        side=side, quantity=D(quantity), price=D(price),
        logical_sequence=sequence)


def _observation(reference="obs-1", symbol="BTCUSDT",
                 price=None, best_bid=None, best_ask=None,
                 last_trade_price=None, sequence=9):
    return ShadowMarketObservation(
        observation_reference=reference, symbol=symbol,
        price=None if price is None else D(price),
        best_bid=None if best_bid is None else D(best_bid),
        best_ask=None if best_ask is None else D(best_ask),
        last_trade_price=(None if last_trade_price is None
                          else D(last_trade_price)),
        logical_sequence=sequence)


def _compare(order=None, execution="default",
             observation=None, request_reference="req-1",
             market_reference="obs-1", sequence=9):
    if execution == "default":
        execution = _execution()
    return COMPARATOR.compare(
        order if order is not None else _order(),
        execution,
        observation if observation is not None
        else _observation(last_trade_price="101"),
        request_reference, market_reference,
        logical_sequence=sequence)


# ── Sözleşme doğrulaması ─────────────────────────────────────────────

class TestComparatorContract:
    @pytest.mark.parametrize("bad", [None, "x", 1, object(), ()])
    def test_bad_order_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            COMPARATOR.compare(bad, _execution(),
                               _observation(), "req-1", "obs-1")

    @pytest.mark.parametrize("bad", ["x", 1, object(), ()])
    def test_bad_execution_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            COMPARATOR.compare(_order(), bad, _observation(),
                               "req-1", "obs-1")

    @pytest.mark.parametrize("bad", [None, "x", 1, object(), ()])
    def test_bad_observation_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            COMPARATOR.compare(_order(), _execution(), bad,
                               "req-1", "obs-1")

    def test_symbol_mismatch_rejected(self):
        with pytest.raises(ShadowContractError) as exc:
            COMPARATOR.compare(
                _order(), _execution(),
                _observation(symbol="ETHUSDT"), "req-1",
                "obs-1")
        assert "INVALID_SHADOW_FIELD:observation" in \
            str(exc.value)

    def test_execution_order_mismatch_rejected(self):
        with pytest.raises(ShadowContractError):
            COMPARATOR.compare(
                _order(), _execution(order_reference="ord-999"),
                _observation(), "req-1", "obs-1")

    def test_execution_symbol_mismatch_rejected(self):
        with pytest.raises(ShadowContractError):
            COMPARATOR.compare(
                _order(), _execution(symbol="ETHUSDT"),
                _observation(), "req-1", "obs-1")

    def test_execution_side_mismatch_rejected(self):
        with pytest.raises(ShadowContractError):
            COMPARATOR.compare(
                _order(), _execution(side=OrderSide.SELL),
                _observation(), "req-1", "obs-1")

    @pytest.mark.parametrize("bad", [None, "", "  ", 5])
    def test_bad_request_reference_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _compare(request_reference=bad)

    @pytest.mark.parametrize("bad", [None, "", "  ", 5])
    def test_bad_market_reference_rejected(self, bad):
        with pytest.raises(ShadowContractError):
            _compare(market_reference=bad)

    def test_execution_optional(self):
        report = _compare(execution=None)
        assert isinstance(report, ShadowComparison)

    def test_error_message_sterile(self):
        with pytest.raises(ShadowContractError) as exc:
            COMPARATOR.compare(None, None, None, "r", "m")
        message = str(exc.value)
        assert message == "INVALID_SHADOW_FIELD:order"


# ── Fiyat deltası ────────────────────────────────────────────────────

class TestPriceDelta:
    @pytest.mark.parametrize("observed,expected_delta", [
        ("101", "1"), ("99", "-1"), ("100", "0"),
        ("100.5", "0.5"), ("250", "150")])
    def test_delta_from_trade_price(self, observed,
                                    expected_delta):
        report = _compare(observation=_observation(
            last_trade_price=observed))
        assert report.price_delta == D(expected_delta)

    def test_trade_price_preferred_over_price(self):
        report = _compare(observation=_observation(
            price="90", last_trade_price="110"))
        assert report.price_delta == D("10")

    def test_price_used_when_no_trade_price(self):
        report = _compare(observation=_observation(price="95"))
        assert report.price_delta == D("-5")

    def test_unknown_observed_price_is_none(self):
        report = _compare(observation=_observation(
            best_bid="99", best_ask="101"))
        assert report.price_delta is None

    def test_expected_price_from_execution(self):
        report = _compare(
            execution=_execution(price="102"),
            observation=_observation(last_trade_price="103"))
        assert report.price_delta == D("1")

    def test_expected_price_from_order_without_execution(self):
        report = _compare(
            execution=None,
            observation=_observation(last_trade_price="103"))
        assert report.price_delta == D("3")

    def test_delta_is_decimal(self):
        report = _compare()
        assert isinstance(report.price_delta, D)


# ── Dolum deltası ────────────────────────────────────────────────────

class TestFillDelta:
    def test_buy_fillable_when_price_crosses_ask(self):
        report = _compare(observation=_observation(
            best_ask="99", last_trade_price="99"))
        assert report.fill_delta == D("0")

    def test_buy_not_fillable_below_ask(self):
        report = _compare(observation=_observation(
            best_ask="101", last_trade_price="101"))
        assert report.fill_delta == D("2")

    def test_buy_unknown_ask_is_none(self):
        report = _compare(observation=_observation(
            best_bid="99", last_trade_price="100"))
        assert report.fill_delta is None

    def test_sell_fillable_when_price_at_bid(self):
        order = _order(side=OrderSide.SELL)
        execution = _execution(side=OrderSide.SELL)
        report = COMPARATOR.compare(
            order, execution,
            _observation(best_bid="100"), "req-1", "obs-1")
        assert report.fill_delta == D("0")

    def test_sell_not_fillable_above_bid(self):
        order = _order(side=OrderSide.SELL)
        execution = _execution(side=OrderSide.SELL)
        report = COMPARATOR.compare(
            order, execution,
            _observation(best_bid="99"), "req-1", "obs-1")
        assert report.fill_delta == D("2")

    def test_sell_unknown_bid_is_none(self):
        order = _order(side=OrderSide.SELL)
        report = COMPARATOR.compare(
            order, _execution(side=OrderSide.SELL),
            _observation(best_ask="101"), "req-1", "obs-1")
        assert report.fill_delta is None

    def test_no_execution_expected_zero(self):
        report = _compare(
            execution=None,
            observation=_observation(best_ask="99"))
        assert report.fill_delta == D("-2")

    def test_exact_price_boundary_buy(self):
        report = _compare(observation=_observation(
            best_ask="100"))
        assert report.fill_delta == D("0")


# ── PnL deltası ──────────────────────────────────────────────────────

class TestPnlDelta:
    @pytest.mark.parametrize("observed,delta", [
        ("101", "2"), ("99", "-2"), ("100", "0"),
        ("103.5", "7.0")])
    def test_buy_pnl_delta(self, observed, delta):
        report = _compare(observation=_observation(
            last_trade_price=observed))
        assert report.pnl_delta == D(delta)

    @pytest.mark.parametrize("observed,delta", [
        ("101", "-2"), ("99", "2"), ("100", "0")])
    def test_sell_pnl_delta_sign_inverted(self, observed,
                                          delta):
        report = COMPARATOR.compare(
            _order(side=OrderSide.SELL),
            _execution(side=OrderSide.SELL),
            _observation(last_trade_price=observed),
            "req-1", "obs-1")
        assert report.pnl_delta == D(delta)

    def test_unknown_price_pnl_none(self):
        report = _compare(observation=_observation(
            best_bid="99"))
        assert report.pnl_delta is None

    def test_pnl_uses_execution_price(self):
        report = _compare(
            execution=_execution(price="98"),
            observation=_observation(last_trade_price="100"))
        assert report.pnl_delta == D("4")

    def test_pnl_decimal_exact(self):
        report = _compare(
            order=_order(quantity="0.3", price="100"),
            execution=_execution(quantity="0.3", price="100"),
            observation=_observation(last_trade_price="100.1"))
        assert report.pnl_delta == D("0.1") * D("0.3")


# ── Gecikme gözlemi ──────────────────────────────────────────────────

class TestLatency:
    @pytest.mark.parametrize("order_seq,obs_seq,latency", [
        (5, 9, 4), (5, 5, 0), (0, 100, 100), (7, 8, 1)])
    def test_latency_is_logical_difference(self, order_seq,
                                           obs_seq, latency):
        report = COMPARATOR.compare(
            _order(sequence=order_seq),
            _execution(sequence=order_seq),
            _observation(sequence=obs_seq,
                         last_trade_price="101"),
            "req-1", "obs-1")
        assert report.latency == latency

    def test_negative_difference_is_none(self):
        report = COMPARATOR.compare(
            _order(sequence=9), _execution(sequence=9),
            _observation(sequence=5), "req-1", "obs-1")
        assert report.latency is None

    def test_latency_is_int(self):
        report = _compare()
        assert isinstance(report.latency, int)


# ── Rapor içeriği ve değişmezlik ─────────────────────────────────────

class TestReport:
    def test_references_carried(self):
        report = _compare(request_reference="req-77",
                          market_reference="obs-77")
        assert report.request_reference == "req-77"
        assert report.paper_reference == "ord-1"
        assert report.market_reference == "obs-77"

    def test_logical_sequence_carried(self):
        report = _compare(sequence=42)
        assert report.logical_sequence == 42

    def test_decision_is_simulated(self):
        report = _compare()
        assert report.decision is ShadowDecision.SIMULATED

    def test_report_immutable(self):
        report = _compare()
        with pytest.raises(Exception):
            report.price_delta = D("0")

    def test_report_no_dict(self):
        assert not hasattr(_compare(), "__dict__")

    def test_no_generated_identifiers(self):
        report = _compare()
        assert report.request_reference == "req-1"
        assert report.market_reference == "obs-1"
        assert report.paper_reference == "ord-1"

    def test_deterministic_repeat(self):
        assert _compare() == _compare()

    def test_audit_default_empty(self):
        assert _compare().audit == ()

    def test_comparator_stateless(self):
        first = _compare(observation=_observation(
            last_trade_price="150"))
        second = _compare(observation=_observation(
            last_trade_price="101"))
        assert first.price_delta == D("50")
        assert second.price_delta == D("1")

    def test_comparator_frozen(self):
        with pytest.raises(Exception):
            COMPARATOR.extra = 1

    def test_no_score_manipulation_fields(self):
        names = ShadowComparison.__dataclass_fields__.keys()
        assert "score" not in names
        assert "optimized" not in names
