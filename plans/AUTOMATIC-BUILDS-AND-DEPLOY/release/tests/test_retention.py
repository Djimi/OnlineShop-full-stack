"""Unit tests for the retention decision layer (Pass 3, subphase 3.8)."""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import retention

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "retention")
REFERENCE = "2026-08-04T00:00:00Z"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def codes(decision):
    return [issue["code"] for issue in decision.issues]


class PolicyValidationTests(unittest.TestCase):
    def test_desired_policy_is_valid(self):
        decision = retention.policy_issues(fixture("policy.json"))
        self.assertTrue(decision.valid, codes(decision))

    def test_keep10_rule_must_be_first(self):
        decision = retention.policy_issues(fixture("policy-invalid-order.json"))
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_RELEASE_RULE_NOT_FIRST", codes(decision))
        self.assertIn("POLICY_RULE_ORDER_INVALID", codes(decision))

    def test_generic_negative_exclusion_rejected(self):
        decision = retention.policy_issues(fixture("policy-generic-exclusion.json"))
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_AMBIGUOUS_SELECTION", codes(decision))
        self.assertIn("POLICY_EXCLUSION_FILTER", codes(decision))

    def test_wrong_counts_rejected(self):
        decision = retention.policy_issues(fixture("policy-wrong-counts.json"))
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_RELEASE_RULE_MISCONFIGURED", codes(decision))
        self.assertIn("POLICY_CANDIDATE_RULE_MISCONFIGURED", codes(decision))
        self.assertIn("POLICY_UNTAGGED_RULE_MISCONFIGURED", codes(decision))

    def test_tagged_rule_without_prefix_list_rejected(self):
        # ECR schema requires an explicit tagPrefixList on every tagged rule;
        # a generic "expire all tagged after N days" rule is not expressible
        # and would be a negative/exclusion filter in disguise.
        policy = fixture("policy.json")
        policy = json.loads(json.dumps(policy))
        policy["rules"].append(
            {
                "rulePriority": 6,
                "description": "generic tagged age rule without prefixes",
                "selection": {
                    "tagStatus": "tagged",
                    "countType": "sinceImagePushed",
                    "countUnit": "days",
                    "countNumber": 30,
                },
                "action": {"type": "expire"},
            }
        )
        decision = retention.policy_issues(policy)
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_TAGPREFIX_REQUIRED", codes(decision))

    def test_multi_prefix_tagged_rule_rejected(self):
        # AWS documents that a multi-entry tagPrefixList/tagPatternList selects
        # only images carrying ALL the listed tags ("only the images with all
        # specified tags are selected"), so a merged multi-prefix rule would
        # silently select nothing — each candidate family gets its own
        # single-prefix rule, and the validator rejects merged lists.
        policy = fixture("policy.json")
        policy = json.loads(json.dumps(policy))
        for rule in policy["rules"]:
            prefixes = rule.get("selection", {}).get("tagPrefixList")
            if prefixes and "main-latest" in prefixes:
                rule["selection"]["tagPrefixList"] = ["main-latest", "branch-"]
        decision = retention.policy_issues(policy)
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_TAGPREFIX_MULTI", codes(decision))

    def test_uncovered_candidate_family_rejected(self):
        policy = fixture("policy.json")
        policy = json.loads(json.dumps(policy))
        for rule in policy["rules"]:
            prefixes = rule.get("selection", {}).get("tagPrefixList")
            if prefixes and "main-latest" in prefixes:
                rule["selection"]["tagPrefixList"] = ["branch-"]
        decision = retention.policy_issues(policy)
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_CANDIDATE_RULE_MISCONFIGURED", codes(decision))

    def test_missing_rules(self):
        self.assertIn("POLICY_EMPTY", codes(retention.policy_issues({"rules": []})))
        self.assertIn("POLICY_INVALID", codes(retention.policy_issues(None)))

    def test_duplicate_priority_rejected(self):
        policy = fixture("policy.json")
        policy = json.loads(json.dumps(policy))
        policy["rules"].append(json.loads(json.dumps(policy["rules"][0])))
        decision = retention.policy_issues(policy)
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_RULE_PRIORITY_INVALID", codes(decision))

    def test_two_untagged_rules_rejected(self):
        policy = fixture("policy.json")
        policy = json.loads(json.dumps(policy))
        policy["rules"].append(
            {
                "rulePriority": 6,
                "description": "second untagged selector",
                "selection": {
                    "tagStatus": "untagged",
                    "countType": "sinceImagePushed",
                    "countUnit": "days",
                    "countNumber": 7,
                },
                "action": {"type": "expire"},
            }
        )
        decision = retention.policy_issues(policy)
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_UNTAGGED_RULE_COUNT", codes(decision))

    def test_duplicate_tag_prefix_rejected(self):
        policy = fixture("policy.json")
        policy = json.loads(json.dumps(policy))
        policy["rules"].append(
            {
                "rulePriority": 6,
                "description": "release prefix reused",
                "selection": {
                    "tagStatus": "tagged",
                    "tagPrefixList": ["release-"],
                    "countType": "imageCountMoreThan",
                    "countNumber": 5,
                },
                "action": {"type": "expire"},
            }
        )
        decision = retention.policy_issues(policy)
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_PREFIX_OVERLAP", codes(decision))


