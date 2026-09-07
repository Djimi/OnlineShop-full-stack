"""Shared record loading/writing helpers for delivery commands (fail-closed)."""

from __future__ import annotations

import json
from pathlib import Path

from botocore.exceptions import ClientError
from pydantic import ValidationError as PydanticValidationError

from .aws.readback import absent_or_read
from .errors import AbsentResourceError, ReadError, ValidationError
from .models import CandidateManifest, ProductionSnapshot, StagingOperationRecord
from .serialization import canonical_json
from .validation import is_expired
from .validation import validate as validate_record


def load_candidate(path: str, max_age_days: int | None = None) -> CandidateManifest:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read candidate manifest {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"candidate manifest {path} is not valid JSON: {error}") from error
    try:
        manifest = CandidateManifest.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(
            f"candidate manifest {path} failed schema validation: {error}"
        ) from error
    errors = validate_record(manifest)
    if errors:
        raise ValidationError(f"candidate manifest {path} is invalid: {'; '.join(errors)}")
    if max_age_days is not None and is_expired(manifest, max_age_days):
        raise ValidationError(
            f"candidate {manifest.candidateId} is expired; exceeds --max-age-days {max_age_days}"
        )
    return manifest


def load_snapshot(
    path: str, require_environment: str | None = None
) -> ProductionSnapshot:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read snapshot {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"snapshot {path} is not valid JSON: {error}") from error
    try:
        snapshot = ProductionSnapshot.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(f"snapshot {path} failed schema validation: {error}") from error
    errors = validate_record(snapshot)
    if errors:
        raise ValidationError(f"snapshot {path} is invalid: {'; '.join(errors)}")
    if require_environment is not None and snapshot.environment != require_environment:
        raise ValidationError(
            f"snapshot {path} declares environment {snapshot.environment!r}; "
            f"expected {require_environment!r}"
        )
    return snapshot


def load_staging_record(path: str) -> StagingOperationRecord:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read staging record {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"staging record {path} is not valid JSON: {error}") from error
    try:
        record = StagingOperationRecord.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(f"staging record {path} failed schema validation: {error}") from error
    errors = validate_record(record)
    if errors:
        raise ValidationError(f"staging record {path} is invalid: {'; '.join(errors)}")
    return record


def load_release_manifest(path: str):
    from .models import ReleaseManifest

    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read release manifest {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"release manifest {path} is not valid JSON: {error}") from error
    try:
        manifest = ReleaseManifest.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(
            f"release manifest {path} failed schema validation: {error}"
        ) from error
    errors = validate_record(manifest)
    if errors:
        raise ValidationError(f"release manifest {path} is invalid: {'; '.join(errors)}")
    return manifest


def write_json(path: str, record) -> None:
    try:
        Path(path).write_text(canonical_json(record.model_dump(mode="json")) + "\n")
    except OSError as error:
        raise ReadError(f"cannot write {path}: {error}") from error


def read_s3_text(s3_client, bucket: str, key: str, label: str) -> str:
    """Read a UTF-8 S3 object, failing closed on absence or read errors."""
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key).get("Body")
    except ClientError as error:
        if absent_or_read(error):
            raise AbsentResourceError(f"{label} s3://{bucket}/{key} not found") from error
        raise ReadError(f"get_object failed for s3://{bucket}/{key}") from error
    if body is None:
        raise ReadError(f"get_object returned no body for s3://{bucket}/{key}")
    try:
        return body.read().decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValidationError(f"{label} s3://{bucket}/{key} is not valid UTF-8") from error


__all__ = [
    "load_candidate",
    "load_release_manifest",
    "load_snapshot",
    "load_staging_record",
    "read_s3_text",
    "write_json",
]
