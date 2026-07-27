"""Mission 2200 — Agent 01: model + eşleyici + anlık görüntü testleri.

Sözleşmeler:
- Tüm görünüm modelleri frozen + slots; float parasal değer REDDEDİLİR.
- Bilinmeyen değerler UNKNOWN'a düşer; sahte sağlıklı durum üretilmez.
- Enum kümeleri kapalıdır.
"""

from decimal import Decimal

import pytest

import operation_control_mapper as ocm
import operation_control_models as m
import operation_control_snapshot as ocs
from operation_control_errors import OperationControlValidationError


# ── Yardımcı geçerli kurucular ──────────────────────────────────────

def valid_audit(**over):
    base = dict(timestamp=1, actor="op", action="A", target="t",
                previous_state="X", requested_state="Y",
                result="COMPLETED", reason="r",
                correlation_id="c-1")
    base.update(over)
    return m.OperationAuditRecord(**base)


def valid_result(**over):
    base = dict(action_id="a-1", status=m.OperationActionStatus.COMPLETED,
                correlation_id="c-1",
                idempotency_status=m.IdempotencyStatus.NEW,
                audit_recorded=True, lifecycle_status="APPLIED",
                previous_state="X", current_state="Y")
    base.update(over)
    return m.OperationActionResult(**base)


def valid_position(**over):
    base = dict(position_id="BTCUSDT", symbol="BTCUSDT", market="SPOT",
                side="BUY", position_status="OPEN", strategy="s",
                entry_price=Decimal("1"), current_price=Decimal("2"),
                quantity=Decimal("3"), notional_value=None,
                realized_pnl=None, unrealized_pnl=None, pnl_percent=None,
                fees=None, stop_loss=None, take_profit=None,
                max_favorable_excursion=None, max_adverse_excursion=None,
                opened_at="t", last_reconciled_at="UNKNOWN",
                reconciliation_state=m.ReconciliationState.UNKNOWN,
                execution_mode="PAPER")
    base.update(over)
    return m.PositionView(**base)


def valid_order(**over):
    base = dict(order_id="1", client_order_id="c", symbol="BTCUSDT",
                side="BUY", order_type="LIMIT", quantity=Decimal("1"),
                requested_price=None, average_fill_price=None,
                filled_quantity=None, remaining_quantity=None,
                status="FILLED", created_at="t", updated_at="t",
                strategy="s", correlation_id="c-1",
                execution_mode="PAPER",
                reconciliation_state=m.ReconciliationState.UNKNOWN)
    base.update(over)
    return m.OrderView(**base)


def valid_product(**over):
    base = dict(symbol="BTCUSDT", market="SPOT", strategy="s",
                automation_state=m.SymbolAutomationState.DISABLED,
                signal_state="UNKNOWN", execution_mode="PAPER",
                direction="UNKNOWN", entry_eligible=False,
                last_signal_at="UNKNOWN", last_decision="UNKNOWN",
                last_rejection_reason="-")
    base.update(over)
    return m.ProductView(**base)


def valid_signal(**over):
    base = dict(signal_time="t", symbol="BTCUSDT", strategy="s",
                direction="LONG", confidence=None, decision="d",
                risk_outcome="PASS", permission_outcome="PASS",
                rejection_code="-", execution_result="-",
                correlation_id="c-1", kind="PROPOSAL")
    base.update(over)
    return m.SignalView(**base)


def valid_recon(**over):
    base = dict(symbol="BTCUSDT", last_reconciled_at="UNKNOWN",
                ledger_position=None, broker_position=None,
                difference=None, order_mismatch=False,
                quantity_mismatch=False, price_mismatch=False,
                orphan_order=False, orphan_position=False,
                state=m.ReconciliationState.UNKNOWN,
                operator_action="-")
    base.update(over)
    return m.ReconciliationView(**base)


def valid_limits(**over):
    base = dict(max_order_notional=None, max_position_notional=None,
                max_open_positions=None, max_daily_loss=None,
                max_drawdown=None, max_symbol_exposure=None,
                cooldown_seconds=None, allowed_markets=("SPOT",),
                allowed_directions=("LONG",),
                allowed_execution_modes=("PAPER",),
                micro_live_authorized=False,
                authorization_expiry="-", kill_switch_active=False)
    base.update(over)
    return m.RiskLimitsView(**base)


def valid_status(**over):
    base = dict(app_version="1.1.0", execution_mode="PAPER",
                automation_state=m.AutomationState.STOPPED,
                kill_switch_state="INACTIVE",
                permission_gate_state="READY",
                risk_engine_state="READY", broker_state="READY",
                ledger_state="READY",
                reconciliation_state=m.ReconciliationState.UNKNOWN,
                last_sync_at="UNKNOWN", last_error_code="-",
                data_freshness=m.DataFreshness.UNKNOWN,
                stop_new_entries=False)
    base.update(over)
    return m.SystemStatusView(**base)


# ── Enum kapalı kümeleri ────────────────────────────────────────────

class TestEnumClosedSets:
    @pytest.mark.parametrize("value", [
        "STOPPED", "STARTING", "RUNNING", "PAUSING", "PAUSED",
        "STOPPING", "BLOCKED", "ERROR"])
    def test_automation_state_member(self, value):
        assert m.AutomationState(value).value == value

    def test_automation_state_size(self):
        assert len(m.AutomationState) == 8

    @pytest.mark.parametrize("value",
                             ["START", "PAUSE", "RESUME", "STOP"])
    def test_automation_command_member(self, value):
        assert m.AutomationCommand(value).value == value

    @pytest.mark.parametrize("value",
                             ["DISABLED", "ENABLED", "PAUSED", "STOPPED"])
    def test_symbol_state_member(self, value):
        assert m.SymbolAutomationState(value).value == value

    @pytest.mark.parametrize("value",
                             ["ENABLE", "PAUSE", "RESUME", "STOP"])
    def test_symbol_command_member(self, value):
        assert m.SymbolCommand(value).value == value

    @pytest.mark.parametrize("value", [
        "MATCHED", "PENDING", "MISMATCH", "STALE", "ERROR", "UNKNOWN"])
    def test_reconciliation_member(self, value):
        assert m.ReconciliationState(value).value == value

    @pytest.mark.parametrize("value", ["FRESH", "STALE", "UNKNOWN"])
    def test_freshness_member(self, value):
        assert m.DataFreshness(value).value == value

    @pytest.mark.parametrize("value", [
        "COMPLETED", "ACCEPTED", "DENIED", "PARTIAL", "FAILED",
        "UNSUPPORTED"])
    def test_action_status_member(self, value):
        assert m.OperationActionStatus(value).value == value

    @pytest.mark.parametrize("value", ["NEW", "REPLAYED", "CONFLICT"])
    def test_idempotency_member(self, value):
        assert m.IdempotencyStatus(value).value == value

    @pytest.mark.parametrize("enum_type,bad", [
        (m.AutomationState, "LIVE"),
        (m.AutomationCommand, "KILL"),
        (m.SymbolAutomationState, "LIVE"),
        (m.SymbolCommand, "DELETE"),
        (m.ReconciliationState, "HEALTHY"),
        (m.DataFreshness, "LIVE"),
        (m.OperationActionStatus, "OK"),
        (m.IdempotencyStatus, "DUPLICATE"),
    ])
    def test_unknown_member_rejected(self, enum_type, bad):
        with pytest.raises(ValueError):
            enum_type(bad)


# ── Alan doğrulama: zorunlu string alanlar ──────────────────────────

