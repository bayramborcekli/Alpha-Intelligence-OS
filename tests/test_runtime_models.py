"""Mission 2100 — Agent 02: Çalışma zamanı model testleri.

Kapsam: enum kapanışı, değişmezlik (frozen+slots), doğrulama
(negatif/NaN/Infinity/bool/boş referans/tekrar), hashlenebilirlik,
serileştirme (asdict), Decimal-yalnız kuralı, hata kodları ve
kamusal ihracat sözleşmeleri.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from dataclasses import FrozenInstanceError, asdict, fields
from decimal import Decimal

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import runtime_enums
import runtime_errors
import runtime_models
from execution_enums import (OrderSide, OrderState, OrderType,
                             PositionSide)
from runtime_enums import (AuditSeverity, AuthorizationState,
                           HeartbeatStatus, RuntimeEnvironment,
                           RuntimeState)
from runtime_errors import (RuntimeConfigurationError,
                            RuntimeContractError,
                            RuntimeDomainError)
from runtime_models import (RuntimeAccountSnapshot,
                            RuntimeAuditRecord,
                            RuntimeAuthorization, RuntimeBalance,
                            RuntimeConfiguration,
                            RuntimeExecutionRecord,
                            RuntimeHeartbeat, RuntimeIdentity,
                            RuntimeLimits, RuntimeOrderIntent,
                            RuntimeOrderRecord, RuntimePosition,
                            RuntimeSession, RuntimeStatistics)

D = Decimal


def _balance(**over):
    base = dict(asset="USDT", free=D("100"), locked=D("5"))
    base.update(over)
    return RuntimeBalance(**base)


def _identity(**over):
    base = dict(session_reference="ses-1",
                account_reference="acc-1",
                environment=RuntimeEnvironment.PAPER)
    base.update(over)
    return RuntimeIdentity(**base)


def _session(**over):
    base = dict(identity=_identity(),
                state=RuntimeState.RUNNING)
    base.update(over)
    return RuntimeSession(**base)


def _position(**over):
    base = dict(symbol="BTCUSDT", side=PositionSide.LONG,
                quantity=D("0.5"), entry_price=D("50000"))
    base.update(over)
    return RuntimePosition(**base)


def _snapshot(**over):
    base = dict(account_reference="acc-1",
                balances=(_balance(),),
                positions=(_position(),))
    base.update(over)
    return RuntimeAccountSnapshot(**base)


def _intent(**over):
    base = dict(intent_reference="int-1", symbol="BTCUSDT",
                side=OrderSide.BUY, order_type=OrderType.LIMIT,
                quantity=D("1"), limit_price=D("50000"))
    base.update(over)
    return RuntimeOrderIntent(**base)


def _order(**over):
    base = dict(order_reference="ord-1",
                intent_reference="int-1",
                state=OrderState.CREATED)
    base.update(over)
    return RuntimeOrderRecord(**base)


def _execution(**over):
    base = dict(execution_reference="exe-1",
                order_reference="ord-1", quantity=D("1"),
                price=D("50000"), fee=D("0.1"),
                fee_asset="USDT")
    base.update(over)
    return RuntimeExecutionRecord(**base)


def _statistics(**over):
    base = dict(session_reference="ses-1", orders_submitted=3,
                orders_filled=2, orders_rejected=1,
                gross_notional=D("100"),
                realized_pnl=D("-5"))
    base.update(over)
    return RuntimeStatistics(**base)


def _heartbeat(**over):
    base = dict(session_reference="ses-1",
                status=HeartbeatStatus.OK,
                heartbeat_reference="hb-1")
    base.update(over)
    return RuntimeHeartbeat(**base)


def _limits(**over):
    base = dict(maximum_order_notional=D("100"),
                maximum_daily_notional=D("1000"),
                maximum_open_orders=5,
                maximum_position_quantity=D("2"))
    base.update(over)
    return RuntimeLimits(**base)


def _configuration(**over):
    base = dict(configuration_reference="cfg-1",
                environment=RuntimeEnvironment.PAPER,
                limits=_limits(),
                policy_reference="pol-1")
    base.update(over)
    return RuntimeConfiguration(**base)


def _authorization(**over):
    base = dict(authorization_reference="auth-1",
                state=AuthorizationState.PENDING,
                granted_by_reference="owner-1",
                scope_reference="scope-1")
    base.update(over)
    return RuntimeAuthorization(**base)


def _audit(**over):
    base = dict(audit_reference="aud-1",
                severity=AuditSeverity.INFO,
                event_code="SESSION_STARTED",
                subject_reference="ses-1")
    base.update(over)
    return RuntimeAuditRecord(**base)


_FACTORIES = {
    "identity": _identity, "session": _session,
    "balance": _balance, "position": _position,
    "snapshot": _snapshot, "intent": _intent,
    "order": _order, "execution": _execution,
    "statistics": _statistics, "heartbeat": _heartbeat,
    "limits": _limits, "configuration": _configuration,
    "authorization": _authorization, "audit": _audit,
}

_MODEL_CLASSES = {
    "identity": RuntimeIdentity, "session": RuntimeSession,
    "balance": RuntimeBalance, "position": RuntimePosition,
    "snapshot": RuntimeAccountSnapshot,
    "intent": RuntimeOrderIntent, "order": RuntimeOrderRecord,
    "execution": RuntimeExecutionRecord,
    "statistics": RuntimeStatistics,
    "heartbeat": RuntimeHeartbeat, "limits": RuntimeLimits,
    "configuration": RuntimeConfiguration,
    "authorization": RuntimeAuthorization,
    "audit": RuntimeAuditRecord,
}

_ALL_KEYS = sorted(_FACTORIES)


# ── Hata hiyerarşisi ────────────────────────────────────────────────

class TestErrors:
    def test_root_is_exception(self):
        assert issubclass(RuntimeDomainError, Exception)

    def test_contract_error_inherits_root(self):
        assert issubclass(RuntimeContractError, RuntimeDomainError)

    def test_configuration_error_inherits_root(self):
        assert issubclass(RuntimeConfigurationError,
                          RuntimeDomainError)

    def test_contract_and_configuration_are_siblings(self):
        assert not issubclass(RuntimeContractError,
                              RuntimeConfigurationError)
        assert not issubclass(RuntimeConfigurationError,
                              RuntimeContractError)

    def test_error_exports_exact(self):
        assert runtime_errors.__all__ == [
            "RuntimeDomainError", "RuntimeContractError",
            "RuntimeConfigurationError"]


# ── Enum kapanışı ───────────────────────────────────────────────────

_ENUM_SPECS = [
    (RuntimeState, ["STOPPED", "STARTING", "RUNNING", "PAUSED",
                    "STOPPING", "FAILED"]),
    (RuntimeEnvironment, ["PAPER", "SHADOW", "MICRO_LIVE"]),
    (HeartbeatStatus, ["OK", "WARNING", "ERROR"]),
    (AuthorizationState, ["NONE", "PENDING", "APPROVED",
                          "DENIED", "EXPIRED"]),
    (AuditSeverity, ["INFO", "WARNING", "ERROR", "CRITICAL"]),
]


class TestEnumClosure:
    @pytest.mark.parametrize("enum_type,members", _ENUM_SPECS)
    def test_members_exact(self, enum_type, members):
        assert [m.name for m in enum_type] == members

    @pytest.mark.parametrize("enum_type,members", _ENUM_SPECS)
    def test_values_equal_names(self, enum_type, members):
        for member in enum_type:
            assert member.value == member.name

    @pytest.mark.parametrize("enum_type,members", _ENUM_SPECS)
    def test_closed_no_aliases(self, enum_type, members):
        assert len(enum_type.__members__) == len(members)

    @pytest.mark.parametrize("enum_type,members", _ENUM_SPECS)
    def test_unknown_value_rejected(self, enum_type, members):
        with pytest.raises(ValueError):
            enum_type("UNLIMITED_LIVE")

    @pytest.mark.parametrize("enum_type,members", _ENUM_SPECS)
    def test_members_hashable(self, enum_type, members):
        assert len({hash(m) for m in enum_type}) == len(members)

    def test_no_unbounded_live_environment(self):
        assert set(RuntimeEnvironment.__members__) == {
            "PAPER", "SHADOW", "MICRO_LIVE"}

    def test_enum_exports_exact(self):
        assert runtime_enums.__all__ == [
            "RuntimeState", "RuntimeEnvironment",
            "HeartbeatStatus", "AuthorizationState",
            "AuditSeverity"]


# ── Geçerli kurulum ─────────────────────────────────────────────────

class TestValidConstruction:
    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_factory_builds(self, key):
        instance = _FACTORIES[key]()
        assert isinstance(instance, _MODEL_CLASSES[key])

    def test_balance_total(self):
        assert _balance(free=D("2"), locked=D("3")).total == D("5")

    def test_optional_defaults_none(self):
        limits = RuntimeLimits()
        assert limits.maximum_order_notional is None
        assert limits.maximum_daily_notional is None
        assert limits.maximum_open_orders is None
        assert limits.maximum_position_quantity is None

    def test_order_default_filled_zero(self):
        assert _order().filled_quantity == D("0")

    def test_snapshot_empty_collections_valid(self):
        snap = RuntimeAccountSnapshot(account_reference="acc-1")
        assert snap.balances == ()
        assert snap.positions == ()

    def test_statistics_defaults_zero(self):
        stats = RuntimeStatistics(session_reference="ses-1")
        assert (stats.orders_submitted, stats.orders_filled,
                stats.orders_rejected) == (0, 0, 0)

    def test_negative_realized_pnl_allowed(self):
        assert _statistics(realized_pnl=D("-10")).realized_pnl \
            == D("-10")

    def test_configuration_without_limits(self):
        assert _configuration(limits=None).limits is None


# ── Değişmezlik ─────────────────────────────────────────────────────

class TestImmutability:
    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_frozen(self, key):
        instance = _FACTORIES[key]()
        field_name = fields(instance)[0].name
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field_name, "mutated")

    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_slots_no_dict(self, key):
        assert not hasattr(_FACTORIES[key](), "__dict__")

    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_new_attribute_rejected(self, key):
        with pytest.raises((AttributeError, TypeError,
                            FrozenInstanceError)):
            setattr(_FACTORIES[key](), "sneaky", 1)

    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_dataclass_params(self, key):
        params = _MODEL_CLASSES[key].__dataclass_params__
        assert params.frozen is True


# ── Hashlenebilirlik ve eşitlik ─────────────────────────────────────

class TestHashEquality:
    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_hashable(self, key):
        assert isinstance(hash(_FACTORIES[key]()), int)

    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_equal_instances_equal_hash(self, key):
        first, second = _FACTORIES[key](), _FACTORIES[key]()
        assert first == second
        assert hash(first) == hash(second)

    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_usable_in_set(self, key):
        assert len({_FACTORIES[key](), _FACTORIES[key]()}) == 1


# ── Serileştirme ────────────────────────────────────────────────────

class TestSerialization:
    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_asdict_keys_match_fields(self, key):
        instance = _FACTORIES[key]()
        data = asdict(instance)
        assert set(data) == {f.name for f in fields(instance)}

    def test_asdict_preserves_decimal(self):
        data = asdict(_balance())
        assert isinstance(data["free"], Decimal)
        assert isinstance(data["locked"], Decimal)

    def test_asdict_nested_snapshot(self):
        data = asdict(_snapshot())
        assert data["balances"][0]["asset"] == "USDT"
        assert data["positions"][0]["symbol"] == "BTCUSDT"

    def test_asdict_roundtrip_reconstruction(self):
        data = asdict(_limits())
        assert RuntimeLimits(**data) == _limits()

    @pytest.mark.parametrize("key", _ALL_KEYS)
    def test_no_float_after_asdict(self, key):
        def _scan(value):
            if isinstance(value, dict):
                for item in value.values():
                    _scan(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    _scan(item)
            else:
                assert not isinstance(value, float)
        _scan(asdict(_FACTORIES[key]()))


# ── Doğrulama reddi ─────────────────────────────────────────────────

_NAN = Decimal("NaN")
_INF = Decimal("Infinity")

_REJECTIONS = [
    # boş / geçersiz referanslar
    ("identity", dict(session_reference="")),
    ("identity", dict(session_reference="   ")),
    ("identity", dict(account_reference="")),
    ("identity", dict(session_reference=None)),
    ("identity", dict(session_reference=7)),
    ("identity", dict(environment="PAPER")),
    ("identity", dict(environment=None)),
    ("identity", dict(logical_sequence=-1)),
    ("identity", dict(logical_sequence=True)),
    ("identity", dict(logical_sequence=D("1"))),
    ("session", dict(identity=None)),
    ("session", dict(identity="ses-1")),
    ("session", dict(state="RUNNING")),
    ("session", dict(state=None)),
    ("session", dict(configuration_reference="")),
    ("session", dict(started_reference=" ")),
    ("session", dict(logical_sequence=-3)),
    # bakiye
    ("balance", dict(asset="")),
    ("balance", dict(asset=None)),
    ("balance", dict(free=D("-1"))),
    ("balance", dict(locked=D("-0.01"))),
    ("balance", dict(free=_NAN)),
    ("balance", dict(free=_INF)),
    ("balance", dict(locked=_NAN)),
    ("balance", dict(free=1)),
    ("balance", dict(free=True)),
    ("balance", dict(free="100")),
    ("balance", dict(free=None)),
    ("balance", dict(locked=None)),
    # pozisyon
    ("position", dict(symbol="")),
    ("position", dict(side="LONG")),
    ("position", dict(side=None)),
    ("position", dict(quantity=D("-1"))),
    ("position", dict(quantity=_NAN)),
    ("position", dict(quantity=_INF)),
    ("position", dict(quantity=None)),
    ("position", dict(quantity=True)),
    ("position", dict(entry_price=D("0"))),
    ("position", dict(entry_price=D("-5"))),
    ("position", dict(entry_price=_NAN)),
    ("position", dict(position_reference="")),
    # anlık görüntü
    ("snapshot", dict(account_reference="")),
    ("snapshot", dict(balances=[_balance()])),
    ("snapshot", dict(balances=(_balance(), _balance()))),
    ("snapshot", dict(balances=("USDT",))),
    ("snapshot", dict(positions=[_position()])),
    ("snapshot", dict(positions=(_position(), _position()))),
    ("snapshot", dict(positions=(1,))),
    ("snapshot", dict(snapshot_reference="")),
    ("snapshot", dict(logical_sequence=-1)),
    # emir niyeti
    ("intent", dict(intent_reference="")),
    ("intent", dict(symbol="")),
    ("intent", dict(side="BUY")),
    ("intent", dict(side=None)),
    ("intent", dict(order_type="LIMIT")),
    ("intent", dict(order_type=None)),
    ("intent", dict(quantity=D("0"))),
    ("intent", dict(quantity=D("-1"))),
    ("intent", dict(quantity=_NAN)),
    ("intent", dict(quantity=_INF)),
    ("intent", dict(quantity=None)),
    ("intent", dict(quantity=1)),
    ("intent", dict(quantity=True)),
    ("intent", dict(limit_price=D("0"))),
    ("intent", dict(limit_price=D("-1"))),
    ("intent", dict(limit_price=_NAN)),
    # emir kaydı
    ("order", dict(order_reference="")),
    ("order", dict(intent_reference="")),
    ("order", dict(state="FILLED")),
    ("order", dict(state=None)),
    ("order", dict(filled_quantity=D("-1"))),
    ("order", dict(filled_quantity=_NAN)),
    ("order", dict(filled_quantity=_INF)),
    ("order", dict(filled_quantity=None)),
    ("order", dict(filled_quantity=True)),
    # gerçekleşme kaydı
    ("execution", dict(execution_reference="")),
    ("execution", dict(order_reference="")),
    ("execution", dict(quantity=D("0"))),
    ("execution", dict(quantity=D("-1"))),
    ("execution", dict(price=D("0"))),
    ("execution", dict(price=D("-1"))),
    ("execution", dict(price=_NAN)),
    ("execution", dict(price=_INF)),
    ("execution", dict(fee=D("-1"))),
    ("execution", dict(fee=_NAN)),
    ("execution", dict(fee=1)),
    ("execution", dict(fee_asset="")),
    # istatistik
    ("statistics", dict(session_reference="")),
    ("statistics", dict(orders_submitted=-1)),
    ("statistics", dict(orders_submitted=True)),
    ("statistics", dict(orders_submitted=None)),
    ("statistics", dict(orders_filled=-2)),
    ("statistics", dict(orders_rejected=D("1"))),
    ("statistics", dict(gross_notional=D("-1"))),
    ("statistics", dict(gross_notional=_NAN)),
    ("statistics", dict(realized_pnl=_NAN)),
    ("statistics", dict(realized_pnl=_INF)),
    ("statistics", dict(realized_pnl=True)),
    ("statistics", dict(realized_pnl=1)),
    # kalp atışı
    ("heartbeat", dict(session_reference="")),
    ("heartbeat", dict(status="OK")),
    ("heartbeat", dict(status=None)),
    ("heartbeat", dict(heartbeat_reference="")),
    ("heartbeat", dict(detail_code=" ")),
    ("heartbeat", dict(logical_sequence=-1)),
    # limitler
    ("limits", dict(maximum_order_notional=D("-1"))),
    ("limits", dict(maximum_order_notional=_NAN)),
    ("limits", dict(maximum_order_notional=_INF)),
    ("limits", dict(maximum_order_notional=1)),
    ("limits", dict(maximum_order_notional=True)),
    ("limits", dict(maximum_daily_notional=D("-1"))),
    ("limits", dict(maximum_open_orders=-1)),
    ("limits", dict(maximum_open_orders=True)),
    ("limits", dict(maximum_position_quantity=D("-1"))),
    # yapılandırma
    ("configuration", dict(configuration_reference="")),
    ("configuration", dict(environment="PAPER")),
    ("configuration", dict(environment=None)),
    ("configuration", dict(limits="limits")),
    ("configuration", dict(limits=7)),
    ("configuration", dict(policy_reference="")),
    # yetkilendirme
    ("authorization", dict(authorization_reference="")),
    ("authorization", dict(authorization_reference="   ")),
    ("authorization", dict(authorization_reference=None)),
    ("authorization", dict(state="APPROVED")),
    ("authorization", dict(state=None)),
    ("authorization", dict(granted_by_reference="")),
    ("authorization", dict(scope_reference=" ")),
    # denetim
    ("audit", dict(audit_reference="")),
    ("audit", dict(severity="INFO")),
    ("audit", dict(severity=None)),
    ("audit", dict(event_code="")),
    ("audit", dict(event_code=None)),
    ("audit", dict(subject_reference="")),
    ("audit", dict(logical_sequence=-1)),
]


class TestValidationRejection:
    @pytest.mark.parametrize("key,overrides", _REJECTIONS)
    def test_rejected(self, key, overrides):
        with pytest.raises(RuntimeContractError):
            _FACTORIES[key](**overrides)

    @pytest.mark.parametrize("key,overrides", _REJECTIONS)
    def test_sterile_error_code(self, key, overrides):
        with pytest.raises(RuntimeContractError) as excinfo:
            _FACTORIES[key](**overrides)
        message = str(excinfo.value)
        assert message.startswith("INVALID_RUNTIME_MODEL_FIELD:")
        assert "Traceback" not in message

    def test_duplicate_balance_asset_rejected(self):
        with pytest.raises(RuntimeContractError):
            _snapshot(balances=(_balance(asset="BTC"),
                                _balance(asset="BTC")))

    def test_duplicate_position_symbol_rejected(self):
        with pytest.raises(RuntimeContractError):
            _snapshot(positions=(_position(), _position()))

    def test_duplicate_position_logical_sequence_rejected(self):
        with pytest.raises(RuntimeContractError):
            _snapshot(positions=(
                _position(symbol="BTCUSDT", logical_sequence=1),
                _position(symbol="ETHUSDT", logical_sequence=1)))

    def test_distinct_position_logical_sequences_accepted(self):
        snap = _snapshot(positions=(
            _position(symbol="BTCUSDT", logical_sequence=1),
            _position(symbol="ETHUSDT", logical_sequence=2)))
        assert len(snap.positions) == 2

    def test_none_position_sequences_not_duplicates(self):
        snap = _snapshot(positions=(
            _position(symbol="BTCUSDT"),
            _position(symbol="ETHUSDT")))
        assert len(snap.positions) == 2

    def test_approved_authorization_without_grantor_rejected(self):
        with pytest.raises(RuntimeContractError):
            _authorization(state=AuthorizationState.APPROVED,
                           granted_by_reference=None)

    def test_approved_authorization_with_grantor_accepted(self):
        auth = _authorization(state=AuthorizationState.APPROVED)
        assert auth.granted_by_reference == "owner-1"

    def test_non_approved_states_allow_missing_grantor(self):
        for state in (AuthorizationState.NONE,
                      AuthorizationState.PENDING,
                      AuthorizationState.DENIED,
                      AuthorizationState.EXPIRED):
            auth = _authorization(state=state,
                                  granted_by_reference=None)
            assert auth.state is state

    def test_distinct_assets_accepted(self):
        snap = _snapshot(balances=(_balance(asset="BTC"),
                                   _balance(asset="ETH")))
        assert len(snap.balances) == 2


# ── Decimal kuralı ──────────────────────────────────────────────────

class TestDecimalOnly:
    @pytest.mark.parametrize("key,field_name", [
        ("balance", "free"), ("balance", "locked"),
        ("position", "quantity"), ("intent", "quantity"),
        ("order", "filled_quantity"), ("execution", "quantity"),
        ("execution", "price")])
    def test_float_rejected(self, key, field_name):
        with pytest.raises(RuntimeContractError):
            _FACTORIES[key](**{field_name: 1.5})

    @pytest.mark.parametrize("key,field_name", [
        ("limits", "maximum_order_notional"),
        ("limits", "maximum_daily_notional"),
        ("limits", "maximum_position_quantity"),
        ("statistics", "gross_notional"),
        ("statistics", "realized_pnl"),
        ("execution", "fee"),
        ("position", "entry_price"),
        ("intent", "limit_price")])
    def test_optional_float_rejected(self, key, field_name):
        with pytest.raises(RuntimeContractError):
            _FACTORIES[key](**{field_name: 0.5})

    def test_decimal_precision_preserved(self):
        balance = _balance(free=D("0.00000001"))
        assert balance.free == D("0.00000001")


# ── Kamusal ihracat ─────────────────────────────────────────────────

class TestPublicExports:
    def test_models_exports_exact(self):
        assert runtime_models.__all__ == [
            "RuntimeSession", "RuntimeIdentity",
            "RuntimeAccountSnapshot", "RuntimeBalance",
            "RuntimePosition", "RuntimeOrderIntent",
            "RuntimeOrderRecord", "RuntimeExecutionRecord",
            "RuntimeStatistics", "RuntimeHeartbeat",
            "RuntimeConfiguration", "RuntimeLimits",
            "RuntimeAuthorization", "RuntimeAuditRecord"]

    def test_fourteen_models(self):
        assert len(runtime_models.__all__) == 14

    @pytest.mark.parametrize("name", [
        "RuntimeSession", "RuntimeIdentity",
        "RuntimeAccountSnapshot", "RuntimeBalance",
        "RuntimePosition", "RuntimeOrderIntent",
        "RuntimeOrderRecord", "RuntimeExecutionRecord",
        "RuntimeStatistics", "RuntimeHeartbeat",
        "RuntimeConfiguration", "RuntimeLimits",
        "RuntimeAuthorization", "RuntimeAuditRecord"])
    def test_export_is_frozen_dataclass(self, name):
        model = getattr(runtime_models, name)
        assert dataclasses.is_dataclass(model)
        assert model.__dataclass_params__.frozen is True

    @pytest.mark.parametrize("name", [
        "RuntimeSession", "RuntimeIdentity",
        "RuntimeAccountSnapshot", "RuntimeBalance",
        "RuntimePosition", "RuntimeOrderIntent",
        "RuntimeOrderRecord", "RuntimeExecutionRecord",
        "RuntimeStatistics", "RuntimeHeartbeat",
        "RuntimeConfiguration", "RuntimeLimits",
        "RuntimeAuthorization", "RuntimeAuditRecord"])
    def test_export_uses_slots(self, name):
        model = getattr(runtime_models, name)
        assert "__slots__" in model.__dict__

    def test_no_generated_identifiers(self):
        # Kimlik üretimi yok: tüm referanslar çağıran-sahipli
        identity = _identity()
        assert identity.session_reference == "ses-1"
        assert identity.account_reference == "acc-1"

    def test_no_behavior_methods(self):
        # Modeller davranış taşımaz: execute/submit/fill yok
        for name in runtime_models.__all__:
            model = getattr(runtime_models, name)
            for forbidden in ("execute", "submit", "fill",
                              "cancel", "send", "place",
                              "reconcile", "settle"):
                assert not hasattr(model, forbidden)
