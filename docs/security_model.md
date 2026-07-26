# Güvenlik Modeli

## Kimlik doğrulama
- Tek sahip hesabı. Kullanıcı adı `ALPHA_OWNER_USERNAME`, parola doğrulaması
  `ALPHA_OWNER_PASSWORD_HASH` (Werkzeug PBKDF2-SHA256) ile yapılır.
  Düz metin parola hiçbir yerde saklanmaz veya loglanmaz.
- Geriye dönük uyumluluk: eski `ADMIN_PASSWORD_HASH` da kabul edilir.
- Zamanlama-güvenli doğrulama: kullanıcı adı yanlış olsa bile sahte hash
  üzerinde doğrulama çalıştırılır; hata mesajı geneldir ("Kullanıcı adı veya
  parola hatalı") ve hangisinin yanlış olduğunu açıklamaz.
- Hız sınırı: IP başına 5 dakikada 5 başarısız deneme → 5 dk kilit; sayaçlar
  SQLite'ta tutulur (çok worker'lı gunicorn'da tutarlı).
- Oturum: imzalı çerez (SESSION_SECRET), HttpOnly, SameSite=Lax, üretimde
  Secure; 8 saat geçerlilik; girişte oturum sabitleme önlemi (session.clear).
- CSRF: durum değiştiren tüm form istekleri Flask-WTF ile korunur;
  `/api/v1/auth/login` oturum öncesi olduğu için muaftır ve hız sınırlıdır.

## Kilitli kurulum (LOCKED SETUP)
Sahip secret'ları eksikse uygulama kilitlenir: giriş yapılamaz, borsa verisi
yüklenmez, API `403` döner. UI yalnızca gerekli değişken ADLARINI listeler;
değerler asla loglanmaz/gösterilmez.

## Borsa güvenliği
- Tüm borsa secret'ları sunucu tarafındadır; frontend'e hiçbir secret geçmez.
- Salt-okunur geçit (`exchange_gateway.py`): yalnızca GET, allowlist ağ
  isteğinden önce; emir/transfer/çekim/kaldıraç kod yolu yok.
- API anahtarları yalnızca maskeli (ilk4…son4) görüntülenir.
- Rota haritasında order/transfer/withdraw/leverage içeren hiçbir yol
  bulunmadığı test ile doğrulanır.

## Denetim (audit)
`security.log` şu olayları maskelenmiş meta verilerle kaydeder: giriş
başarılı/başarısız, çıkış, oturum süresi dolumu, yetkisiz API erişimi, CSRF
reddi, kilitli kurulum. Kayıtlara parola, hash, çerez, oturum token'ı, API
anahtarı veya Authorization başlığı yazılmaz (`log_contains_sensitive`
kontrolü + testler). Her istekte `X-Request-ID` üretilir; log ve hata
yanıtlarına eklenir, iç izleri ifşa etmez.

## HTTP başlıkları
CSP (`default-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`),
X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy; üretimde
HSTS. Debug modu üretimde kapalıdır.
