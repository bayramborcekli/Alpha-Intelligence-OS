"""
auth.py — Alpha-20 v1 kimlik doğrulama.
Tek admin hesabı; Werkzeug hash; IP bazlı rate limiting; 8 saatlik oturum.
Parola asla loglanmaz veya düz metin olarak saklanmaz.
"""
from __future__ import annotations

import ipaddress
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
SESSION_MAX_AGE    = 8 * 3600  # 8 saatlik oturum
SESSION_WARN_SECS  = 300       # Son 5 dakikada uyarı göster
SESSION_REFRESH_AT = 3600      # Son 1 saat kaldığında oturumu uzat

_LOCK    = threading.Lock()
_ATTEMPTS: dict[str, list[float]] = {}   # IP → [timestamp, ...]


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _get_admin_username() -> str:
    return os.environ.get("ADMIN_USERNAME") or "admin"


def _get_admin_password_hash() -> str | None:
    return os.environ.get("ADMIN_PASSWORD_HASH") or None


def _trusted_proxy_networks() -> list[ipaddress._BaseNetwork]:
    """
    TRUSTED_PROXY_IPS ortam değişkeninden güvenilir proxy ağlarını oku.
    Virgülle ayrılmış IP veya CIDR listesi (örn. "10.0.0.1, 172.16.0.0/12").
    Boş / tanımsızsa hiçbir proxy'ye güvenilmez.
    """
    raw = os.environ.get("TRUSTED_PROXY_IPS", "")
    networks: list[ipaddress._BaseNetwork] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue  # geçersiz girdi sessizce atlanır; güven GENİŞLETİLMEZ
    return networks


def _is_trusted_proxy(peer: str) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_proxy_networks())


def get_client_ip() -> str:
    """
    Gerçek istemci IP'sini döndür.

    Güvenlik kuralı: X-Forwarded-For başlığına VARSAYILAN olarak güvenilmez —
    doğrudan bağlanan bir saldırgan her istekte farklı sahte değer göndererek
    IP bazlı login kilidini sıfırlayabilirdi. Başlık yalnızca şu iki koşul
    birden sağlandığında kullanılır:
      1. İsteğin geldiği soket adresi (request.remote_addr) TRUSTED_PROXY_IPS
         ortam değişkeninde tanımlı güvenilir bir proxy ise, ve
      2. Kullanılan değer zincirin SON girdisidir (güvenilir proxy'nin kendi
         eklediği, istemcinin kontrol EDEMEDİĞİ girdi) ve geçerli bir IP'dir.
    Aksi durumda her zaman soket adresi döndürülür. TRUSTED_PROXY_IPS boşken
    (varsayılan) başlık tamamen yok sayılır; proxy arkasında bu, tüm
    isteklerin proxy IP'si altında sayılması demektir — bypass yerine daha
    sıkı (fail-safe) davranıştır.
    """
    peer = request.remote_addr or ""
    if peer and _is_trusted_proxy(peer):
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            candidate = forwarded.split(",")[-1].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate[:45]
            except ValueError:
                pass  # bozuk başlık → soket adresine geri dön
    return (peer or "unknown")[:45]


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


def get_session_remaining_seconds() -> int:
    """Oturumun dolmasına kalan saniyeyi döndür. Geçersiz oturumda 0."""
    login_time_str = session.get("login_time")
    if not login_time_str:
        return 0
    try:
        lt = datetime.fromisoformat(login_time_str)
        elapsed = (datetime.now(timezone.utc) - lt).total_seconds()
        remaining = SESSION_MAX_AGE - elapsed
        return max(0, int(remaining))
    except Exception:
        return 0


def maybe_refresh_session() -> bool:
    """
    Oturum son 1 saat içindeyse (kalan < SESSION_REFRESH_AT) login_time'ı
    sıfırlayarak 8 saati uzat. Uzatma yapıldıysa True döndür.
    """
    if not session.get("logged_in"):
        return False
    remaining = get_session_remaining_seconds()
    if remaining <= 0:
        return False
    if remaining < SESSION_REFRESH_AT:
        session["login_time"] = datetime.now(timezone.utc).isoformat()
        return True
    return False


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
