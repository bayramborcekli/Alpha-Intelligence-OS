"""Mission 1800 — Strategy Intelligence çekirdeği (Agent 02).

PortfolioAnalysis (Mission 1700) zarfını tüketip YALNIZ tavsiye
niteliğinde, açıklanabilir, deterministik StrategyProposal üretir.

Sözleşmeler (Agent 01 mimarisi — değiştirilemez):
- ADVISORY-ONLY: emir/yürütme/exchange kavramı YOKTUR; şemada emir
  tipi, miktar veya fiyat alanı bulunamaz (ağırlık hedefleri yüzdedir).
- Saf hesap: I/O yok, saat yok, rastgelelik yok, global durum yok.
  ``proposal_id`` ve ``generated_at`` bu katmanda ÜRETİLMEZ (API sınırı).
- Para/yüzde matematiği yalnız Decimal; float girdisi REDDEDİLİR.
- Bilinmeyen değer null kalır; kural girdisi null ise kural SESSİZCE
  atlanır ve limitations'a kod düşülür — asla 0 varsayılmaz.
- Kod listeleri kapalıdır (sterile): serbest metin taşınmaz.
- Aynı PortfolioAnalysis → bayt-özdeş çıktı (kararlı sıralama,
  sabit kural değerlendirme sırası).

Hata modeli: bozuk girdi sterile ``ValueError`` kodu üretir
(FLOAT_REJECTED / INVALID_INPUT); exception metni veri taşımaz.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

STRATEGY_VERSION = 1
SUPPORTED_ANALYSIS_VERSION = 1

# Sterile hata kodları
ERROR_FLOAT_REJECTED = "FLOAT_REJECTED"
ERROR_INVALID_INPUT = "INVALID_INPUT"

# Veri kalitesi (girdi status'u aynen taşınır)
_DATA_QUALITIES = ("OK", "PARTIAL", "UNAVAILABLE")

# Eylemler (kapalı liste — yürütme semantiği YOK)
ACTION_REDUCE = "REDUCE"
ACTION_INCREASE = "INCREASE"
ACTION_HOLD = "HOLD"
ACTION_REBALANCE = "REBALANCE"
ACTION_DIVERSIFY = "DIVERSIFY"

# Neden / uyarı / sınırlama kodları (kapalı listeler)
REASON_CONCENTRATION_HIGH = "CONCENTRATION_HIGH"
REASON_DIVERSIFICATION_LOW = "DIVERSIFICATION_LOW"
REASON_EXCESS_CASH = "EXCESS_CASH"
REASON_RISK_LIMIT_NEAR = "RISK_LIMIT_NEAR"
REASON_RISK_LIMIT_BREACHED = "RISK_LIMIT_BREACHED"
REASON_UNDER_ALLOCATED = "UNDER_ALLOCATED"
REASON_OVER_ALLOCATED = "OVER_ALLOCATED"
REASON_LOW_DATA_QUALITY = "LOW_DATA_QUALITY"

WARNING_LOW_DATA_QUALITY = "LOW_DATA_QUALITY"
WARNING_RISK_LIMIT_BREACHED = "RISK_LIMIT_BREACHED"
WARNING_ANALYSIS_UNAVAILABLE = "ANALYSIS_UNAVAILABLE"

LIMIT_MARKET_REGIME_UNKNOWN = "MARKET_REGIME_UNKNOWN"
LIMIT_NO_FORECAST = "NO_FORECAST"
LIMIT_ALLOCATION_UNKNOWN = "ALLOCATION_UNKNOWN"
LIMIT_EXPOSURE_UNKNOWN = "EXPOSURE_UNKNOWN"
LIMIT_CONCENTRATION_UNKNOWN = "CONCENTRATION_UNKNOWN"
LIMIT_DIVERSIFICATION_UNKNOWN = "DIVERSIFICATION_UNKNOWN"
LIMIT_RISK_UTILIZATION_UNKNOWN = "RISK_UTILIZATION_UNKNOWN"

# Geçersizleşme koşulu kodları (kapalı liste)
INVALIDATE_ALLOCATION_CHANGED = "ALLOCATION_CHANGED"
INVALIDATE_CONCENTRATION_REDUCED = "CONCENTRATION_REDUCED"
INVALIDATE_EXPOSURE_CHANGED = "EXPOSURE_CHANGED"
INVALIDATE_RISK_UTILIZATION_CHANGED = "RISK_UTILIZATION_CHANGED"
INVALIDATE_DATA_QUALITY_IMPROVED = "DATA_QUALITY_IMPROVED"

# Portföy-geneli önerilerde enstrüman
PORTFOLIO_INSTRUMENT = "PORTFOLIO"

# Deterministik eşikler (Decimal, sabit — Risk Engine eşikleri DEĞİL;
# tavsiye kalibrasyonudur, limit otoritesi Risk Engine'de kalır)
CASH_EXCESS_PCT = Decimal("60")        # nakit ağırlığı üstü → EXCESS_CASH
CASH_TARGET_PCT = Decimal("30")        # nakit hedef ağırlığı
UNDER_ALLOC_GROSS_PCT = Decimal("20")  # brüt maruziyet altı → UNDER_ALLOCATED
OVER_ALLOC_GROSS_PCT = Decimal("100")  # brüt maruziyet üstü → OVER_ALLOCATED
TOP_SHARE_HIGH_PCT = Decimal("50")     # tek sembol payı üstü → CONCENTRATION
EFFECTIVE_POS_LOW = Decimal("3")       # etkin pozisyon altı → DIVERSIFICATION
UTIL_NEAR_PCT = Decimal("80")          # limit kullanımına yaklaşma
UTIL_BREACH_PCT = Decimal("100")       # limit aşımı

# Güven kalibrasyonu (sabit)
_CONF_BASE = {
    REASON_RISK_LIMIT_BREACHED: Decimal("90"),
    REASON_RISK_LIMIT_NEAR: Decimal("80"),
    REASON_CONCENTRATION_HIGH: Decimal("85"),
    REASON_OVER_ALLOCATED: Decimal("80"),
    REASON_EXCESS_CASH: Decimal("70"),
    REASON_UNDER_ALLOCATED: Decimal("65"),
    REASON_DIVERSIFICATION_LOW: Decimal("70"),
}
_CONF_PARTIAL_PENALTY = Decimal("20")
_CONF_OVERALL_BASE = Decimal("80")

_Q_PCT = Decimal("0.01")
_MAX_ABS = Decimal("1E+18")


# ── Decimal yardımcıları ─────────────────────────────────────────────

def _dec(value: Any) -> Decimal | None:
    """Zarf değerini Decimal'e çevirir; float YASAK; bilinmeyen → None."""
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
            raise ValueError(ERROR_INVALID_INPUT)
    else:
        raise ValueError(ERROR_INVALID_INPUT)
    if not candidate.is_finite() or abs(candidate) >= _MAX_ABS:
        raise ValueError(ERROR_INVALID_INPUT)
    return candidate


