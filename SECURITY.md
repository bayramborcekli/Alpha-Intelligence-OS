# Alpha-20 v1 — Güvenlik Kılavuzu

> **Önemli:** Bu sistem yalnızca PAPER (simülasyon) modunda çalışır.
> Gerçek emir, gerçek para, API imzası veya canlı Binance kimlik doğrulaması içermez.

---

## 1. Güvenlik Mimarisi

```
┌─────────────────────────────────────────────┐
│            Tarayıcı (Admin)                 │
└──────────────────┬──────────────────────────┘
                   │ HTTPS (production)
┌──────────────────▼──────────────────────────┐
│          Flask Web Uygulaması               │
│  ┌──────────────────────────────────────┐   │
│  │  auth.py — Kimlik Doğrulama Katmanı  │   │
│  │  • Werkzeug hash (PBKDF2-SHA256)     │   │
│  │  • IP bazlı rate limiting (5/5dak)   │   │
│  │  • 8 saatlik oturum süresi           │   │
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  Flask-WTF CSRF Koruması             │   │
│  │  • Tüm POST form'ları korumalı       │   │
│  │  • Signed token (SECRET_KEY ile)     │   │
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  Güvenlik HTTP Başlıkları            │   │
│  │  • Content-Security-Policy           │   │
│  │  • X-Frame-Options: DENY            │   │
│  │  • X-Content-Type-Options: nosniff  │   │
│  │  • Referrer-Policy                   │   │
│  │  • Permissions-Policy                │   │
│  │  • HSTS (production HTTPS)           │   │
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  security_log.py — Denetim İzleri   │   │
│  │  • Giriş/çıkış, bot, ayar, coin     │   │
│  │  • RotatingFileHandler (5MB × 5)    │   │
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │  PAPER Modu Kilidi                   │   │
│  │  • mode = "PAPER" zorunlu            │   │
│  │  • Canlı emir kodu yok               │   │
│  │  • Başlangıçta loglansın             │   │
│  └──────────────────────────────────────┘   │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  Binance Herkese Açık API (salt-okunur)     │
│  Yalnızca: /fapi/v1/klines, /ticker, vb.   │
│  API anahtarı: KULLANILMAZ                  │
└─────────────────────────────────────────────┘
```

---

## 2. Secret Yönetimi

### Gerekli Ortam Değişkenleri

| Değişken | Açıklama | Zorunlu |
|----------|----------|---------|
| `FLASK_SECRET_KEY` | Flask oturum şifreleme (güçlü, rastgele) | Evet |
| `ADMIN_USERNAME` | Admin kullanıcı adı | Hayır (varsayılan: "admin") |
| `ADMIN_PASSWORD_HASH` | Werkzeug PBKDF2 hash | Evet |
| `FLASK_ENV` | `production` veya `development` | Hayır (varsayılan: production davranışı) |

### Kurallar

- Sırlar asla kaynak koda yazılmaz
- `.env` dosyası `.gitignore`'da; asla commit edilmez
- `config.json` yalnızca hassas olmayan strateji ayarlarını içerir
- Log dosyalarına parola, token veya hash asla yazılmaz

### Replit Ortamında

Replit Secrets panelinden `FLASK_SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` değerlerini ayarlayın.

---

## 3. Kurulum

### Admin Parolası Oluşturma

```bash
python3 -c "
from werkzeug.security import generate_password_hash
import getpass
p = getpass.getpass('Parola: ')
print(generate_password_hash(p))
"
```

Üretilen hash'i `ADMIN_PASSWORD_HASH` ortam değişkenine atayın. Parolayı hiçbir yere kaydetmeyin.

### Flask Secret Key Oluşturma

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Üretilen değeri `FLASK_SECRET_KEY` ortam değişkenine atayın.

### Uygulamayı Başlatma

```bash
python app.py
```

Uygulama başlangıçta:
1. `FLASK_SECRET_KEY` ve `ADMIN_PASSWORD_HASH` varlığını kontrol eder
2. `config.json` içindeki `mode`'un "PAPER" olduğunu doğrular
3. `security.log`'a `STARTUP` ve `PAPER_MODE_ACTIVE` olaylarını yazar

---

## 4. PAPER Modu

