# Mission 2200 — Agent 02: Profesyonel İşlem Çalışma Alanı

## Kapsam

`/operation-center` sayfası, girişten sonra varsayılan açılış sayfası
olan tam ekran profesyonel işlem çalışma alanına dönüştürüldü.
Mission 2100 yürütme çekirdeğine DOKUNULMADI; tüm eylemler mevcut
`/api/operation-control/*` kontrollü hattından geçer.

## Yeni saf modüller

| Modül | Sorumluluk |
|---|---|
| `operation_workspace_metrics.py` | Decimal-only performans metrikleri: kazanma oranı, kâr faktörü, Sharpe (örneklem std, n-1), maks. düşüş, dönem kârları. Veri yoksa `None` (UNKNOWN) — asla sahte 0. Float girdiler dönüştürülmez, düşürülür ve `dropped_records` ile raporlanır. |
| `operation_workspace_models.py` | Dondurulmuş görünüm modelleri (`PortfolioView`, `PerformanceView`, `BrokerHealthView`, `StrategyView`, `JournalEventView`); katı doğrulama, steril `INVALID_WORKSPACE_FIELD:<alan>` hataları. |
| `operation_workspace_service.py` | Görünüm kurucuları. Açık risk, HERHANGİ bir pozisyonda stop/giriş/miktar eksikse UNKNOWN (kısmi toplam yanıltıcıdır). Günlük yalnız denetimli kaynaklardan (sertifikalı sinyal olayları + operasyon denetim zinciri) beslenir. |
| `operation_workspace_api.py` | Agent 01 okuma zarfının aynen kullanımı + CSV dışa aktarım. Formül enjeksiyonu koruması baştaki boşluk/denetim karakterlerini de tarar (`\t=`, ` =`, `\n=` baypasları kapalı). `None` → `UNKNOWN`. |

## Uçlar (hepsi salt-okunur GET, kimlik doğrulama zorunlu)

- `/api/operation-control/workspace/{portfolio,performance,broker-health,strategies,journal}`
- `/api/operation-control/workspace/export/{positions,orders,signals,journal}.csv`
  (bilinmeyen ad → 404; hücre değerleri steril)

## Arayüz

- Üst çubuk: sistem durumu, yürütme modu, bot durumu, son güncelleme,
  tazelik, broker durumu, gecikme, kill-switch göstergesi.
- Portföy çubuğu, sol strateji paneli (Duraklat/Sürdür/Devre Dışı/
  Yeniden Etkinleştir — hepsi gerçek `/symbols/<sembol>/<komut>`
  uçlarına bağlı; Ayrıntı istemci tarafı filtre), orta pozisyon
  tablosu (Süre sütunu eklendi), sağ sinyal paneli, alt emir tablosu
  (Enter/tık ile genişleyen yaşam döngüsü satırı), performans,
  işlem günlüğü, broker sağlığı, mutabakat, denetim, risk panelleri.
- Agent 01 `oc-*` kimliklerinin TÜMÜ korundu (eski testler geçiyor).
- Erişilebilirlik: klavye ile sıralanabilir başlıklar (tabindex +
  Enter/Space), aria-label'lı arama kutuları, aria-live bölgeleri,
  `resize:vertical` tablolar, tek sütuna inen duyarlı yerleşim.
- Gerçek zamanlılık: 10 sn yoklama (bu depoda SSE/WebSocket
  sertifikasyonca yasak). Yenileme operatör durumunu SIFIRLAMAZ:
  arama metni, sıralama tercihi ve genişletilmiş satırlar
  MutationObserver ile yeniden uygulanır.
- Renk semantiği: yeşil kâr / kırmızı zarar / kehribar beklemede /
  mavi bilgi / gri UNKNOWN. UNKNOWN asla başarı gibi stillenmez.

## Dürüstlük kararları

- `reconnect_count`: depoda ölçen bileşen yok → daima UNKNOWN (null).
- Broker gecikmesi/kalp atışı: `positions_view()` çağrısı çevresinde
  süreç-yerel ölçüm; 60 sn'den eski kalp atışı STALE.
- Sharpe: <2 getiri veya std=0 → UNKNOWN. Dönem kârı: pencerede işlem
  yoksa UNKNOWN (0 değil).
- Stop Taşı / TP Güncelle / Limit Düzenle: sertifikalı API
  desteklemiyor → açıkça devre dışı düğme, dekoratif uç YOK.

## Test sonucu

- 758 yeni PASS, 6 dosya: `test_operation_workspace_metrics.py`,
  `_models.py`, `_service.py`, `_api.py`, `_ui.py`, `_bindings.py`
  (mimari inceleme sonrası 8 CSV-enjeksiyon regresyon testi dahil).
- Tam regresyon: **12.757 PASS + 1 bilinen skip, 0 FAIL**
  (taban 11.999 + 758 yeni).

## Mimari inceleme

Bağımsız mimar incelemesi tek engelleyici bulgu verdi: CSV formül
enjeksiyonu korumasının baştaki boşluk baypası. Düzeltildi ve
regresyon testleriyle kilitlendi. Diğer değişmezler (doğrudan borsa
yazımı yok, fail-closed, steril hatalar, sahte düğme yok, Mission
2100 dokunulmazlığı, sır sızıntısı yok) doğrulandı.
