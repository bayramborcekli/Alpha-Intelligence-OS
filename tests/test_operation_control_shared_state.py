"""Görev: kill-switch/idempotency durumunun worker'lar arası tutarlılığı.

İki ayrı OperationControlService örneği (iki gunicorn worker'ı
simüle eder) aynı paylaşımlı durum deposunu kullanır:

- Aynı idempotency anahtarı ikinci worker'da ASLA yeni eylem
  olarak kabul edilmez (REPLAYED / CONFLICT).
- Otomasyon, sembol, stop-new-entries ve denetim durumu tüm
  worker'larda aynı görünür.
- Bozuk anlık görüntü fail-closed steril hata üretir; durum
  sessizce sıfırlanmaz.
- Gerçek çok-süreçli yarışta anahtar yalnız bir kez APPLIED olur.
"""

from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controlled_execution_api import ControlledExecutionAPI  # noqa: E402
from controlled_execution_foundation import (  # noqa: E402
    ControlledExecutionFoundation)
from controlled_execution_policy import ExtensionRegistry  # noqa: E402
from controlled_execution_router import (  # noqa: E402
    ControlledExecutionRouter)
from execution_risk_models import (  # noqa: E402
    RiskDecision, RiskDecisionType)
from micro_live_authorization import (  # noqa: E402
    MicroLiveAuthorizationService)
from operation_control_models import (  # noqa: E402
    AutomationCommand, AutomationState, IdempotencyStatus,
    OperationActionStatus, SymbolAutomationState, SymbolCommand)
from operation_control_service import (  # noqa: E402
    CONFIRMATION_PHRASE, OperationControlService)
from operation_control_store import (  # noqa: E402
    OperationControlStateError, OperationControlStateStore)
from paper_broker import PaperBroker  # noqa: E402
from paper_execution_service import (  # noqa: E402
    PaperExecutionService, StaticRiskEvaluator)
from shadow_mode import ShadowModeService  # noqa: E402


def make_service(state_path: Path) -> OperationControlService:
    foundation = ControlledExecutionFoundation(ExtensionRegistry())
    broker = PaperBroker(known_symbols=("BTCUSDT",))
    risk = StaticRiskEvaluator(
        RiskDecision(decision=RiskDecisionType.ALLOW))
    api = ControlledExecutionAPI(ControlledExecutionRouter(
        PaperExecutionService(broker=broker, foundation=foundation,
                              risk_evaluator=risk),
        ShadowModeService(broker=broker, foundation=foundation,
                          risk_evaluator=risk),
        MicroLiveAuthorizationService(foundation=foundation)))
    return OperationControlService(
        api, clock=lambda: 1,
        state_store=OperationControlStateStore(state_path))


@pytest.fixture()
def state_path(tmp_path):
    return tmp_path / "operation_control_state.json"


@pytest.fixture()
def worker_a(state_path):
    return make_service(state_path)


@pytest.fixture()
def worker_b(state_path):
    return make_service(state_path)


class TestCrossWorkerIdempotency:
    def test_same_key_replayed_not_reaccepted(self, worker_a,
                                              worker_b):
        first = worker_a.execute_automation_command(
            AutomationCommand.START, "op", idempotency_key="k1")
        assert first.status is OperationActionStatus.COMPLETED
        assert first.lifecycle_status == "APPLIED"
        second = worker_b.execute_automation_command(
            AutomationCommand.START, "op", idempotency_key="k1")
        assert second.idempotency_status is \
            IdempotencyStatus.REPLAYED
        # İkinci worker YENİ yan etki üretmedi; durum aynı kaldı.
        assert worker_b.automation_state is \
            AutomationState.RUNNING

    def test_same_key_different_signature_conflict(
            self, worker_a, worker_b):
        worker_a.execute_automation_command(
            AutomationCommand.START, "op", idempotency_key="k1")
        result = worker_b.execute_automation_command(
            AutomationCommand.STOP, "op", idempotency_key="k1")
        assert result.status is OperationActionStatus.DENIED
        assert result.error_code == "IDEMPOTENCY_CONFLICT"

    def test_stop_new_entries_key_never_reaccepted(
            self, worker_a, worker_b):
        first = worker_a.stop_new_entries_action(
            "op", "acil", CONFIRMATION_PHRASE, "g1")
        assert first.status is OperationActionStatus.COMPLETED
        second = worker_b.stop_new_entries_action(
            "op", "acil", CONFIRMATION_PHRASE, "g1")
        assert second.idempotency_status is \
            IdempotencyStatus.REPLAYED
        assert worker_b.stop_new_entries is True


