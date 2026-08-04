"""Unit tests for the canonical component/repository mapping."""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import components as rc


class MappingTests(unittest.TestCase):
    def test_component_keys(self):
        self.assertEqual(rc.COMPONENT_KEYS, ("auth", "items", "apiGateway", "frontend"))
        self.assertEqual(rc.BACKEND_KEYS, ("auth", "items", "apiGateway"))

    def test_repositories_are_canonical(self):
        self.assertEqual(rc.repository_for("auth"), "onlineshop-auth")
        self.assertEqual(rc.repository_for("items"), "onlineshop-items")
        self.assertEqual(rc.repository_for("apiGateway"), "onlineshop-api-gateway")
        self.assertIsNone(rc.repository_for("frontend"))

    def test_identity_prefixes(self):
        self.assertEqual(rc.identity_prefix("auth"), "auth")
        self.assertEqual(rc.identity_prefix("items"), "items")
        self.assertEqual(rc.identity_prefix("apiGateway"), "api-gateway")
        self.assertEqual(rc.identity_prefix("frontend"), "frontend")

    def test_identity_for(self):
        self.assertEqual(rc.identity_for("auth", "1.2.1"), "auth/1.2.1")
        self.assertEqual(rc.identity_for("apiGateway", "1.2.1"), "api-gateway/1.2.1")

    def test_sbom_for(self):
        self.assertEqual(rc.sbom_for("auth"), "auth.spdx.json")
        self.assertEqual(rc.sbom_for("items"), "items.spdx.json")
        self.assertEqual(rc.sbom_for("apiGateway"), "api-gateway.spdx.json")
        self.assertEqual(rc.sbom_for("frontend"), "frontend.spdx.json")

    def test_unknown_component_raises(self):
        with self.assertRaises(ValueError):
            rc.identity_prefix("orders")
        with self.assertRaises(ValueError):
            rc.sbom_for("orders")

    def test_derived_tags_and_prefixes(self):
        sha = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
        self.assertEqual(rc.candidate_tag_for(sha), f"sha-{sha}")
        self.assertEqual(rc.release_tag_for("1.2.1"), "release-1.2.1")
        self.assertEqual(rc.git_tag_for("1.2.1"), "v1.2.1")
        self.assertEqual(rc.release_prefix_for("1.2.1"), "_releases/v1.2.1/")

    def test_frontend_constants(self):
        self.assertEqual(rc.FRONTEND_ARTIFACT, "frontend-dist.tar.gz")
        self.assertEqual(rc.VERSION_MARKER, "release.json")

    def test_is_backend(self):
        self.assertTrue(rc.is_backend("auth"))
        self.assertFalse(rc.is_backend("frontend"))


if __name__ == "__main__":
    unittest.main()
