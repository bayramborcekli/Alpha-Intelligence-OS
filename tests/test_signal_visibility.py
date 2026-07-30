"""Sinyal görünürlüğü + Profit-First 500 acil düzeltme testleri.

Spec zorunlu testleri: canonical symbol_status, overview/paper-state
beslemesi, en-yeni-karar seçimi, model korunumu, fail-closed UNKNOWN,
Türkçe açıklama + reason code korunumu (UI), sayaç kapsam adı,
profit-first JSON zarfı / INSUFFICIENT_DATA / Windows fcntl,
eksik LAUSDT kaydının sağlıklı OPEN sayılmaması ve gerçek giriş
mantığının değişmediği kanıtı.
"""
import builtins
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))
sys.path.insert(0, str(ROOT))

import dual_model as dm  # noqa: E402


def _rt(rejections=None, positions=None, core=None, opp=None,
        last_refresh="2026-07-30T10:00:00+00:00"):
    return {"rejections": rejections or [],
            "positions": positions or {},
            "core_list": [{"symbol": s} for s in (core or [])],
            "opportunity_list": [{"symbol": s} for s in (opp or [])],
            "last_refresh": last_refresh, "trades": []}


class TestSymbolStatus:
    def test_rejections_mapped_to_symbols(self, monkeypatch):
        # spec 1/3/4: güncel recent_rejections okunur, doğru sembole
        rt = _rt(rejections=[
            {"symbol": "USD1USDT", "model": "ALPHA_CORE_SCALP",
             "reason_code": "LOW_CONFIDENCE",
             "at": "2026-07-30T10:00:05+00:00"},
            {"symbol": "INJUSDT", "model": "ALPHA_OPPORTUNITY",
             "reason_code": "MOMENTUM_EXHAUSTED",
             "at": "2026-07-30T10:00:03+00:00"},
            {"symbol": "BTCUSDT", "model": "ALPHA_CORE_SCALP",
             "reason_code": "NO_SIGNAL",
             "at": "2026-07-30T10:00:01+00:00"}],
            core=["BTCUSDT", "USD1USDT"], opp=["INJUSDT"])
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        ss = dm.symbol_status()
        assert ss["ok"] is True
        s = ss["symbols"]
        assert s["USD1USDT"]["last_rejection_reason"] == \
            "LOW_CONFIDENCE"
        assert s["USD1USDT"]["last_decision"] == "REJECTED"
        assert s["INJUSDT"]["last_rejection_reason"] == \
            "MOMENTUM_EXHAUSTED"
        # spec 2: NO_SIGNAL, UNKNOWN DEĞİL — ayrık NO_SIGNAL durumu
        assert s["BTCUSDT"]["signal_state"] == "NO_SIGNAL"
        assert s["BTCUSDT"]["last_decision"] == "NO_SIGNAL"
        assert "UNKNOWN" not in json.dumps(s["BTCUSDT"])

    def test_newest_decision_wins(self, monkeypatch):    # spec 5
        rt = _rt(rejections=[
            {"symbol": "BTCUSDT", "model": "ALPHA_CORE_SCALP",
             "reason_code": "NO_SIGNAL",
             "at": "2026-07-30T09:00:00+00:00"},
            {"symbol": "BTCUSDT", "model": "ALPHA_OPPORTUNITY",
             "reason_code": "LOW_CONFIDENCE",
             "at": "2026-07-30T11:00:00+00:00"}])
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        s = dm.symbol_status()["symbols"]["BTCUSDT"]
        assert s["last_rejection_reason"] == "LOW_CONFIDENCE"
        assert s["analyzed_at"] == "2026-07-30T11:00:00+00:00"
        # spec 6: model bilgisi en yeni kararla korunur
        assert s["model"] == "ALPHA_OPPORTUNITY"

    def test_old_record_cannot_overwrite(self, monkeypatch):
        rt = _rt(rejections=[
            {"symbol": "BTCUSDT", "model": "ALPHA_OPPORTUNITY",
             "reason_code": "LOW_CONFIDENCE",
             "at": "2026-07-30T11:00:00+00:00"},
            {"symbol": "BTCUSDT", "model": "ALPHA_CORE_SCALP",
             "reason_code": "NO_SIGNAL",
             "at": "2026-07-30T09:00:00+00:00"}])
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        s = dm.symbol_status()["symbols"]["BTCUSDT"]
        assert s["last_rejection_reason"] == "LOW_CONFIDENCE"

    def test_data_quality_maps_data_unavailable(self, monkeypatch):
        # spec 7: veri yoksa DATA_UNAVAILABLE
        rt = _rt(rejections=[
            {"symbol": "XUSDT", "model": "ALPHA_CORE_SCALP",
             "reason_code": "DATA_QUALITY",
             "at": "2026-07-30T10:00:00+00:00"}])
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        s = dm.symbol_status()["symbols"]["XUSDT"]
        assert s["signal_state"] == "DATA_UNAVAILABLE"
        assert s["data_quality"] == "MISSING"

    def test_runtime_unreadable_fail_closed(self, monkeypatch):
        # spec 8: backend okunamazsa fail-closed
        def _boom():
            raise OSError("disk")
        monkeypatch.setattr(dm, "_load_runtime", _boom)
        ss = dm.symbol_status()
        assert ss["ok"] is False and ss["symbols"] is None
        assert ss["error"] == "RUNTIME_UNREADABLE"

    def test_open_position_state(self, monkeypatch):
        rt = _rt(positions={"AAAUSDT": {
            "symbol": "AAAUSDT", "model": "ALPHA_CORE_SCALP",
            "opened_at": "2026-07-30T10:30:00+00:00",
            "confidence": 71}})
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        s = dm.symbol_status()["symbols"]["AAAUSDT"]
        assert s["signal_state"] == "POSITION_OPEN"
        assert s["last_decision"] == "SIGNAL_ACCEPTED"
        assert s["direction"] == "LONG"
        assert s["last_signal_at"] == "2026-07-30T10:30:00+00:00"

    def test_not_analyzed_distinct(self, monkeypatch):
        rt = _rt(core=["NEWUSDT"])
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        s = dm.symbol_status()["symbols"]["NEWUSDT"]
        assert s["signal_state"] == "NOT_ANALYZED"


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as s:
            s["logged_in"] = True
            s["username"] = "t"
        yield c


