"""Unit tests for GitHub artifact identity resolution and bundle verification."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import artifact, checksums

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "artifact")
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
RUN_ID = 123456789
NAME = f"candidate-evidence-{SHA}-1"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


class ArtifactNameTests(unittest.TestCase):
    def test_name_round_trip(self):
        name = artifact.artifact_name_for(SHA, 1)
        self.assertEqual(name, NAME)
        parsed = artifact.parse_artifact_name(name)
        self.assertEqual(parsed["sourceSha"], SHA)
        self.assertEqual(parsed["runAttempt"], "1")

    def test_invalid_names_rejected(self):
        self.assertIsNone(artifact.parse_artifact_name("frontend-dist.tar.gz"))
        self.assertIsNone(artifact.parse_artifact_name("candidate-evidence-short-1"))
        self.assertIsNone(artifact.parse_artifact_name("candidate-evidence-{SHA}-0"))


class SelectArtifactTests(unittest.TestCase):
    def test_selects_single_matching(self):
        selected = artifact.select_artifact(fixture("artifacts-ok.json"), run_id=RUN_ID, name=NAME)
        self.assertEqual(selected["id"], 987)

    def test_duplicate_artifacts_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            artifact.select_artifact(fixture("artifacts-duplicate.json"), run_id=RUN_ID, name=NAME)
        self.assertIn("duplicate", str(ctx.exception))

    def test_expired_artifact_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            artifact.select_artifact(fixture("artifacts-expired.json"), run_id=RUN_ID, name=NAME)
        self.assertIn("expired", str(ctx.exception))

    def test_missing_artifact_rejected(self):
        with self.assertRaises(ValueError):
            artifact.select_artifact(fixture("artifacts-ok.json"), run_id=999, name=NAME)
        with self.assertRaises(ValueError):
            artifact.select_artifact(
                fixture("artifacts-ok.json"), run_id=RUN_ID, name="candidate-evidence-other-1"
            )


class ArtifactDigestTests(unittest.TestCase):
    def test_matching_digest_passes(self):
        sha = checksums.sha256_hex(b"artifact-bytes")
        self.assertTrue(artifact.verify_artifact_digest(sha, sha))

    def test_mismatched_digest_fails(self):
        recorded = checksums.sha256_hex(b"artifact-bytes")
        downloaded = checksums.sha256_hex(b"tampered-bytes")
        self.assertFalse(artifact.verify_artifact_digest(recorded, downloaded))

    def test_empty_recorded_digest_fails(self):
        self.assertFalse(artifact.verify_artifact_digest("", checksums.sha256_hex(b"x")))


class BundleVerificationTests(unittest.TestCase):
    def _make_bundle(self):
        temp = tempfile.mkdtemp()
        with open(os.path.join(temp, "candidate-evidence.json"), "w", encoding="utf-8") as handle:
            json.dump({"runId": RUN_ID}, handle)
        with open(os.path.join(temp, "frontend-dist.tar.gz"), "wb") as handle:
            handle.write(b"fake-archive")
        with open(os.path.join(temp, "frontend-dist.sha256"), "w", encoding="utf-8") as handle:
            handle.write("")
        for sbom in (
            "auth.spdx.json",
            "items.spdx.json",
            "api-gateway.spdx.json",
            "frontend.spdx.json",
        ):
            with open(os.path.join(temp, sbom), "w", encoding="utf-8") as handle:
                handle.write("{}")
        pairs = [
            (checksums.sha256_hex(b"fake-archive"), "frontend-dist.tar.gz"),
            (checksums.sha256_hex(b"{}"), "auth.spdx.json"),
            (checksums.sha256_hex(b"{}"), "items.spdx.json"),
            (checksums.sha256_hex(b"{}"), "api-gateway.spdx.json"),
            (checksums.sha256_hex(b"{}"), "frontend.spdx.json"),
        ]
        lines = [f"{sha}  {path}" for sha, path in sorted(pairs, key=lambda pair: pair[1])]
        with open(os.path.join(temp, "checksums.txt"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return temp

    def test_valid_bundle_passes(self):
        bundle = self._make_bundle()
        try:
            ok, issues = artifact.verify_evidence_bundle(
                bundle, expected_frontend_sha256=checksums.sha256_hex(b"fake-archive")
            )
            self.assertTrue(ok, msg=json.dumps(issues))
        finally:
            shutil.rmtree(bundle)

    def test_missing_file_fails(self):
        bundle = self._make_bundle()
        try:
            os.remove(os.path.join(bundle, "auth.spdx.json"))
            ok, issues = artifact.verify_evidence_bundle(bundle)
            self.assertFalse(ok)
            self.assertTrue(
                any(
                    i["code"] == "MISSING_BUNDLE_FILE" and i["field"] == "auth.spdx.json"
                    for i in issues
                )
            )
        finally:
            shutil.rmtree(bundle)

    def test_archive_checksum_mismatch_fails(self):
        bundle = self._make_bundle()
        try:
            with open(os.path.join(bundle, "frontend-dist.tar.gz"), "wb") as handle:
                handle.write(b"tampered")
            ok, issues = artifact.verify_evidence_bundle(
                bundle, expected_frontend_sha256=checksums.sha256_hex(b"fake-archive")
            )
            self.assertFalse(ok)
            self.assertTrue(any(i["code"] == "ARCHIVE_CHECKSUM_MISMATCH" for i in issues))
        finally:
            shutil.rmtree(bundle)

    def test_unsorted_checksums_fails(self):
        bundle = self._make_bundle()
        try:
            path = os.path.join(bundle, "checksums.txt")
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content.splitlines()[-1] + "\n" + content)
            ok, issues = artifact.verify_evidence_bundle(bundle)
            self.assertFalse(ok)
            self.assertTrue(any(i["code"] == "UNSORTED_CHECKSUMS" for i in issues))
        finally:
            shutil.rmtree(bundle)


if __name__ == "__main__":
    unittest.main()
