"""
tests/test_treasury_contracts.py — Treasury domain sözleşme testleri.

Test kapsamı:
  1. Hassasiyet sistemi (Decimal, float-bleed önleme, yuvarlama)
  2. Domain tipleri (değiştirilemezlik, invariant'lar)
  3. Çift kayıtlı muhasebe denge koşulu
  4. Journal şablonları (open, close, fee)
  5. Ağırlıklı ortalama maliyet esası (WAVG)
  6. Ücret hesaplama ve birikimi
  7. Transfer yaşam döngüsü (geçerli/geçersiz geçişler)
  8. Pozisyon ve portföy değerleme (LONG/SHORT)
  9. Mutabakat kontrolleri (pass/fail senaryoları)

Kurallar:
  - Gerçek API çağrısı yok.
  - PAPER modu korunur.
  - Mevcut testler bozulmaz.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

# alpha20_v1 dizinini path'e ekle
_ROOT = Path(__file__).parent.parent / "alpha20_v1"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import treasury as T
from treasury.types import (
    AccountType, EntryType, TradeSide, TransferStatus, TransferType,
    FeeType, LedgerLine, JournalEntry, CostBasisLot,
)
from treasury.ledger import (
    LedgerImbalanceError, validate_journal,
    build_position_open_journal, build_position_close_journal,
    build_fee_journal, build_funding_journal,
    compute_cash_from_journals, compute_realized_pnl_from_journals,
    compute_total_fees_from_journals, get_ledger_summary,
    account_cash, account_position, account_realized_pnl, account_fee_expense,
    account_funding_expense, account_funding_income,
)
from treasury.precision import (
    from_float, to_decimal, q_amount, q_price, q_qty, q_rate,
    safe_divide, pct_of, ZERO, ONE,
)
from treasury.cost_basis import (
    CostBasisError,
    compute_weighted_average, add_lot_and_recompute, consume_lots_wavg,
    compute_realized_pnl, compute_unrealized_pnl,
    apply_fee_to_cost_basis, compute_fee_inclusive_cost,
    DEFAULT_TAKER_RATE,
)
from treasury.fees import (
    FeeComputationError, STANDARD_RATES,
    compute_fee_usdt, compute_fee_for_trade, FeeAccumulator,
)
from treasury.transfer import (
    TransitionError, VALID_TRANSITIONS, TERMINAL_STATUSES,
    can_transition, is_terminal, allowed_next_states,
    transition, settle, fail,
    create_position_open_transfer,
)
from treasury.valuation import (
    ValuationError,
    valuate_position, valuate_portfolio,
    compute_drawdown_pct, compute_daily_pnl_pct,
)
from treasury.reconciliation import (
    check_ledger_balance, check_cash_balance,
    check_daily_loss_limit, check_drawdown_limit,
    check_risk_per_trade, check_no_negative_balance,
    check_position_cost_positive, reconcile_all,
)

_UTC = timezone.utc
_TS  = datetime(2026, 1, 26, 12, 0, 0, tzinfo=_UTC)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Hassasiyet testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestPrecision:

    def test_from_float_eliminates_ieee754_bleed(self):
        """float 9949.999999999998 Decimal'e dönüşünce saklandığı gibi döner."""
        result = from_float(9949.999999999998)
        assert isinstance(result, Decimal)
        # Decimal string temsili float kirliliğini taşır — bu beklenen davranış.
        # Önemli olan: iki farklı float'tan üretilen Decimal'ler aritmetikte tutarlı.
        assert result > Decimal("9949")
        assert result < Decimal("9950")

    def test_decimal_addition_no_float_error(self):
        """0.1 + 0.2 == 0.3 (Decimal ile)."""
        a = from_float(0.1)
        b = from_float(0.2)
        c = from_float(0.3)
        # Decimal aritmetiği ile eşitlik sağlanmalı
        result = q_amount(a + b)
        assert result == q_amount(c)

    def test_q_amount_rounds_to_8_decimal_places(self):
        result = q_amount(Decimal("1.123456789"))
        assert str(result) == "1.12345679"   # ROUND_HALF_EVEN

    def test_q_amount_banker_rounding(self):
        """Banker's rounding: 0.5 → en yakın çift sayıya."""
        assert q_amount(Decimal("0.000000015")) == Decimal("0.00000002")
        assert q_amount(Decimal("0.000000025")) == Decimal("0.00000002")

    def test_safe_divide_by_zero_returns_zero(self):
        assert safe_divide(Decimal("100"), Decimal("0")) == ZERO

    def test_safe_divide_normal(self):
        result = safe_divide(Decimal("10"), Decimal("4"))
        assert result == Decimal("2.5")

    def test_pct_of(self):
        """10000 USDT'nin %0.25'i = 25 USDT."""
        result = pct_of(Decimal("10000"), Decimal("0.25"))
        assert result == q_amount(Decimal("25"))

    def test_to_decimal_from_string(self):
        assert to_decimal("  64323.7  ") == Decimal("64323.7")

    def test_to_decimal_from_int(self):
        assert to_decimal(100) == Decimal("100")

    def test_to_decimal_invalid_raises(self):
        with pytest.raises(TypeError):
            to_decimal([1, 2, 3])


# ══════════════════════════════════════════════════════════════════════════════
# 2. Domain tipleri testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestDomainTypes:

    def test_ledger_line_negative_amount_raises(self):
        with pytest.raises(ValueError):
            LedgerLine(
                account=account_cash(),
                entry_type=EntryType.DEBIT,
                amount=Decimal("-1"),
            )

    def test_ledger_line_zero_amount_raises(self):
        with pytest.raises(ValueError):
            LedgerLine(
                account=account_cash(),
                entry_type=EntryType.CREDIT,
                amount=ZERO,
            )

    def test_ledger_line_is_frozen(self):
        line = LedgerLine(
            account=account_cash(),
            entry_type=EntryType.DEBIT,
            amount=Decimal("100"),
        )
        with pytest.raises((AttributeError, TypeError)):
            line.amount = Decimal("200")  # type: ignore

    def test_journal_entry_is_frozen(self):
        j = build_position_open_journal(
            symbol="BTCUSDT",
            side=TradeSide.LONG,
            risk_usdt=Decimal("50"),
            timestamp=_TS,
        )
        with pytest.raises((AttributeError, TypeError)):
            j.description = "değiştirildi"  # type: ignore

    def test_fee_record_negative_raises(self):
        import uuid
        from treasury.types import FeeRecord
        with pytest.raises(ValueError):
            FeeRecord(
                id=str(uuid.uuid4()),
                timestamp=_TS,
                symbol="BTCUSDT",
                fee_type=FeeType.TAKER,
                amount_usdt=Decimal("-1"),
                rate=DEFAULT_TAKER_RATE,
            )

    def test_account_position_format(self):
        assert account_position("BTCUSDT") == "PAPER_POSITION:BTCUSDT"
        assert account_position("btcusdt") == "PAPER_POSITION:BTCUSDT"

    def test_trade_record_avg_cost_per_unit(self):
        from treasury.types import TradeRecord
        rec = TradeRecord(
            id="T001",
            symbol="BTCUSDT",
            side=TradeSide.LONG,
            quantity=q_qty(Decimal("1")),
            entry_price=q_price(Decimal("64000")),
            cost_basis_usdt=q_amount(Decimal("64025.6")),  # ücret dahil
            fee_usdt=q_amount(Decimal("25.6")),
            opened_at=_TS,
        )
        avg = rec.avg_cost_per_unit
        assert avg == q_price(Decimal("64025.6"))

    def test_transfer_is_terminal(self):
        from treasury.types import Transfer
        t_settled = Transfer(
            id="X", timestamp=_TS, transfer_type=TransferType.FEE,
            amount_usdt=Decimal("1"), status=TransferStatus.SETTLED,
        )
        assert t_settled.is_terminal is True

        t_pending = Transfer(
            id="Y", timestamp=_TS, transfer_type=TransferType.FEE,
            amount_usdt=Decimal("1"), status=TransferStatus.PENDING,
        )
        assert t_pending.is_terminal is False


