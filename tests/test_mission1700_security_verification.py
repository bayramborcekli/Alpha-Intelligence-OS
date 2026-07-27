"""Mission 1700 / Agent 07 — Güvenlik Doğrulama testleri.

Portfolio Intelligence yığınının (Core, Service, API, UI, Export)
güvenlik garantilerini kanıtlayan statik AST analizi, import denetimi,
secret taraması, salt-okunurluk denetimi ve penetrasyon senaryoları.
Yeni özellik yoktur — yalnız doğrulama.
"""

from __future__ import annotations

import ast
import builtins
import json
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import portfolio_export as pex
import portfolio_intelligence as pcore
import portfolio_service as psv

PASSWORD = "portfolio-sec-parola-1"
HASH = generate_password_hash(PASSWORD)

PORTFOLIO_MODULES = ("portfolio_intelligence.py", "portfolio_service.py",
                     "portfolio_export.py")
UI_TEMPLATE = "templates/portfolio_intelligence.html"
GEN_AT = "2026-07-27T00:00:00+00:00"

API_ROUTES = ("/api/portfolio/intelligence",
              "/api/v1/portfolio/intelligence",
              "/api/portfolio/intelligence/export/json",
              "/api/v1/portfolio/intelligence/export/json",
              "/api/portfolio/intelligence/export/csv",
              "/api/v1/portfolio/intelligence/export/csv")


def _providers(**overrides):
    base = {
        "equity": lambda: {"freshness": "fresh", "data": {
            "nav_usdt": "1000", "cash_usdt": "400",
            "realized_pnl": None, "unrealized_pnl": None,
            "total_fees": None}},
        "positions": lambda: {"freshness": "fresh", "data": [
            {"symbol": "BTCUSDT", "side": "LONG", "quantity": "0.004",
             "entry_price": "100000", "mark_price": "100000",
             "leverage": "1"}]},
        "risk": lambda: {"freshness": "fresh", "data": {
            "drawdown_pct": "2",
            "thresholds": {"max_net_exposure_pct": "200",
                           "max_drawdown_pct": "5",
                           "max_concentration_pct": "80"}}},
    }
    base.update(overrides)
    return base


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
                       "/tmp/test_m1700sec_attempts.db")
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
    monkeypatch.setattr(psv, "build_default_providers", lambda: fixed)


# ── 4. Mimari sınırlar ──────────────────────────────────────────────

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
        mods = self._imports("portfolio_intelligence.py")
        assert mods <= {"decimal", "typing", "__future__"}, mods

    def test_service_imports_only_core_downward(self):
        mods = self._imports("portfolio_service.py")
        # Aşağı yön: yalnız core; sağlayıcı modülleri tembel/enjekte.
        for banned in ("app", "flask", "portfolio_export", "auth"):
            assert banned not in mods, banned
        assert "portfolio_intelligence" in mods

    def test_export_imports_neither_core_nor_service(self):
        mods = self._imports("portfolio_export.py")
        assert mods <= {"csv", "io", "json", "typing", "__future__"}, mods

    def test_no_circular_dependencies(self):
        # core hiçbir mission modülünü, export hiçbirini, service yalnız
        # core'u import eder → çevrim imkânsız (yukarıda kanıtlı).
        core = self._imports("portfolio_intelligence.py")
        assert not core & {"portfolio_service", "portfolio_export", "app"}

    def test_ui_renders_only_no_business_logic(self):
        src = Path(UI_TEMPLATE).read_text(encoding="utf-8")
        for banned in ("toFixed", "parseFloat", "Number(", "Math.",
                       "innerHTML", "eval(", "new Function"):
            assert banned not in src, banned

    def test_api_layer_routing_only(self):
        # Portfolio route fonksiyonları hesap yapmaz: gövdelerinde
        # aritmetik işlem yok, yalnız servis/export çağrısı + yanıt.
        funcs = [n for n in ast.walk(_tree("app.py"))
                 if isinstance(n, ast.FunctionDef)
                 and (n.name.startswith("api_portfolio_intelligence")
                      or n.name == "_portfolio_intelligence_export")]
        assert len(funcs) >= 4
        for fn in funcs:
            for node in ast.walk(fn):
                assert not (isinstance(node, ast.BinOp) and isinstance(
                    node.op, (ast.Mult, ast.Div, ast.Sub, ast.Add))), \
                    fn.name