def _pct(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(_Q_PCT), "f")


# ── Girdi doğrulama / okuma ──────────────────────────────────────────

def _section(portfolio: dict, name: str) -> dict:
    sec = portfolio.get(name)
    return sec if isinstance(sec, dict) else {}


def _validate_analysis(analysis: Any) -> dict:
    if not isinstance(analysis, dict):
        raise ValueError(ERROR_INVALID_INPUT)
    if analysis.get("analysis_version") != SUPPORTED_ANALYSIS_VERSION:
        raise ValueError(ERROR_INVALID_INPUT)
    if analysis.get("status") not in _DATA_QUALITIES:
        raise ValueError(ERROR_INVALID_INPUT)
    portfolio = analysis.get("portfolio")
    if not isinstance(portfolio, dict):
        raise ValueError(ERROR_INVALID_INPUT)
    return portfolio


# ── Öneri kurucu ─────────────────────────────────────────────────────

def _confidence(reason: str, data_quality: str) -> Decimal:
    base = _CONF_BASE[reason]
    if data_quality == "PARTIAL":
        base = base - _CONF_PARTIAL_PENALTY
    return base if base > 0 else Decimal("0")


def _rec(reason: str, data_quality: str, *, instrument: str, action: str,
         priority: int, risk_level: str,
         current_weight: Decimal | None, target_weight: Decimal | None,
         effect_metric: str, effect_direction: str,
         effect_magnitude: Decimal | None,
         invalidation: list[str], extra_reasons: list[str] | None = None
         ) -> dict[str, Any]:
    reasons = [reason] + sorted(extra_reasons or [])
    return {
        "recommendation_id": None,  # sıralama sonrası atanır
        "instrument": instrument,
        "action": action,
        "reason_codes": reasons,
        "priority": priority,
        "confidence": _pct(_confidence(reason, data_quality)),
        "current_weight": _pct(current_weight),
        "target_weight": _pct(target_weight),
        "risk_level": risk_level,
        "expected_effect": {
            "metric": effect_metric,
            "direction": effect_direction,
            "magnitude_pct": _pct(effect_magnitude),
        },
        "invalidation_conditions": sorted(invalidation),
    }


