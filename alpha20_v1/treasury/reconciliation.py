"""
treasury/reconciliation.py — Muhasebe mutabakatı ve kural doğrulaması.

Her mutabakat kontrolü bir CheckResult üretir.
Tüm kontroller bağımsız çalışır — biri başarısız olsa diğerleri devam eder.

Kontrol listesi:
  1. ledger_balance        → Σ(DR) == Σ(CR) — çift kayıt dengesi
  2. cash_balance          → Hesaplanan nakit == Beklenen nakit
  3. position_cost_positive → Açık pozisyon maliyeti > 0
  4. daily_loss_limit      → Günlük kayıp ≤ limit
  5. drawdown_limit        → Drawdown ≤ maksimum
  6. risk_per_trade        → İşlem riski ≤ maksimum
  7. no_negative_balance   → Nakit hiçbir zaman negatif olmaz

Exchange-independent. PAPER modu: gerçek borsa durumu kontrol edilmez.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from .ledger import compute_cash_from_journals
from .precision import q_amount, q_rate, safe_divide, ZERO
from .types import (
    CheckResult, JournalEntry, ReconciliationResult,
)
from .valuation import compute_drawdown_pct, compute_daily_pnl_pct


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# Bireysel kontroller
# ══════════════════════════════════════════════════════════════════════════════

def check_ledger_balance(
    journals: Sequence[JournalEntry],
    tolerance: Decimal = Decimal("0.00000001"),
) -> CheckResult:
    """
    Her journal kaydının dengeli (DR == CR) olduğunu doğrula.

    Kural: Σ(DR tutarları) == Σ(CR tutarları)  ± tolerans
    """
    name = "ledger_balance"
    imbalanced: list[str] = []
    for j in journals:
        if not j.is_balanced(tolerance):
            diff = abs(j.debit_total() - j.credit_total())
            imbalanced.append(f"{j.id}(diff={diff:.8f})")

    passed = len(imbalanced) == 0
    return CheckResult(
        name=name,
        passed=passed,
        expected="Tüm journal kayıtları dengeli",
        actual=f"{len(journals)} journal, {len(imbalanced)} dengesiz: {imbalanced[:3]}",
        message=(
            "✓ Tüm journal kayıtları dengeli." if passed
            else f"✗ {len(imbalanced)} dengeli olmayan journal: {imbalanced[:3]}"
        ),
    )


def check_cash_balance(
    computed_balance: Decimal,
    expected_balance: Decimal,
    tolerance: Decimal = Decimal("0.00000001"),
) -> CheckResult:
    """
    Journal'dan hesaplanan nakit bakiyesi beklenen değere eşit mi?

    Kural: |computed - expected| ≤ tolerans
    """
    name = "cash_balance"
    diff = abs(computed_balance - expected_balance)
    passed = diff <= tolerance
    return CheckResult(
        name=name,
        passed=passed,
        expected=f"{expected_balance:.8f} USDT",
        actual=f"{computed_balance:.8f} USDT (fark={diff:.8f})",
        message=(
            f"✓ Nakit bakiyesi eşleşiyor: {computed_balance:.8f} USDT." if passed
            else f"✗ Nakit uyuşmazlığı: beklenen={expected_balance:.8f}, "
                 f"hesaplanan={computed_balance:.8f}, fark={diff:.8f}"
        ),
    )


def check_position_cost_positive(
    symbol: str,
    cost_basis_usdt: Decimal,
) -> CheckResult:
    """
    Açık pozisyonun maliyet esası pozitif mi?

    Kural: cost_basis_usdt > 0 (pozisyon açıksa)
    """
    name = f"position_cost_positive:{symbol}"
    passed = cost_basis_usdt > ZERO
    return CheckResult(
        name=name,
        passed=passed,
        expected=f"cost_basis > 0 ({symbol})",
        actual=f"{cost_basis_usdt:.8f} USDT",
        message=(
            f"✓ {symbol} maliyet esası pozitif: {cost_basis_usdt:.8f} USDT." if passed
            else f"✗ {symbol} maliyet esası sıfır veya negatif: {cost_basis_usdt:.8f} USDT"
        ),
    )


def check_daily_loss_limit(
    current_balance: Decimal,
    day_start_balance: Decimal,
    limit_pct: Decimal,
) -> CheckResult:
    """
    Günlük kayıp limiti aşılmış mı?

    Kural: |daily_loss_pct| ≤ limit_pct
    Yalnızca kayıplar kontrol edilir; kâr geçer.
    """
    name = "daily_loss_limit"
    daily_pnl_pct = compute_daily_pnl_pct(current_balance, day_start_balance)
    # Negatif pnl = zarar
    loss_pct = q_rate(max(ZERO, -daily_pnl_pct))
    passed = loss_pct <= limit_pct

    return CheckResult(
        name=name,
        passed=passed,
        expected=f"Günlük kayıp ≤ %{limit_pct:.4f}",
        actual=f"Günlük kayıp = %{loss_pct:.4f} (K/Z={daily_pnl_pct:+.4f}%)",
        message=(
            f"✓ Günlük kayıp sınır içinde: %{loss_pct:.4f} ≤ %{limit_pct:.4f}." if passed
            else f"✗ Günlük kayıp limiti aşıldı: %{loss_pct:.4f} > %{limit_pct:.4f}"
        ),
    )


def check_drawdown_limit(
    current_balance: Decimal,
    peak_balance: Decimal,
    max_drawdown_pct: Decimal,
) -> CheckResult:
    """
    Maksimum drawdown aşılmış mı?

    Kural: drawdown_pct ≤ max_drawdown_pct
    """
    name = "drawdown_limit"
    dd_pct = compute_drawdown_pct(current_balance, peak_balance)
    passed = dd_pct <= max_drawdown_pct

    return CheckResult(
        name=name,
        passed=passed,
        expected=f"Drawdown ≤ %{max_drawdown_pct:.4f}",
        actual=f"Drawdown = %{dd_pct:.4f} (peak={peak_balance:.2f}, current={current_balance:.2f})",
        message=(
            f"✓ Drawdown sınır içinde: %{dd_pct:.4f} ≤ %{max_drawdown_pct:.4f}." if passed
            else f"✗ Maksimum drawdown aşıldı: %{dd_pct:.4f} > %{max_drawdown_pct:.4f}"
        ),
    )


def check_risk_per_trade(
    risk_usdt: Decimal,
    balance: Decimal,
    max_risk_pct: Decimal,
) -> CheckResult:
    """
    İşlem başına risk limiti aşılmış mı?

    Kural: (risk_usdt / balance) × 100 ≤ max_risk_pct
    """
    name = "risk_per_trade"
    if balance <= ZERO:
        return CheckResult(
            name=name, passed=False,
            expected=f"risk ≤ %{max_risk_pct:.4f}",
            actual="bakiye sıfır — oran hesaplanamaz",
            message="✗ Bakiye sıfır veya negatif, risk oranı hesaplanamıyor.",
        )
    actual_pct = q_rate(safe_divide(risk_usdt, balance) * Decimal("100"))
    passed = actual_pct <= max_risk_pct

    return CheckResult(
        name=name,
        passed=passed,
        expected=f"risk_pct ≤ %{max_risk_pct:.4f}",
        actual=f"risk_pct = %{actual_pct:.4f} (risk={risk_usdt:.8f}, balance={balance:.2f})",
        message=(
            f"✓ Risk oranı sınır içinde: %{actual_pct:.4f} ≤ %{max_risk_pct:.4f}." if passed
            else f"✗ Risk oranı limiti aşıldı: %{actual_pct:.4f} > %{max_risk_pct:.4f}"
        ),
    )


def check_no_negative_balance(balance: Decimal) -> CheckResult:
    """
    Nakit bakiyesi negatif olamaz.

    Kural: balance >= 0
    PAPER modunda margin call / likidite senaryosu için kritik güvenlik kontrolü.
    """
    name = "no_negative_balance"
    passed = balance >= ZERO
    return CheckResult(
        name=name,
        passed=passed,
        expected="balance ≥ 0",
        actual=f"{balance:.8f} USDT",
        message=(
            f"✓ Bakiye negatif değil: {balance:.8f} USDT." if passed
            else f"✗ Negatif bakiye tespit edildi: {balance:.8f} USDT — kritik hata!"
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Bütünleşik mutabakat
# ══════════════════════════════════════════════════════════════════════════════

def reconcile_all(
    *,
    journals: Sequence[JournalEntry],
    computed_balance: Decimal,
    expected_balance: Decimal,
    current_balance: Decimal,
    day_start_balance: Decimal,
    peak_balance: Decimal,
    risk_usdt: Decimal = ZERO,
    open_position_costs: dict[str, Decimal] | None = None,
    limits: dict | None = None,
    timestamp: datetime | None = None,
) -> ReconciliationResult:
    """
    Tüm mutabakat kontrollerini çalıştır ve özet döndür.

    Args:
        journals:             Tüm journal kayıtları.
        computed_balance:     Journal'lardan hesaplanan nakit.
        expected_balance:     Gerçek (state.json) nakit bakiyesi.
        current_balance:      Güncel nakit (draw-down için).
        day_start_balance:    Günün başındaki bakiye.
        peak_balance:         Tarihin en yüksek bakiyesi.
        risk_usdt:            Mevcut işlem riski (0 = açık pozisyon yok).
        open_position_costs:  {symbol: cost_basis_usdt} — açık pozisyon maliyetleri.
        limits:               Risk limitleri dict (aşağıya bakın).
        timestamp:            Mutabakat zamanı.

    limits dict beklenen anahtarlar:
        daily_loss_limit_pct   → float/Decimal  (varsayılan: 1.0)
        max_drawdown_pct       → float/Decimal  (varsayılan: 5.0)
        max_risk_pct           → float/Decimal  (varsayılan: 0.50)
        cash_tolerance         → Decimal        (varsayılan: 0.00000001)
    """
    lim = limits or {}
    daily_loss_limit = Decimal(str(lim.get("daily_loss_limit_pct", "1.0")))
    max_drawdown     = Decimal(str(lim.get("max_drawdown_pct", "5.0")))
    max_risk         = Decimal(str(lim.get("max_risk_pct", "0.50")))
    cash_tol         = lim.get("cash_tolerance", Decimal("0.00000001"))

    checks: list[CheckResult] = [
        check_ledger_balance(journals),
        check_cash_balance(computed_balance, expected_balance, cash_tol),
        check_no_negative_balance(current_balance),
        check_daily_loss_limit(current_balance, day_start_balance, daily_loss_limit),
        check_drawdown_limit(current_balance, peak_balance, max_drawdown),
    ]

    if risk_usdt > ZERO:
        checks.append(check_risk_per_trade(risk_usdt, current_balance, max_risk))

    for symbol, cost in (open_position_costs or {}).items():
        checks.append(check_position_cost_positive(symbol, cost))

    all_passed = all(c.passed for c in checks)
    return ReconciliationResult(
        passed=all_passed,
        checks=tuple(checks),
        timestamp=timestamp or _now_utc(),
    )
