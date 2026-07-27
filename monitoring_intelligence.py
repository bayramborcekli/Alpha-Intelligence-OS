"""Mission 1900 — Monitoring Core (Agent 02).

Çağıranın SAĞLADIĞI StrategyProposal gözlemlerini ve sonuçlarını
deterministik olarak değerlendirir; immutable MonitoringReport üretir.

Sözleşmeler (Agent 01 mimarisi — değiştirilemez):
- Saf hesap: I/O yok, saat yok, rastgelelik yok, global durum yok.
  ``report_id``/``observed_at`` bu katmanda ÜRETİLMEZ (API sınırı);
  Core çıktısında ``observed_at`` daima ``null`` kalır.
- Tarih deposu YOKTUR: gözlemler yalnız girdiyle gelir; Core hiçbir
  şey saklamaz, okumaz, zamanlamaz.
- ``alerts`` Agent 02'de daima BOŞ immutable koleksiyondur; uyarı
  üretimi yalnız Agent 03 (Alert Engine) sorumluluğundadır.
- Sayısal matematik yalnız Decimal; binary float REDDEDİLİR
  (NaN/Infinity dahil). Bilinmeyen değer null kalır — asla 0 olmaz.
- Geçersiz TEKİL gözlem raporu çökertmez: güvenli biçimde
  "değerlendirilmemiş" sayılır. Zarf düzeyi ihlaller sterile
  ``ValueError`` kodu üretir (FLOAT_REJECTED / INVALID_INPUT).
- Aynı girdi → bayt-özdeş çıktı (kararlı sıralamalar, sabit kural
  sırası, kanonik sabit-nokta string'ler).

Şemada emir/yürütme alanı YOKTUR; modül hesapsal olarak saftır.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

MONITORING_VERSION = 1
SUPPORTED_STRATEGY_VERSION = 1
SUPPORTED_ANALYSIS_VERSION = 1

# Sterile hata kodları (zarf düzeyi ihlaller)
ERROR_FLOAT_REJECTED = "FLOAT_REJECTED"
ERROR_INVALID_INPUT = "INVALID_INPUT"

# Veri kalitesi (kapalı liste — 1700/1800 kalıbı)
DATA_QUALITY_OK = "OK"
DATA_QUALITY_PARTIAL = "PARTIAL"
DATA_QUALITY_UNAVAILABLE = "UNAVAILABLE"
_DATA_QUALITIES = (DATA_QUALITY_OK, DATA_QUALITY_PARTIAL,
                   DATA_QUALITY_UNAVAILABLE)

# Eylemler (Strategy Intelligence kapalı listesi — takma ad üretilmez)
ACTION_REDUCE = "REDUCE"
ACTION_INCREASE = "INCREASE"
ACTION_HOLD = "HOLD"
ACTION_REBALANCE = "REBALANCE"
ACTION_DIVERSIFY = "DIVERSIFY"
_ACTIONS = (ACTION_REDUCE, ACTION_INCREASE, ACTION_HOLD,
            ACTION_REBALANCE, ACTION_DIVERSIFY)
# Yönlü değerlendirme: INCREASE uzun-benzeri (pozitif hareket = pozitif
# getiri), REDUCE kısa-benzeri (negatif hareket = pozitif getiri).
# HOLD/REBALANCE/DIVERSIFY için Agent 01 değerlendirilebilir sonuç
# tanımlamadı → getiri hesabından HARİÇ tutulur.
_LONG_LIKE = (ACTION_INCREASE,)
_SHORT_LIKE = (ACTION_REDUCE,)
_EVALUABLE_ACTIONS = _LONG_LIKE + _SHORT_LIKE

# Sonuç durumu (kapalı liste)
OUTCOME_EVALUATED = "EVALUATED"
OUTCOME_PENDING = "PENDING"
OUTCOME_UNKNOWN = "UNKNOWN"
_OUTCOME_STATUSES = (OUTCOME_EVALUATED, OUTCOME_PENDING, OUTCOME_UNKNOWN)

# Sağlık durumu (kapalı liste)
HEALTH_HEALTHY = "HEALTHY"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_CRITICAL = "CRITICAL"
HEALTH_UNKNOWN = "UNKNOWN"
HEALTH_STATUSES = (HEALTH_HEALTHY, HEALTH_DEGRADED, HEALTH_CRITICAL,
                   HEALTH_UNKNOWN)

# Sınırlama kodları (kapalı liste — tek tanım yeri burasıdır)
LIMIT_NO_OBSERVATIONS = "NO_OBSERVATIONS"
LIMIT_NO_EVALUATED_OUTCOMES = "NO_EVALUATED_OUTCOMES"
LIMIT_INSUFFICIENT_RETURN_DATA = "INSUFFICIENT_RETURN_DATA"
LIMIT_INSUFFICIENT_DRAWDOWN_DATA = "INSUFFICIENT_DRAWDOWN_DATA"
LIMIT_INSUFFICIENT_CONFIDENCE_DATA = "INSUFFICIENT_CONFIDENCE_DATA"
LIMIT_UNKNOWN_MARKET_REGIME = "UNKNOWN_MARKET_REGIME"
LIMIT_PARTIAL_DATA_QUALITY = "PARTIAL_DATA_QUALITY"
LIMITATION_CODES = (
    LIMIT_INSUFFICIENT_CONFIDENCE_DATA,
    LIMIT_INSUFFICIENT_DRAWDOWN_DATA,
    LIMIT_INSUFFICIENT_RETURN_DATA,
    LIMIT_NO_EVALUATED_OUTCOMES,
    LIMIT_NO_OBSERVATIONS,
    LIMIT_PARTIAL_DATA_QUALITY,
    LIMIT_UNKNOWN_MARKET_REGIME,
)

# Gözlem penceresi (v1 varsayılanı: tek anlık görüntü)
WINDOW_SNAPSHOT = "SNAPSHOT"

# Piyasa rejimi (v1: StrategyProposal'dan aynen taşınır; boş → UNKNOWN)
REGIME_UNKNOWN = "UNKNOWN"

# Sağlık eşiği sabitleri (Decimal — hesap fonksiyonlarında sihirli
# sayı YOKTUR; kesin değerler test dosyasında da kilitlenir).
SUCCESS_CRITICAL_PCT = Decimal("25")    # başarı oranı altı → CRITICAL
SUCCESS_DEGRADED_PCT = Decimal("50")    # başarı oranı altı → DEGRADED
DRAWDOWN_CRITICAL_PCT = Decimal("50")   # maksimum düşüş üstü → CRITICAL
DRAWDOWN_DEGRADED_PCT = Decimal("25")   # maksimum düşüş üstü → DEGRADED
CONFIDENCE_ACC_DEGRADED_PCT = Decimal("50")  # kalibrasyon altı → DEGRADED
COVERAGE_DEGRADED_PCT = Decimal("50")   # değerlendirme kapsamı altı → DEGRADED

_Q_PCT = Decimal("0.01")
_MAX_ABS = Decimal("1E+18")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")

# Rapor alan sırası (Agent 01 şeması — sabit)
REPORT_FIELDS = (
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


# ── Decimal yardımcıları ─────────────────────────────────────────────

def _dec(value: Any) -> Decimal | None:
    """Değeri Decimal'e çevirir; float YASAK; bilinmeyen → None.

    Zarf düzeyi tip ihlalleri (float/bool) sterile hata üretir.
    """
    if value is None:
        return None
    if isinstance(value, float):
        raise ValueError(ERROR_FLOAT_REJECTED)
    if isinstance(value, bool):
        raise ValueError(ERROR_INVALID_INPUT)
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, int):
        candidate = Decimal(value)
    elif isinstance(value, str):
        try:
            candidate = Decimal(value.strip())
        except (InvalidOperation, AttributeError):
            return None  # bozuk string: gözlem güvenle değerlendirilmez
    else:
        return None
    if not candidate.is_finite():
        raise ValueError(ERROR_INVALID_INPUT)
    if abs(candidate) >= _MAX_ABS:
        return None
    return candidate


def _pct(value: Decimal | None) -> str | None:
    """Kanonik sabit-nokta string (2 hane); bilinmeyen → None."""
    if value is None:
        return None
    return format(value.quantize(_Q_PCT), "f")


# ── Girdi doğrulama ──────────────────────────────────────────────────

def _validate_envelope(observation_input: Any) -> dict:
    if not isinstance(observation_input, dict):
        raise ValueError(ERROR_INVALID_INPUT)
    if observation_input.get(
            "strategy_version") != SUPPORTED_STRATEGY_VERSION:
        raise ValueError(ERROR_INVALID_INPUT)
    if observation_input.get(
            "analysis_version") != SUPPORTED_ANALYSIS_VERSION:
        raise ValueError(ERROR_INVALID_INPUT)
    recs = observation_input.get("recommendations")
    if recs is None:
        recs = []
    if not isinstance(recs, (list, tuple)):
        raise ValueError(ERROR_INVALID_INPUT)
    for rec in recs:
        if not isinstance(rec, dict):
            raise ValueError(ERROR_INVALID_INPUT)
    quality = observation_input.get("data_quality")
    if quality is not None and quality not in _DATA_QUALITIES:
        raise ValueError(ERROR_INVALID_INPUT)
    return observation_input


def _window(observation_input: dict) -> dict:
    raw = observation_input.get("observation_window")
    if raw is None:
        return {"kind": WINDOW_SNAPSHOT, "samples": None}
    if not isinstance(raw, dict) or not isinstance(raw.get("kind"), str):
        raise ValueError(ERROR_INVALID_INPUT)
    samples = raw.get("samples")
    if samples is not None and (
            isinstance(samples, bool) or not isinstance(samples, int)
            or samples < 0):
        raise ValueError(ERROR_INVALID_INPUT)
    return {"kind": raw["kind"], "samples": samples}


# ── Gözlem değerlendirme ─────────────────────────────────────────────

def _evaluate_observation(rec: dict) -> dict:
    """Tek gözlemi güvenle sınıflandırır — asla raporu çökertmez.

    Dönen sözlük: evaluated (bool), return_pct (Decimal|None),
    success (bool|None), drawdown_pct (Decimal|None),
    calibration (Decimal|None), quality (str|None).
    """
    result: dict[str, Any] = {
        "evaluated": False, "return_pct": None, "success": None,
        "drawdown_pct": None, "calibration": None,
        "quality": rec.get("data_quality")
        if rec.get("data_quality") in _DATA_QUALITIES else None,
    }
    action = rec.get("action")
    outcome_status = rec.get("outcome_status")
    entry = _dec(rec.get("entry_value"))
    observed = _dec(rec.get("observed_value"))

    # Düşüş: yalnız SAĞLANAN peak/trough ile — tarih yeniden kurulmaz.
    peak = _dec(rec.get("peak_value"))
    trough = _dec(rec.get("trough_value"))
    if (peak is not None and trough is not None and peak > _ZERO
            and _ZERO <= trough <= peak):
        result["drawdown_pct"] = (peak - trough) / peak * _HUNDRED

    # Getiri: yalnız yönlü eylem + EVALUATED sonuç + geçerli değerler.
    if (action in _EVALUABLE_ACTIONS
            and outcome_status == OUTCOME_EVALUATED
            and entry is not None and entry > _ZERO
            and observed is not None):
        raw_return = (observed - entry) / entry
        if action in _SHORT_LIKE:
            raw_return = -raw_return
        result["evaluated"] = True
        result["return_pct"] = raw_return * _HUNDRED
        result["success"] = raw_return > _ZERO  # 0 = nötr, başarı DEĞİL

        confidence = _dec(rec.get("confidence"))
        if confidence is not None and _ZERO <= confidence <= _HUNDRED:
            actual = _ONE if result["success"] else _ZERO
            result["calibration"] = (
                _ONE - abs(confidence / _HUNDRED - actual)) * _HUNDRED
    return result


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, _ZERO) / Decimal(len(values))


def _data_quality(observation_input: dict, evaluations: list[dict]) -> str:
    explicit = observation_input.get("data_quality")
    if explicit in _DATA_QUALITIES:
        return explicit
    if not evaluations:
        return DATA_QUALITY_UNAVAILABLE
    qualities = [e["quality"] for e in evaluations]
    if any(q != DATA_QUALITY_OK for q in qualities):
        return DATA_QUALITY_PARTIAL
    return DATA_QUALITY_OK


# ── Sağlık sınıflandırması ───────────────────────────────────────────

def _health_status(quality: str, recommendation_count: int,
                   evaluated_count: int, success_rate: Decimal | None,
                   max_drawdown: Decimal | None,
                   confidence_accuracy: Decimal | None) -> str:
    """Sabit sırada değerlendirilir; ağır durum önceliklidir.

    1) Kanıt yetersizliği → UNKNOWN (bilinmeyen veri SAĞLIKLI sayılmaz)
    2) CRITICAL eşikleri (başarı/düşüş)
    3) DEGRADED eşikleri (başarı/düşüş/kalibrasyon/kapsam/kalite)
    4) HEALTHY
    Null metrik ilgili kuralı sessizce atlar.
    """
    if (quality == DATA_QUALITY_UNAVAILABLE or recommendation_count == 0
            or evaluated_count == 0):
        return HEALTH_UNKNOWN
    if success_rate is not None and success_rate < SUCCESS_CRITICAL_PCT:
        return HEALTH_CRITICAL
    if max_drawdown is not None and max_drawdown > DRAWDOWN_CRITICAL_PCT:
        return HEALTH_CRITICAL
    if success_rate is not None and success_rate < SUCCESS_DEGRADED_PCT:
        return HEALTH_DEGRADED
    if max_drawdown is not None and max_drawdown > DRAWDOWN_DEGRADED_PCT:
        return HEALTH_DEGRADED
    if (confidence_accuracy is not None
            and confidence_accuracy < CONFIDENCE_ACC_DEGRADED_PCT):
        return HEALTH_DEGRADED
    coverage = (Decimal(evaluated_count) / Decimal(recommendation_count)
                * _HUNDRED)
    if coverage < COVERAGE_DEGRADED_PCT:
        return HEALTH_DEGRADED
    if quality == DATA_QUALITY_PARTIAL:
        return HEALTH_DEGRADED
    return HEALTH_HEALTHY


# ── Rapor kurucu ─────────────────────────────────────────────────────

def build_monitoring_report(observation_input: Any) -> dict:
    """MonitoringObservationInput → immutable MonitoringReport.

    Saf ve deterministik: saat/UUID/I-O yok; ``observed_at`` ve
    ``report_id`` null döner (API sınırında doldurulur); ``alerts``
    boş tuple kalır (Agent 03 sahipliği). Girdi mutasyona uğratılmaz.
    """
    envelope = _validate_envelope(observation_input)
    window = _window(envelope)
    recs = list(envelope.get("recommendations") or [])

    evaluations = [_evaluate_observation(rec) for rec in recs]
    recommendation_count = len(evaluations)
    evaluated = [e for e in evaluations if e["evaluated"]]
    evaluated_count = len(evaluated)

    returns = [e["return_pct"] for e in evaluated
               if e["return_pct"] is not None]
    successes = [e for e in evaluated if e["success"] is True]
    drawdowns = [e["drawdown_pct"] for e in evaluations
                 if e["drawdown_pct"] is not None]
    calibrations = [e["calibration"] for e in evaluated
                    if e["calibration"] is not None]

    success_rate = (Decimal(len(successes)) / Decimal(evaluated_count)
                    * _HUNDRED) if evaluated_count else None
    average_return = _mean(returns)
    maximum_drawdown = max(drawdowns) if drawdowns else None
    confidence_accuracy = _mean(calibrations)

    quality = _data_quality(envelope, evaluations)

    regime = envelope.get("market_regime")
    market_regime = regime if isinstance(regime, str) and regime else \
        REGIME_UNKNOWN  # dürüst bilinmezlik

    limitations = set()
    if recommendation_count == 0:
        limitations.add(LIMIT_NO_OBSERVATIONS)
    if evaluated_count == 0:
        limitations.add(LIMIT_NO_EVALUATED_OUTCOMES)
    if recommendation_count and len(returns) < recommendation_count:
        limitations.add(LIMIT_INSUFFICIENT_RETURN_DATA)
    if recommendation_count and len(drawdowns) < recommendation_count:
        limitations.add(LIMIT_INSUFFICIENT_DRAWDOWN_DATA)
    if recommendation_count and len(calibrations) < recommendation_count:
        limitations.add(LIMIT_INSUFFICIENT_CONFIDENCE_DATA)
    if market_regime == REGIME_UNKNOWN:
        limitations.add(LIMIT_UNKNOWN_MARKET_REGIME)
    if quality == DATA_QUALITY_PARTIAL:
        # UNAVAILABLE ayrı temsil edilir (NO_OBSERVATIONS /
        # NO_EVALUATED_OUTCOMES + health UNKNOWN); PARTIAL kodu yalnız
        # gerçekten KISMİ kalite için düşülür.
        limitations.add(LIMIT_PARTIAL_DATA_QUALITY)

    health = _health_status(quality, recommendation_count,
                            evaluated_count, success_rate,
                            maximum_drawdown, confidence_accuracy)

    return MappingProxyType({
        "monitoring_version": MONITORING_VERSION,
        "report_id": None,      # yalnız API sınırında üretilir
        "observed_at": None,    # yalnız API sınırında üretilir
        "strategy_version": SUPPORTED_STRATEGY_VERSION,
        "analysis_version": SUPPORTED_ANALYSIS_VERSION,
        "observation_window": MappingProxyType(window),
        "data_quality": quality,
        "recommendation_count": recommendation_count,
        "evaluated_count": evaluated_count,
        "success_rate": _pct(success_rate),
        "average_return": _pct(average_return),
        "maximum_drawdown": _pct(maximum_drawdown),
        "confidence_accuracy": _pct(confidence_accuracy),
        "market_regime": market_regime,
        "health_status": health,
        "alerts": (),           # Agent 02: daima boş immutable koleksiyon
        "limitations": tuple(sorted(limitations)),
    })
