"""Mission 2100 — Agent 09: Güvenlik doğrulama sözleşmesi.

Bu modül YENİ iş işlevi içermez, Agents 01–08 modüllerini
DEĞİŞTİRMEZ. Mission 2100 üretim modül kümesini ve güvenlik
sözleşmesini bildirimsel olarak dondurur ve VERİLEN kaynak metin
üzerinde çalışan saf tarama işlevleri sunar (dosya sistemi
erişimi YOK — kaynak metni test katmanı okur ve buraya geçirir).

Kapsam: borsa yazımı yok; secret/API anahtarı sızıntısı yok;
kimlik bilgisi loglaması yok; dosya/veritabanı yazımı yok; ortam
mutasyonu yok; dinamik import yok; eval/exec/pickle/subprocess
yok; thread/process sızıntısı yok.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Tuple

__all__ = ["MISSION_2100_MODULES", "FORBIDDEN_IMPORT_ROOTS",
           "FORBIDDEN_TOKENS", "FORBIDDEN_CALL_NAMES",
           "FORBIDDEN_ATTRIBUTE_CALLS", "SecurityFinding",
           "SecurityReport", "parse_source",
           "strip_docstrings", "collect_import_roots",
           "find_forbidden_imports", "find_forbidden_calls",
           "find_forbidden_tokens", "validate_module_source"]

# Mission 2100 sertifikalı üretim modülleri (Agents 01–08)
MISSION_2100_MODULES = (
    # Agent 01 — Foundation
    "controlled_execution_models",
    "controlled_execution_errors",
    "controlled_execution_policy",
    "controlled_execution_foundation",
    # Agent 02 — Runtime katmanı
    "runtime_enums",
    "runtime_errors",
    "runtime_models",
    # Agents 03–04 — Paper katmanı
    "paper_models",
    "paper_errors",
    "paper_ledger",
    "paper_broker",
    "paper_execution_models",
    "paper_execution_errors",
    "paper_execution_mapper",
    "paper_execution_service",
    # Agent 05 — Shadow
    "shadow_models",
    "shadow_errors",
    "shadow_comparator",
    "shadow_mode",
    # Agent 06 — Micro-Live yetkilendirme
    "micro_live_models",
    "micro_live_errors",
    "micro_live_policy",
    "micro_live_authorization",
    # Agent 07 — Yaşam döngüsü & mutabakat
    "lifecycle_models",
    "order_lifecycle",
    "reconciliation_errors",
    "reconciliation",
    # Agent 08 — Birleşik API
    "controlled_execution_api_models",
    "controlled_execution_api_errors",
    "controlled_execution_router",
    "controlled_execution_api",
)

# Yasak import kökleri: ağ, kalıcılık, eşzamanlılık, zaman,
# rastgelelik, ortam, seri hale getirme, broker SDK, web çatısı.
FORBIDDEN_IMPORT_ROOTS = frozenset({
    "os", "sys", "io", "pathlib", "shutil", "tempfile", "glob",
    "socket", "ssl", "http", "urllib", "requests", "httpx",
    "aiohttp", "websockets", "websocket", "flask", "fastapi",
    "django", "uuid", "datetime", "time", "random", "secrets",
    "hashlib", "hmac", "base64", "threading",
    "multiprocessing", "subprocess", "sched", "signal",
    "queue", "asyncio", "json", "pickle", "shelve", "sqlite3",
    "sqlalchemy", "psycopg2", "redis", "logging", "ccxt",
    "binance", "ib_insync", "importlib", "ctypes", "marshal",
    "builtins", "inspect", "gc",
    "app", "risk_api", "monitoring_service",
})

# Yasak kod belirteçleri (docstring'ler arındırılmış kaynakta)
FORBIDDEN_TOKENS = (
    "api_key", "API_KEY", "api_secret", "API_SECRET",
    "passphrase", "password", "credential",
    "http://", "https://", "wss://", "ws://",
    "os.environ", "getenv", "putenv", "setenv",
    "Popen", "fork(", "Thread(", "Process(",
    "create_task", "ensure_future", "run_until_complete",
    "sleep(", "retry", "backoff",
    "uuid4", "token_hex", "urandom",
    ".now(", "utcnow", "time.time", "monotonic",
    "print(", "logging.",
)

# Yasak çağrı adları (AST Name düğümleri): dinamik import,
# kod üretimi, dosya/etkileşim, iç gözlem mutasyonu.
FORBIDDEN_CALL_NAMES = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input",
    "breakpoint", "globals", "vars", "delattr", "exit", "quit",
})

# Yasak öznitelik çağrıları (obj.attr(...) biçiminde)
FORBIDDEN_ATTRIBUTE_CALLS = frozenset({
    "import_module", "loads", "dumps", "run", "call",
    "check_output", "system", "popen", "write_text",
    "write_bytes", "unlink", "rmdir", "mkdir",
})


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    """Tek değişmez güvenlik bulgusu (steril)."""

    module_name: str
    category: str
    detail: str


@dataclass(frozen=True, slots=True)
class SecurityReport:
    """Tek modül için değişmez tarama sonucu."""

    module_name: str
    findings: Tuple[SecurityFinding, ...]

    @property
    def clean(self) -> bool:
        return self.findings == ()


def parse_source(source: str) -> ast.Module:
    """Verilen kaynak metnini AST'ye çevirir (saf)."""
    return ast.parse(source)


