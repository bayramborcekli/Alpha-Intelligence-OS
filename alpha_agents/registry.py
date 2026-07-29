"""Minimum agent registry — agent_id → tanım + kalıcı durum.

Durum (last_run/last_result/last_error) data/agent_registry.json'da
tutulur; import sırasında ağır işlem yapılmaz.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "agent_registry.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False,
                                         indent=2), encoding="utf-8")
    except OSError:
        pass


class Agent:
    """Tanım + tembel çalıştırma. run_fn/status_fn açık çağrıyla çalışır."""

    def __init__(self, agent_id: str, agent_name: str,
                 capabilities: list[str],
                 status_fn: Callable[[], dict],
                 run_fn: Callable[[], dict] | None = None,
                 enabled: bool = True):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.capabilities = capabilities
        self._status_fn = status_fn
        self._run_fn = run_fn
        self.enabled = enabled

    def describe(self) -> dict[str, Any]:
        st = _load_state().get(self.agent_id, {})
        try:
            live = self._status_fn() or {}
        except Exception as exc:  # durum okuma asla patlatmaz
            live = {"status_error": type(exc).__name__}
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "enabled": self.enabled,
            "status": live,
            "last_run": st.get("last_run"),
            "last_result": st.get("last_result"),
            "last_error": st.get("last_error"),
            "capabilities": self.capabilities,
        }

    def run(self) -> dict:
        if not self.enabled or self._run_fn is None:
            raise RuntimeError("agent çalıştırılamaz (devre dışı/salt durum)")
        state = _load_state()
        entry = {"last_run": _now(), "last_result": None,
                 "last_error": None}
        try:
            result = self._run_fn()
            entry["last_result"] = "PASS" if result.get(
                "last_result", result.get("status")) in (
                "PASS", "CONNECTED_READ_ONLY") or result.get(
                "runtime_card") == "green" else "PARTIAL"
            return result
        except Exception as exc:
            entry["last_result"] = "FAIL"
            entry["last_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            raise
        finally:
            state[self.agent_id] = entry
            _save_state(state)


def _registry() -> dict[str, Agent]:
    # Tembel import: uygulama import'unda ağır modül yüklenmez.
    from alpha_agents.binance_connection_agent import build as build_binance
    from alpha_agents.windows_runtime_agent import build as build_windows
    agents = [build_windows(), build_binance()]
    return {a.agent_id: a for a in agents}


def list_agents() -> list[dict]:
    return [a.describe() for a in _registry().values()]


def get_agent(agent_id: str) -> Agent | None:
    return _registry().get(agent_id)
