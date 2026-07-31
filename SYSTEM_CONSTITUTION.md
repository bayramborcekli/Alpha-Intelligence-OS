# Alpha Intelligence OS — Sistem Anayasası

Bu dosya projenin değişmez güvenlik ve yönetim sınırlarını tanımlar.
Makine-okunur güncel durum `governance/project_state.json` dosyasındadır.

## Değişmez ilkeler

1. **Paper First:** Güncel çalışma yalnız `PAPER_LEARNING` / Paper ortamındadır.
2. **Canlı emir yasağı:** Borsaya emir, transfer veya çekim yazma isteği daima sıfırdır.
3. **İnsan onayı:** Restart, canlı risk genişletme ve güvenlik sınırı değişikliği kullanıcı onayı olmadan yapılamaz.
4. **Kanıt:** Her değişiklik hipotez, test, sonuç ve sadakat raporu taşır.
5. **Tek değişken:** Öğrenme döneminde aynı anda yalnız bir strateji hipotezi değiştirilir.
6. **Gerçek veri:** Bilinmeyen değer uydurulmaz; arayüzde sahte/sabit piyasa verisi kullanılmaz.
7. **Karar tarihçesi:** Eski karar silinmez. Yeni karar onu `SUPERSEDES` ilişkisiyle yürürlükten kaldırır.
8. **Çelişkide dur:** Agent, güncel durumla görev arasında çelişki görürse uygulama yapmaz ve Executive Review ister.

## Sert güvenlikler ve yumuşak öğrenme kuralları

Paper strateji ölçütleri (EMA/VWAP, momentum, güven skoru ve net R/R 1.20)
öğrenme/puanlama sinyalidir; tek başına bütün Paper işlemleri durduran değişmez
kapılar değildir. Eski/bozuk veri, aşırı spread, yetersiz likidite, maliyet sonrası
pozitif olmayan hedef, pozisyon/günlük zarar sınırı, tekrar/cooldown ve canlı emir
yasağı sert kalır.

## Yetki sırası

`SYSTEM_CONSTITUTION.md` → `governance/project_state.json` → yürürlükteki
`DECISIONS.md` kaydı → `CURRENT_TASK.md` → görev metni → sohbet özeti.

Anayasa ile kullanıcı tarafından verilmiş daha yeni açık karar çelişirse Agent
önce dosyaları güncelleyecek bir karar değişikliği önerir; sessiz yorum yapmaz.
