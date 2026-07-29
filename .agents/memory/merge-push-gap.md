---
name: Merge push gap
description: Görev ajanı merge'leri GitHub'a push edilmez
---
Kural: Platform merge'leri yalnız Replit main'ine commit eder; GitHub origin'e push ETMEZ. Operatör Windows'a `git pull` ile kod çektiği için her merge sonrası `git log origin/main..HEAD` kontrol edip gitPush çağır.
**Why:** İki kez yaşandı: SSL düzeltmeleri merge edilmişken GitHub'da yoktu; Windows "güncel" sanılırken eski kod koşuyordu.
**How to apply:** Her rapor/teslim öncesi origin/main == HEAD doğrula; ayrıca merge'ler bazen çalışma ağacında son işleri geri alan uncommitted diff bırakır — git status temiz mi bak.
