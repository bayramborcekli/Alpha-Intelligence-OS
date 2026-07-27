"""Mission 2300 Agent 03 — Hesaplarım (My Accounts) kayıt defteri.

Alpha Intelligence bir borsa DEĞİLDİR; kullanıcının kendi yatırım
hesaplarını yöneten otonom bir yatırım işletim sistemidir. Bu modül
yalnız hesap kayıt defterini yönetir:

- İşlem mantığına, otomasyon motoruna, risk motoruna DOKUNMAZ.
- Sır SAKLAMAZ: Binance anahtarları ortam sırlarında yaşar; kayıt
  defteri yalnızca hangi hesabın bağlı olduğunu ve sunum meta
  verisini tutar. Gizli anahtar hiçbir zaman dosyaya yazılmaz.
- Bağlayıcı mimarisi: yeni borsa = katalogda yeni bir kayıt.
  UI yeniden tasarımı gerekmez. Hazır olmayan bağlayıcılar dürüstçe
  "hazır değil" olarak işaretlenir; sahte Bağlan düğmesi yoktur.
"""
from __future__ import annotations

import contextlib
try:
    import fcntl  # POSIX (Linux/Replit) — davranış değişmez
except ImportError:  # Windows: msvcrt tabanlı uyumluluk katmanı
    import portable_flock as fcntl  # type: ignore
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path("alpha20_v1/accounts.json")

# ── Bağlayıcı kataloğu (kapalı küme; geleceğe açık mimari) ─────────


@dataclass(frozen=True)
class Connector:
    """Bir borsa bağlayıcısının yetenek tanımı."""

    exchange: str            # kapalı küme kimliği
    display_name: str        # kullanıcıya görünen ad
    logo: str                # basit rozet (harici görsel yok)
    supported: bool          # bağlayıcı istemcisi hazır mı?
    spot_capable: bool
    futures_capable: bool
    credential_source: str   # "ENV" | "NONE" (PAPER) | "UNAVAILABLE"
    env_key_name: str        # maskeli gösterim için ortam anahtarı adı
    passphrase_supported: bool = False
    subaccount_supported: bool = False
    environments: tuple[str, ...] = ("MAINNET",)


CONNECTORS: dict[str, Connector] = {
    "PAPER": Connector(
        exchange="PAPER", display_name="Kağıt Hesap (Simülasyon)",
        logo="📄", supported=True, spot_capable=False,
        futures_capable=True, credential_source="NONE",
        env_key_name=""),
    "BINANCE_GLOBAL": Connector(
        exchange="BINANCE_GLOBAL", display_name="Binance Global",
        logo="🟡", supported=True, spot_capable=True,
        futures_capable=True, credential_source="ENV",
        env_key_name="BINANCE_API_KEY"),
    "BINANCE_TR": Connector(
        exchange="BINANCE_TR", display_name="Binance TR",
        logo="🇹🇷", supported=True, spot_capable=True,
        futures_capable=False, credential_source="ENV",
        env_key_name="BINANCE_TR_API_KEY"),
    "BYBIT": Connector(
        exchange="BYBIT", display_name="Bybit",
        logo="⬛", supported=False, spot_capable=True,
        futures_capable=True, credential_source="UNAVAILABLE",
        env_key_name="", subaccount_supported=True,
        environments=("MAINNET", "TESTNET")),
    "OKX": Connector(
        exchange="OKX", display_name="OKX",
        logo="⚪", supported=False, spot_capable=True,
        futures_capable=True, credential_source="UNAVAILABLE",
        env_key_name="", passphrase_supported=True,
        subaccount_supported=True,
        environments=("MAINNET", "TESTNET")),
}


class RegistryError(ValueError):
    """Kayıt defteri doğrulama hatası (sterile mesaj)."""


