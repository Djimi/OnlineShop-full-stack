"""Unit tests for CloudTrail management-event coverage audit (Pass 3,
subphase 3.5)."""

import os
import subprocess
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import cloudtrail


def trail(name="onlineshop-trail", multi_region=True, s3_bucket="onlineshop-cloudtrail"):
    return {
        "Name": name,
        "S3BucketName": s3_bucket,
        "IsMultiRegionTrail": multi_region,
    }


def status(logging=True):
    return {"IsLogging": logging, "LatestDeliveryTime": "2026-08-04T12:00:00Z"}


def selectors(include_management=True, read_write="All"):
    return [{"IncludeManagementEvents": include_management, "ReadWriteType": read_write}]


def codes(outcome):
    return [issue["code"] for issue in outcome.issues]


class CoverageTests(unittest.TestCase):
    def test_full_coverage_passes(self):
        outcome = cloudtrail.verify(
            [trail()],
            {"onlineshop-trail": status()},
            {"onlineshop-trail": selectors()},
        )
        self.assertTrue(outcome.valid, outcome.issues)
        self.assertEqual(outcome.covered_services, list(cloudtrail.COVERED_SERVICES))

    def test_no_trail_rejected(self):
        outcome = cloudtrail.verify([], {}, {})
        self.assertFalse(outcome.valid)
        self.assertIn("TRAIL_MISSING", codes(outcome))

    def test_not_logging_rejected(self):
        outcome = cloudtrail.verify(
            [trail()],
            {"onlineshop-trail": status(logging=False)},
            {"onlineshop-trail": selectors()},
        )
        self.assertFalse(outcome.valid)
        self.assertIn("NOT_LOGGING", codes(outcome))

    def test_management_events_disabled_rejected(self):
        outcome = cloudtrail.verify(
            [trail()],
            {"onlineshop-trail": status()},
            {"onlineshop-trail": selectors(include_management=False)},
        )
        self.assertFalse(outcome.valid)
        self.assertIn("MANAGEMENT_EVENTS_DISABLED", codes(outcome))

    def test_single_region_rejected(self):
        outcome = cloudtrail.verify(
            [trail(multi_region=False)],
            {"onlineshop-trail": status()},
            {"onlineshop-trail": selectors()},
        )
        self.assertFalse(outcome.valid)
        self.assertIn("NOT_MULTI_REGION", codes(outcome))

    def test_no_delivery_target_rejected(self):
        outcome = cloudtrail.verify(
            [trail(s3_bucket=None)],
            {"onlineshop-trail": status()},
            {"onlineshop-trail": selectors()},
        )
        self.assertFalse(outcome.valid)
        self.assertIn("NO_DELIVERY_TARGET", codes(outcome))

    def test_target_configured_but_never_delivered_rejected(self):
        # A configured S3/Logs target is not proof of delivery: the trail must
        # show a LatestDeliveryTime (and no delivery error).
        outcome = cloudtrail.verify(
            [trail()],
            {"onlineshop-trail": {"IsLogging": True}},
            {"onlineshop-trail": selectors()},
        )
        self.assertFalse(outcome.valid)
        self.assertIn("NO_DELIVERY_TARGET", codes(outcome))

    def test_active_delivery_error_rejected(self):
        outcome = cloudtrail.verify(
            [trail()],
            {
                "onlineshop-trail": {
                    "IsLogging": True,
                    "LatestDeliveryTime": "2026-08-04T12:00:00Z",
                    "LatestDeliveryError": "S3AccessDenied",
                }
            },
            {"onlineshop-trail": selectors()},
        )
        self.assertFalse(outcome.valid)
        self.assertIn("NO_DELIVERY_TARGET", codes(outcome))


class CliTests(unittest.TestCase):
    def test_cli_verify(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import json

        paths = {
            "trails": os.path.join(base, "tmp-ct-trails.json"),
            "statuses": os.path.join(base, "tmp-ct-statuses.json"),
            "selectors": os.path.join(base, "tmp-ct-selectors.json"),
        }
        try:
            with open(paths["trails"], "w", encoding="utf-8") as handle:
                json.dump([trail()], handle)
            with open(paths["statuses"], "w", encoding="utf-8") as handle:
                json.dump({"onlineshop-trail": status()}, handle)
            with open(paths["selectors"], "w", encoding="utf-8") as handle:
                json.dump({"onlineshop-trail": selectors()}, handle)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "release_contract.cloudtrail",
                    "verify",
                    "--trails",
                    paths["trails"],
                    "--statuses",
                    paths["statuses"],
                    "--selectors",
                    paths["selectors"],
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
