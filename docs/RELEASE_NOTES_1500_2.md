# Mission 1500.2 — Intelligence Workspace · Sürüm Notları

**Durum:** MISSION 1500.2 — CLOSED · 2026-07-26
**Final:** 969 PASS / 0 FAIL / 0 SKIP · Exchange Write: 0 · Secret Exposure: 0

## Mimari zincir

```
Intelligence Snapshot (1500.1 servis çıktısı)
        ↓
Append-Only Timeline        intelligence_timeline.py
        ↓
Workspace Service           intelligence_workspace_service.py
        ↓
Read-Only API               app.py  /api/workspace/*
        ↓
Workspace UI                templates/intelligence_workspace.html  (/workspace)
        ↓
Workspace Export            workspace_export_api.py  /api/workspace/export/*
```

## Güvenlik ve davranış garantileri

- **Read-only Workspace** — hiçbir uç kayıt oluşturmaz/değiştirmez/silmez.
- **Advisory-only** — tüm JSON servis/API zarfları `read_only:true` +
  `advisory_only:true` taşır (CSV/HTML yanıtlar zarf taşımaz ancak aynı
  salt-okunur veriden türetilir); emir dili yoktur.
- **Exchange Write = 0** — workspace modüllerinde borsa istemcisi, ağ
  isteği ve harici LLM yoktur (statik + çalışma-zamanı testli).
- **Deterministic output** — aynı geçmiş + aynı parametreler → bayt-özdeş
  JSON/CSV.
- **Decimal → string** — para değerleri tüm katmanlarda string; float
  yasaktır (`FLOAT_FORBIDDEN`).
- **Unknown → null / "—" / "Veri Yok"** — asla 0 veya tahmini değer
  türetilmez.
- **Risk Engine authoritative** — risk skoru yalnızca Risk Motoru'ndan
  gelir; workspace yalnız geçmişi gösterir, `forecast: null`.
- **History append-only** · **Ledger immutable** · **Audit immutable**.
- **External LLM yok** — 1500.1 sert kilidi geçerlidir.
- **Authentication / CSRF / rate limiting korunur** — yeni istisna veya
  bypass eklenmemiştir.
- **Sterile error modeli** — hata gövdesi sabittir:
  `{"ok":false,"error":{"code":<KOD>,"message":"İşlem tamamlanamadı"}}`.

## Timeline katmanı (`intelligence_timeline.py`)

- Append-only **JSONL**; kayıt sırası **eski → yeni**; overwrite/update/
  delete yoktur (dosya yalnızca `"a"`/`"r"` modlarında açılır — AST testli).
- Decimal → string; float değer **reddedilir** (`FLOAT_FORBIDDEN`).
- Canonical JSON (`sort_keys`) — determinizm garantisi.
- Alan beyaz listesi (11 alan) + yasak anahtar taraması (iç içe);
  `advisory_only`/`read_only` her kayıtta zorla `true`.
- Sınırlar: kayıt başına **MAX_RECORD_BYTES = 16384** bayt; dosya başına
  **MAX_RECORDS = 5000** kayıt (kapasite kontrolü + append tek
  `fcntl.flock(LOCK_EX)` kilidi altında — çok-worker yarışı yok).
- Dosya yolu: varsayılan `intelligence_history.jsonl`; ortam değişkeni
  `ALPHA_INTELLIGENCE_HISTORY_PATH` ile geçersiz kılınabilir. Yol asla
  istek parametresinden gelmez.

## Workspace Service (`intelligence_workspace_service.py`)

| Fonksiyon | Davranış |
|---|---|
| `get_timeline(limit, offset)` | 1-tabanlı id'li hafif özet listesi, `total` |
| `get_snapshot(id)` | Tek kayıt; bulunamazsa sterile `SNAPSHOT_NOT_FOUND` |
| `compare_snapshots(a, b)` | **Derin ve deterministik** karşılaştırma; liste farkları **indeks tabanlıdır** (`insights[1]` gibi); `NEW / CHANGED / REMOVED`; eksik taraf **"Veri Yok"** |
| `get_recommendation_history()` | Kod bazlı gruplama; ardışık tekrarlar `count` ile birleştirilir; `confidence_changed` / `priority_changed` bayrakları |
| `get_risk_evolution()` | Yalnız geçmişten seri; **`forecast: null`** — tahmin/trend kestirimi yapılmaz |
| `search(...)` | start/end/status/confidence/recommendation_code/insight_code/partial/advisory_only filtreleri |

