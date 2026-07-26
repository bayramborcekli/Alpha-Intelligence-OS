"""Mission 1400.1 — uygulama temeli: özellik bayrakları, kurulum durumu,
sürüm/mod bilgisi ve güvenli yapılandırma yanıtları.

Kurallar:
- Bayraklar YALNIZCA sunucu tarafında okunur; frontend asla geçersiz kılamaz.
- Güvenli varsayılan: tüm bayraklar false. Bozuk/bilinmeyen değer → false.
- Hiçbir fonksiyon secret DEĞERİ döndürmez; yalnızca eksik secret ADLARI
  (kurulum ekranı için) listelenebilir.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

APP_NAME = "Alpha Intelligence OS"
UI_LANGUAGE = "tr"

FEATURE_FLAG_NAMES = (
    "ALPHA_ENABLE_DRY_RUN",
    "ALPHA_ENABLE_LIVE_TRADING",
    "ALPHA_ENABLE_TRANSFERS",
    "ALPHA_ENABLE_WITHDRAWALS",
    "ALPHA_ENABLE_INTELLIGENCE",
)

REQUIRED_OWNER_SECRETS = ("ALPHA_OWNER_USERNAME", "ALPHA_OWNER_PASSWORD_HASH")


def _parse_flag(raw: str | None) -> bool:
    """Katı bayrak ayrıştırma: yalnızca 'true' (harf duyarsız) → True.

    Boş, tanımsız, bozuk veya bilinmeyen her değer güvenli tarafta False'tur.
    """
    return (raw or "").strip().lower() == "true"


def feature_flags() -> dict[str, bool]:
    return {name: _parse_flag(os.environ.get(name))
            for name in FEATURE_FLAG_NAMES}


def missing_owner_secrets() -> list[str]:
    """Eksik zorunlu sahip secret'larının ADLARI (değer asla okunup
    döndürülmez). Geriye dönük uyumluluk: eski ADMIN_PASSWORD_HASH +
    (varsa) ADMIN_USERNAME yapılandırması da kurulumu READY sayar."""
    if os.environ.get("ADMIN_PASSWORD_HASH"):
        return []
    return [n for n in REQUIRED_OWNER_SECRETS if not os.environ.get(n)]


def setup_state() -> str:
    """READY | LOCKED."""
    return "LOCKED" if missing_owner_secrets() else "READY"


def app_mode() -> str:
    """Uygulama modu rozeti. Canlı işlem bayrağı kapalıyken daima PAPER."""
    return "LIVE" if feature_flags()["ALPHA_ENABLE_LIVE_TRADING"] else "PAPER"


def environment_mode() -> str:
    return ("production" if os.environ.get("FLASK_ENV") == "production"
            else "development")


def health_payload(version: str) -> dict:
    """Güvenli sağlık yanıtı — secret değeri, dosya yolu, bakiye içermez."""
    payload = {
        "status": "ok",
        "application": APP_NAME,
        "version": version,
        "environment": environment_mode(),
        "setup_state": setup_state(),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }
    missing = missing_owner_secrets()
    if missing:
        # Yalnızca gerekli değişken ADLARI (kurulum yönergesi için izinli).
        payload["required_configuration"] = missing
    return payload


def application_config(version: str, owner_display: str) -> dict:
    """Kimlik doğrulamalı güvenli yapılandırma yanıtı."""
    flags = feature_flags()
    return {
        "application_name": APP_NAME,
        "version": version,
        "mode": app_mode(),
        "feature_flags": flags,
        "live_trading_enabled": flags["ALPHA_ENABLE_LIVE_TRADING"],
        "transfers_enabled": flags["ALPHA_ENABLE_TRANSFERS"],
        "withdrawals_enabled": flags["ALPHA_ENABLE_WITHDRAWALS"],
        "dry_run_enabled": flags["ALPHA_ENABLE_DRY_RUN"],
        "ui_language": UI_LANGUAGE,
        "owner": owner_display,
    }
