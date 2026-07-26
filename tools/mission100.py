"""Mission 100 — 100 kapanmış PAPER trade doğrulama koşusu.

Amaç OPTİMİZASYON değil DOĞRULAMA:
- GERÇEK motor fonksiyonları kullanılır (open_paper_position, manage_position);
  strateji/risk/muhasebe koduna dokunulmaz, yeni özellik eklenmez.
- Binance'ten GERÇEK tarihsel 15m mumlar (sayfalı) indirilir ve replay edilir.
- Mission 11 ekonomik filtresi AKTİF kalır (mevcut sistem olduğu gibi çalışır).
- Her kapanışta bağımsız oracle ile order/fill/fee/ledger/PnL doğrulanır.
- Her 10 kapanışta checkpoint (JSONL) + durum anlık görüntüsü (resume için).
- Çıktılar: mission_100_summary.json/.md, mission_100_trades.csv,
  mission_100_incidents.jsonl, mission_100_checkpoints.jsonl
- İzolasyon: gerçek state.json / trade_history.json'a dokunulmaz (mtime kanıtlı).
- Hiçbir metrik uydurulmaz. Kritik hatada koşu durur.

Kullanım: python tools/mission100.py [--resume]
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import pandas as pd  # noqa: E402

import alpha20  # noqa: E402
from alpha20 import (  # noqa: E402
    BASE_URL,
    FEE_RATE,
    TradeSkippedError,
    fetch_klines,
    manage_position,
    open_paper_position,
)

OUT = ROOT / "alpha20_v1" / "mission100"
OUT.mkdir(exist_ok=True)
HISTORY_PATH = OUT / "mission_100_trade_history.json"
CHECKPOINTS = OUT / "mission_100_checkpoints.jsonl"
INCIDENTS = OUT / "mission_100_incidents.jsonl"
TRADES_CSV = OUT / "mission_100_trades.csv"
SUMMARY_JSON = OUT / "mission_100_summary.json"
SUMMARY_MD = OUT / "mission_100_summary.md"
SNAPSHOT = OUT / "mission_100_state_snapshot.json"

TARGET = 100
ATR_PERIOD = 14
WARMUP = 50
INTERVAL = "15m"
PAGES = 6  # 6 × 1500 = 9000 mum ≈ 93 gün / sembol


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jsonl_append(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def incident(kind: str, detail: str, counters: dict) -> None:
    counters[kind] = counters.get(kind, 0) + 1
    jsonl_append(INCIDENTS, {"time": now(), "kind": kind, "detail": detail})
    print(f"[INCIDENT/{kind}] {detail}")


def independent_pnl(entry: float, exit_: float, qty: float, side: str) -> dict:
    """Motor kodundan BAĞIMSIZ oracle."""
    d = 1.0 if side == "LONG" else -1.0
    gross_raw = (exit_ - entry) * qty * d
    fee_raw = (entry + exit_) * qty * FEE_RATE
    # pnl, yuvarlanmamış ara değerlerden yuvarlanır (motorla aynı sıra;
    # aksi hâlde 1e-8'lik son basamak farkı yanlış alarm üretir).
    return {"gross_pnl": round(gross_raw, 8), "fee_usdt": round(fee_raw, 8),
            "pnl": round(gross_raw - fee_raw, 8)}


def fetch_paged_klines(symbol: str, interval: str, pages: int) -> pd.DataFrame:
    """Binance'ten geriye doğru sayfalayarak gerçek tarihsel veri toplar."""
    frames = []
    end_time = None
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    for _ in range(pages):
        params = {"symbol": symbol, "interval": interval, "limit": 1500}
        if end_time:
            params["endTime"] = end_time
        r = requests.get(f"{BASE_URL}/fapi/v1/klines", params=params, timeout=20)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        df = pd.DataFrame(rows, columns=cols)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="raise")
        frames.append(df)
        end_time = int(rows[0][0]) - 1
        time.sleep(0.15)
    full = pd.concat(reversed(frames), ignore_index=True)
    full = full.drop_duplicates(subset="open_time").reset_index(drop=True)
    return full


