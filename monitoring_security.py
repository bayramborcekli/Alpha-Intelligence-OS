"""Mission 1900 — Monitoring Security Verifier (Agent 07).

YALNIZ doğrulama: tamamlanmış Monitoring yığınını (Core, Alert Engine,
Service, API, Export) statik AST + salt-okunur örnek kontrolleriyle
denetler ve immutable MonitoringSecurityReport döndürür.

Sözleşmeler:
- Üretim nesnesi DEĞİŞTİRİLMEZ, ihlal ONARILMAZ, Exchange ÇAĞRILMAZ;
  monkey patching yok, çalışma zamanı mutasyonu yok.
- Deterministik: aynı kod tabanı → bayt-özdeş rapor; zaman damgası/
  UUID/rastgelelik/önbellek yok.
- Sterile hata yüzeyi: yalnız SECURITY_VERIFICATION_FAILED
  (yol/iz/kaynak kodu/ortam/secret sızmaz); ihlaller yalnız sabit
  kural kodlarıyla raporlanır.
- Doğrulayıcının kendisinde Exchange/Broker/dosya yazımı/DB/ağ/
  zamanlayıcı/thread/ortam/secret erişimi/eval/exec YOKTUR.
"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping

import alert_engine
import monitoring_api
import monitoring_export
import monitoring_intelligence
import monitoring_service

SECURITY_VERSION = 1
ERROR_VERIFICATION = "SECURITY_VERIFICATION_FAILED"

# Rapor alan sırası (sabit)
SECURITY_REPORT_FIELDS = ("verified", "violations", "checked_rules",
                          "version")

# Yasak modüller (önek eşleşmesi — spec §6)
FORBIDDEN_MODULE_PREFIXES = (
    "exchange", "broker", "ccxt", "requests", "httpx", "urllib3",
    "urllib", "socket", "threading", "asyncio", "subprocess",
    "multiprocessing", "sqlite3", "sqlalchemy", "redis", "pickle",
    "shelve", "pathlib", "os", "sys", "dotenv", "secrets",
    "cryptography", "http", "ftplib", "smtplib", "sched", "signal",
    "ctypes", "importlib", "io", "tempfile", "shutil",
)

# Katman başına izinli import yüzeyi (stdlib + proje — kapalı liste)
ALLOWED_IMPORTS = MappingProxyType({
    "monitoring_intelligence": frozenset(
        {"__future__", "decimal", "types", "typing"}),
    "alert_engine": frozenset(
        {"__future__", "collections.abc", "decimal", "types", "typing",
         "monitoring_intelligence"}),
    "monitoring_service": frozenset(
        {"__future__", "types", "typing", "alert_engine",
         "monitoring_intelligence", "strategy_service"}),
    "monitoring_api": frozenset(
        {"__future__", "uuid", "datetime", "types", "typing",
         "monitoring_service"}),
    "monitoring_export": frozenset(
        {"__future__", "json", "decimal", "types", "typing",
         "monitoring_api"}),
})

# Katman bağımlılık grafiği (proje modülleri — yalnız aşağı yönlü)
ALLOWED_PROJECT_DEPS = MappingProxyType({
    "monitoring_intelligence": frozenset(),
    "alert_engine": frozenset({"monitoring_intelligence"}),
    "monitoring_service": frozenset(
        {"monitoring_intelligence", "alert_engine", "strategy_service"}),
    "monitoring_api": frozenset({"monitoring_service"}),
    "monitoring_export": frozenset({"monitoring_api"}),
})
PROJECT_MODULES = frozenset(ALLOWED_PROJECT_DEPS) | {"strategy_service"}

# Onaylı kamu yüzeyi (fonksiyon/sınıf — kapalı liste)
APPROVED_PUBLIC_API = MappingProxyType({
    "monitoring_intelligence": frozenset({"build_monitoring_report"}),
    "alert_engine": frozenset({"build_alert_report"}),
    "monitoring_service": frozenset(
        {"analyze_monitoring", "build_default_monitoring_providers",
         "MonitoringService"}),
    "monitoring_api": frozenset({"analyze_monitoring_api"}),
    "monitoring_export": frozenset(
        {"build_monitoring_export", "serialize_monitoring_export"}),
})

# Meta veri sahipliği: uuid/datetime yalnız API katmanında
METADATA_MODULES = frozenset({"uuid", "datetime", "time"})
METADATA_OWNER = "monitoring_api"
METADATA_FIELDS = frozenset({"report_id", "observed_at", "generated_at"})

# Tehlikeli çağrı adları (AST düzeyi)
FORBIDDEN_CALL_NAMES = frozenset({"eval", "exec", "open", "__import__",
                                  "compile", "input", "breakpoint"})

# Denetlenen kural kodları (sabit sıra — rapor determinizmi)
CHECKED_RULES = (
    "IMPORT_SURFACE",
    "FORBIDDEN_MODULES",
    "DANGEROUS_CALLS",
    "METADATA_OWNERSHIP",
    "METADATA_GENERATION",
    "DEPENDENCY_GRAPH",
    "PUBLIC_API_SURFACE",
    "IMMUTABLE_MODELS",
)

_STACK = MappingProxyType({
    "monitoring_intelligence": monitoring_intelligence,
    "alert_engine": alert_engine,
    "monitoring_service": monitoring_service,
    "monitoring_api": monitoring_api,
    "monitoring_export": monitoring_export,
})


def _fail() -> ValueError:
    # Sterile: yol/iz/kaynak/ortam sızmaz.
    return ValueError(ERROR_VERIFICATION)


# ── AST yardımcıları (salt-okunur) ───────────────────────────────────

def _imported_modules(tree: ast.AST) -> frozenset[str]:
    """Modül düzeyinde + fonksiyon içi TÜM importlar (tam ad)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return frozenset(found)


