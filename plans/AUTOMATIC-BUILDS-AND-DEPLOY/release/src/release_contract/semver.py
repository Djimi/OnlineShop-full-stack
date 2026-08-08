"""Strict canonical SemVer validation and ordering for v1 release labels.

The v1 contract (Decision 12 in 03_RELEASE_TRACEABILITY.md) accepts only
canonical stable ``MAJOR.MINOR.PATCH`` versions: no leading zeroes, prerelease
identifiers, build metadata, or an optional leading ``v``. This keeps Git,
GitHub, ECR, S3, and JSON identities identical and avoids Docker-tag encoding
and SemVer-precedence ambiguity.
"""

from __future__ import annotations

import re

# Canonical v1 label pattern. Duplicated in the JSON Schema so both views stay
# independently reviewable; the schema is authoritative for manifests.
SEMVER_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
SEMVER_RE = re.compile(SEMVER_PATTERN)

_SAFE_ALPHABET_RE = re.compile(r"^[0-9A-Za-z+.-]+$")


class SemVerError(ValueError):
    """Raised when a version string is not a valid canonical v1 release label."""

    def __init__(self, version: object, reason: str) -> None:
        self.version = version
        self.reason = reason
        super().__init__(f"invalid version {version!r}: {reason}")


def validate(version: object) -> tuple[bool, str | None]:
    """Return ``(is_valid, reason)`` for a candidate version string.

    ``reason`` is ``None`` when valid and a deterministic human-readable
    explanation otherwise. This never raises.
    """
    if not isinstance(version, str):
        return False, "must be a string"
    if version == "":
        return False, "must not be empty"
    if version != version.strip():
        return False, "must not contain leading, trailing, or embedded whitespace"
    if version[0] in ("v", "V"):
        return False, "must not include a leading 'v'"
    if not _SAFE_ALPHABET_RE.match(version):
        return False, "contains characters outside the SemVer alphabet"
    if "+" in version:
        return False, "must not include build metadata ('+...')"
    if "-" in version:
        return False, "must not include a prerelease suffix ('-...')"
    parts = version.split(".")
    if len(parts) != 3:
        return False, "must have exactly three dot-separated numeric components"
    for index, part in enumerate(parts):
        if not part.isdigit():
            return False, f"component {index + 1} ({part!r}) is not a number"
        if len(part) > 1 and part.startswith("0"):
            return False, f"component {index + 1} must not have leading zeroes"
    return True, None


def is_valid(version: object) -> bool:
    """Return True only for canonical v1 release labels."""
    valid, _ = validate(version)
    return valid


def parse(version: str) -> tuple[int, int, int]:
    """Parse a validated canonical version into ``(major, minor, patch)``.

    Raises :class:`SemVerError` for anything that is not a canonical label.
    """
    valid, reason = validate(version)
    if not valid:
        raise SemVerError(version, reason if reason is not None else "invalid")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def compare(left: object, right: object) -> int:
    """Return -1, 0, or 1 comparing ``left`` against ``right``.

    Both arguments must be canonical versions; anything else raises
    :class:`SemVerError`. Comparison is numeric per component, matching SemVer
    precedence for stable releases.
    """
    left_valid, left_reason = validate(left)
    if not left_valid:
        raise SemVerError(left, left_reason if left_reason is not None else "invalid")
    right_valid, right_reason = validate(right)
    if not right_valid:
        raise SemVerError(right, right_reason if right_reason is not None else "invalid")
    left_tuple = parse(left)  # type: ignore[arg-type]
    right_tuple = parse(right)  # type: ignore[arg-type]
    if left_tuple < right_tuple:
        return -1
    if left_tuple > right_tuple:
        return 1
    return 0


def is_strictly_increasing(previous: object, current: object) -> bool:
    """Return True when ``current`` is a strictly newer release than ``previous``.

    This is the ordering rule used to reject duplicate or non-increasing
    dispatch inputs during promotion.
    """
    return compare(current, previous) > 0
