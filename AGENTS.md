# AGENTS.md — Geliştirme Kuralları (agent'lar ve katkıcılar için)

Bu depo mission-tabanlı geliştirilir. Her mission; uygulama + test +
mimari inceleme + commit/push + Türkçe teslim raporu ile kapanır.

## Zorunlu proje bağlamı (her Agent için ilk adım)
Her işe başlamadan önce, bu sırayla ve tamamen oku:
1. `governance/project_state.json` — tek makine-okunur güncel gerçek.
2. `SYSTEM_CONSTITUTION.md` — değişmez güvenlik ve yetki sınırları.
3. `DECISIONS.md` — yürürlükteki ve yerine geçen kararlar.
4. `CURRENT_TASK.md` — yalnız güncel öncelik ve kabul şartları.

Ardından `python scripts/project_preflight.py --check` çalıştır. Kontrol
başarısızsa veya görev bu dosyalarla çelişiyorsa hiçbir kaynak dosyayı
değiştirme; `GOVERNANCE_BLOCKED` raporu ver ve Executive Review iste.
Sohbet özeti, eski görev metni veya eski rapor bu dört dosyanın önüne
geçemez. Yeni kullanıcı kararı önce karar defterine ve proje durumuna
işlenmeden uygulamaya alınamaz.

## Değişmez kurallar
1. **Salt-okunur mimari:** borsa yazma isteği SONSUZA DEK 0. Emir,
   transfer, çekim kodu eklenemez; `exchange_gateway` yalnızca GET +
   allowlist'tir.
2. **Dokunulmaz bölgeler:** `alpha20_v1/`, `auth.py`, defter (ledger)
   yazımı ve borsa imzalama katmanı değiştirilmez.
3. **Para matematiği yalnızca `Decimal`** — `float()` yasak; API'de
   Decimal-string serileştirme.
4. **Değer uydurma yok:** bilinmeyen → `null` / "Veri Yok" / "—";
   asla 0 ile doldurulmaz.
5. **Secret hijyeni:** secret'lar yalnızca ortam değişkeninde; yanıt,
   log ve dokümanda ham secret/stack trace bulunamaz.
6. **API deseni:** veri uçları GET-only + auth zorunlu + `no-store`;
   hata yanıtları sterile (kod + sabit Türkçe mesaj). Modüller kökte
   `*_api.py` düz deseniyle eklenir.
7. **Test zorunlu:** her değişiklik test ister; tam regresyon yeşil
   olmadan PASS raporu yazılamaz. Test gizleme/zayıflatma yasak.
8. **Intelligence katmanı:** yalnızca tavsiye (advisory-only); emir dili
   ve fiyat tahmini yasak; harici LLM 1500.1 boyunca sert kilitli;
   deterministik kural tabanlı çekirdek korunur.
9. **UI:** Türkçe varsayılan, mobil uyumlu, CSP uyumlu (harici kaynak
   yok), tüm dinamik içerik kaçışlı; işlem düğmesi eklenemez.

## Çalışma akışı
```
uygula → testleri yaz → tam regresyon → mimari inceleme (bulguları düzelt)
→ commit → GitHub push (main) → Türkçe teslim raporu ("Executive Review bekle")
```

## Referanslar
- Mission geçmişi: `docs/MISSION_INDEX.md`
- API sözleşmeleri: `docs/API_REFERENCE.md`
- Test programı: `docs/TEST_PROGRAM.md`
- Güvenlik modeli: `docs/security_model.md`
