---
name: Task agent scope deletions
description: Merged task agents may delete unrelated recent changes as "out of scope" — verify recent features survive each merge.
---
Görev ajanı merge'leri, kendi görevine "kapsam dışı" görünen YENİ eklenmiş kodu ve `.replit [userenv]` ortam değişkenlerini silebilir (REPLIT_DEV_BYPASS hem kod hem env olarak bir merge'de silindi; login ekranı geri geldi).

**Why:** Ajanlar izole ortamda çalışır ve son ana-dal değişikliklerini bilinçli özellik yerine artık sanabilir.

**How to apply:** Her task merge'inden sonra `git log`/`git show <merge>` ile son eklenen özelliğin dosyalarına ve `.replit`'e bak; silinmişse restore et. Kritik geçici bloklara "görev ajanı silmesin" yorumu ekle.
