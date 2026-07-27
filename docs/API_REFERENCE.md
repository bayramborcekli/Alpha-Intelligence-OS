# API Referansı (salt-okunur yüzey)

Tüm veri API'leri **yalnızca GET** metodu sunar; kimlik doğrulama
zorunludur (anonim → 401) ve yanıtlar `Cache-Control: no-store, private`
taşır. Yazma metodu (POST/PUT/PATCH/DELETE) → 405. Hata yanıtları
sterilizedir: sabit kod + Türkçe mesaj, stack trace/secret asla yok.

## Intelligence (Mission 1500.1)

Bayrak: `ALPHA_INTELLIGENCE_ENABLED=true` gerektirir; kapalı/tanımsızken
tüm uçlar güvenli `{"ok":true,"enabled":false,"status":"UNAVAILABLE"}`
yanıtı verir (settings hariç — o her zaman etkili yapılandırmayı gösterir).
Her ucun `/api/v1/...` takma adı vardır.

| Uç | İçerik |
|---|---|
| `GET /api/intelligence` · `/api/intelligence/summary` | Birleşik özet: `portfolio_summary`, `risk_summary`, `insights`, `recommendations`, `risk_explanations`, `freshness`, `status`, `partial`, `source_errors`, `generated_at` |
| `GET /api/intelligence/insights` | `{ok, enabled, advisory_only, items:[insight]}` |
| `GET /api/intelligence/recommendations` | Öncelik sıralı tavsiye listesi (aynı zarf) |
| `GET /api/intelligence/status` | Hafif durum: `status` (OK/PARTIAL/STALE/UNAVAILABLE), `sources[]`, `partial` |
| `GET /api/intelligence/settings` | Etkili (doğrulanmış) yapılandırma + `validation_warnings` — ham ortam değeri asla dönmez |

### Sözleşme notları
- Para değerleri **Decimal-string** olarak serileştirilir (asla float).
- Bilinmeyen değer `null` döner; hiçbir zaman 0 ile doldurulmaz.
- `confidence`: `HIGH | MEDIUM | LOW | INSUFFICIENT_DATA` — tazelik kanıtı
  olmayan kaynaktan HIGH üretilmez.
- Her insight: Observation / Reason / Impact / Recommendation / Confidence /
  Evidence (kaynak+alan+değer) alanlarını taşır (explainable yapı).
- Tüm çıktılar `read_only:true` ve `advisory_only:true` işaretlidir;
  emir dili ve emir parametresi içermez.

## Intelligence Workspace (Mission 1500.2)

Feature flag'siz çalışır (geçmiş boşsa dürüst boş sonuç). Veri kaynağı
yalnızca `intelligence_workspace_service` → append-only
`intelligence_history.jsonl`'dir. Her ucun `/api/v1/...` takma adı vardır.
Geçersiz parametre → `400 INVALID_PARAMETER`; sağlayıcı hatası → `200`
+ sterile zarf; tüm yanıtlar `read_only`/`advisory_only` taşır.

| Uç | Parametreler / notlar |
|---|---|
| `GET /api/workspace/timeline` | `limit`, `offset` (negatif olmayan tamsayı) |
| `GET /api/workspace/snapshot/<id>` | pozitif tamsayı id; bulunamazsa `404 SNAPSHOT_NOT_FOUND` |
| `GET /api/workspace/compare` | `a`, `b` zorunlu pozitif tamsayı; derin deterministik fark (NEW/CHANGED/REMOVED, indeks tabanlı liste farkları, eksik taraf "Veri Yok"); bulunamayan id → 404 |
| `GET /api/workspace/recommendations` | kod bazlı tavsiye geçmişi (ardışık tekrarlar `count` ile birleşik, `confidence_changed`/`priority_changed`) |
| `GET /api/workspace/risk-evolution` | geçmiş risk skoru serisi; `forecast: null` — tahmin yok |
| `GET /api/workspace/search` | `start`/`date`, `end`/`date_end` (ISO), `status`, `confidence`, `recommendation`, `insight`, `partial`, `advisory_only` (yalnız `true`/`false`) |

### Workspace Export (`/api/workspace/export/*`)

Aynı altı yüzeyin dışa aktarımı: `timeline`, `snapshot/<id>`, `compare`,
`recommendations`, `risk-evolution`, `search` (+`/api/v1` takma adları).
`format=json` varsayılan; `json|csv` dışı → 400. JSON deterministiktir;
CSV düz metin hücrelidir (formül-enjeksiyon korumalı, UTF-8 BOM, CRLF,
yapısal değerler canonical JSON metnine düzleştirilir). Başlıklar:
`Content-Disposition: attachment`, doğru `Content-Type`,
`X-Content-Type-Options: nosniff`, `Cache-Control: no-store, private`.

### Workspace UI
`GET /workspace` — salt-okunur sayfa (login yönlendirmeli); yalnız
Workspace GET API'lerini kullanır; işlem düğmesi yoktur; yanıt
`no-store, private` taşır. Ayrıntı: `docs/RELEASE_NOTES_1500_2.md`.

## Automation API (Mission 1600, `/api/automation/*`)

Kontrollü otomatik Intelligence çalıştırma katmanı. Kimlik doğrulamalı;
API yanıtları (status/run/export) `Cache-Control: no-store, private`
taşır. Her ucun `/api/v1/...` takma adı vardır. Ayrıntılı sözleşme:
`docs/automation.md`.

