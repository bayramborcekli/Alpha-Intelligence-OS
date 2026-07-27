# ADR INDEX — Execution Core v1.0.0

Mimari Karar Kayıtları (ADR). Her kayıt: Karar · Gerekçe ·
Alternatifler · Sonuçlar · Gelecek uyumluluğu.

---

## ADR-001 — Değişmez Modeller (Immutable Models)
**Karar:** Tüm alan modelleri `frozen=True, slots=True` dataclass;
para alanları yalnız `Decimal`; oluşturma sonrası mutasyon imkânsız.
**Gerekçe:** Yürütme yolunda paylaşılan durum mutasyonu en yaygın
kayıp/yarış kaynağıdır; değişmezlik determinizmi bedavaya getirir.
**Alternatifler:** Mutable DTO + savunmacı kopya (reddedildi: kopya
disiplini test edilemez); Pydantic (reddedildi: bağımlılık + örtük
dönüşümler). **Sonuçlar:** Her durum değişikliği YENİ nesnedir; eşitlik
ve hash güvenilirdir. **Gelecek uyumluluğu:** Yeni alanlar yalnız
`None` varsayılanıyla eklenebilir; alan kaldırma majör sürüm ister.

## ADR-002 — Broker Adapter Sınırı
**Karar:** Broker'a dokunan TEK katman `BrokerAdapter` (8 operasyonlu
soyut asenkron sözleşme, template-method + `_do_` kancaları); native
payload sınırdan asla sızmaz. **Gerekçe:** Broker çeşitliliği (Binance,
IBKR, Midas…) çekirdeği kirletmemeli. **Alternatifler:** Borsa SDK'sının
doğrudan kullanımı (reddedildi: test edilemez, sızdırır); operasyon
başına fonksiyon (reddedildi: idempotency/normalizasyon disiplinini
dağıtır). **Sonuçlar:** Yeni broker = yeni adaptör alt sınıfı; çekirdek
değişmez. **Gelecek uyumluluğu:** Yeni operasyon eklemek mimar
incelemesi ister; mevcut 8 operasyon imzası donduruldu.

## ADR-003 — Yürütme Boru Hattı
**Karar:** Kalıcı tek yönlü sıra: API → Service → Risk Engine →
Permission Gate → Kill Switch → Broker Adapter → Broker. Bu bağımlılık
yönü KALICIDIR. **Gerekçe:** Her emir aynı denetim noktalarından aynı
sırada geçmeli; atlama yolu güvenlik deliğidir. **Alternatifler:**
Olay-tabanlı gevşek boru hattı (reddedildi: sıra garantisi kaybolur).
**Sonuçlar:** Alt katman üst katmanı import edemez (AST-testli).
**Gelecek uyumluluğu:** Yeni denetim adımı yalnız Service içine ve
mimar onayıyla eklenir; sıra değişikliği yasak.

## ADR-004 — Çağıran-Sahipli Idempotency
**Karar:** `idempotency_key` çağıran tarafından üretilir; çekirdek asla
kimlik üretmez (UUID yasak); anahtar eksikse istek broker'a gitmeden
reddedilir. **Gerekçe:** Kimlik üretimi rastgelelik ve gizli durum
getirir; tekrar deneme sahipliği çağırandadır. **Alternatifler:**
Çekirdek-üretimli UUID (reddedildi: determinizm bozulur); anahtarsız
kabul (reddedildi: çift emir riski). **Sonuçlar:** TOCTOU sınırı açıkça
belgelidir; exactly-once İDDİA EDİLMEZ. **Gelecek uyumluluğu:** Dağıtık
idempotency deposu ayrı katman olarak eklenebilir; sözleşme değişmez.

## ADR-005 — Sessiz Yeniden Boyutlandırma Yok (No Silent Resize)
**Karar:** Risk Engine boyut küçültmeyi yalnız ÖNERİR
(`SIZE_REDUCTION_REQUIRED` + önerilen miktar); hiçbir katman miktarı
sessizce değiştirmez. **Gerekçe:** Kullanıcının emri kutsaldır; örtük
değişiklik denetlenemez kayba yol açar. **Alternatifler:** Otomatik
küçültüp gönderme (reddedildi: sürpriz yürütme). **Sonuçlar:** Küçültme
kararı çağırana döner; yeni istek çağıran tarafından yapılır.
**Gelecek uyumluluğu:** Otomatik onay ancak açık kullanıcı opt-in
katmanı olarak eklenebilir.

