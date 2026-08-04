"""Release contract helpers for the OnlineShop monorepo (Pass 3).

This package implements the local validation foundation for the release
manifest contract defined in
``plans/AUTOMATIC-BUILDS-AND-DEPLOY/03_RELEASE_TRACEABILITY.md`` (subphase 3.1).
Everything here is offline, deterministic, and uses strict JSON parsing -- never
regex-based JSON parsing or ad-hoc shell string concatenation for security
sensitive values.
"""

from .semver import (
    SemVerError,
    compare,
    is_strictly_increasing,
    is_valid,
    parse,
    validate,
)

__all__ = [
    "SemVerError",
    "compare",
    "is_strictly_increasing",
    "is_valid",
    "parse",
    "validate",
]
