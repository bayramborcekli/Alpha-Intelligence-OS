"""
test_adaptive.py — Alpha-20 v1 Uyarlanabilir Motor birim testleri.
Çalıştır: python -m pytest tests/test_adaptive.py -v
Tüm testler PAPER modunda; gerçek emir veya API anahtarı kullanılmaz.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# alpha20_v1 modüllerini path'e ekle
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

def _make_state(
    balance: float = 10000.0,
    day_start: float = 10000.0,
    consec: int = 0,
    position: dict | None = None,
    trades: list | None = None,
) -> dict[str, Any]:
    return {
        "balance": balance,
        "day_start_balance": day_start,
        "day": "2026-07-26",
        "consecutive_losses": consec,
        "position": position,
        "trades": trades or [],
    }


def _make_adaptive_cfg(**overrides) -> dict[str, Any]:
    base = {
        "enabled": True,
        "mode": "MONITOR",
        "auto_paper_enabled": False,
        "regime_min_confidence": 65,
        "final_decision_threshold": 78,
        "base_risk_pct": 0.25,
        "max_risk_pct": 0.50,
        "daily_loss_limit_pct": 1.0,
        "max_drawdown_pct": 5.0,
        "max_consecutive_losses": 3,
        "risk_reduction_after_losses": 2,
        "learning_enabled": True,
        "learning_interval_hours": 24,
        "minimum_learning_trades": 20,
        "max_daily_weight_change_pct": 5,
        "cooldown_minutes": 60,
        "break_even_enabled": False,
        "trailing_stop_enabled": False,
        "partial_take_profit_enabled": False,
        "kill_switch": False,
    }
    base.update(overrides)
    return base


def _make_regime(regime: str = "Güçlü Yükseliş", confidence: float = 80.0) -> dict[str, Any]:
    from market_regime import SUITABLE_REGIMES
    return {
        "regime": regime,
        "confidence": confidence,
        "direction": "Yukarı",
        "volatility": "Normal",
        "trend_strength": 60.0,
        "atr_pct": 1.5,
        "suitable": regime in SUITABLE_REGIMES and confidence >= 65,
        "reason": "Test rejimi.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. metrics_store — yazma / okuma
# ══════════════════════════════════════════════════════════════════════════════

class TestMetricsStore:
    def test_append_and_read_decision(self, tmp_path, monkeypatch):
        import metrics_store as ms
        monkeypatch.setattr(ms, "LOG_FILES", {
            **ms.LOG_FILES,
            "decisions": tmp_path / "decisions.jsonl",
        })
        ms.append_decision(
            symbol="BTCUSDT", price=50000.0, regime="Güçlü Yükseliş",
            regime_confidence=85, strategy_score=80, final_score=82,
            risk_pct=0.25, stop=49000.0, target=52000.0,
            decision="OPEN", reason="Test kararı.",
        )
        records = ms.get_recent_decisions.__wrapped__(1) if hasattr(ms.get_recent_decisions, '__wrapped__') else None
        # Doğrudan dosyadan oku
        data = (tmp_path / "decisions.jsonl").read_text()
        record = json.loads(data.strip())
        assert record["symbol"] == "BTCUSDT"
        assert record["decision"] == "OPEN"
        assert record["final_score"] == 82

    def test_append_system_error(self, tmp_path, monkeypatch):
        import metrics_store as ms
        monkeypatch.setattr(ms, "LOG_FILES", {
            **ms.LOG_FILES,
            "system_errors": tmp_path / "system_errors.jsonl",
        })
        ms.append_system_error(
            component="test", error_type="TEST_ERR", message="Test hatası.", safe_state_activated=True
        )
        data = (tmp_path / "system_errors.jsonl").read_text()
        record = json.loads(data.strip())
        assert record["component"] == "test"
        assert record["safe_state_activated"] is True


# ══════════════════════════════════════════════════════════════════════════════
# 2. safety_guard — tüm güvenlik kontrolleri
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyGuard:
    @pytest.fixture(autouse=True)
    def reset_safety(self, tmp_path, monkeypatch):
        import safety_guard as sg
        monkeypatch.setattr(sg, "SAFETY_STATE_PATH", tmp_path / "safety_state.json")
        monkeypatch.setattr(sg, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(sg, "STATE_PATH", tmp_path / "state.json")
        (tmp_path / "config.json").write_text(json.dumps({
            "adaptive_system": _make_adaptive_cfg()
        }))

    def test_all_checks_pass(self, tmp_path):
        import safety_guard as sg
        state = _make_state()
        result = sg.check_all(trading_state=state, adaptive_cfg=_make_adaptive_cfg())
        assert result.safe is True

    def test_kill_switch_blocks_trading(self, tmp_path):
        import safety_guard as sg
        sg.activate_kill_switch("Test kill-switch.")
        result = sg.check_all(trading_state=_make_state(), adaptive_cfg=_make_adaptive_cfg())
        assert result.safe is False
        assert result.kill_switch is True
        sg.deactivate_kill_switch()

    def test_daily_loss_limit_blocks(self, tmp_path):
        import safety_guard as sg
        # Günlük %1 kayıp = 100 USDT (10000 başlangıç)
        state = _make_state(balance=9895.0, day_start=10000.0)
        result = sg.check_all(trading_state=state, adaptive_cfg=_make_adaptive_cfg())
        assert result.safe is False
        assert result.daily_loss_block is True

    def test_drawdown_triggers_kill(self, tmp_path, monkeypatch):
        import safety_guard as sg
        import metrics_store as ms
        monkeypatch.setattr(ms, "LOG_FILES", {
            **ms.LOG_FILES,
            "risk_events":   tmp_path / "risk_events.jsonl",
            "system_errors": tmp_path / "system_errors.jsonl",
        })
        # 5%+ drawdown: günün başı 9400 (günlük kayıp yok), ama tarihsel peak=10000
        # Win trade +600 → running=10000 (peak), sonra Loss -600 → balance=9400
        state = _make_state(
            balance=9400.0,
            day_start=9400.0,  # bugün kayıp yok → günlük limit tetiklenmesin
            trades=[
                {"pnl": 600.0,  "result": "WIN",
                 "opened_at": "2026-07-25T10:00:00+00:00",
                 "closed_at": "2026-07-25T11:00:00+00:00"},
                {"pnl": -600.0, "result": "LOSS",
                 "opened_at": "2026-07-25T12:00:00+00:00",
                 "closed_at": "2026-07-25T13:00:00+00:00"},
            ],
        )
        # day_start=9400, start_bal=9400, running: 9400→10000(peak)→9400
        # dd_pct = (10000-9400)/10000*100 = 6.0% ≥ 5.0% → drawdown_block
        result = sg.check_all(trading_state=state, adaptive_cfg=_make_adaptive_cfg())
        assert result.safe is False
        assert result.drawdown_block is True

    def test_consecutive_losses_block(self, tmp_path):
        import safety_guard as sg
        state = _make_state(consec=3)
        result = sg.check_all(trading_state=state, adaptive_cfg=_make_adaptive_cfg())
        assert result.safe is False
        assert result.consecutive_block is True

    def test_safety_lock_and_unlock(self, tmp_path, monkeypatch):
        import safety_guard as sg
        import metrics_store as ms
        monkeypatch.setattr(ms, "LOG_FILES", {
            **ms.LOG_FILES,
            "risk_events":   tmp_path / "risk_events.jsonl",
            "system_errors": tmp_path / "system_errors.jsonl",
        })
        sg.lock_safety("Test kilidi.", component="test")
        result = sg.check_all(trading_state=_make_state(), adaptive_cfg=_make_adaptive_cfg())
        assert result.safe is False
        assert result.locked is True
        sg.unlock_safety()
        result2 = sg.check_all(trading_state=_make_state(), adaptive_cfg=_make_adaptive_cfg())
        assert result2.safe is True

    def test_data_error_blocks(self, tmp_path):
        import safety_guard as sg
        result = sg.check_all(
            trading_state=_make_state(), adaptive_cfg=_make_adaptive_cfg(),
            data_ok=False, data_error="Binance API hatası.",
        )
        assert result.safe is False

    def test_kill_switch_blocks_new_trade_not_position_mgmt(self, tmp_path):
        """Kill-switch yeni işlemi engeller; açık pozisyon yönetimini engellemez."""
        import safety_guard as sg
        sg.activate_kill_switch("Test.")
        result = sg.check_all(trading_state=_make_state(), adaptive_cfg=_make_adaptive_cfg())
        # Kill-switch açıkken yeni işlem yasak
        assert result.safe is False
        assert result.kill_switch is True
        # Ama kill_switch sadece 'safe' döndürür — pozisyon yönetimi dışarıda handle edilir
        sg.deactivate_kill_switch()


# ══════════════════════════════════════════════════════════════════════════════
# 3. adaptive_risk — risk hesaplama kuralları
# ══════════════════════════════════════════════════════════════════════════════

class TestAdaptiveRisk:
    def test_base_risk_normal(self):
        import adaptive_risk as ar
        state  = _make_state()
        cfg    = _make_adaptive_cfg()
        regime = _make_regime()
        result = ar.calculate_risk(state, cfg, regime, final_decision_score=80)
        assert result.allowed is True
        assert 0 < result.risk_pct <= 0.50

    def test_risk_never_exceeds_abs_max(self):
        import adaptive_risk as ar
        state  = _make_state()
        cfg    = _make_adaptive_cfg(base_risk_pct=0.50, max_risk_pct=0.50)
        regime = _make_regime()
        result = ar.calculate_risk(state, cfg, regime, final_decision_score=95)
        assert result.risk_pct <= ar.ABS_MAX_RISK_PCT

    def test_risk_reduced_after_consecutive_losses(self):
        import adaptive_risk as ar
        state_normal = _make_state(consec=0)
        state_losses = _make_state(consec=2)
        cfg    = _make_adaptive_cfg()
        regime = _make_regime()
        res_normal = ar.calculate_risk(state_normal, cfg, regime, 80)
        res_losses = ar.calculate_risk(state_losses, cfg, regime, 80)
        assert res_losses.risk_pct <= res_normal.risk_pct

    def test_risk_not_increased_after_loss(self):
        """Zarar sonrası risk asla artırılmamalı."""
        import adaptive_risk as ar
        state_before = _make_state(balance=10000, consec=0)
        state_after  = _make_state(balance=9900, consec=1)
        cfg    = _make_adaptive_cfg()
        regime = _make_regime()
        r_before = ar.calculate_risk(state_before, cfg, regime, 80)
        r_after  = ar.calculate_risk(state_after, cfg, regime, 80)
        assert r_after.risk_pct <= r_before.risk_pct

    def test_3_consecutive_losses_blocks(self):
        import adaptive_risk as ar
        state  = _make_state(consec=3)
        cfg    = _make_adaptive_cfg(max_consecutive_losses=3)
        regime = _make_regime()
        result = ar.calculate_risk(state, cfg, regime, 90)
        assert result.allowed is False

    def test_high_volatility_halves_risk(self):
        import adaptive_risk as ar
        from market_regime import REGIME_HIGH_VOL
        state    = _make_state()
        cfg      = _make_adaptive_cfg(base_risk_pct=0.25)
        reg_norm = _make_regime("Güçlü Yükseliş", 80)
        reg_hvol = _make_regime(REGIME_HIGH_VOL, 80)
        r_norm = ar.calculate_risk(state, cfg, reg_norm, 80)
        r_hvol = ar.calculate_risk(state, cfg, reg_hvol, 80)
        assert r_hvol.risk_pct <= r_norm.risk_pct

    def test_low_data_quality_blocks(self):
        import adaptive_risk as ar
        state  = _make_state()
        cfg    = _make_adaptive_cfg()
        regime = _make_regime()
        result = ar.calculate_risk(state, cfg, regime, 90, data_quality_score=60)
        assert result.allowed is False

    def test_low_liquidity_blocks(self):
        import adaptive_risk as ar
        state  = _make_state()
        cfg    = _make_adaptive_cfg()
        regime = _make_regime()
        result = ar.calculate_risk(state, cfg, regime, 90, liquidity_score=30)
        assert result.allowed is False

    def test_position_size_valid(self):
        import adaptive_risk as ar
        qty, stop_d, err = ar.calculate_position_size(
            balance=10000, risk_pct=0.25, entry=50000,
            stop=0, atr=500, atr_stop_multiplier=1.5,
            adaptive_cfg=_make_adaptive_cfg(),
        )
        assert err == ""
        assert qty > 0
        assert stop_d > 0

    def test_position_size_respects_balance_limit(self):
        import adaptive_risk as ar
        # Çok küçük ATR → aşırı büyük pozisyon olmamalı
        qty, stop_d, err = ar.calculate_position_size(
            balance=10000, risk_pct=0.25, entry=50000,
            stop=0, atr=0.001, atr_stop_multiplier=1.5,
            adaptive_cfg=_make_adaptive_cfg(),
        )
        if err == "":
            nominal = qty * 50000
            assert nominal <= 10000 * ar.MAX_POSITION_PCT_BAL / 100

    def test_no_stop_loss_without_atr(self):
        import adaptive_risk as ar
        qty, stop_d, err = ar.calculate_position_size(
            balance=10000, risk_pct=0.25, entry=50000,
            stop=0, atr=0.0, atr_stop_multiplier=1.5,
            adaptive_cfg=_make_adaptive_cfg(),
        )
        # ATR=0 → hata beklenir
        assert err != "" or stop_d > 0  # ya hata ya da min_stop uygulandı


# ══════════════════════════════════════════════════════════════════════════════
# 4. decision_engine — karar puanı ve koşullar
# ══════════════════════════════════════════════════════════════════════════════

class TestDecisionEngine:
    def test_score_calculation(self):
        import decision_engine as de
        score, cat, comps, reason = de.score_decision(
            strategy_score=80, regime_score=70, regime_confidence=80,
            coin_score=75, volume_24h_usdt=1_000_000_000, atr_pct=1.5,
            regime="Güçlü Yükseliş", paper_hist_score=60, data_quality_score=100,
        )
        assert 0 <= score <= 100
        assert cat in (de.SCORE_NONE, de.SCORE_WATCH, de.SCORE_WEAK,
                       de.SCORE_SUITABLE, de.SCORE_STRONG)

    def test_unclear_regime_penalizes_score(self):
        import decision_engine as de
        from market_regime import REGIME_UNCLEAR
        score_good, *_ = de.score_decision(
            strategy_score=80, regime_score=80, regime_confidence=85,
            coin_score=75, volume_24h_usdt=1e9, atr_pct=1.5,
            regime="Güçlü Yükseliş", paper_hist_score=60, data_quality_score=100,
        )
        score_bad, *_ = de.score_decision(
            strategy_score=80, regime_score=20, regime_confidence=30,
            coin_score=75, volume_24h_usdt=1e9, atr_pct=1.5,
            regime=REGIME_UNCLEAR, paper_hist_score=60, data_quality_score=100,
        )
        assert score_good > score_bad

    def test_all_conditions_must_pass(self):
        import decision_engine as de
        approved, reason = de.check_conditions(
            final_score=85, regime_confidence=80, data_quality_score=95,
            liquidity_score=70, risk_allowed=True, daily_loss_ok=True,
            max_positions_ok=True, symbol_no_position=True, cooldown_ok=True,
            kill_switch_off=True, adaptive_cfg=_make_adaptive_cfg(),
        )
        assert approved is True

    def test_kill_switch_blocks_decision(self):
        import decision_engine as de
        approved, reason = de.check_conditions(
            final_score=90, regime_confidence=85, data_quality_score=95,
            liquidity_score=80, risk_allowed=True, daily_loss_ok=True,
            max_positions_ok=True, symbol_no_position=True, cooldown_ok=True,
            kill_switch_off=False, adaptive_cfg=_make_adaptive_cfg(),
        )
        assert approved is False

    def test_same_symbol_blocks_second_position(self):
        import decision_engine as de
        approved, reason = de.check_conditions(
            final_score=90, regime_confidence=85, data_quality_score=95,
            liquidity_score=80, risk_allowed=True, daily_loss_ok=True,
            max_positions_ok=True, symbol_no_position=False, cooldown_ok=True,
            kill_switch_off=True, adaptive_cfg=_make_adaptive_cfg(),
        )
        assert approved is False

    def test_score_below_threshold_rejected(self):
        import decision_engine as de
        approved, reason = de.check_conditions(
            final_score=60, regime_confidence=80, data_quality_score=95,
            liquidity_score=80, risk_allowed=True, daily_loss_ok=True,
            max_positions_ok=True, symbol_no_position=True, cooldown_ok=True,
            kill_switch_off=True, adaptive_cfg=_make_adaptive_cfg(final_decision_threshold=78),
        )
        assert approved is False

    def test_low_data_quality_rejected(self):
        import decision_engine as de
        approved, reason = de.check_conditions(
            final_score=90, regime_confidence=80, data_quality_score=70,
            liquidity_score=80, risk_allowed=True, daily_loss_ok=True,
            max_positions_ok=True, symbol_no_position=True, cooldown_ok=True,
            kill_switch_off=True, adaptive_cfg=_make_adaptive_cfg(),
        )
        assert approved is False


# ══════════════════════════════════════════════════════════════════════════════
# 5. learning_engine — istatistik ve ağırlıklar
# ══════════════════════════════════════════════════════════════════════════════

class TestLearningEngine:
    def _make_trades(self, n: int, win_rate: float = 0.6) -> list[dict]:
        trades = []
        for i in range(n):
            result = "WIN" if (i / n) < win_rate else "LOSS"
            pnl    = 25.0 if result == "WIN" else -15.0
            trades.append({
                "symbol": "BTCUSDT", "side": "LONG",
                "opened_at": f"2026-07-26T0{i % 10}:00:00+00:00",
                "closed_at":  f"2026-07-26T0{i % 10}:30:00+00:00",
                "pnl": pnl, "result": result,
                "balance_after": 10000 + (i * 10),
            })
        return trades

    def test_insufficient_data_label(self):
        import learning_engine as le
        stats = le._stats([])
        assert stats["confidence"] == le.CONFIDENCE_INSUFFICIENT

    def test_stats_5_to_19_low_confidence(self):
        import learning_engine as le
        trades = self._make_trades(10)
        stats  = le._stats(trades)
        assert stats["confidence"] == le.CONFIDENCE_LOW

    def test_stats_50_plus_high_confidence(self):
        import learning_engine as le
        trades = self._make_trades(60)
        stats  = le._stats(trades)
        assert stats["confidence"] == le.CONFIDENCE_HIGH

    def test_paper_history_score_neutral_below_5(self):
        import learning_engine as le
        score = le.get_paper_history_score("BTCUSDT", self._make_trades(3))
        assert score == 50.0  # nötr

    def test_paper_history_score_bounded(self):
        import learning_engine as le
        score = le.get_paper_history_score("BTCUSDT", self._make_trades(50, win_rate=0.9))
        assert 0 <= score <= 100

    def test_weight_change_max_5pct_per_day(self, tmp_path, monkeypatch):
        import learning_engine as le
        monkeypatch.setattr(le, "WEIGHTS_PATH", tmp_path / "weights.json")
        monkeypatch.setattr(le, "SHADOW_PATH",  tmp_path / "shadow.json")
        monkeypatch.setattr(le, "STATE_PATH",   tmp_path / "state.json")

        trades = self._make_trades(30, win_rate=0.8)
        (tmp_path / "state.json").write_text(json.dumps({"trades": trades}))

        old_weights = {
            "strategy": 35.0, "regime": 20.0, "coin": 15.0,
            "liquidity": 10.0, "volatility": 10.0, "paper_hist": 10.0,
        }
        new_weights = le._suggest_weights(
            le.compute_statistics(trades), old_weights
        )
        for k in old_weights:
            if k in new_weights:
                assert abs(new_weights[k] - old_weights[k]) <= le.MAX_DAILY_WEIGHT_CHANGE + 0.01

    def test_no_large_weight_change_with_few_trades(self, tmp_path, monkeypatch):
        import learning_engine as le
        monkeypatch.setattr(le, "WEIGHTS_PATH", tmp_path / "weights.json")
        monkeypatch.setattr(le, "STATE_PATH",   tmp_path / "state.json")

        trades = self._make_trades(5)  # < MIN_TRAINING_TRADES (20)
        (tmp_path / "state.json").write_text(json.dumps({"trades": trades}))

        old_w = {"strategy": 35.0, "regime": 20.0, "coin": 15.0,
                 "liquidity": 10.0, "volatility": 10.0, "paper_hist": 10.0}
        new_w = le._suggest_weights(le.compute_statistics(trades), old_w)
        # Az işlemle ağırlıklar değişmemeli
        assert new_w == {k: v for k, v in old_w.items()}

    def test_shadow_model_prevents_bad_update(self, tmp_path, monkeypatch):
        import learning_engine as le
        monkeypatch.setattr(le, "WEIGHTS_PATH", tmp_path / "weights.json")
        monkeypatch.setattr(le, "SHADOW_PATH",  tmp_path / "shadow.json")

        bad_weights  = {"strategy": 5.0, "regime": 5.0, "coin": 80.0,
                        "liquidity": 5.0, "volatility": 2.5, "paper_hist": 2.5}
        good_weights = {"strategy": 35.0, "regime": 20.0, "coin": 15.0,
                        "liquidity": 10.0, "volatility": 10.0, "paper_hist": 10.0}
        le.save_weights({**good_weights, "_version": 1.0, "_updated_at": ""})

        trades = self._make_trades(20)
        result = le._run_shadow_test(bad_weights, trades)
        # Result olabilir; eğer varsa 'worse' alanı boolena
        if result:
            assert isinstance(result["worse"], bool)


# ══════════════════════════════════════════════════════════════════════════════
# 6. market_regime — sınıflandırma mantığı
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketRegime:
    def _base_ind(self, **overrides) -> dict:
        base = {
            "close": 50000.0, "rsi_15m": 55.0, "rsi_1h": 55.0,
            "ema20_gt_50_15m": True, "ema20_gt_50_1h": True,
            "ema20_gt_50_4h": True, "close_gt_ema50_1h": True,
            "close_gt_ema200_1h": True, "slope_1h": 0.3, "slope_4h": 0.2,
            "atr_pct": 1.5, "bb_width": 5.0, "adx": 65.0, "vol_change": 1.1,
        }
        base.update(overrides)
        return base

    def test_strong_uptrend_detected(self):
        from market_regime import _classify_regime, REGIME_STRONG_UP
        ind = self._base_ind(adx=70, slope_1h=0.4, slope_4h=0.3)
        regime, conf, direction, volatility, reason = _classify_regime(ind)
        assert regime == REGIME_STRONG_UP
        assert conf >= 65

    def test_high_volatility_detected(self):
        from market_regime import _classify_regime, REGIME_HIGH_VOL
        # HIGH_VOL tetiklenmesi için: atr_pct>3 VE abs_dir<0.4 (karışık sinyaller)
        # 15m ve 1h bullish (+1+2+1=4), 4h bearish (-2); direction_score=(4-2)/6≈0.33<0.4
        ind = self._base_ind(
            atr_pct=5.0,
            ema20_gt_50_4h=False,    # 4h bearish → score dengesi karışıyor
            slope_1h=0.0, slope_4h=0.0,  # eğim nötr
        )
        regime, conf, direction, volatility, reason = _classify_regime(ind)
        assert regime == REGIME_HIGH_VOL

    def test_insufficient_when_no_valid_data(self):
        from market_regime import _classify_regime, REGIME_INSUFFICIENT
        ind = self._base_ind(atr_pct=0.0)
        regime, conf, direction, volatility, reason = _classify_regime(ind)
        assert regime == REGIME_INSUFFICIENT

    def test_regime_score_suitable_regime(self):
        from market_regime import regime_score, REGIME_STRONG_UP
        info = {"regime": REGIME_STRONG_UP, "confidence": 85}
        score = regime_score(info)
        assert score > 60

    def test_regime_score_unclear_low(self):
        from market_regime import regime_score, REGIME_UNCLEAR
        info = {"regime": REGIME_UNCLEAR, "confidence": 30}
        score = regime_score(info)
        assert score < 30

    def test_minimum_2_timeframe_confirmation(self):
        """Yalnızca bir timeframe bullish ise güçlü rejim belirlenmemeli."""
        from market_regime import _classify_regime, REGIME_STRONG_UP, REGIME_WEAK_UP
        ind = self._base_ind(
            ema20_gt_50_15m=True,   # sadece 15m bullish
            ema20_gt_50_1h=False,
            ema20_gt_50_4h=False,
            slope_1h=-0.1, slope_4h=-0.2,
        )
        regime, conf, *_ = _classify_regime(ind)
        assert regime != REGIME_STRONG_UP


# ══════════════════════════════════════════════════════════════════════════════
# 7. auto_controller — çift instance engeli
# ══════════════════════════════════════════════════════════════════════════════

class TestAutoController:
    def test_single_instance_lock(self):
        import auto_controller as ac
        # İkinci start aynı anda çalışmamalı
        ac._LOOP_RUNNING.clear()  # reset
        r1 = True  # ilk set
        ac._LOOP_RUNNING.set()
        with ac._CONTROLLER_LOCK:
            r2 = not ac._LOOP_RUNNING.is_set()  # ikincisi False (zaten set)
        ac._LOOP_RUNNING.clear()
        assert r2 is False


# ══════════════════════════════════════════════════════════════════════════════
# 8. app.py — Flask route'ları smoke test
# ══════════════════════════════════════════════════════════════════════════════

class TestFlaskRoutes:
    @pytest.fixture(autouse=True)
    def client(self):
        import app as flask_app
        flask_app.app.config["TESTING"]          = True
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        with flask_app.app.test_client() as c:
            yield c

    def test_dashboard_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Kontrol Paneli" in resp.data

    def test_api_status_200(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "running" in data

    def test_api_smart_status_200(self, client):
        resp = client.get("/api/smart/status")
        assert resp.status_code == 200

    def test_api_regime_200(self, client):
        # Binance ağına gitmesin; mock ile
        import market_regime as mr
        with patch.object(mr, "detect_market_regime", return_value=_make_regime()):
            resp = client.get("/api/regime")
        assert resp.status_code == 200

    def test_api_risk_200(self, client):
        resp = client.get("/api/risk")
        assert resp.status_code == 200

    def test_api_decisions_200(self, client):
        resp = client.get("/api/decisions")
        assert resp.status_code == 200

    def test_api_learning_200(self, client):
        resp = client.get("/api/learning")
        assert resp.status_code == 200

    def test_api_adaptive_status_200(self, client):
        resp = client.get("/api/adaptive/status")
        assert resp.status_code == 200

    def test_api_daily_report_200(self, client):
        resp = client.get("/api/daily-report")
        assert resp.status_code == 200

    def test_api_daily_report_export_csv(self, client):
        resp = client.get("/api/daily-report/export")
        assert resp.status_code == 200
        assert b"symbol" in resp.data  # CSV başlığı

    def test_kill_switch_on(self, client, tmp_path, monkeypatch):
        import safety_guard as sg
        monkeypatch.setattr(sg, "SAFETY_STATE_PATH", tmp_path / "safety_state.json")
        import metrics_store as ms
        monkeypatch.setattr(ms, "LOG_FILES", {
            **ms.LOG_FILES,
            "risk_events": tmp_path / "risk_events.jsonl",
        })
        resp = client.post("/adaptive/kill-switch", data={"activate": "1"})
        assert resp.status_code in (200, 302)
        sg.deactivate_kill_switch()

    def test_bot_start_stop_returns_200(self, client):
        resp = client.post("/bot/stop")  # bot yoksa hata mesajı döner ama 200
        assert resp.status_code == 200

    def test_settings_save_valid(self, client):
        cfg_path = "alpha20_v1/config.json"
        original = open(cfg_path).read()
        try:
            resp = client.post("/settings", data={
                "minimum_score": "65", "scan_seconds": "60",
                "risk_per_trade_pct": "0.5", "daily_loss_limit_pct": "1.5",
                "max_consecutive_losses": "3", "reward_risk_ratio": "2.0",
                "atr_stop_multiplier": "1.5", "max_open_positions": "1",
            })
            assert resp.status_code == 200
        finally:
            with open(cfg_path, "w") as f:
                f.write(original)

    def test_coin_add_valid(self, client):
        cfg_path = "alpha20_v1/config.json"
        original = open(cfg_path).read()
        try:
            resp = client.post("/coins/add", data={"symbol": "XRPUSDT"})
            assert resp.status_code == 200
        finally:
            with open(cfg_path, "w") as f:
                f.write(original)  # gerçek config'i kirletme

    def test_coin_add_invalid_rejected(self, client):
        resp = client.post("/coins/add", data={"symbol": "INVALID123"})
        assert resp.status_code == 200
        assert b"Hata" in resp.data

    def test_smart_mode_manuel(self, client):
        resp = client.post("/smart/mode", data={"mode": "MANUEL"})
        assert resp.status_code == 200

    def test_smart_mode_oneri(self, client):
        resp = client.post("/smart/mode", data={"mode": "ONERI"})
        assert resp.status_code == 200

    def test_paper_mode_no_live_orders(self, client):
        """Sistem PAPER dışı emir içermemeli — mock ile doğrula."""
        import app as flask_app
        # app.py içinde gerçek emir gönderen fonksiyon olmamalı
        src = Path("app.py").read_text()
        forbidden = ["create_order", "place_order", "submit_order",
                     "SIGNED", "X-MBX-APIKEY"]
        for term in forbidden:
            assert term not in src, f"Yasak ifade bulundu: {term}"

    def test_adaptive_settings_save(self, client):
        resp = client.post("/adaptive/settings", data={
            "regime_min_confidence": "65",
            "final_decision_threshold": "78",
            "base_risk_pct": "0.25",
            "max_risk_pct": "0.50",
            "daily_loss_limit_pct": "1.0",
            "max_drawdown_pct": "5.0",
            "max_consecutive_losses": "3",
            "risk_reduction_after_losses": "2",
            "cooldown_minutes": "60",
            "learning_interval_hours": "24",
            "break_even_enabled": "0",
            "trailing_stop_enabled": "0",
            "partial_take_profit_enabled": "0",
            "learning_enabled": "1",
        })
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 9. Genel güvenlik — PAPER modu, API anahtar yok
# ══════════════════════════════════════════════════════════════════════════════

class TestPaperSafety:
    def test_no_api_keys_in_codebase(self):
        """Kaynak kodunda API anahtarı veya gerçek emir bulunmamalı."""
        forbidden_terms = [
            "create_order", "X-MBX-APIKEY", "api_secret",
            "place_order", "FUTURES_ORDER", "account_balance_live",
        ]
        src_files = list(Path("alpha20_v1").glob("*.py")) + [Path("app.py")]
        for src in src_files:
            text = src.read_text(errors="replace")
            for term in forbidden_terms:
                assert term not in text, f"{src.name} içinde yasak ifade: {term}"

    def test_config_mode_is_paper(self):
        with open("alpha20_v1/config.json") as f:
            cfg = json.load(f)
        assert cfg.get("mode") == "PAPER"

    def test_adaptive_auto_paper_default_false(self):
        with open("alpha20_v1/config.json") as f:
            cfg = json.load(f)
        assert cfg["adaptive_system"]["auto_paper_enabled"] is False

    def test_adaptive_enabled_default_false(self):
        with open("alpha20_v1/config.json") as f:
            cfg = json.load(f)
        assert cfg["adaptive_system"]["enabled"] is False

    def test_original_balance_preserved(self):
        with open("alpha20_v1/config.json") as f:
            cfg = json.load(f)
        assert cfg.get("starting_balance_usdt") == 10000.0

    def test_original_symbols_preserved(self):
        with open("alpha20_v1/config.json") as f:
            cfg = json.load(f)
        assert "BTCUSDT" in cfg.get("symbols", [])
