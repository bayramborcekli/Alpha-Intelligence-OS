# Güncel Görev — Windows PAPER_LEARNING Akışı

Durum: `IN_PROGRESS`  
Kaynak: `governance/project_state.json`  
Son güncelleme: 2026-07-31

## Tek aktif öncelik

Windows ortamında doğal `PAPER_LEARNING` alım ve kapanış akışını başlatabilecek
kuralları kanıtla. `STRICT` yalnız karşılaştırma tabanı olarak kalır. Tasarım işi
bu teslim tamamlanana kadar bekler.

## Bu görevde izin verilenler

- Paper strateji kapılarının kanıta dayalı, tek-hipotezli düzenlenmesi
- ADR-014 kapsamında `alpha20_v1/dual_model.py` içinde yalnız Paper
  edge/maliyet çarpanı kapısını kalite uyarısına çeviren ve gerçek nihai
  Paper ret nedenini görünür yapan sınırlı değişiklik
- ADR-015 kapsamında toplam Paper açık pozisyon tavanını 10'a çıkaran,
  iki modelin bu birleşik kapasiteyi kullanmasına izin veren ve paneli aynı
  kanonik limitle uyumlu gösteren sınırlı değişiklik
- Paper günlük/ledger etiketleri ve ölçümleri
- Testler, salt-okunur teşhis ve sadakat raporu

## Bu görevde yasak olanlar

- Canlı Binance emri, transfer veya çekim
- Kullanıcı onayı olmadan restart
- Aynı anda birden fazla strateji hipotezini değiştirmek
- Tasarım çalışmasını aktif önceliğin önüne almak
- Sahte işlem veya sabit piyasa verisi üretmek

## Kabul şartları

```text
GOVERNANCE_PREFLIGHT: PASS
CURRENT_TASK_ALIGNED: TRUE
PAPER_LEARNING_FLOW_POSSIBLE: TRUE
PAPER_TOTAL_OPEN_POSITION_LIMIT: 10
STRICT_COMPARISON_PRESERVED: TRUE
LIVE_ORDER_CREATED: FALSE
EXCHANGE_WRITE_REQUESTS: 0
RESTARTED_WITHOUT_APPROVAL: FALSE
TESTS: PASS
```
