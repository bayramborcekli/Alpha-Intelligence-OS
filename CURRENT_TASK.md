# Güncel Görev — alpha20_revize_v2 Windows Uyumluluk Onarımı

Durum: `IN_PROGRESS`  
Kaynak: `governance/project_state.json`  
Son güncelleme: 2026-08-01

## Tek aktif öncelik

`alpha20_revize.zip` bulgularını tam uygulamada yeniden üret; mevcut tam
`app.py`, `/home` ve gerçek Windows başlangıç zincirini koru. SHORT muhasebesi,
minimum dört saat tutma, tekil komisyon hesabı, sembol filtreleri ve aktif karar
eşiği bağlantısını düzelt. Strateji göstergelerine bağımsız müdahale etme.

## Bu görevde izin verilenler

- Tam Windows `app.py` ve başlatma zincirinin korunması
- SHORT yönlü TP/SL ve net PnL muhasebesinin düzeltilmesi
- Stop-loss korunarak kârlı çıkışlarda minimum dört saat tutma
- Stablecoin/leveraged-token filtreleri ve aktif karar eşiği bağlantısı
- Testler, Paper doğrulaması ve güvenli Windows paketi

## Bu görevde yasak olanlar

- Canlı Binance emri, transfer veya çekim
- Kullanıcı onayı olmadan restart
- Mevcut göstergeleri veya yön kurallarını bağımsız yeniden tasarlamak
- Tasarım çalışmasını aktif önceliğin önüne almak
- Sahte işlem veya sabit piyasa verisi üretmek

## Kabul şartları

```text
GOVERNANCE_PREFLIGHT: PASS
HOME_ROUTE: PASS
WINDOWS_START_CHAIN: PASS
AUTO_PAPER_BOOTSTRAP: PASS
SHORT_PNL_AND_EXIT: PASS
MIN_HOLD_4H: PASS
ROUND_TRIP_FEE_SINGLE: PASS
UNIVERSE_FILTERS: PASS
ACTIVE_DECISION_THRESHOLD: PASS
LIVE_ORDER_CREATED: FALSE
EXCHANGE_WRITE_REQUESTS: 0
RESTARTED_WITHOUT_APPROVAL: FALSE
WINDOWS_COMPATIBLE: TRUE
TESTS: PASS
```
