"""Mission 1900 — Monitoring API katmanı (Agent 05).

Yalnız TAŞIMA sınırı: istek doğrulama, API meta verisi üretimi
(``report_id``/``observed_at``/``generated_at`` YALNIZ burada) ve
immutable MonitoringApiResponse kurulumu.

Sözleşmeler (Agent 01 mimarisi — değiştirilemez):
- Hesap YOK: metrik/sağlık/uyarı/orkestrasyon/serileştirme yapılmaz;
  Monitoring Service TAM BİR KEZ çağrılır (tekrar deneme, döngü,
  gizli önbellek, arka plan yürütme yok).
- Meta veri MonitoringReport/AlertReport/MonitoringAnalysis'i
  DEĞİŞTİRMEZ; yanıt onların ETRAFINA kurulur (zarflar aynen taşınır).
- Saat yalnız UTC (RFC3339); UUID4 yalnız ``report_id`` için —
  başka hiçbir rastgelelik yok.
- Sterile hata yüzeyi (kapalı liste): INVALID_API_REQUEST /
  UNSUPPORTED_API_VERSION / UNKNOWN_PROVIDER geçersiz istekleri
  reddeder; servis arızası sterile FAILED yanıtına dönüşür
  (MONITORING_ANALYSIS_ERROR) — iz/yol/yük sızmaz.
- API durumu ile monitoring sağlığı BAĞIMSIZDIR: status yalnız
  kaynak/servis yürütmesinden türetilir, health_status'tan ASLA.

Şemada emir/yürütme alanı YOKTUR; modül taşıma olarak saftır.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Mapping

import monitoring_service

API_VERSION = 1
SUPPORTED_API_VERSIONS = (API_VERSION,)

# Sağlayıcı seçimi (kapalı liste — v1: yalnız varsayılan zincir)
PROVIDER_DEFAULT = "default"
SUPPORTED_PROVIDERS = (PROVIDER_DEFAULT,)

# İstek alanları (kapalı liste)
REQUEST_FIELDS = ("api_version", "provider")

# Sterile API hata kodları (kapalı liste)
ERROR_INVALID_API_REQUEST = "INVALID_API_REQUEST"
ERROR_UNSUPPORTED_API_VERSION = "UNSUPPORTED_API_VERSION"
ERROR_UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
ERROR_ANALYSIS = "MONITORING_ANALYSIS_ERROR"
API_ERROR_CODES = (
    ERROR_INVALID_API_REQUEST,
    ERROR_UNSUPPORTED_API_VERSION,
    ERROR_UNKNOWN_PROVIDER,
    ERROR_ANALYSIS,
)

# Yanıt durumları (kapalı liste)
STATUS_SUCCESS = "SUCCESS"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"
API_STATUSES = (STATUS_SUCCESS, STATUS_PARTIAL, STATUS_FAILED)

# Yanıt alan sırası (sabit — fazladan alan YOK)
API_RESPONSE_FIELDS = (
    "api_version",
    "report_id",
    "observed_at",
    "generated_at",
    "monitoring_analysis",
    "status",
    "limitations",
)


# ── İstek doğrulama ──────────────────────────────────────────────────

def _validate_request(request: Any) -> None:
    """Yalnız API parametreleri; sağlayıcı yükü/metrik enjeksiyonu YOK."""
    if not isinstance(request, Mapping):
        raise ValueError(ERROR_INVALID_API_REQUEST)
    for key in request:
        if key not in REQUEST_FIELDS:
            raise ValueError(ERROR_INVALID_API_REQUEST)
    version = request.get("api_version", API_VERSION)
    if isinstance(version, bool) or version not in SUPPORTED_API_VERSIONS:
        raise ValueError(ERROR_UNSUPPORTED_API_VERSION)
    provider = request.get("provider", PROVIDER_DEFAULT)
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(ERROR_UNKNOWN_PROVIDER)


# ── Durum normalizasyonu ─────────────────────────────────────────────

def _status(analysis: Mapping[str, Any] | None) -> str:
    """Yalnız kaynak/servis yürütmesinden türetilir (sağlıktan ASLA)."""
    if analysis is None:
        return STATUS_FAILED
    sources = analysis["sources"]
    all_complete = all(
        meta["status"] == monitoring_service.SOURCE_COMPLETE
        for meta in sources.values())
    if all_complete and not analysis["limitations"]:
        return STATUS_SUCCESS
    return STATUS_PARTIAL


# ── Kamu sözleşmesi ──────────────────────────────────────────────────

def analyze_monitoring_api(
        request: Mapping[str, Any] | None = None,
        analysis_supplier: Callable[[], Any] | None = None,
) -> MappingProxyType:
    """İstek → (Servis TAM BİR KEZ) → immutable MonitoringApiResponse.

    ``analysis_supplier`` test/DI içindir; None ise gerçek salt-okunur
    varsayılan zincir kullanılır. ``report_id`` (UUID4) ve RFC3339 UTC
    ``observed_at``/``generated_at`` YALNIZ burada üretilir; alınan
    zarflar DEĞİŞTİRİLMEZ, yanıt etraflarına kurulur.
    """
    _validate_request(request if request is not None else {})

    if analysis_supplier is None:
        def analysis_supplier() -> Any:
            return monitoring_service.analyze_monitoring(
                monitoring_service.build_default_monitoring_providers())

    limitations: tuple[str, ...] = ()
    analysis: Any = None
    status = STATUS_FAILED
    try:
        analysis = analysis_supplier()  # TAM BİR KEZ; tekrar yok
        if (not isinstance(analysis, Mapping)
                or tuple(analysis.keys())
                != monitoring_service.ANALYSIS_FIELDS
                or not isinstance(analysis["sources"], Mapping)
                or not all(isinstance(meta, Mapping)
                           and meta.get("status")
                           in monitoring_service.SOURCE_STATES
                           for meta in analysis["sources"].values())
                or not isinstance(analysis["limitations"], (tuple, list))):
            raise ValueError(ERROR_ANALYSIS)
        status = _status(analysis)  # arıza yolunda ASLA fırlatmaz
    except BaseException:
        # Sterile servis arızası: iz/yol/sağlayıcı yükü sızmaz.
        analysis = None
        status = STATUS_FAILED
        limitations = (ERROR_ANALYSIS,)

    now = datetime.now(timezone.utc).isoformat()
    return MappingProxyType({
        "api_version": API_VERSION,
        "report_id": str(uuid.uuid4()),   # yalnız burada; tek rastgelelik
        "observed_at": now,
        "generated_at": now,
        "monitoring_analysis": analysis,  # aynen taşınır (immutable zarf)
        "status": status,
        "limitations": limitations,
    })