class TestCrossWorkerStateVisibility:
    def test_automation_state_shared(self, worker_a, worker_b):
        worker_a.execute_automation_command(
            AutomationCommand.START, "op")
        assert worker_b.automation_state is \
            AutomationState.RUNNING

    def test_symbol_state_shared(self, worker_a, worker_b):
        worker_a.execute_symbol_command(
            "BTCUSDT", SymbolCommand.ENABLE, "op")
        assert worker_b.symbol_state("BTCUSDT") is \
            SymbolAutomationState.ENABLED
        assert worker_b.symbol_states() == {
            "BTCUSDT": SymbolAutomationState.ENABLED}

    def test_kill_switch_block_shared(self, worker_a, worker_b):
        worker_a.execute_automation_command(
            AutomationCommand.START, "op")
        worker_a.record_kill_switch(
            "op", True, "acil durum", CONFIRMATION_PHRASE, "ks1")
        assert worker_b.automation_state is \
            AutomationState.BLOCKED
        # BLOCKED yalnız STOP kabul eder — worker B'de de.
        denied = worker_b.execute_automation_command(
            AutomationCommand.START, "op")
        assert denied.error_code == "INVALID_TRANSITION"

    def test_audit_trail_shared(self, worker_a, worker_b):
        worker_a.execute_automation_command(
            AutomationCommand.START, "op", idempotency_key="k1")
        records = worker_b.audit.records()
        assert len(records) == 1
        assert records[0].action == "AUTOMATION:START"
        assert records[0].idempotency_key == "k1"

    def test_last_error_code_shared(self, worker_a, worker_b):
        worker_a.execute_automation_command(
            AutomationCommand.PAUSE, "op")  # STOPPED→PAUSE geçersiz
        assert worker_b.last_error_code == "INVALID_TRANSITION"

    def test_sequence_not_reused_across_workers(
            self, worker_a, worker_b):
        r1 = worker_a.execute_automation_command(
            AutomationCommand.START, "op")
        r2 = worker_b.execute_automation_command(
            AutomationCommand.STOP, "op")
        assert r1.action_id != r2.action_id
        assert r1.correlation_id != r2.correlation_id


class TestFailClosedStore:
    def test_corrupt_snapshot_sterile_error(self, state_path,
                                            worker_a):
        worker_a.execute_automation_command(
            AutomationCommand.START, "op", idempotency_key="k1")
        state_path.write_text("{bozuk", encoding="utf-8")
        with pytest.raises(OperationControlStateError) as exc:
            worker_a.execute_automation_command(
                AutomationCommand.STOP, "op",
                idempotency_key="k2")
        assert "STATE_STORE_CORRUPT" in str(exc.value)

    def test_wrong_schema_version_rejected(self, state_path,
                                           worker_a):
        state_path.write_text(json.dumps(
            {"schema_version": 999}), encoding="utf-8")
        with pytest.raises(OperationControlStateError):
            worker_a.automation_state  # noqa: B018

    def test_missing_file_clean_defaults(self, worker_a):
        assert worker_a.automation_state is \
            AutomationState.STOPPED
        assert worker_a.stop_new_entries is False


def _race_worker(state_path_str: str, key: str, queue) -> None:
    service = make_service(Path(state_path_str))
    result = service.execute_automation_command(
        AutomationCommand.START, "op", idempotency_key=key)
    queue.put((result.lifecycle_status,
               result.idempotency_status.value))


class TestMultiProcessRace:
    def test_key_applied_exactly_once_across_processes(
            self, state_path):
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()
        procs = [ctx.Process(target=_race_worker,
                             args=(str(state_path), "race-key",
                                   queue))
                 for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(30)
        outcomes = [queue.get(timeout=10) for _ in procs]
        # Anahtar yalnız BİR süreçte NEW kabul edilir; kalanlar
        # saklı sonucu REPLAYED olarak alır (yeni yan etki YOK).
        fresh = [o for o in outcomes if o[1] == "NEW"]
        replayed = [o for o in outcomes if o[1] == "REPLAYED"]
        assert len(fresh) == 1
        assert len(replayed) == len(outcomes) - 1
