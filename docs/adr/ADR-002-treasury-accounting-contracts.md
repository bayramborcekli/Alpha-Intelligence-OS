# ADR-002 — Treasury Accounting Contracts (ES-002)

**Durum:** Kabul Edildi  
**Tarih:** 2026-01-26  
**Kapsam:** `alpha20_v1/treasury/`  
**Yazar:** Alpha Intelligence OS — Engineering

---

## Bağlam

Alpha Intelligence OS, PAPER (simüle) modda kripto işlemlerini takip eder.
Gerçek borsa entegrasyonu olmadan, muhasebe tutarlılığı ve finansal hesap
dürüstlüğü tamamen kod seviyesinde sağlanmalıdır.

Temel gereksinimler:
- İşlem başına K/Z hesaplaması (gerçekleşmiş + gerçekleşmemiş)
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
├── precision.py      Decimal bağlamı, yuvarlamalar, safe_divide
├── types.py          Tüm enum ve frozen dataclass tanımları
├── ledger.py         Çift kayıtlı journal sistemi + hesap şablonları
├── cost_basis.py     Ağırlıklı ortalama maliyet esası (WAVG)
├── fees.py           Ücret hesaplama ve FeeAccumulator
├── transfer.py       Transfer yaşam döngüsü durum makinesi
├── valuation.py      Mark-to-market pozisyon/portföy değerleme
├── reconciliation.py Muhasebe mutabakatı + risk kural doğrulaması
└── __init__.py       Birleşik public API
```

### Temel Tasarım Kararları

#### 1. Decimal Zorunluluğu — Float Yasağı
Tüm finansal hesaplamalar `decimal.Decimal` ile yapılır.
`from_float(x)` → `Decimal(str(x))` yoluyla IEEE 754 kirliliği önlenir.
`q_amount()`, `q_price()`, `q_qty()`, `q_rate()` yuvarlama fonksiyonları
ROUND_HALF_EVEN (banker's rounding) kullanır.

**Neden:** 0.1 + 0.2 ≠ 0.3 (float). Kümülatif hata muhasebe tutarsızlığına yol açar.

#### 2. Değiştirilemez Veri Yapıları
`@dataclass(frozen=True)` — tüm domain nesneleri değiştirilemez.
Durum geçişleri (transfer, journal) yeni nesne üretir.

**Neden:** Audit trail güvenilirliği. Geçmiş kayıtlar sonradan değiştirilemez.

#### 3. Çift Kayıtlı Muhasebe
Her finansal hareket bir `JournalEntry` üretir.
`is_balanced()` → `|Σ(DR) - Σ(CR)| ≤ tolerance`
Template fonksiyonları (`build_position_open_journal` vb.) her zaman dengeli journal üretir.
Dengesiz journal `validate_journal()` tarafından `LedgerImbalanceError` ile reddedilir.

**Neden:** Tek kayıtlı sistemde "phantom" bakiye hataları tespit edilemez.

#### 4. WAVG Maliyet Esası
Birden fazla lotlu pozisyonlarda ağırlıklı ortalama birim maliyet kullanılır.
`consume_lots_wavg()` kısmi kapamayı doğru orantılı uygular.

**Neden:** FIFO/LIFO exchange-bağımlıdır; WAVG standart ve tarafsızdır.

#### 5. Transfer Durum Makinesi
```
PENDING → SUBMITTED → CONFIRMED → SETTLED (terminal)
                    ↘ FAILED              (terminal)
PENDING → CANCELLED                       (terminal)
```
Terminal durumlardan geçiş `TransitionError` fırlatır.
`settle()` PAPER modunda tüm adımları anında tamamlar.

**Neden:** Gerçek borsa gecikmelerini simüle etmek yerine, PAPER modunda
ani yerleştirme gerçekçi ve güvenlidir.

#### 6. Exchange-Independent Tasarım
Hiçbir modül borsa adı, API anahtarı veya HTTP uç noktası içermez.
Fiyatlar dışarıdan enjekte edilir (`current_price` parametresi).

**Neden:** Gelecekte farklı veri kaynakları (Binance, Bybit, mock) sorunsuz takılabilir.

#### 7. Bağımsız Mutabakat Kontrolleri
Her `check_*()` fonksiyonu bağımsız `CheckResult` üretir.
`reconcile_all()` başarısız olan kontroller olsa bile tümünü çalıştırır.

**Neden:** "Fail fast" yerine "fail wide" — operatör tüm sorunları tek seferde görmeli.

---

## Alternatifler Değerlendirilen

| Alternatif | Neden Reddedildi |
|---|---|
| SQLAlchemy ORM ile veritabanı | ES-002 kapsamı: yalnızca domain sözleşmeleri. DB entegrasyonu ES-004+ |
| FIFO maliyet esası | Exchange-bağımlı; WAVG daha tarafsız |
| Float aritmetiği | IEEE 754 kümülatif hatası kabul edilemez |
| Değiştirilebilir (mutable) nesneler | Audit trail güvenilirliğini bozar |
| Exchange API doğrudan çağrısı | PAPER modu ihlali |

---

## Sonuçlar

### Olumlu
- 228/228 test geçiyor (107 treasury sözleşme testi dahil)
- Float kullanımı sıfır (kaynak tarama ile doğrulandı)
- Her journal kaydı dengesi matematiksel olarak garanti
- Tüm tip dönüşümleri `precision.py` üzerinden tek merkezden

### Olumsuz / Sınırlamalar
- DB kalıcılığı yok (ES-004'e bırakıldı)
- Async desteği yok (senkron hesaplama)
- Çoklu para birimi (non-USDT) desteği yok

---

## Bağlantılı Belgeler

- `docs/architecture.md` — Genel sistem haritası
- `docs/security.md` — Güvenlik katmanları
- `alpha20_v1/treasury/__init__.py` — Public API referansı
- `tests/test_treasury_contracts.py` — Tam sözleşme test paketi