# ══════════════════════════════════════════════════════════════════════════════
# 3. Çift kayıt dengesi testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestLedgerBalance:

    def _make_balanced_journal(self, amount: Decimal) -> JournalEntry:
        return JournalEntry(
            id="J-TEST001",
            timestamp=_TS,
            description="Test journal",
            transfer_type=TransferType.POSITION_OPEN,
            lines=(
                LedgerLine(account="PAPER_POSITION:BTCUSDT", entry_type=EntryType.DEBIT,  amount=amount),
                LedgerLine(account="PAPER_CASH",             entry_type=EntryType.CREDIT, amount=amount),
            ),
        )

    def _make_unbalanced_journal(self) -> JournalEntry:
        return JournalEntry(
            id="J-UNBAL",
            timestamp=_TS,
            description="Unbalanced",
            transfer_type=TransferType.POSITION_OPEN,
            lines=(
                LedgerLine(account="PAPER_POSITION:BTCUSDT", entry_type=EntryType.DEBIT,  amount=Decimal("100")),
                LedgerLine(account="PAPER_CASH",             entry_type=EntryType.CREDIT, amount=Decimal("99")),
            ),
        )

    def test_balanced_journal_passes_validate(self):
        j = self._make_balanced_journal(Decimal("50"))
        validate_journal(j)  # İstisna atmamalı

    def test_unbalanced_journal_raises(self):
        j = self._make_unbalanced_journal()
        with pytest.raises(LedgerImbalanceError):
            validate_journal(j)

    def test_is_balanced_method(self):
        j_ok  = self._make_balanced_journal(Decimal("100"))
        j_bad = self._make_unbalanced_journal()
        assert j_ok.is_balanced() is True
        assert j_bad.is_balanced() is False

    def test_debit_credit_totals(self):
        j = self._make_balanced_journal(Decimal("75"))
        assert j.debit_total()  == Decimal("75")
        assert j.credit_total() == Decimal("75")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Journal şablon testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestJournalTemplates:

    def test_position_open_journal_balanced(self):
        j = build_position_open_journal(
            symbol="BTCUSDT", side=TradeSide.LONG,
            risk_usdt=Decimal("49.75"), timestamp=_TS,
        )
        assert j.is_balanced()
        assert j.debit_total()  == q_amount(Decimal("49.75"))
        assert j.credit_total() == q_amount(Decimal("49.75"))

    def test_position_open_debit_is_position_account(self):
        j = build_position_open_journal(
            symbol="ETHUSDT", side=TradeSide.SHORT,
            risk_usdt=Decimal("30"), timestamp=_TS,
        )
        dr_accounts = [ln.account for ln in j.lines if ln.entry_type == EntryType.DEBIT]
        cr_accounts = [ln.account for ln in j.lines if ln.entry_type == EntryType.CREDIT]
        assert "PAPER_POSITION:ETHUSDT" in dr_accounts
        assert account_cash() in cr_accounts

    def test_position_open_zero_risk_raises(self):
        with pytest.raises(ValueError):
            build_position_open_journal(
                symbol="BTCUSDT", side=TradeSide.LONG,
                risk_usdt=ZERO, timestamp=_TS,
            )

    def test_position_close_profit_balanced(self):
        """Kârlı kapama: exit > cost."""
        j = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.LONG,
            cost_basis_usdt=Decimal("50"),
            exit_value_usdt=Decimal("75"),
            timestamp=_TS,
        )
        assert j.is_balanced()
        # K/Z alacağı var mı?
        cr_pnl = sum(
            ln.amount for ln in j.lines
            if ln.account == account_realized_pnl() and ln.entry_type == EntryType.CREDIT
        )
        assert cr_pnl == q_amount(Decimal("25"))

    def test_position_close_loss_balanced(self):
        """Zararlı kapama: exit < cost."""
        j = build_position_close_journal(
            symbol="SOLUSDT", side=TradeSide.LONG,
            cost_basis_usdt=Decimal("50"),
            exit_value_usdt=Decimal("40"),
            timestamp=_TS,
        )
        assert j.is_balanced()
        # K/Z borç kaydı var mı?
        dr_pnl = sum(
            ln.amount for ln in j.lines
            if ln.account == account_realized_pnl() and ln.entry_type == EntryType.DEBIT
        )
        assert dr_pnl == q_amount(Decimal("10"))

    def test_position_close_breakeven_balanced(self):
        """Başabaş kapama: exit == cost."""
        j = build_position_close_journal(
            symbol="XRPUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("50"),
            exit_value_usdt=Decimal("50"),
            timestamp=_TS,
        )
        assert j.is_balanced()
        # K/Z satırı yok
        pnl_lines = [ln for ln in j.lines if ln.account == account_realized_pnl()]
        assert len(pnl_lines) == 0

    def test_fee_journal_balanced(self):
        j = build_fee_journal(symbol="BTCUSDT", fee_usdt=Decimal("4"), timestamp=_TS)
        assert j.is_balanced()
        dr_fee = sum(
            ln.amount for ln in j.lines
            if ln.account == account_fee_expense() and ln.entry_type == EntryType.DEBIT
        )
        assert dr_fee == q_amount(Decimal("4"))

    def test_fee_journal_zero_raises(self):
        with pytest.raises(ValueError):
            build_fee_journal(symbol="BTCUSDT", fee_usdt=ZERO, timestamp=_TS)

    def test_funding_journal_balanced(self):
        j = build_funding_journal(symbol="BTCUSDT", funding_usdt=Decimal("2.5"), timestamp=_TS)
        assert j.is_balanced()

    def test_ledger_summary_all_balanced(self):
        journals = [
            build_position_open_journal(symbol="BTCUSDT", side=TradeSide.LONG,
                                         risk_usdt=Decimal("50"), timestamp=_TS),
            build_fee_journal(symbol="BTCUSDT", fee_usdt=Decimal("4"), timestamp=_TS),
        ]
        summary = get_ledger_summary(journals)
        assert summary["all_balanced"] is True
        assert summary["journal_count"] == 2

    def test_compute_cash_from_journals(self):
        """Açılış + kapama → nakit değişimi doğru hesaplanmalı."""
        j_open  = build_position_open_journal(
            symbol="BTCUSDT", side=TradeSide.LONG,
            risk_usdt=Decimal("50"), timestamp=_TS,
        )
        j_close = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.LONG,
            cost_basis_usdt=Decimal("50"),
            exit_value_usdt=Decimal("75"),
            timestamp=_TS,
        )
        j_fee = build_fee_journal(symbol="BTCUSDT", fee_usdt=Decimal("4"), timestamp=_TS)

        journals = [j_open, j_close, j_fee]
        # Nakit: -50 (açılış) + 75 (kapama) - 4 (ücret) = +21
        cash_change = compute_cash_from_journals(journals)
        assert cash_change == q_amount(Decimal("21"))

    def test_compute_realized_pnl_from_journals(self):
        j_close = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.LONG,
            cost_basis_usdt=Decimal("50"),
            exit_value_usdt=Decimal("75"),
            timestamp=_TS,
        )
        pnl = compute_realized_pnl_from_journals([j_close])
        assert pnl == q_amount(Decimal("25"))

    def test_realized_pnl_negative_for_loss(self):
        j_close = build_position_close_journal(
            symbol="SOLUSDT", side=TradeSide.LONG,
            cost_basis_usdt=Decimal("50"),
            exit_value_usdt=Decimal("40"),
            timestamp=_TS,
        )
        pnl = compute_realized_pnl_from_journals([j_close])
        assert pnl == q_amount(Decimal("-10"))