def _check_import_surface(name: str, modules: frozenset[str],
                          allowed: Mapping[str, frozenset[str]]
                          ) -> tuple[str, ...]:
    extra = modules - allowed[name]
    if extra:
        return (f"IMPORT_SURFACE:{name}",)
    return ()


def _check_forbidden(name: str, modules: frozenset[str],
                     prefixes: tuple[str, ...]) -> tuple[str, ...]:
    for module in sorted(modules):
        root = module.split(".")[0]
        for prefix in prefixes:
            if root == prefix or root.startswith(prefix):
                return (f"FORBIDDEN_MODULE:{name}",)
    return ()


def _check_dangerous_calls(name: str, tree: ast.AST) -> tuple[str, ...]:
    """Doğrudan çağrı, öznitelik çağrısı ve TAKMA AD dahil yakalar.

    ``eval(...)``, ``builtins.__import__``, ``x = eval`` gibi dolaylı
    biçimler de ihlaldir (bypass'a kapalı).
    """
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name)
                and node.id in FORBIDDEN_CALL_NAMES):
            return (f"DANGEROUS_CALL:{name}",)
        if (isinstance(node, ast.Attribute)
                and node.attr in FORBIDDEN_CALL_NAMES):
            return (f"DANGEROUS_CALL:{name}",)
    return ()


def _check_metadata(name: str, modules: frozenset[str]
                    ) -> tuple[str, ...]:
    if name == METADATA_OWNER:
        return ()
    roots = {module.split(".")[0] for module in modules}
    if roots & METADATA_MODULES:
        return (f"METADATA_OWNERSHIP:{name}",)
    return ()


def _check_metadata_generation(name: str, tree: ast.AST
                               ) -> tuple[str, ...]:
    """API dışı katman meta veri alanı SENTEZLEYEMEZ (semantik).

    Dict literal'lerinde ``report_id``/``observed_at``/``generated_at``
    anahtarları yalnız ``None`` sabiti veya aynen taşıma (Name/
    Subscript/Attribute erişimi) olabilir; Call sonucu veya None
    olmayan sabit → ihlal.
    """
    if name == METADATA_OWNER:
        return ()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant)
                    and key.value in METADATA_FIELDS):
                continue
            if isinstance(value, ast.Constant) and value.value is None:
                continue  # dürüst null
            if isinstance(value, (ast.Name, ast.Subscript,
                                  ast.Attribute)):
                continue  # aynen taşıma (passthrough)
            return (f"METADATA_GENERATION:{name}",)
    return ()


