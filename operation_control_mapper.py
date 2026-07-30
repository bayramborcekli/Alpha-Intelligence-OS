"""Mission 2200 — Agent 01: Ham veri → görünüm eşleyicisi.

Ham (dict) kaynak verisini değişmez görünüm modellerine çevirir.

Kurallar:
- Parasal/miktar değerleri str veya Decimal kabul edilir;
  ikili float KESİN reddedilir (sessizce dönüştürülmez).
- Eksik/çözülemeyen alan → None (Decimal) veya "UNKNOWN" (metin).
- Sahte sağlıklı durum üretilmez: bilinmeyen mutabakat UNKNOWN,
  bilinmeyen tazelik UNKNOWN kalır.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional

from operation_control_models import (
    UNKNOWN, DataFreshness, OrderView, PositionView,
    ProductView, ReconciliationState, ReconciliationView,
    RiskLimitsView, SignalView, SymbolAutomationState)

__all__ = [
    "to_decimal",
    "to_text",
    "to_reconciliation_state",
    "to_freshness",
    "map_position",
    "map_order",
    "map_product",
    "map_signal",
    "map_reconciliation",
    "map_risk_limits",
]

_ORDER_LIFECYCLE = frozenset({
    "CREATED", "AUTHORIZED", "REJECTED", "SUBMITTED",
    "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED",
    "CANCEL_REQUESTED", "CANCELLED", "CLOSE_REQUESTED",
    "FAILED", "RECONCILED"})


def to_decimal(value: object) -> Optional[Decimal]:
    """Güvenli Decimal dönüşümü — float REDDEDİLİR."""
    if value is None or isinstance(value, (float, bool)):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
        return parsed if parsed.is_finite() else None
    return None


def to_text(value: object) -> str:
    """Metin dönüşümü — boş/eksik değer UNKNOWN olur."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, bool):
        return UNKNOWN
    if isinstance(value, int):
        return str(value)
    return UNKNOWN


def to_reconciliation_state(value: object
                            ) -> ReconciliationState:
    """Bilinmeyen değer UNKNOWN'a çöker — asla MATCHED'e değil."""
    if isinstance(value, ReconciliationState):
        return value
    if isinstance(value, str):
        try:
            return ReconciliationState(value.strip().upper())
        except ValueError:
            return ReconciliationState.UNKNOWN
    return ReconciliationState.UNKNOWN


def to_freshness(value: object) -> DataFreshness:
    """Bilinmeyen tazelik FRESH sayılamaz."""
    if isinstance(value, DataFreshness):
        return value
    if isinstance(value, str):
        try:
            return DataFreshness(value.strip().upper())
        except ValueError:
            return DataFreshness.UNKNOWN
    return DataFreshness.UNKNOWN


def _symbol_state(value: object) -> SymbolAutomationState:
    if isinstance(value, SymbolAutomationState):
        return value
    if isinstance(value, str):
        try:
            return SymbolAutomationState(
                value.strip().upper())
        except ValueError:
            return SymbolAutomationState.DISABLED
    return SymbolAutomationState.DISABLED


def _lifecycle_status(value: object) -> str:
    text = to_text(value).upper()
    return text if text in _ORDER_LIFECYCLE else UNKNOWN


def map_position(raw: Mapping) -> PositionView:
    """Ham pozisyon kaydını görünüme çevir."""
    return PositionView(
        position_id=to_text(raw.get("position_id")
                            or raw.get("symbol")),
        symbol=to_text(raw.get("symbol")),
        market=to_text(raw.get("market") or "SPOT"),
        side=to_text(raw.get("side")),
        position_status=to_text(raw.get("position_status")
                                or "OPEN"),
        strategy=to_text(raw.get("strategy")),
        entry_price=to_decimal(raw.get("entry_price")),
        current_price=to_decimal(raw.get("current_price")),
        quantity=to_decimal(raw.get("quantity")),
        notional_value=to_decimal(raw.get("notional_value")),
        realized_pnl=to_decimal(raw.get("realized_pnl")),
        unrealized_pnl=to_decimal(raw.get("unrealized_pnl")),
        pnl_percent=to_decimal(raw.get("pnl_percent")),
        fees=to_decimal(raw.get("fees")),
        stop_loss=to_decimal(raw.get("stop_loss")),
        take_profit=to_decimal(raw.get("take_profit")),
        max_favorable_excursion=to_decimal(
            raw.get("max_favorable_excursion")),
        max_adverse_excursion=to_decimal(
            raw.get("max_adverse_excursion")),
        opened_at=to_text(raw.get("opened_at")),
        last_reconciled_at=to_text(
            raw.get("last_reconciled_at")),
        reconciliation_state=to_reconciliation_state(
            raw.get("reconciliation_state")),
        execution_mode=to_text(raw.get("execution_mode")
                               or "PAPER"))


def map_order(raw: Mapping) -> OrderView:
    """Ham emir kaydını görünüme çevir."""
    return OrderView(
        order_id=to_text(raw.get("order_id")),
        client_order_id=to_text(raw.get("client_order_id")),
        symbol=to_text(raw.get("symbol")),
        side=to_text(raw.get("side")),
        order_type=to_text(raw.get("order_type")),
        quantity=to_decimal(raw.get("quantity")),
        requested_price=to_decimal(raw.get("requested_price")),
        average_fill_price=to_decimal(
            raw.get("average_fill_price")),
        filled_quantity=to_decimal(raw.get("filled_quantity")),
        remaining_quantity=to_decimal(
            raw.get("remaining_quantity")),
        status=_lifecycle_status(raw.get("status")),
        created_at=to_text(raw.get("created_at")),
        updated_at=to_text(raw.get("updated_at")),
        strategy=to_text(raw.get("strategy")),
        correlation_id=to_text(raw.get("correlation_id")),
        execution_mode=to_text(raw.get("execution_mode")
                               or "PAPER"),
        reconciliation_state=to_reconciliation_state(
            raw.get("reconciliation_state")))


