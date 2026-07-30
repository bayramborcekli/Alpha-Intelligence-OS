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

**Final kabul (2026-07-30):** Executive Review görevi KABUL etti (kod/mimari, güvenlik, testler PASS; kabul anındaki taban b59a4fc). Açık notlar — ayrı görev/saha kanıtı bekleyenler:
1. İlk gerçek aday üretimi dataset ≥60 kapanan işlem bekliyor (bilinçli eşik).
2. İlk gerçek Paper terfisi henüz saha kanıtı üretmedi.
3. promotion_mode panel/API'de açıkça görünmeli: AUTO_PROMOTE_WITH_GUARDS veya USER_APPROVAL_REQUIRED (yapılmadı — sonraki görev).
4. Loss Containment (mevcut zarar hızını sınırlama) bu görevde kanıtlanmadı; AYRI görev olarak ele alınacak.

**How to apply:** Lab'a yeni aşama/kaynak eklerken bu sızıntı, taze-okuma ve adlandırma sözleşmelerini koru.
