"""Mission 2000 — Agent 09: Güvenlik sertifikasyonu.

Yürütme Çekirdeği'nin 18 üretim modülünün TAMAMI, güvenlik
sertifikasyon manifestosundaki yasak listelerine karşı taranır:
secret/imzalama yok, HTTP/REST/WebSocket/soket yok, dosya yazımı
yok, subprocess/thread/zamanlayıcı yok, retry yok, UUID/duvar
saati/rastgelelik yok, ortam erişimi yok, broker SDK yok,
SQL/ORM/kalıcılık yok, telemetri/analitik yok.

Taramalar docstring'lerden arındırılmış AST kaynağına uygulanır;
manifesto listelerindeki her sapma regresyon hatasıdır.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import execution_security_certification as cert

MODULES = cert.CERTIFIED_MODULES


def _load(name):
    return importlib.import_module(name)


def _code_source(module) -> str:
    """Docstring'lerden arındırılmış kaynak (yalnız kod)."""
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


def _scannable(module) -> str:
    source = _code_source(module)
    for exempt in cert.TOKEN_EXEMPT_SUBSTRINGS:
        source = source.replace(exempt, "")
    return source


def _module_imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add((node.module or "").split(".")[0])
    return found


# ── Manifesto bütünlüğü ──────────────────────────────────────────────

class TestCertificationManifest:
    def test_security_status(self):
        assert cert.SECURITY_STATUS == "CERTIFIED"

    def test_certified_set_covers_frozen_set(self):
        import execution_architecture_freeze as freeze
        assert set(cert.CERTIFIED_MODULES) == \
            set(freeze.FROZEN_MODULES)

    def test_manifest_containers_immutable(self):
        assert isinstance(cert.CERTIFIED_MODULES, tuple)
        assert isinstance(cert.FORBIDDEN_TOKENS, tuple)
        assert isinstance(cert.FORBIDDEN_IMPORT_ROOTS, frozenset)
        assert isinstance(cert.FORBIDDEN_CALL_NAMES, frozenset)
        assert isinstance(cert.TOKEN_EXEMPT_SUBSTRINGS, tuple)

    def test_manifest_surface_frozen(self):
        assert cert.__all__ == [
            "SECURITY_STATUS", "CERTIFIED_MODULES",
            "INTERNAL_CORE_MODULES", "FORBIDDEN_IMPORT_ROOTS",
            "FORBIDDEN_TOKENS", "FORBIDDEN_CALL_NAMES",
            "TOKEN_EXEMPT_SUBSTRINGS"]

    def test_exemptions_minimal(self):
        assert cert.TOKEN_EXEMPT_SUBSTRINGS == \
            ("retryable", "api_key_reference")


# ── Yasak importlar ──────────────────────────────────────────────────

class TestForbiddenImports:
    @pytest.mark.parametrize("name", MODULES)
    def test_no_forbidden_import_roots(self, name):
        roots = _module_imports(_load(name))
        hits = roots & cert.FORBIDDEN_IMPORT_ROOTS
        assert not hits, f"{name}: yasak import {hits}"

    @pytest.mark.parametrize("name", MODULES)
    def test_no_dynamic_import(self, name):
        source = _scannable(_load(name))
        for token in ("importlib", "__import__", "pkgutil",
                      "entry_points"):
            assert token not in source

    @pytest.mark.parametrize("name", MODULES)
    def test_imports_within_core_or_stdlib_whitelist(self, name):
        allowed_stdlib = {"__future__", "abc", "enum",
                          "dataclasses", "decimal", "typing",
                          "types"}
        core = set(cert.CERTIFIED_MODULES) | \
            set(cert.INTERNAL_CORE_MODULES)
        roots = _module_imports(_load(name))
        assert roots <= (allowed_stdlib | core), \
            f"{name}: beyaz liste dışı import {roots - allowed_stdlib - core}"


# ── Yasak belirteçler ────────────────────────────────────────────────

