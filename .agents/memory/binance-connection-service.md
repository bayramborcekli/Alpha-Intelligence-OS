---
name: Binance connection service
description: Kanonik bağlantı servisi kuralları, route-guard genişletme deseni, DPAPI saklama davranışı
---

- `services/binance_connection.py` TEK kanonik bağlantı yolu: test + izin
  politikası (canWithdraw/canTrade → PERMISSION_DENIED, alan yoksa
  UNVERIFIED) + `exchange_credentials.save_local` saklama + audit.
  **Why:** `tests/test_architecture_guard_accounts.py` duplicate imzalı
  hesap-fetch literallerini (`/api/v3/account`, `/open/v1/account/spot`)
  dashboard_api dışındaki dosyalarda yasaklar; windows_setup_flow bu
  yüzden bcn.connect'e delege eder. **How to apply:** yeni bir yüzey
  (CLI/UI/agent) bağlantı testi isterse bu servisi çağır, literal path
  yazma.
- Yeni yazma rotası eklerken `tests/test_mission1500_1_regression.py`
  `ALLOWED_WRITE_ROUTES` bilinçli-genişletme yorumuyla TAM rota olarak
  güncellenir (wildcard yok, testi gevşetme yok).
- `exchange_credentials.py` Windows'ta DPAPI (`enc:"dpapi"`) ile yazar;
  çözülemeyen giriş fail-closed None döner. Linux/Replit testleri yalnız
  fail-closed'u doğrulayabilir; gerçek DPAPI doğrulaması Windows ister.
- Futures durumu her zaman NOT_TESTED (spot-only tombstone); guard'a
  public `/fapi/v1/ping` bilinçli istisna olarak eklendi.
