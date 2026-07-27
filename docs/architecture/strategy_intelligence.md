# Strategy Intelligence — Mimari (Mission 1800, Agent 01)

> DURUM: KABUL EDİLMİŞ MİMARİ — uygulama Agents 02+ tarafından yapılır.
> Bu belge sözleşmedir; katmanlar bu belgeye uymak zorundadır.
> Mission 1700 (Portfolio Intelligence) resmen kapalıdır ve
> DEĞİŞTİRİLMEZ; portföy durumunun tek otoritesi olarak kalır.

## 1. Amaç ve konum

Strategy Intelligence, PortfolioAnalysis zarfını (Mission 1700) tüketip
**yalnız tavsiye niteliğinde**, açıklanabilir, deterministik strateji
önerileri üretir. Çıktı, sürümlü ve değişmez (immutable) bir
**StrategyProposal** nesnesidir. Yürütme yolu YOKTUR.

```
Market Data
    │
    ▼
Intelligence Engine            (mevcut — değişmez)
    │
    ▼
Portfolio Intelligence         (Mission 1700 — otorite, değişmez)
    │  PortfolioAnalysis (analysis_version: 1)
    ▼
Strategy Intelligence          (Mission 1800 — bu mimari)
    │
    ▼
StrategyProposal               (immutable, advisory-only)
```

Yasaklar (mimari düzeyde mutlak): yürütme yolu yok · broker adaptörü
yok · emir nesnesi yok · exchange istemcisi yok · miktar/fiyat/emir
tipi alanı yok.

## 2. Katman sınırları ve sorumluluklar

```
Portfolio Intelligence  ──►  Strategy Core  ──►  Strategy Service
                                                      │
                              Strategy Export ◄──  Strategy API  ──► Strategy UI
```

| Katman | Modül (planlanan) | Sorumluluk | YAPMAZ |
|---|---|---|---|
| **Strategy Core** | `strategy_intelligence.py` | Saf hesap: PortfolioAnalysis → StrategyProposal. Yalnız stdlib (`decimal`, `typing`); hiçbir mission modülünü import etmez. Kural motoru: tahsis/yoğunlaşma/çeşitlendirme/maruziyet/risk değerlendirmesi; öneri + güven + öncelik üretimi. | I/O, saat okuma, `proposal_id` üretme, sağlayıcı çağrısı |
| **Strategy Service** | `strategy_service.py` | Toplama: portföy analiz sağlayıcısını (DI ile `portfolio_service.get_portfolio_analysis`) izole çağırır; sterile kaynak meta; Core'a girdi hazırlar. Matematik yok. | Hesap, HTTP, dosya, saat |
| **Strategy API** | `app.py` GET uçları | Yönlendirme + kompozisyon sınırı: `generated_at` ve `proposal_id` YALNIZ burada üretilir; kimlik kapısı; sterile 500. | Hesap, iş kuralı |
| **Strategy UI** | `templates/strategy_intelligence.html` | Yalnız Strategy API tüketir; `textContent`-only; istemci hesabı yok; 5 görsel durum kalıbı (1700 emsali). | API dışı veri yolu, form/AL-SAT |
| **Strategy Export** | `strategy_export.py` | Mevcut zarfın deterministik JSON/CSV serileştirmesi; bellek içi; zarfı ÜRETMEZ ve DEĞİŞTİRMEZ. | Hesap, zaman damgası, dosya |

- Katman atlaması yok: UI yalnız API'yi, Export yalnız zarfı görür.
- Dairesel bağımlılık yok: Core hiçbir üst katmanı bilmez; Portfolio
  Intelligence, Strategy katmanlarından habersiz kalır (tek yönlü ok).
- Portfolio Intelligence'a dokunulmaz: Strategy, `analyze_portfolio`
  çıktısını olduğu gibi tüketir; 1700 zarf sözleşmesi girdi şemasıdır.

