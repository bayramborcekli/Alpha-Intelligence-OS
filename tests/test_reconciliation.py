"""Mission 2100 — Agent 07: Mutabakat servisi testleri.

Kapsam: eşleşme, tüm uyuşmazlık kodları, eksik/mükerrer tespiti,
null-alan kuralları, boş kaynak atlaması, denetim izi, saflık
(girdi mutasyonu YOK), determinizm, sözleşme doğrulaması ve model
değişmezliği.
"""

import sys
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution_enums import OrderSide  # noqa: E402
from lifecycle_models import (OrderLifecycleState,  # noqa: E402
                              OrderSnapshot, ReconciliationAudit,
                              ReconciliationDecision,
                              ReconciliationMismatch,
                              ReconciliationMismatchCode,
                              ReconciliationReport,
                              ReconciliationReportDecision,
                              ReconciliationResult,
                              ReconciliationSource,
                              ReconciliationStatistics)
from reconciliation import (SOURCE_CHAIN,  # noqa: E402
                            ReconciliationService)
from reconciliation_errors import (  # noqa: E402
    ReconciliationContractError, ReconciliationError,
    ReconciliationInputError)

SRC = ReconciliationSource
CODE = ReconciliationMismatchCode
STATE = OrderLifecycleState

SERVICE = ReconciliationService()

RESULT_SOURCES = (SRC.PAPER, SRC.SHADOW, SRC.MICRO_LIVE)


def snap(source, reference="ORD-1", symbol="BTCUSDT",
         side=OrderSide.BUY, quantity=Decimal("2"),
         status=STATE.FILLED, price=Decimal("100"),
         pnl=Decimal("5"), timestamp=None, sequence=None):
    position = list(SOURCE_CHAIN).index(source)
    if timestamp is None:
        timestamp = 100 + position
    if sequence is None:
        sequence = 10 + position
    if source is SRC.EXECUTION_REQUEST:
        status = STATE.NEW
        pnl = None
    return OrderSnapshot(
        source=source, order_reference=reference, symbol=symbol,
        side=side, quantity=quantity, status=status, price=price,
        pnl=pnl, timestamp=timestamp, logical_sequence=sequence)


def reconcile(request=None, paper=None, shadow=None,
              micro=None, reference="REP-1", sequence=99):
    def default(value, source):
        if value is None:
            return (snap(source),)
        return value
    return SERVICE.reconcile(
        default(request, SRC.EXECUTION_REQUEST),
        default(paper, SRC.PAPER),
        default(shadow, SRC.SHADOW),
        default(micro, SRC.MICRO_LIVE),
        reference, sequence)


class TestMatchedChain:
    def test_full_chain_reconciled(self):
        report = reconcile()
        assert report.decision is \
            ReconciliationReportDecision.RECONCILED
        assert report.results[0].decision is \
            ReconciliationDecision.MATCHED
        assert report.results[0].mismatches == ()

    def test_statistics_on_match(self):
        report = reconcile()
        stats = report.statistics
        assert stats.total_orders == 1
        assert stats.matched_orders == 1
        assert stats.mismatched_orders == 0
        assert stats.missing_orders == 0
        assert stats.total_mismatches == 0

    def test_multiple_matched_orders(self):
        request = (snap(SRC.EXECUTION_REQUEST, "A"),
                   snap(SRC.EXECUTION_REQUEST, "B"))
        paper = (snap(SRC.PAPER, "A"), snap(SRC.PAPER, "B"))
        shadow = (snap(SRC.SHADOW, "A"), snap(SRC.SHADOW, "B"))
        micro = (snap(SRC.MICRO_LIVE, "A"),
                 snap(SRC.MICRO_LIVE, "B"))
        report = reconcile(request, paper, shadow, micro)
        assert report.statistics.matched_orders == 2
        assert report.decision is \
            ReconciliationReportDecision.RECONCILED

    def test_report_carries_reference_and_sequence(self):
        report = reconcile(reference="REP-42", sequence=7)
        assert report.report_reference == "REP-42"
        assert report.logical_sequence == 7

    @pytest.mark.parametrize("source", RESULT_SOURCES)
    def test_empty_result_source_skipped(self, source):
        kwargs = {"paper": None, "shadow": None, "micro": None}
        key = {SRC.PAPER: "paper", SRC.SHADOW: "shadow",
               SRC.MICRO_LIVE: "micro"}[source]
        kwargs[key] = ()
        report = reconcile(**kwargs)
        assert report.decision is \
            ReconciliationReportDecision.RECONCILED

    def test_future_micro_live_absent_is_reconciled(self):
        report = reconcile(micro=())
        assert report.decision is \
            ReconciliationReportDecision.RECONCILED

    def test_order_of_results_follows_request_order(self):
        request = (snap(SRC.EXECUTION_REQUEST, "B"),
                   snap(SRC.EXECUTION_REQUEST, "A"))
        paper = (snap(SRC.PAPER, "B"), snap(SRC.PAPER, "A"))
        report = reconcile(request, paper, (), ())
        refs = [r.order_reference for r in report.results]
        assert refs == ["B", "A"]


