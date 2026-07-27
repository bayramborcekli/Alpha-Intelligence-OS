"""Mission 2100 — Agent 07: Deterministik mutabakat servisi.

Yürütme İsteği → Paper Sonucu → Shadow Sonucu → Micro Live Sonucu
zincirini SAF olarak karşılaştırır ve nihai ReconciliationReport
üretir. Girdi anlık görüntülerine GERİ YAZMAZ, kaynak katmanları
DEĞİŞTİRMEZ, emir vermez, borsaya BAĞLANMAZ. Gizli mutasyon YOKTUR.

Tespit edilen ihlaller (kapalı küme): eksik emir, mükerrer emir,
mükerrer yürütme, miktar / fiyat / durum / PnL uyuşmazlığı, zaman
damgası sıra ihlali, mantıksal sıra ihlali. Her uyuşmazlık denetim
kaydına GEÇER; hiçbir uyuşmazlık sessizce yutulmaz.

Karşılaştırma kuralları (deterministik):
- Miktar/sembol/yön istekle her sağlanan kaynak arasında birebir.
- Fiyat yalnız iki taraf da fiyat taşıyorsa karşılaştırılır
  (bilinmeyen → null; null uydurulmaz).
- Durum ve PnL yalnız SONUÇ kaynakları arasında (zincir sırasıyla
  ardışık çiftler) karşılaştırılır — istek bir niyettir, sonucun
  durumunu/PnL'ini taşımaz.
- Zaman damgası ve mantıksal sıra zincir boyunca kesin artmayan
  OLMAYAN (non-decreasing) olmak zorundadır.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from lifecycle_models import (OrderSnapshot, ReconciliationAudit,
                              ReconciliationDecision,
                              ReconciliationMismatch,
                              ReconciliationMismatchCode,
                              ReconciliationReport,
                              ReconciliationReportDecision,
                              ReconciliationResult,
                              ReconciliationSource,
                              ReconciliationStatistics)
from reconciliation_errors import (ReconciliationContractError,
                                   ReconciliationInputError)

__all__ = ["ReconciliationService", "SOURCE_CHAIN"]

_C = ReconciliationMismatchCode
_SRC = ReconciliationSource

_ERROR_FIELD = "INVALID_RECONCILIATION_FIELD"
_ERROR_INPUT = "RECONCILIATION_INPUT"

# Sabit kaynak zinciri (karşılaştırma sırası).
SOURCE_CHAIN = (_SRC.EXECUTION_REQUEST, _SRC.PAPER, _SRC.SHADOW,
                _SRC.MICRO_LIVE)


def _fail_field(field: str) -> None:
    raise ReconciliationContractError(f"{_ERROR_FIELD}:{field}")


def _require_snapshots(value: object, field: str,
                       source: ReconciliationSource) -> None:
    if not isinstance(value, tuple):
        _fail_field(field)
    for element in value:
        if not isinstance(element, OrderSnapshot):
            _fail_field(field)
        if element.source is not source:
            raise ReconciliationInputError(
                f"{_ERROR_INPUT}:SOURCE_MISMATCH:{source.value}")


def _require_reference(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail_field(field)


def _require_sequence(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or \
            value < 0:
        _fail_field("logical_sequence")


class ReconciliationService:
    """Durumsuz, saf mutabakat servisi."""

    def reconcile(self,
                  request_snapshots: Tuple[OrderSnapshot, ...],
                  paper_snapshots: Tuple[OrderSnapshot, ...],
                  shadow_snapshots: Tuple[OrderSnapshot, ...],
                  micro_live_snapshots: Tuple[OrderSnapshot, ...],
                  report_reference: str,
                  logical_sequence: int) -> ReconciliationReport:
        _require_snapshots(request_snapshots, "request_snapshots",
                           _SRC.EXECUTION_REQUEST)
        _require_snapshots(paper_snapshots, "paper_snapshots",
                           _SRC.PAPER)
        _require_snapshots(shadow_snapshots, "shadow_snapshots",
                           _SRC.SHADOW)
        _require_snapshots(micro_live_snapshots,
                           "micro_live_snapshots", _SRC.MICRO_LIVE)
        _require_reference(report_reference, "report_reference")
        _require_sequence(logical_sequence)

        chain: Tuple[Tuple[ReconciliationSource,
                           Tuple[OrderSnapshot, ...]], ...] = (
            (_SRC.EXECUTION_REQUEST, request_snapshots),
            (_SRC.PAPER, paper_snapshots),
            (_SRC.SHADOW, shadow_snapshots),
            (_SRC.MICRO_LIVE, micro_live_snapshots))

        audit: List[ReconciliationAudit] = [ReconciliationAudit(
            audit_code="RECONCILIATION_STARTED",
            logical_sequence=logical_sequence)]

        # Kaynak başına indeks + kaynak içi mükerrer tespiti.
        indexed: Dict[ReconciliationSource,
                      Dict[str, OrderSnapshot]] = {}
        duplicate_mismatches: Dict[
            str, List[ReconciliationMismatch]] = {}
        order_refs: List[str] = []
        for source, snapshots in chain:
            table: Dict[str, OrderSnapshot] = {}
            for snapshot in snapshots:
                reference = snapshot.order_reference
                if reference in table:
                    code = _C.DUPLICATE_ORDER
                    if source is not _SRC.EXECUTION_REQUEST:
                        code = _C.DUPLICATE_EXECUTION
                    duplicate_mismatches.setdefault(
                        reference, []).append(
                        ReconciliationMismatch(
                            mismatch_code=code,
                            order_reference=reference,
                            source_a=source, source_b=source,
                            logical_sequence=logical_sequence))
                else:
                    table[reference] = snapshot
                if reference not in order_refs:
                    order_refs.append(reference)
            indexed[source] = table

        provided: Dict[ReconciliationSource, bool] = {}
        for source, snapshots in chain:
            provided[source] = len(snapshots) > 0

        results: List[ReconciliationResult] = []
        total_mismatches = 0
        matched = 0
        mismatched = 0
        missing = 0
        for reference in order_refs:
            mismatches: List[ReconciliationMismatch] = list(
                duplicate_mismatches.get(reference, []))
            has_missing = False

            request = indexed[_SRC.EXECUTION_REQUEST].get(
                reference)
            if request is None:
                # Sonuç kaynağında var, istekte YOK — istek
                # kümesi BOŞ olsa bile taban çizgisi istektir.
                for source in SOURCE_CHAIN[1:]:
                    if reference in indexed[source]:
                        mismatches.append(ReconciliationMismatch(
                            mismatch_code=_C.MISSING_ORDER,
                            order_reference=reference,
                            source_a=source,
                            source_b=_SRC.EXECUTION_REQUEST,
                            logical_sequence=logical_sequence))
                has_missing = True

            if request is not None:
                for source in SOURCE_CHAIN[1:]:
                    if not provided[source]:
                        continue
                    downstream = indexed[source].get(reference)
                    if downstream is None:
                        mismatches.append(ReconciliationMismatch(
                            mismatch_code=_C.MISSING_ORDER,
                            order_reference=reference,
                            source_a=_SRC.EXECUTION_REQUEST,
                            source_b=source,
                            logical_sequence=logical_sequence))
                        has_missing = True
                        continue
                    mismatches.extend(self._compare_to_request(
                        request, downstream, logical_sequence))

            # Sonuç kaynakları arası durum / PnL karşılaştırması
            # (zincir sırasıyla ardışık MEVCUT çiftler).
            present: List[OrderSnapshot] = []
            for source in SOURCE_CHAIN[1:]:
                snapshot = indexed[source].get(reference)
                if snapshot is not None:
                    present.append(snapshot)
            for index in range(1, len(present)):
                earlier = present[index - 1]
                later = present[index]
                if earlier.status is not later.status:
                    mismatches.append(ReconciliationMismatch(
                        mismatch_code=_C.STATUS_MISMATCH,
                        order_reference=reference,
                        source_a=earlier.source,
                        source_b=later.source,
                        logical_sequence=logical_sequence))
                if earlier.pnl is not None and \
                        later.pnl is not None and \
                        earlier.pnl != later.pnl:
                    mismatches.append(ReconciliationMismatch(
                        mismatch_code=_C.PNL_MISMATCH,
                        order_reference=reference,
                        source_a=earlier.source,
                        source_b=later.source,
                        logical_sequence=logical_sequence))

            # Zincir boyu zaman damgası / mantıksal sıra ihlali.
            ordered: List[OrderSnapshot] = []
            for source in SOURCE_CHAIN:
                snapshot = indexed[source].get(reference)
                if snapshot is not None:
                    ordered.append(snapshot)
            for index in range(1, len(ordered)):
                earlier = ordered[index - 1]
                later = ordered[index]
                if later.timestamp < earlier.timestamp:
                    mismatches.append(ReconciliationMismatch(
                        mismatch_code=(
                            _C.TIMESTAMP_SEQUENCE_VIOLATION),
                        order_reference=reference,
                        source_a=earlier.source,
                        source_b=later.source,
                        logical_sequence=logical_sequence))
                if later.logical_sequence < \
                        earlier.logical_sequence:
                    mismatches.append(ReconciliationMismatch(
                        mismatch_code=(
                            _C.LOGICAL_SEQUENCE_VIOLATION),
                        order_reference=reference,
                        source_a=earlier.source,
                        source_b=later.source,
                        logical_sequence=logical_sequence))

            if has_missing:
                decision = ReconciliationDecision.MISSING
                missing += 1
            elif mismatches:
                decision = ReconciliationDecision.MISMATCHED
                mismatched += 1
            else:
                decision = ReconciliationDecision.MATCHED
                matched += 1
            total_mismatches += len(mismatches)
            for mismatch in mismatches:
                audit.append(ReconciliationAudit(
                    audit_code=("MISMATCH:"
                                + mismatch.mismatch_code.value),
                    order_reference=reference,
                    logical_sequence=logical_sequence))
            results.append(ReconciliationResult(
                order_reference=reference, decision=decision,
                mismatches=tuple(mismatches),
                logical_sequence=logical_sequence))

        statistics = ReconciliationStatistics(
            total_orders=len(order_refs), matched_orders=matched,
            mismatched_orders=mismatched, missing_orders=missing,
            total_mismatches=total_mismatches)
        decision = ReconciliationReportDecision.RECONCILED
        if total_mismatches > 0:
            decision = ReconciliationReportDecision.DISCREPANT
        audit.append(ReconciliationAudit(
            audit_code="RECONCILIATION_COMPLETED",
            logical_sequence=logical_sequence))
        return ReconciliationReport(
            report_reference=report_reference, decision=decision,
            results=tuple(results), statistics=statistics,
            audit=tuple(audit), logical_sequence=logical_sequence)

    def _compare_to_request(
            self, request: OrderSnapshot,
            downstream: OrderSnapshot,
            logical_sequence: int
    ) -> Tuple[ReconciliationMismatch, ...]:
        mismatches: List[ReconciliationMismatch] = []
        if downstream.quantity != request.quantity or \
                downstream.symbol != request.symbol or \
                downstream.side is not request.side:
            mismatches.append(ReconciliationMismatch(
                mismatch_code=_C.QUANTITY_MISMATCH,
                order_reference=request.order_reference,
                source_a=_SRC.EXECUTION_REQUEST,
                source_b=downstream.source,
                logical_sequence=logical_sequence))
        if request.price is not None and \
                downstream.price is not None and \
                request.price != downstream.price:
            mismatches.append(ReconciliationMismatch(
                mismatch_code=_C.PRICE_MISMATCH,
                order_reference=request.order_reference,
                source_a=_SRC.EXECUTION_REQUEST,
                source_b=downstream.source,
                logical_sequence=logical_sequence))
        return tuple(mismatches)
