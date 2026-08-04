"""Unit tests for release identity collision/resume rules (subphase 3.3)."""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import releaseid

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASEID_FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "releaseid")
VALID_FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "valid")


def fixture(name, base=RELEASEID_FIXTURES):
    with open(os.path.join(base, name), encoding="utf-8") as handle:
        return json.load(handle)


def official_manifest():
    return fixture("official-v1.2.1.json", VALID_FIXTURES)


def codes(decision):
    return [issue["code"] for issue in decision.issues]


class ReleaseIdentityTests(unittest.TestCase):
    def test_clean_proceeds(self):
        decision = releaseid.release_identity_issues(
            official_manifest(), fixture("observed-clean.json")
        )
        self.assertEqual(decision.action, "proceed")
        self.assertEqual(decision.issues, [])

    def test_resume_from_ecr_release_tags(self):
        decision = releaseid.release_identity_issues(
            official_manifest(), fixture("observed-resume-ecr.json")
        )
        self.assertEqual(decision.action, "resume")
        self.assertEqual(decision.issues, [])

    def test_resume_from_git_tag(self):
        decision = releaseid.release_identity_issues(
            official_manifest(), fixture("observed-resume-git.json")
        )
        self.assertEqual(decision.action, "resume")
        self.assertEqual(decision.issues, [])

    def test_resume_from_frontend_marker(self):
        decision = releaseid.release_identity_issues(
            official_manifest(), fixture("observed-resume-frontend.json")
        )
        self.assertEqual(decision.action, "resume")
        self.assertEqual(decision.issues, [])

    def test_git_tag_conflict_fails_closed(self):
        decision = releaseid.release_identity_issues(
            official_manifest(), fixture("observed-conflict-git.json")
        )
        self.assertIn("GIT_TAG_CONFLICT", codes(decision))

    def test_ecr_release_tag_conflict_fails_closed(self):
        decision = releaseid.release_identity_issues(
            official_manifest(), fixture("observed-conflict-ecr.json")
        )
        self.assertIn("ECR_RELEASE_TAG_CONFLICT", codes(decision))

    def test_frontend_marker_conflict_fails_closed(self):
        decision = releaseid.release_identity_issues(
            official_manifest(), fixture("observed-conflict-frontend.json")
        )
        self.assertIn("FRONTEND_PREFIX_CONFLICT", codes(decision))

    def test_invalid_manifest_fails_closed(self):
        manifest = official_manifest()
        manifest["release"]["version"] = "1.2"
        decision = releaseid.release_identity_issues(manifest, fixture("observed-clean.json"))
        self.assertIn("MANIFEST_INVALID", codes(decision))

    def test_missing_observed_fails_closed(self):
        decision = releaseid.release_identity_issues(official_manifest(), None)
        self.assertIn("INVALID_OBSERVED", codes(decision))

    def test_frontend_marker_helper(self):
        manifest = official_manifest()
        marker = releaseid.frontend_marker(manifest)
        self.assertEqual(
            marker,
            {
                "version": "1.2.1",
                "sourceSha": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4",
                "frontendSha256": (
                    "b9debb6b25ee6e6e534f7738d27f53f4153dbf361f097336741ae9fb54939ee4"
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
