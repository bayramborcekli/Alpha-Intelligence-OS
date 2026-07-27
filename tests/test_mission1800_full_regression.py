"""Mission 1800 / Agent 08 — Full Regression bütünleştirme testleri.

Strategy Intelligence yığınının katmanlar arası uçtan uca zincirleri
(Portfolio→Service→Core→Export→API→UI), geriye dönük yüzey uyumluluğu
(Mission 1500/1600/1700), rota çakışması, determinizm, salt-okunurluk,
sağlayıcı çağrı ekonomisi ve dağıtım duman testi. Üretim kodu
DEĞİŞMEZ — yalnız doğrulama.
"""

from __future__ import annotations

import builtins
import copy
import json
import time

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import strategy_export as sx
import strategy_intelligence as score
import strategy_service as ssv

PASSWORD = "full-regression-1800-parola"
HASH = generate_password_hash(PASSWORD)

STRATEGY_API = ("/api/strategy/intelligence",
                "/api/v1/strategy/intelligence")

PROPOSAL_FIELDS = (
    "strategy_version", "proposal_id", "generated_at", "advisory_only",
    "read_only", "portfolio_analysis_version", "confidence",
    "data_quality", "market_regime", "overall_risk", "recommendations",
    "warnings", "limitations")
RECOMMENDATION_FIELDS = (
    "recommendation_id", "instrument", "action", "reason_codes",
    "priority", "confidence", "current_weight", "target_weight",
    "risk_level", "expected_effect", "invalidation_conditions")


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


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB",
                       "/tmp/test_m1800fr_attempts.db")
    monkeypatch.setenv("ALPHA_AUTOMATION_STATE_PATH",
                       str(tmp_path / "automation_state.json"))
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


# ── Uçtan uca öneri üretimi ─────────────────────────────────────────

class TestEndToEndProposal:
    def test_full_chain_portfolio_to_api(self, client, monkeypatch):
        _wire(monkeypatch)
        _login(client)
        p = client.get(STRATEGY_API[1]).get_json()
        # API, servis zarfını meta ile taşır: 13 şema alanı + sources
        assert set(p) == set(PROPOSAL_FIELDS) | {"sources"}
        assert p["strategy_version"] == 1
        assert p["advisory_only"] is True and p["read_only"] is True
        assert p["market_regime"] == "UNKNOWN"
        assert p["proposal_id"] and p["generated_at"]
        for rec in p["recommendations"]:
            assert set(rec) == set(RECOMMENDATION_FIELDS)

    def test_repeated_identical_requests_stable_payload(self, client,
                                                        monkeypatch):
        _wire(monkeypatch)
        _login(client)
        bodies = [client.get(STRATEGY_API[0]).get_json()
                  for _ in range(3)]
        stripped = [{k: v for k, v in b.items()
                     if k not in ("proposal_id", "generated_at")}
                    for b in bodies]
        assert stripped[0] == stripped[1] == stripped[2]
        # meta her istekte API'de yeniden üretilir (tek üretim noktası)
        assert len({b["proposal_id"] for b in bodies}) == 3

    def test_api_proposal_export_compatible_roundtrip(self, client,
                                                      monkeypatch):
        """Export, API-uyumlu öneriyi tüketir (spec §5)."""
        _wire(monkeypatch)
        _login(client)
        api_p = client.get(STRATEGY_API[1]).get_json()
        env, body, mime, filename = sx.serialize_strategy(api_p)
        expected = {k: api_p[k] for k in PROPOSAL_FIELDS}
        assert env == expected                   # sources düşer
        assert json.loads(body.decode("utf-8")) == expected
        assert mime.startswith("application/json") and filename

    def test_service_envelope_exports_after_meta_attach(self):
        p = ssv.analyze_strategy(_providers(freshness="stale"))
        assert p["data_quality"] == "PARTIAL"          # bayat → PARTIAL
        exported = sx.export_strategy_dict(
            {**p, "proposal_id": "sabit", "generated_at": "2026-07-27"})
        assert "sources" not in exported               # meta düşer
        assert tuple(exported) == PROPOSAL_FIELDS

    def test_true_default_chain_reaches_portfolio_service(
            self, client, monkeypatch):
        """GERÇEK build_default_strategy_providers zinciri (mock YOK):
        API isteği portfolio_service.get_portfolio_analysis üzerinden
        core'a ulaşır — dikiş yeri birebir doğrulanır (spy sayacı)."""
        import portfolio_service as psv
        fixed = {
            "equity": lambda: {"freshness": "fresh", "data": {
                "nav_usdt": "1000", "cash_usdt": "400",
                "realized_pnl": "25", "unrealized_pnl": "-5",
                "total_fees": "3.5"}},
            "positions": lambda: {"freshness": "fresh", "data": [
                {"symbol": "BTCUSDT", "side": "LONG",
                 "quantity": "0.004", "entry_price": "100000",
                 "mark_price": "100000", "leverage": "1"}]},
            "risk": lambda: {"freshness": "fresh", "data": {
                "drawdown_pct": "2",
                "thresholds": {"max_net_exposure_pct": "200",
                               "max_drawdown_pct": "5",
                               "max_concentration_pct": "80"}}},
        }
        monkeypatch.setattr(psv, "build_default_providers",
                            lambda: fixed)
        calls = []
        real = psv.get_portfolio_analysis

        def spy(providers, generated_at):
            calls.append(True)
            return real(providers, generated_at)
        monkeypatch.setattr(psv, "get_portfolio_analysis", spy)
        _login(client)
        p = client.get(STRATEGY_API[1]).get_json()
        assert calls == [True]                   # dikiş tam 1 kez
        assert p["strategy_version"] == 1
        assert p["data_quality"] == "OK"
        assert p["portfolio_analysis_version"] == 1
        assert p["proposal_id"] and p["generated_at"]

    def test_recommendation_ordering_preserved_end_to_end(
            self, client, monkeypatch):
        _wire(monkeypatch)
        _login(client)
        api_p = client.get(STRATEGY_API[1]).get_json()
        core_p = score.build_strategy(_analysis())
        assert [r["recommendation_id"]
                for r in api_p["recommendations"]] == \
            [r["recommendation_id"] for r in core_p["recommendations"]]
        exported = sx.export_strategy_dict(api_p)
        assert [r["recommendation_id"]
                for r in exported["recommendations"]] == \
            [r["recommendation_id"] for r in api_p["recommendations"]]


