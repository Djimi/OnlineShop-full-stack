"""Unit tests for release traceability lookups and the consistency audit
(Pass 3, subphase 3.7)."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import traceability as tr

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX = os.path.join(RELEASE_ROOT, "fixtures", "traceability")

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
SHA_OLD = "deadbeefcafebabe1234567890abcdef12345678"
AUTH = "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
ITEMS = "sha256:1a78d43d2aaaca3cafe41539f5fe211f4ef45152a1fa6af5b85006e93b019452"
GATEWAY = "sha256:31f9bfdf4d15f460167ba2b7ce9ada23278be1a9f115f06c01e5242901e8948e"
FRONT = "b9debb6b25ee6e6e534f7738d27f53f4153dbf361f097336741ae9fb54939ee4"
UNKNOWN_SHA = "0" * 40


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as handle:
        return json.load(handle)


def codes(result):
    return [issue["code"] for issue in result.issues]


def load_index():
    return fixture("index.json")


class ValidateIndexTests(unittest.TestCase):
    def test_valid_index(self):
        self.assertEqual(tr.validate_index(load_index()), [])

    def test_malformed_index(self):
        self.assertEqual(
            [i["code"] for i in tr.validate_index({"manifests": "x"})], ["INVALID_INDEX"]
        )
        self.assertEqual([i["code"] for i in tr.validate_index(None)], ["INVALID_INDEX"])

    def test_duplicate_manifest_rejected(self):
        index = load_index()
        index["manifests"].append(json.loads(json.dumps(index["manifests"][0])))
        self.assertIn("INDEX_DUPLICATE_MANIFEST", [i["code"] for i in tr.validate_index(index)])

    def test_invalid_manifest_rejected(self):
        index = load_index()
        bad = json.loads(json.dumps(index["manifests"][0]))
        bad["release"]["version"] = "not-semver"
        index["manifests"].append(bad)
        self.assertIn("INDEX_MANIFEST_INVALID", [i["code"] for i in tr.validate_index(index)])


class ByShaTests(unittest.TestCase):
    def setUp(self):
        self.index = load_index()
        self.observed = fixture("observed-ok.json")

    def test_finds_digests_and_official_releases(self):
        result = tr.lookup_by_sha(self.index, self.observed, SHA)
        self.assertTrue(result.valid)
        self.assertTrue(result.found)
        self.assertEqual(result.data["digests"]["auth"]["imageDigest"], AUTH)
        self.assertEqual(result.data["digests"]["items"]["imageDigest"], ITEMS)
        self.assertEqual(result.data["digests"]["apiGateway"]["imageDigest"], GATEWAY)
        self.assertEqual([r["version"] for r in result.data["officialReleases"]], ["1.2.1"])
        self.assertEqual(result.data["candidateRun"]["runId"], 123456789)

    def test_unknown_sha_not_found(self):
        result = tr.lookup_by_sha(self.index, self.observed, UNKNOWN_SHA)
        self.assertFalse(result.valid)
        self.assertFalse(result.found)
        self.assertIn("NOT_FOUND", codes(result))

    def test_invalid_sha(self):
        result = tr.lookup_by_sha(self.index, self.observed, "short")
        self.assertIn("INVALID_SHA", codes(result))

    def test_sha_in_index_but_ecr_tag_missing(self):
        observed = json.loads(json.dumps(self.observed))
        for repo in ("onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"):
            observed["ecr"][repo]["images"][0]["imageTags"] = ["release-1.2.1"]
        result = tr.lookup_by_sha(self.index, observed, SHA)
        self.assertIn("ECR_SHA_TAG_MISSING", codes(result))
        self.assertFalse(result.valid)


class ByVersionTests(unittest.TestCase):
    def setUp(self):
        self.index = load_index()
        self.observed = fixture("observed-ok.json")

    def test_finds_manifest_and_verifies_live(self):
        result = tr.lookup_by_version(self.index, self.observed, "1.2.1")
        self.assertTrue(result.valid)
        self.assertTrue(result.found)
        self.assertEqual(result.data["sourceSha"], SHA)
        self.assertEqual(result.data["status"], "official")
        self.assertTrue(result.data["live"]["ecrVerified"])
        self.assertTrue(result.data["live"]["frontendMarkerVerified"])
        self.assertEqual(
            sorted(result.data["components"].keys()),
            ["apiGateway", "auth", "frontend", "items"],
        )

    def test_old_release_still_resolvable(self):
        result = tr.lookup_by_version(self.index, self.observed, "1.1.0")
        self.assertTrue(result.valid)
        self.assertEqual(result.data["sourceSha"], SHA_OLD)

    def test_not_found(self):
        result = tr.lookup_by_version(self.index, self.observed, "9.9.9")
        self.assertFalse(result.found)
        self.assertIn("RELEASE_NOT_FOUND", codes(result))

    def test_invalid_version(self):
        result = tr.lookup_by_version(self.index, self.observed, "1.2")
        self.assertIn("INVALID_VERSION", codes(result))

    def test_ambiguous_version(self):
        index = load_index()
        dup = json.loads(json.dumps(index["manifests"][1]))
        dup["release"]["status"] = "candidate"
        index["manifests"].append(dup)
        result = tr.lookup_by_version(index, self.observed, "1.1.0")
        self.assertIn("AMBIGUOUS_VERSION", codes(result))

    def test_ecr_release_tag_mismatch(self):
        observed = fixture("observed-drift-ecr.json")
        result = tr.lookup_by_version(self.index, observed, "1.2.1")
        self.assertIn("ECR_RELEASE_DIGEST_MISMATCH", codes(result))

    def test_ecr_release_tag_missing(self):
        observed = json.loads(json.dumps(self.observed))
        for repo in ("onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"):
            observed["ecr"][repo]["images"] = [
                img
                for img in observed["ecr"][repo]["images"]
                if "release-1.2.1" not in (img.get("imageTags") or [])
            ]
        result = tr.lookup_by_version(self.index, observed, "1.2.1")
        self.assertIn("ECR_RELEASE_TAG_MISSING", codes(result))

    def test_frontend_marker_mismatch(self):
        observed = fixture("observed-drift-frontend.json")
        result = tr.lookup_by_version(self.index, observed, "1.2.1")
        self.assertIn("FRONTEND_MARKER_MISMATCH", codes(result))


class RunningTests(unittest.TestCase):
    def setUp(self):
        self.index = load_index()

    def test_running_matches_official_release(self):
        result = tr.lookup_running(self.index, fixture("observed-ok.json"))
        self.assertTrue(result.valid)
        self.assertFalse(result.data["paused"])
        self.assertEqual(result.data["runningDigests"]["auth"], AUTH)
        self.assertEqual(result.data["releaseIdentity"]["version"], "1.2.1")
        self.assertEqual(result.data["releaseIdentity"]["approver"], "djimi")
        self.assertEqual(result.data["releaseIdentity"]["deploymentRunId"], 123456790)
        self.assertEqual(
            result.data["releaseIdentity"]["componentIdentities"],
            ["api-gateway/1.2.1", "auth/1.2.1", "frontend/1.2.1", "items/1.2.1"],
        )

    def test_running_uses_container_image_digest(self):
        result = tr.lookup_running(self.index, fixture("observed-ok.json"))
        for task in result.data["runningTasks"]:
            for container in task["containers"]:
                self.assertTrue(container["imageDigest"].startswith("sha256:"))

    def test_paused_reports_state_and_td_digests(self):
        result = tr.lookup_running(self.index, fixture("observed-paused.json"))
        self.assertTrue(result.valid)
        self.assertTrue(result.data["paused"])
        self.assertEqual(result.data["lastVerifiedDeployment"]["version"], "1.2.1")
        self.assertEqual(result.data["lastVerifiedDeployment"]["approver"], "djimi")
        for entry in result.data["taskDefinitions"].values():
            self.assertTrue(entry["imageDigest"].startswith("sha256:"))
        # A paused environment must never fabricate a running digest.
        self.assertNotIn("runningDigests", result.data)

    def test_running_unmatched_fails_closed(self):
        result = tr.lookup_running(self.index, fixture("observed-drift-ecs.json"))
        self.assertIn("RUNNING_DIGEST_UNMATCHED", codes(result))

    def test_running_frontend_mismatch_fails_closed(self):
        result = tr.lookup_running(self.index, fixture("observed-drift-frontend.json"))
        self.assertIn("FRONTEND_RUNNING_MISMATCH", codes(result))

    def test_running_ambiguous(self):
        index = load_index()
        duplicate = json.loads(json.dumps(index["manifests"][0]))
        duplicate["release"]["version"] = "1.2.2"
        duplicate["release"]["gitTag"] = "v1.2.2"
        duplicate["release"]["sourceSha"] = "f" * 40
        for key, comp in duplicate["components"].items():
            comp["sourceSha"] = "f" * 40
            if "commonSourceSha" in comp:
                comp["commonSourceSha"] = "f" * 40
            comp["identity"] = comp["identity"].replace("1.2.1", "1.2.2")
            if key == "frontend":
                comp["releasePrefix"] = comp["releasePrefix"].replace("1.2.1", "1.2.2")
            else:
                comp["candidateTag"] = f"sha-{'f' * 40}"
                comp["releaseTag"] = "release-1.2.2"
        # Two official releases claim the exact same backend digests; the
        # running environment therefore matches both -> ambiguous.
        index["manifests"].append(duplicate)
        result = tr.lookup_running(index, fixture("observed-ok.json"))
        self.assertIn("RUNNING_AMBIGUOUS", codes(result))
        # The ambiguity is the only reason for failure (the duplicate is valid).
        self.assertEqual(codes(result), ["RUNNING_AMBIGUOUS"])


class ByDigestTests(unittest.TestCase):
    def setUp(self):
        self.index = load_index()
        self.observed = fixture("observed-ok.json")

    def test_finds_tags_revision_and_release(self):
        result = tr.lookup_by_digest(self.index, self.observed, AUTH)
        self.assertTrue(result.valid)
        self.assertTrue(result.found)
        self.assertEqual(result.data["ociRevision"], SHA)
        self.assertEqual([r["version"] for r in result.data["releaseIdentity"]], ["1.2.1"])
        auth_matches = [e for e in result.data["ecr"] if e["repository"] == "onlineshop-auth"]
        self.assertTrue(any("release-1.2.1" in e["tags"] for e in auth_matches))
        self.assertTrue(any(f"sha-{SHA}" in e["tags"] for e in auth_matches))

    def test_old_digest_resolves_to_old_release(self):
        result = tr.lookup_by_digest(
            self.index,
            self.observed,
            "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        )
        self.assertTrue(result.valid)
        self.assertEqual([r["version"] for r in result.data["releaseIdentity"]], ["1.1.0"])

    def test_not_found(self):
        result = tr.lookup_by_digest(
            self.index,
            self.observed,
            "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        )
        self.assertFalse(result.found)
        self.assertIn("NOT_FOUND", codes(result))

    def test_invalid_digest(self):
        result = tr.lookup_by_digest(self.index, self.observed, "sha256:xyz")
        self.assertIn("INVALID_DIGEST", codes(result))

    def test_ambiguous_digest_across_releases(self):
        index = load_index()
        # Make the 1.1.0 auth digest identical to the 1.2.1 auth digest, but
        # keep a different source SHA -> the digest maps to two releases.
        index["manifests"][1]["components"]["auth"]["imageDigest"] = AUTH
        result = tr.lookup_by_digest(index, self.observed, AUTH)
        self.assertIn("AMBIGUOUS_DIGEST", codes(result))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.index = load_index()

    def test_consistent_environment_passes(self):
        result = tr.audit_consistency(self.index, fixture("observed-ok.json"))
        self.assertTrue(result.valid)
        self.assertEqual([r["version"] for r in result.data["audited"]], ["1.2.1", "1.1.0"])
        self.assertTrue(all(r["valid"] for r in result.data["audited"]))

    def test_paused_environment_passes(self):
        result = tr.audit_consistency(self.index, fixture("observed-paused.json"))
        self.assertTrue(result.valid)
        self.assertTrue(all(r["checks"]["ecs"] is None for r in result.data["audited"]))

    def test_ecs_drift_fails_closed(self):
        result = tr.audit_consistency(self.index, fixture("observed-drift-ecs.json"))
        self.assertIn("RUNNING_DIGEST_UNMATCHED", codes(result))

    def test_frontend_drift_fails_closed(self):
        result = tr.audit_consistency(self.index, fixture("observed-drift-frontend.json"))
        self.assertIn("FRONTEND_MARKER_MISMATCH", codes(result))

    def test_ecr_drift_fails_closed(self):
        result = tr.audit_consistency(self.index, fixture("observed-drift-ecr.json"))
        self.assertIn("ECR_RELEASE_DIGEST_MISMATCH", codes(result))
        # Only the drifted release is flagged for ECR.
        auth_issue = [
            issue for issue in result.issues if issue["code"] == "ECR_RELEASE_DIGEST_MISMATCH"
        ]
        self.assertIn("ecr.onlineshop-auth.release-1.2.1", auth_issue[0]["field"])

    def test_version_filter(self):
        result = tr.audit_consistency(self.index, fixture("observed-ok.json"), version="1.2.1")
        self.assertTrue(result.valid)
        self.assertEqual([r["version"] for r in result.data["audited"]], ["1.2.1"])

    def test_version_not_found(self):
        result = tr.audit_consistency(self.index, fixture("observed-ok.json"), version="9.9.9")
        self.assertIn("RELEASE_NOT_FOUND", codes(result))


class OrderingTests(unittest.TestCase):
    """Newest-official selection and newest-first audit ordering must not depend
    on manifest index order (compare_semver returns only a sign, so it cannot
    order more than two distinct versions)."""

    def _reversed_index(self):
        index = load_index()
        index["manifests"] = list(reversed(index["manifests"]))
        return index

    def test_latest_official_ignores_index_order(self):
        self.assertEqual(tr._latest_official(self._reversed_index())["release"]["version"], "1.2.1")

    def test_audit_newest_first_ignores_index_order(self):
        result = tr.audit_consistency(self._reversed_index(), fixture("observed-ok.json"))
        self.assertTrue(result.valid)
        self.assertEqual([r["version"] for r in result.data["audited"]], ["1.2.1", "1.1.0"])

    def test_paused_last_verified_uses_newest_not_first(self):
        result = tr.lookup_running(self._reversed_index(), fixture("observed-paused.json"))
        self.assertTrue(result.valid)
        self.assertEqual(result.data["lastVerifiedDeployment"]["version"], "1.2.1")


class ByShaDriftTests(unittest.TestCase):
    def setUp(self):
        self.index = load_index()
        self.observed = fixture("observed-ok.json")

    def test_sha_tag_digest_mismatch_fails_closed(self):
        observed = json.loads(json.dumps(self.observed))
        observed["ecr"]["onlineshop-auth"]["images"][0]["imageDigest"] = "sha256:" + "e" * 64
        result = tr.lookup_by_sha(self.index, observed, SHA)
        self.assertIn("ECR_SHA_DIGEST_MISMATCH", codes(result))
        self.assertFalse(result.valid)

    def test_candidate_run_conflict_fails_closed(self):
        index = load_index()
        conflicting = json.loads(json.dumps(index["manifests"][0]))
        conflicting["release"]["candidateWorkflow"]["runId"] = 555666777
        conflicting["release"]["candidateWorkflow"]["url"] = (
            "https://github.com/Djimi/OnlineShop-full-stack/actions/runs/555666777/attempts/1"
        )
        conflicting["release"]["status"] = "candidate"
        conflicting["release"].pop("promotionWorkflow", None)
        for comp in conflicting["components"].values():
            comp.pop("taskDefinitionArn", None)
        index["manifests"].append(conflicting)
        result = tr.lookup_by_sha(index, self.observed, SHA)
        self.assertIn("CANDIDATE_RUN_CONFLICT", codes(result))
        self.assertFalse(result.valid)


class ByVersionFrontendPrefixTests(unittest.TestCase):
    def setUp(self):
        self.index = load_index()

    def _remove_prefix_marker(self, observed, version="1.2.1"):
        key = f"_releases/v{version}/release.json"
        observed = json.loads(json.dumps(observed))
        observed["frontend"]["prefixMarkers"][key] = {
            "exists": False,
            "marker": None,
        }
        return observed

    def test_prefix_marker_missing_fails_closed(self):
        observed = self._remove_prefix_marker(fixture("observed-ok.json"))
        result = tr.lookup_by_version(self.index, observed, "1.2.1")
        self.assertIn("FRONTEND_PREFIX_MARKER_MISSING", codes(result))
        self.assertFalse(result.valid)

    def test_prefix_marker_mismatch_fails_closed(self):
        observed = json.loads(json.dumps(fixture("observed-ok.json")))
        key = "_releases/v1.2.1/release.json"
        observed["frontend"]["prefixMarkers"][key]["marker"]["frontendSha256"] = "e" * 64
        result = tr.lookup_by_version(self.index, observed, "1.2.1")
        self.assertIn("FRONTEND_PREFIX_MARKER_MISMATCH", codes(result))
        self.assertFalse(result.valid)

    def test_old_release_prefix_marker_still_verified(self):
        result = tr.lookup_by_version(self.index, fixture("observed-ok.json"), "1.1.0")
        self.assertTrue(result.valid)
        self.assertTrue(result.data["live"]["frontendPrefixMarkerVerified"])

    def test_malformed_live_marker_does_not_crash(self):
        observed = json.loads(json.dumps(fixture("observed-ok.json")))
        observed["frontend"]["liveMarker"] = {"exists": True, "marker": "not-an-object"}
        result = tr.lookup_by_version(self.index, observed, "1.2.1")
        # The malformed marker is treated as a mismatch, not a crash.
        self.assertIn("FRONTEND_MARKER_MISMATCH", codes(result))
        self.assertFalse(result.valid)


class RunningDigestSetTests(unittest.TestCase):
    def setUp(self):
        self.index = load_index()

    def test_mixed_digests_fail_closed(self):
        observed = json.loads(json.dumps(fixture("observed-ok.json")))
        observed["ecs"]["running"].append(
            {
                "taskArn": "arn:aws:ecs:eu-north-1:799111666795:task/onlineshop-auth/xyz789",
                "taskDefinitionArn": (
                    "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:6"
                ),
                "lastStatus": "RUNNING",
                "containers": [{"name": "auth", "imageDigest": "sha256:" + "a" * 64}],
            }
        )
        result = tr.lookup_running(self.index, observed)
        self.assertIn("RUNNING_MIXED_DIGESTS", codes(result))
        self.assertFalse(result.valid)

    def test_incomplete_digests_fail_closed(self):
        observed = json.loads(json.dumps(fixture("observed-ok.json")))
        observed["ecs"]["running"] = [observed["ecs"]["running"][0]]
        result = tr.lookup_running(self.index, observed)
        self.assertIn("RUNNING_DIGEST_INCOMPLETE", codes(result))
        self.assertFalse(result.valid)

    def test_non_list_running_is_malformed_not_paused(self):
        observed = json.loads(json.dumps(fixture("observed-ok.json")))
        observed["ecs"]["running"] = {"not": "a list"}
        result = tr.lookup_running(self.index, observed)
        self.assertIn("INVALID_OBSERVED", codes(result))
        self.assertFalse(result.valid)

    def test_audit_non_list_running_fails_closed(self):
        observed = json.loads(json.dumps(fixture("observed-ok.json")))
        observed["ecs"]["running"] = {"not": "a list"}
        result = tr.audit_consistency(self.index, observed)
        self.assertIn("INVALID_OBSERVED", codes(result))
        self.assertFalse(result.valid)

    def test_audit_mixed_digests_fail_closed(self):
        observed = json.loads(json.dumps(fixture("observed-ok.json")))
        observed["ecs"]["running"].append(
            {
                "taskArn": "arn:aws:ecs:eu-north-1:799111666795:task/onlineshop-auth/xyz789",
                "taskDefinitionArn": (
                    "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:6"
                ),
                "lastStatus": "RUNNING",
                "containers": [{"name": "auth", "imageDigest": "sha256:" + "b" * 64}],
            }
        )
        result = tr.audit_consistency(self.index, observed)
        self.assertIn("RUNNING_MIXED_DIGESTS", codes(result))
        self.assertFalse(result.valid)


class ByDigestAttributionTests(unittest.TestCase):
    def test_oci_revision_is_manifest_attributed(self):
        result = tr.lookup_by_digest(load_index(), fixture("observed-ok.json"), AUTH)
        self.assertTrue(result.valid)
        self.assertEqual(result.data["ociRevision"], SHA)
        self.assertEqual(result.data["ociRevisionSource"], "release-manifest")
        self.assertFalse(result.data["ociRevisionObservedFromImage"])

    def test_no_release_no_oci_revision_claim(self):
        observed = json.loads(json.dumps(fixture("observed-ok.json")))
        observed["ecr"]["onlineshop-auth"]["images"].append(
            {
                "imageDigest": "sha256:" + "5" * 64,
                "imagePushedAt": "2026-08-04T15:00:00Z",
                "imageTags": ["not-a-release-tag"],
            }
        )
        result = tr.lookup_by_digest(
            load_index(),
            observed,
            "sha256:" + "5" * 64,
        )
        # The digest resolves in ECR but to no indexed release: no revision claim.
        self.assertTrue(result.valid)
        self.assertTrue(result.found)
        self.assertIsNone(result.data["ociRevision"])
        self.assertIsNone(result.data["ociRevisionSource"])


class PartialApiErrorTests(unittest.TestCase):
    def test_missing_service_read_marker_fails_closed(self):
        index = load_index()
        observed = fixture("observed-ok.json")
        observed["ecs"]["services"]["onlineshop-auth"] = {
            "taskDefinition": None,
            "error": "service not returned by describe-services",
        }
        result = tr.lookup_running(index, observed)
        self.assertIn("OBSERVED_READ_ERROR", codes(result))
        self.assertFalse(result.valid)

    def test_malformed_prefix_marker_fails_closed(self):
        index = load_index()
        observed = json.loads(json.dumps(fixture("observed-ok.json")))
        key = "_releases/v1.2.1/release.json"
        observed["frontend"]["prefixMarkers"][key] = {
            "exists": True,
            "marker": "not-an-object",
            "error": "prefix release.json marker is not a JSON object",
        }
        result = tr.lookup_by_version(index, observed, "1.2.1")
        self.assertIn("OBSERVED_READ_ERROR", codes(result))
        self.assertFalse(result.valid)


class ReadErrorTests(unittest.TestCase):
    def test_observed_read_error_fails_closed(self):
        index = load_index()
        observed = fixture("observed-ok.json")
        observed["ecr"]["onlineshop-auth"]["error"] = "AccessDenied while reading images"
        result = tr.lookup_by_sha(index, observed, SHA)
        self.assertIn("OBSERVED_READ_ERROR", codes(result))
        self.assertFalse(result.valid)


class CliTests(unittest.TestCase):
    def _cli(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(RELEASE_ROOT, "src")
        return subprocess.run(
            [sys.executable, "-m", "release_contract.traceability", *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def _common(self, *extra):
        return [
            *extra,
            "--index",
            os.path.join(FX, "index.json"),
            "--observed",
            os.path.join(FX, "observed-ok.json"),
        ]

    def test_by_version_exit_zero(self):
        result = self._cli("by-version", "1.2.1", *self._common())
        self.assertEqual(result.returncode, 0)
        body = json.loads(result.stdout)
        self.assertTrue(body["valid"])

    def test_running_exit_zero(self):
        result = self._cli("running", *self._common())
        self.assertEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stdout)["valid"])

    def test_audit_exit_zero(self):
        result = self._cli("audit", *self._common())
        self.assertEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stdout)["valid"])

    def test_drift_audit_exit_one(self):
        result = self._cli(
            "audit",
            "--index",
            os.path.join(FX, "index.json"),
            "--observed",
            os.path.join(FX, "observed-drift-ecr.json"),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ECR_RELEASE_DIGEST_MISMATCH", result.stdout)

    def test_not_found_exit_one(self):
        result = self._cli("by-version", "9.9.9", *self._common())
        self.assertEqual(result.returncode, 1)

    def test_human_view_goes_to_stderr(self):
        result = self._cli("by-version", "1.2.1", *self._common(), "--human")
        self.assertEqual(result.returncode, 0)
        self.assertIn("lookup succeeded", result.stderr)

    def test_missing_index_usage_error(self):
        result = self._cli(
            "by-version",
            "1.2.1",
            "--index",
            "/nonexistent/index.json",
            "--observed",
            os.path.join(FX, "observed-ok.json"),
        )
        self.assertEqual(result.returncode, 2)

    def test_invalid_index_json_usage_error(self):
        bad = os.path.join(RELEASE_ROOT, "fixtures", "traceability", "not-json.tmp")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        try:
            result = self._cli(
                "by-version",
                "1.2.1",
                "--index",
                bad,
                "--observed",
                os.path.join(FX, "observed-ok.json"),
            )
        finally:
            os.remove(bad)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid JSON", result.stderr)


if __name__ == "__main__":
    unittest.main()
