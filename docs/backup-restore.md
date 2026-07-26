# Alpha Intelligence OS — Yedekleme ve Geri Yükleme

**Sürüm:** 0.1.0-alpha  
**Son güncelleme:** ownership-baseline-v1

---

## Yedekleme

### Kapsam

| Dosya | Dahil | Neden |
|---|---|---|
| `alpha20_v1/config.json` | ✅ | Strateji ayarları |
| `alpha20_v1/smart_config.json` | ✅ | Akıllı seçim yapılandırması |
| `alpha20_v1/safety_state.json` | ✅ | Kill switch durumu |
| `alpha20_v1/state.json` | ✅ | PAPER pozisyon ve bakiye |
| `alpha20_v1/*.jsonl` | ✅ | Karar / risk / öğrenme logları |
| `alpha20_v1/alpha20.log` | ✅ | Bot işlem logu |
| `security.log` | ✅ | Güvenlik olayları |
| `.env` | ❌ | Sır içerir — asla yedeklenmez |
| `.git/` | ❌ | Sürüm kontrolü zaten yapar |
| `backups/` | ❌ | Özyineleme önlenir |

### Yedek Yapısı

```
backups/
└── YYYYMMDD_HHMMSS/
    ├── config.json
    ├── state.json
    ├── ...
    ├── MANIFEST.txt        ← Tarih, sürüm, metadata
    └── CHECKSUMS.sha256    ← SHA-256 bütünlük denetimi
```

### Yedekleme Komutu

```bash
bash backup.sh
```

### Retention Politikası

| Periyot | Saklama |
|---|---|
| Günlük | Son 7 gün |
| Haftalık | Son 4 hafta (Pazar yedekleri) |
| Aylık | Son 6 ay (ayın 1'i yedekleri) |

Otomatik temizlik `backup.sh` içinde tanımlıdır.

### Bütünlük Denetimi

Her yedek `CHECKSUMS.sha256` dosyası içerir.
Doğrulama:

```bash
cd backups/YYYYMMDD_HHMMSS
sha256sum -c CHECKSUMS.sha256
```

---

## Geri Yükleme

### Önce Daima Dry-Run

```bash
bash restore.sh --dry-run backups/YYYYMMDD_HHMMSS
```

Dry-run ne yapar:
- Yedeği bulur ve doğrular
- Bütünlük kontrolü yapar
- Hangi dosyaların üzerine yazılacağını listeler
- **Hiçbir şeyi değiştirmez**

### Gerçek Geri Yükleme

```bash
bash restore.sh backups/YYYYMMDD_HHMMSS
```

Geri yükleme adımları:
1. SHA-256 bütünlük denetimi — başarısız olursa DURUR
2. Mevcut veri üzerine yazmadan önce otomatik ön-yedek alır
3. Dosyaları geri yükler (atomic write ile)
4. `restore_audit.log` dosyasına olay yazar

### Geri Yükleme Prosedürü (Adım Adım)

```bash
# 1. Uygulamayı durdur
pkill -f alpha20 || true

# 2. Mevcut yedekleri listele
ls -la backups/

# 3. Dry-run ile kontrol et
bash restore.sh --dry-run backups/<TIMESTAMP>

# 4. Dry-run başarılıysa gerçek geri yükleme
bash restore.sh backups/<TIMESTAMP>

# 5. Uygulamayı yeniden başlat
python app.py
```

### Bozuk Yedek Davranışı

- SHA-256 doğrulaması başarısız → geri yükleme reddedilir
- `MANIFEST.txt` eksik → geri yükleme reddedilir
- `CHECKSUMS.sha256` eksik → geri yükleme reddedilir
- Hata mesajı `restore_audit.log` dosyasına yazılır

---

## Şifreleme (Önerilen)

Bu sürümde yedekler şifresiz tutulur. Hassas ortamlarda:

```bash
# Şifreli yedek oluştur (örnek — GPG)
tar -czf - backups/TIMESTAMP/ | gpg --symmetric --cipher-algo AES256 \
    -o backups/TIMESTAMP.tar.gz.gpg

# Şifreleme anahtarı kod içine YAZILMAZ — ayrı yönetilir.
```

Gerçek şifreleme anahtarı:
- Kod tabanında ASLA bulunmaz
- `.env` veya donanım güvenlik modülünde saklanır
- `.gitignore`'da listelenen `*.key` / `*.pem` uzantılarını kullanın
