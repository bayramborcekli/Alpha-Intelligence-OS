"""Mission 2100 — Agent 08: Mimari dondurma testleri.

API katmanı modüllerinde yasak importlar, yasak jetonlar, AST
yasakları (float sabiti, lambda, while, with, global, try,
async), değişmezlik (frozen+slots, kalıtım yok), kapalı enum'lar,
karar eşleme kapanışı, bağımlılık yönü ve hata hiyerarşisi.
"""

import ast
import sys
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import controlled_execution_api as api_module  # noqa: E402
import controlled_execution_api_errors as \
    errors_module  # noqa: E402
import controlled_execution_api_models as \
    models_module  # noqa: E402
import controlled_execution_router as \
    router_module  # noqa: E402
from controlled_execution_api import (  # noqa: E402
    _MICRO_DECISION_MAP, _PAPER_DECISION_MAP,
    _SHADOW_DECISION_MAP)
from controlled_execution_api_errors import (  # noqa: E402
    ControlledExecutionAPIConfigurationError,
    ControlledExecutionAPIContractError,
    ControlledExecutionAPIError,
    ControlledExecutionAPIModeError,
    ControlledExecutionAPIRoutingError)
from controlled_execution_api_models import (  # noqa: E402
    ControlledExecutionAPIDecision, ControlledExecutionAudit,
    ControlledExecutionOperation, ControlledExecutionRequest,
    ControlledExecutionResponse, ControlledExecutionState,
    ControlledExecutionStatistics, ControlledExecutionStatus)
from micro_live_models import MicroLiveDecision  # noqa: E402
from paper_execution_models import (  # noqa: E402
    PaperExecutionDecision)
from shadow_models import ShadowDecision  # noqa: E402

MODULES = {
    "controlled_execution_api.py": api_module,
    "controlled_execution_api_models.py": models_module,
    "controlled_execution_api_errors.py": errors_module,
    "controlled_execution_router.py": router_module,
}

SOURCES = {name: (ROOT / name).read_text(encoding="utf-8")
           for name in MODULES}
TREES = {name: ast.parse(source)
         for name, source in SOURCES.items()}

FORBIDDEN_IMPORTS = (
    "os", "sys", "subprocess", "socket", "ssl", "http",
    "urllib", "requests", "httpx", "aiohttp", "websocket",
    "datetime", "time", "random", "uuid", "secrets", "json",
    "pickle", "sqlite3", "threading", "multiprocessing",
    "asyncio", "logging", "pathlib", "io", "tempfile",
    "binance", "ccxt", "flask", "app", "dashboard",
    "paper_broker")

FORBIDDEN_TOKENS = (
    "api_key", "apikey", "api_secret", "password", "token",
    "signature", "hmac", "http://", "https://", "os.environ",
    "getenv", "sleep(", "open(", ".now(", "urandom",
    "randint", "uuid4", "exchange_client", "requests.")

MODEL_CLASSES = (
    ControlledExecutionRequest, ControlledExecutionState,
    ControlledExecutionAudit, ControlledExecutionStatus,
    ControlledExecutionStatistics, ControlledExecutionResponse)

ENUM_CLASSES = (ControlledExecutionOperation,
                ControlledExecutionAPIDecision)