def map_product(raw: Mapping,
                automation_state: object = None
                ) -> ProductView:
    """Ham ürün kaydını görünüme çevir.

    ``automation_state`` servis kayıt defterinden gelir; ham
    veri güvenilir sayılmaz (varsayılan DISABLED)."""
    state = _symbol_state(automation_state
                          if automation_state is not None
                          else raw.get("automation_state"))
    entry_eligible = raw.get("entry_eligible")
    return ProductView(
        symbol=to_text(raw.get("symbol")),
        market=to_text(raw.get("market") or "SPOT"),
        strategy=to_text(raw.get("strategy")),
        automation_state=state,
        signal_state=to_text(raw.get("signal_state")),
        execution_mode=to_text(raw.get("execution_mode")
                               or "PAPER"),
        direction=to_text(raw.get("direction")),
        entry_eligible=entry_eligible is True and
        state is SymbolAutomationState.ENABLED,
        last_signal_at=to_text(raw.get("last_signal_at")),
        last_decision=to_text(raw.get("last_decision")),
        last_rejection_reason=to_text(
            raw.get("last_rejection_reason") or "-"),
        # Kanonik karar alanları — yoksa None (uydurma yok)
        analyzed_at=raw.get("analyzed_at"),
        model=raw.get("model"),
        decision_state=raw.get("decision_state"),
        source=raw.get("source"),
        freshness=raw.get("freshness"),
        confidence=raw.get("confidence"),
        net_reward_risk=raw.get("net_reward_risk"),
        expected_edge=raw.get("expected_edge"),
        data_quality=raw.get("data_quality"))


def map_signal(raw: Mapping) -> SignalView:
    """Ham sinyal kaydını görünüme çevir — öneri EMİR değildir."""
    kind = to_text(raw.get("kind"))
    if kind not in ("PROPOSAL", "CANDIDATE",
                    "AUTHORIZED_INTENT", "ORDER", "POSITION"):
        kind = "PROPOSAL"
    return SignalView(
        signal_time=to_text(raw.get("signal_time")),
        symbol=to_text(raw.get("symbol")),
        strategy=to_text(raw.get("strategy")),
        direction=to_text(raw.get("direction")),
        confidence=to_decimal(raw.get("confidence")),
        decision=to_text(raw.get("decision")),
        risk_outcome=to_text(raw.get("risk_outcome")),
        permission_outcome=to_text(
            raw.get("permission_outcome")),
        rejection_code=to_text(raw.get("rejection_code")
                               or "-"),
        execution_result=to_text(raw.get("execution_result")
                                 or "-"),
        correlation_id=to_text(raw.get("correlation_id")),
        kind=kind)


def map_reconciliation(raw: Mapping) -> ReconciliationView:
    """Ham mutabakat kaydını görünüme çevir."""
    ledger_position = to_decimal(raw.get("ledger_position"))
    broker_position = to_decimal(raw.get("broker_position"))
    difference = to_decimal(raw.get("difference"))
    if difference is None and ledger_position is not None \
            and broker_position is not None:
        difference = ledger_position - broker_position
    return ReconciliationView(
        symbol=to_text(raw.get("symbol")),
        last_reconciled_at=to_text(
            raw.get("last_reconciled_at")),
        ledger_position=ledger_position,
        broker_position=broker_position,
        difference=difference,
        order_mismatch=raw.get("order_mismatch") is True,
        quantity_mismatch=raw.get("quantity_mismatch") is True,
        price_mismatch=raw.get("price_mismatch") is True,
        orphan_order=raw.get("orphan_order") is True,
        orphan_position=raw.get("orphan_position") is True,
        state=to_reconciliation_state(raw.get("state")),
        operator_action=to_text(raw.get("operator_action")
                                or "-"))


def map_risk_limits(raw: Mapping) -> RiskLimitsView:
    """Ham risk limitlerini görünüme çevir (salt gösterim)."""

    def _opt_int(value: object) -> Optional[int]:
        if isinstance(value, bool) or not isinstance(
                value, int) or value < 0:
            return None
        return value

    def _str_tuple(value: object) -> tuple:
        if isinstance(value, (list, tuple)):
            return tuple(v for v in value
                         if isinstance(v, str) and v)
        return ()

    return RiskLimitsView(
        max_order_notional=to_decimal(
            raw.get("max_order_notional")),
        max_position_notional=to_decimal(
            raw.get("max_position_notional")),
        max_open_positions=_opt_int(
            raw.get("max_open_positions")),
        max_daily_loss=to_decimal(raw.get("max_daily_loss")),
        max_drawdown=to_decimal(raw.get("max_drawdown")),
        max_symbol_exposure=to_decimal(
            raw.get("max_symbol_exposure")),
        cooldown_seconds=_opt_int(raw.get("cooldown_seconds")),
        allowed_markets=_str_tuple(raw.get("allowed_markets")),
        allowed_directions=_str_tuple(
            raw.get("allowed_directions")),
        allowed_execution_modes=_str_tuple(
            raw.get("allowed_execution_modes")),
        micro_live_authorized=raw.get(
            "micro_live_authorized") is True,
        authorization_expiry=to_text(
            raw.get("authorization_expiry") or "-"),
        kill_switch_active=raw.get(
            "kill_switch_active") is True)
