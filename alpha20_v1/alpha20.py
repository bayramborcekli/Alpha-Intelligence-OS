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
TRADE_HISTORY_PATH = ROOT / "trade_history.json"
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


MAX_SYMBOLS = 3
MIN_RISK_PCT = 0.25
MAX_RISK_PCT = 0.50
FEE_RATE = 0.001  # taraf başına %0.1 tahmini ücret


def validate_startup_config(config: dict[str, Any]) -> None:
    """Başlangıç kuralları — herhangi biri ihlal edilirse SystemExit."""
    errors: list[str] = []
    if config.get("mode") != "PAPER":
        errors.append("mode PAPER olmalı — gerçek işlem desteklenmiyor.")
    symbols = config.get("symbols") or []
    if not symbols:
        errors.append("En az 1 sembol gerekli.")
    if len(symbols) > MAX_SYMBOLS:
        errors.append(f"En fazla {MAX_SYMBOLS} sembol kullanılabilir (şu an {len(symbols)}).")
    risk = float(config.get("risk_per_trade_pct", 0))
    if not (MIN_RISK_PCT <= risk <= MAX_RISK_PCT):
        errors.append(
            f"risk_per_trade_pct {MIN_RISK_PCT}–{MAX_RISK_PCT} aralığında olmalı (şu an {risk})."
        )
    if int(config.get("max_open_positions", 0)) != 1:
        errors.append("max_open_positions 1 olmalı.")
    if int(config.get("leverage", 1)) != 1:
        errors.append("Kaldıraç desteklenmiyor — leverage 1 olmalı.")
    if errors:
        for err in errors:
            log.error("CONFIG HATASI: %s", err)
        raise SystemExit("Başlangıç doğrulaması başarısız: " + " | ".join(errors))


def print_startup_report(config: dict[str, Any], state: dict[str, Any]) -> None:
    print("Paper Trading Start Ready")
    print(f"Mode: {config['mode']}")
    print(f"Symbols: {', '.join(config['symbols'])}")
    print(f"Starting Virtual Balance: {config['starting_balance_usdt']:.2f} USDT")
    print(f"Current Balance: {state['balance']:.2f} USDT")
    print(f"Risk Per Trade: {config['risk_per_trade_pct']}%")
    print(f"Max Open Positions: {config['max_open_positions']}")
    print(f"Leverage: 1x")
    print(f"Stop Loss: ATR x {config['atr_stop_multiplier']} (zorunlu)")
    print(f"Take Profit: stop mesafesi x {config['reward_risk_ratio']} (zorunlu)")


def compute_realized_pnl(
    entry_price: float,
    exit_price: float,
    quantity: float,
    side: str,
    fee_rate: float = FEE_RATE,
) -> dict[str, float]:
    """Tek gerçek kaynak: realized PnL hesabı (BUG-001).

    Console, State, Trade History ve tüm raporlar bu fonksiyonun çıktısını kullanır.
    Dönüş: {"gross_pnl", "fee_usdt", "pnl"} — pnl = gross_pnl - fee_usdt.
    """
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"Geçersiz yön: {side}")
    if entry_price <= 0 or exit_price <= 0 or quantity <= 0:
        raise ValueError("entry_price, exit_price ve quantity pozitif olmalı.")
    direction = 1 if side == "LONG" else -1
    gross_pnl = (exit_price - entry_price) * quantity * direction
    fee_usdt = (entry_price + exit_price) * quantity * fee_rate  # giriş + çıkış
    return {
        "gross_pnl": round(gross_pnl, 8),
        "fee_usdt": round(fee_usdt, 8),
        "pnl": round(gross_pnl - fee_usdt, 8),
    }


def append_trade_history(trade: dict[str, Any], path: Path | None = None) -> None:
    """Kapanan her işlemi trade_history.json dosyasına otomatik ekler (atomik)."""
    if path is None:
        path = TRADE_HISTORY_PATH  # çağrı anında çözülür (test izolasyonu için)
    history = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        except (json.JSONDecodeError, OSError):
            log.warning("trade_history.json okunamadı — yeni liste başlatılıyor.")
            history = []
    history.append(trade)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    temp.replace(path)