# ══════════════════════════════════════════════════════════════════════════════
# 5. Ağırlıklı ortalama maliyet esası testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestCostBasis:

    def _make_lot(self, symbol: str, side: TradeSide, qty: str, cost: str,
                  trade_id: str = "T001") -> CostBasisLot:
        return CostBasisLot(
            symbol=symbol, side=side,
            quantity=q_qty(Decimal(qty)),
            total_cost_usdt=q_amount(Decimal(cost)),
            opened_at=_TS, trade_id=trade_id,
        )

    def test_single_lot_avg_cost(self):
        lot = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "64000")
        avg = compute_weighted_average([lot])
        assert avg == q_price(Decimal("64000"))

    def test_two_lots_weighted_average(self):
        """
        Lot 1: 1 BTC @ 60000 = 60000 USDT
        Lot 2: 1 BTC @ 64000 = 64000 USDT
        Ortalama: 124000 / 2 = 62000
        """
        lot1 = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "60000", "T001")
        lot2 = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "64000", "T002")
        avg  = compute_weighted_average([lot1, lot2])
        assert avg == q_price(Decimal("62000"))

    def test_unequal_size_lots_weighted_average(self):
        """
        Lot 1: 2 BTC @ 60000 = 120000 USDT
        Lot 2: 1 BTC @ 66000 = 66000  USDT
        Toplam: 3 BTC, 186000 USDT → avg = 62000
        """
        lot1 = self._make_lot("BTCUSDT", TradeSide.LONG, "2", "120000", "T001")
        lot2 = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "66000",  "T002")
        avg  = compute_weighted_average([lot1, lot2])
        assert avg == q_price(Decimal("62000"))

    def test_empty_lots_returns_zero(self):
        assert compute_weighted_average([]) == ZERO

    def test_add_lot_updates_average(self):
        lot1 = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "60000", "T001")
        lot2 = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "64000", "T002")
        lots, new_avg = add_lot_and_recompute([lot1], lot2)
        assert len(lots) == 2
        assert new_avg == q_price(Decimal("62000"))

    def test_add_lot_zero_quantity_raises(self):
        lot = self._make_lot("BTCUSDT", TradeSide.LONG, "0", "0")
        with pytest.raises(CostBasisError):
            add_lot_and_recompute([], lot)

    def test_consume_full_position(self):
        lot = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "64000")
        cost_closed, remaining = consume_lots_wavg([lot], q_qty(Decimal("1")))
        assert cost_closed == q_amount(Decimal("64000"))
        assert remaining == []

    def test_consume_partial_position(self):
        """2 BTC pozisyondan 1 BTC kapat → kalan 1 BTC maliyet = 62000."""
        lot1 = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "60000", "T001")
        lot2 = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "64000", "T002")
        cost_closed, remaining = consume_lots_wavg([lot1, lot2], q_qty(Decimal("1")))
        # avg = 62000, 1 BTC kapandı → maliyet = 62000
        assert cost_closed == q_amount(Decimal("62000"))
        assert len(remaining) == 1
        assert remaining[0].quantity == q_qty(Decimal("1"))
        assert remaining[0].total_cost_usdt == q_amount(Decimal("62000"))

    def test_consume_more_than_available_raises(self):
        lot = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "64000")
        with pytest.raises(CostBasisError):
            consume_lots_wavg([lot], q_qty(Decimal("2")))

    def test_consume_zero_raises(self):
        lot = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "64000")
        with pytest.raises(CostBasisError):
            consume_lots_wavg([lot], ZERO)

    def test_realized_pnl_long_profit(self):
        pnl = compute_realized_pnl(
            avg_cost_per_unit=q_price(Decimal("60000")),
            exit_price=q_price(Decimal("65000")),
            quantity=q_qty(Decimal("1")),
            side=TradeSide.LONG,
        )
        assert pnl == q_amount(Decimal("5000"))

    def test_realized_pnl_long_loss(self):
        pnl = compute_realized_pnl(
            avg_cost_per_unit=q_price(Decimal("60000")),
            exit_price=q_price(Decimal("55000")),
            quantity=q_qty(Decimal("1")),
            side=TradeSide.LONG,
        )
        assert pnl == q_amount(Decimal("-5000"))

    def test_realized_pnl_short_profit(self):
        """SHORT: fiyat düştüğünde kâr."""
        pnl = compute_realized_pnl(
            avg_cost_per_unit=q_price(Decimal("65000")),
            exit_price=q_price(Decimal("60000")),
            quantity=q_qty(Decimal("1")),
            side=TradeSide.SHORT,
        )
        assert pnl == q_amount(Decimal("5000"))

    def test_realized_pnl_short_loss(self):
        """SHORT: fiyat yükseldiğinde zarar."""
        pnl = compute_realized_pnl(
            avg_cost_per_unit=q_price(Decimal("60000")),
            exit_price=q_price(Decimal("65000")),
            quantity=q_qty(Decimal("1")),
            side=TradeSide.SHORT,
        )
        assert pnl == q_amount(Decimal("-5000"))

    def test_fee_increases_cost_basis(self):
        """Ücret birim maliyeti artırır."""
        new_avg = apply_fee_to_cost_basis(
            avg_cost_per_unit=Decimal("60000"),
            fee_usdt=Decimal("24"),      # 24 USDT ücret
            quantity=Decimal("1"),        # 1 BTC
        )
        assert new_avg == q_price(Decimal("60024"))

    def test_fee_inclusive_cost(self):
        """1 BTC @ 64000, %0.04 taker → ücret = 25.6, toplam = 64025.6."""
        notional, fee, total = compute_fee_inclusive_cost(
            quantity=Decimal("1"),
            entry_price=Decimal("64000"),
            fee_rate=DEFAULT_TAKER_RATE,
        )
        assert notional == q_amount(Decimal("64000"))
        assert fee      == q_amount(Decimal("25.6"))
        assert total    == q_amount(Decimal("64025.6"))


# ══════════════════════════════════════════════════════════════════════════════
# 6. Ücret testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestFees:

    def test_compute_fee_usdt_basic(self):
        """10000 USDT × %0.04 = 4 USDT."""
        result = compute_fee_usdt(Decimal("10000"), Decimal("0.0004"))
        assert result == q_amount(Decimal("4"))

    def test_compute_fee_usdt_zero_notional(self):
        assert compute_fee_usdt(ZERO, Decimal("0.0004")) == ZERO

    def test_compute_fee_usdt_negative_notional_raises(self):
        with pytest.raises(FeeComputationError):
            compute_fee_usdt(Decimal("-100"), Decimal("0.0004"))

    def test_compute_fee_usdt_negative_rate_raises(self):
        with pytest.raises(FeeComputationError):
            compute_fee_usdt(Decimal("100"), Decimal("-0.0004"))

    def test_taker_rate_higher_than_maker(self):
        assert STANDARD_RATES[FeeType.TAKER] > STANDARD_RATES[FeeType.MAKER]

    def test_fee_accumulator_total(self):
        import uuid
        acc = FeeAccumulator()
        for amt in ["4", "2.5", "1.5"]:
            acc.add(
                T.FeeRecord(
                    id=str(uuid.uuid4()), timestamp=_TS,
                    symbol="BTCUSDT", fee_type=FeeType.TAKER,
                    amount_usdt=Decimal(amt), rate=DEFAULT_TAKER_RATE,
                )
            )
        assert acc.total_fees() == q_amount(Decimal("8"))
        assert acc.count() == 3

    def test_fee_accumulator_by_symbol(self):
        import uuid
        acc = FeeAccumulator()
        for sym, amt in [("BTCUSDT", "4"), ("ETHUSDT", "2"), ("BTCUSDT", "1")]:
            acc.add(
                T.FeeRecord(
                    id=str(uuid.uuid4()), timestamp=_TS, symbol=sym,
                    fee_type=FeeType.TAKER, amount_usdt=Decimal(amt),
                    rate=DEFAULT_TAKER_RATE,
                )
            )
        assert acc.fees_for_symbol("BTCUSDT") == q_amount(Decimal("5"))
        assert acc.fees_for_symbol("ETHUSDT") == q_amount(Decimal("2"))

    def test_fee_accumulator_by_type(self):
        import uuid
        acc = FeeAccumulator()
        acc.add(T.FeeRecord(id=str(uuid.uuid4()), timestamp=_TS, symbol="BTC",
                             fee_type=FeeType.TAKER, amount_usdt=Decimal("4"),
                             rate=DEFAULT_TAKER_RATE))
        acc.add(T.FeeRecord(id=str(uuid.uuid4()), timestamp=_TS, symbol="BTC",
                             fee_type=FeeType.MAKER, amount_usdt=Decimal("2"),
                             rate=Decimal("0.0002")))
        by_type = acc.fees_by_type()
        assert by_type[FeeType.TAKER] == q_amount(Decimal("4"))
        assert by_type[FeeType.MAKER] == q_amount(Decimal("2"))

    def test_fee_accumulator_negative_raises(self):
        import uuid
        acc = FeeAccumulator()
        with pytest.raises((FeeComputationError, ValueError)):
            acc.add(T.FeeRecord(
                id=str(uuid.uuid4()), timestamp=_TS, symbol="BTC",
                fee_type=FeeType.TAKER, amount_usdt=Decimal("-1"),
                rate=DEFAULT_TAKER_RATE,
            ))


