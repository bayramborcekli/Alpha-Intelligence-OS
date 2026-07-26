from __future__ import annotations

import argparse
import json
import logging
import math
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

BASE_URL = "https://fapi.binance.com"
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
LOG_PATH = ROOT / "alpha20.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("alpha20")


@dataclass
class Position:
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    quantity: float
    risk_usdt: float
    opened_at: str


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp.replace(path)


def initial_state(config: dict[str, Any]) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "balance": float(config["starting_balance_usdt"]),
        "day": today,
        "day_start_balance": float(config["starting_balance_usdt"]),
        "consecutive_losses": 0,
        "position": None,
        "trades": [],
    }


def reset_day_if_needed(state: dict[str, Any]) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if state["day"] != today:
        state["day"] = today
        state["day_start_balance"] = state["balance"]


def fetch_klines(symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
    response = requests.get(
        f"{BASE_URL}/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=15,
    )
    response.raise_for_status()
    rows = response.json()
    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base",
        "taker_quote", "ignore",
    ]
    df = pd.DataFrame(rows, columns=columns)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="raise")
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]

    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    out["ema200"] = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    out["rsi14"] = 100 - (100 / (1 + rs))

    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = true_range.ewm(alpha=1/14, adjust=False).mean()
    out["volume_ma20"] = out["volume"].rolling(20).mean()
    return out


def score_setup(fast: pd.DataFrame, trend: pd.DataFrame) -> tuple[str | None, int, dict[str, Any]]:
    f = fast.iloc[-2]   # Yalnızca kapanmış mum
    t = trend.iloc[-2]

    long_score = 0
    short_score = 0

    # 1 saatlik ana trend: 30 puan
    if t["ema50"] > t["ema200"] and t["close"] > t["ema50"]:
        long_score += 30
    if t["ema50"] < t["ema200"] and t["close"] < t["ema50"]:
        short_score += 30

    # 15 dakikalık kısa trend: 20 puan
    if f["ema20"] > f["ema50"] and f["close"] > f["ema20"]:
        long_score += 20
    if f["ema20"] < f["ema50"] and f["close"] < f["ema20"]:
        short_score += 20

    # RSI: 20 puan
    if 52 <= f["rsi14"] <= 68:
        long_score += 20
    if 32 <= f["rsi14"] <= 48:
        short_score += 20

    # Hacim: 15 puan
    volume_ratio = f["volume"] / f["volume_ma20"] if f["volume_ma20"] else 0
    if volume_ratio >= 1.10:
        long_score += 15
        short_score += 15

    # Son mum yönü: 15 puan
    if f["close"] > f["open"]:
        long_score += 15
    if f["close"] < f["open"]:
        short_score += 15

    details = {
        "price": float(f["close"]),
        "atr": float(f["atr14"]),
        "rsi": round(float(f["rsi14"]), 2),
        "volume_ratio": round(float(volume_ratio), 2),
        "long_score": long_score,
        "short_score": short_score,
    }

    if long_score > short_score:
        return "LONG", long_score, details
    if short_score > long_score:
        return "SHORT", short_score, details
    return None, max(long_score, short_score), details


def can_open(config: dict[str, Any], state: dict[str, Any]) -> tuple[bool, str]:
    if state["position"] is not None:
        return False, "Açık pozisyon var."
    if state["consecutive_losses"] >= config["max_consecutive_losses"]:
        return False, "Arka arkaya zarar limiti doldu."
    daily_loss = state["day_start_balance"] - state["balance"]
    daily_limit = state["day_start_balance"] * config["daily_loss_limit_pct"] / 100
    if daily_loss >= daily_limit:
        return False, "Günlük zarar limiti doldu."
    return True, "Uygun."


def open_paper_position(
    symbol: str,
    side: str,
    details: dict[str, Any],
    config: dict[str, Any],
    state: dict[str, Any],
) -> None:
    entry = details["price"]
    atr = details["atr"]
    stop_distance = atr * config["atr_stop_multiplier"]
    if stop_distance <= 0:
        raise ValueError("ATR stop mesafesi geçersiz.")

    risk_usdt = state["balance"] * config["risk_per_trade_pct"] / 100
    quantity = risk_usdt / stop_distance

    if side == "LONG":
        stop = entry - stop_distance
        target = entry + stop_distance * config["reward_risk_ratio"]
    else:
        stop = entry + stop_distance
        target = entry - stop_distance * config["reward_risk_ratio"]

    position = Position(
        symbol=symbol,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        quantity=quantity,
        risk_usdt=risk_usdt,
        opened_at=datetime.now(timezone.utc).isoformat(),
    )
    state["position"] = asdict(position)
    log.info(
        "PAPER AÇILDI | %s %s | giriş=%.4f stop=%.4f hedef=%.4f risk=%.2f USDT",
        symbol, side, entry, stop, target, risk_usdt,
    )


