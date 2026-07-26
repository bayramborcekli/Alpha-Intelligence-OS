# Intelligence Automation (Mission 1600)

PAPER-only Alpha Intelligence OS içinde, mevcut Intelligence Engine
özetinin belirli aralıklarla otomatik üretilip **append-only** Workspace
zaman çizelgesine kaydedilmesini sağlayan katman. Tamamı salt-okunur
mimariye uyar: **hiçbir borsa yazma isteği yoktur ve olamaz.**

Bu doküman mevcut davranışı açıklar; sözleşmelerin kanıtı test
süitleridir (`tests/test_mission1600_*.py`, toplam 154 test).

---

## 1. Mimari

```
Automation Scheduler (worker içi daemon thread, gunicorn post_fork)
        ↓
Automation Core        automation_engine.py   (durum makinesi, flock, state)
        ↓
Automation Service     automation_service.py  (Intelligence köprüsü, normalize)
        ↓
Existing Intelligence Engine (intelligence_service — değiştirilmedi)
        ↓
append_snapshot()      — YALNIZ Core çağırır
        ↓
Workspace Timeline     intelligence_history.jsonl (append-only)
```

### Bileşen sorumlulukları

| Bileşen | Dosya | Sorumluluk | Yapamayacakları |
|---|---|---|---|
| Core | `automation_engine.py` | Durum makinesi, config, state dosyası, flock tekil-koşu kilidi, `run_once`, zamanlama kararı, `append_snapshot` çağrısı | Intelligence hesabı, HTTP |
| Service | `automation_service.py` | Gerçek IntelligenceService'i sarar, özeti 11 alanlık beyaz listeyle normalize eder, exception'ı sterile `FAILED`'e çevirir | `append_snapshot` (AST-testli yasak), Exchange |
| API | `app.py` (Mission 1600 bölümleri) | İnce route'lar: status / run / export; auth + CSRF; sterile HTTP zarfları | Intelligence'ı doğrudan çalıştırmak, snapshot yazmak |
| UI | `templates/automation.html` | Salt görüntüleme + manuel tetik; veriyi yalnız API'dan alır | İş mantığı, hesaplama, olmayan özellik düğmesi |
| Export | `automation_export_api.py` | Status görünümünü JSON/CSV olarak deterministik dışa aktarır | Koşu başlatmak, state değiştirmek |
| Scheduler | `automation_engine.start_loop` + `app.start_automation_scheduler` | Worker içi daemon döngü; `stop_event` ile kapanır | Varsayılan açık olmak |

### Durum makinesi

Geçiş tablosu (`_TRANSITIONS`):
`disabled → scheduled` · `scheduled → running | disabled` ·
`running → succeeded | failed` · `succeeded → running | disabled` ·
`failed → running | disabled`. Yani tekrar koşularda `succeeded`/`failed`
durumundan **doğrudan** `running`'e geçilir; `scheduled` yalnız
`disabled`'dan çıkışta görülür. Tablo dışı her geçiş `ValueError` üretir
(`transition()`). UI ayrıca `interrupted` (yalnız hata kodu olarak) ve
`unknown` durumlarını gösterir.

---

## 2. Yapılandırma (Config)

Yalnız ortam değişkenleriyle; çalışma zamanında değiştirme API'ı **yoktur**
(bilinçli karar — bkz. §8).

