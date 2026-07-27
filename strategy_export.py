"""Mission 1800 / Agent 06 — Strategy Intelligence Export katmanı.

Salt-okunur serileştirme: veri YALNIZCA mevcut StrategyProposal
zarfından gelir (Agent 03 servis → Agent 02 çekirdek; meta Agent 04
API sınırından). Bu modül zarfı üretmez ve DEĞİŞTİRMEZ; hesap yapılmaz,
öneri üretilmez, dosya yazılmaz (yalnız bellek içi üretim), Exchange
erişimi yoktur, zaman damgası/UUID üretilmez (proposal_id ve
generated_at zarftan olduğu gibi taşınır; yoksa null kalır).

Çıktı sözleşmesi SABİTTİR: tam 13 üst alan + öneri başına tam 11 alan.
Fazla alan (ör. servis 'sources' meta verisi) dışa aktarılmaz; eksik
zorunlu alan uydurulmaz → sterile PROPOSAL_UNAVAILABLE. Null'lar ve
sabit-nokta Decimal string'leri aynen korunur; öneri sırası zarftaki
sıradır (yeniden sıralama YOK). JSON çıktısı aynı girdi için
bayt-özdeştir (sabit şema sırası, UTF-8).
"""

from __future__ import annotations

import json
from typing import Any

FORMATS = ("json",)

JSON_MIME = "application/json; charset=utf-8"
JSON_FILENAME = "strategy_intelligence.json"

CODE_INVALID_FORMAT = "INVALID_FORMAT"
CODE_PROPOSAL_UNAVAILABLE = "PROPOSAL_UNAVAILABLE"

# Sabit şema sırası — çıktı anahtar sırası bundan türetilir.
PROPOSAL_FIELDS = (
    "strategy_version",
    "proposal_id",
    "generated_at",
    "advisory_only",
    "read_only",
    "portfolio_analysis_version",
    "confidence",
    "data_quality",
    "market_regime",
    "overall_risk",
    "recommendations",
    "warnings",
    "limitations",
)

RECOMMENDATION_FIELDS = (
    "recommendation_id",
    "instrument",
    "action",
    "reason_codes",
    "priority",
    "confidence",
    "current_weight",
    "target_weight",
    "risk_level",
    "expected_effect",
    "invalidation_conditions",
)

# proposal_id/generated_at API sınırında eklenir; servis/çekirdek
# çıktısı doğrudan verilirse bu ikisi dürüstçe null taşınır.
_OPTIONAL_FIELDS = ("proposal_id", "generated_at")


class ExportError(ValueError):
    """Sterile export hatası — mesaj yalnız hata kodudur."""


# ── Projeksiyon (yalnız taşıma — hesap YOK) ──────────────────────────

def _clone(value: Any) -> Any:
    """Yalnız veri-taşıyıcı yapıları derin kopyalar (hesap YOK).

    Çıktı ile kaynak zarf arasında paylaşılan mutable referans
    kalmaz — export nesnesini değiştirmek kaynağı ASLA değiştiremez.
    """
    if isinstance(value, dict):
        return {k: _clone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clone(v) for v in value]
    return value


def _project_recommendation(rec: Any) -> dict[str, Any]:
    if not isinstance(rec, dict):
        raise ExportError(CODE_PROPOSAL_UNAVAILABLE)
    for field in RECOMMENDATION_FIELDS:
        if field not in rec:
            raise ExportError(CODE_PROPOSAL_UNAVAILABLE)
    return {field: _clone(rec[field]) for field in RECOMMENDATION_FIELDS}


def export_strategy_dict(proposal: Any) -> dict[str, Any]:
    """StrategyProposal → tam şemalı, sırası sabit yeni dict.

    Girdi mutasyona uğramaz; fazla alanlar (ör. ``sources``) dışa
    aktarılmaz; eksik zorunlu alan → sterile ``PROPOSAL_UNAVAILABLE``.
    """
    if not isinstance(proposal, dict):
        raise ExportError(CODE_PROPOSAL_UNAVAILABLE)
    out: dict[str, Any] = {}
    for field in PROPOSAL_FIELDS:
        if field not in proposal:
            if field in _OPTIONAL_FIELDS:
                out[field] = None
                continue
            raise ExportError(CODE_PROPOSAL_UNAVAILABLE)
        out[field] = _clone(proposal[field])
    recs = out["recommendations"]
    if not isinstance(recs, list):
        raise ExportError(CODE_PROPOSAL_UNAVAILABLE)
    # Sıra zarftaki sıradır — yeniden sıralama/filtreleme YOK.
    out["recommendations"] = [_project_recommendation(r) for r in recs]
    for field in ("warnings", "limitations"):
        if not isinstance(out[field], list):
            raise ExportError(CODE_PROPOSAL_UNAVAILABLE)
        out[field] = list(out[field])
    return out


def export_strategy_json(proposal: Any) -> bytes:
    """Deterministik JSON baytları: aynı girdi → bayt-özdeş çıktı.

    Anahtar sırası sabit şema sırasıdır (``sort_keys`` DEĞİL — alan
    sırası sözleşmenin parçasıdır); null'lar ve sabit-nokta string'ler
    aynen korunur; UTF-8, ASCII'ye kaçış yapılmaz.
    """
    return json.dumps(export_strategy_dict(proposal),
                      ensure_ascii=False,
                      indent=2).encode("utf-8")


# ── HTTP kompozisyon yardımcısı (Mission 1600/1700 export deseni) ────
# (envelope, body|None, mime|None, filename|None) döner. Geçersiz
# format/zarf → sterile hata zarfı, gövde üretilmez. Bu modül HTTP
# bilmez; desen yalnız ileri API entegrasyonu içindir.

def serialize_strategy(proposal: Any, fmt: str = "json"):
    if fmt not in FORMATS:
        return ({"ok": False, "error": {
            "code": CODE_INVALID_FORMAT,
            "message": "Geçersiz format parametresi."}},
            None, None, None)
    try:
        exported = export_strategy_dict(proposal)
    except ExportError:
        return ({"ok": False, "error": {
            "code": CODE_PROPOSAL_UNAVAILABLE,
            "message": "Strateji önerisi dışa aktarılamadı."}},
            None, None, None)
    # Gövde AYNI projeksiyon anlık görüntüsünden üretilir: zarf ile
    # bayt gövdesi arasında tutarsızlık olamaz.
    body = json.dumps(exported, ensure_ascii=False,
                      indent=2).encode("utf-8")
    return exported, body, JSON_MIME, JSON_FILENAME