# ── Kurallar (SABİT değerlendirme sırası) ────────────────────────────

def _rule_risk_limits(util: dict, data_quality: str,
                      limitations: set, warnings: set) -> list[dict]:
    """1) Limit aşımı → REDUCE(1); 2) yaklaşma → HOLD(2)."""
    utils = {}
    for key in ("net_exposure_util_pct", "drawdown_util_pct",
                "concentration_util_pct"):
        utils[key] = _dec(util.get(key))
    breached = util.get("limits_breached")
    breached = breached if isinstance(breached, list) else []
    known = {k: v for k, v in utils.items() if v is not None}
    if not known and not breached:
        limitations.add(LIMIT_RISK_UTILIZATION_UNKNOWN)
        return []
    out = []
    over = sorted(k for k, v in known.items() if v > UTIL_BREACH_PCT)
    near = sorted(k for k, v in known.items()
                  if UTIL_NEAR_PCT < v <= UTIL_BREACH_PCT)
    if breached or over:
        warnings.add(WARNING_RISK_LIMIT_BREACHED)
        worst = max(known.values()) if known else None
        out.append(_rec(
            REASON_RISK_LIMIT_BREACHED, data_quality,
            instrument=PORTFOLIO_INSTRUMENT, action=ACTION_REDUCE,
            priority=1, risk_level="HIGH",
            current_weight=worst, target_weight=UTIL_BREACH_PCT,
            effect_metric="RISK_LIMIT_UTILIZATION",
            effect_direction="DECREASE",
            effect_magnitude=(worst - UTIL_BREACH_PCT) if worst is not None
            and worst > UTIL_BREACH_PCT else None,
            invalidation=[INVALIDATE_RISK_UTILIZATION_CHANGED]))
    elif near:
        worst = max(known[k] for k in near)
        out.append(_rec(
            REASON_RISK_LIMIT_NEAR, data_quality,
            instrument=PORTFOLIO_INSTRUMENT, action=ACTION_HOLD,
            priority=2, risk_level="MODERATE",
            current_weight=worst, target_weight=UTIL_NEAR_PCT,
            effect_metric="RISK_LIMIT_UTILIZATION",
            effect_direction="DECREASE",
            effect_magnitude=worst - UTIL_NEAR_PCT,
            invalidation=[INVALIDATE_RISK_UTILIZATION_CHANGED]))
    return out


def _rule_concentration(conc: dict, data_quality: str,
                        limitations: set) -> list[dict]:
    top_share = _dec(conc.get("top_share_pct"))
    top_symbol = conc.get("top_symbol")
    if top_share is None or not isinstance(top_symbol, str) \
            or not top_symbol:
        limitations.add(LIMIT_CONCENTRATION_UNKNOWN)
        return []
    if top_share <= TOP_SHARE_HIGH_PCT:
        return []
    return [_rec(
        REASON_CONCENTRATION_HIGH, data_quality,
        instrument=top_symbol, action=ACTION_REDUCE,
        priority=2, risk_level="HIGH",
        current_weight=top_share, target_weight=TOP_SHARE_HIGH_PCT,
        effect_metric="TOP_SHARE_PCT", effect_direction="DECREASE",
        effect_magnitude=top_share - TOP_SHARE_HIGH_PCT,
        invalidation=[INVALIDATE_CONCENTRATION_REDUCED])]


