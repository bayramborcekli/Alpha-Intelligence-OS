"""Mission 1800 / Agent 07 — Güvenlik Doğrulama testleri.

Strategy Intelligence yığınının (Core, Service, API, UI, Export)
güvenlik garantilerini kanıtlayan statik AST analizi, import denetimi,
secret taraması, salt-okunurluk denetimi ve penetrasyon senaryoları.
Yeni özellik yoktur — yalnız doğrulama.
"""

from __future__ import annotations

import ast
import builtins
import copy
import json
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import strategy_export as sx
import strategy_intelligence as score
import strategy_service as ssv

PASSWORD = "strategy-sec-parola-1"
HASH = generate_password_hash(PASSWORD)

STRATEGY_MODULES = ("strategy_intelligence.py", "strategy_service.py",
                    "strategy_export.py")
UI_TEMPLATE = "templates/strategy_intelligence.html"

API_ROUTES = ("/api/strategy/intelligence",
              "/api/v1/strategy/intelligence")


def _analysis(status="OK"):
    return {
        "analysis_version": 1, "status": status,
        "portfolio": {
            "allocation": {"assets": [{"symbol": "BTCUSDT"}],
                           "cash_weight_pct": "10.00"},
            "exposure": {"gross_pct": "90.00"},
            "concentration": {"top_symbol": "BTCUSDT",
                              "top_share_pct": "80.00",
                              "effective_positions": "4.00"},
            "risk_utilization": {"net_exposure_util_pct": "45.00",
                                 "drawdown_util_pct": "10.00",
                                 "concentration_util_pct": "50.00",
                                 "limits_breached": []},
        },
    }


def _providers(analysis=None, freshness="fresh"):
    env = analysis if analysis is not None else _analysis()
    return {"portfolio_analysis":
            (lambda: {"freshness": freshness, "data": env})}


def _tree(path):
    return ast.parse(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def client(monkeypatch):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB",
                       "/tmp/test_m1800sec_attempts.db")
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True


def _login(c):
    r = c.post("/api/v1/auth/login",
               json={"username": "sahip", "password": PASSWORD})
    assert r.status_code == 200


def _wire(monkeypatch, providers=None):
    fixed = providers if providers is not None else _providers()
    monkeypatch.setattr(ssv, "build_default_strategy_providers",
                        lambda: fixed)


# ── 4. Mimari sınırlar / bağımlılık yönü ─────────────────────────────

class TestArchitectureBoundaries:
    def _imports(self, path):
        mods = set()
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0])
        return mods

    def test_core_pure_stdlib_no_reverse_deps(self):
        mods = self._imports("strategy_intelligence.py")
        assert mods <= {"decimal", "typing", "__future__"}, mods

    def test_service_imports_only_core_downward(self):
        mods = self._imports("strategy_service.py")
        for banned in ("app", "flask", "strategy_export", "auth",
                       "risk_api", "intelligence_service"):
            assert banned not in mods, banned
        assert "strategy_intelligence" in mods

    def test_export_imports_neither_core_nor_service(self):
        mods = self._imports("strategy_export.py")
        assert mods <= {"json", "typing", "__future__"}, mods

    def test_no_circular_dependencies(self):
        core = self._imports("strategy_intelligence.py")
        assert not core & {"strategy_service", "strategy_export", "app",
                           "portfolio_service", "portfolio_intelligence"}

    def test_portfolio_layer_unaware_of_strategy(self):
        for path in ("portfolio_intelligence.py", "portfolio_service.py",
                     "portfolio_export.py"):
            mods = self._imports(path)
            assert not any(m.startswith("strategy") for m in mods), path

    def test_api_layer_routing_only_no_math(self):
        funcs = [n for n in ast.walk(_tree("app.py"))
                 if isinstance(n, ast.FunctionDef)
                 and n.name in ("api_strategy_intelligence",
                                "strategy_intelligence_page")]
        assert len(funcs) == 2
        for fn in funcs:
            for node in ast.walk(fn):
                assert not (isinstance(node, ast.BinOp) and isinstance(
                    node.op, (ast.Mult, ast.Div, ast.Sub, ast.Add))), \
                    fn.name

    def test_no_math_in_service(self):
        for node in ast.walk(_tree("strategy_service.py")):
            assert not (isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Mult, ast.Div, ast.Sub, ast.Add,
                          ast.Pow, ast.Mod)))

    def test_ui_renders_only_no_business_logic(self):
        src = Path(UI_TEMPLATE).read_text(encoding="utf-8")
        for banned in ("toFixed", "parseFloat", "parseInt", "Number(",
                       "Math.", "innerHTML", "eval(", "new Function",
                       ".sort(", ".reverse("):
            assert banned not in src, banned


