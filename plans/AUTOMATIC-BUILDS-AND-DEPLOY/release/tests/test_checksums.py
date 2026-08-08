"""Unit tests for SHA-256 helpers and canonical manifest checksums."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import checksums


class Sha256Tests(unittest.TestCase):
    def test_sha256_hex_known_vector(self):
        self.assertEqual(
            checksums.sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_sha256_file_matches_bytes(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".bin", delete=False) as handle:
            handle.write(b"release artifact bytes")
            path = handle.name
        try:
            self.assertEqual(
                checksums.sha256_file(path),
                checksums.sha256_hex(b"release artifact bytes"),
            )
        finally:
            os.unlink(path)


class CanonicalJsonTests(unittest.TestCase):
    def test_canonical_encoding_is_key_order_independent(self):
        left = {"b": 1, "a": {"d": [1, 2], "c": "x"}}
        right = {"a": {"c": "x", "d": [1, 2]}, "b": 1}
        self.assertEqual(
            checksums.canonical_json_bytes(left),
            checksums.canonical_json_bytes(right),
        )

    def test_canonical_encoding_is_ascii(self):
        encoded = checksums.canonical_json_bytes({"name": "café"})
        self.assertIn(b"caf\\u00e9", encoded)

    def test_manifest_checksum_stable_across_key_order(self):
        manifest_a = {"schemaVersion": 1, "release": {"version": "1.2.1"}, "components": {}}
        manifest_b = {"components": {}, "release": {"version": "1.2.1"}, "schemaVersion": 1}
        self.assertEqual(
            checksums.manifest_checksum(manifest_a),
            checksums.manifest_checksum(manifest_b),
        )

    def test_manifest_checksum_changes_on_tamper(self):
        base = {"schemaVersion": 1, "release": {"version": "1.2.1"}, "components": {}}
        tampered = dict(base)
        tampered["release"] = {"version": "1.2.2"}
        self.assertNotEqual(
            checksums.manifest_checksum(base), checksums.manifest_checksum(tampered)
        )

    def test_manifest_checksum_file(self):
        document = {"schemaVersion": 1, "release": {"version": "1.2.1"}, "components": {}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(document, handle)
            path = handle.name
        try:
            self.assertEqual(
                checksums.manifest_checksum_file(path), checksums.manifest_checksum(document)
            )
        finally:
            os.unlink(path)

    def test_manifest_checksum_file_rejects_invalid_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{not json")
            path = handle.name
        try:
            with self.assertRaises(json.JSONDecodeError):
                checksums.manifest_checksum_file(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
