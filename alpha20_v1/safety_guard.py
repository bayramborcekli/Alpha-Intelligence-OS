"""
safety_guard.py — Günlük kayıp, drawdown, ardışık zarar, veri hatası ve
kill-switch güvenlik kontrollerini yönetir.
Yeni işlem açılmasını engeller; açık pozisyonların yönetimini durdurmaz.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import metrics_store as ms

ROOT              = Path(__file__).resolve().parent
SAFETY_STATE_PATH = ROOT / "safety_state.json"
CONFIG_PATH       = ROOT / "config.json"
STATE_PATH        = ROOT / "state.json"

_LOCK = threading.RLock()   # RLock: check_all içinden lock_safety çağrısına izin verir

SAFETY_DEFAULTS: dict[str, Any] = {
    "kill_switch":          False,
    "safety_locked":        False,
    "lock_reason":          "",
    "lock_time":            None,
    "consecutive_block_at": None,
    "daily_loss_block":     False,
    "drawdown_block":       False,
    "last_check_time":      None,
    "last_check_result":    "unknown",
    "last_check_reason":    "",
}


# ══════════════════════════════════════════════════════════════════════════════
# Durum dosyası
# ══════════════════════════════════════════════════════════════════════════════

def _load() -> dict[str, Any]:
    if not SAFETY_STATE_PATH.exists():
        return dict(SAFETY_DEFAULTS)
    try:
        with SAFETY_STATE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(SAFETY_DEFAULTS)
        merged.update(data)
        return merged
    except Exception:
        return dict(SAFETY_DEFAULTS)


def _save(state: dict[str, Any]) -> None:
    tmp = SAFETY_STATE_PATH.with_name(".safety_state.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(SAFETY_STATE_PATH)
    except OSError:
        pass
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def get_safety_state() -> dict[str, Any]:
    with _LOCK:
        return _load()


# ══════════════════════════════════════════════════════════════════════════════
# Kill-switch
# ══════════════════════════════════════════════════════════════════════════════

def activate_kill_switch(reason: str = "Kullanıcı tarafından etkinleştirildi.") -> None:
    with _LOCK:
        state = _load()
        state["kill_switch"] = True
        _save(state)
    ms.append_risk_event(event_type="KILL_SWITCH_ON", reason=reason)


def deactivate_kill_switch() -> None:
    with _LOCK:
        state = _load()
        state["kill_switch"] = False
        _save(state)
    ms.append_risk_event(event_type="KILL_SWITCH_OFF", reason="Kullanıcı tarafından kapatıldı.")


# ══════════════════════════════════════════════════════════════════════════════
# Güvenlik kilidi (otomatik hatalar için; kullanıcı onayı gerektirir)
# ══════════════════════════════════════════════════════════════════════════════

def lock_safety(reason: str, component: str = "system") -> None:
    with _LOCK:
        state = _load()
        if not state["safety_locked"]:
            state["safety_locked"] = True
            state["lock_reason"]   = reason
            state["lock_time"]     = datetime.now(timezone.utc).isoformat()
            _save(state)
    ms.append_system_error(component=component, error_type="SAFETY_LOCK",
                           message=reason, safe_state_activated=True)


def unlock_safety() -> None:
    with _LOCK:
        state = _load()
        state["safety_locked"]  = False
        state["lock_reason"]    = ""
        state["lock_time"]      = None
        _save(state)
    ms.append_risk_event(event_type="SAFETY_UNLOCK", reason="Kullanıcı tarafından kilidi açıldı.")


# ══════════════════════════════════════════════════════════════════════════════
# Ana kontrol
# ══════════════════════════════════════════════════════════════════════════════

def _load_trading_state() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_adaptive_config() -> dict[str, Any]:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("adaptive_system", {})
    except Exception:
        return {}


class SafetyResult:
    __slots__ = ("safe", "reason", "locked", "kill_switch",
                 "daily_loss_block", "drawdown_block", "consecutive_block")

    def __init__(self, safe: bool, reason: str, locked: bool = False,
                 kill_switch: bool = False, daily_loss_block: bool = False,
                 drawdown_block: bool = False, consecutive_block: bool = False) -> None:
        self.safe               = safe
        self.reason             = reason
        self.locked             = locked
        self.kill_switch        = kill_switch
        self.daily_loss_block   = daily_loss_block
        self.drawdown_block     = drawdown_block
        self.consecutive_block  = consecutive_block

    def to_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe, "reason": self.reason, "locked": self.locked,
            "kill_switch": self.kill_switch, "daily_loss_block": self.daily_loss_block,
            "drawdown_block": self.drawdown_block, "consecutive_block": self.consecutive_block,
        }


def check_all(
    trading_state: dict[str, Any] | None = None,
    adaptive_cfg: dict[str, Any] | None = None,
    data_ok: bool = True,
    data_error: str = "",
) -> SafetyResult:
    """
    Tüm güvenlik koşullarını kontrol et.
    trading_state / adaptive_cfg None ise dosyadan yükle.
    """
    with _LOCK:
        safety = _load()

        # 1. Kill-switch
        if safety.get("kill_switch"):
            result = SafetyResult(False, "Acil durdur etkin.", kill_switch=True)
            _update_result(safety, result)
            return result

        # 2. Güvenlik kilidi
        if safety.get("safety_locked"):
            result = SafetyResult(False,
                f"Güvenlik kilidi: {safety.get('lock_reason', 'bilinmiyor')}",
                locked=True)
            _update_result(safety, result)
            return result

        # 3. Veri kalitesi
        if not data_ok:
            result = SafetyResult(False, f"Veri hatası: {data_error or 'bilinmiyor'}")
            _update_result(safety, result)
            return result

        # Trading state yükle
        if trading_state is None:
            trading_state = _load_trading_state()
        if trading_state is None:
            result = SafetyResult(False, "Hesap durumu dosyası okunamadı.")
            lock_safety("state.json okunamadı.", component="safety_guard")
            _update_result(safety, result)
            return result

        # Adaptive config yükle
        if adaptive_cfg is None:
            adaptive_cfg = _load_adaptive_config()

        daily_loss_limit  = float(adaptive_cfg.get("daily_loss_limit_pct", 1.0))
        max_drawdown      = float(adaptive_cfg.get("max_drawdown_pct", 5.0))
        max_consec        = int(adaptive_cfg.get("max_consecutive_losses", 3))

        balance           = float(trading_state.get("balance", 0))
        day_start         = float(trading_state.get("day_start_balance", balance) or balance)
        consec            = int(trading_state.get("consecutive_losses", 0))

        # 4. Günlük kayıp limiti
        if day_start > 0:
            daily_loss_pct = (day_start - balance) / day_start * 100
            if daily_loss_pct >= daily_loss_limit:
                safety["daily_loss_block"] = True
                result = SafetyResult(
                    False,
                    f"Günlük zarar limiti aşıldı: %{daily_loss_pct:.2f} >= %{daily_loss_limit}",
                    daily_loss_block=True,
                )
                _update_result(safety, result)
                ms.append_risk_event(event_type="DAILY_LOSS_BLOCK",
                                     reason=result.reason,
                                     details={"daily_loss_pct": round(daily_loss_pct, 3)})
                return result
            else:
                safety["daily_loss_block"] = False

        # 5. Toplam drawdown
        trades = trading_state.get("trades", [])
        if isinstance(trades, list) and trades:
            # Başlangıç bakiyesini bul
            start_bal = float(trading_state.get("day_start_balance", balance))
            # Tüm zamanların en yüksek bakiyesi
            all_bals = [start_bal]
            running  = start_bal
            for t in trades:
                running += float(t.get("pnl", 0) or 0)
                all_bals.append(running)
            peak = max(all_bals)
            if peak > 0:
                dd_pct = (peak - balance) / peak * 100
                if dd_pct >= max_drawdown:
                    safety["drawdown_block"] = True
                    result = SafetyResult(
                        False,
                        f"Maksimum drawdown aşıldı: %{dd_pct:.2f} >= %{max_drawdown}",
                        drawdown_block=True,
                    )
                    _update_result(safety, result)
                    lock_safety(result.reason, component="drawdown_guard")
                    ms.append_risk_event(event_type="DRAWDOWN_KILL",
                                         reason=result.reason,
                                         details={"drawdown_pct": round(dd_pct, 3)})
                    return result
                else:
                    safety["drawdown_block"] = False

        # 6. Ardışık zarar
        if consec >= max_consec:
            safety["consecutive_block_at"] = datetime.now(timezone.utc).isoformat()
            result = SafetyResult(
                False,
                f"Ardışık zarar limiti: {consec} >= {max_consec}",
                consecutive_block=True,
            )
            _update_result(safety, result)
            ms.append_risk_event(event_type="CONSEC_LOSS_BLOCK",
                                 reason=result.reason,
                                 details={"consecutive_losses": consec})
            return result

        # Tüm kontroller geçti
        result = SafetyResult(True, "Tüm kontroller geçti.")
        _update_result(safety, result)
        return result


def _update_result(safety: dict, result: SafetyResult) -> None:
    safety["last_check_time"]   = datetime.now(timezone.utc).isoformat()
    safety["last_check_result"] = "safe" if result.safe else "blocked"
    safety["last_check_reason"] = result.reason
    _save(safety)


# ══════════════════════════════════════════════════════════════════════════════
# Risk hesaplaması için yardımcı
# ══════════════════════════════════════════════════════════════════════════════

def get_drawdown_pct(trading_state: dict[str, Any]) -> float:
    """Mevcut toplam drawdown yüzdesini hesapla."""
    balance  = float(trading_state.get("balance", 0))
    trades   = trading_state.get("trades", [])
    start    = float(trading_state.get("day_start_balance", balance) or balance)
    if not isinstance(trades, list):
        return 0.0
    peak = start
    running = start
    for t in trades:
        running += float(t.get("pnl", 0) or 0)
        peak = max(peak, running)
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - balance) / peak * 100)


def get_daily_pnl(trading_state: dict[str, Any]) -> float:
    balance   = float(trading_state.get("balance", 0))
    day_start = float(trading_state.get("day_start_balance", balance) or balance)
    return round(balance - day_start, 4)