def _rule_diversification(conc: dict, allocation: dict, data_quality: str,
                          limitations: set) -> list[dict]:
    effective = _dec(conc.get("effective_positions"))
    assets = allocation.get("assets")
    if effective is None:
        limitations.add(LIMIT_DIVERSIFICATION_UNKNOWN)
        return []
    if not isinstance(assets, list) or not assets:
        return []  # pozisyon yokken çeşitlendirme önerisi anlamsız
    if effective >= EFFECTIVE_POS_LOW:
        return []
    return [_rec(
        REASON_DIVERSIFICATION_LOW, data_quality,
        instrument=PORTFOLIO_INSTRUMENT, action=ACTION_DIVERSIFY,
        priority=3, risk_level="MODERATE",
        current_weight=None, target_weight=None,
        effect_metric="EFFECTIVE_POSITIONS", effect_direction="INCREASE",
        effect_magnitude=None,
        invalidation=[INVALIDATE_ALLOCATION_CHANGED])]


def _rule_allocation(allocation: dict, exposure: dict, data_quality: str,
                     limitations: set) -> list[dict]:
    cash = _dec(allocation.get("cash_weight_pct"))
    gross_pct = _dec(exposure.get("gross_pct"))
    out = []
    if cash is None:
        limitations.add(LIMIT_ALLOCATION_UNKNOWN)
    elif cash > CASH_EXCESS_PCT:
        out.append(_rec(
            REASON_EXCESS_CASH, data_quality,
            instrument=PORTFOLIO_INSTRUMENT, action=ACTION_REBALANCE,
            priority=4, risk_level="LOW",
            current_weight=cash, target_weight=CASH_TARGET_PCT,
            effect_metric="CASH_WEIGHT_PCT", effect_direction="DECREASE",
            effect_magnitude=cash - CASH_TARGET_PCT,
            invalidation=[INVALIDATE_ALLOCATION_CHANGED]))
    if gross_pct is None:
        limitations.add(LIMIT_EXPOSURE_UNKNOWN)
    elif gross_pct > OVER_ALLOC_GROSS_PCT:
        out.append(_rec(
            REASON_OVER_ALLOCATED, data_quality,
            instrument=PORTFOLIO_INSTRUMENT, action=ACTION_REDUCE,
            priority=2, risk_level="HIGH",
            current_weight=gross_pct, target_weight=OVER_ALLOC_GROSS_PCT,
            effect_metric="GROSS_EXPOSURE_PCT", effect_direction="DECREASE",
            effect_magnitude=gross_pct - OVER_ALLOC_GROSS_PCT,
            invalidation=[INVALIDATE_EXPOSURE_CHANGED]))
    elif gross_pct < UNDER_ALLOC_GROSS_PCT and cash is not None \
            and cash > CASH_EXCESS_PCT:
        out.append(_rec(
            REASON_UNDER_ALLOCATED, data_quality,
            instrument=PORTFOLIO_INSTRUMENT, action=ACTION_INCREASE,
            priority=4, risk_level="LOW",
            current_weight=gross_pct, target_weight=UNDER_ALLOC_GROSS_PCT,
            effect_metric="GROSS_EXPOSURE_PCT", effect_direction="INCREASE",
            effect_magnitude=UNDER_ALLOC_GROSS_PCT - gross_pct,
            invalidation=[INVALIDATE_EXPOSURE_CHANGED]))
    return out


# ── Genel değerlendirme ──────────────────────────────────────────────

