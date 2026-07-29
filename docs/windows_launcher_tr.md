# Alpha Intelligence OS — Windows Masaüstü Kurulum ve Başlatma

## Mimari (tek akıl: `launcher_windows.py`)

```
INSTALL_WINDOWS.cmd ─┐
start_alpha.cmd ─────┼──> launcher_windows.py ──> .venv\Scripts\python.exe
stop_alpha.cmd ──────┘         │                      └─> serve_windows.py
                               │                          (waitress, 127.0.0.1:5000)
                               ├─ runtime\launcher.log  (log; secret YAZILMAZ)
                               └─ runtime\alpha.pid     (pid + root + zaman)
```

- Proje kökü daima `launcher_windows.py` dosyasının konumundan bulunur
  (`%~dp0` + `Path(__file__)`); sabit sürücü/kullanıcı yolu yoktur.
- Sunucu YALNIZ bu clone'un `.venv` python'u ile çalışır; sistem
  Python'u sadece `.venv` oluşturmak için kullanılır.
- Global PATH asla değiştirilmez; git gerektirmez (ZIP klonda çalışır).

## Temiz klonda kurulum

1. Depoyu istediğiniz klasöre kopyalayın (git clone veya ZIP).
2. Proje köküne `.env` koyun (bkz. `.env.example`) — yalnız salt-okunur
   exchange anahtarları. Windows'ta `.env`, bayat OS ortam değişkenlerini
   geçersiz kılar (tek yükleyici: `local_env.py`).
3. `INSTALL_WINDOWS.cmd` çift tıklayın:
   - Python 3.11+ tespiti (`py -3` tercih edilir)
   - `.venv` oluşturma + `requirements.txt` kurulumu (idempotent)
   - Masaüstü kısayolu ("Alpha Intelligence OS" → bu clone'un
     `start_alpha.cmd`'ı; eski clone kısayolu otomatik düzelir)
   - Smoke test (uygulama import edilir; sunucu açılmaz)

## Başlatma / durdurma

- **Başlat:** masaüstü kısayolu veya `start_alpha.cmd`.
  Sıra: bootstrap denetimi → canlı `/health` kopya koruması →
  port 5000 teşhisi → sunucu → hazır olunca tarayıcı `/home`.
  Hata halinde pencere açık kalır, çıkış kodu ve
  `runtime\launcher.log` gösterilir; tarayıcı bozuk sayfaya AÇILMAZ.
- **Durdur:** `stop_alpha.cmd`. Yalnız `runtime\alpha.pid` içindeki,
  kimliği üç kademede doğrulanan (canlı PID + bu clone kökü + komut
  satırında `serve_windows.py`) süreç durdurulur; ilgisiz python
  süreçlerine ve eski clone'lara asla dokunulmaz.

## Port 5000 teşhisi

| Durum | Davranış |
|---|---|
| Alpha sağlıklı çalışıyor | Yeni kopya açılmaz; tarayıcı `/home` |
| Eski clone Alpha'sı | Net hata: önce onu durdurun (kök yolu gösterilir) |
| Yabancı uygulama | Net hata: "Port 5000 başka uygulama tarafından kullanılıyor" |
| Bayat PID dosyası | Otomatik temizlenir, başlatma sürer |

## Exchange bağlantısı (Windows yerel)

- **Binance TR:** `binance_tr_client.py` (base `https://www.binance.tr`,
  salt-okunur allowlist, `trust_env=False`, varsayılan TLS doğrulaması).
- **Binance Global SPOT:** `binance_global_client.py`
  (base `https://api.binance.com`, yalnız `/api/v3/time`, `/api/v3/account`,
  `/api/v3/ticker/price`). Ortak taşıma katmanı: `exchange_transport.py`.
- **Futures:** tamamen devre dışı (Spot-only mod). Hiçbir `/fapi/*`
  çağrısı yapılmaz; panelde DISABLED görünür ve uyarı üretilmez.
  (Yalnız geliştirici bayrağı `ALPHA_FUTURES_ENABLED=1` ile açılır.)

## Linux/Replit üretim yolu

Değişmedi: `gunicorn -c gunicorn.conf.py app:app` (0.0.0.0:5000,
2 worker). Windows girişleri (`serve_windows.py` + launcher) yalnız
masaüstünde kullanılır; Replit'te `.env` okunmaz, process env kazanır.

## Bot başlat/durdur manuel test protokolü (panelden, gerçek Windows)

Bot süreç yönetimi platform-bağımsızdır: PID dosyası
(`alpha20_v1\.bot.pid`) + ctypes `OpenProcess` canlılık kontrolü;
Windows'ta başlatma `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` ile,
durdurma `os.kill(pid, SIGTERM)` (Windows'ta `TerminateProcess`) ile
yapılır. Linux simülasyon testleri `tests/test_windows_proc_compat.py`
içindedir; gerçek makine doğrulaması için aşağıdaki adımları izleyin:

1. **Başlat:** Panel açıkken bot "Başlat" düğmesine basın.
   - Beklenen: "Bot başlatıldı." mesajı; durum kısa sürede "çalışıyor".
   - Kontrol: `alpha20_v1\.bot.pid` dosyası oluşur ve
     `{"pid": <N>, "started_at": ...}` içerir; Görev Yöneticisi'nde
     bu PID'li bir `python.exe` görünür.
2. **Durum kalıcılığı:** Sayfayı yenileyin → durum yine "çalışıyor"
   (canlılık `OpenProcess` ile doğrulanır, `/proc` gerekmez).
3. **Çifte başlatma koruması:** Bot çalışırken tekrar "Başlat" →
   "Bot zaten çalışıyor." mesajı; ikinci süreç AÇILMAZ.
4. **Durdur:** "Durdur" düğmesine basın.
   - Beklenen: "Bot durduruldu."; durum "durdu"; `.bot.pid` SİLİNİR;
     Görev Yöneticisi'nde PID kaybolur. (Windows'ta durdurma
     koşulsuzdur — `TerminateProcess`; nazik kapanış beklenmez.)
5. **Bayat PID temizliği:** Bot kapalıyken `.bot.pid` içine ölü bir PID
   yazın (örn. `{"pid": 999999}`) → panel "durdu" gösterir; "Durdur"
   "çalışan bot bulunamadı" der ve bayat dosyayı siler.
6. **Panel bağımsızlığı:** Bot çalışırken paneli (`stop_alpha.cmd`)
   kapatın → bot süreci YAŞAMAYA devam eder (detached); panel tekrar
   açıldığında durum yine "çalışıyor" görünür.

Herhangi bir adım beklenenden saparsa `runtime\launcher.log` ve
`alpha20_v1\bot_process.log` dosyalarını kaydedip bildirin.

## Manuel Windows test matrisi (özet)

1. Temiz klon + `INSTALL_WINDOWS.cmd` → kurulum tamam mesajı.
2. Kısayoldan başlat → tarayıcı `/home` açılır.
3. İkinci kez başlat → kopya açılmaz, mevcut sekmeye yönlenir.
4. `stop_alpha.cmd` → süreç kapanır; ilgisiz python süreçleri yaşar.
5. Bozuk `runtime\alpha.pid` → başlatma yine çalışır (bayat temizlik).
6. `.env` içindeki TR + Global Spot anahtarları → panelde bakiye görünür;
   log'larda yalnız `present/source/length` görünür, değer görünmez.
