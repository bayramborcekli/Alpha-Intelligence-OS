"""Mission 1900 — Monitoring Service (Agent 04).

İNCE orkestrasyon katmanı: gözlem sağlayıcısını TAM BİR KEZ çalıştırır,
Monitoring Core'u TAM BİR KEZ, Alert Engine'i TAM BİR KEZ çağırır ve
tek immutable MonitoringAnalysis zarfı döndürür.

Sözleşmeler (Agent 01 mimarisi — değiştirilemez):
- Hesap YOK: metrik/sağlık/uyarı kuralı burada değerlendirilmez;
  tümü Core ve Alert Engine'e devredilir. Serileştirme yok, API
  meta verisi yok (``report_id``/``generated_at``/``observed_at``/
  UUID/saat bu katmanda ÜRETİLMEZ — API sınırı sahipliği).
- Kalıcılaştırma yok, tekrar deneme yok, gizli önbellek yok,
  zamanlayıcı yok. Varsayılan sağlayıcı zinciri salt-okunurdur
  (portföy zinciri ``persist=False`` kullanır).
- Sağlayıcı exception'ları asla dışarı sızmaz: sterile UNAVAILABLE
  kaynağa dönüşür; Core dürüst boş girdiyle yine çalışır. Bayat
  (stale) girdi PARTIAL'a düşürülür (kalite beyanı; veri değişmez).
- Beklenmedik iç hatalar sterile ``MONITORING_ANALYSIS_ERROR`` olur;
  dosya yolu / stack trace / sağlayıcı yükü sızmaz.
- Aynı girdi → özdeş çıktı; kararlı kaynak sıralaması; mutasyon yok.

Şemada emir/yürütme alanı YOKTUR; modül orkestrasyon olarak saftır.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Callable, Mapping

import alert_engine
import monitoring_intelligence

# Sağlayıcı sözleşmesi (1700/1800 kalıbı):
# callable() -> {"freshness": "fresh"|"stale", "data": <StrategyProposal>}
PROVIDER_NAMES = ("strategy_proposal",)
FRESHNESS_STATES = ("fresh", "stale")

# Kaynak durumları (kapalı liste — yalnız sağlayıcı yürütmesini anlatır)
SOURCE_COMPLETE = "COMPLETE"
SOURCE_PARTIAL = "PARTIAL"
SOURCE_UNAVAILABLE = "UNAVAILABLE"
SOURCE_STATES = (SOURCE_COMPLETE, SOURCE_PARTIAL, SOURCE_UNAVAILABLE)

# Sterile kodlar (kapalı liste)
CODE_PROVIDER_FAILED = "PROVIDER_FAILED"
CODE_INVALID_RESULT = "INVALID_PROVIDER_RESULT"
CODE_UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
ERROR_ANALYSIS = "MONITORING_ANALYSIS_ERROR"
ERROR_INVALID_INPUT = "INVALID_INPUT"

# Zarf sınırlamaları (kapalı liste)
LIMIT_OBSERVATIONS_UNAVAILABLE = "OBSERVATIONS_UNAVAILABLE"
LIMIT_OBSERVATIONS_STALE = "OBSERVATIONS_STALE"
SERVICE_LIMITATION_CODES = (
    LIMIT_OBSERVATIONS_STALE,
    LIMIT_OBSERVATIONS_UNAVAILABLE,
)

# MonitoringAnalysis alan sırası (sabit)
ANALYSIS_FIELDS = (
    "monitoring_report",
    "alert_report",
    "sources",
    "limitations",
)


# ── Sağlayıcı toplama (sterile) ──────────────────────────────────────

def _collect(provider: Any) -> dict[str, Any]:
    """Sağlayıcıyı TAM BİR KEZ, sterile çalıştırır; sızıntı yok."""
    if not callable(provider):
        return {"available": False, "freshness": None,
                "code": CODE_INVALID_RESULT, "data": None}
    try:
        result = provider()
    except BaseException:
        return {"available": False, "freshness": None,
                "code": CODE_PROVIDER_FAILED, "data": None}
    if (not isinstance(result, dict)
            or result.get("freshness") not in FRESHNESS_STATES
            or not isinstance(result.get("data"), dict)):
        return {"available": False, "freshness": None,
                "code": CODE_INVALID_RESULT, "data": None}
    return {"available": True, "freshness": result["freshness"],
            "code": None, "data": result["data"]}


def _empty_observation_input() -> dict[str, Any]:
    """Sağlayıcı yokken Core'a verilecek dürüst boş girdi."""
    return {
        "strategy_version":
            monitoring_intelligence.SUPPORTED_STRATEGY_VERSION,
        "analysis_version":
            monitoring_intelligence.SUPPORTED_ANALYSIS_VERSION,
        "recommendations": [],
        "data_quality": monitoring_intelligence.DATA_QUALITY_UNAVAILABLE,
        "market_regime": None,
    }


