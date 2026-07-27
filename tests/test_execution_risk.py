"""Mission 2000 — Agent 03 Risk Engine testleri.

Değişmez modeller, deterministik kararlar, Decimal-only politikası,
maruziyet/sermaye/broker yetenek/enstrüman/günlük zarar/boyutlama
doğrulamaları, portföy toplaması, karar tutarlılığı, kamu API, yasak
importlar/yetenekler, broker-özgü ve sembol-özgü kod yokluğu, gizli
yürütme yokluğu doğrulanır.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from decimal import Decimal

import pytest

import execution_risk_engine
import execution_risk_models
import execution_risk_policies
from execution_enums import (
    OrderSide, OrderType, PositionSide, TimeInForce)
from execution_models import ExecutionRequest, Position
from execution_risk_engine import (
    RiskEngine, calculate_exposure, calculate_position_size,
    evaluate_portfolio_risk, validate_execution)
from execution_risk_models import (
    AssetType, BrokerProfile, CapitalState, Exposure, Instrument,
    Portfolio, PortfolioRisk, PositionRisk, RiskDecision,
    RiskDecisionType, RiskLimits)

D = Decimal


def _capital(**overrides):
    base = dict(total_capital=D("100000"),
                available_capital=D("100000"))
    base.update(overrides)
    return CapitalState(**base)


def _portfolio(positions=(), **capital_overrides):
    return Portfolio(capital=_capital(**capital_overrides),
                     positions=tuple(positions))


def _instrument(**overrides):
    base = dict(symbol="SYM-1", asset_type=AssetType.CRYPTO,
                currency="USDT", quote_currency="USDT",
                tick_size=D("0.01"), step_size=D("0.001"))
    base.update(overrides)
    return Instrument(**base)


def _broker(**overrides):
    base = dict(supports_market_orders=True, supports_short=True,
                supports_fractional=True, supports_cancel=True)
    base.update(overrides)
    return BrokerProfile(**base)


def _request(**overrides):
    base = dict(symbol="SYM-1", side=OrderSide.BUY,
                order_type=OrderType.LIMIT, quantity=D("1"),
                time_in_force=TimeInForce.GTC, price=D("100"))
    base.update(overrides)
    return ExecutionRequest(**base)


def _position(**overrides):
    base = dict(symbol="SYM-1", side=PositionSide.LONG,
                quantity=D("2"))
    base.update(overrides)
    return Position(**base)


def _decide(**overrides):
    args = dict(request=_request(), portfolio=_portfolio(),
                instrument=_instrument(), broker_profile=_broker(),
                limits=RiskLimits())
    args.update(overrides)
    return validate_execution(**args)


RISK_MODELS = (BrokerProfile, Instrument, CapitalState, Portfolio,
               RiskLimits, Exposure, PositionRisk, PortfolioRisk,
               RiskDecision)

SAMPLES = {
    BrokerProfile: _broker,
    Instrument: _instrument,
    CapitalState: _capital,
    Portfolio: _portfolio,
    RiskLimits: lambda: RiskLimits(max_exposure=D("10")),
    Exposure: lambda: Exposure(symbol="S", quantity=D("1"),
                               notional=D("2")),
    PositionRisk: lambda: PositionRisk(
        symbol="S", side=PositionSide.LONG, quantity=D("1")),
    PortfolioRisk: lambda: PortfolioRisk(total_notional=D("1")),
    RiskDecision: lambda: RiskDecision(
        decision=RiskDecisionType.ALLOW),
}


# ── Değişmez modeller ────────────────────────────────────────────────

class TestImmutableModels:
    @pytest.mark.parametrize("model", RISK_MODELS)
    def test_frozen(self, model):
        instance = SAMPLES[model]()
        field = dataclasses.fields(model)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, field, None)

    @pytest.mark.parametrize("model", RISK_MODELS)
    def test_slots(self, model):
        instance = SAMPLES[model]()
        assert not hasattr(instance, "__dict__")

    @pytest.mark.parametrize("model", RISK_MODELS)
    def test_hashable_and_equal_by_value(self, model):
        assert isinstance(hash(SAMPLES[model]()), int)
        assert SAMPLES[model]() == SAMPLES[model]()

    @pytest.mark.parametrize("model", RISK_MODELS)
    def test_no_mutable_defaults(self, model):
        for field in dataclasses.fields(model):
            if field.default is not dataclasses.MISSING:
                assert not isinstance(field.default,
                                      (list, dict, set))
            assert field.default_factory is dataclasses.MISSING

    def test_enums_closed(self):
        assert tuple(m.name for m in RiskDecisionType) == (
            "ALLOW", "REJECT", "REDUCE_SIZE",
            "REQUIRE_CONFIRMATION")
        assert tuple(m.name for m in AssetType) == (
            "CRYPTO", "EQUITY", "ETF", "FOREX", "FUTURES", "OPTIONS")

    def test_broker_profile_capabilities_closed(self):
        names = tuple(f.name for f in dataclasses.fields(
            BrokerProfile))
        assert names == (
            "supports_margin", "supports_short",
            "supports_fractional", "supports_options",
            "supports_market_orders", "supports_after_hours",
            "supports_modify", "supports_cancel",
            "supports_trailing_stop", "supports_oco")
        assert all(n.startswith("supports_") for n in names)

    def test_instrument_fields_closed(self):
        assert tuple(f.name for f in dataclasses.fields(
            Instrument)) == (
            "symbol", "asset_type", "currency", "quote_currency",
            "tick_size", "step_size", "price_precision",
            "quantity_precision", "broker_symbol")


# ── Decimal-only ─────────────────────────────────────────────────────

class TestDecimalOnly:
    @pytest.mark.parametrize("bad", [0.5, 1, True, "1"])
    def test_capital_rejects_non_decimal(self, bad):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            CapitalState(total_capital=bad,
                         available_capital=D("1"))

    @pytest.mark.parametrize("bad", [0.5, 2, True])
    def test_limits_reject_non_decimal(self, bad):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            RiskLimits(max_exposure=bad)

    @pytest.mark.parametrize("bad", [0.5, 1, True])
    def test_exposure_rejects_non_decimal(self, bad):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            Exposure(symbol="S", quantity=bad)

    def test_broker_profile_rejects_non_bool(self):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            BrokerProfile(supports_short=1)

    def test_instrument_precision_rejects_bool(self):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            _instrument(price_precision=True)

    def test_no_float_literals_in_risk_modules(self):
        for module in (execution_risk_models,
                       execution_risk_policies,
                       execution_risk_engine):
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node, ast.Constant):
                    assert not isinstance(node.value, float)


# ── Doğrulama hattı: sermaye ─────────────────────────────────────────

class TestCapitalValidation:
    def test_insufficient_capital_rejected(self):
        decision = _decide(request=_request(quantity=D("2000"),
                                            price=D("100")))
        assert decision.decision is RiskDecisionType.REJECT
        assert decision.code == "INSUFFICIENT_CAPITAL"

    def test_exact_capital_allowed(self):
        decision = _decide(request=_request(quantity=D("1000"),
                                            price=D("100")))
        assert decision.decision is RiskDecisionType.ALLOW

    def test_unknown_price_requires_confirmation(self):
        decision = _decide(request=_request(
            order_type=OrderType.MARKET, price=None))
        assert decision.decision is \
            RiskDecisionType.REQUIRE_CONFIRMATION
        assert decision.code == "UNKNOWN_NOTIONAL"

    def test_sell_not_capital_bound(self):
        decision = _decide(request=_request(side=OrderSide.SELL,
                                            quantity=D("1")))
        assert decision.decision is RiskDecisionType.ALLOW


# ── Doğrulama hattı: broker yetenekleri ──────────────────────────────

class TestBrokerCapabilities:
    def test_market_not_supported_rejected(self):
        decision = _decide(
            request=_request(order_type=OrderType.MARKET),
            broker_profile=_broker(supports_market_orders=False))
        assert decision.decision is RiskDecisionType.REJECT
        assert decision.code == "MARKET_NOT_SUPPORTED"

    def test_short_not_supported_rejected(self):
        decision = _decide(
            request=_request(side=OrderSide.SELL, quantity=D("5")),
            broker_profile=_broker(supports_short=False))
        assert decision.decision is RiskDecisionType.REJECT
        assert decision.code == "SHORT_NOT_SUPPORTED"

    def test_sell_within_long_position_not_short(self):
        decision = _decide(
            request=_request(side=OrderSide.SELL, quantity=D("2")),
            portfolio=_portfolio(positions=[_position()]),
            broker_profile=_broker(supports_short=False))
        assert decision.decision is RiskDecisionType.ALLOW

    def test_fractional_not_supported_rejected(self):
        decision = _decide(
            request=_request(quantity=D("0.5")),
            instrument=_instrument(step_size=None),
            broker_profile=_broker(supports_fractional=False))
        assert decision.decision is RiskDecisionType.REJECT
        assert decision.code == "FRACTIONAL_NOT_SUPPORTED"

    def test_whole_quantity_allowed_without_fractional(self):
        decision = _decide(
            request=_request(quantity=D("2")),
            instrument=_instrument(step_size=None),
            broker_profile=_broker(supports_fractional=False))
        assert decision.decision is RiskDecisionType.ALLOW


# ── Doğrulama hattı: enstrüman ───────────────────────────────────────

class TestInstrumentValidation:
    def test_symbol_mismatch_rejected(self):
        decision = _decide(instrument=_instrument(symbol="OTHER"))
        assert decision.decision is RiskDecisionType.REJECT
        assert decision.code == "INSTRUMENT_MISMATCH"

    def test_step_size_violation_rejected(self):
        decision = _decide(request=_request(quantity=D("0.0005")))
        assert decision.decision is RiskDecisionType.REJECT
        assert decision.code == "STEP_SIZE_VIOLATION"

    def test_tick_size_violation_rejected(self):
        decision = _decide(request=_request(price=D("100.005")))
        assert decision.decision is RiskDecisionType.REJECT
        assert decision.code == "TICK_SIZE_VIOLATION"

    def test_aligned_order_allowed(self):
        decision = _decide(request=_request(quantity=D("0.5"),
                                            price=D("99.99")))
        assert decision.decision is RiskDecisionType.ALLOW

    def test_null_sizes_skip_alignment(self):
        decision = _decide(instrument=_instrument(
            tick_size=None, step_size=None))
        assert decision.decision is RiskDecisionType.ALLOW


# ── Doğrulama hattı: maruziyet ───────────────────────────────────────

class TestExposureValidation:
    def test_within_limit_allowed(self):
        decision = _decide(limits=RiskLimits(max_exposure=D("500")))
        assert decision.decision is RiskDecisionType.ALLOW

    def test_exceeded_reduces_size(self):
        decision = _decide(
            request=_request(quantity=D("10"), price=D("100")),
            limits=RiskLimits(max_exposure=D("500")))
        assert decision.decision is RiskDecisionType.REDUCE_SIZE
        assert decision.code == "EXPOSURE_EXCEEDED"
        assert decision.approved_quantity == D("5.000")

    def test_existing_position_counts(self):
        decision = _decide(
            request=_request(quantity=D("3"), price=D("100")),
            portfolio=_portfolio(positions=[_position()]),
            limits=RiskLimits(max_exposure=D("400")))
        assert decision.decision is RiskDecisionType.REDUCE_SIZE
        assert decision.approved_quantity == D("2.000")

    def test_no_headroom_rejected(self):
        decision = _decide(
            request=_request(quantity=D("1"), price=D("100")),
            portfolio=_portfolio(positions=[_position(
                quantity=D("10"))]),
            limits=RiskLimits(max_exposure=D("500")))
        assert decision.decision is RiskDecisionType.REJECT
        assert decision.code == "EXPOSURE_EXCEEDED"

    def test_sell_closing_long_reduces_exposure_allowed(self):
        # Long kapatan SELL net maruziyeti DÜŞÜRÜR → reddedilmez
        decision = _decide(
            request=_request(side=OrderSide.SELL, quantity=D("2"),
                             price=D("100")),
            portfolio=_portfolio(positions=[_position(
                quantity=D("5"))]),
            limits=RiskLimits(max_exposure=D("500")))
        assert decision.decision is RiskDecisionType.ALLOW

    def test_sell_beyond_limit_short_reduced(self):
        # Düz portföyde büyük SELL → |net| limiti aşar → REDUCE_SIZE
        decision = _decide(
            request=_request(side=OrderSide.SELL,
                             quantity=D("10"), price=D("100")),
            limits=RiskLimits(max_exposure=D("500")))
        assert decision.decision is RiskDecisionType.REDUCE_SIZE
        assert decision.approved_quantity == D("5.000")

    def test_short_position_counts_negative(self):
        # Mevcut SHORT poz. + BUY → net maruziyet azalır → ALLOW
        decision = _decide(
            request=_request(quantity=D("3"), price=D("100")),
            portfolio=_portfolio(positions=[_position(
                side=PositionSide.SHORT, quantity=D("10"))]),
            limits=RiskLimits(max_exposure=D("500")))
        assert decision.decision is RiskDecisionType.ALLOW

    def test_reduced_quantity_respects_step(self):
        decision = _decide(
            request=_request(quantity=D("10"), price=D("3")),
            limits=RiskLimits(max_exposure=D("10")))
        assert decision.decision is RiskDecisionType.REDUCE_SIZE
        assert decision.approved_quantity == D("3.333")


# ── Doğrulama hattı: günlük zarar ────────────────────────────────────

class TestDailyLoss:
    def test_exceeded_rejected(self):
        decision = _decide(
            portfolio=_portfolio(daily_realized_pnl=D("-600")),
            limits=RiskLimits(max_daily_loss=D("500")))
        assert decision.decision is RiskDecisionType.REJECT
        assert decision.code == "DAILY_LOSS_EXCEEDED"

    def test_exact_limit_rejected(self):
        decision = _decide(
            portfolio=_portfolio(daily_realized_pnl=D("-500")),
            limits=RiskLimits(max_daily_loss=D("500")))
        assert decision.decision is RiskDecisionType.REJECT

    def test_within_limit_allowed(self):
        decision = _decide(
            portfolio=_portfolio(daily_realized_pnl=D("-499")),
            limits=RiskLimits(max_daily_loss=D("500")))
        assert decision.decision is RiskDecisionType.ALLOW

    def test_profit_never_rejected(self):
        decision = _decide(
            portfolio=_portfolio(daily_realized_pnl=D("600")),
            limits=RiskLimits(max_daily_loss=D("500")))
        assert decision.decision is RiskDecisionType.ALLOW

    def test_unknown_pnl_skips_rule(self):
        decision = _decide(
            limits=RiskLimits(max_daily_loss=D("500")))
        assert decision.decision is RiskDecisionType.ALLOW


# ── Doğrulama hattı: pozisyon boyutlama ──────────────────────────────

class TestPositionSizing:
    def test_over_max_size_reduced(self):
        decision = _decide(
            request=_request(quantity=D("10")),
            limits=RiskLimits(max_position_size=D("4")))
        assert decision.decision is RiskDecisionType.REDUCE_SIZE
        assert decision.code == "MAX_POSITION_SIZE"
        assert decision.approved_quantity == D("4")

    def test_at_max_size_allowed(self):
        decision = _decide(
            request=_request(quantity=D("4")),
            limits=RiskLimits(max_position_size=D("4")))
        assert decision.decision is RiskDecisionType.ALLOW
        assert decision.approved_quantity == D("4")

    def test_calculate_position_size(self):
        assert calculate_position_size(
            D("1000"), D("3"), D("0.001")) == D("333.333")

    def test_calculate_position_size_whole_step(self):
        assert calculate_position_size(D("1000"), D("3"),
                                       D("1")) == D("333")

    def test_calculate_position_size_null_on_zero_price(self):
        assert calculate_position_size(D("1000"), D("0")) is None

    def test_calculate_position_size_null_on_negative(self):
        assert calculate_position_size(D("-1"), D("3")) is None

    @pytest.mark.parametrize("bad", [0.5, 1, True, "1", None])
    def test_calculate_position_size_sterile(self, bad):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            calculate_position_size(bad, D("3"))


# ── Maruziyet hesabı ve portföy toplaması ────────────────────────────

class TestExposureAndPortfolio:
    def test_calculate_exposure(self):
        exposure = calculate_exposure(_position(), D("100"))
        assert exposure == Exposure(symbol="SYM-1",
                                    quantity=D("2"),
                                    notional=D("200"))

    def test_calculate_exposure_unknown_price_null(self):
        assert calculate_exposure(_position()).notional is None

    def test_calculate_exposure_sterile(self):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            calculate_exposure("position", D("1"))
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            calculate_exposure(_position(), 100)

    def test_portfolio_aggregation(self):
        portfolio = _portfolio(positions=[
            _position(), _position(symbol="SYM-2",
                                   quantity=D("1"))])
        risk = evaluate_portfolio_risk(portfolio, {
            "SYM-1": D("100"), "SYM-2": D("50")})
        assert risk.total_notional == D("250")
        assert risk.exposure_ratio == D("0.0025")
        assert len(risk.position_risks) == 2

    def test_unknown_price_nulls_total(self):
        portfolio = _portfolio(positions=[
            _position(), _position(symbol="SYM-2",
                                   quantity=D("1"))])
        risk = evaluate_portfolio_risk(portfolio,
                                       {"SYM-1": D("100")})
        assert risk.total_notional is None
        assert risk.exposure_ratio is None
        assert risk.position_risks[0].notional == D("200")
        assert risk.position_risks[1].notional is None

    def test_empty_portfolio_zero_total(self):
        risk = evaluate_portfolio_risk(_portfolio())
        assert risk.total_notional == D("0")
        assert risk.position_risks == ()

    def test_portfolio_sterile_input(self):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            evaluate_portfolio_risk("portfolio")
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            evaluate_portfolio_risk(
                _portfolio(positions=[_position()]),
                {"SYM-1": 100.0})


# ── Karar tutarlılığı ve determinizm ─────────────────────────────────

class TestDeterminism:
    def test_repeated_identical_decisions(self):
        for _ in range(5):
            assert _decide() == _decide()

    def test_all_paths_deterministic(self):
        scenarios = [
            dict(request=_request(quantity=D("2000"))),
            dict(request=_request(price=None,
                                  order_type=OrderType.MARKET)),
            dict(broker_profile=_broker(
                supports_market_orders=False),
                request=_request(order_type=OrderType.MARKET)),
            dict(instrument=_instrument(symbol="OTHER")),
            dict(limits=RiskLimits(max_exposure=D("50"))),
            dict(portfolio=_portfolio(
                daily_realized_pnl=D("-600")),
                limits=RiskLimits(max_daily_loss=D("500"))),
            dict(limits=RiskLimits(max_position_size=D("0.5"))),
            dict(),
        ]
        for scenario in scenarios:
            assert _decide(**scenario) == _decide(**scenario)

    def test_pipeline_order_capital_before_capability(self):
        # Sermaye (2) yetenekten (3) önce: her ikisi ihlalde sermaye
        decision = _decide(
            request=_request(quantity=D("2000"),
                             order_type=OrderType.MARKET,
                             price=D("100")),
            broker_profile=_broker(supports_market_orders=False))
        assert decision.code == "INSUFFICIENT_CAPITAL"

    def test_pipeline_order_capability_before_instrument(self):
        decision = _decide(
            request=_request(order_type=OrderType.MARKET),
            instrument=_instrument(symbol="OTHER"),
            broker_profile=_broker(supports_market_orders=False))
        assert decision.code == "MARKET_NOT_SUPPORTED"

    def test_pipeline_order_instrument_before_exposure(self):
        decision = _decide(
            instrument=_instrument(symbol="OTHER"),
            limits=RiskLimits(max_exposure=D("1")))
        assert decision.code == "INSTRUMENT_MISMATCH"

    def test_pipeline_order_exposure_before_daily_loss(self):
        decision = _decide(
            portfolio=_portfolio(daily_realized_pnl=D("-600")),
            limits=RiskLimits(max_exposure=D("50"),
                              max_daily_loss=D("500")))
        assert decision.code == "EXPOSURE_EXCEEDED"

    def test_allow_carries_requested_quantity(self):
        decision = _decide()
        assert decision.decision is RiskDecisionType.ALLOW
        assert decision.code is None
        assert decision.approved_quantity == D("1")

    def test_decision_type_closed(self):
        decision = _decide()
        assert decision.decision in RiskDecisionType


# ── Girdi doğrulama (sterile) ────────────────────────────────────────

class TestInputValidation:
    @pytest.mark.parametrize("field,bad", [
        ("request", None), ("request", "r"),
        ("portfolio", None), ("portfolio", 1),
        ("instrument", None), ("instrument", "i"),
        ("broker_profile", None), ("broker_profile", {}),
        ("limits", None), ("limits", ()),
    ])
    def test_wrong_types_sterile(self, field, bad):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            _decide(**{field: bad})

    @pytest.mark.parametrize("quantity", [D("0"), D("-1")])
    def test_non_positive_quantity_sterile(self, quantity):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            _decide(request=_request(quantity=quantity))

    @pytest.mark.parametrize("price", [D("0"), D("-100")])
    def test_non_positive_price_sterile(self, price):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            _decide(request=_request(price=price))

    def test_error_message_bare_code(self):
        try:
            _decide(request=None)
        except ValueError as exc:
            assert str(exc) == "INVALID_RISK_INPUT"

    def test_risk_engine_class(self):
        engine = RiskEngine(RiskLimits(max_position_size=D("4")))
        decision = engine.validate(_request(quantity=D("10")),
                                   _portfolio(), _instrument(),
                                   _broker())
        assert decision.decision is RiskDecisionType.REDUCE_SIZE
        assert engine.limits == RiskLimits(
            max_position_size=D("4"))

    def test_risk_engine_rejects_bad_limits(self):
        with pytest.raises(ValueError, match="INVALID_RISK_INPUT"):
            RiskEngine(limits=None)


# ── Kamu API ve güvenlik ─────────────────────────────────────────────

def _module_imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


class TestPublicApiAndSecurity:
    def test_engine_public_surface(self):
        assert set(execution_risk_engine.__all__) == {
            "validate_execution", "calculate_position_size",
            "calculate_exposure", "evaluate_portfolio_risk",
            "RiskEngine"}

    def test_models_public_surface(self):
        assert set(execution_risk_models.__all__) == {
            "AssetType", "RiskDecisionType", "BrokerProfile",
            "Instrument", "Portfolio", "PortfolioRisk",
            "PositionRisk", "RiskLimits", "Exposure",
            "CapitalState", "RiskDecision"}

    def test_policies_no_public_surface(self):
        assert execution_risk_policies.__all__ == []
        public = {name for name, value in vars(
            execution_risk_policies).items()
            if not name.startswith("_")
            and (inspect.isfunction(value) or inspect.isclass(value))
            and getattr(value, "__module__", None)
            == "execution_risk_policies"}
        assert public == set()

    def test_no_additional_public_callables(self):
        for module, allowed in (
                (execution_risk_engine,
                 set(execution_risk_engine.__all__)),
                (execution_risk_models,
                 set(execution_risk_models.__all__))):
            public = {name for name, value in vars(module).items()
                      if not name.startswith("_")
                      and (inspect.isfunction(value)
                           or inspect.isclass(value))
                      and getattr(value, "__module__", None)
                      == module.__name__}
            assert public <= allowed

    @pytest.mark.parametrize("module", [
        execution_risk_models, execution_risk_policies,
        execution_risk_engine])
    def test_no_forbidden_imports(self, module):
        roots = {m.split(".")[0] for m in _module_imports(module)}
        forbidden = {"uuid", "datetime", "time", "random", "os",
                     "sys", "io", "socket", "requests", "httpx",
                     "urllib", "urllib3", "threading", "asyncio",
                     "subprocess", "sqlite3", "pickle", "shelve",
                     "pathlib", "secrets", "ccxt", "binance",
                     "websocket", "websockets", "aiohttp",
                     "broker_adapter", "binance_spot_adapter"}
        assert not roots & forbidden

    @pytest.mark.parametrize("module", [
        execution_risk_models, execution_risk_policies,
        execution_risk_engine])
    def test_allowed_imports_only(self, module):
        allowed = {"__future__", "enum", "dataclasses", "decimal",
                   "typing", "execution_enums", "execution_models",
                   "execution_risk_models",
                   "execution_risk_policies"}
        assert _module_imports(module) <= allowed

    @pytest.mark.parametrize("module", [
        execution_risk_models, execution_risk_policies,
        execution_risk_engine])
    def test_no_dangerous_calls(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "open", "__import__", "compile")

    @pytest.mark.parametrize("token", [
        "Binance", "IBKR", "Midas", "Bybit", "OKX", "Kraken",
        "binance", "bybit", "okx", "kraken", "midas"])
    def test_no_broker_specific_code(self, token):
        for module in (execution_risk_models,
                       execution_risk_policies,
                       execution_risk_engine):
            assert token not in inspect.getsource(module)

    @pytest.mark.parametrize("token", [
        "BTCUSDT", "ETHUSDT", "AAPL", "THYAO", "USDT\"", "'USDT'"])
    def test_no_symbol_specific_code(self, token):
        for module in (execution_risk_models,
                       execution_risk_policies,
                       execution_risk_engine):
            assert token not in inspect.getsource(module)

    @pytest.mark.parametrize("token", [
        "place_order", "submit_order", "cancel_order",
        "modify_order", "execute_trade", "requests.", "http://",
        "https://"])
    def test_no_hidden_execution(self, token):
        for module in (execution_risk_models,
                       execution_risk_policies,
                       execution_risk_engine):
            assert token not in inspect.getsource(module)

    def test_decision_never_executes(self):
        # Karar nesnesi salt veridir; çağrılabilir üye içermez
        decision = _decide()
        for field in dataclasses.fields(decision):
            assert not callable(getattr(decision, field.name))