class TestMissingOrder:
    @pytest.mark.parametrize("source", RESULT_SOURCES)
    def test_missing_in_result_source(self, source):
        kwargs = {"paper": None, "shadow": None, "micro": None}
        key = {SRC.PAPER: "paper", SRC.SHADOW: "shadow",
               SRC.MICRO_LIVE: "micro"}[source]
        kwargs[key] = (snap(source, "OTHER"),)
        report = reconcile(**kwargs)
        result = next(r for r in report.results
                      if r.order_reference == "ORD-1")
        assert result.decision is ReconciliationDecision.MISSING
        codes = [m.mismatch_code for m in result.mismatches]
        assert CODE.MISSING_ORDER in codes
        mismatch = next(m for m in result.mismatches
                        if m.mismatch_code is CODE.MISSING_ORDER)
        assert mismatch.source_a is SRC.EXECUTION_REQUEST
        assert mismatch.source_b is source

    def test_unknown_order_in_downstream(self):
        paper = (snap(SRC.PAPER), snap(SRC.PAPER, "GHOST"))
        report = reconcile(paper=paper, shadow=(), micro=())
        ghost = next(r for r in report.results
                     if r.order_reference == "GHOST")
        assert ghost.decision is ReconciliationDecision.MISSING
        mismatch = ghost.mismatches[0]
        assert mismatch.mismatch_code is CODE.MISSING_ORDER
        assert mismatch.source_a is SRC.PAPER
        assert mismatch.source_b is SRC.EXECUTION_REQUEST

    def test_empty_request_baseline_still_flags_missing(self):
        # İstek kümesi BOŞ olsa bile taban çizgisi istektir:
        # sonuç kaynağındaki her emir MISSING_ORDER üretir.
        report = reconcile(request=(), paper=(snap(SRC.PAPER),),
                           shadow=(), micro=())
        assert report.decision is \
            ReconciliationReportDecision.DISCREPANT
        result = report.results[0]
        assert result.decision is ReconciliationDecision.MISSING
        mismatch = result.mismatches[0]
        assert mismatch.mismatch_code is CODE.MISSING_ORDER
        assert mismatch.source_a is SRC.PAPER
        assert mismatch.source_b is SRC.EXECUTION_REQUEST

    def test_empty_request_multiple_sources_flag_each(self):
        report = reconcile(request=(), paper=(snap(SRC.PAPER),),
                           shadow=(snap(SRC.SHADOW),), micro=())
        result = report.results[0]
        codes = [m.mismatch_code for m in result.mismatches]
        assert codes.count(CODE.MISSING_ORDER) == 2

    def test_missing_counts_in_statistics(self):
        report = reconcile(paper=(snap(SRC.PAPER, "OTHER"),),
                           shadow=(), micro=())
        assert report.statistics.missing_orders == 2
        assert report.decision is \
            ReconciliationReportDecision.DISCREPANT