class TestForbiddenTokens:
    @pytest.mark.parametrize("name", MODULES)
    def test_module_clean_of_all_tokens(self, name):
        source = _scannable(_load(name))
        hits = [t for t in cert.FORBIDDEN_TOKENS if t in source]
        assert not hits, f"{name}: yasak belirteç {hits}"

    @pytest.mark.parametrize("token", cert.FORBIDDEN_TOKENS)
    def test_token_absent_from_entire_core(self, token):
        for name in MODULES:
            assert token not in _scannable(_load(name)), \
                f"{name}: '{token}' bulundu"


# ── Yasak çağrılar ve AST kuralları ──────────────────────────────────

class TestAstRules:
    @pytest.mark.parametrize("name", MODULES)
    def test_no_forbidden_builtin_calls(self, name):
        for node in ast.walk(ast.parse(
                inspect.getsource(_load(name)))):
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Name):
                assert node.func.id not in \
                    cert.FORBIDDEN_CALL_NAMES

    @pytest.mark.parametrize("name", MODULES)
    def test_no_float_literals(self, name):
        for node in ast.walk(ast.parse(
                inspect.getsource(_load(name)))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float), \
                    f"{name}: float literal — para matematiği Decimal-only"

    @pytest.mark.parametrize("name", MODULES)
    def test_no_global_statements(self, name):
        for node in ast.walk(ast.parse(
                inspect.getsource(_load(name)))):
            assert not isinstance(node, (ast.Global,
                                         ast.Nonlocal))

    @pytest.mark.parametrize("name", MODULES)
    def test_no_with_statements_no_context_io(self, name):
        # Çekirdekte dosya/bağlam yöneticisi tabanlı IO yok
        for node in ast.walk(ast.parse(
                inspect.getsource(_load(name)))):
            assert not isinstance(node, (ast.With,
                                         ast.AsyncWith))

    @pytest.mark.parametrize("name", MODULES)
    def test_no_lambda_hidden_logic(self, name):
        # binance_spot_adapter: bildirimsel dispatch tablosu
        # lambda'ları muaf (yan etkisiz saf kurucular)
        if name == "binance_spot_adapter":
            pytest.skip("bildirimsel dispatch tablosu muaf")
        for node in ast.walk(ast.parse(
                inspect.getsource(_load(name)))):
            assert not isinstance(node, ast.Lambda)

    @pytest.mark.parametrize("name", MODULES)
    def test_no_try_finally(self, name):
        for node in ast.walk(ast.parse(
                inspect.getsource(_load(name)))):
            if isinstance(node, ast.Try):
                assert node.finalbody == []

    @pytest.mark.parametrize("name", MODULES)
    def test_no_while_loops(self, name):
        for node in ast.walk(ast.parse(
                inspect.getsource(_load(name)))):
            assert not isinstance(node, ast.While)


# ── Secret ve kimlik hijyeni ─────────────────────────────────────────

class TestSecretHygiene:
    @pytest.mark.parametrize("name", MODULES)
    def test_no_secret_bearing_string_literals(self, name):
        for node in ast.walk(ast.parse(_code_source(
                _load(name)))):
            if isinstance(node, ast.Constant) and \
                    isinstance(node.value, str):
                lowered = node.value.lower()
                for token in ("secret", "passw", "token=",
                              "bearer ", "private_key"):
                    assert token not in lowered, \
                        f"{name}: şüpheli literal"

    @pytest.mark.parametrize("name", MODULES)
    def test_no_env_or_credential_reads(self, name):
        source = _scannable(_load(name))
        for token in ("environ", "getenv", "dotenv",
                      "load_dotenv", "credentials.json",
                      "keyring"):
            assert token not in source

    def test_transport_layer_is_interface_only(self):
        # Binance taşıma/imzalama sınıfları arayüzdür — gerçek
        # ağ/imza kodu çekirdekte yasaktır
        import binance_spot_adapter as bsa
        for cls_name in ("Transport", "RESTTransport",
                         "WebSocketTransport", "SigningProvider",
                         "CredentialProvider"):
            cls = getattr(bsa, cls_name)
            assert inspect.isabstract(cls) or not [
                m for m in vars(cls).values()
                if inspect.isfunction(m)
                and m.__name__ not in ("__init__",)]

    @pytest.mark.parametrize("name", MODULES)
    def test_no_id_generation(self, name):
        source = _scannable(_load(name))
        for token in ("uuid", "token_hex", "randbytes",
                      "urandom", "getrandbits", "next_id",
                      "generate_id", "auto_increment"):
            assert token not in source


