"""Mission 2200 — Agent 01: güvenlik testleri.

- Operasyon kontrol modülleri borsa/ağ katmanına import düzeyinde
  ASLA dokunmaz; dinamik import/eval yoktur.
- Sır/kimlik bilgisi belirteçleri kaynakta bulunmaz.
- Kimliksiz istekler API'ye erişemez; denetim zinciri sır reddeder.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

OPERATION_MODULES = (
    "operation_control_errors", "operation_control_models",
    "operation_control_policy", "operation_control_audit",
    "operation_control_mapper", "operation_control_snapshot",
    "operation_control_service", "operation_control_api")

# Ağ/borsa/işletim yüzeyi — operasyon katmanında yasak.
FORBIDDEN_ROOTS = frozenset({
    "requests", "httpx", "aiohttp", "urllib", "http", "socket",
    "ssl", "websockets", "websocket", "ccxt", "binance",
    "ib_insync", "subprocess", "os", "sys", "importlib",
    "ctypes", "pickle", "marshal", "flask", "app"})

FORBIDDEN_SOURCE_TOKENS = (
    "api_key", "API_KEY", "api_secret", "API_SECRET",
    "passphrase", "password", "credential", "x-mbx-apikey",
    "http://", "https://", "wss://", "ws://", "os.environ",
    "getenv", "eval(", "exec(", "__import__", "open(",
    "create_order(", "new_order(", "cancel_order(")


def module_source(name: str) -> str:
    return (ROOT / f"{name}.py").read_text(encoding="utf-8")


def strip_docstrings(source: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                for lineno in range(body[0].lineno - 1,
                                    body[0].end_lineno):
                    lines[lineno] = ""
    return "\n".join(lines)


def import_roots(source: str) -> frozenset:
    tree = ast.parse(source)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return frozenset(roots)


class TestModuleImportHygiene:
    @pytest.mark.parametrize("name", OPERATION_MODULES)
    def test_no_forbidden_import_roots(self, name):
        hits = import_roots(module_source(name)) & \
            FORBIDDEN_ROOTS
        assert hits == frozenset(), f"{name}: {sorted(hits)}"

    @pytest.mark.parametrize("name", OPERATION_MODULES)
    def test_no_forbidden_tokens(self, name):
        source = strip_docstrings(module_source(name))
        # Denetim modülü yasak belirteçleri VERİ olarak tanımlar.
        if name == "operation_control_audit":
            source = source.replace(
                '"api_key", "api-key", "apikey", "secret", '
                '"password",', "").replace(
                '"authorization:", "bearer ", "token=", '
                '"x-mbx-apikey",', "")
        hits = [t for t in FORBIDDEN_SOURCE_TOKENS
                if t in source]
        assert hits == [], f"{name}: {hits}"

    @pytest.mark.parametrize("name", OPERATION_MODULES)
    def test_no_dynamic_import_or_eval_calls(self, name):
        tree = ast.parse(module_source(name))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Name):
                assert node.func.id not in (
                    "eval", "exec", "compile", "__import__",
                    "open", "input"), name

    @pytest.mark.parametrize("name", OPERATION_MODULES)
    def test_no_flask_dependency(self, name):
        """Sunum katmanı framework'ten bağımsızdır (import yok)."""
        assert "flask" not in import_roots(module_source(name))


class TestAuthenticationBoundary:
    @pytest.fixture()
    def anon_client(self):
        import app as app_module
        original = app_module.app.config.get("TESTING", False)
        app_module.app.config["TESTING"] = False
        with app_module.app.test_client() as client:
            yield client
        app_module.app.config["TESTING"] = original

    @pytest.mark.parametrize("path", [
        "/operation-center",
        "/api/operation-control/status",
        "/api/operation-control/positions",
        "/api/operation-control/audit"])
    def test_anonymous_get_denied(self, anon_client, path):
        response = anon_client.get(path)
        assert response.status_code in (302, 401, 403)

    @pytest.mark.parametrize("path", [
        "/api/operation-control/automation/start",
        "/api/operation-control/global/kill-switch",
        "/api/operation-control/global/request-close-all"])
    def test_anonymous_post_denied(self, anon_client, path):
        response = anon_client.post(path, json={})
        assert response.status_code in (302, 400, 401, 403)
        # Kimliksiz istek hiçbir eylem zarfı almaz.
        payload = response.get_json(silent=True)
        if payload is not None:
            assert payload.get("ok") is not True


class TestSterileErrors:
    def test_error_envelope_never_carries_raw_exception(self):
        import operation_control_api as oca
        payload, _ = oca.error_envelope(
            "MALFORMED_REQUEST", "Geçersiz istek gövdesi.",
            "c-1", 1)
        text = str(payload).lower()
        for token in ("traceback", "secret", "api_key",
                      "password"):
            assert token not in text

    def test_audit_rejects_secret_material(self):
        from operation_control_audit import OperationAuditTrail
        from operation_control_errors import (
            OperationControlAuditError)
        from tests.test_operation_control_models import (
            valid_audit)
        trail = OperationAuditTrail()
        with pytest.raises(OperationControlAuditError):
            trail.append(valid_audit(
                reason="header X-MBX-APIKEY leaked"))

    def test_confirmation_phrase_not_a_secret(self):
        # Onay ifadesi kasıtlı olarak halka açıktır; sır değildir.
        from operation_control_service import CONFIRMATION_PHRASE
        assert CONFIRMATION_PHRASE == "ONAYLIYORUM"
