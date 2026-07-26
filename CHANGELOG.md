# Değişiklik Günlüğü

## [1500.2] — 2026-07-26 · Intelligence Workspace

Salt-okunur, deterministik Intelligence Workspace: geçmiş snapshot
zaman çizelgesi, karşılaştırma, tavsiye geçmişi, risk evrimi, arama ve
JSON/CSV export. Ayrıntı: `docs/RELEASE_NOTES_1500_2.md`.

### Eklendi
- `intelligence_timeline.py` — append-only JSONL geçmiş motoru
  (Decimal→string, float reddi, canonical JSON, alan beyaz listesi,
  16384B/kayıt + 5000 kayıt sınırı, `fcntl.flock` ile atomik append,
  `ALPHA_INTELLIGENCE_HISTORY_PATH` override)
- `intelligence_workspace_service.py` — salt-okunur orkestrasyon:
  timeline/snapshot/deep-deterministik compare (NEW/CHANGED/REMOVED,
  indeks tabanlı liste farkları, eksik taraf "Veri Yok")/tavsiye
  geçmişi (ardışık tekrar birleştirme)/risk evrimi (`forecast: null`)/
  arama filtreleri
- `GET /api/workspace/{timeline,snapshot/<id>,compare,recommendations,`
  `risk-evolution,search}` (+`/api/v1` takma adları) — GET-only,
  kimlik doğrulamalı, no-store, katı parametre doğrulama, sterile hata
- `/workspace` sayfası — salt-okunur Türkçe UI (XSS-kaçışlı, CSP uyumlu,
  harici kaynaksız, mobil `data-l`, işlem düğmesiz)
- `workspace_export_api.py` + `GET /api/workspace/export/*` — JSON
  (deterministik) ve CSV (düz metin, formül-enjeksiyon korumalı,
  UTF-8 BOM + CRLF, Content-Disposition attachment) export
- Güvenlik doğrulama paketi (45 test) + uçtan uca regresyon (8 test);
  toplam 164 yeni test → **969 PASS / 0 FAIL / 0 SKIP**

### Güvenlik
- Tümü salt-okunur; borsa yazma çağrısı 0; geçmiş dosyası okuma sonrası
  bayt-özdeş; yeni CSRF istisnası/rate-limit bypass yok; `/workspace`
  HTML yanıtına `Cache-Control: no-store, private` eklendi.

### Değişmedi
- Risk Engine, Recommendation Engine, 1500.1 modelleri, Exchange,
  Ledger, Audit, Settings, `alpha20_v1/`, `auth.py`.

## [1500.1] — 2026-07-26 · Intelligence Katmanı

Salt-okunur, deterministik, yalnızca-tavsiye Intelligence katmanı.
Ayrıntı: `docs/RELEASE_NOTES_1500_1.md`.

### Eklendi
- `intelligence_models.py` — tipli veri sözleşmeleri (Confidence/Status/
  Freshness/Evidence/Insight/Summary; Decimal-string serileştirme; yasak
  emir/secret alan korumaları)
- `intelligence_api.py` — kural tabanlı deterministik çekirdek
  (portföy/pozisyon/risk/tazelik analizi; LLM yok, rastgelelik yok)
- `risk_explainer.py` — Risk Motoru skor bileşenlerini ve uyarılarını
  Türkçe Gözlem/Gerekçe/Etki/Öneri şablonlarına çeviren açıklama motoru;
  izlenebilir skor dökümü (RISK_SCORE_TRACE)
- `recommendation_api.py` — öncelik sıralı, tekilleştirilmiş, yalnızca
  tavsiye niteliğinde operasyonel öneriler (8 kategori)
- `intelligence_service.py` — enjekte edilebilir salt-okunur sağlayıcılarla
  birleşik servis; kaynak bazlı tazelik; sterile hata kayıtları
- `GET /api/intelligence{,/summary,/insights,/recommendations,/status,/settings}`
  (+`/api/v1` takma adları) — kimlik doğrulamalı, GET-only, no-store
- `/intelligence` sayfası — Türkçe, mobil uyumlu, XSS-kaçışlı UI
- `intelligence_settings.py` — doğrulanmış ortam yapılandırması
  (`ALPHA_INTELLIGENCE_*`); geçersiz değerde güvenli varsayılan
- Güvenlik/regresyon test katmanı (212 yeni test; toplam 805)

### Güvenlik
- Harici LLM sert kilitli (ortam ne derse desin kapalı); local-only
  zorunlu; borsa yazma çağrısı 0; ledger/audit değişmezliği statik
  olarak da doğrulanır; `X-Permitted-Cross-Domain-Policies: none` eklendi.

### Değişmedi
- `alpha20_v1/`, `auth.py`, defter ve borsa imzalama katmanları.

## [1400.x] — önceki seri
Platform temeli, pano, portföy, defter/denetim, yönetici çubuğu ve Risk
İstihbarat Motoru (bkz. `docs/MISSION_INDEX.md`).
