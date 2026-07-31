# Alpha Intelligence OS — Karar Defteri

Kararlar silinmez. `ACTIVE` yürürlüktedir; `SUPERSEDED` kaydı, yerini alan karar
kimliğini belirtir. Son makine-okunur karar kimliği `project_state.json` içindeki
`decision_head` ile aynı olmalıdır.

## ACTIVE — ADR-015 — Paper toplam açık pozisyon tavanı 10 olacak

- Tarih: 2026-07-31
- Kullanıcı kararı: Paper sisteminin aynı anda yönetebileceği toplam açık
  işlem/pozisyon sayısı 10 yapılacak.
- Uygulama: CORE ve OPPORTUNITY modellerinin her biri, boş kapasite varsa
  toplam tavana kadar pozisyon açabilir; iki model ve varsa legacy Paper
  pozisyonunun birleşik toplamı hiçbir zaman 10'u aşamaz.
- Gerekçe: Eski `CORE 2 / OPPORTUNITY 2 / TOPLAM 4` ve panelde görünen legacy
  `1` sınırı, doğal Paper örneği toplama hedefini gereksiz yere yavaşlatıyor.
- Sert kalanlar: aynı sembolde mükerrer pozisyon, cooldown, bozuk/bayat veri,
  aşırı spread/slippage, yetersiz likidite, maliyet sonrası pozitif olmayan
  hedef, günlük zarar limiti, kill-switch ve canlı emir yasağı.
- Risk kapsamı: Bu genişleme yalnız Paper içindir; pozisyon büyüklükleri,
  günlük zarar sınırı ve borsa yazma isteği değiştirilmez.
- `SUPERSEDES`: Önceki model başına 2 ve birleşik toplam 4 açık pozisyon
  sınırı. ADR-014 ve diğer güvenlik hükümleri yürürlüktedir.

## ACTIVE — ADR-014 — Paper girişleri pozitif-net sınıra kadar gevşetilecek

- Tarih: 2026-07-31
- Kullanıcı kararı: Paper başlangıç alımları son derece gevşetilecek;
  sistemin neredeyse hiç işlem üretememesi kabul edilmeyecek.
- Tek hipotez: `EDGE_BELOW_COST_MULTIPLE`, `PAPER_LEARNING` için sert
  işlem engeli olmaktan çıkarılıp kalite uyarısına dönüştürülecek.
- En geniş güvenli sınır: beklenen brüt hareketten komisyon ve tahmini
  kayma çıkarıldıktan sonra sonuç kesinlikle pozitifse Paper adayı
  açılabilir. Beklenen net sonuç `<= 0` ise giriş yine kapalıdır.
- Görünürlük düzeltmesi: panel STRICT karşılaştırma reddini son karar gibi
  göstermeyecek; Paper ikinci değerlendirmesinin gerçek nihai ret nedenini
  gösterecektir.
- Sert kalanlar: bozuk/bayat veri, aşırı spread/slippage, yetersiz
  likidite, maliyet sonrası pozitif olmayan hedef, pozisyon/günlük zarar,
  tekrar/cooldown ve canlı emir yasağı.
- `SUPERSEDES`: ADR-013 içindeki `edge/maliyet alt sınırı`nın Paper'da sert
  kalacağı hükmü. ADR-013'ün diğer hükümleri yürürlüktedir; STRICT davranış
  değişmez.

## ACTIVE (ADR-014 İLE KISMEN SUPERSEDED) — ADR-013 — Paper giriş seçenekleri kontrollü genişletilecek

- Tarih: 2026-07-31
- Kullanıcı kararı: Paper başlangıç alım seçenekleri, ürünün hiç işlem
  yapamamasını önleyecek biçimde esnetilecek ve geliştirilecek.