class TestAuditRecordValidation:
    def test_valid(self):
        assert valid_audit().actor == "op"

    @pytest.mark.parametrize("fieldname", [
        "actor", "action", "target", "previous_state",
        "requested_state", "result", "reason", "correlation_id"])
    @pytest.mark.parametrize("bad", ["", None, 5])
    def test_required_str(self, fieldname, bad):
        with pytest.raises(OperationControlValidationError):
            valid_audit(**{fieldname: bad})

    @pytest.mark.parametrize("bad", [-1, "1", 1.5, True])
    def test_timestamp_invalid(self, bad):
        with pytest.raises(OperationControlValidationError):
            valid_audit(timestamp=bad)

    @pytest.mark.parametrize("fieldname",
                             ["idempotency_key", "error_code"])
    def test_optional_fields_none_ok(self, fieldname):
        assert getattr(valid_audit(**{fieldname: None}),
                       fieldname) is None

    @pytest.mark.parametrize("fieldname",
                             ["idempotency_key", "error_code"])
    def test_optional_fields_empty_rejected(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_audit(**{fieldname: ""})

    def test_frozen(self):
        with pytest.raises(Exception):
            valid_audit().actor = "x"


class TestActionResultValidation:
    def test_valid(self):
        assert valid_result().status is \
            m.OperationActionStatus.COMPLETED

    @pytest.mark.parametrize("fieldname", [
        "action_id", "correlation_id", "lifecycle_status",
        "previous_state", "current_state"])
    def test_required_str(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_result(**{fieldname: ""})

    def test_status_enum_required(self):
        with pytest.raises(OperationControlValidationError):
            valid_result(status="COMPLETED")

    def test_idem_enum_required(self):
        with pytest.raises(OperationControlValidationError):
            valid_result(idempotency_status="NEW")

    @pytest.mark.parametrize("bad", ["yes", 1, None])
    def test_audit_recorded_bool(self, bad):
        with pytest.raises(OperationControlValidationError):
            valid_result(audit_recorded=bad)

    @pytest.mark.parametrize("bad", [["a"], ("", ), (1,), "a"])
    def test_detail_codes_tuple_of_str(self, bad):
        with pytest.raises(OperationControlValidationError):
            valid_result(detail_codes=bad)

    def test_detail_codes_ok(self):
        assert valid_result(detail_codes=("A:OK",)).detail_codes == \
            ("A:OK",)

    def test_frozen(self):
        with pytest.raises(Exception):
            valid_result().action_id = "x"


DECIMAL_POSITION_FIELDS = (
    "entry_price", "current_price", "quantity", "notional_value",
    "realized_pnl", "unrealized_pnl", "pnl_percent", "fees",
    "stop_loss", "take_profit", "max_favorable_excursion",
    "max_adverse_excursion")


class TestPositionViewValidation:
    def test_valid(self):
        assert valid_position().symbol == "BTCUSDT"

    @pytest.mark.parametrize("fieldname", DECIMAL_POSITION_FIELDS)
    def test_float_rejected(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_position(**{fieldname: 1.5})

    @pytest.mark.parametrize("fieldname", DECIMAL_POSITION_FIELDS)
    def test_none_allowed(self, fieldname):
        assert getattr(valid_position(**{fieldname: None}),
                       fieldname) is None

    @pytest.mark.parametrize("fieldname", [
        "position_id", "symbol", "market", "side",
        "position_status", "strategy", "opened_at",
        "last_reconciled_at", "execution_mode"])
    def test_required_str(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_position(**{fieldname: ""})

    def test_reconciliation_enum(self):
        with pytest.raises(OperationControlValidationError):
            valid_position(reconciliation_state="MATCHED")

    def test_frozen(self):
        with pytest.raises(Exception):
            valid_position().symbol = "X"


class TestOrderViewValidation:
    @pytest.mark.parametrize("fieldname", [
        "quantity", "requested_price", "average_fill_price",
        "filled_quantity", "remaining_quantity"])
    def test_float_rejected(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_order(**{fieldname: 0.1})

    @pytest.mark.parametrize("fieldname", [
        "order_id", "client_order_id", "symbol", "side",
        "order_type", "status", "created_at", "updated_at",
        "strategy", "correlation_id", "execution_mode"])
    def test_required_str(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_order(**{fieldname: ""})

    def test_valid(self):
        assert valid_order().status == "FILLED"


class TestProductSignalReconLimits:
    def test_product_valid(self):
        assert valid_product().entry_eligible is False

    def test_product_entry_eligible_bool(self):
        with pytest.raises(OperationControlValidationError):
            valid_product(entry_eligible="yes")

    def test_product_state_enum(self):
        with pytest.raises(OperationControlValidationError):
            valid_product(automation_state="ENABLED")

    @pytest.mark.parametrize("kind", [
        "PROPOSAL", "CANDIDATE", "AUTHORIZED_INTENT", "ORDER",
        "POSITION"])
    def test_signal_kind_closed_set(self, kind):
        assert valid_signal(kind=kind).kind == kind

    @pytest.mark.parametrize("kind", ["TRADE", "", "proposal", None])
    def test_signal_kind_rejected(self, kind):
        with pytest.raises(OperationControlValidationError):
            valid_signal(kind=kind)

    def test_signal_confidence_float_rejected(self):
        with pytest.raises(OperationControlValidationError):
            valid_signal(confidence=0.9)

    @pytest.mark.parametrize("fieldname", [
        "order_mismatch", "quantity_mismatch", "price_mismatch",
        "orphan_order", "orphan_position"])
    def test_recon_bool_fields(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_recon(**{fieldname: 1})

    def test_recon_state_enum(self):
        with pytest.raises(OperationControlValidationError):
            valid_recon(state="MATCHED")

    @pytest.mark.parametrize("fieldname", [
        "max_order_notional", "max_position_notional",
        "max_daily_loss", "max_drawdown", "max_symbol_exposure"])
    def test_limits_float_rejected(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_limits(**{fieldname: 2.0})

    @pytest.mark.parametrize("fieldname",
                             ["max_open_positions", "cooldown_seconds"])
    def test_limits_int_fields(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_limits(**{fieldname: -1})

    @pytest.mark.parametrize("fieldname", [
        "allowed_markets", "allowed_directions",
        "allowed_execution_modes"])
    def test_limits_tuple_fields(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_limits(**{fieldname: ["SPOT"]})

    def test_limits_valid(self):
        assert valid_limits().micro_live_authorized is False


class TestSystemStatusAndSnapshot:
    def test_status_valid(self):
        assert valid_status().execution_mode == "PAPER"

    @pytest.mark.parametrize("fieldname", [
        "app_version", "execution_mode", "kill_switch_state",
        "permission_gate_state", "risk_engine_state",
        "broker_state", "ledger_state", "last_sync_at",
        "last_error_code"])
    def test_status_required_str(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            valid_status(**{fieldname: ""})

    def test_status_enums(self):
        with pytest.raises(OperationControlValidationError):
            valid_status(automation_state="RUNNING")
        with pytest.raises(OperationControlValidationError):
            valid_status(data_freshness="FRESH")

    def test_snapshot_valid(self):
        snap = m.OperationSnapshot(generated_at=1,
                                   status=valid_status())
        assert snap.positions == ()

    @pytest.mark.parametrize("fieldname", [
        "products", "positions", "orders", "signals",
        "reconciliation"])
    def test_snapshot_tuple_required(self, fieldname):
        with pytest.raises(OperationControlValidationError):
            m.OperationSnapshot(generated_at=1,
                                status=valid_status(),
                                **{fieldname: [1]})

    def test_snapshot_risk_limits_type(self):
        with pytest.raises(OperationControlValidationError):
            m.OperationSnapshot(generated_at=1,
                                status=valid_status(),
                                risk_limits={"x": 1})


# ── Eşleyici (mapper) ───────────────────────────────────────────────

class TestToDecimal:
    @pytest.mark.parametrize("value,expected", [
        ("1.5", Decimal("1.5")), (Decimal("2"), Decimal("2")),
        (3, Decimal("3")), ("  4 ", Decimal("4")),
        ("-0.1", Decimal("-0.1"))])
    def test_valid(self, value, expected):
        assert ocm.to_decimal(value) == expected

    @pytest.mark.parametrize("value", [
        1.5, True, False, None, "abc", "", "NaN", "Infinity",
        object(), []])
    def test_invalid_returns_none(self, value):
        assert ocm.to_decimal(value) is None


class TestToText:
    @pytest.mark.parametrize("value,expected", [
        ("x", "x"), (" y ", "y"), (5, "5")])
    def test_valid(self, value, expected):
        assert ocm.to_text(value) == expected

    @pytest.mark.parametrize("value", ["", "  ", None, True, 1.5, []])
    def test_unknown(self, value):
        assert ocm.to_text(value) == "UNKNOWN"


class TestMapperFallbacks:
    def test_recon_state_unknown_never_matched(self):
        assert ocm.to_reconciliation_state("weird") is \
            m.ReconciliationState.UNKNOWN
        assert ocm.to_reconciliation_state(None) is \
            m.ReconciliationState.UNKNOWN

    def test_freshness_unknown(self):
        assert ocm.to_freshness("live") is m.DataFreshness.UNKNOWN

    def test_map_position_minimal(self):
        view = ocm.map_position({"symbol": "BTCUSDT"})
        assert view.symbol == "BTCUSDT"
        assert view.entry_price is None
        assert view.reconciliation_state is \
            m.ReconciliationState.UNKNOWN

    def test_map_position_float_dropped(self):
        view = ocm.map_position({"symbol": "BTCUSDT",
                                 "entry_price": 1.23})
        assert view.entry_price is None

    @pytest.mark.parametrize("status,expected", [
        ("FILLED", "FILLED"), ("CANCELLED", "CANCELLED"),
        ("weird", "UNKNOWN"), (None, "UNKNOWN"),
        ("close_requested", "CLOSE_REQUESTED")])
    def test_map_order_lifecycle_closed_set(self, status, expected):
        assert ocm.map_order({"status": status}).status == expected

    def test_map_product_registry_wins(self):
        view = ocm.map_product(
            {"symbol": "BTCUSDT", "automation_state": "ENABLED",
             "entry_eligible": True},
            m.SymbolAutomationState.DISABLED)
        assert view.automation_state is \
            m.SymbolAutomationState.DISABLED
        assert view.entry_eligible is False

    def test_map_product_eligible_requires_enabled(self):
        view = ocm.map_product(
            {"symbol": "BTCUSDT", "entry_eligible": True},
            m.SymbolAutomationState.ENABLED)
        assert view.entry_eligible is True

    def test_map_signal_unknown_kind_falls_to_proposal(self):
        assert ocm.map_signal({"kind": "TRADE"}).kind == "PROPOSAL"

    def test_map_reconciliation_difference_derived(self):
        view = ocm.map_reconciliation(
            {"symbol": "X", "ledger_position": "3",
             "broker_position": "1"})
        assert view.difference == Decimal("2")

    def test_map_risk_limits_defaults_denied(self):
        limits = ocm.map_risk_limits({})
        assert limits.micro_live_authorized is False
        assert limits.kill_switch_active is False


# ── Anlık görüntü ───────────────────────────────────────────────────

class TestSnapshotBuilder:
    def _build(self, raw, now=1000):
        return ocs.build_snapshot(raw, now,
                                  m.AutomationState.STOPPED, False)

    def test_empty_raw(self):
        snap = self._build({})
        assert snap.status.execution_mode == "UNKNOWN"
        assert snap.status.data_freshness is m.DataFreshness.UNKNOWN

    def test_fresh_window(self):
        snap = self._build({"status": {
            "execution_mode": "PAPER", "source_timestamp": 950}})
        assert snap.status.data_freshness is m.DataFreshness.FRESH

    def test_stale_window(self):
        snap = self._build({"status": {
            "execution_mode": "PAPER", "source_timestamp": 100}})
        assert snap.status.data_freshness is m.DataFreshness.STALE

    @pytest.mark.parametrize("ts", [None, "1000", 0, -5, True, 2000])
    def test_bad_source_timestamp_unknown(self, ts):
        snap = self._build({"status": {"source_timestamp": ts}})
        assert snap.status.data_freshness is m.DataFreshness.UNKNOWN

    @pytest.mark.parametrize("mode", ["LIVE", "REAL", "", None, "live"])
    def test_mode_never_falls_to_live(self, mode):
        snap = self._build({"status": {"execution_mode": mode}})
        assert snap.status.execution_mode == "UNKNOWN"

    @pytest.mark.parametrize("mode", ["PAPER", "SHADOW", "MICRO_LIVE"])
    def test_allowed_modes(self, mode):
        snap = self._build({"status": {"execution_mode": mode}})
        assert snap.status.execution_mode == mode

    def test_bad_row_skipped(self):
        snap = self._build({"positions": [
            {"symbol": "BTCUSDT"}, "junk", {"symbol": None}, 5]})
        # None sembollü satır UNKNOWN'a düşer; string/int satır atlanır.
        assert all(isinstance(p, m.PositionView)
                   for p in snap.positions)

    def test_symbol_states_from_registry(self):
        snap = self._build({"products": [{"symbol": "BTCUSDT"}]})
        assert snap.products[0].automation_state is \
            m.SymbolAutomationState.DISABLED

    def test_risk_limits_optional(self):
        assert self._build({}).risk_limits is None
        snap = self._build({"risk_limits": {"max_open_positions": 2}})
        assert snap.risk_limits.max_open_positions == 2

    def test_non_mapping_raw(self):
        snap = ocs.build_snapshot("junk", 10,
                                  m.AutomationState.STOPPED, False)
        assert snap.status.execution_mode == "UNKNOWN"

    def test_negative_generated_at_clamped(self):
        snap = ocs.build_snapshot({}, -5,
                                  m.AutomationState.STOPPED, False)
        assert snap.generated_at == 0