def _observation_input(proposal: Mapping[str, Any],
                       degrade_to_partial: bool) -> dict[str, Any]:
    """StrategyProposal → MonitoringObservationInput (hesap YOK).

    Yalnız alan taşıma: öneriler aynen aktarılır (sonuç verisi yoksa
    Core onları güvenle "değerlendirilmemiş" sayar). Bayat girdi
    kalite beyanı olarak PARTIAL'a düşürülür; veri değiştirilmez.
    """
    quality = proposal.get("data_quality")
    if degrade_to_partial and quality == \
            monitoring_intelligence.DATA_QUALITY_OK:
        quality = monitoring_intelligence.DATA_QUALITY_PARTIAL
    recs = proposal.get("recommendations")
    return {
        "strategy_version":
            monitoring_intelligence.SUPPORTED_STRATEGY_VERSION,
        "analysis_version":
            monitoring_intelligence.SUPPORTED_ANALYSIS_VERSION,
        "recommendations": list(recs) if isinstance(recs, (list, tuple))
        else [],
        "data_quality": quality if quality in (
            monitoring_intelligence.DATA_QUALITY_OK,
            monitoring_intelligence.DATA_QUALITY_PARTIAL,
            monitoring_intelligence.DATA_QUALITY_UNAVAILABLE) else None,
        "market_regime": proposal.get("market_regime"),
    }


def _source_meta(status: str, code: str | None) -> MappingProxyType:
    return MappingProxyType({"status": status, "code": code})


# ── Kamu sözleşmesi ──────────────────────────────────────────────────

def analyze_monitoring(providers: Mapping[str, Callable[[], Any]]
                       ) -> MappingProxyType:
    """Sağlayıcı → Monitoring Core → Alert Engine → MonitoringAnalysis.

    Her aşama TAM BİR KEZ çağrılır; tekrar deneme/önbellek yok.
    Dönüş: immutable ``{monitoring_report, alert_report, sources,
    limitations}`` zarfı. Kimlik/saat alanları burada üretilmez.
    """
    if not isinstance(providers, Mapping):
        raise ValueError(ERROR_INVALID_INPUT)
    for name in providers:
        if name not in PROVIDER_NAMES:
            raise ValueError(CODE_UNKNOWN_PROVIDER)

    meta = _collect(providers.get("strategy_proposal"))

    limitations: list[str] = []
    if not meta["available"]:
        status = SOURCE_UNAVAILABLE
        observation_input = _empty_observation_input()
        limitations.append(LIMIT_OBSERVATIONS_UNAVAILABLE)
    elif meta["freshness"] == "stale":
        status = SOURCE_PARTIAL
        observation_input = _observation_input(meta["data"], True)
        limitations.append(LIMIT_OBSERVATIONS_STALE)
    else:
        status = SOURCE_COMPLETE
        observation_input = _observation_input(meta["data"], False)

    try:
        monitoring_report = monitoring_intelligence \
            .build_monitoring_report(observation_input)
        alert_report = alert_engine.build_alert_report(monitoring_report)
    except BaseException:
        # Sterile iç hata: sağlayıcı yükü / iz sızmaz.
        raise ValueError(ERROR_ANALYSIS) from None

    return MappingProxyType({
        "monitoring_report": monitoring_report,
        "alert_report": alert_report,
        "sources": MappingProxyType({
            "strategy_proposal": _source_meta(status, meta["code"]),
        }),
        "limitations": tuple(sorted(limitations)),
    })


class MonitoringService:
    """İnce OO sarmalayıcı; durum tutmaz (yalnız enjekte bağımlılık)."""

    def __init__(self, providers: Mapping[str, Callable[[], Any]]):
        self._providers = dict(providers)

    def get_analysis(self) -> MappingProxyType:
        return analyze_monitoring(self._providers)


def build_default_monitoring_providers() -> dict[str, Callable[[], Any]]:
    """Varsayılan sağlayıcı: gerçek Mission 1800 stratejisi zinciri.

    İçe aktarmalar tembeldir; modül import'u canlı sisteme dokunmaz.
    Zincir uçtan uca salt-okunurdur (portföy risk sağlayıcısı
    ``persist=False`` kullanır); hiçbir sağlayıcı gözlem KALICILAŞTIRMAZ,
    Core çıktısını DEĞİŞTİRMEZ, uyarı ÜRETMEZ, Exchange yazma API'sine
    ERİŞMEZ.
    """
    def strategy_proposal_provider() -> dict[str, Any]:
        import strategy_service
        proposal = strategy_service.analyze_strategy(
            strategy_service.build_default_strategy_providers())
        return {"freshness": "fresh", "data": proposal}

    return {"strategy_proposal": strategy_proposal_provider}
