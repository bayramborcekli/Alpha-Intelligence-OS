"""Mission 1900 — Alert Engine (Agent 03).

Agent 02'nin ürettiği immutable MonitoringReport'u tüketir ve
deterministik, immutable AlertReport üretir.

Sözleşmeler (Agent 01 mimarisi — değiştirilemez):
- Saf kural motoru: I/O yok, saat yok, UUID yok, rastgelelik yok,
  bildirim yok, kalıcılık yok. ``generated_at`` bu katmanda daima
  ``null`` kalır (API sınırı sahipliği); ``report_id`` aynen taşınır.
- Metrik/sağlık YENİDEN HESAPLANMAZ: yalnız MonitoringReport alanları
  okunur; eşik sabitleri monitoring_intelligence'tan içe aktarılır
  (tek tanım yeri — sayısal sabit çoğaltılmaz, döngü yoktur).
- Kapalı kod listesi: her kodun sabit severity / başlık / açıklama /
  bileşen / önerilen eylem şablonu vardır; serbest metin yoktur.
- Uyarı kimlikleri son kararlı sıralamadan SONRA atanan A1, A2, ...
  sayaçlarıdır. Aynı girdi → özdeş çıktı.
- Kural sırası sabittir; ağır severity önce gelir, aynı severity
  içinde belgelenmiş kural sırası korunur. Her kod en fazla bir kez.
- Zarf ihlalleri sterile ValueError kodu üretir (ham istisna yok).

Şemada emir/yürütme alanı YOKTUR; modül hesapsal olarak saftır.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from monitoring_intelligence import (
    CONFIDENCE_ACC_DEGRADED_PCT,
    COVERAGE_DEGRADED_PCT,
    DATA_QUALITY_OK,
    DATA_QUALITY_PARTIAL,
    DATA_QUALITY_UNAVAILABLE,
    DRAWDOWN_DEGRADED_PCT,
    HEALTH_CRITICAL,
    HEALTH_DEGRADED,
    HEALTH_STATUSES,
    LIMIT_NO_EVALUATED_OUTCOMES,
    LIMIT_NO_OBSERVATIONS,
    LIMIT_UNKNOWN_MARKET_REGIME,
    MONITORING_VERSION,
    REGIME_UNKNOWN,
    REPORT_FIELDS,
    SUCCESS_DEGRADED_PCT,
)

ALERT_VERSION = 1

# Sterile hata kodları (kapalı liste — Alert Engine sınırlamaları)
ERROR_INVALID_MONITORING_REPORT = "INVALID_MONITORING_REPORT"
ERROR_UNSUPPORTED_MONITORING_VERSION = "UNSUPPORTED_MONITORING_VERSION"
ERROR_UNKNOWN_HEALTH_STATUS = "UNKNOWN_HEALTH_STATUS"
ERROR_INCONSISTENT_MONITORING_REPORT = "INCONSISTENT_MONITORING_REPORT"
ALERT_LIMITATION_CODES = (
    ERROR_INCONSISTENT_MONITORING_REPORT,
    ERROR_INVALID_MONITORING_REPORT,
    ERROR_UNKNOWN_HEALTH_STATUS,
    ERROR_UNSUPPORTED_MONITORING_VERSION,
)

# Severity (kapalı liste) — açık öncelik haritası; alfabetik sıraya
# ASLA dayanılmaz.
SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"
SEVERITIES = (SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO)
SEVERITY_PRECEDENCE = MappingProxyType({
    SEVERITY_CRITICAL: 0,
    SEVERITY_WARNING: 1,
    SEVERITY_INFO: 2,
})

# Önerilen eylemler (kapalı liste)
ACTION_REVIEW = "REVIEW"
ACTION_ACKNOWLEDGE = "ACKNOWLEDGE"
ACTION_NO_ACTION = "NO_ACTION"
RECOMMENDED_ACTIONS = (ACTION_REVIEW, ACTION_ACKNOWLEDGE, ACTION_NO_ACTION)

# Etkilenen bileşenler (kapalı liste)
COMPONENT_MONITORING = "MONITORING_CORE"
COMPONENT_DATA = "DATA_PIPELINE"
COMPONENT_STRATEGY = "STRATEGY_INTELLIGENCE"
COMPONENT_MARKET = "MARKET_CONTEXT"

# Uyarı alan sırası (sabit 8 alan)
ALERT_FIELDS = (
    "alert_id",
    "severity",
    "code",
    "title",
    "description",
    "affected_component",
    "trigger_reason",
    "recommended_action",
)

# AlertReport alan sırası (sabit 9 alan)
ALERT_REPORT_FIELDS = (
    "alert_version",
    "monitoring_version",
    "report_id",
    "generated_at",
    "health_status",
    "alert_count",
    "highest_severity",
    "alerts",
    "limitations",
)

# ── Kapalı uyarı kod kümesi ──────────────────────────────────────────
# Tek yetkili tanım yeri. Her kodun SABİT severity/başlık/açıklama/
# bileşen/tetik nedeni/önerilen eylemi vardır (serbest metin yok,
# emir/pozisyon/fiyat bilgisi yok). Kritik eşik durumları zaten
# MONITORING_CRITICAL ile temsil edilir; metrik kodları sabit WARNING.
ALERT_CODES = MappingProxyType({
    "MONITORING_CRITICAL": MappingProxyType({
        "severity": SEVERITY_CRITICAL,
        "title": "İzleme durumu kritik",
        "description": ("Strateji izleme sağlık durumu KRİTİK olarak "
                        "sınıflandırıldı; performans metrikleri kritik "
                        "eşiklerin dışında."),
        "affected_component": COMPONENT_MONITORING,
        "trigger_reason": "HEALTH_STATUS_CRITICAL",
        "recommended_action": ACTION_REVIEW,
    }),
    "MONITORING_DEGRADED": MappingProxyType({
        "severity": SEVERITY_WARNING,
        "title": "İzleme durumu bozulmuş",
        "description": ("Strateji izleme sağlık durumu BOZULMUŞ olarak "
                        "sınıflandırıldı; en az bir metrik uyarı "
                        "eşiğinin dışında."),
        "affected_component": COMPONENT_MONITORING,
        "trigger_reason": "HEALTH_STATUS_DEGRADED",
        "recommended_action": ACTION_REVIEW,
    }),
    "DATA_UNAVAILABLE": MappingProxyType({
        "severity": SEVERITY_WARNING,
        "title": "İzleme verisi kullanılamıyor",
        "description": ("İzleme için gerekli gözlem verisi mevcut "
                        "değil; sağlık durumu doğrulanamıyor."),
        "affected_component": COMPONENT_DATA,
        "trigger_reason": "DATA_QUALITY_UNAVAILABLE",
        "recommended_action": ACTION_REVIEW,
    }),
    "DATA_PARTIAL": MappingProxyType({
        "severity": SEVERITY_WARNING,
        "title": "İzleme verisi kısmi",
        "description": ("Gözlem verisinin bir bölümü eksik veya "
                        "düşük kaliteli; metrikler kısmi veriyle "
                        "hesaplandı."),
        "affected_component": COMPONENT_DATA,
        "trigger_reason": "DATA_QUALITY_PARTIAL",
        "recommended_action": ACTION_ACKNOWLEDGE,
    }),
    "NO_OBSERVATIONS": MappingProxyType({
        "severity": SEVERITY_INFO,
        "title": "Gözlem yok",
        "description": ("Bu pencerede değerlendirilecek öneri gözlemi "
                        "bulunmuyor."),
        "affected_component": COMPONENT_DATA,
        "trigger_reason": "RECOMMENDATION_COUNT_ZERO",
        "recommended_action": ACTION_ACKNOWLEDGE,
    }),
    "NO_EVALUATED_OUTCOMES": MappingProxyType({
        "severity": SEVERITY_INFO,
        "title": "Değerlendirilmiş sonuç yok",
        "description": ("Gözlemler mevcut ancak hiçbiri sonuç "
                        "değerlendirmesi için uygun değil."),
        "affected_component": COMPONENT_DATA,
        "trigger_reason": "EVALUATED_COUNT_ZERO",
        "recommended_action": ACTION_ACKNOWLEDGE,
    }),
    "LOW_SUCCESS_RATE": MappingProxyType({
        "severity": SEVERITY_WARNING,
        "title": "Düşük başarı oranı",
        "description": ("Değerlendirilen önerilerin başarı oranı "
                        "bozulma eşiğinin altında."),
        "affected_component": COMPONENT_STRATEGY,
        "trigger_reason": "SUCCESS_RATE_BELOW_THRESHOLD",
        "recommended_action": ACTION_REVIEW,
    }),
    "HIGH_DRAWDOWN": MappingProxyType({
        "severity": SEVERITY_WARNING,
        "title": "Yüksek maksimum düşüş",
        "description": ("Gözlemlenen maksimum düşüş bozulma eşiğinin "
                        "üzerinde."),
        "affected_component": COMPONENT_STRATEGY,
        "trigger_reason": "MAX_DRAWDOWN_ABOVE_THRESHOLD",
        "recommended_action": ACTION_REVIEW,
    }),
    "LOW_CONFIDENCE_ACCURACY": MappingProxyType({
        "severity": SEVERITY_WARNING,
        "title": "Düşük güven kalibrasyonu",
        "description": ("Öneri güven değerleri gerçekleşen sonuçlarla "
                        "zayıf kalibrasyon gösteriyor."),
        "affected_component": COMPONENT_STRATEGY,
        "trigger_reason": "CONFIDENCE_ACCURACY_BELOW_THRESHOLD",
        "recommended_action": ACTION_REVIEW,
    }),
    "LOW_EVALUATION_COVERAGE": MappingProxyType({
        "severity": SEVERITY_WARNING,
        "title": "Düşük değerlendirme kapsamı",
        "description": ("Gözlemlerin yalnız küçük bir bölümü "
                        "değerlendirilebildi; metrikler sınırlı "
                        "kapsamı yansıtıyor."),
        "affected_component": COMPONENT_DATA,
        "trigger_reason": "EVALUATION_COVERAGE_BELOW_THRESHOLD",
        "recommended_action": ACTION_ACKNOWLEDGE,
    }),
    "UNKNOWN_MARKET_REGIME": MappingProxyType({
        "severity": SEVERITY_INFO,
        "title": "Piyasa rejimi bilinmiyor",
        "description": ("Piyasa rejimi belirlenemedi; metrikler rejim "
                        "bağlamı olmadan yorumlanmalı."),
        "affected_component": COMPONENT_MARKET,
        "trigger_reason": "MARKET_REGIME_UNKNOWN",
        "recommended_action": ACTION_NO_ACTION,
    }),
})

_DATA_QUALITIES = (DATA_QUALITY_OK, DATA_QUALITY_PARTIAL,
                   DATA_QUALITY_UNAVAILABLE)
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ── Doğrulama yardımcıları ───────────────────────────────────────────

def _metric(value: Any, code: str) -> Decimal | None:
    """Rapor metriği: None veya Decimal string. Başka her şey ihlal."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(code)
    try:
        parsed = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(code)
    if not parsed.is_finite():
        raise ValueError(code)
    return parsed


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(ERROR_INVALID_MONITORING_REPORT)
    if value < 0:
        raise ValueError(ERROR_INVALID_MONITORING_REPORT)
    return value