- Tek hipotez: `PAPER_LEARNING` aday üretim erişilebilirliği.
- Uygulama: STRICT trend girişi yanında EMA veya VWAP tek-teyit girişi ve
  fiyat hareketine dayalı momentum-probe girişi bulunur. Kısa pencere mum
  hacmi Paper adayını tek başına engellemez ve beklenen edge hesabına girmez.
  Paper edge yalnız pozitif fiyat momentumundan türetilir; düşüşün mutlak
  değeri yükseliş/kâr ihtimali gibi sayılamaz.
  Düşük güven, momentum tükenmesi, false-breakout riski ve net R/R 1.20
  Paper'da kalite etiketi olabilir.
- Ayrım: mum hacmi strateji teyididir; piyasanın temel işlem hacmi ve işlem
  sayısı ise gerçekleştirilebilirlik/likidite güvenliğidir.
- Sert kalanlar: bozuk/bayat veri, aşırı spread/slippage, yetersiz likidite,
  maliyet sonrası pozitif olmayan hedef, edge/maliyet alt sınırı,
  pozisyon/günlük zarar, tekrar/cooldown ve canlı emir yasağı.
- Yetki: Yalnız `alpha20_v1/dual_model.py` içinde bu Paper kapsamıyla sınırlı
  değişiklik, kullanıcı tarafından açıkça onaylanmıştır. STRICT davranışın ve
  borsa salt-okunur sınırının değişmesi onaylanmamıştır.

## ACTIVE — ADR-012 — Kalıcı proje yönetim omurgası

- Tarih: 2026-07-31
- Karar: Her Agent işe başlamadan anayasa, proje durumu, karar defteri ve aktif görevi okuyup preflight çalıştırır.
- Gerekçe: Sohbet hafızası ve dağınık görev raporları proje yönünü güvenilir biçimde korumuyor.
- Sonuç: Çelişkide kaynak değişikliği yapılmaz; `GOVERNANCE_BLOCKED` ve Executive Review gerekir.

## ACTIVE — ADR-011 — Güncel uygulama önceliği

- Tarih: 2026-07-31
- Karar: Önce Windows'ta doğal PAPER_LEARNING alım-kapanış akışı; sonra işlem kanıtları; ardından onaylı Trading Home tasarımı.
- Kısıt: Tasarım çalışması Paper akışı kanıtlanmadan önceliğin önüne geçemez.

## ACTIVE — ADR-010 — Paper kuralları kademeli öğrenme içindir

- Tarih: 2026-07-31
- Karar: EMA/VWAP, momentum, güven skoru ve net R/R 1.20 Paper için kalite ölçütüdür; mutlak işlem durdurucu değildir.
- Sert kalanlar: veri bütünlüğü, spread, likidite, pozitif net hedef, pozisyon/günlük zarar, tekrar/cooldown ve canlı emir yasağı.
- Ölçüm: İlk 10 işlem teknik akış; 20-30 kapanmış işlem performans değerlendirmesi.

## SUPERSEDED — ADR-009 — Paper net R/R 1.20 sert kapısı

- Tarih: 2026-07-31
- Yerine geçen: ADR-010
- Eski karar: `min_net_reward_risk = 1.20` PAPER_LEARNING için de değişmez sert kapı olacaktı.
- Değişme gerekçesi: Bu yapı doğal Paper işlemi matematiksel olarak imkânsız hale getiriyordu; kullanıcı kontrollü ve ölçümlü öğrenme yaklaşımını seçti.

## ACTIVE — ADR-008 — Trading Home referans sözleşmesi

- Tarih: 2026-07-31
- Karar: CORE ve OPPORTUNITY ayrı paneller; izleme başlığı `İZLENEN COİNLER`; Kapat tek tık ve açıklamasız; gerçek veri; onaylı koyu referans yerleşimi.

## ACTIVE — ADR-001..007 — Project Bible temel ilkeleri

Paper First, Risk First, Security First, Human Approval, Exchange Independent,
kodun kendini değiştirmemesi ve son kararın kullanıcıya ait olması yürürlüktedir.
