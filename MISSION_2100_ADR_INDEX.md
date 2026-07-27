# MISSION 2100 — ADR INDEX (ADR-011 … ADR-020)

Her kayıt: Karar · Bağlam · Gerekçe · Alternatifler · Sonuçlar ·
Güvenlik etkisi · Uyumluluk etkisi · Gelecek genişleme politikası.

---

## ADR-011 — Plugin Mimarisi
**Karar:** Tüm yeni yetenekler (broker, strateji, AI, bildirim,
istemci) yalnız kapalı `ExtensionPoint` enum'unda BİLDİRİLEN
noktalardan girer; Agent 01'de yükleyici yoktur.
**Bağlam:** v1.1.0 masaüstü/mobil/plugin ekosistemine hazırlanıyor;
çekirdek dondurulmuş. **Gerekçe:** Bildirim-önce yaklaşım, uygulama
gelmeden sınırları kilitler. **Alternatifler:** entry-points tabanlı
keşif (reddedildi: dinamik import yasak); çekirdek içi kayıt
(reddedildi: çekirdek dondurulmuş). **Sonuçlar:** Genişleme noktası
eklemek enum değişikliği + mimar incelemesi ister. **Güvenlik
etkisi:** Dinamik kod yükleme yüzeyi hiç açılmaz. **Uyumluluk
etkisi:** Çekirdek API'ye sıfır dokunuş. **Gelecek genişleme:**
Yükleyici ancak ayrı, izole ajan teslimiyle ve fail-closed olarak
gelir.

## ADR-012 — Desktop Shell Sınırı
**Karar:** Masaüstü istemci yalnız `DesktopClientAdapter` noktası
üzerinden bağlanır; kontrollü yürütme alanı UI çatısı bilmez.
**Bağlam:** Electron/Tauri benzeri kabuklar planlanıyor.
**Gerekçe:** Çatı bağımlılığı alan katmanını kirletir ve test
edilemez kılar. **Alternatifler:** Doğrudan Flask/REST arayüzü
(reddedildi: HTTP yasak); çatı-özel köprü (reddedildi: taşınmaz).
**Sonuçlar:** Kabuk değişimi alan kodunu etkilemez. **Güvenlik
etkisi:** UI süreci hiçbir zaman politika kararını atlayamaz.
**Uyumluluk etkisi:** Mevcut Flask dashboard'u ayrı, dokunulmadı.
**Gelecek genişleme:** Kabuk adaptörü ayrı pakette, alan API'siyle.

## ADR-013 — Update Manager Sınırı
**Karar:** Güncelleme mekanizması yalnız `UpdateManagerAdapter`
sınırı olarak bildirilir; indirme/kurulum/yeniden başlatma yasak.
**Bağlam:** Dağıtılabilir istemciler güncelleme ister. **Gerekçe:**
Güncelleyici en riskli bileşendir; yürütme alanının dışında
tutulmalı. **Alternatifler:** Çekirdek içi auto-update (reddedildi:
uzak kod çalıştırma riski). **Sonuçlar:** Güncelleme, Execution
Core / Controlled Execution Service / Risk Engine / Broker Adapter
dışında yaşar. **Güvenlik etkisi:** Uzak kod yolu alan katmanına
hiç giremez. **Uyumluluk etkisi:** Yok — yalnız bildirim.
**Gelecek genişleme:** İmza doğrulamalı, insan onaylı ayrı bileşen.

## ADR-014 — Genişleme Politikası
**Karar:** Kayıt defteri (`ExtensionRegistry`) değişmez, sınırlı ve
deterministiktir; kayıt kümesi kapalı enum ile sınırlıdır, tekrar
yasaktır. **Bağlam:** Plugin-first politika kayıt disiplini ister.
**Gerekçe:** Sınırsız/değişken kayıt gizli durum ve güvenlik deliği
üretir. **Alternatifler:** Global mutable registry (reddedildi).
**Sonuçlar:** Kayıt uzayı derleme zamanında bilinir; arama sabit
zamanlıdır. **Güvenlik etkisi:** Çalışma zamanında yüzey büyüyemez.
**Uyumluluk etkisi:** Yok. **Gelecek genişleme:** Yeni nokta = enum
üyesi + mimar incelemesi + yeni ADR.

## ADR-015 — Sürüm Yönetişimi
**Karar:** Mission 2100 hedefi v1.1.0 "Controlled Execution";
çekirdek v1.0.0 API'sinde kırıcı değişiklik majör sürüm olmadan
yasaktır; her ajan taban değerlerini (commit/regresyon) birebir
devralır. **Bağlam:** Mission 2000 kapanışı sürüm disiplinini
kurdu. **Gerekçe:** Çok-ajanlı zincirde sürüm kayması denetimi
imkânsızlaştırır. **Alternatifler:** Sürekli sürümleme (reddedildi:
denetim izi zayıf). **Sonuçlar:** Her teslim taban + artan regresyon
verir. **Güvenlik etkisi:** Sürüm sahteciliği testlerle yakalanır.
**Uyumluluk etkisi:** SemVer korunur. **Gelecek genişleme:** v2.x
ancak yeni misyon + tam sertifika ile.

