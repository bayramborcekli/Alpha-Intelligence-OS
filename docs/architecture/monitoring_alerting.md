# Monitoring & Alerting — Mimari (Mission 1900, Agent 01)

> DURUM: KABUL EDİLMİŞ MİMARİ — uygulama Agents 02+ tarafından yapılır.
> Bu belge sözleşmedir; katmanlar bu belgeye uymak zorundadır.
> Mission 1800 (Strategy Intelligence) resmen kapalıdır ve
> DEĞİŞTİRİLMEZ; StrategyProposal'ın tek otoritesi olarak kalır.

## 1. Amaç ve konum

Monitoring & Alerting, Strategy Intelligence çıktısını (StrategyProposal,
`strategy_version: 1`) **gözlemler**: strateji kalitesini zaman içinde
değerlendirir, öneri sonuçlarını izler, bozulmayı tespit eder ve
anormal davranış için uyarı üretir. Strateji ÜRETMEZ, işlem YÜRÜTMEZ,
borsayla ETKİLEŞMEZ. Mission tamamen salt-okunurdur.

```
Portfolio Intelligence         (Mission 1700 — kapalı, değişmez)
        │  PortfolioAnalysis (analysis_version: 1)
        ▼
Strategy Intelligence          (Mission 1800 — kapalı, değişmez)
        │  StrategyProposal (strategy_version: 1)
        ▼
Monitoring Engine              (Mission 1900 — gözlem/kalite hesabı)
        │  MonitoringReport (immutable)
        ▼
Alert Engine                   (Mission 1900 — kural-bazlı uyarı)
        │  AlertReport (immutable)
        ▼
Monitoring API                 (yalnız GET, taşıma)
        │
        ▼
Monitoring UI                  (yalnız sunum)
```

Mutlak akış kuralları:
- **Geri besleme YOK:** Monitoring hiçbir veriyi Strategy Core'a (ya da
  Portfolio/Risk katmanlarına) geri beslemez; Strategy katmanları
  Monitoring'den habersizdir. Ok tek yönlüdür.
- **Dairesel bağımlılık YOK:** Monitoring Core hiçbir mission modülünü
  import etmez; Alert Engine yalnız MonitoringReport tüketir; 1700/1800
  modülleri Monitoring modüllerini import edemez (AST testiyle
  kilitlenir).
- **Bildirim dışa çıkmaz:** e-posta yok · SMS yok · webhook yok ·
  broker etkileşimi yok. Uyarılar yalnız uygulama içi API/UI yüzeyinde
  görünür.

## 2. Katman sahipliği ve sorumluluklar

| Katman | Modül (planlanan) | Sorumluluk | YAPMAZ |
|---|---|---|---|
| **Monitoring Core** (Engine) | `monitoring_intelligence.py` | Saf hesap: StrategyProposal (+ gözlem penceresi girdisi) → MonitoringReport. Yalnız stdlib (`decimal`, `typing`). Kalite metrikleri (başarı oranı, ortalama getiri, maksimum düşüş, güven doğruluğu), bozulma tespiti, sağlık durumu. | I/O, saat okuma, kimlik üretme, sağlayıcı çağrısı, uyarı üretimi |
| **Alert Engine** | `alert_engine.py` | Saf hesap: MonitoringReport → AlertReport. Kapalı kod listeli, eşik-bazlı deterministik uyarı kuralları. | I/O, bildirim gönderme, monitoring metriği hesaplama |
| **Monitoring Service** | `monitoring_service.py` | Sağlayıcı orkestrasyonu: strateji önerisi sağlayıcısını (DI ile `strategy_service` üzerinden, salt-okunur zincir `persist=False`) izole çağırır; sterile `sources` meta; bayat girdi → PARTIAL düşürmesi; Core + Alert Engine'i sırayla besler. Matematik yok. | Hesap, HTTP, dosya, saat |
| **Monitoring API** | `app.py` GET uçları | Yalnız taşıma: kimlik kapısı; `report_id`/`observed_at` YALNIZ burada üretilir; sterile 500 (`MONITORING_ANALYSIS_ERROR`); `no-store` + `nosniff`. | Hesap, iş kuralı, serileştirme mantığı |
| **Monitoring UI** | `templates/monitoring.html` | Yalnız sunum: tek fetch (v1 ucu), `textContent`-only, istemci hesabı yok, `null → "Unknown"`, kontrol/form yok. | API dışı veri yolu, hesap, yürütme kontrolü |
| **Monitoring Export** | `monitoring_export.py` | Yalnız serileştirme: sabit-şema projeksiyon + deterministik JSON (1600/1700/1800 kalıbı: `(zarf, gövde, mime, dosya adı)`). Raporu ÜRETMEZ ve DEĞİŞTİRMEZ. | Hesap, zaman damgası/kimlik üretimi, dosya yazımı |

Katman atlaması yok: UI yalnız API'yi, Export yalnız API-uyumlu raporu,
Alert Engine yalnız MonitoringReport'u görür.

