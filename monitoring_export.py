"""Mission 1900 — Monitoring Export katmanı (Agent 06).

YALNIZ serileştirme: hazır bir MonitoringApiResponse'u kararlı dış
temsile çevirir. İzleme analizi, uyarı üretimi, sağlayıcı orkestrasyonu,
meta veri üretimi ve kalıcılık YOKTUR.

Sözleşmeler (Agent 01 mimarisi — değiştirilemez):
- Alt katman ÇAĞRILMAZ (API/Service/Core/Alert Engine/sağlayıcı yok);
  bozuk girdi ONARILMAZ, eksik değer SENTEZLENMEZ.
- API meta verisi (report_id/observed_at/generated_at) AYNEN korunur;
  status/health/alerts YENİDEN HESAPLANMAZ.
- Decimal değerler yalnız kanonik string olarak dışa verilir (float'a
  çevrilmez, bilimsel gösterim yok, onaylı hassasiyet korunur).
- Kanonik JSON: ensure_ascii=False, sort_keys=True, separators=(",",":"),
  allow_nan=False, girinti yok, satır sonu yok → bayt-deterministik.
- Sterile hata yüzeyi: tek kod INVALID_MONITORING_EXPORT_INPUT
  (iz/yol/repr/yük sızmaz); kısmi serileştirme yapılmaz.
- Saat/UUID/rastgelelik/ortam erişimi YOK; dosya yazımı YOK.

Alt katman modülleri İTHAL EDİLMEZ (yalnız monitoring_api sabitleri);
beklenen iç şemalar taşıma sınırında yerel sabit olarak kilitlenir.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping

import monitoring_api

# Sterile dışa aktarma hata kodu (tek yetkili kod)
ERROR_INVALID_EXPORT_INPUT = "INVALID_MONITORING_EXPORT_INPUT"

# Dışa aktarma kök şeması (sabit — fazladan alan YOK)
EXPORT_FIELDS = (
    "api_version",
    "report_id",
    "observed_at",
    "generated_at",
    "status",
    "limitations",
    "monitoring",
    "alerts",
    "sources",
)

# Beklenen iç şemalar (taşıma sınırı kopyaları — alt katman ithali
# YASAK olduğundan burada kilitlenir; regresyon testleri kaynak
# modüllerle eş olduklarını doğrular).
ANALYSIS_FIELDS = (
    "monitoring_report",
    "alert_report",
    "sources",
    "limitations",
)
MONITORING_REPORT_FIELDS = (
    "monitoring_version",
    "report_id",
    "observed_at",
    "strategy_version",
    "analysis_version",
    "observation_window",
    "data_quality",
    "recommendation_count",
    "evaluated_count",
    "success_rate",
    "average_return",
    "maximum_drawdown",
    "confidence_accuracy",
    "market_regime",
    "health_status",
    "alerts",
    "limitations",
)
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
SOURCE_META_FIELDS = ("status", "code")
SOURCE_STATES = ("COMPLETE", "PARTIAL", "UNAVAILABLE")


def _fail() -> ValueError:
    # Sterile: mesajda repr/yol/iz/yük yoktur.
    return ValueError(ERROR_INVALID_EXPORT_INPUT)


# ── JSON-uyumlu dönüştürme (immutable) ───────────────────────────────

def _convert(value: Any) -> Any:
    """Decimal→kanonik string; tuple→tuple; Mapping→MappingProxyType.

    None/str/int/bool aynen; float ve bilinmeyen tipler REDDEDİLİR
    (onarım yok, repr sızıntısı yok).
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise _fail()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _fail()
        return format(value, "f")  # bilimsel gösterim yok
    if isinstance(value, (tuple, list)):
        return tuple(_convert(item) for item in value)
    if isinstance(value, Mapping):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # JSON-güvenli anahtar zorunlu
                raise _fail()
            converted[key] = _convert(item)
        return MappingProxyType(converted)
    raise _fail()


