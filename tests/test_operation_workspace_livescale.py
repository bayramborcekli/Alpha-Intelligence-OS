"""Görev 30 — Sharpe/düşüş metriklerinin gerçek ölçekli veriyle
uçtan uca doğrulanması.

Gerçek boyutlu (10k+ kayıt) trade_history.json ve equity_curve.json
dosyaları üretilir; /api/operation-control/workspace/performance
yanıtındaki metrikler modülden BAĞIMSIZ bir hesapla (Fraction
tabanlı kesin aritmetik) karşılaştırılır ve yanıt süresi < 1 sn
olarak ölçülür. Bozuk satır oranı yükseldiğinde dropped_records'un
API yanıtında ve arayüz kodunda görünür kaldığı da test edilir.
"""
import json
import math
import random
import time
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

import app as app_module

SEED = 20260727
TRADE_COUNT = 12_000
EQUITY_COUNT = 10_050
HOLD_MIN, HOLD_MAX = 60, 7_200
SPREAD_SECONDS = 45 * 86_400
WINDOWS = (86_400, 7 * 86_400, 30 * 86_400)
BOUNDARY_GUARD = 7_200  # pencere sınırına bu kadar yakın işlem üretme


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _safe_offset(rng: random.Random, guards) -> int:
    while True:
        off = rng.randint(1, SPREAD_SECONDS)
        if all(abs(off - g) > BOUNDARY_GUARD for g in guards):
            return off


