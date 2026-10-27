"""
test_beancount_helpers.py — Direct unit tests for Beancount classifier helpers.

These target the pure helpers mutated in the mutation pilot (Discussion #123):
_safe_float family, _clean_tx_description, _has_core_sweep_evidence,
_is_dividend_signal, _classify_buy_sell, _is_commodity_trade_tx,
_has_investment_account_signal, and _is_investment_tx. Cases are chosen to
pin input normalization: missing fields, case handling, category fallback,
and custom sweep ticker forwarding. All data is synthetic.
"""

import unittest

from empower_personal_dashboard.beancount import (
    _classify_buy_sell,
    _clean_tx_description,
    _has_core_sweep_evidence,
    _has_investment_account_signal,
    _is_commodity_trade_tx,
    _is_dividend_signal,
    _is_investment_tx,
    _is_negative_number,
    _safe_float,
    _safe_positive_float,
)


class TestSafeFloatHelpers(unittest.TestCase):
    def test_safe_float_parsing(self):
        self.assertEqual(_safe_float("12.5"), 12.5)
        self.assertEqual(_safe_float(-3), -3.0)
        self.assertEqual(_safe_float(None), 0.0)
        self.assertEqual(_safe_float(True), 0.0)
        self.assertEqual(_safe_float("not-a-number"), 0.0)
        self.assertEqual(_safe_float(float("inf")), 0.0)
        self.assertEqual(_safe_float(float("nan")), 0.0)

    def test_safe_positive_and_negative(self):
        self.assertEqual(_safe_positive_float("-42.5"), 42.5)
        self.assertEqual(_safe_positive_float("42.5"), 42.5)
        self.assertEqual(_safe_positive_float(None), 0.0)
        self.assertTrue(_is_negative_number("-0.01"))
        self.assertFalse(_is_negative_number("0"))
        self.assertFalse(_is_negative_number("5"))
        self.assertFalse(_is_negative_number(None))


class TestCleanTxDescription(unittest.TestCase):
    def test_combines_distinct_original(self):
        tx = {"description": "Coffee Shop", "original_description": "COFFEE NYC"}
        self.assertEqual(_clean_tx_description(tx), "coffee shop coffee nyc")

    def test_original_case_is_lowered(self):
        tx = {"description": "Store", "original_description": "STORE ABC"}
        self.assertEqual(_clean_tx_description(tx), "store store abc")

    def test_empty_or_equal_original_returns_primary(self):
        self.assertEqual(
            _clean_tx_description({"description": "Store", "original_description": ""}),
            "store",
        )
        self.assertEqual(
            _clean_tx_description({"description": "Store", "original_description": "STORE"}),
            "store",
        )
        self.assertEqual(_clean_tx_description({"description": "Store"}), "store")

    def test_missing_description_uses_original_only(self):
        tx = {"original_description": "PAYROLL ACME"}
        self.assertEqual(_clean_tx_description(tx), "payroll acme")

    def test_core_phrase_in_primary_suppresses_original(self):
        tx = {
            "description": "Purchase into core account",
            "original_description": "SOMETHING ELSE",
        }
        self.assertEqual(_clean_tx_description(tx), "purchase into core account")
        tx2 = {
            "description": "Redemption from core account",
            "original_description": "SOMETHING ELSE",
        }
        self.assertEqual(_clean_tx_description(tx2), "redemption from core account")


class TestDividendAndSweepSignals(unittest.TestCase):
    def test_dividend_signal_sources(self):
        self.assertTrue(_is_dividend_signal("dividend", "", "", ""))
        self.assertTrue(_is_dividend_signal("", "qualified dividend", "", ""))
        self.assertFalse(_is_dividend_signal("buy", "buy", "", "VTI"))

    def test_dividend_category_needs_ticker_and_type(self):
        # Category-only dividend: needs a ticker plus a type field.
        self.assertTrue(_is_dividend_signal("income", "", "dividend", "VTI"))
        self.assertTrue(_is_dividend_signal("", "income", "dividend", "VTI"))
        self.assertFalse(_is_dividend_signal("", "", "dividend", "VTI"))
        self.assertFalse(_is_dividend_signal("income", "", "dividend", ""))

    def test_core_sweep_evidence(self):
        self.assertTrue(_has_core_sweep_evidence("SPAXX", 1.0, ""))
        self.assertTrue(_has_core_sweep_evidence("CASH", 1.0, ""))
        self.assertTrue(_has_core_sweep_evidence("VTI", 1.0, "money market fund"))
        self.assertTrue(
            _has_core_sweep_evidence("MYFUND", 1.0, "", custom_sweep_tickers={"MYFUND"})
        )
        self.assertFalse(
            _has_core_sweep_evidence("MYFUND", 1.0, "", custom_sweep_tickers={"OTHER"})
        )
        self.assertFalse(_has_core_sweep_evidence("VTI", 275.0, "money market"))
        self.assertFalse(_has_core_sweep_evidence("VTI", 1.0, ""))
        # Boundary: 9-decimal rounding decides the $1.00 NAV tolerance.
        self.assertTrue(_has_core_sweep_evidence("SPAXX", 1.0100000004, ""))


