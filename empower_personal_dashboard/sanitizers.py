"""
sanitizers.py — Text sanitization utilities for Empower Personal Dashboard API.
"""

import re
from typing import Any


def clean_api_text(val: Any) -> str:
    """
    Sanitize API text fields to remove mojibake artifacts such as U+FFFD
    and collapse irregular whitespace.

    Upstream broker and aggregator feeds occasionally inject Unicode replacement
    characters (U+FFFD) where trademark or copyright symbols originally appeared.
    """
    if val is None:
        return ""
    s = str(val)
    # Remove U+FFFD replacement characters
    s = s.replace("\ufffd", "")
    # Collapse multiple consecutive spaces
    s = re.sub(r" {2,}", " ", s)
    return s.strip()


# ECMA-48 escape sequences, matched whole so their parameters and payloads are
# removed too (an OSC 52 clipboard write leaves no residue):
#   CSI:  ESC [ <params 0x30-0x3F> <intermediates 0x20-0x2F> <final 0x40-0x7E>
#   OSC/DCS/SOS/PM/APC:  ESC ] / P / X / ^ / _ <body> <BEL | ESC \ | 0x9C>; an
#        unterminated string is removed up to the end of the value.
#   Other ESC sequences: ESC <intermediates> <final>, or a lone trailing ESC.
_TERMINAL_ESCAPE_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b[\]PX^_][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)?"
    r"|\x1b[ -/]*[0-~]?"
)
_TERMINAL_WHITESPACE_RE = re.compile(r"[\t\n\v\f\r]+")
# Remaining C0 controls, DEL and C1 controls (including 8-bit CSI/OSC introducers).
_TERMINAL_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def terminal_safe_text(val: Any) -> str:
    """
    Make an untrusted value safe to write into terminal-rendered output.

    Removes ANSI/ECMA-48 escape sequences (CSI, OSC, DCS, SOS, PM, APC and
    two-byte ESC sequences) together with their payloads, turns runs of tab,
    newline, vertical tab, form feed and carriage return into a single space so
    a value stays on its table row, and drops every other C0 control, DEL and
    C1 control. Other text, including spacing and U+FFFD, is left unchanged.

    Apply this at render time to table and markdown output only. Stored data and
    JSON output keep the original value.
    """
    if val is None:
        return ""
    s = _TERMINAL_ESCAPE_RE.sub("", str(val))
    s = _TERMINAL_WHITESPACE_RE.sub(" ", s)
    return _TERMINAL_CONTROL_RE.sub("", s)


# Leading characters that make spreadsheet applications evaluate a CSV cell as a
# formula (OWASP "CSV Injection" guidance).
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def csv_safe_cell(val: Any) -> Any:
    """
    Neutralise a CSV cell value against formula (CSV) injection.

    A string that starts with ``=``, ``+``, ``-``, ``@``, tab, carriage return or
    line feed is prefixed with a single quote so spreadsheets treat it as literal text.
    Non-string values (numbers, booleans, None) are returned unchanged, so
    numeric columns such as a negative amount stay numeric.
    """
    if isinstance(val, str) and val.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + val
    return val
