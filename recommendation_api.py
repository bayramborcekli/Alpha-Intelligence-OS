"""Mission 1500.1 / Agent 05 — Tavsiye (Advisory Recommendation) Motoru.

Mevcut doğrulanmış verilerden YALNIZCA bilgilendirme amaçlı, açıklanabilir
operasyonel öneriler üretir. Emir oluşturmaz, emir parametresi (miktar/
fiyat/kaldıraç) üretmez, alım-satım sinyali vermez, kullanıcı adına karar
vermez ve Risk Engine'i geçersiz kılmaz.

Deterministik: aynı girdi → aynı çıktı. Tekrarlayan öneriler kategori
kodu üzerinden birleştirilir; çıktı önem seviyesine göre sıralanır.
Confidence veriden türetilir (taze→HIGH, bayat→MEDIUM, eksik→
INSUFFICIENT_DATA). Bu modül harici LLM/HTTP/borsa modülü kullanmaz.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from intelligence_api import _dec, _pct, _thresholds
from intelligence_models import (
    ConfidenceLevel, DataFreshness, IntelligenceEvidence,
    IntelligenceInsight, IntelligenceStatus, _iso_utc,
)

# Önem sıralaması: küçük sayı = daha önemli. Deterministik.
PRIORITY = {
    "RISK_ALERT_REVIEW": 1,
    "CONCENTRATION_REVIEW": 2,
    "EXPOSURE_REVIEW": 3,
    "CASH_RATIO_REVIEW": 4,
    "POSITION_REVIEW": 5,
    "STALE_DATA_WARNING": 6,
    "DATA_REFRESH": 7,
    "NO_ACTION_NEEDED": 99,
}
_SEVERITY = {1: "Yüksek", 2: "Yüksek", 3: "Orta", 4: "Orta", 5: "Orta",
             6: "Düşük", 7: "Düşük", 99: "Bilgi"}


def _conf_for(freshness_list, sources: tuple[str, ...]) -> ConfidenceLevel:
    """Güven, önerinin DAYANDIĞI kaynakların tazeliğinden türetilir.

    Kaynağa ait tazelik meta verisi yoksa güven yükseltilmez:
    INSUFFICIENT_DATA döner (tazelik kanıtı olmadan HIGH verilmez).
    """
    relevant = [f for f in freshness_list or [] if f.source in sources]
    if not relevant:
        return ConfidenceLevel.INSUFFICIENT_DATA
    st = {f.status for f in relevant}
    if IntelligenceStatus.UNAVAILABLE in st:
        return ConfidenceLevel.INSUFFICIENT_DATA
    if IntelligenceStatus.STALE in st:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.HIGH


def _fresh_for(freshness_list, sources) -> DataFreshness | None:
    """Önerinin kaynağına ait ilk tazelik kaydı (yoksa None)."""
    return next((f for f in freshness_list or []
                 if f.source in sources), None)


def _rec(code, category, title, observation, reason, impact,
         recommendation, confidence, evidence=(), freshness=None):
    return IntelligenceInsight(
        code=code, category=category, title=title, observation=observation,
        reason=reason, impact=impact, recommendation=recommendation,
        confidence=confidence, evidence=evidence, freshness=freshness)


def build_recommendations(account: dict | None = None,
                          positions: list | None = None,
                          risk_summary: dict | None = None,
                          alerts: list | None = None,
                          freshness_list: list | None = None,
                          generated_at: datetime | None = None) -> dict:
    """Önceliklendirilmiş, tekrarsız tavsiye listesi üretir.

    Dönüş: {"ok", "advisory_only", "generated_at", "recommendations":
    [{"priority", "severity", "generated_at", ...insight alanları}]}
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc)
    fl = list(freshness_list or [])
    observed = next((f.observed_at for f in fl
                     if f.observed_at is not None), None)
    th = _thresholds()
    out: dict[str, IntelligenceInsight] = {}   # kod → öneri (tekrarsız)

    def add(code, sources=(), **kw):
        # Güven ve tazelik, önerinin dayandığı kaynaklara özgüdür.
        if code not in out:                    # birleştirme: ilk kayıt kalır
            kw.setdefault("confidence", _conf_for(fl, sources))
            out[code] = _rec(code=code,
                             freshness=_fresh_for(fl, sources), **kw)

    # 1) Risk uyarısı incelemesi (Risk Engine otoritedir; sadece aktarılır)
    if alerts:
        codes = sorted({str(a.get("code")) for a in alerts})
        add("RISK_ALERT_REVIEW", sources=("risk_engine",), category="RISK",
            title="Risk uyarısı incelemesi",
            observation=f"Risk Engine {len(codes)} aktif uyarı bildiriyor: "
                        f"{', '.join(codes)}.",
            reason="Uyarılar motorun tekrarsız uyarı kaydından okunur; "
                   "bu katman uyarıları değiştirmez.",
            impact="Uyarı koşulları sürdükçe risk skoru baskı altında "
                   "kalabilir.",
            recommendation="Uyarı ayrıntılarının Risk sayfasından "
                           "incelenmesi önerilir.",
            evidence=(IntelligenceEvidence(
                source="risk_engine", field="alert_count",
                value=len(codes), unit=None, observed_at=observed),))

    # 2) Yoğunlaşma incelemesi
    single = _dec((risk_summary or {}).get("single_position_pct")) \
        if risk_summary else None
    if single is None and risk_summary:
        lp = risk_summary.get("largest_position") or {}
        single = _dec(lp.get("share_pct"))
    if single is not None and single >= th["MAX_POSITION_PERCENT"]:
        add("CONCENTRATION_REVIEW", sources=("risk_engine",), category="CONCENTRATION",
            title="Yoğunlaşma incelemesi",
            observation=f"En büyük pozisyonun payı %{single}.",
            reason=f"Pay, yapılandırılmış eşiğin "
                   f"(%{th['MAX_POSITION_PERCENT']}) üzerinde.",
            impact="Tek varlıktaki fiyat hareketleri portföyü orantısız "
                   "etkileyebilir.",
            recommendation="Pozisyon dağılımının gözden geçirilmesi "
                           "önerilir.",
            evidence=(IntelligenceEvidence(
                source="risk_engine", field="single_position_pct",
                value=single, unit="%", observed_at=observed),))

    # 3) Maruziyet incelemesi
    exp_pct = _dec((risk_summary or {}).get("exposure_pct_of_margin")) \
        if risk_summary else None
    if exp_pct is not None and exp_pct >= th["HIGH_EXPOSURE_PERCENT"]:
        add("EXPOSURE_REVIEW", sources=("risk_engine",), category="EXPOSURE",
            title="Maruziyet incelemesi",
            observation=f"Brüt maruziyet marj bakiyesinin %{exp_pct}'i.",
            reason=f"Oran, yapılandırılmış eşiğin "
                   f"(%{th['HIGH_EXPOSURE_PERCENT']}) üzerinde.",
            impact="Piyasa dalgalanmalarının hesaba etkisi büyüyebilir.",
            recommendation="Toplam maruziyet düzeyinin değerlendirilmesi "
                           "önerilir.",
            evidence=(IntelligenceEvidence(
                source="risk_engine", field="exposure_pct_of_margin",
                value=exp_pct, unit="%", observed_at=observed),))

    # 4) Nakit oranı incelemesi
    margin = _dec((account or {}).get("usdt_margin_balance"))
    avail = _dec((account or {}).get("usdt_available_balance"))
    cash_pct = _pct(avail, margin)
    if cash_pct is not None and cash_pct <= th["LOW_AVAILABLE_PERCENT"]:
        add("CASH_RATIO_REVIEW", sources=("global_account",), category="PORTFOLIO",
            title="Nakit oranı incelemesi",
            observation=f"Kullanılabilir bakiye marjın %{cash_pct}'i.",
            reason=f"Oran, yapılandırılmış eşiğin "
                   f"(%{th['LOW_AVAILABLE_PERCENT']}) altında.",
            impact="Ani teminat ihtiyaçlarında tampon daralabilir.",
            recommendation="Serbest bakiye düzeyinin gözden geçirilmesi "
                           "önerilir.",
            evidence=(
                IntelligenceEvidence(source="global_account",
                                     field="usdt_available_balance",
                                     value=avail, unit="USDT",
                                     observed_at=observed),
                IntelligenceEvidence(source="global_account",
                                     field="usdt_margin_balance",
                                     value=margin, unit="USDT",
                                     observed_at=observed)))

    # 5) Pozisyon incelemesi (negatif PnL'li pozisyon varsa)
    if positions:
        act = [p for p in positions if p.get("direction") != "FLAT"]
        # Bilinmeyen PnL ASLA 0 sayılmaz: bilinmeyenler ayrı raporlanır.
        losing = sorted(str(p.get("symbol")) for p in act
                        if _dec(p.get("unrealized_pnl")) is not None
                        and _dec(p.get("unrealized_pnl")) < 0)
        unknown_pnl = sorted(str(p.get("symbol")) for p in act
                             if _dec(p.get("unrealized_pnl")) is None)
        if losing:
            add("POSITION_REVIEW", sources=("global_positions",), category="PORTFOLIO",
                title="Pozisyon incelemesi",
                observation=f"{len(losing)} pozisyon zarar bölgesinde: "
                            f"{', '.join(losing)}.",
                reason="Gerçekleşmemiş PnL değerleri doğrulanmış pozisyon "
                       "verisinden okunur.",
                impact="Mevcut seviyeler korunursa zarar gerçekleşmemiş "
                       "olarak sürebilir.",
                recommendation="İlgili pozisyonların durumunun "
                               "değerlendirilmesi önerilir.",
                evidence=(IntelligenceEvidence(
                    source="global_positions", field="unrealized_pnl",
                    value=len(losing), unit=None, observed_at=observed),))
        if unknown_pnl:
            add("DATA_REFRESH", sources=("global_positions",),
                category="DATA_QUALITY", title="Veri yenileme",
                observation=f"{len(unknown_pnl)} pozisyonun PnL değeri "
                            f"doğrulanamadı: {', '.join(unknown_pnl)}.",
                reason="Bilinmeyen PnL 0 sayılmaz; değerlendirme dışında "
                       "tutulur ve açıkça raporlanır.",
                impact="Pozisyon incelemesi bu pozisyonlar için eksiktir.",
                recommendation="Bağlantı durumunun kontrol edilmesi "
                               "önerilir.",
                confidence=ConfidenceLevel.INSUFFICIENT_DATA)

    # 6) Bayat veri uyarısı + 7) Veri yenileme
    stale_sources = sorted(f.source for f in fl
                           if f.status is IntelligenceStatus.STALE)
    missing = not account and positions is None and not risk_summary
    unavailable_sources = sorted(f.source for f in fl
                                 if f.status is IntelligenceStatus.UNAVAILABLE)
    if stale_sources:
        add("STALE_DATA_WARNING", category="DATA_QUALITY",
            title="Bayat veri uyarısı",
            observation=f"Şu kaynakların verisi bayat: "
                        f"{', '.join(stale_sources)}.",
            reason="Tazelik durumu kaynak meta verisinden okunur.",
            impact="Bu kaynaklara dayalı öneriler güncel durumu tam "
                   "yansıtmayabilir.",
            recommendation="Değerlerin, bağlantı yenilenene kadar ihtiyatla "
                           "yorumlanması önerilir.",
            confidence=ConfidenceLevel.MEDIUM)
    if missing or unavailable_sources:
        add("DATA_REFRESH", category="DATA_QUALITY",
            title="Veri yenileme",
            observation=("Hiçbir doğrulanmış kaynak verisi yok."
                         if missing else
                         f"Şu kaynaklara ulaşılamıyor: "
                         f"{', '.join(unavailable_sources)}."),
            reason="Öneri üretimi yalnızca doğrulanmış veriye dayanır; "
                   "eksik veride analiz uydurulmaz.",
            impact="Kapsamlı öneri üretilemiyor.",
            recommendation="Bağlantı durumunun kontrol edilmesi önerilir.",
            confidence=ConfidenceLevel.INSUFFICIENT_DATA)

    # 8) No-action-needed (yalnızca hiçbir bulgu yokken, veri varken)
    if not out and not missing:
        add("NO_ACTION_NEEDED", category="GENERAL",
            title="İşlem gerektiren bulgu yok",
            observation="Mevcut doğrulanmış ölçümler yapılandırılmış "
                        "eşiklerin güvenli tarafında.",
            reason="Hiçbir inceleme koşulu tetiklenmedi.",
            impact="Ek operasyonel adım gerekmiyor; bu bir garanti "
                   "değildir.",
            recommendation="Rutin izlemenin sürdürülmesi önerilir.",
            sources=tuple(f.source for f in fl))

    ordered = sorted(out.values(),
                     key=lambda i: (PRIORITY[i.code], i.code))
    return {
        "ok": True, "advisory_only": True, "read_only": True,
        "generated_at": _iso_utc(generated_at, "generated_at"),
        "recommendations": [
            {"priority": PRIORITY[i.code],
             "severity": _SEVERITY[PRIORITY[i.code]],
             "generated_at": _iso_utc(generated_at, "generated_at"),
             **i.to_dict()}
            for i in ordered],
    }
