"""Dual-model öğrenme köprüsü (dual_learning) — spesifikasyon testleri.

Talimatın 15. bölümündeki 20 madde bu dosyada güvenceye alınır:
veri alımı, model ayrımı, dedupe, bozuk kayıt, örneklem eşiği, teşhis,
%10 adım sınırı, ≤3 parametre, champion koruması, config yükleme,
config_version, terfi/red, rollback, restart persistence, git
temizliği, LIVE ORDERS DISABLED, güvenlik parametreleri, API/UI,
legacy learning_engine regresyonu.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_learning as dl  # noqa: E402
import dual_model as dm  # noqa: E402


def _mk_trade(i: int, model: str = dl.MODEL_CORE, net: float = 1.0,
              symbol: str = "BTCUSDT", result: str = "TP",
              confidence: int = 70, hold: float = 8.0,
              version: str = "BASE") -> dict:
    entry = 100.0
    return {
        "trade_id": f"T{model[:4]}{i:05d}", "symbol": symbol,
        "model": model, "side": "LONG", "entry": entry,
        "exit": entry + net, "quantity": 1.0,
        "notional_usdt": 100.0, "gross_pnl": net + 0.3,
        "fees": 0.2, "slippage": 0.1, "net_pnl": net,
        "result": result, "confidence": confidence,
        "hold_minutes": hold, "opened_at": "2026-07-29T00:00:00+00:00",
        "closed_at": "2026-07-29T01:00:00+00:00",
        "execution_mode": "PAPER", "config_version": version,
        "net_edge_pct": 0.2,
    }


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Öğrenme ve dual-model store'larını geçici dizine izole et."""
    monkeypatch.setattr(dl, "STATE_PATH",
                        tmp_path / "dual_learning_state.json")
    monkeypatch.setattr(dl, "HISTORY_PATH",
                        tmp_path / "dual_learning_history.jsonl")
    monkeypatch.setattr(dl, "RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
    dl._OVERRIDE_CACHE.update(mtime="X", data={})
    yield tmp_path
    dl._OVERRIDE_CACHE.update(mtime="X", data={})


def _write_runtime(path: Path, trades: list[dict]) -> None:
    (path / "dual_model_runtime.json").write_text(
        json.dumps({"trades": trades}), encoding="utf-8")


# ── 1-4: veri alımı, ayrım, dedupe, bozuk kayıt ────────────────────

class TestIngestion:
    def test_closed_trade_enters_dataset(self, iso):
        _write_runtime(iso, [_mk_trade(1)])
        counts = dl.ingest_closed_trades()
        assert counts[dl.MODEL_CORE] == 1
        state = dl._load_state()
        ds = state["models"][dl.MODEL_CORE]["dataset"]
        assert ds[0]["trade_id"] == "TALPH00001"
        assert ds[0]["net_pnl"] == 1.0
        # Kanonik şema alanları mevcut
        for field in ("model_name", "fees", "slippage", "exit_reason",
                      "configuration_version", "hold_duration"):
            assert field in ds[0]

    def test_core_and_opportunity_separate(self, iso):
        _write_runtime(iso, [_mk_trade(1), _mk_trade(2, dl.MODEL_OPP)])
        dl.ingest_closed_trades()
        state = dl._load_state()
        core = state["models"][dl.MODEL_CORE]["dataset"]
        opp = state["models"][dl.MODEL_OPP]["dataset"]
        assert len(core) == 1 and len(opp) == 1
        assert core[0]["model_name"] == dl.MODEL_CORE
        assert opp[0]["model_name"] == dl.MODEL_OPP

    def test_duplicate_not_ingested_twice(self, iso):
        _write_runtime(iso, [_mk_trade(1)])
        assert dl.ingest_closed_trades()[dl.MODEL_CORE] == 1
        assert dl.ingest_closed_trades()[dl.MODEL_CORE] == 0
        state = dl._load_state()
        assert len(state["models"][dl.MODEL_CORE]["dataset"]) == 1

    def test_corrupt_record_excluded(self, iso):
        bad1 = _mk_trade(1); bad1["entry"] = 0        # geçersiz fiyat
        bad2 = _mk_trade(2); del bad2["net_pnl"]      # eksik alan
        bad3 = _mk_trade(3); bad3["model"] = "GHOST"  # bilinmeyen model
        bad4 = _mk_trade(4); bad4["execution_mode"] = "LIVE"
        _write_runtime(iso, [bad1, bad2, bad3, bad4, _mk_trade(5)])
        counts = dl.ingest_closed_trades()
        assert counts[dl.MODEL_CORE] == 1


# ── 5-8: örneklem eşiği, teşhis, adım ve parametre sınırları ───────

class TestSafety:
    def test_insufficient_sample_changes_nothing(self, iso):
        _write_runtime(iso, [_mk_trade(i) for i in range(10)])
        out = dl.run_update({"learning_enabled": True}, force=True)
        assert out is not None
        model = out["models"][dl.MODEL_CORE]
        assert model["diagnosis"] == "INSUFFICIENT_DATA"
        state = dl._load_state()
        ms = state["models"][dl.MODEL_CORE]
        assert ms["challenger"] is None
        assert ms["champion"]["version"] == "BASE"
        assert ms["champion"]["overrides"] == {}

    def test_diagnosis_reason_codes(self, iso):
        # Negatif beklenti + yoğun SL çıkışı → doğru kodlar kanıtla.
        trades = [_mk_trade(i, net=-0.5, result="SL",
                            symbol=f"S{i % 5}USDT")
                  for i in range(30)]
        _write_runtime(iso, trades)
        dl.ingest_closed_trades()
        ms = dl._load_state()["models"][dl.MODEL_CORE]
        m = dl.compute_model_metrics(ms["dataset"])
        diag = dl.diagnose(m, dl.DEFAULT_THRESHOLDS)
        codes = {f["code"] for f in diag["findings"]}
        assert "NEGATIVE_EXPECTANCY" in codes
        assert "STOP_TOO_TIGHT" in codes
        for f in diag["findings"]:  # kanıt zorunlu
            assert "evidence" in f and "sample_size" in f["evidence"]
            assert "risk_note" in f

    def test_step_limited_to_10_pct_and_bounds(self):
        # %10 adım sınırı
        assert dl._clamped_step(0.30, 0.60, "sl_pct") == \
            pytest.approx(0.33)
        # Mutlak sınır clamp
        assert dl._clamped_step(0.16, 0.10, "sl_pct") >= 0.15
        lo, hi = dl.LEARNABLE_BOUNDS["min_confidence"]
        assert dl._clamped_step(89, 200, "min_confidence") <= hi

    def test_max_three_params_per_round(self, iso):
        # Çok sorunlu metrik seti bile ≤3 parametre önerir.
        trades = [_mk_trade(i, net=-0.4, result="SL" if i % 2 else
                            "TIME_EXIT", confidence=45,
                            symbol=f"S{i % 7}USDT")
                  for i in range(80)]
        _write_runtime(iso, trades)
        dl.ingest_closed_trades()
        ms = dl._load_state()["models"][dl.MODEL_CORE]
        m = dl.compute_model_metrics(ms["dataset"])
        diag = dl.diagnose(m, dl.DEFAULT_THRESHOLDS)
        ch = dl.propose_challenger(
            dl.MODEL_CORE, m, diag, dm.DEFAULTS["core"], ms,
            dl.DEFAULT_THRESHOLDS)
        assert ch is not None
        assert len(ch["overrides"]) <= dl.MAX_PARAMS_PER_ROUND
        for k, v in ch["overrides"].items():
            assert k in dl.LEARNABLE_BOUNDS
            lo, hi = dl.LEARNABLE_BOUNDS[k]
            assert lo <= v <= hi

    def test_single_symbol_concentration_blocks_proposal(self, iso):
        trades = [_mk_trade(i, net=-0.4, result="SL")
                  for i in range(80)]  # hepsi BTCUSDT
        _write_runtime(iso, trades)
        dl.ingest_closed_trades()
        ms = dl._load_state()["models"][dl.MODEL_CORE]
        m = dl.compute_model_metrics(ms["dataset"])
        diag = dl.diagnose(m, dl.DEFAULT_THRESHOLDS)
        assert dl.propose_challenger(
            dl.MODEL_CORE, m, diag, dm.DEFAULTS["core"], ms,
            dl.DEFAULT_THRESHOLDS) is None


# ── 9-13: champion/challenger, config yükleme, terfi ───────────────

def _seed_challenger_state(iso, good_shadow: bool) -> None:
    trades = [_mk_trade(i, net=(1.0 if i % 3 else -0.5),
                        confidence=75 if i % 4 else 55,
                        symbol=f"S{i % 6}USDT")
              for i in range(120)]
    _write_runtime(iso, trades)
    dl.ingest_closed_trades()

    def _mut(state):
        ms = dl._model_state(state, dl.MODEL_CORE)
        ms["metrics"] = dl.compute_model_metrics(ms["dataset"])
        ms["challenger"] = {
            "version": "CORE_CHALLENGER_TEST",
            "overrides": {"min_confidence": 66.0},
            "changes": [{"parameter": "min_confidence",
                         "old": 60, "new": 66.0,
                         "reason": "LOW_QUALITY_ENTRIES"}],
            "created_at": dl._now_iso(), "status": "SHADOW",
            "shadow": None}
        sh = dl.shadow_evaluate(ms["challenger"], ms["dataset"])
        if good_shadow:
            # Gerçek alt küme metriği üzerine oynama YOK; iyi senaryo
            # için champion metriğini kötüleştiriyoruz (dürüst kıyas).
            ms["metrics"] = {**ms["metrics"],
                             "expectancy_per_trade": -1.0,
                             "maximum_drawdown": 1e9,
                             "fee_drag": 1e9}
        ms["challenger"]["shadow"] = sh
    dl._update_state(_mut)


class TestChampionChallenger:
    def test_challenger_does_not_touch_champion(self, iso):
        _seed_challenger_state(iso, good_shadow=False)
        ms = dl._load_state()["models"][dl.MODEL_CORE]
        assert ms["champion"]["version"] == "BASE"
        assert ms["champion"]["overrides"] == {}
        assert ms["challenger"]["version"] == "CORE_CHALLENGER_TEST"

    def test_bad_challenger_not_promoted(self, iso):
        _seed_challenger_state(iso, good_shadow=False)
        result = dl.promote(dl.MODEL_CORE)
        assert result["code"] != "PROMOTED"
        assert result["code"].startswith("REJECTED") or \
            result["code"] in ("NOT_EVALUATED", "NO_CHALLENGER")
        ms = dl._load_state()["models"][dl.MODEL_CORE]
        assert ms["champion"]["version"] == "BASE"

    def test_good_challenger_promotes_and_config_loads(self, iso):
        _seed_challenger_state(iso, good_shadow=True)
        result = dl.promote(dl.MODEL_CORE, approved_by="TEST")
        assert result["code"] == "PROMOTED"
        ms = dl._load_state()["models"][dl.MODEL_CORE]
        assert ms["champion"]["version"] == "CORE_CHALLENGER_TEST"
        assert ms["champion"]["previous"]["version"] == "BASE"
        assert ms["promotion_history"][-1]["approved_by"] == "TEST"
        # Dual-model config'i öğrenilmiş değeri GERÇEKTEN yükler.
        dl._OVERRIDE_CACHE.update(mtime=None, data={})
        cfg = dm.get_config()
        assert cfg["core"]["min_confidence"] == 66.0
        assert cfg["core"]["config_version"] == "CORE_CHALLENGER_TEST"
        # OPPORTUNITY etkilenmedi (model ayrımı).
        assert cfg["opportunity"]["min_confidence"] == \
            dm.DEFAULTS["opportunity"]["min_confidence"]
        assert cfg["opportunity"]["config_version"] == "BASE"

    def test_trade_record_carries_config_version(self, iso,
                                                 monkeypatch):
        pos = {"symbol": "BTCUSDT", "model": dl.MODEL_CORE,
               "side": "LONG", "entry": 100.0, "quantity": 1.0,
               "notional_usdt": 100.0, "opened_at": dl._now_iso(),
               "opened_ts": time.time() - 300, "confidence": 70,
               "net_edge_pct": 0.2, "config_version": "CORE_V9"}
        trade = dm._build_trade(pos, 101.0, "TP", time.time())
        assert trade["config_version"] == "CORE_V9"
        assert trade["trade_id"]
        # Normalize katmanı da sürümü taşır.
        rec = dl.normalize_trade(trade)
        assert rec["configuration_version"] == "CORE_V9"


# ── 14: rollback ───────────────────────────────────────────────────

class TestRollback:
    def test_rollback_on_degradation(self, iso):
        _seed_challenger_state(iso, good_shadow=True)
        assert dl.promote(dl.MODEL_CORE)["code"] == "PROMOTED"
        # Terfi sonrası işlemler kötü: negatif beklenti → rollback.
        post = [_mk_trade(1000 + i, net=-1.0, result="SL",
                          symbol=f"P{i % 4}USDT",
                          version="CORE_CHALLENGER_TEST")
                for i in range(15)]

        def _mut(state):
            ms = dl._model_state(state, dl.MODEL_CORE)
            ms["dataset"].extend(dl.normalize_trade(t) for t in post)
            entry = dl._check_rollback(ms, dl.DEFAULT_THRESHOLDS)
            assert entry is not None
            assert entry["reason"] in dl.ROLLBACK_CODES
        dl._update_state(_mut)
        ms = dl._load_state()["models"][dl.MODEL_CORE]
        assert ms["champion"]["version"] == "BASE"  # eski champion
        assert ms["rollback_history"][-1]["from_version"] == \
            "CORE_CHALLENGER_TEST"
        # Öğrenilmiş overlay da eski champion'a döner.
        dl._OVERRIDE_CACHE.update(mtime=None, data={})
        assert dm.get_config()["core"]["config_version"] == "BASE"


# ── 15-16: restart persistence + git temizliği ─────────────────────

class TestPersistenceAndGit:
    def test_state_survives_reload(self, iso):
        _seed_challenger_state(iso, good_shadow=True)
        dl.promote(dl.MODEL_CORE)
        # "Restart": modül state'i yalnız dosyadan okunur.
        state = dl._load_state()
        assert state["models"][dl.MODEL_CORE]["champion"][
            "version"] == "CORE_CHALLENGER_TEST"
        dl._OVERRIDE_CACHE.update(mtime=None, data={})
        assert dl.champion_overrides(dl.MODEL_CORE)[
            "config_version"] == "CORE_CHALLENGER_TEST"

    def test_runtime_files_gitignored(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for name in ("alpha20_v1/dual_learning_state.json",
                     "alpha20_v1/dual_learning_state.lock",
                     "alpha20_v1/dual_learning_history.jsonl"):
            assert name in gi, name

    def test_atomic_write_uses_tmp_replace(self):
        src = (ROOT / "alpha20_v1/dual_learning.py").read_text(
            encoding="utf-8")
        assert "tmp.replace(STATE_PATH)" in src
        assert "fcntl.flock" in src


# ── 17-18: LIVE ORDERS DISABLED + güvenlik parametreleri ───────────

class TestSecurityBoundaries:
    def test_no_live_order_paths(self):
        # Öğrenme modülü ağa çıkmaz ve emir yolu içermez.
        src = (ROOT / "alpha20_v1/dual_learning.py").read_text(
            encoding="utf-8")
        for banned in ("import requests", "api.binance",
                       "/api/v3/order", "signature", "api_key",
                       "API_KEY", "urlopen"):
            assert banned not in src, banned

    def test_learnable_allowlist_excludes_safety_params(self):
        banned = {"total_max_open_positions", "position_usdt",
                  "max_open_positions", "enabled", "execution_mode",
                  "api_key", "emergency_stop", "monitor_seconds"}
        assert not banned & set(dl.LEARNABLE_BOUNDS)

    def test_champion_overrides_filters_unknown_keys(self, iso):
        def _mut(state):
            ms = dl._model_state(state, dl.MODEL_CORE)
            ms["champion"]["overrides"] = {
                "min_confidence": 65.0,
                "position_usdt": 99999.0,       # güvenlik alanı — atılır
                "total_max_open_positions": 99,  # atılır
                "sl_pct": 99.0}                  # sınıra clamplenir
        dl._update_state(_mut)
        dl._OVERRIDE_CACHE.update(mtime=None, data={})
        ov = dl.champion_overrides(dl.MODEL_CORE)["overrides"]
        assert set(ov) == {"min_confidence", "sl_pct"}
        assert ov["sl_pct"] <= dl.LEARNABLE_BOUNDS["sl_pct"][1]

    def test_learning_disabled_stops_run(self, iso):
        assert dl.run_update({"learning_enabled": False},
                             force=True) is None


# ── 19: API/UI gerçek state ────────────────────────────────────────

class TestApiAndUi:
    def test_status_honest_when_empty(self, iso):
        st = dl.status()
        assert st["mode"] == "AUTO_SHADOW"
        assert st["auto_promote"] is False
        core = st["models"][dl.MODEL_CORE]
        assert core["data_sufficiency"] == "INSUFFICIENT_DATA"
        assert core["promotion_readiness"] in ("NO_CHALLENGER",
                                               "NOT_EVALUATED")

    def test_api_route_and_ui_wired(self):
        app_src = (ROOT / "app.py").read_text(encoding="utf-8")
        assert '"/api/dual-model/learning"' in app_src
        assert '"/api/dual-model/learning/promote"' in app_src
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "/api/dual-model/learning" in js
        assert "renderLearning" in js
        # UI sahte veri basmaz — dürüst kodlar aynen görünür.
        for code in ("NO_CHALLENGER", "NOT_EVALUATED",
                     "INSUFFICIENT_DATA"):
            assert code in js, code
        tpl = (ROOT / "templates/trading_home.html").read_text(
            encoding="utf-8")
        assert 'id="th-learn-core"' in tpl
        assert 'id="th-learn-opp"' in tpl


# ── 20: legacy learning_engine bozulmadı + scheduler bağı ──────────

class TestIntegrationWiring:
    def test_learning_engine_bridge_exists(self):
        import learning_engine as le
        assert hasattr(le, "run_dual_learning_update")
        src = (ROOT / "alpha20_v1/learning_engine.py").read_text(
            encoding="utf-8")
        assert "dual_learning" in src

    def test_controller_calls_bridge_not_second_scheduler(self):
        src = (ROOT / "alpha20_v1/auto_controller.py").read_text(
            encoding="utf-8")
        assert "run_dual_learning_update" in src
        # İkinci bir Thread/scheduler açılmıyor (mevcut döngü içinde).
        idx = src.index("run_dual_learning_update")
        assert "Thread(" not in src[idx - 200:idx + 600]

    def test_bridge_failure_does_not_crash_engine(self, iso,
                                                  monkeypatch):
        import learning_engine as le
        monkeypatch.setattr(dl, "run_update",
                            lambda *a, **k: 1 / 0)
        assert le.run_dual_learning_update(
            {"learning_enabled": True}) is None
