"""Project direction must fail closed when decisions drift."""

from scripts.project_preflight import ROOT, validate


def test_governance_preflight_passes():
    state, errors = validate()
    assert errors == []
    assert state["decision_head"] == "ADR-012"


def test_live_and_exchange_writes_remain_disabled():
    state, _ = validate()
    assert state["safety"]["live_orders"] == "DISABLED"
    assert state["safety"]["exchange_write_requests_allowed"] == 0
    assert state["safety"]["paper_only"] is True


def test_paper_learning_contract_is_current():
    state, _ = validate()
    strategy = state["strategy"]
    assert strategy["net_reward_risk_1_20"] == "quality_target_not_paper_blocker"
    assert "STALE_OR_INVALID_MARKET_DATA" in strategy["paper_hard_safety"]
    assert "LIVE_ORDER_PROHIBITION" in strategy["paper_hard_safety"]


def test_only_windows_paper_flow_is_active_priority():
    state, _ = validate()
    active = [p for p in state["priorities"] if p["status"] == "IN_PROGRESS"]
    assert [p["id"] for p in active] == ["PAPER_FLOW_WINDOWS"]
    assert state["priorities"][2]["id"] == "TRADING_HOME_REFERENCE"


def test_agent_instructions_require_preflight():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for required in (
        "governance/project_state.json",
        "SYSTEM_CONSTITUTION.md",
        "DECISIONS.md",
        "CURRENT_TASK.md",
        "python scripts/project_preflight.py --check",
        "GOVERNANCE_BLOCKED",
    ):
        assert required in agents