def _validate_report(report: Any) -> dict[str, Any]:
    """MonitoringReport'u doğrular; ayrıştırılmış görünüm döndürür.

    Girdi MappingProxyType, dict veya başka bir Mapping olabilir;
    asla mutasyona uğratılmaz. İhlaller sterile ValueError kodlarıdır.
    """
    if not isinstance(report, Mapping):
        raise ValueError(ERROR_INVALID_MONITORING_REPORT)
    for field in REPORT_FIELDS:
        if field not in report:
            raise ValueError(ERROR_INVALID_MONITORING_REPORT)
    if report["monitoring_version"] != MONITORING_VERSION:
        raise ValueError(ERROR_UNSUPPORTED_MONITORING_VERSION)
    health = report["health_status"]
    if health not in HEALTH_STATUSES:
        raise ValueError(ERROR_UNKNOWN_HEALTH_STATUS)
    quality = report["data_quality"]
    if quality not in _DATA_QUALITIES:
        raise ValueError(ERROR_INVALID_MONITORING_REPORT)

    # Agent 02 alanı boş OLMAK ZORUNDA: önceki uyarılar birleştirilmez.
    alerts = report["alerts"]
    if not isinstance(alerts, (tuple, list)) or len(alerts) != 0:
        raise ValueError(ERROR_INVALID_MONITORING_REPORT)

    limitations = report["limitations"]
    if not isinstance(limitations, (tuple, list)) or not all(
            isinstance(item, str) for item in limitations):
        raise ValueError(ERROR_INVALID_MONITORING_REPORT)

    recommendation_count = _count(report["recommendation_count"])
    evaluated_count = _count(report["evaluated_count"])
    if evaluated_count > recommendation_count:
        raise ValueError(ERROR_INCONSISTENT_MONITORING_REPORT)

    return {
        "report_id": report["report_id"],
        "health_status": health,
        "data_quality": quality,
        "recommendation_count": recommendation_count,
        "evaluated_count": evaluated_count,
        "success_rate": _metric(report["success_rate"],
                                ERROR_INVALID_MONITORING_REPORT),
        "maximum_drawdown": _metric(report["maximum_drawdown"],
                                    ERROR_INVALID_MONITORING_REPORT),
        "confidence_accuracy": _metric(report["confidence_accuracy"],
                                       ERROR_INVALID_MONITORING_REPORT),
        "market_regime": report["market_regime"],
        "limitations": tuple(limitations),
    }