## 3. StrategyProposal şeması (sürümlü, immutable)

`strategy_version: 1`. Nesne üretimden sonra ASLA mutasyona uğramaz;
tüm katmanlar onu salt-okunur veri olarak taşır (Export mutasyonsuzluğu
1700'deki gibi testle kilitlenir).

```json
{
  "strategy_version": 1,
  "proposal_id": "<yalnız API sınırında üretilen deterministik-olmayan tek kimlik (uuid4)>",
  "generated_at": "<yalnız API sınırında, UTC ISO>",
  "advisory_only": true,
  "read_only": true,
  "portfolio_analysis_version": 1,
  "confidence": "0.00–100.00 | null",
  "data_quality": "OK | PARTIAL | UNAVAILABLE",
  "market_regime": "UNKNOWN | ... | null",
  "overall_risk": "LOW | MODERATE | HIGH | CRITICAL | null",
  "recommendations": [
    {
      "recommendation_id": "R1, R2, ... (zarf içi deterministik sıra)",
      "instrument": "BTCUSDT",
      "action": "REDUCE | INCREASE | HOLD | REBALANCE | DIVERSIFY",
      "reason_codes": ["CONCENTRATION_HIGH", "..."],
      "priority": "1–5 (1 en yüksek)",
      "confidence": "0.00–100.00",
      "current_weight": "sabit-nokta % | null",
      "target_weight": "sabit-nokta % | null",
      "risk_level": "LOW | MODERATE | HIGH",
      "expected_effect": {"metric": "...", "direction": "...",
                          "magnitude_pct": "... | null"},
      "invalidation_conditions": ["kod-listesi"]
    }
  ],
  "warnings": ["kod-listesi"],
  "limitations": ["kod-listesi"]
}
```

Şema kuralları:
- **Yürütme alanı yok:** emir tipi / miktar / fiyat alanları şemada
  bulunamaz (AST + şema testleriyle yasaklanır). Ağırlık hedefleri
  yüzde cinsindendir, emre çevrilemez.
- Tüm sayılar **sabit-nokta string**; bilinmeyen → `null` (asla 0).
- `reason_codes`, `warnings`, `limitations`, `invalidation_conditions`
  yalnız **kapalı listeden kodlardır** (sterile — serbest metin yok).
- `data_quality`, girdi PortfolioAnalysis `status`unu aynen taşır;
  PARTIAL girdi → düşen alanlardan türeyen öneriler bastırılır veya
  güven düşürülür, asla uydurulmaz.
- `market_regime` bu sürümde `"UNKNOWN"` sabitidir (rejim tespiti
  uygulanana dek dürüst bilinmezlik) — bilinen sınırlama olarak
  dokümante edilir.

### Determinizm ve `proposal_id`
Core çıktısı, aynı PortfolioAnalysis için **bayt-özdeştir**
(kararlı sıralama: öneriler sabit kural sırası + enstrüman adı;
listeler sıralı). `proposal_id` ve `generated_at` deterministik
çekirdeğin DIŞINDA, yalnız API kompozisyon sınırında eklenir — böylece
çekirdek determinizmi ile istek-başına kimlik birbirine karışmaz.

## 4. Kural motoru (Core) — değerlendirme eksenleri

Girdi: PortfolioAnalysis `portfolio` bölümleri. Her kural saf
fonksiyondur: `(bölüm, eşikler) → öneri|None + reason_codes`.

1. **Tahsis:** `allocation.cash_weight_pct` aşırılıkları (nakit fazlası
   / tam yatırım) → `REBALANCE`.
2. **Yoğunlaşma:** `concentration.hhi`, `top_share_pct`,
   `risk_utilization.concentration_util_pct` → `REDUCE`/`DIVERSIFY`.
3. **Çeşitlendirme:** `effective_positions` düşüklüğü → `DIVERSIFY`.
4. **Maruziyet:** `exposure.net_pct/gross_pct`,
   `net_exposure_util_pct` → `REDUCE`/`HOLD`.
5. **Risk çıktıları:** `limits_breached`, `drawdown_util_pct`,
   `health.portfolio_health_score` → öncelik/`overall_risk` yükseltme.

Null girdi kuralı: kuralın gerektirdiği alan `null` ise kural **sessizce
atlanır** ve `limitations`'a kod düşülür — 0 varsayılmaz.

## 5. Güvenlik garantileri (mimari zorunluluk, testle kilitlenecek)

Exchange yok · yürütme yok · emir yok · broker SDK yok · dosya sistemi
yazımı yok · geçici dosya yok · `append_snapshot` yok · Workspace
yazımı yok · Timeline yazımı yok · ağ istemcisi yok
(`requests/websocket/socket` AST-yasak) · dinamik yürütme yok
(`eval/exec/compile/__import__` AST-yasak) · sterile hatalar (yalnız
kod; istisna metni/yol/traceback yok) · kimlik sınırı mevcut oturum
kapısıdır (değişmez). Risk sınırı: eşik otoritesi Risk Engine'de kalır;
Strategy yalnız PortfolioAnalysis içinden gelen türetilmiş değerleri
okur (Risk Engine'e yeni doğrudan bağımlılık eklenmez).

Hata modeli (1700 kalıbı): Core `INVALID_INPUT`/`FLOAT_REJECTED`;
Service `ANALYSIS_UNAVAILABLE`/`INVALID_ANALYSIS`; API
`STRATEGY_ANALYSIS_ERROR` (500, sterile); Export
`INVALID_FORMAT`/`PROPOSAL_UNAVAILABLE`.

## 6. Determinizm garantileri

Decimal-only aritmetik (float → `FLOAT_REJECTED`) · sabit-nokta string
çıktı · kararlı sıralama · immutable çıktı (üretim sonrası mutasyon
yok; Export mutasyonsuzluk testi) · sürümlü şema (`strategy_version`)
· deterministik serileştirme (sabit anahtar sırası; aynı zarf →
bayt-özdeş JSON/CSV) · bilinmeyen → `null` · `generated_at` (ve
`proposal_id`) yalnız API sınırında.

## 7. Gelecek uyumluluğu

StrategyProposal, gelecek katmanların TEK tüketim sözleşmesidir;
Strategy Core değişmeden şunlar eklenebilir:

- **Monitoring & Alerting:** proposal'ları okuyup uyarı üretir
  (salt-okur tüketici; Core'a dokunmaz).
- **Execution Control:** insan-onay kapısı; proposal'ı girdi alır,
  ayrı bir onay nesnesi üretir (Core dışı yeni katman).
- **Execution Engine / Paper Trading / Shadow Mode:** onaylı
  proposal'ı emre çevirme İLERİDE ve YALNIZ bu ayrı katmanlarda olur;
  ağırlık-hedefi → miktar çevirisi Core'a asla girmez.

Uyumluluk mekanizmaları: sürümlü şema (`strategy_version` artar,
eski alan sözleşmeleri korunur) · `proposal_id` ile tekil referans ·
kapalı kod listeleri genişletilebilir (tüketiciler bilinmeyen kodu
yok sayar) · Core saf-fonksiyon arayüzü (`build_strategy(analysis)`)
sabit kalır.

## 8. Uygulama ajan planı (1700 emsali)

02 Core → 03 Service → 04 API → 05 UI → 06 Export → 07 Güvenlik →
08 Tam regresyon → 09 Dokümantasyon → 10 Kapanış. Her ajan: testler +
mimari inceleme + tam regresyon (`alpha20_v1/` önce geri alınır) +
kapsamlı commit + push. Yeni "intelligence" içeren rotalar
`EXPECTED_INTEL_ROUTES`'a eklenir.

İlgili: `docs/portfolio_intelligence.md` · `docs/architecture.md` ·
`docs/MISSION_INDEX.md`
