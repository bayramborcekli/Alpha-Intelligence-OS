# Alpha Intelligence OS — Karar Defteri

Kararlar silinmez. `ACTIVE` yürürlüktedir; `SUPERSEDED` kaydı, yerini alan karar
kimliğini belirtir. Son makine-okunur karar kimliği `project_state.json` içindeki
`decision_head` ile aynı olmalıdır.

## ACTIVE — ADR-013 — Paper giriş seçenekleri kontrollü genişletilecek

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
