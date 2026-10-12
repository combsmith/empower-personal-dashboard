"""
test_cli.py — Unit tests for empower CLI.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from empower_personal_dashboard.cli import main as cli_main

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUBPROCESS_ENV = dict(os.environ, PYTHONPATH=str(_REPO_ROOT))

class TestCLI(unittest.TestCase):
    def test_cli_sandbox_all_with_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_balances = Path(tmpdir) / "balances.json"
            out_holdings = Path(tmpdir) / "holdings.json"
            out_txs = Path(tmpdir) / "transactions.jsonl"

            argv = [
                "empower",
                "--sandbox",
                "--all",
                "--csv",
                "--output",
                str(out_balances),
                "--output-holdings",
                str(out_holdings),
                "--output-transactions",
                str(out_txs),
                "--format",
                "markdown",
                "--quiet",
            ]

            with patch.object(sys, "argv", argv):
                exit_code = cli_main()
                self.assertEqual(exit_code, 0)

            # Check JSON/JSONL outputs
            self.assertTrue(out_balances.exists())
            self.assertTrue(out_holdings.exists())
            self.assertTrue(out_txs.exists())

            # Check CSV outputs
            self.assertTrue(out_balances.with_suffix(".csv").exists())
            self.assertTrue(out_holdings.with_suffix(".csv").exists())
            self.assertTrue(out_txs.with_suffix(".csv").exists())

            with open(out_balances, "r", encoding="utf-8") as f:
                b_data = json.load(f)
                self.assertEqual(b_data["mode"], "sandbox_mock")
                self.assertGreater(b_data["net_worth"], 0)

    def test_cli_sandbox_json_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_balances = Path(tmpdir) / "balances.json"
            argv = [
                "empower",
                "--sandbox",
                "--balances",
                "--output",
                str(out_balances),
                "--format",
                "json",
                "--quiet",
            ]

            with patch.object(sys, "argv", argv):
                exit_code = cli_main()
                self.assertEqual(exit_code, 0)

            self.assertTrue(out_balances.exists())

    def test_cli_sandbox_beancount_modular_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_ledger = Path(tmpdir) / "ledger"
            argv = [
                "empower",
                "--sandbox",
                "--all",
                "--beancount",
                "--output-beancount",
                str(out_ledger),
                "--quiet",
            ]

            with patch.object(sys, "argv", argv):
                exit_code = cli_main()
                self.assertEqual(exit_code, 0)

            self.assertTrue((out_ledger / "main.bean").exists())
            self.assertTrue((out_ledger / "accounts.bean").exists())
            self.assertTrue((out_ledger / "balances.bean").exists())
            self.assertTrue((out_ledger / "prices.bean").exists())
            self.assertTrue((out_ledger / "transactions.bean").exists())

    def test_cli_sandbox_beancount_single_file_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "export.bean"
            argv = [
                "empower",
                "--sandbox",
                "--all",
                "--beancount",
                "--output-beancount",
                str(out_file),
                "--quiet",
            ]

            with patch.object(sys, "argv", argv):
                exit_code = cli_main()
                self.assertEqual(exit_code, 0)

            self.assertTrue(out_file.exists())
            content = out_file.read_text(encoding="utf-8")
            self.assertIn("option \"title\" \"Empower Personal Dashboard Ledger\"", content)
            self.assertIn("open Equity:Opening-Balances USD", content)

    def test_cli_rejects_conflicting_append_and_overwrite(self):
        argv = [
            "empower",
            "--sandbox",
            "--all",
            "--beancount",
            "--append",
            "--overwrite-ledger",
        ]
        with patch.object(sys, "argv", argv):
            exit_code = cli_main()
            self.assertNotEqual(exit_code, 0)

    def test_cli_beancount_format_routes_progress_to_stderr(self):
        import io
        argv = [
            "empower",
            "--sandbox",
            "--holdings",
            "--format",
            "beancount",
        ]
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            exit_code = cli_main()
            self.assertEqual(exit_code, 0)

        stdout_val = stdout_buf.getvalue()
        stderr_val = stderr_buf.getvalue()
        # Directives should be on stdout
        self.assertIn("open Equity:Opening-Balances", stdout_val)
        # Progress messages must NOT be on stdout
        self.assertNotIn("[*] Querying Empower", stdout_val)
        # Progress messages must be on stderr
        self.assertIn("[*] Querying Empower", stderr_val)

    def test_cli_holdings_only_beancount_includes_balance_assertions(self):
        import io
        argv = [
            "empower",
            "--sandbox",
            "--holdings",
            "--format",
            "beancount",
        ]
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            exit_code = cli_main()
            self.assertEqual(exit_code, 0)

        stdout_val = stdout_buf.getvalue()
        # Holdings-only format must include commodity unit balance assertions
        self.assertIn("balance Assets:", stdout_val)

    def test_cli_from_data_dir_beancount_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir()
            balances_file = data_dir / "empower_balances.json"
            holdings_file = data_dir / "empower_holdings.json"
            txs_file = data_dir / "empower_transactions.jsonl"
            out_ledger = Path(tmpdir) / "ledger"

            # Write synthetic offline test datasets
            balances_file.write_text(json.dumps({
                "as_of_date": "2026-10-01",
                "net_worth": 100000.0,
                "total_cash": 25000.0,
                "total_investment": 75000.0,
                "total_credit_card": 0.0,
                "total_loan": 0.0,
                "total_mortgage": 0.0,
                "accounts": [
                    {"account_id": "ACC1", "firm_name": "Test Firm", "account_name": "Checking", "account_type": "bank", "balance": 25000.0, "is_asset": True}
                ],
                "mode": "historical"
            }), encoding="utf-8")

            holdings_file.write_text(json.dumps({
                "as_of_date": "2026-10-01",
                "total_value": 75000.0,
                "holdings": [
                    {"user_account_id": 1, "account_name": "Checking", "ticker": "VTI", "quantity": 100.0, "price": 250.0, "value": 25000.0, "cost_basis": 200.0}
                ],
                "mode": "historical"
            }), encoding="utf-8")

            txs_file.write_text(json.dumps({
                "user_transaction_id": "TX999",
                "account_id": "ACC1",
                "account_name": "Checking",
                "transaction_date": "2026-09-30",
                "description": "Test Paycheck",
                "amount": 5000.0,
                "is_credit": True,
                "is_income": True,
                "is_spending": False,
                "transaction_type": "Deposit",
                "category_id": 1,
            }) + "\n", encoding="utf-8")

            argv = [
                "empower",
                "--all",
                "--from-data-dir",
                str(data_dir),
                "--beancount",
                "--output-beancount",
                str(out_ledger),
                "--quiet",
            ]

            with patch.object(sys, "argv", argv):
                exit_code = cli_main()
                self.assertEqual(exit_code, 0)

            self.assertTrue((out_ledger / "main.bean").exists())
            self.assertTrue((out_ledger / "accounts.bean").exists())
            self.assertTrue((out_ledger / "balances.bean").exists())
            self.assertTrue((out_ledger / "holdings.bean").exists())
            self.assertTrue((out_ledger / "prices.bean").exists())
            self.assertTrue((out_ledger / "transactions.bean").exists())

            tx_content = (out_ledger / "transactions.bean").read_text(encoding="utf-8")
            self.assertIn("Test Paycheck", tx_content)
            self.assertIn("^empower-tx-TX999", tx_content)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_balances")
    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_holdings")
    def test_cli_all_without_start_date_passes_none_to_client(self, mock_holdings, mock_balances, mock_txs):
        from empower_personal_dashboard.models import DashboardBalances, DashboardHoldings, DashboardTransactions

        mock_balances.return_value = DashboardBalances(
            as_of_date="2026-10-02",
            net_worth=1000.0,
            total_cash=1000.0,
            total_investment=0.0,
            total_card_liabilities=0.0,
            total_loan=0.0,
            total_mortgage=0.0,
            accounts=[],
        )
        mock_holdings.return_value = DashboardHoldings(as_of_date="2026-10-02", total_value=0.0, holdings=[])
        mock_txs.return_value = DashboardTransactions(
            start_date="2018-01-01",
            end_date="2026-10-02",
            total_transactions=0,
            money_in=0.0,
            money_out=0.0,
            net_cashflow=0.0,
            transactions=[],
        )

        argv = ["empower", "--all", "--quiet", "--session-file", "/nonexistent/session.json", "--mock"]
        with patch.object(sys, "argv", argv):
            exit_code = cli_main()
            self.assertEqual(exit_code, 0)

        mock_txs.assert_called_once()
        _, kwargs = mock_txs.call_args
        self.assertIsNone(kwargs.get("start_date"))

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_cli_transactions_without_start_date_passes_none_to_client(self, mock_txs):
        from empower_personal_dashboard.models import DashboardTransactions

        mock_txs.return_value = DashboardTransactions(
            start_date="2018-01-01",
            end_date="2026-10-02",
            total_transactions=0,
            money_in=0.0,
            money_out=0.0,
            net_cashflow=0.0,
            transactions=[],
        )

        argv = ["empower", "--transactions", "--quiet", "--session-file", "/nonexistent/session.json", "--mock"]
        with patch.object(sys, "argv", argv):
            exit_code = cli_main()
            self.assertEqual(exit_code, 0)

        mock_txs.assert_called_once()
        _, kwargs = mock_txs.call_args
        self.assertIsNone(kwargs.get("start_date"))

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_cli_transactions_with_explicit_start_date(self, mock_txs):
        from empower_personal_dashboard.models import DashboardTransactions

        mock_txs.return_value = DashboardTransactions(
            start_date="2024-01-01",
            end_date="2026-10-02",
            total_transactions=0,
            money_in=0.0,
            money_out=0.0,
            net_cashflow=0.0,
            transactions=[],
        )

        argv = [
            "empower",
            "--transactions",
            "--start-date",
            "2024-01-01",
            "--quiet",
            "--session-file",
            "/nonexistent/session.json",
            "--mock",
        ]
        with patch.object(sys, "argv", argv):
            exit_code = cli_main()
            self.assertEqual(exit_code, 0)

        mock_txs.assert_called_once()
        _, kwargs = mock_txs.call_args
        self.assertEqual(kwargs.get("start_date"), "2024-01-01")

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_cli_beancount_format_with_transactions_ignores_limit(self, mock_txs):
        from empower_personal_dashboard.models import DashboardTransactions

        mock_txs.return_value = DashboardTransactions(
            start_date="2024-01-01",
            end_date="2026-10-02",
            total_transactions=0,
            money_in=0.0,
            money_out=0.0,
            net_cashflow=0.0,
            transactions=[],
        )

        argv = [
            "empower",
            "--transactions",
            "--format",
            "beancount",
            "--limit",
            "50",
            "--quiet",
            "--session-file",
            "/nonexistent/session.json",
            "--mock",
        ]
        with patch.object(sys, "argv", argv):
            exit_code = cli_main()
            self.assertEqual(exit_code, 0)

        mock_txs.assert_called_once()
        _, kwargs = mock_txs.call_args
        # When --format beancount is used with --transactions, limit should be None
        # to fetch complete history for accurate Beancount reconstruction
        self.assertIsNone(kwargs.get("limit"))


class TestMergeTransactionRecords(unittest.TestCase):
    def test_updates_in_place_appends_and_preserves_order(self):
        from empower_personal_dashboard.cli import merge_transaction_records

        existing = [
            {"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0, "status": "posted"},
            {"user_transaction_id": "TX2", "transaction_date": "2026-09-01", "description": "Coffee", "amount": 5.0, "status": "pending"},
        ]
        incoming = [
            {"user_transaction_id": "TX2", "transaction_date": "2026-09-01", "description": "Coffee", "amount": 5.25, "status": "posted"},
            {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0, "status": "posted"},
        ]

        merged = merge_transaction_records(existing, incoming)

        ids = [m["user_transaction_id"] for m in merged]
        # Stable order: preserved historical first, then original position of TX2, then new TX3
        self.assertEqual(ids, ["OLD1", "TX2", "TX3"])
        # Historical record outside the queried window is preserved untouched
        self.assertEqual(merged[0]["description"], "Old")
        # Matching record updated in place (pending -> posted, revised amount)
        self.assertEqual(merged[1]["status"], "posted")
        self.assertEqual(merged[1]["amount"], 5.25)

    def test_preserves_records_without_id(self):
        from empower_personal_dashboard.cli import merge_transaction_records

        existing = [
            {"transaction_date": "2019-05-01", "description": "No id", "amount": 1.0},
            {"user_transaction_id": "A", "description": "has id", "amount": 2.0},
        ]
        incoming = [
            {"user_transaction_id": "A", "description": "updated", "amount": 3.0},
        ]

        merged = merge_transaction_records(existing, incoming)
        self.assertEqual(len(merged), 2)
        # Keyless historical record survives
        self.assertTrue(any(m.get("description") == "No id" for m in merged))
        # Keyed record updated
        self.assertEqual(next(m for m in merged if m.get("user_transaction_id") == "A")["amount"], 3.0)

    def test_keyless_incoming_records_do_not_accumulate(self):
        # A record without an id cannot be matched by key, but re-merging the
        # identical record on every run must not append another copy each time.
        from empower_personal_dashboard.cli import merge_transaction_records

        keyless = {"transaction_date": "2026-09-20", "description": "No id", "amount": 4.0}
        other_keyless = {"transaction_date": "2026-09-21", "description": "Other", "amount": 6.0}

        first = merge_transaction_records([], [dict(keyless)])
        second = merge_transaction_records(first, [dict(keyless), dict(other_keyless)])
        third = merge_transaction_records(second, [dict(keyless), dict(other_keyless)])

        self.assertEqual(first, [keyless])
        self.assertEqual(second, [keyless, other_keyless])
        self.assertEqual(third, [keyless, other_keyless])

    def test_prune_stale_pending_records_inside_window(self):
        from empower_personal_dashboard.cli import (
            merge_transaction_records,
            prune_stale_pending_records,
        )

        existing = [
            {"user_transaction_id": "P1", "transaction_date": "2026-09-10", "status": "pending", "amount": 12.50},
            {"user_transaction_id": "P2", "transaction_date": "2026-09-15", "status": "pending", "amount": 25.00},
        ]
        incoming = [
            {"user_transaction_id": "POSTED1", "transaction_date": "2026-09-10", "status": "posted", "amount": 12.50},
            {"user_transaction_id": "P2", "transaction_date": "2026-09-15", "status": "posted", "amount": 25.00},
        ]

        retained, pruned_count = prune_stale_pending_records(existing, incoming, "2026-09-01", "2026-09-30")
        self.assertEqual(pruned_count, 1)
        self.assertEqual([r["user_transaction_id"] for r in retained], ["P2"])

        merged = merge_transaction_records(retained, incoming)
        ids = [m["user_transaction_id"] for m in merged]
        self.assertNotIn("P1", ids)
        self.assertIn("P2", ids)
        self.assertIn("POSTED1", ids)

    def test_prune_preserves_records_outside_window(self):
        from empower_personal_dashboard.cli import prune_stale_pending_records

        existing = [
            {"user_transaction_id": "P_BEFORE", "transaction_date": "2026-08-31", "status": "pending"},
            {"user_transaction_id": "P_AFTER", "transaction_date": "2026-10-01", "status": "pending"},
            {"user_transaction_id": "P_INSIDE", "transaction_date": "2026-09-15", "status": "pending"},
        ]
        incoming = []

        retained, pruned_count = prune_stale_pending_records(existing, incoming, "2026-09-01", "2026-09-30")
        self.assertEqual(pruned_count, 1)
        retained_ids = [r["user_transaction_id"] for r in retained]
        self.assertIn("P_BEFORE", retained_ids)
        self.assertIn("P_AFTER", retained_ids)
        self.assertNotIn("P_INSIDE", retained_ids)

    def test_prune_preserves_posted_records_inside_window(self):
        from empower_personal_dashboard.cli import prune_stale_pending_records

        existing = [
            {"user_transaction_id": "POSTED_OLD", "transaction_date": "2026-09-15", "status": "posted"},
        ]
        incoming = []

        retained, pruned_count = prune_stale_pending_records(existing, incoming, "2026-09-01", "2026-09-30")
        self.assertEqual(pruned_count, 0)
        self.assertEqual([r["user_transaction_id"] for r in retained], ["POSTED_OLD"])

    def test_prune_preserves_undated_records(self):
        from empower_personal_dashboard.cli import prune_stale_pending_records

        existing = [
            {"user_transaction_id": "P_UNDATED", "transaction_date": None, "status": "pending"},
            {"user_transaction_id": "P_BAD_DATE", "transaction_date": "invalid-date", "status": "pending"},
        ]
        incoming = []

        retained, pruned_count = prune_stale_pending_records(existing, incoming, "2026-09-01", "2026-09-30")
        self.assertEqual(pruned_count, 0)
        self.assertEqual(len(retained), 2)

    def test_prune_unkeyed_pending_records(self):
        from empower_personal_dashboard.cli import prune_stale_pending_records

        existing = [
            {"transaction_date": "2026-09-15", "description": "Keyless pending", "status": "pending"},
        ]
        incoming = []

        retained, pruned_count = prune_stale_pending_records(existing, incoming, "2026-09-01", "2026-09-30")
        self.assertEqual(pruned_count, 1)
        self.assertEqual(len(retained), 0)



class TestCliMerge(unittest.TestCase):
    def _mk_result(self, transactions):
        from empower_personal_dashboard.models import DashboardTransactions

        return DashboardTransactions(
            start_date="2026-09-01",
            end_date="2026-10-01",
            total_transactions=len(transactions),
            money_in=0.0,
            money_out=0.0,
            net_cashflow=0.0,
            transactions=transactions,
        )

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_jsonl_preserves_history_and_applies_updates(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0, "status": "posted"}) + "\n"
                + json.dumps({"user_transaction_id": "TX2", "transaction_date": "2026-09-01", "description": "Coffee", "amount": 5.0, "status": "pending"}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX2", "transaction_date": "2026-09-01", "description": "Coffee", "amount": 5.25, "status": "posted"},
                {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0, "status": "posted"},
            ])

            argv = [
                "empower", "--transactions", "--merge",
                "--output-transactions", str(out),
                "--quiet", "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            self.assertIn("OLD1", by_id)  # historical outside the window preserved
            self.assertEqual(by_id["TX2"]["status"], "posted")  # updated in place
            self.assertEqual(by_id["TX2"]["amount"], 5.25)
            self.assertIn("TX3", by_id)  # new appended
            self.assertEqual(len(records), 3)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_tolerates_null_transaction_date(self, mock_txs):
        # A record with an explicit null transaction_date must not crash the merge:
        # the newest-first ordering uses a None-safe key rather than comparing None
        # against str.
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "OLD1", "transaction_date": None, "description": "No date", "amount": 10.0}) + "\n"
                + json.dumps({"user_transaction_id": "TX2", "transaction_date": "2026-09-01", "description": "Coffee", "amount": 5.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX3", "transaction_date": None, "description": "New no date", "amount": 7.0},
            ])

            argv = [
                "empower", "--transactions", "--merge",
                "--output-transactions", str(out),
                "--quiet", "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            ids = [r["user_transaction_id"] for r in records]
            # Newest-first with a None-safe key: dated records lead, undated ones
            # follow in their merged order (archived OLD1, then newly fetched TX3).
            self.assertEqual(ids, ["TX2", "OLD1", "TX3"])

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_json_output(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.json"
            out.write_text(json.dumps({
                "start_date": "2020-01-15",
                "end_date": "2026-09-01",
                "total_transactions": 1,
                "transactions": [
                    {"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0},
                ],
            }), encoding="utf-8")
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0},
            ])

            argv = [
                "empower", "--transactions", "--merge",
                "--output-transactions", str(out),
                "--quiet", "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(cli_main(), 0)

            data = json.loads(out.read_text(encoding="utf-8"))
            ids = {t["user_transaction_id"] for t in data["transactions"]}
            self.assertEqual(ids, {"OLD1", "TX3"})
            self.assertEqual(data["total_transactions"], 2)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_auto_detects_earliest_start_date(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0}) + "\n"
                + json.dumps({"user_transaction_id": "OLD2", "transaction_date": "2021-06-10", "description": "Older", "amount": 20.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([])

            argv = [
                "empower", "--transactions", "--merge",
                "--output-transactions", str(out),
                "--quiet", "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(cli_main(), 0)

            mock_txs.assert_called_once()
            _, kwargs = mock_txs.call_args
            # Earliest archived transaction date is queried forward
            self.assertEqual(kwargs.get("start_date"), "2020-01-15")

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_explicit_start_date_overrides_earliest_detection(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([])

            argv = [
                "empower", "--transactions", "--merge",
                "--start-date", "2025-01-01",
                "--output-transactions", str(out),
                "--quiet", "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(cli_main(), 0)

            _, kwargs = mock_txs.call_args
            self.assertEqual(kwargs.get("start_date"), "2025-01-01")

    def test_earliest_transaction_date_ignores_invalid_dates(self):
        # Only canonical YYYY-MM-DD strings may be selected: non-string, null,
        # missing, and malformed values must never become the merge start date.
        from empower_personal_dashboard.cli import _earliest_transaction_date

        self.assertIsNone(_earliest_transaction_date([]))
        self.assertIsNone(
            _earliest_transaction_date([
                {"transaction_date": None},
                {"transaction_date": 20200115},          # non-string
                {"transaction_date": "not-a-date"},        # malformed
                {"transaction_date": "2020-13-01"},        # out-of-range month
                {"transaction_date": "2020-1-5"},          # non-canonical padding
                {"transaction_date": "2020-01-15T00:00"},  # extra time component
                {},                                        # missing key
            ])
        )
        # A single valid date surrounded by junk is still selected cleanly.
        self.assertEqual(
            _earliest_transaction_date([
                {"transaction_date": 20200101},
                {"transaction_date": "2021-06-10"},
                {"transaction_date": "garbage"},
                {"transaction_date": "2020-07-04"},
            ]),
            "2020-07-04",
        )

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_skips_invalid_archive_date_for_start(self, mock_txs):
        # A malformed/non-string archived date must not be forwarded as start_date;
        # the earliest *valid* canonical date is queried instead.
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "BAD", "transaction_date": "garbage", "amount": 1.0}) + "\n"
                + json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2021-06-10", "amount": 10.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([])

            code, err = self._run_merge(out)
            self.assertEqual(code, 0, err)
            _, kwargs = mock_txs.call_args
            self.assertEqual(kwargs.get("start_date"), "2021-06-10")

    def test_archive_lock_is_exclusive(self):
        # The merge holds an exclusive lock on the archive's directory for its
        # whole read/fetch/merge/replace sequence. Locking the directory itself
        # means every process targeting the archive contends on the same lock
        # whatever its TMPDIR, and no lock file is created anywhere.
        from empower_personal_dashboard.cli import _archive_lock

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            try:
                import fcntl
            except ImportError:
                # No advisory locking on this platform: the manager is a no-op.
                with _archive_lock(out):
                    pass
                return

            def try_lock():
                fd = os.open(tmpdir, os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)

            with _archive_lock(out):
                self.assertEqual(list(Path(tmpdir).iterdir()), [])
                with self.assertRaises(BlockingIOError):
                    try_lock()
            # Released on exit: the same lock can be taken again.
            try_lock()

    def test_archive_lock_free_acquires_immediately_without_waiting_message(self):
        from empower_personal_dashboard.cli import _archive_lock
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            progress_buf = io.StringIO()
            executed = False
            with _archive_lock(out, timeout=1.0, progress_file=progress_buf):
                executed = True
            self.assertTrue(executed)
            self.assertEqual(progress_buf.getvalue(), "")

    def test_archive_lock_busy_then_released_within_timeout(self):
        from empower_personal_dashboard.cli import _archive_lock
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            fd = os.open(tmpdir, os.O_RDONLY)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def delayed_release():
                time.sleep(0.02)
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

            t = threading.Thread(target=delayed_release)
            t.start()
            try:
                progress_buf = io.StringIO()
                executed = False
                with _archive_lock(out, timeout=1.0, poll_interval=0.005, progress_file=progress_buf):
                    executed = True
                self.assertTrue(executed)
                self.assertIn("[*] Another --merge is running for this archive; waiting...", progress_buf.getvalue())
            finally:
                t.join()

    def test_archive_lock_busy_past_timeout_raises_and_reports_waiting(self):
        from empower_personal_dashboard.cli import _archive_lock, _ArchiveLockTimeoutError
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            fd = os.open(tmpdir, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                progress_buf = io.StringIO()
                with self.assertRaises(_ArchiveLockTimeoutError) as ctx:
                    with _archive_lock(out, timeout=0.03, poll_interval=0.005, progress_file=progress_buf):
                        pass
                self.assertIn("Timed out waiting for archive lock", str(ctx.exception))
                self.assertIn("[*] Another --merge is running for this archive; waiting...", progress_buf.getvalue())
            finally:
                os.close(fd)

    def test_archive_lock_closes_fd_on_timeout(self):
        from empower_personal_dashboard.cli import _archive_lock, _ArchiveLockTimeoutError
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            fd = os.open(tmpdir, os.O_RDONLY)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            closed_fds = []
            orig_close = os.close

            def tracking_close(f):
                closed_fds.append(f)
                orig_close(f)

            try:
                with patch("empower_personal_dashboard.cli.os.close", side_effect=tracking_close):
                    with self.assertRaises(_ArchiveLockTimeoutError):
                        with _archive_lock(out, timeout=0.01, poll_interval=0.005, quiet=True):
                            pass
                self.assertGreater(len(closed_fds), 0)
            finally:
                orig_close(fd)

    def test_archive_lock_retries_on_eintr(self):
        import errno
        from empower_personal_dashboard.cli import _archive_lock
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            calls = 0

            def mock_flock(fd, op):
                nonlocal calls
                calls += 1
                if calls == 1:
                    e = OSError("Interrupted system call")
                    e.errno = errno.EINTR
                    raise e

            with patch("fcntl.flock", side_effect=mock_flock):
                progress_buf = io.StringIO()
                executed = False
                with _archive_lock(out, timeout=1.0, poll_interval=0.005, progress_file=progress_buf):
                    executed = True
                self.assertTrue(executed)
                self.assertGreaterEqual(calls, 2)

    def test_archive_lock_final_attempt_after_last_sleep(self):
        import errno
        from empower_personal_dashboard.cli import _archive_lock
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            calls = 0

            def mock_flock(fd, op):
                nonlocal calls
                calls += 1
                if calls == 1:
                    e = OSError("busy")
                    e.errno = errno.EAGAIN
                    raise e

            # Timeout shorter than poll interval: the single post-sleep retry must still run.
            with patch("fcntl.flock", side_effect=mock_flock):
                with _archive_lock(out, timeout=0.01, poll_interval=0.5, quiet=True):
                    pass
            self.assertEqual(calls, 2)

    def test_archive_lock_retries_on_eacces(self):
        import errno
        from empower_personal_dashboard.cli import _archive_lock
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            calls = 0

            def mock_flock(fd, op):
                nonlocal calls
                calls += 1
                if calls == 1:
                    e = OSError("Permission denied")
                    e.errno = errno.EACCES
                    raise e

            with patch("fcntl.flock", side_effect=mock_flock):
                progress_buf = io.StringIO()
                executed = False
                with _archive_lock(out, timeout=1.0, poll_interval=0.005, progress_file=progress_buf):
                    executed = True
                self.assertTrue(executed)
                self.assertGreaterEqual(calls, 2)

    def test_cli_merge_lock_busy_past_timeout_exits_nonzero_and_leaves_archive_untouched(self):
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            original_content = json.dumps({"user_transaction_id": "OLD1", "description": "Coffee", "amount": 5.0}) + "\n"
            out.write_text(original_content, encoding="utf-8")

            fd = os.open(tmpdir, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                stdout_buf = io.StringIO()
                stderr_buf = io.StringIO()
                argv = [
                    "empower", "--transactions", "--merge",
                    "--output-transactions", str(out),
                    "--merge-lock-timeout", "0.03",
                    "--session-file", "/nonexistent/session.json", "--mock",
                ]
                with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                    code = cli_main()

                self.assertEqual(code, 1)
                self.assertIn("[*] Another --merge is running for this archive; waiting...", stdout_buf.getvalue())
                self.assertIn("Timed out waiting for archive lock", stderr_buf.getvalue())
                self.assertIn("The archive was left unchanged.", stderr_buf.getvalue())
                # Archive must be completely untouched!
                self.assertEqual(out.read_text(encoding="utf-8"), original_content)
            finally:
                os.close(fd)

    def test_cli_merge_lock_timeout_invalid_rejected(self):
        for invalid_val in ["-5", "nan", "inf", "+inf", "-inf", "1e309"]:
            stderr_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--merge-lock-timeout", invalid_val,
            ]
            with patch.object(sys, "argv", argv), patch("sys.stderr", stderr_buf):
                with self.assertRaises(SystemExit) as ctx:
                    cli_main()
                self.assertEqual(ctx.exception.code, 2)
            self.assertIn("--merge-lock-timeout", stderr_buf.getvalue())

    def test_archive_lock_does_not_follow_a_symlinked_directory(self):
        # The lock only ever opens a validated path. A symlinked parent is
        # refused by the read and write steps with their own messages, so the
        # lock steps aside instead of opening the redirected directory.
        from empower_personal_dashboard.cli import _archive_lock

        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = Path(tmpdir) / "real"
            real_dir.mkdir()
            link_dir = Path(tmpdir) / "link"
            link_dir.symlink_to(real_dir)
            with patch("empower_personal_dashboard.cli.os.open") as mock_open:
                with _archive_lock(link_dir / "transactions.jsonl"):
                    pass
            mock_open.assert_not_called()

    def test_archive_lock_tolerates_missing_directory(self):
        # A first run into a directory that does not exist yet has no archive to
        # race on; the lock must not fail or create anything.
        from empower_personal_dashboard.cli import _archive_lock

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "not-created-yet" / "transactions.jsonl"
            with _archive_lock(out):
                pass
            self.assertEqual(list(Path(tmpdir).iterdir()), [])

    def _run_merge(self, out, extra_args=()):
        argv = [
            "empower", "--transactions", "--merge",
            "--output-transactions", str(out),
            "--quiet", "--session-file", "/nonexistent/session.json", "--mock",
        ] + list(extra_args)
        stderr = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
            code = cli_main()
        return code, stderr.getvalue()

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_output_is_newest_first(self, mock_txs):
        # The fetcher returns newest-first, so a merged archive must stay
        # newest-first: newly fetched rows lead instead of trailing the archive.
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "TX2", "transaction_date": "2026-09-01", "description": "Coffee", "amount": 5.0}) + "\n"
                + json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0},
                {"user_transaction_id": "TX2", "transaction_date": "2026-09-01", "description": "Coffee", "amount": 5.25},
            ])

            code, _ = self._run_merge(out)
            self.assertEqual(code, 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual([r["user_transaction_id"] for r in records], ["TX3", "TX2", "OLD1"])
            self.assertEqual(records[1]["amount"], 5.25)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_failed_write_leaves_archive_intact(self, mock_txs):
        # A serialization error mid-write must not truncate the archive: the
        # merged output is written to a temp file and only then swapped in.
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            original = (
                json.dumps({"user_transaction_id": "TX2", "transaction_date": "2026-09-01", "description": "Coffee", "amount": 5.0}) + "\n"
                + json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0}) + "\n"
            )
            out.write_text(original, encoding="utf-8")
            # A set is not JSON-serializable, so writing the updated TX2 fails.
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX2", "transaction_date": "2026-09-01", "description": "Coffee", "amount": 5.25, "tags": {"a", "b"}},
            ])

            code, _ = self._run_merge(out)

            self.assertEqual(code, 1)
            self.assertEqual(out.read_text(encoding="utf-8"), original)
            self.assertEqual(sorted(p.name for p in Path(tmpdir).iterdir()), ["transactions.jsonl"])

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_json_write_is_atomic_on_failure(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.json"
            original = json.dumps({
                "start_date": "2020-01-15",
                "end_date": "2020-01-15",
                "total_transactions": 1,
                "transactions": [
                    {"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0},
                ],
            })
            out.write_text(original, encoding="utf-8")
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0, "tags": {"a"}},
            ])

            code, _ = self._run_merge(out)

            self.assertEqual(code, 1)
            self.assertEqual(out.read_text(encoding="utf-8"), original)
            self.assertEqual(sorted(p.name for p in Path(tmpdir).iterdir()), ["transactions.json"])

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_preserves_existing_file_mode(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0}) + "\n",
                encoding="utf-8",
            )
            # Owner-only, with an execute bit no umask default can produce, so the
            # assertion below can only pass if the existing mode was carried over.
            os.chmod(out, 0o700)
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0},
            ])

            code, _ = self._run_merge(out)

            self.assertEqual(code, 0)
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_new_archive_honours_umask(self, mock_txs):
        # A brand-new archive must get the process umask default, exactly as a
        # plain open() would: a strict umask keeps financial data owner-only.
        mock_txs.return_value = self._mk_result([
            {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0},
        ])
        for umask, expected in ((0o077, 0o600), (0o022, 0o644)):
            with self.subTest(umask=oct(umask)), tempfile.TemporaryDirectory() as tmpdir:
                out = Path(tmpdir) / "transactions.jsonl"
                previous = os.umask(umask)
                try:
                    code, _ = self._run_merge(out)
                finally:
                    os.umask(previous)
                self.assertEqual(code, 0)
                self.assertEqual(out.stat().st_mode & 0o777, expected)
                self.assertEqual(sorted(p.name for p in Path(tmpdir).iterdir()), ["transactions.jsonl"])

    def test_existing_archive_mode_applies_before_content_is_written(self):
        # An owner-only archive must never be staged in a wider temp file: the
        # existing mode is applied before any content is written, not after.
        from empower_personal_dashboard.cli import _atomic_write

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text("{}\n", encoding="utf-8")
            os.chmod(out, 0o600)
            seen = []

            def write_body(f):
                temp_files = [p for p in Path(tmpdir).iterdir() if p.name != out.name]
                seen.extend(p.stat().st_mode & 0o777 for p in temp_files)
                f.write("{}\n")

            previous = os.umask(0o022)
            try:
                _atomic_write(out, write_body)
            finally:
                os.umask(previous)

            self.assertEqual(seen, [0o600])
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_keeps_records_containing_unicode_line_separators(self, mock_txs):
        # U+2028/U+2029 are legal inside JSON strings and are written raw
        # (ensure_ascii=False). They must not be treated as record boundaries.
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            description = "Line one\u2028line two\u2029end"
            out.write_text(
                json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": description, "amount": 10.0}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0},
            ])

            code, err = self._run_merge(out)
            self.assertEqual(code, 0, err)
            # A second merge must be able to read what the first one wrote.
            code, err = self._run_merge(out)
            self.assertEqual(code, 0, err)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").split("\n") if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            self.assertEqual(sorted(by_id), ["OLD1", "TX3"])
            self.assertEqual(by_id["OLD1"]["description"], description)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_refuses_corrupt_jsonl_archive(self, mock_txs):
        # A malformed line must stop the merge before anything is fetched or
        # written; skipping it would silently drop history on the rewrite.
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            original = (
                json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0}) + "\n"
                + '{"user_transaction_id": "TX2", "transaction_da\n'
            )
            out.write_text(original, encoding="utf-8")
            mock_txs.return_value = self._mk_result([])

            code, err = self._run_merge(out)

            self.assertEqual(code, 1)
            self.assertIn("line 2", err)
            self.assertIn("left unchanged", err)
            self.assertNotIn("Unexpected error", err)
            mock_txs.assert_not_called()
            self.assertEqual(out.read_text(encoding="utf-8"), original)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_refuses_invalid_json_archive(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.json"
            original = '{"transactions": [{"user_transaction_id": "OLD1"'
            out.write_text(original, encoding="utf-8")
            mock_txs.return_value = self._mk_result([])

            code, err = self._run_merge(out)

            self.assertEqual(code, 1)
            self.assertIn("left unchanged", err)
            self.assertNotIn("Unexpected error", err)
            mock_txs.assert_not_called()
            self.assertEqual(out.read_text(encoding="utf-8"), original)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_refuses_json_object_without_transactions_list(self, mock_txs):
        # A .json target that is an object with no list-valued "transactions"
        # is not an archive this tool wrote. Reading it as "empty" would let the
        # merge overwrite it with only the fetched window.
        mock_txs.return_value = self._mk_result([
            {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0},
        ])
        for payload in ({"note": "not an archive"}, {"transactions": None}, {"transactions": {"OLD1": {}}}):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmpdir:
                out = Path(tmpdir) / "transactions.json"
                original = json.dumps(payload)
                out.write_text(original, encoding="utf-8")
                mock_txs.reset_mock()

                code, err = self._run_merge(out)

                self.assertEqual(code, 1)
                self.assertIn("left unchanged", err)
                mock_txs.assert_not_called()
                self.assertEqual(out.read_bytes(), original.encode("utf-8"))

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_refuses_non_object_archive_entries(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.json"
            original = json.dumps([{"user_transaction_id": "OLD1", "transaction_date": "2020-01-15"}, 42])
            out.write_text(original, encoding="utf-8")
            mock_txs.return_value = self._mk_result([])

            code, err = self._run_merge(out)

            self.assertEqual(code, 1)
            self.assertIn("left unchanged", err)
            mock_txs.assert_not_called()
            self.assertEqual(out.read_text(encoding="utf-8"), original)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_applies_to_default_output_path(self, mock_txs):
        # --output-transactions has a default, so --merge without it is not a
        # no-op: it merges into the default archive.
        with tempfile.TemporaryDirectory() as tmpdir:
            default_out = Path(tmpdir) / "empower_transactions.jsonl"
            default_out.write_text(
                json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "description": "Old", "amount": 10.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX3", "transaction_date": "2026-09-20", "description": "New", "amount": 7.0},
            ])
            argv = [
                "empower", "--transactions", "--merge",
                "--quiet", "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch("empower_personal_dashboard.cli.DEFAULT_TRANSACTIONS_FILE", default_out), \
                    patch.object(sys, "argv", argv):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in default_out.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual([r["user_transaction_id"] for r in records], ["TX3", "OLD1"])

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_prunes_stale_pending_and_reports_count_in_summary(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "OLD1", "transaction_date": "2020-01-15", "status": "posted", "amount": 10.0}) + "\n"
                + json.dumps({"user_transaction_id": "PENDING_STALE", "transaction_date": "2026-09-10", "status": "pending", "amount": 25.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "POSTED_REPLACEMENT", "transaction_date": "2026-09-10", "status": "posted", "amount": 25.0},
                {"user_transaction_id": "NEW_TX", "transaction_date": "2026-09-20", "status": "posted", "amount": 7.0},
            ])

            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--output-transactions", str(out),
                "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                code = cli_main()

            self.assertEqual(code, 0, stderr_buf.getvalue())
            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            self.assertIn("OLD1", by_id)
            self.assertIn("POSTED_REPLACEMENT", by_id)
            self.assertIn("NEW_TX", by_id)
            self.assertNotIn("PENDING_STALE", by_id)
            self.assertEqual(len(records), 3)

            out_text = stdout_buf.getvalue()
            self.assertIn("[+] Merge:", out_text)
            self.assertIn("1 pruned", out_text)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_records_outside_window_never_removed(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "PEND_BEFORE", "transaction_date": "2026-08-15", "status": "pending", "amount": 10.0}) + "\n"
                + json.dumps({"user_transaction_id": "PEND_INSIDE", "transaction_date": "2026-09-10", "status": "pending", "amount": 20.0}) + "\n",
                encoding="utf-8",
            )
            # Query range explicitly set to 2026-09-01 to 2026-09-30
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "TX_IN", "transaction_date": "2026-09-10", "status": "posted", "amount": 20.0},
            ])

            argv = [
                "empower", "--transactions", "--merge",
                "--start-date", "2026-09-01",
                "--end-date", "2026-09-30",
                "--output-transactions", str(out),
                "--quiet", "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            # Record before the queried window is preserved
            self.assertIn("PEND_BEFORE", by_id)
            # Record inside the queried window is pruned
            self.assertNotIn("PEND_INSIDE", by_id)
            self.assertIn("TX_IN", by_id)
            self.assertEqual(len(records), 2)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_no_prune_when_limited(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "PENDING_STALE", "transaction_date": "2026-09-10", "status": "pending", "amount": 25.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "NEW_TX", "transaction_date": "2026-09-20", "status": "posted", "amount": 7.0},
            ])

            stdout_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--limit", "10",
                "--output-transactions", str(out),
                "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            # Stale pending record is NOT pruned because fetch was limited
            self.assertIn("PENDING_STALE", by_id)
            self.assertIn("NEW_TX", by_id)
            self.assertNotIn("pruned", stdout_buf.getvalue())

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_no_prune_when_account_filtered(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "PENDING_STALE", "transaction_date": "2026-09-10", "status": "pending", "amount": 25.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "NEW_TX", "transaction_date": "2026-09-20", "status": "posted", "amount": 7.0},
            ])

            stdout_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--account-id", "12345",
                "--output-transactions", str(out),
                "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            # Stale pending record is NOT pruned because fetch was account-filtered
            self.assertIn("PENDING_STALE", by_id)
            self.assertIn("NEW_TX", by_id)
            self.assertNotIn("pruned", stdout_buf.getvalue())

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_no_prune_with_opt_out_flag(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "PENDING_STALE", "transaction_date": "2026-09-10", "status": "pending", "amount": 25.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "NEW_TX", "transaction_date": "2026-09-20", "status": "posted", "amount": 7.0},
            ])

            stdout_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--no-prune",
                "--output-transactions", str(out),
                "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            # Stale pending record is NOT pruned because --no-prune was specified
            self.assertIn("PENDING_STALE", by_id)
            self.assertIn("NEW_TX", by_id)
            self.assertNotIn("pruned", stdout_buf.getvalue())

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_no_prune_when_fetch_incomplete(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "PENDING_STALE", "transaction_date": "2026-09-10", "status": "pending", "amount": 25.0}) + "\n",
                encoding="utf-8",
            )
            res = self._mk_result([
                {"user_transaction_id": "NEW_TX", "transaction_date": "2026-09-20", "status": "posted", "amount": 7.0},
            ])
            res.is_complete = False
            mock_txs.return_value = res

            stdout_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--output-transactions", str(out),
                "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            # Not pruned: the fetch is not proven to cover the whole window
            self.assertIn("PENDING_STALE", by_id)
            self.assertIn("NEW_TX", by_id)
            self.assertNotIn("pruned", stdout_buf.getvalue())

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_reports_zero_pruned_when_none_pruned(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "OLD_POSTED", "transaction_date": "2026-09-05", "status": "posted", "amount": 10.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "NEW_TX", "transaction_date": "2026-09-20", "status": "posted", "amount": 7.0},
            ])

            stdout_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--output-transactions", str(out),
                "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf):
                self.assertEqual(cli_main(), 0)

            out_text = stdout_buf.getvalue()
            self.assertIn("[+] Merge:", out_text)
            self.assertIn("0 pruned", out_text)

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_prunes_when_beancount_and_limit_present(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out_bean = Path(tmpdir) / "ledger.bean"
            out.write_text(
                json.dumps({"user_transaction_id": "PENDING_STALE", "transaction_date": "2026-09-10", "status": "pending", "amount": 25.0}) + "\n",
                encoding="utf-8",
            )
            mock_txs.return_value = self._mk_result([
                {"user_transaction_id": "NEW_TX", "transaction_date": "2026-09-20", "status": "posted", "amount": 7.0},
            ])

            stdout_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--beancount",
                "--output-beancount", str(out_bean),
                "--limit", "10",
                "--output-transactions", str(out),
                "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            # Because --beancount fetches complete window without limit (tx_limit=None),
            # the fetch is complete and stale pending record is pruned.
            self.assertNotIn("PENDING_STALE", by_id)
            self.assertIn("NEW_TX", by_id)
            self.assertIn("1 pruned", stdout_buf.getvalue())

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_skips_pruning_for_sandbox_mode(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "PENDING_STALE", "transaction_date": "2026-09-10", "status": "pending", "amount": 25.0}) + "\n",
                encoding="utf-8",
            )
            res = self._mk_result([
                {"user_transaction_id": "NEW_TX", "transaction_date": "2026-09-20", "status": "posted", "amount": 7.0},
            ])
            res.mode = "sandbox_mock"
            mock_txs.return_value = res

            stdout_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--output-transactions", str(out),
                "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            self.assertIn("PENDING_STALE", by_id)
            self.assertIn("NEW_TX", by_id)
            self.assertNotIn("pruned", stdout_buf.getvalue())

    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_transactions")
    def test_merge_cli_pruning_honors_returned_bounds_when_narrower(self, mock_txs):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text(
                json.dumps({"user_transaction_id": "PENDING_EARLY", "transaction_date": "2026-09-05", "status": "pending", "amount": 25.0}) + "\n" +
                json.dumps({"user_transaction_id": "PENDING_IN_BOUNDS", "transaction_date": "2026-09-20", "status": "pending", "amount": 15.0}) + "\n",
                encoding="utf-8",
            )
            res = self._mk_result([
                {"user_transaction_id": "NEW_TX", "transaction_date": "2026-09-25", "status": "posted", "amount": 7.0},
            ])
            res.start_date = "2026-09-15"
            res.end_date = "2026-10-01"
            mock_txs.return_value = res

            stdout_buf = io.StringIO()
            argv = [
                "empower", "--transactions", "--merge",
                "--start-date", "2026-09-01",
                "--end-date", "2026-10-01",
                "--output-transactions", str(out),
                "--session-file", "/nonexistent/session.json", "--mock",
            ]
            with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf):
                self.assertEqual(cli_main(), 0)

            records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {r["user_transaction_id"]: r for r in records}
            # PENDING_EARLY (2026-09-05) is before response start_date (2026-09-15), so it is preserved
            self.assertIn("PENDING_EARLY", by_id)
            # PENDING_IN_BOUNDS (2026-09-20) is within returned window and missing from fetch, so it is pruned
            self.assertNotIn("PENDING_IN_BOUNDS", by_id)
            self.assertIn("NEW_TX", by_id)
            self.assertIn("1 pruned", stdout_buf.getvalue())


class TestSafeOutputPath(unittest.TestCase):
    def test_resolves_regular_path_and_rejects_symlink(self):
        from empower_personal_dashboard.cli import _safe_output_path

        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "out.json"
            self.assertEqual(_safe_output_path(real), real.resolve())
            link = Path(tmp) / "link.json"
            link.symlink_to(real)
            with self.assertRaises(ValueError):
                _safe_output_path(link)

    def test_rejects_output_inside_symlinked_directory(self):
        from empower_personal_dashboard.cli import _safe_output_path

        with tempfile.TemporaryDirectory() as tmp:
            real_dir = Path(tmp) / "real"
            real_dir.mkdir()
            link_dir = Path(tmp) / "link"
            link_dir.symlink_to(real_dir)
            output_inside_link = link_dir / "out.json"
            with self.assertRaises(ValueError):
                _safe_output_path(output_inside_link)


class TestCliLoginChaining(unittest.TestCase):
    @patch("empower_personal_dashboard.cli.interactive_login", return_value=0)
    @patch("empower_personal_dashboard.cli._build_client")
    def test_login_alone_exits_cleanly(self, mock_build_client, mock_interactive_login):
        from empower_personal_dashboard.cli import main
        test_args = ["empower", "--login"]
        with patch.object(sys, "argv", test_args):
            ret = main()
            self.assertEqual(ret, 0)
            mock_interactive_login.assert_called_once()

    @patch("empower_personal_dashboard.cli.interactive_login", return_value=0)
    @patch("empower_personal_dashboard.cli.EmpowerDashboardClient.fetch_balances")
    @patch("empower_personal_dashboard.cli._build_client")
    def test_login_with_all_continues_to_extraction(self, mock_build_client, mock_fetch_balances, mock_interactive_login):
        from empower_personal_dashboard.cli import main
        from empower_personal_dashboard.models import DashboardBalances
        client_mock = MagicMock()
        client_mock.fetch_balances.return_value = DashboardBalances(
            as_of_date="2026-10-04",
            net_worth=1000.0,
            total_cash=1000.0,
            total_investment=0.0,
            total_card_liabilities=0.0,
            total_loan=0.0,
            total_mortgage=0.0,
            total_other_assets=0.0,
            total_other_liabilities=0.0,
            accounts=[],
            mode="live"
        )
        client_mock.fetch_holdings.return_value = None
        client_mock.fetch_transactions.return_value = None
        mock_build_client.return_value = client_mock

        test_args = ["empower", "--login", "--balances", "--format", "json"]
        with patch.object(sys, "argv", test_args):
            ret = main()
            self.assertEqual(ret, 0)
            mock_interactive_login.assert_called_once()
            client_mock.fetch_balances.assert_called_once()

    def test_interactive_login_user_remembered_skips_2fa(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = None
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "USER_REMEMBERED",
                "deviceName": "Empower CLI Client",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_email="user@example.com",
                cli_password="secret-password",
            )

        self.assertEqual(code, 0)
        mock_client.identify_user.assert_called_once_with(username="user@example.com")
        mock_client.authenticate_password.assert_called_once_with(password="secret-password")
        mock_client.request_2fa_challenge.assert_not_called()
        mock_client.submit_2fa_code.assert_not_called()
        output_str = out.getvalue()
        self.assertIn("Device recognized ('Empower CLI Client'). No SMS/Email 2FA challenge required.", output_str)
        self.assertIn("Password authenticated and session resumed.", output_str)

    def test_interactive_login_mfa_required_performs_full_flow(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = None
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "MFA_REQUIRED",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_email="user@example.com",
                cli_password="secret-password",
                cli_mode="SMS",
                cli_code="123456",
            )

        self.assertEqual(code, 0)
        mock_client.identify_user.assert_called_once_with(username="user@example.com")
        mock_client.request_2fa_challenge.assert_called_once_with(mode="SMS")
        mock_client.submit_2fa_code.assert_called_once_with(code="123456", mode="SMS")
        mock_client.authenticate_password.assert_called_once_with(password="secret-password")
        output_str = out.getvalue()
        self.assertIn("Two-factor authentication (2FA) is required.", output_str)
        self.assertIn("2FA verified successfully.", output_str)
        self.assertIn("Password authenticated and device registered.", output_str)

    def test_interactive_login_uses_saved_client_username(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = "saved-user@example.com"
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "USER_REMEMBERED",
                "deviceName": "Empower CLI Client",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out), patch("builtins.input", side_effect=EOFError):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_password="secret-password",
            )

        self.assertEqual(code, 0)
        mock_client.identify_user.assert_called_once_with(username="saved-user@example.com")

    def test_interactive_login_session_authenticated(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = None
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "SESSION_AUTHENTICATED",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_email="user@example.com",
                cli_password="secret-password",
            )

        self.assertEqual(code, 0)
        mock_client.identify_user.assert_called_once_with(username="user@example.com")
        output_str = out.getvalue()
        self.assertIn("Session is already active and authenticated.", output_str)

    def test_interactive_login_unknown_auth_level_with_password(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = None
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "UNKNOWN_LEVEL",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_email="user@example.com",
                cli_password="secret-password",
            )

        self.assertEqual(code, 0)
        mock_client.authenticate_password.assert_called_once_with(password="secret-password")
        mock_client.save_session.assert_called_once()

    def test_interactive_login_reads_saved_session_file(self):
        from empower_personal_dashboard.cli import interactive_login
        import io
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            sess_path = Path(tmpdir) / "session.json"
            sess_path.write_text(json.dumps({"username": "saved@example.com", "csrf": "saved-csrf"}))

            mock_client = MagicMock()
            mock_client.username = None
            mock_client.identify_user.return_value = {
                "spHeader": {
                    "success": True,
                    "authLevel": "USER_REMEMBERED",
                    "deviceName": "Empower CLI Client",
                }
            }
            mock_client.save_session.return_value = sess_path

            out = io.StringIO()
            with patch("sys.stdout", out):
                code = interactive_login(
                    mock_client,
                    sess_path,
                    cli_password="secret-password",
                )

            self.assertEqual(code, 0)
            mock_client.identify_user.assert_called_once_with(username="saved@example.com")

    def test_interactive_login_invalid_session_file_skipped(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        with tempfile.TemporaryDirectory() as tmpdir:
            sess_path = Path(tmpdir) / "session.json"
            sess_path.write_text("invalid json content")

            mock_client = MagicMock()
            mock_client.username = None
            mock_client.identify_user.return_value = {
                "spHeader": {
                    "success": True,
                    "authLevel": "USER_REMEMBERED",
                }
            }
            mock_client.save_session.return_value = sess_path

            out = io.StringIO()
            with patch("sys.stdout", out), patch("builtins.input", return_value="user@example.com"):
                code = interactive_login(
                    mock_client,
                    sess_path,
                    cli_password="secret-password",
                )

            self.assertEqual(code, 0)
            mock_client.identify_user.assert_called_once_with(username="user@example.com")

    def test_interactive_login_session_file_no_username_key(self):
        from empower_personal_dashboard.cli import interactive_login
        import io
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            sess_path = Path(tmpdir) / "session.json"
            sess_path.write_text(json.dumps({"csrf": "saved-csrf"}))

            mock_client = MagicMock()
            mock_client.username = None
            mock_client.identify_user.return_value = {
                "spHeader": {
                    "success": True,
                    "authLevel": "USER_REMEMBERED",
                }
            }
            mock_client.save_session.return_value = sess_path

            out = io.StringIO()
            with patch("sys.stdout", out), patch("builtins.input", return_value="user@example.com"):
                code = interactive_login(
                    mock_client,
                    sess_path,
                    cli_password="secret-password",
                )

            self.assertEqual(code, 0)
            mock_client.identify_user.assert_called_once_with(username="user@example.com")

    def test_interactive_login_user_remembered_password_prompt(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = None
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "USER_REMEMBERED",
                "deviceName": "Empower CLI Client",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out), patch("getpass.getpass", return_value="prompted-password"):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_email="user@example.com",
            )

        self.assertEqual(code, 0)
        mock_client.authenticate_password.assert_called_once_with(password="prompted-password")

    def test_interactive_login_mfa_required_password_prompt(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = None
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "MFA_REQUIRED",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out), patch("builtins.input", return_value="123456"), \
             patch("getpass.getpass", return_value="prompted-password"):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_email="user@example.com",
                cli_mode="SMS",
            )

        self.assertEqual(code, 0)
        mock_client.authenticate_password.assert_called_once_with(password="prompted-password")

    def test_interactive_login_unknown_auth_level_password_prompt(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = None
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "UNKNOWN_LEVEL",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out), patch("getpass.getpass", return_value="prompted-password"):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_email="user@example.com",
            )

        self.assertEqual(code, 0)
        mock_client.authenticate_password.assert_called_once_with(password="prompted-password")

    def test_interactive_login_mfa_required_email_mode(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = None
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "MFA_REQUIRED",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out), patch("builtins.input", return_value="E123456"):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_email="user@example.com",
                cli_password="secret-password",
            )

        self.assertEqual(code, 0)
        mock_client.request_2fa_challenge.assert_called_once_with(mode="EMAIL")

    def test_interactive_login_mfa_required_sms_input_prompt(self):
        from empower_personal_dashboard.cli import interactive_login
        import io

        mock_client = MagicMock()
        mock_client.username = None
        mock_client.identify_user.return_value = {
            "spHeader": {
                "success": True,
                "authLevel": "MFA_REQUIRED",
            }
        }
        mock_client.save_session.return_value = Path("/tmp/mock_session.json")

        out = io.StringIO()
        with patch("sys.stdout", out), patch("builtins.input", side_effect=["", "123456"]):
            code = interactive_login(
                mock_client,
                Path("/tmp/mock_session.json"),
                cli_email="user@example.com",
                cli_password="secret-password",
            )

        self.assertEqual(code, 0)
        mock_client.request_2fa_challenge.assert_called_once_with(mode="SMS")


class TestCliProcessConcurrency(unittest.TestCase):
    """Multi-process end-to-end concurrency tests verifying OS flock serialization."""

    def test_subprocess_merge_lock_timeout_exits_nonzero(self):
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            original = '{"user_transaction_id": "INIT"}\n'
            out.write_text(original, encoding="utf-8")
            fd = os.open(tmpdir, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                res = subprocess.run([
                    sys.executable, "-u", "-m", "empower_personal_dashboard.cli",
                    "--transactions", "--merge",
                    "--output-transactions", str(out),
                    "--merge-lock-timeout", "0.1",
                    "--mock",
                ], capture_output=True, text=True, env=_SUBPROCESS_ENV)
                self.assertEqual(res.returncode, 1)
                self.assertIn("[*] Another --merge is running for this archive; waiting...", res.stdout)
                self.assertIn("Timed out waiting for archive lock", res.stderr)
                self.assertIn("The archive was left unchanged.", res.stderr)
                self.assertEqual(out.read_text(encoding="utf-8"), original)
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                except OSError:
                    pass

    def test_subprocess_merge_lock_released_in_flight(self):
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text('{"user_transaction_id": "INIT"}\n', encoding="utf-8")
            fd = os.open(tmpdir, os.O_RDONLY)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            p = subprocess.Popen([
                sys.executable, "-u", "-m", "empower_personal_dashboard.cli",
                "--transactions", "--merge",
                "--output-transactions", str(out),
                "--merge-lock-timeout", "10.0",
                "--mock",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_SUBPROCESS_ENV)
            collected = []
            lock_released = False
            try:
                while True:
                    line = p.stdout.readline()
                    if not line:
                        break
                    collected.append(line)
                    if "[*] Another --merge is running for this archive; waiting..." in line:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                        os.close(fd)
                        lock_released = True
                        break
            finally:
                if not lock_released:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                        os.close(fd)
                    except OSError:
                        pass

            rest_stdout, stderr = p.communicate()
            full_stdout = "".join(collected) + rest_stdout
            self.assertTrue(lock_released)
            self.assertEqual(p.returncode, 0, f"Error: {stderr}")
            self.assertIn("[*] Another --merge is running for this archive; waiting...", full_stdout)
            self.assertIn("Transactions saved to:", full_stdout)

    def test_subprocess_merge_lock_quiet_suppresses_waiting_notice(self):
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            fd = os.open(tmpdir, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                res = subprocess.run([
                    sys.executable, "-u", "-m", "empower_personal_dashboard.cli",
                    "--transactions", "--merge",
                    "--output-transactions", str(out),
                    "--merge-lock-timeout", "0.1",
                    "--quiet",
                    "--mock",
                ], capture_output=True, text=True, env=_SUBPROCESS_ENV)
                self.assertEqual(res.returncode, 1)
                self.assertNotIn("[*] Another --merge is running", res.stdout)
                self.assertIn("Timed out waiting for archive lock", res.stderr)
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                except OSError:
                    pass

    def test_subprocess_concurrent_merges_serialize_cleanly(self):
        try:
            import fcntl
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "transactions.jsonl"
            out.write_text('{"user_transaction_id": "T_INIT", "amount": 1.0}\n', encoding="utf-8")
            cmd = [
                sys.executable, "-u", "-m", "empower_personal_dashboard.cli",
                "--transactions", "--merge",
                "--output-transactions", str(out),
                "--merge-lock-timeout", "10.0",
                "--mock",
            ]
            p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_SUBPROCESS_ENV)
            p2 = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_SUBPROCESS_ENV)
            out1, err1 = p1.communicate()
            out2, err2 = p2.communicate()
            self.assertEqual(p1.returncode, 0, f"P1 error: {err1}")
            self.assertEqual(p2.returncode, 0, f"P2 error: {err2}")
            lines = [l for l in out.read_text(encoding="utf-8").strip().split("\n") if l]
            self.assertEqual(len(lines), 5)


if __name__ == "__main__":
    unittest.main()