# ── 6. Import denetimi (AST zorunlu) ────────────────────────────────

class TestImportAudit:
    BANNED = ("requests", "websocket", "websockets", "socket",
              "subprocess", "pickle", "marshal", "ctypes", "tempfile",
              "shutil", "importlib", "urllib", "http")

    @pytest.mark.parametrize("path", PORTFOLIO_MODULES)
    def test_no_banned_imports(self, path):
        for node in ast.walk(_tree(path)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                assert n.split(".")[0] not in self.BANNED, (path, n)

    @pytest.mark.parametrize("path", PORTFOLIO_MODULES)
    def test_no_dynamic_execution_or_writes(self, path):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else "")
            assert name not in ("eval", "exec", "compile", "__import__",
                                "system", "popen", "fork", "spawn",
                                "write_text", "write_bytes", "unlink",
                                "mkdir", "remove", "rename"), (path, name)
            if name == "open":
                # open() yalnız okuma modunda olabilir — hiç yok, daha iyi
                modes = [getattr(a, "value", "") for a in node.args[1:]]
                modes += [getattr(kw.value, "value", "")
                          for kw in node.keywords if kw.arg == "mode"]
                assert not any("w" in str(m) or "a" in str(m) or
                               "+" in str(m) for m in modes), path

    @pytest.mark.parametrize("path", ("intelligence_service.py",
                                      "risk_api.py"))
    def test_transitive_provider_modules_no_dynamic_execution(self, path):
        # Varsayılan sağlayıcıların geçişli bağımlılıkları da dinamik
        # kod çalıştırmaz. (Bu modüllerin kendi meşru dosya/veri erişimi
        # kendi mission'larında denetlenmiştir; buradaki kapsam dinamik
        # yürütme yasağıdır.)
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Name):
                # yalın yerleşikler (re.compile gibi nitelikli meşru
                # kullanımlar hariç)
                assert fn.id not in ("eval", "exec", "compile",
                                     "__import__"), (path, fn.id)
            elif isinstance(fn, ast.Attribute):
                assert fn.attr not in ("system", "popen", "fork",
                                       "spawn"), (path, fn.attr)
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(
                    node, ast.Import) else [node.module or ""]
                for m in mods:
                    assert m.split(".")[0] not in (
                        "subprocess", "pickle", "marshal", "ctypes"), \
                        (path, m)

    @pytest.mark.parametrize("path", PORTFOLIO_MODULES)
    def test_no_request_object_usage(self, path):
        # AST tabanlı: flask import'u ve request tanımlayıcısı hiç yok
        # (docstring'lerdeki açıklama metni sayılmaz).
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(
                    node, ast.Import) else [node.module or ""]
                assert not any(m.split(".")[0] == "flask"
                               for m in mods), path
            if isinstance(node, ast.Name):
                assert node.id != "request", path


# ── 5. Salt-okunurluk garantileri ───────────────────────────────────