class EvaluationModelTests(unittest.TestCase):
    def setUp(self):
        self.policy = fixture("policy.json")
        self.images = fixture("images-multitag.json")

    def _auth(self):
        decision = retention.evaluate_images(self.policy, self.images, REFERENCE)
        self.assertTrue(decision.valid, codes(decision))
        return decision.data["repositories"]["onlineshop-auth"]

    def test_keep10_protects_multitag_release_images(self):
        """The core multi-tag proof: release images older than 30 days that are
        within the newest 10 by push order are KEPT by rule 1 (priority 1) even
        though the candidate rules' selection also matches their tags."""
        auth = self._auth()
        by_digest = {image["imageDigest"]: image for image in auth["images"]}
        for index in range(12, 2, -1):
            image = by_digest[f"sha256:1{index:063d}"]
            self.assertEqual(image["action"], "keep")
            self.assertEqual(image["appliedRulePriority"], 1)
            # pushed 17-33 days before the reference date: the candidate rules
            # would match, but rule 1 claims the image first.
            self.assertTrue(any(tag.startswith("release-") for tag in image["imageTags"]))

    def test_oldest_release_images_expire_by_rule1(self):
        auth = self._auth()
        by_digest = {image["imageDigest"]: image for image in auth["images"]}
        for index in (2, 1):
            image = by_digest[f"sha256:1{index:063d}"]
            self.assertEqual(image["action"], "expire")
            self.assertEqual(image["appliedRulePriority"], 1)

    def test_candidates_expire_after_30_days(self):
        auth = self._auth()
        by_digest = {image["imageDigest"]: image for image in auth["images"]}
        self.assertEqual(by_digest[f"sha256:1{100:063d}"]["action"], "expire")  # sha-*, 06-10
        self.assertEqual(by_digest[f"sha256:1{100:063d}"]["appliedRulePriority"], 2)
        self.assertEqual(by_digest[f"sha256:1{102:063d}"]["action"], "expire")  # main-latest, 05-05
        self.assertEqual(by_digest[f"sha256:1{102:063d}"]["appliedRulePriority"], 3)
        self.assertEqual(by_digest[f"sha256:1{101:063d}"]["action"], "keep")  # branch-*, 07-25

    def test_untagged_grace_period(self):
        auth = self._auth()
        by_digest = {image["imageDigest"]: image for image in auth["images"]}
        self.assertEqual(by_digest[f"sha256:1{103:063d}"]["action"], "expire")  # 07-01 > 14d
        self.assertEqual(by_digest[f"sha256:1{103:063d}"]["appliedRulePriority"], 5)
        self.assertEqual(by_digest[f"sha256:1{104:063d}"]["action"], "keep")  # 08-01 < 14d

    def test_expire_set_is_exact(self):
        auth = self._auth()
        expiring = sorted(image["imageTags"] for image in auth["expiring"])
        self.assertEqual(
            expiring,
            sorted(
                [
                    ["release-1.1.0", "sha-0000000000000000000000000000000000000001"],
                    ["release-1.2.0", "sha-0000000000000000000000000000000000000002"],
                    ["sha-0000000000000000000000000000000000000100"],
                    ["main-latest"],
                    [],
                ]
            ),
        )

    def test_reference_date_is_deterministic(self):
        first = retention.evaluate_images(self.policy, self.images, REFERENCE)
        second = retention.evaluate_images(self.policy, self.images, REFERENCE)
        self.assertEqual(first.data, second.data)

    def test_invalid_policy_fails_closed(self):
        decision = retention.evaluate_images(
            fixture("policy-invalid-order.json"), self.images, REFERENCE
        )
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_RELEASE_RULE_NOT_FIRST", codes(decision))

    def test_missing_pushed_at_fails_closed(self):
        images = json.loads(json.dumps(self.images))
        images["onlineshop-auth"]["images"][0]["imagePushedAt"] = None
        decision = retention.evaluate_images(self.policy, images, REFERENCE)
        self.assertFalse(decision.valid)
        self.assertIn("INVALID_IMAGE_RECORD", codes(decision))


