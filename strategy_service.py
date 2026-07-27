"""Mission 1800 — Strategy Intelligence servis katmanı (Agent 03).

Portfolio Intelligence zarfını (PortfolioAnalysis) sağlayıcıdan sterile
biçimde alır, doğrular ve TÜM strateji hesaplamasını
``strategy_intelligence.build_strategy`` çekirdeğine devreder.
Servis hiçbir strateji matematiği yapmaz (kural/güven/öncelik YOK).

Sözleşmeler (Agent 01 mimarisi — değiştirilemez):
- Yalnız veri akışı orkestrasyonu; iş kuralı yok.
- ``proposal_id``/``generated_at`` ÜRETİLMEZ (yalnız API sınırı).
- Kalıcılaştırma yok: dosya/Workspace/Timeline/append_snapshot yok.
- Exchange/emir/execution yok; Flask/HTTP yok; thread/subprocess yok.
- Sağlayıcı exception'ları asla dışarı sızmaz: sterile UNAVAILABLE
  proposal'a dönüşür; bilinmeyen null kalır, asla 0 uydurulmaz.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import strategy_intelligence

# Sağlayıcı sözleşmesi Mission 1700 modeliyle birebir:
# callable() -> {"freshness": "fresh"|"stale", "data": <PortfolioAnalysis>}
PROVIDER_NAMES = ("portfolio_analysis",)
FRESHNESS_STATES = ("fresh", "stale")

# Sterile kaynak kodları
CODE_PROVIDER_FAILED = "PROVIDER_FAILED"
CODE_INVALID_RESULT = "INVALID_PROVIDER_RESULT"
CODE_INVALID_ANALYSIS = "INVALID_ANALYSIS"
CODE_UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"


# ── Sağlayıcı toplama (sterile) ──────────────────────────────────────

def _collect(provider: Any) -> dict[str, Any]:
    """Sağlayıcıyı sterile çalıştırır; exception/bozuk sonuç sızdırmaz."""
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
            or "data" not in result):
        return {"available": False, "freshness": None,
                "code": CODE_INVALID_RESULT, "data": None}
    return {"available": True, "freshness": result["freshness"],
            "code": None, "data": result["data"]}


def _unavailable_analysis() -> dict[str, Any]:
    """Analiz alınamadığında çekirdeğe verilecek dürüst boş girdi."""
    return {
        "analysis_version": strategy_intelligence
        .SUPPORTED_ANALYSIS_VERSION,
        "status": "UNAVAILABLE",
        "portfolio": {},
    }


def _source_meta(meta: Mapping[str, Any], stale_to_partial: bool
                 ) -> dict[str, Any]:
    """Zarfa yazılan sterile kaynak meta verisi (1700 kalıbı)."""
    return {
        "status": "ok" if meta["available"] else "failed",
        "freshness": meta["freshness"] if meta["available"]
        else "unavailable",
        "available": meta["available"],
        "code": meta["code"],
        "degraded_to_partial": stale_to_partial,
    }


# ── Kamu sözleşmesi ──────────────────────────────────────────────────

def analyze_strategy(providers: Mapping[str, Callable[[], Any]]
                     ) -> dict[str, Any]:
    """PortfolioAnalysis sağlayıcısını toplar ve çekirdeğe devreder.

    ``providers``: ``{"portfolio_analysis": callable}``; callable
    ``{"freshness": "fresh"|"stale", "data": <PortfolioAnalysis>}``
    döndürür. Dönüş: StrategyProposal + sterile ``sources`` meta alanı
    (çekirdek zarfının başka hiçbir alanı değiştirilmez;
    ``proposal_id``/``generated_at`` burada YOKTUR).
    """
    if not isinstance(providers, Mapping):
        raise ValueError(strategy_intelligence.ERROR_INVALID_INPUT)
    for name in providers:
        if name not in PROVIDER_NAMES:
            raise ValueError(CODE_UNKNOWN_PROVIDER)

    meta = _collect(providers.get("portfolio_analysis"))

    stale_to_partial = False
    if not meta["available"]:
        analysis: Any = _unavailable_analysis()
    else:
        analysis = meta["data"]
        # Bayat analiz: veri kalitesi dürüstçe PARTIAL'a düşürülür
        # (yalnız durum alanı; portföy verisi değiştirilmez — bu bir
        # kalite beyanıdır, hesap değildir).
        if meta["freshness"] == "stale" and isinstance(analysis, dict) \
                and analysis.get("status") == "OK":
            analysis = dict(analysis)
            analysis["status"] = "PARTIAL"
            stale_to_partial = True

    try:
        proposal = strategy_intelligence.build_strategy(analysis)
    except ValueError:
        # Bozuk/şekilsiz analiz: sterile düşüş — sağlayıcı detayı sızmaz.
        meta = {"available": False, "freshness": None,
                "code": CODE_INVALID_ANALYSIS, "data": None}
        stale_to_partial = False
        proposal = strategy_intelligence.build_strategy(
            _unavailable_analysis())

    proposal["sources"] = {
        "portfolio_analysis": _source_meta(meta, stale_to_partial)}
    return proposal


class StrategyService:
    """İnce OO sarmalayıcı; durum tutmaz (yalnız enjekte bağımlılık)."""

    def __init__(self, providers: Mapping[str, Callable[[], Any]]):
        self._providers = dict(providers)

    def get_proposal(self) -> dict[str, Any]:
        return analyze_strategy(self._providers)


def build_default_strategy_providers() -> dict[str, Callable[[], Any]]:
    """Varsayılan sağlayıcı: gerçek Mission 1700 zinciri.

    İçe aktarmalar tembeldir; bu modülün import'u canlı sisteme
    dokunmaz. ``generated_at`` BİLEREK None geçilir — zaman damgası
    yalnız API kompozisyon sınırında eklenir. Portföy zinciri zaten
    salt-okunurdur (risk sağlayıcısı ``persist=False`` kullanır).
    """
    def portfolio_analysis_provider() -> dict[str, Any]:
        import portfolio_service
        analysis = portfolio_service.get_portfolio_analysis(
            portfolio_service.build_default_providers(), None)
        return {"freshness": "fresh", "data": analysis}

    return {"portfolio_analysis": portfolio_analysis_provider}
