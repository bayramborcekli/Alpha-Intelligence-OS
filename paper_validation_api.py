"""Trading Home için salt-okunur ADR-019 Paper terfi kanıtı.

Modül yalnız kanonik yerel Paper runtime dosyalarını okur. Borsaya ağ isteği
yapmaz, dosya yazmaz ve işlem üretmez. Para/PnL hesapları yalnız ``Decimal``
ile yapılır; finansal değerler API'ye string olarak verilir.

Ölçüm sözleşmesi:

* gözlenmiş her tam UTC saatinde en az 5 gerçek nitelikli Paper açılışı,
* eksik saatleri doldurmak için sahte/zorunlu işlem yok,
* maliyet sonrası net sonuç pozitif ve profit factor en az 1.20,
* performans hükmünden önce en az 20 kapanmış işlem,
* bütün Paper kaynakları birlikte en fazla 10 açık pozisyon.

Sentetik ``RECOVERY_FOCUSED`` sonucu runtime sonucu gibi kullanılmaz. Aday
ölçümüne yalnız gerçek ``ADR016_REGIME_NET_EV`` profilli ve karar kaydında
``RECOVERY_FOCUSED_V1`` strateji sürümünü taşıyan işlemler girer. Önceki
``PAPER_LEARNING`` / sürümsüz ADR-016 / STRICT / klasik Paper kayıtları yeni
performans dönemine katılmaz; bütün açık Paper kayıtları kapasitede sayılır.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "alpha20_v1" / "state.json"
DUAL_RUNTIME_PATH = ROOT / "alpha20_v1" / "dual_model_runtime.json"
CONFIG_PATH = ROOT / "alpha20_v1" / "config.json"

MIN_QUALIFIED_BUYS_PER_FULL_HOUR = 5
MIN_COMPLETED_TRADES = 20
MIN_PROFIT_FACTOR = Decimal("1.20")
MAX_OPEN_POSITIONS = 10
LOOKBACK_HOURS = 24
QUALIFIED_PROFILES = frozenset({"ADR016_REGIME_NET_EV"})
REQUIRED_STRATEGY_VERSION = "RECOVERY_FOCUSED_V1"
LEARNING_THRESHOLDS = {
    "diagnosis": 20,
    "challenger_proposal": 50,
    "promotion_review": 75,
}
_ZERO = Decimal("0")
_Q4 = Decimal("0.0001")


def _read_object(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "MISSING"
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}, "UNREADABLE"
    return (raw, "OK") if isinstance(raw, dict) else ({}, "UNREADABLE")


def _iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _money(value: Decimal | None) -> str | None:
    return (str(value.quantize(_Q4, rounding=ROUND_HALF_UP))
            if value is not None else None)


def _position_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        return [row for row in raw.values() if isinstance(row, dict)]
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def _single_position(raw: Any) -> list[dict[str, Any]]:
    return [raw] if isinstance(raw, dict) else []


def _event(row: dict[str, Any], source: str) -> dict[str, Any] | None:
    if (row.get("execution_mode") != "PAPER" or
            row.get("profile") not in QUALIFIED_PROFILES):
        return None
    decision = row.get("decision_engine")
    if not isinstance(decision, dict) or \
            decision.get("strategy_version") != REQUIRED_STRATEGY_VERSION:
        return None
    opened = _iso(row.get("opened_at"))
    symbol = row.get("symbol")
    if opened is None or not isinstance(symbol, str) or not symbol:
        return None
    return {
        "opened_at": opened,
        "symbol": symbol,
        "model": str(row.get("model") or "UNKNOWN"),
        "profile": str(row.get("profile")),
        "strategy_version": REQUIRED_STRATEGY_VERSION,
        "source": source,
    }


def _closed_key(row: dict[str, Any]) -> tuple[str, ...]:
    trade_id = row.get("trade_id")
    if isinstance(trade_id, str) and trade_id:
        return ("trade_id", trade_id)
    return (
        "fallback",
        str(row.get("symbol") or ""),
        str(row.get("model") or ""),
        str(row.get("opened_at") or ""),
        str(row.get("closed_at") or ""),
    )


def _collect(state: dict[str, Any], dual: dict[str, Any]) \
        -> tuple[list[dict[str, Any]], list[Decimal], int, int]:
    events: list[dict[str, Any]] = []
    closed_results: list[Decimal] = []
    ignored_pnl = 0

    classic_positions = _single_position(state.get("position"))
    dual_positions = _position_rows(dual.get("positions"))
    dual_trades = _position_rows(dual.get("trades"))

    for row in dual_positions:
        item = _event(row, "dual_runtime.positions")
        if item:
            events.append(item)

    seen_closed: set[tuple[str, ...]] = set()
    for row in dual_trades:
        item = _event(row, "dual_runtime.trades")
        if not item:
            continue
        events.append(item)
        key = _closed_key(row)
        if key in seen_closed:
            continue
        seen_closed.add(key)
        pnl = _decimal(row.get("net_pnl"))
        if pnl is None:
            ignored_pnl += 1
        else:
            closed_results.append(pnl)

    # Açık pozisyon kapanınca aynı opened_at ile trade listesine taşınır.
    # Kaynak değişse bile tek gerçekleşmiş açılış olarak sayılır.
    unique: dict[tuple[str, str, datetime], dict[str, Any]] = {}
    for item in events:
        key = (item["symbol"], item["model"], item["opened_at"])
        unique.setdefault(key, item)
    return (sorted(unique.values(), key=lambda row: row["opened_at"]),
            closed_results, len(classic_positions) + len(dual_positions),
            ignored_pnl)


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _ceil_hour(value: datetime) -> datetime:
    floored = _floor_hour(value)
    return floored if value == floored else floored + timedelta(hours=1)


def _hourly(events: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    lookback_start = now - timedelta(hours=LOOKBACK_HOURS)
    observed = [row for row in events if row["opened_at"] <= now]
    recent = [row for row in observed if row["opened_at"] >= lookback_start]
    current_start = _floor_hour(now)
    current_count = sum(
        current_start <= row["opened_at"] <= now for row in recent)
    if not observed:
        return {
            "required_per_full_hour": MIN_QUALIFIED_BUYS_PER_FULL_HOUR,
            "evaluated_full_hours": 0,
            "hours_meeting_target": 0,
            "hours_below_target": 0,
            "coverage_pct": None,
            "minimum_buys_in_full_hour": None,
            "current_partial_hour_buys": current_count,
            "last_completed_hour_buys": None,
            "force_filled_trades": 0,
            "gate_status": "NOT_EVALUATED",
            "recent_full_hours": [],
        }

    # İlk gözlemin kesirli saati değerlendirilmez. Gözlem daha önce başladıysa
    # son 24 saatteki sıfır-alış tam saatleri de FAIL sayılır.
    first = _ceil_hour(max(lookback_start, observed[0]["opened_at"]))
    last_exclusive = current_start
    buckets: list[datetime] = []
    cursor = first
    while cursor < last_exclusive:
        buckets.append(cursor)
        cursor += timedelta(hours=1)
    counts = {
        bucket: sum(bucket <= row["opened_at"] < bucket + timedelta(hours=1)
                    for row in recent)
        for bucket in buckets
    }
    meeting = sum(count >= MIN_QUALIFIED_BUYS_PER_FULL_HOUR
                  for count in counts.values())
    coverage = (Decimal(meeting) * Decimal("100") / Decimal(len(buckets))
                if buckets else None)
    recent_hours = [{
        "hour_utc": bucket.isoformat(),
        "accepted_qualified_buys": counts[bucket],
        "status": ("PASS" if counts[bucket] >=
                   MIN_QUALIFIED_BUYS_PER_FULL_HOUR else "FAIL"),
    } for bucket in buckets[-6:]]
    return {
        "required_per_full_hour": MIN_QUALIFIED_BUYS_PER_FULL_HOUR,
        "evaluated_full_hours": len(buckets),
        "hours_meeting_target": meeting,
        "hours_below_target": len(buckets) - meeting,
        "coverage_pct": _money(coverage),
        "minimum_buys_in_full_hour": min(counts.values()) if counts else None,
        "current_partial_hour_buys": current_count,
        "last_completed_hour_buys": counts[buckets[-1]] if buckets else None,
        "force_filled_trades": 0,
        "gate_status": ("PASS" if buckets and meeting == len(buckets)
                        else "FAIL" if buckets else "NOT_EVALUATED"),
        "recent_full_hours": recent_hours,
    }


def _performance(results: list[Decimal], ignored: int) -> dict[str, Any]:
    gains = sum((value for value in results if value > _ZERO), _ZERO)
    losses = -sum((value for value in results if value < _ZERO), _ZERO)
    net = sum(results, _ZERO)
    if losses > _ZERO:
        factor = gains / losses
        factor_state = "CALCULATED"
    elif gains > _ZERO:
        factor = None
        factor_state = "NO_LOSSES"
    else:
        factor = None
        factor_state = "INSUFFICIENT_DATA"
    return {
        "completed_trades": len(results),
        "minimum_completed_trades_required": MIN_COMPLETED_TRADES,
        "net_after_costs_usdt": _money(net) if results else None,
        "gross_gains_usdt": _money(gains) if results else None,
        "gross_losses_usdt": _money(losses) if results else None,
        "profit_factor": _money(factor),
        "profit_factor_state": factor_state,
        "ignored_closed_rows": ignored,
    }


def _promotion(hourly: dict[str, Any], performance: dict[str, Any],
               source_integrity: str, open_positions: int) -> dict[str, Any]:
    checks = {
        "source_integrity": source_integrity == "COMPLETE",
        "minimum_five_each_full_hour": hourly["gate_status"] == "PASS",
        "no_force_fill": hourly["force_filled_trades"] == 0,
        "minimum_completed_trades": (performance["completed_trades"] >=
                                     MIN_COMPLETED_TRADES),
        "positive_net_after_costs": (
            (_decimal(performance["net_after_costs_usdt"]) or _ZERO) > _ZERO),
        "profit_factor_at_least_1_20": (
            performance["profit_factor_state"] == "NO_LOSSES" and
            performance["completed_trades"] > 0) or (
            (_decimal(performance["profit_factor"]) or _ZERO) >=
            MIN_PROFIT_FACTOR),
        "position_limit_respected": open_positions <= MAX_OPEN_POSITIONS,
    }
    if source_integrity != "COMPLETE":
        status = "DATA_UNAVAILABLE"
    elif hourly["gate_status"] == "NOT_EVALUATED":
        status = "NOT_EVALUATED"
    elif performance["completed_trades"] < MIN_COMPLETED_TRADES:
        status = "INSUFFICIENT_DATA"
    else:
        status = "PASS" if all(checks.values()) else "FAIL"
    return {
        "status": status,
        "checks": checks,
        "live_promotion_allowed": False,
    }


def _learning_status(config: dict[str, Any],
                     completed_trades: int) -> dict[str, Any]:
    """Gerçek öğrenme işçisini ve kanıt engellerini açıkça raporla.

    ``learning_enabled`` tek başına zamanlayıcıyı çalıştırmaz: kanonik
    ``auto_controller`` önce ``adaptive_system.enabled`` alanını denetler.
    Ayrıca mevcut öğrenici yalnız izin-listeli sayısal parametrelerde
    challenger üretebilir; yeni rejim/rota kodunu kendi kendine yazamaz.
    """
    adaptive = config.get("adaptive_system")
    adaptive = adaptive if isinstance(adaptive, dict) else {}
    adaptive_enabled = adaptive.get("enabled") is True
    learning_enabled = adaptive.get("learning_enabled", True) is True
    scheduled = adaptive_enabled and learning_enabled
    blockers: list[str] = []
    if not adaptive_enabled:
        blockers.append("ADAPTIVE_SYSTEM_DISABLED")
    if not learning_enabled:
        blockers.append("LEARNING_DISABLED")
    if completed_trades < LEARNING_THRESHOLDS["diagnosis"]:
        blockers.append("FRESH_COHORT_BELOW_DIAGNOSIS_MINIMUM")
    if completed_trades < LEARNING_THRESHOLDS["challenger_proposal"]:
        blockers.append("FRESH_COHORT_BELOW_CHALLENGER_MINIMUM")
    if completed_trades < LEARNING_THRESHOLDS["promotion_review"]:
        blockers.append("FRESH_COHORT_BELOW_PROMOTION_MINIMUM")
    blockers.append("STRUCTURAL_STRATEGY_REVISION_REQUIRES_CODE_REVIEW")

    if not scheduled:
        status = "SCHEDULER_STOPPED"
    elif completed_trades < LEARNING_THRESHOLDS["diagnosis"]:
        status = "COLLECTING_EVIDENCE"
    elif completed_trades < LEARNING_THRESHOLDS["challenger_proposal"]:
        status = "DIAGNOSIS_ONLY"
    elif completed_trades < LEARNING_THRESHOLDS["promotion_review"]:
        status = "CHALLENGER_EVALUATION"
    else:
        status = "PROMOTION_REVIEW_ELIGIBLE"
    return {
        "status": status,
        "scheduled_worker_effective": scheduled,
        "adaptive_system_enabled": adaptive_enabled,
        "adaptive_mode": str(adaptive.get("mode") or "MONITOR"),
        "auto_paper_enabled": adaptive.get("auto_paper_enabled") is True,
        "learning_enabled": learning_enabled,
        "fresh_versioned_completed_trades": completed_trades,
        "thresholds": dict(LEARNING_THRESHOLDS),
        "learnable_parameter_scope": "BOUNDED_NUMERIC_OVERLAY_ONLY",
        "structural_strategy_revision_supported": False,
        "automatic_code_rewrite_allowed": False,
        "automatic_live_promotion_allowed": False,
        "final_strategy_apply": "OPERATOR_REVIEW_REQUIRED",
        "blockers": blockers,
    }


def snapshot(*, state_path: Path | None = None,
             dual_runtime_path: Path | None = None,
             config_path: Path | None = None,
             now: datetime | None = None) -> dict[str, Any]:
    """Kanonik Paper runtime kanıtını tek steril snapshot olarak döndür."""
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state, state_status = _read_object(state_path or STATE_PATH)
    dual, dual_status = _read_object(dual_runtime_path or DUAL_RUNTIME_PATH)
    config, config_status = _read_object(config_path or CONFIG_PATH)
    statuses = {
        "classic_state": state_status,
        "dual_model_runtime": dual_status,
    }
    source_integrity = ("UNAVAILABLE" if "UNREADABLE" in statuses.values()
                        else "COMPLETE")
    events, results, open_positions, ignored = _collect(state, dual)
    hourly = _hourly(events, observed_at)
    performance = _performance(results, ignored)
    promotion = _promotion(hourly, performance, source_integrity,
                           open_positions)
    learning = _learning_status(config, performance["completed_trades"])
    return {
        "ok": True,
        "strategy_candidate": "RECOVERY_FOCUSED",
        "strategy_version": REQUIRED_STRATEGY_VERSION,
        "runtime_implementation": "ADR019_CONTROLLED_PULLBACK_RECOVERY",
        "measurement_profile": "VERSIONED_ACTUAL_PAPER_OPENINGS",
        "included_profiles": sorted(QUALIFIED_PROFILES),
        "required_strategy_version": REQUIRED_STRATEGY_VERSION,
        "legacy_evidence_excluded": True,
        "window": f"LAST_{LOOKBACK_HOURS}_HOURS_FROM_FIRST_OBSERVED_EVENT",
        "performance_window": "ALL_RETAINED_QUALIFIED_PAPER_CLOSED_TRADES",
        "hour_timezone": "UTC",
        "source_status": statuses,
        "source_integrity": source_integrity,
        "accepted_paper_openings_in_window": sum(
            observed_at - timedelta(hours=LOOKBACK_HOURS) <= row["opened_at"]
            <= observed_at for row in events),
        "open_positions": open_positions,
        "maximum_open_positions": MAX_OPEN_POSITIONS,
        "hourly_frequency": hourly,
        "performance": performance,
        "promotion": promotion,
        "learning": learning,
        "learning_config_status": config_status,
        "live_orders": "DISABLED",
        "exchange_write_requests": 0,
        "generated_at": observed_at.isoformat(),
    }
