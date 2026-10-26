"""
test_beancount_ledger_load.py — Ledger-load validation for Beancount reconstruction.

Every follow-up deferred from PR #46 (issue #48) requires validating the
generated ledger against a *real* Beancount loader rather than string matching,
so these tests load each export through ``beancount.loader.load_string`` and
assert the ledger parses and balances with zero errors. All data is synthetic
and PII-free.
"""

import tempfile
import unittest
from pathlib import Path

from beancount import loader

from empower_personal_dashboard.beancount import BeancountGenerator
from empower_personal_dashboard.exceptions import LedgerAppendError
from empower_personal_dashboard.models import (
    DashboardBalances,
    DashboardHoldings,
    DashboardTransactions,
)


def assert_ledger_loads(testcase, content):
    """Load a single-file ledger string and fail the test on any Beancount error."""
    entries, errors, _options = loader.load_string(content)
    if errors:
        rendered = "\n".join(f"  - {e.message}" for e in errors)
        testcase.fail(f"Beancount reported {len(errors)} load error(s):\n{rendered}")
    return entries


def _brokerage_balances(as_of="2024-10-01", balance=20100.0):
    """Create a synthetic brokerage balance snapshot for testing."""
    return DashboardBalances(
        as_of_date=as_of,
        net_worth=balance,
        total_cash=0.0,
        total_investment=balance,
        total_card_liabilities=0.0,
        total_loan=0.0,
        total_mortgage=0.0,
        accounts=[
            {
                "account_id": "ACC-BRK-001",
                "account_name": "Taxable Brokerage",
                "firm_name": "Acme Brokerage",
                "account_type": "investment",
                "balance": balance,
                "is_asset": True,
                "currency": "USD",
                "user_account_id": 5001,
            }
        ],
    )


def _vti_holdings(as_of="2024-10-01", quantity=60.0, price=280.0, cost_basis=None):
    """Create a standard VTI holdings position for testing."""
    if cost_basis is None:
        cost_basis = quantity * price * 0.75
    total_value = quantity * price
    return DashboardHoldings(
        as_of_date=as_of,
        total_value=total_value,
        holdings=[
            {"user_account_id": 5001, "account_name": "Taxable Brokerage",
             "firm_name": "Acme Brokerage", "ticker": "VTI",
             "quantity": quantity, "price": price, "cost_basis": cost_basis},
        ],
    )


def _vti_buy_transaction(start="2021-01-01", end="2024-10-01", quantity=20.0, price=200.0):
    """Create a standard VTI buy transaction for testing."""
    amount = quantity * price
    return DashboardTransactions(
        start_date=start, end_date=end, total_transactions=1,
        money_in=0.0, money_out=amount, net_cashflow=-amount,
        transactions=[
            {"user_transaction_id": "tx-1", "account_id": "ACC-BRK-001",
             "user_account_id": 5001, "account_name": "Taxable Brokerage",
             "firm_name": "Acme Brokerage", "transaction_date": "2021-06-15",
             "description": "Buy VTI", "amount": amount, "is_cash_out": True,
             "transaction_type": "Buy", "investment_type": "Buy",
             "symbol": "VTI", "price": price, "quantity": quantity},
        ],
    )


class _BaseGeneratorTest(unittest.TestCase):
    """Base class for tests that use BeancountGenerator."""

    def setUp(self):
        self.generator = BeancountGenerator()


class TestLedgerLoadRegression(_BaseGeneratorTest):
    """The pre-existing lifecycle ledger must still load clean (loader harness sanity)."""

    def test_full_lifecycle_single_file_loads(self):
        """A complete lifecycle export must load cleanly through the Beancount loader."""
        balances = _brokerage_balances()
        holdings = _vti_holdings()
        transactions = _vti_buy_transaction()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "ledger.bean"
            self.generator.export_single_file(
                filepath=target, balances=balances, holdings=holdings,
                transactions=transactions, opening_date="2020-01-01",
            )
            assert_ledger_loads(self, target.read_text(encoding="utf-8"))


