"""Tests for resolving the live release's canonical Git tag."""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract.official import OfficialTagError, resolve_official_tag


def tag(name, sha):
    return {"name": name, "commit": {"sha": sha}}


VERSION = "1.2.1"
SHA = "a" * 40


class OfficialTagResolutionTests(unittest.TestCase):
    def test_newer_unrelated_tag_does_not_override_live_release(self):
        selected = resolve_official_tag(
            [[tag("v9.0.0", "9" * 40), tag("v2.0.0", "b" * 40)], [tag("v1.2.1", SHA)]],
            VERSION,
            SHA,
        )
        self.assertEqual(selected, {"tag": "v1.2.1", "version": VERSION, "sha": SHA})

    def test_canonical_tag_must_exist(self):
        with self.assertRaises(OfficialTagError) as raised:
            resolve_official_tag([[tag("v2.0.0", "b" * 40)]], VERSION, SHA)
        self.assertEqual(raised.exception.code, "TAG_NOT_FOUND")

    def test_canonical_tag_sha_must_match_live_marker(self):
        with self.assertRaises(OfficialTagError) as raised:
            resolve_official_tag([[tag("v1.2.1", "b" * 40)]], VERSION, SHA)
        self.assertEqual(raised.exception.code, "TAG_SHA_MISMATCH")

    def test_canonical_tag_with_bad_sha_fails_closed(self):
        with self.assertRaises(OfficialTagError) as raised:
            resolve_official_tag([[tag("v1.2.1", "not-a-full-sha")]], VERSION, SHA)
        self.assertEqual(raised.exception.code, "TAG_SHA_INVALID")

    def test_ambiguous_non_paginated_shape_fails_closed(self):
        with self.assertRaises(OfficialTagError) as raised:
            resolve_official_tag([tag("v1.2.1", SHA)], VERSION, SHA)
        self.assertEqual(raised.exception.code, "TAG_API_SHAPE")

    def test_duplicate_canonical_tag_with_same_identity_is_idempotent(self):
        selected = resolve_official_tag([[tag("v1.2.1", SHA)], [tag("v1.2.1", SHA)]], VERSION, SHA)
        self.assertEqual(selected["tag"], "v1.2.1")

    def test_live_marker_identity_is_validated(self):
        with self.assertRaises(OfficialTagError) as raised:
            resolve_official_tag([[tag("v1.2.1", SHA)]], "v1.2.1-beta", SHA)
        self.assertEqual(raised.exception.code, "LIVE_VERSION_INVALID")
        with self.assertRaises(OfficialTagError) as raised:
            resolve_official_tag([[tag("v1.2.1", SHA)]], VERSION, "not-a-sha")
        self.assertEqual(raised.exception.code, "LIVE_SOURCE_SHA_INVALID")


if __name__ == "__main__":
    unittest.main()