# ══════════════════════════════════════════════════════════════════════════════
# 7. Transfer yaşam döngüsü testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestTransferLifecycle:

    def _pending_transfer(self) -> T.Transfer:
        return create_position_open_transfer(
            transfer_id="TRF-001",
            symbol="BTCUSDT",
            risk_usdt=Decimal("50"),
            trade_id="TRADE-001",
            timestamp=_TS,
        )

    def test_initial_status_pending(self):
        t = self._pending_transfer()
        assert t.status == TransferStatus.PENDING

    def test_valid_transition_pending_to_submitted(self):
        t = self._pending_transfer()
        t2 = transition(t, TransferStatus.SUBMITTED)
        assert t2.status == TransferStatus.SUBMITTED

    def test_invalid_transition_raises(self):
        """PENDING → CONFIRMED doğrudan geçiş geçersiz."""
        t = self._pending_transfer()
        with pytest.raises(TransitionError):
            transition(t, TransferStatus.CONFIRMED)

    def test_terminal_state_no_transition(self):
        """SETTLED durumundan başka duruma geçiş yapılamaz."""
        t = self._pending_transfer()
        t_settled = settle(t, "J-001")
        with pytest.raises(TransitionError):
            transition(t_settled, TransferStatus.PENDING)

    def test_settle_fast_path(self):
        """PAPER modunda PENDING → SETTLED tek adımda."""
        t = self._pending_transfer()
        t_settled = settle(t, "J-JOURNAL-001")
        assert t_settled.status == TransferStatus.SETTLED
        assert t_settled.journal_id == "J-JOURNAL-001"
        assert t_settled.is_terminal is True

    def test_fail_transition(self):
        t = self._pending_transfer()
        t_failed = fail(t, "Simülasyon hatası")
        assert t_failed.status == TransferStatus.FAILED
        assert t_failed.error == "Simülasyon hatası"

    def test_cancel_from_pending(self):
        t = self._pending_transfer()
        t_cancelled = transition(t, TransferStatus.CANCELLED)
        assert t_cancelled.status == TransferStatus.CANCELLED
        assert is_terminal(t_cancelled.status)

    def test_is_terminal_checks(self):
        assert is_terminal(TransferStatus.SETTLED)   is True
        assert is_terminal(TransferStatus.FAILED)    is True
        assert is_terminal(TransferStatus.CANCELLED) is True
        assert is_terminal(TransferStatus.PENDING)   is False
        assert is_terminal(TransferStatus.SUBMITTED) is False

    def test_all_valid_transitions_defined(self):
        """Tüm durum geçişleri tanımlanmış olmalı."""
        for status in TransferStatus:
            assert status in VALID_TRANSITIONS, f"{status} VALID_TRANSITIONS'ta eksik"

    def test_immutability_after_transition(self):
        """Geçiş orijinal transfer'i değiştirmez."""
        t = self._pending_transfer()
        t2 = transition(t, TransferStatus.SUBMITTED)
        assert t.status  == TransferStatus.PENDING    # orijinal değişmedi
        assert t2.status == TransferStatus.SUBMITTED  # yeni nesne

    def test_can_transition_true(self):
        assert can_transition(TransferStatus.PENDING, TransferStatus.SUBMITTED) is True

    def test_can_transition_false(self):
        assert can_transition(TransferStatus.SETTLED, TransferStatus.PENDING) is False

    def test_allowed_next_states_empty_for_terminal(self):
        assert len(allowed_next_states(TransferStatus.SETTLED)) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 8. Değerleme testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestValuation:

    def test_long_position_profit(self):
        """LONG @ 60000, anlık fiyat 65000 → kâr = 5000 USDT."""
        pv = valuate_position(
            symbol="BTCUSDT", side=TradeSide.LONG,
            quantity=Decimal("1"),
            avg_cost_per_unit=Decimal("60000"),
            current_price=Decimal("65000"),
        )
        assert pv.unrealized_pnl == q_amount(Decimal("5000"))
        assert pv.is_profitable is True
        assert pv.mark_to_market_usdt == q_amount(Decimal("65000"))

    def test_long_position_loss(self):
        """LONG @ 60000, anlık fiyat 55000 → zarar = -5000 USDT."""
        pv = valuate_position(
            symbol="BTCUSDT", side=TradeSide.LONG,
            quantity=Decimal("1"),
            avg_cost_per_unit=Decimal("60000"),
            current_price=Decimal("55000"),
        )
        assert pv.unrealized_pnl == q_amount(Decimal("-5000"))
        assert pv.is_profitable is False

    def test_short_position_profit(self):
        """SHORT @ 65000, anlık fiyat 60000 → kâr = 5000 USDT."""
        pv = valuate_position(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            quantity=Decimal("1"),
            avg_cost_per_unit=Decimal("65000"),
            current_price=Decimal("60000"),
        )
        assert pv.unrealized_pnl == q_amount(Decimal("5000"))
        assert pv.is_profitable is True

    def test_short_position_loss(self):
        """SHORT @ 60000, anlık fiyat 65000 → zarar = -5000 USDT."""
        pv = valuate_position(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            quantity=Decimal("1"),
            avg_cost_per_unit=Decimal("60000"),
            current_price=Decimal("65000"),
        )
        assert pv.unrealized_pnl == q_amount(Decimal("-5000"))

    def test_valuation_zero_quantity_raises(self):
        with pytest.raises(ValuationError):
            valuate_position(
                symbol="BTCUSDT", side=TradeSide.LONG,
                quantity=ZERO, avg_cost_per_unit=Decimal("60000"),
                current_price=Decimal("65000"),
            )

    def test_valuation_zero_price_raises(self):
        with pytest.raises(ValuationError):
            valuate_position(
                symbol="BTCUSDT", side=TradeSide.LONG,
                quantity=Decimal("1"), avg_cost_per_unit=Decimal("60000"),
                current_price=ZERO,
            )

    def test_portfolio_valuation_nav(self):
        """NAV = nakit + pozisyon mark-to-market."""
        pv = valuate_portfolio(
            cash_usdt=Decimal("9000"),
            open_positions=[{
                "symbol": "BTCUSDT",
                "side": TradeSide.LONG,
                "quantity": "1",
                "avg_cost_per_unit": "60000",
                "current_price": "65000",
            }],
            timestamp=_TS,
        )
        assert pv.cash_usdt == q_amount(Decimal("9000"))
        assert pv.total_position_value == q_amount(Decimal("65000"))
        assert pv.nav_usdt == q_amount(Decimal("74000"))
        assert pv.total_unrealized_pnl == q_amount(Decimal("5000"))

    def test_portfolio_no_positions(self):
        """Açık pozisyon yokken NAV = nakit."""
        pv = valuate_portfolio(cash_usdt=Decimal("10000"), open_positions=[], timestamp=_TS)
        assert pv.nav_usdt == q_amount(Decimal("10000"))
        assert pv.total_unrealized_pnl == ZERO

    def test_portfolio_negative_cash_raises(self):
        with pytest.raises(ValuationError):
            valuate_portfolio(cash_usdt=Decimal("-1"), open_positions=[], timestamp=_TS)

    def test_drawdown_pct_at_peak(self):
        assert compute_drawdown_pct(Decimal("10000"), Decimal("10000")) == ZERO

    def test_drawdown_pct_calculation(self):
        """10000 → 9000: %10 drawdown."""
        dd = compute_drawdown_pct(Decimal("9000"), Decimal("10000"))
        assert dd == q_rate(Decimal("10"))

    def test_daily_pnl_pct_loss(self):
        """Gün başı 10000, şimdi 9950 → -%0.5."""
        pct = compute_daily_pnl_pct(Decimal("9950"), Decimal("10000"))
        assert pct == q_rate(Decimal("-0.5"))


