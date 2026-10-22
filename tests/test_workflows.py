import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PR_AUTO_REVIEW_WORKFLOW = ROOT / ".github" / "workflows" / "pr-auto-review.yml"


class TestPrAutoReviewWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PR_AUTO_REVIEW_WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)
        cls.job = cls.workflow["jobs"]["pr-auto-review"]

    EXPECTED_GUARD = (
        "github.event_name != 'pull_request' || "
        "(github.event.pull_request.head.repo.fork == false && "
        "github.actor != 'dependabot[bot]')"
    )

    def test_guard_skips_fork_pull_requests(self):
        """Verify the job guard expression matches the expected format."""
        guard = " ".join(self.job["if"].split())
        self.assertEqual(guard, self.EXPECTED_GUARD)

    def test_guard_skips_dependabot_pull_requests(self):
        """Verify the job guard excludes Dependabot pull requests due to missing Actions secrets (#108)."""
        # Dependabot-triggered pull_request runs receive no Actions secrets, so the
        # reusable's checkout fails on an empty GH_PAT_WORKFLOWS token (#108).
        guard = " ".join(self.job["if"].split())
        self.assertEqual(guard, self.EXPECTED_GUARD)

    def test_caller_stub_invariants(self):
        """Verify the caller stub configuration, reusable reference, permissions, and triggers."""
        self.assertEqual(
            self.job["uses"],
            "combsmith/.github/.github/workflows/pr-auto-review-reusable.yml@pr-auto-review/v1-stable",
        )
        self.assertNotIn("with", self.job)
        self.assertEqual(
            self.job["permissions"],
            {"pull-requests": "read", "checks": "read", "actions": "read"},
        )
        self.assertEqual(self.workflow["permissions"], {})

        # PyYAML parses the bare `on:` key as boolean True.
        triggers = self.workflow[True]
        self.assertEqual(triggers["workflow_run"], {"workflows": ["CI"], "types": ["completed"]})
        self.assertEqual(triggers["check_suite"], {"types": ["completed"]})
        self.assertEqual(triggers["pull_request_review"], {"types": ["submitted", "dismissed"]})
        self.assertEqual(
            triggers["pull_request"],
            {"types": ["opened", "reopened", "synchronize", "ready_for_review"]},
        )


if __name__ == "__main__":
    unittest.main()
