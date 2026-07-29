"""TEK kanonik güvenli credential servisi (Mission: PERMANENT WINDOWS
CONFIGURATION & BINANCE CONNECTION SINGLE SOURCE OF TRUTH).

Bütün uygulama katmanları (bağlantı servisi, dashboard, agent registry,
kurulum akışı) credential SAKLAMA/SİLME/OKUMA işlemlerini yalnız bu modül
üzerinden yapar. Depolama motoru `exchange_credentials` modülüdür
(Windows'ta DPAPI şifreli, kullanıcı+makineye bağlı; çözülemezse
fail-closed). Bu servis motorun üstünde kanonik ve TEK API yüzeyidir —
başka modüller `exchange_credentials`'ı doğrudan yazma amaçlı KULLANMAZ.

Kalıcılık sözleşmesi:
- API Key/Secret proje klasörüne düz metin YAZILMAZ (Windows: DPAPI blob).
- Depo dosyası gitignore'dadır → git pull / SETUP tekrar koşuları silemez.
- Yalnız `remove()` (paneldeki "Bağlantıyı Kaldır") credential siler.
- Secret hiçbir log/response/snapshot'a yazılmaz; yalnız maskeli anahtar.
"""
from __future__ import annotations

import os

import exchange_credentials as _xc

PROVIDERS = _xc.EXCHANGES

# Panel/snapshot etiketi — mission sözleşmesi: credential_store alanı.
STORE_WINDOWS_DPAPI = "windows_dpapi"
STORE_LOCAL_FILE = "local_file"       # nt dışı yerel geliştirme (test/CI)
STORE_ENV = "env"                      # Replit Secrets / process env
STORE_NONE = "none"


def credentials(provider: str) -> tuple[str, str]:
    """Tek çözümleme noktası (("", "") = NOT_CONFIGURED)."""
    return _xc.credentials(provider)


def configured(provider: str) -> bool:
    return _xc.configured(provider)


def masked_key(provider: str) -> str:
    return _xc.masked_key(provider)


def mask_key(key: str) -> str:
    return _xc.mask_key(key)


def source(provider: str) -> str:
    """LOCAL_STORE | ENV | NOT_CONFIGURED."""
    return _xc.source(provider)


def store(provider: str, api_key: str, api_secret: str) -> None:
    """Şifreli yerel depoya atomik+kilitli yaz (Windows: DPAPI)."""
    _xc.save_local(provider, api_key, api_secret)


def remove(provider: str) -> bool:
    """Credential'ı kalıcı olarak sil — YALNIZ 'Bağlantıyı Kaldır' yolu."""
    return _xc.remove_local(provider)


def credential_store(provider: str) -> str:
    """Aktif saklama mekanizması etiketi (secret'sız)."""
    src = _xc.source(provider)
    if src == "LOCAL_STORE":
        return STORE_WINDOWS_DPAPI if os.name == "nt" else STORE_LOCAL_FILE
    if src == "ENV":
        return STORE_ENV
    return STORE_NONE


def storage_info(provider: str) -> dict:
    """Panelin 'Saklama Durumu' alanı için güvenli metadata."""
    store_label = credential_store(provider)
    return {
        "credential_store": store_label,
        "persistence": ("Bu bilgisayar ve Windows kullanıcısına bağlı"
                        if store_label == STORE_WINDOWS_DPAPI
                        else "Ortam değişkeni / Secrets"
                        if store_label == STORE_ENV
                        else "Yerel dosya deposu"
                        if store_label == STORE_LOCAL_FILE
                        else "Kayıtlı credential yok"),
        "secret_visibility": "Şifreli, görüntülenemez",
        "git_status": "Repository dışında (gitignore)",
        "restart_behavior": "Korunur",
    }
