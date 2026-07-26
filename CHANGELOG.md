# Değişiklik Günlüğü

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