def _imports(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class TestForbiddenImports:
    @pytest.mark.parametrize("module_name", list(MODULES))
    @pytest.mark.parametrize("banned", FORBIDDEN_IMPORTS)
    def test_no_forbidden_import(self, module_name, banned):
        assert banned not in _imports(TREES[module_name])


class TestForbiddenTokens:
    @pytest.mark.parametrize("module_name", list(MODULES))
    @pytest.mark.parametrize("token", FORBIDDEN_TOKENS)
    def test_no_forbidden_token(self, module_name, token):
        assert token not in SOURCES[module_name].lower()


class TestAstBans:
    @pytest.mark.parametrize("module_name", list(MODULES))
    @pytest.mark.parametrize("node_type", [
        ast.Lambda, ast.While, ast.With, ast.AsyncWith,
        ast.Global, ast.Try, ast.AsyncFunctionDef, ast.Await,
        ast.Yield, ast.YieldFrom])
    def test_no_banned_node(self, module_name, node_type):
        found = [node for node in ast.walk(TREES[module_name])
                 if isinstance(node, node_type)]
        assert found == []

    @pytest.mark.parametrize("module_name", list(MODULES))
    def test_no_float_literal(self, module_name):
        floats = [node for node in ast.walk(TREES[module_name])
                  if isinstance(node, ast.Constant) and
                  isinstance(node.value, float)]
        assert floats == []


class TestImmutableModels:
    @pytest.mark.parametrize("model", MODEL_CLASSES)
    def test_frozen_dataclass(self, model):
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True

    @pytest.mark.parametrize("model", MODEL_CLASSES)
    def test_slots(self, model):
        assert "__slots__" in model.__dict__
        assert "__dict__" not in model.__slots__

    @pytest.mark.parametrize("model", MODEL_CLASSES)
    def test_no_inheritance(self, model):
        assert model.__bases__ == (object,)

    @pytest.mark.parametrize("model", MODEL_CLASSES)
    def test_has_post_init_validation(self, model):
        assert hasattr(model, "__post_init__")

    def test_no_mutable_default_collections(self):
        for model in MODEL_CLASSES:
            for field in fields(model):
                assert not isinstance(field.default,
                                      (list, dict, set))


class TestClosedEnums:
    @pytest.mark.parametrize("enum_class", ENUM_CLASSES)
    def test_value_equals_name(self, enum_class):
        assert issubclass(enum_class, Enum)
        for member in enum_class:
            assert member.value == member.name

    def test_operation_members_exact(self):
        assert {m.name for m in ControlledExecutionOperation} \
            == {"SUBMIT", "CANCEL", "STATUS", "POSITIONS",
                "ORDERS", "EXECUTIONS", "STATISTICS",
                "HEARTBEAT"}

    def test_decision_members_exact(self):
        assert {m.name for m in
                ControlledExecutionAPIDecision} == \
            {"ACCEPTED", "DENIED", "RECOMMENDATION_ONLY",
             "REPORTED"}


class TestDecisionMapClosure:
    def test_paper_map_covers_all_decisions(self):
        assert set(_PAPER_DECISION_MAP.keys()) == \
            set(PaperExecutionDecision)

    def test_shadow_map_covers_all_decisions(self):
        assert set(_SHADOW_DECISION_MAP.keys()) == \
            set(ShadowDecision)

    def test_micro_map_covers_all_decisions(self):
        assert set(_MICRO_DECISION_MAP.keys()) == \
            set(MicroLiveDecision)

    def test_maps_target_api_decisions_only(self):
        for mapping in (_PAPER_DECISION_MAP,
                        _SHADOW_DECISION_MAP,
                        _MICRO_DECISION_MAP):
            for target in mapping.values():
                assert isinstance(
                    target, ControlledExecutionAPIDecision)

    def test_denials_never_map_to_accepted(self):
        denied = ControlledExecutionAPIDecision.DENIED
        assert _PAPER_DECISION_MAP[
            PaperExecutionDecision.DENIED] is denied
        assert _SHADOW_DECISION_MAP[
            ShadowDecision.DENIED] is denied
        assert _MICRO_DECISION_MAP[
            MicroLiveDecision.DENIED] is denied
        assert _MICRO_DECISION_MAP[
            MicroLiveDecision.NOT_AUTHORIZED] is denied


class TestDependencyDirection:
    def test_models_do_not_import_services(self):
        names = _imports(TREES[
            "controlled_execution_api_models.py"])
        for banned in ("paper_execution_service", "shadow_mode",
                       "micro_live_authorization",
                       "controlled_execution_api",
                       "controlled_execution_router"):
            assert banned not in names

    def test_errors_import_nothing_domain(self):
        names = _imports(TREES[
            "controlled_execution_api_errors.py"])
        assert names <= {"__future__"}

    def test_router_does_not_import_api(self):
        names = _imports(TREES[
            "controlled_execution_router.py"])
        assert "controlled_execution_api" not in names
        assert "controlled_execution_api_models" not in names

    def test_api_does_not_import_broker_or_foundation(self):
        names = _imports(TREES["controlled_execution_api.py"])
        assert "paper_broker" not in names
        assert "controlled_execution_foundation" not in names

    def test_foundation_files_untouched(self):
        # A01 dosyaları Agent 08 tarafından sahiplenilmez.
        source = (ROOT /
                  "controlled_execution_models.py").read_text(
            encoding="utf-8")
        assert "class ControlledExecutionMode" in source
        assert "ControlledExecutionAPI" not in source


class TestErrorHierarchy:
    @pytest.mark.parametrize("error_class", [
        ControlledExecutionAPIContractError,
        ControlledExecutionAPIConfigurationError,
        ControlledExecutionAPIModeError,
        ControlledExecutionAPIRoutingError])
    def test_rooted_at_api_error(self, error_class):
        assert issubclass(error_class,
                          ControlledExecutionAPIError)

    def test_root_is_exception(self):
        assert ControlledExecutionAPIError.__bases__ == \
            (Exception,)

    def test_turkish_docstrings(self):
        for module in MODULES.values():
            assert module.__doc__
            assert "Mission 2100" in module.__doc__