class TestReadOnly:
    @pytest.mark.parametrize("path", PORTFOLIO_MODULES + ("app.py",))
    def test_no_append_snapshot_anywhere_in_stack(self, path):
        src = Path(path).read_text(encoding="utf-8")
        if path == "app.py":
            # Portfolio bloklarında append_snapshot geçmez.
            for fname in ("api_portfolio_intelligence",
                          "_portfolio_intelligence_export"):
                i = src.index(f"def {fname}")
                block = src[i:src.index("\n@app.", i)
                            if "\n@app." in src[i:] else len(src)]
                assert "append_snapshot" not in block, fname
            return
        # AST tabanlı: append_snapshot tanımlayıcısı kodda hiç
        # kullanılmaz (docstring'deki "yok" beyanı sayılmaz).
        for node in ast.walk(_tree(path)):
            name = ""
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                name = " ".join(a.name for a in node.names)
            assert "append_snapshot" not in name, path

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
        for route in API_ROUTES + ("/portfolio-intelligence",):
            assert client.get(route).status_code == 200
        assert writes == []

    def test_real_provider_path_performs_no_file_writes(
            self, client, monkeypatch):
        """GERÇEK build_default_providers zinciri (risk_api.summary
        dahil) ile: portföy istekleri hiçbir dosya yazmaz.

        Risk Engine iç okumaları (hesap/pozisyon) çevrimdışı ortamda
        veri döndürecek şekilde beslenir ki summary() snapshot-append
        koşuluna kadar İLERLEYEBİLSİN — persist=False bunu engellemeli.
        """
        import risk_api

        monkeypatch.setattr(risk_api, "_account", lambda: {
            "usdt_margin_balance": "1000", "usdt_available_balance":
            "400"})
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
        assert appended == []                   # snapshot append YOK
        assert writes == []                     # dosya yazımı YOK

    def test_risk_summary_persist_false_skips_snapshot(self,
                                                       monkeypatch):
        import risk_api
        monkeypatch.setattr(risk_api, "_account", lambda: {
            "usdt_margin_balance": "1000",
            "usdt_available_balance": "400"})
        monkeypatch.setattr(risk_api, "_active_positions", lambda: [])
        monkeypatch.setattr(risk_api, "_open_orders_count", lambda: 0)
        appended = []
        monkeypatch.setattr(risk_api, "_append_snapshot",
                            lambda snap: appended.append(snap))
        out_ro = risk_api.summary(persist=False)
        assert appended == []
        risk_api.summary()                      # varsayılan davranış
        if out_ro.get("risk_score") is not None:
            assert appended, "persist=True snapshot yazmalıydı"

    def test_portfolio_service_uses_read_only_summary(self):
        src = Path("portfolio_service.py").read_text(encoding="utf-8")
        assert "risk_api.summary(persist=False)" in src
        assert "risk_api.summary()" not in src

    def test_no_exchange_or_order_symbols_in_modules(self):
        for path in PORTFOLIO_MODULES:
            low = Path(path).read_text(encoding="utf-8").lower()
            for banned in ("binance", "new_order", "create_order",
                           "cancel_order", "leverage_change",
                           "futures_create", "post(", "put(", "delete("):
                assert banned not in low, (path, banned)

    def test_risk_engine_not_mutated_by_service_mapping(self):
        src = Path("portfolio_service.py").read_text(encoding="utf-8")
        # risk_api yalnız thresholds()/summary() okuma uçlarıyla anılır
        for banned in ("set_", "update_", "write", "save", "persist"):
            assert f"risk_api.{banned}" not in src, banned


# ── 7. Veri güvenliği ───────────────────────────────────────────────

class TestDataSafety:
    def test_unknown_stays_null_never_zero(self):
        env = psv.get_portfolio_analysis(
            _providers(risk=lambda: (_ for _ in ()).throw(
                RuntimeError("x"))), GEN_AT)
        perf = env["portfolio"]["performance"]
        assert perf["realized_pnl"] is None
        assert perf["total_fees"] is None
        assert perf["drawdown_pct"] is None
        assert env["portfolio"]["risk_utilization"][
            "drawdown_util_pct"] is None

    def test_float_rejected_by_core_via_service(self):
        env = psv.get_portfolio_analysis(
            _providers(equity=lambda: {"freshness": "fresh", "data": {
                "nav_usdt": 1000.5, "cash_usdt": "400",
                "realized_pnl": None, "unrealized_pnl": None,
                "total_fees": None}}), GEN_AT)
        # float sızıntısı sessizce kabul edilmez: kaynak düşer/temizlenir
        assert env["status"] in ("PARTIAL", "UNAVAILABLE")

    def test_no_float_literals_in_money_paths(self):
        for node in ast.walk(_tree("portfolio_intelligence.py")):
            if isinstance(node, ast.Constant) and \
                    isinstance(node.value, float):
                pytest.fail(f"core'da float sabiti: {node.value}")

    def test_envelope_numbers_are_strings(self):
        env = psv.get_portfolio_analysis(_providers(), GEN_AT)

        def walk(node):
            if isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
            else:
                assert not isinstance(node, float), node
        walk(env)

    def test_export_does_not_mutate_payload(self):
        env = psv.get_portfolio_analysis(_providers(), GEN_AT)
        snap = json.dumps(env, sort_keys=True)
        pex.export_analysis(env, "json")
        pex.export_analysis(env, "csv")
        assert json.dumps(env, sort_keys=True) == snap