class PreviewValidationTests(unittest.TestCase):
    def setUp(self):
        self.policy = fixture("policy.json")
        self.images = fixture("images-multitag.json")
        self.protected = fixture("protected.json")

    def test_consistent_preview_passes(self):
        decision = retention.preview_issues(
            self.policy, self.images, fixture("evaluator-ok.json"), self.protected, REFERENCE
        )
        self.assertTrue(decision.valid, codes(decision))

    def test_protected_image_expiring_fails_closed(self):
        decision = retention.preview_issues(
            self.policy,
            self.images,
            fixture("evaluator-protected-expiring.json"),
            self.protected,
            REFERENCE,
        )
        self.assertFalse(decision.valid)
        self.assertIn("PROTECTED_IMAGE_EXPIRING", codes(decision))
        # A release-tagged image selected by a non-official rule is a policy
        # defect even when it is not in the protected set.
        self.assertIn("RELEASE_RULE_NOT_APPLIED", codes(decision))

    def test_preview_disagreement_fails_closed(self):
        decision = retention.preview_issues(
            self.policy,
            self.images,
            fixture("evaluator-disagreement.json"),
            self.protected,
            REFERENCE,
        )
        self.assertFalse(decision.valid)
        self.assertIn("PREVIEW_DISAGREEMENT", codes(decision))

    def test_unknown_image_fails_closed(self):
        preview = json.loads(json.dumps(fixture("evaluator-ok.json")))
        preview["onlineshop-auth"]["previewResults"].append(
            {
                "imageDigest": "sha256:" + "f" * 64,
                "imageTags": ["sha-" + "e" * 40],
                "imagePushedAt": "2026-07-01T00:00:00Z",
                "action": {"type": "EXPIRE"},
                "appliedRulePriority": 2,
            }
        )
        decision = retention.preview_issues(
            self.policy, self.images, preview, self.protected, REFERENCE
        )
        self.assertFalse(decision.valid)
        self.assertIn("PREVIEW_UNKNOWN_IMAGE", codes(decision))

    def test_protected_flag_only_guards_listed_digests(self):
        # A preview expiring a candidate image is fine even though the release
        # rule protects other digests.
        decision = retention.preview_issues(
            self.policy, self.images, fixture("evaluator-ok.json"), [], REFERENCE
        )
        self.assertTrue(decision.valid, codes(decision))


class AuditWindowTests(unittest.TestCase):
    def test_audit_lists_all_when_fewer_than_ten(self):
        decision = retention.audit_rollback_window(
            fixture("index.json"), fixture("observed-audit-ok.json")
        )
        self.assertTrue(decision.valid, codes(decision))
        self.assertEqual(decision.data["rollbackCapable"], ["1.2.1", "1.1.0"])
        self.assertEqual(decision.data["released"], 2)
        self.assertEqual(decision.data["window"], 2)
        self.assertEqual(decision.data["outsideWindow"], [])

    def test_audit_missing_artifact_fails_closed(self):
        decision = retention.audit_rollback_window(
            fixture("index.json"), fixture("observed-audit-missing.json")
        )
        self.assertFalse(decision.valid)
        self.assertIn("RETENTION_ARTIFACT_MISSING", codes(decision))
        self.assertNotIn("1.1.0", decision.data["rollbackCapable"])

    def test_audit_mismatched_artifact_fails_closed(self):
        decision = retention.audit_rollback_window(
            fixture("index.json"), fixture("observed-audit-mismatch.json")
        )
        self.assertFalse(decision.valid)
        self.assertIn("RETENTION_ARTIFACT_MISMATCH", codes(decision))

    def test_audit_reports_exactly_ten(self):
        decision = retention.audit_rollback_window(
            fixture("window-index.json"), fixture("window-observed-ok.json")
        )
        self.assertTrue(decision.valid, codes(decision))
        self.assertEqual(len(decision.data["rollbackCapable"]), 10)
        self.assertEqual(decision.data["rollbackCapable"][0], "1.12.0")
        self.assertEqual(decision.data["rollbackCapable"][-1], "1.3.0")
        self.assertEqual(decision.data["outsideWindow"], ["1.2.0", "1.1.0"])

    def test_older_metadata_only_release_never_rollback_capable(self):
        # The two releases beyond the window appear only in outsideWindow; even
        # when their artifacts are entirely absent they are never claimed.
        index = fixture("window-index.json")
        observed = json.loads(json.dumps(fixture("window-observed-ok.json")))
        observed["ecr"]["onlineshop-auth"]["releaseTags"].pop("release-1.1.0", None)
        observed["frontend"]["prefixMarkers"].pop("_releases/v1.1.0/release.json", None)
        decision = retention.audit_rollback_window(index, observed)
        self.assertTrue(decision.valid, codes(decision))
        self.assertNotIn("1.1.0", decision.data["rollbackCapable"])
        self.assertIn("1.1.0", decision.data["outsideWindow"])

    def test_observed_read_error_fails_closed(self):
        observed = json.loads(json.dumps(fixture("observed-audit-ok.json")))
        observed["ecr"]["onlineshop-auth"] = {
            "releaseTags": {},
            "images": [],
            "error": "describe-images failed",
        }
        decision = retention.audit_rollback_window(fixture("index.json"), observed)
        self.assertFalse(decision.valid)
        self.assertIn("OBSERVED_READ_ERROR", codes(decision))

    def test_invalid_index_fails_closed(self):
        decision = retention.audit_rollback_window(
            {"repository": "x"}, fixture("observed-audit-ok.json")
        )
        self.assertFalse(decision.valid)
        self.assertIn("INVALID_INDEX", codes(decision))


