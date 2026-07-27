"""Mission 2000 — Agent 02 yürütme alan modeli testleri.

Değişmez modeller, dondurulmuş enumlar, durum makinesi (onaylı ve
yasak tüm geçişler), determinizm, hashlenebilirlik, Decimal-only
politikası, meta veri taşıma, Binance normalizasyon tablosu, eşitlik,
serileştirme uyumluluğu, mutable varsayılan yokluğu ve yasak import
yokluğu doğrulanır.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from decimal import Decimal

import pytest

import execution_enums
import execution_models
import execution_state_machine
from execution_enums import (
    ExecutionStatus, OrderSide, OrderState, OrderType, PositionSide,
    TimeInForce)
from execution_models import (
    ExecutionMetadata, ExecutionRequest, ExecutionResult, Fill, Order,
    Position, ValidationResult)
from execution_state_machine import validate_transition
from execution_state_machine import (
    _APPROVED_TRANSITIONS as APPROVED_TRANSITIONS,
    _BINANCE_STATE_NORMALIZATION as BINANCE_STATE_NORMALIZATION,
    _TERMINAL_STATES as TERMINAL_STATES)

D = Decimal


def _request(**overrides):
    base = dict(symbol="BTCUSDT", side=OrderSide.BUY,
                order_type=OrderType.LIMIT, quantity=D("0.5"),
                time_in_force=TimeInForce.GTC, price=D("50000.00"))
    base.update(overrides)
    return ExecutionRequest(**base)


def _order(**overrides):
    base = dict(symbol="BTCUSDT", side=OrderSide.SELL,
                order_type=OrderType.MARKET, quantity=D("1"),
                time_in_force=TimeInForce.IOC,
                state=OrderState.CREATED)
    base.update(overrides)
    return Order(**base)


def _fill(**overrides):
    base = dict(symbol="BTCUSDT", side=OrderSide.BUY,
                quantity=D("0.25"), price=D("49999.99"))
    base.update(overrides)
    return Fill(**base)


MODELS = (ExecutionRequest, ExecutionResult, Order, Position, Fill,
          ExecutionMetadata, ValidationResult)

SAMPLES = {
    ExecutionRequest: _request,
    Order: _order,
    Fill: _fill,
    Position: lambda: Position(symbol="ETHUSDT",
                               side=PositionSide.LONG,
                               quantity=D("2"),
                               entry_price=D("3000")),
    ExecutionMetadata: lambda: ExecutionMetadata(
        execution_id="x", requested_at="t1", processed_at="t2",
        correlation_id="c"),
    ValidationResult: lambda: ValidationResult(approved=True),
    ExecutionResult: lambda: ExecutionResult(
        status=ExecutionStatus.SUCCESS, order=_order(),
        fills=(_fill(),), metadata=ExecutionMetadata()),
}


# ── Enumlar ──────────────────────────────────────────────────────────

class TestEnums:
    @pytest.mark.parametrize("enum_cls,members", [
        (OrderSide, ("BUY", "SELL")),
        (OrderType, ("MARKET", "LIMIT", "STOP_LIMIT", "STOP_MARKET",
                     "TAKE_PROFIT")),
        (TimeInForce, ("GTC", "IOC", "FOK")),
        (OrderState, ("CREATED", "VALIDATED", "SUBMITTED",
                      "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED",
                      "CANCELLED", "REJECTED", "EXPIRED")),
        (PositionSide, ("LONG", "SHORT", "FLAT")),
        (ExecutionStatus, ("SUCCESS", "FAILED", "REJECTED",
                           "PARTIAL")),
    ])
    def test_enum_members_frozen(self, enum_cls, members):
        assert tuple(m.name for m in enum_cls) == members

    @pytest.mark.parametrize("enum_cls", [
        OrderSide, OrderType, TimeInForce, OrderState, PositionSide,
        ExecutionStatus])
    def test_enum_values_match_names(self, enum_cls):
        for member in enum_cls:
            assert member.value == member.name

    @pytest.mark.parametrize("enum_cls", [
        OrderSide, OrderType, TimeInForce, OrderState, PositionSide,
        ExecutionStatus])
    def test_enum_members_immutable(self, enum_cls):
        member = next(iter(enum_cls))
        with pytest.raises((AttributeError, TypeError)):
            member.value = "X"  # type: ignore[misc]
        with pytest.raises((AttributeError, TypeError)):
            setattr(enum_cls, member.name, "X")


# ── Değişmezlik ──────────────────────────────────────────────────────

class TestImmutability:
    @pytest.mark.parametrize("model", MODELS)
    def test_frozen(self, model):
        instance = SAMPLES[model]()
        field = dataclasses.fields(model)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, field, None)

    @pytest.mark.parametrize("model", MODELS)
    def test_slots(self, model):
        instance = SAMPLES[model]()
        assert not hasattr(instance, "__dict__")
        with pytest.raises((AttributeError, TypeError)):
            instance.new_attribute = 1  # type: ignore[attr-defined]

    @pytest.mark.parametrize("model", MODELS)
    def test_dataclass_params(self, model):
        params = model.__dataclass_params__
        assert params.frozen is True

    @pytest.mark.parametrize("model", MODELS)
    def test_no_mutable_defaults(self, model):
        for field in dataclasses.fields(model):
            if field.default is not dataclasses.MISSING:
                assert not isinstance(field.default,
                                      (list, dict, set))
            assert field.default_factory is dataclasses.MISSING


# ── Hashlenebilirlik ve eşitlik ──────────────────────────────────────

class TestHashEquality:
    @pytest.mark.parametrize("model", MODELS)
    def test_hashable(self, model):
        instance = SAMPLES[model]()
        assert isinstance(hash(instance), int)
        assert instance in {instance}

    @pytest.mark.parametrize("model", MODELS)
    def test_equality_by_value(self, model):
        assert SAMPLES[model]() == SAMPLES[model]()
        assert hash(SAMPLES[model]()) == hash(SAMPLES[model]())

    def test_inequality(self):
        assert _request() != _request(quantity=D("0.6"))
        assert _order() != _order(state=OrderState.VALIDATED)


# ── Decimal-only politikası ──────────────────────────────────────────

class TestDecimalOnly:
    @pytest.mark.parametrize("bad", [0.5, 1, True, "0.5", None])
    def test_request_quantity_rejects_non_decimal(self, bad):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            _request(quantity=bad)

    @pytest.mark.parametrize("bad", [0.5, 1, True, "50000"])
    def test_request_price_rejects_non_decimal(self, bad):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            _request(price=bad)

    @pytest.mark.parametrize("bad", [0.5, 2, True])
    def test_order_fields_reject_non_decimal(self, bad):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            _order(quantity=bad)
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            _order(filled_quantity=bad)

    @pytest.mark.parametrize("bad", [0.5, 3, True])
    def test_fill_and_position_reject_non_decimal(self, bad):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            _fill(price=bad)
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            Position(symbol="X", side=PositionSide.FLAT, quantity=bad)

    def test_null_preserved_for_unknown(self):
        order = _order()
        assert order.price is None
        assert order.filled_quantity is None
        assert order.order_id is None

    def test_no_float_literals_in_production_modules(self):
        for module in (execution_enums, execution_models,
                       execution_state_machine):
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node, ast.Constant):
                    assert not isinstance(node.value, float)


# ── Tür doğrulama (sterile) ──────────────────────────────────────────

class TestTypeValidation:
    def test_request_rejects_wrong_enums(self):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            _request(side="BUY")
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            _request(order_type="LIMIT")
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            _request(time_in_force="GTC")

    def test_empty_symbol_rejected(self):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            _request(symbol="")

    def test_metadata_rejects_non_string(self):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            ExecutionMetadata(execution_id=1)

    def test_result_rejects_non_tuple_fills(self):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            ExecutionResult(status=ExecutionStatus.SUCCESS,
                            fills=[_fill()])

    def test_result_rejects_non_fill_members(self):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            ExecutionResult(status=ExecutionStatus.SUCCESS,
                            fills=(1,))

    def test_validation_result_rejects_non_bool(self):
        with pytest.raises(ValueError, match="INVALID_MODEL_FIELD"):
            ValidationResult(approved="yes")

    def test_error_message_is_sterile(self):
        try:
            _request(quantity=0.5)
        except ValueError as exc:
            assert str(exc) == "INVALID_MODEL_FIELD"


# ── Durum makinesi: onaylı geçişler ──────────────────────────────────

APPROVED = [
    (OrderState.CREATED, OrderState.VALIDATED),
    (OrderState.VALIDATED, OrderState.SUBMITTED),
    (OrderState.VALIDATED, OrderState.REJECTED),
    (OrderState.SUBMITTED, OrderState.ACKNOWLEDGED),
    (OrderState.SUBMITTED, OrderState.CANCELLED),
    (OrderState.SUBMITTED, OrderState.EXPIRED),
    (OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED),
    (OrderState.ACKNOWLEDGED, OrderState.FILLED),
    (OrderState.PARTIALLY_FILLED, OrderState.FILLED),
    (OrderState.PARTIALLY_FILLED, OrderState.CANCELLED),
]

FORBIDDEN = [
    (OrderState.FILLED, OrderState.CREATED),
    (OrderState.FILLED, OrderState.SUBMITTED),
    (OrderState.FILLED, OrderState.VALIDATED),
    (OrderState.CANCELLED, OrderState.FILLED),
    (OrderState.REJECTED, OrderState.SUBMITTED),
    (OrderState.REJECTED, OrderState.FILLED),
    (OrderState.EXPIRED, OrderState.FILLED),
    (OrderState.PARTIALLY_FILLED, OrderState.CREATED),
    (OrderState.PARTIALLY_FILLED, OrderState.VALIDATED),
]


class TestStateMachine:
    @pytest.mark.parametrize("current,target", APPROVED)
    def test_approved_transition(self, current, target):
        assert validate_transition(current, target) is True

    @pytest.mark.parametrize("current,target", FORBIDDEN)
    def test_forbidden_transition(self, current, target):
        assert validate_transition(current, target) is False

    def test_exhaustive_closure(self):
        # Onaylı liste dışındaki TÜM ikililer yasaktır
        approved = set(APPROVED)
        for current in OrderState:
            for target in OrderState:
                expected = (current, target) in approved
                assert validate_transition(current, target) is expected

    @pytest.mark.parametrize("state", TERMINAL_STATES)
    def test_terminal_states_have_no_exit(self, state):
        assert APPROVED_TRANSITIONS[state] == ()

    def test_all_states_covered(self):
        assert set(APPROVED_TRANSITIONS) == set(OrderState)

    def test_deterministic(self):
        for _ in range(3):
            for current, target in APPROVED + FORBIDDEN:
                first = validate_transition(current, target)
                second = validate_transition(current, target)
                assert first is second

    def test_graph_immutable(self):
        with pytest.raises(TypeError):
            APPROVED_TRANSITIONS[OrderState.FILLED] = (
                OrderState.CREATED,)

    @pytest.mark.parametrize("bad", ["CREATED", None, 1, 0.5])
    def test_invalid_input_sterile(self, bad):
        with pytest.raises(ValueError, match="INVALID_ORDER_STATE"):
            validate_transition(bad, OrderState.VALIDATED)
        with pytest.raises(ValueError, match="INVALID_ORDER_STATE"):
            validate_transition(OrderState.CREATED, bad)


# ── Exchange normalizasyonu ──────────────────────────────────────────

class TestExchangeNormalization:
    @pytest.mark.parametrize("binance,canonical", [
        ("NEW", OrderState.SUBMITTED),
        ("PARTIALLY_FILLED", OrderState.PARTIALLY_FILLED),
        ("FILLED", OrderState.FILLED),
        ("CANCELED", OrderState.CANCELLED),
        ("REJECTED", OrderState.REJECTED),
    ])
    def test_binance_mapping(self, binance, canonical):
        assert BINANCE_STATE_NORMALIZATION[binance] is canonical

    def test_mapping_closed(self):
        assert set(BINANCE_STATE_NORMALIZATION) == {
            "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED",
            "REJECTED"}

    def test_mapping_targets_are_canonical(self):
        for target in BINANCE_STATE_NORMALIZATION.values():
            assert isinstance(target, OrderState)

    def test_mapping_immutable(self):
        with pytest.raises(TypeError):
            BINANCE_STATE_NORMALIZATION["NEW"] = OrderState.CREATED


# ── Meta veri ────────────────────────────────────────────────────────

class TestMetadata:
    def test_fields_closed_set(self):
        names = tuple(f.name for f in dataclasses.fields(
            ExecutionMetadata))
        assert names == ("execution_id", "requested_at",
                         "processed_at", "correlation_id")
        assert names == execution_models._METADATA_FIELDS

    def test_defaults_null(self):
        metadata = ExecutionMetadata()
        for field in dataclasses.fields(ExecutionMetadata):
            assert getattr(metadata, field.name) is None

    def test_propagation_carries_same_object(self):
        metadata = ExecutionMetadata(execution_id="e1")
        request = _request(metadata=metadata)
        result = ExecutionResult(status=ExecutionStatus.SUCCESS,
                                 metadata=request.metadata)
        assert result.metadata is metadata

    def test_models_never_generate_metadata(self):
        # Modül zaman/UUID üretemez — alanlar yalnız verilenle dolar
        assert ExecutionMetadata().execution_id is None
        roots = {m.split(".")[0] for m in _module_imports(
            execution_models)}
        assert not roots & {"uuid", "datetime", "time", "random"}


# ── Serileştirme uyumluluğu ──────────────────────────────────────────

class TestSerialization:
    def test_request_asdict_json_compatible(self):
        raw = dataclasses.asdict(_request())
        payload = json.dumps(raw, default=lambda v: (
            v.value if hasattr(v, "value") else format(v, "f")))
        parsed = json.loads(payload)
        assert parsed["symbol"] == "BTCUSDT"
        assert parsed["quantity"] == "0.5"

    def test_decimal_precision_preserved(self):
        order = _order(price=D("50000.1000"))
        assert format(order.price, "f") == "50000.1000"

    def test_result_asdict_round_trip_fields(self):
        raw = dataclasses.asdict(SAMPLES[ExecutionResult]())
        assert set(raw) == {"status", "order", "fills", "code",
                            "metadata"}
        assert isinstance(raw["fills"], tuple)
        assert isinstance(raw["fills"][0], dict)


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
    def test_models_public_surface(self):
        assert set(execution_models.__all__) == {
            "ExecutionRequest", "ExecutionResult", "Order",
            "Position", "Fill", "ExecutionMetadata",
            "ValidationResult"}

    def test_enums_public_surface(self):
        assert set(execution_enums.__all__) == {
            "OrderSide", "OrderType", "TimeInForce", "OrderState",
            "PositionSide", "ExecutionStatus"}

    def test_state_machine_public_surface(self):
        assert execution_state_machine.__all__ == [
            "validate_transition"]

    def test_no_additional_public_callables(self):
        for module, allowed in (
                (execution_models, set(execution_models.__all__)),
                (execution_enums, set(execution_enums.__all__)),
                (execution_state_machine,
                 {"validate_transition"})):
            public = {name for name, value
                      in vars(module).items()
                      if not name.startswith("_")
                      and (inspect.isfunction(value)
                           or inspect.isclass(value))
                      and getattr(value, "__module__", None)
                      == module.__name__}
            assert public <= allowed

    def test_no_public_constants_beyond_approved(self):
        # Onaylı yüzey dışındaki tüm modül sabitleri alt çizgilidir
        import types as types_module
        for module in (execution_enums, execution_models,
                       execution_state_machine):
            approved = set(module.__all__)
            for name, value in vars(module).items():
                if name.startswith("_") or name in approved:
                    continue
                assert (inspect.ismodule(value)
                        or getattr(value, "__module__", None)
                        != module.__name__ and not isinstance(
                            value, (str, tuple, dict,
                                    types_module.MappingProxyType)))

    @pytest.mark.parametrize("module", [
        execution_enums, execution_models, execution_state_machine])
    def test_no_forbidden_imports(self, module):
        roots = {m.split(".")[0] for m in _module_imports(module)}
        forbidden = {"uuid", "datetime", "time", "random", "os",
                     "sys", "io", "socket", "requests", "httpx",
                     "urllib", "urllib3", "threading", "asyncio",
                     "subprocess", "sqlite3", "pickle", "shelve",
                     "pathlib", "secrets", "ccxt", "binance",
                     "websocket", "websockets", "aiohttp"}
        assert not roots & forbidden
        for name in roots:
            assert not name.startswith("exchange_")
            assert not name.startswith("broker")

    @pytest.mark.parametrize("module", [
        execution_enums, execution_models, execution_state_machine])
    def test_allowed_imports_only(self, module):
        allowed = {"__future__", "enum", "dataclasses", "decimal",
                   "types", "typing", "execution_enums"}
        assert _module_imports(module) <= allowed

    @pytest.mark.parametrize("module", [
        execution_enums, execution_models, execution_state_machine])
    def test_no_dangerous_calls(self, module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "open", "__import__", "compile")

    def test_no_execution_behavior(self):
        # Alan modülleri emir yürütmez; yürütme fiilleri yoktur
        for module in (execution_enums, execution_models,
                       execution_state_machine):
            text = inspect.getsource(module)
            for token in ("place_order", "submit_order",
                          "create_order(", "requests.", "http"):
                assert token not in text
