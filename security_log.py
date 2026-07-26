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
    """Hassas kelime içeren alanı maskele; log format enjeksiyonunu engelle."""
    lower = text.lower()
    for word in _FORBIDDEN_WORDS:
        if word in lower:
            return "[REDACTED]"
    # Pipe / satır sonu karakterleri log alanı enjeksiyonuna izin vermesin
    text = text.replace("|", "/").replace("\n", " ").replace("\r", " ")
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
        parts.append(f"user={_sanitize(username)[:64]}")
    if ip:
        parts.append(f"ip={_sanitize(ip)[:45]}")
    if detail:
        parts.append(f"detail={_sanitize(detail)}")
    _logger.info(" | ".join(parts))


_LINE_RE = None  # lazily compiled


def get_security_summary(hours: int = 24, max_events: int = 10) -> dict:
    """
    security.log dosyasından son `hours` saatteki başarısız giriş / kilitlenme
    özetini çıkar. Parola veya hassas veri içermez (log'a zaten yazılmaz).

    Dönen sözlük:
      fail_count      — son N saatteki LOGIN_FAIL sayısı
      locked_ip_count — rate-limit'e takılan (kilitlenen) farklı IP sayısı
      last_lockout    — son kilitlenme zamanı (UTC string) veya None
      recent          — en son olaylar listesi [{time, event, ip, detail}]
    """
    import re

    global _LINE_RE
    if _LINE_RE is None:
        _LINE_RE = re.compile(
            r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z \| (?P<rest>.+)$"
        )

    summary = {
        "fail_count": 0,
        "locked_ip_count": 0,
        "last_lockout": None,
        "recent": [],
    }
    if not LOG_PATH.exists():
        return summary

    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return summary

    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    locked_ips: set[str] = set()
    events: list[tuple[datetime, dict]] = []
    last_lockout_ts: datetime | None = None

    for line in reversed(lines):
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            # Satırlar kronolojik sırada olmayabilir (saat değişimi, birleşik
            # dosyalar); erken durmak yerine pencere dışı satırı atla.
            continue
        fields: dict[str, str] = {}
        for part in m.group("rest").split(" | "):
            if "=" in part:
                k, _, v = part.partition("=")
                fields[k.strip()] = v.strip()
        if fields.get("event") != LOGIN_FAIL:
            continue
        ip     = fields.get("ip", "")
        detail = fields.get("detail", "")
        summary["fail_count"] += 1
        is_lockout = detail.lower().startswith("rate limited")
        if is_lockout:
            if ip:
                locked_ips.add(ip)
            if last_lockout_ts is None or ts > last_lockout_ts:
                last_lockout_ts = ts
        events.append((ts, {
            "time":    ts.strftime("%Y-%m-%d %H:%M:%S"),
            "event":   "Kilitlendi" if is_lockout else "Başarısız giriş",
            "ip":      ip or "—",
            "lockout": is_lockout,
        }))

    # Satırlar dosyada kronolojik olmayabilir; zaman damgasına göre yeniden sırala
    events.sort(key=lambda item: item[0], reverse=True)
    summary["recent"] = [ev for _, ev in events[:max_events]]
    if last_lockout_ts is not None:
        summary["last_lockout"] = last_lockout_ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    summary["locked_ip_count"] = len(locked_ips)
    return summary


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