| Değişken | Varsayılan | Kural |
|---|---|---|
| `ALPHA_AUTOMATION_ENABLED` | kapalı | Yalnız `"true"` (büyük/küçük harf duyarsız) etkinleştirir; `"1"`, `"yes"` vb. ETMEZ |
| `ALPHA_AUTOMATION_INTERVAL_MINUTES` | 60 | Tamsayı; minimum 5 (altı 5'e sabitlenir); bozuk değer → varsayılan |
| `ALPHA_AUTOMATION_TIMEOUT_SECONDS` | 120 | Tamsayı; minimum 10; bozuk değer → varsayılan |
| `ALPHA_AUTOMATION_STATE_PATH` | `automation_state.json` | State dosyası konumu (test/işletme amaçlı) |

---

## 3. State Dosyası

`automation_state.json` — Core'un tekil doğruluk kaynağı.

**İçerdiği alanlar (tam şema, fazlası yazılAMAZ):**
`state`, `run_id`, `last_run_started_at`, `last_run_finished_at`,
`last_run_status`, `last_error_code`, `last_snapshot_recorded`,
`last_duration_seconds`.

**İçermedikleri:** secret, yol, PID, thread bilgisi, exception metni,
Intelligence payload'ı. Hata ayrıntısı yalnız sterile kod olarak saklanır.

- **Kim yazar:** yalnız Automation Core (`_save_state`).
- **Kim okur:** Core, Status API, Export (salt-okunur).
- **Atomik yazım:** `.tmp` dosyasına yaz + `os.replace` (yarım dosya imkânsız).
- **Kilit:** yan dosya `automation_state.json.lock` üzerinde
  `flock(LOCK_EX | LOCK_NB)`. Kilit alınamazsa koşu **atlanır**
  (`DUPLICATE_RUN`) — bekleme/kuyruk yoktur. `finally` bloğunda kilit her
  koşulda bırakılır.
- **Duplicate koruması:** süreçler-arası flock (2 gunicorn worker'ı dahil)
  + append yalnız kilit içinde. Aynı anda en fazla BİR koşu append edebilir.
- **Recovery:** restart sonrası `state=running` bulunursa koşu
  `failed` + `INTERRUPTED` olarak işaretlenir; otomatik yeniden deneme
  YOKTUR. `recover_interrupted()` aktif kilitli koşulara dokunmaz.
- **Bozuk dosya:** okunamayan/şema dışı state güvenli boş duruma
  (`disabled`) düşer; asla exception sızdırmaz.

---

## 4. Koşu Sözleşmesi (`run_once`)

1. Config kapalıysa: koşu yok (`skip_reason=DISABLED`).
2. flock alınamazsa: `DUPLICATE_RUN`, hiçbir yazma yok.
3. Özet üretimi (Service üzerinden). Exception → `EXECUTION_FAILED`.
4. Süre `timeout_seconds`'ı aşarsa → `TIMEOUT` (yumuşak, post-hoc).
5. Sonuç `status ∈ {OK, PARTIAL}` değilse → `INVALID_RESULT`.
6. Yalnız OK/PARTIAL sonuç `append_snapshot` ile **bir kez** yazılır;
   append denemesi başarısızsa `APPEND_FAILED` ve **asla retry yok**
   (mükerrer kayıt riski — Agent 01 kararı).
7. FAILED/UNAVAILABLE/TIMEOUT/INVALID sonuçlar **snapshot üretmez**.

Sterile hata kodları: `EXECUTION_FAILED`, `TIMEOUT`, `INVALID_RESULT`,
`APPEND_FAILED`, `INTERRUPTED`, `DUPLICATE_RUN`. Exception metni hiçbir
katmanda saklanmaz/gösterilmez.

---

## 5. API Sözleşmesi

Tüm uçların `/api/v1/...` takma adı vardır. Auth: mevcut `_security_gate`
(tek sahip oturumu). Tüm yanıtlar `Cache-Control: no-store, private` taşır.

### GET `/api/automation/status`

- **Amaç:** sterile, salt-okunur durum görünümü. **CSRF:** gerekmez (GET).
- **Parametre:** yok. **Content-Type:** `application/json`.
- **200 yanıtı:** `ok`, `read_only`, `advisory_only` + alanlar:
  `enabled`, `interval_minutes`, `state`, `running`, `run_id`,
  `last_run_started_at`, `last_run_finished_at`, `last_run_status`,
  `last_error_code`, `last_snapshot_recorded`, `next_due`
  (son bitiş + aralık; koşu yoksa `null`).
- **401:** anonim. Bilinmeyen değerler `null`'dur, asla 0 türetilmez.

### POST `/api/automation/run`

- **Amaç:** manuel tek koşu tetiği. Route incedir:
  `automation_service.run_automation`'a delege eder.
- **Auth:** oturum zorunlu. **CSRF:** global CSRFProtect — istisna yok;
  eksik/geçersiz token → 400. (Bilinen sıra: anonim POST'ta CSRF 400,
  auth 401'den önce gelir — erişim her iki durumda engellidir.)
- **Gövde:** boş JSON; kullanıcıdan hiçbir fonksiyon/yol/komut alınmaz.
- **200:** `{ok, read_only, advisory_only, ran, appended, error_code,
  final_state, run_id}`.
- **409 `DUPLICATE_RUN`:** kilit meşgul. **503 `AUTOMATION_DISABLED`:**
  ortam kapalı. **500 `AUTOMATION_ERROR`:** sterile beklenmedik hata.

### GET `/api/automation/export/status`

- **Amaç:** status görünümünün indirilebilir dışa aktarımı.
- **Parametre:** `format=json|csv` (varsayılan `json`); başka format →
  `400 INVALID_FORMAT`. **CSRF:** gerekmez (GET, salt-okunur).
- **Alanlar:** status API ile birebir aynı beyaz liste (yukarıdaki 11 alan).
- **JSON:** deterministik (sıralı anahtar, 2 girinti), `application/json`.
- **CSV:** `field,value` sabit satır sırası, UTF-8 BOM, CRLF, formül
  enjeksiyonu nötralize (`=`, `+`, `-`, `@`, TAB, CR önekli METİNLER `'`
  ile korunur; sayısal değerler bozulmaz), bilinmeyen → `—`.
- **Header'lar:** `Content-Disposition: attachment` — statik dosya adı
  `automation_status.json` veya `automation_status.csv`,
  `X-Content-Type-Options: nosniff`, `Cache-Control: no-store, private`.
- **503 `STATUS_UNAVAILABLE`:** durum kaynağı okunamadı (sterile).
- **History export YOKTUR** — bkz. §8.

### GET `/automation` (UI)

Oturum yönlendirmeli sayfa (anonim → 302 `/login`). Yalnız yukarıdaki iki
API'ı çağırır; 30 sn'de bir status polling (WebSocket/SSE yok). Yalnız
**Şimdi Çalıştır** ve **Yenile** eylemleri vardır; enable/disable düğmesi
bilinçli olarak yoktur. API değerleri `textContent` ile basılır (XSS
koruması); `innerHTML` yalnız beyaz-listeli durum rozeti içindir.

---

## 6. Scheduler ve Worker Modeli

- `gunicorn.conf.py` → `post_fork` → `app.start_automation_scheduler()`
  (universe_manager/auto_controller ile aynı kanıtlı desen).
- Varsayılan **kapalı**; yalnız `ALPHA_AUTOMATION_ENABLED=true` ile başlar.
- Worker başına en fazla BİR döngü (`threading.Lock` + canlılık kontrolü);
  thread `daemon=True` ve `stop_event` ile kontrollü kapanır.
- Başlatma hatası worker'ı düşürmez (sterile log, `None` döner).
- 2 worker → 2 döngü olabilir; **tekil koşu garantisi süreçler-arası
  flock'tadır**, dağıtık scheduler iddiası yoktur (Agent 01 kararı).
- `scheduler_tick` yalnız `should_run` (son bitiş + aralık geçtiyse)
  onaylarsa `run_once` çağırır; aktif koşu varken tick duplicate üretmez.

---

## 7. Güvenlik Garantileri

| Garanti | Mekanizma / kanıt |
|---|---|
| Workspace Read-Only | Automation hiçbir workspace ucuna yazmaz; timeline'a yalnız `append_snapshot` ile eklenir |
| Timeline Append-Only | Tek yazma yolu Core'daki `append_snapshot`; overwrite API'ı yok; duplicate flock'la engelli |
| Ledger / Audit Immutable | Automation modülleri bu modülleri import bile etmez (AST-testli) |
| Exchange Isolation | Exchange/ağ modülü import yasağı statik testli; Exchange Write Request = 0 |
| Advisory-Only | Service normalize'ı `advisory_only=true` değerini zorlar |
| Authentication / Authorization | Mevcut `_security_gate`; yeni rol/permission icat edilmedi; bypass yok |
| CSRF | Tek state-changing uç (`run`) global CSRFProtect altında; istisna/query-token yok |
| Secret Protection | Yanıt/state/export/UI'da secret alanı yok; gerçek ortam secret değerlerinin yanıtlarda geçmediği canlı testlidir |
| Deterministic Output | JSON sıralı anahtar; CSV sabit kolon/satır; bayt-düzeyi eşitlik testli |
| CSV Injection | `_cell` nötralizasyonu (formül önekleri) + `csv.writer` quote/newline escaping |
| XSS | UI `textContent`; sunucu API verisini HTML'e gömmez |
| Sterile Errors | Yalnız hata kodları; exception metni hiçbir yüzeyde yok (exception-flood testli) |
| Scheduler Safety | Varsayılan kapalı, tekil döngü, daemon, çökmeyen startup |

Doğrulama: `tests/test_mission1600_security_verification.py` (38 test:
statik analiz, secret taraması, dosya bütünlüğü, 16 penetrasyon senaryosu).

---

## 8. Mimari Kararlar (özet gerekçeler)

- **Neden Service ayrı?** Intelligence köprüsü ve normalize (politika)
  ile durum/kilit/zamanlama (mekanizma) ayrışır; Service sahte servisle
  test edilebilir, Core Intelligence'sız test edilebilir.
- **Neden `append_snapshot` yalnız Core'da?** Tek yazma noktası =
  append-only garantisinin denetlenebilir olması. Service/route'ta çağrı
  olmadığı AST ile test edilir.
- **Neden duplicate koruması Core'da?** Timeline'ın kendi append'i dedup
  yapmaz (1500.2 sözleşmesi değiştirilemez); koruma yazan katmanda olmalıdır.
- **Neden worker başına scheduler?** Yeni süreç/paket eklemeden mevcut
  gunicorn modeliyle çalışır; tekillik flock'la sağlandığından worker
  sayısından bağımsız güvenlidir.
- **Neden flock?** Süreçler-arası, işletim sistemi garantili, sahibi
  çökerse kendiliğinden düşer (stale-lock sorunu yok), stdlib-only.
- **Neden state dosyası (DB değil)?** 8 alanlık tekil kayıt; atomik
  `os.replace` yeterli; yeni bağımlılık/migration istenmedi.
- **Neden enable endpoint yok?** Yapılandırma ortam tabanlıdır; kalıcı
  runtime-config modeli repoda yoktur — kanıtsız endpoint eklenmedi.
- **Neden history export yok?** Çalışma geçmişi modeli yoktur (state yalnız
  son koşuyu tutar); tam geçmiş zaten Workspace timeline'ında ve onun kendi
  export'unda vardır. Yeni depo icat etmek yasaktı.
- **Neden sterile response?** Exception metni yol/host/iç ayrıntı
  sızdırabilir; tüm hatalar sabit kodlara indirgenir.

---

## 9. Operasyon Rehberi

**Açma:** Secrets/ortama `ALPHA_AUTOMATION_ENABLED=true` ekleyin ve
uygulamayı yeniden başlatın (`Start application` workflow'u). Doğrulama:
`GET /api/automation/status` → `"enabled": true`, `state: scheduled`.

**Kapatma:** değişkeni silin veya `false` yapın + restart. Çalışan koşu
restart'ta `INTERRUPTED` işaretlenir; veri bozulmaz, yeniden deneme olmaz.

**Aralık değiştirme:** `ALPHA_AUTOMATION_INTERVAL_MINUTES=<dk>` (min 5) +
restart.

**Restart/Shutdown:** her zaman güvenlidir — state atomik yazılır, kilit
süreçle birlikte düşer, `recover_interrupted` yarım koşuyu işaretler.

**İzleme:** UI `/automation` · `GET /api/automation/status` ·
`GET /health`. Log: uygulama logları sterile'dir (kod bazlı); secret veya
exception metni loglanmaz.

**Sorun giderme:**

| Belirti | Muhtemel neden | Eylem |
|---|---|---|
| `enabled: false` bekliyordunuz ama koşmuyor | Değer `"true"` değil (ör. `1`) | Değeri tam `true` yapın + restart |
| 409 `DUPLICATE_RUN` | Başka koşu aktif (diğer worker olabilir) | Bekleyin; status'ta `running` izleyin |
| `last_error_code: INTERRUPTED` | Koşu sırasında restart | Normal; bir sonraki tetik temiz başlar |
| `APPEND_FAILED` | Timeline yazılamadı | Disk/`ALPHA_INTELLIGENCE_HISTORY_PATH` kontrolü; retry bilinçli yok |
| `TIMEOUT` | Koşu `timeout_seconds`'ı aştı | Timeout'u artırın veya kaynak sorununu inceleyin |
| Export 503 `STATUS_UNAVAILABLE` | State dosyası okunamıyor | Dosya izin/disk kontrolü |

---

## 10. Geliştirici Rehberi

**Eklenebilir:** yeni salt-okunur görünümler/exportlar (mevcut state
sözleşmesi üzerinden), yeni sterile hata kodları (testleriyle), UI
iyileştirmeleri (yalnız mevcut API alanlarıyla).

**Eklenemez / değiştirilemez:**

1. `append_snapshot` çağrısı Core dışına taşınamaz; Service/route/export
   snapshot yazamaz (AST testleri kırar).
2. Route katmanı Intelligence çalıştıramaz; her zaman Service'e delege.
3. UI iş mantığı içeremez; veri kaynağı yalnız Automation API'larıdır.
4. Export salt-okunurdur; koşu tetikleyemez, state değiştiremez.
5. Exchange import/çağrısı tüm automation modüllerinde yasaktır.
6. Workspace'e yazılamaz; timeline overwrite edilemez.
7. Exception metni hiçbir yanıt/log/state alanına giremez (sterile kural).
8. Yeni paket bağımlılığı eklenemez (stdlib + mevcut Flask yığını).
9. `alpha20_v1/`, `auth.py`, ledger, audit, exchange imzalama
   dosyalarına dokunulamaz (proje geneli kural).
10. Kullanıcıdan modül/fonksiyon/yol/komut girdisi alınamaz; tek kullanıcı
    parametresi export'taki `format`'tır (beyaz listeli).

Yeni değişiklik teslim kuralı: test ekle → tam regresyon
(1400 + 1500.1 + 1500.2 + 1600) → mimari inceleme → commit/push.