class TestDuplicates:
    def test_duplicate_order_in_request(self):
        request = (snap(SRC.EXECUTION_REQUEST),
                   snap(SRC.EXECUTION_REQUEST))
        report = reconcile(request=request)
        result = report.results[0]
        codes = [m.mismatch_code for m in result.mismatches]
        assert CODE.DUPLICATE_ORDER in codes
        assert result.decision is \
            ReconciliationDecision.MISMATCHED

    @pytest.mark.parametrize("source", RESULT_SOURCES)
    def test_duplicate_execution_in_result_source(self, source):
        kwargs = {"paper": None, "shadow": None, "micro": None}
        key = {SRC.PAPER: "paper", SRC.SHADOW: "shadow",
               SRC.MICRO_LIVE: "micro"}[source]
        kwargs[key] = (snap(source), snap(source))
        report = reconcile(**kwargs)
        result = report.results[0]
        codes = [m.mismatch_code for m in result.mismatches]
        assert CODE.DUPLICATE_EXECUTION in codes

    def test_duplicate_mismatch_sources_are_same(self):
        report = reconcile(paper=(snap(SRC.PAPER),
                                  snap(SRC.PAPER)))
        mismatch = next(
            m for m in report.results[0].mismatches
            if m.mismatch_code is CODE.DUPLICATE_EXECUTION)
        assert mismatch.source_a is SRC.PAPER
        assert mismatch.source_b is SRC.PAPER


class TestFieldMismatches:
    @pytest.mark.parametrize("source", RESULT_SOURCES)
    def test_quantity_mismatch(self, source):
        kwargs = {"paper": None, "shadow": None, "micro": None}
        key = {SRC.PAPER: "paper", SRC.SHADOW: "shadow",
               SRC.MICRO_LIVE: "micro"}[source]
        kwargs[key] = (snap(source, quantity=Decimal("3")),)
        report = reconcile(**kwargs)
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.QUANTITY_MISMATCH in codes

    def test_symbol_divergence_flagged(self):
        paper = (snap(SRC.PAPER, symbol="ETHUSDT"),)
        report = reconcile(paper=paper, shadow=(), micro=())
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.QUANTITY_MISMATCH in codes

    def test_side_divergence_flagged(self):
        paper = (snap(SRC.PAPER, side=OrderSide.SELL),)
        report = reconcile(paper=paper, shadow=(), micro=())
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.QUANTITY_MISMATCH in codes

    @pytest.mark.parametrize("source", RESULT_SOURCES)
    def test_price_mismatch(self, source):
        kwargs = {"paper": None, "shadow": None, "micro": None}
        key = {SRC.PAPER: "paper", SRC.SHADOW: "shadow",
               SRC.MICRO_LIVE: "micro"}[source]
        kwargs[key] = (snap(source, price=Decimal("101")),)
        report = reconcile(**kwargs)
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.PRICE_MISMATCH in codes

    def test_null_price_never_compared(self):
        request = (snap(SRC.EXECUTION_REQUEST, price=None),)
        paper = (snap(SRC.PAPER, price=Decimal("999")),)
        report = reconcile(request, paper, (), ())
        assert report.decision is \
            ReconciliationReportDecision.RECONCILED

    def test_null_downstream_price_never_compared(self):
        paper = (snap(SRC.PAPER, price=None),)
        report = reconcile(paper=paper, shadow=(), micro=())
        assert report.decision is \
            ReconciliationReportDecision.RECONCILED

    def test_status_mismatch_between_result_sources(self):
        paper = (snap(SRC.PAPER, status=STATE.FILLED),)
        shadow = (snap(SRC.SHADOW, status=STATE.CANCELLED),)
        report = reconcile(paper=paper, shadow=shadow, micro=())
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.STATUS_MISMATCH in codes

    def test_request_status_not_compared_to_results(self):
        # İstek NEW, sonuçlar FILLED — durum uyuşmazlığı DEĞİL.
        report = reconcile()
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.STATUS_MISMATCH not in codes

    def test_pnl_mismatch(self):
        paper = (snap(SRC.PAPER, pnl=Decimal("5")),)
        shadow = (snap(SRC.SHADOW, pnl=Decimal("6")),)
        report = reconcile(paper=paper, shadow=shadow, micro=())
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.PNL_MISMATCH in codes

    def test_null_pnl_never_compared(self):
        paper = (snap(SRC.PAPER, pnl=None),)
        shadow = (snap(SRC.SHADOW, pnl=Decimal("6")),)
        report = reconcile(paper=paper, shadow=shadow, micro=())
        assert report.decision is \
            ReconciliationReportDecision.RECONCILED

    def test_negative_pnl_allowed_and_compared(self):
        paper = (snap(SRC.PAPER, pnl=Decimal("-2")),)
        shadow = (snap(SRC.SHADOW, pnl=Decimal("-2")),)
        report = reconcile(paper=paper, shadow=shadow, micro=())
        assert report.decision is \
            ReconciliationReportDecision.RECONCILED

    def test_multiple_mismatches_all_recorded(self):
        paper = (snap(SRC.PAPER, quantity=Decimal("9"),
                      price=Decimal("1")),)
        report = reconcile(paper=paper, shadow=(), micro=())
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.QUANTITY_MISMATCH in codes
        assert CODE.PRICE_MISMATCH in codes


