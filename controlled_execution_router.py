"""Mission 2100 — Agent 08: Kontrollü Yürütme yönlendiricisi.

Kapalı mod → servis eşlemesi:

    PAPER      → PaperExecutionService
    SHADOW     → ShadowModeService
    MICRO_LIVE → MicroLiveAuthorizationService

Doğrudan broker yönlendirmesi YOKTUR: yönlendirici yalnız Mission
2100 servislerini tanır; borsa/broker/ağ katmanına referans bile
tutmaz. Eşleme dışı her mod steril hatayla REDDEDİLİR
(fail-closed). Yönlendirici durumsuzdur ve kayıt kümesi kuruluşta
DONDURULUR.
"""

from __future__ import annotations

from types import MappingProxyType

from controlled_execution_api_errors import (
    ControlledExecutionAPIConfigurationError,
    ControlledExecutionAPIRoutingError)
from controlled_execution_models import ControlledExecutionMode
from micro_live_authorization import MicroLiveAuthorizationService
from paper_execution_service import PaperExecutionService
from shadow_mode import ShadowModeService

__all__ = ["ControlledExecutionRouter"]

_ERROR_CONFIGURATION = "API_CONFIGURATION"
_ERROR_ROUTING = "API_ROUTING"


class ControlledExecutionRouter:
    """Kapalı, değişmez mod → servis yönlendirme tablosu."""

    __slots__ = ("_routes",)

    def __init__(self, paper_service: PaperExecutionService,
                 shadow_service: ShadowModeService,
                 micro_live_service: MicroLiveAuthorizationService
                 ) -> None:
        if not isinstance(paper_service, PaperExecutionService):
            raise ControlledExecutionAPIConfigurationError(
                f"{_ERROR_CONFIGURATION}:INVALID_PAPER_SERVICE")
        if not isinstance(shadow_service, ShadowModeService):
            raise ControlledExecutionAPIConfigurationError(
                f"{_ERROR_CONFIGURATION}:INVALID_SHADOW_SERVICE")
        if not isinstance(micro_live_service,
                          MicroLiveAuthorizationService):
            raise ControlledExecutionAPIConfigurationError(
                f"{_ERROR_CONFIGURATION}"
                ":INVALID_MICRO_LIVE_SERVICE")
        object.__setattr__(self, "_routes", MappingProxyType({
            ControlledExecutionMode.PAPER: paper_service,
            ControlledExecutionMode.SHADOW: shadow_service,
            ControlledExecutionMode.MICRO_LIVE:
                micro_live_service,
        }))

    def __setattr__(self, name: str, value: object) -> None:
        raise ControlledExecutionAPIConfigurationError(
            f"{_ERROR_CONFIGURATION}:ROUTER_IMMUTABLE")

    @property
    def routes(self) -> MappingProxyType:
        """Salt-okunur yönlendirme tablosu."""
        return self._routes

    def resolve(self, mode: object) -> object:
        """Kapalı eşleme — bilinmeyen mod fail-closed reddedilir."""
        if not isinstance(mode, ControlledExecutionMode):
            raise ControlledExecutionAPIRoutingError(
                f"{_ERROR_ROUTING}:INVALID_MODE")
        service = self._routes.get(mode)
        if service is None:
            raise ControlledExecutionAPIRoutingError(
                f"{_ERROR_ROUTING}:UNROUTED_MODE")
        return service
