---
name: Windows SSL truststore
description: Binance public kline SSLError'larının Windows kök çözümü
---
Kural: Windows'ta SSLError (fapi.binance.com) çoğunlukla antivirüs/proxy HTTPS denetimi — kök sertifika Windows deposunda, certifi görmez. Çözüm truststore.inject_into_ssl(), yalnız os.name=="nt" ve `from app import app`'ten ÖNCE (serve_windows.main).
**Why:** verify=False yasak (operatör kuralı + testli kaynak denetimi tests/test_windows_ssl_truststore.py); truststore doğrulamayı açık tutarak OS deposunu kullanır.
**How to apply:** Yeni Windows entrypoint eklenirse injection oraya da konmalı; launcher her açılışta certifi+truststore günceller.
