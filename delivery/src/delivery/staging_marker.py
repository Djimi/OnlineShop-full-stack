"""Staging ownership marker (D1) stored as an RDS tag on the staging DB.

Tag key ``onlineshop:staging-owner``, value is the compact, versioned encoding
of ``OwnershipMarker``. Acquisition happens BEFORE any staging mutation so the
reconcile race window closes: tagging a stopped DB instance is safe, and a
valid marker is never overwritten. Expiry is a generous 3-hour bound on one
staging lifecycle — long enough that no live run is ever stolen, short enough
that the 15-minute reconcile reclaims a genuinely lost run the same day.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from .errors import StagingMarkerConflict, ValidationError
from .models.staging import OwnershipMarker, StagingOperationRecord

MARKER_TAG_KEY = "onlineshop:staging-owner"
MARKER_TTL = timedelta(hours=3)
MARKER_VERSION = "v1"
MAX_MARKER_VALUE_LENGTH = 256
AWS_TAG_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_.:/=+@-]+$")
POSITIVE_DECIMAL_PATTERN = re.compile(r"^[1-9][0-9]*$")


def marker_is_active(marker: OwnershipMarker, now: datetime | None = None) -> bool:
    """A marker is active while its expiresAt is in the future."""
    reference = now if now is not None else datetime.now(UTC)
    return marker.expiresAt > reference


def parse_marker(value: str) -> OwnershipMarker:
    """Decode only the versioned AWS-safe marker form, failing closed."""
    try:
        _validate_marker_value(value)
        fields = value.split(":")
        if len(fields) != 7:
            raise ValueError("expected exactly 7 colon-separated fields")
        version, operation_id, run_id, run_attempt, owner, acquired, expires = fields
        if version != MARKER_VERSION:
            raise ValueError(f"unsupported marker version {version!r}")
        integer_fields = (run_id, run_attempt, acquired, expires)
        if any(POSITIVE_DECIMAL_PATTERN.fullmatch(field) is None for field in integer_fields):
            raise ValueError("run IDs, attempt, and timestamps must be positive decimals")
        return OwnershipMarker(
            operationId=operation_id,
            workflowRunId=int(run_id),
            workflowRunAttempt=int(run_attempt),
            owner=owner,
            acquiredAt=datetime.fromtimestamp(int(acquired), UTC),
            expiresAt=datetime.fromtimestamp(int(expires), UTC),
        )
    except (PydanticValidationError, ValueError, OverflowError, OSError) as error:
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
        {MARKER_TAG_KEY: marker_value(marker)},
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
    acquired_at = datetime.now(UTC).replace(microsecond=0)
    return OwnershipMarker(
        operationId=operation_id,
        workflowRunId=workflow_run_id,
        workflowRunAttempt=workflow_run_attempt,
        owner=owner,
        acquiredAt=acquired_at,
        expiresAt=acquired_at + MARKER_TTL,
    )


def marker_value(marker: OwnershipMarker) -> str:
    """Encode a marker as the compact v1 AWS-safe RDS tag value."""
    try:
        validated = OwnershipMarker.model_validate(marker.model_dump())
    except PydanticValidationError as error:
        raise ValidationError(f"staging ownership marker is malformed: {error}") from error
    timestamps = (validated.acquiredAt, validated.expiresAt)
    if any(timestamp.microsecond != 0 for timestamp in timestamps):
        raise ValidationError("staging ownership marker timestamps must use whole seconds")
    acquired, expires = (int(timestamp.timestamp()) for timestamp in timestamps)
    if acquired <= 0 or expires <= 0:
        raise ValidationError("staging ownership marker timestamps must be positive")
    value = ":".join(
        (
            MARKER_VERSION,
            validated.operationId,
            str(validated.workflowRunId),
            str(validated.workflowRunAttempt),
            validated.owner,
            str(acquired),
            str(expires),
        )
    )
    _validate_marker_value(value)
    return value


def _validate_marker_value(value: str) -> None:
    if len(value) > MAX_MARKER_VALUE_LENGTH:
        raise ValidationError(
            f"marker exceeds the AWS RDS tag value limit of {MAX_MARKER_VALUE_LENGTH} characters"
        )
    if AWS_TAG_VALUE_PATTERN.fullmatch(value) is None:
        raise ValidationError("marker contains characters forbidden in an AWS RDS tag value")