# ── 5. Import / çağrı denetimi (AST zorunlu) ─────────────────────────

class TestImportAudit:
    BANNED = ("requests", "websocket", "websockets", "socket",
              "subprocess", "threading", "multiprocessing", "pickle",
              "marshal", "ctypes", "tempfile", "shutil", "importlib",
              "urllib", "http", "flask", "binance", "ccxt", "os",
              "sys", "uuid", "time", "datetime", "random", "secrets")

    @pytest.mark.parametrize("path", STRATEGY_MODULES)
    def test_no_banned_imports(self, path):
        for node in ast.walk(_tree(path)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                assert n.split(".")[0] not in self.BANNED, (path, n)

    @pytest.mark.parametrize("path", STRATEGY_MODULES)
    def test_no_dynamic_execution_or_writes(self, path):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else "")
            assert name not in ("eval", "exec", "compile", "__import__",
                                "system", "popen", "fork", "spawn",
                                "open", "write_text", "write_bytes",
                                "unlink", "mkdir", "remove",
                                "rename"), (path, name)

    @pytest.mark.parametrize("path", STRATEGY_MODULES)
    def test_no_persistence_identifiers(self, path):
        for node in ast.walk(_tree(path)):
            name = ""
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            low = name.lower()
            for banned in ("append_snapshot", "workspace", "timeline",
                           "thread", "popen"):
                assert banned not in low, (path, name)

    @pytest.mark.parametrize("path", STRATEGY_MODULES)
    def test_no_request_object_usage(self, path):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Name):
                assert node.id != "request", path

    @pytest.mark.parametrize("path", ("strategy_intelligence.py",
                                      "strategy_service.py"))
    def test_no_uuid_or_clock_outside_api(self, path):
        src = Path(path).read_text(encoding="utf-8")
        for banned in ("uuid4", "uuid.", "time.time", "datetime.now",
                       "utcnow", "monotonic", "perf_counter",
                       "random."):
            assert banned not in src, (path, banned)

    def test_export_no_uuid_or_clock(self):
        src = Path("strategy_export.py").read_text(encoding="utf-8")
        for banned in ("uuid4", "time.time", "datetime.now", "utcnow",
                       "random."):
            assert banned not in src, banned

    def test_no_exchange_or_order_symbols_in_modules(self):
        for path in STRATEGY_MODULES:
            low = Path(path).read_text(encoding="utf-8").lower()
            for banned in ("binance", "new_order", "create_order",
                           "cancel_order", "leverage_change",
                           "futures_create", "post(", "put(",
                           "delete("):
                assert banned not in low, (path, banned)


# ── 6. Salt-okunurluk / kalıcılık yokluğu ────────────────────────────

