"""Mission 2100 — Agent 01: Mod güvenlik modeli, geçiş politikası
ve genişleme kayıt defteri.

Bildirimsel katman: her modun KALICI güvenlik sözleşmesi, izinli
mod geçiş matrisi ve gelecek genişleme noktalarının kapalı
bildirimi. Uygulama YOKTUR: plugin yükleme, dinamik import, dosya
sistemi taraması ve paket kurulumu yasaktır.

Varsayılan güvenlik: bilinmeyen/eksik/belirsiz her durum KAPALI
(fail-closed) değerlendirilir. Örtük mod yükseltmesi yoktur:
ortam/zaman/strateji/broker/AI kaynaklı yükseltme yasak; tek
gelecek yükseltme yolu (SHADOW → MICRO_LIVE) ayrı, açık insan
yetkilendirme bileşenine bırakılmıştır.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Optional

from controlled_execution_errors import (
    ControlledExecutionContractError)
from controlled_execution_models import ControlledExecutionMode

__all__ = ["ExtensionPoint", "ExtensionRegistry"]

_ERROR_INVALID_FIELD = "INVALID_CONTROLLED_MODEL_FIELD"


class ExtensionPoint(Enum):
    """Gelecek genişleme noktalarının kapalı bildirimi.

    Yalnız sözleşme/bildirim — uygulama sonraki ajanlardadır.
    """

    PAPER_EXECUTION_PROVIDER = "PaperExecutionProvider"
    SHADOW_OBSERVATION_PROVIDER = "ShadowObservationProvider"
    MICRO_LIVE_AUTHORIZATION_PROVIDER = (
        "MicroLiveAuthorizationProvider")
    RUNTIME_STATE_PROVIDER = "RuntimeStateProvider"
    RUNTIME_AUDIT_SINK = "RuntimeAuditSink"
    DESKTOP_CLIENT_ADAPTER = "DesktopClientAdapter"
    MOBILE_CLIENT_ADAPTER = "MobileClientAdapter"
    UPDATE_MANAGER_ADAPTER = "UpdateManagerAdapter"
    PLUGIN_PROVIDER = "PluginProvider"


@dataclass(frozen=True, slots=True)
class ExtensionRegistry:
    """Deterministik, değişmez, SINIRLI genişleme kayıt defteri.

    Yalnız bildirilen noktaları taşır; yükleme/keşif/kurulum
    yapmaz. Kayıt kümesi kapalı ExtensionPoint enum'u ile sınırlı
    ve tekrarsızdır.
    """

    points: tuple = tuple(ExtensionPoint)

    def __post_init__(self) -> None:
        if not isinstance(self.points, tuple):
            raise ControlledExecutionContractError(
                f"{_ERROR_INVALID_FIELD}:points")
        seen = set()
        for point in self.points:
            if not isinstance(point, ExtensionPoint) or \
                    point in seen:
                raise ControlledExecutionContractError(
                    f"{_ERROR_INVALID_FIELD}:points")
            seen.add(point)

    def is_declared(self, point: object) -> bool:
        """Sabit-zamanlı üyelik; bilinmeyen girdi → False."""
        if not isinstance(point, ExtensionPoint):
            return False
        return point in self.points


@dataclass(frozen=True, slots=True)
class _ModeSafety:
    """Bir modun KALICI güvenlik sözleşmesi (iç, değişmez)."""

    exchange_write_allowed: bool
    simulated_fill_allowed: bool
    broker_read_allowed: bool
    human_confirmation_required: bool
    explicit_authorization_required: bool


# Kalıcı mod güvenlik sözleşmeleri (Agent 01: Micro Live'ın
# sözleşmesi TANIMLANIR ama etkinleştirilmez — borsa yazmaları
# çalışma zamanında her zaman reddedilir)
_MODE_SAFETY = MappingProxyType({
    ControlledExecutionMode.PAPER: _ModeSafety(
        exchange_write_allowed=False,
        simulated_fill_allowed=True,
        broker_read_allowed=False,
        human_confirmation_required=False,
        explicit_authorization_required=False),
    ControlledExecutionMode.SHADOW: _ModeSafety(
        exchange_write_allowed=False,
        simulated_fill_allowed=False,
        broker_read_allowed=True,
        human_confirmation_required=False,
        explicit_authorization_required=False),
    ControlledExecutionMode.MICRO_LIVE: _ModeSafety(
        exchange_write_allowed=True,
        simulated_fill_allowed=False,
        broker_read_allowed=True,
        human_confirmation_required=True,
        explicit_authorization_required=True),
})

# Varsayılan mod: PAPER (yazmayan)
_DEFAULT_MODE = ControlledExecutionMode.PAPER

# İzinli doğrudan geçişler — otomatik/örtük yükseltme YOK
_ALLOWED_TRANSITIONS = frozenset({
    (ControlledExecutionMode.PAPER,
     ControlledExecutionMode.SHADOW),
    (ControlledExecutionMode.SHADOW,
     ControlledExecutionMode.PAPER),
})

# Gelecekte YALNIZ açık yetkilendirme bileşeniyle açılabilecek
# kontrollü geçiş (Agent 01'de izinli DEĞİL)
_FUTURE_AUTHORIZED_TRANSITIONS = frozenset({
    (ControlledExecutionMode.SHADOW,
     ControlledExecutionMode.MICRO_LIVE),
})
