"""Mission 2100 — Agent 09: Soak (dayanım) testleri.

Mantıksal 1/6/12/24 saat profilleri GERÇEK Controlled Execution
API'si üzerinden koşulur: determinizm, anlık görüntü bütünlüğü,
bellek kararlılığı (tracemalloc), nesne sızıntısı (gc) ve durum
bozulmaması doğrulanır. Duvar saati yoktur — profiller sabit
çevrim sayılarına eşlenir.
"""

import gc
import sys
import tracemalloc
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controlled_execution_api import (  # noqa: E402
    ControlledExecutionAPI)
from controlled_execution_api_models import (  # noqa: E402
    ControlledExecutionOperation, ControlledExecutionRequest,
    ControlledExecutionState)
from controlled_execution_foundation import (  # noqa: E402
    ControlledExecutionFoundation)
from controlled_execution_models import (  # noqa: E402
    ControlledExecutionMode, ControlledExecutionPolicy)
from controlled_execution_policy import (  # noqa: E402
    ExtensionRegistry)
from controlled_execution_router import (  # noqa: E402
    ControlledExecutionRouter)
from execution_enums import (OrderSide, OrderType,  # noqa: E402
                             TimeInForce)
from execution_kill_switch_models import (  # noqa: E402
    KillSwitchReason, KillSwitchSnapshot, KillSwitchState)
from execution_models import ExecutionRequest  # noqa: E402
from execution_risk_models import (RiskDecision,  # noqa: E402
                                   RiskDecisionType)
from micro_live_authorization import (  # noqa: E402
    MicroLiveAuthorizationService)
from paper_broker import PaperBroker  # noqa: E402
from paper_execution_models import (  # noqa: E402
    PaperExecutionReferences)
from paper_execution_service import (  # noqa: E402
    PaperExecutionService, StaticRiskEvaluator)
from paper_models import PaperLedgerSnapshot  # noqa: E402
from shadow_models import (ShadowMarketObservation,  # noqa: E402
                           ShadowSnapshot)
from shadow_mode import ShadowModeService  # noqa: E402
from soak_runner import (CYCLES_PER_LOGICAL_HOUR,  # noqa: E402
                         SOAK_PROFILES, SoakContractError,
                         SoakProfile, SoakReport,
                         profile_by_name, run_soak)

MODE = ControlledExecutionMode
OP = ControlledExecutionOperation

BROKER = PaperBroker(known_symbols=("BTCUSDT",))
FOUNDATION = ControlledExecutionFoundation(ExtensionRegistry())
ALLOW = StaticRiskEvaluator(RiskDecision(
    decision=RiskDecisionType.ALLOW))
API = ControlledExecutionAPI(ControlledExecutionRouter(
    PaperExecutionService(broker=BROKER, foundation=FOUNDATION,
                          risk_evaluator=ALLOW),
    ShadowModeService(broker=BROKER, foundation=FOUNDATION,
                      risk_evaluator=ALLOW),
    MicroLiveAuthorizationService(foundation=FOUNDATION)))

KS = KillSwitchSnapshot(state=KillSwitchState.ENABLED,
                        reason=KillSwitchReason.MANUAL,
                        timestamp=1, sequence_id=1)
PAPER_POLICY = ControlledExecutionPolicy(
    mode=MODE.PAPER, simulated_fill_allowed=True)
SHADOW_POLICY = ControlledExecutionPolicy(
    mode=MODE.SHADOW, broker_read_allowed=True)

LEDGER = PaperLedgerSnapshot(
    quote_asset="USDT", initial_cash=D("1000"),
    cash=D("1000"), reserved_cash=D("0"),
    realized_pnl=D("0"), commission_paid=D("0"))
REFERENCES = PaperExecutionReferences(
    request_reference="soak-req",
    previous_ledger_reference="ledger-0",
    current_ledger_reference="ledger-1", logical_sequence=3)
EXECUTION = ExecutionRequest(
    symbol="BTCUSDT", side=OrderSide.BUY,
    order_type=OrderType.LIMIT, quantity=D("1"),
    time_in_force=TimeInForce.GTC, price=D("100"))
