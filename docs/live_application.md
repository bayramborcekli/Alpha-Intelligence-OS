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
| `GET /panel` | korumalı | klasik bot kontrol paneli |

Kimliksiz istek: API'de `401` (kilitli kurulumda `403`), tarayıcı
sayfalarında `/login` yönlendirmesi. Her yanıtta `X-Request-ID` başlığı bulunur.

## Özellik bayrakları (yalnızca sunucu tarafı)
`ALPHA_ENABLE_DRY_RUN`, `ALPHA_ENABLE_LIVE_TRADING`, `ALPHA_ENABLE_TRANSFERS`,
`ALPHA_ENABLE_WITHDRAWALS` — hepsi varsayılan **false**; yalnızca `true`
değeri (harf duyarsız) bayrağı açar, bozuk değerler false sayılır. Frontend
bayrakları asla geçersiz kılamaz. Bu sprintte canlı emir, transfer ve çekim
kod yolu yoktur.

## Dil
Varsayılan arayüz dili Türkçe'dir (`ui_language: "tr"` yapılandırma
yanıtında). Gelecekte İngilizce desteği için metinler şablon katmanında
tutulur.