- `alpha20_v1/config.json` içinde `"mode": "PAPER"` zorunludur
- Başlangıçta otomatik olarak kontrol edilir; "PAPER" değilse düzeltilir
- Kaynak kodda `create_order`, `place_order`, gerçek API imzası bulunmaz
- Dashboard'da kalıcı PAPER rozeti görünür
- Tüm işlemler simüle edilir; gerçek para kullanılmaz

---

## 5. Kimlik Doğrulama

### Giriş

- `/login` sayfasından kullanıcı adı + parola ile giriş
- Parola Werkzeug PBKDF2-SHA256 ile hash'lenir
- Her başarısız girişten sonra sayaç artar
- 5 başarısız denemede 5 dakika kilitlenir
- Oturum 8 saat sonra otomatik kapanır

### Çıkış

`/logout` route'u oturumu temizler ve giriş sayfasına yönlendirir.

---

## 6. Güvenlik Olayları (security.log)

Her olayda: `timestamp | event=TIP | user=KULLANICI | ip=IP | detail=DETAY`

| Olay | Açıklama |
|------|----------|
| `LOGIN_OK` | Başarılı giriş |
| `LOGIN_FAIL` | Başarısız giriş (parola loglanmaz) |
| `LOGOUT` | Çıkış |
| `BOT_START` | Bot başlatıldı |
| `BOT_STOP` | Bot durduruldu |
| `SETTINGS_CHANGE` | Strateji ayarları değiştirildi |
| `COIN_ADD` | Coin eklendi |
| `COIN_DEL` | Coin silindi |
| `KILL_SWITCH` | Acil durdur etkinleştirildi/kapatıldı |
| `ADAPTIVE_CHANGE` | Uyarlanabilir motor ayarları değiştirildi |
| `PAPER_MODE_ACTIVE` | Başlangıçta PAPER modu doğrulandı |
| `STARTUP` | Uygulama başlatıldı |
| `CSRF_FAIL` | CSRF token hatası |

---

## 7. Yedekleme

### Yedek Alma

```bash
bash backup.sh
```

Yedekler `backups/YYYYMMDD_HHMMSS/` klasörüne kaydedilir.
7 günden eski yedekler otomatik silinir.

### Yedeklenen Dosyalar

- `config.json` — strateji ayarları
- `state.json` — PAPER trading durumu
- `*.jsonl` — karar, risk, öğrenme logları
- `alpha20.log`, `bot_process.log` — bot logları
- `smart_config.json`, `safety_state.json`
- `security.log` — güvenlik denetim izi

### Dahil Edilmeyen

- `.env` — gerçek sırlar içerir
- `backups/` — özyinelemeli önlenir

### Geri Yükleme

```bash
# 1. Hedef yedek klasörünü seç
ls backups/

# 2. Dosyaları kopyala (mevcut dosyaları yedekle)
cp alpha20_v1/config.json alpha20_v1/config.json.before_restore
cp backups/20260726_120000/config.json alpha20_v1/config.json
cp backups/20260726_120000/state.json alpha20_v1/state.json

# 3. Uygulamayı yeniden başlat
python app.py
```

---

## 8. API Anahtarı Sızıntısı Durumunda

Bu sistem Binance API anahtarı kullanmaz. Ancak `FLASK_SECRET_KEY` veya `ADMIN_PASSWORD_HASH` sızdıysa:

1. **Hemen:** Tüm aktif oturumları geçersiz kılın (uygulamayı yeniden başlatın)
2. **Yeni SECRET_KEY üretin:** `python3 -c "import secrets; print(secrets.token_hex(32))"`
3. **Yeni parola hash'i üretin** ve `ADMIN_PASSWORD_HASH`'i güncelleyin
4. **Security log'u inceleyin:** `tail -100 security.log`
5. **Eski değerleri Replit Secrets'tan kaldırın**

---

## 9. Güncelleme Prosedürü

1. `bash backup.sh` ile yedek alın
2. Güncellemeleri uygulayın
3. `python -m pytest tests/` ile testleri çalıştırın
4. `python app.py` ile uygulamayı başlatın ve login testini yapın
5. `security.log`'u kontrol edin

---

## 10. Bilinen Kısıtlamalar

- Tek admin hesabı desteklenir (RBAC yok)
- Rate limiting bellekte saklanır; uygulama yeniden başlarsa sıfırlanır
- HTTPS sertifikası deployment katmanında yapılandırılmalıdır (Replit otomatik sağlar)
- Session'lar sunucu tarafında saklanmaz; SECRET_KEY değişirse tüm oturumlar geçersiz olur