# ── Kural değerlendirme ──────────────────────────────────────────────
# SABİT, BELGELENMİŞ KURAL SIRASI (aynı severity içindeki nihai sıra):
#  1 MONITORING_CRITICAL        health_status == CRITICAL
#  2 MONITORING_DEGRADED        health_status == DEGRADED
#  3 DATA_UNAVAILABLE           data_quality == UNAVAILABLE
#  4 DATA_PARTIAL               data_quality == PARTIAL
#  5 NO_OBSERVATIONS            recommendation_count == 0 veya sınırlama
#  6 NO_EVALUATED_OUTCOMES      evaluated_count == 0 veya sınırlama
#  7 LOW_SUCCESS_RATE           success_rate < SUCCESS_DEGRADED_PCT
#  8 HIGH_DRAWDOWN              maximum_drawdown > DRAWDOWN_DEGRADED_PCT
#  9 LOW_CONFIDENCE_ACCURACY    confidence_accuracy < CONFIDENCE_ACC_DEGRADED_PCT
# 10 LOW_EVALUATION_COVERAGE    kapsam < COVERAGE_DEGRADED_PCT
# 11 UNKNOWN_MARKET_REGIME      rejim null/UNKNOWN veya sınırlama

def _triggered_codes(view: dict[str, Any]) -> list[str]:
    """Kuralları sabit sırada değerlendirir; kod başına en fazla bir
    tetikleme (metrik + sınırlama aynı kodu bağımsız tetiklese bile)."""
    codes: list[str] = []
    limitations = view["limitations"]

    if view["health_status"] == HEALTH_CRITICAL:
        codes.append("MONITORING_CRITICAL")
    if view["health_status"] == HEALTH_DEGRADED:
        codes.append("MONITORING_DEGRADED")
    if view["data_quality"] == DATA_QUALITY_UNAVAILABLE:
        codes.append("DATA_UNAVAILABLE")
    if view["data_quality"] == DATA_QUALITY_PARTIAL:
        codes.append("DATA_PARTIAL")
    if (view["recommendation_count"] == 0
            or LIMIT_NO_OBSERVATIONS in limitations):
        codes.append("NO_OBSERVATIONS")
    if (view["evaluated_count"] == 0
            or LIMIT_NO_EVALUATED_OUTCOMES in limitations):
        codes.append("NO_EVALUATED_OUTCOMES")
    if (view["success_rate"] is not None
            and view["success_rate"] < SUCCESS_DEGRADED_PCT):
        codes.append("LOW_SUCCESS_RATE")
    if (view["maximum_drawdown"] is not None
            and view["maximum_drawdown"] > DRAWDOWN_DEGRADED_PCT):
        codes.append("HIGH_DRAWDOWN")
    if (view["confidence_accuracy"] is not None
            and view["confidence_accuracy"] < CONFIDENCE_ACC_DEGRADED_PCT):
        codes.append("LOW_CONFIDENCE_ACCURACY")
    if view["recommendation_count"] > 0:
        coverage = (Decimal(view["evaluated_count"])
                    / Decimal(view["recommendation_count"]) * _HUNDRED)
        if coverage < COVERAGE_DEGRADED_PCT:
            codes.append("LOW_EVALUATION_COVERAGE")
    regime = view["market_regime"]
    if (regime is None or regime == REGIME_UNKNOWN
            or LIMIT_UNKNOWN_MARKET_REGIME in limitations):
        codes.append("UNKNOWN_MARKET_REGIME")

    # Tekrarsızlık garantisi (kod başına en fazla bir uyarı).
    seen: set[str] = set()
    unique: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            unique.append(code)
    return unique


