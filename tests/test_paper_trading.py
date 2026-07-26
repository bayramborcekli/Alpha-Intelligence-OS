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
