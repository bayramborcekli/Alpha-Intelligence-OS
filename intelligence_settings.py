"""Mission 1500.1 / Agent 09 — Intelligence ayarları ve feature flag'leri.

Kurallar:
- Tüm ayarlar ortam değişkeninden OKUNUR; UI üzerinden kalıcı config
  yazımı YOKTUR (salt-okunur katman).
- Geçersiz / bilinmeyen değerlerde her zaman GÜVENLİ VARSAYILAN kullanılır
  ve doğrulama uyarısı (yalnızca kod — ham değer asla) kaydedilir.
- External LLM 1500.1 boyunca KİLİTLİDİR: ortam ne derse desin kapalı.
- Ham ortam değişkeni değerleri hiçbir çıktıya konmaz.
"""

import os

# Ortam değişkeni adları (öneri: ALPHA_INTELLIGENCE_* — eski
# ALPHA_ENABLE_INTELLIGENCE bayrağı geriye dönük uyumluluk için okunur)
ENV_ENABLED = "ALPHA_INTELLIGENCE_ENABLED"
ENV_ENABLED_LEGACY = "ALPHA_ENABLE_INTELLIGENCE"
ENV_LOCAL_ONLY = "ALPHA_INTELLIGENCE_LOCAL_ONLY"
ENV_EXTERNAL_LLM = "ALPHA_INTELLIGENCE_EXTERNAL_LLM_ENABLED"
ENV_EXPLAINABILITY = "ALPHA_INTELLIGENCE_EXPLAINABILITY_LEVEL"
ENV_RECOMMENDATION = "ALPHA_INTELLIGENCE_RECOMMENDATION_LEVEL"

SETTING_NAMES = (ENV_ENABLED, ENV_LOCAL_ONLY, ENV_EXTERNAL_LLM,
                 ENV_EXPLAINABILITY, ENV_RECOMMENDATION)

# 1500.1 varsayılanları
DEFAULTS = {
    "enabled": False,            # yapılandırılabilir; güvenli taraf kapalı
    "local_only": True,
    "external_llm_enabled": False,
    "explainability_level": "detailed",
    "recommendation_level": "advisory",
}

EXPLAINABILITY_LEVELS = ("basic", "detailed")
RECOMMENDATION_LEVELS = ("advisory",)   # 1500.1: yalnızca tavsiye modu

# Mission 1500.1 kilidi: harici LLM koşulsuz kapalı
EXTERNAL_LLM_HARD_LOCK = True


def _parse_bool(name: str, default: bool, warnings: list) -> bool:
    """Katı bool: yalnızca 'true'/'false' geçerli; aksi güvenli varsayılan."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    v = raw.strip().lower()
    if v == "true":
        return True
    if v == "false":
        return False
    warnings.append({"setting": name, "code": "INVALID_BOOL",
                     "message": "Geçersiz değer — güvenli varsayılan "
                                "kullanıldı."})
    return default


def _parse_choice(name: str, allowed: tuple, default: str,
                  warnings: list) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    v = raw.strip().lower()
    if v in allowed:
        return v
    warnings.append({"setting": name, "code": "INVALID_CHOICE",
                     "message": "Geçersiz değer — güvenli varsayılan "
                                "kullanıldı."})
    return default


def get_settings() -> dict:
    """Doğrulanmış, etkili Intelligence yapılandırması.

    Dönen sözlük yalnızca türetilmiş/etkili değerleri içerir; ham ortam
    değişkeni değerleri asla dahil edilmez.
    """
    warnings: list = []

    # enabled: yeni ad öncelikli; tanımsızsa eski bayrak okunur
    if os.environ.get(ENV_ENABLED) not in (None, ""):
        enabled = _parse_bool(ENV_ENABLED, DEFAULTS["enabled"], warnings)
    else:
        enabled = _parse_bool(ENV_ENABLED_LEGACY, DEFAULTS["enabled"],
                              warnings)

    local_only = _parse_bool(ENV_LOCAL_ONLY, DEFAULTS["local_only"],
                             warnings)
    external_llm = _parse_bool(ENV_EXTERNAL_LLM,
                               DEFAULTS["external_llm_enabled"], warnings)
    explainability = _parse_choice(ENV_EXPLAINABILITY,
                                   EXPLAINABILITY_LEVELS,
                                   DEFAULTS["explainability_level"],
                                   warnings)
    recommendation = _parse_choice(ENV_RECOMMENDATION,
                                   RECOMMENDATION_LEVELS,
                                   DEFAULTS["recommendation_level"],
                                   warnings)

    # Zorunluluklar (öncelik sırası: mission kilidi > local-only)
    if external_llm and EXTERNAL_LLM_HARD_LOCK:
        external_llm = False
        warnings.append({"setting": ENV_EXTERNAL_LLM,
                         "code": "EXTERNAL_LLM_LOCKED",
                         "message": "Harici LLM 1500.1 kapsamında "
                                    "kilitli — kapalı tutuldu."})
    if external_llm and local_only:
        external_llm = False
        warnings.append({"setting": ENV_EXTERNAL_LLM,
                         "code": "LOCAL_ONLY_ENFORCED",
                         "message": "local_only=true iken harici LLM "
                                    "açılamaz — kapalı tutuldu."})

    return {
        "enabled": enabled,
        "local_only": local_only,
        "external_llm_enabled": external_llm,
        "explainability_level": explainability,
        "recommendation_level": recommendation,
        "validation_warnings": warnings,
    }
