# Alpha Intelligence OS — Karar Defteri

Kararlar silinmez. `ACTIVE` yürürlüktedir; `SUPERSEDED` kaydı, yerini alan karar
kimliğini belirtir. Son makine-okunur karar kimliği `project_state.json` içindeki
`decision_head` ile aynı olmalıdır.

## ACTIVE — ADR-015 — Otonom PAPER Strateji Evrimi (Maksimum Kar Optimizasyonu)

- Tarih: 2026-08-01
- Kullanıcı kararı: Sistem PAPER modda kalacak; canlı emir, Futures, transfer ve borsa yazma isteği daima sıfır kalacak. Ancak strateji parametreleri, risk yönetimi kuralları ve konfigürasyon otonom olarak evrimleştirilebilir. "Kod kendini değiştiremez" kuralı ADR-001..007'deki haliyle yürürlükten kaldırılmaz; yalnızca `config.json`, `dual_model` parametreleri ve strateji skorlama ağırlıkları için sınırlı otonom değişiklik izni verilir.
- Hedef: PAPER portföyünde Profit Factor ≥ 1.20 ve net pozitif PnL elde etmek. Mevcut veriler (37 işlem, PF 0.10, win rate %10.81) gösteriyor ki: trailing stop çok dar, entry confidence düşük, fee drag baskın.
- Otonom değişiklik kapsamı:
  - `dual_model.py` içindeki `DEFAULTS` parametre grid'i (tp_pct, sl_pct, trailing_pct, min_confidence, max_hold_minutes)
  - `alpha20.py` içindeki `score_setup` ağırlıkları ve `evaluate_trade_economics` safety_factor
  - `auto_controller.py` içindeki adaptive risk ve decision threshold
  - Fee-aware position sizing: pozisyon büyüklüğü, round-trip cost'un 1.5x üstünde net edge gerektirir
- Değişmez sınırlar: canlı emir yasağı, exchange_write=0, Windows uyumluluk, Binance Global/Spot API bağlantıları, SSL doğrulama (verify=False yasak), secret güvenliği.
- SUPERSEDES: ADR-014'ün strateji bağımsızlık kısıtını (ADR-014 teknik uyumluluk onarımlarını korur).

## ACTIVE — ADR-014 — alpha20_revize_v2 Windows uyumluluk onarımı

- Tarih: 2026-08-01
- Kullanıcı kararı: `alpha20_revize.zip` bulguları gerçek tam uygulamada
  yeniden üretilecek; strateji bağımsız olarak değiştirilmeden paket mevcut
  Windows başlangıç zincirine zarar vermeyecek biçimde onarılacaktır.
- Dar yetki: `alpha20_v1/alpha20.py`, `alpha20_v1/auto_controller.py`,
  `alpha20_v1/dual_model.py` ve `alpha20_v1/config.json` yalnız SHORT
  muhasebesi, minimum tutma, tekil maliyet hesabı, evren filtresi, aktif eşik
  ve Windows entegrasyonuyla sınırlı olarak değiştirilebilir.
- Entegrasyon: Tam `app.py`, `/home`, `start_alpha.cmd` →
  `launcher_windows.py` → `serve_windows.py` zinciri ve otomatik Paper
  bootstrap korunur; minimal `app.py` kullanılmaz.
- Değişmez sınırlar: yalnız PAPER; canlı emir, Futures, transfer ve borsa
  yazma isteği `0`; `.env`, secret, runtime/state ve işlem geçmişi pakete
  alınmaz veya değiştirilmez.
- SUPERSEDES: ADR-013'ün görev sırası. ADR-013 runtime bütünlüğü onarımları
  korunur.

## ACTIVE — ADR-013 — Kimi P0 runtime ve piyasa verisi onarımı

- Tarih: 2026-08-01
- Kullanıcı kararı: Kimi denetimindeki talimatlar bire bir uygulanacaktır;
  farklı strateji, kanıt kapısı veya yeni ADR yaklaşımı eklenmeyecektir.
- Uygulama sırası: `dual_model.py` runtime karantina/yedek/atomik yazım;
  `alpha20.py` ve diğer runtime okuyucularında fail-closed davranış;
  `auto_controller.py` gerçek zaman damgası, önceki fiyat, 24 saat hacim,
  spread/likidite ve bağımsız coin skoru; ardından strateji eşikleri
  değiştirilmeden Paper/dip-recovery testleri.
- Dar yetki: Bu P0 görevinde `alpha20_v1/dual_model.py`,
  `alpha20_v1/alpha20.py`, `alpha20_v1/universe_manager.py`,
  `alpha20_v1/dual_learning.py` ve `alpha20_v1/auto_controller.py` yalnız
  yukarıdaki veri/runtime bütünlüğü kapsamıyla değiştirilebilir.
- Değişmez sınırlar: canlı emirler kapalı, borsa yazma isteği `0`, risk ve
  strateji eşikleri değişmez; sahte/sabit piyasa değeri üretilmez.
- SUPERSEDES: ADR-011'in Paper akışını veri bütünlüğü onarımından önceleyen
  sırası. Paper hedefi iptal edilmez; P0 onarımından sonra devam eder.

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
kodun kendini değiştirmemesi (konfigürasyon/strateji parametrelerinin otonom evrimi ADR-015 ile sınırlı olarak açılmıştır) ve son kararın kullanıcıya ait olması yürürlüktedir.

Paper First, Risk First, Security First, Human Approval, Exchange Independent,
kodun kendini değiştirmemesi ve son kararın kullanıcıya ait olması yürürlüktedir.
