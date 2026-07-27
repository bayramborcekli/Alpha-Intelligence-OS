# Strategy Intelligence (Mission 1800)

> Resmî dokümantasyon — Agents 01–08 tarafından teslim edilen sistemi
> OLDUĞU GİBİ tanımlar. Gelecek çalışma, uygulanmış gibi anlatılmaz.

## A. Genel bakış

Mission 1800, Mission 1700 PortfolioAnalysis zarfını tüketip **yalnız
tavsiye niteliğinde**, açıklanabilir, deterministik bir
StrategyProposal (`strategy_version: 1`) üreten katmanlı bir sistem
ekler.

- **Salt-okunur felsefe:** hiçbir katman emir vermez, Exchange'e
  bağlanmaz, dosya/Workspace/Timeline yazmaz. Öneri üretimi bir "okuma
  ve yorum" işlemidir; risk-limit otoritesi Risk Engine'de kalır.
- **Advisory-only tasarım:** şemada emir tipi, miktar veya fiyat alanı
  YOKTUR — hedefler yüzde ağırlıklardır (`target_weight`). Zarf bunu
  açıkça beyan eder: `advisory_only: true`, `read_only: true`.
- **Kapsam:** Core kural motoru, Service toplama katmanı, salt-okunur
  GET API, salt-okunur UI paneli, deterministik JSON Export ve
  güvenlik/regresyon doğrulaması.

## B. Mimari ve katman sorumlulukları

```
portfolio_service.py (Mission 1700 — PortfolioAnalysis kaynağı)
        │  build_default_strategy_providers(): tembel import,
        │  generated_at=None, risk yolu persist=False (salt-okunur)
        ▼
strategy_service.py    — TOPLAMA: sağlayıcı izolasyonu, sterile
        │                sources meta, bayat→PARTIAL düşürmesi;
        │                strateji matematiği YOK
        ▼
strategy_intelligence.py — HESAP (Core): saf stdlib (decimal/typing),
        │                Decimal-only deterministik kural motoru
        ▼
app.py (API)           — YÖNLENDİRME: GET uçları, kimlik kapısı,
        │                proposal_id + generated_at kompozisyon sınırı
        ├──────────────► templates/strategy_intelligence.html (UI)
        │                — RENDER: yalnız API tüketir, hesap YOK
        ▼
strategy_export.py     — SERİLEŞTİRME: sabit-şema projeksiyon + JSON;
                         öneriyi üretmez ve DEĞİŞTİRMEZ
```

- Ters/dairesel bağımlılık yok: Core hiçbir mission modülünü import
  etmez; Service yalnız Core'u; Export yalnız `json/typing` kullanır;
  Portfolio katmanı Strategy'den habersizdir.
- Katman atlaması yok: UI yalnız API'yi, Export yalnız API-uyumlu
  öneriyi tüketir; API serileştirme/hesap yapmaz.

## C. StrategyProposal şeması (v1 — 13 üst alan, sabit sıra)

