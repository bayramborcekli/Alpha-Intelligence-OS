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

## Diğer salt-okunur yüzeyler (1400 serisi)
- `GET /api/risk/{summary,exposure,alerts,history,simulator}` — Risk Motoru
  (simülatörün POST varyantı yalnızca YEREL hesap yapar; borsaya istek yok)
- `GET /api/v1/executive/summary` — yönetici üst çubuğu
- Pano/portföy/pozisyon/emir/defter API'leri — ilgili `*_api.py` modülleri

## Kimlik doğrulama
- `POST /api/v1/auth/login` · `POST /api/v1/auth/logout` — oturum; giriş
  denemeleri IP bazlı oran sınırlıdır (aşımda 429).