OBSERVATION = ShadowMarketObservation(
    observation_reference="obs-1", symbol="BTCUSDT",
    best_bid=D("99"), best_ask=D("101"),
    last_trade_price=D("100"), logical_sequence=2)

PAPER_STATE = ControlledExecutionState(
    ledger=LEDGER, paper_references=REFERENCES)
SHADOW_STATE = ControlledExecutionState(
    ledger=LEDGER, paper_references=REFERENCES,
    shadow=ShadowSnapshot(snapshot_reference="sh-0"))


def _paper_submit():
    return API.submit(ControlledExecutionRequest(
        mode=MODE.PAPER, operation=OP.SUBMIT,
        request_reference="soak-req", logical_sequence=3,
        policy=PAPER_POLICY, kill_switch=KS,
        execution=EXECUTION, order_reference="ord-1"),
        PAPER_STATE)


def _shadow_submit():
    return API.submit(ControlledExecutionRequest(
        mode=MODE.SHADOW, operation=OP.SUBMIT,
        request_reference="soak-req", logical_sequence=3,
        policy=SHADOW_POLICY, kill_switch=KS,
        execution=EXECUTION, order_reference="ord-1",
        observation=OBSERVATION), SHADOW_STATE)


def _paper_reads():
    responses = []
    for operation, method in (
            (OP.POSITIONS, API.positions),
            (OP.ORDERS, API.orders),
            (OP.EXECUTIONS, API.executions),
            (OP.STATISTICS, API.statistics),
            (OP.STATUS, API.status),
            (OP.HEARTBEAT, API.heartbeat)):
        responses.append(method(ControlledExecutionRequest(
            mode=MODE.PAPER, operation=operation,
            request_reference="soak-read",
            logical_sequence=1), PAPER_STATE))
    return tuple(responses)


def _ledger_probe():
    return (LEDGER.cash, LEDGER.reserved_cash,
            LEDGER.realized_pnl, LEDGER.orders,
            LEDGER.executions, LEDGER.sequence)


LEDGER_BASELINE = _ledger_probe()

SCENARIOS = {
    "paper_submit": _paper_submit,
    "shadow_submit": _shadow_submit,
    "paper_reads": _paper_reads,
}


class TestProfiles:
    def test_four_profiles(self):
        assert tuple(p.logical_hours for p in SOAK_PROFILES) \
            == (1, 6, 12, 24)

    @pytest.mark.parametrize("profile", SOAK_PROFILES,
                             ids=lambda p: p.name)
    def test_cycle_mapping(self, profile):
        assert profile.cycles == profile.logical_hours * \
            CYCLES_PER_LOGICAL_HOUR

    @pytest.mark.parametrize("name", ["SOAK_1H", "SOAK_6H",
                                      "SOAK_12H", "SOAK_24H"])
    def test_profile_by_name(self, name):
        assert profile_by_name(name).name == name

    def test_unknown_profile_fail_closed(self):
        with pytest.raises(SoakContractError):
            profile_by_name("SOAK_48H")

    def test_profile_immutable(self):
        with pytest.raises(Exception):
            SOAK_PROFILES[0].cycles = 1

    @pytest.mark.parametrize("kwargs", [
        dict(name="", logical_hours=1, cycles=60),
        dict(name="X", logical_hours=0, cycles=0),
        dict(name="X", logical_hours=1, cycles=61),
        dict(name="X", logical_hours=True, cycles=60)])
    def test_profile_contract(self, kwargs):
        with pytest.raises(SoakContractError):
            SoakProfile(**kwargs)


