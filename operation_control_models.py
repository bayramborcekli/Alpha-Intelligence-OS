"""Mission 2200 — Agent 01: Operasyon Kontrol Merkezi modelleri.

İlkeler:
- Tüm veri sınıfları frozen + slots (değişmezlik).
- Parasal/miktar alanları YALNIZ ``Decimal`` — ikili float
  ticaret değeri taşıyamaz (fail-closed doğrulama).
- Bilinmeyen değerler açıkça ``UNKNOWN`` olarak temsil edilir;
  sahte sağlıklı durum ÜRETİLMEZ.
- Koleksiyonlar tuple; sabit kümeler kapalı Enum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, unique
from typing import Optional, Tuple

from operation_control_errors import (
    OperationControlValidationError)

__all__ = [
    "UNKNOWN",
    "AutomationState",
    "AutomationCommand",
    "SymbolAutomationState",
    "SymbolCommand",
    "ReconciliationState",
    "DataFreshness",
    "OperationActionStatus",
    "IdempotencyStatus",
    "OperationAuditRecord",
    "OperationActionResult",
    "PositionView",
    "OrderView",
    "ProductView",
    "SignalView",
    "ReconciliationView",
    "RiskLimitsView",
    "SystemStatusView",
    "OperationSnapshot",
]

UNKNOWN = "UNKNOWN"


def _fail(fieldname: str) -> None:
    raise OperationControlValidationError(
        f"INVALID_OPERATION_FIELD:{fieldname}")


def _require_str(value: object, fieldname: str) -> None:
    if not isinstance(value, str) or not value:
        _fail(fieldname)


def _require_opt_str(value: object, fieldname: str) -> None:
    if value is None:
        return
    _require_str(value, fieldname)


def _require_int(value: object, fieldname: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) \
            or value < 0:
        _fail(fieldname)


def _require_bool(value: object, fieldname: str) -> None:
    if not isinstance(value, bool):
        _fail(fieldname)


def _require_decimal(value: object, fieldname: str) -> None:
    """İkili float ticaret değeri KESİN reddedilir."""
    if isinstance(value, float):
        _fail(fieldname)
    if not isinstance(value, Decimal):
        _fail(fieldname)
    if not value.is_finite():
        _fail(fieldname)


def _require_opt_decimal(value: object, fieldname: str) -> None:
    if value is None:
        return
    _require_decimal(value, fieldname)


def _require_enum(value: object, enum_type: type,
                  fieldname: str) -> None:
    if not isinstance(value, enum_type):
        _fail(fieldname)


@unique
class AutomationState(Enum):
    """Otomatik ticaret durum makinesi — kapalı küme."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@unique
class AutomationCommand(Enum):
    """Operatör otomasyon komutları — kapalı küme."""

    START = "START"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"


@unique
class SymbolAutomationState(Enum):
    """Sembol düzeyi otomasyon durumu — kapalı küme."""

    DISABLED = "DISABLED"
    ENABLED = "ENABLED"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


@unique
class SymbolCommand(Enum):
    """Sembol düzeyi komutlar — kapalı küme."""

    ENABLE = "ENABLE"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"


@unique
class ReconciliationState(Enum):
    """Mutabakat durumu — bayat sonuç sağlıklı GÖSTERİLMEZ."""

    MATCHED = "MATCHED"
    PENDING = "PENDING"
    MISMATCH = "MISMATCH"
    STALE = "STALE"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


@unique
class DataFreshness(Enum):
    """Veri tazeliği — bilinmeyen tazelik FRESH sayılamaz."""

    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@unique
class OperationActionStatus(Enum):
    """Eylem sonucu — kısmi başarı asla tam başarı sayılmaz."""

    COMPLETED = "COMPLETED"
    ACCEPTED = "ACCEPTED"
    DENIED = "DENIED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