class TestSequenceViolations:
    def test_timestamp_violation(self):
        paper = (snap(SRC.PAPER, timestamp=50),)
        report = reconcile(paper=paper, shadow=(), micro=())
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.TIMESTAMP_SEQUENCE_VIOLATION in codes

    def test_timestamp_violation_between_results(self):
        paper = (snap(SRC.PAPER, timestamp=200),)
        shadow = (snap(SRC.SHADOW, timestamp=150),)
        report = reconcile(paper=paper, shadow=shadow, micro=())
        mismatch = next(
            m for m in report.results[0].mismatches
            if m.mismatch_code is
            CODE.TIMESTAMP_SEQUENCE_VIOLATION)
        assert mismatch.source_a is SRC.PAPER
        assert mismatch.source_b is SRC.SHADOW

    def test_equal_timestamps_allowed(self):
        paper = (snap(SRC.PAPER, timestamp=100),)
        report = reconcile(paper=paper, shadow=(), micro=())
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.TIMESTAMP_SEQUENCE_VIOLATION not in codes

    def test_logical_sequence_violation(self):
        paper = (snap(SRC.PAPER, sequence=1),)
        report = reconcile(paper=paper, shadow=(), micro=())
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.LOGICAL_SEQUENCE_VIOLATION in codes

    def test_equal_logical_sequences_allowed(self):
        paper = (snap(SRC.PAPER, sequence=10),)
        report = reconcile(paper=paper, shadow=(), micro=())
        codes = [m.mismatch_code
                 for m in report.results[0].mismatches]
        assert CODE.LOGICAL_SEQUENCE_VIOLATION not in codes


class TestAuditTrail:
    def test_audit_starts_and_completes(self):
        report = reconcile()
        assert report.audit[0].audit_code == \
            "RECONCILIATION_STARTED"
        assert report.audit[-1].audit_code == \
            "RECONCILIATION_COMPLETED"

    def test_every_mismatch_recorded_in_audit(self):
        paper = (snap(SRC.PAPER, quantity=Decimal("9")),)
        report = reconcile(paper=paper, shadow=(), micro=())
        mismatch_audits = [a for a in report.audit
                           if a.audit_code.startswith(
                               "MISMATCH:")]
        assert len(mismatch_audits) == \
            report.statistics.total_mismatches
        assert mismatch_audits[0].audit_code == \
            "MISMATCH:QUANTITY_MISMATCH"
        assert mismatch_audits[0].order_reference == "ORD-1"

    def test_no_mismatch_audits_when_matched(self):
        report = reconcile()
        mismatch_audits = [a for a in report.audit
                           if a.audit_code.startswith(
                               "MISMATCH:")]
        assert mismatch_audits == []


