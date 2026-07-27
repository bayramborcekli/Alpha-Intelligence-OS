"""Mission 2200 Agent 02 — çalışma alanı API uçları + CSV testleri."""
import json

import pytest

import operation_workspace_api as wa

import app as app_module


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c


@pytest.fixture()
def anon():
    # Kimlik doğrulama kapısı TESTING=True iken atlanır; 401/302
    # davranışını doğrulamak için gerçek kapıyla test edilir.
    previous = app_module.app.config.get("TESTING")
    app_module.app.config["TESTING"] = False
    try:
        with app_module.app.test_client() as c:
            yield c
    finally:
        app_module.app.config["TESTING"] = previous


ENDPOINTS = [
    "/api/operation-control/workspace/portfolio",
    "/api/operation-control/workspace/performance",
    "/api/operation-control/workspace/broker-health",
    "/api/operation-control/workspace/strategies",
    "/api/operation-control/workspace/journal",
]

DATA_KEYS = {
    "portfolio": "portfolio",
    "performance": "performance",
    "broker-health": "broker_health",
    "strategies": "strategies",
    "journal": "journal",
}

ENVELOPE_KEYS = {"ok", "data", "error_code", "message",
                 "correlation_id", "generated_at", "data_freshness",
                 "execution_mode"}


# ── Kimlik doğrulama ──────────────────────────────────────────────

class TestAuth:
    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_unauthenticated_401(self, anon, path):
        r = anon.get(path)
        assert r.status_code == 401

    @pytest.mark.parametrize("name", sorted(wa.CSV_EXPORTS))
    def test_csv_unauthenticated_401(self, anon, name):
        r = anon.get(
            f"/api/operation-control/workspace/export/{name}.csv")
        assert r.status_code == 401


# ── Zarf sözleşmesi ────────────────────────────────────────────────

class TestEnvelope:
    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_200_and_envelope(self, client, path):
        r = client.get(path)
        assert r.status_code == 200
        body = r.get_json()
        assert ENVELOPE_KEYS.issubset(body.keys())
        assert body["ok"] is True

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_data_key_present(self, client, path):
        body = client.get(path).get_json()
        key = DATA_KEYS[path.rsplit("/", 1)[1]]
        assert key in body["data"]

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_correlation_id_string(self, client, path):
        body = client.get(path).get_json()
        assert isinstance(body["correlation_id"], str)
        assert body["correlation_id"]

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_no_secrets_leaked(self, client, path):
        text = client.get(path).get_data(as_text=True).lower()
        for token in ("api_key", "api_secret", "password",
                      "session_secret", "traceback"):
            assert token not in text, token

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_post_method_not_allowed(self, client, path):
        assert client.post(path).status_code == 405

    def test_portfolio_fields(self, client):
        p = client.get(ENDPOINTS[0]).get_json()["data"]["portfolio"]
        for field in ("portfolio_value", "cash", "equity",
                      "daily_pnl", "weekly_pnl", "monthly_pnl",
                      "open_risk", "exposure", "drawdown_pct",
                      "largest_winner", "largest_loser",
                      "open_position_count", "source_freshness"):
            assert field in p, field

    def test_performance_fields(self, client):
        p = client.get(ENDPOINTS[1]).get_json()["data"]["performance"]
        for field in ("trade_count", "win_rate_pct", "average_win",
                      "average_loss", "profit_factor", "sharpe",
                      "max_drawdown_pct", "average_hold_seconds",
                      "daily_profit", "weekly_profit",
                      "monthly_profit", "dropped_records",
                      "equity_curve"):
            assert field in p, field

    def test_broker_fields(self, client):
        b = client.get(
            ENDPOINTS[2]).get_json()["data"]["broker_health"]
        for field in ("heartbeat_state", "latency_ms", "api_status",
                      "rate_limit_state", "reconnect_count",
                      "synchronization_state", "authentication_state",
                      "permission_state", "data_age_seconds"):
            assert field in b, field

    def test_broker_reconnect_never_fake_number(self, client):
        b = client.get(
            ENDPOINTS[2]).get_json()["data"]["broker_health"]
        # Depoda ölçen bileşen yok: dürüst UNKNOWN (null) beklenir.
        assert b["reconnect_count"] is None

    def test_strategies_is_list(self, client):
        s = client.get(ENDPOINTS[3]).get_json()["data"]["strategies"]
        assert isinstance(s, list)

    def test_journal_is_list(self, client):
        j = client.get(ENDPOINTS[4]).get_json()["data"]["journal"]
        assert isinstance(j, list)


# ── CSV dışa aktarma ───────────────────────────────────────────────

