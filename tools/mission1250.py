"""Mission 1250 — STRUCTURAL TRADE ECONOMICS.

Amaç: Sinyal akışına dokunmadan stop mesafesi ↔ fee yükü yapısal ilişkisini
kontrollü ölçmek.

Sabitler (değişmez): gerçek sinyal akışı (skor >= 65), fee_safety_factor=2.0,
risk %0,5, veri dönemi, başlangıç state'i, PAPER kilidi, oracle'lar.

Varyantlar (motor konfigürasyonundan türetildi, tarama YOK):
  A) baseline: atr_stop_multiplier = 1.5 (mevcut config)
  B) geniş 1 : atr_stop_multiplier = 2.25 (baseline × 1.5)
  C) geniş 2 : atr_stop_multiplier = 3.0  (baseline × 2)
  D) baseline çarpan + minimum stop_distance/entry eşiği = %0,4
     (ekonomik filtrenin sf=2.0 ile ima ettiği başabaş stop mesafesi
      ≈ 2·FEE_RATE·sf/rr = %0,2'nin 2 katı; motorun kendi
      evaluate_trade_economics hesabıyla uygulanır, motor kodu değişmez)

Determinizm: sinyal akışı bir kez önceden hesaplanır; baseline varyantı iki kez
koşulur ve işlem-düzeyi SHA256 birebir aynı olmak zorundadır.

MFE/MAE: replay sırasında her işlem için giriş→çıkış mumlarından R cinsinden
en iyi lehte (MFE) ve en kötü aleyhte (MAE) hareket ölçülür (sadece gözlem).

Kullanım: python tools/mission1250.py
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))
sys.path.insert(0, str(ROOT / "tools"))

import pandas as pd  # noqa: E402

import alpha20  # noqa: E402
from alpha20 import (  # noqa: E402
    TradeSkippedError,
    evaluate_trade_economics,
    fetch_klines,
    manage_position,
    open_paper_position,
)
from mission1200 import (  # noqa: E402  (kanıtlanmış harness altyapısı)
    ORACLE_FEE_RATE,
    fetch_paged_klines,
    independent_pnl,
    precompute_signal_stream,
    preflight,
)

OUT = ROOT / "alpha20_v1" / "mission1250"
OUT.mkdir(exist_ok=True)
HISTORY_PATH = OUT / "mission_1250_trade_history.json"
INCIDENTS = OUT / "mission_1250_incidents.jsonl"
SUMMARY_JSON = OUT / "mission_1250_summary.json"
SUMMARY_MD = OUT / "mission_1250_summary.md"

INTERVAL = "15m"
PAGES = 12  # mission1200 ile aynı dönem uzunluğu (~6 ay/sembol)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def incident(kind: str, detail: str, counters: dict) -> None:
    counters[kind] = counters.get(kind, 0) + 1
    with INCIDENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": now(), "kind": kind, "detail": detail},
                           ensure_ascii=False) + "\n")
    print(f"[INCIDENT/{kind}] {detail}")


def run_variant(label: str, atr_mult: float, min_stop_pct: float | None,
                data: dict, stream: list[dict], config: dict) -> dict:
    """Tek varyant: taze state, aynı veri, aynı sinyal akışı.
    atr_mult → config kopyasında atr_stop_multiplier.
    min_stop_pct → None değilse, motorun evaluate_trade_economics çıktısındaki
    stop_distance_pct bu eşiğin altındaki sinyalleri (harness seviyesinde,
    ekonomik filtreden AYRI sayılarak) reddeder."""
    cfg = dict(config, atr_stop_multiplier=atr_mult)
    counters = {"ledger_mismatch": 0, "pnl_mismatch": 0,
                "risk_violation": 0, "warning": 0, "exception": 0}
    starting = float(cfg["starting_balance_usdt"])
    state = {"balance": starting,
             "day": datetime.now(timezone.utc).date().isoformat(),
             "day_start_balance": starting,
             "consecutive_losses": 0, "position": None, "trades": [],
             "network_errors": 0}
    if HISTORY_PATH.exists():
        HISTORY_PATH.unlink()

    eligible = executed = rejected_econ = rejected_minstop = 0
    resume_time = 0
    fatal = None
    excursions = []  # işlem sırasına göre {mfe_r, mae_r}
    stop_pcts, notionals, qtys, risks = [], [], [], []

    for ev in stream:
        if ev["time"] < resume_time:
            continue
        eligible += 1
        sym, side, details, j = ev["symbol"], ev["side"], ev["details"], ev["j"]
        df = data[sym]
        balance_before = state["balance"]
        state["day_start_balance"] = state["balance"]
        state["consecutive_losses"] = 0

        # D varyantı: minimum stop-mesafe eşiği (motorun kendi hesabıyla)
        if min_stop_pct is not None:
            econ = evaluate_trade_economics(details["price"], details["atr"],
                                            side, state["balance"], cfg)
            if econ["stop_distance_pct"] < min_stop_pct:
                rejected_minstop += 1
                continue

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
            incident("risk_violation",
                     f"[{label}] trade#{executed} risk "
                     f"{pos['risk_usdt']}≠{exp_risk}", counters)

        entry = pos["entry"]
        stop_dist = abs(entry - pos["stop"])
        stop_pcts.append(stop_dist / entry * 100)
        notionals.append(entry * pos["quantity"])
        qtys.append(pos["quantity"])
        risks.append(pos["risk_usdt"])

        # Replay + MFE/MAE (R cinsinden) gözlemi
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
                # Çıkış mumu: motorun stop-first tie-break modeliyle tutarlı
                # olarak yalnızca çıkış yönündeki hareket sayılır (stop'ta
                # kapananın aynı mumdaki lehte ucu MFE'ye yazılmaz ve tersi).
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
            for lst in (stop_pcts, notionals, qtys, risks):
                lst.pop()
            incident("warning", f"[{label}] {sym} {side}: veri sonunda SL/TP "
                     "yok — giriş iptal", counters)
            resume_time = 2**63
            continue
        resume_time = int(df["open_time"].iloc[close_idx]) + 1
        excursions.append({"mfe_r": mfe, "mae_r": min(mae, 1.0)})

        # Bağımsız oracle doğrulaması (mission1200 ile aynı)
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
        exp = independent_pnl(t["entry_price"], t["exit_price"], t["quantity"],
                              t["side"])
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

    csv_path = OUT / f"mission_1250_trades_{label}.csv"
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
    digest = hashlib.sha256(json.dumps(
        [[t["symbol"], t["side"], t["entry_price"], t["exit_price"],
          t["quantity"], t["pnl"], t["close_reason"]] for t in trades],
        ensure_ascii=False).encode()).hexdigest()

    def avg(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    return {
        "label": label, "atr_stop_multiplier": atr_mult,
        "min_stop_distance_pct": min_stop_pct,
        "trades_sha256": digest,
        "eligible_signals": eligible,
        "executed_trades": executed,
        "rejected_by_economic_filter": rejected_econ,
        "rejected_by_min_stop_threshold": rejected_minstop,
        "closed_trades": n,
        "avg_stop_distance_pct": avg(stop_pcts),
        "avg_notional_usdt": avg(notionals),
        "avg_position_qty": avg(qtys),
        "avg_gross_winner": avg([t["gross_pnl"] for t in wins]),
        "avg_gross_loser": avg([t["gross_pnl"] for t in losses]),
        "avg_fee_usdt": avg([t["fee_usdt"] for t in trades]),
        "fee_to_gross_profit_ratio": (round(total_fees / gross_profit, 4)
                                      if gross_profit > 0 else None),
        "fee_to_expected_risk_ratio": (round(total_fees / total_risk, 4)
                                       if total_risk > 0 else None),
        "win_rate_pct": round(len(wins) / n * 100, 2) if n else None,
        "net_pnl": round(net, 4),
        "avg_net_pnl_per_trade": avg(pnls),
        "gross_profit_factor": (round(gross_profit / gross_loss, 4)
                          if gross_loss else None),
        "expectancy_r": avg(r_values),
        "max_drawdown_pct": round(max_dd, 4),
        "winners_avg_mae_r": avg([e["mae_r"] for e in win_exc]),
        "losers_avg_mfe_r": avg([e["mfe_r"] for e in loss_exc]),
        "losers_mfe_ge_1r_pct": (round(sum(1 for e in loss_exc
                                           if e["mfe_r"] >= 1.0)
                                       / len(loss_exc) * 100, 2)
                                 if loss_exc else None),
        "final_balance": round(state["balance"], 4),
        "open_positions": 1 if state["position"] else 0,
        "ledger_mismatches": counters["ledger_mismatch"],
        "pnl_mismatches": counters["pnl_mismatch"],
        "risk_violations": counters["risk_violation"],
        "exceptions": counters["exception"],
        "warnings": counters["warning"],
        "fatal": fatal,
        "trades_csv": csv_path.name,
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
        print("KRİTİK: testler geçmedi — Mission 1250 başlatılmadı.")
        return 2

    config = json.loads((ROOT / "alpha20_v1" / "config.json").read_text())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    base_mult = float(config["atr_stop_multiplier"])
    sf = float(config["fee_safety_factor"])
    rr = float(config["reward_risk_ratio"])
    # Ekonomik filtrenin ima ettiği başabaş stop mesafesi (%):
    breakeven_pct = round(2 * ORACLE_FEE_RATE * sf / rr * 100, 4)  # ≈ 0.2
    min_stop_pct = round(breakeven_pct * 2, 4)                     # D eşiği 0.4

    variants = [
        ("A_baseline", base_mult, None),
        ("B_wide1", round(base_mult * 1.5, 4), None),
        ("C_wide2", round(base_mult * 2.0, 4), None),
        ("D_minstop", base_mult, min_stop_pct),
    ]

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
    print(f"Preflight ✔ | sf={sf} sabit | baseline çarpan={base_mult} | "
          f"D eşiği={min_stop_pct}% (başabaş {breakeven_pct}% × 2)")

    run_id = f"M1250-{uuid.uuid4().hex[:10]}"
    print(f"run_id={run_id}")
    print(f"Gerçek {INTERVAL} veri indiriliyor ({PAGES}×1500 mum/sembol)...")
    data = {sym: fetch_paged_klines(sym, INTERVAL, PAGES)
            for sym in config["symbols"]}
    for sym, df in data.items():
        print(f"  {sym}: {len(df)} mum "
              f"({pd.to_datetime(df['open_time'].iloc[0], unit='ms').date()} → "
              f"{pd.to_datetime(df['open_time'].iloc[-1], unit='ms').date()})")
    period = {"from": pd.to_datetime(min(int(d['open_time'].iloc[0])
                                         for d in data.values()),
                                     unit="ms").isoformat(),
              "to": pd.to_datetime(max(int(d['open_time'].iloc[-1])
                                       for d in data.values()),
                                   unit="ms").isoformat()}

    print("Gerçek sinyal akışı önceden hesaplanıyor "
          f"(skor >= {config['minimum_score']})...")
    stream = precompute_signal_stream(data, config)
    print(f"  sinyal akışı: {len(stream)} aday zaman noktası")

    results = {}
    for label, mult, msp in variants:
        print(f"\nVARYANT {label}: atr_mult={mult}"
              + (f" min_stop={msp}%" if msp else ""))
        r = run_variant(label, mult, msp, data, stream, config)
        results[label] = r
        print(f"  {label}: closed={r['closed_trades']} "
              f"net={r['net_pnl']:+.2f} exp_R={r['expectancy_r']} "
              f"fee/risk={r['fee_to_expected_risk_ratio']}")

    # Determinizm kanıtı: baseline ikinci kez koşulur, hash birebir aynı olmalı
    print("\nDeterminizm kanıtı: baseline yeniden koşuluyor...")
    rep = run_variant("A_baseline_repeat", base_mult, None, data, stream,
                      config)
    deterministic = rep["trades_sha256"] == results["A_baseline"]["trades_sha256"]
    if not deterministic:
        incident("exception", "determinizm ihlali: baseline tekrar koşusu "
                 "farklı sonuç verdi", glob)
    print(f"  determinizm: {'OK' if deterministic else 'İHLAL'}")

    for p in real_files:
        after = p.stat().st_mtime if p.exists() else None
        if after != mtimes[p.name]:
            incident("exception", f"İZOLASYON İHLALİ: {p.name} değişti", glob)

    all_res = list(results.values())
    technical_ok = (glob["exception"] == 0 and deterministic and all(
        r["ledger_mismatches"] == 0 and r["pnl_mismatches"] == 0
        and r["risk_violations"] == 0 and r["exceptions"] == 0
        and r["open_positions"] == 0 for r in all_res + [rep]))
    passed = technical_ok and all(r["closed_trades"] > 0 for r in all_res)

    summary = {
        "mission": "Mission 1250 — Structural Trade Economics",
        "commit": commit, "run_id": run_id,
        "mode": "PAPER (izole replay, gerçek sinyal akışı skor>="
                f"{config['minimum_score']}, sf={sf} sabit)",
        "data_period": period, "started_at": started_at,
        "finished_at": now(),
        "duration_seconds": round(time.time() - t0, 1), "tests": test_line,
        "signal_stream_events": len(stream),
        "economic_breakeven_stop_pct": breakeven_pct,
        "variant_definitions": {l: {"atr_stop_multiplier": m,
                                    "min_stop_distance_pct": s}
                                for l, m, s in variants},
        "deterministic": deterministic,
        "variants": results,
        "technical_integrity": "PASS" if technical_ok else "FAIL",
        "result": "PASS" if passed else "FAIL",
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    md = ["# Mission 1250 — Structural Trade Economics", ""]
    for k in ("result", "technical_integrity", "deterministic", "commit",
              "run_id", "mode", "data_period", "tests",
              "signal_stream_events", "economic_breakeven_stop_pct",
              "duration_seconds"):
        md.append(f"- **{k}**: {summary[k]}")
    md.append("\n## Varyant Karşılaştırması\n")
    labels = list(results)
    keys = [k for k in results[labels[0]]
            if k not in ("label", "trades_sha256", "fatal", "trades_csv")]
    md.append("| metrik | " + " | ".join(labels) + " |")
    md.append("|---|" + "---|" * len(labels))
    for k in keys:
        md.append(f"| {k} | "
                  + " | ".join(str(results[l][k]) for l in labels) + " |")
    SUMMARY_MD.write_text("\n".join(md) + "\n")

    print("\n══════════ MISSION 1250 ÖZETİ ══════════")
    for k, v in summary.items():
        if k != "variants":
            print(f"{k}: {v}")
    print("═════════════════════════════════════════")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