def mask_key(key: str) -> str:
    """Anahtarı ABCD…XY89 biçiminde maskeler; sır sızdırmaz."""
    if not key:
        return "-"
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _default_accounts() -> list[dict[str, Any]]:
    now = _now_iso()
    return [
        {"account_id": "paper", "exchange": "PAPER",
         "nickname": "Kağıt Hesap", "connected": True, "primary": True,
         "environment": "MAINNET", "spot_enabled": False,
         "futures_enabled": True, "created_at": now,
         "updated_at": now, "last_sync_at": "UNKNOWN"},
        {"account_id": "binance-global", "exchange": "BINANCE_GLOBAL",
         "nickname": "Binance Global", "connected": True,
         "primary": False, "environment": "MAINNET",
         "spot_enabled": True, "futures_enabled": True,
         "created_at": now, "updated_at": now,
         "last_sync_at": "UNKNOWN"},
        {"account_id": "binance-tr", "exchange": "BINANCE_TR",
         "nickname": "Binance TR", "connected": True, "primary": False,
         "environment": "MAINNET", "spot_enabled": True,
         "futures_enabled": False, "created_at": now,
         "updated_at": now, "last_sync_at": "UNKNOWN"},
        {"account_id": "bybit", "exchange": "BYBIT",
         "nickname": "Bybit", "connected": False, "primary": False,
         "environment": "MAINNET", "spot_enabled": False,
         "futures_enabled": False, "created_at": now,
         "updated_at": now, "last_sync_at": "UNKNOWN"},
        {"account_id": "okx", "exchange": "OKX",
         "nickname": "OKX", "connected": False, "primary": False,
         "environment": "MAINNET", "spot_enabled": False,
         "futures_enabled": False, "created_at": now,
         "updated_at": now, "last_sync_at": "UNKNOWN"},
    ]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


REQUIRED_FIELDS = ("account_id", "exchange", "nickname", "connected",
                   "primary", "environment", "spot_enabled",
                   "futures_enabled", "created_at", "updated_at",
                   "last_sync_at")


def _validate(accounts: list[dict[str, Any]]) -> None:
    if not isinstance(accounts, list) or not accounts:
        raise RegistryError("Kayıt defteri boş veya bozuk.")
    seen: set[str] = set()
    primaries = 0
    for acc in accounts:
        if not isinstance(acc, dict):
            raise RegistryError("Hesap kaydı bozuk.")
        for field in REQUIRED_FIELDS:
            if field not in acc:
                raise RegistryError(f"Eksik alan: {field}")
        if acc["exchange"] not in CONNECTORS:
            raise RegistryError("Bilinmeyen borsa bağlayıcısı.")
        if acc["account_id"] in seen:
            raise RegistryError("Yinelenen hesap kimliği.")
        seen.add(acc["account_id"])
        if acc["primary"]:
            primaries += 1
            if not acc["connected"]:
                raise RegistryError(
                    "Birincil hesap bağlı olmak zorundadır.")
    if primaries != 1:
        raise RegistryError("Tam olarak bir birincil hesap olmalı.")


