# Operatör Kılavuzu (Türkçe)

## İlk kurulum — parola hash'i oluşturma
1. Uygulamayı açın; sahip secret'ları yoksa **Kurulum Kilitli** ekranı /
   kurulum sihirbazı (`/setup`) görünür.
2. Sihirbazda güçlü bir parola girin; sunucu yalnızca **hash** üretir,
   parolanız hiçbir yerde saklanmaz.
3. Üretilen hash'i kopyalayın ve Replit **Secrets** bölümüne ekleyin:
   - `ALPHA_OWNER_USERNAME` → seçtiğiniz kullanıcı adı
   - `ALPHA_OWNER_PASSWORD_HASH` → sihirbazın ürettiği hash
4. Uygulamayı yeniden başlatın; kurulum durumu **Uygulama Hazır** olur.

Hash'i asla kaynak koduna veya sohbete yapıştırmayın; yalnızca Secrets'a.

## Giriş
- `/login` sayfasında **Kullanıcı Adı** ve **Parola** girin → **Giriş Yap**.
- 5 dakika içinde 5 başarısız deneme IP'nizi 5 dakika kilitler
  ("Tekrar Deneyin" mesajı ile).
- Oturum 8 saat geçerlidir; son 5 dakikada uyarı görürsünüz.

## Çıkış
Sağ üstteki **Oturumu Kapat** düğmesi oturumu anında geçersiz kılar.

## Kilitli kurulum davranışı
Sahip secret'ları eksikse: giriş kapalıdır, borsa verisi yüklenmez, ekranda
yalnızca gerekli secret ADLARI listelenir (değerler asla gösterilmez).

## Güvenli varsayılanlar
Tüm özellik bayrakları kapalıdır: `ALPHA_ENABLE_DRY_RUN`,
`ALPHA_ENABLE_LIVE_TRADING`, `ALPHA_ENABLE_TRANSFERS`,
`ALPHA_ENABLE_WITHDRAWALS` = `false`. **Canlı emir yürütme devre dışıdır**
ve yalnızca sunucu tarafında, açık talimatla etkinleştirilebilir.

## Genel Bakış panosu (Mission 1400.2)
- Menüden **📊 Genel Bakış** (`/overview`) ile açılır; 4 kart: Binance
  Global, Binance Futures, Binance TR, Sistem Sağlığı.
- Durum etiketleri: **GÜNCEL** (yeşil), **ESKİ VERİ** (sarı),
  **KULLANILAMIYOR** (kırmızı). Bir kaynak hata verse bile diğer kartlar
  çalışmaya devam eder.
- **⟳ Tümünü Yenile** düğmesi yalnızca uygulama önbelleğini temizleyip
  verileri yeniden okur; borsada hiçbir şey değiştirmez. Sayfa 30 saniyede
  bir kendini yeniler (sekme gizliyken duraklar).
- Borsanın `canTrade` bayrağı borsa yeteneğidir; uygulamanın canlı emir
  durumu her zaman **DEVRE DIŞI** olarak ayrıca gösterilir.
- Bot kontrolü artık menüdeki **🤖 Bot Kontrol** (`/panel`) sayfasındadır.

## Portföy, Pozisyonlar ve Emirler (Mission 1400.3)
- Menüden **💼 Portföy**, **📈 Pozisyonlar**, **🧾 Emirler** açılır; üçü de
  salt-okunurdur — emir verme/iptal/kapatma düğmesi yoktur ve olmayacaktır.
- Portföy'de hesaplar ayrıdır; birleşik toplam gösterilmez (TRY ile USDT
  dönüşümsüz toplanamaz). "Sıfır Bakiyeleri Göster" ile tüm varlıklar görünür.
- Pozisyonlarda yön metinle gösterilir: LONG / SHORT / FLAT. "Toplam
  Gerçekleşmemiş PnL" açık pozisyonların anlık değeridir, gerçekleşmiş kâr
  değildir.
- **⬇ CSV Dışa Aktar** düğmeleri Excel-uyumlu (UTF-8 BOM) dosya indirir;
  dosyalarda secret veya ham borsa yanıtı bulunmaz.
- Her bölümde veri durumu görünür: GÜNCEL / ESKİ VERİ / KULLANILAMIYOR.

## Üretim başlatma komutu
```
gunicorn -c gunicorn.conf.py app:app
```
