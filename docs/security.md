# Alpha Intelligence OS — Güvenlik Belgesi

**Sürüm:** 0.1.0-alpha  
**Son güncelleme:** ownership-baseline-v1

> Bu belge `SECURITY.md` ile çakışmaz.
> `SECURITY.md` → operasyonel rehber (olay yanıtı, acil adımlar)  
> Bu dosya → mimari güvenlik kararları ve katman açıklaması

---

## Güvenlik Katmanları

### 1. Kimlik Doğrulama

| Özellik | Uygulama |
|---|---|
| Parola saklama | Werkzeug PBKDF2-SHA256 (`generate_password_hash`) |
| Parola doğrulama | `check_password_hash` — zamanlama saldırısına karşı sabit süreli karşılaştırma |
| Kullanıcı adı | `ADMIN_USERNAME` env var; varsayılan `admin` |
| Oturum şifreleme | `SESSION_SECRET` / `FLASK_SECRET_KEY` (Replit Secret) |
| Oturum süresi | 8 saat; `before_request` her istekte kontrol eder |
| Çıkış | `session.clear()` + client-side cookie silme |

### 2. Kimliksiz Erişim Koruması

`before_request` hook:
- `TESTING=True` ise bypass (test ortamı)
- `/login`, `/static` hariç tüm rotalar korunur
- Oturum yoksa veya `session["logged_in"]` yoksa → `/login?next=<url>` yönlendirmesi
- `next` parametresi yalnızca göreli URL kabul eder (açık yönlendirme önleme)

### 3. CSRF Koruması

- `Flask-WTF CSRFProtect` — tüm uygulama genelinde etkin
- Her POST formunda `csrf_token()` zorunlu
- Dashboard'da JS: tüm POST formlarına otomatik CSRF hidden input enjeksiyonu
- **CSRF hatası + kimliksiz istek → `/login` yönlendirmesi (dashboard sızdırılmaz)**
- `TESTING=True` ortamında `WTF_CSRF_ENABLED=False` (test kolaylığı)

### 4. HTTP Güvenlik Başlıkları

`after_request` hook tüm yanıtlara ekler:

| Başlık | Değer |
|---|---|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (production only) |

### 5. Rate Limiting

- 5 başarısız giriş / 5 dakika → IP kilitlenir
- Farklı IP'ler bağımsız sayaçlara sahip
- Başarılı giriş sayacı sıfırlar
- Lockout süresi dolduğunda otomatik açılır

### 6. PAPER Modu Kilidi

`enforce_paper_mode_lock()` her başlangıçta:
- `config.json` → `mode` alanını `"PAPER"` olarak zorlar
- Canlı emir fonksiyonları kod tabanında bulunmaz
- Borsa API anahtarı okunmaz/kullanılmaz

### 7. Güvenlik Loglama

`security_log.py`:
- `RotatingFileHandler` — 1 MB / 3 yedek dosya
- Olay türleri: `LOGIN_OK`, `LOGIN_FAIL`, `CSRF_FAIL`, `RATE_LIMIT`, `PAPER_LOCK`, `BOT_START`, `BOT_STOP`, `SETTINGS_CHANGE`, `COIN_ADD`, `COIN_REMOVE`, `KILL_SWITCH`, `STARTUP`
- `_sanitize()` — `password=`, `secret=`, `token=` değerlerini `[REDACTED]` ile maskeler

---

## Gizli Veri Yönetimi

```
SIRMAK NEREDEİ SAKLANIR?

  Geliştirme:    .env dosyası (git'e commit edilmez)
  Üretim:        Replit Secrets (environment variables)
  Kod tabanı:    ASLA — taranan ve test edilen

HANGI SIRLAR KULLANILIR?

  SESSION_SECRET    → Flask oturum şifreleme
  ADMIN_PASSWORD_HASH → Giriş parolası hash'i
  ADMIN_USERNAME    → Giriş kullanıcı adı (opsiyonel)
  FLASK_SECRET_KEY  → SESSION_SECRET yoksa fallback
```

---

## Olay Yanıt Prosedürü

Detay için: [`../SECURITY.md`](../SECURITY.md)

| Senaryo | İlk Adım |
|---|---|
| Şüpheli giriş | `security.log` incele, oturumu kapat |
| Parola sızdı | `ADMIN_PASSWORD_HASH` rotasyonu, `SESSION_SECRET` yenile |
| Yetkisiz erişim | Kill switch aktif et, logları kaydet |
| Yedek bütünlük hatası | Önceki yedeğe dön, `restore.sh --dry-run` ile test et |
