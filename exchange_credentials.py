"""Kanonik borsa credential çözümleyici + Windows yerel güvenli depo.

TEK credential resolver: aktif exchange client'lar env isimlerini kendi
başlarına ARAMAZ; çözümleme yalnızca bu modül üzerinden yapılır.

Kanonik isimler:
    Binance Global : BINANCE_GLOBAL_API_Key / BINANCE_GLOBAL_Secret_Key
    Binance TR     : BINANCE_TR_API_KEY     / BINANCE_TR_API_SECRET

Legacy alias'lar (yalnız geriye dönük uyumluluk katmanı; kanonik isim
mevcutsa kanonik HER ZAMAN kazanır):
    Global key   : BINANCE_GLOBAL_API_KEY, BINANCE_API_KEY, BINANCE_API_Key
    Global secret: BINANCE_GLOBAL_API_SECRET, BINANCE_API_SECRET,
                   BINANCE_Secret_Key

Çözümleme önceliği:
    Windows/yerel : data/exchange_credentials.json → kanonik env → legacy env
    Replit        : kanonik env (Secrets) → legacy env   (dosya OKUNMAZ)

Windows deposu (data/exchange_credentials.json):
    - project-local + gitignored (data/ zaten ignore'da)
    - atomic yazma (tmp + os.replace), 0600/0700 izinler (POSIX'te)
    - symlink / path-traversal reddi
    - bozuk JSON → fail-closed (o borsa NOT_CONFIGURED görünür)
    - data/local_admin.json'dan TAMAMEN AYRI: admin parolası ile exchange
      anahtarları asla aynı dosyada tutulmaz.

GÜVENLİK: secret/key değeri asla loglanmaz; presence raporu yalnız
VAR/YOK bilgisi döndürür; UI için yalnız maskeli anahtar üretilir.
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
FILE = DATA_DIR / "exchange_credentials.json"

SCHEMA_VERSION = 1
EXCHANGES = ("BINANCE_GLOBAL", "BINANCE_TR")

# Kanonik env isimleri (Mission sözleşmesi)
CANONICAL = {
    "BINANCE_GLOBAL": ("BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key"),
    "BINANCE_TR": ("BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET"),
}

# Legacy alias'lar — SADECE geriye dönük uyumluluk; sıra = öncelik.
LEGACY = {
    "BINANCE_GLOBAL": (
        ("BINANCE_GLOBAL_API_KEY", "BINANCE_GLOBAL_API_SECRET"),
        ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
        ("BINANCE_API_Key", "BINANCE_Secret_Key"),
    ),
    "BINANCE_TR": (),
}

_MAX_LEN = 256


def _local_store_enabled() -> bool:
    """Yerel dosya deposu yalnız Replit DIŞINDA devrededir."""
    return not local_env.is_replit()


def _safe_paths_ok() -> bool:
    try:
        if DATA_DIR.is_symlink() or FILE.is_symlink():
            return False
        resolved = FILE.resolve()
        return str(resolved).startswith(str(ROOT.resolve()) + os.sep)
    except OSError:
        return False


def _load_store() -> dict:
    """Yerel depo içeriği; her tür bozulmada fail-closed ({})."""
    if not _local_store_enabled() or not _safe_paths_ok():
        return {}
    try:
        if not FILE.is_file():
            return {}
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    accounts = data.get("accounts")
    return accounts if isinstance(accounts, dict) else {}


def _store_entry(exchange: str) -> tuple[str, str] | None:
    entry = _load_store().get(exchange)
    if not isinstance(entry, dict):
        return None
    key = entry.get("api_key")
    sec = entry.get("api_secret")
    if (isinstance(key, str) and key.strip()
            and isinstance(sec, str) and sec.strip()):
        return key.strip(), sec.strip()
    return None


def save_local(exchange: str, api_key: str, api_secret: str) -> None:
    """Windows/yerel: anahtar çiftini atomic olarak depoya yaz.

    Değerler asla loglanmaz. Replit'te çağrılamaz."""
    if not _local_store_enabled():
        raise ValueError("Replit ortamında yerel exchange deposu "
                         "kullanılamaz; Secrets kullanın.")
    if exchange not in EXCHANGES:
        raise ValueError("bilinmeyen borsa")
    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()
    if not api_key or not api_secret:
        raise ValueError("api_key/api_secret boş olamaz")
    if len(api_key) > _MAX_LEN or len(api_secret) > _MAX_LEN:
        raise ValueError("api_key/api_secret çok uzun")
    if any(c.isspace() for c in api_key) or any(c.isspace()
                                                for c in api_secret):
        raise ValueError("api_key/api_secret boşluk içeremez")
    if DATA_DIR.is_symlink() or FILE.is_symlink():
        raise ValueError("symlink hedefe yazma reddedildi")
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass  # Windows: en iyi çaba
    # Süreçler arası kilit: oku-değiştir-yaz kaybını önler (iki worker
    # farklı borsayı aynı anda güncellerse biri diğerini ezmesin).
    lock_path = DATA_DIR / ".exchange_credentials.lock"
    lock_fh = open(lock_path, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except ImportError:  # Windows: msvcrt tabanlı kilit
            import msvcrt
            lock_fh.seek(0)
            msvcrt.locking(lock_fh.fileno(), msvcrt.LK_LOCK, 1)
        _save_locked(exchange, api_key, api_secret)
    finally:
        lock_fh.close()


def _save_locked(exchange: str, api_key: str, api_secret: str) -> None:
    accounts = _load_store()
    accounts[exchange] = {
        "api_key": api_key,
        "api_secret": api_secret,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    record = {"schema_version": SCHEMA_VERSION, "accounts": accounts}
    fd, tmp_name = tempfile.mkstemp(dir=str(DATA_DIR),
                                    prefix=".exchange_cred_", suffix=".tmp")
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


def _env_pair(exchange: str) -> tuple[str, str]:
    """Env çözümleme: kanonik HER ZAMAN önce; legacy yalnız yedek."""
    ck, cs = CANONICAL[exchange]
    key = os.environ.get(ck, "").strip()
    sec = os.environ.get(cs, "").strip()
    if key and sec:
        return key, sec
    for lk, ls in LEGACY[exchange]:
        k2 = key or os.environ.get(lk, "").strip()
        s2 = sec or os.environ.get(ls, "").strip()
        if k2 and s2:
            return k2, s2
    # Kısmi yapılandırma: eksik parça legacy'den bağımsız tamamlanır ki
    # presence raporu / maskeli gösterim dürüst kalsın (configured()
    # yine ikisini birden ister — fail closed).
    for lk, ls in LEGACY[exchange]:
        key = key or os.environ.get(lk, "").strip()
        sec = sec or os.environ.get(ls, "").strip()
    return key, sec


def credentials(exchange: str) -> tuple[str, str]:
    """TEK çözümleme noktası. ("", "") = NOT_CONFIGURED."""
    if exchange not in EXCHANGES:
        return "", ""
    if _local_store_enabled():
        stored = _store_entry(exchange)
        if stored is not None:
            # Yerel depo kaydı varsa env asla override edemez.
            return stored
    return _env_pair(exchange)


def configured(exchange: str) -> bool:
    key, sec = credentials(exchange)
    return bool(key and sec)


def source(exchange: str) -> str:
    """Aktif kaynak etiketi: LOCAL_STORE | ENV | NOT_CONFIGURED."""
    if _local_store_enabled() and _store_entry(exchange) is not None:
        return "LOCAL_STORE"
    key, sec = _env_pair(exchange)
    return "ENV" if (key and sec) else "NOT_CONFIGURED"


def mask_key(key: str) -> str:
    """Anahtarı 5415************ biçiminde maskeler; sır sızdırmaz."""
    if not key:
        return ""
    return key[:4] + "*" * max(len(key) - 4, 12) if len(key) > 4 \
        else "*" * 16


def masked_key(exchange: str) -> str:
    key, _sec = credentials(exchange)
    return mask_key(key)


def presence_report() -> dict[str, dict[str, bool | str]]:
    """Yalnız VAR/YOK + kaynak; asla değer döndürmez."""
    out: dict[str, dict[str, bool | str]] = {}
    for ex in EXCHANGES:
        key, sec = credentials(ex)
        out[ex] = {"key_present": bool(key), "secret_present": bool(sec),
                   "source": source(ex)}
    return out
