"""Windows/yerel yönetici kimlik deposu — Replit Secrets'tan TAM AYRIM.

Kural:
- Replit / production: bu modül HİÇ kullanılmaz (Secrets kazanır).
- Windows / yerel: giriş kimliğinin TEK doğruluk kaynağı
  `data/local_admin.json` dosyasıdır. İşletim sistemi / kullanıcı
  environment değişkenleri (eski clone, stale system env) girişi
  ETKİLEYEMEZ ve override EDEMEZ.

Dosya şeması (yalnızca bu 4 alan; plaintext parola ASLA yazılmaz):
    {"schema_version": 1, "username": "...",
     "password_hash": "pbkdf2:...", "created_at": "ISO-8601"}

Güvenlik:
- Atomic yazma (aynı dizinde geçici dosya + os.replace).
- Dosya izinleri 0600, dizin 0700 (POSIX'te; Windows'ta en iyi çaba).
- Symlink reddedilir (dosya VE data dizini) — path traversal yok.
- Bozuk JSON / eksik alan / yanlış tip → fail-closed (None: giriş kapalı,
  kurulum sihirbazı açılır; dosya asla sessizce silinmez).
- Hash değeri hiçbir zaman loglanmaz.

Migration (tek seferlik): dosya yoksa ve process env'de eski
ALPHA_OWNER_* / ADMIN_* hash'i varsa dosyaya taşınır; sonrasında runtime
kaynağı yalnızca dosyadır.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import local_env

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
FILE = DATA_DIR / "local_admin.json"

SCHEMA_VERSION = 1
_ALLOWED_HASH_PREFIXES = ("pbkdf2:", "scrypt:")


def enabled() -> bool:
    """Yerel kimlik deposu yalnızca Replit DIŞINDA devrededir."""
    return not local_env.is_replit()


def _safe_paths_ok() -> bool:
    """Symlink/path-traversal koruması: data dizini ve dosya symlink olamaz;
    dosya gerçekten proje kökü altında kalmalı."""
    try:
        if DATA_DIR.is_symlink() or FILE.is_symlink():
            return False
        # resolve() sonrası hâlâ ROOT altında mı? (junction/symlink kaçışı)
        resolved = FILE.resolve()
        return str(resolved).startswith(str(ROOT.resolve()) + os.sep)
    except OSError:
        return False


def load() -> dict | None:
    """Kimlik kaydını oku; her tür bozulmada fail-closed (None) döner.

    None = yapılandırılmamış → giriş kapalı, /setup açılır."""
    if not enabled():
        return None
    if not _safe_paths_ok():
        return None
    try:
        if not FILE.is_file():
            return None
        raw = FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None  # bozuk JSON → fail-closed
    if not isinstance(data, dict):
        return None
    username = data.get("username")
    pw_hash = data.get("password_hash")
    if not isinstance(username, str) or not username.strip():
        return None
    if not isinstance(pw_hash, str) or \
            not pw_hash.startswith(_ALLOWED_HASH_PREFIXES):
        return None
    return {"schema_version": data.get("schema_version"),
            "username": username,
            "password_hash": pw_hash,
            "created_at": data.get("created_at")}


def save(username: str, password_hash: str) -> None:
    """Atomic yazma ile kimlik kaydını oluştur/güncelle.

    Değer loglanmaz; plaintext parola bu API'ye hiç girmez."""
    if not enabled():
        raise ValueError("Replit ortamında yerel kimlik deposu kullanılamaz; "
                         "Secrets kullanın.")
    if not isinstance(username, str) or not username.strip():
        raise ValueError("username boş olamaz")
    if not isinstance(password_hash, str) or \
            not password_hash.startswith(_ALLOWED_HASH_PREFIXES):
        raise ValueError("geçersiz password_hash biçimi")
    if DATA_DIR.is_symlink() or FILE.is_symlink():
        raise ValueError("symlink hedefe yazma reddedildi")
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass  # Windows: en iyi çaba
    record = {
        "schema_version": SCHEMA_VERSION,
        "username": username,
        "password_hash": password_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    fd, tmp_name = tempfile.mkstemp(dir=str(DATA_DIR),
                                    prefix=".local_admin_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass  # Windows: en iyi çaba
        os.replace(tmp_name, FILE)  # atomic
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def migrate_from_env() -> bool:
    """Tek seferlik migration: dosya yoksa ve process env'de eski hash
    varsa `data/local_admin.json`'a taşı. Başarılıysa True.

    Migration SONRASI runtime kaynağı yalnızca dosyadır; env okunmaz."""
    if not enabled():
        return False
    if load() is not None:
        return False  # dosya zaten var — env asla override edemez
    pw_hash = (os.environ.get("ALPHA_OWNER_PASSWORD_HASH")
               or os.environ.get("ADMIN_PASSWORD_HASH") or "")
    if not pw_hash.startswith(_ALLOWED_HASH_PREFIXES):
        return False
    username = (os.environ.get("ALPHA_OWNER_USERNAME")
                or os.environ.get("ADMIN_USERNAME") or "admin")
    try:
        save(username, pw_hash)
    except (ValueError, OSError):
        return False
    return True


def get_credentials() -> tuple[str, str] | None:
    """(username, password_hash) veya None. Gerekirse tek seferlik
    env-migration dener. Windows girişinin TEK kaynağı."""
    rec = load()
    if rec is None and migrate_from_env():
        rec = load()
    if rec is None:
        return None
    return rec["username"], rec["password_hash"]
