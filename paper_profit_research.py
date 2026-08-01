"""Windows uyumlu ADR-024 gerçek Binance Spot 4h kanıt koşucusu."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import requests

from paper_profit_strategy import Candle, dec, evaluate


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "paper_profit_4h"
REPORT_PATH = ROOT / "data" / "paper_profit_evidence.json"
BASE_URL = "https://api.binance.com"
KLINE_PATH = "/api/v3/klines"
INTERVAL = "4h"
INTERVAL_MS = 4 * 60 * 60 * 1000
DEFAULT_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "LTCUSDT", "TRXUSDT",
)


class ResearchError(RuntimeError):
    pass


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _session() -> requests.Session:
    if os.name == "nt":
        try:
            import truststore
            truststore.inject_into_ssl()
        except (ImportError, RuntimeError):
            pass
    session = requests.Session()
    session.trust_env = False
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "Alpha-Intelligence-OS-Paper-Profit/1.0",
    })
    return session


def fetch_klines(session: requests.Session, symbol: str, start_ms: int,
                 end_ms: int) -> list[list[Any]]:
    rows: dict[int, list[Any]] = {}
    cursor = start_ms
    while cursor < end_ms:
        response = session.get(
            BASE_URL + KLINE_PATH,
            params={"symbol": symbol, "interval": INTERVAL, "limit": 1000,
                    "startTime": cursor, "endTime": end_ms},
            timeout=(10, 30),
        )
        if response.status_code != 200:
            raise ResearchError(
                f"{symbol} market data HTTP_{response.status_code}")
        try:
            batch = response.json()
        except ValueError as exc:
            raise ResearchError(f"{symbol} market data JSON invalid") from exc
        if not isinstance(batch, list):
            raise ResearchError(f"{symbol} market data shape invalid")
        if not batch:
            break
        for row in batch:
            if not isinstance(row, list) or len(row) < 7:
                raise ResearchError(f"{symbol} kline row invalid")
            opened = int(row[0])
            if start_ms <= opened < end_ms:
                rows[opened] = row
        next_cursor = int(batch[-1][0]) + INTERVAL_MS
        if next_cursor <= cursor:
            raise ResearchError(f"{symbol} kline pagination stalled")
        cursor = next_cursor
        if len(batch) < 1000:
            break
        time.sleep(0.08)
    ordered = [rows[key] for key in sorted(rows)]
    if len(ordered) < 500:
        raise ResearchError(f"{symbol} has only {len(ordered)} usable candles")
    return ordered


def _cache_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol}_{INTERVAL}.json"


def _cache(symbol: str, rows: Sequence[Sequence[Any]], fetched_at: str,
           start_ms: int, end_ms: int) -> Path:
    path = _cache_path(symbol)
    _atomic_json(path, {
        "source": "BINANCE_SPOT_PUBLIC_GET",
        "base_url": BASE_URL,
        "endpoint": KLINE_PATH,
        "symbol": symbol,
        "interval": INTERVAL,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "fetched_at": fetched_at,
        "rows": list(rows),
    })
    return path


def _read_cache(symbol: str) -> tuple[list[list[Any]], str]:
    path = _cache_path(symbol)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchError(f"{symbol} cache unavailable") from exc
    if (not isinstance(value, dict) or value.get("source") !=
            "BINANCE_SPOT_PUBLIC_GET" or value.get("interval") != INTERVAL or
            not isinstance(value.get("rows"), list)):
        raise ResearchError(f"{symbol} cache invalid")
    return value["rows"], str(value.get("fetched_at") or "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(*, symbols: Sequence[str] = DEFAULT_SYMBOLS, years: int = 2,
        offline: bool = False, report_path: Path = REPORT_PATH) -> dict[str, Any]:
    if years < 2:
        raise ResearchError("at least two years of data are required")
    unique_symbols = tuple(dict.fromkeys(symbol.strip().upper()
                                         for symbol in symbols))
    if len(unique_symbols) < 5:
        raise ResearchError("at least five core Spot symbols are required")
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000) // INTERVAL_MS * INTERVAL_MS
    start_ms = int((now - timedelta(days=365 * years)).timestamp() * 1000)
    fetched_at = now.isoformat(timespec="seconds")
    session = None if offline else _session()
    data: dict[str, list[Candle]] = {}
    provenance: dict[str, Any] = {}
    try:
        for symbol in unique_symbols:
            if offline:
                rows, source_time = _read_cache(symbol)
                path = _cache_path(symbol)
            else:
                assert session is not None
                rows = fetch_klines(session, symbol, start_ms, end_ms)
                source_time = fetched_at
                path = _cache(symbol, rows, fetched_at, start_ms, end_ms)
            candles = [Candle.from_binance(row) for row in rows]
            for previous, current in zip(candles, candles[1:]):
                if current.open_time <= previous.open_time:
                    raise ResearchError(f"{symbol} candles are not chronological")
            data[symbol] = candles
            provenance[symbol] = {
                "candles": len(candles),
                "first_open_time": candles[0].open_time,
                "last_close_time": candles[-1].close_time,
                "fetched_at": source_time,
                "cache_sha256": _sha256(path),
            }
    finally:
        if session is not None:
            session.close()

    evidence = evaluate(data, cost_pct=dec("0.30"))
    evidence.update({
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market": "BINANCE_SPOT_USDT",
        "source": "BINANCE_SPOT_PUBLIC_GET",
        "symbols": list(unique_symbols),
        "years_requested": years,
        "chronological_split": {"train_pct": 60, "validation_pct": 20,
                                "holdout_pct": 20},
        "provenance": provenance,
    })
    _atomic_json(report_path, evidence)
    return evidence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ADR-024 gerçek 4h Spot kârlılık kanıtını çalıştırır.")
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run(symbols=args.symbols, years=args.years,
                     offline=args.offline)
    except (ResearchError, requests.RequestException, ValueError) as exc:
        print(f"SONUC: ERROR | {exc}")
        return 2
    print("SONUC: " + report["status"])
    print("VALIDATION PF: " + str(report["validation"]["profit_factor"]))
    print("HOLDOUT PF: " + str(report["holdout"]["profit_factor"]))
    print("RAPOR: " + str(REPORT_PATH))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
