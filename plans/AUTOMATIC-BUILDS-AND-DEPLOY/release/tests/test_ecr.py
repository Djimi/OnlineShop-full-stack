"""Unit tests for ECR release tag promotion rules (subphase 3.3)."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import components as rc
from release_contract import ecr

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "ecr")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def codes(decision):
    return [issue["code"] for issue in decision.issues]


class TagFamilyTests(unittest.TestCase):
    def test_mutable_convenience_tags(self):
        self.assertTrue(rc.is_mutable_convenience_tag("main-latest"))
        self.assertTrue(rc.is_mutable_convenience_tag("branch-feature-x"))
        self.assertFalse(
            rc.is_mutable_convenience_tag("sha-a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4")
        )
        self.assertFalse(rc.is_mutable_convenience_tag("release-1.2.1"))
        self.assertFalse(rc.is_mutable_convenience_tag("latest"))

    def test_immutable_tags(self):
        self.assertTrue(rc.is_immutable_tag("sha-a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"))
        self.assertTrue(rc.is_immutable_tag("release-1.2.1"))
        self.assertFalse(rc.is_immutable_tag("main-latest"))
        self.assertFalse(rc.is_immutable_tag("branch-x"))

    def test_latest_absent(self):
        self.assertTrue(rc.LATEST_ABSENT)
        self.assertEqual(rc.LATEST_TAG, "latest")

    def test_repo_arns(self):
        arns = rc.ecr_repository_arns("eu-north-1", "799111666795")
        self.assertEqual(
            arns,
            [
                "arn:aws:ecr:eu-north-1:799111666795:repository/onlineshop-auth",
                "arn:aws:ecr:eu-north-1:799111666795:repository/onlineshop-items",
                "arn:aws:ecr:eu-north-1:799111666795:repository/onlineshop-api-gateway",
            ],
        )


class PromoteDecisionTests(unittest.TestCase):
    def test_mint_when_release_absent(self):
        decision = ecr.promote_release_decision(
            fixture("existing-mint.json"), fixture("expected-canonical.json")
        )
        self.assertEqual(decision.action, "mint")
        self.assertEqual(decision.issues, [])

    def test_reuse_when_release_matches(self):
        decision = ecr.promote_release_decision(
            fixture("existing-resume.json"), fixture("expected-canonical.json")
        )
        self.assertEqual(decision.action, "reuse")
        self.assertEqual(decision.issues, [])

    def test_conflict_when_release_different(self):
        decision = ecr.promote_release_decision(
            fixture("existing-conflict.json"), fixture("expected-canonical.json")
        )
        self.assertIn("RELEASE_TAG_CONFLICT", codes(decision))
        self.assertEqual(decision.action, "mint")

    def test_candidate_missing_fails_closed(self):
        decision = ecr.promote_release_decision(
            fixture("existing-candidate-missing.json"), fixture("expected-canonical.json")
        )
        self.assertIn("CANDIDATE_TAG_MISSING", codes(decision))

    def test_candidate_mismatch_fails_closed(self):
        decision = ecr.promote_release_decision(
            fixture("existing-candidate-mismatch.json"), fixture("expected-canonical.json")
        )
        self.assertIn("CANDIDATE_DIGEST_MISMATCH", codes(decision))

    def test_invalid_version_fails_closed(self):
        expected = fixture("expected-canonical.json")
        expected["version"] = "1.2.1-beta"
        decision = ecr.promote_release_decision(fixture("existing-mint.json"), expected)
        self.assertIn("INVALID_VERSION", codes(decision))

    def test_wrong_candidate_tag_fails_closed(self):
        expected = fixture("expected-canonical.json")
        expected["candidateTag"] = "sha-deadbeef"
        decision = ecr.promote_release_decision(fixture("existing-mint.json"), expected)
        self.assertIn("CANDIDATE_TAG_MISMATCH", codes(decision))

    def test_missing_existing_fails_closed(self):
        decision = ecr.promote_release_decision(None, fixture("expected-canonical.json"))
        self.assertIn("MISSING_EXISTING", codes(decision))

    def test_verify_passes_when_both_match(self):
        decision = ecr.verify_release_digest(
            fixture("existing-resume.json"), fixture("expected-canonical.json")
        )
        self.assertEqual(decision.issues, [])

    def test_verify_fails_on_release_mismatch(self):
        decision = ecr.verify_release_digest(
            fixture("existing-conflict.json"), fixture("expected-canonical.json")
        )
        self.assertIn("RELEASE_DIGEST_MISMATCH", codes(decision))

    def test_verify_fails_on_candidate_mismatch(self):
        decision = ecr.verify_release_digest(
            fixture("existing-candidate-mismatch.json"), fixture("expected-canonical.json")
        )
        self.assertIn("CANDIDATE_DIGEST_MISMATCH", codes(decision))


class EcrCliTests(unittest.TestCase):
    def _cli(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(RELEASE_ROOT, "src")
        return subprocess.run(
            [sys.executable, "-m", "release_contract.ecr", *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_decide_mint_exit_zero(self):
        result = self._cli(
            "decide",
            "--existing",
            os.path.join(FIXTURES, "existing-mint.json"),
            "--expected",
            os.path.join(FIXTURES, "expected-canonical.json"),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn('"action":"mint"', result.stdout)

    def test_decide_conflict_exit_one(self):
        result = self._cli(
            "decide",
            "--existing",
            os.path.join(FIXTURES, "existing-conflict.json"),
            "--expected",
            os.path.join(FIXTURES, "expected-canonical.json"),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("RELEASE_TAG_CONFLICT", result.stdout)

    def test_verify_reuse_exit_zero(self):
        result = self._cli(
            "verify",
            "--existing",
            os.path.join(FIXTURES, "existing-resume.json"),
            "--expected",
            os.path.join(FIXTURES, "expected-canonical.json"),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn('"valid":true', result.stdout)


if __name__ == "__main__":
    unittest.main()