## Read-Only API (`/api/workspace/*`)

Tüm uçlar: **yalnız GET** (HEAD/OPTIONS otomatik) · kimlik doğrulama
zorunlu (anonim → **401**) · `Cache-Control: no-store, private` ·
`read_only`/`advisory_only` zarfı · geçersiz parametre → **400**
`INVALID_PARAMETER` · yazma metodu → **405**. Her ucun `/api/v1/...`
takma adı vardır.

| Uç | Parametreler | 404 |
|---|---|---|
| `GET /api/workspace/timeline` | `limit`, `offset` (negatif → 400) | — |
| `GET /api/workspace/snapshot/<id>` | id pozitif tamsayı (aksi 400) | bulunamayan id |
| `GET /api/workspace/compare` | `a`, `b` zorunlu pozitif tamsayı | bulunamayan id |
| `GET /api/workspace/recommendations` | — | — |
| `GET /api/workspace/risk-evolution` | — | — |
| `GET /api/workspace/search` | `start`/`date`, `end`/`date_end` (ISO, aksi 400), `status`, `confidence`, `recommendation`, `insight`, `partial`, `advisory_only` (yalnız `true`/`false`) | — |

Sağlayıcı içi hata → **200** + sterile zarf (`ok:false`).

## Workspace UI (`GET /workspace`)

`dash_base.html`'i genişletir; navigasyonda tek "Workspace" bağlantısı.
Bölümler: Zaman Çizelgesi · Snapshot Detayı · Snapshot Karşılaştırma ·
Tavsiye Geçmişi · Risk Evrimi · Arama ve Filtreler · Dışa Aktarım alanı.

- UI **salt-okunurdur**; yalnız Workspace **GET** API'lerini kullanır.
- İşlem/emir/güncelleme/silme butonu yoktur (yalnız sorgu düğmeleri).
- `null → "—"`, eksik karşılaştırma tarafı → `"Veri Yok"`.
- XSS kaçışı: `esc()/vy()/txt()` yardımcıları; snapshot JSON'u
  `textContent` ile; kaçışsız `innerHTML` yolu yoktur.
- Harici JS/CSS/CDN yoktur; mevcut CSP ile uyumludur. Risk evrimi yerel
  CSS çubuk gösterimidir (kütüphanesiz, tahmin çizgisiz).
- Sayfa yanıtı `Cache-Control: no-store, private` taşır.
- Arama tarihleri `datetime-local` → UTC ISO'ya çevrilir.

## Workspace Export (`/api/workspace/export/*`)

Uçlar (hepsi GET + `/api/v1` takma adı): `timeline`, `snapshot/<id>`,
`compare`, `recommendations`, `risk-evolution`, `search`.

- `format=json` **varsayılan**; `json|csv` dışı → **400**.
- **JSON**: servis zarfı aynen, deterministik (sıralı anahtar).
- **CSV**: yalnız düz metin hücreler; **formül enjeksiyon koruması**
  (`=`, `+`, `@`, tab, CR önekli metinler `'` ile nötralize; sayısal
  string'ler ve negatif Decimal'lar değişmez); yapısal değerler canonical
  JSON metnine düzleştirilir; **UTF-8 BOM** + **CRLF**.
- Decimal string korunur; bilinmeyen → `"—"`.
- Başlıklar: `Content-Disposition: attachment; filename="workspace_*"`,
  doğru `Content-Type`, `X-Content-Type-Options: nosniff`,
  `Cache-Control: no-store, private`.
- Snapshot bulunamadı → **404** sterile; diğer sağlayıcı hataları →
  **200** sterile JSON (CSV istense bile).

## Güvenlik doğrulaması (Agent 07 — 45 test)

- Tüm API/export uçlarında authentication; `/workspace` anonim → login
  yönlendirmesi.
- Yalnız GET/HEAD/OPTIONS; POST/PUT/PATCH/DELETE → 405 (oturum açıkken bile).
- Yeni CSRF istisnası yok (tek `@csrf.exempt` önceden var olan login ucu);
  rate-limit bypass yok.