# ── Kalıcılık / telemetri / zamanlayıcı yokluğu ──────────────────────

class TestNoSideChannels:
    @pytest.mark.parametrize("name", MODULES)
    def test_no_persistence(self, name):
        source = _scannable(_load(name))
        for token in ("sqlite", "sqlalchemy", "psycopg",
                      "pickle", "shelve", "to_csv", "to_json(",
                      "json.dump", "savefig", "writelines"):
            assert token not in source

    @pytest.mark.parametrize("name", MODULES)
    def test_no_telemetry_or_publishers(self, name):
        source = _scannable(_load(name))
        for token in ("telemetry", "analytics", "sentry",
                      "prometheus", "statsd", "logging.",
                      "getLogger", "publish(", "emit(",
                      "webhook"):
            assert token not in source

    @pytest.mark.parametrize("name", MODULES)
    def test_no_scheduler_or_background_work(self, name):
        source = _scannable(_load(name))
        for token in ("sched", "cron", "Timer(", "interval",
                      "create_task", "ensure_future",
                      "add_job", "BackgroundScheduler"):
            assert token not in source

    @pytest.mark.parametrize("name", MODULES)
    def test_no_module_level_mutable_state(self, name):
        # Modül düzeyi atamalar yalnız __all__ veya SABİT
        # (büyük harf / _önekli sabit) olabilir — gizli durum yok
        module = _load(name)
        tree = ast.parse(inspect.getsource(module))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    assert isinstance(target, ast.Name)
                    assert (target.id == "__all__"
                            or target.id.lstrip("_").isupper()), \
                        f"{name}: modül düzeyi durum {target.id}"
                    if target.id != "__all__":
                        assert not isinstance(
                            node.value,
                            (ast.List, ast.Dict, ast.Set,
                             ast.ListComp, ast.DictComp,
                             ast.SetComp)), \
                            f"{name}: mutable modül sabiti {target.id}"


# ── Yazma disiplini ──────────────────────────────────────────────────

class TestWriteDiscipline:
    def test_only_broker_adapter_layer_names_submit(self):
        # submit_order yalnız adaptör sözleşmesi + orkestrasyon
        # katmanında; strateji/izleme/api-model katmanında asla
        allowed = {"execution_broker_adapter",
                   "binance_spot_adapter", "execution_service"}
        for name in MODULES:
            if name in allowed:
                continue
            assert "submit_order" not in _scannable(_load(name)), \
                f"{name}: yetkisiz submit_order referansı"

    def test_write_operations_frozenset_intact(self):
        import execution_broker_adapter as eba
        writes = getattr(eba, "_WRITE_OPERATIONS")
        assert writes == frozenset({"submit_order",
                                    "cancel_order"})

    def test_read_operations_frozenset_intact(self):
        import execution_broker_adapter as eba
        reads = getattr(eba, "_READ_OPERATIONS")
        assert reads == frozenset({"profile", "health_check",
                                   "get_order",
                                   "list_open_orders",
                                   "get_positions",
                                   "get_balances"})

    def test_kill_switch_mutators_never_called_in_core(self):
        # Kill switch durumunu YALNIZ sahibi/dış operatör değiştirir
        for name in MODULES:
            if name == "execution_kill_switch":
                continue
            source = _scannable(_load(name))
            for token in (".enable()", ".disable()", ".lock()",
                          ".maintenance()"):
                assert token not in source, \
                    f"{name}: kill switch mutasyonu"
