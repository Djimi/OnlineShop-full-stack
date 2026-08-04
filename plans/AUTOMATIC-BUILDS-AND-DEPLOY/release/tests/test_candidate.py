"""Unit tests for candidate artifact production rules (subphase 3.2)."""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import candidate
from release_contract import components as rc
from release_contract.validate import validate_data

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "candidate")
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def codes(decision):
    return [issue["code"] for issue in decision.issues]


class OciLabelsTests(unittest.TestCase):
    def test_canonical_labels(self):
        labels = rc.oci_labels(
            "auth",
            sha=SHA,
            source="https://github.com/Djimi/OnlineShop-full-stack",
            created="2026-08-04T12:00:00Z",
            run_id=123456789,
            run_attempt=1,
            event="push",
            ref="refs/heads/main",
        )
        self.assertEqual(labels[rc.OCI_REVISION], SHA)
        self.assertEqual(labels[rc.OCI_SOURCE], "https://github.com/Djimi/OnlineShop-full-stack")
        self.assertEqual(labels[rc.OCI_TITLE], "OnlineShop auth service")
        self.assertEqual(labels[rc.BUILD_RUN_LABEL], "123456789-1")
        self.assertEqual(labels[rc.PRODUCER_RUN_ID], "123456789")
        self.assertEqual(labels[rc.PRODUCER_RUN_ATTEMPT], "1")
        self.assertEqual(labels[rc.PRODUCER_EVENT], "push")
        self.assertEqual(labels[rc.PRODUCER_REF], "refs/heads/main")

    def test_items_records_common_revision(self):
        labels = rc.oci_labels(
            "items",
            sha=SHA,
            source="https://github.com/Djimi/OnlineShop-full-stack",
            created="2026-08-04T12:00:00Z",
            run_id=1,
            run_attempt=1,
            event="push",
            ref="refs/heads/main",
        )
        self.assertEqual(labels[rc.COMMON_REVISION_LABEL], SHA)

    def test_unknown_backend_raises(self):
        with self.assertRaises(ValueError):
            rc.oci_labels(
                "frontend",
                sha=SHA,
                source="x",
                created="x",
                run_id=1,
                run_attempt=1,
                event="push",
                ref="refs/heads/main",
            )

    def test_run_url(self):
        self.assertEqual(
            rc.run_url("Djimi/OnlineShop-full-stack", 123, 2),
            "https://github.com/Djimi/OnlineShop-full-stack/actions/runs/123/attempts/2",
        )


class ReuseDecisionTests(unittest.TestCase):
    def test_canonical_existing_reused(self):
        decision = candidate.should_reuse_image(
            fixture("existing-canonical.json"), fixture("expected-canonical.json")
        )
        self.assertTrue(decision.reuse)
        self.assertEqual(decision.issues, [])

    def test_reuse_without_producer_conclusion(self):
        expected = fixture("expected-canonical.json")
        del expected["producerConclusion"]
        decision = candidate.should_reuse_image(fixture("existing-canonical.json"), expected)
        self.assertTrue(decision.reuse)

    def test_wrong_revision_fails_closed(self):
        decision = candidate.should_reuse_image(
            fixture("existing-wrong-revision.json"), fixture("expected-canonical.json")
        )
        self.assertFalse(decision.reuse)
        self.assertIn("REVISION_MISMATCH", codes(decision))

    def test_feature_ref_fails_closed(self):
        decision = candidate.should_reuse_image(
            fixture("existing-feature-ref.json"), fixture("expected-canonical.json")
        )
        self.assertFalse(decision.reuse)
        self.assertIn("PRODUCER_REF_MISMATCH", codes(decision))

    def test_dispatch_event_fails_closed(self):
        decision = candidate.should_reuse_image(
            fixture("existing-dispatch-event.json"), fixture("expected-canonical.json")
        )
        self.assertFalse(decision.reuse)
        self.assertIn("PRODUCER_EVENT_MISMATCH", codes(decision))

    def test_missing_producer_labels_fail_closed(self):
        decision = candidate.should_reuse_image(
            fixture("existing-missing-producer.json"), fixture("expected-canonical.json")
        )
        self.assertFalse(decision.reuse)
        self.assertIn("PRODUCER_IDENTITY_MISSING", codes(decision))

    def test_failed_producer_fails_closed(self):
        expected = fixture("expected-canonical.json")
        expected["producerConclusion"] = "failure"
        decision = candidate.should_reuse_image(fixture("existing-canonical.json"), expected)
        self.assertFalse(decision.reuse)
        self.assertIn("PRODUCER_UNSUCCESSFUL", codes(decision))

    def test_invalid_digest_fails_closed(self):
        existing = fixture("existing-canonical.json")
        existing["imageDigest"] = "md5:deadbeef"
        decision = candidate.should_reuse_image(existing, fixture("expected-canonical.json"))
        self.assertFalse(decision.reuse)
        self.assertIn("INVALID_DIGEST", codes(decision))

    def test_missing_existing_fails_closed(self):
        decision = candidate.should_reuse_image(None, fixture("expected-canonical.json"))
        self.assertFalse(decision.reuse)
        self.assertIn("MISSING_IMAGE", codes(decision))

    def test_invalid_expected_sha_fails_closed(self):
        expected = fixture("expected-canonical.json")
        expected["sha"] = "short"
        decision = candidate.should_reuse_image(fixture("existing-canonical.json"), expected)
        self.assertFalse(decision.reuse)
        self.assertIn("INVALID_EXPECTED", codes(decision))


