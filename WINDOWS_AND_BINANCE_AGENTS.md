# Windows Runtime Recovery Agent ve Binance Connection Agent

## Windows Runtime Recovery Agent nedir?
Windows makinede Alpha Intelligence OS'u tek adımda yeşile getiren kalıcı
sistem agent'ıdır. Yetenekleri: Git bulma/kurma/güncelleme, Python/.venv
hazırlama, requirements + certifi/truststore kurulumu, `.env` güvenli
onarımı, Binance public veri kontrolü, yalnız Alpha süreçlerini kapatma,
`serve_windows.py` başlatma, `/health/runtime` doğrulama ve tek FINAL
PASS/FAIL raporu.

Üç çağırma yolu (üçü de aynı Python servislerini kullanır):
1. **`SETUP_AND_START_WINDOWS.cmd`** — çift tıklanır; önerilen yol.
2. Panel: Genel Bakış → Bağlantılar / (yerel) agent durumu.
3. Yerel endpoint: `POST /api/agents/windows-runtime/run` — yalnız yerel
   Windows'ta, oturum + CSRF korumalı. Replit/public üzerinden Windows
   makine işlemi yapılamaz (403).

## SETUP_AND_START_WINDOWS.cmd nasıl kullanılır?
Proje klasöründeki dosyaya çift tıklayın. Betik yarıda durmaz, ikinci
çalıştırma istemez; sonunda RUNTIME CARD 🟢/🟡/🔴 içeren tek FINAL raporu
gösterir. 🟢 değilse rapordaki ROOT CAUSE satırı tek gerçek nedeni söyler.

## Binance Global API anahtarı nasıl oluşturulur?
1. Binance → Profil → **API Management** → *Create API*.
2. Yetkilerde **yalnız "Enable Reading"** açık kalsın.
3. **Enable Spot & Margin Trading, Enable Withdrawals, Enable Futures**
   KAPALI olmalı. Bu yazılım salt okunur çalışır; işlem/çekim yetkili
   anahtarlar bağlantı sihirbazı tarafından **reddedilir ve kaydedilmez**.
4. (Önerilir) *Restrict access to trusted IPs only* ile Windows
   makinenizin dış IP'sini ekleyin. IP kısıtı nedeniyle bağlantı
   reddedilirse panel size yalnız "bu IP'ye izin verin" işlemini gösterir;
   anahtarı yeniden girmeniz gerekmez.

## Neden withdrawal/trading izinleri kapalı olmalı?
Anahtar sızsa bile salt okunur anahtar yalnız bakiye görüntüleyebilir;
para çekemez, emir gönderemez. Bu yazılımda canlı emir yolu zaten kalıcı
olarak kapalıdır (LIVE ORDERS: DISABLED), ancak savunma katmanı anahtarın
kendisinde başlar.

## Binance TR bağlantısı
Binance TR resmî API'siyle (www.binance.tr) ayrı adaptörden test edilir;
Global endpoint'leri TR için kullanılmaz. TR API'si yetki alanlarını her
zaman dönmediği için salt-okunurluk kanıtlanamıyorsa durum dürüstçe
`CONNECTED_PERMISSIONS_UNVERIFIED` (sarı) gösterilir — asla "READ ONLY
VERIFIED" gibi yanlış güven verilmez.

## Bağlantı sihirbazı
Panel → `/settings/binance`. İki bağımsız kart: **BINANCE GLOBAL** ve
**BINANCE TR** (birinin hatası diğerini bozmaz). Her kartta durum, son
test zamanı, hesap türü, yetki doğrulama durumu, maskeli API anahtarı ve
**Bağlan / Test Et / Güncelle / Bağlantıyı Kaldır** butonları vardır.
API Key/Secret maskeli forma bir kez girilir; secret bir daha
gösterilmez. `SETUP_AND_START_WINDOWS.cmd` yeşil bittiğinde bu sayfayı
tarayıcıda açmayı önerir.

## Durum kodları
| Kod | Anlamı |
|---|---|
| `NOT_CONFIGURED` | Anahtar girilmemiş; Paper sistem yine de çalışır |
| `TESTING` | Test sürüyor |
| `CONNECTED_READ_ONLY` | Bağlı; salt okunurluk API'den doğrulandı |
| `CONNECTED_PERMISSIONS_UNVERIFIED` | Bağlı; API yetki alanı vermedi (sarı) |
| `INVALID_CREDENTIALS` | Anahtar/secret geçersiz |
| `IP_RESTRICTED` | Anahtar IP kısıtlı; panel gerekli IP'yi gösterir |
| `PERMISSION_DENIED` | İşlem/çekim yetkili anahtar reddedildi |
| `TIMESTAMP_DRIFT` | Saat farkı; otomatik sunucu-zamanı offset'iyle tekrar denenir |
| `NETWORK_DEGRADED` | TLS/DNS/zaman aşımı/rate-limit kaynaklı geçici sorun |
| `DISCONNECTED` | Bağlantı kullanıcı tarafından kaldırıldı |
| `ERROR` | Sınıflandırılamayan hata |