# ── Katman doğrulaması ──────────────────────────────────────────────

class TestCrossLayer:
    def test_no_layer_bypass_api_uses_service_only(self, client,
                                                   monkeypatch):
        called = []
        real = ssv.analyze_strategy

        def spy(providers):
            called.append(True)
            return real(providers)
        monkeypatch.setattr(ssv, "analyze_strategy", spy)
        _wire(monkeypatch)
        _login(client)
        assert client.get(STRATEGY_API[0]).status_code == 200
        assert called == [True]

    def test_core_called_exactly_once_per_request(self, monkeypatch):
        """Hesap tekrarı yok: bir analiz → bir core çağrısı."""
        calls = []
        real = score.build_strategy

        def spy(analysis):
            calls.append(True)
            return real(analysis)
        monkeypatch.setattr(ssv, "build_strategy", spy, raising=False)
        monkeypatch.setattr(score, "build_strategy", spy)
        ssv.analyze_strategy(_providers())
        assert len(calls) == 1

    def test_provider_called_exactly_once(self):
        calls = {"n": 0}

        def provider():
            calls["n"] += 1
            return {"freshness": "fresh", "data": _analysis()}
        ssv.analyze_strategy({"portfolio_analysis": provider})
        assert calls["n"] == 1

    def test_no_mutation_between_layers(self):
        env = _analysis()
        snapshot = copy.deepcopy(env)
        p = ssv.analyze_strategy(_providers(analysis=env))
        p_snap = copy.deepcopy(p)
        sx.export_strategy_dict({**{k: v for k, v in p.items()
                                    if k != "sources"},
                                 "proposal_id": None,
                                 "generated_at": None})
        assert env == snapshot                  # girdi değişmedi
        assert p == p_snap                      # servis zarfı değişmedi

    def test_meta_only_at_api(self, client, monkeypatch):
        core_p = score.build_strategy(_analysis())
        svc_p = ssv.analyze_strategy(_providers())
        for p in (core_p, svc_p):
            assert "proposal_id" not in p
            assert "generated_at" not in p
        _wire(monkeypatch)
        _login(client)
        api_p = client.get(STRATEGY_API[0]).get_json()
        assert api_p["proposal_id"] and api_p["generated_at"]

    def test_ui_consumes_api_json_only(self):
        import re
        from pathlib import Path
        src = Path("templates/strategy_intelligence.html").read_text(
            encoding="utf-8")
        assert re.findall(r'fetch\("([^"]+)"', src) == \
            ["/api/v1/strategy/intelligence"]
        for banned in ("innerHTML", "Math.", "toFixed", ".sort("):
            assert banned not in src, banned

    def test_no_duplicated_serialization_in_api(self):
        from pathlib import Path
        src = Path("app.py").read_text(encoding="utf-8")
        i = src.index("def api_strategy_intelligence")
        block = src[i:src.index("\n@app.", i)]
        assert "export_strategy" not in block   # API export'u çağırmaz
        assert "json.dumps" not in block        # elle serileştirme yok


