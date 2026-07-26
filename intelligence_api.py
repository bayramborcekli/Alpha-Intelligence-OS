"""Mission 1500.1 / Agent 03 — Deterministik Intelligence çekirdeği.

Tamamen KURAL TABANLI: harici LLM yok, rastgelelik yok, fiyat tahmini
yok, getiri iddiası yok. Aynı girdi → aynı çıktı.

Girdi sözleşmesi: yalnızca mevcut salt-okunur servislerden alınmış
NORMALİZE veri (dict/list) parametre olarak verilir — bu modül exchange
istemcilerine DOĞRUDAN ERİŞMEZ (HTTP/borsa modülü importu yoktur).
Risk Engine çıktısı yalnızca TÜKETİLİR; yeniden hesaplanmaz, değiştirilmez.

Veri yoksa analiz UYDURULMAZ: ilgili içgörü INSUFFICIENT_DATA güveniyle
"Veri Yok" gerekçesi taşır veya hiç üretilmez. Tüm para matematiği
Decimal'dir; tüm çıktılar tavsiye niteliğindedir (advisory_only=True).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from intelligence_models import (
    ConfidenceLevel, DataFreshness, IntelligenceEvidence,
    IntelligenceInsight, IntelligenceStatus, IntelligenceSummary,
)

STABLECOINS = frozenset({"USDT", "USDC", "FDUSD", "BUSD", "TUSD",
                         "DAI", "USDP"})

# Eşikler risk_config.json'dan okunur (Risk Engine ile aynı dosya) —
# dosya yoksa/bozuksa güvenli varsayılanlar. Sabit iş-mantığı eşiği yok.
_CONFIG_PATH = Path("risk_config.json")
_DEFAULTS = {
    "MAX_POSITION_PERCENT": Decimal("25"),
    "POSITION_HIGH_PERCENT": Decimal("40"),
    "HIGH_EXPOSURE_PERCENT": Decimal("150"),
    "LOW_AVAILABLE_PERCENT": Decimal("20"),
    "MAX_OPEN_ORDERS": Decimal("20"),
}


def _thresholds() -> dict[str, Decimal]:
    vals = dict(_DEFAULTS)
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for k in vals:
                d = _dec(raw.get(k))
                if d is not None:
                    vals[k] = d
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return vals


def _dec(v) -> Decimal | None:
    if v is None or isinstance(v, bool) or v == "":
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _pct(part: Decimal | None, whole: Decimal | None) -> Decimal | None:
    if part is None or whole is None or whole == 0:
        return None
    try:
        return (part / whole * 100).quantize(Decimal("0.01"),
                                             rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _ev(source: str, fld: str, value, unit: str | None,
        observed_at: datetime | None) -> IntelligenceEvidence:
    return IntelligenceEvidence(source=source, field=fld, value=value,
                                unit=unit, observed_at=observed_at)


def _insight(code, category, title, observation, reason, impact,
             recommendation, confidence, evidence=(), freshness=None):
    return IntelligenceInsight(
        code=code, category=category, title=title, observation=observation,
        reason=reason, impact=impact, recommendation=recommendation,
        confidence=confidence, evidence=evidence, freshness=freshness)


# ── Analizler ───────────────────────────────────────────────────────────────

def analyze_portfolio(account: dict | None,
                      observed_at: datetime | None = None) -> list:
    """Portföy bileşimi + nakit/stablecoin oranı + gerçekleşmemiş PnL.

    account: dashboard servisinin normalize global hesap modeli.
    """
    if not account:
        return [_insight(
            "PORTFOLIO_DATA_MISSING", "DATA_QUALITY", "Portföy verisi yok",
            "Doğrulanmış hesap verisi alınamadı.",
            "Kaynak servis veri döndürmedi.",
            "Portföy analizi yapılamıyor — hiçbir değer tahmin edilmedi.",
            "Bağlantı durumunu kontrol etmeniz önerilir.",
            ConfidenceLevel.INSUFFICIENT_DATA)]
    out = []
    margin = _dec(account.get("usdt_margin_balance"))
    avail = _dec(account.get("usdt_available_balance"))
    upnl = _dec(account.get("unrealized_pnl"))

    cash_pct = _pct(avail, margin)
    if cash_pct is None:
        out.append(_insight(
            "CASH_RATIO_UNKNOWN", "PORTFOLIO", "Nakit oranı bilinmiyor",
            "Marj veya kullanılabilir bakiye doğrulanamadı.",
            "Oran için iki değer de gereklidir.",
            "Nakit tamponu değerlendirilemiyor.",
            "Veri geldiğinde otomatik hesaplanacaktır.",
            ConfidenceLevel.INSUFFICIENT_DATA))
    else:
        low = cash_pct <= _thresholds()["LOW_AVAILABLE_PERCENT"]
        out.append(_insight(
            "CASH_STABLECOIN_RATIO", "PORTFOLIO",
            "Nakit/stablecoin oranı",
            f"Kullanılabilir USDT bakiyesi marjın %{cash_pct}'i.",
            "Evren tek para birimidir (USDT); oran kullanılabilir/marj "
            "bakiyesinden hesaplanır.",
            ("Nakit tamponu düşük — ani hareketlerde esneklik azalır."
             if low else "Nakit tamponu mevcut."),
            ("Serbest bakiyeyi artırmayı değerlendirmeniz önerilir."
             if low else "Mevcut tamponu korumanız önerilir."),
            ConfidenceLevel.HIGH,
            evidence=(
                _ev("global_account", "usdt_margin_balance", margin,
                    "USDT", observed_at),
                _ev("global_account", "usdt_available_balance", avail,
                    "USDT", observed_at))))
    if upnl is None:
        out.append(_insight(
            "UNREALIZED_PNL_UNKNOWN", "PORTFOLIO",
            "Gerçekleşmemiş PnL bilinmiyor",
            "Gerçekleşmemiş PnL doğrulanamadı.", "Kaynak alan boş.",
            "PnL durumu değerlendirilemiyor.", "Veri bekleniyor.",
            ConfidenceLevel.INSUFFICIENT_DATA))
    else:
        neg = upnl < 0
        out.append(_insight(
            "UNREALIZED_PNL_STATUS", "PORTFOLIO",
            "Gerçekleşmemiş PnL durumu",
            f"Toplam gerçekleşmemiş PnL {upnl} USDT.",
            "Değer doğrudan doğrulanmış hesap verisinden okunur.",
            ("Açık pozisyonlar toplamda zarar bölgesinde."
             if neg else ("Açık pozisyonlar toplamda kâr bölgesinde."
                          if upnl > 0 else "Açık PnL nötr.")),
            ("Pozisyonları gözden geçirmeniz önerilir."
             if neg else "Mevcut durumu izlemeniz önerilir."),
            ConfidenceLevel.HIGH,
            evidence=(_ev("global_account", "unrealized_pnl", upnl,
                          "USDT", observed_at),)))
    return out


def analyze_positions(positions: list | None,
                      margin_balance=None,
                      observed_at: datetime | None = None) -> list:
    """Pozisyon sayısı, long/short dengesi, tek-varlık yoğunluğu."""
    if positions is None:
        return [_insight(
            "POSITIONS_DATA_MISSING", "DATA_QUALITY",
            "Pozisyon verisi yok", "Doğrulanmış pozisyon listesi alınamadı.",
            "Kaynak servis veri döndürmedi.",
            "Pozisyon analizi yapılamıyor.", "Bağlantıyı kontrol edin.",
            ConfidenceLevel.INSUFFICIENT_DATA)]
    active = [p for p in positions if p.get("direction") != "FLAT"]
    out = []
    if not active:
        out.append(_insight(
            "NO_OPEN_POSITIONS", "PORTFOLIO", "Açık pozisyon yok",
            "Doğrulanmış veride açık pozisyon bulunmuyor.",
            "Pozisyon listesi boş.", "Piyasa riski taşınmıyor.",
            "Bilgi amaçlıdır; işlem önerilmez.",
            ConfidenceLevel.HIGH))
        return out

    def notional(p) -> Decimal | None:
        """Bilinmeyen bacak ASLA 0'a çevrilmez — None döner."""
        amt, mark = _dec(p.get("position_amt")), _dec(p.get("mark_price"))
        if amt is None or mark is None:
            return None
        return abs(amt) * mark

    known = [p for p in active if notional(p) is not None]
    unknown = [p for p in active if notional(p) is None]
    longs = sum((notional(p) for p in known
                 if p.get("direction") == "LONG"), Decimal(0))
    shorts = sum((notional(p) for p in known
                  if p.get("direction") == "SHORT"), Decimal(0))
    gross = longs + shorts

    out.append(_insight(
        "OPEN_POSITION_COUNT", "PORTFOLIO", "Açık pozisyon sayısı",
        f"{len(active)} açık pozisyon var.",
        "Doğrulanmış pozisyon listesinden sayıldı.",
        "Her pozisyon ayrı takip gerektirir.",
        "Pozisyonları düzenli izlemeniz önerilir.",
        ConfidenceLevel.HIGH))

    if unknown:
        # Eksik alanlı pozisyonlar oran matematiğine katılmaz ve açıkça
        # raporlanır — 0'a zorlanarak dağılım çarpıtılmaz.
        syms = ", ".join(sorted(str(p.get("symbol")) for p in unknown))
        out.append(_insight(
            "POSITION_VALUE_UNKNOWN", "DATA_QUALITY",
            "Pozisyon değeri doğrulanamadı",
            f"{len(unknown)} pozisyonun nominal değeri hesaplanamadı "
            f"({syms}).",
            "position_amt veya mark_price alanı doğrulanamadı.",
            "Bu pozisyonlar maruziyet/yoğunluk oranlarına dahil edilmedi; "
            "oranlar eksik veri nedeniyle kısmi olabilir.",
            "Kaynak bağlantısı yenilendiğinde oranlar tamamlanacaktır.",
            ConfidenceLevel.INSUFFICIENT_DATA))
        if not known:
            return sorted(out, key=lambda i: i.code)

    lp, sp = _pct(longs, gross), _pct(shorts, gross)
    if lp is not None and sp is not None:
        one_sided = lp == 100 or sp == 100
        out.append(_insight(
            "LONG_SHORT_EXPOSURE", "EXPOSURE", "Long/Short dengesi",
            f"Maruziyetin %{lp}'i long, %{sp}'i short.",
            "Nominal değerler işaretine göre gruplandı.",
            ("Maruziyet tek yönlü — piyasa yönü riskine tam açık."
             if one_sided else "Maruziyet iki yöne dağılmış."),
            "Yön dengesini risk toleransınıza göre değerlendirin.",
            ConfidenceLevel.HIGH))

    # Tek varlık yoğunluğu (en büyük pozisyonun brüt içindeki payı)
    if gross > 0:
        top = max(known, key=notional)
        share = _pct(notional(top), gross)
        th = _thresholds()
        if share is not None:
            high = share >= th["POSITION_HIGH_PERCENT"]
            warn = share >= th["MAX_POSITION_PERCENT"]
            out.append(_insight(
                "SINGLE_ASSET_CONCENTRATION", "CONCENTRATION",
                "Tek varlık yoğunluğu",
                f"En büyük pozisyon ({top.get('symbol')}) brüt maruziyetin "
                f"%{share}'i.",
                f"Eşikler yapılandırmadan okunur "
                f"(uyarı %{th['MAX_POSITION_PERCENT']}, yüksek "
                f"%{th['POSITION_HIGH_PERCENT']}).",
                ("Tek varlığa bağımlılık yüksek." if high else
                 ("Yoğunluk uyarı eşiğinin üzerinde." if warn
                  else "Yoğunluk eşiklerin altında.")),
                ("Dağılımı çeşitlendirmeyi değerlendirmeniz önerilir."
                 if warn else "Mevcut dağılımı izlemeniz önerilir."),
                ConfidenceLevel.HIGH,
                evidence=(_ev("global_positions", "symbol",
                              str(top.get("symbol")), None, observed_at),)))
    return out