def build_alert_report(monitoring_report: Any) -> MappingProxyType:
    """MonitoringReport → immutable AlertReport.

    Saf ve deterministik: saat/UUID/I-O yok; ``generated_at`` null
    kalır (API sahipliği); ``report_id`` aynen taşınır. Girdi
    mutasyona uğratılmaz; çıktı derinlemesine immutable'dır.
    """
    view = _validate_report(monitoring_report)

    codes = _triggered_codes(view)
    # Kararlı sıralama: önce severity önceliği, sonra belgelenmiş kural
    # sırası (kural sırası zaten codes listesinin sırasıdır). Python
    # sort kararlı olduğundan aynı severity içinde kural sırası korunur.
    ordered = sorted(codes,
                     key=lambda c: SEVERITY_PRECEDENCE[
                         ALERT_CODES[c]["severity"]])

    alerts = tuple(
        MappingProxyType({
            "alert_id": f"A{index}",
            "severity": ALERT_CODES[code]["severity"],
            "code": code,
            "title": ALERT_CODES[code]["title"],
            "description": ALERT_CODES[code]["description"],
            "affected_component": ALERT_CODES[code]["affected_component"],
            "trigger_reason": ALERT_CODES[code]["trigger_reason"],
            "recommended_action": ALERT_CODES[code]["recommended_action"],
        })
        for index, code in enumerate(ordered, start=1)
    )

    highest = None
    if alerts:
        highest = min((alert["severity"] for alert in alerts),
                      key=SEVERITY_PRECEDENCE.__getitem__)

    return MappingProxyType({
        "alert_version": ALERT_VERSION,
        "monitoring_version": MONITORING_VERSION,
        "report_id": view["report_id"],
        "generated_at": None,   # yalnız API sınırında üretilir
        "health_status": view["health_status"],
        "alert_count": len(alerts),
        "highest_severity": highest,
        "alerts": alerts,
        "limitations": (),      # başarı yolunda boş; kapalı küme
    })
