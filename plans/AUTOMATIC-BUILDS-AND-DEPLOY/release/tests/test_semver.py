"""Unit tests for canonical v1 SemVer validation and ordering."""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import semver


class ValidateTests(unittest.TestCase):
    def test_accepts_canonical_versions(self):
        versions = ("0.0.0", "0.1.0", "1.0.0", "1.2.3", "10.20.30", "999.0.1", "1234567890.0.0")
        for version in versions:
            valid, reason = semver.validate(version)
            self.assertTrue(valid, f"{version}: {reason}")
            self.assertIsNone(reason)

    def test_rejects_non_string(self):
        for value in (123, 1.5, None, ["1.2.3"], {"v": "1.2.3"}):
            valid, reason = semver.validate(value)
            self.assertFalse(valid)
            self.assertIn("string", reason)

    def test_rejects_empty_and_whitespace(self):
        for version in ("", "   ", "1.2.3 ", " 1.2.3", "1.2 .3", "1.2.3\t"):
            valid, _ = semver.validate(version)
            self.assertFalse(valid, f"{version!r} should be rejected")

    def test_rejects_leading_v(self):
        for version in ("v1.2.3", "V1.2.3"):
            valid, reason = semver.validate(version)
            self.assertFalse(valid)
            self.assertIn("leading 'v'", reason)

    def test_rejects_prerelease_and_build_metadata(self):
        valid, reason = semver.validate("1.2.3-rc.1")
        self.assertFalse(valid)
        self.assertIn("prerelease", reason)
        valid, reason = semver.validate("1.2.3+build.5")
        self.assertFalse(valid)
        self.assertIn("build metadata", reason)

    def test_rejects_leading_zeroes(self):
        for version in ("01.2.3", "1.02.3", "1.2.03"):
            valid, reason = semver.validate(version)
            self.assertFalse(valid)
            self.assertIn("leading zeroes", reason)

    def test_rejects_wrong_component_count(self):
        for version in ("1.2", "1.2.3.4", "1", "1.2.3.4.5"):
            valid, reason = semver.validate(version)
            self.assertFalse(valid)
            self.assertIn("three dot-separated", reason)

    def test_rejects_non_numeric_components(self):
        for version in ("1.2.x", "a.b.c", "1..3", "1.2.3-alpha.1", "1,2,3"):
            valid, _ = semver.validate(version)
            self.assertFalse(valid)

    def test_rejects_unsafe_shell_characters(self):
        for version in ("1.2.3;rm -rf /", "1.2.3$(id)", "1.2.3`id`", "1.2.3\n", "1.2.3\ttest"):
            valid, _ = semver.validate(version)
            self.assertFalse(valid, f"{version!r} must be rejected")

    def test_is_valid(self):
        self.assertTrue(semver.is_valid("1.2.3"))
        self.assertFalse(semver.is_valid("1.2.3-rc.1"))
        self.assertFalse(semver.is_valid(123))


class ParseTests(unittest.TestCase):
    def test_parse_returns_numeric_tuple(self):
        self.assertEqual(semver.parse("1.2.3"), (1, 2, 3))
        self.assertEqual(semver.parse("0.0.0"), (0, 0, 0))

    def test_parse_raises_on_invalid(self):
        with self.assertRaises(semver.SemVerError):
            semver.parse("1.2.3-beta")


class CompareTests(unittest.TestCase):
    def test_ordering(self):
        self.assertEqual(semver.compare("1.2.3", "1.2.3"), 0)
        self.assertEqual(semver.compare("1.2.3", "1.2.4"), -1)
        self.assertEqual(semver.compare("2.0.0", "1.9.9"), 1)
        self.assertEqual(semver.compare("0.9.9", "1.0.0"), -1)

    def test_compare_raises_on_invalid(self):
        with self.assertRaises(semver.SemVerError):
            semver.compare("1.2.3", "not-a-version")
        with self.assertRaises(semver.SemVerError):
            semver.compare("v1.2.3", "1.2.3")

    def test_is_strictly_increasing(self):
        self.assertTrue(semver.is_strictly_increasing("1.2.3", "1.2.4"))
        self.assertTrue(semver.is_strictly_increasing("1.2.3", "2.0.0"))
        self.assertFalse(semver.is_strictly_increasing("1.2.4", "1.2.3"))
        self.assertFalse(semver.is_strictly_increasing("1.2.3", "1.2.3"))
        self.assertFalse(semver.is_strictly_increasing("1.2.3", "1.2.2"))


if __name__ == "__main__":
    unittest.main()