def strip_docstrings(source: str) -> str:
    """Docstring'leri arındırılmış eşdeğer kaynak üretir.

    Belirteç taraması yalnız ÇALIŞAN koda uygulanır; Türkçe
    açıklama metinleri yanlış pozitif ÜRETMEZ."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def collect_import_roots(tree: ast.Module) -> frozenset:
    """Modülün import ettiği kök ad kümesi."""
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                roots.add(node.module.split(".")[0])
    return frozenset(roots)


def find_forbidden_imports(module_name: str, source: str
                           ) -> Tuple[SecurityFinding, ...]:
    """Yasak import köklerini bulur."""
    roots = collect_import_roots(parse_source(source))
    findings = []
    for root in sorted(roots & FORBIDDEN_IMPORT_ROOTS):
        findings.append(SecurityFinding(
            module_name=module_name,
            category="FORBIDDEN_IMPORT", detail=root))
    return tuple(findings)


def find_forbidden_calls(module_name: str, source: str
                         ) -> Tuple[SecurityFinding, ...]:
    """eval/exec/dinamik import/dosya-süreç çağrılarını bulur."""
    findings = []
    for node in ast.walk(parse_source(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and \
                func.id in FORBIDDEN_CALL_NAMES:
            findings.append(SecurityFinding(
                module_name=module_name,
                category="FORBIDDEN_CALL", detail=func.id))
        if isinstance(func, ast.Attribute) and \
                func.attr in FORBIDDEN_ATTRIBUTE_CALLS:
            findings.append(SecurityFinding(
                module_name=module_name,
                category="FORBIDDEN_ATTRIBUTE_CALL",
                detail=func.attr))
    return tuple(findings)


def find_forbidden_tokens(module_name: str, source: str
                          ) -> Tuple[SecurityFinding, ...]:
    """Docstring'ler arındırılmış kaynakta yasak belirteçler."""
    stripped = strip_docstrings(source)
    findings = []
    for token in FORBIDDEN_TOKENS:
        if token in stripped:
            findings.append(SecurityFinding(
                module_name=module_name,
                category="FORBIDDEN_TOKEN", detail=token))
    return tuple(findings)


def validate_module_source(module_name: str, source: str
                           ) -> SecurityReport:
    """Tek modül için tam güvenlik taraması (saf, deterministik)."""
    findings = (find_forbidden_imports(module_name, source) +
                find_forbidden_calls(module_name, source) +
                find_forbidden_tokens(module_name, source))
    return SecurityReport(module_name=module_name,
                          findings=findings)