@unique
class IdempotencyStatus(Enum):
    """Idempotency sonucu — kapalı küme."""

    NEW = "NEW"
    REPLAYED = "REPLAYED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class OperationAuditRecord:
    """Değişmez denetim kaydı — sır/ham gövde taşımaz."""

    timestamp: int
    actor: str
    action: str
    target: str
    previous_state: str
    requested_state: str
    result: str
    reason: str
    correlation_id: str
    idempotency_key: Optional[str] = None
    error_code: Optional[str] = None

    def __post_init__(self) -> None:
        _require_int(self.timestamp, "timestamp")
        _require_str(self.actor, "actor")
        _require_str(self.action, "action")
        _require_str(self.target, "target")
        _require_str(self.previous_state, "previous_state")
        _require_str(self.requested_state, "requested_state")
        _require_str(self.result, "result")
        _require_str(self.reason, "reason")
        _require_str(self.correlation_id, "correlation_id")
        _require_opt_str(self.idempotency_key,
                         "idempotency_key")
        _require_opt_str(self.error_code, "error_code")


@dataclass(frozen=True, slots=True)
class OperationActionResult:
    """Durum değiştiren eylemin steril sonucu."""

    action_id: str
    status: OperationActionStatus
    correlation_id: str
    idempotency_status: IdempotencyStatus
    audit_recorded: bool
    lifecycle_status: str
    previous_state: str
    current_state: str
    error_code: Optional[str] = None
    detail_codes: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_str(self.action_id, "action_id")
        _require_enum(self.status, OperationActionStatus,
                      "status")
        _require_str(self.correlation_id, "correlation_id")
        _require_enum(self.idempotency_status,
                      IdempotencyStatus, "idempotency_status")
        _require_bool(self.audit_recorded, "audit_recorded")
        _require_str(self.lifecycle_status, "lifecycle_status")
        _require_str(self.previous_state, "previous_state")
        _require_str(self.current_state, "current_state")
        _require_opt_str(self.error_code, "error_code")
        if not isinstance(self.detail_codes, tuple) or any(
                not isinstance(c, str) or not c
                for c in self.detail_codes):
            _fail("detail_codes")


@dataclass(frozen=True, slots=True)
class PositionView:
    """Açık pozisyon görünümü — parasal alanlar Decimal."""

    position_id: str
    symbol: str
    market: str
    side: str
    position_status: str
    strategy: str
    entry_price: Optional[Decimal]
    current_price: Optional[Decimal]
    quantity: Optional[Decimal]
    notional_value: Optional[Decimal]
    realized_pnl: Optional[Decimal]
    unrealized_pnl: Optional[Decimal]
    pnl_percent: Optional[Decimal]
    fees: Optional[Decimal]
    stop_loss: Optional[Decimal]
    take_profit: Optional[Decimal]
    max_favorable_excursion: Optional[Decimal]
    max_adverse_excursion: Optional[Decimal]
    opened_at: str
    last_reconciled_at: str
    reconciliation_state: ReconciliationState
    execution_mode: str

    def __post_init__(self) -> None:
        _require_str(self.position_id, "position_id")
        _require_str(self.symbol, "symbol")
        _require_str(self.market, "market")
        _require_str(self.side, "side")
        _require_str(self.position_status, "position_status")
        _require_str(self.strategy, "strategy")
        for name in ("entry_price", "current_price", "quantity",
                     "notional_value", "realized_pnl",
                     "unrealized_pnl", "pnl_percent", "fees",
                     "stop_loss", "take_profit",
                     "max_favorable_excursion",
                     "max_adverse_excursion"):
            _require_opt_decimal(getattr(self, name), name)
        _require_str(self.opened_at, "opened_at")
        _require_str(self.last_reconciled_at,
                     "last_reconciled_at")
        _require_enum(self.reconciliation_state,
                      ReconciliationState,
                      "reconciliation_state")
        _require_str(self.execution_mode, "execution_mode")


