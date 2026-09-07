"""Production frontend live marker (CT-PROD-02 / OP-DEP-03).

The live entry point marker (S3 key ``frontendLiveMarker``, e.g.
``release.json``) names the deployed frontend identity. Before official
finalization it names the CANDIDATE; finalization replaces it with an
identity-equivalent OFFICIAL marker that names the release while preserving
candidate/checksum identity (OP-FIN-01 step 5).
"""

from __future__ import annotations

import json
import re

from pydantic import Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from .errors import ValidationError
from .models.common import StrictRecord
from .serialization import canonical_json

_RELEASE_ID = re.compile(r"^release-\d{4}$")


class LiveMarker(StrictRecord):
    schemaVersion: str = "1.0"
    releaseId: str | None = Field(default=None, pattern=r"^release-\d{4}$")
    candidateId: str
    sourceSha: str = Field(pattern=r"^[0-9a-f]{40}$")
    frontendSha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _candidate_always_named(self) -> LiveMarker:
        if not self.candidateId:
            raise ValueError("candidateId must be non-empty")
        return self


def marker_document(marker: LiveMarker) -> str:
    """Canonical JSON document stored in S3 (deterministic byte identity)."""
    return canonical_json(marker.model_dump(mode="json"))


def build_candidate_marker(
    candidate_id: str, source_sha: str, frontend_sha256: str
) -> LiveMarker:
    return LiveMarker(
        releaseId=None,
        candidateId=candidate_id,
        sourceSha=source_sha,
        frontendSha256=frontend_sha256,
    )


def build_official_marker(marker: LiveMarker, release_id: str) -> LiveMarker:
    """Return the identity-equivalent official marker naming the release."""
    return LiveMarker(
        releaseId=release_id,
        candidateId=marker.candidateId,
        sourceSha=marker.sourceSha,
        frontendSha256=marker.frontendSha256,
    )


def parse_live_marker(raw: str) -> LiveMarker | None:
    """Parse a live marker document; None when the text is not marker JSON.

    A non-JSON marker (e.g. the legacy plain-text ``release-NNNN`` marker) is
    not an error here — callers decide how to treat legacy shapes.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if "candidateId" not in data or "sourceSha" not in data or "frontendSha256" not in data:
        return None
    try:
        return LiveMarker.model_validate(data)
    except PydanticValidationError as error:
        raise ValidationError(f"live marker document is malformed: {error}") from error


def markers_identity_equivalent(a: LiveMarker, b: LiveMarker) -> bool:
    """True when both markers name the same candidate bytes (CT-PROD-02)."""
    return (
        a.candidateId == b.candidateId
        and a.sourceSha == b.sourceSha
        and a.frontendSha256 == b.frontendSha256
    )


def marker_release_id(raw: str) -> str | None:
    """Extract the release id from a marker document, or legacy plain text."""
    parsed = parse_live_marker(raw)
    if parsed is not None:
        return parsed.releaseId
    stripped = raw.strip()
    return stripped if _RELEASE_ID.fullmatch(stripped) else None


__all__ = [
    "LiveMarker",
    "build_candidate_marker",
    "build_official_marker",
    "marker_document",
    "marker_release_id",
    "markers_identity_equivalent",
    "parse_live_marker",
]