def analyze_risk(risk_summary: dict | None,
                 observed_at: datetime | None = None) -> list:
    """Risk Engine çıktısını YALNIZCA tüketir ve açıklar (skor aynen)."""
    if not risk_summary or not risk_summary.get("ok"):
        return [_insight(
            "RISK_ENGINE_UNAVAILABLE", "DATA_QUALITY",
            "Risk motoru verisi yok",
            "Risk Engine özeti alınamadı.", "Kaynak yanıt vermedi.",
            "Risk açıklaması üretilemiyor — skor uydurulmaz.",
            "Risk sayfasını kontrol edin.",
            ConfidenceLevel.INSUFFICIENT_DATA)]
    out = []
    score = risk_summary.get("risk_score")
    cls = risk_summary.get("classification")
    if score is None:
        out.append(_insight(
            "RISK_SCORE_UNKNOWN", "RISK", "Risk skoru bilinmiyor",
            "Risk Engine skoru null döndürdü.",
            "Girdiler doğrulanamadığında motor skor üretmez.",
            "Sağlık değerlendirmesi yapılamıyor.",
            "Kaynak bağlantılarını kontrol edin.",
            ConfidenceLevel.INSUFFICIENT_DATA))
    else:
        out.append(_insight(
            "RISK_HEALTH_EXPLAIN", "RISK", "Risk sağlık skoru",
            f"Risk Engine skoru {score}/100 ({cls}).",
            "Skor deterministik Risk Engine tarafından hesaplanır; "
            "bu katman skoru yeniden hesaplamaz ve değiştirmez.",
            "Skor hesap sağlığının kural tabanlı özetidir.",
            "Skor bileşenlerini Risk sayfasından inceleyebilirsiniz.",
            ConfidenceLevel.HIGH,
            # Otoriter skor DÖNÜŞTÜRÜLMEDEN aynen aktarılır
            evidence=(_ev("risk_engine", "score",
                          score if isinstance(score, (int, str))
                          else str(score), None, observed_at),)))
    alerts = risk_summary.get("alerts")
    if isinstance(alerts, list) and alerts:
        codes = sorted({str(a.get("code")) for a in alerts})
        out.append(_insight(
            "ACTIVE_RISK_ALERTS", "RISK", "Aktif risk uyarıları",
            f"{len(codes)} aktif uyarı: {', '.join(codes)}.",
            "Uyarılar Risk Engine'in tekrarsız uyarı motorundan okunur.",
            "Uyarılar dikkat gerektiren koşulları işaret eder.",
            "Uyarı ayrıntılarını Risk sayfasında inceleyin.",
            ConfidenceLevel.HIGH,
            evidence=(_ev("risk_engine", "alert_count", len(codes),
                          None, observed_at),)))
    return out


