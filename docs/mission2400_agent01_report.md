# Mission 2400 — Agent 01: Windows Tek-Tık Başlatıcı

## 1. Keşif (mevcut proje)
- Proje kökü: mevcut yerel klasör (sabit sürücü varsayımı yok).
- Mevcut başlatma: `gunicorn -c gunicorn.conf.py app:app` (Linux) —
  gunicorn ve `fcntl` Windows'ta yoktur.
- Sağlık ucu: `GET /health` (kimlik doğrulamasız). Trading Home: `/home`.
- Port: 5000. Tek Flask uygulaması; ayrı servis gerekmez.
- Gizli değerler ortam değişkenlerinden okunur (SESSION_SECRET,
  ALPHA_OWNER_*, borsa anahtarları) — mekanizma değişmedi.

## 2. Oluşturulan dosyalar
- `portable_flock.py` — fcntl'in Windows uyumluluk katmanı.
  POSIX'te GERÇEK fcntl'e birebir vekâlet (Linux davranışı sıfır
  değişiklik; testle kanıtlı). Windows'ta msvcrt.locking.
- 5 paylaşımlı-durum modülünde `import fcntl` →
  `try: fcntl / except ImportError: portable_flock` (Linux'ta ilk dal).
- `serve_windows.py` — waitress tek süreç, yalnız 127.0.0.1:5000.
- `start_alpha.cmd` → `tools/windows/launch_alpha.ps1` — dinamik kök,
  ortam doğrulama, canlı /health kopya koruması, gizli başlatma,
  hazırlık beklemesi (≤60 sn) SONRA tarayıcı, sanitize
  `runtime\launcher.log`, Türkçe hata kutuları.
- `stop_alpha.cmd` → `tools/windows/stop_alpha.ps1` — üçlü kimlik
  (pid + süreç başlama zamanı + bu projenin serve_windows.py TAM
  yolu) doğrulanmadan HİÇBİR sürece dokunmaz; imaj adına göre toplu
  öldürme yok; kilit dosyası yalnız kapanıştan sonra silinir.
- `tools/windows/create_shortcuts.ps1` — masaüstüne "Alpha
  Intelligence OS" ve "— Durdur" kısayolları (yönetici gerekmez).
- `docs/windows_launcher_tr.md` — kurulum + manuel kabul listesi.
- `tests/test_mission2400_windows_launcher.py` — 29 test.

## 3. Kopya koruması
Canlı `/health` denetimi birincil (bayat PID'e güvenilmez); ikinci
çift tık yalnız tarayıcıyı açar. PID meta dosyası yalnız durdurma
kimliği içindir.

## 4. Güvenlik
Betikler hiçbir gizli değişkeni okumaz/loglamaz/argüman yapmaz
(testle kilitli); sunucu yalnız 127.0.0.1'e bağlanır; yeni düz-metin
gizli dosyası oluşturulmaz.

## 5. Test ve regresyon
- Mission 2400 paketi: 29 PASS (POSIX vekâlet kanıtı, süreçler arası
  NB kilit çakışması, betik sözleşmeleri, sızıntı denetimi).
- Mimari inceleme: durdurma kimliği bulgusu → üçlü kimlik + negatif
  sözleşme testleriyle giderildi.
- Tam regresyon: **13.012 PASS + 1 bilinen skip, 0 FAIL**.
- Linux üretim yolu (gunicorn 2 worker) değişmedi; Replit iş akışı
  yeniden başlatıldı ve çalışıyor.

## 6. Bilinen sınırlar
- Bu ortam Linux olduğundan Windows manuel kabul testi BURADA
  KOŞULAMADI; `docs/windows_launcher_tr.md` içindeki adımlarla
  kullanıcı makinesinde koşulmalıdır.
- Windows'ta sunucu tek süreçtir (waitress); Linux'taki 2-worker
  yapı Windows'a taşınmaz.
