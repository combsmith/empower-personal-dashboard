"""
test_models.py — Unit tests for data models.
"""

import unittest
from empower_personal_dashboard.models import (
    AccountBalance,
    DashboardBalances,
    InvestmentHolding,
    DashboardHoldings,
    Transaction,
    DashboardTransactions,
)


class TestModels(unittest.TestCase):
    def test_account_balance_model(self):
        acct = AccountBalance(
            account_id="ACC-001",
            account_name="Primary Checking",
            firm_name="Apex Bank",
            account_type="BANK",
            balance=15000.50,
        )
        d = acct.to_dict()
        self.assertEqual(d["account_id"], "ACC-001")
        self.assertEqual(d["balance"], 15000.50)
        self.assertTrue(d["is_asset"])

    def test_dashboard_balances_model(self):
        bals = DashboardBalances(
            as_of_date="2026-09-30",
            net_worth=500000.0,
            total_cash=50000.0,
            total_investment=460000.0,
            total_card_liabilities=10000.0,
            total_loan=0.0,
            total_mortgage=0.0,
            accounts=[{"account_name": "Checking", "balance": 50000.0}],
        )
        d = bals.to_dict()
        self.assertEqual(d["net_worth"], 500000.0)
        self.assertEqual(d["accounts_count"], 1)

    def test_holdings_model(self):
        holding = InvestmentHolding(
            user_account_id=101,
            account_name="Retirement 401(k)",
            ticker="VTI",
            cusip="922908769",
            description="Total Stock Market ETF",
            holding_type="ETF",
            quantity=100.0,
            price=275.0,
            value=27500.0,
        )
        d = holding.to_dict()
        self.assertEqual(d["ticker"], "VTI")
        self.assertEqual(d["value"], 27500.0)

        dashboard_holdings = DashboardHoldings(
            as_of_date="2026-09-30",
            total_value=27500.0,
            holdings=[d],
        )
        hd = dashboard_holdings.to_dict()
        self.assertEqual(hd["total_value"], 27500.0)
        self.assertEqual(hd["holdings_count"], 1)

    def test_transaction_model(self):
        tx = Transaction(
            user_transaction_id="TXN-101",
            account_id="ACC-001",
            user_account_id=101,
            account_name="Primary Checking",
            transaction_date="2026-09-15",
            description="Coffee Shop",
            original_description="COFFEE SHOP 123",
            amount=4.75,
            is_credit=False,
            is_cash_in=False,
            is_cash_out=True,
            is_income=False,
            is_spending=True,
            transaction_type="Purchase",
        )
        d = tx.to_dict()
        self.assertEqual(d["amount"], 4.75)
        self.assertEqual(d["description"], "Coffee Shop")

        dt = DashboardTransactions(
            start_date="2026-09-01",
            end_date="2026-09-30",
            total_transactions=1,
            money_in=0.0,
            money_out=4.75,
            net_cashflow=-4.75,
            transactions=[d],
        )
        dtd = dt.to_dict()
        self.assertEqual(dtd["net_cashflow"], -4.75)

    def test_account_balance_to_dict_exact(self):
        # Exact-dict assertion: kills wire-key and rounding mutants that
        # per-field spot checks leave alive (mutation pilot, Discussion #123).
        acct = AccountBalance(
            account_id="ACC-001",
            account_name="Primary Checking",
            firm_name="Apex Bank",
            account_type="BANK",
            balance=15000.506,
            is_asset=False,
            currency="EUR",
            last_refreshed="2026-09-30T08:00:00",
            user_account_id=101,
        )
        self.assertEqual(
            acct.to_dict(),
            {
                "account_id": "ACC-001",
                "account_name": "Primary Checking",
                "firm_name": "Apex Bank",
                "account_type": "BANK",
                "balance": 15000.51,
                "is_asset": False,
                "currency": "EUR",
                "last_refreshed": "2026-09-30T08:00:00",
                "user_account_id": 101,
            },
        )

    def test_dashboard_balances_to_dict_exact_and_from_dict(self):
        accounts = [{"account_name": "Checking", "balance": 50000.0}]
        bals = DashboardBalances(
            as_of_date="2026-09-30",
            net_worth=500000.456,
            total_cash=50000.456,
            total_investment=460000.456,
            total_card_liabilities=10000.456,
            total_loan=2000.456,
            total_mortgage=300000.456,
            accounts=accounts,
            total_other_assets=700.456,
            total_other_liabilities=800.456,
            mode="sandbox_mock",
        )
        self.assertEqual(
            bals.to_dict(),
            {
                "as_of_date": "2026-09-30",
                "net_worth": 500000.46,
                "total_cash": 50000.46,
                "total_investment": 460000.46,
                "total_credit_card": 10000.46,
                "total_loan": 2000.46,
                "total_mortgage": 300000.46,
                "total_other_assets": 700.46,
                "total_other_liabilities": 800.46,
                "accounts_count": 1,
                "accounts": accounts,
                "mode": "sandbox_mock",
            },
        )
        restored = DashboardBalances.from_dict(bals.to_dict())
        self.assertEqual(restored.total_card_liabilities, 10000.46)
        self.assertEqual(restored.accounts, accounts)
        empty = DashboardBalances.from_dict({})
        self.assertEqual(empty.as_of_date, "")
        self.assertEqual(empty.net_worth, 0.0)
        self.assertEqual(empty.total_card_liabilities, 0.0)
        self.assertEqual(empty.accounts, [])
        self.assertEqual(empty.mode, "historical")

    def test_investment_holding_to_dict_exact(self):
        holding = InvestmentHolding(
            user_account_id=101,
            account_name="Retirement 401(k)",
            ticker="VTI",
            cusip="922908769",
            description="Total Stock Market ETF",
            holding_type="ETF",
            quantity=100.125,
            price=275.506,
            value=27500.506,
            cost_basis=20000.506,
            holding_percentage=55.506,
            one_day_percent_change=0.506,
            one_day_value_change=139.506,
        )
        self.assertEqual(
            holding.to_dict(),
            {
                "user_account_id": 101,
                "account_name": "Retirement 401(k)",
                "ticker": "VTI",
                "cusip": "922908769",
                "description": "Total Stock Market ETF",
                "holding_type": "ETF",
                "quantity": 100.125,
                "price": 275.51,
                "value": 27500.51,
                "cost_basis": 20000.51,
                "holding_percentage": 55.51,
                "one_day_percent_change": 0.506,
                "one_day_value_change": 139.51,
            },
        )
        bare = InvestmentHolding(
            user_account_id=None, account_name="A", ticker="VTI", cusip="C",
            description="D", holding_type="ETF", quantity=1.0, price=2.0, value=2.0,
        )
        d = bare.to_dict()
        self.assertIsNone(d["cost_basis"])
        self.assertIsNone(d["holding_percentage"])
        self.assertIsNone(d["one_day_value_change"])
        hd = DashboardHoldings(
            as_of_date="2026-09-30", total_value=27500.506, holdings=[d], mode="sandbox_mock"
        ).to_dict()
        self.assertEqual(
            hd,
            {
                "as_of_date": "2026-09-30",
                "total_value": 27500.51,
                "holdings_count": 1,
                "holdings": [d],
                "mode": "sandbox_mock",
            },
        )
        restored = DashboardHoldings.from_dict({})
        self.assertEqual(restored.as_of_date, "")
        self.assertEqual(restored.total_value, 0.0)
        self.assertEqual(restored.holdings, [])
        self.assertEqual(restored.mode, "historical")

    def test_transaction_to_dict_exact_and_from_dict(self):
        tx = Transaction(
            user_transaction_id="TXN-101",
            account_id="ACC-001",
            user_account_id=101,
            account_name="Primary Checking",
            transaction_date="2026-09-15",
            description="Coffee Shop",
            original_description="COFFEE SHOP 123",
            amount=4.756,
            is_credit=True,
            is_cash_in=True,
            is_cash_out=False,
            is_income=True,
            is_spending=False,
            transaction_type="Purchase",
            investment_type="Buy",
            symbol="VTI",
            price=275.506,
            quantity=2.5,
            status="pending",
            category_id=7,
            category="Dining",
            category_name="Restaurants",
        )
        self.assertEqual(
            tx.to_dict(),
            {
                "user_transaction_id": "TXN-101",
                "account_id": "ACC-001",
                "user_account_id": 101,
                "account_name": "Primary Checking",
                "transaction_date": "2026-09-15",
                "description": "Coffee Shop",
                "original_description": "COFFEE SHOP 123",
                "amount": 4.76,
                "is_credit": True,
                "is_cash_in": True,
                "is_cash_out": False,
                "is_income": True,
                "is_spending": False,
                "transaction_type": "Purchase",
                "investment_type": "Buy",
                "symbol": "VTI",
                "price": 275.506,
                "quantity": 2.5,
                "status": "pending",
                "category_id": 7,
                "category": "Dining",
                "category_name": "Restaurants",
            },
        )
        dtd = DashboardTransactions(
            start_date="2026-09-01", end_date="2026-09-30", total_transactions=1,
            money_in=100.456, money_out=4.756, net_cashflow=95.706,
            transactions=[tx.to_dict()], mode="sandbox_mock",
        ).to_dict()
        self.assertEqual(dtd["money_in"], 100.46)
        self.assertEqual(dtd["money_out"], 4.76)
        self.assertEqual(dtd["net_cashflow"], 95.71)
        self.assertEqual(dtd["total_transactions"], 1)
        restored = DashboardTransactions.from_dict(
            {"transactions": [tx.to_dict()]}
        )
        self.assertEqual(restored.total_transactions, 1)
        self.assertEqual(restored.money_in, 0.0)
        self.assertEqual(restored.mode, "historical")
        self.assertTrue(restored.is_complete)

    def test_from_dict_maps_every_field(self):
        # Distinct value per field: kills from_dict key, default, and
        # drop-to-None mutants that spot checks leave alive.
        bals = DashboardBalances.from_dict(
            {
                "as_of_date": "2026-09-30",
                "net_worth": 1.11,
                "total_cash": 2.22,
                "total_investment": 3.33,
                "total_credit_card": 4.44,
                "total_loan": 5.55,
                "total_mortgage": 6.66,
                "total_other_assets": 7.77,
                "total_other_liabilities": 8.88,
                "accounts": [{"account_name": "Checking"}],
                "mode": "sandbox_mock",
            }
        )
        self.assertEqual(bals.as_of_date, "2026-09-30")
        self.assertEqual(bals.net_worth, 1.11)
        self.assertEqual(bals.total_cash, 2.22)
        self.assertEqual(bals.total_investment, 3.33)
        self.assertEqual(bals.total_card_liabilities, 4.44)
        self.assertEqual(bals.total_loan, 5.55)
        self.assertEqual(bals.total_mortgage, 6.66)
        self.assertEqual(bals.total_other_assets, 7.77)
        self.assertEqual(bals.total_other_liabilities, 8.88)
        self.assertEqual(bals.accounts, [{"account_name": "Checking"}])
        self.assertEqual(bals.mode, "sandbox_mock")

        holdings = DashboardHoldings.from_dict(
            {
                "as_of_date": "2026-09-30",
                "total_value": 9.99,
                "holdings": [{"ticker": "VTI"}],
                "mode": "sandbox_mock",
            }
        )
        self.assertEqual(holdings.as_of_date, "2026-09-30")
        self.assertEqual(holdings.total_value, 9.99)
        self.assertEqual(holdings.holdings, [{"ticker": "VTI"}])
        self.assertEqual(holdings.mode, "sandbox_mock")

        txs = DashboardTransactions.from_dict(
            {
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
                "total_transactions": 3,
                "money_in": 1.11,
                "money_out": 2.22,
                "net_cashflow": -1.11,
                "transactions": [{"user_transaction_id": "T-1"}],
                "mode": "sandbox_mock",
            }
        )
        self.assertEqual(txs.start_date, "2026-09-01")
        self.assertEqual(txs.end_date, "2026-09-30")
        self.assertEqual(txs.total_transactions, 3)
        self.assertEqual(txs.money_in, 1.11)
        self.assertEqual(txs.money_out, 2.22)
        self.assertEqual(txs.net_cashflow, -1.11)
        self.assertEqual(txs.transactions, [{"user_transaction_id": "T-1"}])
        self.assertEqual(txs.mode, "sandbox_mock")

    def test_dashboard_transactions_to_dict_exact_and_defaults(self):
        from empower_personal_dashboard.models import DashboardHistories

        inner = [{"user_transaction_id": "T-1"}]
        dtd = DashboardTransactions(
            start_date="2026-09-01", end_date="2026-09-30", total_transactions=1,
            money_in=100.456, money_out=4.756, net_cashflow=95.706,
            transactions=inner, mode="sandbox_mock",
        ).to_dict()
        self.assertEqual(
            dtd,
            {
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
                "total_transactions": 1,
                "money_in": 100.46,
                "money_out": 4.76,
                "net_cashflow": 95.71,
                "transactions": inner,
                "mode": "sandbox_mock",
            },
        )
        empty_b = DashboardBalances.from_dict({})
        self.assertEqual(empty_b.total_cash, 0.0)
        self.assertEqual(empty_b.total_investment, 0.0)
        self.assertEqual(empty_b.total_loan, 0.0)
        self.assertEqual(empty_b.total_mortgage, 0.0)
        self.assertEqual(empty_b.total_other_assets, 0.0)
        self.assertEqual(empty_b.total_other_liabilities, 0.0)
        empty_t = DashboardTransactions.from_dict({})
        self.assertEqual(empty_t.start_date, "")
        self.assertEqual(empty_t.end_date, "")
        self.assertEqual(empty_t.total_transactions, 0)
        self.assertEqual(empty_t.money_in, 0.0)
        self.assertEqual(empty_t.money_out, 0.0)
        self.assertEqual(empty_t.net_cashflow, 0.0)
        self.assertEqual(empty_t.transactions, [])
        empty_h = DashboardHistories.from_dict({})
        self.assertEqual(empty_h.start_date, "")
        self.assertEqual(empty_h.end_date, "")
        self.assertEqual(empty_h.total_points, 0)
        self.assertEqual(empty_h.histories, [])

    def test_daily_history_point_to_dict_exact(self):
        from empower_personal_dashboard.models import DailyHistoryPoint, DashboardHistories

        pt = DailyHistoryPoint(
            date="2024-01-15", net_worth=125000.506, total_assets=135000.506,
            total_liabilities=10000.506, balances={"ACC-1": 1.5},
        )
        self.assertEqual(
            pt.to_dict(),
            {
                "date": "2024-01-15",
                "net_worth": 125000.51,
                "total_assets": 135000.51,
                "total_liabilities": 10000.51,
                "balances": {"ACC-1": 1.5},
            },
        )
        hd = DashboardHistories(
            start_date="2024-01-01", end_date="2024-01-31",
            histories=[pt.to_dict()], total_points=0, mode="sandbox_mock",
        ).to_dict()
        self.assertEqual(hd["total_points"], 1)
        restored = DashboardHistories.from_dict({"histories": [pt.to_dict()]})
        self.assertEqual(restored.total_points, 1)
        self.assertEqual(restored.mode, "live")
        exact = DashboardHistories(
            start_date="2024-01-01", end_date="2024-01-31",
            histories=[pt.to_dict()], total_points=99, mode="sandbox_mock",
        ).to_dict()
        self.assertEqual(
            exact,
            {
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "total_points": 99,
                "histories": [pt.to_dict()],
                "mode": "sandbox_mock",
            },
        )
        full = DashboardHistories.from_dict(
            {
                "start_date": "2024-02-01",
                "end_date": "2024-02-29",
                "histories": [pt.to_dict(), pt.to_dict()],
                "total_points": 42,
                "mode": "sandbox_mock",
            }
        )
        self.assertEqual(full.start_date, "2024-02-01")
        self.assertEqual(full.end_date, "2024-02-29")
        self.assertEqual(full.total_points, 42)
        self.assertEqual(full.mode, "sandbox_mock")
        self.assertEqual(len(full.histories), 2)

    def test_histories_model(self):
        from empower_personal_dashboard.models import DailyHistoryPoint, DashboardHistories

        pt = DailyHistoryPoint(
            date="2024-01-15",
            net_worth=125000.50,
            total_assets=135000.50,
            total_liabilities=10000.00,
            balances={"ACC-INV-001": 100000.0, "ACC-CHK-002": 35000.50, "ACC-CRD-003": -10000.0},
        )
        d = pt.to_dict()
        self.assertEqual(d["date"], "2024-01-15")
        self.assertEqual(d["net_worth"], 125000.50)
        self.assertEqual(d["total_assets"], 135000.50)
        self.assertEqual(d["total_liabilities"], 10000.00)
        self.assertEqual(d["balances"]["ACC-INV-001"], 100000.0)

        histories = DashboardHistories(
            start_date="2024-01-01",
            end_date="2024-01-31",
            histories=[d],
            total_points=1,
            mode="live",
        )
        hd = histories.to_dict()
        self.assertEqual(hd["start_date"], "2024-01-01")
        self.assertEqual(hd["total_points"], 1)
        self.assertEqual(len(hd["histories"]), 1)

        restored = DashboardHistories.from_dict(hd)
        self.assertEqual(restored.start_date, "2024-01-01")
        self.assertEqual(restored.total_points, 1)

    def test_histories_from_dict_defaults_mode_to_schema_valid_live(self):
        from empower_personal_dashboard.models import DashboardHistories

        # When the payload omits mode, the default must be a schema-supported
        # value ("live"/"sandbox_mock"), never the invalid "historical".
        restored = DashboardHistories.from_dict(
            {"start_date": "2024-01-01", "end_date": "2024-01-31", "histories": []}
        )
        self.assertEqual(restored.mode, "live")
        self.assertEqual(restored.to_dict()["mode"], "live")


if __name__ == "__main__":
    unittest.main()