def print_performance_summary(state: dict[str, Any]) -> None:
    """Terminal performans özeti."""
    trades = state.get("trades", [])
    print("\n══════ PAPER PERFORMANS ÖZETİ ══════")
    print(f"Bakiye: {state['balance']:.2f} USDT")
    if not trades:
        print("Kapanan işlem yok.")
        print("════════════════════════════════════")
        return
    pnls = [float(t.get("pnl", 0) or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    fees = sum(float(t.get("fee_usdt", 0) or 0) for t in trades)
    best = max(trades, key=lambda t: float(t.get("pnl", 0) or 0))
    worst = min(trades, key=lambda t: float(t.get("pnl", 0) or 0))
    print(f"Toplam işlem: {len(trades)} | Kazanç: {len(wins)} | Zarar: {len(losses)}")
    print(f"Kazanma oranı: {len(wins) / len(trades) * 100:.1f}%")
    print(f"Net PnL: {sum(pnls):+.2f} USDT | Toplam fee: {fees:.2f} USDT")
    if wins:
        print(f"Ortalama kazanç: {sum(wins)/len(wins):+.2f} USDT")
    if losses:
        print(f"Ortalama zarar: {sum(losses)/len(losses):+.2f} USDT")
    print(f"En iyi: {best.get('symbol')} {float(best.get('pnl', 0)):+.2f} | "
          f"En kötü: {worst.get('symbol')} {float(worst.get('pnl', 0)):+.2f}")
    print("════════════════════════════════════")


def reset_day_if_needed(state: dict[str, Any]) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if state["day"] != today:
        state["day"] = today
        state["day_start_balance"] = state["balance"]


def fetch_klines_safe(
    symbol: str, interval: str, limit: int = 300, state: dict[str, Any] | None = None
) -> pd.DataFrame | None:
    """Ağ/veri hatasında None döndürür — yeni pozisyon açılmasını engeller."""
    try:
        df = fetch_klines(symbol, interval, limit)
        if df is None or df.empty:
            raise ValueError("Boş kline verisi.")
        return df
    except Exception as exc:
        if state is not None:
            state["network_errors"] = int(state.get("network_errors", 0)) + 1
        log.warning("VERİ HATASI | %s %s | %s — yeni işlem açılmayacak.", symbol, interval, exc)
        return None


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


def can_open(
    config: dict[str, Any], state: dict[str, Any], symbol: str | None = None
) -> tuple[bool, str]:
    position = state.get("position")
    if position is not None:
        if symbol is not None and position.get("symbol") == symbol:
            return False, f"{symbol} için zaten açık pozisyon var (mükerrer engellendi)."
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
    if state.get("position") is not None:
        raise ValueError("Zaten açık pozisyon var — ikinci pozisyon açılamaz.")
    entry = details["price"]
    atr = details["atr"]
    if entry is None or entry <= 0:
        raise ValueError(f"Geçersiz giriş fiyatı: {entry}")
    if atr is None or atr <= 0:
        raise ValueError(f"Geçersiz ATR: {atr}")
    stop_distance = atr * config["atr_stop_multiplier"]
    if stop_distance <= 0:
        raise ValueError("ATR stop mesafesi geçersiz.")

    risk_usdt = state["balance"] * config["risk_per_trade_pct"] / 100
    if risk_usdt <= 0 or state["balance"] <= 0:
        raise ValueError(f"Yetersiz sanal bakiye: {state['balance']:.2f} USDT")
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
    df = fetch_klines_safe(pos.symbol, "1m", 5, state=state)
    if df is None:
        # Veri yok — pozisyon güvenle korunur, kapatma kararı verilmez.
        return
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

    # BUG-001: PnL yalnızca compute_realized_pnl ile hesaplanır (tek kaynak).
    pnl_data = compute_realized_pnl(pos.entry, exit_price, pos.quantity, pos.side)
    pnl = pnl_data["pnl"]
    close_reason = "STOP_LOSS" if result == "LOSS" else "TAKE_PROFIT"
    state["balance"] += pnl
    state["consecutive_losses"] = state["consecutive_losses"] + 1 if pnl < 0 else 0
    trade_record = {
        **raw,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "entry_price": pos.entry,
        "exit_price": exit_price,
        "exit": exit_price,
        "fee_usdt": pnl_data["fee_usdt"],
        "gross_pnl": pnl_data["gross_pnl"],
        "pnl": pnl,
        "result": result,
        "close_reason": close_reason,
        "balance_after": round(state["balance"], 8),
    }
    state["trades"].append(trade_record)
    state["position"] = None  # Muhasebe önce kapanır — history yazımı best-effort.
    try:
        append_trade_history(trade_record)
    except OSError as exc:
        log.warning("trade_history.json yazılamadı (%s) — muhasebe etkilenmedi.", exc)
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
    data_error = False
    for symbol in config["symbols"]:
        sym_ok, sym_reason = can_open(config, state, symbol=symbol)
        if not sym_ok:
            log.info("%s atlandı: %s", symbol, sym_reason)
            continue
        try:
            fast_raw = fetch_klines_safe(symbol, config["interval"], state=state)
            trend_raw = fetch_klines_safe(symbol, config["trend_interval"], state=state)
            if fast_raw is None or trend_raw is None:
                data_error = True  # Herhangi bir veri hatası → bu döngüde hiç işlem açma.
                continue
            fast = add_indicators(fast_raw)
            trend = add_indicators(trend_raw)
            side, score, details = score_setup(fast, trend)
            log.info(
                "%s | yön=%s skor=%s RSI=%s hacim=%.2f",
                symbol, side, score, details["rsi"], details["volume_ratio"],
            )
            if side and score >= config["minimum_score"]:
                candidates.append((score, symbol, side, details))
        except Exception as exc:
            log.exception("%s taranamadı: %s", symbol, exc)

    if data_error:
        log.warning("Ağ/veri hatası nedeniyle bu döngüde yeni pozisyon açılmayacak.")
    elif candidates:
        score, symbol, side, details = max(candidates, key=lambda x: x[0])
        open_paper_position(symbol, side, details, config, state)
    else:
        log.info("Eşik üzerinde fırsat bulunmadı.")

    save_json(STATE_PATH, state)


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha-20 v1 PAPER trading bot")
    parser.add_argument("--once", action="store_true", help="Bir kez tara ve çık.")
    parser.add_argument("--reset", action="store_true", help="Sanal hesabı sıfırla.")
    parser.add_argument("--dry-run", action="store_true", help="Doğrula, raporla ve çık — işlem yok.")
    parser.add_argument("--report", action="store_true", help="Başlangıç raporunu yazdır.")
    parser.add_argument("--summary", action="store_true", help="Performans özetini yazdır ve çık.")
    args = parser.parse_args()

    config = load_json(CONFIG_PATH, {})
    validate_startup_config(config)

    if args.reset or not STATE_PATH.exists():
        state = initial_state(config)
        save_json(STATE_PATH, state)
    else:
        state = load_json(STATE_PATH, initial_state(config))

    log.info("Alpha-20 v1 başladı | MOD=PAPER | bakiye=%.2f", state["balance"])
    log.info("PAPER modu doğrulandı — gerçek emir gönderimi yok, API anahtarı yok.")

    if args.summary:
        print_performance_summary(state)
        return
    if args.report or args.dry_run:
        print_startup_report(config, state)
    if args.dry_run:
        log.info("Dry-run tamamlandı — döngü başlatılmadı.")
        return

    time.sleep(2)  # Başlangıç onay gecikmesi

    if args.once:
        run_cycle(config, state)
        print_performance_summary(state)
        return

    while True:
        try:
            run_cycle(config, state)
        except KeyboardInterrupt:
            log.info("Kullanıcı tarafından durduruldu.")
            print_performance_summary(state)
            break
        except Exception as exc:
            log.exception("Döngü hatası: %s", exc)
        time.sleep(int(config["scan_seconds"]))


if __name__ == "__main__":
    main()
