"""Mission 1200 — REAL SIGNAL VALIDATION + FEE CALIBRATION.

Amaç: Scripted girişleri kapatıp GERÇEK sinyal akışını uçtan uca ölçmek.
- Girişler motorun KENDİ yolu ile seçilir: add_indicators + score_setup,
  skor >= config.minimum_score (65), run_cycle ile aynı aday seçimi
  (en yüksek skor, eşitlikte sembol listesi sırası — max() semantiği).
- Motor koduna dokunulmaz; PAPER kilidi ve ekonomik filtre aktif.
- Saat/gün/rejim filtresi YOK.
- AŞAMA A (baseline): mevcut fee_safety_factor (config) ile koşu.
- AŞAMA B: sf ∈ {1.5, 2.0, 2.5} varyantları — AYNI veri, AYNI başlangıç
  state'i, AYNI sinyal akışı. Sinyal akışı bir kez önceden hesaplanır
  (skor sf'den ve bakiyeden bağımsızdır) → varyantlar birebir karşılaştırılabilir
  ve deterministiktir.
- Her kapanış bağımsız oracle ile doğrulanır (mission100/1000 ile aynı).
- İzolasyon: gerçek state.json / trade_history.json'a dokunulmaz.

Kullanım: python tools/mission1200.py
"""
from __future__ import annotations

import csv
import hashlib
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
    TradeSkippedError,
    add_indicators,
    fetch_klines,
    manage_position,
    open_paper_position,
    score_setup,
)

OUT = ROOT / "alpha20_v1" / "mission1200"
OUT.mkdir(exist_ok=True)
HISTORY_PATH = OUT / "mission_1200_trade_history.json"
INCIDENTS = OUT / "mission_1200_incidents.jsonl"
SUMMARY_JSON = OUT / "mission_1200_summary.json"
SUMMARY_MD = OUT / "mission_1200_summary.md"

ATR_PERIOD = 14
WARMUP = 900
INTERVAL = "15m"
PAGES = 12  # 12×1500 = 18.000 mum ≈ 6 ay/sembol — baseline ≥300 kapanış için
            # yeterli; 64 sayfalık tam dönem gerçek sinyalle 2400+ işlem üretip
            # bellek limitini aştı (koşu OOM ile öldü).
BASELINE_MIN_CLOSED = 300
VARIANT_SFS = [1.5, 2.0, 2.5]

# Oracle motor globalinden BAĞIMSIZ (taraf başına %0,1 taker varsayımı).
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
    for f in failures:
        incident("exception", f"preflight: {f}", counters)
    return failures


def precompute_signal_stream(data: dict, config: dict) -> list[dict]:
    """GERÇEK sinyal akışı: her 15m adımda motorun add_indicators + score_setup
    fonksiyonları çağrılır; skor >= minimum_score olan adaylar toplanır ve
    run_cycle'daki max() semantiğiyle zaman başına TEK aday seçilir.
    Skor sf'den/bakiyeden bağımsızdır → akış tüm varyantlarda birebir aynıdır."""
    min_score = config["minimum_score"]
    per_symbol: dict[str, dict] = {}
    for sym in config["symbols"]:
        df = data[sym]
        fast = add_indicators(df)
        raw = df.copy()
        raw["ts"] = pd.to_datetime(raw["open_time"], unit="ms", utc=True)
        trend_raw = (raw.set_index("ts")
                     .resample("1h")
                     .agg({"open": "first", "high": "max", "low": "min",
                           "close": "last", "volume": "sum"})
                     .dropna().reset_index())
        trend_raw["open_time"] = trend_raw["ts"].astype("int64") // 10**6
        trend = add_indicators(trend_raw)
        per_symbol[sym] = {"df": df, "fast": fast, "trend": trend,
                           "trend_times": trend["open_time"].to_numpy()}

    by_time: dict[int, list] = {}
    for sym in config["symbols"]:  # config sırası korunur (tie-break için)
        p = per_symbol[sym]
        df, fast, trend = p["df"], p["fast"], p["trend"]
        times = df["open_time"].to_numpy()
        import numpy as np
        t_pos = np.searchsorted(p["trend_times"], times, side="right")
        for j in range(WARMUP, len(df)):
            tp = int(t_pos[j])
            if tp < 2:
                continue
            side, score, details = score_setup(fast.iloc[:j + 1],
                                               trend.iloc[:tp])
            if side and score >= min_score:
                by_time.setdefault(int(times[j]), []).append(
                    (score, sym, side, details, j))
    stream = []
    for t in sorted(by_time):
        cands = by_time[t]
        score, sym, side, details, j = max(cands, key=lambda x: x[0])
        stream.append({"time": t, "score": score, "symbol": sym,
                       "side": side, "details": details, "j": j,
                       "n_candidates": len(cands)})
    return stream


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
    total_fees = sum(t["fee_usdt"] for t in trades)
    return {
        "closed": len(pnls), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "total_fees": round(total_fees, 4),
        "fee_to_gross_profit_ratio": (round(total_fees / gross_profit, 4)
                                      if gross_profit > 0 else None),
        "net_pnl": round(sum(pnls), 4),
        "avg_net_pnl_per_trade": (round(sum(pnls) / len(pnls), 4)
                                  if pnls else None),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_drawdown_pct": round(max_dd, 4),
    }