class TestPurityAndDeterminism:
    def test_inputs_not_mutated(self):
        request = (snap(SRC.EXECUTION_REQUEST),)
        paper = (snap(SRC.PAPER),)
        before_request = tuple(request)
        before_paper = tuple(paper)
        SERVICE.reconcile(request, paper, (), (), "REP-1", 1)
        assert request == before_request
        assert paper == before_paper

    def test_deterministic_repeat(self):
        first = reconcile(paper=(snap(
            SRC.PAPER, quantity=Decimal("9")),))
        second = reconcile(paper=(snap(
            SRC.PAPER, quantity=Decimal("9")),))
        assert first == second

    def test_report_is_frozen(self):
        report = reconcile()
        with pytest.raises(FrozenInstanceError):
            report.decision = \
                ReconciliationReportDecision.RECONCILED


class TestContractValidation:
    @pytest.mark.parametrize("argument", ["request", "paper",
                                          "shadow", "micro"])
    def test_wrong_source_rejected(self, argument):
        wrong = {"request": SRC.PAPER,
                 "paper": SRC.EXECUTION_REQUEST,
                 "shadow": SRC.MICRO_LIVE,
                 "micro": SRC.SHADOW}[argument]
        kwargs = {"request": None, "paper": None,
                  "shadow": None, "micro": None,
                  argument: (snap(wrong),)}
        with pytest.raises(ReconciliationInputError) as info:
            reconcile(**kwargs)
        assert "SOURCE_MISMATCH" in str(info.value)

    @pytest.mark.parametrize("argument,field", [
        ("request", "request_snapshots"),
        ("paper", "paper_snapshots"),
        ("shadow", "shadow_snapshots"),
        ("micro", "micro_live_snapshots")])
    def test_non_tuple_rejected(self, argument, field):
        kwargs = {"request": None, "paper": None,
                  "shadow": None, "micro": None,
                  argument: [snap(SRC.PAPER)]}
        with pytest.raises(ReconciliationContractError) as info:
            reconcile(**kwargs)
        assert str(info.value) == \
            f"INVALID_RECONCILIATION_FIELD:{field}"

    def test_non_snapshot_element_rejected(self):
        with pytest.raises(ReconciliationContractError):
            reconcile(request=("not-a-snapshot",))

    @pytest.mark.parametrize("reference", [None, "", "  ", 5])
    def test_invalid_report_reference(self, reference):
        with pytest.raises(ReconciliationContractError):
            reconcile(reference=reference)

    @pytest.mark.parametrize("sequence",
                             [None, "1", 1.5, True, -1])
    def test_invalid_sequence(self, sequence):
        with pytest.raises(ReconciliationContractError):
            reconcile(sequence=sequence)


def make_mismatch(**overrides):
    values = dict(mismatch_code=CODE.QUANTITY_MISMATCH,
                  order_reference="ORD-1",
                  source_a=SRC.EXECUTION_REQUEST,
                  source_b=SRC.PAPER, logical_sequence=1)
    values.update(overrides)
    return ReconciliationMismatch(**values)


def make_result(**overrides):
    values = dict(order_reference="ORD-1",
                  decision=ReconciliationDecision.MATCHED,
                  mismatches=(), logical_sequence=1)
    values.update(overrides)
    return ReconciliationResult(**values)


def make_statistics(**overrides):
    values = dict(total_orders=1, matched_orders=1,
                  mismatched_orders=0, missing_orders=0,
                  total_mismatches=0)
    values.update(overrides)
    return ReconciliationStatistics(**values)


