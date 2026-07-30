"""İki dinamik liste + iki kısa vadeli PAPER modeli — kabul testleri.

Spec: CORE LIQUIDITY (ALPHA_CORE_SCALP) + OPPORTUNITY
(ALPHA_OPPORTUNITY_BURST); ayrı risk bütçeleri, sahiplik arbitrajı,
zorunlu execution quality, ayrı metrikler, git dışı kalıcılık,
LIVE ORDERS DISABLED.
"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_model as dm  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
    monkeypatch.setattr(dm, "LEGACY_STATE_PATH",
                        tmp_path / "state.json")
    yield


def _ticker(sym, vol=100e6, bid=100.0, ask=100.02, last=100.01,
            high=103.0, low=99.0, count=300000, chg=2.0):
    return {"symbol": sym, "quoteVolume": str(vol),
            "bidPrice": str(bid), "askPrice": str(ask),
            "lastPrice": str(last), "highPrice": str(high),
            "lowPrice": str(low), "count": count,
            "priceChangePercent": str(chg)}


def _klines(n=60, base=100.0, trend=0.001, vol=1000.0,
            burst_last=3.0):
    out = []
    price = base
    for i in range(n):
        price *= (1 + trend)
        v = vol * (burst_last if i >= n - 5 else 1.0)
        out.append([0, str(price * 0.999), str(price * 1.001),
                    str(price * 0.998), str(price), str(v)])
    return out


CFG = dm.get_config({})


class TestLists:
    def test_core_list_pinned_and_filtered(self):
        ticks = [_ticker("BTCUSDT"), _ticker("ETHUSDT"),
                 _ticker("SOLUSDT"),
                 _ticker("BNBUSDT", vol=900e6),
                 _ticker("DUSTUSDT", vol=1e5, count=100),  # elenir
                 _ticker("WIDEUSDT", bid=100, ask=101)]     # spread
        core = dm.build_core_list(ticks, CFG)
        syms = [r["symbol"] for r in core]
        for p in dm.PINNED:
            assert p in syms
        assert "BNBUSDT" in syms
        assert "DUSTUSDT" not in syms and "WIDEUSDT" not in syms

    def test_core_list_size_from_config(self):
        ticks = [_ticker(f"C{i}USDT", vol=(100 + i) * 1e6)
                 for i in range(30)] + [
            _ticker(s) for s in dm.PINNED]
        cfg = dm.get_config({"dual_model": {"core": {
            "list_size": 8}}})
        assert len(dm.build_core_list(ticks, cfg)) == 8

    def test_opportunity_excludes_core_and_ranks_burst(self):
        ticks = [_ticker("BTCUSDT", chg=9.0),
                 _ticker("PUMPUSDT", vol=20e6, chg=15.0,
                         high=120, low=95, count=50000),
                 _ticker("FLATUSDT", vol=20e6, chg=0.1,
                         high=100.2, low=100.0, count=50000)]
        opp = dm.build_opportunity_list(ticks, CFG, {"BTCUSDT"})
        syms = [r["symbol"] for r in opp]
        assert "BTCUSDT" not in syms          # core'da → hariç
        assert "PUMPUSDT" in syms
        assert "FLATUSDT" not in syms          # volatilite yetersiz
        assert opp[0]["opportunity_type"] == "VOLUME_BREAKOUT"

    def test_leveraged_tokens_excluded(self):
        ticks = [_ticker("BTCUPUSDT"), _ticker("XBEARUSDT")]
        assert dm.build_core_list(ticks, CFG) == []


class TestSignals:
    def test_long_signal_with_volume_confirmation(self):
        sig = dm.evaluate_signal("X", _klines(), dm.MODEL_CORE)
        assert sig["side"] == "LONG"
        assert sig["confidence"] >= 60
        assert sig["expected_gross_edge_pct"] > 0

    def test_no_signal_downtrend(self):
        sig = dm.evaluate_signal("X", _klines(trend=-0.002),
                                 dm.MODEL_CORE)
        assert sig["side"] is None
        assert sig["reason_code"] == "NO_SIGNAL"

    def test_short_data_is_data_quality(self):
        sig = dm.evaluate_signal("X", _klines(n=10), dm.MODEL_CORE)
        assert sig["reason_code"] == "DATA_QUALITY"

    def test_opportunity_needs_volume(self):
        sig = dm.evaluate_signal("X", _klines(burst_last=0.5),
                                 dm.MODEL_OPP)
        assert sig["side"] is None
        assert sig["reason_code"] in ("MOMENTUM_EXHAUSTED",
                                      "FALSE_BREAKOUT_RISK")


class TestExecutionQuality:
    ROW = {"spread_pct": 0.02, "volume_usdt": 100e6,
           "trade_count": 300000}
    SIG = {"confidence": 80, "expected_gross_edge_pct": 0.9,
           "side": "LONG", "last": 100.0}

    def test_pass_produces_positive_net_edge(self):
        ok, reason, net = dm.execution_quality_gate(
            self.ROW, self.SIG, dm.MODEL_CORE, CFG)
        assert ok and reason is None and net > 0

    @pytest.mark.parametrize("row,sig,expected", [
        ({**ROW, "spread_pct": 9.0}, SIG, "SPREAD_TOO_HIGH"),
        ({**ROW, "volume_usdt": 1}, SIG, "LOW_LIQUIDITY"),
        ({**ROW, "trade_count": 1}, SIG, "LOW_BOOK_DEPTH"),
        (ROW, {**SIG, "confidence": 10}, "LOW_CONFIDENCE"),
        (ROW, {**SIG, "expected_gross_edge_pct": 0.05}, "FEE_DRAG"),
        (ROW, {**SIG, "expected_gross_edge_pct": 0.21},
         "EXPECTED_EDGE_TOO_LOW"),
    ])
    def test_gates(self, row, sig, expected):
        ok, reason, _ = dm.execution_quality_gate(
            row, sig, dm.MODEL_CORE, CFG)
        assert not ok and reason == expected

    def test_opportunity_wider_tolerance(self):
        row = {**self.ROW, "spread_pct": 0.07, "volume_usdt": 10e6,
               "trade_count": 30000}
        ok_core, r_core, _ = dm.execution_quality_gate(
            row, self.SIG, dm.MODEL_CORE, CFG)
        ok_opp, _, _ = dm.execution_quality_gate(
            row, self.SIG, dm.MODEL_OPP, CFG)
        assert not ok_core and ok_opp  # farklı sınırlar

    def test_all_reason_codes_defined(self):
        for code in ("NO_SIGNAL", "DUPLICATE_MODEL_OWNERSHIP",
                     "DATA_QUALITY", "COOLDOWN", "POSITION_LIMIT"):
            assert code in dm.REASON_CODES
        assert len(dm.REASON_CODES) == 16


class TestOwnership:
    def test_highest_net_edge_wins(self):
        res = dm.resolve_ownership({
            dm.MODEL_CORE: [{"symbol": "XUSDT",
                             "net_edge_pct": 0.2}],
            dm.MODEL_OPP: [{"symbol": "XUSDT",
                            "net_edge_pct": 0.5}]})
        assert res["winners"][0]["model"] == dm.MODEL_OPP
        rej = res["rejected"][0]
        assert rej["model"] == dm.MODEL_CORE
        assert rej["reason_code"] == "DUPLICATE_MODEL_OWNERSHIP"
        assert rej["winner_model"] == dm.MODEL_OPP


SIG = {"side": "LONG", "confidence": 80, "last": 100.0}


class TestPositions:
    def test_open_and_persist(self):
        ok, _ = dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG,
                                     0.3, CFG)
        assert ok
        rt = dm._load_runtime()
        p = rt["positions"]["AUSDT"]
        assert p["model"] == dm.MODEL_CORE
        assert p["execution_mode"] == "PAPER"  # ledger model adıyla

    def test_duplicate_symbol_rejected(self):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG)
        ok, reason = dm.try_open_position("AUSDT", dm.MODEL_OPP,
                                          SIG, 0.3, CFG)
        assert not ok and reason == "DUPLICATE_POSITION"

    def test_legacy_same_symbol_rejected(self):
        """Eski tek-evren botunun pozisyonu aynı sembolde açılışı engeller."""
        dm.LEGACY_STATE_PATH.write_text(json.dumps(
            {"position": {"symbol": "AUSDT", "side": "LONG"}}),
            encoding="utf-8")
        ok, reason = dm.try_open_position("AUSDT", dm.MODEL_CORE,
                                          SIG, 0.3, CFG)
        assert not ok and reason == "DUPLICATE_POSITION"
        # Farklı sembol açılabilir
        assert dm.try_open_position("BUSDT", dm.MODEL_CORE, SIG,
                                    0.3, CFG)[0]

    def test_legacy_counts_toward_total_cap(self):
        """Toplam tavan legacy state.json pozisyonunu da sayar."""
        dm.LEGACY_STATE_PATH.write_text(json.dumps(
            {"position": {"symbol": "LEGUSDT", "side": "LONG"}}),
            encoding="utf-8")
        assert dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG,
                                    0.3, CFG)[0]
        assert dm.try_open_position("BUSDT", dm.MODEL_CORE, SIG,
                                    0.3, CFG)[0]
        assert dm.try_open_position("CUSDT", dm.MODEL_OPP, SIG,
                                    0.3, CFG)[0]
        # 3 dual + 1 legacy = 4 → tavan dolu
        ok, reason = dm.try_open_position("DUSDT", dm.MODEL_OPP,
                                          SIG, 0.3, CFG)
        assert not ok and reason == "RISK_LIMIT"

    def test_model_and_total_limits(self):
        for i, s in enumerate(["AUSDT", "BUSDT"]):
            assert dm.try_open_position(s, dm.MODEL_CORE, SIG,
                                        0.3, CFG)[0]
        ok, reason = dm.try_open_position("CUSDT", dm.MODEL_CORE,
                                          SIG, 0.3, CFG)
        assert not ok and reason == "POSITION_LIMIT"  # CORE max 2
        assert dm.try_open_position("DUSDT", dm.MODEL_OPP, SIG,
                                    0.3, CFG)[0]
        assert dm.try_open_position("EUSDT", dm.MODEL_OPP, SIG,
                                    0.3, CFG)[0]
        ok, reason = dm.try_open_position("FUSDT", dm.MODEL_OPP,
                                          SIG, 0.3, CFG)
        assert not ok  # toplam 4 + OPP limiti
        assert reason in ("POSITION_LIMIT", "RISK_LIMIT")

    def test_tp_close_with_fees_and_slippage(self):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG,
                             now=1000.0)
        closed = dm.monitor_positions(
            lambda s: 100.0 * 1.006, CFG, now=1060.0)
        assert len(closed) == 1
        t = closed[0]
        assert t["result"] == "TP" and t["model"] == dm.MODEL_CORE
        assert t["fees"] > 0 and t["slippage"] > 0
        assert t["net_pnl"] < t["gross_pnl"]  # fee+slippage düşüldü
        assert dm._load_runtime()["positions"] == {}

    def test_sl_and_time_exit(self):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG,
                             now=1000.0)
        closed = dm.monitor_positions(lambda s: 99.0, CFG,
                                      now=1010.0)
        assert closed[0]["result"] == "SL"
        dm.try_open_position("BUSDT", dm.MODEL_CORE, SIG, 0.3, CFG,
                             now=1000.0)
        closed = dm.monitor_positions(lambda s: 100.05, CFG,
                                      now=1000.0 + 16 * 60)
        assert closed[0]["result"] == "TIME_EXIT"

    def test_opportunity_cooldown_after_losses(self):
        for i, s in enumerate(["AUSDT", "BUSDT"]):
            dm.try_open_position(s, dm.MODEL_OPP, SIG, 0.3, CFG,
                                 now=1000.0 + i)
            dm.monitor_positions(lambda x: 99.0, CFG,
                                 now=1100.0 + i)  # SL: 2 kayıp
        ok, reason = dm.try_open_position("CUSDT", dm.MODEL_OPP,
                                          SIG, 0.3, CFG,
                                          now=1200.0)
        assert not ok and reason == "COOLDOWN"


class TestMetricsAndSnapshot:
    def test_metrics_separated_by_model(self):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG,
                             now=1000.0)
        dm.monitor_positions(lambda s: 101.0, CFG, now=1030.0)
        dm.record_rejection("BUSDT", dm.MODEL_OPP, "LOW_CONFIDENCE")
        mc = dm.model_metrics(dm.MODEL_CORE)
        mo = dm.model_metrics(dm.MODEL_OPP)
        assert mc["closed_positions"] == 1 and mc["net_pnl"] != 0
        assert mo["closed_positions"] == 0
        assert mo["rejection_reasons"] == {"LOW_CONFIDENCE": 1}
        assert mc["rejection_reasons"] == {}
        for key in ("win_rate", "profit_factor", "max_drawdown",
                    "expectancy_per_trade", "average_hold_minutes",
                    "fees", "slippage", "trades_per_day"):
            assert key in mc

    def test_snapshot_consistency(self):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG)
        dm.try_open_position("BUSDT", dm.MODEL_OPP, SIG, 0.3, CFG)
        snap = dm.snapshot()
        c = snap["counters"]
        assert c["core_open"] == 1 and c["opportunity_open"] == 1
        assert c["total_open"] == 2 == len(snap["positions"])
        assert snap["live_orders"] == "DISABLED"

    def test_restart_persistence(self):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG)
        dm._update_runtime(lambda rt: rt.update(
            core_list=[{"symbol": "BTCUSDT"}]))
        # "restart": modül durumu değil dosya okunur
        snap = dm.snapshot()
        assert snap["counters"]["total_open"] == 1
        assert snap["core_list"][0]["symbol"] == "BTCUSDT"


class TestRateLimitGuard:
    """Dual-model istekleri paylaşımlı 429/418 korumasına uyar."""

    def test_backoff_blocks_request(self, monkeypatch):
        import alpha20 as a20
        monkeypatch.setattr(a20, "rate_limit_remaining",
                            lambda now=None: 42.0)
        with pytest.raises(dm.RateLimited):
            dm.fetch_spot_klines("BTCUSDT")

    def test_429_registered_to_shared_state(self, monkeypatch):
        import alpha20 as a20
        seen = {}
        monkeypatch.setattr(a20, "rate_limit_remaining",
                            lambda now=None: 0.0)
        monkeypatch.setattr(
            a20, "register_rate_limit",
            lambda status, response=None, now=None:
            seen.setdefault("status", status) or 60.0)

        class Resp:
            status_code = 429
        import requests
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: Resp())
        with pytest.raises(dm.RateLimited):
            dm.fetch_spot_tickers()
        assert seen["status"] == 429

    def test_fetch_spot_prices_parses_batch(self, monkeypatch):
        monkeypatch.setattr(dm, "_guarded_get",
                            lambda *a, **k: [
                                {"symbol": "AUSDT", "price": "1.5"},
                                {"symbol": "BUSDT", "price": "x"}])
        assert dm.fetch_spot_prices(["AUSDT", "BUSDT"]) == {
            "AUSDT": 1.5}
        assert dm.fetch_spot_prices([]) == {}


class TestManualCloseAndVisibility:
    def test_manual_close_with_fresh_price(self, monkeypatch):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG,
                             now=1000.0)
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {"AUSDT": 101.0})
        ok, msg = dm.manual_close("ausdt")  # case-insensitive
        assert ok and msg == "CLOSED"
        rt = dm._load_runtime()
        assert rt["positions"] == {}
        t = rt["trades"][0]
        assert t["result"] == "MANUAL_CLOSE"
        assert t["net_pnl"] < t["gross_pnl"]  # fee+slippage düşüldü

    def test_manual_close_rejected_without_fresh_price(
            self, monkeypatch):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG)
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {})
        ok, msg = dm.manual_close("AUSDT")
        assert not ok and msg == "PRICE_UNAVAILABLE"
        assert "AUSDT" in dm._load_runtime()["positions"]

    def test_manual_close_unknown_position(self):
        ok, msg = dm.manual_close("NOPEUSDT", price=1.0)
        assert not ok and msg == "POSITION_NOT_FOUND"

    def test_snapshot_with_prices_enriches_positions(
            self, monkeypatch):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG)
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {"AUSDT": 102.0})
        p = dm.snapshot(with_prices=True)["positions"][0]
        assert p["current_price"] == 102.0
        for key in ("quantity", "notional_usdt", "entry", "tp",
                    "sl", "model", "unrealized_net_pnl",
                    "unrealized_pnl_pct", "est_fees",
                    "est_slippage"):
            assert key in p
        assert p["unrealized_net_pnl"] > 0

    def test_snapshot_unknown_price_stays_none(self, monkeypatch):
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG)
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: (_ for _ in ()).throw(
                                dm.RateLimited("x")))
        p = dm.snapshot(with_prices=True)["positions"][0]
        assert p["current_price"] is None
        assert p["unrealized_net_pnl"] is None  # uydurma fiyat yok

    def test_close_endpoint(self, monkeypatch):
        import app as app_module
        dm.try_open_position("AUSDT", dm.MODEL_CORE, SIG, 0.3, CFG)
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {"AUSDT": 100.5})
        app_module.app.config["TESTING"] = True
        app_module.app.config["WTF_CSRF_ENABLED"] = False
        with app_module.app.test_client() as c:
            with c.session_transaction() as s:
                s["logged_in"] = True
                s["username"] = "t"
            r = c.post("/api/dual-model/close",
                       json={"symbol": "AUSDT"})
            assert r.status_code == 200 and r.get_json()["ok"]
            r2 = c.post("/api/dual-model/close",
                        json={"symbol": "AUSDT"})
            assert r2.status_code == 400

    def test_ui_position_table_and_close_button(self):
        tpl = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        assert "th-dm-pos-table" in tpl
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "/api/dual-model/close" in js
        assert "renderDualPositions" in js
        assert "window.confirm" in js  # kazara kapatmaya karşı


class TestGitCleanAndSafety:
    def test_runtime_paths_gitignored(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "alpha20_v1/dual_model_runtime.json" in gi
        assert "alpha20_v1/.dual_model.lock" in gi

    def test_no_private_or_order_endpoints(self):
        src = (ROOT / "alpha20_v1/dual_model.py").read_text(
            encoding="utf-8")
        for forbidden in ("/api/v3/order", "signature", "apiKey",
                          "X-MBX-APIKEY"):
            assert forbidden not in src  # LIVE ORDERS DISABLED


class TestApiAndUi:
    def test_state_endpoint(self):
        import app as app_module
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            with c.session_transaction() as s:
                s["logged_in"] = True
                s["username"] = "t"
            r = c.get("/api/dual-model/state")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] and d["data"]["live_orders"] == "DISABLED"
        assert "counters" in d["data"] and "metrics" in d["data"]

    def test_home_has_two_tables_and_counters(self):
        tpl = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        for el in ("th-dm-core-table", "th-dm-opp-table",
                   "th-dm-core-uni", "th-dm-opp-uni",
                   "th-dm-core-open", "th-dm-opp-open",
                   "th-dm-total-open"):
            assert el in tpl
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "/api/dual-model/state" in js
        assert "renderDualModel" in js


def test_record_startup_failure_writes_last_error():
    """Windows başlangıç hatası panele görünür: last_error yazılır."""
    dm.record_startup_failure("DUAL_MODEL_LOOP_NOT_STARTED: test")
    rt = dm._load_runtime()
    assert rt["last_error"] == "DUAL_MODEL_LOOP_NOT_STARTED: test"


# ── Windows SSL/ağ dayanıklılığı (WRONG_VERSION_NUMBER kök nedeni) ──

class _Resp:
    status_code = 200

    def __init__(self, status_code: int = 200):
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


def _no_backoff(monkeypatch):
    monkeypatch.setattr(dm.time, "sleep", lambda s: None)


def test_guarded_get_retries_transient_ssl_error(monkeypatch):
    """Aralıklı TLS müdahalesi (Windows AV/proxy): ilk deneme SSLError,
    ikinci deneme geçer — legacy fetch_klines ile aynı davranış."""
    import requests
    _no_backoff(monkeypatch)
    calls = {"n": 0}

    def _get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.SSLError("WRONG_VERSION_NUMBER")
        return _Resp()

    monkeypatch.setattr(requests, "get", _get)
    assert dm._guarded_get("/api/v3/ticker/price") == {"ok": True}
    assert calls["n"] == 2


def test_guarded_get_ssl_exhaustion_raises_diagnosed_error(monkeypatch):
    """Kalıcı SSL hatası: retry'lar tükenince teşhisli RuntimeError —
    döngü bunu last_error'a yazar (sessiz ölüm yok, verify kapanmaz)."""
    import requests
    _no_backoff(monkeypatch)

    def _get(url, params=None, timeout=None):
        raise requests.exceptions.SSLError(
            "[SSL: WRONG_VERSION_NUMBER] wrong version number")

    monkeypatch.setattr(requests, "get", _get)
    with pytest.raises(RuntimeError):
        dm._guarded_get("/api/v3/ticker/price", retries=1)


