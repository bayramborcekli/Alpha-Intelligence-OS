# Portfolio Intelligence (Mission 1700)

> Resmî dokümantasyon — Agents 01–08 tarafından teslim edilen sistemi
> OLDUĞU GİBİ tanımlar. Gelecek çalışma, uygulanmış gibi anlatılmaz.

## 1. Amaç

Mission 1700, PAPER hesabın portföy durumunu **salt-okunur ve tavsiye
niteliğinde** analiz eden katmanlı bir sistem ekler: özkaynak, pozisyon
dağılımı, maruziyet, yoğunlaşma (çeşitlendirme), risk-limit kullanımı ve
0–100 portföy sağlık skoru. Hiçbir katman emir vermez, Exchange'e
yazmaz, Workspace/Timeline'a dokunmaz.

## 2. Mimari ve katman sorumlulukları

```
sağlayıcılar (equity / positions / risk)   ← bağımlılık enjeksiyonu
        │
        ▼
portfolio_service.py   — TOPLAMA: sağlayıcı izolasyonu, sterile kaynak
        │                meta verisi, saf eşleyiciler; matematik YOK
        ▼
portfolio_intelligence.py — HESAP (Core): saf stdlib (decimal/typing),
        │                Decimal-only, deterministik analiz
        ▼
app.py (API)           — YÖNLENDİRME: GET uçları, kimlik kapısı,
        │                generated_at kompozisyon sınırı; hesap YOK
        ├──────────────► templates/portfolio_intelligence.html (UI)
        │                — RENDER: yalnız API tüketir, istemci hesabı YOK
        ▼
portfolio_export.py    — SERİLEŞTİRME: JSON/CSV, bellek içi; zarfı
                         üretmez ve DEĞİŞTİRMEZ
```

- Ters/dairesel bağımlılık yok: Core hiçbir mission modülünü import
  etmez; Service yalnız Core'u; Export yalnızca `csv/io/json` kullanır.
- Katman atlaması yok: UI yalnız API'yi, Export yalnız mevcut zarfı
  tüketir (alternatif veri yolu yok).

## 3. Veri modeli — PortfolioAnalysis zarfı

`analyze_portfolio(inputs)` (Core) ve `get_portfolio_analysis(providers,
generated_at)` (Service) şu zarfı üretir (`analysis_version: 1`):

```json
{
  "ok": true, "read_only": true, "advisory_only": true,
  "analysis_version": 1,
  "status": "OK | PARTIAL | UNAVAILABLE",
  "generated_at": "<yalnız API sınırında üretilir>",
  "sources": {"equity|positions|risk":
      {"status", "freshness", "available", "code"}},
  "portfolio": {
    "equity": {"nav_usdt", "cash_usdt", "realized_pnl",
               "unrealized_pnl", "total_fees"},
    "positions": [{"symbol", "side", "quantity", "entry_price",
                   "mark_price", "leverage", "notional",
                   "weight_pct", "unrealized_pnl"}],
    "allocation": {"assets": [{"symbol", "notional", "weight_pct"}],
                   "cash_weight_pct", "unallocated_or_unknown_pct"},
    "exposure": {"gross", "net", "long", "short",
                 "gross_pct", "net_pct", "unknown_positions"},
    "concentration": {"hhi", "top_symbol", "top_share_pct",
                      "effective_positions"},
    "performance": {"realized_pnl", "unrealized_pnl", "total_fees",
                    "drawdown_pct", "forecast": null},
    "risk_utilization": {"net_exposure_util_pct", "drawdown_util_pct",
                         "concentration_util_pct", "limits_breached"},
    "health": {"portfolio_health_score",
               "components": [{"code", "score", "weight"}]}
  }
}
```

- Tüm sayılar **sabit-nokta string**tir (para 8 hane, yüzdeler 2 hane);
  zarfın hiçbir yerinde `float` bulunmaz.
- Bilinmeyen değer → `null`; **asla 0 türetilmez**.
- `performance.forecast` bu sürümde daima `null`dur (tahmin motoru
  uygulanmadı — bilinen sınırlama).

### Kaynak normalizasyonu (saf eşleyiciler)

- `map_account_to_equity`: `usdt_margin_balance → nav_usdt`,
  `usdt_available_balance → cash_usdt`; hesaptan türetilemeyen
  `realized_pnl/total_fees` dürüstçe `null`.
- `map_positions`: FLAT/sıfır miktar atlanır; miktar mutlak değer.
- `map_risk_view`: eşikler Risk Engine'in `thresholds()` çıktısından
  (`HIGH_EXPOSURE_PERCENT`, `|DRAWDOWN_WARN_PERCENT|`,
  `POSITION_CRITICAL_PERCENT`).

### Risk Engine sınırı

