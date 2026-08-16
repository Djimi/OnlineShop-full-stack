"""Staging ownership marker (D1) stored as an RDS tag on the staging DB.

Tag key ``onlineshop:staging-owner``, value is the canonical JSON of
``OwnershipMarker``. Acquisition happens BEFORE any staging mutation so the
reconcile race window closes: tagging a stopped DB instance is safe, and a
valid marker is never overwritten. Expiry is a generous 3-hour bound on one
staging lifecycle — long enough that no live run is ever stolen, short enough
that the 15-minute reconcile reclaims a genuinely lost run the same day.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from .errors import StagingMarkerConflict, ValidationError
from .models.staging import OwnershipMarker, StagingOperationRecord
from .serialization import canonical_json

MARKER_TAG_KEY = "onlineshop:staging-owner"
MARKER_TTL = timedelta(hours=3)


def marker_is_active(marker: OwnershipMarker, now: datetime | None = None) -> bool:
    """A marker is active while its expiresAt is in the future."""
    reference = now if now is not None else datetime.now(UTC)
    return marker.expiresAt > reference


def parse_marker(value: str) -> OwnershipMarker:
    """Parse a marker tag value, failing closed on any shape mismatch."""
    try:
        return OwnershipMarker.model_validate_json(value)
    except PydanticValidationError as error:
        raise ValidationError(f"staging ownership marker is malformed: {error}") from error


def read_marker(rds_client: Any, db_arn: str) -> OwnershipMarker | None:
    """Read the ownership marker from RDS tags; absent only when the tag is absent.

    A malformed (unparseable) value is an error, never treated as absence.
    """
    from .aws.rds import list_tags_for_resource

    tags = list_tags_for_resource(rds_client, db_arn)
    value = tags.get(MARKER_TAG_KEY)
    if value is None:
        return None
    return parse_marker(value)


def acquire_marker(
    rds_client: Any, db_arn: str, marker: OwnershipMarker, now: datetime | None = None
) -> None:
    """Acquire the staging ownership marker, failing closed on a valid owner."""
    from .aws.rds import add_tags_to_resource

    existing = read_marker(rds_client, db_arn)
    if existing is not None and marker_is_active(existing, now):
        raise StagingMarkerConflict(
            f"staging is already owned by operation {existing.operationId} "
            f"(expires {existing.expiresAt.isoformat()}); refusing to steal ownership"
        )
    add_tags_to_resource(
        rds_client,
        db_arn,
        {MARKER_TAG_KEY: canonical_json(marker.model_dump(mode="json"))},
    )


def release_marker(rds_client: Any, db_arn: str) -> None:
    """Remove the ownership marker and verify the read-back is absent."""
    from .aws.rds import remove_tags_from_resource

    remove_tags_from_resource(rds_client, db_arn, [MARKER_TAG_KEY])


def assert_marker_owned_by(
    marker: OwnershipMarker | None, record: StagingOperationRecord
) -> OwnershipMarker:
    """Continuation ownership check: the live marker must be this operation's."""
    if marker is None:
        raise StagingMarkerConflict(
            f"staging ownership marker is missing for operation {record.operationId}; "
            "the environment may have been reclaimed — refusing to continue"
        )
    if not marker_is_active(marker):
        raise StagingMarkerConflict(
            f"staging ownership marker for operation {record.operationId} has expired"
        )
    identity = record.candidate
    mismatches = []
    if marker.operationId != record.operationId:
        mismatches.append(f"operationId {marker.operationId} != {record.operationId}")
    if marker.workflowRunId != identity.workflowRunId:
        mismatches.append(f"workflowRunId {marker.workflowRunId} != {identity.workflowRunId}")
    if marker.workflowRunAttempt != identity.workflowRunAttempt:
        mismatches.append(
            f"workflowRunAttempt {marker.workflowRunAttempt} != {identity.workflowRunAttempt}"
        )
    if mismatches:
        raise StagingMarkerConflict(
            "live staging ownership marker does not belong to this operation: "
            + "; ".join(mismatches)
        )
    return marker


def build_marker(
    operation_id: str, workflow_run_id: int, workflow_run_attempt: int, owner: str
) -> OwnershipMarker:
    """Build a fresh ownership marker with a 3-hour TTL."""
    acquired_at = datetime.now(UTC)
    return OwnershipMarker(
        operationId=operation_id,
        workflowRunId=workflow_run_id,
        workflowRunAttempt=workflow_run_attempt,
        owner=owner,
        acquiredAt=acquired_at,
        expiresAt=acquired_at + MARKER_TTL,
    )


def marker_value(marker: OwnershipMarker) -> str:
    """Canonical JSON tag value of a marker (never contains secrets)."""
    return canonical_json(marker.model_dump(mode="json"))
