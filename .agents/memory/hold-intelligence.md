---
name: Hold Intelligence (PHI) gölge katmanı
description: Pozisyon yönetimi gölge skorlama mimarisi ve sözleşme sınırları
---

# Hold Intelligence (PHI)

- `alpha20_v1/hold_intelligence.py`: trend_health (çok göstergeli oylama, tek indikatör karar veremez), classify_regime (6 rejim), profit_quality, trend_decay_reasons (12 kod), compute_phi, hold_state (6 sınıf), adaptive_giveback_limit, variant_decisions (balanced/conservative/aggressive), hold_review, sembol+rejim hafızası.
- **Sözleşme:** tamamen gölge — gerçek TP/SL/trailing/time-exit değişmez; PHI/hold_state hiçbir gerçek karara bağlanamaz; champion değişmez; LIVE ORDERS DISABLED.
- **Fail-closed:** eksik/NaN/pozitif-olmayan girdi → None + DATA_QUALITY; 0/1 ikamesi yasak (PFDE kuralı devam).
- **Look-ahead yasak:** varyant EXIT kararı o anki net PnL ile dondurulur; kapanış incelemesi yalnız işlem-içi kanıt kullanır; "erken çıkış" NOT_PROVABLE.
- **Eşzamanlılık dersi:** `_hold_shadow_cycle` klines'ı kilit dışında çeker, kopyada değerlendirir; geri yazım MERGE-ONLY (canlı sayaçlar geri alınmaz, yalnız yeni variant_exit anahtarları eklenir, opened_ts eşleşmezse atlanır). Snapshot-sonra-yaz kalıbı düz üzerine-yazamaz.
- **Rapor sınırlama:** gölge dosyası kuyruktan ~256KB okunur; hafıza projeksiyonu en çok örneklemli 50 kova; strategy_lab.status() 60 sn süreç içi önbellek.
- Dosyalar (gitignore'da): hold_shadow.jsonl(+.lock/.tmp), hold_memory.json(+.lock/.tmp). Gölge yazımı ayrı kalıcı .lock + kırpma (PFDE kalıbı).
- Route guard: /api/hold-intelligence/report EXPECTED_INTEL_ROUTES allowlist'ine gerekçeli yorumla eklendi (yeni "intelligence" rotaları bu listeye eklenmeli yoksa test kırılır).
