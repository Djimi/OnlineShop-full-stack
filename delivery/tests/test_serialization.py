"""Tests for canonical JSON serialization and SHA-256 hashing."""

from delivery.serialization import canonical_json, sha256_hex


def test_canonical_json_is_deterministic_for_the_same_dict():
    data = {"a": 1, "b": {"c": [1, 2], "d": "x"}, "e": "caf\u00e9"}
    assert canonical_json(data) == canonical_json(data)


def test_canonical_json_is_compact_and_has_no_trailing_newline():
    assert canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'


def test_canonical_json_preserves_definition_order():
    assert canonical_json({"z": 1, "a": 2}) == '{"z":1,"a":2}'


def test_canonical_json_does_not_escape_unicode():
    assert canonical_json({"name": "caf\u00e9"}) == '{"name":"caf\u00e9"}'


def test_sha256_known_vectors():
    assert (
        sha256_hex(b"")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert (
        sha256_hex(b"abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
