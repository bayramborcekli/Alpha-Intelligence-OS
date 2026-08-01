"""ADR-024 kanıt raporunu Trading Home'a salt-okunur sunar."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "data" / "paper_profit_evidence.json"


def snapshot(path: Path = REPORT_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "ok": True,
            "status": "NOT_RUN",
            "activation": "BLOCKED_NO_EVIDENCE",
            "strategy_version": "PAPER_PROFIT_V1_CANDIDATE",
            "timeframe": "4h",
            "message": "Windows kanıt testi henüz çalıştırılmadı.",
            "live_orders": "DISABLED",
            "exchange_write_requests": 0,
        }
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {
            "ok": False,
            "status": "DATA_UNAVAILABLE",
            "activation": "BLOCKED_INVALID_EVIDENCE",
            "strategy_version": "PAPER_PROFIT_V1_CANDIDATE",
            "timeframe": "4h",
            "message": "Kârlılık kanıt raporu okunamıyor.",
            "live_orders": "DISABLED",
            "exchange_write_requests": 0,
        }
    if not isinstance(value, dict):
        return {
            "ok": False,
            "status": "DATA_UNAVAILABLE",
            "activation": "BLOCKED_INVALID_EVIDENCE",
            "strategy_version": "PAPER_PROFIT_V1_CANDIDATE",
            "timeframe": "4h",
            "message": "Kârlılık kanıt raporu sözleşmeyle uyumsuz.",
            "live_orders": "DISABLED",
            "exchange_write_requests": 0,
        }
    valid = (
        value.get("source") == "BINANCE_SPOT_PUBLIC_GET" and
        value.get("timeframe") == "4h" and
        value.get("strategy_version") == "PAPER_PROFIT_V1_CANDIDATE" and
        value.get("round_trip_cost_pct") == "0.30" and
        value.get("holdout_used_for_selection") is False and
        isinstance(value.get("validation"), dict) and
        isinstance(value.get("holdout"), dict) and
        isinstance(value.get("gates"), dict) and
        value["gates"].get("live_orders") == "DISABLED" and
        value["gates"].get("exchange_write_requests") == 0
    )
    if not valid:
        return {
            "ok": False,
            "status": "DATA_UNAVAILABLE",
            "activation": "BLOCKED_INVALID_EVIDENCE",
            "strategy_version": "PAPER_PROFIT_V1_CANDIDATE",
            "timeframe": "4h",
            "message": "Kârlılık kanıt raporu sözleşmeyle uyumsuz.",
            "live_orders": "DISABLED",
            "exchange_write_requests": 0,
        }
    value = dict(value)
    value["ok"] = True
    value["live_orders"] = "DISABLED"
    value["exchange_write_requests"] = 0
    return value
