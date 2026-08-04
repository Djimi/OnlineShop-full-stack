"""SHA-256 helpers and canonical manifest checksums.

A manifest checksum is the SHA-256 of the manifest's canonical JSON encoding:
sorted object keys, compact separators, and ASCII escaping. This makes the
checksum reproducible regardless of source formatting or key order while still
detecting any later alteration or deletion of release assets.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_hex(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    """Return the SHA-256 hex digest of a file on disk, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(obj: Any) -> bytes:
    """Encode ``obj`` to a canonical, stable JSON byte representation."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def manifest_checksum(obj: Any) -> str:
    """Return the deterministic checksum of an already-parsed manifest object."""
    return sha256_hex(canonical_json_bytes(obj))


def manifest_checksum_file(path: str) -> str:
    """Parse the JSON document at ``path`` and return its manifest checksum.

    Raises ``json.JSONDecodeError`` for invalid JSON so callers can distinguish
    a malformed document from a checksum mismatch.
    """
    with open(path, encoding="utf-8") as handle:
        obj = json.load(handle)
    return manifest_checksum(obj)
