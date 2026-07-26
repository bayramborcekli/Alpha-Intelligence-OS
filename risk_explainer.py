"""Mission 1500.1 / Agent 04 — Risk Açıklama Motoru.

Risk Intelligence Engine çıktısını (skor, skor bileşenleri, uyarılar)
Türkçe, deterministik ve İZLENEBİLİR açıklamalara dönüştürür.

Kurallar:
- Risk Engine kararı DEĞİŞTİRİLMEZ; skor uydurulmaz; veri içinde
  olmayan sebep üretilmez (her açıklama motorun kendi bileşen/uyarı
  kaydına dayanır ve kanıt alanı taşır).
- Kesin sonuç/kazanç garantisi verilmez; emir dili ("al", "sat",
  "pozisyon aç") kullanılmaz — öneriler inceleme/değerlendirme dilinde.
- Metin şablonları deterministiktir: aynı girdi → aynı çıktı.
- Bu modül harici LLM, HTTP veya borsa modülü kullanmaz.
"""

from __future__ import annotations

from datetime import datetime

from intelligence_models import (
    ConfidenceLevel, IntelligenceEvidence, IntelligenceInsight,
)

# ── Deterministik şablonlar ─────────────────────────────────────────────────
# Skor bileşeni faktörü → (başlık, gerekçe, olası etki, öneri, kanıt alanı)
_COMPONENT_TEMPLATES = {
    "margin_usage": (
        "Marj kullanımı skoru düşürdü",
        "Marj kullanım oranı yapılandırılmış eşiğin üzerinde ölçüldü.",
        "Yüksek marj kullanımı, ters fiyat hareketlerinde hesabın "
        "esnekliğini azaltabilir.",
        "Marj kullanım düzeyi operatör tarafından gözden geçirilebilir.",
        "margin_usage_pct"),
    "exposure": (
        "Maruziyet skoru düşürdü",
        "Brüt maruziyetin marj bakiyesine oranı yapılandırılmış eşiğin "
        "üzerinde ölçüldü.",
        "Yüksek maruziyet, piyasa dalgalanmalarının hesap değerine "
        "etkisini büyütebilir.",
        "Toplam maruziyet düzeyi operatör tarafından yeniden "
        "değerlendirilebilir.",
        "exposure_pct_of_margin"),
    "concentration": (
        "Yoğunlaşma skoru düşürdü",
        "En büyük varlığın toplam maruziyet içindeki oranı belirlenen "
        "yoğunluk eşiğinin üzerindedir.",
        "Bu varlıktaki fiyat hareketleri toplam portföy değerini "
        "orantısız etkileyebilir.",
        "Pozisyon dağılımı operatör tarafından yeniden "
        "değerlendirilebilir.",
        "single_position_pct"),
    "available_balance": (
        "Düşük kullanılabilir bakiye skoru düşürdü",
        "Kullanılabilir bakiyenin marj bakiyesine oranı yapılandırılmış "
        "eşiğin altında ölçüldü.",
        "Düşük serbest bakiye, ani teminat ihtiyaçlarında tamponu "
        "daraltabilir.",
        "Serbest bakiye düzeyi operatör tarafından gözden geçirilebilir.",
        "margin_usage_pct"),
    "open_orders": (
        "Açık emir yoğunluğu skoru düşürdü",
        "Açık emir sayısı yapılandırılmış eşiğin üzerinde sayıldı.",
        "Çok sayıda açık emir, takip ve operasyon yükünü artırabilir.",
        "Açık emir listesi operatör tarafından incelenebilir.",
        "open_order_count"),
    "drawdown": (
        "Günlük düşüş skoru düşürdü",
        "Yerel doğrulanmış geçmişe göre günlük düşüş uyarı eşiğini aştı.",
        "Süregelen düşüş, hesap değerindeki gerilemenin devam ettiğine "
        "işaret edebilir.",
        "Düşüşün kaynağı operatör tarafından incelenebilir.",
        "daily_drawdown_pct"),
}

# Uyarı kodu → aynı 4'lü yapı + kanıt alanı
_ALERT_TEMPLATES = {
    "HIGH_EXPOSURE": _COMPONENT_TEMPLATES["exposure"],
    "HIGH_MARGIN_USAGE": _COMPONENT_TEMPLATES["margin_usage"],
    "LOW_AVAILABLE_BALANCE": _COMPONENT_TEMPLATES["available_balance"],
    "SINGLE_ASSET_CONCENTRATION": _COMPONENT_TEMPLATES["concentration"],
    "LARGE_DRAWDOWN": _COMPONENT_TEMPLATES["drawdown"],
    "NEGATIVE_UNREALIZED_PNL": (
        "Negatif gerçekleşmemiş PnL bildirimi",
        "Açık pozisyonların toplam gerçekleşmemiş PnL değeri negatif "
        "ölçüldü.",
        "Mevcut fiyat seviyeleri korunursa açık pozisyonlar zarar "
        "bölgesinde kalabilir.",
        "Pozisyonların durumu operatör tarafından değerlendirilebilir.",
        "score"),
}


def _ev(field: str, value, observed_at: datetime | None):
    return IntelligenceEvidence(source="risk_engine", field=field,
                                value=value, unit=None,
                                observed_at=observed_at)


