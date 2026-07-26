# ADR-002 — Treasury Accounting Contracts (ES-002 Rev 1)

**Durum:** Kabul Edildi — Revize Edildi  
**İlk tarih:** 2026-01-26  
**Revizyon tarihi:** 2026-07-26  
**Kapsam:** `alpha20_v1/treasury/`  
**Yazar:** Alpha Intelligence OS — Engineering

---

## Bağlam

Alpha Intelligence OS, PAPER (simüle) modda kripto işlemlerini takip eder.
Gerçek borsa entegrasyonu olmadan, muhasebe tutarlılığı ve finansal hesap
dürüstlüğü tamamen kod seviyesinde sağlanmalıdır.

Temel gereksinimler:
- İşlem başına K/Z hesaplaması (gerçekleşmiş + gerçekleşmemiş)
- LONG ve SHORT pozisyonlar için doğru muhasebe semantiği
- Çift kayıtlı muhasebe denge koşulu (DR = CR)
- Float kaynaklı kümülatif hata birikmesinin önlenmesi
- Exchange-independent tasarım (borsa API'si koda gömülmez)
- Değiştirilemez veri yapıları (hata ayıklanabilirlik)

---

## Karar

`alpha20_v1/treasury/` altında 9 modülden oluşan değiştirilemez domain sözleşmesi oluşturuldu.

### Modül Mimarisi

```
treasury/
├── precision.py      Decimal hassasiyet, yuvarlamalar, safe_divide
├── types.py          Tüm enum ve frozen dataclass tanımları
├── ledger.py         Çift kayıtlı journal sistemi + hesap şablonları
├── cost_basis.py     Ağırlıklı ortalama maliyet esası (WAVG)
├── fees.py           Ücret hesaplama ve FeeAccumulator
├── transfer.py       Transfer yaşam döngüsü durum makinesi
├── valuation.py      Mark-to-market pozisyon/portföy değerleme
├── reconciliation.py Muhasebe mutabakatı + risk kural doğrulaması
└── __init__.py       Birleşik public API
```

---

## Temel Tasarım Kararları

### 1. Float Politikası — Giriş Sınırı Normalleştirmesi

`float` yalnızca domain API'nin **giriş sınırında** kabul edilir:
- `from_float(x)` → `Decimal(str(x))` ile normalize edilir
- `to_decimal(x)` → tüm sayısal türleri kabul eder, float → str → Decimal

**İç hesaplamalar tamamen Decimal'dir.** Muhasebe değerlerine hiçbir zaman
float aritmetiği uygulanmaz. "Float kullanımı sıfır" ifadesi iç hesaplamalar
için doğrudur; giriş sınırındaki normalleştirme kasıtlı ve belgeli bir karardır.

**Neden:** 0.1 + 0.2 ≠ 0.3 (float). Kümülatif hata muhasebe tutarsızlığına yol açar.

### 2. Decimal Global Context — Yan Etki Yok

`precision.py` import edildiğinde **global `setcontext` çağrılmaz**.
Tüm yuvarlama işlemleri `quantize(..., rounding=ROUND_HALF_EVEN)` ile
**çağrı bazında** yapılır.

**Neden:** Global context mutasyonu diğer modüllerin Decimal davranışını
beklenmedik biçimde değiştirebilir. Çağrı bazında yuvarlama izole, öngörülebilir
ve test edilebilirdir.

### 3. Değiştirilemez Veri Yapıları

`@dataclass(frozen=True)` — tüm domain nesneleri değiştirilemez.
Durum geçişleri (transfer, journal) yeni nesne üretir.

**Neden:** Audit trail güvenilirliği. Geçmiş kayıtlar sonradan değiştirilemez.

### 4. Çift Kayıtlı Muhasebe

Her finansal hareket bir `JournalEntry` üretir.
`is_balanced()` → `|Σ(DR) - Σ(CR)| ≤ tolerance`
Şablon fonksiyonları her zaman dengeli journal üretir.
Dengesiz journal `validate_journal()` tarafından `LedgerImbalanceError` ile reddedilir.

**Neden:** Tek kayıtlı sistemde "phantom" bakiye hataları tespit edilemez.

### 5. SHORT Muhasebesi — PAPER Basitleştirilmiş Model

SHORT pozisyon, LONG gibi teminat esaslı muhasebeleştirilir:

**Açılış:**
```
DR PAPER_POSITION:{SYMBOL}   collateral (= cost_basis)
   CR PAPER_CASH             collateral
```
Nakit etkisi: -collateral (teminat ayrıldı).

**Kapanış (kâr, exit_nominal < avg_cost):**
```
DR PAPER_CASH               cost_basis + pnl   (teminat geri + kâr)
   CR PAPER_POSITION:SYM    cost_basis          (teminat hesabı kapatıldı)
   CR PAPER_REALIZED_PNL    pnl                 (kâr)
```

**Kapanış (zarar, exit_nominal > avg_cost):**
```
DR PAPER_CASH               cost_basis - zarar  (teminat geri - zarar)
DR PAPER_REALIZED_PNL       zarar
   CR PAPER_POSITION:SYM    cost_basis
```

`exit_value_usdt` parametresi her iki yön için `qty × exit_price` (exit nominal).
Nakit etkisi `build_position_close_journal` içinde side-aware hesaplanır.

Kısıtlama: SHORT zarar teminatı aşamaz (margin call PAPER modda desteklenmez).

**Neden:** PAPER modda leverage yok; teminat = tam pozisyon değeri.
Basitleştirilmiş model, liability/margin hesapları olmadan doğru K/Z üretir.

### 6. Portfolio NAV — LONG/SHORT Ayrımı

```
NAV = nakit + LONG pozisyon değeri + SHORT özkaynak

LONG pozisyon değeri = Σ(LONG qty × current_price)
SHORT özkaynak       = Σ(SHORT collateral + unrealized_pnl)
                     = Σ(avg_cost × qty + (avg_cost - current) × qty)
```

SHORT `mark_to_market_usdt` (qty × current_price) **doğrudan NAV'a eklenmez**.
Bu değer ham piyasa verisidir; SHORT bir yükümlülüktür, varlık değil.

**Neden:** SHORT pozisyonun açık piyasa değerini varlık gibi eklemek NAV'ı şişirir.
Doğru model: teminat + gerçekleşmemiş K/Z = pozisyondan beklenen nakit.

### 7. Funding Muhasebesi — Ödeme / Tahsilat Ayrımı

```
Ödeme (is_income=False — trader borçlu):
  DR PAPER_FUNDING_EXPENSE   amount
     CR PAPER_CASH           amount     ← nakit azalır

Tahsilat (is_income=True — trader alacaklı):
  DR PAPER_CASH              amount     ← nakit artar
     CR PAPER_FUNDING_INCOME amount
```

**Neden:** Her iki yönü aynı hesapta DR/CR olarak kaydetmek bakiye raporunu bozar.
Gider ve gelir hesapları ayrı tutulmalıdır.

### 8. WAVG Invariant

`compute_weighted_average` ve `add_lot_and_recompute` çağrılarında tüm lotların
aynı `symbol` ve `side` değerine sahip olduğu doğrulanır. Karışık durumda
`CostBasisError` fırlatılır.

**Neden:** Farklı sembol veya yöne ait lotların WAVG'si finansal olarak anlamsızdır.

---

## Test Sonuçları

| Kapsam | Geçen | Toplam |
|---|---|---|
| Treasury sözleşme testleri | 131 | 131 |
| Tam repository | 252 | 252 |

Treasury test sınıfları:
- `TestPrecision` — Decimal dönüşüm, yuvarlama, banker's rounding
- `TestDomainTypes` — Değiştirilemezlik, field validation
- `TestLedgerBalance` — Denge koşulu
- `TestJournalTemplates` — LONG/SHORT journal şablonları
- `TestCostBasis` — WAVG, lot yönetimi, K/Z
- `TestFees` — Ücret hesaplama, FeeAccumulator
- `TestTransferLifecycle` — Durum makinesi
- `TestValuation` — LONG/SHORT mark-to-market, portföy NAV
- `TestReconciliation` — Mutabakat kontrolleri
- `TestPublicAPI` — Import bütünlüğü, PAPER kilidi
- `TestSHORTAccounting` — SHORT journal semantiği ve K/Z
- `TestPortfolioNAV` — LONG-only / SHORT-only / karma NAV
- `TestFundingJournal` — Ödeme ve tahsilat ayrımı
- `TestWAVGInvariant` — Homojenlik doğrulaması
- `TestDecimalContext` — Global context değişmezliği

---

## Alternatifler Değerlendirilen

| Alternatif | Neden Reddedildi |
|---|---|
| SHORT için LIABILITY hesabı | Aşırı karmaşık; PAPER modda leverage yok |
| FIFO maliyet esası | Exchange-bağımlı; WAVG daha tarafsız |
| Float aritmetiği | IEEE 754 kümülatif hatası kabul edilemez |
| Global setcontext | Diğer modüllere yan etki riski |
| Tek funding hesabı | Gelir/gider ayrımı olmadan bakiye raporu bozulur |

---

## Bilinen Sınırlamalar

- DB kalıcılığı yok (ES-004'e bırakıldı)
- Async desteği yok — senkron hesaplama
- Yalnızca USDT quote currency; çoklu kur desteği planlanmadı
- SHORT margin call (zarar > collateral) PAPER modda desteklenmiyor
- pytest-cov kurulu olmadığından satır bazında coverage raporu üretilmedi

---

## Bağlantılı Belgeler

- `docs/architecture.md` — Genel sistem haritası
- `docs/security.md` — Güvenlik katmanları
- `alpha20_v1/treasury/__init__.py` — Public API referansı
- `tests/test_treasury_contracts.py` — Tam sözleşme test paketi
