"""Unit tests for S3 REST origin + CloudFront OAC hardening rules
(Pass 3, subphase 3.5)."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import frontend_hosting

DIST_ID = frontend_hosting.FRONTEND_DISTRIBUTION_ID
BUCKET = frontend_hosting.FRONTEND_BUCKET
DIST_ARN = frontend_hosting.DISTRIBUTION_ARN
REST_DOMAIN = f"{BUCKET}.s3.eu-north-1.amazonaws.com"
WEBSITE_DOMAIN = f"{BUCKET}.s3-website.eu-north-1.amazonaws.com"


def distribution_config(domain=REST_DOMAIN, oac="OAC123", fallback=True):
    config = {
        "Origins": {
            "Quantity": 2,
            "Items": [
                {"Id": "s3-frontend", "DomainName": domain, "OriginAccessControlId": oac},
                {"Id": "alb-api", "DomainName": "alb.example.elb.amazonaws.com"},
            ],
        },
        "DefaultCacheBehavior": {"TargetOriginId": "s3-frontend"},
    }
    if fallback:
        config["CustomErrorResponses"] = {
            "Quantity": 1,
            "Items": [
                {
                    "ErrorCode": 404,
                    "ResponsePagePath": "/index.html",
                    "ResponseCode": 200,
                }
            ],
        }
    return config


def oac_policy(arn=DIST_ARN):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowCloudFrontServicePrincipalReadOnly",
                "Effect": "Allow",
                "Principal": {"Service": "cloudfront.amazonaws.com"},
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{BUCKET}/*",
                "Condition": {"StringEquals": {"aws:SourceArn": arn}},
            }
        ],
    }


def public_policy():
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PublicReadGetObject",
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{BUCKET}/*",
            }
        ],
    }


def pab(**overrides):
    block = {
        "BlockPublicAcls": True,
        "IgnorePublicAcls": True,
        "BlockPublicPolicy": True,
        "RestrictPublicBuckets": True,
    }
    block.update(overrides)
    return {"PublicAccessBlockConfiguration": block}


def codes(outcome):
    return [issue["code"] for issue in outcome.issues]


class VerifyTests(unittest.TestCase):
    def test_hardened_state_passes(self):
        outcome = frontend_hosting.verify(
            distribution_config(), oac_policy(), pab(), website_config=None
        )
        self.assertTrue(outcome.valid, outcome.issues)

    def test_website_origin_rejected(self):
        outcome = frontend_hosting.verify(
            distribution_config(domain=WEBSITE_DOMAIN), oac_policy(), pab(), None
        )
        self.assertFalse(outcome.valid)
        self.assertIn("WEBSITE_ORIGIN", codes(outcome))

    def test_missing_oac_rejected(self):
        outcome = frontend_hosting.verify(distribution_config(oac=""), oac_policy(), pab(), None)
        self.assertFalse(outcome.valid)
        self.assertIn("OAC_MISSING", codes(outcome))

    def test_public_read_policy_rejected(self):
        outcome = frontend_hosting.verify(distribution_config(), public_policy(), pab(), None)
        self.assertFalse(outcome.valid)
        self.assertIn("PUBLIC_READ_POLICY", codes(outcome))

    def test_missing_oac_policy_rejected(self):
        outcome = frontend_hosting.verify(distribution_config(), None, pab(), None)
        self.assertFalse(outcome.valid)
        self.assertIn("BUCKET_POLICY_MISSING", codes(outcome))
        self.assertIn("OAC_POLICY_MISSING", codes(outcome))

    def test_oac_policy_wrong_distribution_arn_rejected(self):
        outcome = frontend_hosting.verify(
            distribution_config(),
            oac_policy(arn="arn:aws:cloudfront::000000000000:distribution/OTHER"),
            pab(),
            None,
        )
        self.assertFalse(outcome.valid)
        self.assertIn("OAC_POLICY_MISSING", codes(outcome))

    def test_open_public_access_rejected(self):
        outcome = frontend_hosting.verify(
            distribution_config(),
            oac_policy(),
            pab(RestrictPublicBuckets=False),
            None,
        )
        self.assertFalse(outcome.valid)
        self.assertIn("PUBLIC_ACCESS_OPEN", codes(outcome))

    def test_website_still_enabled_rejected(self):
        website = {"IndexDocument": {"Suffix": "index.html"}}
        outcome = frontend_hosting.verify(distribution_config(), oac_policy(), pab(), website)
        self.assertFalse(outcome.valid)
        self.assertIn("WEBSITE_ENABLED", codes(outcome))

    def test_missing_spa_fallback_rejected(self):
        outcome = frontend_hosting.verify(
            distribution_config(fallback=False), oac_policy(), pab(), None
        )
        self.assertFalse(outcome.valid)
        self.assertIn("SPA_FALLBACK_MISSING", codes(outcome))

    def test_missing_distribution_rejected(self):
        outcome = frontend_hosting.verify(None, oac_policy(), pab(), None)
        self.assertFalse(outcome.valid)
        self.assertIn("MISSING_DISTRIBUTION", codes(outcome))


class MigrationPlanTests(unittest.TestCase):
    def test_plan_is_ordered_and_documented(self):
        plan = frontend_hosting.migration_plan(distribution_config())
        steps = [step["step"] for step in plan]
        # The fixture is already hardened (REST origin + OAC), so only the
        # precondition/policy/public-access/website/verify steps remain.
        self.assertEqual(steps, ["0", "4", "5", "6", "7"])
        for step in plan:
            self.assertTrue(step["mutation"])
            self.assertTrue(step["readBack"])

    def test_plan_includes_origin_steps_when_migration_needed(self):
        plan = frontend_hosting.migration_plan(distribution_config(domain=WEBSITE_DOMAIN, oac=""))
        steps = [step["step"] for step in plan]
        self.assertEqual(steps, ["0", "1", "2", "3", "4", "5", "6", "7"])

    def test_plan_mutations_never_mint_release_identity(self):
        # The plan only touches CloudFront/S3; it must not contain any
        # ecr:put-image / promote-image-digest / git tag operation.
        text = json.dumps(
            frontend_hosting.migration_plan(distribution_config(domain=WEBSITE_DOMAIN, oac=""))
        )
        for forbidden in ("promote-image-digest", "ecr:", "git tag", "release-<version>"):
            self.assertNotIn(forbidden, text)

    def test_migration_preconditions_accept_public_read_starting_policy(self):
        outcome = frontend_hosting.migration_preconditions(public_policy())
        self.assertTrue(outcome.valid, outcome.issues)

    def test_migration_preconditions_accept_oac_policy(self):
        outcome = frontend_hosting.migration_preconditions(oac_policy())
        self.assertTrue(outcome.valid, outcome.issues)

    def test_migration_preconditions_reject_unconfigured_policy(self):
        outcome = frontend_hosting.migration_preconditions(None)
        self.assertFalse(outcome.valid)
        self.assertIn("PRECONDITION_NO_POLICY", codes(outcome))

    def test_migration_preconditions_reject_lockout_policy(self):
        outcome = frontend_hosting.migration_preconditions(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": "arn:aws:iam::000000000000:role/some-other"},
                        "Action": "s3:GetObject",
                        "Resource": f"arn:aws:s3:::{BUCKET}/*",
                    }
                ],
            }
        )
        self.assertFalse(outcome.valid)
        self.assertIn("PRECONDITION_LOCKOUT", codes(outcome))


class CliTests(unittest.TestCase):
    def test_cli_verify(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths = {
            "dist": os.path.join(base, "tmp-oac-dist.json"),
            "policy": os.path.join(base, "tmp-oac-policy.json"),
            "pab": os.path.join(base, "tmp-oac-pab.json"),
        }
        try:
            with open(paths["dist"], "w", encoding="utf-8") as handle:
                json.dump(distribution_config(), handle)
            with open(paths["policy"], "w", encoding="utf-8") as handle:
                json.dump(oac_policy(), handle)
            with open(paths["pab"], "w", encoding="utf-8") as handle:
                json.dump(pab(), handle)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "release_contract.frontend_hosting",
                    "verify",
                    "--distribution",
                    paths["dist"],
                    "--bucket-policy",
                    paths["policy"],
                    "--public-access-block",
                    paths["pab"],
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('"valid":true', proc.stdout)
        finally:
            for path in paths.values():
                if os.path.exists(path):
                    os.remove(path)


if __name__ == "__main__":
    unittest.main()