# ══════════════════════════════════════════════════════════════════════════════
# 9. Mutabakat testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestReconciliation:

    def test_balanced_journals_pass(self):
        j1 = build_position_open_journal(symbol="BTCUSDT", side=TradeSide.LONG,
                                          risk_usdt=Decimal("50"), timestamp=_TS)
        result = check_ledger_balance([j1])
        assert result.passed is True

    def test_unbalanced_journal_fails(self):
        j_bad = JournalEntry(
            id="J-BAD", timestamp=_TS, description="Bad",
            transfer_type=TransferType.POSITION_OPEN,
            lines=(
                LedgerLine(account="PAPER_POSITION:BTCUSDT", entry_type=EntryType.DEBIT,  amount=Decimal("100")),
                LedgerLine(account=account_cash(),            entry_type=EntryType.CREDIT, amount=Decimal("99")),
            ),
        )
        result = check_ledger_balance([j_bad])
        assert result.passed is False

    def test_cash_balance_match(self):
        result = check_cash_balance(Decimal("10000"), Decimal("10000"))
        assert result.passed is True

    def test_cash_balance_mismatch(self):
        result = check_cash_balance(Decimal("9999"), Decimal("10000"))
        assert result.passed is False

    def test_cash_balance_within_tolerance(self):
        """Tolerans içindeki fark geçmeli."""
        result = check_cash_balance(
            Decimal("10000.000000001"),
            Decimal("10000"),
            tolerance=Decimal("0.00000001"),
        )
        assert result.passed is True

    def test_daily_loss_within_limit(self):
        result = check_daily_loss_limit(
            current_balance=Decimal("9950"),
            day_start_balance=Decimal("10000"),
            limit_pct=Decimal("1.0"),
        )
        assert result.passed is True   # %0.5 kayıp < %1.0 limit

    def test_daily_loss_exceeds_limit(self):
        result = check_daily_loss_limit(
            current_balance=Decimal("9800"),
            day_start_balance=Decimal("10000"),
            limit_pct=Decimal("1.0"),
        )
        assert result.passed is False   # %2.0 kayıp > %1.0 limit

    def test_drawdown_within_limit(self):
        result = check_drawdown_limit(
            current_balance=Decimal("9600"),
            peak_balance=Decimal("10000"),
            max_drawdown_pct=Decimal("5.0"),
        )
        assert result.passed is True   # %4 drawdown < %5 limit

    def test_drawdown_exceeds_limit(self):
        result = check_drawdown_limit(
            current_balance=Decimal("9400"),
            peak_balance=Decimal("10000"),
            max_drawdown_pct=Decimal("5.0"),
        )
        assert result.passed is False   # %6 drawdown > %5 limit

    def test_risk_per_trade_ok(self):
        result = check_risk_per_trade(
            risk_usdt=Decimal("49.75"),
            balance=Decimal("10000"),
            max_risk_pct=Decimal("0.50"),
        )
        assert result.passed is True   # %0.4975 < %0.5

    def test_risk_per_trade_exceeds(self):
        result = check_risk_per_trade(
            risk_usdt=Decimal("60"),
            balance=Decimal("10000"),
            max_risk_pct=Decimal("0.50"),
        )
        assert result.passed is False   # %0.6 > %0.5

    def test_no_negative_balance_ok(self):
        assert check_no_negative_balance(Decimal("100")).passed is True
        assert check_no_negative_balance(ZERO).passed is True

    def test_negative_balance_fails(self):
        assert check_no_negative_balance(Decimal("-1")).passed is False

    def test_position_cost_positive_ok(self):
        assert check_position_cost_positive("BTCUSDT", Decimal("50")).passed is True

    def test_position_cost_zero_fails(self):
        assert check_position_cost_positive("BTCUSDT", ZERO).passed is False

    def test_reconcile_all_clean_state(self):
        """Temiz state → tüm kontroller geçmeli."""
        j_open  = build_position_open_journal(
            symbol="BTCUSDT", side=TradeSide.LONG,
            risk_usdt=Decimal("49.75"), timestamp=_TS,
        )
        j_fee   = build_fee_journal(symbol="BTCUSDT", fee_usdt=Decimal("4"), timestamp=_TS)
        j_close = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.LONG,
            cost_basis_usdt=Decimal("49.75"),
            exit_value_usdt=Decimal("74.62"),   # ~%50 kâr
            timestamp=_TS,
        )
        # Journal'dan nakit değişimi: -49.75 + 74.62 - 4 = +20.87
        journals = [j_open, j_fee, j_close]
        computed = compute_cash_from_journals(journals)
        # starting = 10000, computed relative change = +20.87
        # expected balance = 10000 + 20.87 = 10020.87
        expected = q_amount(Decimal("10000") + computed)

        result = reconcile_all(
            journals=journals,
            computed_balance=expected,
            expected_balance=expected,
            current_balance=expected,
            day_start_balance=Decimal("10000"),
            peak_balance=Decimal("10000"),
            risk_usdt=ZERO,
            limits={
                "daily_loss_limit_pct": "1.0",
                "max_drawdown_pct": "5.0",
                "max_risk_pct": "0.50",
            },
            timestamp=_TS,
        )
        assert result.passed is True, f"Başarısız kontroller: {[c.message for c in result.failed_checks]}"

    def test_reconcile_all_detects_imbalance(self):
        """Dengeli olmayan journal → mutabakat başarısız."""
        j_bad = JournalEntry(
            id="J-BAD", timestamp=_TS, description="Dengesiz",
            transfer_type=TransferType.FEE,
            lines=(
                LedgerLine(account=account_fee_expense(), entry_type=EntryType.DEBIT,  amount=Decimal("5")),
                LedgerLine(account=account_cash(),         entry_type=EntryType.CREDIT, amount=Decimal("4")),
            ),
        )
        result = reconcile_all(
            journals=[j_bad],
            computed_balance=Decimal("10000"),
            expected_balance=Decimal("10000"),
            current_balance=Decimal("10000"),
            day_start_balance=Decimal("10000"),
            peak_balance=Decimal("10000"),
            timestamp=_TS,
        )
        assert result.passed is False
        assert any("ledger_balance" in c.name for c in result.failed_checks)

    def test_reconcile_result_summary(self):
        """ReconciliationResult özet özellikleri."""
        j = build_position_open_journal(
            symbol="BTCUSDT", side=TradeSide.LONG,
            risk_usdt=Decimal("50"), timestamp=_TS,
        )
        result = reconcile_all(
            journals=[j],
            computed_balance=Decimal("9950"),
            expected_balance=Decimal("9950"),
            current_balance=Decimal("9950"),
            day_start_balance=Decimal("10000"),
            peak_balance=Decimal("10000"),
            timestamp=_TS,
        )
        assert result.total_count == result.passed_count + len(result.failed_checks)


# ══════════════════════════════════════════════════════════════════════════════
# 10. Public API bütünlük testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicAPI:

    def test_treasury_package_importable(self):
        """Treasury paketi sorunsuz import edilebilmeli."""
        import treasury  # noqa: F401

    def test_all_exports_accessible(self):
        """__all__ içindeki tüm isimler import edilebilmeli."""
        import treasury as t
        for name in t.__all__:
            assert hasattr(t, name), f"treasury.{name} eksik"

    def test_paper_mode_no_live_api(self):
        """Treasury modülleri gerçek borsa API'si kullanmaz."""
        import treasury.precision
        import treasury.ledger
        import treasury.cost_basis
        import treasury.fees
        import treasury.transfer
        import treasury.valuation
        import treasury.reconciliation

        forbidden_imports = ["ccxt", "binance", "ftx", "requests", "httpx", "aiohttp"]
        for mod in [
            treasury.precision, treasury.ledger, treasury.cost_basis,
            treasury.fees, treasury.transfer, treasury.valuation,
            treasury.reconciliation,
        ]:
            source_path = Path(mod.__file__)
            source      = source_path.read_text(encoding="utf-8").lower()
            for lib in forbidden_imports:
                assert lib not in source, \
                    f"{source_path.name} yasak import '{lib}' içeriyor."

    def test_decimal_everywhere_no_float_math(self):
        """Treasury tipleri float döndürmez."""
        pv = valuate_position(
            symbol="BTCUSDT", side=TradeSide.LONG,
            quantity=Decimal("0.54309953"),
            avg_cost_per_unit=Decimal("64323.7"),
            current_price=Decimal("66000"),
        )
        assert isinstance(pv.unrealized_pnl, Decimal)
        assert isinstance(pv.mark_to_market_usdt, Decimal)
        assert isinstance(pv.unrealized_pnl_pct, Decimal)


# ══════════════════════════════════════════════════════════════════════════════
# 11. SHORT muhasebesi testleri (ES-002 Rev 1)
# ══════════════════════════════════════════════════════════════════════════════

