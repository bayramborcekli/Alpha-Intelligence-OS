"""ADR-019 Windows Paper doğrulama API ve UI sözleşmesi."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paper_validation_api as pva


NOW = datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _at(hour: int, minute: int = 0) -> str:
    return datetime(2026, 8, 1, hour, minute,
                    tzinfo=timezone.utc).isoformat()


def _trade(symbol: str, opened_at: str, pnl: str,
           *, profile: str = "ADR016_REGIME_NET_EV",
           model: str = "ALPHA_CORE_SCALP",
           strategy_version: str | None = pva.REQUIRED_STRATEGY_VERSION
           ) -> dict:
    row = {
        "symbol": symbol,
        "model": model,
        "opened_at": opened_at,
        "closed_at": NOW.isoformat(),
        "execution_mode": "PAPER",
        "profile": profile,
        "net_pnl": pnl,
    }
    if strategy_version is not None:
        row["decision_engine"] = {"strategy_version": strategy_version}
    return row


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_no_runtime_data_is_honest_not_evaluated(tmp_path):
    config = tmp_path / "config.json"
    _write(config, {"adaptive_system": {"enabled": False,
                                         "learning_enabled": True}})
    result = pva.snapshot(state_path=tmp_path / "state.json",
                          dual_runtime_path=tmp_path / "dual.json",
                          config_path=config, now=NOW)
    assert result["promotion"]["status"] == "NOT_EVALUATED"
    assert result["performance"]["net_after_costs_usdt"] is None
    assert result["hourly_frequency"]["force_filled_trades"] == 0
    assert result["live_orders"] == "DISABLED"
    assert result["exchange_write_requests"] == 0
    assert result["learning"]["status"] == "SCHEDULER_STOPPED"
    assert "ADAPTIVE_SYSTEM_DISABLED" in result["learning"]["blockers"]
    assert result["learning"]["automatic_code_rewrite_allowed"] is False


def test_learning_status_distinguishes_scheduler_and_evidence(tmp_path):
    config = tmp_path / "config.json"
    _write(config, {"adaptive_system": {
        "enabled": True, "mode": "MONITOR", "auto_paper_enabled": False,
        "learning_enabled": True}})
    result = pva.snapshot(
        state_path=tmp_path / "state.json",
        dual_runtime_path=tmp_path / "dual.json",
        config_path=config, now=NOW)
    learning = result["learning"]
    assert learning["status"] == "COLLECTING_EVIDENCE"
    assert learning["scheduled_worker_effective"] is True
    assert learning["auto_paper_enabled"] is False
    assert learning["thresholds"] == {
        "diagnosis": 20, "challenger_proposal": 50,
        "promotion_review": 75}
    assert learning["structural_strategy_revision_supported"] is False


def test_each_observed_full_hour_must_have_five_without_force_fill(tmp_path):
    state = tmp_path / "state.json"
    dual = tmp_path / "dual.json"
    _write(state, {"position": None, "trades": []})
    trades = []
    for hour, count in ((9, 5), (10, 4), (11, 5)):
        for index in range(count):
            trades.append(_trade(f"C{hour}{index}USDT",
                                 _at(hour, index), "1"))
    _write(dual, {"positions": {}, "trades": trades})
    result = pva.snapshot(state_path=state, dual_runtime_path=dual, now=NOW)
    hourly = result["hourly_frequency"]
    assert hourly["evaluated_full_hours"] == 3
    assert hourly["hours_meeting_target"] == 2
    assert hourly["minimum_buys_in_full_hour"] == 4
    assert hourly["force_filled_trades"] == 0
    assert hourly["gate_status"] == "FAIL"


def test_promotion_passes_only_with_all_evidence(tmp_path):
    state = tmp_path / "state.json"
    dual = tmp_path / "dual.json"
    _write(state, {"position": None, "trades": []})
    trades = []
    for index in range(24):
        hour = 9 + index % 3
        pnl = "2" if index < 18 else "-1"
        trades.append(_trade(f"P{index}USDT", _at(hour, index % 50), pnl,
                             profile="ADR016_REGIME_NET_EV"))
    _write(dual, {"positions": {}, "trades": trades})
    result = pva.snapshot(state_path=state, dual_runtime_path=dual, now=NOW)
    assert result["hourly_frequency"]["gate_status"] == "PASS"
    assert result["performance"]["completed_trades"] == 24
    assert result["performance"]["net_after_costs_usdt"] == "30.0000"
    assert result["performance"]["profit_factor"] == "6.0000"
    assert result["promotion"]["status"] == "PASS"
    assert result["promotion"]["live_promotion_allowed"] is False


def test_open_and_closed_copy_is_one_buy_and_capacity_is_reported(tmp_path):
    state = tmp_path / "state.json"
    dual = tmp_path / "dual.json"
    row = _trade("BTCUSDT", _at(11), "1")
    _write(state, {"position": None, "trades": []})
    _write(dual, {"positions": {"BTCUSDT": dict(row)},
                  "trades": [dict(row)]})
    result = pva.snapshot(state_path=state, dual_runtime_path=dual, now=NOW)
    assert result["accepted_paper_openings_in_window"] == 1
    assert result["performance"]["completed_trades"] == 1
    assert result["open_positions"] == 1
    assert result["maximum_open_positions"] == 10


def test_duplicate_closed_trade_does_not_inflate_performance(tmp_path):
    state = tmp_path / "state.json"
    dual = tmp_path / "dual.json"
    row = _trade("BTCUSDT", _at(11), "2")
    row["trade_id"] = "same-id"
    _write(state, {"position": None})
    _write(dual, {"positions": {}, "trades": [row, dict(row)]})
    result = pva.snapshot(state_path=state, dual_runtime_path=dual, now=NOW)
    assert result["accepted_paper_openings_in_window"] == 1
    assert result["performance"]["completed_trades"] == 1
    assert result["performance"]["net_after_costs_usdt"] == "2.0000"


def test_strict_and_classic_do_not_pollute_candidate_metrics(tmp_path):
    state = tmp_path / "state.json"
    dual = tmp_path / "dual.json"
    _write(state, {"position": {"symbol": "LEGACYUSDT",
                                 "opened_at": _at(11)}})
    strict = _trade("STRICTUSDT", _at(11), "999", profile="STRICT")
    live = _trade("LIVEUSDT", _at(11), "999")
    live["execution_mode"] = "LIVE"
    _write(dual, {"positions": {}, "trades": [strict, live]})
    result = pva.snapshot(state_path=state, dual_runtime_path=dual, now=NOW)
    assert result["accepted_paper_openings_in_window"] == 0
    assert result["performance"]["completed_trades"] == 0
    assert result["performance"]["net_after_costs_usdt"] is None
    assert result["open_positions"] == 1


def test_legacy_learning_and_unversioned_adr016_do_not_pollute_new_cohort(
        tmp_path):
    state = tmp_path / "state.json"
    dual = tmp_path / "dual.json"
    _write(state, {"position": None})
    legacy_learning = _trade(
        "OLDLEARNUSDT", _at(11), "-99", profile="PAPER_LEARNING")
    unversioned = _trade(
        "OLDADRUSDT", _at(11), "-88", strategy_version=None)
    wrong_version = _trade(
        "OTHERUSDT", _at(11), "-77", strategy_version="OTHER_V1")
    current = _trade("NEWUSDT", _at(11), "2")
    _write(dual, {"positions": {}, "trades": [
        legacy_learning, unversioned, wrong_version, current]})
    result = pva.snapshot(state_path=state, dual_runtime_path=dual, now=NOW)
    assert result["accepted_paper_openings_in_window"] == 1
    assert result["performance"]["completed_trades"] == 1
    assert result["performance"]["net_after_costs_usdt"] == "2.0000"
    assert result["required_strategy_version"] == "RECOVERY_FOCUSED_V1"
    assert result["legacy_evidence_excluded"] is True


def test_unreadable_source_fails_closed(tmp_path):
    state = tmp_path / "state.json"
    dual = tmp_path / "dual.json"
    state.write_text("{broken", encoding="utf-8")
    _write(dual, {"positions": {}, "trades": []})
    result = pva.snapshot(state_path=state, dual_runtime_path=dual, now=NOW)
    assert result["source_integrity"] == "UNAVAILABLE"
    assert result["promotion"]["status"] == "DATA_UNAVAILABLE"


def test_old_observation_with_no_recent_buys_is_fail(tmp_path):
    state = tmp_path / "state.json"
    dual = tmp_path / "dual.json"
    _write(state, {"position": None})
    old = _trade("OLDUSDT", (NOW - timedelta(hours=30)).isoformat(), "1")
    _write(dual, {"positions": {}, "trades": [old]})
    result = pva.snapshot(state_path=state, dual_runtime_path=dual, now=NOW)
    assert result["hourly_frequency"]["evaluated_full_hours"] >= 23
    assert result["hourly_frequency"]["gate_status"] == "FAIL"


def test_money_math_has_no_float_or_exchange_surface():
    source = (ROOT / "paper_validation_api.py").read_text(encoding="utf-8")
    assert "float(" not in source
    for forbidden in ("/api/v3/order", "apiKey", "secret", "urlopen",
                      "requests.post", "requests.put", "requests.delete"):
        assert forbidden not in source


def test_route_is_get_only_authenticated_by_global_gate_and_no_store(
        monkeypatch, tmp_path):
    import app as app_module
    state = tmp_path / "state.json"
    dual = tmp_path / "dual.json"
    _write(state, {"position": None})
    _write(dual, {"positions": {}, "trades": []})
    monkeypatch.setattr(pva, "STATE_PATH", state)
    monkeypatch.setattr(pva, "DUAL_RUNTIME_PATH", dual)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        response = client.get("/api/paper/validation")
        post = client.post("/api/paper/validation")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.get_json()["live_orders"] == "DISABLED"
    assert post.status_code == 405


def test_route_failure_is_sterile(monkeypatch):
    import app as app_module

    def _fail():
        raise RuntimeError("sensitive detail")

    monkeypatch.setattr(pva, "snapshot", _fail)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        response = client.get("/api/paper/validation")
    body = response.get_json()
    assert response.status_code == 503
    assert body["error"]["code"] == "PAPER_VALIDATION_UNAVAILABLE"
    assert "sensitive detail" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store, private"


def test_trading_home_contains_real_validation_contract():
    template = (ROOT / "templates" / "trading_home.html").read_text(
        encoding="utf-8")
    css = (ROOT / "static" / "css" / "trading_home_v2.css").read_text(
        encoding="utf-8")
    js = (ROOT / "static" / "js" / "trading_home.js").read_text(
        encoding="utf-8")
    for token in ("PAPER PROFIT V1 — 4 SAATLİK KANIT",
                  "Trend filtreli kanal kırılımı",
                  "%0,30 maliyet",
                  "th-val-status", "th-val-hour", "th-val-net",
                  "th-val-pf", "th-val-capacity"):
        assert token in template
    assert ".th-validation" in css
    assert 'fetch("/api/paper-profit/evidence"' in js
    assert "Holdout" in js
    assert "th-val-learning" in template
    assert "Stop katsayısı" in js
    assert "/api/v3/order" not in js