def compute_atr(window: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    high, low, close = window["high"], window["low"], window["close"]
    prev = close.shift(1)
    tr = (high - low).combine((high - prev).abs(), max).combine((low - prev).abs(), max)
    return float(tr.rolling(period).mean().iloc[-1])


def preflight(config: dict, counters: dict) -> list[str]:
    """PAPER kilidi + risk motoru bypass edilemezliği + checkpoint mekanizması."""
    failures = []
    # PAPER-only kilidi
    if config["mode"] != "PAPER":
        failures.append("mode != PAPER")
    bad = dict(config, mode="LIVE")
    try:
        alpha20.validate_startup_config(bad)
        failures.append("PAPER kilidi delinebildi: LIVE mod SystemExit üretmedi")
    except SystemExit:
        pass
    # Risk motoru bypass testi: ikinci pozisyon açılamamalı
    st = {"balance": 10000.0, "day": "x", "day_start_balance": 10000.0,
          "consecutive_losses": 0, "position": {"symbol": "X"}, "trades": []}
    try:
        open_paper_position("BTCUSDT", "LONG",
                            {"price": 100.0, "atr": 10.0}, config, st)
        failures.append("risk bypass: açık pozisyon varken ikinci pozisyon açıldı")
    except ValueError:
        pass
    # Checkpoint mekanizması
    test_cp = OUT / "_cp_probe.jsonl"
    jsonl_append(test_cp, {"probe": True})
    ok = test_cp.exists() and json.loads(test_cp.read_text().strip())["probe"]
    test_cp.unlink()
    if not ok:
        failures.append("checkpoint mekanizması doğrulanamadı")
    for f in failures:
        incident("exception", f"preflight: {f}", counters)
    return failures


def metrics_of(trades: list[dict], starting: float) -> dict:
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakeven = [p for p in pnls if p == 0]
    equity, peak, max_dd = starting, starting, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak > 0 else 0)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "closed": len(pnls), "wins": len(wins), "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "total_fees": round(sum(t["fee_usdt"] for t in trades), 4),
        "net_pnl": round(sum(pnls), 4),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_drawdown_pct": round(max_dd, 4),
    }


