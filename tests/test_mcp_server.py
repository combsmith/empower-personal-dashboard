"""
test_mcp_server.py — Unit tests for Empower Personal Dashboard FastMCP server.
"""

import asyncio
import functools
import json
import os
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from empower_personal_dashboard.client import EmpowerDashboardClient
from empower_personal_dashboard.exceptions import EmpowerError, SessionExpiredError
from empower_personal_dashboard.mcp_server import (
    FastMCP,
    _DataCache,
    _mask_account_identifier,
    create_mcp_server,
)


class _FakeFastMCP:
    """Lightweight test double for FastMCP used when the optional 'mcp' package is not installed."""

    def __init__(self, name: str, instructions: str = "") -> None:
        """Initialize the fake MCP server registry."""
        self.name = name
        self.instructions = instructions
        self._tools: dict = {}
        self._resources: dict = {}
        self._prompts: dict = {}

    def tool(self):
        """Register a tool callable."""
        def decorator(fn):
            """Store tool callable by function name."""
            self._tools[fn.__name__] = fn
            return fn

        return decorator

    def resource(self, uri: str):
        """Register a resource callable by URI."""
        def decorator(fn):
            """Store resource callable by URI."""
            self._resources[uri] = fn
            return fn

        return decorator

    def prompt(self):
        """Register a prompt callable."""
        def decorator(fn):
            """Store prompt callable by function name."""
            self._prompts[fn.__name__] = fn
            return fn

        return decorator

    async def list_tools(self):
        """List registered tool descriptors."""
        return [types.SimpleNamespace(name=n) for n in self._tools]

    async def list_resources(self):
        """List registered resource descriptors."""
        return [types.SimpleNamespace(uri=u) for u in self._resources]

    async def list_prompts(self):
        """List registered prompt descriptors."""
        return [types.SimpleNamespace(name=n) for n in self._prompts]

    async def call_tool(self, name: str, arguments: dict):
        """Invoke a registered tool and wrap its JSON payload."""
        res = self._tools[name](**arguments)
        return types.SimpleNamespace(
            is_error=False,
            content=[types.SimpleNamespace(text=json.dumps(res))],
        )

    async def read_resource(self, uri: str):
        """Read a registered resource by URI."""
        return [self._resources[uri]()]

    async def get_prompt(self, name: str, arguments: dict):
        """Render a registered prompt by name."""
        res = self._prompts[name](**arguments)
        return types.SimpleNamespace(
            messages=[types.SimpleNamespace(content=types.SimpleNamespace(text=res))]
        )


def requires_fastmcp(test):
    """Run the test body using real FastMCP when installed, or _FakeFastMCP otherwise."""

    @functools.wraps(test)
    def wrapper(self, *args, **kwargs):
        """Execute test with FastMCP or the _FakeFastMCP test double."""
        if FastMCP is None:
            with patch("empower_personal_dashboard.mcp_server.FastMCP", _FakeFastMCP):
                self.client = EmpowerDashboardClient(mock_mode=True)
                self.server = create_mcp_server(client=self.client)
                return test(self, *args, **kwargs)
        return test(self, *args, **kwargs)

    return wrapper