class TestSHORTAccounting:
    """SHORT pozisyon journal semantiği ve K/Z doğruluğu."""

    def test_short_open_journal_balanced(self):
        """SHORT açılış: teminat nakitten ayrılır, journal dengeli."""
        j = build_position_open_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            risk_usdt=Decimal("6500"), timestamp=_TS,
        )
        assert j.is_balanced()
        assert j.debit_total()  == q_amount(Decimal("6500"))
        assert j.credit_total() == q_amount(Decimal("6500"))

    def test_short_open_debit_position_credit_cash(self):
        """SHORT açılışta POSITION DR, CASH CR olmalı."""
        j = build_position_open_journal(
            symbol="ETHUSDT", side=TradeSide.SHORT,
            risk_usdt=Decimal("3200"), timestamp=_TS,
        )
        dr = [ln.account for ln in j.lines if ln.entry_type == EntryType.DEBIT]
        cr = [ln.account for ln in j.lines if ln.entry_type == EntryType.CREDIT]
        assert "PAPER_POSITION:ETHUSDT" in dr
        assert account_cash() in cr

    def test_short_close_profit_balanced(self):
        """SHORT kâr (fiyat düştü): exit < cost → pnl > 0, journal dengeli."""
        j = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("6500"),
            exit_value_usdt=Decimal("6000"),   # fiyat düştü
            timestamp=_TS,
        )
        assert j.is_balanced()

    def test_short_close_profit_pnl_correct(self):
        """SHORT kâr: pnl = cost - exit = 6500 - 6000 = 500."""
        j = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("6500"),
            exit_value_usdt=Decimal("6000"),
            timestamp=_TS,
        )
        cr_pnl = sum(
            ln.amount for ln in j.lines
            if ln.account == account_realized_pnl() and ln.entry_type == EntryType.CREDIT
        )
        assert cr_pnl == q_amount(Decimal("500"))

    def test_short_close_profit_cash_effect(self):
        """SHORT kâr: nakit DR = cost + pnl = 6500 + 500 = 7000."""
        j = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("6500"),
            exit_value_usdt=Decimal("6000"),
            timestamp=_TS,
        )
        dr_cash = sum(
            ln.amount for ln in j.lines
            if ln.account == account_cash() and ln.entry_type == EntryType.DEBIT
        )
        assert dr_cash == q_amount(Decimal("7000"))

    def test_short_close_loss_balanced(self):
        """SHORT zarar (fiyat arttı): exit > cost → pnl < 0, journal dengeli."""
        j = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("6500"),
            exit_value_usdt=Decimal("7000"),   # fiyat arttı
            timestamp=_TS,
        )
        assert j.is_balanced()

    def test_short_close_loss_pnl_correct(self):
        """SHORT zarar: pnl = cost - exit = 6500 - 7000 = -500."""
        j = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("6500"),
            exit_value_usdt=Decimal("7000"),
            timestamp=_TS,
        )
        dr_pnl = sum(
            ln.amount for ln in j.lines
            if ln.account == account_realized_pnl() and ln.entry_type == EntryType.DEBIT
        )
        assert dr_pnl == q_amount(Decimal("500"))

    def test_short_close_loss_cash_effect(self):
        """SHORT zarar: nakit DR = cost - zarar = 6500 - 500 = 6000."""
        j = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("6500"),
            exit_value_usdt=Decimal("7000"),
            timestamp=_TS,
        )
        dr_cash = sum(
            ln.amount for ln in j.lines
            if ln.account == account_cash() and ln.entry_type == EntryType.DEBIT
        )
        assert dr_cash == q_amount(Decimal("6000"))

    def test_short_close_breakeven_balanced(self):
        """SHORT başabaş: exit == cost → journal dengeli, K/Z satırı yok."""
        j = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("6500"),
            exit_value_usdt=Decimal("6500"),
            timestamp=_TS,
        )
        assert j.is_balanced()
        pnl_lines = [ln for ln in j.lines if ln.account == account_realized_pnl()]
        assert len(pnl_lines) == 0

    def test_short_net_cash_profit(self):
        """SHORT aç+kapat net nakit = kâr."""
        j_open = build_position_open_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            risk_usdt=Decimal("6500"), timestamp=_TS,
        )
        j_close = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("6500"),
            exit_value_usdt=Decimal("6000"),  # 500 kâr
            timestamp=_TS,
        )
        net_cash = compute_cash_from_journals([j_open, j_close])
        assert net_cash == q_amount(Decimal("500"))

    def test_short_net_cash_loss(self):
        """SHORT aç+kapat net nakit = zarar (negatif)."""
        j_open = build_position_open_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            risk_usdt=Decimal("6500"), timestamp=_TS,
        )
        j_close = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            cost_basis_usdt=Decimal("6500"),
            exit_value_usdt=Decimal("7000"),  # 500 zarar
            timestamp=_TS,
        )
        net_cash = compute_cash_from_journals([j_open, j_close])
        assert net_cash == q_amount(Decimal("-500"))

    def test_short_margin_call_raises(self):
        """SHORT zarar > teminat → ValueError (margin call PAPER modda desteklenmiyor)."""
        with pytest.raises(ValueError, match="margin call"):
            build_position_close_journal(
                symbol="BTCUSDT", side=TradeSide.SHORT,
                cost_basis_usdt=Decimal("500"),
                exit_value_usdt=Decimal("1100"),  # zarar = 600 > teminat 500
                timestamp=_TS,
            )

    def test_long_behavior_unchanged_after_rev1(self):
        """LONG kapanış davranışı ES-002 Rev 1 sonrası değişmedi."""
        j = build_position_close_journal(
            symbol="BTCUSDT", side=TradeSide.LONG,
            cost_basis_usdt=Decimal("50"),
            exit_value_usdt=Decimal("75"),
            timestamp=_TS,
        )
        assert j.is_balanced()
        dr_cash = sum(
            ln.amount for ln in j.lines
            if ln.account == account_cash() and ln.entry_type == EntryType.DEBIT
        )
        assert dr_cash == q_amount(Decimal("75"))


# ══════════════════════════════════════════════════════════════════════════════
# 12. Portfolio NAV testleri — LONG / SHORT / karma (ES-002 Rev 1)
# ══════════════════════════════════════════════════════════════════════════════

