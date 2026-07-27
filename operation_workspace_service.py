"""Mission 2200 Agent 02 — Çalışma alanı görünüm kurucuları (saf).

Agent 01 anlık görüntüsü + denetim zinciri + ham hesap/işlem
verisinden terminal panellerinin görünümlerini kurar. Hiçbir
fonksiyon G/Ç yapmaz; tüm veri parametre olarak gelir.

Dürüstlük ilkeleri:
- Eksik kaynak → None/UNKNOWN; asla uydurma değer.
- Portföy çubuğu hesap verisi sağlanmadıysa parasal alanları
  UNKNOWN gösterir; pozisyonlardan türetilebilen alanlar
  (exposure, largest winner/loser, açık pozisyon sayısı)
  pozisyon görünümlerinden hesaplanır.
- Günlük yalnız denetimli kaynaklardan kurulur.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Optional, Sequence, Tuple

from operation_control_mapper import to_decimal, to_text
from operation_control_models import (
    OperationAuditRecord, PositionView, ProductView, SignalView)
from operation_workspace_metrics import (
    DAY_SECONDS, MONTH_SECONDS, WEEK_SECONDS, PerformanceMetrics,
    compute_metrics, parse_trades, period_profit)
from operation_workspace_models import (
    BROKER_STATES, UNKNOWN, BrokerHealthView, JournalEventView,
    PerformanceView, PortfolioView, StrategyView)

__all__ = [
    "build_portfolio_view", "build_performance_view",
    "build_broker_health_view", "build_strategy_rows",
    "build_journal_events",
]


# ── Portföy çubuğu ─────────────────────────────────────────────────

def _position_pnl(position: PositionView) -> Optional[Decimal]:
    parts = [p for p in (position.realized_pnl,
                         position.unrealized_pnl) if p is not None]
    if not parts:
        return None
    return sum(parts, Decimal("0"))


def build_portfolio_view(positions: Sequence[PositionView],
                         account_raw: object,
                         trades_raw: object,
                         now: int,
                         freshness: str = UNKNOWN) -> PortfolioView:
    """Hesap verisi (account_raw) yoksa parasal alanlar UNKNOWN.

    account_raw sözleşmesi: {portfolio_value, cash, equity,
    drawdown_pct} — Decimal'e çevrilebilir metinler."""
    account = account_raw if isinstance(account_raw, Mapping) else {}
    trades, _ = parse_trades(trades_raw)

    exposure = None
    notionals = [p.notional_value for p in positions
                 if p.notional_value is not None]
    if notionals:
        exposure = sum(notionals, Decimal("0"))

    open_risk = None
    risks = []
    for p in positions:
        # Açık risk yalnız stop bilinen pozisyonlar için
        # hesaplanabilir: |entry - stop| * qty. Stop bilinmeyen
        # tek pozisyon bile varsa toplam risk UNKNOWN'dur
        # (kısmi toplam yanıltıcı olur).
        if (p.stop_loss is None or p.entry_price is None
                or p.quantity is None):
            risks = None
            break
        risks.append(abs(p.entry_price - p.stop_loss) * abs(p.quantity))
    if risks is not None and risks:
        open_risk = sum(risks, Decimal("0"))

    pnls = [(_position_pnl(p), p) for p in positions]
    known = [pnl for pnl, _ in pnls if pnl is not None]
    largest_winner = max(known) if known else None
    largest_loser = min(known) if known else None

    return PortfolioView(
        portfolio_value=to_decimal(account.get("portfolio_value")),
        cash=to_decimal(account.get("cash")),
        equity=to_decimal(account.get("equity")),
        daily_pnl=period_profit(trades, now, DAY_SECONDS),
        weekly_pnl=period_profit(trades, now, WEEK_SECONDS),
        monthly_pnl=period_profit(trades, now, MONTH_SECONDS),
        open_risk=open_risk,
        exposure=exposure,
        drawdown_pct=to_decimal(account.get("drawdown_pct")),
        largest_winner=largest_winner,
        largest_loser=largest_loser,
        open_position_count=len(list(positions)),
        source_freshness=freshness if isinstance(freshness, str)
        and freshness else UNKNOWN,
    )


# ── Performans ─────────────────────────────────────────────────────

def build_performance_view(trades_raw: object, equity_raw: object,
                           now: int) -> PerformanceView:
    metrics: PerformanceMetrics = compute_metrics(
        trades_raw, equity_raw, now)
    return PerformanceView(
        trade_count=metrics.trade_count,
        win_count=metrics.win_count,
        loss_count=metrics.loss_count,
        dropped_records=metrics.dropped_records,
        win_rate_pct=metrics.win_rate_pct,
        loss_rate_pct=metrics.loss_rate_pct,
        average_win=metrics.average_win,
        average_loss=metrics.average_loss,
        profit_factor=metrics.profit_factor,
        sharpe=metrics.sharpe,
        max_drawdown_pct=metrics.max_drawdown_pct,
        average_hold_seconds=metrics.average_hold_seconds,
        daily_profit=metrics.daily_profit,
        weekly_profit=metrics.weekly_profit,
        monthly_profit=metrics.monthly_profit,
        equity_curve=tuple((p.at, p.equity)
                           for p in metrics.equity_curve),
    )