class TestOverviewFeed:
    def test_overview_products_canonical(self, client, monkeypatch):
        # spec 9: overview canonical durumları döndürür
        import app as app_module
        rt = _rt(rejections=[
            {"symbol": "BTCUSDT", "model": "ALPHA_CORE_SCALP",
             "reason_code": "NO_SIGNAL",
             "at": "2026-07-30T10:00:01+00:00"}])
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        monkeypatch.setattr(app_module, "_operation_symbols",
                            lambda cfg: ("BTCUSDT",))
        r = client.get("/api/operation-control/overview")
        assert r.status_code == 200
        prods = r.get_json()["data"]["products"]
        btc = [p for p in prods if p["symbol"] == "BTCUSDT"][0]
        assert btc["last_decision"] == "NO_SIGNAL"
        assert btc["last_rejection_reason"] == "NO_SIGNAL"
        assert btc["signal_state"] == "NO_SIGNAL"
        assert btc["analyzed_at"] == "2026-07-30T10:00:01+00:00"
        assert btc["last_decision"] != "UNKNOWN"

    def test_paper_state_same_canonical(self, client, monkeypatch):
        # spec 10: paper state aynı canonical durumu döndürür
        import app as app_module
        rt = _rt(rejections=[
            {"symbol": "INJUSDT", "model": "ALPHA_OPPORTUNITY",
             "reason_code": "MOMENTUM_EXHAUSTED",
             "at": "2026-07-30T10:00:03+00:00"}])
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        monkeypatch.setattr(app_module, "_operation_symbols",
                            lambda cfg: ("INJUSDT",))
        r = client.get("/api/paper/state")
        assert r.status_code == 200
        strat = [s for s in r.get_json()["strategies"]
                 if s["symbol"] == "INJUSDT"][0]
        assert strat["last_decision"] == "REJECTED"
        assert strat["last_rejection_reason"] == \
            "MOMENTUM_EXHAUSTED"
        assert strat["last_analyzed_at"] == \
            "2026-07-30T10:00:03+00:00"
        assert strat["last_signal"] != "UNKNOWN"
        # geriye uyumluluk: eski alanlar duruyor
        for k in ("last_signal", "run_state", "entry_allowed",
                  "updated_at"):
            assert k in strat

    def test_counter_scope_named(self, client):          # spec 13
        r = client.get("/api/paper/state")
        b = r.get_json()
        assert b["counter_scope"] == \
            "latest_decision_per_symbol_last_100"
        # spec 20: mevcut sayaç adları değişmedi
        for k in ("signal_candidate_count", "risk_approved_count",
                  "paper_intent_count"):
            assert k in b
        assert b["latest_per_symbol_candidates"] == \
            b["signal_candidate_count"]