def test_guarded_get_429_never_retried_registers_backoff(monkeypatch):
    """429 yanıtı ASLA retry edilmez (ban riski): TEK istek atılır,
    paylaşımlı geri çekilmeye kaydedilir ve RateLimited fırlatılır."""
    import requests
    import alpha20 as a20
    _no_backoff(monkeypatch)
    calls = {"n": 0}
    registered = {"args": None}

    def _get(url, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(status_code=429)

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(a20, "rate_limit_remaining", lambda: 0.0)
    monkeypatch.setattr(
        a20, "register_rate_limit",
        lambda code, resp: registered.__setitem__("args", (code, resp)))
    with pytest.raises(dm.RateLimited):
        dm._guarded_get("/api/v3/ticker/price", retries=5)
    assert calls["n"] == 1, "429 retry edildi — ban riski!"
    assert registered["args"] is not None
    assert registered["args"][0] == 429


def test_guarded_get_418_never_retried(monkeypatch):
    """418 (IP ban uyarısı) da tek istek + RateLimited — retry yok."""
    import requests
    import alpha20 as a20
    _no_backoff(monkeypatch)
    calls = {"n": 0}

    def _get(url, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(status_code=418)

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(a20, "rate_limit_remaining", lambda: 0.0)
    monkeypatch.setattr(a20, "register_rate_limit", lambda c, r: None)
    with pytest.raises(dm.RateLimited):
        dm._guarded_get("/api/v3/ticker/price", retries=5)
    assert calls["n"] == 1


def test_monitor_price_failure_defers_exits_and_sets_last_error(
        monkeypatch):
    """Fiyat yenileme başarısız → pozisyon KAPANMAZ, çıkış ertelenir,
    neden last_error'da görünür (sağlık paneli KIRMIZI nedenini gösterir)."""
    dm._update_runtime(lambda rt: rt.__setitem__("positions", [
        {"symbol": "EWYBUSDT", "model": dm.MODEL_CORE, "status": "OPEN"}]))
    monkeypatch.setattr(dm, "fetch_spot_prices",
                        lambda syms: (_ for _ in ()).throw(
                            RuntimeError("SSL WRONG_VERSION_NUMBER")))
    closed = {"n": 0}
    monkeypatch.setattr(dm, "monitor_positions",
                        lambda *a, **k: closed.__setitem__("n", 1))
    fail = dm._monitor_open_positions(["EWYBUSDT"], {}, dm.get_config(),
                                      False)
    assert fail is True
    assert closed["n"] == 0  # TP/SL kararı verilmedi
    rt = dm._load_runtime()
    assert str(rt["last_error"]).startswith("PRICE_REFRESH_FAILED")
    # Toparlanma: taze fiyat gelince kendi hatamız temizlenir
    monkeypatch.setattr(dm, "fetch_spot_prices",
                        lambda syms: {"EWYBUSDT": 1.0})
    fail2 = dm._monitor_open_positions(["EWYBUSDT"], {}, dm.get_config(),
                                       True)
    assert fail2 is False
    assert dm._load_runtime()["last_error"] is None


def test_monitor_rate_limited_defers_silently(monkeypatch):
    """Paylaşımlı geri çekilme: çıkışlar sessizce ertelenir, hata yazılmaz."""
    monkeypatch.setattr(dm, "fetch_spot_prices",
                        lambda syms: (_ for _ in ()).throw(
                            dm.RateLimited("30s")))
    assert dm._monitor_open_positions(["X"], {}, dm.get_config(),
                                      False) is False
    assert dm._load_runtime().get("last_error") is None


def test_no_verify_false_in_source():
    """Doğrulama ASLA kapatılmaz (operatör kuralı)."""
    src = (ROOT / "alpha20_v1" / "dual_model.py").read_text(
        encoding="utf-8")
    assert "verify=False" not in src
    assert "LIVE ORDERS DISABLED" in src
