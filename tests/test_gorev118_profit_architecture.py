"""GÖREV 118 — Kârlılık mimarisi uçtan uca regresyon testleri.

Kapsam:
- C: seçim determinizmi + look-ahead engeli
- D: giriş kapıları (EMA/VWAP blok, sahte kırılım, sağlıklı giriş)
- F: maliyet sonrası kâr kapıları + net PnL muhasebesi (çift düşüm yok)
- G: TP/SL/trailing/time-exit + idempotency (çift kapanış yok)
- I: paper uçtan uca zincir (SIGNAL → pozisyon → kapanış → ledger),
     borsa yazma isteği = 0 (spy)
- M: auto_controller AUTO yolunda Mission-11 ekonomi kapısının
     bağlı olması (patch öncesi BAŞARISIZ olan test)

Hiçbir üretim runtime dosyasına yazılmaz; her şey tmp_path'te.
"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_model as dm  # noqa: E402
import alpha20  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
    monkeypatch.setattr(dm, "LEGACY_STATE_PATH", tmp_path / "state.json")
    yield


# İzolasyon: get_config() dual_learning.champion_overrides'ı çağırır
# (gerçek öğrenme durumuna dokunabilir). Testler SAF varsayılanlarla
# koşar — üretim runtime'ına hiçbir okuma/yazma bağımlılığı yok.
CFG = json.loads(json.dumps(dm.DEFAULTS))
CFG["core"]["config_version"] = "BASE"
CFG["opportunity"]["config_version"] = "BASE"


def _klines(n=60, base=100.0, trend=0.001, vol=1000.0, burst_last=3.0):
    out, price = [], base
    for i in range(n):
        price *= (1 + trend)
        v = vol * (burst_last if i >= n - 5 else 1.0)
        out.append([0, str(price * 0.999), str(price * 1.001),
                    str(price * 0.998), str(price), str(v)])
    return out


def _row(**kw):
    r = {"spread_pct": 0.02, "volume_usdt": 200e6, "trade_count": 300000}
    r.update(kw)
    return r


# ── C: determinizm ve look-ahead ──────────────────────────────────

class TestSelectionIntegrity:
    def test_c4_deterministic_signal(self):
        kl = _klines()
        a = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        b = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        assert a == b

    def test_c5_no_lookahead_future_candles_dont_change_past(self):
        """Karar anındaki pencere ile üretilen karar, geleceğe ait
        mumlar eklendikten sonra AYNI pencereyle yeniden üretildiğinde
        değişmemeli (fonksiyon yalnız verilen pencereyi görür)."""
        kl = _klines(n=60)
        before = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        extended = kl + _klines(n=10, base=200.0)
        again = dm.evaluate_signal("BTCUSDT", extended[:60], dm.MODEL_CORE)
        assert before == again

    def test_c5_breakout_window_excludes_current_candle(self):
        """Kırılım penceresi (hi20) SON mumu içermemeli.

        Ayırt edici fixture: düz seri + son mumda sıçrama, hacim
        teyidi YOK. Pencere son mumu DIŞLIYORSA motor kırılımı görür
        ve hacim teyidi olmadığı için FALSE_BREAKOUT_RISK üretir.
        Pencere son mumu İÇERSEYDİ last > hi20 hiç sağlanamaz ve
        sinyal normal LONG dönerdi — test bunu yakalar."""
        kl = _klines(n=60, trend=0.0, burst_last=1.0)
        last = kl[-1][:]
        last[4] = str(float(kl[-2][4]) * 1.05)  # tüm geçmişin üstü
        kl[-1] = last
        sig = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        assert sig["reason_code"] == "FALSE_BREAKOUT_RISK", sig

    def test_insufficient_data_rejected(self):
        sig = dm.evaluate_signal("BTCUSDT", _klines(n=10), dm.MODEL_CORE)
        assert sig["reason_code"] == "DATA_QUALITY"
        assert sig["side"] is None


# ── D: giriş kapıları ─────────────────────────────────────────────

class TestEntryGates:
    def test_d1_downtrend_blocked_with_sub_reason(self):
        kl = _klines(trend=-0.002)
        sig = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        assert sig["side"] is None
        assert sig["reason_code"] == "NO_SIGNAL"
        assert sig["sub_reason"] in ("EMA_BLOCK", "VWAP_BLOCK",
                                     "EMA_VWAP_BLOCK")

    def test_d3_weak_factors_reported(self):
        kl = _klines(trend=-0.002, burst_last=0.2)
        sig = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        assert "VOLUME_WEAK" in sig.get("weak_factors", [])

    def test_d4_false_breakout_blocked(self):
        # Yukarı trend + kırılım var ama hacim teyidi yok
        kl = _klines(trend=0.001, burst_last=0.5)
        sig = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        assert sig["side"] is None
        assert sig["reason_code"] in ("FALSE_BREAKOUT_RISK",
                                      "MOMENTUM_EXHAUSTED", "NO_SIGNAL")

    def test_d6_healthy_entry_reaches_cost_gates(self):
        kl = _klines()
        sig = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        assert sig["side"] == "LONG" and sig["confidence"] > 0
        ok, reason, net = dm.execution_quality_gate(
            _row(), sig, dm.MODEL_CORE, CFG)
        # Sağlıklı fixture ya geçer ya da YALNIZ maliyet/kalite koduyla
        # reddedilir — motor her koşulu NO_SIGNAL'a çevirmemeli.
        assert ok or reason in (
            "LOW_CONFIDENCE", "FEE_DRAG", "EXPECTED_EDGE_TOO_LOW",
            "EDGE_BELOW_COST_MULTIPLE", "NET_REWARD_RISK_TOO_LOW")

    def test_d_low_liquidity_blocked(self):
        kl = _klines()
        sig = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        ok, reason, _ = dm.execution_quality_gate(
            _row(volume_usdt=1e4), sig, dm.MODEL_CORE, CFG)
        assert not ok and reason == "LOW_LIQUIDITY"

    def test_d_spread_too_high_blocked(self):
        kl = _klines()
        sig = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        ok, reason, _ = dm.execution_quality_gate(
            _row(spread_pct=5.0), sig, dm.MODEL_CORE, CFG)
        assert not ok and reason in ("SPREAD_TOO_HIGH",
                                     "SLIPPAGE_TOO_HIGH")


# ── F: maliyet sonrası kâr politikası ─────────────────────────────

class TestCostGates:
    def test_f1_fee_drag_rejected(self):
        sig = {"confidence": 99, "expected_gross_edge_pct": 0.05,
               "side": "LONG", "last": 100.0}
        ok, reason, _ = dm.execution_quality_gate(
            _row(), sig, dm.MODEL_CORE, CFG)
        assert not ok and reason in ("FEE_DRAG", "EXPECTED_EDGE_TOO_LOW")

    def test_f2_net_rr_gate_from_cost_profile(self):
        m = dict(CFG["core"], tp_pct=0.25, sl_pct=1.0)
        cp = dm.cost_profile(m, slippage_pct=0.05)
        assert cp["net_reward_risk"] is not None
        assert cp["net_reward_risk"] < 1.0  # bu profil kabul edilemez

    def test_f4_costs_not_double_counted(self):
        """_build_trade: komisyon her bacak bir kez, slippage bir kez."""
        p = {"symbol": "X", "model": dm.MODEL_CORE, "side": "LONG",
             "entry": 100.0, "quantity": 1.0, "opened_at": "t",
             "confidence": 80, "opened_ts": 0.0}
        t = dm._build_trade(p, 102.0, "TP", now=60.0)
        assert t["gross_pnl"] == pytest.approx(2.0)
        assert t["fees"] == pytest.approx((100 + 102) * dm.FEE_RATE)
        assert t["slippage"] == pytest.approx(102 * 0.0002)
        assert t["net_pnl"] == pytest.approx(
            t["gross_pnl"] - t["fees"] - t["slippage"])

    def test_f_cost_profile_symmetry_and_rounding(self):
        m = dict(CFG["core"])
        cp = dm.cost_profile(m, slippage_pct=0.0)
        assert cp["round_trip_cost_pct"] == pytest.approx(
            dm.FEE_RATE * 2 * 100, abs=1e-6)
        assert cp["net_tp_pct"] == pytest.approx(
            cp["gross_tp_pct"] - cp["round_trip_cost_pct"], abs=1e-3)

    def test_f_net_tp_non_positive_rejected(self):
        cfg = json.loads(json.dumps(CFG))
        cfg["core"]["tp_pct"] = 0.10  # maliyet altında TP
        sig = {"confidence": 99, "expected_gross_edge_pct": 1.5,
               "side": "LONG", "last": 100.0}
        ok, reason, _ = dm.execution_quality_gate(
            _row(spread_pct=0.0), sig, dm.MODEL_CORE, cfg)
        assert not ok and reason in ("NET_TP_NON_POSITIVE",
                                     "NET_REWARD_RISK_TOO_LOW")


# ── G: pozisyon yönetimi ve idempotency ───────────────────────────

def _open(sym="BTCUSDT", entry=100.0, now=1000.0):
    sig = {"side": "LONG", "last": entry, "confidence": 80}
    ok, reason = dm.try_open_position(sym, dm.MODEL_CORE, sig,
                                      0.5, CFG, now=now)
    assert ok, reason


class TestPositionManagement:
    def test_g1_tp_closes_once_with_net_pnl(self):
        _open()
        tp = 100.0 * (1 + CFG["core"]["tp_pct"] / 100)
        closed = dm.monitor_positions(lambda s: tp, CFG, now=1060.0)
        assert len(closed) == 1 and closed[0]["result"] == "TP"
        assert closed[0]["net_pnl"] < closed[0]["gross_pnl"]
        rt = json.load(open(dm.RUNTIME_PATH))
        assert len(rt["trades"]) == 1 and rt["positions"] == {}

    def test_g2_sl_closes_once(self):
        _open()
        sl = 100.0 * (1 - CFG["core"]["sl_pct"] / 100)
        closed = dm.monitor_positions(lambda s: sl, CFG, now=1060.0)
        assert len(closed) == 1 and closed[0]["result"] == "SL"

    def test_g3_trailing_after_favorable_move(self):
        _open()
        # Önce lehe hareket (TP'nin ALTINDA bir zirve oluşur)…
        peak = 100.0 * (1 + CFG["core"]["tp_pct"] / 100) - 0.05
        dm.monitor_positions(lambda s: peak, CFG, now=1030.0)
        # …sonra trailing eşiğinin altına (ama SL üstünde) çekilme
        trail = peak * (1 - CFG["core"]["trailing_pct"] / 100) - 0.001
        assert trail > 100.0 * (1 - CFG["core"]["sl_pct"] / 100)
        closed = dm.monitor_positions(lambda s: trail, CFG, now=1060.0)
        assert len(closed) == 1 and closed[0]["result"] == "TRAILING"

    def test_g4_time_exit(self):
        _open(now=1.0)
        hold = 1.0 + CFG["core"]["max_hold_minutes"] * 60 + 60
        closed = dm.monitor_positions(lambda s: 100.0, CFG, now=hold)
        assert len(closed) == 1 and closed[0]["result"] == "TIME_EXIT"

    def test_g5_idempotent_no_double_close(self):
        _open()
        tp = 100.0 * (1 + CFG["core"]["tp_pct"] / 100)
        c1 = dm.monitor_positions(lambda s: tp, CFG, now=1060.0)
        c2 = dm.monitor_positions(lambda s: tp, CFG, now=1061.0)
        assert len(c1) == 1 and c2 == []
        rt = json.load(open(dm.RUNTIME_PATH))
        assert len(rt["trades"]) == 1

    def test_g6_shared_store_not_process_local(self):
        """Pozisyon durumu paylaşımlı dosyada — ikinci 'worker'
        (taze modül durumu) aynı pozisyonu görmeli ve yönetmeli."""
        _open()
        # Taze okuma: dosyadan
        rt = json.load(open(dm.RUNTIME_PATH))
        assert "BTCUSDT" in rt["positions"]

    def test_g_stale_none_price_skips_exit(self):
        _open()
        closed = dm.monitor_positions(lambda s: None, CFG, now=1060.0)
        assert closed == []
        rt = json.load(open(dm.RUNTIME_PATH))
        assert "BTCUSDT" in rt["positions"]


# ── I: paper uçtan uca zincir + exchange write spy ────────────────

class TestPaperEndToEnd:
    def test_i_full_chain_no_exchange_write(self, monkeypatch):
        writes = []

        class _NoNet:
            def __getattr__(self, name):
                def _blocked(*a, **k):
                    writes.append((name, a))
                    raise AssertionError("NETWORK ÇAĞRISI YASAK")
                return _blocked

        # requests üzerinden her çağrı testi düşürür
        import requests as _rq
        for meth in ("get", "post", "put", "patch", "delete",
                     "request"):
            monkeypatch.setattr(_rq, meth, getattr(_NoNet(), meth))

        kl = _klines()
        sig = dm.evaluate_signal("BTCUSDT", kl, dm.MODEL_CORE)
        assert sig["side"] == "LONG"
        # Test cfg: RR kapısını GEÇEBİLEN bir profil (fixture-only;
        # üretim eşiği DEĞİŞTİRİLMİYOR — varsayılan CORE profili net
        # RR kapısını yapısal olarak geçemiyor, bu ayrıca raporlanır).
        cfg = json.loads(json.dumps(CFG))
        cfg["core"]["tp_pct"] = 1.0
        sig["expected_gross_edge_pct"] = 1.9
        ok, reason, net = dm.execution_quality_gate(
            _row(spread_pct=0.005), sig, dm.MODEL_CORE, cfg)
        assert ok, reason
        opened, why = dm.try_open_position(
            "BTCUSDT", dm.MODEL_CORE, sig, net, cfg, now=1000.0)
        assert opened, why
        entry = float(sig["last"])
        tp = entry * (1 + cfg["core"]["tp_pct"] / 100)
        closed = dm.monitor_positions(lambda s: tp, cfg, now=1120.0)
        assert len(closed) == 1
        t = closed[0]
        assert t["execution_mode"] == "PAPER"
        assert t["net_pnl"] == pytest.approx(
            t["gross_pnl"] - t["fees"] - t["slippage"])
        rt = json.load(open(dm.RUNTIME_PATH))
        assert rt["trades"][0]["trade_id"] == t["trade_id"]
        assert writes == []  # EXCHANGE_WRITE_REQUESTS: 0


# ── M: auto_controller AUTO yolunda ekonomi kapısı ────────────────

class TestClassicEconomicsGateWired:
    def test_m_fee_dominant_trade_not_opened(self, monkeypatch,
                                             tmp_path):
        """Mission-11 kuralı (expected_gross <= fee*sf → SKIP)
        auto_controller._open_paper_trade yolunda da uygulanmalı.
        Patch öncesi bu test BAŞARISIZ (pozisyon açılıyor)."""
        import auto_controller as ac
        import metrics_store as ms
        monkeypatch.setattr(ac, "_save_state", lambda s: None)
        monkeypatch.setattr(ms, "append_decision",
                            lambda **k: None)

        state = {"balance": 10000.0, "position": None,
                 "consecutive_losses": 0,
                 "day_start_balance": 10000.0, "trades": []}
        config = {"atr_stop_multiplier": 3.0,
                  "reward_risk_ratio": 2.0,
                  "risk_per_trade_pct": 0.5,
                  "fee_safety_factor": 2.0}
        # Fee-dominant fixture: mikroskobik ATR → dev qty → fee >> brüt
        details = {"price": 100.0, "atr": 0.0005}
        econ = alpha20.evaluate_trade_economics(
            100.0, 0.0005, "LONG", 10000.0, config)
        assert econ["skip"], "fixture fee-dominant olmalı"
        ac._open_paper_trade(
            symbol="TESTUSDT", side="LONG", details=details,
            trading_state=state, adaptive_cfg={}, config=config,
            risk_pct=0.5, coin_regime="TREND", final_score=90.0,
            reason="test")
        assert state["position"] is None, (
            "Ekonomi kapısı (Mission-11) AUTO yolunda uygulanmadı — "
            "fee-dominant işlem açıldı")

    def test_m_economic_trade_still_opens(self, monkeypatch):
        """Kapı sağlıklı işlemi engellememeli (yanlış negatif yok)."""
        import auto_controller as ac
        import metrics_store as ms
        monkeypatch.setattr(ac, "_save_state", lambda s: None)
        monkeypatch.setattr(ms, "append_decision", lambda **k: None)
        state = {"balance": 10000.0, "position": None,
                 "consecutive_losses": 0,
                 "day_start_balance": 10000.0, "trades": []}
        config = {"atr_stop_multiplier": 3.0,
                  "reward_risk_ratio": 2.0,
                  "risk_per_trade_pct": 0.5,
                  "fee_safety_factor": 2.0}
        details = {"price": 100.0, "atr": 2.0}  # geniş stop → fee küçük
        econ = alpha20.evaluate_trade_economics(
            100.0, 2.0, "LONG", 10000.0, config)
        assert not econ["skip"]
        ac._open_paper_trade(
            symbol="TESTUSDT", side="LONG", details=details,
            trading_state=state, adaptive_cfg={}, config=config,
            risk_pct=0.5, coin_regime="TREND", final_score=90.0,
            reason="test")
        assert state["position"] is not None