def _check_dependencies(name: str, modules: frozenset[str],
                        allowed_deps: Mapping[str, frozenset[str]]
                        ) -> tuple[str, ...]:
    project = {module.split(".")[0] for module in modules} & (
        PROJECT_MODULES | frozenset(allowed_deps))
    if project - allowed_deps[name]:
        return (f"DEPENDENCY_GRAPH:{name}",)
    return ()


def _public_names(module: Any) -> frozenset[str]:
    """Kamu çağrılabilirleri: modülde tanımlı + PROJE re-export'ları.

    Stdlib içe aktarımları (Decimal, MappingProxyType, ...) yüzey
    sayılmaz; ancak proje modüllerinden takma ad/yeniden dışa aktarma
    kamu yüzeyine girer (gizli giriş noktası bypass'ına kapalı).
    """
    names: set[str] = set()
    for attr in dir(module):
        if attr.startswith("_"):
            continue
        value = getattr(module, attr)
        if not (inspect.isfunction(value) or inspect.isclass(value)):
            continue
        origin = getattr(value, "__module__", None)
        if origin == module.__name__ or origin in PROJECT_MODULES:
            names.add(attr)
    return frozenset(names)


def _check_public_api(name: str, module: Any,
                      approved: Mapping[str, frozenset[str]]
                      ) -> tuple[str, ...]:
    if _public_names(module) != approved[name]:
        return (f"PUBLIC_API_SURFACE:{name}",)
    return ()


# ── Immutability (salt-okunur örnekler; üretim nesnesi mutasyonsuz) ──

def _is_deeply_immutable(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, Decimal)):
        return True
    if isinstance(value, tuple):
        return all(_is_deeply_immutable(item) for item in value)
    if isinstance(value, MappingProxyType):
        return all(_is_deeply_immutable(item) for item in value.values())
    return False


def _check_immutability() -> tuple[str, ...]:
    violations: list[str] = []
    report = monitoring_intelligence.build_monitoring_report(
        {"strategy_version":
             monitoring_intelligence.SUPPORTED_STRATEGY_VERSION,
         "analysis_version":
             monitoring_intelligence.SUPPORTED_ANALYSIS_VERSION})
    if not _is_deeply_immutable(report):
        violations.append("IMMUTABLE_MODELS:monitoring_report")
    alert_report = alert_engine.build_alert_report(report)
    if not _is_deeply_immutable(alert_report):
        violations.append("IMMUTABLE_MODELS:alert_report")
    export = monitoring_export.build_monitoring_export({
        "api_version": 1,
        "report_id": "00000000-0000-4000-8000-000000000000",
        "observed_at": "2026-01-01T00:00:00+00:00",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "monitoring_analysis": None,
        "status": "FAILED",
        "limitations": ("MONITORING_ANALYSIS_ERROR",),
    })
    if not _is_deeply_immutable(export):
        violations.append("IMMUTABLE_MODELS:monitoring_export")
    return tuple(violations)


# ── Kamu sözleşmesi ──────────────────────────────────────────────────

def verify_monitoring_security() -> MappingProxyType:
    """Deterministik, salt-okunur güvenlik doğrulaması.

    Dönen MonitoringSecurityReport immutable'dır; ``verified`` yalnız
    ``violations == ()`` iken True olur. Arıza → sterile
    ``ValueError("SECURITY_VERIFICATION_FAILED")``.
    """
    try:
        violations: list[str] = []
        for name, module in _STACK.items():
            tree = ast.parse(inspect.getsource(module))
            modules = _imported_modules(tree)
            violations += _check_import_surface(
                name, modules, ALLOWED_IMPORTS)
            violations += _check_forbidden(
                name, modules, FORBIDDEN_MODULE_PREFIXES)
            violations += _check_dangerous_calls(name, tree)
            violations += _check_metadata(name, modules)
            violations += _check_metadata_generation(name, tree)
            violations += _check_dependencies(
                name, modules, ALLOWED_PROJECT_DEPS)
            violations += _check_public_api(
                name, module, APPROVED_PUBLIC_API)
        violations += _check_immutability()
        ordered = tuple(sorted(set(violations)))
        return MappingProxyType({
            "verified": ordered == (),
            "violations": ordered,
            "checked_rules": CHECKED_RULES,
            "version": SECURITY_VERSION,
        })
    except BaseException:
        raise _fail() from None
