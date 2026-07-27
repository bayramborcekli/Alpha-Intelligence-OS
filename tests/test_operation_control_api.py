"""Mission 2200 — Agent 01: API zarfı + Flask uç noktası testleri.

Zarf sözleşmesi kapalıdır; HTTP kod tabloları kural tablosudur.
Flask testleri gerçek uygulama + test istemcisi kullanır.
"""

import json
from decimal import Decimal

import pytest

import operation_control_api as oca
from operation_control_models import (
    AutomationState, DataFreshness, IdempotencyStatus,
    OperationActionResult, OperationActionStatus as OAS,
    OperationSnapshot)
from tests.test_operation_control_models import (
    valid_audit, valid_position, valid_status)


def result_with(**over):
    base = dict(action_id="a-1", status=OAS.COMPLETED,
                correlation_id="c-1",
                idempotency_status=IdempotencyStatus.NEW,
                audit_recorded=True, lifecycle_status="APPLIED",
                previous_state="X", current_state="Y")
    base.update(over)
    return OperationActionResult(**base)


def snapshot():
    return OperationSnapshot(generated_at=10,
                             status=valid_status(
                                 data_freshness=DataFreshness.FRESH))


# ── Serileştirme ───────────────────────────────────────────────────

class TestSerialization:
    @pytest.mark.parametrize("value,expected", [
        (Decimal("1.5"), "1.5"), (AutomationState.RUNNING,
                                  "RUNNING"),
        ("x", "x"), (5, 5), (True, True), (None, None),
        ((Decimal("1"), "a"), ["1", "a"]),
        (object(), "UNKNOWN"), (1.5, "UNKNOWN"),
    ])
    def test_serialize_value(self, value, expected):
        assert oca.serialize_value(value) == expected

    def test_serialize_view_position(self):
        data = oca.serialize_view(
            valid_position(entry_price=Decimal("1.25")))
        assert data["entry_price"] == "1.25"
        assert data["reconciliation_state"] == "UNKNOWN"
        json.dumps(data)  # JSON-uyumlu olmalı

    def test_serialize_audit(self):
        rows = oca.serialize_audit((valid_audit(),))
        assert rows[0]["actor"] == "op"
        json.dumps(rows)


# ── Zarf sözleşmesi ────────────────────────────────────────────────

class TestEnvelopes:
    def test_envelope_keys_closed(self):
        assert oca.ENVELOPE_KEYS == (
            "ok", "data", "error_code", "message",
            "correlation_id", "generated_at", "data_freshness",
            "execution_mode")
        assert oca.ACTION_KEYS == oca.ENVELOPE_KEYS + (
            "action_id", "idempotency_status", "audit_recorded",
            "lifecycle_status")

    def test_read_envelope(self):
        payload, status = oca.read_envelope(
            {"a": 1}, snapshot(), "c-1", 10)
        assert status == 200
        assert payload["ok"] is True
        assert set(payload) == set(oca.ENVELOPE_KEYS)
        assert payload["data_freshness"] == "FRESH"
        assert payload["execution_mode"] == "PAPER"

    def test_read_envelope_no_snapshot(self):
        payload, _ = oca.read_envelope({}, None, "c-1", 10)
        assert payload["data_freshness"] == "UNKNOWN"
        assert payload["execution_mode"] == "UNKNOWN"

    def test_error_envelope(self):
        payload, status = oca.error_envelope(
            "UNKNOWN_TARGET:position", "Pozisyon bulunamadı.",
            "c-1", 10)
        assert status == 404
        assert payload["ok"] is False
        assert set(payload) == set(oca.ENVELOPE_KEYS)

    def test_error_envelope_explicit_status(self):
        _, status = oca.error_envelope("X", "m", "c", 1,
                                       http_status=418)
        assert status == 418

    def test_action_envelope_keys(self):
        payload, status = oca.action_envelope(
            result_with(), snapshot(), 10)
        assert status == 200
        assert set(payload) == set(oca.ACTION_KEYS)
        assert payload["ok"] is True
        assert payload["data"]["status"] == "COMPLETED"

    def test_action_envelope_denied_not_ok(self):
        payload, status = oca.action_envelope(
            result_with(status=OAS.DENIED,
                        error_code="POLICY_DENIED:x"),
            None, 10)
        assert payload["ok"] is False
        assert status == 403


# ── HTTP kod tabloları ─────────────────────────────────────────────