## 3. MonitoringReport şeması (sürümlü, immutable)

`monitoring_version: 1`. Üretim sonrası ASLA mutasyona uğramaz; tüm
sayılar sabit-nokta string; **bilinmeyen → `null` (asla 0)**.

```json
{
  "monitoring_version": 1,
  "report_id": "<yalnız API sınırında, uuid4 hex>",
  "observed_at": "<yalnız API sınırında, UTC ISO>",
  "strategy_version": 1,
  "analysis_version": 1,
  "observation_window": {"kind": "SNAPSHOT | ...", "samples": "int | null"},
  "data_quality": "OK | PARTIAL | UNAVAILABLE",
  "recommendation_count": "int | null",
  "evaluated_count": "int | null",
  "success_rate": "sabit-nokta % | null",
  "average_return": "sabit-nokta % | null",
  "maximum_drawdown": "sabit-nokta % | null",
  "confidence_accuracy": "sabit-nokta % | null",
  "market_regime": "UNKNOWN",
  "health_status": "HEALTHY | DEGRADED | CRITICAL | UNKNOWN",
  "alerts": ["<AlertReport öğeleri — bkz. §4>"],
  "limitations": ["kapalı kod listesi, ad-sıralı"]
}
```

Şema kuralları:
- **Dürüst bilinmezlik:** kalıcılık ve tarih deposu OLMADIĞI için
  (bkz. §6) tarihsel metrikler (`success_rate`, `average_return`,
  `maximum_drawdown`, `confidence_accuracy`) sağlayıcı bu veriyi
  sunmadıkça `null` kalır ve ilgili `*_UNKNOWN` sınırlama kodu düşülür
  — asla uydurulmaz, asla 0 varsayılmaz. v1'de varsayılan gözlem
  penceresi tek anlık görüntüdür (`SNAPSHOT`).
- `market_regime`, tüketilen StrategyProposal'dan aynen taşınır (v1'de
  `"UNKNOWN"`).
- `health_status` deterministik türetilir: girdi kalitesi + uyarı
  şiddetlerinden (CRITICAL uyarı → `CRITICAL`; girdi UNAVAILABLE →
  `UNKNOWN`).
- `limitations` yalnız kapalı listeden kodlardır (ör. `NO_OUTCOME_DATA`,
  `NO_HISTORY`, `SINGLE_SNAPSHOT_WINDOW`, `STRATEGY_UNAVAILABLE` —
  kesin liste Agent 02'de sabitlenir ve dokümante edilir).
- **Kalite taşıyıcısı:** genel durum (OK/PARTIAL/UNAVAILABLE) rapor
  gövdesindeki `data_quality` alanında taşınır (1800 kalıbı — zarf/meta
  içinde değil); girdi StrategyProposal `data_quality` değerinden ve
  sağlayıcı tazeliğinden türetilir. HTTP üçünde de 200 döner.
- `report_id`/`observed_at` deterministik çekirdeğin DIŞINDA, yalnız
  API kompozisyon sınırında eklenir (1800 `proposal_id` kalıbı).
- **Kod listeleri erken donar:** `limitations`, uyarı `code`,
  `trigger_reason` kapalı listeleri Agent 02/03'te TEK yerde (Core /
  Alert Engine sabitleri) tanımlanır ve tüm katman/testler o sabitleri
  referans alır — katmanlar arası liste kopyası yasaktır.

## 4. Alert modeli (kapalı, sterile, yürütmesiz)

Alert Engine, MonitoringReport'u eşik-bazlı deterministik kurallarla
değerlendirir ve uyarı listesi üretir. Uyarı alanları (sabit sıra):

