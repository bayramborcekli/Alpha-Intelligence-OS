"""Paper pozisyon yönetiminin birleştirilmesi (Mission: UNIFY PAPER POSITION MGMT).

Kanıtlanan zincir:
  sinyal → pozisyon aç → SL/TP tetiklenir → KANONİK manage_position kapatır
  → state.json (defter) güncellenir — hem klasik motor hem auto_controller
  aynı fonksiyonu kullanır.

MERGE GUARD NOTU: Operatör onaylı davranış; görev ajanları 'kapsam dışı'
diye kaldıramaz.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
A20 = ROOT / "alpha20_v1"
# KOŞULSUZ 0. konuma ekle: repo kökünde de bir alpha20.py var; yol sonda
# kalırsa yanlış modül import edilir (test_paper_trading ile aynı desen).
sys.path.insert(0, str(A20))

import alpha20  # noqa: E402
import auto_controller as ac  # noqa: E402


def _candle_df(high: float, low: float) -> pd.DataFrame:
    return pd.DataFrame([{"open": (high + low) / 2, "high": high,
                          "low": low, "close": (high + low) / 2,
                          "volume": 1.0}])


def _state_with_position(extended: bool) -> dict:
    pos = {
        "symbol": "BTCUSDT", "side": "LONG", "entry": 100.0,
        "stop": 95.0, "target": 110.0, "quantity": 1.0,
        "risk_usdt": 5.0, "opened_at": "2026-07-28T00:00:00+00:00",
    }
    if extended:  # auto_controller'ın yazdığı ek alanlar
        pos.update({"regime": "Trend", "final_score": 81.2,
                    "reason": "test", "atr": 1.0, "rr": 2.0})
    return {"balance": 10000.0, "position": pos, "trades": [],
            "consecutive_losses": 0}


@pytest.fixture(autouse=True)
def _no_history_io(monkeypatch):
    monkeypatch.setattr(alpha20, "append_trade_history", lambda rec: None)


def test_manage_position_closes_at_stop_and_updates_ledger(monkeypatch):
    """SL tetiklenir → pozisyon kapanır, trade defterine yazılır, bakiye düşer."""
    state = _state_with_position(extended=False)
    monkeypatch.setattr(alpha20, "fetch_klines_safe",
                        lambda *a, **k: _candle_df(high=96.0, low=94.0))
    alpha20.manage_position(state)
    assert state["position"] is None
    assert len(state["trades"]) == 1
    t = state["trades"][0]
    assert t["close_reason"] == "STOP_LOSS" and t["result"] == "LOSS"
    assert state["balance"] < 10000.0  # zarar + komisyon işlendi


def test_manage_position_accepts_auto_controller_extended_record(monkeypatch):
    """auto_controller'ın genişletilmiş pozisyon kaydı kanonik fonksiyonda
    ÇÖKMEDEN işlenir (Position(**raw) TypeError regresyonu)."""
    state = _state_with_position(extended=True)
    monkeypatch.setattr(alpha20, "fetch_klines_safe",
                        lambda *a, **k: _candle_df(high=111.0, low=105.0))
    alpha20.manage_position(state)
    assert state["position"] is None
    t = state["trades"][0]
    assert t["close_reason"] == "TAKE_PROFIT" and t["result"] == "WIN"
    assert t["regime"] == "Trend"  # genişletilmiş alanlar kayıtta korunur
    assert state["balance"] > 10000.0


def test_auto_controller_cycle_closes_open_position(monkeypatch, tmp_path):
    """_run_single_cycle: açık pozisyon KANONİK manage_position ile kapanır
    ve state.json'a kalıcı yazılır (açma+kapatma aynı motor zinciri)."""
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(_state_with_position(extended=True)),
                          encoding="utf-8")
    monkeypatch.setattr(ac, "STATE_PATH", state_file)
    monkeypatch.setattr(alpha20, "fetch_klines_safe",
                        lambda *a, **k: _candle_df(high=96.0, low=94.0))

    # Döngüyü pozisyon yönetiminden hemen sonra durdur: safety unsafe döner.
    import safety_guard as sg
    import metrics_store as ms

    class _Unsafe:
        safe = False
        kill_switch = False
        reason = "test-stop"
        def to_dict(self):  # noqa: D401
            return {"safe": False}

    monkeypatch.setattr(sg, "check_all", lambda **k: _Unsafe())
    monkeypatch.setattr(sg, "lock_safety", lambda *a, **k: None)
    monkeypatch.setattr(ms, "update_panel_status", lambda *a, **k: None)
    monkeypatch.setattr(ms, "append_system_error", lambda **k: None)

    ac._run_single_cycle(adaptive_cfg={"mode": "MONITOR"}, symbols=[])

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["position"] is None
    assert saved["trades"] and saved["trades"][0]["close_reason"] == "STOP_LOSS"
    assert saved["balance"] < 10000.0


def test_signal_generation_still_works():
    """Sinyal üretimi: sentetik yükseliş verisinde score_setup LONG ve
    eşik üstü skor döndürür (zincirin 1. halkası)."""
    n = 250
    base = [100 + i * 0.5 for i in range(n)]
    df = pd.DataFrame({
        "open": [b - 0.2 for b in base],
        "high": [b + 0.6 for b in base],
        "low":  [b - 0.6 for b in base],
        "close": base,
        "volume": [100.0] * (n - 1) + [200.0],
    })
    ind = alpha20.add_indicators(df)
    side, score, details = alpha20.score_setup(ind, ind)
    assert side == "LONG" and score >= 65


def test_source_guard_auto_controller_calls_canonical_manage():
    """Merge guard: auto_controller kanonik manage_position çağrısını içerir."""
    src = (A20 / "auto_controller.py").read_text(encoding="utf-8")
    assert "alpha20.manage_position(trading_state)" in src
    assert "KANONİK SL/TP" in src
