"""Mission 2000 — Agent 09: Mimari dondurma sertifikasyonu.

Yürütme Çekirdeği'nin modül kümesi, kamu API yüzeyleri, boru hattı
katmanlaması ve alan sahipliği manifesto ile birebir doğrulanır.
Her sapma regresyon hatasıdır: ekleme açık mimar incelemesi
gerektirir, kaldırma ve yeniden adlandırma yasaktır.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import sys
from types import MappingProxyType

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import execution_architecture_freeze as freeze

FROZEN = freeze.FROZEN_MODULES


def _load(name):
    return importlib.import_module(name)


def _module_imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add((node.module or "").split(".")[0])
    return found


def _defined_classes(module):
    return {node.name for node in ast.walk(
        ast.parse(inspect.getsource(module)))
        if isinstance(node, ast.ClassDef)}


def _defined_functions(module):
    tree = ast.parse(inspect.getsource(module))
    return {node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef,
                                 ast.AsyncFunctionDef))}


# ── Manifesto bütünlüğü ──────────────────────────────────────────────

class TestFreezeManifest:
    def test_freeze_status(self):
        assert freeze.FREEZE_STATUS == "FROZEN"

    def test_frozen_module_set_closed(self):
        assert len(FROZEN) == 20
        assert len(set(FROZEN)) == 20

    def test_public_api_covers_every_frozen_module(self):
        assert set(freeze.PUBLIC_API.keys()) == set(FROZEN)

    def test_manifest_containers_immutable(self):
        assert isinstance(freeze.PUBLIC_API, MappingProxyType)
        assert isinstance(freeze.DOMAIN_OWNERSHIP,
                          MappingProxyType)
        assert isinstance(freeze.PIPELINE_IMPORT_CONTRACT,
                          MappingProxyType)
        assert isinstance(freeze.FROZEN_MODULES, tuple)
        assert isinstance(freeze.PIPELINE_ORDER, tuple)

    def test_manifest_mapping_rejects_mutation(self):
        with pytest.raises(TypeError):
            freeze.PUBLIC_API["yeni_modul"] = ()

    def test_ownership_mapping_rejects_mutation(self):
        with pytest.raises(TypeError):
            freeze.DOMAIN_OWNERSHIP["Order"] = "baska_modul"

    def test_export_tuples_are_tuples(self):
        for exports in freeze.PUBLIC_API.values():
            assert isinstance(exports, tuple)

    def test_freeze_module_surface(self):
        assert freeze.__all__ == [
            "FREEZE_STATUS", "FROZEN_MODULES", "PUBLIC_API",
            "PIPELINE_ORDER", "DOMAIN_OWNERSHIP",
            "PIPELINE_IMPORT_CONTRACT"]

    def test_pipeline_order_frozen(self):
        assert freeze.PIPELINE_ORDER == (
            "execution_api", "execution_service",
            "execution_risk_engine", "execution_permission_gate",
            "execution_kill_switch", "execution_broker_adapter")


# ── Kamu API dondurması (modül başına) ───────────────────────────────

class TestPublicApiFreeze:
    @pytest.mark.parametrize("name", FROZEN)
    def test_module_importable(self, name):
        assert _load(name) is not None

    @pytest.mark.parametrize("name", FROZEN)
    def test_all_matches_manifest_exactly(self, name):
        module = _load(name)
        assert tuple(module.__all__) == freeze.PUBLIC_API[name], \
            f"{name}: kamu API yüzeyi manifesto ile uyuşmuyor"

    @pytest.mark.parametrize("name", FROZEN)
    def test_every_export_resolvable(self, name):
        module = _load(name)
        for symbol in freeze.PUBLIC_API[name]:
            assert hasattr(module, symbol), \
                f"{name}.{symbol} kaldırılmış — kaldırma YASAK"

    @pytest.mark.parametrize("name", FROZEN)
    def test_no_undeclared_public_defs(self, name):
        module = _load(name)
        public = {n for n, v in vars(module).items()
                  if not n.startswith("_")
                  and (inspect.isclass(v) or inspect.isfunction(v))
                  and getattr(v, "__module__", None)
                  == module.__name__}
        assert public <= set(module.__all__), \
            f"{name}: bildirilmemiş kamu sembolü — ekleme mimar incelemesi gerektirir"

    @pytest.mark.parametrize("name", FROZEN)
    def test_no_renamed_exports(self, name):
        # __all__ içindeki her ad gerçek tanımla eşleşmeli
        module = _load(name)
        for symbol in module.__all__:
            value = getattr(module, symbol)
            if inspect.isclass(value) or inspect.isfunction(value):
                assert value.__name__ == symbol


# ── Alan sahipliği ───────────────────────────────────────────────────

class TestDomainOwnership:
    @pytest.mark.parametrize("symbol,owner",
                             list(freeze.DOMAIN_OWNERSHIP.items()))
    def test_symbol_defined_in_owner(self, symbol, owner):
        module = _load(owner)
        assert symbol in _defined_classes(module) or \
            symbol in _defined_functions(module)

    @pytest.mark.parametrize("symbol,owner",
                             list(freeze.DOMAIN_OWNERSHIP.items()))
    def test_symbol_defined_nowhere_else(self, symbol, owner):
        for name in FROZEN:
            if name == owner:
                continue
            assert symbol not in _defined_classes(_load(name)), \
                f"{symbol} kopyası {name} içinde — tek sahip {owner}"

    def test_no_duplicate_class_names_anywhere(self):
        seen = {}
        for name in FROZEN:
            for cls in _defined_classes(_load(name)):
                assert cls not in seen, \
                    f"{cls}: {seen.get(cls)} ve {name} içinde tanımlı"
                seen[cls] = name

    @pytest.mark.parametrize("symbol", [
        "ExecutionRequest", "Order", "Position", "Fill",
        "Instrument", "BrokerProfile", "RiskDecision",
        "ExecutionMode", "BrokerOperationResult",
        "BrokerRequestContext"])
    def test_spec_required_owners_present(self, symbol):
        assert symbol in freeze.DOMAIN_OWNERSHIP


# ── Boru hattı katmanlaması (AST) ────────────────────────────────────

class TestPipelineLayering:
    @pytest.mark.parametrize(
        "layer,forbidden",
        list(freeze.PIPELINE_IMPORT_CONTRACT.items()))
    def test_lower_layer_never_imports_upper(self, layer,
                                             forbidden):
        assert not _module_imports(_load(layer)) & set(forbidden)

    def test_api_imports_service(self):
        import execution_api
        assert "execution_service" in _module_imports(
            execution_api)

    def test_service_imports_risk_gate_switch_adapter(self):
        import execution_service
        imports = _module_imports(execution_service)
        for lower in ("execution_risk_engine",
                      "execution_permission_gate",
                      "execution_kill_switch",
                      "execution_broker_adapter"):
            assert lower in imports

    def test_gate_imports_kill_switch(self):
        import execution_permission_gate
        assert "execution_kill_switch" in _module_imports(
            execution_permission_gate)

    def test_binance_adapter_inherits_broker_adapter_only(self):
        import binance_spot_adapter
        from binance_spot_adapter import BinanceSpotAdapter
        from execution_broker_adapter import BrokerAdapter
        assert BinanceSpotAdapter.__bases__ == (BrokerAdapter,)

    def test_api_does_not_import_broker_or_risk(self):
        import execution_api
        imports = _module_imports(execution_api)
        for lower in ("execution_risk_engine",
                      "execution_kill_switch",
                      "execution_broker_adapter",
                      "binance_spot_adapter",
                      "execution_permission_gate"):
            assert lower not in imports

    def test_pipeline_modules_all_frozen(self):
        assert set(freeze.PIPELINE_ORDER) <= set(FROZEN)

    def test_strategy_and_monitoring_outside_core(self):
        import monitoring_service
        import strategy_service
        core = set(FROZEN)
        for module in (monitoring_service, strategy_service):
            assert not _module_imports(module) & {
                "execution_api", "execution_service",
                "execution_broker_adapter",
                "binance_spot_adapter"}
        assert "monitoring_service" not in core


# ── Dondurulmuş sınıf yüzeyleri ──────────────────────────────────────

class TestFrozenClassSurfaces:
    @pytest.mark.parametrize("cls_path,expected", [
        (("execution_api", "ExecutionApi"), {"execute"}),
        (("execution_service", "ExecutionService"), {"execute"}),
        (("execution_permission_gate", "ExecutionPermissionGate"),
         {"evaluate"}),
        (("execution_api_mapper", "ExecutionApiMapper"),
         {"to_service_request", "to_api_response"}),
        (("execution_risk_engine", "RiskEngine"),
         {"validate", "limits"}),
        (("execution_kill_switch", "KillSwitch"),
         {"enable", "disable", "lock", "maintenance",
          "is_execution_allowed", "current_state"}),
    ])
    def test_public_method_surface_frozen(self, cls_path,
                                          expected):
        module_name, cls_name = cls_path
        cls = getattr(_load(module_name), cls_name)
        public = {n for n in dir(cls) if not n.startswith("_")}
        assert public == expected

    def test_broker_adapter_operation_surface_frozen(self):
        from execution_broker_adapter import BrokerAdapter
        public = {n for n in dir(BrokerAdapter)
                  if not n.startswith("_")}
        assert public == {"profile", "health_check", "get_order",
                          "list_open_orders", "get_positions",
                          "get_balances", "submit_order",
                          "cancel_order"}

    def test_broker_adapter_hooks_stay_abstract(self):
        from execution_broker_adapter import BrokerAdapter
        assert inspect.isabstract(BrokerAdapter)
        hooks = {name for name in
                 BrokerAdapter.__abstractmethods__}
        assert hooks == {"_do_profile", "_do_health_check",
                         "_do_get_order", "_do_list_open_orders",
                         "_do_get_positions", "_do_get_balances",
                         "_do_submit_order", "_do_cancel_order"}

    def test_resolver_surface_frozen(self):
        from execution_service import BrokerAdapterResolver
        assert BrokerAdapterResolver.__abstractmethods__ == \
            frozenset({"resolve", "profile"})


# ── Dondurulmuş enum yüzeyleri ───────────────────────────────────────

_ENUM_SNAPSHOTS = [
    ("execution_enums", "OrderSide", ("BUY", "SELL")),
    ("execution_enums", "OrderType",
     ("MARKET", "LIMIT", "STOP_LIMIT", "STOP_MARKET",
      "TAKE_PROFIT")),
    ("execution_enums", "TimeInForce", ("GTC", "IOC", "FOK")),
    ("execution_risk_models", "RiskDecisionType",
     ("ALLOW", "REJECT", "REDUCE_SIZE", "REQUIRE_CONFIRMATION")),
    ("execution_broker_models", "ExecutionMode",
     ("PAPER", "SHADOW", "MICRO_LIVE", "LIVE")),
    ("execution_broker_models", "BrokerOperationStatus",
     ("SUCCESS", "REJECTED", "NOT_FOUND", "UNSUPPORTED",
      "TEMPORARY_FAILURE", "PERMANENT_FAILURE", "UNKNOWN")),
    ("execution_broker_models", "BrokerHealthState",
     ("HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN")),
    ("execution_service_models", "ExecutionServiceStatus",
     ("SUBMITTED", "NOT_SUBMITTED", "REJECTED_BY_RISK",
      "BLOCKED_BY_KILL_SWITCH", "REQUIRES_CONFIRMATION",
      "SIZE_REDUCTION_REQUIRED", "BROKER_REJECTED",
      "BROKER_TEMPORARY_FAILURE", "BROKER_PERMANENT_FAILURE",
      "BROKER_UNAVAILABLE", "INVALID_REQUEST",
      "UNKNOWN_FAILURE")),
    ("execution_service_models", "ExecutionTraceStep",
     ("INPUT_VALIDATED", "BROKER_RESOLVED", "RISK_EVALUATED",
      "RISK_ALLOWED", "RISK_DENIED", "KILL_SWITCH_CHECKED",
      "EXECUTION_PERMITTED", "EXECUTION_DENIED",
      "BROKER_SUBMISSION_STARTED", "BROKER_SUBMISSION_COMPLETED",
      "BROKER_SUBMISSION_FAILED", "RESULT_NORMALIZED")),
    ("execution_api_models", "ExecutionApiStatus",
     ("SUBMITTED", "NOT_SUBMITTED", "VALIDATION_FAILED",
      "REJECTED_BY_RISK", "BLOCKED_BY_KILL_SWITCH",
      "REQUIRES_CONFIRMATION", "SIZE_REDUCTION_REQUIRED",
      "BROKER_FAILURE", "UNKNOWN_FAILURE")),
]


class TestFrozenEnums:
    @pytest.mark.parametrize("module_name,enum_name,members",
                             _ENUM_SNAPSHOTS)
    def test_enum_members_frozen(self, module_name, enum_name,
                                 members):
        enum_cls = getattr(_load(module_name), enum_name)
        assert tuple(m.name for m in enum_cls) == members

    @pytest.mark.parametrize("module_name,enum_name,members",
                             _ENUM_SNAPSHOTS)
    def test_enum_values_equal_names(self, module_name, enum_name,
                                     members):
        enum_cls = getattr(_load(module_name), enum_name)
        for member in enum_cls:
            assert member.value == member.name

    def test_broker_error_code_count_frozen(self):
        from execution_broker_errors import BrokerErrorCode
        assert len(list(BrokerErrorCode)) == 20


# ── Mission 2000 FROZEN işareti ──────────────────────────────────────

class TestMissionFrozenMark:
    def test_architecture_test_marks_frozen(self):
        path = os.path.join(_ROOT, "tests",
                            "test_mission_2000_architecture.py")
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert 'MISSION_2000_STATUS = "FROZEN"' in content

    def test_all_planned_modules_delivered(self):
        for name in ("execution_api.py", "execution_service.py",
                     "execution_risk_engine.py",
                     "execution_kill_switch.py",
                     "execution_broker_adapter.py",
                     "binance_spot_adapter.py"):
            assert os.path.exists(os.path.join(
                _ROOT, name)), f"{name} teslim edilmemiş"
