"""Unit tests for the controlled promotion decision layer (subphase 3.4)."""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from typing import ClassVar

from release_contract import promotion

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "promotion")
VALID = os.path.join(RELEASE_ROOT, "fixtures", "valid")

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def valid_fixture(name):
    with open(os.path.join(VALID, name), encoding="utf-8") as handle:
        return json.load(handle)


def codes(decision):
    return [issue["code"] for issue in decision.issues]


class DispatchTests(unittest.TestCase):
    def test_valid_inputs(self):
        decision = promotion.dispatch_issues("1.2.1", "123456789")
        self.assertTrue(decision.valid)
        self.assertEqual(decision.issues, [])

    def test_invalid_semver(self):
        decision = promotion.dispatch_issues("1.2.1-beta", "123456789")
        self.assertFalse(decision.valid)
        self.assertIn("INVALID_VERSION", codes(decision))

    def test_invalid_run_id(self):
        decision = promotion.dispatch_issues("1.2.1", "not-a-number")
        self.assertFalse(decision.valid)
        self.assertIn("INVALID_RUN_ID", codes(decision))
        self.assertIn("INVALID_RUN_ID", codes(promotion.dispatch_issues("1.2.1", "0")))

    def test_digest_never_accepted(self):
        # An image tag/digest typed by hand is never a valid dispatch input.
        decision = promotion.dispatch_issues("1.2.1", "sha256:abc")
        self.assertFalse(decision.valid)


class SourceShaBindingTests(unittest.TestCase):
    def test_omitted_selector_is_allowed(self):
        decision = promotion.source_sha_issues("", SHA)
        self.assertTrue(decision.valid)

    def test_matching_selector_is_allowed(self):
        decision = promotion.source_sha_issues(SHA, SHA)
        self.assertTrue(decision.valid)
        self.assertEqual(decision.issues, [])

    def test_mismatching_selector_fails_closed(self):
        decision = promotion.source_sha_issues("f" * 40, SHA)
        self.assertFalse(decision.valid)
        self.assertIn("SOURCE_SHA_MISMATCH", codes(decision))

    def test_invalid_selector_fails_closed(self):
        decision = promotion.source_sha_issues("not-a-sha", SHA)
        self.assertFalse(decision.valid)
        self.assertIn("INVALID_SHA", codes(decision))


class RunEvidenceTests(unittest.TestCase):
    def test_ok(self):
        decision = promotion.run_evidence_issues(fixture("run-ok.json"), SHA)
        self.assertTrue(decision.valid)
        self.assertEqual(decision.issues, [])

    def test_wrong_event(self):
        decision = promotion.run_evidence_issues(fixture("run-wrong-event.json"), SHA)
        self.assertIn("RUN_EVENT_MISMATCH", codes(decision))

    def test_wrong_ref(self):
        decision = promotion.run_evidence_issues(fixture("run-wrong-ref.json"), SHA)
        self.assertIn("RUN_REF_MISMATCH", codes(decision))

    def test_wrong_sha(self):
        decision = promotion.run_evidence_issues(fixture("run-wrong-sha.json"), SHA)
        self.assertIn("RUN_SHA_MISMATCH", codes(decision))

    def test_failed(self):
        decision = promotion.run_evidence_issues(fixture("run-failed.json"), SHA)
        self.assertIn("RUN_UNSUCCESSFUL", codes(decision))

    def test_staging_failed(self):
        decision = promotion.run_evidence_issues(fixture("run-staging-failed.json"), SHA)
        self.assertIn("RUN_STAGING_UNSUCCESSFUL", codes(decision))

    def test_missing_run(self):
        decision = promotion.run_evidence_issues(None, SHA)
        self.assertIn("RUN_MISSING", codes(decision))

    def test_missing_attempt(self):
        run = dict(fixture("run-ok.json"))
        del run["runAttempt"]
        decision = promotion.run_evidence_issues(run, SHA)
        self.assertIn("RUN_IDENTITY_MISSING", codes(decision))