class TestModelContracts:
    @pytest.mark.parametrize("field,value", [
        ("source", "PAPER"), ("order_reference", ""),
        ("symbol", None), ("side", "BUY"), ("quantity", 1.0),
        ("quantity", Decimal("0")), ("status", "FILLED"),
        ("price", Decimal("-1")), ("pnl", 5.0),
        ("pnl", Decimal("NaN")), ("timestamp", -1),
        ("timestamp", True), ("logical_sequence", "1")])
    def test_snapshot_contract(self, field, value):
        values = dict(source=SRC.PAPER, order_reference="ORD-1",
                      symbol="BTCUSDT", side=OrderSide.BUY,
                      quantity=Decimal("1"),
                      status=STATE.FILLED)
        values[field] = value
        with pytest.raises(ReconciliationContractError) as info:
            OrderSnapshot(**values)
        assert str(info.value) == \
            f"INVALID_RECONCILIATION_FIELD:{field}"

    def test_snapshot_pnl_may_be_negative(self):
        snapshot = snap(SRC.PAPER, pnl=Decimal("-3.5"))
        assert snapshot.pnl == Decimal("-3.5")

    @pytest.mark.parametrize("field,value", [
        ("mismatch_code", "MISSING_ORDER"),
        ("order_reference", ""), ("source_a", "PAPER"),
        ("source_b", None), ("logical_sequence", -1)])
    def test_mismatch_contract(self, field, value):
        with pytest.raises(ReconciliationContractError):
            make_mismatch(**{field: value})

    def test_matched_result_rejects_mismatches(self):
        with pytest.raises(ReconciliationContractError):
            make_result(mismatches=(make_mismatch(),))

    def test_mismatched_result_requires_mismatches(self):
        with pytest.raises(ReconciliationContractError):
            make_result(
                decision=ReconciliationDecision.MISMATCHED)

    def test_missing_result_requires_mismatches(self):
        with pytest.raises(ReconciliationContractError):
            make_result(decision=ReconciliationDecision.MISSING)

    def test_statistics_totals_must_balance(self):
        with pytest.raises(ReconciliationContractError):
            make_statistics(total_orders=5)

    @pytest.mark.parametrize("field", [
        "total_orders", "matched_orders", "mismatched_orders",
        "missing_orders", "total_mismatches"])
    def test_statistics_reject_negative(self, field):
        with pytest.raises(ReconciliationContractError):
            make_statistics(**{field: -1})

    @pytest.mark.parametrize("field,value", [
        ("audit_code", ""), ("logical_sequence", -1),
        ("order_reference", "")])
    def test_audit_contract(self, field, value):
        values = dict(audit_code="RECONCILIATION_STARTED",
                      logical_sequence=1)
        values[field] = value
        with pytest.raises(ReconciliationContractError):
            ReconciliationAudit(**values)

    def test_report_requires_statistics(self):
        with pytest.raises(ReconciliationContractError):
            ReconciliationReport(
                report_reference="REP-1",
                decision=(
                    ReconciliationReportDecision.RECONCILED),
                results=(), statistics=None)

    def test_error_hierarchy_closed(self):
        assert issubclass(ReconciliationContractError,
                          ReconciliationError)
        assert issubclass(ReconciliationInputError,
                          ReconciliationError)


IMMUTABLE_CASES = [
    (lambda: snap(SRC.PAPER), "quantity"),
    (lambda: snap(SRC.PAPER), "status"),
    (lambda: snap(SRC.PAPER), "pnl"),
    (lambda: snap(SRC.PAPER), "source"),
    (make_mismatch, "mismatch_code"),
    (make_mismatch, "source_a"),
    (make_result, "decision"),
    (make_result, "mismatches"),
    (make_statistics, "total_orders"),
    (make_statistics, "total_mismatches")]


class TestImmutability:
    @pytest.mark.parametrize("factory,field", IMMUTABLE_CASES)
    def test_models_frozen(self, factory, field):
        instance = factory()
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field, "mutated")
