"""Unit tests for IAM least-privilege and OIDC trust validation (subphase 3.3)."""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import iam

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IAM_FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "iam")
PLAN_DIR = os.path.join(RELEASE_ROOT, "..")

POLICIES = {
    "candidate-build": "github-actions-candidate-build-policy.json",
    "promotion": "github-actions-promotion-policy.json",
    "production-deploy": "github-actions-production-deploy-policy.json",
    "rollback": "github-actions-rollback-policy.json",
}
TRUST = "github-actions-oidc-trust-policy.json"


def load_policy(name):
    with open(os.path.join(PLAN_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def load_fixture(name):
    with open(os.path.join(IAM_FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def codes(outcome):
    return [issue["code"] for issue in outcome.issues]


def actions_of(policy):
    return {
        action
        for statement in policy["Statement"]
        for action in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    }


class IamPolicyValidationTests(unittest.TestCase):
    def test_all_source_controlled_policies_are_valid(self):
        for name, filename in POLICIES.items():
            with self.subTest(policy=name):
                outcome = iam.iam_policy_issues(load_policy(filename))
                self.assertTrue(outcome.valid, msg=f"{name}: {outcome.issues}")

    def test_broad_ecr_resource_rejected(self):
        outcome = iam.iam_policy_issues(load_fixture("invalid-broad-ecr-resource.json"))
        self.assertIn("BROAD_ECR_RESOURCE", codes(outcome))

    def test_getauthtoken_must_be_global(self):
        outcome = iam.iam_policy_issues(load_fixture("invalid-getauthtoken-scoped.json"))
        self.assertIn("GLOBAL_ACTION_SCOPED", codes(outcome))

    def test_passrole_wildcard_rejected(self):
        outcome = iam.iam_policy_issues(load_fixture("invalid-passrole-unscoped.json"))
        self.assertIn("PASSROLE_UNSCOPED", codes(outcome))

    def test_passrole_without_service_condition_rejected(self):
        outcome = iam.iam_policy_issues(load_fixture("invalid-passrole-no-condition.json"))
        self.assertIn("PASSROLE_NO_SERVICE_CONDITION", codes(outcome))

    def test_mutating_action_on_wildcard_rejected(self):
        outcome = iam.iam_policy_issues(load_fixture("invalid-mutation-wildcard.json"))
        self.assertIn("MUTATION_ON_WILDCARD", codes(outcome))

    def test_promotion_policy_has_no_layer_upload(self):
        actions = actions_of(load_policy(POLICIES["promotion"]))
        self.assertFalse(actions & iam.ECR_LAYER_UPLOAD_ACTIONS)
        self.assertIn("ecr:PutImage", actions)

    def test_rollback_policy_has_no_put_image(self):
        actions = actions_of(load_policy(POLICIES["rollback"]))
        self.assertNotIn("ecr:PutImage", actions)

    def test_candidate_build_policy_has_no_deploy_actions(self):
        actions = actions_of(load_policy(POLICIES["candidate-build"]))
        denied_prefixes = ("ecs:", "s3:", "cloudfront:", "elasticloadbalancing:", "rds:", "iam:")
        for action in actions:
            self.assertFalse(
                any(action.startswith(prefix) for prefix in denied_prefixes),
                msg=f"candidate-build must not hold {action}",
            )


class TrustPolicyValidationTests(unittest.TestCase):
    def test_source_controlled_trust_policy_is_valid(self):
        outcome = iam.trust_policy_issues(load_policy(TRUST))
        self.assertTrue(outcome.valid, msg=f"trust: {outcome.issues}")

    def test_missing_audience_rejected(self):
        outcome = iam.trust_policy_issues(load_fixture("invalid-trust-no-aud.json"))
        self.assertIn("OIDC_AUD_MISMATCH", codes(outcome))

    def test_missing_environment_subject_rejected(self):
        outcome = iam.trust_policy_issues(load_fixture("invalid-trust-no-env-subject.json"))
        self.assertIn("MISSING_SUBJECT", codes(outcome))

    def test_wrong_audience_rejected(self):
        outcome = iam.trust_policy_issues(load_fixture("invalid-trust-wrong-aud.json"))
        self.assertIn("OIDC_AUD_MISMATCH", codes(outcome))


if __name__ == "__main__":
    unittest.main()
