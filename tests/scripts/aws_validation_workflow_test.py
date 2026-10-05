"""Parsed workflow contract, including queue fields beyond actionlint's schema."""

import unittest
from pathlib import Path

import yaml


class WorkflowContract(unittest.TestCase):
    def test_validation_is_one_65_minute_locked_operator_job_after_pre_oidc_preflight(
        self,
    ):
        workflow = yaml.safe_load(
            (
                Path(__file__).resolve().parents[2]
                / ".github/workflows/aws-validation.yml"
            ).read_text()
        )
        self.assertEqual(
            set(workflow.get("on", workflow.get(True))), {"workflow_dispatch"}
        )
        jobs = workflow["jobs"]
        self.assertEqual(
            [jobs[name]["name"] for name in ["request", "build", "publish"]],
            ["Candidate request", "Build candidate", "Publish candidate"],
        )
        self.assertNotIn("concurrency", jobs["publish"])
        self.assertIn("validate", jobs)
        job = jobs["validate"]
        self.assertEqual(job["timeout-minutes"], 65)
        self.assertEqual(
            job["concurrency"],
            {
                "group": "aws-testing-environment",
                "queue": "max",
                "cancel-in-progress": False,
            },
        )
        self.assertEqual(job["environment"], "aws-testing")
        steps = job["steps"]
        oidc = next(
            i
            for i, s in enumerate(steps)
            if "configure-aws-credentials@" in s.get("uses", "")
        )
        self.assertTrue(
            any("validate-preflight" in s.get("run", "") for s in steps[:oidc])
        )
        self.assertEqual(steps[oidc]["with"]["role-duration-seconds"], 7200)
        artifacts = [s for s in steps if "upload-artifact@" in s.get("uses", "")]
        self.assertTrue(artifacts)
        retained = next(
            s for s in steps if s.get("name") == "Retain sanitized runtime evidence"
        )
        finalize = next(s for s in steps if "finalize-success" in s.get("run", ""))
        self.assertEqual(retained["if"], "always()")
        self.assertGreaterEqual(retained["with"]["retention-days"], 14)
        self.assertEqual(finalize["if"], "success()")
        self.assertLess(steps.index(retained), steps.index(finalize))
        self.assertTrue(
            all(
                step["if"] == "failure() || cancelled()"
                for step in steps
                if "finalize-failure" in step.get("run", "")
            )
        )
        self.assertEqual(
            jobs["outcome"]["needs"], ["request", "build", "publish", "validate"]
        )
        self.assertNotIn("id-token", jobs["build"]["permissions"])