Futures: Spot-only mimari gereği imzalı Futures erişimi bu üründen kalıcı
olarak kaldırılmıştır; Futures durumu `NOT_TESTED` gösterilir ve Global
Spot bağlantısını etkilemez.

## Credential'lar nerede ve nasıl korunur?
- **Windows:** `data/exchange_credentials.json` içinde **DPAPI ile
  kullanıcı/makineye bağlı şifreli** saklanır. Dosya başka bilgisayara
  kopyalanırsa otomatik açılamaz.
- **Replit:** Yerel dosya deposu devre dışıdır; anahtarlar Replit
  Secrets'ta yönetilir; repository dosyasına asla yazılmaz.
- Loglarda/audit kayıtlarında/API yanıtlarında yalnız maskeli anahtar
  (`ABCD************`) bulunur; secret hiçbir çıktıda yer almaz.
- Audit: `data/connection_audit.jsonl` — bağlantı denemesi/başarı/ret/
  güncelleme/silme olayları, secret'sız.

## Bağlantı nasıl kaldırılır?
`/settings/binance` → ilgili kartta **Bağlantıyı Kaldır**. Anahtar yerel
şifreli depodan silinir ve durum `NOT_CONFIGURED` olur.

## Kalıcılık ve tek kaynak prensibi (PERMANENT CONFIGURATION)
Ayarların TEK kaynağı vardır; aynı ayar iki yerde tutulmaz:

| Ne | Nerede | Kalıcılık |
|---|---|---|
| Kod | GitHub `main` (git pull ile güncellenir) | Kalıcı |
| Runtime ayarları (`FLASK_ENV`, `LOCAL_DEV_BYPASS`, `PAPER_MODE`, `ALPHA_WINDOWS_PAPER_AUTO`) | Yerel `.env` (git dışı) | `git pull` ve SETUP silmez; SETUP yalnız eksik/hatalı değeri onarır ve önce `.env.backup_<timestamp>` yedeği alır |
| Binance Global/TR credential | Windows **DPAPI** şifreli depo (`services/secure_credentials.py` tek kanonik servis) | Uygulama yeniden başlasa, SETUP tekrar çalışsa, `git pull` yapılsa da **korunur** |
| Secret'sız bağlantı durumu | `data/integration_status.json` (yalnız provider, connection_status, permission_status, account_type, futures_status, masked_api_key, last_test, last_error_code, credential_store) | Silinirse DPAPI kaydından otomatik yeniden oluşturulur |

Kurallar:
- API Secret proje klasörüne **asla düz metin yazılmaz**; hiçbir ekranda
  tekrar gösterilmez; tam API Key loglanmaz.
- Credential'ı yalnız paneldeki **"Bağlantıyı Kaldır"** düğmesi siler.
- Bilgisayar veya Windows kullanıcısı değişirse DPAPI kaydı açılamaz
  (fail-closed, ERROR görünür) — bağlantının panelden yeniden kurulması
  gerekir; bu bilinçli bir güvenlik özelliğidir.
- SETUP_AND_START_WINDOWS.cmd tekrar çalıştırılabilir: mevcut bağlantıyı
  görür, "zaten yapılandırılmış" der, otomatik test eder, anahtar istemez.
- Sunucu açılışında kayıtlı credential varsa arka planda otomatik
  read-only test yapılır; geçici ağ/TLS hatası credential'ı silmez.
- `.env.example` yalnız örnektir; runtime tarafından okunmaz.
- `tools/mission13*.py` dosyaları geçmiş misyon kanıtı olarak donmuş,
  elle çalıştırılan tanılama betikleridir; runtime credential yolu
  değildir ve hiçbir yerde credential SAKLAMAZ.

## Live Trading
Bu yazılımda canlı emir gönderimi, emir iptali, kaldıraç/margin değişimi,
çekim ve transfer yolları **kalıcı olarak kapalıdır**. Tüm raporlarda
`LIVE ORDERS: DISABLED` görünür. Gerçek bir secret örneği bu belgede ve
hiçbir dosyada yer almaz.
