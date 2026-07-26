"""Sprint 3 — Paper Validation Engine.

Mevcut PAPER motorunun ÜZERİNE salt-okunur bir doğrulama katmanı.
Strateji, risk, treasury, ledger veya borsa koduna dokunmaz.

Bileşenler:
  1. compute_performance_metrics  — kapanan işlemlerden performans metrikleri
  2. build_equity_curve / persist — equity eğrisi üretimi ve kalıcılığı
  3. generate_session_report      — oturum raporu (dict + metin)
  4. run_health_checks            — durum/veri sağlık kontrolleri
  5. run_validation               — --validate modu (hepsini çalıştırır)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
CONFIG_PATH = ROOT / "config.json"
TRADE_HISTORY_PATH = ROOT / "trade_history.json"
EQUITY_CURVE_PATH = ROOT / "equity_curve.json"
SESSION_REPORT_PATH = ROOT / "session_report.json"

log = logging.getLogger("alpha20.validation")


def _atomic_write(path: Path, data: Any) -> None:
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp.replace(path)


# ── 1. Performans metrikleri ──────────────────────────────────────────────────

def compute_performance_metrics(
    trades: list[dict[str, Any]], starting_balance: float
) -> dict[str, Any]:
    """Kapanan işlemlerden salt-okunur performans metrikleri üretir."""
    pnls = [float(t.get("pnl", 0) or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net_pnl = sum(pnls)
    total_fees = sum(float(t.get("fee_usdt", 0) or 0) for t in trades)

    win_rate = len(wins) / len(pnls) * 100 if pnls else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 4) if gross_loss > 0 else None
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = (
        win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss if pnls else 0.0
    )

    # Max drawdown — equity eğrisi üzerinden
    equity = starting_balance
    peak = starting_balance
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)

    # Ardışık seri
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for p in pnls:
        if p > 0:
            cur_win, cur_loss = cur_win + 1, 0
        elif p < 0:
            cur_loss, cur_win = cur_loss + 1, 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    return {
        "total_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "net_pnl": round(net_pnl, 8),
        "gross_win": round(gross_win, 8),
        "gross_loss": round(gross_loss, 8),
        "profit_factor": profit_factor,
        "avg_win": round(avg_win, 8),
        "avg_loss": round(avg_loss, 8),
        "expectancy": round(expectancy, 8),
        "total_fees": round(total_fees, 8),
        "max_drawdown_pct": round(max_dd, 4),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "return_pct": round(net_pnl / starting_balance * 100, 4)
        if starting_balance > 0 else 0.0,
    }


# ── 2. Equity eğrisi ──────────────────────────────────────────────────────────

def build_equity_curve(
    trades: list[dict[str, Any]], starting_balance: float
) -> list[dict[str, Any]]:
    """Her kapanan işlem sonrası equity noktası üretir (salt-okunur)."""
    curve = [{
        "timestamp": None,
        "equity": round(starting_balance, 8),
        "trade_index": 0,
        "symbol": None,
        "pnl": 0.0,
    }]
    equity = starting_balance
    for i, t in enumerate(trades, start=1):
        pnl = float(t.get("pnl", 0) or 0)
        equity += pnl
        curve.append({
            "timestamp": t.get("closed_at"),
            "equity": round(equity, 8),
            "trade_index": i,
            "symbol": t.get("symbol"),
            "pnl": round(pnl, 8),
        })
    return curve


def persist_equity_curve(
    trades: list[dict[str, Any]],
    starting_balance: float,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Equity eğrisini equity_curve.json dosyasına atomik yazar."""
    curve = build_equity_curve(trades, starting_balance)
    _atomic_write(path or EQUITY_CURVE_PATH, curve)
    return curve


# ── 3. Oturum raporu ──────────────────────────────────────────────────────────

def generate_session_report(
    state: dict[str, Any],
    config: dict[str, Any],
    path: Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Oturum raporunu üretir ve (write=True ise) session_report.json'a yazar."""
    trades = state.get("trades", [])
    starting = float(config.get("starting_balance_usdt", 0))
    metrics = compute_performance_metrics(trades, starting)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": config.get("mode"),
        "symbols": config.get("symbols", []),
        "starting_balance": starting,
        "current_balance": round(float(state.get("balance", 0)), 8),
        "open_position": state.get("position"),
        "day": state.get("day"),
        "consecutive_losses": state.get("consecutive_losses", 0),
        "network_errors": state.get("network_errors", 0),
        "metrics": metrics,
    }
    if write:
        _atomic_write(path or SESSION_REPORT_PATH, report)
    return report


def format_session_report(report: dict[str, Any]) -> str:
    m = report["metrics"]
    pos = report["open_position"]
    lines = [
        "══════ PAPER OTURUM RAPORU ══════",
        f"Üretildi: {report['generated_at'][:19]} UTC",
        f"Mod: {report['mode']} | Semboller: {', '.join(report['symbols'])}",
        f"Başlangıç: {report['starting_balance']:.2f} → Güncel: {report['current_balance']:.2f} USDT"
        f" ({m['return_pct']:+.2f}%)",
        f"İşlem: {m['total_trades']} | Kazanç: {m['wins']} | Zarar: {m['losses']}"
        f" | Kazanma oranı: {m['win_rate_pct']}%",
        f"Net PnL: {m['net_pnl']:+.2f} | Fee: {m['total_fees']:.2f}"
        f" | Profit factor: {m['profit_factor'] if m['profit_factor'] is not None else '—'}",
        f"Expectancy: {m['expectancy']:+.4f} | Max drawdown: {m['max_drawdown_pct']}%",
        f"Seriler: {m['max_win_streak']} kazanç / {m['max_loss_streak']} zarar",
        f"Açık pozisyon: {pos['symbol'] + ' ' + pos['side'] if pos else 'YOK'}",
        f"Ağ hatası sayısı: {report['network_errors']}",
        "══════════════════════════════════",
    ]
    return "\n".join(lines)


