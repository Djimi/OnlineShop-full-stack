"""End-to-end validation tests: fixtures, schema states, cross-field rules."""

import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

import release_contract.validate as validate
from release_contract import crossrules

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(RELEASE_ROOT, "fixtures")
VALID_DIR = os.path.join(FIXTURES, "valid")
INVALID_DIR = os.path.join(FIXTURES, "invalid")
EXPECTED_TABLE = os.path.join(INVALID_DIR, "EXPECTED.md")

SOURCE_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"


def load_valid(name):
    with open(os.path.join(VALID_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def parse_expected_table():
    """Parse the authoritative fixture -> primary-code table from EXPECTED.md."""
    expected = {}
    table = False
    with open(EXPECTED_TABLE, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped.startswith("| Fixture |"):
                table = True
                continue
            if not table or not stripped.startswith("| `"):
                continue
            columns = [column.strip() for column in stripped.strip("|").split("|")]
            if len(columns) < 2:
                continue
            fixture = columns[0].strip("`")
            code = columns[1].strip().strip("`")
            expected[fixture] = code
    return expected


class ValidFixtureTests(unittest.TestCase):
    def test_every_valid_fixture_is_accepted(self):
        fixtures = sorted(name for name in os.listdir(VALID_DIR) if name.endswith(".json"))
        self.assertTrue(fixtures, "no valid fixtures found")
        for name in fixtures:
            with self.subTest(fixture=name):
                result = validate.validate_file(os.path.join(VALID_DIR, name))
                self.assertTrue(result.valid, f"{name} rejected: {result.issues}")
                self.assertIsNotNone(result.checksum)
                self.assertEqual(len(result.issues), 0)


class InvalidFixtureTests(unittest.TestCase):
    def test_every_invalid_fixture_fails_with_expected_code(self):
        expected = parse_expected_table()
        self.assertTrue(expected, "expected-code table is empty")
        for name, primary_code in sorted(expected.items()):
            path = os.path.join(INVALID_DIR, name)
            self.assertTrue(os.path.isfile(path), f"missing fixture file for {name}")
            with self.subTest(fixture=name):
                result = validate.validate_file(path)
                self.assertFalse(result.valid, f"{name} should be rejected")
                codes = [issue["code"] for issue in result.issues]
                self.assertIn(
                    primary_code,
                    codes,
                    f"{name} primary error {primary_code} missing from {codes}",
                )

    def test_all_invalid_fixtures_are_listed_in_table(self):
        expected = parse_expected_table()
        on_disk = {name for name in os.listdir(INVALID_DIR) if name.endswith(".json")}
        documented = set(expected)
        self.assertEqual(
            on_disk,
            documented,
            "fixtures and EXPECTED.md table must not drift",
        )


class DeterminismTests(unittest.TestCase):
    def test_invalid_result_is_deterministic(self):
        base = load_valid("candidate-v1.2.1.json")
        base["release"]["version"] = "1.2"
        first = validate.validate_data(copy.deepcopy(base))
        second = validate.validate_data(copy.deepcopy(base))
        self.assertEqual(first.issues, second.issues)
        self.assertEqual(first.valid, second.valid)

    def test_valid_result_is_deterministic(self):
        base = load_valid("official-v1.2.1.json")
        first = validate.validate_data(copy.deepcopy(base))
        second = validate.validate_data(copy.deepcopy(base))
        self.assertEqual(first.issues, second.issues)


class SchemaStateTests(unittest.TestCase):
    def test_unsupported_schema_version_rejected(self):
        base = load_valid("candidate-v1.2.1.json")
        base["schemaVersion"] = 2
        result = validate.validate_data(base)
        self.assertFalse(result.valid)
        self.assertIn("CONST_MISMATCH", [issue["code"] for issue in result.issues])

    def test_candidate_may_not_contain_promotion_workflow(self):
        base = load_valid("candidate-v1.2.1.json")
        base["release"]["promotionWorkflow"] = {
            "runId": 1,
            "actor": "djimi",
            "approvedBy": "djimi",
            "approvedAt": "2026-08-04T14:00:00Z",
            "deployedAt": "2026-08-04T14:30:00Z",
        }
        result = validate.validate_data(base)
        self.assertFalse(result.valid)
        codes = [issue["code"] for issue in result.issues]
        self.assertIn("EXTRA_FIELD", codes)
        fields = [issue["field"] for issue in result.issues]
        self.assertIn("release.promotionWorkflow", fields)

    def test_candidate_may_not_contain_task_definition_arn(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["auth"]["taskDefinitionArn"] = (
            "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5"
        )
        result = validate.validate_data(base)
        self.assertFalse(result.valid)
        codes = [issue["code"] for issue in result.issues]
        self.assertIn("EXTRA_FIELD", codes)

    def test_official_requires_promotion_workflow(self):
        base = load_valid("official-v1.2.1.json")
        del base["release"]["promotionWorkflow"]
        result = validate.validate_data(base)
        self.assertFalse(result.valid)
        self.assertIn("MISSING_FIELD", [issue["code"] for issue in result.issues])

    def test_official_requires_task_definition_arn(self):
        base = load_valid("official-v1.2.1.json")
        del base["components"]["auth"]["taskDefinitionArn"]
        result = validate.validate_data(base)
        self.assertFalse(result.valid)
        self.assertIn("MISSING_FIELD", [issue["code"] for issue in result.issues])
        self.assertTrue(
            any(issue["field"] == "components.auth.taskDefinitionArn" for issue in result.issues)
        )

    def test_state_transition_is_explicit(self):
        # Flipping status without promotion evidence must fail; adding the
        # promotion evidence is the only path to official.
        base = load_valid("candidate-v1.2.1.json")
        base["release"]["status"] = "official"
        self.assertFalse(validate.validate_data(base).valid)

        official = load_valid("official-v1.2.1.json")
        self.assertTrue(validate.validate_data(official).valid)


class UnsafeInputTests(unittest.TestCase):
    def test_control_character_in_string_rejected(self):
        base = load_valid("candidate-v1.2.1.json")
        base["release"]["sourceSha"] = SOURCE_SHA
        base["release"]["repository"] = "Djimi/OnlineShop-full-stack\x00"
        result = validate.validate_data(base)
        self.assertFalse(result.valid)
        codes = [issue["code"] for issue in result.issues]
        self.assertIn("UNSAFE_CHARACTER", codes)

    def test_control_character_in_object_key_rejected(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["evil\x00key"] = {"identity": "auth/1.2.1"}
        result = validate.validate_data(base)
        self.assertFalse(result.valid)
        self.assertIn("UNSAFE_CHARACTER", [issue["code"] for issue in result.issues])


class CrossFieldTests(unittest.TestCase):
    def test_identity_mismatch(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["auth"]["identity"] = "auth/9.9.9"
        self.assert_cross_issue(base, "IDENTITY_MISMATCH")

    def test_component_sha_mismatch(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["items"]["sourceSha"] = "f" * 40
        self.assert_cross_issue(base, "SHA_MISMATCH")

    def test_common_source_sha_mismatch(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["items"]["commonSourceSha"] = "f" * 40
        self.assert_cross_issue(base, "SHA_MISMATCH")

    def test_git_tag_mismatch(self):
        base = load_valid("candidate-v1.2.1.json")
        base["release"]["gitTag"] = "v1.2.2"
        self.assert_cross_issue(base, "GIT_TAG_MISMATCH")

    def test_repository_mismatch(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["auth"]["repository"] = "onlineshop-evil"
        self.assert_cross_issue(base, "REPOSITORY_MISMATCH")

    def test_candidate_tag_mismatch(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["apiGateway"]["candidateTag"] = "sha-" + "f" * 40
        self.assert_cross_issue(base, "CANDIDATE_TAG_MISMATCH")

    def test_release_tag_mismatch(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["auth"]["releaseTag"] = "release-1.2.2"
        self.assert_cross_issue(base, "RELEASE_TAG_MISMATCH")

    def test_release_prefix_mismatch(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["frontend"]["releasePrefix"] = "_releases/v1.2.2/"
        self.assert_cross_issue(base, "RELEASE_PREFIX_MISMATCH")

    def test_sbom_mismatch_direct_rule(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["auth"]["sbom"] = "evil.spdx.json"
        issues = crossrules.apply_cross_field_rules(base)
        codes = [issue["code"] for issue in issues]
        self.assertIn("SBOM_MISMATCH", codes)

        result = validate.validate_data(copy.deepcopy(base))
        self.assertFalse(result.valid)
        self.assertIn("INVALID_FORMAT", [issue["code"] for issue in result.issues])

    def test_artifact_mismatch_direct_rule(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["frontend"]["artifact"] = "evil.tar.gz"
        issues = crossrules.apply_cross_field_rules(base)
        codes = [issue["code"] for issue in issues]
        self.assertIn("ARTIFACT_MISMATCH", codes)

    def assert_cross_issue(self, manifest, code):
        result = validate.validate_data(manifest)
        self.assertFalse(result.valid)
        self.assertIn(code, [issue["code"] for issue in result.issues])


class NoNewFieldsTests(unittest.TestCase):
    def test_unknown_manifest_field_rejected(self):
        base = load_valid("candidate-v1.2.1.json")
        base["buildRun"] = {"id": 1}
        result = validate.validate_data(base)
        self.assertFalse(result.valid)
        self.assertIn("EXTRA_FIELD", [issue["code"] for issue in result.issues])

    def test_unknown_component_field_rejected(self):
        base = load_valid("candidate-v1.2.1.json")
        base["components"]["auth"]["buildRun"] = 123
        result = validate.validate_data(base)
        self.assertFalse(result.valid)
        self.assertIn("EXTRA_FIELD", [issue["code"] for issue in result.issues])


class LoadTests(unittest.TestCase):
    def test_invalid_json_reported_as_invalid_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{broken json")
            path = handle.name
        try:
            result = validate.validate_file(path)
            self.assertFalse(result.valid)
            self.assertIn("INVALID_JSON", [issue["code"] for issue in result.issues])
        finally:
            os.unlink(path)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            validate.load_manifest("/nonexistent/release-manifest.json")


class ChecksumIntegrationTests(unittest.TestCase):
    def test_result_exposes_stable_checksum(self):
        base = load_valid("official-v1.2.1.json")
        result = validate.validate_data(base)
        self.assertTrue(result.valid)
        self.assertEqual(len(result.checksum), 64)

    def test_checksum_detects_alteration(self):
        base = load_valid("official-v1.2.1.json")
        pristine = validate.validate_data(copy.deepcopy(base))
        base["release"]["deployedAt"] = "2026-08-05T00:00:00Z"
        altered = validate.validate_data(copy.deepcopy(base))
        self.assertNotEqual(pristine.checksum, altered.checksum)


if __name__ == "__main__":
    unittest.main()
