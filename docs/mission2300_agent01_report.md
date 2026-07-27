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
