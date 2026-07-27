"""Mission 2000 — Agent 09: Yürütme Çekirdeği güvenlik sertifikası.

Bu modül YENİ iş işlevi içermez. Yürütme Çekirdeği'nin güvenlik
sözleşmesini bildirimsel olarak dondurur: sertifikalı modül
kümesi, yasak import kökleri ve yasak kod belirteçleri. Test
paketleri bu listeleri canlı kaynak koduna (docstring'ler
arındırılmış AST) uygular; her sapma regresyon hatasıdır.

Sertifika kapsamı: secret/API anahtarı/imzalama yok; HTTP/REST/
WebSocket/soket yok; dosya yazımı yok; subprocess/thread/process/
zamanlayıcı yok; retry yok; UUID üretimi yok; duvar saati yok;
rastgelelik yok; ortam erişimi yok; broker SDK yok; SQL/ORM/
kalıcılık yok; telemetri/analitik/izleme yayıncısı yok.
"""

from __future__ import annotations

__all__ = ["SECURITY_STATUS", "CERTIFIED_MODULES",
           "INTERNAL_CORE_MODULES", "FORBIDDEN_IMPORT_ROOTS",
           "FORBIDDEN_TOKENS", "FORBIDDEN_CALL_NAMES",
           "TOKEN_EXEMPT_SUBSTRINGS"]

SECURITY_STATUS = "CERTIFIED"

# Sertifikalı üretim modülleri (Yürütme Çekirdeği'nin tamamı)
CERTIFIED_MODULES = (
    "execution_enums",
    "execution_models",
    "execution_state_machine",
    "execution_risk_models",
    "execution_risk_policies",
    "execution_risk_engine",
    "execution_kill_switch_models",
    "execution_kill_switch",
    "execution_broker_models",
    "execution_broker_errors",
    "execution_broker_adapter",
    "binance_spot_adapter",
    "binance_normalizer",
    "binance_capabilities",
    "execution_permission_gate",
    "execution_service_models",
    "execution_service",
    "execution_api_models",
    "execution_api_mapper",
    "execution_api",
)

# Çekirdeğin iç yardımcı modülleri (kamu API dondurmasında yüzey
# taşımaz ama TAM güvenlik taramasına ve dondurmaya tabidir)
INTERNAL_CORE_MODULES = ("execution_kill_switch_models",
                         "execution_risk_policies")

# Yasak import kökleri (ağ, kalıcılık, eşzamanlılık, zaman,
# rastgelelik, ortam, telemetri, broker SDK'ları, web çatıları)
FORBIDDEN_IMPORT_ROOTS = frozenset({
    "os", "sys", "io", "pathlib", "shutil", "tempfile", "glob",
    "socket", "ssl", "http", "urllib", "requests", "httpx",
    "aiohttp", "websockets", "websocket", "flask", "fastapi",
    "django", "uuid", "datetime", "time", "random", "secrets",
    "hashlib", "hmac", "base64", "threading", "multiprocessing",
    "subprocess", "sched", "signal", "queue", "asyncio", "json",
    "pickle", "shelve", "sqlite3", "sqlalchemy", "psycopg2",
    "redis", "logging", "ccxt", "binance", "ib_insync",
    "monitoring_service", "monitoring_api", "strategy_service",
    "app", "risk_api",
})

# Yasak kod belirteçleri (docstring'ler arındırılmış kaynakta)
FORBIDDEN_TOKENS = (
    "api_key", "API_KEY", "api_secret", "API_SECRET", "passphrase",
    "password", "signature", "sign(", "hmac", "sha256",
    "http://", "https://", "wss://", "ws://",
    "requests.", "urlopen", "Session(",
    "open(", "Path(", "os.environ", "getenv",
    "subprocess", "Popen", "Thread(", "Process(", "fork",
    "create_task", "ensure_future", "run_until_complete",
    "asyncio.run", "sleep", "retry", "backoff",
    "uuid", "uuid4", "token_hex", "urandom",
    "datetime.now", "utcnow", "time.time", "monotonic",
    "perf_counter", "random.",
    "SELECT ", "INSERT ", "UPDATE ", "DELETE FROM", "sqlalchemy",
    "telemetry", "analytics", "metrics.", "publish(", "emit(",
    "while True",
)

# Yasak yerleşik çağrılar (AST düzeyinde)
FORBIDDEN_CALL_NAMES = frozenset({
    "eval", "exec", "open", "__import__", "compile", "print",
    "input", "breakpoint", "exit", "quit",
})

# Meşru sözleşme sözcükleri — taramadan önce çıkarılır
# ("retryable" alanı retry uygulaması DEĞİLDİR;
#  "api_key_reference" bir referans ADI'dır, secret değildir)
TOKEN_EXEMPT_SUBSTRINGS = ("retryable", "api_key_reference")
