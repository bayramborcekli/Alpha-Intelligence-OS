#!/usr/bin/env python3
"""Fail-closed project alignment check for humans, agents and CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "governance" / "project_state.json"
REQUIRED = (
    ROOT / "SYSTEM_CONSTITUTION.md",
    ROOT / "DECISIONS.md",
    ROOT / "CURRENT_TASK.md",
    STATE_PATH,
)


def validate() -> tuple[dict, list[str]]:
    errors: list[str] = []
    for path in REQUIRED:
        if not path.is_file():
            errors.append(f"missing:{path.relative_to(ROOT)}")
    if errors:
        return {}, errors

    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"invalid_state:{type(exc).__name__}"]

    safety = state.get("safety", {})
    strategy = state.get("strategy", {})
    priorities = state.get("priorities", [])
    checks = {
        "source_of_truth": state.get("source_of_truth") is True,
        "paper_mode": state.get("operating_mode") == "PAPER_LEARNING",
        "pause_active": safety.get("global_pause") == "ACTIVE",
        "live_disabled": safety.get("live_orders") == "DISABLED",
        "zero_exchange_writes": safety.get("exchange_write_requests_allowed") == 0,
        "paper_only": safety.get("paper_only") is True,
        "rr_is_quality_target": strategy.get("net_reward_risk_1_20") == "quality_target_not_paper_blocker",
        "one_change": strategy.get("one_hypothesis_change_at_a_time") is True,
        "one_active_priority": sum(p.get("status") == "IN_PROGRESS" for p in priorities) == 1,
        "priority_order": [p.get("rank") for p in priorities] == list(range(1, len(priorities) + 1)),
        "decision_head_present": state.get("decision_head", "") in (ROOT / "DECISIONS.md").read_text(encoding="utf-8"),
    }
    errors.extend(name for name, passed in checks.items() if not passed)
    return state, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate and print the active project contract")
    parser.parse_args()
    state, errors = validate()
    if errors:
        print("GOVERNANCE_PREFLIGHT: FAIL")
        print("GOVERNANCE_BLOCKED: " + ", ".join(errors))
        return 1
    active = next(p for p in state["priorities"] if p["status"] == "IN_PROGRESS")
    print("GOVERNANCE_PREFLIGHT: PASS")
    print(f"DECISION_HEAD: {state['decision_head']}")
    print(f"ACTIVE_PRIORITY: {active['id']} — {active['goal']}")
    print(f"OPERATING_MODE: {state['operating_mode']}")
    print(f"GLOBAL_PAUSE: {state['safety']['global_pause']}")
    print(f"LIVE_ORDERS: {state['safety']['live_orders']}")
    print(f"EXCHANGE_WRITE_REQUESTS_ALLOWED: {state['safety']['exchange_write_requests_allowed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
