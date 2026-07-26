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

## Üretim başlatma komutu
```
gunicorn -c gunicorn.conf.py app:app
```
