"""
test_sanitizers.py — Unit tests for text sanitizers.
"""

import unittest
from empower_personal_dashboard.sanitizers import (
    clean_api_text,
    csv_safe_cell,
    terminal_safe_text,
)


class TestSanitizers(unittest.TestCase):
    def test_clean_api_text_removes_replacement_char(self):
        dirty = "Standard� Trust� Index Fund"
        cleaned = clean_api_text(dirty)
        self.assertEqual(cleaned, "Standard Trust Index Fund")
        self.assertNotIn("�", cleaned)

    def test_clean_api_text_handles_none_and_empty(self):
        self.assertEqual(clean_api_text(None), "")
        self.assertEqual(clean_api_text(""), "")

    def test_clean_api_text_collapses_whitespace(self):
        messy = "Global    Market    Bond"
        self.assertEqual(clean_api_text(messy), "Global Market Bond")


class TestTerminalSafeText(unittest.TestCase):
    """terminal_safe_text strips terminal control sequences from display text (#131)."""

    def test_none_and_empty(self):
        self.assertEqual(terminal_safe_text(None), "")
        self.assertEqual(terminal_safe_text(""), "")

    def test_plain_text_unchanged(self):
        self.assertEqual(terminal_safe_text("Synthetic Bank — Checking ®"), "Synthetic Bank — Checking ®")

    def test_non_string_values_are_stringified(self):
        self.assertEqual(terminal_safe_text(42), "42")
        self.assertEqual(terminal_safe_text(-12.5), "-12.5")

    def test_strips_csi_sequences(self):
        self.assertEqual(terminal_safe_text("Check\x1b[2J\x1b[1;31mRED\x1b[0m"), "CheckRED")
        # CSI with private parameter bytes and an intermediate byte.
        self.assertEqual(terminal_safe_text("a\x1b[?25lb\x1b[1 qc"), "abc")

    def test_strips_osc_terminated_by_bel(self):
        # OSC 52 clipboard write: the whole payload must be removed, not just ESC.
        self.assertEqual(terminal_safe_text("Bank\x1b]52;c;ZXZpbA==\x07 Name"), "Bank Name")

    def test_strips_osc_terminated_by_st(self):
        self.assertEqual(
            terminal_safe_text("A\x1b]8;;https://example.invalid\x1b\\link\x1b]8;;\x1b\\B"),
            "AlinkB",
        )

    def test_strips_osc_terminated_by_c1_st(self):
        self.assertEqual(terminal_safe_text("A\x1b]0;title\x9cB"), "AB")

    def test_strips_unterminated_osc_to_end(self):
        self.assertEqual(terminal_safe_text("Bank\x1b]52;c;ZXZpbA=="), "Bank")

    def test_strips_dcs_sos_pm_apc_strings(self):
        for intro in ("P", "X", "^", "_"):
            with self.subTest(intro=intro):
                self.assertEqual(terminal_safe_text(f"A\x1b{intro}payload\x1b\\B"), "AB")

    def test_strips_two_byte_and_nf_escapes(self):
        # RIS (ESC c), DECSC (ESC 7), charset designation (ESC ( 0).
        self.assertEqual(terminal_safe_text("A\x1bcB\x1b7C\x1b(0D"), "ABCD")

    def test_strips_lone_trailing_escape(self):
        self.assertEqual(terminal_safe_text("A\x1b"), "A")

    def test_strips_eight_bit_c1_controls(self):
        # 8-bit CSI / OSC introducers are removed; leftover parameter bytes are inert text.
        self.assertEqual(terminal_safe_text("A\x9b31mB\x9d0;t\x07C\x85D"), "A31mB0;tCD")

    def test_strips_c0_controls_and_del(self):
        self.assertEqual(terminal_safe_text("A\x00B\x07C\x08D\x7fE\x1fF"), "ABCDEF")

    def test_whitespace_controls_become_single_space(self):
        self.assertEqual(terminal_safe_text("Line1\r\nLine2\tTab\x0bV\x0cF"), "Line1 Line2 Tab V F")

    def test_does_not_alter_replacement_char_or_spacing(self):
        # Unlike clean_api_text, display escaping does not touch U+FFFD or spacing.
        self.assertEqual(terminal_safe_text("  A�  B  "), "  A�  B  ")


class TestCsvSafeCell(unittest.TestCase):
    """csv_safe_cell neutralises formula-leading string cells (#135)."""

    def test_formula_prefixes_are_quoted(self):
        for value in ("=1+1", "+1", "-1+1", "@SUM(A1)", "\t=1", "\r=1"):
            with self.subTest(value=value):
                self.assertEqual(csv_safe_cell(value), "'" + value)

    def test_leading_line_feed_formula_is_quoted(self):
        value = "\n=HYPERLINK(\"http://example.invalid\")"
        self.assertEqual(csv_safe_cell(value), "'" + value)

    def test_tab_or_cr_prefix_is_quoted_even_without_formula(self):
        for value in ("\tSynthetic", "\rSynthetic", "\nSynthetic"):
            with self.subTest(value=value):
                self.assertEqual(csv_safe_cell(value), "'" + value)

    def test_whitespace_padded_formula_is_quoted(self):
        # Importers that trim leading whitespace (and users who re-save after
        # trimming) would otherwise see a live formula.
        for value in (" =1+1", "   @SUM(A1)", " +1", " \t-1+1", "\r\n=HYPERLINK(\"x\")"):
            with self.subTest(value=value):
                self.assertEqual(csv_safe_cell(value), "'" + value)

    def test_safe_strings_unchanged(self):
        for value in ("", "   ", "Synthetic Grocery", "1=1", " a=1", "'already"):
            with self.subTest(value=value):
                self.assertEqual(csv_safe_cell(value), value)

    def test_numbers_and_other_types_unchanged(self):
        for value in (-12.5, 0, 3, True, None):
            with self.subTest(value=value):
                self.assertIs(csv_safe_cell(value), value)


if __name__ == "__main__":
    unittest.main()