def manage_position(state: dict[str, Any]) -> None:
    raw = state.get("position")
    if not raw:
        return

    pos = Position(**raw)
    df = fetch_klines(pos.symbol, "1m", 5)
    last = df.iloc[-1]
    high, low = float(last["high"]), float(last["low"])

    exit_price = None
    result = None

    # Aynı mum içinde hem stop hem hedef görülürse temkinli olarak stop varsayılır.
    if pos.side == "LONG":
        if low <= pos.stop:
            exit_price, result = pos.stop, "LOSS"
        elif high >= pos.target:
            exit_price, result = pos.target, "WIN"
    else:
        if high >= pos.stop:
            exit_price, result = pos.stop, "LOSS"
        elif low <= pos.target:
            exit_price, result = pos.target, "WIN"

    if exit_price is None:
        return

    direction = 1 if pos.side == "LONG" else -1
    pnl = (exit_price - pos.entry) * pos.quantity * direction
    state["balance"] += pnl
    state["consecutive_losses"] = state["consecutive_losses"] + 1 if pnl < 0 else 0
    state["trades"].append({
        **raw,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "exit": exit_price,
        "pnl": round(pnl, 8),
        "result": result,
        "balance_after": round(state["balance"], 8),
    })
    state["position"] = None
    log.info(
        "PAPER KAPANDI | %s %s | sonuç=%s pnl=%.2f bakiye=%.2f",
        pos.symbol, pos.side, result, pnl, state["balance"],
    )


def run_cycle(config: dict[str, Any], state: dict[str, Any]) -> None:
    reset_day_if_needed(state)
    manage_position(state)

    allowed, reason = can_open(config, state)
    if not allowed:
        log.info("Yeni işlem yok: %s", reason)
        save_json(STATE_PATH, state)
        return

    candidates = []
    for symbol in config["symbols"]:
        try:
            fast = add_indicators(fetch_klines(symbol, config["interval"]))
            trend = add_indicators(fetch_klines(symbol, config["trend_interval"]))
            side, score, details = score_setup(fast, trend)
            log.info(
                "%s | yön=%s skor=%s RSI=%s hacim=%.2f",
                symbol, side, score, details["rsi"], details["volume_ratio"],
            )
            if side and score >= config["minimum_score"]:
                candidates.append((score, symbol, side, details))
        except Exception as exc:
            log.exception("%s taranamadı: %s", symbol, exc)

    if candidates:
        score, symbol, side, details = max(candidates, key=lambda x: x[0])
        open_paper_position(symbol, side, details, config, state)
    else:
        log.info("Eşik üzerinde fırsat bulunmadı.")

    save_json(STATE_PATH, state)


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha-20 v1 PAPER trading bot")
    parser.add_argument("--once", action="store_true", help="Bir kez tara ve çık.")
    parser.add_argument("--reset", action="store_true", help="Sanal hesabı sıfırla.")
    args = parser.parse_args()

    config = load_json(CONFIG_PATH, {})
    if config.get("mode") != "PAPER":
        raise RuntimeError("v1 yalnızca PAPER modunda çalışır.")

    if args.reset or not STATE_PATH.exists():
        state = initial_state(config)
        save_json(STATE_PATH, state)
    else:
        state = load_json(STATE_PATH, initial_state(config))

    log.info("Alpha-20 v1 başladı | MOD=PAPER | bakiye=%.2f", state["balance"])

    if args.once:
        run_cycle(config, state)
        return

    while True:
        try:
            run_cycle(config, state)
        except KeyboardInterrupt:
            log.info("Kullanıcı tarafından durduruldu.")
            break
        except Exception as exc:
            log.exception("Döngü hatası: %s", exc)
        time.sleep(int(config["scan_seconds"]))


if __name__ == "__main__":
    main()