def explain_component(component: dict,
                      observed_at: datetime | None = None
                      ) -> IntelligenceInsight | None:
    """Tek skor bileşeni (motorun kendi ceza kaydı) → açıklama."""
    factor = component.get("factor")
    tpl = _COMPONENT_TEMPLATES.get(factor)
    if tpl is None:
        return None            # tanınmayan faktör için sebep ÜRETİLMEZ
    title, reason, impact, rec, _field = tpl
    detail = component.get("detail")
    penalty = component.get("penalty")
    observation = (f"{detail} ölçüldü; skor bu nedenle {penalty} puan "
                   f"düşürüldü." if detail else
                   f"Skor bu bileşen nedeniyle {penalty} puan düşürüldü.")
    return IntelligenceInsight(
        code=f"RISK_FACTOR_{factor.upper()}", category="RISK",
        title=title, observation=observation, reason=reason,
        impact=impact, recommendation=rec, confidence=ConfidenceLevel.HIGH,
        evidence=(_ev("score", str(penalty), observed_at),))


def explain_alerts(alerts: list | None,
                   observed_at: datetime | None = None) -> list:
    """Risk Engine'in tekrarsız uyarıları → açıklamalar (kod sıralı)."""
    out = []
    for a in sorted(alerts or [], key=lambda x: str(x.get("code"))):
        tpl = _ALERT_TEMPLATES.get(a.get("code"))
        if tpl is None:
            continue           # tanınmayan uyarı için metin uydurulmaz
        title, reason, impact, rec, field = tpl
        out.append(IntelligenceInsight(
            code=f"RISK_ALERT_{a['code']}", category="RISK", title=title,
            observation=a.get("explanation") or a.get("message") or "—",
            reason=reason, impact=impact, recommendation=rec,
            confidence=ConfidenceLevel.HIGH,
            evidence=(_ev(field, a.get("severity"), observed_at),)))
    return out


def explain_risk(risk_summary: dict | None,
                 observed_at: datetime | None = None) -> list:
    """Skorun neden yükseldiğini/düştüğünü izlenebilir biçimde açıklar.

    Girdi: risk_api.summary() çıktısı (risk_score, score_components,
    concentration/alert alanları). Skor asla yeniden hesaplanmaz.
    """
    if not risk_summary or not risk_summary.get("ok"):
        return [IntelligenceInsight(
            code="RISK_EXPLAIN_UNAVAILABLE", category="DATA_QUALITY",
            title="Risk açıklaması üretilemiyor",
            observation="Risk Engine özeti alınamadı.",
            reason="Açıklamalar yalnızca motorun doğrulanmış çıktısına "
                   "dayanır; veri yokken sebep üretilmez.",
            impact="Skor değerlendirmesi yapılamıyor.",
            recommendation="Kaynak bağlantıları operatör tarafından "
                           "kontrol edilebilir.",
            confidence=ConfidenceLevel.INSUFFICIENT_DATA)]

    score = risk_summary.get("risk_score")
    cls = risk_summary.get("classification")
    components = risk_summary.get("score_components") or []
    out = []

    if score is None:
        out.append(IntelligenceInsight(
            code="RISK_SCORE_UNKNOWN_EXPLAIN", category="RISK",
            title="Risk skoru hesaplanamadı",
            observation="Risk Engine skor üretmedi (girdiler "
                        "doğrulanamadı).",
            reason="Motor, doğrulanamayan girdiyle skor uydurmak yerine "
                   "null döndürür.",
            impact="Sağlık sınıflandırması yapılamıyor.",
            recommendation="Veri kaynakları operatör tarafından kontrol "
                           "edilebilir.",
            confidence=ConfidenceLevel.INSUFFICIENT_DATA))
        return out

    total_penalty = sum(int(c.get("penalty", 0)) for c in components
                        if str(c.get("penalty", "")).lstrip("-").isdigit())
    if components:
        factors = ", ".join(sorted(str(c.get("factor"))
                                   for c in components))
        observation = (f"Skor {score}/100 ({cls}). 100 taban puandan "
                       f"toplam {total_penalty} puan düşüldü; etkili "
                       f"faktörler: {factors}.")
        impact = ("Skor, listelenen faktörler iyileşmeden yükselmez; "
                  "her faktörün katkısı aşağıda ayrı açıklanmıştır.")
    else:
        observation = (f"Skor {score}/100 ({cls}). Hiçbir ceza faktörü "
                       f"tetiklenmedi.")
        impact = ("Mevcut ölçümlere göre hesap sağlığı eşiklerin güvenli "
                  "tarafındadır; bu bir garanti değildir.")
    out.append(IntelligenceInsight(
        code="RISK_SCORE_TRACE", category="RISK",
        title="Risk skorunun izlenebilir açıklaması",
        observation=observation,
        reason="Skor, deterministik Risk Engine'in kural tabanlı ceza "
               "bileşenlerinden oluşur; bu katman skoru aynen aktarır.",
        impact=impact,
        recommendation="Bileşen ayrıntıları Risk sayfasından "
                       "incelenebilir.",
        confidence=ConfidenceLevel.HIGH,
        evidence=(_ev("score", score, observed_at),
                  _ev("classification", str(cls), observed_at))))

    for c in sorted(components, key=lambda x: str(x.get("factor"))):
        ins = explain_component(c, observed_at)
        if ins is not None:
            out.append(ins)

    out.extend(explain_alerts(risk_summary.get("alerts"), observed_at))
    return out