Risk Engine **okuma otoritesidir**: yalnız `risk_api.thresholds()` ve
`risk_api.summary(persist=False)` çağrılır. `persist=False`, Risk
Engine'in günlük `risk_history.jsonl` snapshot ekini bu yol için
kapatır — portföy istekleri hiçbir dosya yazımı tetiklemez (Agent 07
doğrulanmış düzeltmesi). Risk Engine durumu hiçbir biçimde değiştirilmez.

## 4. API

Tüm uçlar **yalnız GET** (HEAD/OPTIONS otomatik); mevcut global oturum
kapısının arkasındadır (girişsiz `401`); yanıtlar
`Cache-Control: no-store, private` + `nosniff` taşır.

| Uç | Amaç |
|---|---|
| `GET /api/portfolio/intelligence` · `GET /api/v1/portfolio/intelligence` | PortfolioAnalysis zarfı (JSON). Girdi yok — `generated_at` istemciden ALINMAZ, API sınırında UTC ISO üretilir. `OK/PARTIAL/UNAVAILABLE` üçü de HTTP `200` döner (durum zarfın içindedir). Beklenmedik hata → `500 PORTFOLIO_ANALYSIS_ERROR` (sterile). |
| `GET /api/portfolio/intelligence/export/json` · `/api/v1/...` | Zarfın deterministik JSON baytları; `Content-Disposition: attachment; filename="portfolio_intelligence.json"`. |
| `GET /api/portfolio/intelligence/export/csv` · `/api/v1/...` | Düzleştirilmiş CSV raporu; `portfolio_intelligence.csv`. |
| `GET /portfolio-intelligence` | UI sayfası (oturum yönlendirmeli). |

### Hata modeli (sterile — yalnız kod, asla istisna metni/yol/traceback)

| Kod | Katman | Anlam |
|---|---|---|
| `FLOAT_REJECTED` | Core | Para alanında float girdi reddedildi |
| `INVALID_INPUT` | Core | Girdi şeması geçersiz |
| `PROVIDER_FAILED` | Service | Sağlayıcı istisna fırlattı (yalnız o kaynak düşer) |
| `INVALID_PROVIDER_RESULT` | Service | Sağlayıcı sonucu şekilsiz/geçersiz |
| `UNKNOWN_PROVIDER` | Service | Tanımsız sağlayıcı adı (`ValueError`) |
| `PORTFOLIO_ANALYSIS_ERROR` | API | Beklenmedik istisna → HTTP 500 |
| `INVALID_FORMAT` | Export | json/csv dışı format |
| `ANALYSIS_UNAVAILABLE` | Export | Zarf yok/şekilsiz |

## 5. Export

- **Kaynak tektir:** mevcut PortfolioAnalysis zarfı. Export hesap
  yapmaz, zarfı değiştirmez (mutasyonsuzluk testli), zaman damgası
  üretmez (`generated_at` zarftan taşınır).
- **JSON:** zarfın deterministik bayt temsili (sabit anahtar sırası,
  UTF-8, girintili); alan adları/null/sabit-nokta string'ler aynen.
- **CSV:** başlık `section,field,value`; bölüm sırası sabit:
  `meta → summary → positions → risk → diversification → sources`
  (pozisyonlar `1.symbol` gibi indeksli, zarf sırasında; kaynaklar
  ad-sıralı). Bilinmeyen → **boş hücre** (asla 0). UTF-8 BOM + CRLF
  (Türkçe Excel uyumu); formül enjeksiyonu nötralize (`= + - @`
  öneki sayı değilse `'` ile etkisizleştirilir).
- Yalnız bellek içi üretim: dosya sistemi yazımı ve geçici dosya yok.

## 6. UI (`/portfolio-intelligence`)

- Sunucu statik kabuk render eder; tarayıcı YALNIZ
  `/api/v1/portfolio/intelligence` uç noktasını çeker.
- 5 görsel durum: **Yükleniyor** · **SAĞLIKLI** (OK) · **KISMİ**
  (PARTIAL + uyarı bandı) · **KULLANILAMAZ** (UNAVAILABLE + kırmızı
  bant; önce TÜM değerler temizlenir, portföy değerleri hiç render
  edilmez) · **HATA** (API hatası; tüm tablolar temizlenir).
- `null → "—"`; istemci hesabı yok (toFixed/parseFloat/Number yok);
  yalnız `textContent` (innerHTML yok → XSS güvenli); form/buton/giriş
  alanı yok (yenileme, mevcut global ⟳ butonuna bağlanır).

## 7. Güvenlik modeli (Agent 07 doğrulanmış garantiler)