class AncestryTests(unittest.TestCase):
    def test_ok(self):
        decision = promotion.ancestry_issues(fixture("ancestry-ok.json"))
        self.assertTrue(decision.valid)
        self.assertEqual(decision.issues, [])

    def test_behind_official(self):
        decision = promotion.ancestry_issues(fixture("ancestry-behind-official.json"))
        self.assertIn("CANDIDATE_BEHIND_OFFICIAL", codes(decision))

    def test_not_on_main(self):
        decision = promotion.ancestry_issues(fixture("ancestry-not-on-main.json"))
        self.assertIn("CANDIDATE_NOT_ON_MAIN", codes(decision))

    def test_same_version(self):
        decision = promotion.ancestry_issues(fixture("ancestry-same-version.json"))
        self.assertIn("VERSION_NOT_INCREASING", codes(decision))

    def test_first_release(self):
        decision = promotion.ancestry_issues(fixture("ancestry-first.json"))
        self.assertTrue(decision.valid)

    def test_missing_compare(self):
        ancestry = dict(fixture("ancestry-ok.json"))
        del ancestry["reachableFromMain"]
        decision = promotion.ancestry_issues(ancestry)
        self.assertIn("REACHABLE_MISSING", codes(decision))


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.manifest = valid_fixture("candidate-v1.2.1.json")

    def test_ok(self):
        decision = promotion.preflight_issues(self.manifest, fixture("observed-preflight-ok.json"))
        self.assertTrue(decision.valid)
        self.assertEqual(decision.issues, [])

    def test_db_unreviewed(self):
        decision = promotion.preflight_issues(
            self.manifest, fixture("observed-preflight-db-unreviewed.json")
        )
        self.assertIn("SCHEMA_CHANGE_UNREVIEWED", codes(decision))

    def test_db_reviewed(self):
        decision = promotion.preflight_issues(
            self.manifest, fixture("observed-preflight-db-reviewed.json")
        )
        self.assertNotIn("SCHEMA_CHANGE_UNREVIEWED", codes(decision))

    def test_identity_blocked(self):
        decision = promotion.preflight_issues(
            self.manifest, fixture("observed-preflight-identity-blocked.json")
        )
        self.assertIn("GIT_TAG_CONFLICT", codes(decision))
        self.assertIn("RELEASE_IDENTITY_BLOCKED", codes(decision))

    def test_manifest_invalid(self):
        manifest = dict(self.manifest)
        manifest["components"] = {}
        decision = promotion.preflight_issues(manifest, fixture("observed-preflight-ok.json"))
        self.assertIn("MANIFEST_INVALID", codes(decision))

    def test_staging_gate_missing(self):
        # A manifest without a stagingValidation record cannot pass schema
        # validation, so the preflight fails closed with MANIFEST_INVALID before
        # any mutation.
        manifest = json.loads(json.dumps(self.manifest))
        del manifest["release"]["stagingValidation"]
        decision = promotion.preflight_issues(manifest, fixture("observed-preflight-ok.json"))
        self.assertIn("MANIFEST_INVALID", codes(decision))


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.manifest = valid_fixture("candidate-v1.2.1.json")

    def test_ok(self):
        decision = promotion.snapshot_issues(fixture("snapshot-ok.json"), self.manifest)
        self.assertTrue(decision.valid)

    def test_missing_fields(self):
        decision = promotion.snapshot_issues(fixture("snapshot-missing-fields.json"), self.manifest)
        self.assertIn("SNAPSHOT_MISSING_FIELD", codes(decision))

    def test_missing_service(self):
        snapshot = json.loads(json.dumps(fixture("snapshot-ok.json")))
        del snapshot["services"]["onlineshop-items"]
        decision = promotion.snapshot_issues(snapshot, self.manifest)
        self.assertIn("SNAPSHOT_SERVICE_MISSING", codes(decision))

    def test_missing_official(self):
        snapshot = json.loads(json.dumps(fixture("snapshot-ok.json")))
        del snapshot["officialRelease"]
        decision = promotion.snapshot_issues(snapshot, self.manifest)
        self.assertIn("SNAPSHOT_OFFICIAL_MISSING", codes(decision))


class DeploymentPlanTests(unittest.TestCase):
    def test_ok(self):
        decision = promotion.deployment_plan_issues(fixture("plan-ok.json"))
        self.assertTrue(decision.valid)

    def test_wrong_order(self):
        decision = promotion.deployment_plan_issues(fixture("plan-wrong-order.json"))
        self.assertIn("PLAN_ORDER_INVALID", codes(decision))

    def test_unsafe(self):
        decision = promotion.deployment_plan_issues(fixture("plan-unsafe.json"))
        for code in (
            "CIRCUIT_BREAKER_DISABLED",
            "ROLLBACK_DISABLED",
            "MIN_HEALTHY_PERCENT",
            "MAX_PERCENT",
        ):
            self.assertIn(code, codes(decision))


class WaiterTests(unittest.TestCase):
    EXPECTED: ClassVar[dict[str, object]] = {
        "component": "auth",
        "deploymentId": "ecs-svc/7000000000000000001",
        "taskDefinitionArn": (
            "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5"
        ),
        "imageDigest": "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0",
    }

    def test_ok(self):
        decision = promotion.waiter_verified(fixture("waiter-ok.json"), self.EXPECTED)
        self.assertTrue(decision.valid)

    def test_wrong_deployment(self):
        decision = promotion.waiter_verified(fixture("waiter-wrong-deployment.json"), self.EXPECTED)
        self.assertIn("DEPLOYMENT_ID_MISMATCH", codes(decision))

    def test_in_progress(self):
        decision = promotion.waiter_verified(fixture("waiter-in-progress.json"), self.EXPECTED)
        self.assertIn("DEPLOYMENT_NOT_COMPLETED", codes(decision))

    def test_wrong_digest(self):
        decision = promotion.waiter_verified(fixture("waiter-wrong-digest.json"), self.EXPECTED)
        self.assertIn("WAITER_DIGEST_MISMATCH", codes(decision))


