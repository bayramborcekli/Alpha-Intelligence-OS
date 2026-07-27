"""Mission 2200 Agent 02 — Çalışma Alanı görünüm modelleri (saf).

Operation Control Center'ın işlem terminali görünümleri:
portföy çubuğu, performans, broker sağlığı, strateji paneli ve
işlem günlüğü zaman çizelgesi.

Kurallar (Agent 01 sözleşmesiyle aynı):
- Yalnız stdlib + kendi katmanı; framework yok, G/Ç yok.
- Parasal alanlar Decimal; float kurucu düzeyinde REDDEDİLİR.
- Bilinmeyen/eksik değer None'dur ve UI'da UNKNOWN görünür —
  asla sahte 0 veya sahte "sağlıklı" durum üretilmez.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Optional, Tuple

from operation_control_errors import OperationControlValidationError

__all__ = [
    "UNKNOWN", "BROKER_STATES", "JOURNAL_KINDS",
    "PortfolioView", "PerformanceView", "BrokerHealthView",
    "StrategyView", "JournalEventView",
]

UNKNOWN = "UNKNOWN"

# Broker sağlık alanlarının izinli durum kümeleri (fail-closed:
# küme dışı her değer UNKNOWN'a düşürülür, hataya değil — sağlık
# ekranı asla çökmez ama asla uydurmaz).
BROKER_STATES = frozenset({
    "OK", "DEGRADED", "DOWN", "STALE", "LIMITED", "BLOCKED",
    "SYNCED", "OUT_OF_SYNC", "AUTHENTICATED", "UNAUTHENTICATED",
    "GRANTED", "DENIED", "READ_ONLY", UNKNOWN,
})

# İşlem günlüğü olay türleri — yalnız sertifikalı/denetimli
# olaylar; UI bu küme dışını göstermez.
JOURNAL_KINDS = frozenset({
    "SIGNAL_GENERATED", "RISK_APPROVED", "RISK_REJECTED",
    "AUTHORIZED", "SUBMITTED", "FILLED", "CANCELLED",
    "REJECTED", "CLOSED", "RECONCILED", "OPERATOR_ACTION",
})


def _fail(fieldname: str) -> None:
    raise OperationControlValidationError(
        f"INVALID_WORKSPACE_FIELD:{fieldname}")


def _require_str(value: object, fieldname: str) -> None:
    if not isinstance(value, str) or not value:
        _fail(fieldname)


def _require_int(value: object, fieldname: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(fieldname)


def _require_opt_int(value: object, fieldname: str) -> None:
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) \
            or value < 0:
        _fail(fieldname)


def _require_bool(value: object, fieldname: str) -> None:
    if not isinstance(value, bool):
        _fail(fieldname)


def _require_opt_decimal(value: object, fieldname: str) -> None:
    if value is None:
        return
    if isinstance(value, float) or not isinstance(value, Decimal) \
            or not value.is_finite():
        _fail(fieldname)


@dataclass(frozen=True)
class PortfolioView:
    """Portföy çubuğu. Tüm parasal alanlar Optional[Decimal]."""
    portfolio_value: Optional[Decimal]
    cash: Optional[Decimal]
    equity: Optional[Decimal]
    daily_pnl: Optional[Decimal]
    weekly_pnl: Optional[Decimal]
    monthly_pnl: Optional[Decimal]
    open_risk: Optional[Decimal]
    exposure: Optional[Decimal]
    drawdown_pct: Optional[Decimal]
    largest_winner: Optional[Decimal]
    largest_loser: Optional[Decimal]
    open_position_count: int
    source_freshness: str

    def __post_init__(self):
        for field in fields(self):
            if field.name == "open_position_count":
                _require_int(self.open_position_count, field.name)
                if self.open_position_count < 0:
                    _fail(field.name)
            elif field.name == "source_freshness":
                _require_str(self.source_freshness, field.name)
            else:
                _require_opt_decimal(getattr(self, field.name),
                                     field.name)


@dataclass(frozen=True)
class PerformanceView:
    """Performans panosu; metrik modülü çıktısının görünümü."""
    trade_count: int
    win_count: int
    loss_count: int
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
    equity_curve: Tuple[Tuple[int, Decimal], ...]

    def __post_init__(self):
        for name in ("trade_count", "win_count", "loss_count",
                     "dropped_records"):
            _require_int(getattr(self, name), name)
            if getattr(self, name) < 0:
                _fail(name)
        _require_opt_int(self.average_hold_seconds,
                         "average_hold_seconds")
        for name in ("win_rate_pct", "loss_rate_pct", "average_win",
                     "average_loss", "profit_factor", "sharpe",
                     "max_drawdown_pct", "daily_profit",
                     "weekly_profit", "monthly_profit"):
            _require_opt_decimal(getattr(self, name), name)
        if not isinstance(self.equity_curve, tuple):
            _fail("equity_curve")
        for point in self.equity_curve:
            if (not isinstance(point, tuple) or len(point) != 2
                    or not isinstance(point[0], int)
                    or isinstance(point[0], bool)
                    or not isinstance(point[1], Decimal)):
                _fail("equity_curve")


@dataclass(frozen=True)
class BrokerHealthView:
    """'Connected' yerine dürüst sağlık alanları. Ölçülmeyen alan
    UNKNOWN'dur (ör. reconnect_count şu an ölçülmüyor → None)."""
    heartbeat_state: str
    heartbeat_at: Optional[int]
    latency_ms: Optional[int]
    api_status: str
    rate_limit_state: str
    reconnect_count: Optional[int]
    synchronization_state: str
    authentication_state: str
    permission_state: str
    data_age_seconds: Optional[int]

    def __post_init__(self):
        for name in ("heartbeat_state", "api_status",
                     "rate_limit_state", "synchronization_state",
                     "authentication_state", "permission_state"):
            value = getattr(self, name)
            _require_str(value, name)
            if value not in BROKER_STATES:
                _fail(name)
        for name in ("heartbeat_at", "latency_ms",
                     "reconnect_count", "data_age_seconds"):
            _require_opt_int(getattr(self, name), name)


@dataclass(frozen=True)
class StrategyView:
    """Sol panel — sembol başına strateji satırı."""
    symbol: str
    strategy: str
    state: str
    direction: str
    confidence_pct: Optional[Decimal]
    pnl_today: Optional[Decimal]
    entry_eligible: bool
    last_signal_at: str
    open_position_count: int

    def __post_init__(self):
        for name in ("symbol", "strategy", "state", "direction",
                     "last_signal_at"):
            _require_str(getattr(self, name), name)
        _require_opt_decimal(self.confidence_pct, "confidence_pct")
        _require_opt_decimal(self.pnl_today, "pnl_today")
        _require_bool(self.entry_eligible, "entry_eligible")
        _require_int(self.open_position_count,
                     "open_position_count")
        if self.open_position_count < 0:
            _fail("open_position_count")


@dataclass(frozen=True)
class JournalEventView:
    """İşlem günlüğü zaman çizelgesi olayı (yalnız denetimli
    kaynaklar: sinyal görünümleri, denetim zinciri, emir yaşam
    döngüsü). kind küme dışıysa kurucu REDDEDER."""
    event_time: str
    kind: str
    symbol: str
    detail: str
    status: str
    correlation_id: str

    def __post_init__(self):
        for name in ("event_time", "kind", "symbol", "detail",
                     "status", "correlation_id"):
            _require_str(getattr(self, name), name)
        if self.kind not in JOURNAL_KINDS:
            _fail("kind")