class CoverageTests(unittest.TestCase):
    def test_window_covered_in_order(self):
        decision = retention.policy_coverage_issues(
            fixture("policy.json"), fixture("window-index.json"), fixture("window-observed-ok.json")
        )
        self.assertTrue(decision.valid, codes(decision))
        self.assertEqual(len(decision.data["window"]), 10)

    def test_backport_gap_fails_closed(self):
        decision = retention.policy_coverage_issues(
            fixture("policy.json"),
            fixture("window-index.json"),
            fixture("window-observed-gap.json"),
        )
        self.assertFalse(decision.valid)
        self.assertIn("POLICY_WINDOW_GAP", codes(decision))


class FrontendRetentionTests(unittest.TestCase):
    def test_unprotected_prefixes_expirable(self):
        decision = retention.frontend_retention_issues(fixture("frontend-prefixes-ok.json"))
        self.assertTrue(decision.valid, codes(decision))
        self.assertEqual(
            decision.data["expirablePrefixes"], ["_releases/v1.1.0/", "_releases/v1.2.0/"]
        )

    def test_protected_delete_fails_closed(self):
        decision = retention.frontend_retention_issues(fixture("frontend-prefixes-fail.json"))
        self.assertFalse(decision.valid)
        self.assertIn("FRONTEND_PROTECTED_DELETE", codes(decision))
        self.assertIn("FRONTEND_UNKNOWN_PREFIX", codes(decision))

    def test_current_and_known_good_never_deleted(self):
        state = fixture("frontend-prefixes-ok.json")
        state = json.loads(json.dumps(state))
        state["proposedDeletions"] = ["_releases/v1.12.0/", "_releases/v1.11.0/"]
        decision = retention.frontend_retention_issues(state)
        self.assertFalse(decision.valid)
        self.assertIn("FRONTEND_PROTECTED_DELETE", codes(decision))

    def test_missing_protected_prefix_fails_closed(self):
        state = fixture("frontend-prefixes-ok.json")
        state = json.loads(json.dumps(state))
        state["prefixes"] = [entry for entry in state["prefixes"] if entry["version"] != "1.12.0"]
        decision = retention.frontend_retention_issues(state)
        self.assertFalse(decision.valid)
        self.assertIn("FRONTEND_RETENTION_GAP", codes(decision))


class RetentionClassesTests(unittest.TestCase):
    def test_known_config_passes(self):
        decision = retention.retention_classes_issues(fixture("retention-classes.json"))
        self.assertTrue(decision.valid, codes(decision))
        self.assertEqual(decision.data["classes"]["candidate-artifact"], 30)

    def test_invalid_config_fails_closed(self):
        decision = retention.retention_classes_issues(fixture("retention-classes-invalid.json"))
        self.assertFalse(decision.valid)
        self.assertIn("RETENTION_CLASS_MISMATCH", codes(decision))
        self.assertIn("RETENTION_CLASS_UNKNOWN", codes(decision))

    def test_missing_class_fails_closed(self):
        config = fixture("retention-classes.json")
        config = json.loads(json.dumps(config))
        del config["classes"]["sbom"]
        decision = retention.retention_classes_issues(config)
        self.assertFalse(decision.valid)
        self.assertIn("RETENTION_CLASS_MISSING", codes(decision))

    def test_indefinite_must_be_indefinite(self):
        config = fixture("retention-classes.json")
        config = json.loads(json.dumps(config))
        config["classes"]["audit-evidence"] = 30
        decision = retention.retention_classes_issues(config)
        self.assertFalse(decision.valid)
        self.assertIn("RETENTION_CLASS_MISMATCH", codes(decision))


if __name__ == "__main__":
    unittest.main()