class TestClassifyBuySell(unittest.TestCase):
    def test_explicit_types_in_either_field(self):
        self.assertEqual(_classify_buy_sell("buy", "", "VTI", 10, price=100.0), (True, False))
        self.assertEqual(_classify_buy_sell("reinvest", "", "VTI", 10, price=100.0), (True, False))
        self.assertEqual(_classify_buy_sell("", "reinvest", "VTI", 10, price=100.0), (True, False))
        self.assertEqual(_classify_buy_sell("sell", "", "VTI", 10, price=100.0), (False, True))
        self.assertEqual(_classify_buy_sell("disposal", "", "VTI", 10, price=100.0), (False, True))
        self.assertEqual(_classify_buy_sell("", "disposal", "VTI", 10, price=100.0), (False, True))

    def test_conflicting_explicit_types_are_neither(self):
        self.assertEqual(
            _classify_buy_sell("buy", "sell", "VTI", 10, price=100.0), (False, False)
        )

    def test_requires_ticker_and_positive_qty_and_price(self):
        self.assertEqual(_classify_buy_sell("buy", "", "", 10, price=100.0), (False, False))
        self.assertEqual(_classify_buy_sell("buy", "", "VTI", 0, price=100.0), (False, False))
        self.assertEqual(_classify_buy_sell("", "", "VTI", 10, price=0.0), (False, False))

    def test_dividend_is_not_a_trade(self):
        self.assertEqual(
            _classify_buy_sell("dividend", "", "VTI", 10, price=100.0), (False, False)
        )

    def test_default_arguments_mean_no_price_no_trade(self):
        self.assertEqual(_classify_buy_sell("shares out", "", "SPAXX", 10), (False, False))

    def test_core_phrases(self):
        self.assertEqual(
            _classify_buy_sell("", "", "SPAXX", 10, price=1.0,
                               desc_clean="purchase into core account"),
            (True, False),
        )
        self.assertEqual(
            _classify_buy_sell("", "", "SPAXX", 10, price=1.0,
                               desc_clean="redemption from core account"),
            (False, True),
        )

    def test_both_core_phrases_cancel_out(self):
        desc = "purchase into core account redemption from core account"
        self.assertEqual(
            _classify_buy_sell("", "", "SPAXX", 10, price=1.0, desc_clean=desc),
            (False, False),
        )

    def test_explicit_buy_via_investment_type_only(self):
        self.assertEqual(_classify_buy_sell("", "buy", "VTI", 10, price=100.0), (True, False))

    def test_shares_in_never_a_core_buy(self):
        # "shares in" is excluded from core buys even with a buy phrase.
        self.assertEqual(
            _classify_buy_sell("shares in", "", "VTI", 10, price=100.0,
                               desc_clean="purchase into core account"),
            (False, False),
        )

    def test_shares_in_out_with_sweep_evidence(self):
        self.assertEqual(
            _classify_buy_sell("shares out", "", "SPAXX", 10, price=1.0),
            (True, False),
        )
        self.assertEqual(
            _classify_buy_sell("shares in", "", "SPAXX", 10, price=1.0),
            (False, True),
        )
        # Without sweep evidence, bare shares in/out are not trades.
        self.assertEqual(
            _classify_buy_sell("shares out", "", "VTI", 10, price=100.0),
            (False, False),
        )


