"""Mission 2200 — Agent 01: Operasyon anlık görüntü kurucusu.

Ham kaynak verisinden (app katmanının topladığı sözlükler)
tek, değişmez ``OperationSnapshot`` kurar. Eksik bölümler
UNKNOWN görünümlere düşer; tazelik eşiği aşıldığında veri
STALE işaretlenir — sahte sağlıklı durum üretilmez.
"""

from __future__ import annotations

from typing import Mapping, Optional

from operation_control_mapper import (
    map_order, map_position, map_product, map_reconciliation,
    map_risk_limits, map_signal, to_reconciliation_state,
    to_text)
from operation_control_models import (
    UNKNOWN, AutomationState, DataFreshness, OperationSnapshot,
    ReconciliationState, SystemStatusView)

__all__ = ["DEFAULT_FRESHNESS_WINDOW_SECONDS",
           "build_status_view", "build_snapshot"]

DEFAULT_FRESHNESS_WINDOW_SECONDS = 120


def _freshness(generated_at: int, source_at: object,
               window: int) -> DataFreshness:
    """Kaynak zamanı bilinmiyorsa UNKNOWN; eskiyse STALE."""
    if not isinstance(source_at, int) or isinstance(
            source_at, bool) or source_at <= 0:
        return DataFreshness.UNKNOWN
    if source_at > generated_at:
        return DataFreshness.UNKNOWN
    if generated_at - source_at > window:
        return DataFreshness.STALE
    return DataFreshness.FRESH


def build_status_view(raw: Mapping, generated_at: int,
                      automation_state: AutomationState,
                      stop_new_entries: bool,
                      freshness_window: int =
                      DEFAULT_FRESHNESS_WINDOW_SECONDS
                      ) -> SystemStatusView:
    """Sistem durumu görünümünü fail-closed kur."""
    if not isinstance(raw, Mapping):
        raw = {}
    mode = to_text(raw.get("execution_mode"))
    if mode not in ("PAPER", "SHADOW", "MICRO_LIVE"):
        # Bilinmeyen mod asla LIVE veya sahte moda çökmez.
        mode = UNKNOWN

    def _state(key: str) -> str:
        value = to_text(raw.get(key))
        return value if value != UNKNOWN else UNKNOWN

    return SystemStatusView(
        app_version=to_text(raw.get("app_version")),
        execution_mode=mode,
        automation_state=automation_state,
        kill_switch_state=_state("kill_switch_state"),
        permission_gate_state=_state("permission_gate_state"),
        risk_engine_state=_state("risk_engine_state"),
        broker_state=_state("broker_state"),
        ledger_state=_state("ledger_state"),
        reconciliation_state=to_reconciliation_state(
            raw.get("reconciliation_state")),
        last_sync_at=to_text(raw.get("last_sync_at")),
        last_error_code=to_text(raw.get("last_error_code")
                                or "-"),
        data_freshness=_freshness(
            generated_at, raw.get("source_timestamp"),
            freshness_window),
        stop_new_entries=stop_new_entries is True)


def build_snapshot(raw: Mapping, generated_at: int,
                   automation_state: AutomationState,
                   stop_new_entries: bool,
                   symbol_states: Optional[Mapping] = None,
                   freshness_window: int =
                   DEFAULT_FRESHNESS_WINDOW_SECONDS
                   ) -> OperationSnapshot:
    """Tam operasyon anlık görüntüsünü kur.

    ``symbol_states``: servis kayıt defterinden sembol →
    SymbolAutomationState eşlemesi (ham veri güvenilmez)."""
    if not isinstance(raw, Mapping):
        raw = {}
    if not isinstance(generated_at, int) or isinstance(
            generated_at, bool) or generated_at < 0:
        generated_at = 0
    states = symbol_states if isinstance(
        symbol_states, Mapping) else {}

    def _rows(key: str, mapper):
        value = raw.get(key)
        if not isinstance(value, (list, tuple)):
            return ()
        rows = []
        for item in value:
            if isinstance(item, Mapping):
                try:
                    rows.append(mapper(item))
                except Exception:
                    # Tek bozuk satır tüm görünümü düşürmez;
                    # satır sessizce değil, eksik olarak düşer
                    # (satır sayısı UI'da görünür).
                    continue
        return tuple(rows)

    products = ()
    value = raw.get("products")
    if isinstance(value, (list, tuple)):
        rows = []
        for item in value:
            if isinstance(item, Mapping):
                symbol = to_text(item.get("symbol"))
                try:
                    rows.append(map_product(
                        item, states.get(symbol)))
                except Exception:
                    continue
        products = tuple(rows)

    risk_limits = None
    if isinstance(raw.get("risk_limits"), Mapping):
        try:
            risk_limits = map_risk_limits(raw["risk_limits"])
        except Exception:
            risk_limits = None

    return OperationSnapshot(
        generated_at=generated_at,
        status=build_status_view(
            raw.get("status") if isinstance(
                raw.get("status"), Mapping) else {},
            generated_at, automation_state, stop_new_entries,
            freshness_window),
        products=products,
        positions=_rows("positions", map_position),
        orders=_rows("orders", map_order),
        signals=_rows("signals", map_signal),
        reconciliation=_rows("reconciliation",
                             map_reconciliation),
        risk_limits=risk_limits)