@contextlib.contextmanager
def registry_lock(path: Path = REGISTRY_PATH):
    """Süreçler arası kilit: iki gunicorn işçisi aynı anda mutasyon
    yaparsa son-yazan-kazanır kaybını önler (fcntl, bloklu)."""
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load_registry(path: Path = REGISTRY_PATH) -> list[dict[str, Any]]:
    """Kayıt defterini okur; yoksa varsayılan tohumla oluşturur."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        accounts = raw.get("accounts") if isinstance(raw, dict) else None
        if accounts is None:
            raise RegistryError("Kayıt defteri biçimi bozuk.")
        _validate(accounts)
        return accounts
    except FileNotFoundError:
        accounts = _default_accounts()
        save_registry(accounts, path)
        return accounts
    except (OSError, ValueError) as exc:
        raise RegistryError("Kayıt defteri okunamadı.") from exc


def save_registry(accounts: list[dict[str, Any]],
                  path: Path = REGISTRY_PATH) -> None:
    _validate(accounts)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "accounts": accounts}, fh,
                      ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def find(accounts: list[dict[str, Any]],
         account_id: str) -> dict[str, Any]:
    acc = next((a for a in accounts
                if a["account_id"] == account_id), None)
    if acc is None:
        raise RegistryError("Hesap bulunamadı.")
    return acc


def connect(accounts: list[dict[str, Any]], account_id: str) -> None:
    acc = find(accounts, account_id)
    conn = CONNECTORS[acc["exchange"]]
    if not conn.supported:
        raise RegistryError(
            f"{conn.display_name} bağlayıcısı henüz hazır değil; "
            "bağlantı dürüstçe reddedildi.")
    if conn.credential_source == "ENV" and not os.environ.get(
            conn.env_key_name, ""):
        raise RegistryError(
            "Bu hesabın kimlik bilgisi ortam sırlarında tanımlı "
            "değil; önce sır ekleyin.")
    acc["connected"] = True
    acc["updated_at"] = _now_iso()


def disconnect(accounts: list[dict[str, Any]], account_id: str,
               automation_running: bool) -> None:
    acc = find(accounts, account_id)
    if acc["primary"]:
        raise RegistryError(
            "Birincil hesabın bağlantısı kesilemez; önce başka bir "
            "hesabı birincil yapın.")
    if automation_running and acc["exchange"] == "PAPER":
        raise RegistryError(
            "Otomasyon çalışırken yürütme defteri hesabının "
            "bağlantısı kesilemez.")
    acc["connected"] = False
    acc["updated_at"] = _now_iso()


def set_primary(accounts: list[dict[str, Any]],
                account_id: str) -> None:
    target = find(accounts, account_id)
    if not target["connected"]:
        raise RegistryError(
            "Bağlı olmayan hesap birincil yapılamaz.")
    if not CONNECTORS[target["exchange"]].supported:
        raise RegistryError(
            "Hazır olmayan bağlayıcı birincil yapılamaz.")
    for acc in accounts:
        acc["primary"] = acc["account_id"] == account_id
    target["updated_at"] = _now_iso()


def edit(accounts: list[dict[str, Any]], account_id: str,
         nickname: str | None = None,
         spot_enabled: bool | None = None,
         futures_enabled: bool | None = None) -> None:
    acc = find(accounts, account_id)
    conn = CONNECTORS[acc["exchange"]]
    if nickname is not None:
        nickname = nickname.strip()
        if not (1 <= len(nickname) <= 40):
            raise RegistryError("Takma ad 1–40 karakter olmalı.")
        acc["nickname"] = nickname
    if spot_enabled is not None:
        if spot_enabled and not conn.spot_capable:
            raise RegistryError(
                "Bu hesap spot işlemi desteklemiyor.")
        acc["spot_enabled"] = bool(spot_enabled)
    if futures_enabled is not None:
        if futures_enabled and not conn.futures_capable:
            raise RegistryError(
                "Bu hesap vadeli işlemi desteklemiyor.")
        acc["futures_enabled"] = bool(futures_enabled)
    acc["updated_at"] = _now_iso()


def touch_sync(accounts: list[dict[str, Any]],
               account_id: str) -> None:
    acc = find(accounts, account_id)
    acc["last_sync_at"] = _now_iso()
    acc["updated_at"] = _now_iso()


def execution_eligible(accounts: list[dict[str, Any]]) -> list[str]:
    """Otomasyonun emir gönderebileceği hesaplar: yalnız BAĞLI ve
    bağlayıcısı hazır olanlar. Bağlantısı kesik hesaba emir gitmez."""
    return [a["account_id"] for a in accounts
            if a["connected"] and CONNECTORS[a["exchange"]].supported]


def card_view(acc: dict[str, Any]) -> dict[str, Any]:
    """Sunum kartı: sır içermez; ortam anahtarı yalnız maskelenir."""
    conn = CONNECTORS[acc["exchange"]]
    if conn.credential_source == "ENV":
        api_key_masked = mask_key(os.environ.get(conn.env_key_name, ""))
        credentials_configured = bool(
            os.environ.get(conn.env_key_name, ""))
    elif conn.credential_source == "NONE":
        api_key_masked = "-"
        credentials_configured = True
    else:
        api_key_masked = "-"
        credentials_configured = False
    return {
        "account_id": acc["account_id"],
        "exchange": acc["exchange"],
        "display_name": conn.display_name,
        "logo": conn.logo,
        "nickname": acc["nickname"],
        "connected": acc["connected"],
        "primary": acc["primary"],
        "environment": acc["environment"],
        "spot_enabled": acc["spot_enabled"],
        "futures_enabled": acc["futures_enabled"],
        "spot_capable": conn.spot_capable,
        "futures_capable": conn.futures_capable,
        "connector_ready": conn.supported,
        "credentials_configured": credentials_configured,
        "credential_source": conn.credential_source,
        "api_key_masked": api_key_masked,
        "passphrase_supported": conn.passphrase_supported,
        "subaccount_supported": conn.subaccount_supported,
        "environments": list(conn.environments),
        "last_sync_at": acc["last_sync_at"],
        "updated_at": acc["updated_at"],
    }
