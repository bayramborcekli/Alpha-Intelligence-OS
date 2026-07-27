"""Mission 2000 — Execution Foundation: deterministik Risk Engine.

YALNIZ yürütme güvenliğini değerlendirir. ASLA emir yürütmez, iptal
etmez, değiştirmez; BrokerAdapter'ı, ağı, dosya sistemini, ortamı
bilmez; zaman/UUID/rastgelelik üretmez.

Dondurulmuş deterministik doğrulama sırası:
1 Girdi doğrulama → 2 Sermaye doğrulama → 3 Broker yetenek
doğrulama → 4 Enstrüman doğrulama → 5 Maruziyet doğrulama →
6 Günlük zarar doğrulama → 7 Pozisyon boyutlama → 8 Risk kararı.

Onaylı kararlar (kapalı küme): ALLOW · REJECT · REDUCE_SIZE ·
REQUIRE_CONFIRMATION. Kural tablosu `execution_risk_policies`
docstring'inde dondurulmuştur — gizli kural yoktur.

Kamu yüzeyi: validate_execution, calculate_position_size,
calculate_exposure, evaluate_portfolio_risk, RiskEngine. Ek kamu API
yoktur.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Optional

from execution_models import ExecutionRequest, Position
from execution_risk_models import (
    BrokerProfile,
    Exposure,
    Instrument,
    Portfolio,
    PortfolioRisk,
    PositionRisk,
    RiskDecision,
    RiskDecisionType,
    RiskLimits,
)
from execution_risk_policies import (
    _apply_position_sizing,
    _quantize_step,
    _validate_broker_capabilities,
    _validate_capital,
    _validate_daily_loss,
    _validate_exposure,
    _validate_input,
    _validate_instrument,
)
from execution_risk_policies import (
    CODE_EXPOSURE_EXCEEDED as _CODE_EXPOSURE,
)
from execution_risk_policies import (
    CODE_UNKNOWN_NOTIONAL as _CODE_UNKNOWN_NOTIONAL,
)

__all__ = [
    "validate_execution", "calculate_position_size",
    "calculate_exposure", "evaluate_portfolio_risk", "RiskEngine",
]

_ERROR_INVALID_INPUT = "INVALID_RISK_INPUT"
_ZERO = Decimal("0")


def validate_execution(request: ExecutionRequest,
                       portfolio: Portfolio,
                       instrument: Instrument,
                       broker_profile: BrokerProfile,
                       limits: RiskLimits) -> RiskDecision:
    """Dondurulmuş 8 adımlı deterministik risk doğrulaması."""
    # 1 — girdi doğrulama
    _validate_input(request, portfolio, instrument, broker_profile,
                    limits)

    # 2 — sermaye doğrulama
    capital_code = _validate_capital(request, portfolio)
    if capital_code == _CODE_UNKNOWN_NOTIONAL:
        return RiskDecision(
            decision=RiskDecisionType.REQUIRE_CONFIRMATION,
            code=capital_code)
    if capital_code is not None:
        return RiskDecision(decision=RiskDecisionType.REJECT,
                            code=capital_code)

    # 3 — broker yetenek doğrulama
    capability_code = _validate_broker_capabilities(
        request, portfolio, instrument, broker_profile)
    if capability_code is not None:
        return RiskDecision(decision=RiskDecisionType.REJECT,
                            code=capability_code)

    # 4 — enstrüman doğrulama
    instrument_code = _validate_instrument(request, instrument)
    if instrument_code is not None:
        return RiskDecision(decision=RiskDecisionType.REJECT,
                            code=instrument_code)

    # 5 — maruziyet doğrulama
    exposure_code, reduced = _validate_exposure(
        request, portfolio, instrument, limits)
    if exposure_code is not None:
        if reduced is None or reduced <= _ZERO:
            return RiskDecision(decision=RiskDecisionType.REJECT,
                                code=_CODE_EXPOSURE)
        return RiskDecision(decision=RiskDecisionType.REDUCE_SIZE,
                            code=_CODE_EXPOSURE,
                            approved_quantity=reduced)

    # 6 — günlük zarar doğrulama
    daily_code = _validate_daily_loss(portfolio, limits)
    if daily_code is not None:
        return RiskDecision(decision=RiskDecisionType.REJECT,
                            code=daily_code)

    # 7 — pozisyon boyutlama
    sizing_code, sized = _apply_position_sizing(request, limits)
    if sizing_code is not None:
        return RiskDecision(decision=RiskDecisionType.REDUCE_SIZE,
                            code=sizing_code,
                            approved_quantity=sized)

    # 8 — risk kararı
    return RiskDecision(decision=RiskDecisionType.ALLOW, code=None,
                        approved_quantity=request.quantity)


def calculate_position_size(available_capital: Decimal,
                            price: Decimal,
                            step_size: Optional[Decimal] = None
                            ) -> Optional[Decimal]:
    """Sermaye ve fiyattan azami pozisyon miktarı (step'e aşağı).

    Fiyat sıfır/negatif veya sermaye negatifse null döner (asla 0
    uydurulmaz). Deterministiktir.
    """
    if not isinstance(available_capital, Decimal) or \
            not isinstance(price, Decimal) or \
            not (step_size is None or isinstance(step_size, Decimal)):
        raise ValueError(_ERROR_INVALID_INPUT)
    if price <= _ZERO or available_capital < _ZERO:
        return None
    return _quantize_step(available_capital / price, step_size)


def calculate_exposure(position: Position,
                       price: Optional[Decimal] = None) -> Exposure:
    """Pozisyon maruziyeti — fiyat bilinmiyorsa notional null."""
    if not isinstance(position, Position) or \
            not (price is None or isinstance(price, Decimal)):
        raise ValueError(_ERROR_INVALID_INPUT)
    notional = None if price is None else position.quantity * price
    return Exposure(symbol=position.symbol,
                    quantity=position.quantity, notional=notional)


def evaluate_portfolio_risk(
        portfolio: Portfolio,
        prices: Optional[Mapping[str, Decimal]] = None
) -> PortfolioRisk:
    """Portföy risk toplaması.

    Fiyatı bilinmeyen pozisyonun notional'ı null kalır; herhangi bir
    notional bilinmiyorsa toplam da null'dur (bilinmeyen asla 0
    sayılmaz). exposure_ratio = toplam notional / toplam sermaye
    (payda sıfır/bilinmeyense null).
    """
    if not isinstance(portfolio, Portfolio) or \
            not (prices is None or isinstance(prices, Mapping)):
        raise ValueError(_ERROR_INVALID_INPUT)
    position_risks = []
    total: Optional[Decimal] = _ZERO
    for position in portfolio.positions:
        price = None if prices is None else prices.get(
            position.symbol)
        if price is not None and not isinstance(price, Decimal):
            raise ValueError(_ERROR_INVALID_INPUT)
        notional = None if price is None else \
            position.quantity * price
        if notional is None:
            total = None
        elif total is not None:
            total += notional
        position_risks.append(PositionRisk(
            symbol=position.symbol, side=position.side,
            quantity=position.quantity, notional=notional,
            exposure_ratio=None))
    ratio = None
    if total is not None and \
            portfolio.capital.total_capital > _ZERO:
        ratio = total / portfolio.capital.total_capital
    if not portfolio.positions:
        total = _ZERO
    return PortfolioRisk(total_notional=total, exposure_ratio=ratio,
                         position_risks=tuple(position_risks))


class RiskEngine:
    """Dondurulmuş limitlerle deterministik risk değerlendirici."""

    __slots__ = ("_limits",)

    def __init__(self, limits: RiskLimits) -> None:
        if not isinstance(limits, RiskLimits):
            raise ValueError(_ERROR_INVALID_INPUT)
        object.__setattr__(self, "_limits", limits)

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    def validate(self, request: ExecutionRequest,
                 portfolio: Portfolio, instrument: Instrument,
                 broker_profile: BrokerProfile) -> RiskDecision:
        return validate_execution(request, portfolio, instrument,
                                  broker_profile, self._limits)
