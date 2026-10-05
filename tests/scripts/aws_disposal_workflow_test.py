"""Manual disposal workflow contract at the parsed YAML boundary."""

import unittest
from pathlib import Path

import yaml


class DisposalWorkflowContract(unittest.TestCase):
    def test_disposal_is_guarded_before_oidc_in_the_shared_non_cancelling_session(self):
        path = Path(__file__).resolve().parents[2] / ".github/workflows/aws-dispose.yml"
        self.assertTrue(path.is_file(), "manual disposal workflow is missing")
        workflow = yaml.safe_load(path.read_text())
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})
        inputs = triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"generation", "confirmation"})
        self.assertTrue(inputs["generation"]["required"])
        self.assertTrue(inputs["confirmation"]["required"])
        jobs = workflow["jobs"]
        self.assertEqual(set(jobs), {"dispose"})
        job = jobs["dispose"]
        self.assertEqual(job["timeout-minutes"], 30)
        self.assertEqual(job["environment"], "aws-testing")
        self.assertEqual(
            job["concurrency"],
            {
                "group": "aws-testing-environment",
                "queue": "max",
                "cancel-in-progress": False,
            },
        )
        steps = job["steps"]
        oidc = next(
            i
            for i, step in enumerate(steps)
            if "configure-aws-credentials@" in step.get("uses", "")
        )
        self.assertTrue(
            any("dispose-preflight" in step.get("run", "") for step in steps[:oidc])
        )
        self.assertTrue(
            any("dispose" in step.get("run", "") for step in steps[oidc + 1 :])
        )
        self.assertTrue(
            any("upload-artifact@" in step.get("uses", "") for step in steps)
        )
        self.assertNotIn("id-token", jobs.get("build", {}).get("permissions", {}))
