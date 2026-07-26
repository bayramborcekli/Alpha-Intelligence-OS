"""Mission 1000 — 1000 kapanmış PAPER trade doğrulama + gözlem koşusu.

Amaç OPTİMİZASYON değil DOĞRULAMA ve GÖZLEM:
- GERÇEK motor fonksiyonları kullanılır (open_paper_position, manage_position);
  strateji/risk/muhasebe koduna dokunulmaz, yeni özellik/gösterge/AI eklenmez.
- Binance'ten GERÇEK tarihsel 15m mumlar (sayfalı) indirilir ve replay edilir.
- Ekonomik filtre AKTİF kalır; reddettikleri ayrıca sayılır.
- Her kapanışta bağımsız oracle ile order/fill/fee/ledger/PnL doğrulanır.
- Her 50 kapanışta checkpoint (JSONL) + durum anlık görüntüsü (resume için).
- Gözlem metrikleri (ATR, süre, notional, fee oranı, sembol/saat/gün dağılımı,
  rejim, sinyal skoru) MOTORUN MEVCUT göstergeleriyle ölçülür — yeni sinyal
  mantığı OLUŞTURMAZ, sadece raporlanır.
- Çıktılar: mission_1000_summary.json/.md, mission_1000_trades.csv,
  mission_1000_checkpoints.jsonl, mission_1000_incidents.jsonl,
  mission_1000_metrics.csv
- İzolasyon: gerçek state.json / trade_history.json'a dokunulmaz (mtime kanıtlı).
- Hiçbir metrik uydurulmaz. Kritik hatada koşu durur.

Kullanım: python tools/mission1000.py [--resume]
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
import traceback
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import pandas as pd  # noqa: E402

import alpha20  # noqa: E402
from alpha20 import (  # noqa: E402
    BASE_URL,
    TradeSkippedError,
    add_indicators,
    fetch_klines,
    manage_position,
    open_paper_position,
    score_setup,
)

OUT = ROOT / "alpha20_v1" / "mission1000"
OUT.mkdir(exist_ok=True)
HISTORY_PATH = OUT / "mission_1000_trade_history.json"
CHECKPOINTS = OUT / "mission_1000_checkpoints.jsonl"
INCIDENTS = OUT / "mission_1000_incidents.jsonl"
TRADES_CSV = OUT / "mission_1000_trades.csv"
METRICS_CSV = OUT / "mission_1000_metrics.csv"
SUMMARY_JSON = OUT / "mission_1000_summary.json"
SUMMARY_MD = OUT / "mission_1000_summary.md"
SNAPSHOT = OUT / "mission_1000_state_snapshot.json"

TARGET = 1000
CHECKPOINT_EVERY = 50
ATR_PERIOD = 14
WARMUP = 900          # 1h ema200 için yeterli 15m mum (gözlem skorunda)
INTERVAL = "15m"
PAGES = 64            # 64 × 1500 = 96.000 mum ≈ 2,7 yıl / sembol
INTERVAL_MIN = 15

# Oracle motor globalinden BAĞIMSIZ: taraf başına %0,1 taker ücret varsayımı.
# (Motorun FEE_RATE'i buradan sapıyorsa oracle uyuşmazlık üretir — istenen budur.)
ORACLE_FEE_RATE = 0.001


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
    """Motor kodundan BAĞIMSIZ oracle (yuvarlama sırası motorla aynı)."""
    d = 1.0 if side == "LONG" else -1.0
    gross_raw = (exit_ - entry) * qty * d
    fee_raw = (entry + exit_) * qty * ORACLE_FEE_RATE
    return {"gross_pnl": round(gross_raw, 8), "fee_usdt": round(fee_raw, 8),
            "pnl": round(gross_raw - fee_raw, 8)}


def fetch_paged_klines(symbol: str, interval: str, pages: int) -> pd.DataFrame:
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
        time.sleep(0.12)
    full = pd.concat(reversed(frames), ignore_index=True)
    return full.drop_duplicates(subset="open_time").reset_index(drop=True)


def compute_atr(window: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    w = window.tail(period * 4)
    high, low, close = w["high"], w["low"], w["close"]
    prev = close.shift(1)
    tr = (high - low).combine((high - prev).abs(), max).combine((low - prev).abs(), max)
    return float(tr.rolling(period).mean().iloc[-1])


def observe_signal(window: pd.DataFrame) -> dict:
    """GÖZLEM: motorun MEVCUT add_indicators + score_setup fonksiyonlarıyla
    giriş anındaki skor ve rejimi ölçer. Karar VERMEZ, sadece kaydeder."""
    fast_raw = window.tail(1200).copy()
    fast_raw["ts"] = pd.to_datetime(fast_raw["open_time"], unit="ms", utc=True)
    trend_raw = (fast_raw.set_index("ts")
                 .resample("1h")
                 .agg({"open": "first", "high": "max", "low": "min",
                       "close": "last", "volume": "sum"})
                 .dropna().reset_index())
    fast = add_indicators(fast_raw)
    trend = add_indicators(trend_raw)
    side, score, details = score_setup(fast, trend)
    t = trend.iloc[-2]
    if t["ema50"] > t["ema200"] and t["close"] > t["ema50"]:
        regime = "TREND_UP"
    elif t["ema50"] < t["ema200"] and t["close"] < t["ema50"]:
        regime = "TREND_DOWN"
    else:
        regime = "RANGE"
    return {"score": int(score), "score_side": side, "regime": regime}


def preflight(config: dict, counters: dict) -> list[str]:
    failures = []
    if config["mode"] != "PAPER":
        failures.append("mode != PAPER")
    bad = dict(config, mode="LIVE")
    try:
        alpha20.validate_startup_config(bad)
        failures.append("PAPER kilidi delinebildi: LIVE mod SystemExit üretmedi")
    except SystemExit:
        pass
    st = {"balance": 10000.0, "day": "x", "day_start_balance": 10000.0,
          "consecutive_losses": 0, "position": {"symbol": "X"}, "trades": []}
    try:
        open_paper_position("BTCUSDT", "LONG",
                            {"price": 100.0, "atr": 10.0}, config, st)
        failures.append("risk bypass: açık pozisyon varken ikinci pozisyon açıldı")
    except ValueError:
        pass
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
    equity, peak, max_dd = starting, starting, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak > 0 else 0)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "closed": len(pnls), "wins": len(wins), "losses": len(losses),
        "breakeven": len(pnls) - len(wins) - len(losses),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "total_fees": round(sum(t["fee_usdt"] for t in trades), 4),
        "net_pnl": round(sum(pnls), 4),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_drawdown_pct": round(max_dd, 4),
    }


def aggregate_observations(meta: list[dict]) -> dict:
    """Ek gözlem metrikleri (sadece raporlama)."""
    if not meta:
        return {}
    n = len(meta)
    gross_profit = sum(m["pnl"] for m in meta if m["pnl"] > 0)
    total_fee = sum(m["fee_usdt"] for m in meta)

    def group(key):
        g: dict = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0, "wins": 0})
        for m in meta:
            b = g[m[key]]
            b["trades"] += 1
            b["net_pnl"] += m["pnl"]
            if m["pnl"] > 0:
                b["wins"] += 1
        return {str(k): {"trades": v["trades"],
                         "net_pnl": round(v["net_pnl"], 4),
                         "win_rate_pct": round(v["wins"] / v["trades"] * 100, 2)}
                for k, v in sorted(g.items())}

    return {
        "avg_atr": round(sum(m["atr"] for m in meta) / n, 6),
        "avg_duration_minutes": round(sum(m["duration_min"] for m in meta) / n, 1),
        "avg_notional_usdt": round(sum(m["notional"] for m in meta) / n, 2),
        "avg_fee_usdt": round(total_fee / n, 4),
        "fee_to_gross_profit_ratio": (round(total_fee / gross_profit, 4)
                                      if gross_profit > 0 else None),
        "by_symbol": group("symbol"),
        "by_hour_utc": group("hour"),
        "by_weekday": group("weekday"),
        "by_regime": group("regime"),
        "by_score_bucket": group("score_bucket"),
    }


def write_metrics_csv(obs: dict) -> None:
    with METRICS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["section", "key", "trades", "net_pnl", "win_rate_pct"])
        for k in ("avg_atr", "avg_duration_minutes", "avg_notional_usdt",
                  "avg_fee_usdt", "fee_to_gross_profit_ratio"):
            w.writerow(["scalar", k, "", obs.get(k), ""])
        for section in ("by_symbol", "by_hour_utc", "by_weekday",
                        "by_regime", "by_score_bucket"):
            for key, v in obs.get(section, {}).items():
                w.writerow([section, key, v["trades"], v["net_pnl"],
                            v["win_rate_pct"]])


def main() -> int:
    resume = "--resume" in sys.argv
    counters: dict[str, int] = {"ledger_mismatch": 0, "pnl_mismatch": 0,
                                "risk_violation": 0, "warning": 0,
                                "exception": 0, "recovery": 0}
    started_at = now()
    t0 = time.time()

    print("Ön kontrol: tüm testler çalıştırılıyor...")
    tests = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=ROOT, capture_output=True, text=True)
    test_line = tests.stdout.strip().splitlines()[-1] if tests.stdout else "?"
    print(f"  pytest: {test_line}")
    if tests.returncode != 0:
        print("KRİTİK: testler geçmedi — Mission 1000 başlatılmadı.")
        return 2

    config = json.loads((ROOT / "alpha20_v1" / "config.json").read_text())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()

    # İzolasyon
    alpha20.TRADE_HISTORY_PATH = HISTORY_PATH
    real_files = [ROOT / "alpha20_v1" / "state.json",
                  ROOT / "alpha20_v1" / "trade_history.json"]
    mtimes = {p.name: (p.stat().st_mtime if p.exists() else None) for p in real_files}

    if preflight(config, counters):
        print("KRİTİK: preflight başarısız — koşu durduruldu.")
        return 2
    print("Preflight: PAPER kilidi ✔ | risk bypass engelli ✔ | checkpoint ✔")

    starting_balance = float(config["starting_balance_usdt"])
    meta: list[dict] = []
    skipped = 0
    opened = 0
    if resume and SNAPSHOT.exists():
        snap = json.loads(SNAPSHOT.read_text())
        run_id, state = snap["run_id"], snap["state"]
        meta = snap["meta"]
        skipped = snap["skipped"]
        opened = snap["opened"]
        counters.update(snap["counters"])
        counters["recovery"] += 1
        # History dosyası snapshot'tan İLERİDE olabilir (çökme, checkpoint ile
        # bir sonraki checkpoint arasında olduysa). Snapshot ile hizala:
        # fazla kayıtları at; eksikse veya pnl uyuşmuyorsa devam REDDEDİLİR.
        n_snap = len(state["trades"])
        hist = json.loads(HISTORY_PATH.read_text()) if HISTORY_PATH.exists() else []
        if len(hist) < n_snap:
            print("KRİTİK: history snapshot'tan kısa — devam güvenli değil.")
            return 2
        hist = hist[:n_snap]
        for h, t in zip(hist, state["trades"]):
            if h["pnl"] != t["pnl"]:
                print("KRİTİK: history/snapshot pnl uyuşmazlığı — devam reddedildi.")
                return 2
        HISTORY_PATH.write_text(json.dumps(hist, ensure_ascii=False, indent=2))
        jsonl_append(INCIDENTS, {"time": now(), "kind": "recovery",
                                 "detail": f"resume from {snap['saved_at']}, "
                                           f"closed={len(state['trades'])}"})
        print(f"RESUME: run_id={run_id} kapanmış={len(state['trades'])}")
    else:
        run_id = f"M1000-{uuid.uuid4().hex[:10]}"
        for p in (CHECKPOINTS, INCIDENTS, HISTORY_PATH, SNAPSHOT):
            if p.exists():
                p.unlink()
        state = {"balance": starting_balance,
                 "day": datetime.now(timezone.utc).date().isoformat(),
                 "day_start_balance": starting_balance,
                 "consecutive_losses": 0, "position": None, "trades": [],
                 "network_errors": 0}
        print(f"run_id={run_id} | başlangıç bakiyesi={starting_balance}")

    symbols = config["symbols"]
    print(f"Gerçek {INTERVAL} tarihsel veri indiriliyor "
          f"({PAGES}×1500 mum/sembol ≈ 2,7 yıl)...")
    data = {}
    for sym in symbols:
        df = fetch_paged_klines(sym, INTERVAL, PAGES)
        data[sym] = df
        print(f"  {sym}: {len(df)} mum "
              f"({pd.to_datetime(df['open_time'].iloc[0], unit='ms').date()} → "
              f"{pd.to_datetime(df['open_time'].iloc[-1], unit='ms').date()})")

    cursor = {s: WARMUP + ATR_PERIOD for s in symbols}
    i = 0
    if resume and SNAPSHOT.exists():
        # Deterministik devam: imleçler open_time çapasıyla yeniden bulunur;
        # çapa yeni veri setinde yoksa devam REDDEDİLİR (sessiz sapma olmasın).
        i = snap.get("i", 0)
        for s, anchor in snap.get("anchors", {}).items():
            if anchor is None:
                cursor[s] = len(data[s])
                continue
            hit = data[s].index[data[s]["open_time"] == anchor]
            if len(hit) == 0:
                print(f"KRİTİK: resume çapası bulunamadı ({s} @ {anchor}) — "
                      "veri kaymış, devam güvenli değil. Temiz koşu başlatın.")
                return 2
            cursor[s] = int(hit[0])
    sides = ["LONG", "SHORT"]
    fatal = None

    def checkpoint() -> None:
        m = metrics_of(state["trades"], starting_balance)
        cp = {"run_id": run_id, "time": now(),
              "closed_trades": m["closed"],
              "win_rate_pct": m["win_rate_pct"],
              "gross_profit": m["gross_profit"], "gross_loss": m["gross_loss"],
              "net_pnl": m["net_pnl"], "total_fees": m["total_fees"],
              "profit_factor": m["profit_factor"],
              "max_drawdown_pct": m["max_drawdown_pct"],
              "balance": round(state["balance"], 4),
              "open_positions": 1 if state["position"] else 0,
              "ledger_mismatch": counters["ledger_mismatch"],
              "pnl_mismatch": counters["pnl_mismatch"],
              "risk_violation": counters["risk_violation"],
              "skipped_trades": skipped,
              "economic_filter_rejects": skipped,
              "exceptions": counters["exception"],
              "uptime_seconds": round(time.time() - t0, 1)}
        jsonl_append(CHECKPOINTS, cp)
        anchors = {s: (int(data[s]["open_time"].iloc[cursor[s]])
                       if cursor[s] < len(data[s]) else None)
                   for s in symbols}
        SNAPSHOT.write_text(json.dumps(
            {"run_id": run_id, "saved_at": now(), "state": state,
             "counters": counters, "meta": meta, "skipped": skipped,
             "opened": opened, "i": i, "anchors": anchors},
            ensure_ascii=False))
        print(f"CHECKPOINT #{m['closed']//CHECKPOINT_EVERY}: "
              f"kapanmış={m['closed']} net={m['net_pnl']:+.2f} "
              f"bakiye={cp['balance']:.2f} dd={m['max_drawdown_pct']}% "
              f"uptime={cp['uptime_seconds']}s")

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

        # Gözlem: giriş anı skoru/rejimi (motorun kendi fonksiyonlarıyla)
        try:
            obs = observe_signal(window)
        except Exception as exc:  # gözlem hatası koşuyu durdurmaz
            incident("warning", f"observe_signal: {exc}", counters)
            obs = {"score": -1, "score_side": None, "regime": "UNKNOWN"}
        entry_ts = pd.to_datetime(int(df["open_time"].iloc[start - 1]),
                                  unit="ms", utc=True)

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
                duration_candles = j - start + 1
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
            incident("pnl_mismatch", f"trade#{opened} PnL/fee oracle uyuşmazlığı",
                     counters)
        if abs((state["balance"] - balance_before) - t["pnl"]) > 1e-8:
            incident("ledger_mismatch", f"trade#{opened} bakiye deltası≠pnl", counters)
        hist = json.loads(HISTORY_PATH.read_text())
        if len(hist) != len(state["trades"]) or hist[-1]["pnl"] != t["pnl"]:
            incident("ledger_mismatch", f"trade#{opened} history uyuşmazlığı", counters)

        meta.append({
            "symbol": sym, "side": t["side"],
            "entry_time_utc": entry_ts.isoformat(),
            "hour": entry_ts.hour,
            "weekday": entry_ts.strftime("%a"),
            "atr": atr,
            "duration_min": duration_candles * INTERVAL_MIN,
            "notional": t["entry_price"] * t["quantity"],
            "fee_usdt": t["fee_usdt"], "pnl": t["pnl"],
            "score": obs["score"], "score_side": obs["score_side"],
            "score_bucket": f"{(obs['score'] // 20) * 20:03d}-"
                            f"{(obs['score'] // 20) * 20 + 19:03d}"
                            if obs["score"] >= 0 else "N/A",
            "regime": obs["regime"],
        })

        n = len(state["trades"])
        if n % CHECKPOINT_EVERY == 0:
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

    if trades:
        fields = ["symbol", "side", "entry_price", "exit_price", "quantity",
                  "stop", "target", "risk_usdt", "gross_pnl", "fee_usdt", "pnl",
                  "result", "close_reason", "opened_at", "closed_at", "balance_after"]
        with TRADES_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(trades)

    m = metrics_of(trades, starting_balance)
    obs_agg = aggregate_observations(meta)
    write_metrics_csv(obs_agg)
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
    raw_net = sum(t["pnl"] for t in trades)
    if abs((state["balance"] - starting_balance) - raw_net) > 1e-6:
        fail_reasons.append("bakiye değişimi ≠ net PnL")
    passed = not fail_reasons

    summary = {
        "mission": "Mission 1000", "commit": commit, "run_id": run_id,
        "mode": f"PAPER (izole replay, gerçek Binance {INTERVAL} verisi, "
                "ekonomik filtre aktif)",
        "started_at": started_at, "finished_at": finished_at,
        "duration_seconds": duration, "tests": test_line,
        "opened_trades": opened, "closed_trades": m["closed"],
        "skipped_trades": skipped, "economic_filter_rejects": skipped,
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
        "observations": obs_agg,
        "result": "PASS" if passed else "FAIL",
        "fail_reasons": fail_reasons,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    md = ["# Mission 1000 — Yönetici Özeti", ""]
    md.append("## Teknik Altyapı Doğrulaması")
    for k in ("result", "commit", "run_id", "mode", "tests", "closed_trades",
              "ledger_validation_count", "ledger_mismatch_count",
              "pnl_mismatch_count", "risk_violation_count", "exception_count",
              "warning_count", "recovery_count", "open_positions",
              "duration_seconds"):
        md.append(f"- **{k}**: {summary[k]}")
    md.append("")
    md.append("## Strateji Performansı (ayrı değerlendirme)")
    for k in ("opened_trades", "skipped_trades", "wins", "losses",
              "win_rate_pct", "gross_profit", "gross_loss", "total_fees",
              "net_pnl", "profit_factor", "max_drawdown_pct",
              "starting_balance", "final_balance"):
        md.append(f"- **{k}**: {summary[k]}")
    md.append("")
    md.append("## Gözlem Metrikleri")
    for k, v in obs_agg.items():
        if not isinstance(v, dict):
            md.append(f"- **{k}**: {v}")
    for section in ("by_symbol", "by_regime", "by_score_bucket"):
        md.append(f"\n### {section}")
        for key, v in obs_agg.get(section, {}).items():
            md.append(f"- {key}: {v['trades']} işlem, net {v['net_pnl']:+.2f}, "
                      f"WR {v['win_rate_pct']}%")
    if fail_reasons:
        md.append("\n## FAIL Nedenleri")
        md.extend(f"- {r}" for r in fail_reasons)
    SUMMARY_MD.write_text("\n".join(md) + "\n")

    print("\n══════════ MISSION 1000 ÖZETİ ══════════")
    for k, v in summary.items():
        if k != "observations":
            print(f"{k}: {v}")
    print("═════════════════════════════════════════")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
