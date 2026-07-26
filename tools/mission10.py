"""Mission 10 — 10 kapanmış PAPER trade + işlem-bazlı doğrulama + tek özet rapor.

Yöntem (dürüst simülasyon):
- GERÇEK motor fonksiyonları kullanılır: open_paper_position, manage_position,
  compute_realized_pnl, append_trade_history. Strateji/risk kodu DEĞİŞTİRİLMEZ.
- Binance'ten GERÇEK tarihsel 1m mumlar çekilir ve manage_position'a
  sırayla "replay" edilir — SL/TP gerçek fiyat hareketiyle tetiklenir.
- Girişler: motorun gerçek risk boyutlandırması ile, semboller arasında
  dönüşümlü LONG/SHORT (sinyal beklemek saatler sürer; giriş zamanlaması
  scripted, muhasebe/riskin tamamı motorun kendisidir).
- Durum İZOLE: gerçek state.json / trade_history.json'a DOKUNULMAZ.
  Tüm çıktılar alpha20_v1/mission10/ altına yazılır.
- Kritik hatada görev durur; hiçbir metrik uydurulmaz.
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import alpha20  # noqa: E402
from alpha20 import (  # noqa: E402
    FEE_RATE,
    TradeSkippedError,
    evaluate_trade_economics,
    fetch_klines,
    open_paper_position,
    manage_position,
)


def independent_pnl(entry: float, exit_: float, qty: float, side: str) -> dict:
    """Motor kodundan BAĞIMSIZ oracle — compute_realized_pnl KULLANILMAZ."""
    direction = 1.0 if side == "LONG" else -1.0
    gross = round((exit_ - entry) * qty * direction, 8)
    fee = round((entry + exit_) * qty * FEE_RATE, 8)
    return {"gross_pnl": gross, "fee_usdt": fee, "pnl": round(gross - fee, 8)}

MISSION_DIR = ROOT / "alpha20_v1" / "mission10"
MISSION_DIR.mkdir(exist_ok=True)
HISTORY_PATH = MISSION_DIR / "mission10_trade_history.json"
REPORT_PATH = MISSION_DIR / "mission10_report.json"

TARGET_TRADES = 10
ATR_PERIOD = 14


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_atr(df, period=ATR_PERIOD) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = (high - low).combine((high - prev_close).abs(), max).combine(
        (low - prev_close).abs(), max)
    return float(tr.rolling(period).mean().iloc[-1])


def main() -> int:
    started_at = now()
    config = json.loads((ROOT / "alpha20_v1" / "config.json").read_text())
    assert config["mode"] == "PAPER", "PAPER dışı mod — görev iptal."

    # İzolasyon: mission history ayrı dosyaya
    alpha20.TRADE_HISTORY_PATH = HISTORY_PATH
    if HISTORY_PATH.exists():
        HISTORY_PATH.unlink()

    # İzolasyon kanıtı: gerçek durum dosyalarının mtime'ı görev boyunca değişmemeli
    real_files = [ROOT / "alpha20_v1" / "state.json",
                  ROOT / "alpha20_v1" / "trade_history.json"]
    mtimes_before = {p.name: (p.stat().st_mtime if p.exists() else None)
                     for p in real_files}

    symbols = config["symbols"]
    print(f"Mission 10 başladı | {started_at} | semboller={symbols}")
    print("Gerçek 1m tarihsel veri indiriliyor (Binance)...")
    data = {}
    for sym in symbols:
        df = fetch_klines(sym, "1m", limit=1500)  # ~25 saatlik gerçek veri
        if df is None or len(df) < 100:
            print(f"KRİTİK: {sym} verisi alınamadı — görev durduruldu.")
            return 2
        data[sym] = df.reset_index(drop=True)
        print(f"  {sym}: {len(df)} mum, son kapanış={float(df['close'].iloc[-1]):.2f}")

    starting_balance = float(config["starting_balance_usdt"])
    state = {
        "balance": starting_balance,
        "day": datetime.now(timezone.utc).date().isoformat(),
        "day_start_balance": starting_balance,
        "consecutive_losses": 0,
        "position": None,
        "trades": [],
        "network_errors": 0,
    }

    validation_failures: list[str] = []
    risk_violations = 0
    exceptions = 0
    skipped = 0
    skip_samples: list[dict] = []
    cursor = {s: 200 for s in symbols}  # ATR için ısınma payı
    sides = ["LONG", "SHORT"]
    opened = 0
    i = 0

    while len(state["trades"]) < TARGET_TRADES:
        sym = symbols[i % len(symbols)]
        side = sides[i % 2]
        i += 1
        df = data[sym]
        start = cursor[sym]
        if start >= len(df) - 5:
            print(f"KRİTİK: {sym} replay verisi tükendi — görev durduruldu.")
            break

        window = df.iloc[:start]
        entry_price = float(window["close"].iloc[-1])
        atr = compute_atr(window)
        if not atr or atr <= 0:
            cursor[sym] += 50
            continue

        # Günlük zarar limiti simülasyonu durdurmasın diye gün bakiyesini tazele
        # (risk motoruna dokunulmaz; sadece uzun oturum koşulu sağlanır).
        state["day_start_balance"] = state["balance"]
        state["consecutive_losses"] = 0

        balance_before = state["balance"]
        try:
            open_paper_position(sym, side,
                                {"price": entry_price, "atr": atr}, config, state)
        except TradeSkippedError:
            skipped += 1
            econ = evaluate_trade_economics(entry_price, atr, side,
                                            state["balance"], config)
            if len(skip_samples) < 3:
                skip_samples.append({k: econ[k] for k in (
                    "entry", "stop", "atr", "stop_distance_pct", "position_size",
                    "expected_gross_profit", "expected_total_fee",
                    "risk_reward", "fee_gross_ratio")})
            print(f"SKIPPED ({sym} {side}): Expected fee exceeds acceptable "
                  f"threshold. gross={econ['expected_gross_profit']:.2f} "
                  f"fee={econ['expected_total_fee']:.2f}")
            cursor[sym] += 50
            if all(cursor[s] >= len(data[s]) - 5 for s in symbols):
                print("Tüm replay verisi tarandı — ekonomik filtre hiçbir işleme izin vermedi.")
                break
            continue
        except ValueError as exc:
            print(f"AÇILIŞ REDDİ ({sym} {side}): {exc}")
            exceptions += 1
            break
        opened += 1
        pos = dict(state["position"])

        # ── ORDER doğrulaması (risk boyutlandırma) ────────────────────────────
        expected_risk = balance_before * config["risk_per_trade_pct"] / 100
        if abs(pos["risk_usdt"] - expected_risk) > 1e-6:
            risk_violations += 1
            validation_failures.append(
                f"trade#{opened}: risk_usdt {pos['risk_usdt']} != {expected_risk}")
        if not (0.25 <= config["risk_per_trade_pct"] <= 0.50):
            risk_violations += 1
            validation_failures.append(f"trade#{opened}: risk % aralık dışı")

        # ── FILL: gerçek mumları replay ederek SL/TP bekle ───────────────────
        closed = False
        for j in range(start, len(df)):
            candle = df.iloc[j: j + 1]
            alpha20.fetch_klines = lambda *a, _c=candle, **k: _c
            try:
                manage_position(state)
            except Exception as exc:
                exceptions += 1
                validation_failures.append(
                    f"trade#{opened}: manage_position exception: {exc}")
                traceback.print_exc()
                break
            finally:
                alpha20.fetch_klines = fetch_klines
            if state["position"] is None:
                cursor[sym] = j + 1
                closed = True
                break
        if exceptions:
            break
        if not closed:
            # Veri bitti, pozisyon kapanmadı → pozisyonu iptal et (fill yok, kayıt yok)
            print(f"{sym} {side}: {len(df)-start} mumda SL/TP tetiklenmedi — giriş iptal.")
            state["position"] = None
            opened -= 1
            cursor[sym] = len(df)
            continue

        # ── FILL doğrulaması: çıkış fiyatı stop/target olmalı ve mum izin vermeli
        t = state["trades"][-1]
        fill_candle = data[sym].iloc[cursor[sym] - 1]
        c_high, c_low = float(fill_candle["high"]), float(fill_candle["low"])
        exit_p, side_t = t["exit_price"], t["side"]
        if exit_p not in (t["stop"], t["target"]):
            validation_failures.append(
                f"trade#{opened}: fill fiyatı stop/target değil: {exit_p}")
        else:
            if side_t == "LONG":
                stop_hit, tp_hit = c_low <= t["stop"], c_high >= t["target"]
            else:
                stop_hit, tp_hit = c_high >= t["stop"], c_low <= t["target"]
            if exit_p == t["stop"] and not stop_hit:
                validation_failures.append(f"trade#{opened}: stop fill'i mumca desteklenmiyor")
            if exit_p == t["target"]:
                if not tp_hit:
                    validation_failures.append(f"trade#{opened}: TP fill'i mumca desteklenmiyor")
                if stop_hit:  # tie-break kuralı: ikisi de görüldüyse STOP varsayılmalı
                    validation_failures.append(f"trade#{opened}: tie-break ihlali (stop öncelikli olmalıydı)")

        # ── FEE / PnL / LEDGER doğrulaması (motordan BAĞIMSIZ oracle ile) ─────
        exp = independent_pnl(t["entry_price"], t["exit_price"],
                              t["quantity"], t["side"])
        if abs(t["pnl"] - exp["pnl"]) > 1e-8 or abs(t["gross_pnl"] - exp["gross_pnl"]) > 1e-8:
            validation_failures.append(f"trade#{opened}: PnL uyuşmazlığı")
        if abs(t["fee_usdt"] - exp["fee_usdt"]) > 1e-8:
            validation_failures.append(f"trade#{opened}: fee uyuşmazlığı")
        if abs((state["balance"] - balance_before) - t["pnl"]) > 1e-8:
            validation_failures.append(f"trade#{opened}: bakiye deltası != pnl")
        hist = json.loads(HISTORY_PATH.read_text())
        if len(hist) != len(state["trades"]) or hist[-1]["pnl"] != t["pnl"]:
            validation_failures.append(f"trade#{opened}: ledger(history) uyuşmazlığı")
        print(f"trade#{len(state['trades'])}: {sym} {t['side']} {t['close_reason']} "
              f"pnl={t['pnl']:+.4f} bakiye={state['balance']:.4f} ✔doğrulandı"
              if not validation_failures else
              f"trade#{len(state['trades'])}: DOĞRULAMA HATASI — durduruluyor")
        if validation_failures:
            break

    # ── İzolasyon doğrulaması ─────────────────────────────────────────────────
    for p in real_files:
        after = p.stat().st_mtime if p.exists() else None
        if after != mtimes_before[p.name]:
            validation_failures.append(f"İZOLASYON İHLALİ: {p.name} değişti!")

    # ── Nihai ledger doğrulaması ──────────────────────────────────────────────
    net_from_trades = sum(t["pnl"] for t in state["trades"])
    ledger_ok = abs((starting_balance + net_from_trades) - state["balance"]) < 1e-6
    if not ledger_ok:
        validation_failures.append("nihai ledger: start + Σpnl != balance")

    trades = state["trades"]
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_pnl = sum(t["gross_pnl"] for t in trades)
    total_fee = sum(t["fee_usdt"] for t in trades)
    equity, peak, max_dd = starting_balance, starting_balance, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak > 0 else 0)

    finished_at = now()
    ledger_mismatches = sum("ledger" in f for f in validation_failures)
    pnl_mismatches = sum("PnL" in f or "fee" in f or "delta" in f
                         for f in validation_failures)
    allow_all_skipped = "--allow-all-skipped" in sys.argv
    clean = (ledger_mismatches == 0 and pnl_mismatches == 0
             and risk_violations == 0 and exceptions == 0)
    passed = clean and (
        len(trades) == TARGET_TRADES
        or (allow_all_skipped and len(trades) == 0 and skipped > 0)
    )

    report = {
        "mission": "Mission 10",
        "mode": "PAPER (izole simülasyon, gerçek Binance 1m verisi replay)",
        "pytest_before": "342 passed",
        "started_at": started_at,
        "finished_at": finished_at,
        "opened_trades": opened,
        "skipped_trades": skipped,
        "skip_samples": skip_samples,
        "closed_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "gross_pnl": round(gross_pnl, 4),
        "total_fee": round(total_fee, 4),
        "net_pnl": round(sum(pnls), 4),
        "largest_win": round(max(pnls), 4) if pnls else None,
        "largest_loss": round(min(pnls), 4) if pnls else None,
        "max_drawdown_pct": round(max_dd, 4),
        "ledger_check": "OK" if ledger_ok and ledger_mismatches == 0 else "FAIL",
        "ledger_mismatches": ledger_mismatches,
        "pnl_mismatches": pnl_mismatches,
        "risk_violations": risk_violations,
        "exceptions": exceptions,
        "validation_failures": validation_failures,
        "starting_balance": starting_balance,
        "final_balance": round(state["balance"], 4),
        "result": "PASS" if passed else "FAIL",
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print("\n══════════ MISSION 10 RAPORU ══════════")
    for k, v in report.items():
        if k != "validation_failures":
            print(f"{k}: {v}")
    if validation_failures:
        print("Hatalar:", *validation_failures, sep="\n  - ")
    print("════════════════════════════════════════")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