class CanonicalSetTests(unittest.TestCase):
    def test_canonical_set_has_no_issues(self):
        issues = candidate.canonical_set_issues(fixture("backends-canonical.json"), sha=SHA)
        self.assertEqual(issues, [])

    def test_missing_backend_fails(self):
        backends = fixture("backends-canonical.json")
        backends["items"] = None
        issues = candidate.canonical_set_issues(backends, sha=SHA)
        self.assertTrue(any(i["code"] == "MISSING_BACKEND" for i in issues))

    def test_split_producer_set_fails(self):
        backends = fixture("backends-canonical.json")
        backends["apiGateway"]["labels"][rc.PRODUCER_RUN_ID] = "424242"
        issues = candidate.canonical_set_issues(backends, sha=SHA)
        self.assertTrue(any(i["code"] == "PRODUCER_SET_SPLIT" for i in issues))

    def test_items_common_revision_mismatch_fails(self):
        backends = fixture("backends-canonical.json")
        backends["items"]["labels"][rc.COMMON_REVISION_LABEL] = "0" * 40
        issues = candidate.canonical_set_issues(backends, sha=SHA)
        self.assertTrue(any(i["code"] == "COMMON_REVISION_MISMATCH" for i in issues))

    def test_missing_producer_run_attempt_fails(self):
        backends = fixture("backends-canonical.json")
        del backends["apiGateway"]["labels"][rc.PRODUCER_RUN_ATTEMPT]
        issues = candidate.canonical_set_issues(backends, sha=SHA)
        self.assertTrue(any(i["code"] == "PRODUCER_IDENTITY_MISSING" for i in issues))


class CandidateManifestTests(unittest.TestCase):
    def _build(self, version="1.2.1"):
        context = fixture("context-canonical.json")
        context["version"] = version
        return candidate.build_candidate_manifest(context, fixture("components-canonical.json"))

    def test_builds_schema_valid_candidate_matching_fixture(self):
        manifest = self._build()
        with open(
            os.path.join(RELEASE_ROOT, "fixtures", "valid", "candidate-v1.2.1.json"),
            encoding="utf-8",
        ) as handle:
            expected = json.load(handle)
        self.assertEqual(manifest, expected)

    def test_manifest_is_schema_valid(self):
        manifest = self._build()
        result = validate_data(manifest)
        self.assertTrue(result.valid, msg=json.dumps(result.issues))
        self.assertEqual(manifest["release"]["gitTag"], "v1.2.1")

    def test_invalid_version_rejected(self):
        with self.assertRaises(ValueError):
            self._build(version="1.2.1-beta")


if __name__ == "__main__":
    unittest.main()
