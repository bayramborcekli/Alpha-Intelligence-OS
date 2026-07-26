"""
treasury — Alpha Intelligence OS Muhasebe Sözleşmesi

Bu paket Treasury Engine'in çekirdek domain sözleşmesini ve
değiştirilemez veri yapılarını içerir.

Exchange-independent tasarım:
  Hiçbir modül borsa adı veya API uç noktası içermez.
  Tüm dış bağımlılıklar (fiyat, miktar) enjekte edilir.

PAPER kilidi:
  Bu paket gerçek emir göndermez; yalnızca muhasebe kayıtlarını yönetir.

Float politikası:
  float yalnızca giriş sınırında (from_float, to_decimal) kabul edilir ve
  Decimal(str(value)) ile normalize edilir. İç hesaplamalar tamamen Decimal'dir.

Decimal context:
  Bu modül import edildiğinde global Decimal context değiştirilmez.
  Tüm yuvarlama açık quantize() çağrılarıyla yapılır (ROUND_HALF_EVEN).

Alt modüller:
  precision      → Sabit ondalık hassasiyet (Decimal)
  types          → Tüm domain tipleri (enum + frozen dataclass)
  ledger         → Çift kayıtlı muhasebe + journal şablonları
  cost_basis     → Ağırlıklı ortalama maliyet esası (WAVG)
  fees           → İşlem ücreti hesaplama ve birikimi
  transfer       → Transfer yaşam döngüsü durum makinesi
  valuation      → Mark-to-market pozisyon ve portföy değerleme
  reconciliation → Muhasebe mutabakatı ve kural doğrulaması
"""
from __future__ import annotations

# ── Hassasiyet ────────────────────────────────────────────────────────────────
from .precision import (
    QUANT_AMOUNT, QUANT_QTY, QUANT_PRICE, QUANT_RATE, QUANT_DISPLAY,
    ZERO, ONE,
    from_float, to_decimal,
    q_amount, q_qty, q_price, q_rate, q_display,
    safe_divide, pct_of, abs_amount,
)

# ── Domain tipleri ────────────────────────────────────────────────────────────
from .types import (
    AccountType, EntryType, TradeSide, TradeResult,
    TransferType, TransferStatus, FeeType, CostBasisMethod,
    LedgerLine, JournalEntry,
    TradeRecord, CostBasisLot, FeeRecord, Transfer,
    PositionValuation, PortfolioValuation,
    CheckResult, ReconciliationResult,
)

# ── Defter (Ledger) ───────────────────────────────────────────────────────────
from .ledger import (
    LedgerImbalanceError,
    account_cash, account_position, account_realized_pnl,
    account_fee_expense,
    account_funding_expense, account_funding_income, account_funding,
    validate_journal,
    build_position_open_journal,
    build_position_close_journal,
    build_fee_journal,
    build_funding_journal,
    compute_account_balance,
    compute_cash_from_journals,
    compute_realized_pnl_from_journals,
    compute_total_fees_from_journals,
    get_ledger_summary,
)

# ── Maliyet esası ─────────────────────────────────────────────────────────────
from .cost_basis import (
    CostBasisError,
    DEFAULT_TAKER_RATE, DEFAULT_MAKER_RATE,
    compute_weighted_average,
    add_lot_and_recompute,
    consume_lots_wavg,
    compute_realized_pnl,
    compute_unrealized_pnl,
    apply_fee_to_cost_basis,
    compute_fee_inclusive_cost,
)

# ── Ücret ─────────────────────────────────────────────────────────────────────
from .fees import (
    FeeComputationError,
    STANDARD_RATES,
    compute_fee_usdt,
    compute_fee_for_trade,
    standard_rate,
    FeeAccumulator,
)

# ── Transfer yaşam döngüsü ────────────────────────────────────────────────────
from .transfer import (
    TransitionError,
    VALID_TRANSITIONS, TERMINAL_STATUSES,
    can_transition, is_terminal, allowed_next_states,
    transition, settle, fail,
    create_position_open_transfer,
    create_position_close_transfer,
    create_fee_transfer,
)

# ── Değerleme ─────────────────────────────────────────────────────────────────
from .valuation import (
    ValuationError,
    valuate_position,
    valuate_portfolio,
    compute_drawdown_pct,
    compute_daily_pnl_pct,
    compute_position_weight,
)

# ── Mutabakat ─────────────────────────────────────────────────────────────────
from .reconciliation import (
    check_ledger_balance,
    check_cash_balance,
    check_position_cost_positive,
    check_daily_loss_limit,
    check_drawdown_limit,
    check_risk_per_trade,
    check_no_negative_balance,
    reconcile_all,
)

__all__ = [
    # Hassasiyet
    "QUANT_AMOUNT", "QUANT_QTY", "QUANT_PRICE", "QUANT_RATE", "QUANT_DISPLAY",
    "ZERO", "ONE",
    "from_float", "to_decimal",
    "q_amount", "q_qty", "q_price", "q_rate", "q_display",
    "safe_divide", "pct_of", "abs_amount",
    # Tipler
    "AccountType", "EntryType", "TradeSide", "TradeResult",
    "TransferType", "TransferStatus", "FeeType", "CostBasisMethod",
    "LedgerLine", "JournalEntry",
    "TradeRecord", "CostBasisLot", "FeeRecord", "Transfer",
    "PositionValuation", "PortfolioValuation",
    "CheckResult", "ReconciliationResult",
    # Defter
    "LedgerImbalanceError",
    "account_cash", "account_position", "account_realized_pnl",
    "account_fee_expense",
    "account_funding_expense", "account_funding_income", "account_funding",
    "validate_journal",
    "build_position_open_journal", "build_position_close_journal",
    "build_fee_journal", "build_funding_journal",
    "compute_account_balance",
    "compute_cash_from_journals", "compute_realized_pnl_from_journals",
    "compute_total_fees_from_journals", "get_ledger_summary",
    # Maliyet esası
    "CostBasisError",
    "DEFAULT_TAKER_RATE", "DEFAULT_MAKER_RATE",
    "compute_weighted_average", "add_lot_and_recompute", "consume_lots_wavg",
    "compute_realized_pnl", "compute_unrealized_pnl",
    "apply_fee_to_cost_basis", "compute_fee_inclusive_cost",
    # Ücret
    "FeeComputationError", "STANDARD_RATES",
    "compute_fee_usdt", "compute_fee_for_trade", "standard_rate",
    "FeeAccumulator",
    # Transfer
    "TransitionError",
    "VALID_TRANSITIONS", "TERMINAL_STATUSES",
    "can_transition", "is_terminal", "allowed_next_states",
    "transition", "settle", "fail",
    "create_position_open_transfer", "create_position_close_transfer",
    "create_fee_transfer",
    # Değerleme
    "ValuationError",
    "valuate_position", "valuate_portfolio",
    "compute_drawdown_pct", "compute_daily_pnl_pct", "compute_position_weight",
    # Mutabakat
    "check_ledger_balance", "check_cash_balance",
    "check_position_cost_positive", "check_daily_loss_limit",
    "check_drawdown_limit", "check_risk_per_trade", "check_no_negative_balance",
    "reconcile_all",
]