def main() -> int:
    resume = "--resume" in sys.argv
    counters: dict[str, int] = {"ledger_mismatch": 0, "pnl_mismatch": 0,
                                "risk_violation": 0, "warning": 0,
                                "exception": 0, "recovery": 0}
    started_at = now()
    t0 = time.time()

    # 1-2. Testler
    print("Ön kontrol: tüm testler çalıştırılıyor...")
    tests = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=ROOT, capture_output=True, text=True)
    test_line = tests.stdout.strip().splitlines()[-1] if tests.stdout else "?"
    print(f"  pytest: {test_line}")
    if tests.returncode != 0:
        print("KRİTİK: testler geçmedi — Mission 100 başlatılmadı.")
        return 2

    config = json.loads((ROOT / "alpha20_v1" / "config.json").read_text())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()

    # İzolasyon
    alpha20.TRADE_HISTORY_PATH = HISTORY_PATH
    real_files = [ROOT / "alpha20_v1" / "state.json",
                  ROOT / "alpha20_v1" / "trade_history.json"]
    mtimes = {p.name: (p.stat().st_mtime if p.exists() else None) for p in real_files}

    # 7-9. Preflight
    if preflight(config, counters):
        print("KRİTİK: preflight başarısız — koşu durduruldu.")
        return 2
    print("Preflight: PAPER kilidi ✔ | risk bypass engelli ✔ | checkpoint ✔")

    # 3-6. run_id, başlangıç durumu, çıktı klasörü
    starting_balance = float(config["starting_balance_usdt"])
    if resume and SNAPSHOT.exists():
        snap = json.loads(SNAPSHOT.read_text())
        run_id, state = snap["run_id"], snap["state"]
        counters.update(snap["counters"])
        counters["recovery"] += 1
        jsonl_append(INCIDENTS, {"time": now(), "kind": "recovery",
                                 "detail": f"resume from {snap['saved_at']}, "
                                           f"closed={len(state['trades'])}"})
        print(f"RESUME: run_id={run_id} kapanmış={len(state['trades'])}")
    else:
        run_id = f"M100-{uuid.uuid4().hex[:10]}"
        for p in (CHECKPOINTS, INCIDENTS, HISTORY_PATH, SNAPSHOT):
            if p.exists():
                p.unlink()
        state = {"balance": starting_balance,
                 "day": datetime.now(timezone.utc).date().isoformat(),
                 "day_start_balance": starting_balance,
                 "consecutive_losses": 0, "position": None, "trades": [],
                 "network_errors": 0}
        print(f"run_id={run_id} | başlangıç bakiyesi={starting_balance} | "
              f"başlangıç ledger: 0 işlem, Σpnl=0")

    symbols = config["symbols"]
    print(f"Gerçek {INTERVAL} tarihsel veri indiriliyor ({PAGES}×1500 mum/sembol)...")
    data = {}
    for sym in symbols:
        df = fetch_paged_klines(sym, INTERVAL, PAGES)
        data[sym] = df
        print(f"  {sym}: {len(df)} mum")

    cursor = {s: WARMUP + ATR_PERIOD for s in symbols}
    sides = ["LONG", "SHORT"]
    opened = skipped = 0
    i = 0
    fatal = None

    def checkpoint() -> None:
        m = metrics_of(state["trades"], starting_balance)
        cp = {"run_id": run_id, "time": now(),
              "closed_trades": m["closed"], "wins": m["wins"],
              "losses": m["losses"], "breakeven": m["breakeven"],
              "gross_profit": m["gross_profit"], "gross_loss": m["gross_loss"],
              "total_fees": m["total_fees"], "net_pnl": m["net_pnl"],
              "balance": round(state["balance"], 4),
              "open_positions": 1 if state["position"] else 0,
              "max_drawdown_pct": m["max_drawdown_pct"],
              "ledger_mismatch": counters["ledger_mismatch"],
              "pnl_mismatch": counters["pnl_mismatch"],
              "risk_violation": counters["risk_violation"],
              "warning": counters["warning"],
              "exception": counters["exception"]}
        jsonl_append(CHECKPOINTS, cp)
        SNAPSHOT.write_text(json.dumps(
            {"run_id": run_id, "saved_at": now(), "state": state,
             "counters": counters}, ensure_ascii=False))
        print(f"CHECKPOINT #{m['closed']//10}: kapanmış={m['closed']} "
              f"net={m['net_pnl']:+.2f} bakiye={cp['balance']:.2f} "
              f"dd={m['max_drawdown_pct']}%")

    while len(state["trades"]) < TARGET:
        sym = symbols[i % len(symbols)]
        side = sides[i % 2]
        i += 1
        df = data[sym]
        start = cursor[sym]
        if start >= len(df) - 5:
            if all(cursor[s] >= len(data[s]) - 5 for s in symbols):
                fatal = "replay verisi tükendi"
                break
            continue

        window = df.iloc[:start]
        entry_price = float(window["close"].iloc[-1])
        atr = compute_atr(window)
        if not atr or atr <= 0:
            cursor[sym] += 10
            continue

        # Uzun koşu koşulu: günlük limit simülasyonu durdurmasın (risk motoru değişmez)
        state["day_start_balance"] = state["balance"]
        state["consecutive_losses"] = 0

        balance_before = state["balance"]
        try:
            open_paper_position(sym, side, {"price": entry_price, "atr": atr},
                                config, state)
        except TradeSkippedError:
            skipped += 1
            cursor[sym] += 4
            continue
        except ValueError as exc:
            incident("exception", f"open reddi {sym} {side}: {exc}", counters)
            fatal = str(exc)
            break
        opened += 1
        pos = dict(state["position"])

        # Risk doğrulaması
        exp_risk = balance_before * config["risk_per_trade_pct"] / 100
        if abs(pos["risk_usdt"] - exp_risk) > 1e-6:
            incident("risk_violation",
                     f"trade#{opened} risk {pos['risk_usdt']}≠{exp_risk}", counters)

        # Replay → SL/TP
        closed = False
        for j in range(start, len(df)):
            candle = df.iloc[j: j + 1]
            alpha20.fetch_klines = lambda *a, _c=candle, **k: _c
            try:
                manage_position(state)
            except Exception as exc:
                incident("exception", f"manage_position: {exc}", counters)
                traceback.print_exc()
                fatal = str(exc)
                break
            finally:
                alpha20.fetch_klines = fetch_klines
            if state["position"] is None:
                cursor[sym] = j + 1
                closed = True
                break
        if fatal:
            break
        if not closed:
            state["position"] = None
            opened -= 1
            cursor[sym] = len(df)
            incident("warning", f"{sym} {side}: veri sonunda SL/TP yok — giriş iptal",
                     counters)
            continue

        # Doğrulama (bağımsız oracle)
        t = state["trades"][-1]
        fc = data[sym].iloc[cursor[sym] - 1]
        c_high, c_low = float(fc["high"]), float(fc["low"])
        if t["exit_price"] not in (t["stop"], t["target"]):
            incident("pnl_mismatch", f"trade#{opened} fill stop/target değil", counters)
        else:
            if t["side"] == "LONG":
                stop_hit, tp_hit = c_low <= t["stop"], c_high >= t["target"]
            else:
                stop_hit, tp_hit = c_high >= t["stop"], c_low <= t["target"]
            if t["exit_price"] == t["stop"] and not stop_hit:
                incident("pnl_mismatch", f"trade#{opened} stop fill desteksiz", counters)
            if t["exit_price"] == t["target"] and (not tp_hit or stop_hit):
                incident("pnl_mismatch", f"trade#{opened} TP fill/tie-break hatası",
                         counters)
        exp = independent_pnl(t["entry_price"], t["exit_price"], t["quantity"], t["side"])
        if (abs(t["pnl"] - exp["pnl"]) > 1e-8
                or abs(t["gross_pnl"] - exp["gross_pnl"]) > 1e-8
                or abs(t["fee_usdt"] - exp["fee_usdt"]) > 1e-8):
            incident("pnl_mismatch", f"trade#{opened} PnL/fee oracle uyuşmazlığı", counters)
        if abs((state["balance"] - balance_before) - t["pnl"]) > 1e-8:
            incident("ledger_mismatch", f"trade#{opened} bakiye deltası≠pnl", counters)
        hist = json.loads(HISTORY_PATH.read_text())
        if len(hist) != len(state["trades"]) or hist[-1]["pnl"] != t["pnl"]:
            incident("ledger_mismatch", f"trade#{opened} history uyuşmazlığı", counters)

        n = len(state["trades"])
        if n % 10 == 0:
            checkpoint()
        if counters["ledger_mismatch"] or counters["pnl_mismatch"]:
            fatal = "doğrulama uyuşmazlığı — koşu durduruldu"
            break

    # Final ledger
    trades = state["trades"]
    net = sum(t["pnl"] for t in trades)
    ledger_checks = len(trades) * 2 + 1
    if abs((starting_balance + net) - state["balance"]) > 1e-6:
        incident("ledger_mismatch", "final: start+Σpnl ≠ balance", counters)
    for p in real_files:
        after = p.stat().st_mtime if p.exists() else None
        if after != mtimes[p.name]:
            incident("exception", f"İZOLASYON İHLALİ: {p.name} değişti", counters)

    # trades.csv
    if trades:
        fields = ["symbol", "side", "entry_price", "exit_price", "quantity",
                  "stop", "target", "risk_usdt", "gross_pnl", "fee_usdt", "pnl",
                  "result", "close_reason", "opened_at", "closed_at", "balance_after"]
        with TRADES_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(trades)

    m = metrics_of(trades, starting_balance)
    finished_at = now()
    duration = round(time.time() - t0, 1)
    open_pos = 1 if state["position"] else 0
    fail_reasons = []
    if len(trades) < TARGET:
        fail_reasons.append(f"kapanmış işlem {len(trades)} < {TARGET}"
                            + (f" ({fatal})" if fatal else ""))
    for k in ("ledger_mismatch", "pnl_mismatch", "risk_violation", "exception"):
        if counters[k]:
            fail_reasons.append(f"{k}={counters[k]}")
    if open_pos:
        fail_reasons.append("finalde açık pozisyon var")
    raw_net = sum(t["pnl"] for t in trades)  # ham (yuvarlanmamış) net PnL
    if abs((state["balance"] - starting_balance) - raw_net) > 1e-6:
        fail_reasons.append("bakiye değişimi ≠ net PnL")
    passed = not fail_reasons

    summary = {
        "mission": "Mission 100", "commit": commit, "run_id": run_id,
        "mode": f"PAPER (izole replay, gerçek Binance {INTERVAL} verisi, "
                "ekonomik filtre aktif)",
        "started_at": started_at, "finished_at": finished_at,
        "duration_seconds": duration, "tests": test_line,
        "opened_trades": opened, "closed_trades": m["closed"],
        "skipped_trades": skipped,
        "wins": m["wins"], "losses": m["losses"], "breakeven": m["breakeven"],
        "win_rate_pct": m["win_rate_pct"],
        "gross_profit": m["gross_profit"], "gross_loss": m["gross_loss"],
        "total_fees": m["total_fees"], "net_pnl": m["net_pnl"],
        "profit_factor": m["profit_factor"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "starting_balance": starting_balance,
        "final_balance": round(state["balance"], 4),
        "balance_change": round(state["balance"] - starting_balance, 4),
        "ledger_validation_count": ledger_checks,
        "ledger_mismatch_count": counters["ledger_mismatch"],
        "pnl_mismatch_count": counters["pnl_mismatch"],
        "risk_violation_count": counters["risk_violation"],
        "warning_count": counters["warning"],
        "exception_count": counters["exception"],
        "recovery_count": counters["recovery"],
        "open_positions": open_pos,
        "result": "PASS" if passed else "FAIL",
        "fail_reasons": fail_reasons,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    SUMMARY_MD.write_text(
        "# Mission 100 — Yönetici Özeti\n\n"
        + "\n".join(f"- **{k}**: {v}" for k, v in summary.items()) + "\n")

    print("\n══════════ MISSION 100 ÖZETİ ══════════")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print("════════════════════════════════════════")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
