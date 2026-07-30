---
name: Dual-model öğrenme köprüsü
description: dual_learning tasarım sözleşmeleri — allowlist/clamp, dürüst gölge, tek tetikleme
---
Öğrenme motoru dual-model'e `dual_learning` köprüsüyle bağlı; paralel ikinci optimizer/scheduler YASAK.

**Kurallar:**
- Öğrenme yalnız `LEARNABLE_BOUNDS` izin listesinde çalışır; overlay her yolda (öneri + `champion_overrides`) yeniden filtre + clamp edilir. Güvenlik alanları (position_usdt, max_open_positions, LIVE kilidi) asla listeye girmez.
- Gölge değerlendirme dürüsttür: `GATE_SUBSET_REPLAY` — yalnız giriş kapısı alt kümesi gerçek sonuçlarla ölçülür; TP/SL kayıttan yeniden oynatılamaz ve bu shadow sonucunda açıkça işaretlenir. Uydurma iyileşme iddiası yasak.
- Köprü çevrim başına TEK yerden tetiklenir: auto_controller döngüsü → `learning_engine.run_dual_learning_update`. `run_learning_update` onu ÇAĞIRMAZ (mimar bulgusuydu; çift tetikleme/IO churn).
- Mod AUTO_SHADOW, auto_promote varsayılan kapalı; terfi yalnız `/api/dual-model/learning/promote` (CSRF+auth, allowlist testine gerekçeli eklendi) ile.
- State git dışı: `dual_learning_state.json` (flock + atomic replace), history jsonl. `config_version` pozisyon açılışında damgalanır, trade kaydına ve normalize şemaya taşınır — rollback bu sürüm filtresiyle çalışır.

**How to apply:** Öğrenilebilir parametre eklerken LEARNABLE_BOUNDS + sınır + test; yeni tetikleme noktası eklemeden önce tek-tetikleme regresyon testine bak (test_dual_learning).
