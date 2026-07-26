# Replit Dağıtımı

## Başlatma komutu
Üretim ve geliştirmede aynı komut kullanılır:

```
gunicorn -c gunicorn.conf.py app:app
```

- `bind = 0.0.0.0:5000` (platform host/port bağlaması)
- 2 sync worker; gunicorn SIGTERM ile düzgün kapanır (graceful shutdown),
  `post_fork` kancası arka plan döngülerini ve açılış yapılandırma
  doğrulamasını başlatır (graceful startup).
- Frontend sunucu tarafında Jinja2 ile üretilir; ayrı build adımı yoktur
  (statik dosyalar `/static/` altından servis edilir).
- Debug modu hiçbir zaman açılmaz; `FLASK_ENV=production` iken HSTS eklenir.

## Ortam doğrulaması
Açılışta yapılandırma doğrulanır; sahip secret'ları yoksa uygulama KİLİTLİ
KURULUM moduna girer (giriş kapalı, borsa verisi yüklenmez).

## Gerekli secret'lar
- `SESSION_SECRET` — oturum imzalama
- `ALPHA_OWNER_USERNAME`, `ALPHA_OWNER_PASSWORD_HASH` — sahip girişi
- Borsa anahtarları (yalnızca backend): `BINANCE_API_KEY/SECRET`,
  `BINANCE_TRADING_API_KEY/SECRET`, `BINANCE_TR_API_KEY/SECRET`

Testler geçmeden herkese açık dağıtım yapmayın.
