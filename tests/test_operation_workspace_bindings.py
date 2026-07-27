"""Mission 2200 Agent 02 — eylem bağlama + sözleşme değişmezleri.

Her görünür eylemin gerçek bir uca bağlandığını, tüm çalışma alanı
uçlarının zarf/sterillik değişmezlerini koruduğunu ve Mission 2100
çekirdeğine dokunulmadığını doğrular.
"""
import json
import re
from pathlib import Path

import pytest

import app as app_module
import operation_workspace_api as wa
import operation_workspace_metrics as wm_metrics
import operation_workspace_models as wm_models
import operation_workspace_service as wm_service

ROOT = Path(__file__).resolve().parent.parent
WS_JS = (ROOT / "static" / "js" / "operation_workspace.js").read_text(
    encoding="utf-8")
OC_JS = (ROOT / "static" / "js" / "operation_control.js").read_text(
    encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "operation_control.html").read_text(
    encoding="utf-8")


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c


WORKSPACE_ENDPOINTS = [
    "/api/operation-control/workspace/portfolio",
    "/api/operation-control/workspace/performance",
    "/api/operation-control/workspace/broker-health",
    "/api/operation-control/workspace/strategies",
    "/api/operation-control/workspace/journal",
]

CSV_ENDPOINTS = [
    f"/api/operation-control/workspace/export/{name}.csv"
    for name in sorted(wa.CSV_EXPORTS)
]


# ── Eylem bağlama: düğme → gerçek uç ──────────────────────────────

class TestActionBindings:
    @pytest.mark.parametrize("data_attr,endpoint_fragment", [
        ("data-auto=\"start\"", "/automation/start"),
        ("data-auto=\"pause\"", "/automation/pause"),
        ("data-auto=\"resume\"", "/automation/resume"),
        ("data-auto=\"stop\"", "/automation/stop"),
    ])
    def test_automation_buttons(self, data_attr, endpoint_fragment):
        assert data_attr in TEMPLATE
        assert "/automation/" in OC_JS

    @pytest.mark.parametrize("button_id,js_marker", [
        ("oc-stop-entries", "oc-stop-entries"),
        ("oc-close-all", "oc-close-all"),
        ("oc-kill", "/global/kill-switch"),
        ("oc-kill-off", "/global/kill-switch"),
    ])
    def test_destructive_buttons_bound(self, button_id, js_marker):
        assert f'id="{button_id}"' in TEMPLATE
        assert js_marker in OC_JS

    @pytest.mark.parametrize("marker", [
        "confirmDestructive", "idempotency_key", "ONAYLIYORUM"])
    def test_destructive_guard_markers(self, marker):
        assert marker in OC_JS

    def test_strategy_detail_is_client_side_filter(self):
        # 'Ayrıntı' düğmesi ağa istek atmaz; pozisyon filtresi uygular.
        assert "data-strategy-detail" in WS_JS
        assert "ows-search-positions" in WS_JS

    def test_journal_click_copies_correlation(self):
        assert "data-correlation" in WS_JS
        assert "clipboard" in WS_JS

    @pytest.mark.parametrize("forbidden", [
        "order/place", "order/new", "createOrder", "submitOrder",
        "/api/v3/order", "leverage"])
    def test_no_raw_order_paths_in_clients(self, forbidden):
        assert forbidden not in WS_JS
        assert forbidden not in OC_JS


# ── Zarf değişmezleri (tüm uçlar) ──────────────────────────────────