- Exchange yok · emir/yürütme yok · `append_snapshot` kullanımı yok ·
  Workspace/Timeline yazımı yok · dosya sistemi yazımı yok (gerçek
  sağlayıcı zinciri dahil, `open`-nöbetçili testle) · geçici dosya yok ·
  ağ istemcisi yok (`requests/websocket/socket` import'u AST-yasak) ·
  dinamik yürütme yok (`eval/exec/compile/__import__` AST-yasak) ·
  payload mutasyonu yok.
- Sterile hatalar: yanıtlarda istisna metni, dosya yolu, sağlayıcı içi
  ayrıntı, Risk Engine/Exchange içi bilgisi ve secret DEĞERİ bulunmaz.
- Kimlik sınırı: tüm sayfa/API uçları mevcut oturum kapısının
  arkasında; sahte cookie/Bearer/XFF ile bypass 401 ile reddedilir.

## 8. Determinizm garantileri

- **Decimal-only:** Core tüm parayı `decimal.Decimal` ile işler; float
  girdi `FLOAT_REJECTED` ile reddedilir; çıktı sabit-nokta string.
- **Kararlı sıralama:** pozisyonlar zarf sırası, kaynaklar ad-sıralı,
  JSON anahtarları sabit sıralı, CSV satır planı sabittir.
- **Null koruması:** bilinmeyen `null` kalır (CSV'de boş hücre); sıfır
  türetimi yasaktır.
- **generated_at sınırı:** zaman damgası YALNIZ API kompozisyon
  sınırında üretilir; Core/Service/Export duvar saati okumaz
  (random/uuid/gizli zaman damgası yok).
- **Bayt-özdeşlik:** aynı zarf → bayt-özdeş JSON ve CSV; aynı
  sağlayıcı verisi + aynı `generated_at` → özdeş zarf.

## 9. Operasyonel notlar

- **Sağlayıcı enjeksiyonu:** `build_default_providers()` üç sağlayıcıyı
  (`equity`, `positions`, `risk`) tembel import'la bağlar
  (`intelligence_service` anlık görüntüsü + `risk_api`). Test/alternatif
  ortamlar kendi `{"freshness","data"}` sözleşmeli çağrılabilirlerini
  enjekte edebilir.
- **Tazelik:** sağlayıcı sonucu anlık görünümdür → `fresh`; hata →
  `unavailable` olarak işaretlenir.
- **PARTIAL:** en az bir kaynak sağlıklı, en az biri düşük — düşen
  kaynağın alanları `null`, kalanlar geçerlidir; HTTP yine 200.
- **UNAVAILABLE:** hiçbir kaynak kullanılamıyor — tüm portföy alanları
  `null`; UI sağlıklı görünüme asla düşmez.
- **Performans:** her sağlayıcı analiz başına TAM 1 kez çağrılır; çift
  hesap yok. Ölçülen sınırlar: 50 analiz < 5 sn, 100 export < 5 sn
  (Agent 08 üst-sınır testleri).

## 10. Bilinen sınırlamalar

- `performance.forecast` daima `null` (tahmin motoru yok).
- `realized_pnl`/`total_fees` hesap anlık görüntüsünden türetilemez →
  varsayılan sağlayıcılarla `null` (dürüstlük ilkesi).
- UI durum geçişleri statik şablon analiziyle test edilir (JS çalıştıran
  tarayıcı testi altyapıda yok); dal-yapısı testleri bu boşluğu kapatır.
- CSV, rapor amaçlı düzleştirmedir; kayıpsız temsil JSON export'tur.

## 11. Test stratejisi ve regresyon sonuçları

| Agent | Kapsam | Test dosyası | Test |
|---|---|---|---|
| 02 Core | saf hesap, Decimal, zarf | `tests/test_mission1700_portfolio_core.py` | 27 |
| 03 Service | izolasyon, eşleyiciler, sterile meta | `tests/test_mission1700_portfolio_service.py` | 23 |
| 04 API | uçlar, kimlik, sterile 500 | `tests/test_mission1700_portfolio_api.py` | 17 |
| 05 UI | 5 durum, null→"—", fetch yüzeyi | `tests/test_mission1700_portfolio_ui.py` | 20 |
| 06 Export | JSON/CSV determinizm, sterile hatalar | `tests/test_mission1700_portfolio_export.py` | 21 |
| 07 Güvenlik | AST denetimleri, penetrasyon, salt-okunurluk | `tests/test_mission1700_security_verification.py` | 50 |
| 08 Regresyon | katmanlar-arası bütünleştirme | `tests/test_mission1700_full_regression.py` | 15 |

Toplam yeni Mission 1700 testi: **173** · Agent 08 kapanış regresyonu:
**1321 PASS / 0 FAIL / 0 SKIP** · Exchange Write 0 · Secret Exposure 0.

İlgili: `docs/API_REFERENCE.md` · `docs/MISSION_INDEX.md` ·
`docs/automation.md`
