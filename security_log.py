"""
security_log.py — Alpha-20 v1 güvenlik olayı loglama.
RotatingFileHandler ile security.log dosyasına yazar (5 MB × 5 dosya).
Parola, token, session değeri veya API anahtarı asla loglanmaz.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT     = Path(__file__).resolve().parent
LOG_PATH = ROOT / "security.log"

# ── Olay tipleri ──────────────────────────────────────────────────────────────
LOGIN_OK          = "LOGIN_OK"
LOGIN_FAIL        = "LOGIN_FAIL"
LOGOUT            = "LOGOUT"
BOT_START         = "BOT_START"
BOT_STOP          = "BOT_STOP"
SETTINGS_CHANGE   = "SETTINGS_CHANGE"
COIN_ADD          = "COIN_ADD"
COIN_DEL          = "COIN_DEL"
KILL_SWITCH       = "KILL_SWITCH"
ADAPTIVE_CHANGE   = "ADAPTIVE_CHANGE"
PAPER_MODE_ACTIVE = "PAPER_MODE_ACTIVE"
STARTUP           = "STARTUP"
CONFIG_ERROR      = "CONFIG_ERROR"
CSRF_FAIL         = "CSRF_FAIL"
SESSION_EXPIRED   = "SESSION_EXPIRED"
UNAUTHORIZED_API  = "UNAUTHORIZED_API"
APP_LOCKED        = "APP_LOCKED"

# ── Hassas kelimeler (paranoia guard) ─────────────────────────────────────────
_FORBIDDEN_WORDS = ("password", "passwd", "secret", "token", "hash",
                    "key", "api_key", "credential")

# ── Logger kurulumu ───────────────────────────────────────────────────────────
_logger = logging.getLogger("alpha20.security")
_logger.setLevel(logging.INFO)
_logger.propagate = False


def _setup() -> None:
    if _logger.handlers:
        return
    try:
        handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fmt = logging.Formatter(
            "%(asctime)sZ | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(fmt)
        _logger.addHandler(handler)
    except OSError:
        # Log dosyası yazılamıyorsa sessizce geç
        pass


_setup()


def _sanitize(text: str) -> str:
    """Hassas kelime içeren alanı maskele."""
    lower = text.lower()
    for word in _FORBIDDEN_WORDS:
        if word in lower:
            return "[REDACTED]"
    return text[:200]


def log_event(
    event_type: str,
    detail: str = "",
    username: str = "",
    ip: str = "",
) -> None:
    """
    Güvenlik olayını logla.
    Parola, token veya secret içeren değerler otomatik maskelenir.
    """
    parts: list[str] = [f"event={event_type}"]
    if username:
        parts.append(f"user={username[:64]}")
    if ip:
        parts.append(f"ip={ip[:45]}")
    if detail:
        parts.append(f"detail={_sanitize(detail)}")
    _logger.info(" | ".join(parts))


def log_contains_sensitive(log_path: Path | None = None) -> bool:
    """Test yardımcısı: log dosyasında hassas kelime var mı?"""
    path = log_path or LOG_PATH
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for word in _FORBIDDEN_WORDS:
            if word + "=" in text or word + ":" in text:
                return True
        return False
    except OSError:
        return False