# ── Determinizm + şema ──────────────────────────────────────────────

class TestDeterminismSchema:
    def test_core_deterministic(self):
        assert score.build_strategy(_analysis()) == \
            score.build_strategy(_analysis())

    def test_export_byte_identical(self):
        p = {**score.build_strategy(_analysis()),
             "proposal_id": "sabit", "generated_at": "2026-07-27"}
        assert sx.export_strategy_json(p) == \
            sx.export_strategy_json(copy.deepcopy(p))

    def test_export_key_order_is_schema_order(self):
        p = {**score.build_strategy(_analysis()),
             "proposal_id": None, "generated_at": None}
        exported = sx.export_strategy_dict(p)
        assert tuple(exported) == PROPOSAL_FIELDS
        for rec in exported["recommendations"]:
            assert tuple(rec) == RECOMMENDATION_FIELDS

    def test_unknown_preserved_as_null_never_zero(self):
        p = ssv.analyze_strategy({"portfolio_analysis":
                                  lambda: (_ for _ in ()).throw(
                                      OSError())})
        assert p["confidence"] is None and p["overall_risk"] is None
        assert p["data_quality"] == "UNAVAILABLE"
        assert p["recommendations"] == []
        dumped = json.dumps(p)
        assert '"confidence": null' in dumped.replace('": null',
                                                      '": null')

    def test_null_preserved_through_export(self):
        p = ssv.analyze_strategy({"portfolio_analysis": lambda: None})
        exported = sx.export_strategy_dict(
            {**{k: v for k, v in p.items() if k != "sources"},
             "proposal_id": None, "generated_at": None})
        assert exported["confidence"] is None
        assert exported["overall_risk"] is None
        body = sx.export_strategy_json(
            {**{k: v for k, v in p.items() if k != "sources"},
             "proposal_id": None, "generated_at": None})
        assert b'"confidence": null' in body

    def test_decimal_strings_survive_full_chain(self, client,
                                                monkeypatch):
        _wire(monkeypatch)
        _login(client)
        api_p = client.get(STRATEGY_API[1]).get_json()
        for rec in api_p["recommendations"]:
            for key in ("current_weight", "target_weight"):
                assert not isinstance(rec[key], float), rec
        body = sx.serialize_strategy(api_p)[1]
        assert b"80.00" in body or api_p["recommendations"] == []

    def test_no_floats_anywhere_in_api_payload(self, client,
                                               monkeypatch):
        _wire(monkeypatch)
        _login(client)
        raw = client.get(STRATEGY_API[0]).get_data(as_text=True)
        parsed = json.loads(raw, parse_float=lambda s: pytest.fail(
            f"float sızdı: {s}"))
        assert parsed["strategy_version"] == 1

    def test_float_input_rejected_not_silently_accepted(self):
        env = _analysis()
        env["portfolio"]["exposure"]["gross_pct"] = 90.0
        p = ssv.analyze_strategy(_providers(analysis=env))
        assert p["data_quality"] == "UNAVAILABLE"


