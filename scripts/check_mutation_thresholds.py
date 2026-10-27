#!/usr/bin/env python3
"""Per-module mutation score checker for the mutmut pilot.

mutmut 3 reports run-wide totals only. This wrapper reads the per-mutant
exit codes mutmut stores in ``mutants/**/*.py.meta`` (``exit_code_by_key``),
aggregates them by source module, prints a per-module mutation score, and —
unless ``--report-only`` is given — exits non-zero when a module falls below
its ``--threshold MODULE=SCORE`` gate.

Score definition (aligned with Stryker's mutation score):
detected / (detected + undetected) * 100, where detected = killed + timeout
and undetected = survived + no tests. A "no tests" mutant is a coverage gap,
so it counts against the module. Errors and undecided statuses (suspicious,
segfault, skipped, not checked, caught by type check, ...) are reported in
the "other" column but excluded from the score.

Exit codes: 0 = all gates met (or report-only), 1 = a gate was breached,
2 = missing or unusable mutation data.

Stdlib only. See docs/mutation-triage.md for baselines and gate values.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Optional

# Mirrors mutmut.stats.status_by_exit_code (mutmut 3.x). Unknown codes are
# "suspicious", matching mutmut's defaultdict behaviour.
STATUS_BY_EXIT_CODE = {
    1: "killed",
    3: "killed",
    0: "survived",
    5: "no tests",
    33: "no tests",
    2: "check was interrupted by user",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught by type check",
    -24: "timeout",
    24: "timeout",
    152: "timeout",
    255: "timeout",
    -11: "segfault",
    -9: "segfault",
}


def status_of(exit_code) -> str:
    if exit_code is None:
        return "not checked"
    return STATUS_BY_EXIT_CODE.get(exit_code, "suspicious")


def module_of(mutant_key: str) -> str:
    """Module name from a mutant key like ``pkg.beancount.x_fn__mutmut_1``."""
    parts = mutant_key.split(".")
    return parts[1] if len(parts) > 1 else parts[0]


def load_statuses(mutants_dir: Path) -> Dict[str, Counter]:
    """Aggregate mutant statuses by module from every ``*.py.meta`` file.

    The module is taken from the meta file name (``beancount.py.meta`` ->
    ``beancount``), not from the mutant key: keys for module-level functions
    (e.g. in ``__init__.py``) have no module component to split on.
    """
    by_module: Dict[str, Counter] = defaultdict(Counter)
    for meta_path in sorted(mutants_dir.rglob("*.py.meta")):
        module = meta_path.name[: -len(".meta")].removesuffix(".py")
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for exit_code in (data.get("exit_code_by_key") or {}).values():
            by_module[module][status_of(exit_code)] += 1
    return dict(by_module)


DETECTED = ("killed", "timeout")
UNDETECTED = ("survived", "no tests")


def compute_score(counts) -> Optional[float]:
    """Mutation score in percent, or None when nothing was decided."""
    detected = sum(counts.get(status, 0) for status in DETECTED)
    undetected = sum(counts.get(status, 0) for status in UNDETECTED)
    decided = detected + undetected
    if decided == 0:
        return None
    return detected / decided * 100.0


def _parse_thresholds(raw) -> Dict[str, float]:
    thresholds: Dict[str, float] = {}
    for item in raw or []:
        if "=" not in item:
            raise ValueError(f"threshold must be MODULE=SCORE, got: {item!r}")
        name, _, value = item.partition("=")
        thresholds[name.strip()] = float(value)
    return thresholds


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutants-dir", default="mutants",
                        help="mutmut output directory (default: mutants)")
    parser.add_argument("--threshold", action="append", default=[],
                        metavar="MODULE=SCORE",
                        help="per-module minimum score; repeatable")
    parser.add_argument("--report-only", action="store_true",
                        help="print scores but never fail on a breach")
    args = parser.parse_args(argv)

    mutants_dir = Path(args.mutants_dir)
    if not mutants_dir.is_dir():
        print(f"error: mutants directory not found: {mutants_dir}", file=sys.stderr)
        return 2

    try:
        thresholds = _parse_thresholds(args.threshold)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    by_module = load_statuses(mutants_dir)
    if not by_module:
        print(f"error: no *.py.meta mutation data under {mutants_dir}", file=sys.stderr)
        return 2

    print(f"{'module':<16} {'score':>7} {'killed':>7} {'survived':>9} "
          f"{'no tests':>9} {'timeout':>8} {'other':>6}  gate")
    breached = []
    missing = []
    for module in sorted(set(by_module) | set(thresholds)):
        counts = by_module.get(module)
        if counts is None:
            missing.append(module)
            print(f"{module:<16} {'n/a':>7} {'-':>7} {'-':>9} {'-':>9} {'-':>8} {'-':>6}  "
                  f"{thresholds[module]:.1f}  NO DATA")
            continue
        score = compute_score(counts)
        other = sum(
            v for k, v in counts.items() if k not in DETECTED + UNDETECTED
        )
        gate = thresholds.get(module)
        gate_text = f"{gate:.1f}" if gate is not None else "-"
        score_text = f"{score:.1f}" if score is not None else "n/a"
        flag = ""
        if gate is not None:
            if score is None or score < gate:
                breached.append(module)
                flag = "  BREACH"
        print(f"{module:<16} {score_text:>7} {counts.get('killed', 0):>7} "
              f"{counts.get('survived', 0):>9} {counts.get('no tests', 0):>9} "
              f"{counts.get('timeout', 0):>8} {other:>6}  {gate_text}{flag}")

    if missing:
        print(f"error: no mutation data for gated module(s): {', '.join(missing)}",
              file=sys.stderr)
        return 2
    if breached and not args.report_only:
        print(f"error: mutation score below gate: {', '.join(breached)}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
