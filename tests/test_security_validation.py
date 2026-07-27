"""Mission 2100 — Agent 09: Güvenlik doğrulama testleri.

Sözleşme, MISSION_2100_MODULES kümesindeki CANLI kaynak koduna
uygulanır: yasak import, yasak çağrı (eval/exec/dinamik import/
dosya-süreç), yasak belirteç (secret/ağ/ortam/zaman/rastgelelik/
log) ve tarayıcının kendisinin doğruluğu.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import security_validation as sv  # noqa: E402

MODULES = sv.MISSION_2100_MODULES
SOURCES = {name: (ROOT / f"{name}.py").read_text(
    encoding="utf-8") for name in MODULES}
STRIPPED = {name: sv.strip_docstrings(source)
            for name, source in SOURCES.items()}


class TestModuleSet:
    def test_module_count(self):
        assert len(MODULES) == 31

    def test_no_duplicates(self):
        assert len(set(MODULES)) == len(MODULES)

    @pytest.mark.parametrize("module_name", MODULES)
    def test_module_file_exists(self, module_name):
        assert (ROOT / f"{module_name}.py").is_file()


class TestForbiddenImports:
    @pytest.mark.parametrize("module_name", MODULES)
    def test_no_forbidden_imports(self, module_name):
        findings = sv.find_forbidden_imports(
            module_name, SOURCES[module_name])
        assert findings == ()

    @pytest.mark.parametrize("module_name", MODULES)
    @pytest.mark.parametrize("root", sorted((
        "os", "subprocess", "socket", "requests", "pickle",
        "threading", "multiprocessing", "importlib", "ctypes",
        "sqlite3", "uuid", "random", "datetime", "binance",
        "ccxt", "flask", "logging")))
    def test_specific_root_absent(self, module_name, root):
        roots = sv.collect_import_roots(
            sv.parse_source(SOURCES[module_name]))
        assert root not in roots


class TestForbiddenCalls:
    @pytest.mark.parametrize("module_name", MODULES)
    def test_no_forbidden_calls(self, module_name):
        findings = sv.find_forbidden_calls(
            module_name, SOURCES[module_name])
        assert findings == ()

    @pytest.mark.parametrize("module_name", MODULES)
    @pytest.mark.parametrize("banned", sorted(
        sv.FORBIDDEN_CALL_NAMES))
    def test_specific_call_absent(self, module_name, banned):
        import ast
        for node in ast.walk(
                sv.parse_source(SOURCES[module_name])):
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Name):
                assert node.func.id != banned


class TestForbiddenTokens:
    @pytest.mark.parametrize("module_name", MODULES)
    @pytest.mark.parametrize("token", sv.FORBIDDEN_TOKENS)
    def test_token_absent(self, module_name, token):
        assert token not in STRIPPED[module_name]

    @pytest.mark.parametrize("module_name", MODULES)
    def test_full_report_clean(self, module_name):
        report = sv.validate_module_source(
            module_name, SOURCES[module_name])
        assert report.clean
        assert report.module_name == module_name


class TestNoExchangeWrite:
    @pytest.mark.parametrize("module_name", MODULES)
    @pytest.mark.parametrize("fragment", [
        "create" + "_order(self", "place" + "_order(",
        "new" + "_order(", "order" + "_market(",
        "transfer(", "withdraw("])
    def test_no_live_order_fragments(self, module_name,
                                     fragment):
        assert fragment not in STRIPPED[module_name]


class TestScannerCorrectness:
    """Tarayıcı sahte-temiz OLMAMALI: bilinen kötü kaynakları
    yakaladığı kanıtlanır."""

    def test_detects_forbidden_import(self):
        findings = sv.find_forbidden_imports(
            "bad", "import os\n")
        assert findings != ()
        assert findings[0].category == "FORBIDDEN_IMPORT"
        assert findings[0].detail == "os"

    def test_detects_from_import(self):
        findings = sv.find_forbidden_imports(
            "bad", "from subprocess import run\n")
        assert findings[0].detail == "subprocess"

    def test_detects_aliased_import(self):
        """Takma ad gizleme İŞE YARAMAZ: kök ad yakalanır."""
        findings = sv.find_forbidden_imports(
            "bad", "import os as harmless\n")
        assert findings[0].detail == "os"

    def test_detects_aliased_from_import(self):
        findings = sv.find_forbidden_imports(
            "bad", "from pickle import loads as parse\n")
        assert findings[0].detail == "pickle"

    def test_detects_submodule_import(self):
        findings = sv.find_forbidden_imports(
            "bad", "import os.path\n")
        assert findings[0].detail == "os"

    def test_detects_builtins_indirection(self):
        """builtins üzerinden eval/exec dolaylaması: kök import
        yasağıyla kapanır."""
        findings = sv.find_forbidden_imports(
            "bad", "import builtins\n")
        assert findings[0].detail == "builtins"

    def test_detects_importlib_indirection(self):
        findings = sv.find_forbidden_imports(
            "bad", "import importlib\n")
        assert findings[0].detail == "importlib"

    @pytest.mark.parametrize("call", ["eval", "exec",
                                      "__import__", "open",
                                      "compile"])
    def test_detects_forbidden_call(self, call):
        findings = sv.find_forbidden_calls(
            "bad", f"x = {call}('data')\n")
        assert findings != ()
        assert findings[0].detail == call

    def test_detects_attribute_call(self):
        findings = sv.find_forbidden_calls(
            "bad", "m.import_module('os')\n")
        assert findings[0].category == \
            "FORBIDDEN_ATTRIBUTE_CALL"

    @pytest.mark.parametrize("token", ["api_key", "password",
                                       "https://",
                                       "os.environ",
                                       ".now(", "sleep("])
    def test_detects_forbidden_token(self, token):
        source = f'value = "{token}"\n'
        findings = sv.find_forbidden_tokens("bad", source)
        assert any(f.detail == token for f in findings)

    def test_docstrings_exempt(self):
        source = '"""api_key belgeleme örneği."""\nx = 1\n'
        assert sv.find_forbidden_tokens("ok", source) == ()

    def test_clean_source_clean_report(self):
        report = sv.validate_module_source("ok", "x = 1\n")
        assert report.clean

    def test_report_immutable(self):
        report = sv.validate_module_source("ok", "x = 1\n")
        with pytest.raises(Exception):
            report.findings = ("mutated",)

    def test_finding_immutable(self):
        finding = sv.SecurityFinding(
            module_name="m", category="c", detail="d")
        with pytest.raises(Exception):
            finding.detail = "x"

    def test_deterministic_scan(self):
        name = MODULES[0]
        first = sv.validate_module_source(name, SOURCES[name])
        second = sv.validate_module_source(name, SOURCES[name])
        assert first == second


class TestContractSets:
    def test_forbidden_roots_frozen(self):
        assert isinstance(sv.FORBIDDEN_IMPORT_ROOTS, frozenset)

    def test_forbidden_calls_frozen(self):
        assert isinstance(sv.FORBIDDEN_CALL_NAMES, frozenset)

    def test_tokens_tuple(self):
        assert isinstance(sv.FORBIDDEN_TOKENS, tuple)

    @pytest.mark.parametrize("root", ["os", "subprocess",
                                      "pickle", "importlib",
                                      "threading", "socket",
                                      "requests", "binance"])
    def test_critical_roots_covered(self, root):
        assert root in sv.FORBIDDEN_IMPORT_ROOTS

    @pytest.mark.parametrize("call", ["eval", "exec",
                                      "__import__", "open"])
    def test_critical_calls_covered(self, call):
        assert call in sv.FORBIDDEN_CALL_NAMES
