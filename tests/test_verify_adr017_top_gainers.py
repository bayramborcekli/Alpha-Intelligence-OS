"""Canlı Top Gainers Windows doğrulayıcısının çevrimdışı testleri."""
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "windows" / "verify_adr017_top_gainers.py"
_spec = importlib.util.spec_from_file_location(
    "verify_adr017_top_gainers", TOOL)
live = importlib.util.module_from_spec(_spec)
sys.modules["verify_adr017_top_gainers"] = live
_spec.loader.exec_module(live)


def _symbol(base, *, status="TRADING", spot=True):
    return {
        "symbol": f"{base}USDT", "baseAsset": base,
        "quoteAsset": "USDT", "status": status,
        "isSpotTradingAllowed": spot,
    }


def _ticker(symbol, change, volume=1_000_000):
    return {
        "symbol": symbol, "priceChangePercent": str(change),
        "quoteVolume": str(volume), "count": "100000",
        "bidPrice": "99.9", "askPrice": "100.1", "lastPrice": "100",
        "highPrice": "105", "lowPrice": "95",
    }


def test_universe_includes_all_trading_usdt_altcoins_and_excludes_noise():
    info = {"symbols": [
        _symbol("ETH"), _symbol("DOGE"), _symbol("BTC"),
        _symbol("USDC"), _symbol("ETHUP"), _symbol("OLD", status="BREAK"),
        {**_symbol("EUR"), "quoteAsset": "EUR", "symbol": "ETHEUR"},
    ]}
    assert live.tradable_altcoin_symbols(info) == {"ETHUSDT", "DOGEUSDT"}


def test_live_top_gainers_are_positive_and_ranked_at_call_time():
    info = {"symbols": [_symbol("ETH"), _symbol("DOGE"), _symbol("ADA")]}
    count, rows = live.select_live_top_gainers(info, [
        _ticker("ETHUSDT", 3, 8_000_000),
        _ticker("DOGEUSDT", 7, 2_000_000),
        _ticker("ADAUSDT", -1, 9_000_000),
    ], top=20)
    assert count == 3
    assert [row["symbol"] for row in rows] == ["DOGEUSDT", "ETHUSDT"]
    assert [row["live_rank"] for row in rows] == [1, 2]


def test_evaluator_uses_public_live_list_and_never_opens_position(monkeypatch):
    info = {"symbols": [_symbol("DOGE"), _symbol("ETH")]}
    monkeypatch.setattr(live.dm, "_guarded_get", lambda path, **kwargs: info)
    monkeypatch.setattr(live.dm, "fetch_spot_tickers", lambda: [
        _ticker("DOGEUSDT", 8), _ticker("ETHUSDT", 4)])
    monkeypatch.setattr(live.dm, "fetch_adr016_klines",
                        lambda symbol, cfg: [[1]] * 1000)

    def decision(row, symbol, klines, model, cfg):
        eligible = symbol == "DOGEUSDT"
        return ({
            "regime": "TREND", "strategy": "TREND_PULLBACK",
            "sample_size": 40,
            "probabilities": {"tp": "0.700000"},
            "net_ev_pct": "0.1200",
            "net_ev_lower_bound_pct": "0.0500" if eligible else "-0.0100",
        }, None, eligible,
            None if eligible else "NET_EV_CONFIDENCE_LOW", 0.12)

    monkeypatch.setattr(live.dm, "evaluate_adr016_candidate", decision)
    report = live.evaluate_live_top_gainers(20, max_minutes=10)
    assert report["universe_size"] == 2
    assert report["best_eligible_list"] == ["DOGEUSDT"]
    assert report["live_orders"] == "DISABLED"
    assert report["exchange_write_requests"] == 0
    assert report["max_runtime_minutes"] == 10.0
    assert report["timed_out"] is False


def test_ten_minute_deadline_stops_before_next_symbol(monkeypatch):
    info = {"symbols": [_symbol("DOGE"), _symbol("ETH")]}
    monkeypatch.setattr(live.dm, "_guarded_get", lambda path, **kwargs: info)
    monkeypatch.setattr(live.dm, "fetch_spot_tickers", lambda: [
        _ticker("DOGEUSDT", 8), _ticker("ETHUSDT", 4)])
    ticks = iter([0.0, 0.0, 601.0])
    monkeypatch.setattr(live.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(live.dm, "fetch_adr016_klines",
                        lambda symbol, cfg: [[1]] * 1000)
    monkeypatch.setattr(live.dm, "evaluate_adr016_candidate", lambda *args: (
        {"regime": "TREND", "strategy": "TREND_PULLBACK",
         "sample_size": 40, "probabilities": {"tp": "0.7"},
         "net_ev_pct": "0.1", "net_ev_lower_bound_pct": "0.05"},
        None, True, None, 0.1))
    report = live.evaluate_live_top_gainers(20, max_minutes=10)
    assert report["evaluated_before_deadline"] == 1
    assert report["timed_out"] is True


def test_report_writes_exact_json_and_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "JSON_REPORT", tmp_path / "report.json")
    monkeypatch.setattr(live, "CSV_REPORT", tmp_path / "report.csv")
    report = {"top_gainers": [{
        "live_rank": 1, "symbol": "DOGEUSDT",
        "price_change_24h_pct": 9.0, "quote_volume_24h_usdt": 1.0,
        "spread_pct": 0.01, "decision": "NO_TRADE",
        "reason_code": "STRATEGY_NOT_CONFIRMED", "regime": "TREND",
        "strategy": "TREND_PULLBACK", "calibration_samples": 0,
        "historical_tp_probability": None, "historical_net_ev_pct": None,
        "historical_net_ev_lower_bound_pct": None,
    }]}
    live.write_reports(report)
    assert json.loads(live.JSON_REPORT.read_text(encoding="utf-8")) == report
    assert "DOGEUSDT" in live.CSV_REPORT.read_text(encoding="utf-8-sig")


def test_source_contains_no_exchange_write_path():
    source = TOOL.read_text(encoding="utf-8")
    for forbidden in ("/api/v3/order", "X-MBX-APIKEY", "signature="):
        assert forbidden not in source