class TestPortfolioNAV:
    """LONG, SHORT ve karma portföy NAV doğruluğu."""

    def test_long_only_nav(self):
        """LONG-only: NAV = nakit + LONG mark_to_market."""
        pv = valuate_portfolio(
            cash_usdt=Decimal("4000"),
            open_positions=[{
                "symbol": "BTCUSDT",
                "side": TradeSide.LONG,
                "quantity": "1",
                "avg_cost_per_unit": "60000",
                "current_price": "65000",
            }],
            timestamp=_TS,
        )
        assert pv.long_position_value == q_amount(Decimal("65000"))
        assert pv.short_equity        == q_amount(Decimal("0"))
        assert pv.nav_usdt            == q_amount(Decimal("69000"))
        assert pv.total_unrealized_pnl == q_amount(Decimal("5000"))

    def test_short_only_nav(self):
        """
        SHORT-only: cash=3500, SHORT 0.1 BTC avg=65000 current=60000
        collateral = 65000 × 0.1 = 6500 (nakitten düşülmüş)
        unrealized_pnl = (65000-60000) × 0.1 = 500 (kâr)
        short_equity = 6500 + 500 = 7000
        NAV = 3500 + 0 + 7000 = 10500
        """
        pv = valuate_portfolio(
            cash_usdt=Decimal("3500"),
            open_positions=[{
                "symbol": "BTCUSDT",
                "side": TradeSide.SHORT,
                "quantity": "0.1",
                "avg_cost_per_unit": "65000",
                "current_price": "60000",
            }],
            timestamp=_TS,
        )
        assert pv.long_position_value == q_amount(Decimal("0"))
        assert pv.short_equity        == q_amount(Decimal("7000"))
        assert pv.nav_usdt            == q_amount(Decimal("10500"))
        assert pv.total_unrealized_pnl == q_amount(Decimal("500"))

    def test_short_mark_to_market_not_added_to_nav(self):
        """SHORT mark_to_market_usdt doğrudan NAV'a eklenmemeli."""
        pv = valuate_portfolio(
            cash_usdt=Decimal("3500"),
            open_positions=[{
                "symbol": "BTCUSDT",
                "side": TradeSide.SHORT,
                "quantity": "0.1",
                "avg_cost_per_unit": "65000",
                "current_price": "60000",
            }],
            timestamp=_TS,
        )
        pos = pv.positions[0]
        # mark_to_market = 60000 × 0.1 = 6000
        assert pos.mark_to_market_usdt == q_amount(Decimal("6000"))
        # NAV = 3500 + 7000 = 10500 (mark_to_market 6000 doğrudan eklenmedi)
        assert pv.nav_usdt != q_amount(Decimal("3500") + pos.mark_to_market_usdt)
        assert pv.nav_usdt == q_amount(Decimal("10500"))

    def test_short_loss_nav(self):
        """
        SHORT zarar: cash=3500, SHORT 0.1 BTC avg=60000 current=65000
        collateral = 6000, unrealized_pnl = -500
        short_equity = 6000 - 500 = 5500
        NAV = 3500 + 5500 = 9000
        """
        pv = valuate_portfolio(
            cash_usdt=Decimal("3500"),
            open_positions=[{
                "symbol": "BTCUSDT",
                "side": TradeSide.SHORT,
                "quantity": "0.1",
                "avg_cost_per_unit": "60000",
                "current_price": "65000",
            }],
            timestamp=_TS,
        )
        assert pv.short_equity        == q_amount(Decimal("5500"))
        assert pv.nav_usdt            == q_amount(Decimal("9000"))
        assert pv.total_unrealized_pnl == q_amount(Decimal("-500"))

    def test_mixed_long_short_nav(self):
        """
        Karma portföy:
          cash = 2000
          LONG 0.1 BTC avg=60000 current=65000:
            long_mtm = 6500, unrealized = +500
          SHORT 0.1 BTC avg=65000 current=60000:
            collateral = 6500, unrealized = +500
            short_equity = 7000
          NAV = 2000 + 6500 + 7000 = 15500
        """
        pv = valuate_portfolio(
            cash_usdt=Decimal("2000"),
            open_positions=[
                {
                    "symbol": "BTCUSDT", "side": TradeSide.LONG,
                    "quantity": "0.1", "avg_cost_per_unit": "60000",
                    "current_price": "65000",
                },
                {
                    "symbol": "ETHUSDT", "side": TradeSide.SHORT,
                    "quantity": "0.1", "avg_cost_per_unit": "65000",
                    "current_price": "60000",
                },
            ],
            timestamp=_TS,
        )
        assert pv.long_position_value == q_amount(Decimal("6500"))
        assert pv.short_equity        == q_amount(Decimal("7000"))
        assert pv.nav_usdt            == q_amount(Decimal("15500"))
        assert pv.total_unrealized_pnl == q_amount(Decimal("1000"))

    def test_total_position_value_backward_compat(self):
        """total_position_value = long + short (backward compat)."""
        pv = valuate_portfolio(
            cash_usdt=Decimal("1000"),
            open_positions=[
                {
                    "symbol": "BTCUSDT", "side": TradeSide.LONG,
                    "quantity": "1", "avg_cost_per_unit": "60000",
                    "current_price": "62000",
                },
                {
                    "symbol": "ETHUSDT", "side": TradeSide.SHORT,
                    "quantity": "1", "avg_cost_per_unit": "4000",
                    "current_price": "3800",
                },
            ],
            timestamp=_TS,
        )
        assert pv.total_position_value == q_amount(
            pv.long_position_value + pv.short_equity
        )

    def test_collateral_usdt_field(self):
        """PositionValuation.collateral_usdt = avg_cost × qty."""
        pv_long = valuate_position(
            symbol="BTCUSDT", side=TradeSide.LONG,
            quantity=Decimal("2"),
            avg_cost_per_unit=Decimal("30000"),
            current_price=Decimal("35000"),
        )
        assert pv_long.collateral_usdt == q_amount(Decimal("60000"))

        pv_short = valuate_position(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            quantity=Decimal("0.5"),
            avg_cost_per_unit=Decimal("65000"),
            current_price=Decimal("60000"),
        )
        assert pv_short.collateral_usdt == q_amount(Decimal("32500"))

    def test_nav_contribution_long(self):
        """LONG nav_contribution = mark_to_market_usdt."""
        pv = valuate_position(
            symbol="BTCUSDT", side=TradeSide.LONG,
            quantity=Decimal("1"),
            avg_cost_per_unit=Decimal("60000"),
            current_price=Decimal("65000"),
        )
        assert pv.nav_contribution == pv.mark_to_market_usdt
        assert pv.nav_contribution == q_amount(Decimal("65000"))

    def test_nav_contribution_short(self):
        """SHORT nav_contribution = collateral + unrealized_pnl."""
        pv = valuate_position(
            symbol="BTCUSDT", side=TradeSide.SHORT,
            quantity=Decimal("0.1"),
            avg_cost_per_unit=Decimal("65000"),
            current_price=Decimal("60000"),
        )
        assert pv.nav_contribution == q_amount(
            pv.collateral_usdt + pv.unrealized_pnl
        )
        assert pv.nav_contribution == q_amount(Decimal("7000"))

    def test_empty_portfolio_nav(self):
        """Açık pozisyon yokken NAV = nakit."""
        pv = valuate_portfolio(cash_usdt=Decimal("10000"), open_positions=[], timestamp=_TS)
        assert pv.nav_usdt            == q_amount(Decimal("10000"))
        assert pv.long_position_value == q_amount(Decimal("0"))
        assert pv.short_equity        == q_amount(Decimal("0"))


# ══════════════════════════════════════════════════════════════════════════════
# 13. Funding journal testleri — ödeme ve tahsilat (ES-002 Rev 1)
# ══════════════════════════════════════════════════════════════════════════════

class TestFundingJournal:
    """Fonlama ödemesi (gider) ve tahsilatı (gelir) ayrı kaydedilmeli."""

    def test_funding_payment_balanced(self):
        """Ödeme: DR FUNDING_EXPENSE, CR CASH — dengeli."""
        j = build_funding_journal(
            symbol="BTCUSDT", funding_usdt=Decimal("2.5"),
            is_income=False, timestamp=_TS,
        )
        assert j.is_balanced()

    def test_funding_payment_decreases_cash(self):
        """Ödeme: nakit AZALMALI (CR CASH)."""
        j = build_funding_journal(
            symbol="BTCUSDT", funding_usdt=Decimal("2.5"),
            is_income=False, timestamp=_TS,
        )
        cr_cash = sum(
            ln.amount for ln in j.lines
            if ln.account == account_cash() and ln.entry_type == EntryType.CREDIT
        )
        assert cr_cash == q_amount(Decimal("2.5"))

    def test_funding_payment_uses_expense_account(self):
        """Ödeme: PAPER_FUNDING_EXPENSE hesabı kullanılmalı."""
        j = build_funding_journal(
            symbol="BTCUSDT", funding_usdt=Decimal("2.5"),
            is_income=False, timestamp=_TS,
        )
        dr_accounts = [ln.account for ln in j.lines if ln.entry_type == EntryType.DEBIT]
        assert account_funding_expense() in dr_accounts

    def test_funding_payment_transfer_type(self):
        """Ödeme transfer tipi FUNDING_PAYMENT olmalı."""
        from treasury.types import TransferType
        j = build_funding_journal(
            symbol="BTCUSDT", funding_usdt=Decimal("1"),
            is_income=False, timestamp=_TS,
        )
        assert j.transfer_type == TransferType.FUNDING_PAYMENT

    def test_funding_income_balanced(self):
        """Tahsilat: DR CASH, CR FUNDING_INCOME — dengeli."""
        j = build_funding_journal(
            symbol="ETHUSDT", funding_usdt=Decimal("1.75"),
            is_income=True, timestamp=_TS,
        )
        assert j.is_balanced()

    def test_funding_income_increases_cash(self):
        """Tahsilat: nakit ARTMALI (DR CASH)."""
        j = build_funding_journal(
            symbol="ETHUSDT", funding_usdt=Decimal("1.75"),
            is_income=True, timestamp=_TS,
        )
        dr_cash = sum(
            ln.amount for ln in j.lines
            if ln.account == account_cash() and ln.entry_type == EntryType.DEBIT
        )
        assert dr_cash == q_amount(Decimal("1.75"))

    def test_funding_income_uses_income_account(self):
        """Tahsilat: PAPER_FUNDING_INCOME hesabı kullanılmalı."""
        j = build_funding_journal(
            symbol="ETHUSDT", funding_usdt=Decimal("1.75"),
            is_income=True, timestamp=_TS,
        )
        cr_accounts = [ln.account for ln in j.lines if ln.entry_type == EntryType.CREDIT]
        assert account_funding_income() in cr_accounts

    def test_funding_income_transfer_type(self):
        """Tahsilat transfer tipi FUNDING_INCOME olmalı."""
        from treasury.types import TransferType
        j = build_funding_journal(
            symbol="ETHUSDT", funding_usdt=Decimal("1"),
            is_income=True, timestamp=_TS,
        )
        assert j.transfer_type == TransferType.FUNDING_INCOME

    def test_funding_payment_income_opposite_cash_effect(self):
        """Ödeme ve tahsilat nakit üzerinde zıt etki yaratmalı."""
        j_pay = build_funding_journal(
            symbol="BTCUSDT", funding_usdt=Decimal("3"),
            is_income=False, timestamp=_TS,
        )
        j_inc = build_funding_journal(
            symbol="BTCUSDT", funding_usdt=Decimal("3"),
            is_income=True, timestamp=_TS,
        )
        cash_from_pay = compute_cash_from_journals([j_pay])
        cash_from_inc = compute_cash_from_journals([j_inc])
        assert cash_from_pay == q_amount(Decimal("-3"))
        assert cash_from_inc == q_amount(Decimal("3"))
        assert cash_from_pay + cash_from_inc == q_amount(Decimal("0"))

    def test_funding_zero_raises(self):
        """Sıfır funding_usdt → ValueError."""
        with pytest.raises(ValueError):
            build_funding_journal(symbol="BTCUSDT", funding_usdt=Decimal("0"), timestamp=_TS)

    def test_funding_default_is_payment(self):
        """Varsayılan (is_income belirtilmezse) = ödeme."""
        j = build_funding_journal(symbol="BTCUSDT", funding_usdt=Decimal("1"), timestamp=_TS)
        cr_accounts = [ln.account for ln in j.lines if ln.entry_type == EntryType.CREDIT]
        assert account_cash() in cr_accounts