# ── Salt-okunurluk + güvenlik sabitleri ─────────────────────────────

class TestReadOnlySecurity:
    def test_strategy_requests_no_file_writes(self, client,
                                              monkeypatch):
        _wire(monkeypatch)
        _login(client)
        real_open = builtins.open
        writes = []

        def guard(file, mode="r", *a, **k):
            if any(m in str(mode) for m in ("w", "a", "x", "+")):
                writes.append((str(file), str(mode)))
            return real_open(file, mode, *a, **k)
        monkeypatch.setattr(builtins, "open", guard)
        for route in STRATEGY_API + ("/strategy-intelligence",):
            assert client.get(route).status_code == 200
        assert writes == []

    def test_get_only_no_execution_path(self, client):
        _login(client)
        for route in STRATEGY_API:
            for method in ("post", "put", "patch", "delete"):
                assert getattr(client, method)(route).status_code == \
                    405, (route, method)

    def test_no_exchange_connectivity_symbols(self):
        from pathlib import Path
        for path in ("strategy_intelligence.py", "strategy_service.py",
                     "strategy_export.py"):
            low = Path(path).read_text(encoding="utf-8").lower()
            for banned in ("binance", "new_order", "create_order",
                           "cancel_order", "requests.", "socket"):
                assert banned not in low, (path, banned)

    def test_provider_failure_sterile_no_leakage(self, client,
                                                 monkeypatch):
        _wire(monkeypatch, {"portfolio_analysis":
                            lambda: (_ for _ in ()).throw(RuntimeError(
                                "/etc/passwd BINANCE_API_SECRET"))})
        _login(client)
        for route in STRATEGY_API:
            r = client.get(route)
            assert r.status_code == 200
            body = r.get_data(as_text=True)
            p = r.get_json()
            assert p["data_quality"] == "UNAVAILABLE"
            for leak in ("passwd", "BINANCE_API_SECRET", "Traceback",
                         "RuntimeError"):
                assert leak not in body, (route, leak)

    def test_unexpected_error_sterile_500(self, client, monkeypatch):
        monkeypatch.setattr(
            ssv, "analyze_strategy",
            lambda *a, **k: (_ for _ in ()).throw(KeyError("ic")))
        _login(client)
        for route in STRATEGY_API:
            r = client.get(route)
            assert r.status_code == 500
            assert r.get_json()["error"]["code"] == \
                "STRATEGY_ANALYSIS_ERROR"

    def test_auth_required_on_strategy_surfaces(self, client):
        for route in STRATEGY_API:
            assert client.get(route).status_code == 401, route
        assert client.get("/strategy-intelligence").status_code in \
            (302, 401)


# ── Mission 1700 + önceki yüzey uyumluluğu ──────────────────────────