class FrontendPublicationTests(unittest.TestCase):
    def test_ok(self):
        decision = promotion.frontend_publication_issues(fixture("frontend-plan-ok.json"))
        self.assertTrue(decision.valid)

    def test_delete_forbidden(self):
        decision = promotion.frontend_publication_issues(fixture("frontend-plan-unsafe.json"))
        self.assertIn("FRONTEND_DELETE_FORBIDDEN", codes(decision))

    def test_missing_prefix(self):
        decision = promotion.frontend_publication_issues(fixture("frontend-plan-no-prefix.json"))
        self.assertIn("FRONTEND_PREFIX_MISSING", codes(decision))
        self.assertIn("FRONTEND_ORDER_INVALID", codes(decision))


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = valid_fixture("official-v1.2.1.json")

    def test_ok(self):
        decision = promotion.verification_issues(fixture("verify-ok.json"), self.manifest)
        self.assertTrue(decision.valid)

    def test_digest_mismatch(self):
        decision = promotion.verification_issues(
            fixture("verify-digest-mismatch.json"), self.manifest
        )
        self.assertIn("RUNNING_DIGEST_MISMATCH", codes(decision))

    def test_alb_unhealthy(self):
        decision = promotion.verification_issues(
            fixture("verify-alb-unhealthy.json"), self.manifest
        )
        self.assertIn("ALB_UNHEALTHY", codes(decision))

    def test_marker_mismatch(self):
        decision = promotion.verification_issues(
            fixture("verify-marker-mismatch.json"), self.manifest
        )
        self.assertIn("FRONTEND_MARKER_MISMATCH", codes(decision))

    def test_empty_running(self):
        observed = json.loads(json.dumps(fixture("verify-ok.json")))
        observed["running"] = []
        decision = promotion.verification_issues(observed, self.manifest)
        self.assertIn("RUNNING_TASKS_MISSING", codes(decision))


class FinalizationTests(unittest.TestCase):
    def test_publish(self):
        decision = promotion.finalization_decision(fixture("finalize-publish.json"))
        self.assertTrue(decision.valid)
        self.assertEqual(decision.action, "publish")

    def test_resume(self):
        decision = promotion.finalization_decision(fixture("finalize-resume.json"))
        self.assertTrue(decision.valid)
        self.assertEqual(decision.action, "resume")

    def test_before_verification(self):
        decision = promotion.finalization_decision(fixture("finalize-before-verify.json"))
        self.assertFalse(decision.valid)
        self.assertIn("PUBLICATION_BEFORE_VERIFICATION", codes(decision))

    def test_conflict(self):
        decision = promotion.finalization_decision(fixture("finalize-conflict.json"))
        self.assertFalse(decision.valid)
        self.assertIn("RELEASE_TAG_CONFLICT", codes(decision))
        self.assertEqual(decision.action, "fail-closed")


class CompensationTests(unittest.TestCase):
    def test_partial_reverse_order(self):
        decision = promotion.compensation_steps(
            fixture("snapshot-ok.json"), fixture("changed-partial.json")
        )
        self.assertTrue(decision.valid)
        # Reverse deploy order restricted to changed components: apiGateway,
        # items, auth.
        self.assertEqual(
            [step["component"] for step in decision.steps], ["apiGateway", "items", "auth"]
        )

    def test_all_includes_frontend(self):
        decision = promotion.compensation_steps(
            fixture("snapshot-ok.json"), fixture("changed-all.json")
        )
        self.assertTrue(decision.valid)
        self.assertEqual(
            [step["component"] for step in decision.steps],
            ["frontend", "apiGateway", "items", "auth"],
        )

    def test_missing_snapshot(self):
        decision = promotion.compensation_steps(None, fixture("changed-partial.json"))
        self.assertFalse(decision.valid)
        self.assertIn("SNAPSHOT_MISSING", codes(decision))

    def test_unknown_changed_component(self):
        decision = promotion.compensation_steps(
            fixture("snapshot-ok.json"), fixture("changed-not-in-snapshot.json")
        )
        # "items" is a known component with a snapshot entry, so it is restorable.
        self.assertTrue(decision.valid)
        self.assertEqual([step["component"] for step in decision.steps], ["items"])


if __name__ == "__main__":
    unittest.main()