class TestCommodityAndInvestmentTx(unittest.TestCase):
    def test_trade_by_transaction_type_case_insensitive(self):
        tx = {"symbol": "VTI", "transaction_type": "Buy", "quantity": 10, "price": 100}
        self.assertTrue(_is_commodity_trade_tx(tx))
        self.assertTrue(_is_investment_tx(tx))

    def test_trade_by_investment_type_only(self):
        tx = {"symbol": "VTI", "investment_type": "Sell", "quantity": 10, "price": 100}
        self.assertTrue(_is_commodity_trade_tx(tx))

    def test_no_ticker_no_trade(self):
        tx = {"transaction_type": "Buy", "quantity": 10, "price": 100}
        self.assertFalse(_is_commodity_trade_tx(tx))

    def test_symbol_less_dividend_is_investment(self):
        self.assertTrue(_is_investment_tx({"transaction_type": "Dividend"}))
        self.assertTrue(_is_investment_tx({"investment_type": "Dividend"}))

    def test_category_dividend_with_ticker_and_type(self):
        tx = {
            "symbol": "VTI",
            "transaction_type": "Income",
            "category": "Dividend",
            "quantity": 0,
            "price": 0,
        }
        self.assertTrue(_is_investment_tx(tx))
        tx_name = {
            "symbol": "VTI",
            "transaction_type": "Income",
            "category_name": "Dividend",
            "quantity": 0,
            "price": 0,
        }
        self.assertTrue(_is_investment_tx(tx_name))

    def test_custom_sweep_tickers_are_forwarded(self):
        tx = {
            "symbol": "MYFUND",
            "transaction_type": "Shares Out",
            "quantity": 10,
            "price": 1.0,
        }
        self.assertTrue(
            _is_commodity_trade_tx(tx, custom_sweep_tickers={"MYFUND"})
        )
        self.assertTrue(_is_investment_tx(tx, custom_sweep_tickers={"MYFUND"}))
        self.assertFalse(_is_commodity_trade_tx(tx))

    def test_trade_by_description_phrase(self):
        tx = {
            "symbol": "SPAXX",
            "description": "Purchase into core account",
            "quantity": 10,
            "price": 1.0,
        }
        self.assertTrue(_is_commodity_trade_tx(tx))

    def test_dividend_category_blocks_trade_via_category_fields(self):
        # A dividend category (either field) stops a phrase trade being a buy.
        base = {
            "symbol": "VTI",
            "transaction_type": "Income",
            "description": "Purchase into core account",
            "quantity": 10,
            "price": 100,
        }
        self.assertFalse(_is_commodity_trade_tx({**base, "category": "Dividend"}))
        self.assertFalse(_is_commodity_trade_tx({**base, "category_name": "Dividend"}))
        self.assertTrue(_is_commodity_trade_tx(base))

    def test_category_dividend_without_any_type_is_not_investment(self):
        # _is_dividend_signal requires a type field for category-only
        # dividends. Absent both, this is not an investment tx — and the
        # "XXXX" fallback mutants flip bool(tx or inv), so this pins it.
        self.assertFalse(_is_investment_tx({"symbol": "VTI", "category": "Dividend"}))
        self.assertFalse(
            _is_commodity_trade_tx(
                {
                    "symbol": "VTI",
                    "category": "Dividend",
                    "description": "Coffee Shop",
                    "quantity": 10,
                    "price": 100,
                }
            )
        )

    def test_category_dividend_without_type_does_not_block_phrase_trade(self):
        # Same guard from the trade side: with no type field, a category-only
        # dividend does not suppress a core purchase phrase trade.
        tx = {
            "symbol": "VTI",
            "category": "Dividend",
            "description": "Purchase into core account",
            "quantity": 10,
            "price": 100,
        }
        self.assertTrue(_is_commodity_trade_tx(tx))

    def test_account_signal_fallback(self):
        self.assertTrue(_is_investment_tx({"account_type": "Brokerage Account"}))
        self.assertTrue(_is_investment_tx({"account_name": "My Roth IRA"}))
        self.assertTrue(
            _has_investment_account_signal({"account_type": "Investment"})
        )
        self.assertFalse(
            _has_investment_account_signal(
                {"account_type": "Checking", "account_name": "Everyday"}
            )
        )
        self.assertFalse(_is_investment_tx({"account_type": "Checking"}))

    def test_plain_bank_transaction_is_not_investment(self):
        tx = {
            "symbol": "",
            "transaction_type": "Purchase",
            "description": "Coffee Shop",
            "quantity": 0,
            "price": 0,
            "account_type": "Bank",
        }
        self.assertFalse(_is_investment_tx(tx))


if __name__ == "__main__":
    unittest.main()