class TestBackwardCompatibility:
    PAGES = ("/", "/intelligence", "/workspace", "/automation",
             "/portfolio-intelligence", "/strategy-intelligence")
    APIS = ("/api/automation/status",
            "/api/v1/automation/export/status?format=json")

    def test_all_pages_render(self, client):
        _login(client)
        for page in self.PAGES:
            assert client.get(page).status_code == 200, page

    def test_previous_apis_respond(self, client):
        _login(client)
        for api in self.APIS:
            r = client.get(api)
            assert r.status_code in (200, 503), api
            assert "Traceback" not in r.get_data(as_text=True)

    def test_mission1700_portfolio_chain_intact(self, client,
                                                monkeypatch):
        import portfolio_service as psv
        fixed = {
            "equity": lambda: {"freshness": "fresh", "data": {
                "nav_usdt": "1000", "cash_usdt": "400",
                "realized_pnl": "25", "unrealized_pnl": "-5",
                "total_fees": "3.5"}},
            "positions": lambda: {"freshness": "fresh", "data": [
                {"symbol": "BTCUSDT", "side": "LONG",
                 "quantity": "0.004", "entry_price": "100000",
                 "mark_price": "100000", "leverage": "1"}]},
            "risk": lambda: {"freshness": "fresh", "data": {
                "drawdown_pct": "2",
                "thresholds": {"max_net_exposure_pct": "200",
                               "max_drawdown_pct": "5",
                               "max_concentration_pct": "80"}}},
        }
        monkeypatch.setattr(psv, "build_default_providers",
                            lambda: fixed)
        _login(client)
        api = client.get("/api/v1/portfolio/intelligence").get_json()
        assert api["status"] == "OK"
        assert api["portfolio"]["equity"]["nav_usdt"] == \
            "1000.00000000"  # 8 basamak normalize gösterim (1700 kuralı)
        for fmt in ("json", "csv"):
            r = client.get(
                f"/api/v1/portfolio/intelligence/export/{fmt}")
            assert r.status_code == 200, fmt

    def test_agent07_fix_did_not_change_portfolio_output(self):
        """Agent 07 persist=False düzeltmesi yalnız yan etkiyi kaldırır;
        analiz çıktısı şekli/statüsü değişmemiştir."""
        import portfolio_service as psv
        env = psv.get_portfolio_analysis({
            "equity": lambda: {"freshness": "fresh", "data": {
                "nav_usdt": "1000", "cash_usdt": "400",
                "realized_pnl": "25", "unrealized_pnl": "-5",
                "total_fees": "3.5"}},
            "positions": lambda: {"freshness": "fresh", "data": [
                {"symbol": "BTCUSDT", "side": "LONG",
                 "quantity": "0.004", "entry_price": "100000",
                 "mark_price": "100000", "leverage": "1"}]},
            "risk": lambda: {"freshness": "fresh", "data": {
                "drawdown_pct": "2",
                "thresholds": {"max_net_exposure_pct": "200",
                               "max_drawdown_pct": "5",
                               "max_concentration_pct": "80"}}},
        }, "2026-07-27T00:00:00+00:00")
        assert env["status"] == "OK"
        assert env["analysis_version"] == 1

    def test_auth_gate_unchanged(self, client):
        for path in self.PAGES[1:] + ("/api/automation/status",
                                      "/api/v1/strategy/intelligence"):
            assert client.get(path).status_code in (401, 302), path

    def test_no_route_collisions(self):
        seen = {}
        for rule in flask_app.app.url_map.iter_rules():
            for method in rule.methods - {"HEAD", "OPTIONS"}:
                key = (rule.rule, method)
                assert key not in seen, key
                seen[key] = rule.endpoint

    def test_cache_headers_consistent(self, client, monkeypatch):
        _wire(monkeypatch)
        _login(client)
        r = client.get(STRATEGY_API[1])
        assert r.headers["Cache-Control"] == "no-store, private"
        assert r.headers.get("X-Content-Type-Options") == "nosniff"


# ── Performans üst sınırları ────────────────────────────────────────

class TestPerformance:
    def test_core_and_export_within_bounds(self):
        t0 = time.perf_counter()
        for _ in range(50):
            p = score.build_strategy(_analysis())
        assert time.perf_counter() - t0 < 5.0
        full = {**p, "proposal_id": "sabit", "generated_at": "x"}
        t1 = time.perf_counter()
        for _ in range(100):
            sx.export_strategy_json(full)
        assert time.perf_counter() - t1 < 5.0

    def test_api_response_within_bounds(self, client, monkeypatch):
        _wire(monkeypatch)
        _login(client)
        t0 = time.perf_counter()
        for _ in range(10):
            assert client.get(STRATEGY_API[1]).status_code == 200
        assert time.perf_counter() - t0 < 10.0


# ── Dağıtım duman testi ─────────────────────────────────────────────

class TestDeploymentSmoke:
    def test_routes_registered(self):
        rules = {r.rule for r in flask_app.app.url_map.iter_rules()}
        for must in ("/strategy-intelligence",) + STRATEGY_API + (
                "/portfolio-intelligence",
                "/api/v1/portfolio/intelligence"):
            assert must in rules, must

    def test_templates_parse(self):
        envn = flask_app.app.jinja_env
        for name in ("dash_base.html", "strategy_intelligence.html",
                     "portfolio_intelligence.html"):
            envn.get_template(name)

    def test_root_reachable_without_error(self, client):
        assert client.get("/").status_code in (200, 302, 401)