- Template autoescape açık; `|safe` yok; harici script yok.
- Path traversal etkisiz; kullanıcı girdisi dosya yolunu belirleyemez
  (`path` sorgu parametresi kanıtlanabilir şekilde etkisiz).
- Exception/stack trace/dosya yolu/env/credential/secret sızmaz
  (zorlanmış istisna testli).
- Exchange/network/LLM erişimi yok (kaynak + import taraması).
- Okumalar sonrası geçmiş dosyası **bayt-özdeş** kalır.
- Kapatılan tek bulgu: `/workspace` HTML yanıtına `no-store, private`
  eklendi (minimum değişiklik).

## Operasyonel yapılandırma

| Öğe | Değer |
|---|---|
| Geçmiş dosyası | `intelligence_history.jsonl` (repo kökü) |
| Ortam override | `ALPHA_INTELLIGENCE_HISTORY_PATH` |
| Kayıt sınırı | `MAX_RECORDS = 5000` |
| Kayıt boyutu | `MAX_RECORD_BYTES = 16384` bayt |
| Kilitleme | `fcntl.flock(LOCK_EX)` — kapasite kontrolü + append atomik |

## Migration / Rollback notu

Rollback gerektiğinde güvenle yapılabilir:
- `/workspace` UI rotası, `/api/workspace/*` API rotaları,
  `/api/workspace/export/*` rotaları ve `dash_base.html` nav bağlantısı
  kaldırılabilir; workspace modülleri devre dışı bırakılabilir.
- Geçmiş dosyasının silinmesi **zorunlu değildir** (salt veri; okunmazsa
  etkisizdir).
- 1500.1 Intelligence sistemi workspace'ten bağımsız çalışmaya devam eder.
- Ledger/Audit/Exchange verisine hiçbir müdahale gerekmez.
- Migration gerekmez: workspace yalnızca yeni, izole bir okuma yüzeyidir.

## Test tarihçesi (doğrulanmış agent raporlarından)

| Aşama | Toplam test |
|---|---|
| 1500.2 başlangıcı (1500.1 kapanışı) | 805 |
| Agent 02 — Timeline (+24) | 829 |
| Agent 03 — Workspace Service (+26) | 855 |
| Agent 04 — Read-Only API (+18) | 873 |
| Agent 05 — Workspace UI (+23) | 896 |
| Agent 06 — Export (+20) | 916 |
| Agent 07 — Security (+45) | 961 |
| Agent 08 — Full Regression (+8) | **969** |

FAIL: 0 · SKIP: 0 · Exchange Write Request: 0 · Secret Exposure: 0
Final commit (Agent 08): `a6305d1`

## Bilinen sınırlamalar (dürüst beyan)

- Geçmiş üretimde **henüz beslenmiyor**: snapshot kayıt entegrasyonu
  sonraki mission kapsamıdır; o zamana dek tüm workspace uçları dürüstçe
  boş sonuç döner (`total: 0`, boş seri/liste).
- Eski/kısmi şema kayıtlarında eksik alanlar türetilmez (null/"—").
- Liste karşılaştırması **indeks tabanlıdır**: sıra değişimi içerik
  değişikliği olarak görülebilir.
- `forecast: null` — trend tahmini bilinçli olarak yoktur.
- `limit/offset/a/b` için özel üst sınır yoktur; kaynak tüketimi
  `MAX_RECORDS = 5000` veri sınırıyla doğal olarak sınırlıdır.
- UI'daki "Dışa Aktarım" alanı **yer tutucudur** (Agent 05'te eklendi);
  export uçları Agent 06'da geldi ancak UI bağlantısı — "UI
  değiştirilmeyecek" kuralı gereği — bu seride güncellenmemiştir.

## MISSION 1500.2 — CLOSED

- Final Tests: **969 PASS / 0 FAIL / 0 SKIP**
- Exchange Write: **0**
- Secret Exposure: **0**
- Read-only: **Confirmed**
- Advisory-only: **Confirmed**
- Deterministic: **Confirmed**
- Decimal Integrity: **Confirmed**
- History Append-only: **Confirmed**
- Authentication: **Preserved**
- CSRF: **Preserved**
- Rate Limiting: **Preserved**
- Ledger: **Immutable**
- Audit: **Immutable**