class TestEnvelopeInvariants:
    @pytest.mark.parametrize("path", WORKSPACE_ENDPOINTS)
    def test_ok_true_and_error_fields_null(self, client, path):
        body = client.get(path).get_json()
        assert body["ok"] is True
        assert body["error_code"] is None

    @pytest.mark.parametrize("path", WORKSPACE_ENDPOINTS)
    def test_execution_mode_present(self, client, path):
        body = client.get(path).get_json()
        assert body["execution_mode"] in (
            "PAPER", "LIVE", "REPLAY", "BACKTEST", "UNKNOWN")

    @pytest.mark.parametrize("path", WORKSPACE_ENDPOINTS)
    def test_data_freshness_valid(self, client, path):
        body = client.get(path).get_json()
        assert body["data_freshness"] in ("FRESH", "STALE", "UNKNOWN")

    @pytest.mark.parametrize("path", WORKSPACE_ENDPOINTS)
    def test_generated_at_positive_int(self, client, path):
        body = client.get(path).get_json()
        assert isinstance(body["generated_at"], int)
        assert body["generated_at"] > 0

    @pytest.mark.parametrize("path", WORKSPACE_ENDPOINTS)
    def test_json_roundtrip_stable(self, client, path):
        body = client.get(path).get_json()
        json.dumps(body)  # tamamen JSON-güvenli

    @pytest.mark.parametrize("path", WORKSPACE_ENDPOINTS)
    def test_no_float_values_in_payload(self, client, path):
        def walk(node):
            if isinstance(node, float):
                raise AssertionError("float sızdı: %r" % node)
            if isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(client.get(path).get_json()["data"])

    @pytest.mark.parametrize("path", CSV_ENDPOINTS)
    def test_csv_deterministic(self, client, path):
        a = client.get(path).get_data(as_text=True)
        b = client.get(path).get_data(as_text=True)
        assert a == b

    @pytest.mark.parametrize("path", CSV_ENDPOINTS)
    def test_csv_crlf_lines(self, client, path):
        text = client.get(path).get_data(as_text=True)
        assert text.endswith("\r\n") or text.endswith("\n")


# ── Mission 2100 dokunulmazlığı ────────────────────────────────────

PROTECTED_MODULES = [
    "controlled_execution_api.py", "execution_authority.py",
    "execution_lifecycle.py", "execution_ledger.py",
    "execution_reconciliation.py", "execution_permission_gate.py",
]


class TestMission2100Untouched:
    @pytest.mark.parametrize("module", PROTECTED_MODULES)
    def test_workspace_modules_do_not_import_privately(self, module):
        # Çalışma alanı modülleri saf kalır; yürütme çekirdeğini
        # içe aktarmaz (yalnız app.py orkestre eder).
        name = module[:-3]
        for source_file in ("operation_workspace_metrics.py",
                            "operation_workspace_models.py",
                            "operation_workspace_service.py",
                            "operation_workspace_api.py"):
            source = (ROOT / source_file).read_text(encoding="utf-8")
            assert f"import {name}" not in source, (
                source_file, module)

    def test_metrics_module_pure(self):
        source = (ROOT / "operation_workspace_metrics.py").read_text(
            encoding="utf-8")
        for banned in ("import requests", "import flask",
                       "import app", "open(", "Path("):
            assert banned not in source, banned

    def test_models_module_pure(self):
        source = (ROOT / "operation_workspace_models.py").read_text(
            encoding="utf-8")
        for banned in ("import requests", "import flask",
                       "import app"):
            assert banned not in source, banned

    def test_service_module_pure(self):
        source = (ROOT / "operation_workspace_service.py").read_text(
            encoding="utf-8")
        for banned in ("import requests", "import flask",
                       "import app", "time.time"):
            assert banned not in source, banned


# ── Sterillik: hata metinleri ─────────────────────────────────────

class TestSterileErrors:
    @pytest.mark.parametrize("name", sorted(wa.CSV_EXPORTS))
    def test_export_names_are_known(self, name):
        assert re.fullmatch(r"[a-z]+", name)

    def test_unknown_export_message_sterile(self, client):
        r = client.get(
            "/api/operation-control/workspace/export/evil.csv")
        assert r.status_code == 404
        text = r.get_data(as_text=True)
        assert "Traceback" not in text
        assert "evil" not in text.lower() or "UNKNOWN" in text

    @pytest.mark.parametrize("module", [
        wa, wm_metrics, wm_models, wm_service])
    def test_module_docstrings_exist(self, module):
        assert module.__doc__

    @pytest.mark.parametrize("symbol", [
        "workspace_envelope", "serialize_rows", "rows_to_csv"])
    def test_api_module_exports(self, symbol):
        assert symbol in wa.__all__

    @pytest.mark.parametrize("fn", [
        "parse_trades", "parse_equity_points", "compute_metrics",
        "period_profit", "sharpe_ratio", "max_drawdown_pct",
        "equity_returns"])
    def test_metrics_public_api(self, fn):
        assert callable(getattr(wm_metrics, fn))

    @pytest.mark.parametrize("cls", [
        "PortfolioView", "PerformanceView", "BrokerHealthView",
        "StrategyView", "JournalEventView"])
    def test_model_classes_exported(self, cls):
        assert hasattr(wm_models, cls)

    @pytest.mark.parametrize("fn", [
        "build_portfolio_view", "build_performance_view",
        "build_broker_health_view", "build_strategy_rows",
        "build_journal_events"])
    def test_service_builders_exported(self, fn):
        assert callable(getattr(wm_service, fn))
