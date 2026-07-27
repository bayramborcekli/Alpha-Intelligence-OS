"""Mission 2100 — Agent 07: Mimari & güvenlik testleri.

AST/kaynak taramaları: yasak import'lar (ağ, dosya sistemi, süreç,
iş parçacığı, rastgelelik, UUID, duvar saati), yasak token'lar,
float literal yasağı, frozen+slots modeller, kapalı enum'lar,
bağımlılık yönü ve hata hiyerarşisi.
"""

import ast
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import lifecycle_models  # noqa: E402
import order_lifecycle  # noqa: E402
import reconciliation  # noqa: E402
import reconciliation_errors  # noqa: E402
from lifecycle_models import (LifecycleAudit,  # noqa: E402
                              LifecycleEvent, LifecycleOperation,
                              OrderLifecycle, OrderLifecycleState,
                              OrderSnapshot, ReconciliationAudit,
                              ReconciliationDecision,
                              ReconciliationMismatch,
                              ReconciliationMismatchCode,
                              ReconciliationReport,
                              ReconciliationReportDecision,
                              ReconciliationResult,
                              ReconciliationSource,
                              ReconciliationStatistics)
from reconciliation_errors import (  # noqa: E402
    LifecycleContractError, LifecycleStateError,
    LifecycleTransitionError, ReconciliationContractError,
    ReconciliationError, ReconciliationInputError)

MODULES = ("lifecycle_models.py", "order_lifecycle.py",
           "reconciliation.py", "reconciliation_errors.py")

FORBIDDEN_IMPORTS = (
    "os", "sys", "io", "socket", "ssl", "http", "urllib",
    "requests", "websocket", "websockets", "aiohttp", "sqlite3",
    "threading", "multiprocessing", "subprocess", "asyncio",
    "random", "uuid", "secrets", "datetime", "time", "json",
    "pickle", "ctypes", "importlib")

FORBIDDEN_TOKENS = (
    "api_key", "password", "signature", "http://", "https://",
    "os.environ", "getenv", "sleep(", "while True", "open(",
    "exec(", "eval(", "__import__", ".now(", "urandom",
    "submit_to_exchange")

MODEL_CLASSES = (LifecycleEvent, LifecycleAudit, OrderLifecycle,
                 OrderSnapshot, ReconciliationMismatch,
                 ReconciliationAudit, ReconciliationResult,
                 ReconciliationStatistics, ReconciliationReport)


def read_source(module_name):
    return (_ROOT / module_name).read_text(encoding="utf-8")


def parse(module_name):
    return ast.parse(read_source(module_name))


def imported_names(module_name):
    names = set()
    for node in ast.walk(parse(module_name)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        if isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module.split(".")[0])
    return names


class TestForbiddenImports:
    @pytest.mark.parametrize("module", MODULES)
    @pytest.mark.parametrize("forbidden", FORBIDDEN_IMPORTS)
    def test_no_forbidden_import(self, module, forbidden):
        assert forbidden not in imported_names(module)

    @pytest.mark.parametrize("module", MODULES)
    def test_only_allowed_imports(self, module):
        allowed = {"__future__", "dataclasses", "decimal",
                   "enum", "types", "typing",
                   "execution_enums", "lifecycle_models",
                   "order_lifecycle", "reconciliation_errors"}
        assert imported_names(module) <= allowed


class TestForbiddenTokens:
    @pytest.mark.parametrize("module", MODULES)
    @pytest.mark.parametrize("token", FORBIDDEN_TOKENS)
    def test_no_forbidden_token(self, module, token):
        assert token not in read_source(module)


class TestAstConstraints:
    @pytest.mark.parametrize("module", MODULES)
    def test_no_float_literal(self, module):
        for node in ast.walk(parse(module)):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)

    @pytest.mark.parametrize("module", MODULES)
    def test_no_lambda(self, module):
        for node in ast.walk(parse(module)):
            assert not isinstance(node, ast.Lambda)

    @pytest.mark.parametrize("module", MODULES)
    def test_no_while_loop(self, module):
        for node in ast.walk(parse(module)):
            assert not isinstance(node, ast.While)

    @pytest.mark.parametrize("module", MODULES)
    def test_no_with_block(self, module):
        for node in ast.walk(parse(module)):
            assert not isinstance(node,
                                  (ast.With, ast.AsyncWith))

    @pytest.mark.parametrize("module", MODULES)
    def test_no_global_or_nonlocal(self, module):
        for node in ast.walk(parse(module)):
            assert not isinstance(node,
                                  (ast.Global, ast.Nonlocal))

    @pytest.mark.parametrize("module", MODULES)
    def test_no_async_code(self, module):
        for node in ast.walk(parse(module)):
            assert not isinstance(
                node, (ast.AsyncFunctionDef, ast.Await))

    @pytest.mark.parametrize("module", MODULES)
    def test_no_try_silencing(self, module):
        # Sessiz yutma yok: modüllerde try/except bile YOK.
        for node in ast.walk(parse(module)):
            assert not isinstance(node, ast.Try)


