"""Mission 1260 — OUT-OF-SAMPLE VALIDATION.

Amaç: Mission 1250'de seçilen atr_stop_multiplier=3.0 adayını, seçimde
KULLANILMAMIŞ veri üzerinde 1.5 kontrolüne karşı doğrulamak.

Veri dönemi (KOŞUDAN ÖNCE SABİTLENDİ, sonuca göre değiştirilemez):
- Mission 1200/1250 seçim verisi: 2026-01-20 → 2026-07-26.
- İleri tarihli veri henüz mevcut olmadığı için out-of-sample dönem,
  seçimde kullanılmamış olan ÖNCEKİ penceredir:
  bitiş = 2026-01-20T00:00:00Z (hariç), 12 sayfa × 1500 mum geriye (~6 ay).
- Sızıntı garantisi: koşu, indirilen her mumun open_time değerinin
  2026-01-20T00:00:00Z'den küçük olduğunu doğrular; aksi hâlde durur.

Değişmeyenler: skor eşiği >=65, risk %0,5, sf=2.0, RR=2.0, sinyal mantığı,
semboller, 15m, oracle'lar, PAPER kilidi.

Karşılaştırma: A) mult=1.5 kontrol, C) mult=3.0 aday — aynı veri, aynı
başlangıç state'i, aynı önceden hesaplanmış sinyal akışı, aynı fee modeli.
Determinizm: C adayı ikinci kez koşulur; işlem-düzeyi SHA256 birebir olmalı.

Kullanım: python tools/mission1260.py
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
sys.path.insert(0, str(ROOT / "tools"))

import pandas as pd  # noqa: E402

import alpha20  # noqa: E402
from alpha20 import (  # noqa: E402
    BASE_URL,
    TradeSkippedError,
    fetch_klines,
    manage_position,
    open_paper_position,
)
from mission1200 import (  # noqa: E402
    independent_pnl,
    precompute_signal_stream,
    preflight,
)

OUT = ROOT / "alpha20_v1" / "mission1260"
OUT.mkdir(exist_ok=True)
HISTORY_PATH = OUT / "mission_1260_trade_history.json"
INCIDENTS = OUT / "mission_1260_incidents.jsonl"
SUMMARY_JSON = OUT / "mission_1260_summary.json"
SUMMARY_MD = OUT / "mission_1260_summary.md"

INTERVAL = "15m"
INTERVAL_MIN = 15
PAGES = 12
# SABİT dönem sınırı: seçim verisinin başlangıcı (2026-01-20T00:00:00Z, hariç)
OOS_END_MS = int(pd.Timestamp("2026-01-20T00:00:00Z").timestamp() * 1000)

CONTROL_MULT = 1.5
CANDIDATE_MULT = 3.0


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def incident(kind: str, detail: str, counters: dict) -> None:
    counters[kind] = counters.get(kind, 0) + 1
    with INCIDENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": now(), "kind": kind, "detail": detail},
                           ensure_ascii=False) + "\n")
    print(f"[INCIDENT/{kind}] {detail}")


def fetch_oos_klines(symbol: str) -> pd.DataFrame:
    """SABİT bitiş zamanından (OOS_END_MS, hariç) geriye sayfalı indirme."""
    frames = []
    end_time = OOS_END_MS - 1
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_base",
            "taker_quote", "ignore"]
    for _ in range(PAGES):
        params = {"symbol": symbol, "interval": INTERVAL, "limit": 1500,
                  "endTime": end_time}
        r = requests.get(f"{BASE_URL}/fapi/v1/klines", params=params,
                         timeout=20)
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
    full = full.drop_duplicates(subset="open_time").reset_index(drop=True)
    if int(full["open_time"].max()) >= OOS_END_MS:
        raise SystemExit(f"VERİ SIZINTISI: {symbol} verisinde seçim dönemine "
                         "taşan mum var — koşu durduruldu.")
    return full


def run_variant(label: str, atr_mult: float, data: dict, stream: list[dict],
                config: dict) -> dict:
    cfg = dict(config, atr_stop_multiplier=atr_mult)
    counters = {"ledger_mismatch": 0, "pnl_mismatch": 0, "risk_violation": 0,
                "warning": 0, "exception": 0}
    starting = float(cfg["starting_balance_usdt"])
    state = {"balance": starting,
             "day": datetime.now(timezone.utc).date().isoformat(),
             "day_start_balance": starting,
             "consecutive_losses": 0, "position": None, "trades": [],
             "network_errors": 0}
    if HISTORY_PATH.exists():
        HISTORY_PATH.unlink()

    eligible = executed = rejected_econ = 0
    resume_time = 0
    fatal = None
    excursions, stop_pcts, risks, holds = [], [], [], []

    for ev in stream:
        if ev["time"] < resume_time:
            continue
        eligible += 1
        sym, side, details, j = ev["symbol"], ev["side"], ev["details"], ev["j"]
        df = data[sym]
        balance_before = state["balance"]
        state["day_start_balance"] = state["balance"]
        state["consecutive_losses"] = 0
        try:
            open_paper_position(sym, side, details, cfg, state)
        except TradeSkippedError:
            rejected_econ += 1
            continue
        except ValueError as exc:
            incident("exception", f"[{label}] open reddi {sym} {side}: {exc}",
                     counters)
            fatal = str(exc)
            break
        executed += 1
        pos = dict(state["position"])

        exp_risk = balance_before * cfg["risk_per_trade_pct"] / 100
        if abs(pos["risk_usdt"] - exp_risk) > 1e-6:
            incident("risk_violation", f"[{label}] trade#{executed} risk "
                     f"{pos['risk_usdt']}≠{exp_risk}", counters)

        entry = pos["entry"]
        stop_dist = abs(entry - pos["stop"])
        stop_pcts.append(stop_dist / entry * 100)
        risks.append(pos["risk_usdt"])

        closed = False
        close_idx = None
        mfe = mae = 0.0
        for k in range(j, len(df)):
            candle = df.iloc[k: k + 1]
            c_high = float(candle["high"].iloc[0])
            c_low = float(candle["low"].iloc[0])
            fav = (c_high - entry) if side == "LONG" else (entry - c_low)
            adv = (entry - c_low) if side == "LONG" else (c_high - entry)
            alpha20.fetch_klines = lambda *a, _c=candle, **kw: _c
            try:
                manage_position(state)
            except Exception as exc:
                incident("exception", f"[{label}] manage_position: {exc}",
                         counters)
                traceback.print_exc()
                fatal = str(exc)
                break
            finally:
                alpha20.fetch_klines = fetch_klines
            if state["position"] is None:
                # Çıkış mumu: stop-first tie-break ile tutarlı excursion
                if state["trades"][-1]["close_reason"] == "STOP_LOSS":
                    mae = max(mae, adv / stop_dist)
                else:
                    mfe = max(mfe, fav / stop_dist)
                closed = True
                close_idx = k
                break
            mfe = max(mfe, fav / stop_dist)
            mae = max(mae, adv / stop_dist)
        if fatal:
            break
        if not closed:
            state["position"] = None
            executed -= 1
            stop_pcts.pop()
            risks.pop()
            incident("warning", f"[{label}] {sym} {side}: veri sonunda SL/TP "
                     "yok — giriş iptal", counters)
            resume_time = 2**63
            continue
        resume_time = int(df["open_time"].iloc[close_idx]) + 1
        holds.append((close_idx - j + 1) * INTERVAL_MIN)
        excursions.append({"mfe_r": mfe, "mae_r": min(mae, 1.0)})

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
                incident("pnl_mismatch", f"[{label}] trade#{executed} stop "
                         "fill desteksiz", counters)
            if t["exit_price"] == t["target"] and (not tp_hit or stop_hit):
                incident("pnl_mismatch", f"[{label}] trade#{executed} TP "
                         "fill/tie-break hatası", counters)
        exp = independent_pnl(t["entry_price"], t["exit_price"],
                              t["quantity"], t["side"])
        if (abs(t["pnl"] - exp["pnl"]) > 1e-8
                or abs(t["gross_pnl"] - exp["gross_pnl"]) > 1e-8
                or abs(t["fee_usdt"] - exp["fee_usdt"]) > 1e-8):
            incident("pnl_mismatch", f"[{label}] trade#{executed} PnL/fee "
                     "oracle uyuşmazlığı", counters)
        if abs((state["balance"] - balance_before) - t["pnl"]) > 1e-8:
            incident("ledger_mismatch", f"[{label}] trade#{executed} bakiye "
                     "deltası≠pnl", counters)
        hist = json.loads(HISTORY_PATH.read_text())
        if len(hist) != len(state["trades"]) or hist[-1] != t:
            incident("ledger_mismatch", f"[{label}] trade#{executed} history "
                     "uyuşmazlığı", counters)

        if len(state["trades"]) % 200 == 0:
            print(f"  [{label}] kapanmış={len(state['trades'])} net="
                  f"{sum(x['pnl'] for x in state['trades']):+.2f}")
        if counters["ledger_mismatch"] or counters["pnl_mismatch"]:
            fatal = "doğrulama uyuşmazlığı"
            break

    trades = state["trades"]
    net = sum(t["pnl"] for t in trades)
    if abs((starting + net) - state["balance"]) > 1e-6:
        incident("ledger_mismatch", f"[{label}] final: start+Σpnl ≠ balance",
                 counters)

    csv_path = OUT / f"mission_1260_trades_{label}.csv"
    if trades:
        fields = ["symbol", "side", "entry_price", "exit_price", "quantity",
                  "stop", "target", "risk_usdt", "gross_pnl", "fee_usdt",
                  "pnl", "result", "close_reason", "balance_after"]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(trades)

    pnls = [t["pnl"] for t in trades]
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gross_profit = sum(t["gross_pnl"] for t in trades if t["gross_pnl"] > 0)
    gross_loss = abs(sum(t["gross_pnl"] for t in trades if t["gross_pnl"] < 0))
    net_profit = sum(t["pnl"] for t in wins)
    net_loss = abs(sum(t["pnl"] for t in losses))
    total_fees = sum(t["fee_usdt"] for t in trades)
    total_risk = sum(risks)
    equity, peak, max_dd = starting, starting, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak > 0 else 0)
    n = len(trades)
    r_values = [t["pnl"] / r for t, r in zip(trades, risks)] if n else []
    win_exc = [e for e, t in zip(excursions, trades) if t["pnl"] > 0]
    loss_exc = [e for e, t in zip(excursions, trades) if t["pnl"] <= 0]
    top5 = sorted((t["pnl"] for t in wins), reverse=True)[:5]
    digest = hashlib.sha256(json.dumps(
        [[t["symbol"], t["side"], t["entry_price"], t["exit_price"],
          t["quantity"], t["pnl"], t["close_reason"]] for t in trades],
        ensure_ascii=False).encode()).hexdigest()

    def avg(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    return {
        "label": label, "atr_stop_multiplier": atr_mult,
        "trades_sha256": digest,
        "eligible_signals": eligible, "executed_trades": executed,
        "rejected_by_economic_filter": rejected_econ,
        "closed_trades": n,
        "win_rate_pct": round(len(wins) / n * 100, 2) if n else None,
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "total_fees": round(total_fees, 4),
        "fee_to_expected_risk_ratio": (round(total_fees / total_risk, 4)
                                       if total_risk > 0 else None),
        "fee_to_gross_profit_ratio": (round(total_fees / gross_profit, 4)
                                      if gross_profit > 0 else None),
        "net_pnl": round(net, 4),
        "avg_net_pnl_per_trade": avg(pnls),
        "expectancy_r": avg(r_values),
        "gross_profit_factor": (round(gross_profit / gross_loss, 4)
                                if gross_loss else None),
        "net_profit_factor": (round(net_profit / net_loss, 4)
                              if net_loss else None),
        "max_drawdown_pct": round(max_dd, 4),
        "avg_stop_distance_pct": avg(stop_pcts),
        "avg_holding_time_min": avg(holds),
        "winners_avg_mae_r": avg([e["mae_r"] for e in win_exc]),
        "losers_avg_mfe_r": avg([e["mfe_r"] for e in loss_exc]),
        "losers_mfe_ge_1r_pct": (round(sum(1 for e in loss_exc
                                           if e["mfe_r"] >= 1.0)
                                       / len(loss_exc) * 100, 2)
                                 if loss_exc else None),
        "top5_winners_pnl": round(sum(top5), 4),
        "top5_winners_share_of_net_profit_pct": (
            round(sum(top5) / net_profit * 100, 2) if net_profit > 0 else None),
        "final_balance": round(state["balance"], 4),
        "open_positions": 1 if state["position"] else 0,
        "ledger_mismatches": counters["ledger_mismatch"],
        "pnl_mismatches": counters["pnl_mismatch"],
        "risk_violations": counters["risk_violation"],
        "exceptions": counters["exception"],
        "warnings": counters["warning"],
        "fatal": fatal, "trades_csv": csv_path.name,
    }


def main() -> int:
    started_at = now()
    t0 = time.time()
    print("Ön kontrol: tüm testler çalıştırılıyor...")
    tests = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=ROOT, capture_output=True, text=True)
    test_line = tests.stdout.strip().splitlines()[-1] if tests.stdout else "?"
    print(f"  pytest: {test_line}")
    if tests.returncode != 0:
        print("KRİTİK: testler geçmedi.")
        return 2

    config = json.loads((ROOT / "alpha20_v1" / "config.json").read_text())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    config_diff = {"atr_stop_multiplier": {"old": CONTROL_MULT,
                                           "new": config["atr_stop_multiplier"]}}
    if float(config["atr_stop_multiplier"]) != CANDIDATE_MULT:
        print("KRİTİK: config.json atr_stop_multiplier aday değeri (3.0) değil.")
        return 2

    alpha20.TRADE_HISTORY_PATH = HISTORY_PATH
    real_files = [ROOT / "alpha20_v1" / "state.json",
                  ROOT / "alpha20_v1" / "trade_history.json"]
    mtimes = {p.name: (p.stat().st_mtime if p.exists() else None)
              for p in real_files}
    if INCIDENTS.exists():
        INCIDENTS.unlink()
    glob = {"exception": 0}
    if preflight(config, glob):
        print("KRİTİK: preflight başarısız.")
        return 2
    print(f"Preflight ✔ | OOS dönem sınırı (sabit): "
          f"< {pd.Timestamp(OOS_END_MS, unit='ms', tz='UTC')}")

    run_id = f"M1260-{uuid.uuid4().hex[:10]}"
    print(f"run_id={run_id}")
    print(f"Out-of-sample {INTERVAL} veri indiriliyor "
          f"({PAGES}×1500 mum/sembol, bitiş sabit)...")
    data = {sym: fetch_oos_klines(sym) for sym in config["symbols"]}
    for sym, df in data.items():
        print(f"  {sym}: {len(df)} mum "
              f"({pd.to_datetime(df['open_time'].iloc[0], unit='ms').date()} → "
              f"{pd.to_datetime(df['open_time'].iloc[-1], unit='ms').date()})")
    period = {"from": pd.to_datetime(min(int(d['open_time'].iloc[0])
                                         for d in data.values()),
                                     unit="ms").isoformat(),
              "to": pd.to_datetime(max(int(d['open_time'].iloc[-1])
                                       for d in data.values()),
                                   unit="ms").isoformat(),
              "leakage_boundary_utc": pd.Timestamp(
                  OOS_END_MS, unit="ms").isoformat()}

    print("Sinyal akışı önceden hesaplanıyor "
          f"(skor >= {config['minimum_score']})...")
    stream = precompute_signal_stream(data, config)
    print(f"  sinyal akışı: {len(stream)} aday zaman noktası")

    results = {}
    for label, mult in (("A_control_1_5", CONTROL_MULT),
                        ("C_candidate_3_0", CANDIDATE_MULT)):
        print(f"\nVARYANT {label}: atr_mult={mult}")
        r = run_variant(label, mult, data, stream, config)
        results[label] = r
        print(f"  {label}: closed={r['closed_trades']} "
              f"net={r['net_pnl']:+.2f} exp_R={r['expectancy_r']} "
              f"fee/risk={r['fee_to_expected_risk_ratio']} "
              f"dd={r['max_drawdown_pct']}%")

    print("\nDeterminizm kanıtı: aday yeniden koşuluyor...")
    rep = run_variant("C_repeat", CANDIDATE_MULT, data, stream, config)
    deterministic = (rep["trades_sha256"]
                     == results["C_candidate_3_0"]["trades_sha256"])
    if not deterministic:
        incident("exception", "determinizm ihlali: aday tekrar koşusu farklı",
                 glob)
    print(f"  determinizm: {'OK' if deterministic else 'İHLAL'}")

    for p in real_files:
        after = p.stat().st_mtime if p.exists() else None
        if after != mtimes[p.name]:
            incident("exception", f"İZOLASYON İHLALİ: {p.name} değişti", glob)

    a, c = results["A_control_1_5"], results["C_candidate_3_0"]
    technical_ok = (glob["exception"] == 0 and deterministic and all(
        r["ledger_mismatches"] == 0 and r["pnl_mismatches"] == 0
        and r["risk_violations"] == 0 and r["exceptions"] == 0
        and r["open_positions"] == 0 for r in [a, c, rep]))
    structural_repeat = (
        c["fee_to_expected_risk_ratio"] < a["fee_to_expected_risk_ratio"]
        and c["max_drawdown_pct"] < a["max_drawdown_pct"]
        and c["expectancy_r"] > a["expectancy_r"])
    passed = technical_ok and structural_repeat

    summary = {
        "mission": "Mission 1260 — Out-of-Sample Validation",
        "commit": commit, "run_id": run_id,
        "config_diff": config_diff,
        "mode": "PAPER (izole replay, out-of-sample dönem, gerçek sinyal "
                f"akışı skor>={config['minimum_score']}, sf=2.0)",
        "data_period": period,
        "selection_period_not_overlapping": "2026-01-20 → 2026-07-26 "
                                            "(Mission 1200/1250) ile kesişim yok",
        "started_at": started_at, "finished_at": now(),
        "duration_seconds": round(time.time() - t0, 1), "tests": test_line,
        "signal_stream_events": len(stream),
        "deterministic": deterministic,
        "variants": results,
        "candidate_repeat_sha256": rep["trades_sha256"],
        "structural_advantage_repeated": structural_repeat,
        "technical_integrity": "PASS" if technical_ok else "FAIL",
        "result": "PASS" if passed else "FAIL",
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    md = ["# Mission 1260 — Out-of-Sample Validation", ""]
    for k in ("result", "technical_integrity", "deterministic",
              "structural_advantage_repeated", "commit", "run_id",
              "config_diff", "mode", "data_period",
              "selection_period_not_overlapping", "tests",
              "signal_stream_events", "duration_seconds"):
        md.append(f"- **{k}**: {summary[k]}")
    md.append("\n## Karşılaştırma (A kontrol vs C aday)\n")
    labels = list(results)
    keys = [k for k in a if k not in ("label", "fatal", "trades_csv")]
    md.append("| metrik | " + " | ".join(labels) + " |")
    md.append("|---|" + "---|" * len(labels))
    for k in keys:
        md.append(f"| {k} | "
                  + " | ".join(str(results[l][k]) for l in labels) + " |")
    SUMMARY_MD.write_text("\n".join(md) + "\n")

    print("\n══════════ MISSION 1260 ÖZETİ ══════════")
    for k, v in summary.items():
        if k != "variants":
            print(f"{k}: {v}")
    print("═════════════════════════════════════════")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
