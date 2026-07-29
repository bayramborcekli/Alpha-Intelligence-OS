"""
metrics_store.py — Karar, işlem, rejim ve öğrenme günlüklerini JSONL formatında
atomik ve dayanıklı biçimde saklar. Loglara gizli bilgi yazılmaz.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

LOG_FILES: dict[str, Path] = {
    "decisions":         ROOT / "decisions.jsonl",
    "risk_events":       ROOT / "risk_events.jsonl",
    "learning_updates":  ROOT / "learning_updates.jsonl",
    "regime_history":    ROOT / "regime_history.jsonl",
    "universe_changes":  ROOT / "universe_changes.jsonl",
    "system_errors":     ROOT / "system_errors.jsonl",
}

MAX_BYTES     = 10 * 1024 * 1024   # 10 MB
MAX_LINES     = 2_000
ROTATE_KEEP   = 3                   # kaç eski dosya tutulsun

_LOCKS: dict[str, threading.Lock] = {k: threading.Lock() for k in LOG_FILES}


# ══════════════════════════════════════════════════════════════════════════════
# Düşük seviye yardımcılar
# ══════════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rotate(path: Path) -> None:
    """Dosya MAX_BYTES veya MAX_LINES'ı aşmışsa .1, .2 … olarak döndür."""
    try:
        if not path.exists():
            return
        stat = path.stat()
        if stat.st_size < MAX_BYTES:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                n = sum(1 for _ in f)
            if n < MAX_LINES:
                return
        # Eski arşivleri kaydır
        for i in range(ROTATE_KEEP - 1, 0, -1):
            src = path.with_suffix(f".jsonl.{i}")
            dst = path.with_suffix(f".jsonl.{i + 1}")
            if src.exists():
                src.replace(dst)
        path.replace(path.with_suffix(".jsonl.1"))
    except OSError:
        pass


def _atomic_append(path: Path, record: dict[str, Any]) -> None:
    """JSONL satırını atomik şekilde dosyanın sonuna ekle."""
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    # temp → rename yerine append+fsync (JSONL için standart)
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def _append(key: str, record: dict[str, Any]) -> None:
    path = LOG_FILES[key]
    with _LOCKS[key]:
        _rotate(path)
        _atomic_append(path, record)


def _read_tail(key: str, n: int = 100) -> list[dict[str, Any]]:
    """Dosyanın sonundan en fazla n kayıt oku."""
    path = LOG_FILES[key]
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        results = []
        for line in reversed(lines[-n:]):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return results
    except OSError:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Karar günlüğü
# ══════════════════════════════════════════════════════════════════════════════

def append_decision(
    *,
    symbol: str,
    price: float,
    regime: str,
    regime_confidence: float,
    strategy_score: float,
    final_score: float,
    risk_pct: float,
    stop: float | None,
    target: float | None,
    decision: str,           # "OPEN" | "WATCH" | "REJECT"
    reason: str,
    config_version: str = "1",
    components: dict | None = None,
    trace: dict | None = None,
) -> None:
    """``trace``: Decision Trace ek alanları (correlation_id,
    data_status, selected_risk_profile, calculated_position_size,
    risk_result, final_decision, rejection_reason ...). Karar kaydına
    olduğu gibi eklenir; eski kayıtlarla geriye uyumlu (opsiyonel)."""
    _append("decisions", {
        **(trace or {}),
        "ts": _now_iso(), "symbol": symbol, "price": round(price, 8),
        "regime": regime, "regime_confidence": regime_confidence,
        "strategy_score": strategy_score, "final_score": final_score,
        "risk_pct": risk_pct,
        "stop": round(stop, 8) if stop else None,
        "target": round(target, 8) if target else None,
        "decision": decision, "reason": reason,
        "config_version": config_version,
        "components": components or {},
    })


def get_recent_decisions(n: int = 20) -> list[dict]:
    return _read_tail("decisions", n)


# ══════════════════════════════════════════════════════════════════════════════
# Risk olayları
# ══════════════════════════════════════════════════════════════════════════════

def append_risk_event(
    *,
    event_type: str,
    reason: str,
    details: dict | None = None,
) -> None:
    _append("risk_events", {
        "ts": _now_iso(), "event_type": event_type,
        "reason": reason, "details": details or {},
    })


def get_recent_risk_events(n: int = 50) -> list[dict]:
    return _read_tail("risk_events", n)


# ══════════════════════════════════════════════════════════════════════════════
# Öğrenme güncellemeleri
# ══════════════════════════════════════════════════════════════════════════════

def append_learning_update(
    *,
    version: int,
    changes: dict[str, Any],
    trade_count: int,
    confidence: str,
    shadow_result: dict | None = None,
) -> None:
    _append("learning_updates", {
        "ts": _now_iso(), "version": version, "changes": changes,
        "trade_count": trade_count, "confidence": confidence,
        "shadow_result": shadow_result or {},
    })


def get_recent_learning_updates(n: int = 20) -> list[dict]:
    return _read_tail("learning_updates", n)


# ══════════════════════════════════════════════════════════════════════════════
# Rejim geçmişi
# ══════════════════════════════════════════════════════════════════════════════

def append_regime(
    *,
    symbol: str,
    regime: str,
    confidence: float,
    direction: str,
    volatility: str,
    trend_strength: float,
    suitable: bool,
    reason: str,
) -> None:
    _append("regime_history", {
        "ts": _now_iso(), "symbol": symbol, "regime": regime,
        "confidence": confidence, "direction": direction,
        "volatility": volatility, "trend_strength": trend_strength,
        "suitable": suitable, "reason": reason,
    })


def get_recent_regime_history(n: int = 50) -> list[dict]:
    return _read_tail("regime_history", n)


# ══════════════════════════════════════════════════════════════════════════════
# Evren değişiklikleri
# ══════════════════════════════════════════════════════════════════════════════

def append_universe_change(
    *,
    added: list[str],
    removed: list[str],
    mode: str,
    reason: str,
) -> None:
    _append("universe_changes", {
        "ts": _now_iso(), "added": added, "removed": removed,
        "mode": mode, "reason": reason,
    })


# ══════════════════════════════════════════════════════════════════════════════
# Sistem hataları
# ══════════════════════════════════════════════════════════════════════════════

def append_system_error(
    *,
    component: str,
    error_type: str,
    message: str,
    safe_state_activated: bool = False,
) -> None:
    _append("system_errors", {
        "ts": _now_iso(), "component": component,
        "error_type": error_type, "message": message[:500],
        "safe_state_activated": safe_state_activated,
    })


def get_recent_errors(n: int = 20) -> list[dict]:
    return _read_tail("system_errors", n)


# ══════════════════════════════════════════════════════════════════════════════
# Panel durum dosyası
# ══════════════════════════════════════════════════════════════════════════════

PANEL_STATUS_PATH = ROOT / "panel_status.json"
_PANEL_LOCK = threading.Lock()


def update_panel_status(data: dict[str, Any]) -> None:
    """Panelin /api/* uç noktaları için durum dosyasını güncelle."""
    with _PANEL_LOCK:
        existing: dict[str, Any] = {}
        if PANEL_STATUS_PATH.exists():
            try:
                with PANEL_STATUS_PATH.open("r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(data)
        existing["updated_at"] = _now_iso()
        tmp = PANEL_STATUS_PATH.with_name(".panel_status.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(PANEL_STATUS_PATH)
        except OSError:
            pass
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)


def read_panel_status() -> dict[str, Any]:
    if not PANEL_STATUS_PATH.exists():
        return {}
    try:
        with PANEL_STATUS_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