class TestReadOnly:
    def test_full_stack_request_performs_no_file_writes(
            self, client, monkeypatch):
        _wire(monkeypatch)
        _login(client)
        real_open = builtins.open
        writes = []

        def guard(file, mode="r", *a, **k):
            if any(m in str(mode) for m in ("w", "a", "x", "+")):
                writes.append((str(file), str(mode)))
            return real_open(file, mode, *a, **k)
        monkeypatch.setattr(builtins, "open", guard)
        for route in API_ROUTES + ("/strategy-intelligence",):
            assert client.get(route).status_code == 200
        assert writes == []

    def test_real_provider_path_performs_no_file_writes(
            self, client, monkeypatch):
        """GERÇEK 1700→1800 zinciri (risk_api.summary dahil):
        strateji istekleri snapshot append/dosya yazımı yapmaz."""
        import risk_api

        monkeypatch.setattr(risk_api, "_account", lambda: {
            "usdt_margin_balance": "1000",
            "usdt_available_balance": "400"})
        monkeypatch.setattr(risk_api, "_active_positions", lambda: [
            {"symbol": "BTCUSDT", "side": "LONG", "quantity": "0.004",
             "entry_price": "100000", "mark_price": "100000",
             "leverage": "1", "unrealized_pnl": "0"}])
        monkeypatch.setattr(risk_api, "_open_orders_count", lambda: 0)

        class _Snap:
            def get_snapshot(self):
                return {"account": {"usdt_margin_balance": "1000",
                                    "usdt_available_balance": "400"},
                        "positions": []}
        import intelligence_service
        monkeypatch.setattr(intelligence_service, "IntelligenceService",
                            lambda: _Snap())

        appended = []
        monkeypatch.setattr(risk_api, "_append_snapshot",
                            lambda snap: appended.append(snap))
        real_open = builtins.open
        writes = []

        def guard(file, mode="r", *a, **k):
            if any(m in str(mode) for m in ("w", "a", "x", "+")):
                writes.append((str(file), str(mode)))
            return real_open(file, mode, *a, **k)
        _login(client)
        monkeypatch.setattr(builtins, "open", guard)
        for route in API_ROUTES:
            assert client.get(route).status_code == 200
        assert appended == []
        assert writes == []

    def test_true_default_chain_no_snapshot_append(self, monkeypatch):
        """IntelligenceService STUB'LANMADAN gerçek varsayılan zincir:
        risk_api._append_snapshot HİÇ çağrılmaz (Agent 07 bulgusuyla
        kapatılan persist=True sızıntısının kalıcı regresyon kilidi).

        Yalnız ağ/veri kaynakları çevrimdışı beslenir; iç çağrı grafiği
        (get_snapshot → risk sağlayıcı → summary) gerçek koddur.
        """
        import risk_api

        monkeypatch.setattr(risk_api, "_account", lambda: {
            "usdt_margin_balance": "1000",
            "usdt_available_balance": "400"})
        monkeypatch.setattr(risk_api, "_active_positions", lambda: [
            {"symbol": "BTCUSDT", "side": "LONG", "quantity": "0.004",
             "entry_price": "100000", "mark_price": "100000",
             "leverage": "1", "unrealized_pnl": "0"}])
        monkeypatch.setattr(risk_api, "_open_orders_count", lambda: 0)
        import dashboard_api
        monkeypatch.setattr(dashboard_api, "global_account", lambda: {
            "ok": True, "account": {"usdt_margin_balance": "1000",
                                    "usdt_available_balance": "400"}})
        monkeypatch.setattr(dashboard_api, "global_positions",
                            lambda: {"ok": True, "positions": []})

        appended = []
        monkeypatch.setattr(risk_api, "_append_snapshot",
                            lambda snap: appended.append(snap))
        real_open = builtins.open
        writes = []

        def guard(file, mode="r", *a, **k):
            if any(m in str(mode) for m in ("w", "a", "x", "+")):
                writes.append((str(file), str(mode)))
            return real_open(file, mode, *a, **k)
        monkeypatch.setattr(builtins, "open", guard)
        p = ssv.analyze_strategy(ssv.build_default_strategy_providers())
        assert p["strategy_version"] == 1
        assert appended == []                   # snapshot append YOK
        assert writes == []                     # dosya yazımı YOK

    def test_portfolio_default_chain_risk_readonly_source(self):
        src = Path("portfolio_service.py").read_text(encoding="utf-8")
        assert "risk_api.summary(persist=False)" in src
        assert "risk_api.summary()" not in src
        # varsayılan (persist=True) IntelligenceService kurucusu
        # salt-okunur zincirde ÇIPLAK kullanılamaz:
        assert "IntelligenceService()" not in src

    def test_core_and_export_no_open_calls_dynamic(self, monkeypatch):
        real_open = builtins.open
        opens = []

        def guard(file, mode="r", *a, **k):
            opens.append(str(file))
            return real_open(file, mode, *a, **k)
        monkeypatch.setattr(builtins, "open", guard)
        p = score.build_strategy(_analysis())
        sx.export_strategy_json({**p, "proposal_id": None,
                                 "generated_at": None})
        assert opens == []


# ── 7. Yürütme yüzeyi yokluğu ────────────────────────────────────────

class TestNoExecutionSurface:
    def test_api_get_only_unsupported_methods_rejected(self, client):
        _login(client)
        for route in API_ROUTES:
            for method in ("post", "put", "patch", "delete"):
                assert getattr(client, method)(route).status_code == \
                    405, (route, method)

    def test_no_execution_fields_anywhere_in_proposal(self, client,
                                                      monkeypatch):
        _wire(monkeypatch)
        _login(client)
        text = client.get(API_ROUTES[0]).get_data(as_text=True).lower()
        for banned in ('"quantity"', '"qty"', '"price"', '"order',
                       '"execute', '"side"', '"leverage"',
                       '"stop_loss"', '"take_profit"'):
            assert banned not in text, banned

    def test_core_schema_has_no_execution_keys(self):
        p = score.build_strategy(_analysis())
        dumped = json.dumps(p).lower()
        for banned in ("order_type", "market_order", "limit_order",
                       "execute", "submit"):
            assert banned not in dumped, banned

    def test_ui_has_no_execution_controls(self):
        low = Path(UI_TEMPLATE).read_text(encoding="utf-8").lower()
        for banned in ("<form", "<input", "<select", "<textarea",
                       "order_submit", "execute(", "submit("):
            assert banned not in low, banned
        assert "<button" not in low

    def test_route_fuzzing_no_hidden_surface(self, client):
        _login(client)
        for path in ("/api/strategy/intelligence/run",
                     "/api/strategy/intelligence/execute",
                     "/api/strategy/intelligence/apply",
                     "/api/strategy/intelligence/%2e%2e/secrets",
                     "/api/v1/strategy/intelligence/x"):
            r = client.get(path)
            assert r.status_code in (301, 308, 404), (path,
                                                      r.status_code)


