"""Unit tests for the rollback decision layer (Pass 3, subphase 3.6)."""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import rollback

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "rollback")
VALID = os.path.join(RELEASE_ROOT, "fixtures", "valid")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def valid_fixture(name):
    with open(os.path.join(VALID, name), encoding="utf-8") as handle:
        return json.load(handle)


def codes(decision):
    return [issue["code"] for issue in decision.issues]


class DispatchTests(unittest.TestCase):
    def test_valid_version(self):
        decision = rollback.dispatch_issues("1.1.0")
        self.assertTrue(decision.valid)

    def test_invalid_semver(self):
        decision = rollback.dispatch_issues("1.1.0-beta")
        self.assertFalse(decision.valid)
        self.assertIn("INVALID_VERSION", codes(decision))

    def test_rejects_non_version_inputs(self):
        for bad in ("v1.1.0", "sha256:abcdef", "a1b2c3d4", "latest"):
            self.assertFalse(rollback.dispatch_issues(bad).valid)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.index = fixture("index.json")
        self.observed = fixture("observed-ok.json")

    def test_selects_previous_complete_official(self):
        decision = rollback.selection_issues(self.index, self.observed, "1.1.0")
        self.assertTrue(decision.valid)
        self.assertEqual(decision.issues, [])
        self.assertEqual(
            rollback.latest_complete_officials(self.index, self.observed), ["1.2.1", "1.1.0"]
        )

    def test_target_not_found(self):
        decision = rollback.selection_issues(self.index, self.observed, "9.9.9")
        self.assertFalse(decision.valid)
        self.assertIn("TARGET_NOT_FOUND", codes(decision))

    def test_target_not_official(self):
        index = fixture("index-candidate.json")
        decision = rollback.selection_issues(index, self.observed, "1.1.0")
        self.assertFalse(decision.valid)
        self.assertIn("TARGET_NOT_OFFICIAL", codes(decision))

    def test_target_is_current(self):
        observed = fixture("observed-current.json")
        decision = rollback.selection_issues(self.index, observed, "1.1.0")
        self.assertFalse(decision.valid)
        self.assertIn("TARGET_IS_CURRENT", codes(decision))

    def test_missing_artifact(self):
        observed = fixture("observed-missing.json")
        decision = rollback.selection_issues(self.index, observed, "1.1.0")
        self.assertFalse(decision.valid)
        self.assertIn("TARGET_ARTIFACT_MISSING", codes(decision))

    def test_tampered_artifact(self):
        observed = fixture("observed-tampered.json")
        decision = rollback.selection_issues(self.index, observed, "1.1.0")
        self.assertFalse(decision.valid)
        self.assertIn("TARGET_ARTIFACT_MISMATCH", codes(decision))

    def test_outside_rollback_window(self):
        index, observed = self._build_window(12)
        decision = rollback.selection_issues(index, observed, "1.2.0")
        self.assertFalse(decision.valid)
        self.assertIn("TARGET_OUTSIDE_ROLLBACK_WINDOW", codes(decision))
        self.assertEqual(rollback.latest_complete_officials(index, observed)[0], "1.12.0")

    def test_latest_ten_complete_are_selectable(self):
        index, observed = self._build_window(12)
        decision = rollback.selection_issues(index, observed, "1.3.0")
        self.assertTrue(decision.valid)
        self.assertEqual(len(rollback.latest_complete_officials(index, observed)), 10)

    def test_invalid_version(self):
        decision = rollback.selection_issues(self.index, self.observed, "1.1")
        self.assertFalse(decision.valid)
        self.assertIn("INVALID_VERSION", codes(decision))

    def test_invalid_index_fails_closed(self):
        decision = rollback.selection_issues({"repository": "x"}, self.observed, "1.1.0")
        self.assertFalse(decision.valid)
        self.assertIn("INVALID_INDEX", codes(decision))

    def _build_window(self, count):
        template = fixture("index.json")["manifests"][1]
        manifests = []
        observed = {
            "ecr": {
                repo: {"releaseTags": {}}
                for repo in ("onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway")
            },
            "frontend": {"prefixMarkers": {}},
            "currentRelease": {"version": f"1.{count}.0"},
        }
        for index in range(count, 0, -1):
            version = f"1.{index}.0"
            manifest = json.loads(json.dumps(template))
            sha = f"{index:040d}"
            digest_suffix = f"{index:064d}"
            manifest["release"]["version"] = version
            manifest["release"]["gitTag"] = f"v{version}"
            manifest["release"]["sourceSha"] = sha
            manifest["components"]["frontend"]["sourceSha"] = sha
            manifest["components"]["frontend"]["sha256"] = f"{index + 100:064d}"
            for comp in ("auth", "items", "apiGateway"):
                identity_prefix = {
                    "auth": "auth",
                    "items": "items",
                    "apiGateway": "api-gateway",
                }[comp]
                manifest["components"][comp]["sourceSha"] = sha
                manifest["components"][comp]["imageDigest"] = f"sha256:{digest_suffix}"
                manifest["components"][comp]["candidateTag"] = f"sha-{sha}"
                manifest["components"][comp]["releaseTag"] = f"release-{version}"
                manifest["components"][comp]["identity"] = f"{identity_prefix}/{version}"
                observed["ecr"][manifest["components"][comp]["repository"]]["releaseTags"][
                    f"release-{version}"
                ] = manifest["components"][comp]["imageDigest"]
            manifest["components"]["items"]["commonSourceSha"] = sha
            manifest["components"]["frontend"]["identity"] = f"frontend/{version}"
            manifest["components"]["frontend"]["releasePrefix"] = f"_releases/v{version}/"
            marker_key = f"_releases/v{version}/release.json"
            observed["frontend"]["prefixMarkers"][marker_key] = {
                "exists": True,
                "marker": {
                    "version": version,
                    "sourceSha": sha,
                    "frontendSha256": manifest["components"]["frontend"]["sha256"],
                },
            }
            manifests.append(manifest)
        index = {"repository": "Djimi/OnlineShop-full-stack", "manifests": manifests}
        return index, observed