class TestShortPositions(_BaseGeneratorTest):
    """Item 1 — negative snapshot quantities preserved end to end."""

    def test_current_short_emits_negative_lot_and_assertion_and_loads(self):
        """Negative snapshot quantities produce negative opening lots that load cleanly."""
        # A currently-held short: the snapshot reports a negative quantity and no
        # offsetting trades. It must survive aggregation, produce a negative
        # opening lot, and a negative unit balance assertion that loads.
        holdings = DashboardHoldings(
            as_of_date="2024-10-01",
            total_value=-1200.0,
            holdings=[
                {"user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "ticker": "GE",
                 "quantity": -10.0, "price": 12.0},
            ],
        )
        balances = _brokerage_balances(balance=-1200.0)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "short.bean"
            self.generator.export_single_file(
                filepath=target, balances=balances, holdings=holdings,
            )
            content = target.read_text(encoding="utf-8")
        self.assertIn("-10.000000 GE", content)
        self.assertRegex(content, r"balance\s+Assets:AcmeBrokerage:TaxableBrokerage\s+-10\.000000 GE")
        assert_ledger_loads(self, content)

    def test_current_short_distinct_from_fully_sold_security(self):
        """Current shorts are distinct from pre-window longs that were fully sold."""
        # SHORTCO is a current short (in the snapshot, negative) while SOLDCO is a
        # pre-window long sold to zero (absent from the snapshot, net-sold). The
        # short yields a negative opening lot; the fully-sold yields a positive
        # reconstructed opening lot.
        holdings = DashboardHoldings(
            as_of_date="2024-10-01", total_value=-600.0,
            holdings=[
                {"user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "ticker": "SHORTCO",
                 "quantity": -5.0, "price": 12.0},
            ],
        )
        transactions = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=1,
            money_in=1000.0, money_out=0.0, net_cashflow=1000.0,
            transactions=[
                {"user_transaction_id": "tx-sold", "account_id": "ACC-BRK-001",
                 "user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "transaction_date": "2024-05-01",
                 "description": "Sell SOLDCO", "amount": 1000.0, "is_cash_in": True,
                 "is_credit": True, "transaction_type": "Sell",
                 "investment_type": "Sell", "symbol": "SOLDCO",
                 "price": 100.0, "quantity": 10.0},
            ],
        )
        balances = _brokerage_balances(balance=-600.0)
        holdings_out = self.generator.generate_holdings_bean(
            holdings=holdings, balances=balances, transactions=transactions,
        )
        self.assertIn('"SHORTCO Position"', holdings_out)
        self.assertRegex(holdings_out, r"-5\.000000 SHORTCO")
        self.assertIn('"SOLDCO Opening Position"', holdings_out)
        self.assertIn("10.000000 SOLDCO", holdings_out)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "mix.bean"
            self.generator.export_single_file(
                filepath=target, balances=balances, holdings=holdings,
                transactions=transactions,
            )
            assert_ledger_loads(self, target.read_text(encoding="utf-8"))

    def test_short_with_in_window_sell_and_buy_reconciles_and_loads(self):
        """A currently-held short with in-window sell and buy activity reconciles and loads."""
        holdings = DashboardHoldings(
            as_of_date="2024-10-01", total_value=-60.0,
            holdings=[
                {"user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "ticker": "GE",
                 "quantity": -5.0, "price": 12.0},
            ],
        )
        txns = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=2,
            money_in=100.0, money_out=120.0, net_cashflow=-20.0,
            transactions=[
                {"user_transaction_id": "tx-s1", "account_id": "ACC-BRK-001",
                 "user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "transaction_date": "2024-03-01",
                 "description": "Sell GE", "amount": 100.0, "is_cash_in": True,
                 "is_credit": True, "transaction_type": "Sell", "symbol": "GE",
                 "price": 10.0, "quantity": 10.0},
                {"user_transaction_id": "tx-b1", "account_id": "ACC-BRK-001",
                 "user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "transaction_date": "2024-08-01",
                 "description": "Buy GE", "amount": 120.0, "is_cash_out": True,
                 "transaction_type": "Buy", "symbol": "GE",
                 "price": 12.0, "quantity": 10.0},
            ],
        )
        balances = _brokerage_balances(balance=-60.0)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "short_churn.bean"
            self.generator.export_single_file(
                filepath=target, balances=balances, holdings=holdings,
                transactions=txns,
            )
            content = target.read_text(encoding="utf-8")
        self.assertRegex(
            content, r"balance\s+Assets:AcmeBrokerage:TaxableBrokerage\s+-5\.000000 GE"
        )
        assert_ledger_loads(self, content)

    def test_short_with_in_window_buy_partial_cover_reconciles_and_loads(self):
        """A short position partially covered by an in-window buy reconciles and loads."""
        holdings = DashboardHoldings(
            as_of_date="2024-10-01", total_value=-120.0,
            holdings=[
                {"user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "ticker": "GE",
                 "quantity": -10.0, "price": 12.0},
            ],
        )
        txns = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=1,
            money_in=0.0, money_out=60.0, net_cashflow=-60.0,
            transactions=[
                {"user_transaction_id": "tx-b1", "account_id": "ACC-BRK-001",
                 "user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "transaction_date": "2024-08-01",
                 "description": "Buy GE", "amount": 60.0, "is_cash_out": True,
                 "transaction_type": "Buy", "symbol": "GE",
                 "price": 12.0, "quantity": 5.0},
            ],
        )
        balances = _brokerage_balances(balance=-120.0)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "short_cover.bean"
            self.generator.export_single_file(
                filepath=target, balances=balances, holdings=holdings,
                transactions=txns,
            )
            content = target.read_text(encoding="utf-8")
        self.assertRegex(
            content, r"balance\s+Assets:AcmeBrokerage:TaxableBrokerage\s+-10\.000000 GE"
        )
        assert_ledger_loads(self, content)

    def test_short_with_in_window_sell_reconciles_and_loads(self):
        """A short position deepened by an in-window sell reconciles and loads."""
        holdings = DashboardHoldings(
            as_of_date="2024-10-01", total_value=-180.0,
            holdings=[
                {"user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "ticker": "GE",
                 "quantity": -15.0, "price": 12.0},
            ],
        )
        txns = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=1,
            money_in=100.0, money_out=0.0, net_cashflow=100.0,
            transactions=[
                {"user_transaction_id": "tx-s1", "account_id": "ACC-BRK-001",
                 "user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "transaction_date": "2024-03-01",
                 "description": "Sell GE", "amount": 100.0, "is_cash_in": True,
                 "is_credit": True, "transaction_type": "Sell", "symbol": "GE",
                 "price": 10.0, "quantity": 10.0},
            ],
        )
        balances = _brokerage_balances(balance=-180.0)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "short_sell.bean"
            self.generator.export_single_file(
                filepath=target, balances=balances, holdings=holdings,
                transactions=txns,
            )
            content = target.read_text(encoding="utf-8")
        self.assertRegex(
            content, r"balance\s+Assets:AcmeBrokerage:TaxableBrokerage\s+-15\.000000 GE"
        )
        assert_ledger_loads(self, content)

    def test_sign_flipped_positions_both_directions_reconcile_and_load(self):
        """Positions that flip sign across the window (short->long and long->short) reconcile and load."""
        # Case 1: short-to-long flip across the window (snapshot=+5 FLIP, window Buy 10 FLIP).
        holdings_long = DashboardHoldings(
            as_of_date="2024-10-01", total_value=60.0,
            holdings=[
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "ticker": "FLIP", "quantity": 5.0, "price": 12.0},
            ],
        )
        txns_buy = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=1,
            money_in=0.0, money_out=120.0, net_cashflow=-120.0,
            transactions=[
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "account_type": "investment", "transaction_date": "2024-08-01",
                 "description": "Buy FLIP", "amount": 120.0, "is_cash_out": True,
                 "transaction_type": "Buy", "symbol": "FLIP",
                 "price": 12.0, "quantity": 10.0},
            ],
        )
        # Case 2: long-to-short flip across the window (snapshot=-5 FLIP, window Sell 10 FLIP).
        holdings_short = DashboardHoldings(
            as_of_date="2024-10-01", total_value=-60.0,
            holdings=[
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "ticker": "FLIP", "quantity": -5.0, "price": 12.0},
            ],
        )
        txns_sell = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=1,
            money_in=120.0, money_out=0.0, net_cashflow=120.0,
            transactions=[
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "account_type": "investment", "transaction_date": "2024-08-01",
                 "description": "Sell FLIP", "amount": 120.0, "is_cash_in": True,
                 "is_credit": True, "transaction_type": "Sell", "symbol": "FLIP",
                 "price": 12.0, "quantity": 10.0},
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            long_flip_file = Path(tmp) / "flip_long.bean"
            self.generator.export_single_file(
                filepath=long_flip_file, holdings=holdings_long, transactions=txns_buy,
            )
            long_content = long_flip_file.read_text(encoding="utf-8")
            self.assertIn(
                "2024-10-03 balance Assets:AcmeBrokerage:TaxableBrokerage 5.000000 FLIP",
                long_content,
            )
            assert_ledger_loads(self, long_content)

            short_flip_file = Path(tmp) / "flip_short.bean"
            self.generator.export_single_file(
                filepath=short_flip_file, holdings=holdings_short, transactions=txns_sell,
            )
            short_content = short_flip_file.read_text(encoding="utf-8")
            self.assertIn(
                "2024-10-03 balance Assets:AcmeBrokerage:TaxableBrokerage -5.000000 FLIP",
                short_content,
            )
            assert_ledger_loads(self, short_content)