def analyze_freshness(freshness_list: list | None) -> list:
    """Kaynak tazeliği: STALE/UNAVAILABLE kaynaklar için veri-kalite içgörüsü."""
    out = []
    for f in freshness_list or []:
        if not isinstance(f, DataFreshness):
            raise TypeError("freshness_list yalnızca DataFreshness içerir")
        if f.status in (IntelligenceStatus.STALE,
                        IntelligenceStatus.UNAVAILABLE):
            stale = f.status is IntelligenceStatus.STALE
            out.append(_insight(
                f"FRESHNESS_{f.source.upper()}", "DATA_QUALITY",
                f"Veri tazeliği: {f.source}",
                (f"{f.source} verisi bayat (yaş: {f.age_seconds} sn)."
                 if stale else f"{f.source} verisi alınamıyor."),
                "Tazelik durumu kaynak meta verisinden okunur.",
                "Bu kaynağa dayalı içgörüler daha düşük güvenilirlikte.",
                "Bağlantı yenilenene kadar değerleri ihtiyatla yorumlayın.",
                ConfidenceLevel.MEDIUM if stale
                else ConfidenceLevel.INSUFFICIENT_DATA,
                freshness=f))
    return sorted(out, key=lambda i: i.code)


def build_intelligence_summary(account: dict | None = None,
                               positions: list | None = None,
                               risk_summary: dict | None = None,
                               freshness_list: list | None = None,
                               generated_at: datetime | None = None,
                               ) -> IntelligenceSummary:
    """Deterministik özet: aynı girdi → aynı çıktı (generated_at hariç,
    determinism için açıkça verilebilir)."""
    if generated_at is None:
        generated_at = datetime.now(timezone.utc)
    observed = None
    for f in freshness_list or []:
        if f.observed_at is not None:
            observed = f.observed_at
            break

    insights = (analyze_portfolio(account, observed)
                + analyze_positions(positions, observed_at=observed)
                + analyze_risk(risk_summary, observed)
                + analyze_freshness(freshness_list))
    insights = sorted(insights, key=lambda i: i.code)
    # Pozisyon geneli eksik-değer durumunda maruziyet oranları kısmidir;
    # bu, POSITION_VALUE_UNKNOWN içgörüsüyle açıkça raporlanır.

    sources = {"account": account, "positions": positions,
               "risk": risk_summary}
    missing = [k for k, v in sources.items() if not v]
    stale = any(f.status in (IntelligenceStatus.STALE,
                             IntelligenceStatus.UNAVAILABLE)
                for f in freshness_list or [])
    if len(missing) == len(sources):
        status = IntelligenceStatus.UNAVAILABLE
    elif missing:
        status = IntelligenceStatus.PARTIAL
    elif stale:
        status = IntelligenceStatus.STALE
    else:
        status = IntelligenceStatus.OK

    margin = _dec((account or {}).get("usdt_margin_balance"))
    avail = _dec((account or {}).get("usdt_available_balance"))
    active_count = (len([p for p in positions
                         if p.get("direction") != "FLAT"])
                    if positions is not None else None)
    portfolio_summary = {
        "usdt_margin_balance": margin,          # None → null (asla 0)
        "usdt_available_balance": avail,
        "unrealized_pnl": _dec((account or {}).get("unrealized_pnl")),
        "open_position_count": active_count,
        "currency_universe": "USDT",
    }
    risk_ok = bool(risk_summary and risk_summary.get("ok"))
    risk_out = {
        "risk_score": risk_summary.get("risk_score") if risk_ok else None,
        "classification": risk_summary.get("classification")
        if risk_ok else None,
        "alert_count": len(risk_summary.get("alerts") or [])
        if risk_ok else None,
    }
    recommendations = tuple(i.recommendation for i in insights
                            if i.confidence is ConfidenceLevel.HIGH
                            and i.recommendation != "—")
    warnings = tuple(
        i.title for i in insights
        if i.confidence is ConfidenceLevel.INSUFFICIENT_DATA)

    return IntelligenceSummary(
        status=status, generated_at=generated_at,
        portfolio_summary=portfolio_summary, risk_summary=risk_out,
        insights=tuple(insights), recommendations=recommendations,
        warnings=warnings, freshness=tuple(freshness_list or ()))