class SchemaCompatibilityTests(unittest.TestCase):
    def test_no_schema_change(self):
        decision = rollback.schema_compatibility_issues(fixture("schema-ok.json"))
        self.assertTrue(decision.valid)

    def test_unreviewed_schema_change(self):
        decision = rollback.schema_compatibility_issues(fixture("schema-unreviewed.json"))
        self.assertFalse(decision.valid)
        self.assertIn("SCHEMA_COMPATIBILITY_UNREVIEWED", codes(decision))

    def test_reviewed_schema_change(self):
        decision = rollback.schema_compatibility_issues(fixture("schema-reviewed.json"))
        self.assertTrue(decision.valid)

    def test_missing_state(self):
        decision = rollback.schema_compatibility_issues(None)
        self.assertFalse(decision.valid)
        self.assertIn("SCHEMA_STATE_MISSING", codes(decision))


class FrontendRestoreTests(unittest.TestCase):
    def test_valid_restore_plan(self):
        decision = rollback.frontend_restore_issues(fixture("frontend-restore-ok.json"))
        self.assertTrue(decision.valid)

    def test_delete_forbidden(self):
        decision = rollback.frontend_restore_issues(fixture("frontend-restore-unsafe.json"))
        self.assertFalse(decision.valid)
        self.assertIn("FRONTEND_DELETE_FORBIDDEN", codes(decision))

    def test_prefix_required(self):
        decision = rollback.frontend_restore_issues(fixture("frontend-restore-no-prefix.json"))
        self.assertFalse(decision.valid)
        self.assertIn("FRONTEND_PREFIX_MISSING", codes(decision))

    def test_invalidation_required(self):
        decision = rollback.frontend_restore_issues(fixture("frontend-restore-no-invalidate.json"))
        self.assertFalse(decision.valid)
        self.assertIn("FRONTEND_INVALIDATION_MISSING", codes(decision))


class ResultTests(unittest.TestCase):
    def test_valid_result(self):
        decision = rollback.result_issues(fixture("result-ok.json"))
        self.assertTrue(decision.valid)
        self.assertEqual(decision.action, "write")

    def test_idempotent_resume(self):
        decision = rollback.result_issues(fixture("result-resume.json"))
        self.assertTrue(decision.valid)
        self.assertEqual(decision.action, "resume")

    def test_conflict_fails_closed(self):
        decision = rollback.result_issues(fixture("result-conflict.json"))
        self.assertFalse(decision.valid)
        self.assertIn("RESULT_CONFLICT", codes(decision))

    def test_invalid_result(self):
        decision = rollback.result_issues(fixture("result-invalid.json"))
        self.assertFalse(decision.valid)
        self.assertIn("RESULT_NOT_VERIFIED", codes(decision))
        self.assertIn("RESULT_SAME_RELEASE", codes(decision))
        self.assertIn("RESULT_AUDIT_NOT_ANNOTATED", codes(decision))
        self.assertIn("RESULT_WORKFLOW_MISSING", codes(decision))

    def test_from_snapshot_mismatch(self):
        state = fixture("result-ok.json")
        state = json.loads(json.dumps(state))
        state["snapshot"]["officialRelease"]["version"] = "1.0.0"
        decision = rollback.result_issues(state)
        self.assertFalse(decision.valid)
        self.assertIn("RESULT_FROM_SNAPSHOT_MISMATCH", codes(decision))

    def test_target_mismatch(self):
        state = fixture("result-ok.json")
        state = json.loads(json.dumps(state))
        state["result"]["to"]["version"] = "1.0.0"
        decision = rollback.result_issues(state)
        self.assertFalse(decision.valid)
        self.assertIn("RESULT_TARGET_MISMATCH", codes(decision))


class ReusedPromotionDecisionsTests(unittest.TestCase):
    """The promotion rules reused by rollback stay wired and green."""

    def test_snapshot(self):
        snapshot = fixture(os.path.join("..", "promotion", "snapshot-ok.json"))
        manifest = valid_fixture("official-v1.2.1.json")
        decision = rollback.snapshot_issues(snapshot, manifest)
        self.assertTrue(decision.valid)

    def test_plan(self):
        plan = fixture(os.path.join("..", "promotion", "plan-ok.json"))
        decision = rollback.deployment_plan_issues(plan)
        self.assertTrue(decision.valid)

    def test_verify(self):
        observed = fixture(os.path.join("..", "promotion", "verify-ok.json"))
        manifest = valid_fixture("official-v1.2.1.json")
        decision = rollback.verification_issues(observed, manifest)
        self.assertTrue(decision.valid)

    def test_compensate(self):
        snapshot = fixture(os.path.join("..", "promotion", "snapshot-ok.json"))
        changed = fixture(os.path.join("..", "promotion", "changed-partial.json"))
        decision = rollback.compensation_steps(snapshot, changed)
        self.assertTrue(decision.valid)


if __name__ == "__main__":
    unittest.main()