@dataclass(frozen=True, slots=True)
class OrderView:
    """Emir yaşam döngüsü görünümü."""

    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Optional[Decimal]
    requested_price: Optional[Decimal]
    average_fill_price: Optional[Decimal]
    filled_quantity: Optional[Decimal]
    remaining_quantity: Optional[Decimal]
    status: str
    created_at: str
    updated_at: str
    strategy: str
    correlation_id: str
    execution_mode: str
    reconciliation_state: ReconciliationState

    def __post_init__(self) -> None:
        for name in ("order_id", "client_order_id", "symbol",
                     "side", "order_type", "status",
                     "created_at", "updated_at", "strategy",
                     "correlation_id", "execution_mode"):
            _require_str(getattr(self, name), name)
        for name in ("quantity", "requested_price",
                     "average_fill_price", "filled_quantity",
                     "remaining_quantity"):
            _require_opt_decimal(getattr(self, name), name)
        _require_enum(self.reconciliation_state,
                      ReconciliationState,
                      "reconciliation_state")


@dataclass(frozen=True, slots=True)
class ProductView:
    """Yönetilen ürün (sembol) görünümü."""

    symbol: str
    market: str
    strategy: str
    automation_state: SymbolAutomationState
    signal_state: str
    execution_mode: str
    direction: str
    entry_eligible: bool
    last_signal_at: str
    last_decision: str
    last_rejection_reason: str
    # Kanonik karar kaynağı alanları (dual_model_runtime) — sinyal
    # görünürlüğü görevi. None = veri mevcut değil (uydurulmaz).
    analyzed_at: str | None = None
    model: str | None = None
    decision_state: str | None = None
    source: str | None = None
    freshness: str | None = None
    confidence: float | int | None = None
    net_reward_risk: float | None = None
    expected_edge: float | None = None
    data_quality: str | None = None

    def __post_init__(self) -> None:
        for name in ("symbol", "market", "strategy",
                     "signal_state", "execution_mode",
                     "direction", "last_signal_at",
                     "last_decision",
                     "last_rejection_reason"):
            _require_str(getattr(self, name), name)
        _require_enum(self.automation_state,
                      SymbolAutomationState, "automation_state")
        _require_bool(self.entry_eligible, "entry_eligible")


@dataclass(frozen=True, slots=True)
class SignalView:
    """Sinyal/karar görünümü — öneri EMİR DEĞİLDİR."""

    signal_time: str
    symbol: str
    strategy: str
    direction: str
    confidence: Optional[Decimal]
    decision: str
    risk_outcome: str
    permission_outcome: str
    rejection_code: str
    execution_result: str
    correlation_id: str
    kind: str = "PROPOSAL"

    def __post_init__(self) -> None:
        for name in ("signal_time", "symbol", "strategy",
                     "direction", "decision", "risk_outcome",
                     "permission_outcome", "rejection_code",
                     "execution_result", "correlation_id"):
            _require_str(getattr(self, name), name)
        _require_opt_decimal(self.confidence, "confidence")
        if self.kind not in ("PROPOSAL", "CANDIDATE",
                             "AUTHORIZED_INTENT", "ORDER",
                             "POSITION"):
            _fail("kind")


@dataclass(frozen=True, slots=True)
class ReconciliationView:
    """Mutabakat satırı — fark alanları Decimal."""

    symbol: str
    last_reconciled_at: str
    ledger_position: Optional[Decimal]
    broker_position: Optional[Decimal]
    difference: Optional[Decimal]
    order_mismatch: bool
    quantity_mismatch: bool
    price_mismatch: bool
    orphan_order: bool
    orphan_position: bool
    state: ReconciliationState
    operator_action: str

    def __post_init__(self) -> None:
        _require_str(self.symbol, "symbol")
        _require_str(self.last_reconciled_at,
                     "last_reconciled_at")
        for name in ("ledger_position", "broker_position",
                     "difference"):
            _require_opt_decimal(getattr(self, name), name)
        for name in ("order_mismatch", "quantity_mismatch",
                     "price_mismatch", "orphan_order",
                     "orphan_position"):
            _require_bool(getattr(self, name), name)
        _require_enum(self.state, ReconciliationState, "state")
        _require_str(self.operator_action, "operator_action")