def run_variant(label: str, sf: float, data: dict, stream: list[dict],
                config: dict, counters: dict) -> dict:
    """Tek varyant koşusu: taze state, aynı veri, aynı sinyal akışı."""
    cfg = dict(config, fee_safety_factor=sf)
    starting = float(cfg["starting_balance_usdt"])
    state = {"balance": starting,
             "day": datetime.now(timezone.utc).date().isoformat(),
             "day_start_balance": starting,
             "consecutive_losses": 0, "position": None, "trades": [],
             "network_errors": 0}
    if HISTORY_PATH.exists():
        HISTORY_PATH.unlink()

    eligible = executed = rejected = 0
    score_sum = 0
    resume_time = 0  # pozisyon kapanana kadar yeni sinyal değerlendirilmez
    fatal = None

    for ev in stream:
        if ev["time"] < resume_time:
            continue
        eligible += 1
        sym, side, details, j = ev["symbol"], ev["side"], ev["details"], ev["j"]
        df = data[sym]
        balance_before = state["balance"]
        # Uzun koşu koşulu (mission1000 ile aynı): günlük limit simülasyonu
        # durdurmasın — risk motoru koduna dokunulmaz.
        state["day_start_balance"] = state["balance"]
        state["consecutive_losses"] = 0
        try:
            open_paper_position(sym, side, details, cfg, state)
        except TradeSkippedError:
            rejected += 1
            continue
        except ValueError as exc:
            incident("exception", f"[{label}] open reddi {sym} {side}: {exc}",
                     counters)
            fatal = str(exc)
            break
        executed += 1
        score_sum += ev["score"]
        pos = dict(state["position"])

        exp_risk = balance_before * cfg["risk_per_trade_pct"] / 100
        if abs(pos["risk_usdt"] - exp_risk) > 1e-6:
            incident("risk_violation",
                     f"[{label}] trade#{executed} risk "
                     f"{pos['risk_usdt']}≠{exp_risk}", counters)

        closed = False
        close_idx = None
        for k in range(j, len(df)):
            candle = df.iloc[k: k + 1]
            alpha20.fetch_klines = lambda *a, _c=candle, **kw: _c
            try:
                manage_position(state)
            except Exception as exc:
                incident("exception", f"[{label}] manage_position: {exc}", counters)
                traceback.print_exc()
                fatal = str(exc)
                break
            finally:
                alpha20.fetch_klines = fetch_klines
            if state["position"] is None:
                closed = True
                close_idx = k
                break
        if fatal:
            break
        if not closed:
            state["position"] = None
            executed -= 1
            score_sum -= ev["score"]
            incident("warning", f"[{label}] {sym} {side}: veri sonunda SL/TP yok "
                     "— giriş iptal", counters)
            resume_time = 2**63  # veri bitti
            continue
        resume_time = int(df["open_time"].iloc[close_idx]) + 1

        # Bağımsız oracle doğrulaması
        t = state["trades"][-1]
        fc = df.iloc[close_idx]
        c_high, c_low = float(fc["high"]), float(fc["low"])
        if t["exit_price"] not in (t["stop"], t["target"]):
            incident("pnl_mismatch", f"[{label}] trade#{executed} fill "
                     "stop/target değil", counters)
        else:
            if t["side"] == "LONG":
                stop_hit, tp_hit = c_low <= t["stop"], c_high >= t["target"]
            else:
                stop_hit, tp_hit = c_high >= t["stop"], c_low <= t["target"]
            if t["exit_price"] == t["stop"] and not stop_hit:
                incident("pnl_mismatch", f"[{label}] trade#{executed} stop fill "
                         "desteksiz", counters)
            if t["exit_price"] == t["target"] and (not tp_hit or stop_hit):
                incident("pnl_mismatch", f"[{label}] trade#{executed} TP "
                         "fill/tie-break hatası", counters)
        exp = independent_pnl(t["entry_price"], t["exit_price"], t["quantity"],
                              t["side"])
        if (abs(t["pnl"] - exp["pnl"]) > 1e-8
                or abs(t["gross_pnl"] - exp["gross_pnl"]) > 1e-8
                or abs(t["fee_usdt"] - exp["fee_usdt"]) > 1e-8):
            incident("pnl_mismatch", f"[{label}] trade#{executed} PnL/fee oracle "
                     "uyuşmazlığı", counters)
        if abs((state["balance"] - balance_before) - t["pnl"]) > 1e-8:
            incident("ledger_mismatch", f"[{label}] trade#{executed} bakiye "
                     "deltası≠pnl", counters)
        hist = json.loads(HISTORY_PATH.read_text())
        if len(hist) != len(state["trades"]) or hist[-1]["pnl"] != t["pnl"]:
            incident("ledger_mismatch", f"[{label}] trade#{executed} history "
                     "uyuşmazlığı", counters)

        n = len(state["trades"])
        if n % 50 == 0:
            print(f"  [{label}] kapanmış={n} net="
                  f"{sum(x['pnl'] for x in state['trades']):+.2f}")
        if counters["ledger_mismatch"] or counters["pnl_mismatch"]:
            fatal = "doğrulama uyuşmazlığı"
            break

    # Final ledger
    trades = state["trades"]
    net = sum(t["pnl"] for t in trades)
    if abs((starting + net) - state["balance"]) > 1e-6:
        incident("ledger_mismatch", f"[{label}] final: start+Σpnl ≠ balance",
                 counters)

    csv_path = OUT / f"mission_1200_trades_{label}.csv"
    if trades:
        fields = ["symbol", "side", "entry_price", "exit_price", "quantity",
                  "stop", "target", "risk_usdt", "gross_pnl", "fee_usdt", "pnl",
                  "result", "close_reason", "opened_at", "closed_at",
                  "balance_after"]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(trades)

    m = metrics_of(trades, starting)
    # İşlem-düzeyi determinizm parmak izi (agregalar tesadüfen çakışamaz)
    digest = hashlib.sha256(json.dumps(
        [[t["symbol"], t["side"], t["entry_price"], t["exit_price"],
          t["quantity"], t["pnl"], t["close_reason"]] for t in trades],
        ensure_ascii=False).encode()).hexdigest()
    return {
        "label": label, "fee_safety_factor": sf,
        "trades_sha256": digest,
        "eligible_signals": eligible, "executed_trades": executed,
        "rejected_by_economic_filter": rejected,
        "closed_trades": m["closed"], "wins": m["wins"], "losses": m["losses"],
        "win_rate_pct": m["win_rate_pct"],
        "gross_profit": m["gross_profit"], "gross_loss": m["gross_loss"],
        "total_fees": m["total_fees"],
        "fee_to_gross_profit_ratio": m["fee_to_gross_profit_ratio"],
        "net_pnl": m["net_pnl"],
        "avg_net_pnl_per_trade": m["avg_net_pnl_per_trade"],
        "profit_factor": m["profit_factor"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "avg_signal_score": round(score_sum / executed, 2) if executed else None,
        "final_balance": round(state["balance"], 4),
        "open_positions": 1 if state["position"] else 0,
        "fatal": fatal,
        "trades_csv": csv_path.name,
    }


def main() -> int:
    counters: dict[str, int] = {"ledger_mismatch": 0, "pnl_mismatch": 0,
                                "risk_violation": 0, "warning": 0,
                                "exception": 0}
    started_at = now()
    t0 = time.time()

    print("Ön kontrol: tüm testler çalıştırılıyor...")
    tests = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=ROOT, capture_output=True, text=True)
    test_line = tests.stdout.strip().splitlines()[-1] if tests.stdout else "?"
    print(f"  pytest: {test_line}")
    if tests.returncode != 0:
        print("KRİTİK: testler geçmedi — Mission 1200 başlatılmadı.")
        return 2

    config = json.loads((ROOT / "alpha20_v1" / "config.json").read_text())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    baseline_sf = float(config.get("fee_safety_factor", 2.0))

    alpha20.TRADE_HISTORY_PATH = HISTORY_PATH
    real_files = [ROOT / "alpha20_v1" / "state.json",
                  ROOT / "alpha20_v1" / "trade_history.json"]
    mtimes = {p.name: (p.stat().st_mtime if p.exists() else None)
              for p in real_files}

    if INCIDENTS.exists():
        INCIDENTS.unlink()
    if preflight(config, counters):
        print("KRİTİK: preflight başarısız.")
        return 2
    print(f"Preflight ✔ | baseline sf={baseline_sf} | "
          f"minimum_score={config['minimum_score']}")

    run_id = f"M1200-{uuid.uuid4().hex[:10]}"
    print(f"run_id={run_id}")
    print(f"Gerçek {INTERVAL} veri indiriliyor ({PAGES}×1500 mum/sembol)...")
    data = {}
    for sym in config["symbols"]:
        df = fetch_paged_klines(sym, INTERVAL, PAGES)
        data[sym] = df
        print(f"  {sym}: {len(df)} mum "
              f"({pd.to_datetime(df['open_time'].iloc[0], unit='ms').date()} → "
              f"{pd.to_datetime(df['open_time'].iloc[-1], unit='ms').date()})")
    period = {
        "from": pd.to_datetime(
            min(int(d['open_time'].iloc[0]) for d in data.values()),
            unit="ms").isoformat(),
        "to": pd.to_datetime(
            max(int(d['open_time'].iloc[-1]) for d in data.values()),
            unit="ms").isoformat(),
    }

    print("Gerçek sinyal akışı önceden hesaplanıyor (motorun score_setup'ı, "
          f"skor >= {config['minimum_score']})...")
    t1 = time.time()
    stream = precompute_signal_stream(data, config)
    print(f"  sinyal akışı: {len(stream)} aday zaman noktası "
          f"({round(time.time() - t1, 1)}s)")

    # AŞAMA A — BASELINE (mevcut sf)
    print(f"\nAŞAMA A — BASELINE (sf={baseline_sf})")
    results = {}
    baseline = run_variant("baseline", baseline_sf, data, stream, config,
                           counters)
    results["baseline"] = baseline
    print(f"  baseline: closed={baseline['closed_trades']} "
          f"net={baseline['net_pnl']:+.2f} wr={baseline['win_rate_pct']}%")

    baseline_ok = (baseline["closed_trades"] >= BASELINE_MIN_CLOSED
                   and counters["ledger_mismatch"] == 0
                   and counters["pnl_mismatch"] == 0
                   and counters["risk_violation"] == 0
                   and counters["exception"] == 0
                   and baseline["open_positions"] == 0)

    # AŞAMA B — FEE CALIBRATION (baseline teknik olarak temizse)
    if baseline_ok:
        print("\nAŞAMA B — FEE CALIBRATION")
        for sf in VARIANT_SFS:
            label = f"sf_{str(sf).replace('.', '_')}"
            if abs(sf - baseline_sf) < 1e-12:
                # Aynı veri + aynı akış + deterministik koşu → baseline ile özdeş.
                # Determinizmi KANITLAMAK için yine de yeniden koşulur.
                pass
            r = run_variant(label, sf, data, stream, config, counters)
            results[label] = r
            print(f"  {label}: eligible={r['eligible_signals']} "
                  f"executed={r['executed_trades']} rejected="
                  f"{r['rejected_by_economic_filter']} closed="
                  f"{r['closed_trades']} net={r['net_pnl']:+.2f}")
        # Determinizm kanıtı: sf=2.0 varyantı baseline ile birebir aynı olmalı
        rep = results.get(f"sf_{str(baseline_sf).replace('.', '_')}")
        if rep:
            same = (rep["trades_sha256"] == baseline["trades_sha256"]
                    and all(rep[k] == baseline[k] for k in
                            ("closed_trades", "net_pnl", "total_fees",
                             "win_rate_pct", "eligible_signals",
                             "rejected_by_economic_filter")))
            if not same:
                incident("exception",
                         "determinizm ihlali: sf=baseline varyantı baseline "
                         "koşusuyla aynı sonucu vermedi", counters)
    else:
        print("Baseline teknik/örneklem koşullarını sağlamadı — AŞAMA B atlandı.")

    for p in real_files:
        after = p.stat().st_mtime if p.exists() else None
        if after != mtimes[p.name]:
            incident("exception", f"İZOLASYON İHLALİ: {p.name} değişti", counters)

    technical_ok = (counters["ledger_mismatch"] == 0
                    and counters["pnl_mismatch"] == 0
                    and counters["risk_violation"] == 0
                    and counters["exception"] == 0
                    and all(r["open_positions"] == 0 for r in results.values()))
    passed = technical_ok and baseline_ok and len(results) == 1 + len(VARIANT_SFS)

    summary = {
        "mission": "Mission 1200 — Real Signal Validation", "commit": commit,
        "run_id": run_id,
        "mode": "PAPER (izole replay, gerçek Binance 15m verisi, GERÇEK sinyal "
                f"akışı skor>={config['minimum_score']}, ekonomik filtre aktif)",
        "data_period": period,
        "started_at": started_at, "finished_at": now(),
        "duration_seconds": round(time.time() - t0, 1), "tests": test_line,
        "signal_stream_events": len(stream),
        "baseline_min_closed_required": BASELINE_MIN_CLOSED,
        "variants": results,
        "ledger_mismatch_count": counters["ledger_mismatch"],
        "pnl_mismatch_count": counters["pnl_mismatch"],
        "risk_violation_count": counters["risk_violation"],
        "warning_count": counters["warning"],
        "exception_count": counters["exception"],
        "technical_integrity": "PASS" if technical_ok else "FAIL",
        "real_signal_baseline": "PASS" if baseline_ok else "FAIL",
        "result": "PASS" if passed else "FAIL",
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    md = ["# Mission 1200 — Real Signal Validation", ""]
    for k in ("result", "technical_integrity", "real_signal_baseline",
              "commit", "run_id", "mode", "data_period", "tests",
              "signal_stream_events", "duration_seconds"):
        md.append(f"- **{k}**: {summary[k]}")
    md.append("\n## Varyant Karşılaştırması\n")
    keys = ["fee_safety_factor", "eligible_signals", "executed_trades",
            "rejected_by_economic_filter", "closed_trades", "win_rate_pct",
            "gross_profit", "gross_loss", "total_fees",
            "fee_to_gross_profit_ratio", "net_pnl", "avg_net_pnl_per_trade",
            "profit_factor", "max_drawdown_pct", "avg_signal_score",
            "final_balance"]
    labels = list(results)
    md.append("| metrik | " + " | ".join(labels) + " |")
    md.append("|---|" + "---|" * len(labels))
    for k in keys:
        md.append(f"| {k} | " + " | ".join(str(results[l][k]) for l in labels)
                  + " |")
    SUMMARY_MD.write_text("\n".join(md) + "\n")

    print("\n══════════ MISSION 1200 ÖZETİ ══════════")
    for k, v in summary.items():
        if k != "variants":
            print(f"{k}: {v}")
    for l, r in results.items():
        print(f"--- {l}: " + json.dumps(r, ensure_ascii=False))
    print("═════════════════════════════════════════")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