| Uç | Notlar |
|---|---|
| `GET /api/automation/status` | Sterile durum görünümü: `enabled`, `interval_minutes`, `state`, `running`, `run_id`, `last_run_started_at`, `last_run_finished_at`, `last_run_status`, `last_error_code`, `last_snapshot_recorded`, `next_due`; bilinmeyen → `null` |
| `POST /api/automation/run` | Manuel tek koşu; CSRF zorunlu; `200 {ran, appended, error_code, final_state, run_id}` · `409 DUPLICATE_RUN` · `503 AUTOMATION_DISABLED` · `500 AUTOMATION_ERROR` (sterile) |
| `GET /api/automation/export/status` | `format=json\|csv` (varsayılan json; başka değer → `400 INVALID_FORMAT`); status ile birebir alan beyaz listesi; JSON deterministik, CSV formül-enjeksiyon korumalı (UTF-8 BOM, CRLF, `field,value`); `Content-Disposition: attachment` (statik ad), `nosniff`; kaynak okunamazsa `503 STATUS_UNAVAILABLE` |

Enable/disable ve history export uçları YOKTUR (ortam tabanlı
yapılandırma; çalışma geçmişi modeli yok — `docs/automation.md` §8).

### Automation UI
`GET /automation` — oturum yönlendirmeli sayfa; yalnız yukarıdaki status
ve run uçlarını kullanır; 30 sn status polling; yalnız "Şimdi Çalıştır" +
"Yenile" eylemleri.

## Portfolio Intelligence API (Mission 1700, `/api/portfolio/intelligence*`)

Salt-okunur, tavsiye niteliğinde portföy analizi. Kimlik doğrulamalı;
yanıtlar `Cache-Control: no-store, private` + `nosniff` taşır; tüm
uçlar YALNIZ GET ve her birinin `/api/v1/...` takma adı vardır.
Ayrıntılı sözleşme: `docs/portfolio_intelligence.md`.

| Uç | Notlar |
|---|---|
| `GET /api/portfolio/intelligence` | PortfolioAnalysis zarfı (`analysis_version: 1`): `status OK\|PARTIAL\|UNAVAILABLE` (üçü de HTTP 200), `sources` sterile meta, `portfolio{equity, positions, allocation, exposure, concentration, performance, risk_utilization, health}`; tüm sayılar sabit-nokta string, bilinmeyen → `null` (asla 0); `generated_at` yalnız API sınırında üretilir; beklenmedik hata → `500 PORTFOLIO_ANALYSIS_ERROR` (sterile) |
| `GET /api/portfolio/intelligence/export/json` | Zarfın deterministik JSON baytları; `Content-Disposition: attachment; filename="portfolio_intelligence.json"` |
| `GET /api/portfolio/intelligence/export/csv` | Düzleştirilmiş rapor: `section,field,value`; bölüm sırası `meta→summary→positions→risk→diversification→sources`; bilinmeyen → boş hücre; UTF-8 BOM + CRLF; formül-enjeksiyon korumalı; `portfolio_intelligence.csv` |

### Portfolio Intelligence UI
`GET /portfolio-intelligence` — oturum yönlendirmeli salt-okunur sayfa;
yalnız `/api/v1/portfolio/intelligence` ucunu kullanır; işlem/AL-SAT
kontrolü yoktur; 5 görsel durum (Yükleniyor/SAĞLIKLI/KISMİ/
KULLANILAMAZ/HATA); `null → "—"`.

## Strategy Intelligence API (Mission 1800, `/api/strategy/intelligence`)

Salt-okunur, tavsiye niteliğinde strateji önerisi. Kimlik doğrulamalı;
yanıtlar `Cache-Control: no-store, private` + `nosniff` taşır; uç
YALNIZ GET ve `/api/v1/...` takma adı vardır. Ayrıntılı sözleşme:
`docs/mission1800_strategy_intelligence.md`.

| Uç | Notlar |
|---|---|
| `GET /api/strategy/intelligence` | StrategyProposal (`strategy_version: 1`): 13 şema alanı + sterile `sources` meta; `advisory_only/read_only: true`; `data_quality OK\|PARTIAL\|UNAVAILABLE` (üçü de HTTP 200); öneriler kapalı kod listeleriyle açıklanır, emir/miktar/fiyat alanı YOK; tüm sayılar sabit-nokta string, bilinmeyen → `null`; `proposal_id` + `generated_at` yalnız API sınırında üretilir; beklenmedik hata → `500 STRATEGY_ANALYSIS_ERROR` (sterile) |

### Strategy Intelligence UI
`GET /strategy-intelligence` — oturum yönlendirmeli salt-okunur sayfa;
yalnız `/api/v1/strategy/intelligence` ucunu kullanır; işlem/yürütme
kontrolü yoktur; `null → "Unknown"`.

## Diğer salt-okunur yüzeyler (1400 serisi)
- `GET /api/risk/{summary,exposure,alerts,history,simulator}` — Risk Motoru
  (simülatörün POST varyantı yalnızca YEREL hesap yapar; borsaya istek yok)
- `GET /api/v1/executive/summary` — yönetici üst çubuğu
- Pano/portföy/pozisyon/emir/defter API'leri — ilgili `*_api.py` modülleri

## Kimlik doğrulama
- `POST /api/v1/auth/login` · `POST /api/v1/auth/logout` — oturum; giriş
  denemeleri IP bazlı oran sınırlıdır (aşımda 429).