| Alan | Kural |
|---|---|
| `alert_id` | Kararlı sıralama SONRASI atanan `A1, A2, …` (sayaç; UUID/rastgelelik yok) |
| `severity` | Kapalı liste: `INFO` \| `WARNING` \| `CRITICAL` |
| `code` | Kapalı kod listesi (serbest metin yok) — ör. `STRATEGY_UNAVAILABLE`, `DATA_QUALITY_DEGRADED`, `RISK_LIMIT_BREACHED_OBSERVED`, `CONFIDENCE_LOW`, `DEGRADATION_DETECTED` (kesin liste Agent 02/03'te sabitlenir) |
| `title` | Koddan türetilen SABİT şablon metni (girdi verisi enjekte edilmez) |
| `description` | Sabit şablon; sayısal bağlam yalnız sabit-nokta string olarak |
| `affected_component` | Kapalı liste: `STRATEGY` \| `PORTFOLIO` \| `DATA_QUALITY` \| `MONITORING` |
| `trigger_reason` | Kapalı kod (hangi eşik/koşul tetikledi) |
| `recommended_action` | Kapalı liste: `REVIEW` \| `ACKNOWLEDGE` \| `NO_ACTION` — **yürütme/işlem talimatı ASLA değil** (emir, miktar, fiyat, AL/SAT alanı şemada bulunamaz; testle yasaklanır) |

Uyarılar `(severity düzeyi, code)` ile kararlı sıralanır; `alert_id`
bu sıradan atanır. Aynı MonitoringReport → bayt-özdeş uyarı listesi.

## 5. Determinizm garantileri

Decimal-only aritmetik (float → `FLOAT_REJECTED`) · sabit-nokta string
çıktı · bilinmeyen → `null` · kararlı sıralama + sayaç kimlikleri ·
immutable çıktılar (Export derin-izole kopya) · sürümlü şema
(`monitoring_version`) · deterministik serileştirme (sabit anahtar
sırası; aynı rapor → bayt-özdeş JSON) · duvar saati/UUID yalnız API
sınırında (Core/Service/Export'ta AST-yasak) · gizli mutable durum yok
(tekrar çağrılar bağımsız).

Hata modeli (1700/1800 kalıbı, sterile — yalnız kod): Core
`INVALID_INPUT`/`FLOAT_REJECTED`; Service `PROVIDER_FAILED`/
`INVALID_PROVIDER_RESULT`/`INVALID_PROPOSAL`/`UNKNOWN_PROVIDER`; API
`MONITORING_ANALYSIS_ERROR` (500); Export `INVALID_FORMAT`/
`REPORT_UNAVAILABLE`.

## 6. Güvenlik garantileri (mimari zorunluluk, testle kilitlenecek)

Mission ASLA: işlem yürütmez · emir üretmez · borsaya bağlanmaz ·
snapshot yazmaz · dosya kalıcılığı yapmaz (geçici dosya dahil) · broker
SDK kullanmaz · thread oluşturmaz · soket açmaz · arka plan
zamanlaması yapmaz. "Sürekli gözlem", istek-anında değerlendirme
olarak gerçeklenir: her GET, o anki zinciri okur ve raporu bellekte
üretir — scheduler/daemon YOKTUR (mevcut no-threads kuralıyla uyumlu).

Ek yasaklar: dışa bildirim yok (e-posta/SMS/webhook/push) · ağ
istemcisi yok (`requests/websocket/socket` AST-yasak) · dinamik
yürütme yok (`eval/exec/compile/__import__` AST-yasak) · sağlayıcı
sızıntısı yok (sterile `sources` meta; istisna metni/yol/traceback
yanıtlara asla çıkmaz) · secret erişimi yok · varsayılan sağlayıcı
zinciri uçtan uca salt-okunur (`persist=False` yolu korunur, 1800
Agent 07 dersi). Kimlik sınırı mevcut oturum kapısıdır (değişmez).

## 7. API ve UI yüzeyi (planlanan)

| Uç | Amaç |
|---|---|
| `GET /api/monitoring/report` · `GET /api/v1/monitoring/report` | MonitoringReport (uyarılar gömülü); OK/PARTIAL/UNAVAILABLE kalitelerinin üçü de HTTP 200; sterile 500 |
| `GET /monitoring` | Salt-okunur UI sayfası (oturum yönlendirmeli) |

Rota adında "intelligence" geçerse `EXPECTED_INTEL_ROUTES` regresyon
listesine eklenir (mevcut kural). Export ucu Agent 06 kapsamında aynı
kalıpla eklenir (`monitoring_report.json`).

## 8. Gelecek uyumluluğu (Mission 2000 — Execution Foundation)

MonitoringReport + AlertReport, Mission 2000'in **salt-okunur girdi
sözleşmesidir**; Monitoring mimarisi değişmeden tüketilebilir:

- Sürümlü şema: `monitoring_version` artar, eski alan sözleşmeleri
  korunur; tüketiciler bilinmeyen kodları yok sayar.
- `report_id` ile tekil referans; kapalı kod listeleri genişletilebilir.
- Monitoring TAVSİYE niteliğinde kalır: Execution Foundation kendi
  ayrı katmanında insan-onay kapısıyla kurulur; Monitoring hiçbir zaman
  yürütme kararı vermez, `recommended_action` kapalı listesi
  (`REVIEW/ACKNOWLEDGE/NO_ACTION`) emir semantiği kazanmaz.

## 9. Uygulama ajan planı (1700/1800 emsali)

02 Monitoring Core → 03 Alert Engine → 04 Service → 05 API → 06 UI →
07 Export → 08 Güvenlik → 09 Tam regresyon → 10 Dokümantasyon →
11 Kapanış (ya da 1800 kalıbında birleşik 02–10 dökümü — kesin bölme
Executive onayıyla). Her ajan: testler + mimari inceleme + tam
regresyon (`alpha20_v1/` önce geri alınır) + kapsamlı commit + push.

İlgili: `docs/mission1800_strategy_intelligence.md` ·
`docs/architecture/strategy_intelligence.md` ·
`docs/portfolio_intelligence.md` · `docs/MISSION_INDEX.md`
