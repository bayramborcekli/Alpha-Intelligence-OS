# Canlı Web Uygulaması (Mission 1400.1)

## Mimari
- **Backend:** Flask + Gunicorn (mevcut depo yığını korunmuştur, spec'e uygun
  "en küçük istikrarlı mimari"). 2 sync worker, `0.0.0.0:5000`.
- **Frontend:** Sunucu tarafı Jinja2 şablonları + duyarlı (responsive) CSS,
  framework yok. Tarayıcı yalnızca Alpha backend'iyle konuşur.
- **Kabuk:** `GET /` — kenar çubuğu (masaüstü) / açılır menü (mobil), üst
  başlık, mod rozeti, Başlangıç sayfası. Navigasyon girdilerinin çoğu bu
  sprintte devre dışı yer tutucudur; "Genel Bakış" klasik paneli (`/panel`) açar.

## Rotalar
| Rota | Erişim | Açıklama |
|---|---|---|
| `GET /health`, `GET /api/v1/health` | herkese açık | güvenli sağlık bilgisi |
| `GET /login`, `POST /api/v1/auth/login` | herkese açık | sahip girişi |
| `POST /api/v1/auth/logout` | korumalı | oturumu kapat |
| `GET /api/v1/auth/session` | korumalı | oturum durumu |
| `GET /api/v1/application/config` | korumalı | güvenli yapılandırma |
| `GET /` | korumalı | uygulama kabuğu (Başlangıç) |
| `GET /overview` | korumalı | Genel Bakış — salt-okunur canlı pano (1400.2) |
| `GET /panel` | korumalı | bot kontrol paneli |
| `GET /api/v1/overview` | korumalı | tüm kaynakların tipli toplaması |
| `GET /api/v1/global/account` | korumalı | Binance Global Futures hesap özeti |
| `GET /api/v1/global/positions` | korumalı | açık pozisyonlar (`?include_zero=true` ile tümü) |
| `GET /api/v1/global/orders` | korumalı | açık emirler (salt-okunur GET) |
| `GET /api/v1/tr/account` | korumalı | Binance TR spot bakiyeleri |
| `GET /api/v1/tr/movements/summary` | korumalı | 1310B ledger'ından hareket özeti (yeniden besleme yok) |
| `GET /api/v1/system/status` | korumalı | sistem sağlığı + yazma sayaçları |
| `POST /api/v1/refresh` | korumalı + CSRF | uygulama-yerel önbellek temizleme |

Kimliksiz istek: API'de `401` (kilitli kurulumda `403`), tarayıcı
sayfalarında `/login` yönlendirmesi. Her yanıtta `X-Request-ID` başlığı bulunur.

## Özellik bayrakları (yalnızca sunucu tarafı)
`ALPHA_ENABLE_DRY_RUN`, `ALPHA_ENABLE_LIVE_TRADING`, `ALPHA_ENABLE_TRANSFERS`,
`ALPHA_ENABLE_WITHDRAWALS` — hepsi varsayılan **false**; yalnızca `true`
değeri (harf duyarsız) bayrağı açar, bozuk değerler false sayılır. Frontend
bayrakları asla geçersiz kılamaz. Bu sprintte canlı emir, transfer ve çekim
kod yolu yoktur.

## Canlı pano (Mission 1400.2)
- **Veri akışı:** tarayıcı → Alpha backend → borsa (yalnızca GET + allowlist).
  Tarayıcı borsayla asla doğrudan konuşmaz; ham borsa yanıtı asla dışarı verilmez.
- **Tipli meta:** her kaynak yanıtında `source`, `retrieved_at` (ISO UTC),
  `age_seconds`, `freshness` (FRESH/STALE/UNAVAILABLE), `latency_ms` bulunur.
- **Tazelik politikası (merkezî):** hesap/pozisyon/emir ≤60 sn = GÜNCEL;
  TR hareket özeti ≤15 dk = GÜNCEL. UI etiketleri: GÜNCEL / ESKİ VERİ /
  KULLANILAMIYOR (renk + metin, yalnızca renk değil).
- **Önbellek TTL'leri:** hesap 15 sn, pozisyon/emir 10 sn, TR hesap 30 sn,
  hareket özeti 5 dk. `POST /api/v1/refresh` yalnızca bu uygulama-yerel
  önbellekleri temizler; borsa tarafında hiçbir şey değiştirmez.
- **Decimal güvenliği:** finansal değerler borsanın string hassasiyetiyle
  taşınır; binary float aritmetiği yapılmaz. UI Türkçe yerel biçim gösterir,
  tam hassasiyet `title` içinde saklanır.
- **Kısmi hata:** tek kaynağın hatası diğer kartları karartmaz; hata kartı
  güvenli mesaj + varsa son bilinen veri (yaşıyla) gösterir. Kaynaklar arası
  birleşik portföy toplamı üretilmez.
- **canTrade:** borsanın `canTrade` bayrağı yalnızca borsa yeteneğidir;
  uygulamanın canlı emir durumu her zaman ayrı ve **DEVRE DIŞI** gösterilir.
- **Otomatik yenileme:** 30 sn'de bir; sekme gizliyken duraklar.
- **Pano birleştirme:** "Genel Bakış" = `/overview` (tek birincil pano).
  `/panel` bot kontrol sayfası olarak kalır; eski borsa izleme bölümü
  `/overview`'a yönlendirir. `GET /api/exchange/summary` uyumluluk için korunur.

## Dil
Varsayılan arayüz dili Türkçe'dir (`ui_language: "tr"` yapılandırma
yanıtında). Gelecekte İngilizce desteği için metinler şablon katmanında
tutulur.
