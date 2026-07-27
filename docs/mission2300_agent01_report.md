# Mission 2300 — Agent 01: Trading Home Temeli

## Felsefe

Kullanıcı trader DEĞİLDİR; yapay zekâ trader'dır, kullanıcı portföy
sahibidir. Trading Home bu felsefeyi yansıtan, teknik gürültüden
arındırılmış varsayılan ana sayfadır. Operation Center'a DOKUNULMADI;
menüden erişilebilir durumda.

## Kapsam disiplini (frontend-only)

- Backend / API / veritabanı / servis / otomasyon motoru: değişiklik YOK.
- `app.py`'deki tek dokunuş: `GET /home` sayfa render rotası +
  giriş sonrası varsayılan `next_url`'ün `/home` yapılması.
- Veri yalnız MEVCUT uçlardan okunur:
  `/api/operation-control/{status,positions,orders,products,signals}`,
  `/api/operation-control/workspace/{portfolio,journal}`,
  `/api/v1/global/account`, `/api/v1/tr/account`.
- Tek yazma eylemi: mevcut kontrollü kapatma niyeti ucu
  (`/positions/<id>/close`, neden + ONAYLIYORUM + idempotency).

## Yerleşim

- **Yapışkan üst çubuk:** Portföy Değeri, Bugünkü Kazanç, Aktif
  İşlemler, Sıradaki İşlemler, Otonom Mod, Bot Durumu, Cüzdan
  Bağlantısı — teknik bilgi yok.
- **Sol panel — Cüzdanlar:** Binance Global (Vadeli) + Binance TR;
  ad, bakiye, kullanılabilir bakiye, durum, son eşitleme. Ayar yok.
- **Orta panel — Aktif İşlemler (en büyük):** varlık, yön
  (Yükseliş/Düşüş), anlık kazanç, süre, durum, Kapat düğmesi.
  RSI/EMA/MACD ve hiçbir gösterge YOK (testle kilitli).
- **Sağ panel — Sıra:** Bekliyor / Hazırlanıyor / Yürütülüyor /
  Kapanıyor rozetleri; mevcut anlık görüntüden dürüstçe türetilir
  (products.entry_eligible → bekliyor, sinyal SUBMITTED →
  hazırlanıyor, açık emir → yürütülüyor, CLOSE_REQUESTED → kapanıyor).
- **Alt — Son Hareketler:** denetimli günlükten sade dille
  ("BTCUSDT işlemi açıldı" gibi), en yeni 12 olay.

## Dürüstlük

- Hesap API'si başarısızsa cüzdan kartı UNKNOWN gösterir; bakiye
  asla uydurulmaz.
- Bilinmeyen her değer gri UNKNOWN; sahte 0 yok.
- Gerçek zamanlılık: 12 sn yoklama (bu depoda SSE/WebSocket yasak).

## Test sonucu

- 82 yeni PASS (`tests/test_mission2300_trading_home.py` +
  güncellenen navigasyon testi).
- Tam regresyon: **12.839 PASS + 1 bilinen skip, 0 FAIL**
  (taban 12.757 + 82).

## Mimari inceleme

Bağımsız inceleme: **PASS**, engelleyici bulgu yok. Kapsam
disiplini, XSS kaçışları, dürüst UNKNOWN davranışı ve tek yazma
yolunun kontrollü niyet ucu olduğu doğrulandı.

---

# Mission 2300 — Agent 02: AI Karar Panosu

Trading Home, "Yapay zekâm şu an ne yapıyor?" sorusuna 5 saniyede
yanıt veren otonom yatırım panosuna dönüştürüldü (yine frontend-only).

- **Büyük mod kartı:** OTONOM / DANIŞMAN + basit durum rozeti
  (Çalışıyor / Duraklatıldı / Hata—Acil Durduruldu / Çevrimdışı);
  iç otomasyon mantığı sızdırılmaz.
- **Aktif işlemler:** Varlık, Yön, Giriş Fiyatı, Anlık Kazanç, Süre,
  Durum (Yönetiliyor / Kademelendiriliyor / Kapatılıyor / Çıkış
  Bekliyor / Acil Çıkış / Tamamlandı); tek eylem: Kapat. Bilinmeyen
  iç durum ham haliyle gösterilmez.
