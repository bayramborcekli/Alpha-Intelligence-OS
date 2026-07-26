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
| `GET /portfolio`, `/positions`, `/orders` | korumalı | Portföy / Pozisyonlar / Emirler çalışma alanları (1400.3) |
| `GET /api/v1/portfolio` | korumalı | ayrık hesap bölümleri (Global + TR varlıkları) |
| `GET /api/v1/portfolio/export.csv` | korumalı | portföy CSV dışa aktarımı |
| `GET /api/v1/global/positions/export.csv` | korumalı | pozisyonlar CSV dışa aktarımı |
| `GET /api/v1/global/orders/export.csv` | korumalı | açık emirler CSV dışa aktarımı |
| `GET /ledger`, `/audit`, `/reports` | korumalı | Defter / Denetim / Raporlar çalışma alanları (1400.4) |
| `GET /api/v1/ledger/{events,summary,integrity,reconciliation}` | korumalı | ekle-yalnız defter görünümleri |
| `GET /api/v1/audit/{events,summary}` | korumalı | uygulama denetim olayları |
| `GET /api/v1/reports`, `/api/v1/reports/{id}`, `/{id}/download` | korumalı | görev raporu kayıt defteri |
| `GET /api/v1/ledger/export.csv`, `/api/v1/audit/export.csv` | korumalı | defter/denetim CSV dışa aktarımı |
| `GET /risk` | korumalı | Risk İstihbarat Motoru çalışma alanı (1400.6) |
| `GET /api/v1/risk/{summary,exposure,alerts,history}` | korumalı | salt-okunur risk analizi (tavsiye niteliğinde; `/api/risk/*` eski takma adları korunur) |
| `POST /api/v1/risk/simulator` | korumalı + CSRF | işlem-öncesi simülatör — YALNIZCA yerel hesap, borsa iletişimi yok |
| `GET /api/v1/executive/summary` | korumalı | yönetici üst çubuğu özeti (1400.5) |

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

## Portföy / Pozisyonlar / Emirler (Mission 1400.3)
- **Hesap ayrımı:** Binance Global Futures ve Binance TR ayrı bölümlerde
  gösterilir. TRY→USDT dönüşümü yapılmaz ve kaynaklar arası birleşik toplam
  KASITLI olarak gösterilmez — farklı para birimlerinin dönüşümsüz toplamı
  yanıltıcı olurdu.
- **Pozisyon yönü (TEK YÖN modu):** miktar > 0 → LONG, < 0 → SHORT,
  = 0 → FLAT. Aynı sembolde eşzamanlı LONG+SHORT satırı üretilmez.
  Varsayılan görünüm aktif pozisyonlardır; "Sıfır Pozisyonları Göster" ile
  FLAT satırlar eklenir.
- **Gerçekleşmemiş PnL:** açık pozisyonların anlık kâr/zararıdır;
  gerçekleşmiş kâr veya hesap özkaynağı değildir. Sayfadaki toplam yalnızca
  aynı Futures hesabı içinde ve Decimal aritmetiğiyle hesaplanır.
- **Emirler:** yalnızca açık emirler görüntülenir. `kalan = miktar −
  gerçekleşen` Decimal ile hesaplanır; doluluk durumu borsanın `status`
  alanından okunur, miktarlardan çıkarsanmaz.
- **Eylem düğmesi yoktur:** iptal/kapat/düzenle/gönder kontrolleri kasıtlı
  olarak yoktur — uygulama salt-okunurdur ve borsa yazma kod yolu içermez.
- **Filtre/sıralama:** arama, yön/taraf/tür/marjin filtreleri ve sıralama
  istemci tarafında sterilize veri üzerinde çalışır; API sorgu parametreleri
  (include_zero, search, sort, order, limit) sunucuda doğrulanır, geçersiz
  değer 400 INVALID_PARAMETER döner.