class TestSoakRuns:
    @pytest.mark.parametrize("profile", SOAK_PROFILES,
                             ids=lambda p: p.name)
    @pytest.mark.parametrize("scenario",
                             sorted(SCENARIOS))
    def test_soak_passes(self, profile, scenario):
        report = run_soak(profile, SCENARIOS[scenario],
                          reference_state=LEDGER_BASELINE,
                          state_probe=_ledger_probe)
        assert report.passed
        assert report.deterministic
        assert report.state_intact
        assert report.divergence_count == 0
        assert report.cycles_executed == profile.cycles

    def test_24h_snapshot_not_corrupted(self):
        run_soak(profile_by_name("SOAK_24H"), _paper_submit,
                 reference_state=LEDGER_BASELINE,
                 state_probe=_ledger_probe)
        assert _ledger_probe() == LEDGER_BASELINE
        assert LEDGER.cash == D("1000")

    def test_divergence_detected(self):
        counter = []

        def unstable():
            counter.append(1)
            return len(counter)

        report = run_soak(profile_by_name("SOAK_1H"), unstable)
        assert not report.deterministic
        assert report.divergence_count == 59
        assert not report.passed

    def test_inplace_baseline_mutation_detected(self):
        """Aynı mutable nesneyi döndürüp yerinde mutasyon,
        eşitlik karşılaştırmasını KANDIRAMAZ (repr koruması)."""
        shared = [0]

        def sneaky():
            shared[0] = shared[0] + 1
            return shared

        report = run_soak(profile_by_name("SOAK_1H"), sneaky)
        assert not report.passed
        assert report.divergence_count > 0 or \
            not report.state_intact

    def test_state_corruption_detected(self):
        box = {"value": 0}

        def mutating():
            box["value"] = box["value"] + 1
            return "ok"

        report = run_soak(profile_by_name("SOAK_1H"), mutating,
                          reference_state=0,
                          state_probe=lambda: box["value"])
        assert not report.state_intact
        assert not report.passed


class TestResourceStability:
    def test_memory_stable_over_soak(self):
        _paper_submit()
        tracemalloc.start()
        run_soak(profile_by_name("SOAK_1H"), _paper_submit)
        gc.collect()
        first, _ = tracemalloc.get_traced_memory()
        run_soak(profile_by_name("SOAK_6H"), _paper_submit)
        gc.collect()
        second, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        # 6 kat çevrimde artık bellek büyümesi sınırlı olmalı
        assert second - first < 256 * 1024

    def test_no_object_leak(self):
        run_soak(profile_by_name("SOAK_1H"), _paper_submit)
        gc.collect()
        before = len(gc.get_objects())
        run_soak(profile_by_name("SOAK_6H"), _paper_submit)
        gc.collect()
        after = len(gc.get_objects())
        assert after - before < 500

    def test_no_thread_leak(self):
        import threading
        before = threading.active_count()
        run_soak(profile_by_name("SOAK_1H"), _paper_submit)
        assert threading.active_count() == before

    def test_no_uncollectable_garbage(self):
        run_soak(profile_by_name("SOAK_1H"), _shadow_submit)
        gc.collect()
        assert gc.garbage == []


class TestReportContract:
    def test_report_immutable(self):
        report = run_soak(profile_by_name("SOAK_1H"),
                          _paper_reads)
        with pytest.raises(Exception):
            report.deterministic = False

    @pytest.mark.parametrize("kwargs", [
        dict(profile_name="", logical_hours=1,
             cycles_executed=60, deterministic=True,
             divergence_count=0, state_intact=True),
        dict(profile_name="X", logical_hours=-1,
             cycles_executed=60, deterministic=True,
             divergence_count=0, state_intact=True),
        dict(profile_name="X", logical_hours=1,
             cycles_executed=60, deterministic="yes",
             divergence_count=0, state_intact=True)])
    def test_report_contract(self, kwargs):
        with pytest.raises(SoakContractError):
            SoakReport(**kwargs)

    @pytest.mark.parametrize("bad", [None, "profile", 1])
    def test_invalid_profile_rejected(self, bad):
        with pytest.raises(SoakContractError):
            run_soak(bad, _paper_reads)

    def test_non_callable_rejected(self):
        with pytest.raises(SoakContractError):
            run_soak(profile_by_name("SOAK_1H"), "operation")

    def test_probe_without_reference_rejected(self):
        with pytest.raises(SoakContractError):
            run_soak(profile_by_name("SOAK_1H"), _paper_reads,
                     state_probe=_ledger_probe)

    def test_soak_deterministic_reports(self):
        first = run_soak(profile_by_name("SOAK_1H"),
                         _paper_reads)
        second = run_soak(profile_by_name("SOAK_1H"),
                          _paper_reads)
        assert first == second
