---
name: Exchange credential resolver
description: Kanonik borsa credential çözümleme ve tek hesap fetch yolu kuralları
---

**Kural:** Binance credential'ları YALNIZ `exchange_credentials.py` çözer.
Kanonik isimler: `BINANCE_GLOBAL_API_Key`/`BINANCE_GLOBAL_Secret_Key`,
`BINANCE_TR_API_KEY`/`BINANCE_TR_API_SECRET`. Legacy alias'lar
(BINANCE_API_KEY vb.) yalnız resolver'ın geriye dönük katmanında; kanonik
her zaman kazanır. Windows'ta `data/exchange_credentials.json` (atomic,
0600, flock'lu RMW) env'i ezer; Replit'te dosya OKUNMAZ (Secrets kanonik).

**İmzalı hesap fetch'i tek yol:** `dashboard_api._spot_account_raw()` /
`_tr_account_raw()` (10s TTL + per-kind lock). portfolio_api ve
exchange_gateway buna delege eder — kendi imzalı fetch/env okuması eklemek
`tests/test_architecture_guard_accounts.py`'yi kırar (bilerek).

**Durum modeli:** creds yoksa `NOT_CONFIGURED` (EXCHANGE_AUTH_FAILED
DEĞİL); app.py `_connection_state()` snapshot'a NOT_CONFIGURED/HEALTHY/
STALE/AUTH_FAILED/CONNECTION_FAILED/DISABLED yazar.

**Why:** Ekranlar (Genel Bakış/Portföy/Hesaplarım) ayrı fetch+cred
yollarıyla çelişkili değer gösteriyordu; kanonik ad da en son öncelikteydi.

**How to apply:** Yeni bir borsa veri yolu eklerken resolver'dan geç ve
ham fetch'i dashboard_api raw cache'e ekle; guard testini güncelle.

**Test tuzağı:** `data/local_admin.json` workspace'te mevcut olduğundan
setup-wizard testleri `_patch_local_admin(tmp_path)` ile depo yolunu
taşımalı, yoksa /setup rotaları 404 döner.

**Ekran tutarlılığı:** Bağlantı durumu türetimi TEK yerde:
`dashboard_api.connection_state()`; `_serve()` her modele
`connection_state` damgalar. Hesaplarım kartı, Genel Bakış ve yönetici
şeridi bu alanı OKUR — hiçbir ekran kendi credential/health türetimi
yapamaz; UI alan eksikse "Bağlı" değil "Durum Bilinmiyor" gösterir.

**Windows .env kodlama tuzağı:** Notepad UTF-8'i BOM ile kaydeder (ilk anahtar `\ufeffKEY` olur), PowerShell `Set-Content` varsayılanı UTF-16'dır — ikisi de "GLOBAL=False/TR=False" belirtisi üretir. `.env` ayrıştırıcısı utf-8-sig + utf-16 fallback ile okumalı; bozuk dosyada fail-closed boş dict.
