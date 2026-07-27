"""Mission 2100 — Agent 08: Yönlendirici testleri.

Kapalı mod → servis eşlemesi, kuruluş sözleşmesi, değişmezlik ve
doğrudan broker yönlendirmesi olmaması.
"""

import sys
from pathlib import Path
from types import MappingProxyType

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlled_execution_api_errors import (  # noqa: E402
    ControlledExecutionAPIConfigurationError,
    ControlledExecutionAPIError,
    ControlledExecutionAPIRoutingError)
from controlled_execution_foundation import (  # noqa: E402
    ControlledExecutionFoundation)
from controlled_execution_models import (  # noqa: E402
    ControlledExecutionMode)
from controlled_execution_policy import (  # noqa: E402
    ExtensionRegistry)
from controlled_execution_router import (  # noqa: E402
    ControlledExecutionRouter)
from execution_risk_models import (RiskDecision,  # noqa: E402
                                   RiskDecisionType)
from micro_live_authorization import (  # noqa: E402
    MicroLiveAuthorizationService)
from paper_broker import PaperBroker  # noqa: E402
from paper_execution_service import (  # noqa: E402
    PaperExecutionService, StaticRiskEvaluator)
from shadow_mode import ShadowModeService  # noqa: E402

BROKER = PaperBroker(known_symbols=("BTCUSDT",))
FOUNDATION = ControlledExecutionFoundation(ExtensionRegistry())
ALLOW = StaticRiskEvaluator(RiskDecision(
    decision=RiskDecisionType.ALLOW))

PAPER = PaperExecutionService(broker=BROKER,
                              foundation=FOUNDATION,
                              risk_evaluator=ALLOW)
SHADOW = ShadowModeService(broker=BROKER, foundation=FOUNDATION,
                           risk_evaluator=ALLOW)
MICRO = MicroLiveAuthorizationService(foundation=FOUNDATION)


def make_router():
    return ControlledExecutionRouter(PAPER, SHADOW, MICRO)


class TestConstruction:
    def test_valid_construction(self):
        assert isinstance(make_router(),
                          ControlledExecutionRouter)

    @pytest.mark.parametrize("bad", [None, "paper", 1, SHADOW])
    def test_invalid_paper_service(self, bad):
        with pytest.raises(
                ControlledExecutionAPIConfigurationError) as e:
            ControlledExecutionRouter(bad, SHADOW, MICRO)
        assert "INVALID_PAPER_SERVICE" in str(e.value)

    @pytest.mark.parametrize("bad", [None, "shadow", 2, PAPER])
    def test_invalid_shadow_service(self, bad):
        with pytest.raises(
                ControlledExecutionAPIConfigurationError) as e:
            ControlledExecutionRouter(PAPER, bad, MICRO)
        assert "INVALID_SHADOW_SERVICE" in str(e.value)

    @pytest.mark.parametrize("bad", [None, "micro", 3, PAPER])
    def test_invalid_micro_service(self, bad):
        with pytest.raises(
                ControlledExecutionAPIConfigurationError) as e:
            ControlledExecutionRouter(PAPER, SHADOW, bad)
        assert "INVALID_MICRO_LIVE_SERVICE" in str(e.value)


class TestResolution:
    def test_paper_routes_to_paper_service(self):
        assert make_router().resolve(
            ControlledExecutionMode.PAPER) is PAPER

    def test_shadow_routes_to_shadow_service(self):
        assert make_router().resolve(
            ControlledExecutionMode.SHADOW) is SHADOW

    def test_micro_live_routes_to_authorization(self):
        assert make_router().resolve(
            ControlledExecutionMode.MICRO_LIVE) is MICRO

    @pytest.mark.parametrize("bad", [None, "PAPER", 1, object()])
    def test_invalid_mode_rejected(self, bad):
        with pytest.raises(
                ControlledExecutionAPIRoutingError) as e:
            make_router().resolve(bad)
        assert str(e.value) == "API_ROUTING:INVALID_MODE"

    def test_all_modes_routed(self):
        router = make_router()
        assert set(router.routes.keys()) == set(
            ControlledExecutionMode)

    def test_resolution_deterministic(self):
        router = make_router()
        first = router.resolve(ControlledExecutionMode.PAPER)
        second = router.resolve(ControlledExecutionMode.PAPER)
        assert first is second


class TestImmutability:
    def test_router_attributes_frozen(self):
        router = make_router()
        with pytest.raises(
                ControlledExecutionAPIConfigurationError):
            router.new_attribute = "x"

    def test_routes_read_only(self):
        router = make_router()
        assert isinstance(router.routes, MappingProxyType)
        with pytest.raises(TypeError):
            router.routes[ControlledExecutionMode.PAPER] = None

    def test_route_table_cannot_be_replaced(self):
        router = make_router()
        with pytest.raises(
                ControlledExecutionAPIConfigurationError):
            router._routes = {}


class TestNoDirectBrokerRouting:
    def test_router_holds_no_broker(self):
        router = make_router()
        assert not hasattr(router, "broker")
        assert not hasattr(router, "_broker")

    def test_route_targets_are_mission_services_only(self):
        router = make_router()
        allowed = (PaperExecutionService, ShadowModeService,
                   MicroLiveAuthorizationService)
        for service in router.routes.values():
            assert isinstance(service, allowed)

    def test_error_hierarchy(self):
        assert issubclass(
            ControlledExecutionAPIConfigurationError,
            ControlledExecutionAPIError)
        assert issubclass(ControlledExecutionAPIRoutingError,
                          ControlledExecutionAPIError)
