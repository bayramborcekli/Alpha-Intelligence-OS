"""ADR-019 Windows Paper E2E doğrulayıcısının saf sözleşme testleri."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "windows" / "verify_paper_app.py"
SPEC = importlib.util.spec_from_file_location("verify_paper_app", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _payload() -> dict:
    return {
        "ok": True,
        "live_orders": "DISABLED",
        "exchange_write_requests": 0,
        "maximum_open_positions": 10,
        "included_profiles": ["ADR016_REGIME_NET_EV"],
        "required_strategy_version": "RECOVERY_FOCUSED_V1",
        "legacy_evidence_excluded": True,
        "hourly_frequency": {
            "required_per_full_hour": 5,
            "force_filled_trades": 0,
        },
        "performance": {"minimum_completed_trades_required": 20},
        "promotion": {
            "status": "NOT_EVALUATED",
            "live_promotion_allowed": False,
        },
        "learning": {
            "status": "SCHEDULER_STOPPED",
            "automatic_code_rewrite_allowed": False,
            "automatic_live_promotion_allowed": False,
            "structural_strategy_revision_supported": False,
        },
    }


def test_safe_not_evaluated_payload_is_application_pass():
    assert MODULE.validate_validation(_payload()) == []


def test_every_safety_drift_fails():
    mutations = (
        ("live_orders", "ENABLED", "LIVE_ORDERS_NOT_DISABLED"),
        ("exchange_write_requests", 1, "EXCHANGE_WRITE_REQUESTS_NONZERO"),
        ("maximum_open_positions", 11, "POSITION_LIMIT_NOT_TEN"),
    )
    for key, value, finding in mutations:
        payload = _payload()
        payload[key] = value
        assert finding in MODULE.validate_validation(payload)
    payload = _payload()
    payload["hourly_frequency"]["force_filled_trades"] = 1
    assert "FORCE_FILLED_TRADES_NONZERO" in \
        MODULE.validate_validation(payload)
    payload = _payload()
    payload["included_profiles"] = ["STRICT"]
    assert "QUALIFIED_PROFILES_INVALID" in \
        MODULE.validate_validation(payload)
    payload = _payload()
    payload["required_strategy_version"] = "OLD"
    assert "STRATEGY_VERSION_INVALID" in \
        MODULE.validate_validation(payload)
    payload = _payload()
    payload["legacy_evidence_excluded"] = False
    assert "LEGACY_EVIDENCE_NOT_EXCLUDED" in \
        MODULE.validate_validation(payload)


def test_home_contract_requires_all_markers():
    html = " ".join(MODULE.REQUIRED_HOME_MARKERS)
    assert MODULE.validate_home(html) == []
    assert MODULE.validate_home(html.replace('id="th-val-pf"', "")) == [
        'HOME_MARKER_MISSING:id="th-val-pf"']


def test_verifier_has_get_only_surface():
    source = PATH.read_text(encoding="utf-8")
    for forbidden in ("Request(", "method=", "/api/v3/order", "write_text",
                      "write_bytes", "requests.post"):
        assert forbidden not in source