## ADR-006 — Broker Bağımsızlığı
**Karar:** Çekirdekte broker adı dallanması yasak (`if broker ==
"Binance"` vb. AST-yasak); tüm yetenek farkları `BrokerProfile`
verisiyle ifade edilir. **Gerekçe:** Ad-tabanlı dallanma her yeni
broker'da çekirdek değişikliği zorlar. **Alternatifler:** Strateji
deseni + ad kayıt defteri (kısmen kabul: resolver), çekirdek içi
switch (reddedildi). **Sonuçlar:** Yeni broker çekirdeğe SIFIR satır
ekler. **Gelecek uyumluluğu:** Yeni yetenek = `BrokerProfile`'a
`None`-varsayılanlı yeni alan.

## ADR-007 — Yürütme Çekirdeği Dondurması
**Karar:** 20 modül, kamu API yüzeyleri, boru hattı sırası ve sahiplik
`execution_architecture_freeze.py` manifestosuyla kalıcı donduruldu;
testler canlı kodu manifestoyla karşılaştırır. **Gerekçe:** Çok-ajanlı
geliştirmede sessiz sürüklenme en büyük risktir. **Alternatifler:**
Konvansiyon + kod incelemesi (reddedildi: makine-denetimsiz).
**Sonuçlar:** Ekleme mimar incelemesi ister; kaldırma/yeniden
adlandırma yasak. **Gelecek uyumluluğu:** Genişletme serbest,
değiştirme majör sürüm ister.

## ADR-008 — Tek Kanonik Sahiplik
**Karar:** Her alan modelinin TEK sahip modülü vardır
(`DOMAIN_OWNERSHIP` haritası); kopya tanım test-yasak. **Gerekçe:**
Çift tanım sessiz şema sapması üretir. **Alternatifler:** Paylaşılan
"models" mega-modülü (reddedildi: katman sınırlarını eritir).
**Sonuçlar:** Import yönü sahiplikten türetilir. **Gelecek
uyumluluğu:** Yeni model → sahibi ile birlikte haritaya eklenir.

## ADR-009 — Yürütme İzin Kapısı
**Karar:** Kill Switch durumu yalnız `ExecutionPermissionGate`
üzerinden okunur; kapı risk kararı + switch durumunu tek izin
sonucuna indirger; çekirdek switch'i asla MUTASYONA uğratmaz.
**Gerekçe:** İzin mantığının tek noktada toplanması atlama yolunu
imkânsızlaştırır. **Alternatifler:** Service içinde satır-içi kontrol
(reddedildi: kopyalanabilir, atlanabilir). **Sonuçlar:** Reddedilen
yollar SIFIR broker çağrısı yapar (testli). **Gelecek uyumluluğu:**
Yeni izin kaynağı (ör. canlı yetkilendirme) kapıya eklenir, Service
değişmez.

## ADR-010 — Deterministik Yürütme
**Karar:** Aynı girdi + aynı bağımlılık çıktıları → aynı sonuç, aynı
iz, aynı broker çağrı sayısı. Duvar saati, UUID, rastgelelik,
thread, retry, uyku üretim modüllerinde yasak. **Gerekçe:** Finansal
yürütme yeniden üretilebilir olmalı; hata ayıklama ve denetim buna
dayanır. **Alternatifler:** "Pratikte deterministik" gevşekliği
(reddedildi: test edilemez). **Sonuçlar:** Zaman/kimlik ihtiyacı olan
her değer çağırandan gelir (`logical_sequence`, `idempotency_key`).
**Gelecek uyumluluğu:** Zamana bağlı özellikler enjekte edilen saat
soyutlamasıyla ve ayrı mimar incelemesiyle eklenebilir.
