---
name: Continuous Strategy Lab
description: Otonom strateji üretimi/ön test/terfi laboratuvarının sözleşmeleri
---
`strategy_lab` dual_learning'in UZANTISIDIR (paralel ikinci öğrenme sistemi yasak): aynı LEARNABLE_BOUNDS izin listesi, challenger/promote/rollback yolları, flock+atomic git-dışı state kalıbı.

**Kurallar:**
- Tik geçmişi yok → adaylar GATE_SUBSET_REPLAY ile (giriş filtreleri gerçek işlemlere; çıkışlar exit_params_replayable=False, kanıt yalnız ileri-zaman Paper'dan). Uydurma backtest yazma.
- Sızıntı sözleşmesi: dataset kronolojik train 60 / walk 15 / holdout 25; holdout aday başına TEK kez tüketilir ve ADAY ÜRETİM GİRDİLERİ (teşhis/zarar/kâr-yakalama) holdout HARİÇ pencereden gelir.
- STAGE5 senkronu dual_learning state'ini TAZE okur; yalnız `installed_as_challenger` doğrulanmış adaylar başarısız sayılır (bayat snapshot yanlış-düşme üretmişti).
- dl challenger `changes` UI sözleşmesi: `{parameter, old, new}` — başka şema panel alanlarını undefined bırakır.
- LIVE yolu: en ileri durum LIVE_ELIGIBLE etiketi; LIVE_ENABLED modülde tanımsız. Operasyonel attest bayrakları (restart/rollback/kill-switch/simulator/stress) config'ten gelir, varsayılan False → NOT_ELIGIBLE.
- Yazma rotası adı "strategy" İÇEREMEZ (Mission 1800 strategy rotalarını read-only kilitler) → bilinçli `/api/lab/control`; şablonda küçük harf "strategy" kelimesi de yasak (mission2300 bekçisi).
- Tetikleme tek nokta: auto_controller döngüsü → learning_engine.run_strategy_lab_cycle; uygunluk (interval/devre kesici/kontroller) lab içinde.

**How to apply:** Lab'a yeni aşama/kaynak eklerken bu sızıntı, taze-okuma ve adlandırma sözleşmelerini koru.