# ── 4. Sağlık monitörü ────────────────────────────────────────────────────────

def run_health_checks(
    state: dict[str, Any], config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Bağımsız sağlık kontrolleri — hiçbir durumu değiştirmez."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1. PAPER modu
    add("paper_mode", config.get("mode") == "PAPER",
        f"mode={config.get('mode')}")

    # 2. State şeması
    required = ("balance", "day", "day_start_balance", "consecutive_losses",
                "position", "trades")
    missing = [k for k in required if k not in state]
    add("state_schema", not missing,
        "tamam" if not missing else f"eksik alanlar: {missing}")

    # 3. Bakiye tutarlılığı: start + Σpnl == balance
    starting = float(config.get("starting_balance_usdt", 0))
    net = sum(float(t.get("pnl", 0) or 0) for t in state.get("trades", []))
    expected = starting + net
    actual = float(state.get("balance", 0))
    diff = abs(expected - actual)
    add("balance_consistency", diff < 0.01,
        f"beklenen={expected:.4f} gerçek={actual:.4f} fark={diff:.6f}")

    # 4. Bakiye pozitif
    add("balance_positive", actual > 0, f"bakiye={actual:.2f}")

    # 5. Açık pozisyon geçerliliği (SL/TP zorunlu)
    pos = state.get("position")
    if pos is None:
        add("open_position_valid", True, "açık pozisyon yok")
    else:
        valid = (
            pos.get("stop") is not None and pos.get("target") is not None
            and float(pos.get("quantity", 0)) > 0
            and float(pos.get("entry", 0)) > 0
            and pos.get("side") in ("LONG", "SHORT")
        )
        add("open_position_valid", valid,
            f"{pos.get('symbol')} {pos.get('side')} stop={pos.get('stop')} "
            f"target={pos.get('target')}")

    # 6. İşlem kayıtları bütünlüğü
    bad = [
        i for i, t in enumerate(state.get("trades", []))
        if "pnl" not in t or "close_reason" not in t or "fee_usdt" not in t
    ]
    add("trade_records_complete", not bad,
        "tamam" if not bad else f"eksik alanlı işlem indeksleri: {bad}")

    # 7. Ağ hatası birikimi
    net_err = int(state.get("network_errors", 0))
    add("network_errors_low", net_err < 50, f"network_errors={net_err}")

    # 8. trade_history.json ↔ state tutarlılığı (varsa)
    if TRADE_HISTORY_PATH.exists():
        try:
            history = json.loads(TRADE_HISTORY_PATH.read_text())
            state_count = len(state.get("trades", []))
            if not isinstance(history, list):
                add("trade_history_consistent", False,
                    f"beklenmeyen tip: {type(history).__name__}")
            else:
                # History kümülatiftir (reset sonrası state sıfırlanabilir) → >= zorunlu.
                ok = len(history) >= state_count
                add("trade_history_consistent", ok,
                    f"history={len(history)} kayıt, state={state_count} kayıt"
                    + ("" if ok else " — history eksik/kırpılmış!"))
        except (json.JSONDecodeError, OSError) as exc:
            add("trade_history_consistent", False, f"okunamadı: {exc}")
    else:
        state_count = len(state.get("trades", []))
        # Sprint 2 öncesi legacy kayıtlar history'ye yazılmamış olabilir.
        legacy = sum(1 for t in state.get("trades", []) if t.get("legacy_record"))
        ok = state_count == legacy
        add("trade_history_consistent", ok,
            "dosya henüz yok (işlem kapanınca oluşur)" if ok
            else f"dosya yok ama state'te {state_count - legacy} legacy-olmayan işlem var")

    return checks


def health_ok(checks: list[dict[str, Any]]) -> bool:
    return all(c["ok"] for c in checks)


# ── 5. --validate modu ────────────────────────────────────────────────────────

def run_validation(
    state: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    write_files: bool = True,
) -> tuple[bool, dict[str, Any]]:
    """Tüm doğrulama katmanını çalıştırır.

    Dönüş: (ok, {"checks", "report", "equity_points"})
    """
    if config is None:
        config = json.loads(CONFIG_PATH.read_text())
    if state is None:
        if not STATE_PATH.exists():
            print("STATE YOK — motor henüz hiç çalışmadı. Doğrulanacak veri yok.")
            return False, {"checks": [], "report": None, "equity_points": 0}
        state = json.loads(STATE_PATH.read_text())

    checks = run_health_checks(state, config)
    starting = float(config.get("starting_balance_usdt", 0))
    trades = state.get("trades", [])

    if write_files:
        curve = persist_equity_curve(trades, starting)
    else:
        curve = build_equity_curve(trades, starting)
    report = generate_session_report(state, config, write=write_files)

    print("\n══════ SAĞLIK KONTROLLERİ ══════")
    for c in checks:
        mark = "✔" if c["ok"] else "✘"
        print(f"{mark} {c['name']}: {c['detail']}")
    ok = health_ok(checks)
    print(f"Sonuç: {'SAĞLIKLI' if ok else 'SORUN VAR'} "
          f"({sum(1 for c in checks if c['ok'])}/{len(checks)})")

    if report is not None:
        print()
        print(format_session_report(report))
        print(f"\nEquity eğrisi: {len(curve)} nokta → {EQUITY_CURVE_PATH.name}")
        print(f"Oturum raporu → {SESSION_REPORT_PATH.name}")

    return ok, {"checks": checks, "report": report, "equity_points": len(curve)}