- **CSV dışa aktarımı:** sunucu tarafında tipli modellerden üretilir; UTF-8
  BOM (Türkçe Excel), ISO-8601 zaman, ham Decimal string, sabit sütun sırası.
  Metin hücrelerinde formül enjeksiyonu (`= + - @` vb.) nötralize edilir;
  sayısal sütunlardaki negatif değerler DEĞİŞTİRİLMEZ. Portföy ve pozisyon
  dışa aktarımı doğrulanmış filtreleri uygular; emir dışa aktarımı tüm açık
  emirleri içerir (UI'da belirtilir).
- **Eski veri:** merkezî 1400.2 tazelik politikası aynen kullanılır; her
  bölümde kaynak, alınma zamanı, yaş ve GÜNCEL/ESKİ VERİ/KULLANILAMIYOR
  etiketi görünür. Önbellekten sunulan veri asla "yeni alındı" gibi
  etiketlenmez.

## Defter / Denetim / Raporlar (Mission 1400.4)
- **Defter ≠ Denetim:** Defter, borsa kaynaklı finansal olayların ekle-yalnız
  (append-only) kaydıdır (1310B); Denetim ise uygulamanın kendi güvenlik
  olaylarıdır (giriş, CSRF, dışa aktarım vb.). İki kavram asla birleştirilmez.
- **Ekle-yalnız:** Defter kayıtları yalnızca eklenir; UI veya API üzerinden
  düzenleme/silme/onarma yolu YOKTUR. Bir kayıt hatalıysa bile otomatik
  onarım yapılmaz — bu, kanıt bütünlüğünün ön koşuludur.
- **Engellenen tekrar (duplicate-block):** Aynı kaynak işlem ikinci kez
  alındığında deftere eklenmez; engellenen sayısı 1310B kanıtından raporlanır.
- **Bütünlük durumları:** PASS (tüm kayıtlar geçerli, kimlikler tekil, sıra
  deterministik) / PARTIAL (izole bozuk kayıt veya eksik özet var) /
  FAIL (tekrarlanan kimlik, sıra bozukluğu veya kaynak okunamıyor —
  CSV dışa aktarımı fail-closed kapanır). Hash doğrulaması "varlık kontrolü"
  düzeyindedir; ham yük saklanmadığı için yeniden hesaplama desteklenmez.
- **PARTIAL mutabakat:** ASLA PASS gibi gösterilmez. Neden: Binance TR API
  geçmişi tüm spot işlemleri/dönüşümleri kapsamayabilir; açılış bakiyesi
  ÜRETİLMEZ, farklar varlık bazında olduğu gibi raporlanır.
- **Olay normalizasyonu:** Orijinal tip korunur; onaylı kategorilere
  eşlenemeyenler UNKNOWN olur, kanıtsız tür çıkarımı yapılmaz. Kaynak işlem
  kimlikleri maskeli gösterilir (baş/son karakterler).
- **Denetim sanitizasyonu:** parola/hash/token/çerez asla loglanmaz ve API'ye
  çıkmaz; IP adresleri maskelenir; denetim satırlarını GETİRMEK yeni denetim
  kaydı üretmez (rekürsiyon yok), yalnızca sayfa açılışı tek olay yazar.
- **Rapor kayıt defteri:** Raporlar sabit bir kayıt defterinden keşfedilir;
  kullanıcıdan dosya yolu alınmaz → yol geçişi (path traversal) mümkün
  değildir. İndirme sterilize JSON'dur; iç mutlak yollar ve hassas anahtarlar
  temizlenir. Bilinmeyen rapor kimliği güvenli 404 döner.
- **CSV:** UTF-8 BOM, ISO zaman, ham Decimal string; metin hücrelerinde
  formül enjeksiyonu nötralize edilir, sayısal negatifler değişmez; dışa
  aktarım boyutu sınırlıdır (defter/denetim ≤ 500 satır).
- **PDF durumu:** ERTELENDİ — depoda hazır ve stabil bir PDF üretim yolu
  yoktur; kırılgan bağımlılık eklenmedi.
- **Tazelik:** Defter GÜNCEL (bütünlük ≤15 dk önce doğrulandı) / ESKİ VERİ /
  KULLANILAMIYOR; raporlar MEVCUT / EKSİK / GEÇERSİZ. Tarihsel kanıt için
  "canlı" terimi kullanılmaz.

## Risk İstihbarat Motoru (Mission 1400.6)
- **Tavsiye niteliğinde:** motor hiçbir otomatik işlem yapmaz, emir
  oluşturmaz, borsaya yazma isteği göndermez. Tüm rotalar GET-only.
- **Deterministik skor (0-100):** kural tabanlı cezalarla hesaplanır
  (marj kullanımı, maruziyet, konsantrasyon, kullanılabilir bakiye, açık
  emir, günlük düşüş). Yapay zekâ yok, rastgelelik yok; aynı girdi aynı
  skoru üretir. Sınıflar: Mükemmel / İyi / Orta / Yüksek Risk / Kritik.
- **Maruziyet evreni tek para birimidir** (Global Futures USDT). Binance TR
  varlıkları yalnızca adet olarak listelenir; kur tahminiyle USD karşılığı
  ÜRETİLMEZ, çapraz kur birleştirme yapılmaz.
- **Düşüş (drawdown) değerleri** yalnızca yerel ekle-yalnız anlık görüntü
  geçmişinden hesaplanır; yeterli doğrulanmış geçmiş yoksa "Veri Yok"
  gösterilir — asla tahmin edilmez.
- **Uyarılar tekrarsızdır** (kod başına en çok bir uyarı) ve yalnızca
  bilgilendiricidir.
- **Simülatör** tamamen yerel hesaptır: sembol/yön/fiyat/miktar/kaldıraç
  tipli doğrulamadan geçer (400 INVALID_PARAMETER), borsaya istek atılmaz,
  emir önizlemesi/gönderimi yoktur. Tasfiye tamponu 1/kaldıraç yaklaşımıdır
  ve açıkça öyle etiketlenir.
- **Geçmiş** `risk_history.jsonl` dosyasında ekle-yalnız tutulur: günde en
  çok bir kayıt (skor, maruziyet, marj kullanımı, düşüş ve uyarı kodları),
  önceki kayıtların üzerine asla yazılmaz, bozuk satırlar izole edilir
  (dosya onarılmaz). Tüm para matematiği Decimal'dir.
- **Eşikler yapılandırılabilir:** `risk_config.json` içinde
  `RISK_HIGH_MARGIN`, `RISK_CRITICAL_MARGIN`, `MAX_POSITION_PERCENT`,
  `POSITION_HIGH_PERCENT`, `POSITION_CRITICAL_PERCENT`,
  `MAX_EXCHANGE_PERCENT`, `HIGH_EXPOSURE_PERCENT`, `LOW_AVAILABLE_PERCENT`,
  `DRAWDOWN_WARN_PERCENT`. İş mantığında sabit kodlu eşik yoktur; dosya
  eksik/bozuksa güvenli varsayılanlar kullanılır.
- **Skor sınıf bantları:** 90-100 Mükemmel, 75-89 İyi, 60-74 Orta,
  40-59 Yüksek Risk, 0-39 Kritik.
- Yönetici üst çubuğundaki "Risk Seviyesi" artık bu motorun doğrulanmış
  skorundan beslenir.

## Dil
Varsayılan arayüz dili Türkçe'dir (`ui_language: "tr"` yapılandırma
yanıtında). Gelecekte İngilizce desteği için metinler şablon katmanında
tutulur.