def _plain(value: Any) -> Any:
    """Immutable model → json.dumps'ın kabul ettiği düz yapı."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


# ── Girdi doğrulama (sterile) ────────────────────────────────────────

def _require_fields(candidate: Any, fields: tuple[str, ...]) -> Mapping:
    if (not isinstance(candidate, Mapping)
            or tuple(candidate.keys()) != fields):
        raise _fail()
    return candidate


def _require_str_tuple(candidate: Any) -> tuple[str, ...]:
    if not isinstance(candidate, (tuple, list)):
        raise _fail()
    for item in candidate:
        if not isinstance(item, str):
            raise _fail()
    return tuple(candidate)


def _validate_response(api_response: Any) -> Mapping[str, Any]:
    response = _require_fields(
        api_response, monitoring_api.API_RESPONSE_FIELDS)
    if response["api_version"] != monitoring_api.API_VERSION or isinstance(
            response["api_version"], bool):
        raise _fail()
    for key in ("report_id", "observed_at", "generated_at"):
        if not isinstance(response[key], str) or not response[key]:
            raise _fail()
    if response["status"] not in monitoring_api.API_STATUSES:
        raise _fail()
    _require_str_tuple(response["limitations"])

    analysis = response["monitoring_analysis"]
    if analysis is None:
        if response["status"] != monitoring_api.STATUS_FAILED:
            raise _fail()
        return response

    analysis = _require_fields(analysis, ANALYSIS_FIELDS)
    _require_fields(analysis["monitoring_report"], MONITORING_REPORT_FIELDS)
    report = analysis["monitoring_report"]
    if not isinstance(report["alerts"], (tuple, list)):
        raise _fail()
    _require_str_tuple(report["limitations"])

    alert_report = _require_fields(
        analysis["alert_report"], ALERT_REPORT_FIELDS)
    if not isinstance(alert_report["alerts"], (tuple, list)):
        raise _fail()
    for alert in alert_report["alerts"]:
        _require_fields(alert, ALERT_FIELDS)
    _require_str_tuple(alert_report["limitations"])

    if not isinstance(analysis["sources"], Mapping):
        raise _fail()
    for name, meta in analysis["sources"].items():
        if not isinstance(name, str):
            raise _fail()
        _require_fields(meta, SOURCE_META_FIELDS)
        if meta["status"] not in SOURCE_STATES:
            raise _fail()
        if meta["code"] is not None and not isinstance(meta["code"], str):
            raise _fail()
    _require_str_tuple(analysis["limitations"])
    return response


# ── Kamu sözleşmesi ──────────────────────────────────────────────────

def build_monitoring_export(api_response: Any) -> MappingProxyType:
    """MonitoringApiResponse → derin immutable, JSON-uyumlu model.

    Hesap YOK: status/health/alerts aynen taşınır; meta veri aynen
    korunur; girdi DEĞİŞTİRİLMEZ.
    """
    response = _validate_response(api_response)
    analysis = response["monitoring_analysis"]

    if analysis is None:
        monitoring: Any = None
        alerts: tuple = ()
        sources: tuple = ()
    else:
        monitoring = _convert(analysis["monitoring_report"])
        alerts = tuple(
            _convert(alert) for alert in analysis["alert_report"]["alerts"])
        sources = tuple(
            MappingProxyType({
                "name": name,
                "status": meta["status"],
                "code": meta["code"],
            })
            for name, meta in analysis["sources"].items())

    return MappingProxyType({
        "api_version": response["api_version"],
        "report_id": response["report_id"],
        "observed_at": response["observed_at"],
        "generated_at": response["generated_at"],
        "status": response["status"],
        "limitations": _require_str_tuple(response["limitations"]),
        "monitoring": monitoring,
        "alerts": alerts,
        "sources": sources,
    })


def serialize_monitoring_export(api_response: Any) -> str:
    """Kanonik JSON string (bayt-deterministik; satır sonu yok)."""
    export = build_monitoring_export(api_response)
    try:
        return json.dumps(
            _plain(export),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except Exception:
        # Sterile: serileştirici iç mesajı/repr sızmaz.
        raise _fail() from None
