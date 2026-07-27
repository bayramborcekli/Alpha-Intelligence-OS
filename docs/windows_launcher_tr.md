# Windows Tek-Tık Başlatıcı (Mission 2400 — Agent 01)

Alpha Intelligence OS'u Windows masaüstünden **tek çift tıkla**
başlatma akışı. Uygulama mantığı, API sözleşmeleri ve Replit/Linux
üretim yolu (gunicorn) değişmedi.

## Nasıl çalışır

```
Masaüstü kısayolu "Alpha Intelligence OS"
    ↓
start_alpha.cmd  (küçültülmüş, hemen kapanır)
    ↓
tools\windows\launch_alpha.ps1
    ├─ proje kökünü dinamik çözer (C: varsayımı yok; D:\AlphaIntelligenceOS olabilir)
    ├─ temel doğrulama: app.py, serve_windows.py, Python/.venv, yazılabilir runtime\
    ├─ /health zaten cevap veriyorsa → kopya başlatmaz, tarayıcıyı açar
    ├─ port doluysa ama /health cevapsızsa → Türkçe hata, sayfa açılmaz
    ├─ python serve_windows.py'yi GİZLİ başlatır, PID'i runtime\alpha.pid'e yazar
    ├─ /health hazır olana dek bekler (≤60 sn)
    └─ hazırsa varsayılan tarayıcıda http://127.0.0.1:5000/home açılır
```

- **Sunucu girişi:** `serve_windows.py` — waitress, tek süreç, 8 iş
  parçacığı, yalnız `127.0.0.1` (gunicorn ve `fcntl` Windows'ta yok).
- **Kilit uyumluluğu:** `portable_flock.py` — paylaşımlı durum
  modülleri önce gerçek `fcntl`'i dener (Linux davranışı birebir
  aynı), yalnız Windows'ta msvcrt tabanlı katmana düşer.
- **Kopya koruması:** canlı `/health` denetimi + PID dosyası
  doğrulaması. İkinci çift tık yeni sunucu/işçi başlatmaz, yalnız
  Trading Home'u açar. Bayat PID dosyasına asla tek başına güvenilmez.
- **Günlük:** `runtime\launcher.log` — yalnız sabit, sanitize
  mesajlar (zaman damgası, istek, hazırlık sonucu, hata kodu).
  API anahtarı/gizli değer/ortam içeriği asla yazılmaz.

## Kurulum (tek sefer)

1. Proje klasörünü istediğiniz yere kopyalayın (ör. `D:\AlphaIntelligenceOS`).
2. [Python 3.11](https://www.python.org/downloads/) kurun ("Add to PATH" işaretli).
3. Proje klasöründe bir kez çalıştırın:
   ```
   py -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
4. Gerekli ortam değişkenlerini **Windows kullanıcı ortam
   değişkenleri** olarak tanımlayın (Başlat → "ortam değişkenleri").
   Gerekenler yalnız ad olarak: `SESSION_SECRET`,
   `ALPHA_OWNER_USERNAME`, `ALPHA_OWNER_PASSWORD_HASH` ve borsa
   anahtarları. Başlatıcı bunları **okumaz, loglamaz, kopyalamaz**;
   yeni düz-metin gizli dosyası oluşturulmaz.
5. Kısayolları oluşturun:
   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows\create_shortcuts.ps1
   ```
   Masaüstüne "Alpha Intelligence OS" ve "Alpha Intelligence OS —
   Durdur" kısayolları gelir. Yönetici hakkı gerekmez.

## Durdurma

"— Durdur" kısayolu (`stop_alpha.cmd` → `tools\windows\stop_alpha.ps1`):

1. `runtime\alpha.pid`'deki PID'i okur.
2. Sürecin yaşadığını **ve** komut satırında `serve_windows.py`
   geçtiğini doğrular — ilgisiz python süreçlerine dokunmaz; toplu
   `taskkill /IM python.exe` kullanılmaz.
3. Önce kibarca (`taskkill /PID <pid> /T`), 3 sn sonra gerekirse
   zorla durdurur. PID dosyası yalnız kapanıştan sonra silinir.

Tarayıcıyı kapatmak uygulamayı durdurmaz (mevcut mimari); uygulama
Durdur kısayoluna kadar arkaplanda çalışmaya devam eder.

## Manuel kabul testi (Windows'ta koşulmalı)

1. Uygulama tamamen kapalıyken masaüstü kısayoluna çift tıkla.
2. Hiçbir komut yazmadan uygulamanın başladığını doğrula.
3. Trading Home'un otomatik açıldığını doğrula.
4. PAPER modunun aktif kaldığını, canlı emrin kapalı olduğunu doğrula.
5. Kısayola ikinci kez çift tıkla → kopya süreç/işçi başlamadığını
   doğrula (`runtime\launcher.log`: "Zaten calisiyor").
6. Tarayıcıyı kapat → uygulamanın çalışmaya devam ettiğini doğrula.
7. Durdur kısayoluyla durdur; ilgisiz python süreçlerinin
   etkilenmediğini doğrula (Görev Yöneticisi).
8. Yeniden başlat ve normal açılışı doğrula.

## Bilinen sınırlar

- Bu ortam Linux olduğu için Windows kabul testi burada
  KOŞULAMADI; yukarıdaki listeyle kullanıcı makinesinde koşulmalıdır.
- Windows'ta sunucu tek süreçtir (waitress); Linux'taki 2-worker
  gunicorn yapısı Windows'a taşınmaz (gerek de yoktur).
- `Get-NetTCPConnection` Windows 8+ gerektirir (desteklenen tüm
  güncel Windows sürümlerinde vardır).
