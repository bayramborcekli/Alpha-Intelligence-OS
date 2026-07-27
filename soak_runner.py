"""Mission 2100 — Agent 09: Deterministik soak (dayanım) koşucusu.

Bu modül YENİ iş işlevi içermez ve Agents 01–08'i DEĞİŞTİRMEZ.
Duvar saati YOKTUR: soak süreleri MANTIKSAL saat profilleridir —
her mantıksal saat sabit sayıda deterministik çevrime eşlenir.
Koşucu, çağıranın verdiği saf işlemi N kez yürütür ve şunları
doğrular:

- Deterministik davranış: her çevrim sonucu ilk çevrimle birebir
  aynı olmalıdır (sapma sayısı raporlanır).
- Anlık görüntü bütünlüğü: çağıranın verdiği referans girdi her
  çevrim sonrası değişmemiş olmalıdır.
- Kaynak/nesne sızıntısı: işlemler değişmez zarflar döndürür;
  koşucu sonuç biriktirmez (sabit bellek), yalnız sayaç tutar.

Zamanlayıcı, iş parçacığı, uyku, ağ ve dosya erişimi YOKTUR.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

__all__ = ["SoakProfile", "SoakReport", "SOAK_PROFILES",
           "CYCLES_PER_LOGICAL_HOUR", "profile_by_name",
           "run_soak", "SoakContractError"]

# Bir mantıksal saat = 60 deterministik çevrim (sabit, bilinçli)
CYCLES_PER_LOGICAL_HOUR = 60


class SoakContractError(Exception):
    """Soak sözleşme ihlali — steril kod taşır."""


def _fail(field: str) -> None:
    raise SoakContractError(f"INVALID_SOAK_FIELD:{field}")


@dataclass(frozen=True, slots=True)
class SoakProfile:
    """Değişmez soak profili — mantıksal süre tanımı."""

    name: str
    logical_hours: int
    cycles: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            _fail("name")
        if isinstance(self.logical_hours, bool) or \
                not isinstance(self.logical_hours, int) or \
                self.logical_hours <= 0:
            _fail("logical_hours")
        if isinstance(self.cycles, bool) or \
                not isinstance(self.cycles, int) or \
                self.cycles != self.logical_hours * \
                CYCLES_PER_LOGICAL_HOUR:
            _fail("cycles")


# Spesifikasyon profilleri: 1 / 6 / 12 / 24 mantıksal saat
SOAK_PROFILES = (
    SoakProfile(name="SOAK_1H", logical_hours=1, cycles=60),
    SoakProfile(name="SOAK_6H", logical_hours=6, cycles=360),
    SoakProfile(name="SOAK_12H", logical_hours=12, cycles=720),
    SoakProfile(name="SOAK_24H", logical_hours=24,
                cycles=1440),
)


def profile_by_name(name: str) -> SoakProfile:
    """Kapalı profil kümesinden ada göre profil (fail-closed)."""
    for profile in SOAK_PROFILES:
        if profile.name == name:
            return profile
    raise SoakContractError("INVALID_SOAK_FIELD:profile_name")


@dataclass(frozen=True, slots=True)
class SoakReport:
    """Değişmez soak sonucu."""

    profile_name: str
    logical_hours: int
    cycles_executed: int
    deterministic: bool
    divergence_count: int
    state_intact: bool

    def __post_init__(self) -> None:
        if not isinstance(self.profile_name, str) or \
                not self.profile_name:
            _fail("profile_name")
        for field_name in ("logical_hours", "cycles_executed",
                           "divergence_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or \
                    not isinstance(value, int) or value < 0:
                _fail(field_name)
        if not isinstance(self.deterministic, bool):
            _fail("deterministic")
        if not isinstance(self.state_intact, bool):
            _fail("state_intact")

    @property
    def passed(self) -> bool:
        return self.deterministic and self.state_intact and \
            self.divergence_count == 0


def run_soak(profile: SoakProfile,
             operation: Callable[[], object],
             reference_state: Optional[object] = None,
             state_probe: Optional[
                 Callable[[], object]] = None) -> SoakReport:
    """Profili deterministik olarak koşar.

    `operation`: her çevrimde çağrılan SAF işlem; değişmez ve
    karşılaştırılabilir bir sonuç döndürmelidir.
    `reference_state` + `state_probe`: verilirse her çevrim
    sonrası `state_probe()` sonucu `reference_state` ile
    karşılaştırılır (anlık görüntü bozulması tespiti).
    Sonuçlar BİRİKTİRİLMEZ: yalnız ilk sonuç ve sayaçlar tutulur
    (sabit bellek — kaynak sızıntısı yok)."""
    if not isinstance(profile, SoakProfile):
        _fail("profile")
    if not callable(operation):
        _fail("operation")
    if (reference_state is None) != (state_probe is None):
        _fail("state_probe")
    if state_probe is not None and not callable(state_probe):
        _fail("state_probe")

    baseline = operation()
    # Yerinde mutasyon koruması: temel sonucun yazılı temsili
    # koşu başında sabitlenir; aynı nesnenin sonradan mutasyonu
    # eşitlik karşılaştırmasını KANDIRAMAZ.
    baseline_image = repr(baseline)
    divergence = 0
    state_intact = True
    if state_probe is not None and \
            state_probe() != reference_state:
        state_intact = False
    for _cycle in range(profile.cycles - 1):
        result = operation()
        if result != baseline or \
                repr(result) != baseline_image:
            divergence = divergence + 1
        if repr(baseline) != baseline_image:
            state_intact = False
        if state_probe is not None and \
                state_probe() != reference_state:
            state_intact = False
    return SoakReport(
        profile_name=profile.name,
        logical_hours=profile.logical_hours,
        cycles_executed=profile.cycles,
        deterministic=divergence == 0,
        divergence_count=divergence,
        state_intact=state_intact)