# ── 8. Sterile hata modeli / sızıntı yasağı ──────────────────────────

class TestSterileErrors:
    def test_auth_bypass_attempts_fail(self, client):
        for route in API_ROUTES:
            assert client.get(route).status_code == 401, route
            assert client.get(
                route, headers={"X-Forwarded-For": "127.0.0.1",
                                "Authorization": "Bearer sahte",
                                "Cookie": "session=sahte"}
            ).status_code == 401, route

    def test_provider_exception_text_never_leaks(self, client,
                                                 monkeypatch):
        _wire(monkeypatch, {"portfolio_analysis":
                            lambda: (_ for _ in ()).throw(RuntimeError(
                                "/home/runner/gizli.pem "
                                "BINANCE_API_KEY"))})
        _login(client)
        for route in API_ROUTES:
            r = client.get(route)
            assert r.status_code == 200  # dürüst UNAVAILABLE
            body = r.get_data(as_text=True)
            for leak in ("gizli.pem", "/home/runner",
                         "BINANCE_API_KEY", "RuntimeError",
                         "Traceback"):
                assert leak not in body, (route, leak)

    def test_unexpected_error_sterile_500(self, client, monkeypatch):
        monkeypatch.setattr(
            ssv, "analyze_strategy",
            lambda *a, **k: (_ for _ in ()).throw(
                ValueError("beklenmeyen ic durum")))
        _login(client)
        for route in API_ROUTES:
            r = client.get(route)
            assert r.status_code == 500
            assert r.get_json()["error"]["code"] == \
                "STRATEGY_ANALYSIS_ERROR"
            assert "beklenmeyen" not in r.get_data(as_text=True)

    def test_responses_do_not_leak_env_secret_values(self, client,
                                                     monkeypatch):
        import os
        _wire(monkeypatch)
        _login(client)
        secrets = [os.environ.get(k) for k in (
            "BINANCE_API_KEY", "BINANCE_API_SECRET",
            "BINANCE_TRADING_API_KEY", "BINANCE_TRADING_API_SECRET",
            "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET",
            "SESSION_SECRET", "ALPHA_OWNER_PASSWORD_HASH")]
        for route in API_ROUTES + ("/strategy-intelligence",):
            body = client.get(route).get_data(as_text=True)
            for val in secrets:
                if val:
                    assert val not in body, route

    def test_core_errors_are_code_only(self):
        for bad in (None, [], {"analysis_version": 2, "status": "OK",
                               "portfolio": {}}):
            with pytest.raises(ValueError) as e:
                score.build_strategy(bad)
            msg = str(e.value)
            assert msg in (score.ERROR_INVALID_INPUT,
                           score.ERROR_FLOAT_REJECTED), msg
            assert " " not in msg  # yalnız kod, açıklama metni yok

    def test_export_errors_are_code_only(self):
        with pytest.raises(sx.ExportError) as e:
            sx.export_strategy_dict({"gizli_yol": "/etc/passwd"})
        assert str(e.value) == sx.CODE_PROPOSAL_UNAVAILABLE
        assert "gizli_yol" not in str(e.value)

    def test_no_provider_module_names_in_api_response(self, client,
                                                      monkeypatch):
        _wire(monkeypatch)
        _login(client)
        text = client.get(API_ROUTES[0]).get_data(as_text=True)
        for banned in ("strategy_service", "portfolio_service",
                       "risk_api", "intelligence_service",
                       "strategy_intelligence.py"):
            assert banned not in text, banned


# ── 9. Değişmezlik / determinizm ─────────────────────────────────────

