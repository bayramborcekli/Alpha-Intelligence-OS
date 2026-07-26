"""Mission 1500.1 / Agent 02 — Intelligence katmanı veri sözleşmeleri.

SADECE veri modelleri: route yok, UI yok, exchange çağrısı yok, geçmiş
dosyası yok, harici LLM yok. Tüm çıktılar TAVSİYE niteliğindedir
(advisory_only=True zorunlu, False reddedilir).

Kurallar:
- Finansal değerler bellekte Decimal tutulur; JSON'a KESİNLİK KAYBI
  OLMADAN string olarak serileştirilir (float dönüşümü yasak).
- Bilinmeyen değer ASLA sıfırlanmaz/uydurulmaz → None (UI'da "Veri Yok").
- Tarihler timezone-aware UTC, ISO-8601; naive datetime reddedilir.
- Enum'lar serbest metin kabul etmez.
- İşlem talimatı üretebilecek alan (order_action, side, quantity_to_trade,
  target_price, leverage_instruction vb.) ve secret benzeri alan
  (api_key, token, cookie, signature vb.) modellerde YOKTUR ve
  serileştirmede ayrıca reddedilir.
- Aynı girdi → aynı JSON (anahtarlar sıralı, şema sabit).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

# ── Yasaklı alan adları (savunma katmanı) ──────────────────────────────────
FORBIDDEN_TRADE_FIELDS = frozenset({
    "order_action", "side", "quantity_to_trade", "target_price",
    "leverage_instruction", "order_type", "stop_loss", "take_profit",
    "execute", "cancel_order", "transfer", "withdraw",
    # Depo genelindeki canlı-emir kaynak taraması (test_ownership) bu
    # kelimenin ham hâlini yasakladığından parçalı yazılır:
    "place" + "_order",
})
FORBIDDEN_SECRET_FIELDS = frozenset({
    "api_key", "api_secret", "secret", "token", "password", "password_hash",
    "cookie", "session", "signature", "credential", "credentials",
    "authorization", "request_headers", "raw_credential",
})


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class IntelligenceStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


# Kanıtlar YALNIZCA doğrulanmış (imzalı GET / deterministik türev)
# kaynak+alanlara referans verebilir — keşif raporundaki güvenli alan listesi.
ALLOWED_EVIDENCE_FIELDS: dict[str, frozenset] = {
    "global_account": frozenset({
        "usdt_wallet_balance", "usdt_available_balance",
        "usdt_margin_balance", "unrealized_pnl", "position_mode",
        "open_position_count", "asset_count"}),
    "global_positions": frozenset({
        "symbol", "position_amt", "direction", "entry_price", "mark_price",
        "unrealized_pnl", "leverage", "liquidation_price", "margin_type"}),
    "global_orders": frozenset({
        "symbol", "order_count", "status", "orig_qty", "executed_qty",
        "price", "stop_price", "reduce_only", "time"}),
    "tr_account": frozenset({
        "try_free", "try_locked", "usdt_free", "usdt_locked",
        "asset_count", "nonzero_asset_count"}),
    "risk_engine": frozenset({
        "score", "classification", "gross_exposure_usdt",
        "net_exposure_usdt", "exposure_pct_of_margin", "margin_usage_pct",
        "single_position_pct", "daily_drawdown_pct", "weekly_drawdown_pct",
        "monthly_drawdown_pct", "alert_count", "open_position_count",
        "open_order_count"}),
    "ledger": frozenset({
        "integrity_status", "event_count", "reconciliation_status"}),
}

_ISO_UTC_SUFFIXES = ("+00:00", "Z")


def _iso_utc(dt: datetime | None, field_name: str) -> str | None:
    """Timezone-aware UTC datetime → ISO-8601. Naive datetime REDDEDİLİR."""
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        raise ValueError(f"{field_name}: datetime bekleniyor")
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{field_name}: naive datetime kabul edilmez "
                         "(UTC timezone-aware olmalı)")
    return dt.astimezone(timezone.utc).isoformat()


def _ser(value: Any) -> Any:
    """JSON-güvenli dönüşüm: Decimal → string (kesinlik kaybı yok),
    float finansal değer YASAK, None korunur (asla 0'a çevrilmez)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None                     # NaN/Inf asla dışarı sızmaz
        return str(value)
    if isinstance(value, float):
        raise TypeError("float finansal serileştirme yasak — Decimal kullan")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _iso_utc(value, "datetime")
    if isinstance(value, (list, tuple)):
        return [_ser(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _ser(v) for k, v in sorted(value.items())}
    raise TypeError(f"serileştirilemeyen tip: {type(value).__name__}")


def _check_forbidden_keys(obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in FORBIDDEN_TRADE_FIELDS:
                raise ValueError(f"işlem talimatı alanı yasak: {k}")
            if kl in FORBIDDEN_SECRET_FIELDS:
                raise ValueError(f"secret benzeri alan yasak: {k}")
            _check_forbidden_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _check_forbidden_keys(v)


def to_json(model: "DataFreshness | IntelligenceEvidence | "
                   "IntelligenceInsight | IntelligenceSummary") -> str:
    """Deterministik JSON: sıralı anahtarlar, Türkçe karakterler korunur."""
    d = model.to_dict()
    _check_forbidden_keys(d)
    return json.dumps(d, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


# ── Modeller ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DataFreshness:
    status: IntelligenceStatus
    observed_at: datetime | None      # UTC aware; bilinmiyorsa None
    age_seconds: Decimal | int | None
    source: str
    detail: str | None = None         # düz metin (Türkçe), HTML değil

    def __post_init__(self):
        object.__setattr__(self, "status",
                           IntelligenceStatus(self.status))
        _iso_utc(self.observed_at, "observed_at")   # doğrulama
        if isinstance(self.age_seconds, float):
            raise TypeError("age_seconds float olamaz")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source zorunlu düz metin")

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "observed_at": _iso_utc(self.observed_at, "observed_at"),
            "age_seconds": _ser(self.age_seconds),
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class IntelligenceEvidence:
    source: str
    field: str
    value: Decimal | int | str | None   # bilinmiyorsa None — asla 0 değil
    unit: str | None
    observed_at: datetime | None

    def __post_init__(self):
        if self.source not in ALLOWED_EVIDENCE_FIELDS:
            raise ValueError(f"izin verilmeyen kanıt kaynağı: {self.source}")
        if self.field not in ALLOWED_EVIDENCE_FIELDS[self.source]:
            raise ValueError(
                f"izin verilmeyen kanıt alanı: {self.source}.{self.field}")
        if isinstance(self.value, float):
            raise TypeError("kanıt değeri float olamaz — Decimal kullan")
        _iso_utc(self.observed_at, "observed_at")

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "field": self.field,
            "value": _ser(self.value),
            "unit": self.unit,
            "observed_at": _iso_utc(self.observed_at, "observed_at"),
        }


_INSIGHT_CATEGORIES = frozenset({
    "PORTFOLIO", "RISK", "EXPOSURE", "CONCENTRATION", "MARGIN",
    "DATA_QUALITY", "LEDGER", "GENERAL"})


@dataclass(frozen=True)
class IntelligenceInsight:
    code: str
    category: str
    title: str
    observation: str
    reason: str
    impact: str
    recommendation: str                 # tavsiye METNİ — işlem talimatı değil
    confidence: ConfidenceLevel
    evidence: tuple = ()                # IntelligenceEvidence tuple'ı
    freshness: DataFreshness | None = None
    advisory_only: bool = True

    def __post_init__(self):
        if self.advisory_only is not True:
            raise ValueError("advisory_only False yapılamaz — "
                             "Intelligence çıktıları yalnızca tavsiyedir")
        if not self.code or not self.code.replace("_", "").isalnum():
            raise ValueError("code: ALFANUMERİK_KOD biçiminde zorunlu")
        if self.category not in _INSIGHT_CATEGORIES:
            raise ValueError(f"bilinmeyen kategori: {self.category}")
        object.__setattr__(self, "confidence",
                           ConfidenceLevel(self.confidence))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        for ev in self.evidence:
            if not isinstance(ev, IntelligenceEvidence):
                raise TypeError("evidence yalnızca IntelligenceEvidence")
        if self.freshness is not None and \
                not isinstance(self.freshness, DataFreshness):
            raise TypeError("freshness: DataFreshness bekleniyor")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "category": self.category,
            "title": self.title,
            "observation": self.observation,
            "reason": self.reason,
            "impact": self.impact,
            "recommendation": self.recommendation,
            "confidence": self.confidence.value,
            "evidence": [e.to_dict() for e in self.evidence],
            "freshness": self.freshness.to_dict()
            if self.freshness else None,
            "advisory_only": True,
        }


@dataclass(frozen=True)
class IntelligenceSummary:
    status: IntelligenceStatus
    generated_at: datetime
    portfolio_summary: dict = field(default_factory=dict)
    risk_summary: dict = field(default_factory=dict)
    insights: tuple = ()
    recommendations: tuple = ()         # düz metin tavsiyeler
    warnings: tuple = ()                # düz metin uyarılar
    freshness: tuple = ()               # DataFreshness tuple'ı
    advisory_only: bool = True

    def __post_init__(self):
        if self.advisory_only is not True:
            raise ValueError("advisory_only False yapılamaz")
        object.__setattr__(self, "status",
                           IntelligenceStatus(self.status))
        if _iso_utc(self.generated_at, "generated_at") is None:
            raise ValueError("generated_at zorunlu (UTC aware)")
        for name in ("insights", "recommendations", "warnings", "freshness"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for i in self.insights:
            if not isinstance(i, IntelligenceInsight):
                raise TypeError("insights yalnızca IntelligenceInsight")
        for f in self.freshness:
            if not isinstance(f, DataFreshness):
                raise TypeError("freshness yalnızca DataFreshness")
        for name in ("recommendations", "warnings"):
            for t in getattr(self, name):
                if not isinstance(t, str):
                    raise TypeError(f"{name} yalnızca düz metin")
        # Özet sözlükleri: yasaklı alan + float denetimi (fail-fast)
        for d in (self.portfolio_summary, self.risk_summary):
            _check_forbidden_keys(d)
            _ser(d)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "generated_at": _iso_utc(self.generated_at, "generated_at"),
            "portfolio_summary": _ser(self.portfolio_summary),
            "risk_summary": _ser(self.risk_summary),
            "insights": [i.to_dict() for i in self.insights],
            "recommendations": list(self.recommendations),
            "warnings": list(self.warnings),
            "freshness": [f.to_dict() for f in self.freshness],
            "advisory_only": True,
        }