# ── 8. API güvenliği ────────────────────────────────────────────────

class TestApiSecurity:
    def test_get_only_unsupported_methods_rejected(self, client):
        _login(client)
        for route in API_ROUTES:
            for method in ("post", "put", "patch", "delete"):
                assert getattr(client, method)(route).status_code == 405, \
                    (route, method)

    def test_auth_bypass_attempts_fail(self, client):
        for route in API_ROUTES:
            assert client.get(route).status_code == 401, route
            assert client.get(
                route, headers={"X-Forwarded-For": "127.0.0.1",
                                "Authorization": "Bearer sahte",
                                "Cookie": "session=sahte"}
            ).status_code == 401, route

    def test_route_fuzzing_no_hidden_surface(self, client):
        _login(client)
        for path in ("/api/portfolio/intelligence/export/xml",
                     "/api/portfolio/intelligence/export/",
                     "/api/portfolio/intelligence/export",
                     "/api/portfolio/intelligence/%2e%2e/secrets",
                     "/api/portfolio/intelligence/run",
                     "/api/v1/portfolio/intelligence/export/json/x"):
            r = client.get(path)
            assert r.status_code in (301, 308, 404), (path, r.status_code)

    def test_provider_exception_text_never_leaks(self, client,
                                                 monkeypatch):
        _wire(monkeypatch, _providers(
            risk=lambda: (_ for _ in ()).throw(
                RuntimeError("/home/runner/gizli.pem BINANCE_API_KEY"))))
        _login(client)
        for route in API_ROUTES:
            body = client.get(route).get_data(as_text=True)
            for leak in ("gizli.pem", "/home/runner", "BINANCE_API_KEY",
                         "RuntimeError", "Traceback"):
                assert leak not in body, (route, leak)

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
        for route in API_ROUTES + ("/portfolio-intelligence",):
            body = client.get(route).get_data(as_text=True)
            for val in secrets:
                if val:
                    assert val not in body, route

    def test_unexpected_status_from_core_stays_sterile(self, client,
                                                       monkeypatch):
        _wire(monkeypatch)
        monkeypatch.setattr(
            psv, "get_portfolio_analysis",
            lambda *a, **k: (_ for _ in ()).throw(
                ValueError("beklenmeyen ic durum")))
        _login(client)
        for route in API_ROUTES:
            r = client.get(route)
            assert r.status_code == 500
            assert r.get_json()["error"]["code"] == \
                "PORTFOLIO_ANALYSIS_ERROR"
            assert "beklenmeyen" not in r.get_data(as_text=True)


# ── 9. UI güvenliği ─────────────────────────────────────────────────

class TestUiSecurity:
    def test_no_execution_controls_or_buy_sell(self):
        src = Path(UI_TEMPLATE).read_text(encoding="utf-8")
        low = src.lower()
        # Not: şablondaki "Canlı emir: DEVRE DIŞI / AL-SAT yok" gibi
        # NEGATİF beyan metinleri kontrol dışıdır; burada gerçek kontrol
        # yüzeyi (form/giriş/buton/JS eylemi) denetlenir.
        for banned in ("<form", "<input", "<select", "<textarea",
                       "buy", "sell", "order_submit", "execute(",
                       "submit("):
            assert banned not in low, banned
        assert "<button" not in low  # kendi butonu yok; global ⟳ kullanır

    def test_textcontent_only_no_dom_injection(self):
        src = Path(UI_TEMPLATE).read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                       "document.write", "srcdoc"):
            assert banned not in src, banned
        assert "textContent" in src

    def test_xss_payload_in_symbol_is_json_escaped(self, client,
                                                   monkeypatch):
        evil = '<script>alert(1)</script>"</td><img src=x onerror=1>'
        _wire(monkeypatch, _providers(
            positions=lambda: {"freshness": "fresh", "data": [
                {"symbol": evil, "side": "LONG", "quantity": "1",
                 "entry_price": "10", "mark_price": "10",
                 "leverage": "1"}]}))
        _login(client)
        r = client.get("/api/v1/portfolio/intelligence")
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("application/json")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        # JSON enjeksiyonu yok: gövde geçerli JSON, payload string kalır
        env = json.loads(r.get_data(as_text=True))
        syms = [p["symbol"] for p in env["portfolio"]["positions"]]
        assert evil in syms