class TestStatusCodes:
    @pytest.mark.parametrize("code,expected", [
        ("INVALID_TRANSITION", 409),
        ("IDEMPOTENCY_CONFLICT", 409),
        ("POLICY_DENIED:reason_required", 403),
        ("KILL_SWITCH_ACTIVE", 423),
        ("KILL_SWITCH_DENIED", 423),
        ("DEPENDENCY_UNAVAILABLE:ledger", 503),
        ("POSITION_DATA_INCOMPLETE", 422),
        ("UNKNOWN_TARGET:position", 404),
        ("MALFORMED_REQUEST", 400),
        ("UNSUPPORTED_CAPABILITY", 422),
        ("PERMISSION_DENIED", 403),
        ("RISK_DENIED", 422),
        ("RISK_REJECTED", 422),
        ("EXECUTION_REJECTED", 422),
        ("SOMETHING_NEW", 500),
        ("", 500), (None, 500), (5, 500),
    ])
    def test_error_code_table(self, code, expected):
        assert oca.status_code_for_error(code) == expected

    @pytest.mark.parametrize("status,expected", [
        (OAS.COMPLETED, 200), (OAS.ACCEPTED, 202),
        (OAS.PARTIAL, 202), (OAS.FAILED, 422),
        (OAS.UNSUPPORTED, 422)])
    def test_action_table(self, status, expected):
        assert oca.status_code_for_action(
            result_with(status=status)) == expected

    def test_denied_resolves_from_error_code(self):
        assert oca.status_code_for_action(result_with(
            status=OAS.DENIED,
            error_code="IDEMPOTENCY_CONFLICT")) == 409


# ── Flask uç noktaları ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client
    app_module.app.config["TESTING"] = False


def post(client, path, **body):
    return client.post(path, data=json.dumps(body),
                       content_type="application/json")


READ_ENDPOINTS = ("status", "products", "positions", "orders",
                  "signals", "reconciliation", "risk", "audit")


class TestReadEndpoints:
    @pytest.mark.parametrize("name", READ_ENDPOINTS)
    def test_read_ok(self, client, name):
        response = client.get(f"/api/operation-control/{name}")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert set(payload) == set(oca.ENVELOPE_KEYS)

    @pytest.mark.parametrize("name", READ_ENDPOINTS)
    def test_no_store(self, client, name):
        response = client.get(f"/api/operation-control/{name}")
        assert "no-store" in response.headers.get(
            "Cache-Control", "")

    def test_status_exposes_confirmation_phrase(self, client):
        payload = client.get(
            "/api/operation-control/status").get_json()
        assert payload["data"]["confirmation_phrase"] == \
            "ONAYLIYORUM"

    def test_status_never_live(self, client):
        payload = client.get(
            "/api/operation-control/status").get_json()
        assert payload["data"]["execution_mode"] != "LIVE"

    def test_page_renders(self, client):
        response = client.get("/operation-center")
        assert response.status_code == 200
        assert b"Operation" in response.data


class TestActionEndpoints:
    def test_start_stop(self, client):
        response = post(client,
                        "/api/operation-control/automation/start",
                        idempotency_key="fl-start-1")
        assert response.status_code in (200, 423)
        if response.status_code == 200:
            payload = response.get_json()
            assert set(payload) == set(oca.ACTION_KEYS)
            stop = post(
                client,
                "/api/operation-control/automation/stop",
                idempotency_key="fl-stop-1")
            assert stop.status_code == 200

    def test_unknown_command_rejected(self, client):
        response = post(
            client, "/api/operation-control/automation/launch")
        assert response.status_code in (400, 404)

    def test_unknown_symbol_command_rejected(self, client):
        response = post(
            client,
            "/api/operation-control/symbols/BTCUSDT/delete")
        assert response.status_code in (400, 404)

    def test_close_unknown_position(self, client):
        response = post(
            client,
            "/api/operation-control/positions/NOPE123/close",
            reason="r", confirm_phrase="ONAYLIYORUM",
            idempotency_key="fl-c1")
        assert response.status_code == 404
        assert response.get_json()["error_code"] == \
            "UNKNOWN_TARGET:position"

    def test_close_all_wrong_phrase(self, client):
        response = post(
            client,
            "/api/operation-control/global/request-close-all",
            reason="r", confirm_phrase="yanlis",
            idempotency_key="fl-c2")
        assert response.status_code == 403
        assert response.get_json()["error_code"] == \
            "POLICY_DENIED:confirmation_required"

    def test_stop_new_entries_missing_reason(self, client):
        response = post(
            client,
            "/api/operation-control/global/stop-new-entries",
            confirm_phrase="ONAYLIYORUM",
            idempotency_key="fl-c3")
        assert response.status_code == 403
        assert response.get_json()["error_code"] == \
            "POLICY_DENIED:reason_required"

    def test_kill_switch_engage_requires_guard(self, client):
        response = post(
            client, "/api/operation-control/global/kill-switch",
            engaged=True)
        assert response.status_code in (400, 403)

    def test_action_response_no_store(self, client):
        response = post(
            client,
            "/api/operation-control/global/stop-new-entries")
        assert "no-store" in response.headers.get(
            "Cache-Control", "")
