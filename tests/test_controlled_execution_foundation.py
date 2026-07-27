"""Mission 2100 — Agent 01: Kontrollü Yürütme Temeli testleri.

Kapalı mod enum'u, varsayılan PAPER, fail-closed davranış, mod
güvenlik meta verisi, değişmez modeller, geçiş matrisi, genişleme
kayıt defteri, kamu API dondurması ve güvenlik taramaları.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import sys
from decimal import Decimal

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import controlled_execution_errors as ce_errors
import controlled_execution_foundation as ce_foundation
import controlled_execution_models as ce_models
import controlled_execution_policy as ce_policy
from controlled_execution_errors import (
    ControlledExecutionConfigurationError,
    ControlledExecutionContractError, ControlledExecutionError)
from controlled_execution_foundation import (
    ControlledExecutionFoundation)
from controlled_execution_models import (
    ControlledExecutionDecision, ControlledExecutionDecisionCode,
    ControlledExecutionMode, ControlledExecutionPolicy)
from controlled_execution_policy import (
    ExtensionPoint, ExtensionRegistry)

D = Decimal
_MODE = ControlledExecutionMode
_CODE = ControlledExecutionDecisionCode

CE_MODULES = (ce_errors, ce_models, ce_policy, ce_foundation)


def _foundation():
    return ControlledExecutionFoundation(ExtensionRegistry())


def _policy(mode=_MODE.PAPER, **overrides):
    defaults = {
        _MODE.PAPER: dict(simulated_fill_allowed=True),
        _MODE.SHADOW: dict(broker_read_allowed=True),
        _MODE.MICRO_LIVE: dict(
            broker_read_allowed=True,
            human_confirmation_required=True,
            explicit_authorization_required=True),
    }[mode]
    defaults.update(overrides)
    return ControlledExecutionPolicy(mode=mode, **defaults)


def _code_source(module) -> str:
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def _module_imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add((node.module or "").split(".")[0])
    return found


# ── Kapalı mod enum'u ────────────────────────────────────────────────

class TestRuntimeModeEnum:
    def test_exactly_three_modes(self):
        assert tuple(m.name for m in _MODE) == (
            "PAPER", "SHADOW", "MICRO_LIVE")

    @pytest.mark.parametrize("forbidden", [
        "LIVE", "FULL_LIVE", "PRODUCTION", "AUTO_LIVE",
        "UNRESTRICTED"])
    def test_no_unrestricted_live_mode(self, forbidden):
        assert forbidden not in _MODE.__members__

    @pytest.mark.parametrize("mode", list(_MODE))
    def test_values_equal_names(self, mode):
        assert mode.value == mode.name

    def test_decision_code_enum_closed(self):
        assert tuple(c.name for c in _CODE) == (
            "ALLOW_NON_WRITING_MODE", "DENY_EXCHANGE_WRITE",
            "REQUIRE_EXPLICIT_AUTHORIZATION", "INVALID_MODE",
            "INVALID_POLICY", "INVALID_TRANSITION")

    def test_extension_point_enum_closed(self):
        assert tuple(p.value for p in ExtensionPoint) == (
            "PaperExecutionProvider",
            "ShadowObservationProvider",
            "MicroLiveAuthorizationProvider",
            "RuntimeStateProvider", "RuntimeAuditSink",
            "DesktopClientAdapter", "MobileClientAdapter",
            "UpdateManagerAdapter", "PluginProvider")


# ── Varsayılan güvenlik ──────────────────────────────────────────────

class TestDefaultSafety:
    def test_default_mode_is_paper(self):
        assert _foundation().default_mode() is _MODE.PAPER

    def test_default_policy_denies_exchange_write(self):
        policy = ControlledExecutionPolicy(mode=_MODE.PAPER)
        assert policy.exchange_write_allowed is False

    def test_all_policy_defaults_non_writing(self):
        policy = ControlledExecutionPolicy(mode=_MODE.PAPER)
        assert policy.simulated_fill_allowed is False
        assert policy.broker_read_allowed is False
        assert policy.human_confirmation_required is False
        assert policy.explicit_authorization_required is False
        assert policy.maximum_order_notional is None
        assert policy.authorization_reference is None

    def test_missing_policy_denied(self):
        decision = _foundation().evaluate_policy(None)
        assert decision.code is _CODE.INVALID_POLICY
        assert decision.allowed is False

    def test_foundation_requires_registry(self):
        with pytest.raises(
                ControlledExecutionConfigurationError):
            ControlledExecutionFoundation(None)

    @pytest.mark.parametrize("bad", [object(), "PAPER", 1, []])
    def test_foundation_rejects_non_registry(self, bad):
        with pytest.raises(
                ControlledExecutionConfigurationError):
            ControlledExecutionFoundation(bad)

    @pytest.mark.parametrize("bad", [object(), "policy", 5, ()])
    def test_wrong_policy_type_contract_error(self, bad):
        with pytest.raises(ControlledExecutionContractError):
            _foundation().evaluate_policy(bad)


# ── Mod güvenlik meta verisi ─────────────────────────────────────────

_SAFETY_MATRIX = [
    (_MODE.PAPER, False, True, False, False, False),
    (_MODE.SHADOW, False, False, True, False, False),
    (_MODE.MICRO_LIVE, True, False, True, True, True),
]


class TestModeSafetyMetadata:
    @pytest.mark.parametrize(
        "mode,write,fill,read,human,auth", _SAFETY_MATRIX)
    def test_permanent_safety_contract(self, mode, write, fill,
                                       read, human, auth):
        safety = ce_policy._MODE_SAFETY[mode]
        assert safety.exchange_write_allowed is write
        assert safety.simulated_fill_allowed is fill
        assert safety.broker_read_allowed is read
        assert safety.human_confirmation_required is human
        assert safety.explicit_authorization_required is auth

    def test_safety_map_covers_all_modes(self):
        assert set(ce_policy._MODE_SAFETY.keys()) == set(_MODE)

    def test_safety_map_immutable(self):
        with pytest.raises(TypeError):
            ce_policy._MODE_SAFETY[_MODE.PAPER] = None

    def test_safety_records_frozen(self):
        safety = ce_policy._MODE_SAFETY[_MODE.PAPER]
        with pytest.raises(Exception):
            safety.exchange_write_allowed = True

    @pytest.mark.parametrize("mode", [_MODE.PAPER, _MODE.SHADOW])
    def test_non_writing_modes_never_write(self, mode):
        assert ce_policy._MODE_SAFETY[
            mode].exchange_write_allowed is False


# ── Politika değerlendirme (fail-closed) ─────────────────────────────

class TestPolicyEvaluation:
    def test_paper_policy_allowed(self):
        decision = _foundation().evaluate_policy(_policy())
        assert decision.code is _CODE.ALLOW_NON_WRITING_MODE
        assert decision.allowed is True
        assert decision.mode is _MODE.PAPER

    def test_shadow_policy_allowed(self):
        decision = _foundation().evaluate_policy(
            _policy(_MODE.SHADOW))
        assert decision.code is _CODE.ALLOW_NON_WRITING_MODE
        assert decision.mode is _MODE.SHADOW

    @pytest.mark.parametrize("mode", list(_MODE))
    def test_exchange_write_always_denied(self, mode):
        policy = _policy(mode, exchange_write_allowed=True) \
            if mode is _MODE.MICRO_LIVE else None
        if policy is None:
            # yazmayan modlarda yazma talebi mod çelişkisidir
            decision = _foundation().evaluate_policy(
                ControlledExecutionPolicy(
                    mode=mode, exchange_write_allowed=True))
            assert decision.code in (
                _CODE.INVALID_POLICY, _CODE.DENY_EXCHANGE_WRITE)
        else:
            decision = _foundation().evaluate_policy(policy)
            assert decision.code is _CODE.DENY_EXCHANGE_WRITE
        assert decision.allowed is False

    def test_micro_live_without_authorization_denied(self):
        decision = _foundation().evaluate_policy(
            _policy(_MODE.MICRO_LIVE))
        assert decision.code is \
            _CODE.REQUIRE_EXPLICIT_AUTHORIZATION
        assert decision.reason == "MISSING_AUTHORIZATION"

    def test_micro_live_with_reference_still_not_allowed(self):
        # Agent 01: yetkilendirme bileşeni yok → fail-closed
        decision = _foundation().evaluate_policy(
            _policy(_MODE.MICRO_LIVE,
                    authorization_reference="auth-ref-1"))
        assert decision.code is \
            _CODE.REQUIRE_EXPLICIT_AUTHORIZATION
        assert decision.allowed is False

    def test_micro_live_never_returns_allow(self):
        for reference in (None, "ref-a", "ref-b"):
            decision = _foundation().evaluate_policy(
                _policy(_MODE.MICRO_LIVE,
                        authorization_reference=reference))
            assert decision.allowed is False

    @pytest.mark.parametrize("mode,field", [
        (_MODE.PAPER, "broker_read_allowed"),
        (_MODE.SHADOW, "simulated_fill_allowed"),
    ])
    def test_policy_exceeding_mode_safety_invalid(self, mode,
                                                  field):
        decision = _foundation().evaluate_policy(
            _policy(mode, **{field: True}))
        assert decision.code is _CODE.INVALID_POLICY
        assert decision.reason == "POLICY_MODE_CONFLICT"

    @pytest.mark.parametrize("field", [
        "human_confirmation_required",
        "explicit_authorization_required"])
    def test_micro_live_missing_requirement_invalid(self, field):
        decision = _foundation().evaluate_policy(
            _policy(_MODE.MICRO_LIVE, **{field: False}))
        assert decision.code is _CODE.INVALID_POLICY

    def test_evaluation_deterministic(self):
        results = [_foundation().evaluate_policy(_policy())
                   for _ in range(3)]
        assert results[0] == results[1] == results[2]

    def test_micro_live_limits_representable(self):
        policy = _policy(_MODE.MICRO_LIVE,
                         maximum_order_notional=D("10"),
                         maximum_daily_notional=D("50"),
                         maximum_open_orders=2,
                         authorization_reference="ref")
        assert policy.maximum_order_notional == D("10")
        decision = _foundation().evaluate_policy(policy)
        assert decision.allowed is False  # yine de kapalı


# ── Geçiş matrisi ────────────────────────────────────────────────────

class TestTransitionPolicy:
    @pytest.mark.parametrize("current,target", [
        (_MODE.PAPER, _MODE.SHADOW),
        (_MODE.SHADOW, _MODE.PAPER)])
    def test_allowed_transitions(self, current, target):
        decision = _foundation().evaluate_transition(current,
                                                     target)
        assert decision.code is _CODE.ALLOW_NON_WRITING_MODE
        assert decision.mode is target

    def test_shadow_to_micro_live_requires_authorization(self):
        decision = _foundation().evaluate_transition(
            _MODE.SHADOW, _MODE.MICRO_LIVE)
        assert decision.code is \
            _CODE.REQUIRE_EXPLICIT_AUTHORIZATION
        assert decision.allowed is False

    def test_paper_to_micro_live_forbidden(self):
        decision = _foundation().evaluate_transition(
            _MODE.PAPER, _MODE.MICRO_LIVE)
        assert decision.code is _CODE.INVALID_TRANSITION

    @pytest.mark.parametrize("target", list(_MODE))
    def test_micro_live_to_anything_forbidden_or_self(
            self, target):
        decision = _foundation().evaluate_transition(
            _MODE.MICRO_LIVE, target)
        assert decision.code is _CODE.INVALID_TRANSITION

    @pytest.mark.parametrize("mode", list(_MODE))
    def test_self_transition_not_in_matrix(self, mode):
        decision = _foundation().evaluate_transition(mode, mode)
        assert decision.code is _CODE.INVALID_TRANSITION

    @pytest.mark.parametrize("bad", [
        None, "PAPER", "MICRO_LIVE", 1, object()])
    def test_unknown_mode_denied(self, bad):
        decision = _foundation().evaluate_transition(bad,
                                                     _MODE.PAPER)
        assert decision.code is _CODE.INVALID_MODE
        decision = _foundation().evaluate_transition(_MODE.PAPER,
                                                     bad)
        assert decision.code is _CODE.INVALID_MODE

    def test_no_implicit_escalation_paths(self):
        # Yükseltilmiş moda giden TEK yol açık yetkilendirmedir
        for current in _MODE:
            decision = _foundation().evaluate_transition(
                current, _MODE.MICRO_LIVE)
            assert decision.code is not \
                _CODE.ALLOW_NON_WRITING_MODE

    def test_transition_matrix_deterministic(self):
        for pair in ((_MODE.PAPER, _MODE.SHADOW),
                     (_MODE.PAPER, _MODE.MICRO_LIVE)):
            results = [_foundation().evaluate_transition(*pair)
                       for _ in range(3)]
            assert results[0] == results[1] == results[2]


# ── Model doğrulama ──────────────────────────────────────────────────

class TestModelValidation:
    @pytest.mark.parametrize("bad", [None, "PAPER", 1, object()])
    def test_invalid_mode_rejected(self, bad):
        with pytest.raises(ControlledExecutionContractError):
            ControlledExecutionPolicy(mode=bad)

    @pytest.mark.parametrize("field", [
        "exchange_write_allowed", "simulated_fill_allowed",
        "broker_read_allowed", "human_confirmation_required",
        "explicit_authorization_required"])
    @pytest.mark.parametrize("bad", [1, 0, "true", None])
    def test_non_bool_flags_rejected(self, field, bad):
        with pytest.raises(ControlledExecutionContractError):
            ControlledExecutionPolicy(mode=_MODE.PAPER,
                                      **{field: bad})

    @pytest.mark.parametrize("field", [
        "maximum_order_notional", "maximum_daily_notional"])
    @pytest.mark.parametrize("bad", [
        1, "10", True, D("-1"), D("NaN"), D("Infinity")])
    def test_invalid_decimal_limits_rejected(self, field, bad):
        with pytest.raises(ControlledExecutionContractError):
            ControlledExecutionPolicy(mode=_MODE.PAPER,
                                      **{field: bad})

    def test_float_limit_rejected(self):
        with pytest.raises(ControlledExecutionContractError):
            ControlledExecutionPolicy(
                mode=_MODE.PAPER,
                maximum_order_notional=eval("10.0"))

    @pytest.mark.parametrize("bad", [True, False, -1, "3", D("1")])
    def test_invalid_open_orders_rejected(self, bad):
        with pytest.raises(ControlledExecutionContractError):
            ControlledExecutionPolicy(mode=_MODE.PAPER,
                                      maximum_open_orders=bad)

    @pytest.mark.parametrize("bad", [True, False, -5, "7"])
    def test_bool_and_invalid_sequence_rejected(self, bad):
        with pytest.raises(ControlledExecutionContractError):
            ControlledExecutionPolicy(mode=_MODE.PAPER,
                                      logical_sequence=bad)

    @pytest.mark.parametrize("bad", ["", "   ", 5, object()])
    def test_blank_reference_rejected(self, bad):
        with pytest.raises(ControlledExecutionContractError):
            ControlledExecutionPolicy(
                mode=_MODE.PAPER, authorization_reference=bad)

    def test_unknown_stays_none(self):
        policy = ControlledExecutionPolicy(mode=_MODE.PAPER)
        assert policy.maximum_daily_notional is None
        assert policy.maximum_open_orders is None
        assert policy.logical_sequence is None

    def test_sterile_error_code(self):
        with pytest.raises(ControlledExecutionContractError) as e:
            ControlledExecutionPolicy(mode=None)
        assert "INVALID_CONTROLLED_MODEL_FIELD" in str(e.value)

    @pytest.mark.parametrize("bad", [None, "ALLOW", 1])
    def test_decision_requires_code_enum(self, bad):
        with pytest.raises(ControlledExecutionContractError):
            ControlledExecutionDecision(code=bad)

    def test_decision_blank_reason_rejected(self):
        with pytest.raises(ControlledExecutionContractError):
            ControlledExecutionDecision(
                code=_CODE.INVALID_MODE, reason="  ")


# ── Değişmezlik ve hash ──────────────────────────────────────────────

class TestImmutability:
    def test_policy_frozen(self):
        policy = _policy()
        with pytest.raises(Exception):
            policy.exchange_write_allowed = True

    def test_decision_frozen(self):
        decision = _foundation().evaluate_policy(_policy())
        with pytest.raises(Exception):
            decision.code = _CODE.INVALID_MODE

    def test_policy_hashable_and_equal(self):
        assert _policy() == _policy()
        assert hash(_policy()) == hash(_policy())

    def test_decision_hashable_and_equal(self):
        first = _foundation().evaluate_policy(_policy())
        second = _foundation().evaluate_policy(_policy())
        assert first == second
        assert hash(first) == hash(second)

    @pytest.mark.parametrize("cls", [
        ControlledExecutionPolicy, ControlledExecutionDecision,
        ExtensionRegistry, ControlledExecutionFoundation])
    def test_slots_everywhere(self, cls):
        assert "__slots__" in cls.__dict__ or \
            cls.__dataclass_params__.frozen  # dataclass slots

    def test_no_dict_on_instances(self):
        for instance in (_policy(), ExtensionRegistry(),
                         _foundation()):
            assert not hasattr(instance, "__dict__")


# ── Genişleme kayıt defteri ──────────────────────────────────────────

class TestExtensionRegistry:
    def test_default_registry_declares_all_points(self):
        registry = ExtensionRegistry()
        assert registry.points == tuple(ExtensionPoint)

    @pytest.mark.parametrize("point", list(ExtensionPoint))
    def test_each_point_declared(self, point):
        assert ExtensionRegistry().is_declared(point) is True

    @pytest.mark.parametrize("bad", [
        None, "PluginProvider", 1, object()])
    def test_unknown_point_false(self, bad):
        assert ExtensionRegistry().is_declared(bad) is False

    def test_registry_immutable(self):
        registry = ExtensionRegistry()
        with pytest.raises(Exception):
            registry.points = ()

    def test_registry_bounded_and_deterministic(self):
        assert len(ExtensionRegistry().points) == 9
        assert ExtensionRegistry() == ExtensionRegistry()
        assert hash(ExtensionRegistry()) == \
            hash(ExtensionRegistry())

    @pytest.mark.parametrize("bad_points", [
        None, [], "points",
        (ExtensionPoint.PLUGIN_PROVIDER,
         ExtensionPoint.PLUGIN_PROVIDER),
        ("PluginProvider",)])
    def test_invalid_points_rejected(self, bad_points):
        with pytest.raises(ControlledExecutionContractError):
            ExtensionRegistry(points=bad_points)

    def test_subset_registry_allowed(self):
        registry = ExtensionRegistry(points=(
            ExtensionPoint.PAPER_EXECUTION_PROVIDER,))
        assert registry.is_declared(
            ExtensionPoint.PAPER_EXECUTION_PROVIDER)
        assert not registry.is_declared(
            ExtensionPoint.PLUGIN_PROVIDER)

    def test_foundation_exposes_immutable_view(self):
        points = _foundation().extension_points()
        assert isinstance(points, tuple)
        assert points == tuple(ExtensionPoint)


# ── Kamu API ─────────────────────────────────────────────────────────

class TestPublicApi:
    def test_explicit_exports(self):
        assert ce_errors.__all__ == [
            "ControlledExecutionError",
            "ControlledExecutionContractError",
            "ControlledExecutionConfigurationError"]
        assert ce_models.__all__ == [
            "ControlledExecutionMode",
            "ControlledExecutionDecisionCode",
            "ControlledExecutionPolicy",
            "ControlledExecutionDecision"]
        assert ce_policy.__all__ == [
            "ExtensionPoint", "ExtensionRegistry"]
        assert ce_foundation.__all__ == [
            "ControlledExecutionFoundation"]

    def test_ten_primary_symbols(self):
        combined = (ce_errors.__all__ + ce_models.__all__ +
                    ce_policy.__all__ + ce_foundation.__all__)
        assert len(combined) == 10
        assert set(combined) == {
            "ControlledExecutionMode",
            "ControlledExecutionPolicy",
            "ControlledExecutionFoundation",
            "ControlledExecutionDecision",
            "ControlledExecutionDecisionCode",
            "ControlledExecutionError",
            "ControlledExecutionContractError",
            "ControlledExecutionConfigurationError",
            "ExtensionPoint", "ExtensionRegistry"}

    def test_internal_maps_not_exported(self):
        for internal in ("_MODE_SAFETY", "_ALLOWED_TRANSITIONS",
                         "_FUTURE_AUTHORIZED_TRANSITIONS",
                         "_DEFAULT_MODE"):
            assert internal not in ce_policy.__all__

    def test_exception_hierarchy_closed(self):
        assert issubclass(ControlledExecutionContractError,
                          ControlledExecutionError)
        assert issubclass(ControlledExecutionConfigurationError,
                          ControlledExecutionError)

    @pytest.mark.parametrize("module", CE_MODULES)
    def test_no_undeclared_public_defs(self, module):
        public = {n for n, v in vars(module).items()
                  if not n.startswith("_")
                  and (inspect.isclass(v) or
                       inspect.isfunction(v))
                  and getattr(v, "__module__", None)
                  == module.__name__}
        assert public <= set(module.__all__)

    def test_foundation_public_surface(self):
        public = {n for n in dir(ControlledExecutionFoundation)
                  if not n.startswith("_")}
        assert public == {"default_mode", "extension_points",
                          "evaluate_policy",
                          "evaluate_transition"}


# ── Yasak davranışlar (foundation asla yürütmez) ─────────────────────

class TestNoExecutionBehavior:
    def test_foundation_never_imports_core(self):
        core = {"execution_api", "execution_service",
                "execution_risk_engine", "execution_kill_switch",
                "execution_broker_adapter",
                "binance_spot_adapter", "execution_models",
                "execution_api_models"}
        for module in CE_MODULES:
            assert not _module_imports(module) & core

    def test_no_submit_or_simulate_tokens(self):
        for module in CE_MODULES:
            # "simulated_fill_allowed" bir SÖZLEŞME alanıdır,
            # simülasyon uygulaması değildir — taramadan çıkar
            source = _code_source(module).replace(
                "simulated_fill", "")
            for token in ("submit_order", "cancel_order",
                          "simulate", "fill(", "KillSwitch",
                          "RiskEngine", "BrokerAdapter",
                          "ExecutionApi(", "ExecutionService("):
                assert token not in source, \
                    f"{module.__name__}: {token}"

    def test_no_broker_name_branching(self):
        for module in CE_MODULES:
            source = _code_source(module)
            for token in ("Binance", "IBKR", "Midas", "Bybit",
                          "OKX", "Kraken", "if broker"):
                assert token not in source

    def test_no_async_no_awaitable_surface(self):
        for name in ("evaluate_policy", "evaluate_transition",
                     "default_mode"):
            method = getattr(ControlledExecutionFoundation, name)
            assert not inspect.iscoroutinefunction(method)