- **Sıra:** Hazır / Bekliyor / Hazırlanıyor / Yürütülüyor / Kapanıyor —
  açıklamasız; her sembol tek durumla görünür (çelişki yasak).
- **Son hareketler:** en yeni üstte (istemci tarafında da sıralanır),
  en fazla 20 kayıt, yalın dil; operatör olayları teknik detay
  sızdırmaz.
- **Yasaklar testle kilitli:** RSI/EMA/MACD/ADX, güven yüzdesi,
  strateji içi bilgiler, karar gerekçeleri, JSON/teknik günlük.
- Durum ucu düşerse mod kartı ve üst çubuk birlikte UNKNOWN/Çevrimdışı
  olur — bayat değer kalmaz.

Test: 27 yeni PASS (dosya toplamı 109); tam regresyon
**12.866 PASS + 1 skip, 0 FAIL**. Mimari inceleme: ilk turda 4 bulgu,
tümü düzeltilip regresyon testiyle kilitlendi → **PASS**.

---

# Mission 2300 — Agent 03: Hesaplarım (My Accounts)

Ayarlar → Hesaplarım: kullanıcının kendi yatırım hesaplarının
yapılandırıldığı TEK yer. Alpha Intelligence borsa değildir; bağlı
hesapları yöneten otonom yatırım işletim sistemidir.

## Mimari

- `accounts_registry.py` (saf modül): bağlayıcı kataloğu + kayıt
  defteri (`alpha20_v1/accounts.json`, atomik yazma + fcntl süreçler
  arası kilit). Yeni borsa = katalogda yeni kayıt; UI yeniden
  tasarımı gerekmez.
- Bağlayıcılar: PAPER, Binance Global, Binance TR (hazır);
  Bybit, OKX (bağlayıcı hazır değil — dürüstçe devre dışı, sahte
  Bağlan düğmesi yok).
- Sır İLKESİ: kayıt defteri sır SAKLAMAZ. Binance anahtarları ortam
  sırlarında yaşar; API yalnız maskeli anahtar döndürür
  (ABCD****XY89), gizli anahtar hiçbir zaman gösterilmez, panoya
  kopyalama engelli.

## Uçlar (CSRF+auth korumalı)

- GET `/api/accounts`, `/api/accounts/wallets`,
  `/api/accounts/portfolio`
- POST `/api/accounts/<id>/{connect,disconnect,primary,edit,test,sync}`
- Bağlantı testi mevcut dashboard_api sonuçlarını sade durumlara
  eşler (ham istisna yok). Depolama hatası sterile STORAGE_ERROR.

## Kurallar

- Tam olarak BİR birincil hesap; birincilin bağlantısı kesilemez;
  otomasyon çalışırken yürütme defteri (PAPER) kesilemez; bağlantısı
  kesik/hazır olmayan hesap birincil olamaz ve otomasyon uygunluk
  listesine (`execution_eligible`) asla girmez.
- Toplam portföy = bağlı hesapların Decimal toplamı; herhangi bir
  bileşen bilinmiyorsa toplam UNKNOWN — asla tahmin edilmez
  (TRY→USDT dönüşümü tahmin gerektirdiğinden TRY≠0 iken hesap değeri
  UNKNOWN kalır).

## Trading Home entegrasyonu

Cüzdan paneli artık YALNIZ `/api/accounts/wallets`'tan okur;
`/api/v1/global/account` ve `/api/v1/tr/account` doğrudan çağrıları
kaldırıldı. Kart, borsa-bağımsız alanlardan üretilir — yeni borsa
Trading Home değişikliği gerektirmez. Yerleşim değişmedi.

## Test ve inceleme

- 81 yeni PASS (`tests/test_mission2300_my_accounts.py` + Trading
  Home test güncellemeleri); yazma-yüzeyi kilidi bilinçli genişletme
  yorumuyla güncellendi.
- Tam regresyon: **12.918 PASS + 1 skip, 0 FAIL**.
- Mimari inceleme: **PASS**; iki sağlamlaştırma önerisi (sterile
  depolama hatası + süreçler arası kilit) uygulandı ve testle
  kilitlendi.