@dataclass(frozen=True, slots=True)
class RiskLimitsView:
    """Etkin risk limitleri görünümü (salt gösterim)."""

    max_order_notional: Optional[Decimal]
    max_position_notional: Optional[Decimal]
    max_open_positions: Optional[int]
    max_daily_loss: Optional[Decimal]
    max_drawdown: Optional[Decimal]
    max_symbol_exposure: Optional[Decimal]
    cooldown_seconds: Optional[int]
    allowed_markets: Tuple[str, ...]
    allowed_directions: Tuple[str, ...]
    allowed_execution_modes: Tuple[str, ...]
    micro_live_authorized: bool
    authorization_expiry: str
    kill_switch_active: bool

    def __post_init__(self) -> None:
        for name in ("max_order_notional",
                     "max_position_notional", "max_daily_loss",
                     "max_drawdown", "max_symbol_exposure"):
            _require_opt_decimal(getattr(self, name), name)
        for name in ("max_open_positions", "cooldown_seconds"):
            value = getattr(self, name)
            if value is not None:
                _require_int(value, name)
        for name in ("allowed_markets", "allowed_directions",
                     "allowed_execution_modes"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(
                    not isinstance(v, str) or not v
                    for v in value):
                _fail(name)
        _require_bool(self.micro_live_authorized,
                      "micro_live_authorized")
        _require_str(self.authorization_expiry,
                     "authorization_expiry")
        _require_bool(self.kill_switch_active,
                      "kill_switch_active")


@dataclass(frozen=True, slots=True)
class SystemStatusView:
    """Sistem yürütme durumu — bilinmeyen değer UNKNOWN."""

    app_version: str
    execution_mode: str
    automation_state: AutomationState
    kill_switch_state: str
    permission_gate_state: str
    risk_engine_state: str
    broker_state: str
    ledger_state: str
    reconciliation_state: ReconciliationState
    last_sync_at: str
    last_error_code: str
    data_freshness: DataFreshness
    stop_new_entries: bool

    def __post_init__(self) -> None:
        for name in ("app_version", "execution_mode",
                     "kill_switch_state",
                     "permission_gate_state",
                     "risk_engine_state", "broker_state",
                     "ledger_state", "last_sync_at",
                     "last_error_code"):
            _require_str(getattr(self, name), name)
        _require_enum(self.automation_state, AutomationState,
                      "automation_state")
        _require_enum(self.reconciliation_state,
                      ReconciliationState,
                      "reconciliation_state")
        _require_enum(self.data_freshness, DataFreshness,
                      "data_freshness")
        _require_bool(self.stop_new_entries,
                      "stop_new_entries")


@dataclass(frozen=True, slots=True)
class OperationSnapshot:
    """Operasyon Merkezi'nin tek okuma anlık görüntüsü."""

    generated_at: int
    status: SystemStatusView
    products: Tuple[ProductView, ...] = ()
    positions: Tuple[PositionView, ...] = ()
    orders: Tuple[OrderView, ...] = ()
    signals: Tuple[SignalView, ...] = ()
    reconciliation: Tuple[ReconciliationView, ...] = ()
    risk_limits: Optional[RiskLimitsView] = None

    def __post_init__(self) -> None:
        _require_int(self.generated_at, "generated_at")
        _require_enum(self.status, SystemStatusView, "status")
        checks = (
            ("products", ProductView),
            ("positions", PositionView),
            ("orders", OrderView),
            ("signals", SignalView),
            ("reconciliation", ReconciliationView),
        )
        for name, expected in checks:
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(
                    not isinstance(v, expected)
                    for v in value):
                _fail(name)
        if self.risk_limits is not None and not isinstance(
                self.risk_limits, RiskLimitsView):
            _fail("risk_limits")
