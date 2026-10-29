"""
test_beancount_directive_injection.py — Beancount directive-injection regressions.

Covers issues #139 (dates), #142 (mapped account targets), #143 (currencies), and
#147 (include filenames). Each syntax-bearing value the generator interpolates
unquoted (or inside an ``include`` string) could previously carry a line break
that smuggled an extra directive into the ledger. These tests feed crafted
synthetic values and require the export to fail closed (or, for include paths
that are merely unusual, to be escaped so the real ``beancount.loader`` reads
them back as the intended filename). All data is synthetic and PII-free.
"""

import json
import tempfile
import unittest
from pathlib import Path

from beancount import loader

from empower_personal_dashboard.beancount import (
    BeancountGenerator,
    BeancountMapper,
    _quote_beancount_string,
    _validate_account_name,
    _validate_currency,
    _validate_iso_date,
)
from empower_personal_dashboard.models import (
    DashboardBalances,
    DashboardHoldings,
    DashboardTransactions,
)

INJECTED_ACCOUNT = "Assets:Injected"
INJECTION = "\n2024-01-02 open " + INJECTED_ACCOUNT + " USD"


def _balances(as_of="2024-10-01", currency="USD"):
    return DashboardBalances(
        as_of_date=as_of,
        net_worth=100.0,
        total_cash=100.0,
        total_investment=0.0,
        total_card_liabilities=0.0,
        total_loan=0.0,
        total_mortgage=0.0,
        accounts=[{
            "account_id": "ACC-CHK-001",
            "account_name": "Checking",
            "firm_name": "Acme Bank",
            "account_type": "bank",
            "balance": 100.0,
            "is_asset": True,
            "currency": currency,
        }],
    )


def _holdings(as_of="2024-10-01"):
    return DashboardHoldings(
        as_of_date=as_of,
        total_value=1000.0,
        holdings=[{
            "account_name": "Taxable Brokerage", "firm_name": "Acme Brokerage",
            "ticker": "VTI", "quantity": 5.0, "price": 200.0, "cost_basis": 750.0,
        }],
    )


def _transactions(tx_date="2024-01-05", tx_id="TX-0001", description="Coffee Shop"):
    return DashboardTransactions(
        start_date="2024-01-01",
        end_date="2024-10-01",
        total_transactions=1,
        money_in=0.0,
        money_out=5.0,
        net_cashflow=-5.0,
        transactions=[{
            "user_transaction_id": tx_id,
            "transaction_date": tx_date,
            "description": description,
            "category": "Dining",
            "amount": 5.0,
            "is_spending": True,
            "account_id": "ACC-CHK-001",
            "account_name": "Checking",
            "firm_name": "Acme Bank",
        }],
    )


def _injected_entries(text):
    """Return the entries the real Beancount loader parses for ``INJECTED_ACCOUNT``."""
    entries, _errors, _options = loader.load_string(text)
    return [e for e in entries if getattr(e, "account", None) == INJECTED_ACCOUNT]


class _FailClosedMixin:
    def assert_fail_closed(self, produce):
        """Require ``produce()`` to raise ValueError rather than emit an injected ledger.

        On the unpatched generator the call returns text; loading it with the real
        Beancount loader then shows the smuggled ``open`` directive, which makes the
        failure message self-explanatory.
        """
        try:
            text = produce()
        except ValueError as exc:
            # The message must not echo a raw line break: MCP resources render
            # errors as ``;; ERROR ...`` comment lines.
            self.assertNotIn("\n", str(exc))
            return exc
        injected = _injected_entries(text)
        self.fail(
            "Expected ValueError; generator emitted a ledger instead "
            f"({len(injected)} injected {INJECTED_ACCOUNT} directive(s) parsed)."
        )
        return None  # pragma: no cover