class TestImmutableModels:
    @pytest.mark.parametrize("model", MODEL_CLASSES)
    def test_frozen_dataclass(self, model):
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen

    @pytest.mark.parametrize("model", MODEL_CLASSES)
    def test_slots(self, model):
        assert hasattr(model, "__slots__")
        assert not hasattr(model, "__dict__") or \
            "__dict__" not in model.__slots__

    @pytest.mark.parametrize("model", MODEL_CLASSES)
    def test_no_inheritance(self, model):
        assert model.__bases__ == (object,)

    @pytest.mark.parametrize("model", MODEL_CLASSES)
    def test_collection_fields_are_tuples(self, model):
        for field in fields(model):
            annotation = str(field.type)
            assert "List" not in annotation
            assert "list" not in annotation
            assert "Dict" not in annotation
            assert "Set" not in annotation


class TestClosedEnums:
    def test_order_lifecycle_states_exact(self):
        assert {s.name for s in OrderLifecycleState} == {
            "NEW", "VALIDATED", "ACCEPTED", "QUEUED",
            "SUBMITTED", "FILLED", "CANCELLED", "REJECTED",
            "FAILED", "CLOSED"}

    def test_lifecycle_operations_exact(self):
        assert {o.name for o in LifecycleOperation} == {
            "VALIDATE", "ACCEPT", "QUEUE", "SUBMIT", "FILL",
            "CANCEL", "REJECT", "FAIL", "CLOSE"}

    def test_sources_exact(self):
        assert {s.name for s in ReconciliationSource} == {
            "EXECUTION_REQUEST", "PAPER", "SHADOW",
            "MICRO_LIVE"}

    def test_mismatch_codes_exact(self):
        assert {c.name for c in ReconciliationMismatchCode} == {
            "MISSING_ORDER", "DUPLICATE_ORDER",
            "DUPLICATE_EXECUTION", "QUANTITY_MISMATCH",
            "PRICE_MISMATCH", "STATUS_MISMATCH",
            "PNL_MISMATCH", "TIMESTAMP_SEQUENCE_VIOLATION",
            "LOGICAL_SEQUENCE_VIOLATION"}

    def test_decisions_exact(self):
        assert {d.name for d in ReconciliationDecision} == {
            "MATCHED", "MISMATCHED", "MISSING"}
        assert {d.name
                for d in ReconciliationReportDecision} == {
            "RECONCILED", "DISCREPANT"}

    @pytest.mark.parametrize("enum_type", [
        OrderLifecycleState, LifecycleOperation,
        ReconciliationSource, ReconciliationMismatchCode,
        ReconciliationDecision, ReconciliationReportDecision])
    def test_enum_values_mirror_names(self, enum_type):
        for member in enum_type:
            assert member.value == member.name


class TestDependencyDirection:
    @pytest.mark.parametrize("module", MODULES)
    @pytest.mark.parametrize("forbidden_module", [
        "execution_service", "execution_broker_adapter",
        "execution_api", "paper_execution_service",
        "micro_live_authorization", "dashboard_api",
        "alpha20_v1", "app"])
    def test_no_higher_layer_import(self, module,
                                    forbidden_module):
        assert forbidden_module not in imported_names(module)

    def test_models_import_only_errors_and_enums(self):
        assert imported_names("lifecycle_models.py") <= {
            "__future__", "dataclasses", "decimal", "enum",
            "typing", "execution_enums",
            "reconciliation_errors"}

    def test_errors_import_nothing_local(self):
        assert imported_names("reconciliation_errors.py") <= {
            "__future__"}

    def test_services_do_not_import_each_other(self):
        assert "reconciliation" not in imported_names(
            "order_lifecycle.py")
        assert "order_lifecycle" not in imported_names(
            "reconciliation.py")


class TestErrorHierarchy:
    @pytest.mark.parametrize("error_type", [
        LifecycleContractError, LifecycleTransitionError,
        LifecycleStateError, ReconciliationContractError,
        ReconciliationInputError])
    def test_closed_root(self, error_type):
        assert issubclass(error_type, ReconciliationError)
        assert issubclass(error_type, Exception)

    def test_root_is_exception_only(self):
        assert ReconciliationError.__bases__ == (Exception,)


class TestDeterministicCertification:
    def test_transition_targets_unique_per_operation(self):
        for _allowed, target in \
                order_lifecycle.TRANSITION_MATRIX.values():
            assert isinstance(
                target,
                lifecycle_models.OrderLifecycleState)

    def test_source_chain_fixed_order(self):
        assert reconciliation.SOURCE_CHAIN == (
            ReconciliationSource.EXECUTION_REQUEST,
            ReconciliationSource.PAPER,
            ReconciliationSource.SHADOW,
            ReconciliationSource.MICRO_LIVE)

    @pytest.mark.parametrize("module_object", [
        lifecycle_models, order_lifecycle, reconciliation,
        reconciliation_errors])
    def test_module_has_no_mutable_state(self, module_object):
        for name in dir(module_object):
            if name.startswith("_"):
                continue
            value = getattr(module_object, name)
            assert not isinstance(value, (list, dict, set))
