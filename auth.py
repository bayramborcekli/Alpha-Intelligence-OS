"""
auth.py — Alpha-20 v1 kimlik doğrulama.
Tek admin hesabı; Werkzeug hash; IP bazlı rate limiting; 8 saatlik oturum.
Parola asla loglanmaz veya düz metin olarak saklanmaz.
"""
from __future__ import annotations

import os
import time
import threading
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from flask import current_app, redirect, request, session, url_for
from werkzeug.security import check_password_hash

# ── Rate limiting ─────────────────────────────────────────────────────────────
MAX_ATTEMPTS     = 5      # Bu sayıda başarısız denemeden sonra kilitle
LOCKOUT_SECONDS  = 300    # 5 dakika kilit süresi
WINDOW_SECONDS   = 300    # Bu zaman diliminde denemeleri say
SESSION_MAX_AGE  = 8 * 3600  # 8 saatlik oturum

_LOCK    = threading.Lock()
_ATTEMPTS: dict[str, list[float]] = {}   # IP → [timestamp, ...]


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _get_admin_username() -> str:
    return os.environ.get("ADMIN_USERNAME") or "admin"


def _get_admin_password_hash() -> str | None:
    return os.environ.get("ADMIN_PASSWORD_HASH") or None


def get_client_ip() -> str:
    """Gerçek istemci IP'sini döndür (proxy arkasında çalışır)."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.remote_addr or "unknown")[:45]


# ── Rate limiting ─────────────────────────────────────────────────────────────

def check_rate_limit(ip: str) -> tuple[bool, int]:
    """
    (allowed, seconds_remaining) döndür.
    allowed=False ise giriş denenemez.
    """
    now = time.time()
    with _LOCK:
        attempts = [t for t in _ATTEMPTS.get(ip, []) if now - t < WINDOW_SECONDS]
        _ATTEMPTS[ip] = attempts
        if len(attempts) >= MAX_ATTEMPTS:
            oldest = min(attempts)
            remaining = int(LOCKOUT_SECONDS - (now - oldest))
            if remaining > 0:
                return False, remaining
            _ATTEMPTS[ip] = []
        return True, 0


def record_attempt(ip: str, *, success: bool) -> None:
    """Giriş denemesini kaydet. Başarılıysa sayacı sıfırla."""
    now = time.time()
    with _LOCK:
        if success:
            _ATTEMPTS[ip] = []
        else:
            prev = [t for t in _ATTEMPTS.get(ip, []) if now - t < WINDOW_SECONDS]
            prev.append(now)
            _ATTEMPTS[ip] = prev


# ── Kimlik doğrulama ──────────────────────────────────────────────────────────

def verify_credentials(username: str, password: str) -> bool:
    """
    Kullanıcı adı ve parolayı env var'larla karşılaştır.
    Hash eşleşmesi Werkzeug üzerinden yapılır; parola loglanmaz.
    """
    if not username or not password:
        return False
    expected_user = _get_admin_username()
    expected_hash = _get_admin_password_hash()
    if not expected_hash:
        # Parola hash'i yapılandırılmamış = erişim yok
        return False
    if username != expected_user:
        return False
    try:
        return check_password_hash(expected_hash, password)
    except Exception:
        return False


def password_hash_configured() -> bool:
    """Üretim ortamı için parola hash'i ayarlanmış mı?"""
    return bool(_get_admin_password_hash())


# ── Oturum yönetimi ───────────────────────────────────────────────────────────

def start_session(username: str) -> None:
    """Başarılı girişten sonra oturumu başlat."""
    session.permanent = True
    session["logged_in"] = True
    session["username"] = username
    session["login_time"] = datetime.now(timezone.utc).isoformat()


def clear_session() -> str:
    """Oturumu temizle; önceki kullanıcı adını döndür."""
    username = session.get("username", "")
    session.clear()
    return username


def _session_expired() -> bool:
    """Oturum 8 saati aşmışsa True."""
    login_time_str = session.get("login_time")
    if not login_time_str:
        return True
    try:
        lt = datetime.fromisoformat(login_time_str)
        return (datetime.now(timezone.utc) - lt).total_seconds() > SESSION_MAX_AGE
    except Exception:
        return True


# ── Dekoratör ─────────────────────────────────────────────────────────────────

def login_required(f: Any) -> Any:
    """
    Tüm korumalı route'lara ekle.
    TESTING=True ise atlanır (birim testler için).
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        # Test modu: kimlik doğrulamayı atla
        if current_app.config.get("TESTING") or current_app.config.get("LOGIN_DISABLED"):
            return f(*args, **kwargs)
        # Giriş kontrolü
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        # Oturum süresi kontrolü
        if _session_expired():
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated
