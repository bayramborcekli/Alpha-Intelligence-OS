"""Task 29 — Emir yaşam döngüsü zinciri: servis + GET ucu testleri."""
from decimal import Decimal

import pytest

import operation_control_models as m
import operation_workspace_service as ws
from operation_workspace_models import (LIFECYCLE_EVENT_TYPES,
                                        OrderLifecycleEventView)

import app as app_module


def make_order(**over):
    base = dict(order_id="42", client_order_id="c42",
                symbol="BTCUSDT", side="BUY", order_type="LIMIT",
                quantity=Decimal("1"), requested_price=Decimal("100"),
                average_fill_price=None,
                filled_quantity=Decimal("0.5"),
                remaining_quantity=Decimal("0.5"),
                status="PARTIALLY_FILLED",
                created_at="1753600000000",
                updated_at="1753600060000",
                strategy="alpha20_v1", correlation_id="corr-1",
                execution_mode="PAPER",
                reconciliation_state=m.ReconciliationState.UNKNOWN)
    base.update(over)
    return m.OrderView(**base)


class TestBuilder:
    def test_observed_events_only(self):
        events = ws.build_order_lifecycle_events(make_order())
        types = [e.event_type for e in events]
        assert types == ["ORDER_CREATED", "FILL_PROGRESS",
                         "STATUS_OBSERVED"]
        for e in events:
            assert isinstance(e, OrderLifecycleEventView)
            assert e.event_type in LIFECYCLE_EVENT_TYPES
            assert e.correlation_id == "corr-1"

    def test_epoch_converted_to_iso(self):
        events = ws.build_order_lifecycle_events(make_order())
        assert events[0].event_time.startswith("2025-07-27T")

    def test_no_fill_event_when_zero(self):
        events = ws.build_order_lifecycle_events(
            make_order(filled_quantity=Decimal("0"),
                       remaining_quantity=Decimal("1")))
        assert all(e.event_type != "FILL_PROGRESS" for e in events)

    def test_unknown_everything_gives_empty_chain(self):
        order = make_order(created_at="UNKNOWN",
                           updated_at="UNKNOWN",
                           filled_quantity=None,
                           remaining_quantity=None,
                           status="UNKNOWN",
                           correlation_id="UNKNOWN")
        assert ws.build_order_lifecycle_events(order) == ()

    def test_correlated_signal_and_audit_linked(self):
        signal = m.SignalView(
            signal_time="t1", symbol="BTCUSDT", strategy="s",
            direction="LONG", confidence=None,
            decision="APPROVED", risk_outcome="UNKNOWN",
            permission_outcome="UNKNOWN", rejection_code="-",
            execution_result="EXECUTED",
            correlation_id="corr-1")
        audit = m.OperationAuditRecord(
            timestamp=1, actor="op", action="CLOSE_REQUEST",
            target="BTCUSDT", previous_state="-",
            requested_state="-", result="ACCEPTED", reason="r",
            correlation_id="corr-1")
        events = ws.build_order_lifecycle_events(
            make_order(), [signal], [audit])
        types = [e.event_type for e in events]
        assert "SIGNAL_LINKED" in types
        assert "OPERATOR_ACTION" in types

    def test_unmatched_correlation_not_linked(self):
        signal_fields = dict(
            signal_time="t1", symbol="BTCUSDT", strategy="s",
            direction="LONG", confidence=None,
            decision="APPROVED", risk_outcome="UNKNOWN",
            permission_outcome="UNKNOWN", rejection_code="-",
            execution_result="EXECUTED",
            correlation_id="other")
        events = ws.build_order_lifecycle_events(
            make_order(), [m.SignalView(**signal_fields)], [])
        assert all(e.event_type != "SIGNAL_LINKED" for e in events)


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c


ENVELOPE_KEYS = {"ok", "data", "error_code", "message",
                 "correlation_id", "generated_at",
                 "data_freshness", "execution_mode"}


class TestEndpoint:
    def test_unknown_order_404_envelope(self, client):
        r = client.get("/api/operation-control/workspace/orders/"
                       "no-such-order/lifecycle")
        assert r.status_code == 404
        body = r.get_json()
        assert body["ok"] is False
        assert body["error_code"] == "ORDER_NOT_FOUND"

    def test_existing_order_returns_chain(self, client, monkeypatch):
        snapshot = app_module._operation_snapshot()
        order = make_order()
        patched = type(snapshot)(
            **{**{f: getattr(snapshot, f) for f in
                  snapshot.__dataclass_fields__},
               "orders": (order,)})
        monkeypatch.setattr(app_module, "_operation_snapshot",
                            lambda: patched)
        r = client.get("/api/operation-control/workspace/orders/"
                       "42/lifecycle")
        assert r.status_code == 200
        body = r.get_json()
        assert ENVELOPE_KEYS <= set(body)
        data = body["data"]
        assert data["order_id"] == "42"
        assert data["count"] == len(data["lifecycle"]) >= 3
        first = data["lifecycle"][0]
        assert first["event_type"] == "ORDER_CREATED"
        assert first["correlation_id"] == "corr-1"

    def test_post_not_allowed(self, client):
        r = client.post("/api/operation-control/workspace/orders/"
                        "42/lifecycle")
        assert r.status_code == 405
