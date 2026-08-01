# -*- coding: utf-8 -*-
r"""ADR-017 canlı Binance Top Gainers salt-okunur Windows doğrulayıcısı.

Çalıştığı anda Binance Spot exchangeInfo + 24h ticker verisini okur. Sabit
watchlist kullanmaz: TRADING durumundaki bütün Spot-USDT altcoinleri içinden
stablecoin ve kaldıraçlı token tabanlarını çıkarır, güncel pozitif 24h değişime
göre sıralar ve ilk 20'yi ADR-016 karar zincirinden geçirir.

Bu araç emir, transfer veya hesap uç noktası çağırmaz. Sonuçlar geçmiş 5m
mumlardan kalibre edilmiş Paper kararlarıdır; gelecek kâr garantisi değildir.

Windows kullanımı (depo kökünden):

    .venv\Scripts\python.exe tools\windows\verify_adr017_top_gainers.py

Kanıt: verify_adr017_top_gainers_report.json ve .csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
ALPHA = REPO / "alpha20_v1"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(ALPHA))

import dual_model as dm  # noqa: E402


JSON_REPORT = REPO / "verify_adr017_top_gainers_report.json"
CSV_REPORT = REPO / "verify_adr017_top_gainers_report.csv"
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
STABLE_BASES = frozenset({
    "AEUR", "AUD", "BIDR", "BRL", "BUSD", "DAI", "EURI", "EUR",
    "FDUSD", "GBP", "IDRT", "NGN", "PAX", "PYUSD", "RUB", "TUSD",
    "TRY", "UAH", "USDC", "USDE", "USD1", "USDP", "XUSD",
})


def _is_altcoin(symbol: dict[str, Any]) -> bool:
    """Bütün Spot-USDT altcoin evreninin açıklanabilir filtresi."""
    base = str(symbol.get("baseAsset") or "").upper()
    quote = str(symbol.get("quoteAsset") or "").upper()
    if (symbol.get("status") != "TRADING" or quote != "USDT"
            or not base or base == "BTC" or base in STABLE_BASES):
        return False
    if symbol.get("isSpotTradingAllowed") is False:
        return False
    return not any(base.endswith(suffix) for suffix in LEVERAGED_SUFFIXES)


def tradable_altcoin_symbols(exchange_info: dict[str, Any]) -> set[str]:
    rows = exchange_info.get("symbols") if isinstance(exchange_info, dict) \
        else []
    return {
        str(row.get("symbol")) for row in rows or []
        if isinstance(row, dict) and _is_altcoin(row)
    }


def select_live_top_gainers(exchange_info: dict[str, Any],
                            tickers: list[dict[str, Any]],
                            top: int = 20) -> tuple[int, list[dict]]:
    """Canlı evreni pozitif 24h fiyat değişimine göre deterministik sırala."""
    universe = tradable_altcoin_symbols(exchange_info)
    rows: list[dict] = []
    for ticker in tickers:
        symbol = str(ticker.get("symbol") or "")
        if symbol not in universe:
            continue
        fields = dm._ticker_fields(ticker)
        if fields is None or fields["change_pct"] <= 0:
            continue
        rows.append({"symbol": symbol, **fields})
    rows.sort(key=lambda row: (-row["change_pct"], -row["volume_usdt"],
                               row["symbol"]))
    limit = max(1, min(50, int(top)))
    selected = rows[:limit]
    for rank, row in enumerate(selected, start=1):
        row["live_rank"] = rank
    return len(universe), selected


def _main_config() -> dict[str, Any]:
    with (ALPHA / "config.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def evaluate_live_top_gainers(top: int = 20,
                              max_minutes: float = 10.0) -> dict[str, Any]:
    """Salt-okunur canlı sıralama + ADR-016 değerlendirmesi."""
    deadline = time.monotonic() + max(1.0, float(max_minutes)) * 60.0
    cfg = dm.get_config(_main_config())
    engine_cfg = cfg.get("decision_engine") or {}
    exchange_info = dm._guarded_get("/api/v3/exchangeInfo", timeout=20)
    tickers = dm.fetch_spot_tickers()
    universe_size, gainers = select_live_top_gainers(
        exchange_info, tickers, top=top)

    evaluated = []
    timed_out = False
    for row in gainers:
        if time.monotonic() >= deadline:
            timed_out = True
            break
        symbol = row["symbol"]
        klines = dm.fetch_adr016_klines(symbol, engine_cfg)
        decision, _signal, eligible, reason, _net = \
            dm.evaluate_adr016_candidate(
                row, symbol, klines, dm.MODEL_CORE, cfg)
        probabilities = decision.get("probabilities") or {}
        evaluated.append({
            "live_rank": row["live_rank"],
            "symbol": symbol,
            "price_change_24h_pct": round(row["change_pct"], 4),
            "quote_volume_24h_usdt": round(row["volume_usdt"], 2),
            "spread_pct": round(row["spread_pct"], 4),
            "decision": "PAPER_ELIGIBLE" if eligible else "NO_TRADE",
            "reason_code": reason,
            "regime": decision.get("regime"),
            "strategy": decision.get("strategy"),
            "calibration_samples": decision.get("sample_size", 0),
            "historical_tp_probability": probabilities.get("tp"),
            "historical_net_ev_pct": decision.get("net_ev_pct"),
            "historical_net_ev_lower_bound_pct":
                decision.get("net_ev_lower_bound_pct"),
        })

    eligible = [row for row in evaluated
                if row["decision"] == "PAPER_ELIGIBLE"]
    eligible.sort(key=lambda row: (
        -float(row["historical_net_ev_lower_bound_pct"] or -999999),
        row["live_rank"]))
    return {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "test_type": "BINANCE_LIVE_TOP_GAINERS_READ_ONLY",
        "source": "Binance Spot public exchangeInfo + 24h ticker + 5m klines",
        "universe": "ALL_TRADING_SPOT_USDT_ALTCOINS",
        "universe_size": universe_size,
        "requested_top": max(1, min(50, int(top))),
        "positive_gainers_returned": len(gainers),
        "evaluated_before_deadline": len(evaluated),
        "max_runtime_minutes": max(1.0, float(max_minutes)),
        "timed_out": timed_out,
        "stable_and_leveraged_excluded": True,
        "btc_excluded": True,
        "live_orders": "DISABLED",
        "exchange_write_requests": 0,
        "eligible_count": len(eligible),
        "best_eligible_list": [row["symbol"] for row in eligible],
        "top_gainers": evaluated,
        "warning": (
            "24h yükseliş kâr garantisi değildir; kârlılık alanları geçmiş "
            "5m kalibrasyonudur ve geleceği garanti etmez."
        ),
    }


def write_reports(report: dict[str, Any]) -> None:
    JSON_REPORT.write_text(json.dumps(
        report, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "live_rank", "symbol", "price_change_24h_pct",
        "quote_volume_24h_usdt", "spread_pct", "decision",
        "reason_code", "regime", "strategy", "calibration_samples",
        "historical_tp_probability", "historical_net_ev_pct",
        "historical_net_ev_lower_bound_pct",
    ]
    with CSV_REPORT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["top_gainers"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ADR-017 canlı Binance Top Gainers doğrulayıcısı")
    parser.add_argument("--top", type=int, default=20,
                        help="Canlı listeden değerlendirilecek ilk N (1-50)")
    parser.add_argument("--max-minutes", type=float, default=10.0,
                        help="Değerlendirme süresi üst sınırı (varsayılan 10)")
    args = parser.parse_args()
    try:
        report = evaluate_live_top_gainers(args.top, args.max_minutes)
        write_reports(report)
    except Exception as exc:
        print(f"[FAIL] Canlı salt-okunur doğrulama tamamlanamadı: {exc}")
        return 1

    print("\nADR-017 CANLI BINANCE TOP GAINERS (SALT-OKUNUR)")
    print("#  SYMBOL         24H %   KARAR           REJIM       NET-EV ALT")
    for row in report["top_gainers"]:
        lower = row["historical_net_ev_lower_bound_pct"] or "-"
        print(f"{row['live_rank']:>2} {row['symbol']:<13} "
              f"{row['price_change_24h_pct']:>7.2f}  "
              f"{row['decision']:<14} "
              f"{str(row['regime'] or '-'):11} {lower}")
    print(f"\nEvren: {report['universe_size']} altcoin | "
          f"Uygun: {report['eligible_count']} | Canlı emir: DISABLED")
    print(f"Rapor: {JSON_REPORT.name} / {CSV_REPORT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
