"""Deterministic JSON serialization and SHA-256 helpers."""

import hashlib
import json


def canonical_json(data: dict) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