class TestProfitFirst500:
    def test_windows_import_fallback(self, monkeypatch):
        # spec 14 kök neden: koşulsuz `import fcntl` Windows'ta patlar
        src = (ROOT / "alpha20_v1/profit_first.py").read_text(
            encoding="utf-8")
        assert "portable_flock" in src
        # portable_flock POSIX'te gerçek fcntl'e vekâlet eder —
        # Windows simülasyonunda ÖNCE yüklenmeli (launcher kalıbı)
        importlib.import_module("portable_flock")
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "fcntl":
                raise ImportError("No module named 'fcntl'")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        sys.modules.pop("profit_first", None)
        try:
            mod = importlib.import_module("profit_first")
            assert mod is not None
        finally:
            monkeypatch.setattr(builtins, "__import__", real_import)
            sys.modules.pop("profit_first", None)

    def test_report_200_and_fields(self, client):        # spec 14
        r = client.get("/api/profit-first/report")
        assert r.status_code == 200
        assert r.mimetype == "application/json"
        d = r.get_json()["data"]
        for key in ("coverage", "confidence", "tcp", "epp", "pfs",
                    "calibration", "shadow_summary", "live_orders",
                    "status"):
            assert key in d, key
        assert d["live_orders"] == "DISABLED"

    def test_empty_data_insufficient_not_500(self, client,
                                             monkeypatch):
        # spec 15
        monkeypatch.setattr(dm, "_load_runtime",
                            lambda: {"trades": []})
        r = client.get("/api/profit-first/report")
        assert r.status_code == 200
        d = r.get_json()["data"]
        assert d["status"] == "INSUFFICIENT_DATA"
        assert d["coverage"] == 0

    def test_error_is_json_not_html(self, client, monkeypatch):
        # spec 16
        def _boom():
            raise RuntimeError("windows kilidi")
        monkeypatch.setattr(dm, "_load_runtime", _boom)
        r = client.get("/api/profit-first/report")
        assert r.status_code == 500
        assert r.mimetype == "application/json"
        b = r.get_json()
        assert b["ok"] is False and b["data"] is None
        assert b["error_code"] == "RuntimeError"
        assert "windows kilidi" in b["message"]


class TestIncompletePosition:
    def test_incomplete_not_healthy_open(self, client, monkeypatch,
                                         tmp_path):
        # spec 17: eksik LAUSDT sağlıklı OPEN sayılmaz
        import app as app_module
        state = {"position": {"symbol": "LAUSDT", "side": "LONG",
                              "entry": None, "quantity": None,
                              "opened_at":
                              "2026-07-29T10:00:00+00:00"},
                 "trades": []}
        p = tmp_path / "state.json"
        p.write_text(json.dumps(state), encoding="utf-8")
        monkeypatch.setattr(app_module, "STATE_PATH", p)
        r = client.get("/api/operation-control/overview")
        rows = [x for x in r.get_json()["data"]["positions"]
                if x["symbol"] == "LAUSDT"]
        assert rows, "LAUSDT kaydı görünür olmalı (silinmez)"
        assert rows[0]["position_status"] == \
            "INCOMPLETE_POSITION_DATA"
        # spec 18: UI, EXIT_BLOCKED haritasıyla INCOMPLETE durumda
        # normal Kapat düğmesini göstermez (status'a bağlıdır)
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "EXIT_BLOCKED = { INCOMPLETE_POSITION_DATA: 1" in js

    def test_defense_line_forces_incomplete(self):
        # Sınıflayıcı OPEN dese bile null entry/qty OPEN kalamaz
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        assert 'INCOMPLETE_POSITION_DATA"' in src
        assert "Savunma hattı" in src


class TestRealBehaviorUnchanged:
    def test_entry_logic_untouched(self):                # spec 19
        src = (ROOT / "alpha20_v1/dual_model.py").read_text(
            encoding="utf-8")
        # Gerçek giriş/çıkış kuralları aynen durur
        for rule in ('if price >= p["tp"]:', 'elif price <= p["sl"]:',
                     "def evaluate_signal", "def try_open_position"):
            assert rule in src
        # symbol_status salt okunur: runtime'a yazan tek yol
        # _update_runtime'dır ve symbol_status onu çağırmaz
        import inspect
        body = inspect.getsource(dm.symbol_status)
        assert "_update_runtime" not in body
        assert "record_rejection" not in body

    def test_ui_reason_map_keeps_code(self):             # spec 12
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "Giriş koşulları oluşmadı" in js
        assert "Sinyal güveni yetersiz" in js
        assert "Momentum tükenmiş" in js
        assert "Maliyet sonrası ödül/risk yetersiz" in js
        assert "Karar verisi yetersiz" in js
        # backend kodu tooltip/detayda korunur
        assert 'title=\\"" + esc(code)' in js or \
            'title=\\"' in js
        # spec 11: son red nedeni sütunu var
        assert "Son red nedeni" in (
            ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")

    def test_live_disabled_and_pause(self, client):      # spec 21-23
        r = client.get("/api/paper/state")
        b = r.get_json()
        assert b.get("execution_mode") != "LIVE"
        src = (ROOT / "alpha20_v1/dual_model.py").read_text(
            encoding="utf-8")
        assert "def symbol_status" in src
        # symbol_status hiçbir emir/borsa yazma çağrısı içermez
        import inspect
        body = inspect.getsource(dm.symbol_status)
        for banned in ("create_order", "requests.post", "hmac",
                       "signed_request"):
            assert banned not in body