class TestDeficitFloorShortfall(_BaseGeneratorTest):
    """Item 4 — a deficit-floored opening lot must still match the asserted snapshot."""

    def test_deficit_floor_reconciles_to_snapshot_and_loads(self):
        """Deficit-floor adjustments reconcile to the snapshot and load cleanly."""
        # Snapshot holds 5 GE. The window sells 10 then buys 10 (net 0), so the
        # running deficit (10) floors the opening lot to 10 — which would leave a
        # final quantity of 10, contradicting the 5-share snapshot assertion.
        # An explicit shortfall reconciliation must bring the final to 5.
        holdings = DashboardHoldings(
            as_of_date="2024-10-01", total_value=60.0,
            holdings=[
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "ticker": "GE", "quantity": 5.0, "price": 12.0},
            ],
        )
        churn = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=2,
            money_in=0.0, money_out=0.0, net_cashflow=0.0,
            transactions=[
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "account_type": "investment", "transaction_date": "2024-03-01",
                 "description": "Sell GE", "amount": 100.0, "is_cash_in": True,
                 "is_credit": True, "transaction_type": "Sell", "symbol": "GE",
                 "price": 10.0, "quantity": 10.0},
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "account_type": "investment", "transaction_date": "2024-08-01",
                 "description": "Buy GE", "amount": 120.0, "is_cash_out": True,
                 "transaction_type": "Buy", "symbol": "GE",
                 "price": 12.0, "quantity": 10.0},
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "deficit.bean"
            self.generator.export_single_file(
                filepath=target, holdings=holdings, transactions=churn,
            )
            content = target.read_text(encoding="utf-8")
        # The floored opening lot of 10 is still emitted...
        self.assertIn("10.000000 GE {", content)
        # ...and the ledger loads: the shortfall reconciliation makes the final
        # quantity equal the 5-share snapshot assertion.
        assert_ledger_loads(self, content)

    def test_deficit_floor_with_negative_naive_baseline_and_short_reconciles_and_loads(self):
        """Long position with naive_baseline < 0 and max_deficit > 0 delays assertion to D+2; short stays at D+1 and loads."""
        # Snapshot holds 5 GE (long) and -5 SHORTCO (short). For GE, selling 10
        # then buying 20 gives net_buys = +10, naive_baseline = -5, max_deficit = 10
        # -> floored baseline = 10 and a -15 shortfall reconciliation on D+1 (assertion D+2).
        # SHORTCO has no shortfall reconciliation and is asserted at D+1.
        holdings = DashboardHoldings(
            as_of_date="2024-10-01", total_value=0.0,
            holdings=[
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "ticker": "GE", "quantity": 5.0, "price": 12.0},
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "ticker": "SHORTCO", "quantity": -5.0, "price": 12.0},
            ],
        )
        churn = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=2,
            money_in=100.0, money_out=240.0, net_cashflow=-140.0,
            transactions=[
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "account_type": "investment", "transaction_date": "2024-03-01",
                 "description": "Sell GE", "amount": 100.0, "is_cash_in": True,
                 "is_credit": True, "transaction_type": "Sell", "symbol": "GE",
                 "price": 10.0, "quantity": 10.0},
                {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                 "account_type": "investment", "transaction_date": "2024-08-01",
                 "description": "Buy GE", "amount": 240.0, "is_cash_out": True,
                 "transaction_type": "Buy", "symbol": "GE",
                 "price": 12.0, "quantity": 20.0},
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "deficit_neg_naive.bean"
            self.generator.export_single_file(
                filepath=target, holdings=holdings, transactions=churn,
            )
            content = target.read_text(encoding="utf-8")
        self.assertIn("10.000000 GE {", content)
        self.assertIn("-15.000000 GE {}", content)
        self.assertIn("2024-10-03 balance Assets:AcmeBrokerage:TaxableBrokerage 5.000000 GE", content)
        self.assertIn("2024-10-02 balance Assets:AcmeBrokerage:TaxableBrokerage -5.000000 SHORTCO", content)
        assert_ledger_loads(self, content)

    def test_subsequent_append_with_changed_and_deeper_surplus_reconciles_and_loads(self):
        """Appending a later snapshot whose window has a larger surplus or deeper deficit emits delta reconciliation and loads."""
        def holdings(as_of, qty=5.0):
            return DashboardHoldings(
                as_of_date=as_of, total_value=qty * 12.0,
                holdings=[
                    {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                     "ticker": "GE", "quantity": qty, "price": 12.0},
                ],
            )

        base_txs = [
            {"user_transaction_id": "tx-ge-1", "account_name": "Taxable Brokerage",
             "firm_name": "Acme Brokerage", "account_type": "investment",
             "transaction_date": "2024-03-01", "description": "Sell GE",
             "amount": 100.0, "is_cash_in": True, "is_credit": True,
             "transaction_type": "Sell", "symbol": "GE", "price": 10.0, "quantity": 10.0},
            {"user_transaction_id": "tx-ge-2", "account_name": "Taxable Brokerage",
             "firm_name": "Acme Brokerage", "account_type": "investment",
             "transaction_date": "2024-08-01", "description": "Buy GE",
             "amount": 120.0, "is_cash_out": True, "transaction_type": "Buy",
             "symbol": "GE", "price": 12.0, "quantity": 10.0},
        ]
        # Snapshot 1 (2024-10-01): baseline=10, floor_surplus=5 -> reconciles -5 on 2024-10-02
        txns_1 = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=2,
            money_in=100.0, money_out=120.0, net_cashflow=-20.0,
            transactions=base_txs,
        )
        # Snapshot 2a (2024-10-15): same opening lot (10), but an additional buy of 2
        # with snapshot=5 raises floor_surplus from 5 to 7 -> delta surplus = 2 on 2024-10-16.
        txns_2a = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-15", total_transactions=3,
            money_in=100.0, money_out=144.0, net_cashflow=-44.0,
            transactions=base_txs + [
                {"user_transaction_id": "tx-ge-3", "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "account_type": "investment",
                 "transaction_date": "2024-10-08", "description": "Buy GE",
                 "amount": 24.0, "is_cash_out": True, "transaction_type": "Buy",
                 "symbol": "GE", "price": 12.0, "quantity": 2.0},
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger_a = Path(tmp) / "delta_surplus.bean"
            self.generator.export_single_file(
                filepath=ledger_a, holdings=holdings("2024-10-01"), transactions=txns_1,
            )
            self.generator.export_single_file(
                filepath=ledger_a, holdings=holdings("2024-10-15"), transactions=txns_2a,
            )
            content_a = ledger_a.read_text(encoding="utf-8")
            self.assertEqual(content_a.count("Snapshot Shortfall Reconciliation"), 2)
            self.assertIn("-2.000000 GE {}", content_a)
            assert_ledger_loads(self, content_a)

            # Snapshot 2b (2024-10-15): deeper post-snapshot deficit (sell 12 on 2024-10-05,
            # buy 12 on 2024-10-10) requires a top-up lot (+7 GE) and delta reconciliation (-7 GE).
            txns_2b = DashboardTransactions(
                start_date="2024-01-01", end_date="2024-10-15", total_transactions=4,
                money_in=244.0, money_out=264.0, net_cashflow=-20.0,
                transactions=base_txs + [
                    {"user_transaction_id": "tx-ge-4", "account_name": "Taxable Brokerage",
                     "firm_name": "Acme Brokerage", "account_type": "investment",
                     "transaction_date": "2024-10-05", "description": "Sell GE",
                     "amount": 144.0, "is_cash_in": True, "is_credit": True,
                     "transaction_type": "Sell", "symbol": "GE", "price": 12.0, "quantity": 12.0},
                    {"user_transaction_id": "tx-ge-5", "account_name": "Taxable Brokerage",
                     "firm_name": "Acme Brokerage", "account_type": "investment",
                     "transaction_date": "2024-10-10", "description": "Buy GE",
                     "amount": 144.0, "is_cash_out": True, "transaction_type": "Buy",
                     "symbol": "GE", "price": 12.0, "quantity": 12.0},
                ],
            )
            ledger_b = Path(tmp) / "deeper_deficit.bean"
            self.generator.export_single_file(
                filepath=ledger_b, holdings=holdings("2024-10-01"), transactions=txns_1,
            )
            self.generator.export_single_file(
                filepath=ledger_b, holdings=holdings("2024-10-15"), transactions=txns_2b,
            )
            content_b = ledger_b.read_text(encoding="utf-8")
            self.assertEqual(content_b.count("Snapshot Shortfall Reconciliation"), 2)
            assert_ledger_loads(self, content_b)

    def test_later_snapshot_without_new_reconciliation_asserts_at_d_plus_1_and_allows_next_day_trade(self):
        """When a later append emits no new reconciliation, its assertion stays at D+1 so a D+1 trade on a third append loads."""
        def holdings(as_of, qty):
            return DashboardHoldings(
                as_of_date=as_of, total_value=qty * 12.0,
                holdings=[
                    {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                     "ticker": "GE", "quantity": qty, "price": 12.0},
                ],
            )

        base_txs = [
            {"user_transaction_id": "tx-ge-1", "account_name": "Taxable Brokerage",
             "firm_name": "Acme Brokerage", "account_type": "investment",
             "transaction_date": "2024-03-01", "description": "Sell GE",
             "amount": 100.0, "is_cash_in": True, "is_credit": True,
             "transaction_type": "Sell", "symbol": "GE", "price": 10.0, "quantity": 10.0},
            {"user_transaction_id": "tx-ge-2", "account_name": "Taxable Brokerage",
             "firm_name": "Acme Brokerage", "account_type": "investment",
             "transaction_date": "2024-08-01", "description": "Buy GE",
             "amount": 120.0, "is_cash_out": True, "transaction_type": "Buy",
             "symbol": "GE", "price": 12.0, "quantity": 10.0},
        ]
        txns_1 = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=2,
            money_in=100.0, money_out=120.0, net_cashflow=-20.0, transactions=base_txs,
        )
        txns_2 = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-15", total_transactions=2,
            money_in=100.0, money_out=120.0, net_cashflow=-20.0, transactions=base_txs,
        )
        txns_3 = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-20", total_transactions=3,
            money_in=100.0, money_out=240.0, net_cashflow=-140.0,
            transactions=base_txs + [
                {"user_transaction_id": "tx-ge-3", "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "account_type": "investment",
                 "transaction_date": "2024-10-16", "description": "Buy GE",
                 "amount": 120.0, "is_cash_out": True, "transaction_type": "Buy",
                 "symbol": "GE", "price": 12.0, "quantity": 10.0},
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.bean"
            self.generator.export_single_file(
                filepath=ledger, holdings=holdings("2024-10-01", 5.0), transactions=txns_1,
            )
            self.generator.export_single_file(
                filepath=ledger, holdings=holdings("2024-10-15", 5.0), transactions=txns_2,
            )
            self.generator.export_single_file(
                filepath=ledger, holdings=holdings("2024-10-20", 15.0), transactions=txns_3,
            )
            content = ledger.read_text(encoding="utf-8")
        self.assertIn("2024-10-16 balance Assets:AcmeBrokerage:TaxableBrokerage 5.000000 GE", content)
        self.assertNotIn("2024-10-17 balance", content)
        assert_ledger_loads(self, content)

    def test_pre_cutoff_deeper_deficit_on_append_raises_ledger_append_error(self):
        """A later append introducing a deeper deficit before an existing assertion cutoff raises LedgerAppendError."""
        def holdings(as_of):
            return DashboardHoldings(
                as_of_date=as_of, total_value=60.0,
                holdings=[
                    {"account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
                     "ticker": "GE", "quantity": 5.0, "price": 12.0},
                ],
            )

        base_txs = [
            {"user_transaction_id": "tx-ge-1", "account_name": "Taxable Brokerage",
             "firm_name": "Acme Brokerage", "account_type": "investment",
             "transaction_date": "2024-03-01", "description": "Sell GE",
             "amount": 100.0, "is_cash_in": True, "is_credit": True,
             "transaction_type": "Sell", "symbol": "GE", "price": 10.0, "quantity": 10.0},
            {"user_transaction_id": "tx-ge-2", "account_name": "Taxable Brokerage",
             "firm_name": "Acme Brokerage", "account_type": "investment",
             "transaction_date": "2024-08-01", "description": "Buy GE",
             "amount": 120.0, "is_cash_out": True, "transaction_type": "Buy",
             "symbol": "GE", "price": 12.0, "quantity": 10.0},
        ]
        txns_1 = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-01", total_transactions=2,
            money_in=100.0, money_out=120.0, net_cashflow=-20.0, transactions=base_txs,
        )
        txns_2 = DashboardTransactions(
            start_date="2024-01-01", end_date="2024-10-15", total_transactions=4,
            money_in=250.0, money_out=270.0, net_cashflow=-20.0,
            transactions=[
                {"user_transaction_id": "tx-ge-0a", "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "account_type": "investment",
                 "transaction_date": "2024-02-01", "description": "Sell GE",
                 "amount": 150.0, "is_cash_in": True, "is_credit": True,
                 "transaction_type": "Sell", "symbol": "GE", "price": 10.0, "quantity": 15.0},
                {"user_transaction_id": "tx-ge-0b", "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "account_type": "investment",
                 "transaction_date": "2024-02-15", "description": "Buy GE",
                 "amount": 150.0, "is_cash_out": True, "transaction_type": "Buy",
                 "symbol": "GE", "price": 10.0, "quantity": 15.0},
            ] + base_txs,
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "pre_cutoff.bean"
            self.generator.export_single_file(
                filepath=ledger, holdings=holdings("2024-10-01"), transactions=txns_1,
                opening_date="2020-01-01",
            )
            before_single = ledger.read_text(encoding="utf-8")
            with self.assertRaises(LedgerAppendError):
                self.generator.export_single_file(
                    filepath=ledger, holdings=holdings("2024-10-15"), transactions=txns_2,
                    opening_date="2020-01-01",
                )
            self.assertEqual(ledger.read_text(encoding="utf-8"), before_single)
            assert_ledger_loads(self, before_single)

            mod_dir = Path(tmp) / "modular"
            self.generator.export_modular_ledger(
                destination_dir=mod_dir, holdings=holdings("2024-10-01"), transactions=txns_1,
                opening_date="2020-01-01",
            )
            before_balances = (mod_dir / "balances.bean").read_text(encoding="utf-8")
            with self.assertRaises(LedgerAppendError):
                self.generator.export_modular_ledger(
                    destination_dir=mod_dir, holdings=holdings("2024-10-15"), transactions=txns_2,
                    opening_date="2020-01-01",
                )
            self.assertEqual((mod_dir / "balances.bean").read_text(encoding="utf-8"), before_balances)


class TestBackwardAppend(_BaseGeneratorTest):
    """Item 3 — an append that broadens the window into the past is rejected."""

    def setUp(self):
        super().setUp()
        self.balances = _brokerage_balances()
        self.holdings = _vti_holdings(quantity=60.0, price=280.0, cost_basis=12600.0)

    def _txns(self, start, end, date):
        return DashboardTransactions(
            start_date=start, end_date=end, total_transactions=1,
            money_in=0.0, money_out=4000.0, net_cashflow=-4000.0,
            transactions=[
                {"user_transaction_id": f"tx-{date}", "account_id": "ACC-BRK-001",
                 "user_account_id": 5001, "account_name": "Taxable Brokerage",
                 "firm_name": "Acme Brokerage", "transaction_date": date,
                 "description": "Buy VTI", "amount": 4000.0, "is_cash_out": True,
                 "transaction_type": "Buy", "investment_type": "Buy",
                 "symbol": "VTI", "price": 200.0, "quantity": 20.0},
            ],
        )

    def test_backward_broadened_append_is_rejected(self):
        """Appends that broaden the window into the past are rejected before modifying files."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "ledger"
            self.generator.export_modular_ledger(
                destination_dir=dest, balances=self.balances, holdings=self.holdings,
                transactions=self._txns("2022-01-01", "2024-10-01", "2022-06-15"),
                opening_date="2022-01-01",
            )
            # A second export whose opening lot predates the persisted window start
            # must be rejected rather than silently leaving the stale opening lot.
            with self.assertRaises(LedgerAppendError):
                self.generator.export_modular_ledger(
                    destination_dir=dest, balances=self.balances, holdings=self.holdings,
                    transactions=self._txns("2020-01-01", "2024-10-01", "2020-06-15"),
                    opening_date="2020-01-01",
                )

    def test_markerless_single_file_with_cash_pad_still_rejects_backward_append(self):
        """A marker-less single-file ledger with a 2020-01-01 cash pad still enforces a 2022 opening-lot start."""
        balances_with_cash = DashboardBalances(
            as_of_date="2024-10-01",
            net_worth=25100.0,
            total_cash=5000.0,
            total_investment=20100.0,
            total_card_liabilities=0.0,
            total_loan=0.0,
            total_mortgage=0.0,
            accounts=[
                self.balances.accounts[0],
                {
                    "account_id": "ACC-CASH-001",
                    "account_name": "Everyday Checking",
                    "firm_name": "Acme Bank",
                    "account_type": "cash",
                    "balance": 5000.0,
                    "is_asset": True,
                    "currency": "USD",
                    "user_account_id": 5002,
                },
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "legacy.bean"
            self.generator.export_single_file(
                filepath=ledger, balances=balances_with_cash, holdings=self.holdings,
                transactions=self._txns("2022-01-01", "2024-10-01", "2022-06-15"),
                opening_date="2022-06-01",
            )
            # Strip comment headers (including empower_window_start) to simulate a
            # legacy marker-less single-file ledger that contains a 2020-01-01 cash pad.
            stripped = "\n".join(
                line for line in ledger.read_text(encoding="utf-8").splitlines()
                if not line.strip().startswith(";;")
            ) + "\n"
            self.assertIn("2020-01-01 pad Assets:AcmeBank:EverydayChecking Equity:Opening-Balances", stripped)
            ledger.write_text(stripped, encoding="utf-8")

            with self.assertRaises(LedgerAppendError):
                self.generator.export_single_file(
                    filepath=ledger, balances=balances_with_cash, holdings=self.holdings,
                    transactions=self._txns("2021-01-01", "2024-10-01", "2021-06-15"),
                    opening_date="2021-01-01",
                )

    def test_forward_append_still_works_and_loads(self):
        """Forward appends with matching window bounds load cleanly."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "ledger"
            self.generator.export_modular_ledger(
                destination_dir=dest, balances=self.balances, holdings=self.holdings,
                transactions=self._txns("2020-01-01", "2024-10-01", "2020-06-15"),
                opening_date="2020-01-01",
            )
            # Re-exporting the same data with an equal window start appends
            # cleanly (the transaction is a duplicate, so no new lot or trade is
            # added) and the ledger still balances.
            self.generator.export_modular_ledger(
                destination_dir=dest, balances=self.balances, holdings=self.holdings,
                transactions=self._txns("2020-01-01", "2024-10-01", "2020-06-15"),
                opening_date="2020-01-01",
            )
            main = (dest / "main.bean").read_text(encoding="utf-8")
            # Resolve includes into one string for the loader.
            combined = []
            for inc in ["accounts.bean", "holdings.bean", "balances.bean",
                        "prices.bean", "transactions.bean"]:
                combined.append((dest / inc).read_text(encoding="utf-8"))
            header = "\n".join(
                line for line in main.splitlines() if not line.startswith("include")
            )
            assert_ledger_loads(self, header + "\n" + "\n".join(combined))


class TestIncompleteWindowReconciliation(_BaseGeneratorTest):
    """Integration test for incomplete-window reconciliation with real Beancount loader."""

    def setUp(self):
        super().setUp()
        from empower_personal_dashboard.client import EmpowerDashboardClient
        self.client = EmpowerDashboardClient(mock_mode=True)

    def test_incomplete_window_extend_policy_generates_loadable_ledger(self):
        """Incomplete-window extend policy generates a ledger that loads cleanly.

        When transactions end before the snapshot date, the client reconciles
        the window by extending the fetch through the snapshot. The resulting
        ledger must load without Beancount errors and balance.
        """
        # Create holdings snapshot at 2024-10-01 with VTI position
        balances = _brokerage_balances(as_of="2024-10-01", balance=20100.0)
        holdings = _vti_holdings()
        # Incomplete transactions: end_date is before holdings.as_of_date
        incomplete_txns = _vti_buy_transaction(start="2021-01-01", end="2024-06-30")
        # Reconcile by extending the window to the snapshot date
        reconciled_txns = self.client.reconcile_transaction_window(
            incomplete_txns, holdings, policy="extend", limited=False,
        )
        # The reconciled window must now reach the snapshot date
        self.assertEqual(reconciled_txns.end_date, holdings.as_of_date)
        # Generate a ledger with the extended transactions
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "extended.bean"
            self.generator.export_single_file(
                filepath=target, balances=balances, holdings=holdings,
                transactions=reconciled_txns, opening_date="2021-01-01",
            )
            content = target.read_text(encoding="utf-8")
        # The generated ledger must load cleanly without Beancount errors
        assert_ledger_loads(self, content)

    def test_sell_with_omitted_amount_and_price_loads_without_capital_loss(self):
        """A sell that omits both price and amount loads cleanly without inferring a 100% capital loss."""
        holdings = _vti_holdings(as_of="2024-10-01", quantity=50.0, price=280.0, cost_basis=10000.0)
        txns = DashboardTransactions(
            start_date="2024-01-01",
            end_date="2024-10-01",
            total_transactions=2,
            money_in=0.0,
            money_out=0.0,
            net_cashflow=0.0,
            transactions=[
                {
                    "user_transaction_id": "tx-unpriced-sell-1",
                    "account_id": "ACC-BRK-001",
                    "user_account_id": 5001,
                    "account_name": "Taxable Brokerage",
                    "firm_name": "Acme Brokerage",
                    "transaction_date": "2024-06-15",
                    "description": "Sell VTI Unpriced",
                    "amount": None,
                    "transaction_type": "Sell",
                    "investment_type": "Sell",
                    "symbol": "VTI",
                    "price": None,
                    "quantity": 10.0,
                },
                {
                    "user_transaction_id": "tx-unpriced-sell-2",
                    "account_id": "ACC-BRK-001",
                    "user_account_id": 5001,
                    "account_name": "Taxable Brokerage",
                    "firm_name": "Acme Brokerage",
                    "transaction_date": "2024-07-01",
                    "description": "Dispose SOLDCO Unpriced",
                    "amount": 0.0,
                    "transaction_type": "Disposal",
                    "investment_type": "Sell",
                    "symbol": "SOLDCO",
                    "price": 0.0,
                    "quantity": 5.0,
                },
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "unpriced_sell.bean"
            self.generator.export_single_file(
                filepath=target,
                balances=_brokerage_balances(as_of="2024-10-01", balance=14000.0),
                holdings=holdings,
                transactions=txns,
                opening_date="2020-01-01",
            )
            content = target.read_text(encoding="utf-8")
        self.assertNotIn("@ 0.0000 USD", content)
        self.assertNotIn("\n  Income:CapitalGains\n", content)
        self.assertIn("\n  Equity:Transfers\n", content)
        entries = assert_ledger_loads(self, content)
        for entry in entries:
            if getattr(entry, "narration", "") in ("Sell VTI Unpriced", "Dispose SOLDCO Unpriced"):
                for posting in entry.postings:
                    if posting.account == "Assets:AcmeBrokerage:TaxableBrokerage":
                        self.assertNotEqual(posting.units.currency, "USD")


if __name__ == "__main__":
    unittest.main()