class TestDateDirectiveInjection(_FailClosedMixin, unittest.TestCase):
    """#139: syntax-bearing dates must be canonical ISO ``YYYY-MM-DD``."""

    def test_transaction_date_injection_fails_closed(self):
        gen = BeancountGenerator()
        txs = _transactions(tx_date="2024-01-05" + INJECTION + "\n2024-01-05")
        self.assert_fail_closed(
            lambda: gen.generate_accounts_bean(_balances(), None, txs)
            + gen.generate_transactions_bean(txs, balances=_balances())
        )

    def test_balances_as_of_date_injection_fails_closed(self):
        gen = BeancountGenerator()
        bal = _balances(as_of="2024-10-01" + INJECTION + "\n2024-10-01")
        self.assert_fail_closed(
            lambda: gen.generate_accounts_bean(bal) + gen.generate_balances_bean(bal)
        )

    def test_holdings_as_of_date_injection_fails_closed_for_prices(self):
        gen = BeancountGenerator()
        hold = _holdings(as_of="2024-10-01" + INJECTION + "\n2024-10-01")
        self.assert_fail_closed(lambda: gen.generate_prices_bean(hold))

    def test_holdings_as_of_date_injection_fails_closed_for_balances(self):
        gen = BeancountGenerator()
        hold = _holdings(as_of="2024-10-01" + INJECTION + "\n2024-10-01")
        self.assert_fail_closed(
            lambda: gen.generate_accounts_bean(None, hold) + gen.generate_balances_bean(None, hold)
        )

    def test_opening_date_injection_fails_closed_in_holdings(self):
        gen = BeancountGenerator()
        self.assert_fail_closed(
            lambda: gen.generate_accounts_bean(None, _holdings())
            + gen.generate_holdings_bean(
                _holdings(), opening_date="2020-01-01" + INJECTION + "\n2020-01-01"
            )
        )

    def test_opening_date_injection_fails_closed_in_single_file_export(self):
        gen = BeancountGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "ledger.bean"
            with self.assertRaises(ValueError):
                gen.export_single_file(
                    target, holdings=_holdings(), append=False,
                    opening_date="2020-01-01" + INJECTION + "\n2020-01-01",
                )
            self.assertFalse(target.exists(), "a rejected export must not write the ledger")

    def test_fallback_date_key_injection_fails_closed(self):
        # ``date`` is the fallback key the opening-lot inference reads when
        # ``transaction_date`` is absent.
        gen = BeancountGenerator()
        txs = _transactions(tx_date=None)
        txs.transactions[0]["date"] = "2024-01-05" + INJECTION
        txs.transactions[0]["symbol"] = "VTI"
        self.assert_fail_closed(lambda: gen.generate_holdings_bean(None, transactions=txs))

    def test_as_of_comment_lines_cannot_forge_window_marker(self):
        # The ``;; Cash & Liability Balances (as of ...)`` and ``;; Investment
        # Commodity Unit Balances (snapshot as of ...)`` comment headers, and the
        # holdings ``;; empower_window_start:`` marker itself, interpolate dates.
        # A line break there could forge a whole-line window marker that a later
        # append trusts, so every such date must fail closed.
        forged = "2024-10-01)\n;; empower_window_start: 1990-01-01\n;; ("
        gen = BeancountGenerator()
        for label, produce in (
            ("cash header", lambda: gen.generate_balances_bean(_balances(as_of=forged))),
            ("commodity header", lambda: gen.generate_balances_bean(None, _holdings(as_of=forged))),
            ("window marker", lambda: gen.generate_holdings_bean(_holdings(), opening_date=forged)),
            ("window marker via as_of", lambda: gen.generate_holdings_bean(_holdings(as_of=forged))),
        ):
            with self.subTest(site=label):
                with self.assertRaises(ValueError):
                    produce()

    def test_non_canonical_dates_rejected(self):
        for bad in (
            "2024-1-5", "20240105", "2024/01/05", "2024-01-05T00:00", "2024-01-05 ",
            "2024-02-30", "", "２０２４-01-05", 20240105, None,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    _validate_iso_date(bad, "test date")

    def test_canonical_dates_accepted_unchanged(self):
        for good in ("2024-01-05", "2024-02-29", "1999-12-31"):
            with self.subTest(good=good):
                self.assertEqual(_validate_iso_date(good, "test date"), good)

    def test_missing_transaction_date_keeps_existing_fallback(self):
        gen = BeancountGenerator()
        out = gen.generate_transactions_bean(_transactions(tx_date=None))
        self.assertIn('2000-01-01 * "Coffee Shop"', out)


class TestAccountTargetInjection(_FailClosedMixin, unittest.TestCase):
    """#142: configured mapping targets must match Beancount's account grammar."""

    def _gen_with(self, **attrs):
        mapper = BeancountMapper()
        for name, value in attrs.items():
            setattr(mapper, name, value)
        return BeancountGenerator(mapper=mapper)

    def test_account_target_injection_fails_closed(self):
        gen = self._gen_with(accounts={"ACC-CHK-001": "Assets:Acme:Checking" + INJECTION})
        self.assert_fail_closed(lambda: gen.generate_accounts_bean(_balances()))

    def test_category_target_injection_fails_closed(self):
        gen = self._gen_with(categories={"Dining": "Expenses:Dining" + INJECTION})
        self.assert_fail_closed(lambda: gen.generate_accounts_bean(_balances()))

    def test_regex_target_injection_fails_closed(self):
        gen = self._gen_with(regex_rules=[{"pattern": "Coffee", "account": "Expenses:Coffee" + INJECTION}])
        self.assert_fail_closed(lambda: gen.generate_accounts_bean(_balances()))

    def test_transaction_override_injection_fails_closed_in_postings(self):
        gen = self._gen_with(transaction_overrides={"TX-0001": "Expenses:Misc" + INJECTION})
        txs = _transactions()
        self.assert_fail_closed(
            lambda: gen.generate_accounts_bean(_balances(), None, txs)
            + gen.generate_transactions_bean(txs, balances=_balances())
        )

    def test_mapping_file_injection_fails_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            path.write_text(
                json.dumps({"categories": {"Dining": "Expenses:Dining" + INJECTION}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                BeancountMapper(mapping_path=path)
            self.assertIn("categories", str(ctx.exception))
            self.assertNotIn("\n", str(ctx.exception))

    def test_invalid_account_names_rejected(self):
        for bad in (
            "Expenses:Dining\n", "Expense:Food", "Expenses", "Expenses:food", "Expenses:Food Dining",
            "Expenses::Food", "Expenses:Food:", " Expenses:Food", "", None, 42,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    _validate_account_name(bad, "test")

    def test_valid_mapping_targets_still_export_and_load(self):
        gen = self._gen_with(
            accounts={"ACC-CHK-001": "Assets:Acme:Checking-1234"},
            categories={"Dining": "Expenses:Food:Dining"},
            regex_rules=[{"pattern": "Coffee", "account": "Expenses:Food:Coffee"}],
            transaction_overrides={"TX-0002": "Expenses:Misc"},
        )
        txs = _transactions()
        bal = _balances()
        text = (
            gen.generate_accounts_bean(bal, None, txs, include_pads=False)
            + gen.generate_balances_bean(bal, transactions=txs)
            + gen.generate_transactions_bean(txs, balances=bal)
        )
        _entries, errors, _opts = loader.load_string(text)
        self.assertEqual([e.message for e in errors], [])
        self.assertIn("2000-01-01 open Assets:Acme:Checking-1234 USD\n", text)
        self.assertIn("2000-01-01 open Expenses:Food:Dining\n", text)
        self.assertIn("2000-01-01 open Expenses:Food:Coffee\n", text)


class TestCurrencyInjection(_FailClosedMixin, unittest.TestCase):
    """#143: account currencies must match Beancount's commodity grammar."""

    def test_currency_injection_fails_closed_in_open_directive(self):
        gen = BeancountGenerator()
        self.assert_fail_closed(lambda: gen.generate_accounts_bean(_balances(currency="USD" + INJECTION)))

    def test_currency_injection_fails_closed_in_balance_assertion(self):
        gen = BeancountGenerator()
        bal = _balances(currency="USD" + INJECTION)
        self.assert_fail_closed(lambda: gen.generate_balances_bean(bal))

    def test_invalid_currencies_rejected(self):
        for bad in ("usd", "USD\n", "US D", "U", "-USD", "USD-", "A" * 25, "", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    _validate_currency(bad, "test")

    def test_valid_currencies_accepted(self):
        for good in ("USD", "EUR", "BRK.B", "VTI_2", "ABC'D", "A1"):
            with self.subTest(good=good):
                self.assertEqual(_validate_currency(good, "test"), good)

    def test_valid_non_usd_currency_is_emitted_unchanged(self):
        gen = BeancountGenerator()
        bal = _balances(currency="EUR")
        text = gen.generate_accounts_bean(bal) + gen.generate_balances_bean(bal)
        self.assertIn("2000-01-01 open Assets:AcmeBank:Checking EUR\n", text)
        self.assertIn("2024-10-01 balance Assets:AcmeBank:Checking 100.00 EUR\n", text)


class TestIncludeFilenameInjection(_FailClosedMixin, unittest.TestCase):
    """#147: include filenames must be serialized as safe Beancount strings."""

    def test_include_quote_and_newline_injection_fails_closed(self):
        gen = BeancountGenerator()
        self.assert_fail_closed(lambda: gen.generate_main_bean(['x.bean"' + INJECTION + "\n;"]))

    def test_quote_rejects_line_breaks_and_nul(self):
        for bad in ("a\nb.bean", "a\rb.bean", "a\x00b.bean"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    _quote_beancount_string(bad, "include filename")

    def test_quote_escapes_quotes_and_backslashes(self):
        self.assertEqual(_quote_beancount_string("extra.bean", "f"), '"extra.bean"')
        self.assertEqual(_quote_beancount_string('we"ird\\x.bean', "f"), '"we\\"ird\\\\x.bean"')

    def _write_extra(self, ledger_dir, name):
        ledger_dir.mkdir(parents=True, exist_ok=True)
        (ledger_dir / name).write_text("2000-01-01 open Assets:Extra:Weird USD\n", encoding="utf-8")

    def _load_main(self, ledger_dir):
        entries, errors, _opts = loader.load_file(str(ledger_dir / "main.bean"))
        return entries, [e.message for e in errors]

    def test_discovered_quoted_filename_is_escaped_and_loads(self):
        gen = BeancountGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger"
            self._write_extra(ledger_dir, 'we"ird\\x.bean')
            gen.export_modular_ledger(ledger_dir, balances=_balances(), append=False)
            entries, errors = self._load_main(ledger_dir)
            self.assertEqual(errors, [])
            self.assertTrue(any(getattr(e, "account", None) == "Assets:Extra:Weird" for e in entries))

    def test_discovered_quoted_filename_is_escaped_on_append(self):
        gen = BeancountGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger"
            gen.export_modular_ledger(ledger_dir, balances=_balances(), append=False)
            self._write_extra(ledger_dir, 'we"ird.bean')
            gen.export_modular_ledger(ledger_dir, balances=_balances(), append=True)
            gen.export_modular_ledger(ledger_dir, balances=_balances(), append=True)
            main_text = (ledger_dir / "main.bean").read_text(encoding="utf-8")
            self.assertEqual(main_text.count('include "we\\"ird.bean"\n'), 1)
            entries, errors = self._load_main(ledger_dir)
            self.assertEqual(errors, [])
            self.assertTrue(any(getattr(e, "account", None) == "Assets:Extra:Weird" for e in entries))

    def test_discovered_newline_filename_fails_closed_without_touching_ledger(self):
        gen = BeancountGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger"
            gen.export_modular_ledger(ledger_dir, balances=_balances(), append=False)
            before = (ledger_dir / "main.bean").read_text(encoding="utf-8")
            self._write_extra(ledger_dir, 'x.bean"' + INJECTION.replace(":", "_") + '\n;.bean')
            with self.assertRaises(ValueError):
                gen.export_modular_ledger(ledger_dir, balances=_balances(), append=True)
            self.assertEqual((ledger_dir / "main.bean").read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