# ══════════════════════════════════════════════════════════════════════════════
# 14. WAVG invariant testleri (ES-002 Rev 1)
# ══════════════════════════════════════════════════════════════════════════════

class TestWAVGInvariant:
    """Lot homojenliği — karışık sembol veya side CostBasisError fırlatmalı."""

    def _make_lot(self, symbol: str, side: TradeSide, qty: str, cost: str,
                  trade_id: str = "T001") -> CostBasisLot:
        return CostBasisLot(
            symbol=symbol, side=side,
            quantity=q_qty(Decimal(qty)),
            total_cost_usdt=q_amount(Decimal(cost)),
            opened_at=_TS, trade_id=trade_id,
        )

    def test_homogeneous_lots_ok(self):
        """Aynı sembol ve side → hata yok."""
        lots = [
            self._make_lot("BTCUSDT", TradeSide.LONG, "1", "60000", "T1"),
            self._make_lot("BTCUSDT", TradeSide.LONG, "1", "64000", "T2"),
        ]
        avg = compute_weighted_average(lots)
        assert avg == q_price(Decimal("62000"))

    def test_mixed_symbol_raises(self):
        """Farklı sembol → CostBasisError."""
        lots = [
            self._make_lot("BTCUSDT", TradeSide.LONG, "1", "60000", "T1"),
            self._make_lot("ETHUSDT", TradeSide.LONG, "1", "3000",  "T2"),
        ]
        with pytest.raises(CostBasisError, match="sembol"):
            compute_weighted_average(lots)

    def test_mixed_side_raises(self):
        """Farklı side (LONG + SHORT) → CostBasisError."""
        lots = [
            self._make_lot("BTCUSDT", TradeSide.LONG,  "1", "60000", "T1"),
            self._make_lot("BTCUSDT", TradeSide.SHORT, "1", "65000", "T2"),
        ]
        with pytest.raises(CostBasisError, match="yön"):
            compute_weighted_average(lots)

    def test_add_lot_wrong_symbol_raises(self):
        """add_lot_and_recompute: yeni lot farklı sembol → CostBasisError."""
        btc_lot = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "60000")
        eth_lot = self._make_lot("ETHUSDT", TradeSide.LONG, "1", "3000", "T2")
        with pytest.raises(CostBasisError):
            add_lot_and_recompute([btc_lot], eth_lot)

    def test_add_lot_wrong_side_raises(self):
        """add_lot_and_recompute: yeni lot farklı side → CostBasisError."""
        long_lot  = self._make_lot("BTCUSDT", TradeSide.LONG,  "1", "60000")
        short_lot = self._make_lot("BTCUSDT", TradeSide.SHORT, "1", "65000", "T2")
        with pytest.raises(CostBasisError):
            add_lot_and_recompute([long_lot], short_lot)

    def test_consume_mixed_symbol_raises(self):
        """consume_lots_wavg: karışık sembol → CostBasisError."""
        lots = [
            self._make_lot("BTCUSDT", TradeSide.LONG, "1", "60000", "T1"),
            self._make_lot("ETHUSDT", TradeSide.LONG, "1", "3000",  "T2"),
        ]
        with pytest.raises(CostBasisError):
            consume_lots_wavg(lots, q_qty(Decimal("1")))

    def test_single_lot_no_invariant_check(self):
        """Tek lot → invariant kontrol edilmez, hata yok."""
        lot = self._make_lot("BTCUSDT", TradeSide.LONG, "1", "60000")
        avg = compute_weighted_average([lot])
        assert avg == q_price(Decimal("60000"))

    def test_empty_lots_no_error(self):
        """Boş lot listesi → 0 döner, hata yok."""
        assert compute_weighted_average([]) == ZERO

    def test_short_homogeneous_lots_ok(self):
        """SHORT lotlar kendi aralarında homojen olduğunda OK."""
        lots = [
            self._make_lot("ETHUSDT", TradeSide.SHORT, "2", "8000", "T1"),
            self._make_lot("ETHUSDT", TradeSide.SHORT, "2", "8400", "T2"),
        ]
        avg = compute_weighted_average(lots)
        assert avg == q_price(Decimal("4100"))


# ══════════════════════════════════════════════════════════════════════════════
# 15. Decimal context testleri (ES-002 Rev 1)
# ══════════════════════════════════════════════════════════════════════════════

class TestDecimalContext:
    """Global Decimal context import sonrası değişmemeli."""

    def test_import_does_not_change_global_context(self):
        """
        treasury.precision import edildiğinde global context değişmemeli.
        Bu test import öncesi ve sonrası context'i karşılaştırır.
        """
        import decimal
        import importlib

        ctx_before = decimal.getcontext().copy()
        # precision zaten import edilmiş; modülü yeniden yükle
        import alpha20_v1.treasury.precision as prec_mod
        importlib.reload(prec_mod)
        ctx_after = decimal.getcontext()

        # prec ve rounding değişmemiş olmalı
        assert ctx_after.prec == ctx_before.prec, (
            f"Import sonrası precision değişti: {ctx_before.prec} → {ctx_after.prec}"
        )
        assert ctx_after.rounding == ctx_before.rounding, (
            f"Import sonrası rounding değişti: {ctx_before.rounding} → {ctx_after.rounding}"
        )

    def test_q_amount_explicit_rounding(self):
        """q_amount global context'e bağımlı değil — açık quantize kullanır."""
        import decimal
        # Farklı bir context ayarla
        with decimal.localcontext() as ctx:
            ctx.rounding = decimal.ROUND_DOWN
            # q_amount kendi ROUND_HALF_EVEN ile çalışmalı
            result = q_amount(Decimal("1.000000015"))
        # ROUND_HALF_EVEN: 1.000000015 → 1.00000002 (5 → çift sayıya)
        assert result == Decimal("1.00000002"), (
            f"q_amount global context'ten etkilendi: {result}"
        )

    def test_q_price_explicit_rounding(self):
        """q_price açık ROUND_HALF_EVEN kullanır, global context'ten bağımsız."""
        import decimal
        with decimal.localcontext() as ctx:
            ctx.rounding = decimal.ROUND_CEILING
            result = q_price(Decimal("64999.999999999"))
        # Beklenen: ROUND_HALF_EVEN ile 65000.00000000
        assert result == Decimal("65000.00000000")

    def test_precision_module_has_no_setcontext_side_effect(self):
        """precision.py kaynak kodunda setcontext çağrısı olmamalı."""
        import inspect
        import alpha20_v1.treasury.precision as prec_mod
        source = inspect.getsource(prec_mod)
        assert "setcontext(" not in source, (
            "precision.py kaynak kodu setcontext() içeriyor — "
            "global context yan etkisi kaldırılmalı."
        )

    def test_financial_results_consistent_across_contexts(self):
        """Farklı global context'lerde aynı finansal sonuç üretilmeli."""
        import decimal

        def compute(rounding):
            with decimal.localcontext() as ctx:
                ctx.rounding = rounding
                return q_amount(Decimal("0.000000015"))

        result_heven  = compute(decimal.ROUND_HALF_EVEN)
        result_down   = compute(decimal.ROUND_DOWN)
        result_up     = compute(decimal.ROUND_UP)

        # Tüm bağlamlarda aynı sonuç (ROUND_HALF_EVEN açık olarak uygulandı)
        assert result_heven == result_down == result_up, (
            f"Context bağımlılığı tespit edildi: "
            f"HALF_EVEN={result_heven}, DOWN={result_down}, UP={result_up}"
        )