class TestCsvEndpoints:
    @pytest.mark.parametrize("name", sorted(wa.CSV_EXPORTS))
    def test_csv_200(self, client, name):
        r = client.get(
            f"/api/operation-control/workspace/export/{name}.csv")
        assert r.status_code == 200
        assert "text/csv" in r.content_type
        assert "attachment" in r.headers.get(
            "Content-Disposition", "")

    @pytest.mark.parametrize("name", [
        "secrets", "config", "unknown", "..%2Fpasswd", "positions "])
    def test_unknown_export_404(self, client, name):
        r = client.get(
            f"/api/operation-control/workspace/export/{name}.csv")
        assert r.status_code == 404

    @pytest.mark.parametrize("name", sorted(wa.CSV_EXPORTS))
    def test_csv_no_secret_tokens(self, client, name):
        text = client.get(
            f"/api/operation-control/workspace/export/{name}.csv"
        ).get_data(as_text=True).lower()
        for token in ("api_key", "api_secret", "password"):
            assert token not in text


# ── rows_to_csv birimi ─────────────────────────────────────────────

class TestRowsToCsv:
    def test_empty(self):
        assert wa.rows_to_csv([]) == "empty\r\n"

    def test_headers_from_first_row(self):
        out = wa.rows_to_csv([{"a": 1, "b": "x"}])
        lines = out.splitlines()
        assert lines[0] == "a,b"
        assert lines[1] == "1,x"

    def test_none_becomes_unknown(self):
        out = wa.rows_to_csv([{"a": None}])
        assert "UNKNOWN" in out

    @pytest.mark.parametrize("evil", [
        "=cmd()", "+SUM(A1)", "-2+3", "@import"])
    def test_formula_injection_neutralized(self, evil):
        out = wa.rows_to_csv([{"a": evil}])
        body = out.splitlines()[1]
        assert body.startswith("'") or body.startswith("\"'")

    @pytest.mark.parametrize("evil", [
        "\t=cmd()", " =SUM(A1)", "\n=1+1", "\r=x", "  +2",
        "\t\t-3", " @import", "\x00=x"])
    def test_formula_injection_whitespace_bypass_blocked(self, evil):
        out = wa.rows_to_csv([{"a": evil}])
        # Nötrleştirilmiş hücre kesme işaretiyle başlamalı:
        # tırnak/boşluk katmanı ne olursa olsun `'` + kötü değer
        # dizisi çıktıda bulunmalıdır.
        assert "'" + evil in out

    def test_safe_values_untouched(self):
        out = wa.rows_to_csv([{"a": "BTCUSDT"}])
        assert "'BTCUSDT" not in out

    def test_multiple_rows(self):
        out = wa.rows_to_csv([{"a": 1}, {"a": 2}, {"a": 3}])
        assert len(out.splitlines()) == 4

    @pytest.mark.parametrize("value,expected", [
        (None, "UNKNOWN"),
        (True, "true"),
        (False, "false"),
        ("plain", "plain"),
        (0, "0"),
        (42, "42"),
    ])
    def test_csv_cell_scalars(self, value, expected):
        assert wa.rows_to_csv([{"a": value}]).splitlines()[1] \
            .strip('"') == expected

    @pytest.mark.parametrize("dec,expected", [
        ("1.5", "1.5"), ("0", "0"), ("100.00000000", "100.00000000"),
        ("1E+2", "100"),
    ])
    def test_csv_cell_decimal_plain_format(self, dec, expected):
        from decimal import Decimal
        line = wa.rows_to_csv([{"a": Decimal(dec)}]).splitlines()[1]
        assert line == expected

    def test_csv_cell_negative_decimal_neutralized(self):
        from decimal import Decimal
        line = wa.rows_to_csv([{"a": Decimal("-5")}]).splitlines()[1]
        assert line.lstrip('"').startswith("'-5")

    @pytest.mark.parametrize("key", ["a", "sym", "correlation_id"])
    def test_header_key_passthrough(self, key):
        assert wa.rows_to_csv([{key: "v"}]).splitlines()[0] == key

    def test_serialize_rows_decimal(self):
        from decimal import Decimal
        rows = wa.serialize_rows([{"v": Decimal("1.5")}])
        assert rows[0]["v"] == "1.5"
        json.dumps(rows)  # JSON-güvenli olmalı


# ── Varsayılan açılış sayfası ──────────────────────────────────────

class TestLandingRedirect:
    def test_login_page_defaults_to_operation_center(self, anon):
        r = anon.get("/login")
        assert r.status_code == 200

    def test_protected_page_redirects_to_login(self, anon):
        r = anon.get("/operation-center")
        assert r.status_code in (301, 302)
        assert "/login" in r.headers["Location"]

    def test_operation_center_renders_for_user(self, client):
        r = client.get("/operation-center")
        assert r.status_code == 200
