"""Mission 2000 — Execution Foundation: değişmez risk alan modelleri.

Risk Engine'in tükettiği ve ürettiği kanonik sözleşmeler. Her model
dondurulmuş (frozen=True, slots=True) dataclass'tır: değişmez,
hashlenebilir, mutable varsayılan içermez; para/miktar alanları
YALNIZ Decimal kabul eder (sterile INVALID_RISK_INPUT).

Broker soyutlaması: Risk Engine hiçbir broker/exchange adını bilmez;
yalnız `BrokerProfile` yeteneklerini görür. Enstrüman soyutlaması:
Risk Engine hiçbir sembolü tanımaz; yalnız `Instrument` alanlarını
görür. Bilinmeyen değer null'dur; asla 0 değildir.

Güvenlik: I/O yok, ağ yok, zaman/UUID/rastgelelik yok, broker/exchange
SDK importu yok.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from enum import Enum, unique
from typing import Optional, Tuple

from execution_enums import PositionSide
from execution_models import Position

__all__ = [
    "AssetType", "RiskDecisionType", "BrokerProfile", "Instrument",
    "Portfolio", "PortfolioRisk", "PositionRisk", "RiskLimits",
    "Exposure", "CapitalState", "RiskDecision",
]

_ERROR_INVALID_INPUT = "INVALID_RISK_INPUT"


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError(_ERROR_INVALID_INPUT)


def _is_decimal(value: object) -> bool:
    return isinstance(value, Decimal)


def _is_optional_decimal(value: object) -> bool:
    return value is None or isinstance(value, Decimal)


def _is_optional_str(value: object) -> bool:
    return value is None or isinstance(value, str)


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_optional_int(value: object) -> bool:
    return value is None or (isinstance(value, int)
                             and not isinstance(value, bool))


@unique
class AssetType(Enum):
    """Enstrüman varlık tipi (sembol-bağımsız)."""

    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"
    ETF = "ETF"
    FOREX = "FOREX"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"


@unique
class RiskDecisionType(Enum):
    """Onaylı risk kararları — kapalı küme."""

    ALLOW = "ALLOW"
    REJECT = "REJECT"
    REDUCE_SIZE = "REDUCE_SIZE"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"


@dataclass(frozen=True, slots=True)
class BrokerProfile:
    """Broker YETENEK profili — broker adı/kimliği içermez."""

    supports_margin: bool = False
    supports_short: bool = False
    supports_fractional: bool = False
    supports_options: bool = False
    supports_market_orders: bool = False
    supports_after_hours: bool = False
    supports_modify: bool = False
    supports_cancel: bool = False
    supports_trailing_stop: bool = False
    supports_oco: bool = False

    def __post_init__(self) -> None:
        for field in fields(self):
            _require(_is_bool(getattr(self, field.name)))


@dataclass(frozen=True, slots=True)
class Instrument:
    """Sembol-bağımsız enstrüman tanımı."""

    symbol: str
    asset_type: AssetType
    currency: str
    quote_currency: str
    tick_size: Optional[Decimal] = None
    step_size: Optional[Decimal] = None
    price_precision: Optional[int] = None
    quantity_precision: Optional[int] = None
    broker_symbol: Optional[str] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.symbol, str) and bool(self.symbol))
        _require(isinstance(self.asset_type, AssetType))
        _require(isinstance(self.currency, str) and
                 bool(self.currency))
        _require(isinstance(self.quote_currency, str) and
                 bool(self.quote_currency))
        _require(_is_optional_decimal(self.tick_size))
        _require(_is_optional_decimal(self.step_size))
        _require(_is_optional_int(self.price_precision))
        _require(_is_optional_int(self.quantity_precision))
        _require(_is_optional_str(self.broker_symbol))


@dataclass(frozen=True, slots=True)
class CapitalState:
    """Sermaye durumu."""

    total_capital: Decimal
    available_capital: Decimal
    daily_realized_pnl: Optional[Decimal] = None

    def __post_init__(self) -> None:
        _require(_is_decimal(self.total_capital))
        _require(_is_decimal(self.available_capital))
        _require(_is_optional_decimal(self.daily_realized_pnl))


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Portföy anlık görüntüsü."""

    capital: CapitalState
    positions: Tuple[Position, ...] = ()

    def __post_init__(self) -> None:
        _require(isinstance(self.capital, CapitalState))
        _require(isinstance(self.positions, tuple))
        for position in self.positions:
            _require(isinstance(position, Position))


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Dondurulmuş risk limitleri (null = limit tanımsız)."""

    max_position_size: Optional[Decimal] = None
    max_exposure: Optional[Decimal] = None
    max_daily_loss: Optional[Decimal] = None
    max_portfolio_exposure: Optional[Decimal] = None

    def __post_init__(self) -> None:
        for field in fields(self):
            _require(_is_optional_decimal(getattr(self, field.name)))


@dataclass(frozen=True, slots=True)
class Exposure:
    """Tek enstrüman maruziyeti (bilinmeyen notional = null)."""

    symbol: str
    quantity: Decimal
    notional: Optional[Decimal] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.symbol, str) and bool(self.symbol))
        _require(_is_decimal(self.quantity))
        _require(_is_optional_decimal(self.notional))


@dataclass(frozen=True, slots=True)
class PositionRisk:
    """Tek pozisyon riski."""

    symbol: str
    side: PositionSide
    quantity: Decimal
    notional: Optional[Decimal] = None
    exposure_ratio: Optional[Decimal] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.symbol, str) and bool(self.symbol))
        _require(isinstance(self.side, PositionSide))
        _require(_is_decimal(self.quantity))
        _require(_is_optional_decimal(self.notional))
        _require(_is_optional_decimal(self.exposure_ratio))


@dataclass(frozen=True, slots=True)
class PortfolioRisk:
    """Portföy toplam riski."""

    total_notional: Optional[Decimal] = None
    exposure_ratio: Optional[Decimal] = None
    position_risks: Tuple[PositionRisk, ...] = ()

    def __post_init__(self) -> None:
        _require(_is_optional_decimal(self.total_notional))
        _require(_is_optional_decimal(self.exposure_ratio))
        _require(isinstance(self.position_risks, tuple))
        for risk in self.position_risks:
            _require(isinstance(risk, PositionRisk))


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Risk kararı — kapalı karar kümesi + sterile kod."""

    decision: RiskDecisionType
    code: Optional[str] = None
    approved_quantity: Optional[Decimal] = None

    def __post_init__(self) -> None:
        _require(isinstance(self.decision, RiskDecisionType))
        _require(_is_optional_str(self.code))
        _require(_is_optional_decimal(self.approved_quantity))