| Alan | Anlam |
|---|---|
| `strategy_version` | Şema sürümü; daima `1` |
| `proposal_id` | Öneri kimliği — YALNIZ API sınırında üretilir (Core/Service/Export üretmez; Export'ta yoksa `null` taşınır) |
| `generated_at` | UTC ISO zaman damgası — YALNIZ API sınırında üretilir (aynı sahiplik kuralı) |
| `advisory_only` | Daima `true` — öneri tavsiyedir, yürütme semantiği yok |
| `read_only` | Daima `true` — sistem hiçbir durumu değiştirmez |
| `portfolio_analysis_version` | Tüketilen PortfolioAnalysis sürümü; daima `1` |
| `confidence` | Genel güven (sabit-nokta yüzde string); temel `80`, PARTIAL'da `-20`, öneri varsa öneri ortalamasıyla harmanlanır; UNAVAILABLE → `null` |
| `data_quality` | Girdi `status`u aynen: `OK` \| `PARTIAL` \| `UNAVAILABLE` (bayat sağlayıcı → PARTIAL düşürmesi Service'te) |
| `market_regime` | v1'de daima `"UNKNOWN"` (rejim tespiti uygulanmadı — `MARKET_REGIME_UNKNOWN` sınırlaması her zarfta) |
| `overall_risk` | `CRITICAL` (limit aşımı) \| `HIGH` \| `MODERATE` \| `LOW`; değerlendirme temeli yoksa `null` |
| `recommendations` | Öneri listesi (aşağıda); UNAVAILABLE'da daima boş |
| `warnings` | Kapalı kod listesi, ad-sıralı: `LOW_DATA_QUALITY`, `RISK_LIMIT_BREACHED`, `ANALYSIS_UNAVAILABLE` |
| `limitations` | Kapalı kod listesi, ad-sıralı: `MARKET_REGIME_UNKNOWN`, `NO_FORECAST` (her zarfta) + `ALLOCATION_UNKNOWN`, `EXPOSURE_UNKNOWN`, `CONCENTRATION_UNKNOWN`, `DIVERSIFICATION_UNKNOWN`, `RISK_UTILIZATION_UNKNOWN` |

Service zarfı ayrıca sterile `sources` meta alanı taşır
(`{"portfolio_analysis": {status, freshness, available, code,
degraded_to_partial}}`); Export bu meta'yı şema dışı olduğu için düşürür.

### Öneri alanları (öneri başına 11 alan, sabit sıra)

| Alan | Anlam |
|---|---|
| `recommendation_id` | Kararlı sıralama SONRASI atanan `R1, R2, …` (sayaç; rastgelelik/UUID yok) |
| `instrument` | Sembol (ör. yoğunlaşma kuralında `top_symbol`) veya portföy-geneli için `"PORTFOLIO"` |
| `action` | Kapalı liste: `REDUCE` \| `INCREASE` \| `HOLD` \| `REBALANCE` \| `DIVERSIFY` — yürütme değil, yön beyanı |
| `reason_codes` | Kapalı kod listesi (serbest metin yok): `RISK_LIMIT_BREACHED`, `RISK_LIMIT_NEAR`, `CONCENTRATION_HIGH`, `DIVERSIFICATION_LOW`, `EXCESS_CASH`, `UNDER_ALLOCATED`, `OVER_ALLOCATED`, `LOW_DATA_QUALITY` |
| `priority` | Tamsayı; 1 en acil (limit aşımı) … 4 (tahsis ince ayarı) |
| `confidence` | Neden-bazlı sabit kalibrasyon (ör. aşım `90`, yoğunlaşma `85`); PARTIAL'da `-20`; sabit-nokta string |
| `current_weight` | Mevcut değer (yüzde string) veya `null` |
| `target_weight` | Hedef değer (yüzde string) veya `null` — miktar/fiyat DEĞİL |
| `risk_level` | `HIGH` \| `MODERATE` \| `LOW` |
| `expected_effect` | `{metric, direction, magnitude_pct}` — ör. `{TOP_SHARE_PCT, DECREASE, "30.00"}`; bilinmeyen büyüklük `null` |
| `invalidation_conditions` | Kapalı kod listesi, ad-sıralı: `ALLOCATION_CHANGED`, `CONCENTRATION_REDUCED`, `EXPOSURE_CHANGED`, `RISK_UTILIZATION_CHANGED`, `DATA_QUALITY_IMPROVED` |

## D. Kural motoru (deterministik — AI yok, ML yok, rastgelelik yok)

SABİT değerlendirme sırası: **risk limitleri → yoğunlaşma →
çeşitlendirme → tahsis/maruziyet**. Tüm eşikler sabit Decimal
sabitleridir (tavsiye kalibrasyonu; Risk Engine eşiklerinin yerine
geçmez):

1. **Risk limitleri** (`risk_utilization`): herhangi bir kullanım
   > %100 veya `limits_breached` doluysa → `REDUCE` (öncelik 1, HIGH,
   `RISK_LIMIT_BREACHED` + uyarı); %80–100 arası → `HOLD` (öncelik 2,
   MODERATE, `RISK_LIMIT_NEAR`).
2. **Yoğunlaşma** (`concentration`): `top_share_pct` > %50 →
   `REDUCE top_symbol` (öncelik 2, HIGH, hedef %50).
3. **Çeşitlendirme**: `effective_positions` < 3 ve varlık listesi
   doluysa → `DIVERSIFY` (öncelik 3, MODERATE). Pozisyon yokken
   çeşitlendirme önerilmez.
4. **Tahsis/maruziyet** (`allocation` + `exposure`): nakit ağırlığı
   > %60 → `REBALANCE` (öncelik 4, LOW, hedef %30, `EXCESS_CASH`);
   brüt maruziyet > %100 → `REDUCE` (öncelik 2, HIGH,
   `OVER_ALLOCATED`); brüt < %20 VE nakit > %60 → `INCREASE`
   (öncelik 4, LOW, `UNDER_ALLOCATED`).

Kural girdisi `null` ise kural SESSİZCE atlanır ve ilgili
`*_UNKNOWN` kodu `limitations`a düşülür — asla 0 varsayılmaz.
`data_quality = UNAVAILABLE` ise hiçbir kural çalışmaz: öneri listesi
boş, `confidence`/`overall_risk` `null`, uyarılar
`ANALYSIS_UNAVAILABLE` + `LOW_DATA_QUALITY`.

## E. Determinizm garantileri

- **Decimal-only:** tüm yüzde matematiği `decimal.Decimal`; float
  girdi `FLOAT_REJECTED` ile reddedilir (Core'da float sabiti dahi
  yoktur); çıktı sabit-nokta string (2 hane).
- **Bilinmeyen → null:** hiçbir katman 0 türetmez; UI `null → "—"`.
- **Kararlı sıralama:** öneriler `(priority, instrument, ilk
  reason_code)` üçlüsüyle sıralanır, `recommendation_id` bu sıradan
  atanır; kod listeleri ad-sıralıdır; Export anahtar sırası sabit şema
  sırasıdır.
- **Değişmez çıktılar:** girdi zarfı hiçbir katmanda mutasyona
  uğramaz; Export derin-izole kopyalar döndürür (çıktıyı değiştirmek
  kaynağı değiştiremez); tekrar çağrılar arasında gizli durum yoktur.
- **`proposal_id`/`generated_at` sahipliği:** YALNIZ API kompozisyon
  sınırında üretilir; Core/Service/Export duvar saati okumaz, UUID
  üretmez (AST-testli yasak).
- Aynı girdi → aynı zarf; Export'ta bayt-özdeş JSON.

## F. Güvenlik modeli (Agent 07 doğrulanmış garantiler)

- **Salt-okunur:** Exchange yok · emir/yürütme yok · broker SDK yok ·
  dosya sistemi yazımı yok (gerçek varsayılan zincir dahil,
  `open`-nöbetçili testle) · geçici dosya yok · `append_snapshot` /
  Workspace / Timeline yazımı yok · thread/subprocess yok · ağ
  istemcisi yok (AST-yasak import listesi).
- **Yürütme yüzeyi yok:** tüm uçlar YALNIZ GET (diğer metodlar 405);
  UI'da form/giriş/buton yok; şemada emir/miktar/fiyat alanı yok;
  gizli rota yok (fuzz-testli).
- **Sterile hatalar:** yanıtlarda istisna metni, dosya yolu, modül
  adı, traceback ve secret DEĞERİ bulunmaz; Core/Service/Export
  hataları yalnız koddur.
- **Katman izolasyonu:** bağımlılık yönü tek yönlü ve AST-kanıtlıdır;
  Service/API'de aritmetik yasaktır.
- **Agent 07 düzeltmesi:** varsayılan sağlayıcı zincirinin risk özeti
  okuma yolu `persist=False` ile bağlandı — strateji/portföy GET
  istekleri `risk_history.jsonl` snapshot eki dahil hiçbir yazım
  tetiklemez (analiz çıktısı değişmedi; Agent 08 ayrıca doğruladı).

## G. API

Tüm uçlar **yalnız GET**; mevcut global oturum kapısının arkasında
(girişsiz `401`); yanıtlar `Cache-Control: no-store, private` +
`nosniff` taşır.

| Uç | Amaç |
|---|---|
| `GET /api/strategy/intelligence` · `GET /api/v1/strategy/intelligence` | StrategyProposal (JSON): 13 şema alanı + sterile `sources` meta. Girdi yok — `proposal_id` (uuid4 hex) ve `generated_at` (UTC ISO) YALNIZ burada eklenir. `OK/PARTIAL/UNAVAILABLE` üçü de HTTP `200` döner (kalite zarfın içindedir). Beklenmedik hata → `500 STRATEGY_ANALYSIS_ERROR` (sterile). |
| `GET /strategy-intelligence` | UI sayfası (oturum yönlendirmeli). |

### Hata modeli (sterile — yalnız kod)

| Kod | Katman | Anlam |
|---|---|---|
| `FLOAT_REJECTED` | Core | Yüzde/para alanında float girdi reddedildi |
| `INVALID_INPUT` | Core | Girdi şeması/sürümü geçersiz |
| `PROVIDER_FAILED` | Service | Sağlayıcı istisna fırlattı → dürüst UNAVAILABLE öneri |
| `INVALID_PROVIDER_RESULT` | Service | Sağlayıcı sonucu şekilsiz |
| `INVALID_ANALYSIS` | Service | Analiz zarfı çekirdekçe reddedildi → sterile düşüş |
| `UNKNOWN_PROVIDER` | Service | Tanımsız sağlayıcı adı (`ValueError`) |
| `STRATEGY_ANALYSIS_ERROR` | API | Beklenmedik istisna → HTTP 500 |
| `INVALID_FORMAT` | Export | json dışı format |
| `PROPOSAL_UNAVAILABLE` | Export | Öneri yok/şekilsiz/eksik zorunlu alan |

## H. UI (`/strategy-intelligence`)

- Sunucu statik kabuk render eder; tarayıcı YALNIZ
  `/api/v1/strategy/intelligence` uç noktasını çeker (tek fetch).
- Salt-okunur panel: genel değerlendirme kartları
  (güven/kalite/rejim/risk), öneri tablosu, uyarı/sınırlama listeleri.
- `null → "Unknown"`; boş öneri listesi → "No recommendations.";
  PARTIAL → "Partial data available" bandı; UNAVAILABLE → "Strategy
  unavailable" bandı.
- İstemci hesabı yok (toFixed/parseFloat/Math/sort yok); yalnız
  `textContent` (innerHTML yok → XSS güvenli); form/buton/giriş alanı
  yok — yürütme kontrolü yoktur.

## I. Export (`strategy_export.py`)

- **Kaynak tektir:** API-uyumlu StrategyProposal. Export hesap yapmaz,
  öneri üretmez, zaman damgası/UUID üretmez.
- **`export_strategy_dict(proposal)`:** tam 13 üst alan + öneri başına
  tam 11 alanlık sabit-şema projeksiyonu; fazla alan (ör. `sources`)
  düşer; `proposal_id`/`generated_at` yoksa dürüstçe `null` taşınır
  (alan asla atlanmaz); başka zorunlu alan eksikse sterile
  `PROPOSAL_UNAVAILABLE`. Dönen yapı derin-izole kopyadır.
- **`export_strategy_json(proposal)`:** UTF-8 baytlar, sabit şema
  anahtar sırası, `indent=2`, Türkçe karakterler kaçışsız; aynı girdi
  → bayt-özdeş çıktı.
- **`serialize_strategy(proposal, fmt="json")`:** 1600/1700 kalıbı
  `(zarf, gövde, mime, dosya adı)` dörtlüsü; `FORMATS = ("json",)`;
  dosya adı `strategy_intelligence.json`; geçersiz format →
  `INVALID_FORMAT` sterile zarfı. Yalnız bellek içi üretim.

## J. Test stratejisi ve regresyon sonuçları

| Agent | Kapsam | Test dosyası | Test |
|---|---|---|---|
| 02 Core | kural motoru, Decimal, şema | `tests/test_mission1800_strategy_core.py` | 45 |
| 03 Service | izolasyon, bayat→PARTIAL, sterile meta | `tests/test_mission1800_strategy_service.py` | 28 |
| 04 API | uçlar, meta sınırı, sterile 500 | `tests/test_mission1800_strategy_api.py` | 23 |
| 05 UI | render yüzeyi, null→Unknown, fetch | `tests/test_mission1800_strategy_ui.py` | 24 |
| 06 Export | sabit şema, bayt-özdeşlik, izolasyon | `tests/test_mission1800_strategy_export.py` | 29 |
| 07 Güvenlik | AST denetimleri, penetrasyon, salt-okunurluk | `tests/test_mission1800_strategy_security.py` | 58 |
| 08 Regresyon | katmanlar-arası bütünleştirme, 1700 uyumu | `tests/test_mission1800_full_regression.py` | 39 |

Toplam yeni Mission 1800 testi: **246** · Agent 08 kapanış regresyonu:
**1581 PASS / 0 FAIL / 0 SKIP** · Exchange Write 0 · Secret Exposure 0.

## Bilinen sınırlamalar

- `market_regime` daima `"UNKNOWN"` (rejim tespiti uygulanmadı);
  `NO_FORECAST` — tahmin motoru yoktur. Her zarf bunları `limitations`
  ile beyan eder.
- Öneriler sabit eşikli kural çıktısıdır; piyasa verisi, tarihsel
  performans veya öğrenme kullanılmaz.
- UI durum geçişleri statik şablon analiziyle test edilir (JS
  çalıştıran tarayıcı testi altyapıda yok).

İlgili: `docs/architecture/strategy_intelligence.md` (Agent 01 mimari
sözleşmesi) · `docs/portfolio_intelligence.md` ·
`docs/API_REFERENCE.md` · `docs/MISSION_INDEX.md`
