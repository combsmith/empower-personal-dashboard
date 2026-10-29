"""
test_mutation_thresholds.py — Unit tests for scripts/check_mutation_thresholds.py.

The wrapper reads mutmut 3 ``*.py.meta`` files under ``mutants/`` and reports a
per-module mutation score. Fixtures here are synthetic meta files using the
real mutmut exit-code mapping (1=killed, 0=survived, 5=no tests, 36=timeout,
None=not checked); no real mutmut run is needed.
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_mutation_thresholds.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_mutation_thresholds", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_meta(root: Path, source_rel: str, exit_code_by_key: dict) -> None:
    meta = Path(root) / source_rel
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"exit_code_by_key": exit_code_by_key}), encoding="utf-8")


class TestCheckMutationThresholds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_module_of_extracts_second_component(self):
        key = "empower_personal_dashboard.beancount.x_safe_float__mutmut_1"
        self.assertEqual(self.mod.module_of(key), "beancount")
        key2 = "empower_personal_dashboard.models.x_to_dict__mutmut_3"
        self.assertEqual(self.mod.module_of(key2), "models")

    def test_score_counts_no_tests_as_undetected(self):
        # A mutant no test covers is a coverage gap, not an undecided result.
        counts = {"killed": 8, "survived": 2, "no tests": 10}
        self.assertEqual(self.mod.compute_score(counts), 40.0)

    def test_score_counts_timeout_as_detected(self):
        # Matches Stryker: a mutant that hangs the suite was detected.
        counts = {"killed": 6, "survived": 2, "timeout": 2}
        self.assertEqual(self.mod.compute_score(counts), 80.0)

    def test_score_excludes_errors_and_unchecked(self):
        counts = {
            "killed": 8,
            "survived": 2,
            "suspicious": 4,
            "segfault": 1,
            "skipped": 3,
            "not checked": 50,
            "caught by type check": 2,
        }
        self.assertEqual(self.mod.compute_score(counts), 80.0)

    def test_score_none_when_nothing_checked(self):
        self.assertIsNone(self.mod.compute_score({"killed": 0, "survived": 0}))
        self.assertIsNone(self.mod.compute_score({}))
        self.assertIsNone(self.mod.compute_score({"not checked": 12}))

    def test_main_fails_when_untested_mutants_drag_score_below_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(
                tmp,
                "empower_personal_dashboard/models.py.meta",
                {
                    "empower_personal_dashboard.models.x_a__mutmut_1": 1,
                    "empower_personal_dashboard.models.x_b__mutmut_1": 5,
                    "empower_personal_dashboard.models.x_b__mutmut_2": 33,
                },
            )
            code = self.mod.main(["--mutants-dir", tmp, "--threshold", "models=50"])
        self.assertEqual(code, 1)

    def test_load_statuses_aggregates_by_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(
                tmp,
                "empower_personal_dashboard/models.py.meta",
                {
                    "empower_personal_dashboard.models.x_a__mutmut_1": 1,
                    "empower_personal_dashboard.models.x_a__mutmut_2": 0,
                    "empower_personal_dashboard.models.x_b__mutmut_1": 5,
                    "empower_personal_dashboard.models.x_c__mutmut_1": None,
                },
            )
            _write_meta(
                tmp,
                "empower_personal_dashboard/sanitizers.py.meta",
                {"empower_personal_dashboard.sanitizers.x_c__mutmut_1": 36},
            )
            by_module = self.mod.load_statuses(Path(tmp))
        models = by_module["empower_personal_dashboard/models"]
        self.assertEqual(models["killed"], 1)
        self.assertEqual(models["survived"], 1)
        self.assertEqual(models["no tests"], 1)
        self.assertEqual(models["not checked"], 1)
        self.assertEqual(by_module["empower_personal_dashboard/sanitizers"]["timeout"], 1)

    def test_load_statuses_uses_meta_filename_for_init_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(
                tmp,
                "empower_personal_dashboard/__init__.py.meta",
                {"empower_personal_dashboard.x_create_server__mutmut_1": 1},
            )
            by_module = self.mod.load_statuses(Path(tmp))
        self.assertEqual(by_module["empower_personal_dashboard/__init__"]["killed"], 1)
        self.assertNotIn("x_create_server__mutmut_1", by_module)

    def test_main_passes_when_thresholds_met(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(
                tmp,
                "empower_personal_dashboard/models.py.meta",
                {
                    "empower_personal_dashboard.models.x_a__mutmut_1": 1,
                    "empower_personal_dashboard.models.x_a__mutmut_2": 1,
                    "empower_personal_dashboard.models.x_a__mutmut_3": 0,
                },
            )
            code = self.mod.main(["--mutants-dir", tmp, "--threshold", "models=60"])
        self.assertEqual(code, 0)

    def test_main_fails_when_threshold_breached(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(
                tmp,
                "empower_personal_dashboard/models.py.meta",
                {
                    "empower_personal_dashboard.models.x_a__mutmut_1": 1,
                    "empower_personal_dashboard.models.x_a__mutmut_2": 0,
                },
            )
            code = self.mod.main(["--mutants-dir", tmp, "--threshold", "models=80"])
        self.assertEqual(code, 1)

    def test_main_report_only_never_fails_on_breach(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(
                tmp,
                "empower_personal_dashboard/models.py.meta",
                {"empower_personal_dashboard.models.x_a__mutmut_1": 0},
            )
            code = self.mod.main(
                ["--mutants-dir", tmp, "--threshold", "models=80", "--report-only"]
            )
        self.assertEqual(code, 0)

    def test_main_errors_on_missing_mutants_dir(self):
        code = self.mod.main(["--mutants-dir", "/nonexistent-mutants-dir-xyz"])
        self.assertEqual(code, 2)

    def test_main_errors_when_threshold_module_has_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(
                tmp,
                "empower_personal_dashboard/models.py.meta",
                {"empower_personal_dashboard.models.x_a__mutmut_1": 1},
            )
            code = self.mod.main(["--mutants-dir", tmp, "--threshold", "beancount=80"])
        self.assertEqual(code, 2)

    def test_load_statuses_keys_same_basename_by_path(self):
        # Same file name in different subpackages must not merge (issue #125).
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(tmp, "pkg/a/models.py.meta", {"pkg.a.models.x_f__mutmut_1": 1})
            _write_meta(
                tmp,
                "pkg/b/models.py.meta",
                {"pkg.b.models.x_f__mutmut_1": 0, "pkg.b.models.x_f__mutmut_2": 0},
            )
            by_module = self.mod.load_statuses(Path(tmp))
        self.assertEqual(set(by_module), {"pkg/a/models", "pkg/b/models"})
        self.assertEqual(self.mod.compute_score(by_module["pkg/a/models"]), 100.0)
        self.assertEqual(self.mod.compute_score(by_module["pkg/b/models"]), 0.0)

    def test_main_gates_same_basename_modules_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(tmp, "pkg/a/models.py.meta", {"pkg.a.models.x_f__mutmut_1": 1})
            _write_meta(tmp, "pkg/b/models.py.meta", {"pkg.b.models.x_f__mutmut_1": 0})
            passing = self.mod.main(
                ["--mutants-dir", tmp, "--threshold", "pkg/a/models=90"]
            )
            failing = self.mod.main(
                ["--mutants-dir", tmp, "--threshold", "pkg/b/models=90"]
            )
        self.assertEqual(passing, 0)
        self.assertEqual(failing, 1)

    def test_main_accepts_full_path_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(
                tmp,
                "empower_personal_dashboard/models.py.meta",
                {"empower_personal_dashboard.models.x_a__mutmut_1": 0},
            )
            code = self.mod.main(
                ["--mutants-dir", tmp, "--threshold", "empower_personal_dashboard/models=80"]
            )
        self.assertEqual(code, 1)

    def test_main_short_alias_resolves_when_unambiguous(self):
        # models=80 must gate empower_personal_dashboard/models, not go missing.
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(
                tmp,
                "empower_personal_dashboard/models.py.meta",
                {"empower_personal_dashboard.models.x_a__mutmut_1": 0},
            )
            _write_meta(
                tmp,
                "empower_personal_dashboard/sanitizers.py.meta",
                {"empower_personal_dashboard.sanitizers.x_a__mutmut_1": 1},
            )
            breach = self.mod.main(["--mutants-dir", tmp, "--threshold", "models=80"])
            ok = self.mod.main(["--mutants-dir", tmp, "--threshold", "sanitizers=80"])
        self.assertEqual(breach, 1)
        self.assertEqual(ok, 0)

    def test_main_errors_on_ambiguous_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(tmp, "pkg/a/models.py.meta", {"pkg.a.models.x_f__mutmut_1": 1})
            _write_meta(tmp, "pkg/b/models.py.meta", {"pkg.b.models.x_f__mutmut_1": 1})
            code = self.mod.main(["--mutants-dir", tmp, "--threshold", "models=50"])
        self.assertEqual(code, 2)

    def test_resolve_thresholds_exact_key_beats_alias(self):
        # A top-level models.py is keyed "models"; it wins over pkg/models.
        resolved = self.mod.resolve_thresholds(
            {"models": 50.0}, ["models", "pkg/models"]
        )
        self.assertEqual(resolved, {"models": 50.0})

    def test_resolve_thresholds_ambiguous_alias_names_candidates(self):
        with self.assertRaises(ValueError) as ctx:
            self.mod.resolve_thresholds({"models": 50.0}, ["pkg/a/models", "pkg/b/models"])
        self.assertIn("pkg/a/models", str(ctx.exception))
        self.assertIn("pkg/b/models", str(ctx.exception))

    def test_resolve_thresholds_rejects_duplicate_resolved_key(self):
        with self.assertRaises(ValueError) as ctx:
            self.mod.resolve_thresholds(
                {"models": 95.0, "pkg/models": 50.0}, ["pkg/models"]
            )
        self.assertIn("duplicate", str(ctx.exception))

    def test_resolve_thresholds_keeps_unknown_name_for_no_data_report(self):
        resolved = self.mod.resolve_thresholds({"beancount": 80.0}, ["pkg/models"])
        self.assertEqual(resolved, {"beancount": 80.0})

    def test_parse_thresholds_rejects_repeated_name(self):
        # A repeated name used to overwrite the first gate silently, so the
        # duplicate check in resolve_thresholds never saw it.
        with self.assertRaises(ValueError) as ctx:
            self.mod._parse_thresholds(["models=95", "models=50"])
        self.assertIn("duplicate", str(ctx.exception))

    def test_main_errors_on_repeated_threshold_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(tmp, "pkg/models.py.meta", {"pkg.models.x_f__mutmut_1": 1})
            code = self.mod.main(["--mutants-dir", tmp,
                                  "--threshold", "models=95", "--threshold", "models=50"])
        self.assertEqual(code, 2)

    def test_help_says_partial_paths_are_not_accepted(self):
        parser_help = io.StringIO()
        with contextlib.redirect_stdout(parser_help), self.assertRaises(SystemExit):
            self.mod.main(["--help"])
        self.assertIn("partial paths", parser_help.getvalue())

    def test_load_statuses_keys_mirror_mutmut_source_layout(self):
        # mutmut writes mutants/<source path>.meta, so the key for
        # empower_personal_dashboard/models.py is "empower_personal_dashboard/models".
        # The CI gates use these full keys: a layout change (e.g. a src/ prefix)
        # must surface as NO DATA (exit 2), never as a silently ungated module.
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(tmp, "src/empower_personal_dashboard/models.py.meta",
                        {"empower_personal_dashboard.models.x_f__mutmut_1": 1})
            code = self.mod.main(["--mutants-dir", tmp, "--threshold",
                                  "empower_personal_dashboard/models=95"])
        self.assertEqual(code, 2)

    def test_mutation_workflow_gates_use_full_path_keys(self):
        # Short names resolve by file name, so a new same-named file could move
        # a gate silently; the required check pins every pilot module by its key.
        workflow = (Path(__file__).resolve().parent.parent
                    / ".github" / "workflows" / "mutation.yml").read_text(encoding="utf-8")
        for key, gate in (("models", 95), ("sanitizers", 90), ("beancount", 91)):
            self.assertIn(f"--threshold empower_personal_dashboard/{key}={gate}", workflow)
        self.assertNotRegex(workflow, r"--threshold (models|sanitizers|beancount)=")


if __name__ == "__main__":
    unittest.main()