def _overall_risk(recs: list[dict], util: dict) -> str | None:
    breached = util.get("limits_breached")
    if isinstance(breached, list) and breached:
        return "CRITICAL"
    levels = {r["risk_level"] for r in recs}
    utils = [
        _dec(util.get(k)) for k in
        ("net_exposure_util_pct", "drawdown_util_pct",
         "concentration_util_pct")]
    known = [u for u in utils if u is not None]
    if any(u > UTIL_BREACH_PCT for u in known):
        return "CRITICAL"
    if "HIGH" in levels or any(u > UTIL_NEAR_PCT for u in known):
        return "HIGH"
    if not known and not recs:
        return None  # değerlendirme temeli yok — bilinmezlik korunur
    if "MODERATE" in levels:
        return "MODERATE"
    return "LOW"


def _overall_confidence(recs: list[dict], data_quality: str
                        ) -> Decimal | None:
    if data_quality == "UNAVAILABLE":
        return None
    base = _CONF_OVERALL_BASE
    if data_quality == "PARTIAL":
        base = base - _CONF_PARTIAL_PENALTY
    if recs:
        total = sum(Decimal(r["confidence"]) for r in recs)
        rec_avg = total / Decimal(len(recs))
        base = (base + rec_avg) / Decimal("2")
    return base


# ── Kamu API ─────────────────────────────────────────────────────────

def build_strategy(portfolio_analysis: Any) -> dict[str, Any]:
    """PortfolioAnalysis → StrategyProposal (deterministik, advisory-only).

    ``proposal_id`` ve ``generated_at`` içermez — bunlar YALNIZ API
    kompozisyon sınırında eklenir (Agent 01 mimarisi §3).
    """
    portfolio = _validate_analysis(portfolio_analysis)
    data_quality = portfolio_analysis["status"]

    warnings: set[str] = set()
    limitations: set[str] = {LIMIT_MARKET_REGIME_UNKNOWN,
                             LIMIT_NO_FORECAST}
    recs: list[dict] = []

    util = _section(portfolio, "risk_utilization")
    if data_quality == "UNAVAILABLE":
        # Analiz temeli yok: öneri ÜRETİLMEZ; dürüst bilinmezlik.
        warnings.add(WARNING_ANALYSIS_UNAVAILABLE)
        warnings.add(WARNING_LOW_DATA_QUALITY)
        limitations.update((LIMIT_ALLOCATION_UNKNOWN,
                            LIMIT_EXPOSURE_UNKNOWN,
                            LIMIT_CONCENTRATION_UNKNOWN,
                            LIMIT_DIVERSIFICATION_UNKNOWN,
                            LIMIT_RISK_UTILIZATION_UNKNOWN))
    else:
        if data_quality == "PARTIAL":
            warnings.add(WARNING_LOW_DATA_QUALITY)
        # SABİT kural sırası (Agent 01 §4): risk → yoğunlaşma →
        # çeşitlendirme → tahsis/maruziyet
        recs.extend(_rule_risk_limits(util, data_quality,
                                      limitations, warnings))
        recs.extend(_rule_concentration(
            _section(portfolio, "concentration"), data_quality,
            limitations))
        recs.extend(_rule_diversification(
            _section(portfolio, "concentration"),
            _section(portfolio, "allocation"), data_quality, limitations))
        recs.extend(_rule_allocation(
            _section(portfolio, "allocation"),
            _section(portfolio, "exposure"), data_quality, limitations))

    # Kararlı nihai sıralama: öncelik → enstrüman → ilk neden kodu
    recs.sort(key=lambda r: (r["priority"], r["instrument"],
                             r["reason_codes"][0]))
    for i, rec in enumerate(recs, start=1):
        rec["recommendation_id"] = f"R{i}"

    return {
        "strategy_version": STRATEGY_VERSION,
        "advisory_only": True,
        "read_only": True,
        "portfolio_analysis_version": SUPPORTED_ANALYSIS_VERSION,
        "confidence": _pct(_overall_confidence(recs, data_quality)),
        "data_quality": data_quality,
        "market_regime": "UNKNOWN",  # v1: rejim tespiti yok (sınırlama)
        "overall_risk": _overall_risk(recs, util)
        if data_quality != "UNAVAILABLE" else None,
        "recommendations": recs,
        "warnings": sorted(warnings),
        "limitations": sorted(limitations),
    }