## ADR-016 — Kontrollü Yürütme Modları
**Karar:** Kapalı `ControlledExecutionMode` enum'u: PAPER, SHADOW,
MICRO_LIVE — başka mod yok; sınırsız canlı mod Mission 2100'de
YOKTUR. **Bağlam:** Kağıt→gölge→mikro-canlı kademeli güven
merdiveni. **Gerekçe:** Mod uzayının kapalı olması, yetki modelinin
tamlığını kanıtlanabilir kılar. **Alternatifler:** Serbest string
modlar (reddedildi: yazım hatası = güvenlik deliği). **Sonuçlar:**
Yeni mod eklemek enum + güvenlik sözleşmesi + ADR ister. **Güvenlik
etkisi:** Bilinmeyen mod her yerde RED. **Uyumluluk etkisi:**
Çekirdeğin `ExecutionMode`'u ayrı ve dokunulmamış kalır. **Gelecek
genişleme:** LIVE modu ancak ayrı misyon + tam yetkilendirme
altyapısıyla düşünülebilir.

## ADR-017 — Paper Trading İzolasyonu
**Karar:** PAPER modunda borsa yazması ve broker okuması KAPALI,
yalnız simüle dolum İZNİ vardır; simülasyonun kendisi Agent 01'de
uygulanmaz. **Bağlam:** Paper motoru sonraki ajanın işi. **Gerekçe:**
Kağıt ortamı gerçek borsa yüzeyinden tamamen yalıtılmalı; okuma
bile sızıntı kanalıdır. **Alternatifler:** Kağıt modunda canlı
fiyat okuması (reddedildi: SHADOW'un işi). **Sonuçlar:**
PaperExecutionProvider çekirdek-üstü, tam yalıtık gelir. **Güvenlik
etkisi:** PAPER'dan borsaya hiçbir çağrı türetilemez. **Uyumluluk
etkisi:** Yok. **Gelecek genişleme:** Dolum modeli ayrı ADR ile.

## ADR-018 — Shadow Mode Salt-Okurdur
**Karar:** SHADOW modunda borsa yazması ve simüle dolum KAPALI;
yalnız broker OKUMASI izinlidir. **Bağlam:** Gerçek sinyal, sıfır
emir, karşılaştırmalı iz hedefi. **Gerekçe:** Gölge modun değeri
gerçek piyasa gözlemidir; herhangi bir yazma izni modun amacını
bozar. **Alternatifler:** Gölge modda test emri (reddedildi: artık
gölge değildir). **Sonuçlar:** ShadowObservationProvider yalnız
okuma sözleşmesi alır. **Güvenlik etkisi:** Yazma yolu tip
düzeyinde kapalıdır. **Uyumluluk etkisi:** Çekirdeğin okuma
operasyonları yeterlidir; değişiklik gerekmez. **Gelecek
genişleme:** Karşılaştırma izi RuntimeAuditSink'e yazılır
(çekirdek dışı).

## ADR-019 — Micro Live Açık Yetkilendirme İster
**Karar:** MICRO_LIVE kalıcı sözleşmesi: insan onayı ZORUNLU +
açık yetkilendirme ZORUNLU + Decimal limitler; Agent 01 modu
TANIMLAR ama ETKİNLEŞTİRMEZ — borsa yazması çalışma zamanında her
zaman reddedilir. **Bağlam:** Gelecek mikro-canlı geçişin sınırları
bugünden kilitlenmeli. **Gerekçe:** Yetki sözleşmesi uygulamadan
önce dondurulursa, uygulama sözleşmeye uymak zorunda kalır.
**Alternatifler:** Yetkilendirmeyi uygulamayla birlikte tanımlamak
(reddedildi: sınır pazarlığa açılır). **Sonuçlar:**
MicroLiveAuthorizationProvider olmadan MICRO_LIVE politikası asla
ALLOW alamaz. **Güvenlik etkisi:** En tehlikeli mod en çok kilitli
moddur. **Uyumluluk etkisi:** Kill Switch + Permission Gate zinciri
aynen geçerli kalır. **Gelecek genişleme:** Yetkilendirme bileşeni
ayrı ajan, ayrı sertifika, ADR-009 uyarınca kapı genişletmesiyle.

## ADR-020 — Örtük Mod Yükseltmesi Yok
**Karar:** Mod geçişleri kapalı matristir: PAPER↔SHADOW izinli;
SHADOW→MICRO_LIVE yalnız gelecek açık yetkilendirme bileşeniyle;
diğer tüm geçişler (PAPER→MICRO_LIVE dahil) RED. Otomatik, ortam,
zaman, strateji, broker veya AI kaynaklı yükseltme YASAK.
**Bağlam:** Otonom sistemlerde sessiz yetki tırmanması en kritik
risktir. **Gerekçe:** Yükseltme her zaman insan-kaynaklı ve
denetlenebilir olmalı. **Alternatifler:** Güven skoruna bağlı
otomatik terfi (reddedildi: AI-driven escalation yasağı).
**Sonuçlar:** Geçiş değerlendirmesi deterministik ve sabit
zamanlıdır. **Güvenlik etkisi:** Yükseltme saldırı yüzeyi kapalı.
**Uyumluluk etkisi:** Yok. **Gelecek genişleme:** Matris
değişikliği yeni ADR + mimar incelemesi + insan onayı ister.
