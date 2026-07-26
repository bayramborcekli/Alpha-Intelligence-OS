"""
treasury/precision.py — Sabit ondalık hassasiyet sistemi.

Tüm muhasebe hesaplamalarında float yerine Decimal kullanılır.
IEEE 754 yuvarlama hataları (örn. 9949.999999999998) bu modül ile önlenir.

Float politikası:
  float yalnızca giriş sınırında kabul edilir: from_float(x) veya to_decimal(x)
  aracılığıyla Decimal(str(x)) ile normalize edilir. İç hesaplamalar tamamen
  Decimal'dir; float aritmetiği hiçbir zaman muhasebe değerlerine uygulanmaz.

Global context yan etkisi:
  Bu modül import edildiğinde global Decimal context'i DEĞİŞTİRMEZ.
  Tüm yuvarlama işlemleri açık quantize(..., rounding=ROUND_HALF_EVEN) ile
  çağrı bazında yapılır. Yan etki riski yoktur.

Kurallar:
- Tüm finansal miktarlar Decimal tipinde saklanır.
- Yuvarlama: ROUND_HALF_EVEN (banker's rounding) — sistematik hatayı önler.
- float → Decimal dönüşümü yalnızca from_float() ile yapılır (str üzerinden).
- Decimal → float dönüşümü yalnızca sunum katmanında izin verilir.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Union

# ── Hassasiyet sabitleri ──────────────────────────────────────────────────────
QUANT_AMOUNT  = Decimal("0.00000001")   # 8 basamak — USDT miktarları
QUANT_QTY     = Decimal("0.00000001")   # 8 basamak — coin miktarları
QUANT_PRICE   = Decimal("0.00000001")   # 8 basamak — fiyatlar
QUANT_RATE    = Decimal("0.00000001")   # 8 basamak — oran / yüzde
QUANT_DISPLAY = Decimal("0.01")         # 2 basamak — kullanıcıya gösterim

# Sayısal sıfır / bir sabitleri
ZERO = Decimal("0")
ONE  = Decimal("1")

# ── Tür takma adı ─────────────────────────────────────────────────────────────
Number = Union[Decimal, int, str, float]


# ── Dönüştürücüler ────────────────────────────────────────────────────────────

def from_float(value: float) -> Decimal:
    """
    float'ı hassas Decimal'e dönüştür.
    str() üzerinden gidilir — IEEE 754 kirliliği önlenir.
    Bu, domain API'sinin float kabul eden tek giriş noktasıdır.

    >>> from_float(9949.999999999998)
    Decimal('9949.999999999998')
    >>> from_float(0.1) + from_float(0.2) == from_float(0.3)
    True
    """
    if not isinstance(value, (int, float)):
        raise TypeError(f"from_float yalnızca int/float kabul eder, {type(value)} değil.")
    return Decimal(str(value))


def to_decimal(value: Number) -> Decimal:
    """
    Herhangi bir sayısal türü Decimal'e dönüştür.
    - Decimal  → değişmeden döner
    - int/float → from_float üzerinden (giriş sınırı normalleştirmesi)
    - str       → Decimal(str) — leading whitespace strip edilir
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return from_float(float(value))
    if isinstance(value, str):
        return Decimal(value.strip())
    raise TypeError(f"Desteklenmeyen tür: {type(value)}")


# ── Quantize yardımcıları ─────────────────────────────────────────────────────
# Tüm yuvarlama açık quantize() ile yapılır — global context'e bağımlılık yok.

def q_amount(value: Number) -> Decimal:
    """USDT miktarını 8 basamağa yuvarla (ROUND_HALF_EVEN)."""
    return to_decimal(value).quantize(QUANT_AMOUNT, rounding=ROUND_HALF_EVEN)


def q_qty(value: Number) -> Decimal:
    """Coin miktarını 8 basamağa yuvarla (ROUND_HALF_EVEN)."""
    return to_decimal(value).quantize(QUANT_QTY, rounding=ROUND_HALF_EVEN)


def q_price(value: Number) -> Decimal:
    """Fiyatı 8 basamağa yuvarla (ROUND_HALF_EVEN)."""
    return to_decimal(value).quantize(QUANT_PRICE, rounding=ROUND_HALF_EVEN)


def q_rate(value: Number) -> Decimal:
    """Oranı / yüzdeyi 8 basamağa yuvarla (ROUND_HALF_EVEN)."""
    return to_decimal(value).quantize(QUANT_RATE, rounding=ROUND_HALF_EVEN)


def q_display(value: Number) -> Decimal:
    """Gösterim için 2 basamağa yuvarla (sunum katmanı)."""
    return to_decimal(value).quantize(QUANT_DISPLAY, rounding=ROUND_HALF_EVEN)


# ── Güvenli aritmetik ─────────────────────────────────────────────────────────

def safe_divide(numerator: Number, denominator: Number) -> Decimal:
    """
    Sıfıra bölmeyi yakala; Decimal("0") döndür.
    Muhasebede bölme işlemi her zaman bu fonksiyon üzerinden yapılır.
    """
    d = to_decimal(denominator)
    if d == ZERO:
        return ZERO
    return to_decimal(numerator) / d


def pct_of(amount: Number, pct: Number) -> Decimal:
    """
    amount'ın pct yüzdesini hesapla.
    pct 0-100 skalasında verilir: pct_of(10000, 0.25) → 25.00000000
    """
    return q_amount(to_decimal(amount) * to_decimal(pct) / Decimal("100"))


def abs_amount(value: Number) -> Decimal:
    """Mutlak değer — negatif tutarları pozitife çevirir."""
    return abs(to_decimal(value))