# ── Broker sağlığı ─────────────────────────────────────────────────

def _state(raw: Mapping, key: str) -> str:
    value = raw.get(key)
    if isinstance(value, str) and value.strip().upper() \
            in BROKER_STATES:
        return value.strip().upper()
    return UNKNOWN


def _opt_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def build_broker_health_view(raw: object,
                             now: int) -> BrokerHealthView:
    """Ölçülmeyen alan None/UNKNOWN kalır — 'Connected' gibi tek
    kelimelik sahte sağlık yerine alan alan dürüst durum."""
    data = raw if isinstance(raw, Mapping) else {}
    heartbeat_at = _opt_int(data.get("heartbeat_at"))
    heartbeat_state = UNKNOWN
    data_age = None
    if heartbeat_at is not None and isinstance(now, int) \
            and now >= heartbeat_at:
        data_age = now - heartbeat_at
        heartbeat_state = "OK" if data_age <= 60 else "STALE"
    return BrokerHealthView(
        heartbeat_state=heartbeat_state,
        heartbeat_at=heartbeat_at,
        latency_ms=_opt_int(data.get("latency_ms")),
        api_status=_state(data, "api_status"),
        rate_limit_state=_state(data, "rate_limit_state"),
        reconnect_count=_opt_int(data.get("reconnect_count")),
        synchronization_state=_state(data, "synchronization_state"),
        authentication_state=_state(data, "authentication_state"),
        permission_state=_state(data, "permission_state"),
        data_age_seconds=data_age,
    )


# ── Strateji paneli ────────────────────────────────────────────────

def build_strategy_rows(products: Sequence[ProductView],
                        positions: Sequence[PositionView],
                        signals: Sequence[SignalView]
                        ) -> Tuple[StrategyView, ...]:
    """Sembol başına strateji satırı. PnL bugünü pozisyonların
    bilinen PnL'lerinden; hiçbiri bilinmiyorsa UNKNOWN."""
    latest_signal: dict = {}
    for signal in signals:
        latest_signal.setdefault(signal.symbol, signal)
    rows = []
    for product in products:
        symbol_positions = [p for p in positions
                            if p.symbol == product.symbol]
        pnls = [pnl for pnl in (_position_pnl(p)
                                for p in symbol_positions)
                if pnl is not None]
        signal = latest_signal.get(product.symbol)
        confidence = signal.confidence if signal is not None \
            else None
        rows.append(StrategyView(
            symbol=product.symbol,
            strategy=product.strategy or UNKNOWN,
            state=product.automation_state.value,
            direction=product.direction or UNKNOWN,
            confidence_pct=confidence,
            pnl_today=sum(pnls, Decimal("0")) if pnls else None,
            entry_eligible=product.entry_eligible,
            last_signal_at=product.last_signal_at or UNKNOWN,
            open_position_count=len(symbol_positions),
        ))
    return tuple(rows)


# ── İşlem günlüğü ──────────────────────────────────────────────────

_SIGNAL_KIND = {
    "EXECUTED": "FILLED",
    "SUBMITTED": "SUBMITTED",
    "AUTHORIZED": "AUTHORIZED",
    "RISK_APPROVED": "RISK_APPROVED",
    "RISK_REJECTED": "RISK_REJECTED",
    "REJECTED": "REJECTED",
}


def _signal_kind(signal: SignalView) -> str:
    execution = (signal.execution_result or "").upper()
    if execution in _SIGNAL_KIND:
        return _SIGNAL_KIND[execution]
    decision = (signal.decision or "").upper()
    if decision in _SIGNAL_KIND:
        return _SIGNAL_KIND[decision]
    return "SIGNAL_GENERATED"


def build_journal_events(signals: Sequence[SignalView],
                         audit_records: Sequence[OperationAuditRecord],
                         limit: int = 200
                         ) -> Tuple[JournalEventView, ...]:
    """Yalnız denetimli kaynaklar: sertifikalı sinyal görünümleri
    ve operasyon denetim zinciri. Uydurma olay üretilmez."""
    events = []
    for signal in signals:
        events.append(JournalEventView(
            event_time=signal.signal_time or UNKNOWN,
            kind=_signal_kind(signal),
            symbol=signal.symbol or UNKNOWN,
            detail=to_text(signal.decision) or UNKNOWN,
            status=to_text(signal.execution_result) or UNKNOWN,
            correlation_id=signal.correlation_id or UNKNOWN,
        ))
    for record in audit_records:
        events.append(JournalEventView(
            event_time=str(record.timestamp),
            kind="OPERATOR_ACTION",
            symbol=record.target or UNKNOWN,
            detail=record.action or UNKNOWN,
            status=record.result or UNKNOWN,
            correlation_id=record.correlation_id or UNKNOWN,
        ))
    events.reverse()
    if not isinstance(limit, int) or isinstance(limit, bool) \
            or limit <= 0:
        limit = 200
    return tuple(events[:limit])