# ── 10. Export güvenliği + determinizm ──────────────────────────────

class TestExportSecurityDeterminism:
    def test_json_and_csv_deterministic_bytes(self):
        env1 = psv.get_portfolio_analysis(_providers(), GEN_AT)
        env2 = psv.get_portfolio_analysis(_providers(), GEN_AT)
        assert pex.export_analysis(env1, "json")[1] == \
            pex.export_analysis(env2, "json")[1]
        assert pex.export_analysis(env1, "csv")[1] == \
            pex.export_analysis(env2, "csv")[1]

    def test_formula_injection_neutralized_end_to_end(self, client,
                                                      monkeypatch):
        _wire(monkeypatch, _providers(
            positions=lambda: {"freshness": "fresh", "data": [
                {"symbol": "=cmd|'/c calc'!A0", "side": "LONG",
                 "quantity": "1", "entry_price": "10",
                 "mark_price": "10", "leverage": "1"}]}))
        _login(client)
        body = client.get(
            "/api/v1/portfolio/intelligence/export/csv").get_data()
        text = body.decode("utf-8")
        assert "'=cmd" in text and "\r\n=cmd" not in text

    def test_large_decimal_and_negative_values_survive(self):
        env = psv.get_portfolio_analysis(_providers(
            equity=lambda: {"freshness": "fresh", "data": {
                "nav_usdt": "123456789012345678.12345678",
                "cash_usdt": "-0.00000001",
                "realized_pnl": None, "unrealized_pnl": None,
                "total_fees": None}}), GEN_AT)
        _, body, _, _ = pex.export_analysis(env, "json")
        decoded = json.loads(body.decode("utf-8"))
        nav = decoded["portfolio"]["equity"]["nav_usdt"]
        assert isinstance(nav, str) and nav.startswith("123456789012345")
        assert "e" not in nav.lower()           # bilimsel gösterim yok

    def test_no_random_uuid_or_hidden_timestamps(self):
        for path in PORTFOLIO_MODULES:
            src = Path(path).read_text(encoding="utf-8")
            for banned in ("random", "uuid", "time.time", "datetime.now",
                           "utcnow", "monotonic", "perf_counter"):
                assert banned not in src, (path, banned)

    def test_malformed_provider_payloads_sterile(self):
        cases = [
            {"positions": lambda: {"freshness": "fresh",
                                   "data": "bozuk"}},
            {"positions": lambda: {"yanlis": True}},
            {"equity": lambda: None},
            {"risk": lambda: {"freshness": "fresh",
                              "data": {"thresholds": "x"}}},
        ]
        for case in cases:
            env = psv.get_portfolio_analysis(_providers(**case), GEN_AT)
            assert env["status"] in ("PARTIAL", "UNAVAILABLE")
            dumped = json.dumps(env)
            for leak in ("Traceback", "bozuk", "yanlis"):
                assert leak not in dumped


# ── 11. Secret taraması (statik) ────────────────────────────────────

class TestSecretScan:
    MARKERS = ("api_key", "api_secret", "apikey", "private key",
               "begin rsa", "mnemonic", "session_secret", "set-cookie")

    @pytest.mark.parametrize("path",
                             PORTFOLIO_MODULES + (UI_TEMPLATE,))
    def test_no_hardcoded_secret_material(self, path):
        low = Path(path).read_text(encoding="utf-8").lower()
        for marker in self.MARKERS:
            assert marker not in low, (path, marker)