class TestMCPServer(unittest.TestCase):
    def setUp(self) -> None:
        if FastMCP is not None:
            self.client = EmpowerDashboardClient(mock_mode=True)
            self.server = create_mcp_server(client=self.client)
        else:
            self.client = None
            self.server = None

    def test_mask_account_identifier(self) -> None:
        self.assertEqual(_mask_account_identifier(""), "")
        self.assertEqual(_mask_account_identifier(None), "")
        self.assertEqual(_mask_account_identifier("1234"), "1234")
        self.assertEqual(_mask_account_identifier("9876543210"), "****3210")
        self.assertEqual(_mask_account_identifier("123"), "123")

    @requires_fastmcp
    def test_create_mcp_server_registration(self) -> None:
        async def run_check() -> None:
            tools = await self.server.list_tools()
            tool_names = [t.name for t in tools]
            self.assertIn("get_net_worth_summary", tool_names)
            self.assertIn("get_balances", tool_names)
            self.assertIn("get_holdings", tool_names)
            self.assertIn("get_transactions", tool_names)
            self.assertIn("check_auth_status", tool_names)
            self.assertIn("export_data", tool_names)
            self.assertIn("get_export_options", tool_names)

            resources = await self.server.list_resources()
            resource_uris = [str(r.uri) for r in resources]
            self.assertIn("empower://balances/summary", resource_uris)
            self.assertIn("empower://holdings/portfolio", resource_uris)
            self.assertIn("empower://accounts/list", resource_uris)
            self.assertIn("empower://export/options", resource_uris)

            prompts = await self.server.list_prompts()
            prompt_names = [p.name for p in prompts]
            self.assertIn("portfolio_review", prompt_names)
            self.assertIn("spending_audit", prompt_names)
            self.assertIn("recent_purchases_audit", prompt_names)
            self.assertIn("top_holdings_review", prompt_names)

        asyncio.run(run_check())

    @requires_fastmcp
    def test_create_mcp_server_configures_password_provider(self) -> None:
        client = EmpowerDashboardClient(mock_mode=True)
        self.assertIsNone(client.password_provider)
        create_mcp_server(client=client)
        self.assertIsNotNone(client.password_provider)

    @requires_fastmcp
    @patch("empower_personal_dashboard.mcp_server.resolve_password")
    def test_mcp_password_provider_respects_falsy_empower_keyring(self, mock_resolve) -> None:
        client = EmpowerDashboardClient(mock_mode=True, username="mcp_user@example.com")
        create_mcp_server(client=client)
        self.assertIsNotNone(client.password_provider)

        with patch.dict(os.environ, {"EMPOWER_KEYRING": "0"}, clear=True):
            client.password_provider()
            mock_resolve.assert_called_with(
                username="mcp_user@example.com",
                credential_helper=None,
            )

    @requires_fastmcp
    def test_tool_get_net_worth_summary(self) -> None:
        async def run_check() -> None:
            res = await self.server.call_tool("get_net_worth_summary", {})
            self.assertFalse(res.is_error)
            payload = json.loads(res.content[0].text)
            self.assertEqual(payload["status"], "success")
            self.assertGreater(payload["net_worth"], 0)
            self.assertGreater(payload["total_cash"], 0)
            self.assertGreater(payload["total_investment"], 0)
            self.assertEqual(payload["accounts_count"], 5)

            # Markdown format
            res_md = await self.server.call_tool("get_net_worth_summary", {"format": "markdown"})
            payload_md = json.loads(res_md.content[0].text)
            self.assertIn("formatted_output", payload_md)
            self.assertIn("### Empower Net Worth Summary", payload_md["formatted_output"])

            # Table format
            res_tbl = await self.server.call_tool("get_net_worth_summary", {"format": "table"})
            payload_tbl = json.loads(res_tbl.content[0].text)
            self.assertIn("formatted_output", payload_tbl)
            self.assertNotEqual(payload_tbl["formatted_output"], payload_md["formatted_output"])
            self.assertIn("| Category | Amount |", payload_tbl["formatted_output"])

        asyncio.run(run_check())

    @requires_fastmcp
    def test_tool_get_balances_with_filtering(self) -> None:
        async def run_check() -> None:
            # All accounts
            res_all = await self.server.call_tool("get_balances", {})
            payload_all = json.loads(res_all.content[0].text)
            self.assertEqual(payload_all["status"], "success")
            self.assertEqual(payload_all["accounts_count"], 5)

            # Cash filter
            res_cash = await self.server.call_tool("get_balances", {"account_types": ["CASH"], "format": "markdown"})
            payload_cash = json.loads(res_cash.content[0].text)
            self.assertEqual(payload_cash["status"], "success")
            self.assertEqual(payload_cash["accounts_count"], 2)
            self.assertGreater(payload_cash["totals_by_type"]["cash"], 0)
            self.assertEqual(payload_cash["totals_by_type"]["investment"], 0.0)
            for a in payload_cash["accounts"]:
                self.assertEqual(a["account_type"], "BANK")
            self.assertNotIn("Horizon 401(k)", payload_cash["formatted_output"])

            # Investment filter
            res_inv = await self.server.call_tool("get_balances", {"account_types": ["INVESTMENT"]})
            payload_inv = json.loads(res_inv.content[0].text)
            self.assertEqual(payload_inv["status"], "success")
            self.assertEqual(payload_inv["accounts_count"], 2)
            self.assertGreater(payload_inv["totals_by_type"]["investment"], 0)
            self.assertEqual(payload_inv["totals_by_type"]["cash"], 0.0)

            # Markdown and table formats
            res_md = await self.server.call_tool("get_balances", {"format": "markdown"})
            payload_md = json.loads(res_md.content[0].text)
            self.assertIn("formatted_output", payload_md)
            self.assertIn("|", payload_md["formatted_output"])

            res_tbl = await self.server.call_tool("get_balances", {"format": "table"})
            payload_tbl = json.loads(res_tbl.content[0].text)
            self.assertIn("formatted_output", payload_tbl)

        asyncio.run(run_check())

    @requires_fastmcp
    def test_tool_get_holdings_with_filter_and_limit(self) -> None:
        async def run_check() -> None:
            # Filter by ticker
            res_vti = await self.server.call_tool("get_holdings", {"ticker": "VTI"})
            payload_vti = json.loads(res_vti.content[0].text)
            self.assertEqual(payload_vti["status"], "success")
            self.assertEqual(payload_vti["matching_count"], 1)
            self.assertEqual(payload_vti["holdings"][0]["ticker"], "VTI")

            # Clamped limit
            res_limit = await self.server.call_tool("get_holdings", {"limit": 1})
            payload_limit = json.loads(res_limit.content[0].text)
            self.assertEqual(payload_limit["returned_count"], 1)

            # Aggregation by ticker
            res_agg = await self.server.call_tool("get_holdings", {"aggregate_by_ticker": True})
            payload_agg = json.loads(res_agg.content[0].text)
            self.assertEqual(payload_agg["status"], "success")
            for h in payload_agg["holdings"]:
                self.assertIn("holding_percentage", h)
                self.assertIn("accounts_count", h)
                for acct in h.get("accounts", []):
                    self.assertIn("user_account_id", acct)
                    self.assertIn("account_id", acct)
                    self.assertIsNotNone(acct["user_account_id"])

            # Filter by account_id
            res_acct = await self.server.call_tool("get_holdings", {"account_id": 1001})
            payload_acct = json.loads(res_acct.content[0].text)
            self.assertEqual(payload_acct["status"], "success")
            self.assertGreaterEqual(payload_acct["matching_count"], 1)

            # Sort by ticker
            res_sort_ticker = await self.server.call_tool("get_holdings", {"sort_by": "ticker", "format": "markdown"})
            payload_sort_ticker = json.loads(res_sort_ticker.content[0].text)
            tickers = [h["ticker"] for h in payload_sort_ticker["holdings"]]
            self.assertEqual(tickers, sorted(tickers))
            self.assertIn("#### Top Positions", payload_sort_ticker["formatted_output"])

            # Min value filter
            res_min = await self.server.call_tool("get_holdings", {"min_value": 50000.0})
            payload_min = json.loads(res_min.content[0].text)
            for h in payload_min["holdings"]:
                self.assertGreaterEqual(h["value"], 50000.0)

            # Formats
            res_md = await self.server.call_tool("get_holdings", {"format": "markdown"})
            payload_md = json.loads(res_md.content[0].text)
            self.assertIn("formatted_output", payload_md)

            res_tbl = await self.server.call_tool("get_holdings", {"format": "table"})
            payload_tbl = json.loads(res_tbl.content[0].text)
            self.assertIn("formatted_output", payload_tbl)

        asyncio.run(run_check())

    @requires_fastmcp
    def test_tool_get_transactions_with_filter(self) -> None:
        async def run_check() -> None:
            res = await self.server.call_tool(
                "get_transactions",
                {"start_date": "2026-09-01", "end_date": "2026-09-30", "limit": 10},
            )
            payload = json.loads(res.content[0].text)
            self.assertEqual(payload["status"], "success")
            self.assertGreaterEqual(payload["total_transactions"], 1)
            self.assertGreaterEqual(payload["returned_count"], 1)

            # Category filter
            res_cat = await self.server.call_tool("get_transactions", {"category": "payroll"})
            payload_cat = json.loads(res_cat.content[0].text)
            self.assertEqual(payload_cat["status"], "success")

            # Relative days filter
            res_days = await self.server.call_tool("get_transactions", {"days": 30})
            payload_days = json.loads(res_days.content[0].text)
            self.assertEqual(payload_days["status"], "success")
            self.assertGreater(payload_days["returned_count"], 0)
            now_utc = datetime.now(timezone.utc)
            min_date = (now_utc - timedelta(days=30)).strftime("%Y-%m-%d")
            for tx in payload_days["transactions"]:
                self.assertGreaterEqual(tx["transaction_date"], min_date)

            # Negative days error check
            res_neg = await self.server.call_tool("get_transactions", {"days": -5})
            payload_neg = json.loads(res_neg.content[0].text)
            self.assertEqual(payload_neg["status"], "error")
            self.assertEqual(payload_neg["error_code"], "INVALID_ARGUMENT")

            # Spending only filter with cashflow recalculation
            res_spending = await self.server.call_tool("get_transactions", {"spending_only": True})
            payload_spending = json.loads(res_spending.content[0].text)
            self.assertEqual(payload_spending["status"], "success")
            self.assertIn("period_net_cashflow", payload_spending)
            for tx in payload_spending["transactions"]:
                self.assertTrue(tx["is_spending"])
            self.assertLessEqual(payload_spending["net_cashflow"], 0)

            # Income only filter
            res_income = await self.server.call_tool("get_transactions", {"income_only": True})
            payload_income = json.loads(res_income.content[0].text)
            self.assertEqual(payload_income["status"], "success")
            for tx in payload_income["transactions"]:
                self.assertTrue(tx["is_income"])

            # Transaction type filter
            res_type = await self.server.call_tool("get_transactions", {"transaction_type": "Purchase"})
            payload_type = json.loads(res_type.content[0].text)
            self.assertEqual(payload_type["status"], "success")
            for tx in payload_type["transactions"]:
                self.assertEqual(tx["transaction_type"], "Purchase")

            # Min amount filter
            res_amount = await self.server.call_tool("get_transactions", {"min_amount": 1000.0})
            payload_amount = json.loads(res_amount.content[0].text)
            for tx in payload_amount["transactions"]:
                self.assertGreaterEqual(abs(tx["amount"]), 1000.0)

            # Formatted output
            res_md = await self.server.call_tool("get_transactions", {"format": "markdown"})
            payload_md = json.loads(res_md.content[0].text)
            self.assertIn("formatted_output", payload_md)

            res_tbl = await self.server.call_tool("get_transactions", {"format": "table"})
            payload_tbl = json.loads(res_tbl.content[0].text)
            self.assertIn("formatted_output", payload_tbl)

        asyncio.run(run_check())

    @requires_fastmcp
    def test_tool_check_auth_status(self) -> None:
        async def run_check() -> None:
            res = await self.server.call_tool("check_auth_status", {})
            payload = json.loads(res.content[0].text)
            self.assertTrue(payload["authenticated"])
            self.assertEqual(payload["status"], "authenticated")

        asyncio.run(run_check())

    @requires_fastmcp
    def test_auth_required_error_handling(self) -> None:
        async def run_check() -> None:
            mock_client = MagicMock()
            mock_client.fetch_balances.side_effect = SessionExpiredError("Session token expired")
            failing_server = create_mcp_server(client=mock_client)

            res = await failing_server.call_tool("get_net_worth_summary", {})
            payload = json.loads(res.content[0].text)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error_code"], "AUTH_REQUIRED")
            self.assertIn("empower --login", payload["message"])

        asyncio.run(run_check())

    @requires_fastmcp
    def test_auto_reload_session_on_mtime_change(self) -> None:
        async def run_check() -> None:
            mock_client = MagicMock()
            mock_client.mock_mode = False
            mock_session_file = MagicMock()
            mock_session_file.exists.return_value = True
            mock_session_file.stat.return_value.st_mtime = 100.0
            mock_client.session_file = mock_session_file
            mock_client.fetch_balances.return_value = self.client.fetch_balances()

            server = create_mcp_server(client=mock_client)
            await server.call_tool("get_net_worth_summary", {})
            self.assertEqual(mock_client.load_session.call_count, 0)

            # Simulate out-of-band login updating file mtime on disk
            mock_session_file.stat.return_value.st_mtime = 200.0
            await server.call_tool("check_auth_status", {})
            self.assertEqual(mock_client.load_session.call_count, 1)

        asyncio.run(run_check())

    @requires_fastmcp
    def test_api_error_handling(self) -> None:
        async def run_check() -> None:
            mock_client = MagicMock()
            mock_client.fetch_balances.side_effect = EmpowerError("Upstream timeout")
            failing_server = create_mcp_server(client=mock_client)

            res = await failing_server.call_tool("get_net_worth_summary", {})
            payload = json.loads(res.content[0].text)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error_code"], "API_ERROR")
            self.assertIn("Upstream timeout", payload["message"])

        asyncio.run(run_check())

    @requires_fastmcp
    def test_in_memory_ttl_caching(self) -> None:
        async def run_check() -> None:
            mock_client = MagicMock()
            mock_client.fetch_balances.return_value = self.client.fetch_balances()
            cached_server = create_mcp_server(client=mock_client, cache_ttl_seconds=60)

            # First call triggers fetch_balances
            await cached_server.call_tool("get_net_worth_summary", {})
            self.assertEqual(mock_client.fetch_balances.call_count, 1)

            # Second call within TTL hits cache
            await cached_server.call_tool("get_net_worth_summary", {})
            self.assertEqual(mock_client.fetch_balances.call_count, 1)

            # Different tool sharing balance cache hits cache
            await cached_server.call_tool("get_balances", {})
            self.assertEqual(mock_client.fetch_balances.call_count, 1)

        asyncio.run(run_check())

    @requires_fastmcp
    def test_tool_get_export_options(self) -> None:
        async def run_check() -> None:
            res = await self.server.call_tool("get_export_options", {})
            self.assertFalse(res.is_error)
            payload = json.loads(res.content[0].text)
            self.assertEqual(payload["status"], "success")
            self.assertIn("json", payload["supported_formats"])
            self.assertIn("csv", payload["supported_formats"])
            self.assertIn("all", payload["data_scopes"])
            self.assertIn("bulk_export_csv", payload["cli_examples"])
            self.assertIn("export_data", payload["mcp_export_tool"])

        asyncio.run(run_check())

    @requires_fastmcp
    def test_tool_export_data(self) -> None:
        async def run_check() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch.dict(os.environ, {"EMPOWER_EXPORT_ROOT": tmpdir}):
                    res = await self.server.call_tool(
                        "export_data",
                        {"destination_dir": tmpdir, "scope": "all", "export_csv": True},
                    )
                    self.assertFalse(res.is_error)
                    payload = json.loads(res.content[0].text)
                    self.assertEqual(payload["status"], "success")
                    self.assertEqual(payload["files_count"], 6)

                    dest = Path(tmpdir)
                    self.assertTrue((dest / "empower_balances.json").exists())
                    self.assertTrue((dest / "empower_balances.csv").exists())
                    self.assertTrue((dest / "empower_holdings.json").exists())
                    self.assertTrue((dest / "empower_holdings.csv").exists())
                    self.assertTrue((dest / "empower_transactions.jsonl").exists())
                    self.assertTrue((dest / "empower_transactions.csv").exists())

                    # Invalid scope validation
                    res_inv_scope = await self.server.call_tool(
                        "export_data",
                        {"destination_dir": tmpdir, "scope": "invalid_scope"},
                    )
                    payload_inv_scope = json.loads(res_inv_scope.content[0].text)
                    self.assertEqual(payload_inv_scope["status"], "error")
                    self.assertEqual(payload_inv_scope["error_code"], "INVALID_ARGUMENT")

                    # Path traversal outside EMPOWER_EXPORT_ROOT
                    outside_dir = str(Path(tmpdir).parent / "unauthorized_export_dir")
                    res_outside = await self.server.call_tool(
                        "export_data",
                        {"destination_dir": outside_dir, "scope": "all"},
                    )
                    payload_outside = json.loads(res_outside.content[0].text)
                    self.assertEqual(payload_outside["status"], "error")
                    self.assertEqual(payload_outside["error_code"], "ACCESS_DENIED")

                    # Symlinked destination directory rejection
                    sym_dir = dest / "symlink_dir"
                    try:
                        sym_dir.symlink_to(dest)
                        res_sym = await self.server.call_tool(
                            "export_data",
                            {"destination_dir": str(sym_dir), "scope": "balances"},
                        )
                        payload_sym = json.loads(res_sym.content[0].text)
                        self.assertEqual(payload_sym["status"], "error")
                        self.assertEqual(payload_sym["error_code"], "ACCESS_DENIED")
                    finally:
                        if sym_dir.is_symlink():
                            sym_dir.unlink()

                    # Symlinked target file rejection
                    sym_target = dest / "sub_export"
                    sym_target.mkdir()
                    dummy_target = dest / "dummy.json"
                    dummy_target.write_text("{}")
                    target_link = sym_target / "empower_balances.json"
                    try:
                        target_link.symlink_to(dummy_target)
                        res_file_sym = await self.server.call_tool(
                            "export_data",
                            {"destination_dir": str(sym_target), "scope": "balances"},
                        )
                        payload_file_sym = json.loads(res_file_sym.content[0].text)
                        self.assertEqual(payload_file_sym["status"], "error")
                        self.assertEqual(payload_file_sym["error_code"], "ACCESS_DENIED")
                    finally:
                        if target_link.is_symlink():
                            target_link.unlink()

            # Specific scope without CSV
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch.dict(os.environ, {"EMPOWER_EXPORT_ROOT": tmpdir}):
                    res_bal = await self.server.call_tool(
                        "export_data",
                        {"destination_dir": tmpdir, "scope": "balances", "export_csv": False},
                    )
                    payload_bal = json.loads(res_bal.content[0].text)
                    self.assertEqual(payload_bal["status"], "success")
                    self.assertEqual(payload_bal["files_count"], 1)
                    dest = Path(tmpdir)
                    self.assertTrue((dest / "empower_balances.json").exists())
                    self.assertFalse((dest / "empower_balances.csv").exists())

            # Export with Beancount enabled
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch.dict(os.environ, {"EMPOWER_EXPORT_ROOT": tmpdir}):
                    res_bean = await self.server.call_tool(
                        "export_data",
                        {"destination_dir": tmpdir, "scope": "all", "export_csv": False, "export_beancount": True},
                    )
                    payload_bean = json.loads(res_bean.content[0].text)
                    self.assertEqual(payload_bean["status"], "success")
                    dest = Path(tmpdir)
                    main_bean = dest / "ledger" / "main.bean"
                    accounts_bean = dest / "ledger" / "accounts.bean"
                    balances_bean = dest / "ledger" / "balances.bean"
                    holdings_bean = dest / "ledger" / "holdings.bean"
                    prices_bean = dest / "ledger" / "prices.bean"
                    transactions_bean = dest / "ledger" / "transactions.bean"

                    self.assertTrue(main_bean.exists())
                    self.assertIn('option "operating_currency" "USD"', main_bean.read_text(encoding="utf-8"))
                    self.assertIn('include "holdings.bean"', main_bean.read_text(encoding="utf-8"))

                    self.assertTrue(accounts_bean.exists())
                    self.assertIn("open Equity:Opening-Balances", accounts_bean.read_text(encoding="utf-8"))

                    self.assertTrue(balances_bean.exists())
                    self.assertIn("balance Assets:", balances_bean.read_text(encoding="utf-8"))

                    self.assertTrue(holdings_bean.exists())
                    self.assertIn("Portfolio Snapshot", holdings_bean.read_text(encoding="utf-8"))

                    self.assertTrue(prices_bean.exists())
                    self.assertIn("price", prices_bean.read_text(encoding="utf-8"))

                    self.assertTrue(transactions_bean.exists())
                    self.assertIn("empower_id:", transactions_bean.read_text(encoding="utf-8"))

            # Verify get_export_options tool contains beancount
            res_options = await self.server.call_tool("get_export_options", {})
            payload_options = json.loads(res_options.content[0].text)
            self.assertIn("beancount", payload_options["supported_formats"])

        asyncio.run(run_check())

    @requires_fastmcp
    def test_resources_and_prompts(self) -> None:
        async def run_check() -> None:
            # Read resource
            res_content = await self.server.read_resource("empower://balances/summary")
            self.assertGreater(len(res_content), 0)

            res_export = await self.server.read_resource("empower://export/options")
            self.assertIn("Export Options", str(res_export))
            self.assertIn("Beancount", str(res_export))

            # Read Beancount resources
            res_prices = await self.server.read_resource("empower://beancount/prices")
            self.assertIn("price", str(res_prices))

            res_bean_bal = await self.server.read_resource("empower://beancount/balances")
            self.assertIn("balance", str(res_bean_bal))

            # Get prompts
            prompt_res = await self.server.get_prompt("portfolio_review", {"risk_profile": "aggressive"})
            self.assertIn("portfolio review", prompt_res.messages[0].content.text)
            self.assertIn("aggressive", prompt_res.messages[0].content.text)

            prompt_purchases = await self.server.get_prompt("recent_purchases_audit", {"days": 14})
            self.assertIn("14 days", prompt_purchases.messages[0].content.text)

            prompt_top_holdings = await self.server.get_prompt("top_holdings_review", {"limit": 5})
            self.assertIn("top 5 investment holdings", prompt_top_holdings.messages[0].content.text)

        asyncio.run(run_check())

    @requires_fastmcp
    def test_beancount_resources_handle_auth_error_gracefully(self) -> None:
        async def run_check() -> None:
            from empower_personal_dashboard.exceptions import SessionExpiredError

            error_client = EmpowerDashboardClient(mock_mode=True)
            error_client.fetch_holdings = MagicMock(side_effect=SessionExpiredError("Expired session"))
            error_client.fetch_balances = MagicMock(side_effect=SessionExpiredError("Expired session"))
            error_client.fetch_transactions = MagicMock(side_effect=SessionExpiredError("Expired session"))
            err_server = create_mcp_server(client=error_client)

            res_prices = await err_server.read_resource("empower://beancount/prices")
            self.assertIn(";; ERROR [AUTH_REQUIRED]", str(res_prices))
            self.assertIn("empower --login", str(res_prices))

            res_balances = await err_server.read_resource("empower://beancount/balances")
            self.assertIn(";; ERROR [AUTH_REQUIRED]", str(res_balances))
            self.assertIn("empower --login", str(res_balances))

            res_tx = await err_server.read_resource("empower://beancount/transactions")
            self.assertIn(";; ERROR [AUTH_REQUIRED]", str(res_tx))
            self.assertIn("empower --login", str(res_tx))

        asyncio.run(run_check())

    @requires_fastmcp
    def test_export_data_beancount_captures_balance_warnings(self) -> None:
        async def run_check() -> None:
            from empower_personal_dashboard.exceptions import EmpowerError

            warn_client = EmpowerDashboardClient(mock_mode=True)
            warn_client.fetch_balances = MagicMock(side_effect=EmpowerError("Failed to fetch balances"))
            warn_server = create_mcp_server(client=warn_client)

            with tempfile.TemporaryDirectory() as tmpdir:
                with patch.dict(os.environ, {"EMPOWER_EXPORT_ROOT": tmpdir}):
                    res = await warn_server.call_tool(
                        "export_data",
                        {
                            "destination_dir": tmpdir,
                            "scope": "transactions",
                            "export_beancount": True,
                        },
                    )
                    payload = json.loads(res.content[0].text)
                    self.assertEqual(payload["status"], "success")
                    self.assertIn("warnings", payload)
                    self.assertTrue(any("Balances unavailable" in w for w in payload["warnings"]))

        asyncio.run(run_check())

    @requires_fastmcp
    def test_export_data_local_file_not_found_returns_export_error_not_auth_required(self) -> None:
        """Local FileNotFoundError in export_data returns EXPORT_ERROR while missing session maps to AUTH_REQUIRED."""
        async def run_check() -> None:
            """Verify local write errors return EXPORT_ERROR and session fetch errors return AUTH_REQUIRED."""
            with (
                tempfile.TemporaryDirectory() as tmpdir,
                patch.dict(os.environ, {"EMPOWER_EXPORT_ROOT": tmpdir}),
            ):
                # Local file write FileNotFoundError should report EXPORT_ERROR, not AUTH_REQUIRED
                with patch(
                    "empower_personal_dashboard.mcp_server.export_balances_csv",
                    side_effect=FileNotFoundError("No such file or directory: empower_balances.csv"),
                ):
                    res = await self.server.call_tool(
                        "export_data",
                        {"destination_dir": tmpdir, "scope": "balances", "export_csv": True},
                    )
                    payload = json.loads(res.content[0].text)
                    self.assertEqual(payload["status"], "error")
                    self.assertEqual(payload["error_code"], "EXPORT_ERROR")

                # Missing session FileNotFoundError during API fetch still maps to AUTH_REQUIRED
                missing_sess_client = EmpowerDashboardClient(mock_mode=True)
                missing_sess_client.fetch_balances = MagicMock(
                    side_effect=FileNotFoundError("Session file missing")
                )
                missing_sess_client.fetch_holdings = MagicMock(
                    side_effect=FileNotFoundError("Session file missing")
                )
                missing_sess_client.fetch_transactions = MagicMock(
                    side_effect=FileNotFoundError("Session file missing")
                )
                missing_sess_server = create_mcp_server(client=missing_sess_client)
                for scope in ("balances", "holdings", "transactions"):
                    res_auth = await missing_sess_server.call_tool(
                        "export_data",
                        {"destination_dir": tmpdir, "scope": scope, "export_csv": False},
                    )
                    payload_auth = json.loads(res_auth.content[0].text)
                    self.assertEqual(payload_auth["status"], "error")
                    self.assertEqual(payload_auth["error_code"], "AUTH_REQUIRED")

        asyncio.run(run_check())

    def test_import_error_when_mcp_missing(self) -> None:
        """Verify create_mcp_server raises ImportError when FastMCP is unavailable."""
        with patch("empower_personal_dashboard.mcp_server.FastMCP", None):
            with self.assertRaises(ImportError) as ctx:
                create_mcp_server()
            self.assertIn("pip install 'empower-personal-dashboard[mcp]'", str(ctx.exception))



if __name__ == "__main__":
    unittest.main()