class TestImmutabilityDeterminism:
    def test_core_deterministic_byte_identical(self):
        a = json.dumps(score.build_strategy(_analysis()),
                       sort_keys=True)
        b = json.dumps(score.build_strategy(_analysis()),
                       sort_keys=True)
        assert a == b

    def test_service_deterministic(self):
        a = ssv.analyze_strategy(_providers())
        b = ssv.analyze_strategy(_providers())
        assert a == b

    def test_export_byte_identical(self):
        p = {**score.build_strategy(_analysis()),
             "proposal_id": "sabit", "generated_at": "2026-07-27"}
        assert sx.export_strategy_json(p) == sx.export_strategy_json(
            copy.deepcopy(p))

    def test_no_hidden_mutable_state_across_calls(self):
        first = ssv.analyze_strategy(_providers())
        first["recommendations"].append({"sahte": True})
        first["warnings"].append("SAHTE")
        second = ssv.analyze_strategy(_providers())
        assert {"sahte": True} not in second["recommendations"]
        assert "SAHTE" not in second["warnings"]

    def test_input_envelope_never_mutated_by_stack(self):
        env = _analysis()
        snapshot = copy.deepcopy(env)
        p = ssv.analyze_strategy(
            {"portfolio_analysis":
             lambda: {"freshness": "stale", "data": env}})
        sx.export_strategy_dict({**{k: v for k, v in p.items()
                                    if k != "sources"},
                                 "proposal_id": None,
                                 "generated_at": None})
        assert env == snapshot

    def test_meta_only_at_api_boundary(self, client, monkeypatch):
        core_p = score.build_strategy(_analysis())
        service_p = ssv.analyze_strategy(_providers())
        assert "proposal_id" not in core_p
        assert "generated_at" not in core_p
        assert "proposal_id" not in service_p
        assert "generated_at" not in service_p
        _wire(monkeypatch)
        _login(client)
        api_p = client.get(API_ROUTES[0]).get_json()
        assert api_p["proposal_id"] and api_p["generated_at"]

    def test_unknown_stays_null_never_zero(self):
        p = ssv.analyze_strategy({"portfolio_analysis":
                                  lambda: (_ for _ in ()).throw(
                                      OSError())})
        assert p["confidence"] is None
        assert p["overall_risk"] is None
        assert p["recommendations"] == []

    def test_float_rejected_end_to_end(self):
        env = _analysis()
        env["portfolio"]["exposure"]["gross_pct"] = 90.0
        p = ssv.analyze_strategy(_providers(analysis=env))
        assert p["data_quality"] == "UNAVAILABLE"  # sessiz kabul YOK

    def test_no_float_literals_in_core(self):
        for node in ast.walk(_tree("strategy_intelligence.py")):
            if isinstance(node, ast.Constant) and \
                    isinstance(node.value, float):
                pytest.fail(f"core'da float sabiti: {node.value}")

    def test_envelope_numbers_are_strings_no_floats(self):
        p = ssv.analyze_strategy(_providers())

        def walk(node):
            if isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
            else:
                assert not isinstance(node, float), node
        walk(p)


# ── 10. UI güvenliği ─────────────────────────────────────────────────

class TestUiSecurity:
    def test_textcontent_only_no_dom_injection(self):
        src = Path(UI_TEMPLATE).read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                       "document.write", "srcdoc", "| safe", "|safe"):
            assert banned not in src, banned
        assert "textContent" in src

    def test_ui_fetches_strategy_api_only(self):
        import re
        src = Path(UI_TEMPLATE).read_text(encoding="utf-8")
        assert re.findall(r'fetch\("([^"]+)"', src) == \
            ["/api/v1/strategy/intelligence"]

    def test_xss_payload_in_instrument_json_escaped(self, client,
                                                    monkeypatch):
        evil = '<script>alert(1)</script>"</td><img src=x onerror=1>'
        env = _analysis()
        env["portfolio"]["concentration"]["top_symbol"] = evil
        env["portfolio"]["allocation"]["assets"] = [{"symbol": evil}]
        _wire(monkeypatch, _providers(analysis=env))
        _login(client)
        r = client.get(API_ROUTES[1])
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("application/json")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        p = json.loads(r.get_data(as_text=True))
        instruments = [rec["instrument"]
                       for rec in p["recommendations"]]
        assert evil in instruments  # string kalır, HTML'e dönüşmez


# ── 11. Secret taraması (statik) ─────────────────────────────────────

class TestSecretScan:
    MARKERS = ("api_key", "api_secret", "apikey", "private key",
               "begin rsa", "mnemonic", "session_secret", "set-cookie")

    @pytest.mark.parametrize("path",
                             STRATEGY_MODULES + (UI_TEMPLATE,))
    def test_no_hardcoded_secret_material(self, path):
        low = Path(path).read_text(encoding="utf-8").lower()
        for marker in self.MARKERS:
            assert marker not in low, (path, marker)