def generate_trades(now: int, count: int, rng: random.Random):
    # UTC gün başlangıcı da bir sınırdır: daily_profit artık kayan
    # 24 saat değil UTC gün penceresi kullandığından, gün dönümüne
    # yakın işlem üretme (test sırasında saat ilerlerse sınıf
    # değiştirmesin).
    day_offset = now % 86_400
    guards = tuple(WINDOWS) + (day_offset,)
    rows = []
    for i in range(count):
        closed = now - _safe_offset(rng, guards)
        hold = rng.randint(HOLD_MIN, HOLD_MAX)
        pnl = round(rng.uniform(-80.0, 100.0), 2)
        fee = round(rng.uniform(0.0, 2.5), 2)
        rows.append({
            "symbol": rng.choice(["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
            "pnl": pnl,
            "fee_usdt": fee,
            "opened_at": _iso(closed - hold),
            "closed_at": _iso(closed),
            "trade_index": i,
        })
    return rows


def generate_equity(now: int, count: int, rng: random.Random):
    rows = []
    equity = 10_000.0
    at = now - count * 60
    for i in range(count):
        equity = round(max(1_000.0,
                           equity * (1 + rng.uniform(-0.004, 0.0042))), 2)
        rows.append({
            "timestamp": _iso(at + i * 60),
            "equity": equity,
            "trade_index": i,
            "symbol": None,
            "pnl": 0.0,
        })
    return rows


# ── Bağımsız hesap (Fraction — modül kodundan ayrı yol) ────────────

def quantize_fraction(fr: Fraction, exp: str) -> Decimal:
    """Half-even yuvarlama ile kesirden Decimal'e — bağımsız yol."""
    step = Fraction(Decimal(exp))
    q, r = divmod(fr, step)
    half = step / 2
    if r > half or (r == half and q % 2):
        q += 1
    return (Decimal(q) * Decimal(exp)).quantize(Decimal(exp))


def independent_metrics(trade_rows, equity_rows, now: int) -> dict:
    nets = []
    holds = []
    window_sums = {w: (Fraction(0), 0) for w in WINDOWS}
    for row in trade_rows:
        if row.get("pnl") is None or not isinstance(
                row.get("closed_at"), str):
            continue
        try:
            net = (Fraction(Decimal(str(row["pnl"])))
                   - Fraction(Decimal(str(row.get("fee_usdt") or 0))))
            closed = int(datetime.fromisoformat(
                row["closed_at"]).timestamp())
        except (ValueError, ArithmeticError):
            continue
        nets.append(net)
        opened = row.get("opened_at")
        if isinstance(opened, str):
            holds.append(closed - int(
                datetime.fromisoformat(opened).timestamp()))
        day_start = (now // 86_400) * 86_400
        for w in WINDOWS:
            # Günlük pencere UTC gün başlangıcından itibaren sayar
            # (utc_day_profit); hafta/ay kayan pencere kalır.
            low = day_start if w == 86_400 else now - w
            if low <= closed <= now:
                total, n = window_sums[w]
                window_sums[w] = (total + net, n + 1)

    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    count = len(nets)
    gross_win = sum(wins, Fraction(0))
    gross_loss = sum(losses, Fraction(0))

    # Özkaynak eğrisi — Sharpe float ile hesaplanır (tolerans
    # karşılaştırması); kesirli ortak payda toplamı 10k noktada
    # pratik değildir.
    eq = [Fraction(Decimal(str(r["equity"]))) for r in equity_rows]
    rets = [(float(b) - float(a)) / float(a)
            for a, b in zip(eq, eq[1:]) if a > 0]
    mean_f = sum(rets) / len(rets)
    var_f = sum((r - mean_f) ** 2 for r in rets) / (len(rets) - 1)
    sharpe_float = mean_f / math.sqrt(var_f)

    peak = eq[0]
    worst = Fraction(0)
    for e in eq:
        if e > peak:
            peak = e
        dd = (peak - e) / peak * 100
        if dd > worst:
            worst = dd

    return {
        "trade_count": count,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": quantize_fraction(
            Fraction(len(wins), count) * 100, "0.01"),
        "average_win": quantize_fraction(
            gross_win / len(wins), "0.00000001"),
        "average_loss": quantize_fraction(
            gross_loss / len(losses), "0.00000001"),
        "profit_factor": quantize_fraction(
            gross_win / -gross_loss, "0.0001"),
        "sharpe_float": sharpe_float,
        "max_drawdown_pct": quantize_fraction(worst, "0.01"),
        "average_hold_seconds": sum(holds) // len(holds),
        "window_profits": {
            w: (quantize_fraction(total, "0.00000001") if n else None)
            for w, (total, n) in window_sums.items()},
    }


# ── Fikstürler ─────────────────────────────────────────────────────

@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c


@pytest.fixture()
def live_dataset(tmp_path, monkeypatch):
    rng = random.Random(SEED)
    now = int(time.time())
    trades = generate_trades(now, TRADE_COUNT, rng)
    equity = generate_equity(now, EQUITY_COUNT, rng)
    data_dir = tmp_path / "alpha20_v1"
    data_dir.mkdir()
    (data_dir / "trade_history.json").write_text(
        json.dumps(trades))
    (data_dir / "equity_curve.json").write_text(
        json.dumps(equity))
    monkeypatch.chdir(tmp_path)
    return {"trades": trades, "equity": equity, "now": now,
            "dir": data_dir}


def _get_performance(client):
    start = time.perf_counter()
    response = client.get(
        "/api/operation-control/workspace/performance")
    elapsed = time.perf_counter() - start
    assert response.status_code == 200
    return response.get_json()["data"]["performance"], elapsed


# ── Uçtan uca doğruluk ─────────────────────────────────────────────

class TestLiveScaleAccuracy:
    def test_metrics_match_independent_computation(
            self, client, live_dataset):
        perf, _ = _get_performance(client)
        expected = independent_metrics(
            live_dataset["trades"], live_dataset["equity"],
            live_dataset["now"])

        assert perf["trade_count"] == expected["trade_count"]
        assert perf["win_count"] == expected["win_count"]
        assert perf["loss_count"] == expected["loss_count"]
        assert perf["dropped_records"] == 0
        assert Decimal(perf["win_rate_pct"]) == \
            expected["win_rate_pct"]
        assert Decimal(perf["average_win"]) == \
            expected["average_win"]
        assert Decimal(perf["average_loss"]) == \
            expected["average_loss"]
        assert Decimal(perf["profit_factor"]) == \
            expected["profit_factor"]
        assert Decimal(perf["max_drawdown_pct"]) == \
            expected["max_drawdown_pct"]
        assert perf["average_hold_seconds"] == \
            expected["average_hold_seconds"]

        # Sharpe: karekök irrasyonel olduğundan bağımsız float
        # hesapla 1e-4 hassasiyet karşılaştırılır (API 0.0001'e
        # nicemler).
        assert perf["sharpe"] is not None
        assert abs(float(Decimal(perf["sharpe"]))
                   - expected["sharpe_float"]) < 5e-4

    def test_period_profits_match(self, client, live_dataset):
        perf, _ = _get_performance(client)
        expected = independent_metrics(
            live_dataset["trades"], live_dataset["equity"],
            live_dataset["now"])
        day, week, month = WINDOWS
        pairs = [("daily_profit", day), ("weekly_profit", week),
                 ("monthly_profit", month)]
        for field, window in pairs:
            want = expected["window_profits"][window]
            got = perf[field]
            if want is None:
                assert got is None, field
            else:
                assert got is not None, field
                assert Decimal(got) == want, field

    def test_equity_curve_downsampled(self, client, live_dataset):
        # Task 35: eğri yanıtta üst sınıra örneklenir; ilk/son
        # gerçek noktalar korunur, metrikler tam veriden gelir.
        from operation_workspace_service import (
            EQUITY_CURVE_MAX_POINTS)
        perf, _ = _get_performance(client)
        curve = perf["equity_curve"]
        assert len(curve) == EQUITY_CURVE_MAX_POINTS
        assert EQUITY_CURVE_MAX_POINTS < EQUITY_COUNT
        equity = live_dataset["equity"]

        def _epoch(row):
            return int(datetime.fromisoformat(
                row["timestamp"]).timestamp())

        assert int(curve[0][0]) == _epoch(equity[0])
        assert int(curve[-1][0]) == _epoch(equity[-1])
        # Örneklenen her nokta gerçek veride birebir var (uydurma yok).
        real = {(_epoch(e), Decimal(str(e["equity"])))
                for e in equity}
        assert all((int(p[0]), Decimal(str(p[1]))) in real
                   for p in curve)

    def test_latency_under_one_second(self, client, live_dataset):
        # Isınma turu sonrası ölç — görev eşiği: < 1 sn.
        _get_performance(client)
        _, elapsed = _get_performance(client)
        assert elapsed < 1.0, f"yanıt {elapsed:.3f} sn"


# ── Bozuk kayıt görünürlüğü ────────────────────────────────────────

CORRUPT_COUNT = 1_500


class TestCorruptionVisibility:
    @pytest.fixture()
    def corrupted(self, live_dataset):
        rng = random.Random(SEED + 1)
        trades = list(live_dataset["trades"])
        variants = [
            lambda r: {**r, "pnl": None},
            lambda r: {**r, "pnl": "garbage"},
            lambda r: {**r, "closed_at": "not-a-date"},
            lambda r: {k: v for k, v in r.items()
                       if k != "closed_at"},
        ]
        idx = rng.sample(range(len(trades)), CORRUPT_COUNT)
        for j, i in enumerate(idx):
            trades[i] = variants[j % len(variants)](trades[i])
        (live_dataset["dir"] / "trade_history.json").write_text(
            json.dumps(trades))
        return trades

    def test_dropped_records_visible_in_api(self, client,
                                            corrupted):
        perf, elapsed = _get_performance(client)
        assert perf["dropped_records"] == CORRUPT_COUNT
        assert perf["trade_count"] == TRADE_COUNT - CORRUPT_COUNT
        # Sağlam kayıtların metrikleri hâlâ hesaplanır.
        assert perf["win_rate_pct"] is not None
        assert perf["sharpe"] is not None
        assert elapsed < 1.0

    def test_non_dict_rows_counted_as_dropped(self, client,
                                              live_dataset):
        # Dosyaya karışan string/null/sayı satırları da sayaçta
        # görünmeli — sessiz atlama yok.
        trades = list(live_dataset["trades"])
        junk = ["garbage-line", None, 42, ["nested"], True]
        trades = junk + trades
        (live_dataset["dir"] / "trade_history.json").write_text(
            json.dumps(trades))
        perf, _ = _get_performance(client)
        assert perf["dropped_records"] == len(junk)
        assert perf["trade_count"] == TRADE_COUNT

    def test_dropped_records_rendered_by_ui(self):
        js = (app_module.ROOT / "static" / "js"
              / "operation_workspace.js").read_text(encoding="utf-8")
        assert "dropped_records" in js
        assert "Düşen Kayıt" in js
