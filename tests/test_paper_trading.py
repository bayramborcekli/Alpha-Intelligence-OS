"""Paper trading akış testleri — ES-003 gece PAPER başlangıcı.

Ağa hiç çıkmaz: fetch_klines monkeypatch ile sahtelenir.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import alpha20  # noqa: E402
from alpha20 import (  # noqa: E402
    append_trade_history,
    compute_realized_pnl,
    can_open,
    fetch_klines_safe,
    initial_state,
    manage_position,
    open_paper_position,
    save_json,
    load_json,
    validate_startup_config,
)

_TS = datetime.now(timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def _isolate_trade_history(tmp_path, monkeypatch):
    """Testler gerçek trade_history.json dosyasını kirletmesin."""
    monkeypatch.setattr(alpha20, "TRADE_HISTORY_PATH", tmp_path / "trade_history.json")
    monkeypatch.setattr(alpha20, "DUAL_MODEL_RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")


def base_config(**overrides):
    cfg = {
        "mode": "PAPER",
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "interval": "15m",
        "trend_interval": "1h",
        "scan_seconds": 60,
        "starting_balance_usdt": 10000.0,
        "risk_per_trade_pct": 0.5,
        "daily_loss_limit_pct": 1.5,
        "max_consecutive_losses": 3,
        "minimum_score": 65,
        "reward_risk_ratio": 2.0,
        "atr_stop_multiplier": 1.5,
        "max_open_positions": 1,
    }
    cfg.update(overrides)
    return cfg


def fresh_state(config=None):
    return initial_state(config or base_config())


def details(price=65000.0, atr=100.0):
    return {"price": price, "atr": atr, "rsi": 55.0, "volume_ratio": 1.2,
            "long_score": 80, "short_score": 0}


def kline_df(high, low):
    return pd.DataFrame([{
        "open_time": 0, "open": (high + low) / 2, "high": high, "low": low,
        "close": (high + low) / 2, "volume": 100.0, "close_time": 0,
        "quote_volume": 0, "trades": 0, "taker_base": 0, "taker_quote": 0,
        "ignore": 0,
    }])


# ── 1. paper buy/open ─────────────────────────────────────────────────────────

class TestPaperOpen:
    def test_paper_buy_open(self):
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        pos = st["position"]
        assert pos is not None
        assert pos["symbol"] == "BTCUSDT"
        assert pos["side"] == "LONG"
        assert pos["entry"] == 65000.0
        assert pos["stop"] < pos["entry"] < pos["target"]
        assert pos["quantity"] > 0
        assert pos["risk_usdt"] == pytest.approx(10000.0 * 0.5 / 100)
        assert "opened_at" in pos

    def test_stop_and_target_mandatory(self):
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", "SHORT", details(), cfg, st)
        pos = st["position"]
        assert pos["stop"] > pos["entry"] > pos["target"]  # SHORT yönü


# ── 2. paper sell/close + SL/TP ───────────────────────────────────────────────

class TestPaperClose:
    def _open(self, side="LONG"):
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", side, details(), cfg, st)
        return cfg, st

    def test_paper_sell_close(self, monkeypatch):
        cfg, st = self._open("LONG")
        target = st["position"]["target"]
        monkeypatch.setattr(alpha20, "fetch_klines",
                            lambda *a, **k: kline_df(high=target + 10, low=target - 5))
        manage_position(st)
        assert st["position"] is None
        assert len(st["trades"]) == 1

    def test_take_profit_close(self, monkeypatch):
        cfg, st = self._open("LONG")
        target = st["position"]["target"]
        monkeypatch.setattr(alpha20, "fetch_klines",
                            lambda *a, **k: kline_df(high=target + 10, low=target - 5))
        manage_position(st)
        trade = st["trades"][0]
        assert trade["close_reason"] == "TAKE_PROFIT"
        assert trade["pnl"] > 0
        assert st["balance"] > 10000.0

    def test_stop_loss_close(self, monkeypatch):
        cfg, st = self._open("LONG")
        stop = st["position"]["stop"]
        monkeypatch.setattr(alpha20, "fetch_klines",
                            lambda *a, **k: kline_df(high=stop + 5, low=stop - 10))
        manage_position(st)
        trade = st["trades"][0]
        assert trade["close_reason"] == "STOP_LOSS"
        assert trade["pnl"] < 0
        assert st["balance"] < 10000.0
        assert st["consecutive_losses"] == 1

    def test_fee_logged(self, monkeypatch):
        cfg, st = self._open("LONG")
        target = st["position"]["target"]
        monkeypatch.setattr(alpha20, "fetch_klines",
                            lambda *a, **k: kline_df(high=target + 10, low=target - 5))
        manage_position(st)
        trade = st["trades"][0]
        assert trade["fee_usdt"] > 0
        assert trade["pnl"] == pytest.approx(trade["gross_pnl"] - trade["fee_usdt"])

    def test_close_reason_and_fields_logged(self, monkeypatch):
        cfg, st = self._open("LONG")
        target = st["position"]["target"]
        monkeypatch.setattr(alpha20, "fetch_klines",
                            lambda *a, **k: kline_df(high=target + 10, low=target - 5))
        manage_position(st)
        trade = st["trades"][0]
        for field in ("symbol", "side", "entry_price", "exit_price", "quantity",
                      "fee_usdt", "pnl", "close_reason", "opened_at", "closed_at"):
            assert field in trade, f"Eksik alan: {field}"


# ── 3. Guard'lar ──────────────────────────────────────────────────────────────

class TestGuards:
    def test_insufficient_balance(self):
        cfg, st = base_config(), fresh_state()
        st["balance"] = 0.0
        with pytest.raises(ValueError, match="Yetersiz sanal bakiye"):
            open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        assert st["position"] is None

    def test_duplicate_position_rejected(self):
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        ok, reason = can_open(cfg, st, symbol="BTCUSDT")
        assert not ok
        assert "mükerrer" in reason or "Açık pozisyon" in reason

    def test_second_position_any_symbol_rejected(self):
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        ok, _ = can_open(cfg, st, symbol="ETHUSDT")
        assert not ok  # max 1 açık pozisyon

    def test_dual_model_same_symbol_rejected(self, tmp_path):
        """Dual-model motorunun pozisyonu legacy açılışı engeller."""
        cfg, st = base_config(), fresh_state()
        (tmp_path / "dual_model_runtime.json").write_text(json.dumps(
            {"positions": {"BTCUSDT": {"symbol": "BTCUSDT",
                                       "model": "ALPHA_CORE_SCALP"}}}),
            encoding="utf-8")
        ok, reason = can_open(cfg, st, symbol="BTCUSDT")
        assert not ok
        assert "dual-model" in reason and "DUPLICATE_POSITION" in reason
        ok, _ = can_open(cfg, st, symbol="ETHUSDT")
        assert ok

    def test_dual_model_positions_count_toward_total_cap(self, tmp_path):
        """Toplam tavan (4) doluysa legacy yeni pozisyon açamaz."""
        cfg, st = base_config(), fresh_state()
        positions = {f"S{i}USDT": {"symbol": f"S{i}USDT"} for i in range(4)}
        (tmp_path / "dual_model_runtime.json").write_text(
            json.dumps({"positions": positions}), encoding="utf-8")
        ok, reason = can_open(cfg, st, symbol="BTCUSDT")
        assert not ok
        assert "RISK_LIMIT" in reason
        # Sembolsüz (global) kontrol de bloklanır
        ok, reason = can_open(cfg, st)
        assert not ok and "RISK_LIMIT" in reason

    def test_dual_model_cap_from_config(self, tmp_path):
        cfg = base_config(dual_model={"total_max_open_positions": 2})
        st = fresh_state()
        positions = {"AUSDT": {"symbol": "AUSDT"},
                     "BUSDT": {"symbol": "BUSDT"}}
        (tmp_path / "dual_model_runtime.json").write_text(
            json.dumps({"positions": positions}), encoding="utf-8")
        ok, reason = can_open(cfg, st, symbol="CUSDT")
        assert not ok and "RISK_LIMIT" in reason

    def test_invalid_price_rejected(self):
        cfg, st = base_config(), fresh_state()
        with pytest.raises(ValueError, match="Geçersiz giriş fiyatı"):
            open_paper_position("BTCUSDT", "LONG", details(price=0), cfg, st)
        with pytest.raises(ValueError, match="Geçersiz ATR"):
            open_paper_position("BTCUSDT", "LONG", details(atr=-1), cfg, st)
        assert st["position"] is None


# ── 4. Restart / state restore ────────────────────────────────────────────────

class TestStateRestore:
    def test_restart_state_restore(self, tmp_path):
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        path = tmp_path / "state.json"
        save_json(path, st)
        restored = load_json(path, {})
        assert restored == st
        assert restored["position"]["symbol"] == "BTCUSDT"
        assert restored["balance"] == st["balance"]


# ── 5. Ağ hatası ──────────────────────────────────────────────────────────────

class TestNetworkFailure:
    def test_network_failure_no_trade(self, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("ağ yok")
        monkeypatch.setattr(alpha20, "fetch_klines", boom)
        st = fresh_state()
        assert fetch_klines_safe("BTCUSDT", "15m", state=st) is None
        assert st["network_errors"] == 1
        assert st["position"] is None  # işlem açılmadı

    def test_partial_network_failure_blocks_cycle(self, monkeypatch):
        """Bir sembol bile veri hatası verirse o döngüde hiç pozisyon açılmaz."""
        cfg, st = base_config(), fresh_state()

        def flaky(symbol, interval, limit=300):
            if symbol == "ETHUSDT":
                raise ConnectionError("ağ yok")
            # Güçlü LONG sinyali üretebilecek yapay veri
            rows = [{"open_time": i, "open": 100 + i, "high": 101 + i,
                     "low": 99 + i, "close": 100.5 + i, "volume": 1000.0,
                     "close_time": i, "quote_volume": 0, "trades": 0,
                     "taker_base": 0, "taker_quote": 0, "ignore": 0}
                    for i in range(300)]
            return pd.DataFrame(rows)

        monkeypatch.setattr(alpha20, "fetch_klines", flaky)
        monkeypatch.setattr(alpha20, "save_json", lambda *a, **k: None)
        alpha20.run_cycle(cfg, st)
        assert st["position"] is None  # kısmi hata → hiç işlem yok
        assert st["network_errors"] >= 1

    def test_open_rejected_when_position_exists(self):
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        with pytest.raises(ValueError, match="açık pozisyon"):
            open_paper_position("ETHUSDT", "LONG", details(), cfg, st)

    def test_network_failure_position_preserved(self, monkeypatch):
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        def boom(*a, **k):
            raise ConnectionError("ağ yok")
        monkeypatch.setattr(alpha20, "fetch_klines", boom)
        manage_position(st)
        assert st["position"] is not None  # pozisyon korunur, kapatılmaz
        assert len(st["trades"]) == 0


# ── 5b. BUG-001: Tek PnL kaynağı + trade history + özet ───────────────────────

class TestSinglePnLSource:
    """Console, State, Trade History aynı realized PnL değerini kullanmalı."""

    def test_compute_realized_pnl_long_win(self):
        r = compute_realized_pnl(100.0, 110.0, 2.0, "LONG")
        assert r["gross_pnl"] == pytest.approx(20.0)
        assert r["fee_usdt"] == pytest.approx((100 + 110) * 2 * 0.001)
        assert r["pnl"] == pytest.approx(r["gross_pnl"] - r["fee_usdt"])

    def test_compute_realized_pnl_short_win(self):
        r = compute_realized_pnl(110.0, 100.0, 2.0, "SHORT")
        assert r["gross_pnl"] == pytest.approx(20.0)
        assert r["pnl"] < r["gross_pnl"]  # fee düşülür

    def test_compute_realized_pnl_invalid_inputs(self):
        with pytest.raises(ValueError):
            compute_realized_pnl(0, 100, 1, "LONG")
        with pytest.raises(ValueError):
            compute_realized_pnl(100, 100, 1, "SIDEWAYS")

    def test_all_records_share_same_pnl(self, monkeypatch, tmp_path):
        """Regression: state, trades kaydı, trade_history ve bakiye deltası aynı pnl."""
        hist_path = tmp_path / "hist.json"
        monkeypatch.setattr(alpha20, "TRADE_HISTORY_PATH", hist_path)
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        pos = st["position"]
        expected = compute_realized_pnl(
            pos["entry"], pos["target"], pos["quantity"], "LONG")
        balance_before = st["balance"]
        target = pos["target"]
        monkeypatch.setattr(alpha20, "fetch_klines",
                            lambda *a, **k: kline_df(high=target + 10, low=target - 5))
        manage_position(st)

        trade = st["trades"][0]                              # State / Trade History (UI)
        history = json.loads(hist_path.read_text())          # trade_history.json
        assert trade["pnl"] == expected["pnl"]
        assert trade["fee_usdt"] == expected["fee_usdt"]
        assert trade["gross_pnl"] == expected["gross_pnl"]
        assert history[-1]["pnl"] == expected["pnl"]
        assert st["balance"] - balance_before == pytest.approx(expected["pnl"])
        assert trade["balance_after"] == pytest.approx(st["balance"])

    def test_trade_history_appends(self, monkeypatch, tmp_path):
        hist_path = tmp_path / "hist.json"
        monkeypatch.setattr(alpha20, "TRADE_HISTORY_PATH", hist_path)
        append_trade_history({"symbol": "BTCUSDT", "pnl": 1.0})
        append_trade_history({"symbol": "ETHUSDT", "pnl": -2.0})
        history = json.loads(hist_path.read_text())
        assert len(history) == 2
        assert history[0]["symbol"] == "BTCUSDT"

    def test_trade_history_survives_corrupt_file(self, monkeypatch, tmp_path):
        hist_path = tmp_path / "hist.json"
        hist_path.write_text("BOZUK{{{")
        monkeypatch.setattr(alpha20, "TRADE_HISTORY_PATH", hist_path)
        append_trade_history({"symbol": "BTCUSDT", "pnl": 1.0})
        assert len(json.loads(hist_path.read_text())) == 1

    def test_history_write_failure_does_not_corrupt_close(self, monkeypatch):
        """History yazımı patlasa bile pozisyon kapanır, çift kapanış olmaz."""
        cfg, st = base_config(), fresh_state()
        open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        target = st["position"]["target"]
        monkeypatch.setattr(alpha20, "fetch_klines",
                            lambda *a, **k: kline_df(high=target + 10, low=target - 5))

        def boom(*a, **k):
            raise OSError("disk dolu")
        monkeypatch.setattr(alpha20, "append_trade_history", boom)

        manage_position(st)  # exception yukarı taşmamalı
        assert st["position"] is None          # pozisyon kapandı
        assert len(st["trades"]) == 1          # tek kayıt
        balance_after = st["balance"]
        manage_position(st)                    # ikinci çağrı no-op
        assert len(st["trades"]) == 1
        assert st["balance"] == balance_after  # çift PnL yok

    def test_performance_summary_output(self, capsys):
        st = fresh_state()
        st["trades"] = [
            {"symbol": "BTCUSDT", "pnl": 56.57, "fee_usdt": 43.43},
            {"symbol": "ETHUSDT", "pnl": -25.0, "fee_usdt": 10.0},
        ]
        alpha20.print_performance_summary(st)
        out = capsys.readouterr().out
        assert "PERFORMANS ÖZETİ" in out
        assert "Toplam işlem: 2" in out
        assert "Kazanma oranı: 50.0%" in out

    def test_performance_summary_empty(self, capsys):
        alpha20.print_performance_summary(fresh_state())
        out = capsys.readouterr().out
        assert "Kapanan işlem yok" in out


# ── 5c. Mission 11: Pre-trade ekonomik filtre ─────────────────────────────────

class TestEconomicFilter:
    def _cfg(self, **o):
        cfg = base_config()
        cfg.setdefault("fee_safety_factor", 2.0)
        cfg.update(o)
        return cfg

    def test_wide_stop_passes(self):
        # Geniş stop → küçük pozisyon → düşük fee → işlem açılabilir
        econ = alpha20.evaluate_trade_economics(
            entry=65000, atr=650, side="LONG", balance=10000, config=self._cfg())
        assert econ["skip"] is False
        assert econ["expected_gross_profit"] == pytest.approx(
            10000 * 0.5 / 100 * 2.0)  # risk × RR
        assert econ["fee_gross_ratio"] < 0.5

    def test_tiny_stop_skipped(self):
        # 1m ATR benzeri minik stop → dev pozisyon → fee brütü ezer → SKIP
        econ = alpha20.evaluate_trade_economics(
            entry=65000, atr=8, side="LONG", balance=10000, config=self._cfg())
        assert econ["skip"] is True
        assert econ["expected_total_fee"] > econ["expected_gross_profit"]

    def test_required_metrics_present(self):
        econ = alpha20.evaluate_trade_economics(
            entry=100, atr=1, side="SHORT", balance=10000, config=self._cfg())
        for key in ("entry", "stop", "atr", "stop_distance_pct", "position_size",
                    "expected_gross_profit", "expected_total_fee",
                    "risk_reward", "fee_gross_ratio", "safety_factor"):
            assert key in econ

    def test_safety_factor_configurable(self):
        # Sınırda bir kurulum: sf=0 ile geçer, çok yüksek sf ile atlanır
        kw = dict(entry=65000, atr=650, side="LONG", balance=10000)
        assert alpha20.evaluate_trade_economics(
            **kw, config=self._cfg(fee_safety_factor=0.0))["skip"] is False
        assert alpha20.evaluate_trade_economics(
            **kw, config=self._cfg(fee_safety_factor=100.0))["skip"] is True

    def test_open_raises_trade_skipped(self):
        cfg, st = base_config(), fresh_state()
        cfg["fee_safety_factor"] = 2.0
        d = details()
        d["atr"] = d["price"] * 0.0001  # ekonomik olarak saçma sıkılıkta stop
        with pytest.raises(alpha20.TradeSkippedError, match="SKIPPED"):
            open_paper_position("BTCUSDT", "LONG", d, cfg, st)
        assert st["position"] is None  # hiçbir durum değişmedi
        assert st["balance"] == cfg["starting_balance_usdt"]

    def test_open_allows_economic_trade(self):
        cfg, st = base_config(), fresh_state()
        cfg["fee_safety_factor"] = 2.0
        open_paper_position("BTCUSDT", "LONG", details(), cfg, st)
        assert st["position"] is not None

    def test_skip_is_not_risk_violation(self):
        """SKIP sonrası consecutive_losses ve bakiye değişmez."""
        cfg, st = base_config(), fresh_state()
        d = details()
        d["atr"] = d["price"] * 0.0001
        before = dict(st)
        with pytest.raises(alpha20.TradeSkippedError):
            open_paper_position("BTCUSDT", "SHORT", d, cfg, st)
        assert st["consecutive_losses"] == before["consecutive_losses"]
        assert st["trades"] == []


# ── 6. Başlangıç doğrulaması ──────────────────────────────────────────────────

class TestStartupValidation:
    def test_valid_config_passes(self):
        validate_startup_config(base_config())

    def test_paper_mode_enforced(self):
        with pytest.raises(SystemExit):
            validate_startup_config(base_config(mode="LIVE"))

    def test_max_symbols_enforced(self):
        with pytest.raises(SystemExit):
            validate_startup_config(base_config(
                symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]))

    def test_risk_low_rejected(self):
        with pytest.raises(SystemExit):
            validate_startup_config(base_config(risk_per_trade_pct=0.24))

    def test_risk_high_rejected(self):
        with pytest.raises(SystemExit):
            validate_startup_config(base_config(risk_per_trade_pct=0.51))

    def test_risk_bounds_inclusive(self):
        validate_startup_config(base_config(risk_per_trade_pct=0.25))
        validate_startup_config(base_config(risk_per_trade_pct=0.50))

    def test_multi_position_rejected(self):
        with pytest.raises(SystemExit):
            validate_startup_config(base_config(max_open_positions=2))

    def test_leverage_rejected(self):
        with pytest.raises(SystemExit):
            validate_startup_config(base_config(leverage=5))

    def test_repo_config_is_valid(self):
        cfg = json.loads((ROOT / "alpha20_v1" / "config.json").read_text())
        validate_startup_config(cfg)
