"""PFDE (Profit-First Decision Engine) gölge katmanı sözleşmeleri.

- PFS/TCP/EPP gerçek işlem kararını DEĞİŞTİRMEZ (kapıya bağlı değil)
- skorlar deterministik; veri yoksa None + DATA_QUALITY (uydurma yok)
- gölge kayıtları pozisyon → trade kaydına taşınır
- erken-pencere (30/60/90/180 sn) izleri monitörde donar
- istatistik yardımcıları bilinen değerleri verir
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "alpha20_v1"))
import dual_model as dm  # noqa: E402
import profit_first as pf  # noqa: E402


def _klines(n=60, start=100.0, step=0.05):
    return [[i * 60_000, 0, 0, 0, str(start + i * step), "10"]
            for i in range(n)]


ROW = {"symbol": "TESTUSDT", "spread_pct": 0.01,
       "volume_usdt": 1e8, "trade_count": 3e5,
       "volatility_pct": 3.0, "change_pct": 2.0, "last": 103.0}
SIG = {"side": "LONG", "confidence": 70,
       "expected_gross_edge_pct": 0.6, "rsi": 58, "vol_ratio": 1.6}


class TestScores:
    def test_tcp_requires_data(self):
        assert pf.compute_tcp(SIG, []) is None
        assert pf.compute_tcp(SIG, _klines(5)) is None

    def test_tcp_deterministic_range(self):
        a = pf.compute_tcp(SIG, _klines())
        b = pf.compute_tcp(SIG, _klines())
        assert a == b and 0 <= a <= 100

    def test_epp_none_without_tcp_or_cost(self):
        assert pf.compute_epp(None, 0.5, 0.2) is None
        assert pf.compute_epp(70.0, 0.5, 0) is None

    def test_epp_scales_with_coverage(self):
        low = pf.compute_epp(80.0, 0.1, 0.25)
        high = pf.compute_epp(80.0, 0.6, 0.25)
        assert high > low

    def test_score_candidate_fields_and_determinism(self):
        cfg = dm.get_config()
        ctx = {"btc_change_pct": 1.0, "trades": []}
        s1 = pf.score_candidate(ROW, SIG, _klines(), dm.MODEL_CORE,
                                cfg["core"], ctx)
        s2 = pf.score_candidate(ROW, SIG, _klines(), dm.MODEL_CORE,
                                cfg["core"], ctx)
        for k in ("confidence", "tcp", "epp", "pfs", "reasons",
                  "components", "local_top", "cost_pct"):
            assert k in s1
        assert {k: v for k, v in s1.items() if k != "at"} == \
               {k: v for k, v in s2.items() if k != "at"}

    def test_local_top_reason_near_peak(self):
        # Sürekli yükselen seri: son fiyat zirvede → yerel tepe riski
        cfg = dm.get_config()
        s = pf.score_candidate(ROW, SIG, _klines(), dm.MODEL_CORE,
                               cfg["core"], {"btc_change_pct": 1.0,
                                             "trades": []})
        assert "LOCAL_TOP_HIGH_RISK" in s["reasons"]

    def test_stablecoin_risk(self):
        cfg = dm.get_config()
        row = {**ROW, "symbol": "USDCUSDT"}
        s = pf.score_candidate(row, SIG, _klines(), dm.MODEL_CORE,
                               cfg["core"], {"trades": []})
        assert "STABLECOIN_RISK" in s["reasons"]
        assert s["components"]["stablecoin"] == 0.0

    def test_cost_not_covered_reason(self):
        cfg = dm.get_config()
        sig = {**SIG, "expected_gross_edge_pct": 0.05}
        s = pf.score_candidate(ROW, sig, _klines(), dm.MODEL_CORE,
                               cfg["core"], {"trades": []})
        assert "COST_NOT_COVERED" in s["reasons"]

    def test_no_klines_marks_data_quality(self):
        cfg = dm.get_config()
        s = pf.score_candidate(ROW, SIG, [], dm.MODEL_CORE,
                               cfg["core"], {"trades": []})
        assert s["tcp"] is None and s["epp"] is None
        assert "DATA_QUALITY" in s["reasons"]

    def test_missing_inputs_yield_none_not_fabrication(self):
        # Mimar bulgusu: eksik girdi 0/1 İKAMESİYLE skorlanamaz.
        cfg = dm.get_config()
        row = {"symbol": "TESTUSDT"}  # spread/hacim/volatilite yok
        sig = {"side": "LONG", "confidence": 70}  # edge/rsi/vr yok
        s = pf.score_candidate(row, sig, _klines(), dm.MODEL_CORE,
                               cfg["core"], {"trades": []})
        assert s["tcp"] is None and s["epp"] is None
        for k in ("cost_after_move", "volatility_quality", "spread",
                  "liquidity", "volume_confirmation"):
            assert s["components"][k] is None
        assert "DATA_QUALITY" in s["reasons"]
        assert "COST_NOT_COVERED" not in s["reasons"]
        assert "LOW_VOL_QUALITY" not in s["reasons"]

    def test_tcp_requires_rsi_and_vol_ratio(self):
        assert pf.compute_tcp({"vol_ratio": 1.5}, _klines()) is None
        assert pf.compute_tcp({"rsi": 55}, _klines()) is None


class TestStats:
    def test_roc_auc_perfect_and_inverse(self):
        assert pf.roc_auc([90, 80, 20, 10], [1, 1, 0, 0]) == 1.0
        assert pf.roc_auc([10, 20, 80, 90], [1, 1, 0, 0]) == 0.0
        assert pf.roc_auc([50, 50], [1, 1]) is None

    def test_brier(self):
        assert pf.brier([1.0, 0.0], [1, 0]) == 0.0
        assert pf.brier([0.0, 1.0], [1, 0]) == 1.0

    def test_precision_recall(self):
        out = pf.precision_recall([90, 70, 40, 10], [1, 0, 1, 0], 60)
        assert out["precision"] == 0.5 and out["recall"] == 0.5

    def test_calibration_buckets(self):
        cal = pf.calibration([95, 90, 5], [1, 0, 0])
        top = cal[-1]
        assert top["n"] == 2 and top["actual_pct"] == 50.0

    def test_predictor_stats_insufficient(self):
        out = pf.predictor_stats([None, None, 50], [1, 0, 1], "x")
        assert "note" in out and "roc_auc" not in out


class TestNeverProfitable:
    def test_profitable_returns_none(self):
        assert pf.classify_never_profitable(
            {"net_pnl": 0.5, "mfe_pct": 1.0}, 0.23) is None

    def test_groups(self):
        c = pf.classify_never_profitable(
            {"net_pnl": -0.3, "mfe_pct": 0.0, "mae_pct": 0.4}, 0.23)
        assert c["cause"] == "ADVERSE_MOVE_DOMINANT"
        c = pf.classify_never_profitable(
            {"net_pnl": -0.3, "mfe_pct": 0.1, "mae_pct": 0.1}, 0.23)
        assert c["cause"] == "MOVE_BELOW_COST"
        c = pf.classify_never_profitable(
            {"net_pnl": -0.3, "mfe_pct": 0.8, "mae_pct": 0.1}, 0.23)
        assert c["cause"] == "PROFIT_GIVEBACK"
        c = pf.classify_never_profitable(
            {"net_pnl": -0.3}, 0.23)
        assert c["cause"] == "DATA_QUALITY" and c["net_pnl"] == -0.3

    def test_uses_trades_own_cost_not_default(self):
        # İşlemin kendi fee+slippage'ı: (0.05+0.01)/50*100 = %0.12
        # mfe 0.15 > 0.12 → PROFIT_GIVEBACK (varsayılan 0.23 ile
        # MOVE_BELOW_COST olurdu — gerçek maliyet kazanır)
        c = pf.classify_never_profitable(
            {"net_pnl": -0.1, "mfe_pct": 0.15, "mae_pct": 0.05,
             "fees": 0.05, "slippage": 0.01, "notional_usdt": 50.0},
            0.23)
        assert c["cause"] == "PROFIT_GIVEBACK"
        assert c["cost_pct"] == pytest.approx(0.12)

    def test_prevention_rule_nonempty(self):
        for cause in ("NO_FAVORABLE_MOVE", "MOVE_BELOW_COST",
                      "PROFIT_GIVEBACK", "ADVERSE_MOVE_DOMINANT",
                      "DATA_QUALITY"):
            assert pf.prevention_rule(cause)


class TestShadowFile:
    def test_append_read_and_trim(self, tmp_path):
        p = tmp_path / "shadow.jsonl"
        assert pf.append_shadow({"a": 1}, p)
        assert pf.append_shadow({"a": 2}, p)
        rows = pf.read_shadow(10, p)
        assert [r["a"] for r in rows] == [1, 2]
        # Kilit ayrı kalıcı dosyada (replace edilen JSONL'de değil)
        assert (tmp_path / "shadow.jsonl.lock").exists()

    def test_trim_keeps_tail(self, tmp_path, monkeypatch):
        p = tmp_path / "shadow.jsonl"
        monkeypatch.setattr(pf, "SHADOW_MAX_BYTES", 200)
        monkeypatch.setattr(pf, "SHADOW_KEEP_LINES", 3)
        for i in range(30):
            assert pf.append_shadow({"i": i}, p)
        rows = pf.read_shadow(100, p)
        assert len(rows) < 30 and rows[-1]["i"] == 29

    def test_read_missing_returns_empty(self, tmp_path):
        assert pf.read_shadow(10, tmp_path / "yok.jsonl") == []


class TestIntegration:
    def _isolate(self, monkeypatch, tmp_path):
        rt_path = tmp_path / "rt.json"
        monkeypatch.setattr(dm, "RUNTIME_PATH", rt_path, raising=False)
        # _load/_update kendi path sabitini kullanıyorsa yönlendir
        state = {"positions": {}, "trades": []}

        def _load():
            return json.loads(json.dumps(state))

        def _update(mut):
            mut(state)
            return state
        monkeypatch.setattr(dm, "_load_runtime", _load)
        monkeypatch.setattr(dm, "_update_runtime", _update)
        return state

    def test_open_stores_shadow_and_early_marks(self, monkeypatch,
                                                tmp_path):
        state = self._isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(dm, "legacy_open_position", lambda: None)
        cfg = dm.get_config()
        sig = {**SIG, "last": 100.0}
        ok, reason = dm.try_open_position(
            "TESTUSDT", dm.MODEL_CORE, sig, 0.4, cfg, now=1000.0,
            shadow={"pfs": 55.5, "tcp": 60.0, "epp": 50.0})
        assert ok, reason
        p = state["positions"]["TESTUSDT"]
        assert p["shadow_scores"]["pfs"] == 55.5
        assert p["early_marks"] == {}

    def test_monitor_freezes_early_marks_and_trade_carries(
            self, monkeypatch, tmp_path):
        state = self._isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(dm, "legacy_open_position", lambda: None)
        cfg = dm.get_config()
        sig = {**SIG, "last": 100.0}
        dm.try_open_position("TESTUSDT", dm.MODEL_CORE, sig, 0.4,
                             cfg, now=1000.0,
                             shadow={"pfs": 42.0})
        # 65 sn sonra fiyat hafif yukarı: 30 ve 60 sn izleri donar
        dm.monitor_positions(lambda s: 100.1, cfg, now=1065.0)
        p = state["positions"]["TESTUSDT"]
        assert "30" in p["early_marks"] and "60" in p["early_marks"]
        assert "180" not in p["early_marks"]  # 180 sn henüz dolmadı
        assert p["early_marks"]["30"]["mfe"] == pytest.approx(0.1)
        # Gecikmeli ölçüm dürüstlüğü: gerçek ölçüm anı kaydedilir
        assert p["early_marks"]["30"]["at_sec"] == pytest.approx(65.0)
        # TP'ye taşı → kapanışta trade gölge + izleri taşır
        dm.monitor_positions(lambda s: 101.0, cfg, now=1200.0)
        assert not state["positions"]
        t = state["trades"][0]
        assert t["shadow_scores"]["pfs"] == 42.0
        assert "30" in t["early_marks"]

    def test_shadow_failure_does_not_block_open(self, monkeypatch,
                                                tmp_path):
        # score_candidate patlasa bile döngü sözleşmesi: giriş kararı
        # gölgeden bağımsız (loop'ta try/except); burada None shadow
        # ile açılışın çalıştığını kanıtla.
        state = self._isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(dm, "legacy_open_position", lambda: None)
        cfg = dm.get_config()
        ok, _ = dm.try_open_position(
            "TESTUSDT", dm.MODEL_CORE, {**SIG, "last": 100.0},
            0.4, cfg, now=1000.0, shadow=None)
        assert ok
        assert state["positions"]["TESTUSDT"]["shadow_scores"] is None


class TestReport:
    def test_build_report_contract(self):
        trades = [
            {"net_pnl": -0.3, "confidence": 80, "mfe_pct": 0.1,
             "mae_pct": 0.2, "hold_minutes": 1.0,
             "net_edge_pct": 0.1},
            {"net_pnl": 0.4, "confidence": 60, "mfe_pct": 1.0,
             "mae_pct": 0.05, "hold_minutes": 5.0,
             "net_edge_pct": 0.5,
             "shadow_scores": {"pfs": 70.0, "tcp": 60.0, "epp": 55.0,
                               "local_top": {"dist_high_20_pct": 0.5}},
             "early_marks": {"30": {"mfe": 0.2, "mae": 0.0,
                                    "at_sec": 31.0},
                             "60": {"mfe": 0.2, "mae": 0.0,
                                    "at_sec": 300.0}}},  # geç → elenir
        ]
        rep = pf.build_report(trades, shadow_limit=0)
        assert rep["closed_trades"] == 2
        assert rep["contract"] == {
            "pfs_changes_real_trades": False,
            "live_orders": "DISABLED", "champion_changed": False}
        assert rep["confidence_analysis"]
        assert any(p["predictor"] == "PFS"
                   for p in rep["predictor_comparison"])
        assert rep["early_window"]["coverage_n"] == 1
        assert rep["local_top"]["coverage_n"] == 1
        assert rep["never_profitable"]

    def test_report_endpoint(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import app as app_mod
        app_mod.app.config["TESTING"] = True
        with app_mod.app.test_client() as client:
            with client.session_transaction() as s:
                s["logged_in"] = True
                s["username"] = "t"
            r = client.get("/api/profit-first/report")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["data"]["contract"][
            "pfs_changes_real_trades"] is False


class TestRealGateUntouched:
    def test_reason_codes_unchanged(self):
        # PFS nedenleri AYRI kayıttadır. ADR-016, gerçek Paper karar
        # zincirine altı açıklanabilir rejim/EV/güven/sıra kodu ekler.
        assert len(dm.REASON_CODES) == 25
        for reason in ("REGIME_UNSTABLE", "STRATEGY_NOT_CONFIRMED",
                       "INSUFFICIENT_CALIBRATION",
                       "NET_EV_NON_POSITIVE",
                       "NET_EV_CONFIDENCE_LOW",
                       "RANK_BELOW_CYCLE_CUTOFF"):
            assert reason in dm.REASON_CODES
        assert "LOCAL_TOP_HIGH_RISK" not in dm.REASON_CODES
        assert "LOCAL_TOP_HIGH_RISK" in pf.PFS_REASON_CODES

    def test_gate_signature_has_no_pfs(self):
        import inspect
        src = inspect.getsource(dm.execution_quality_gate)
        assert "pfs" not in src.lower()
