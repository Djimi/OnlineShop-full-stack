"""candidate commands: validate manifests and build them from structured inputs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from .. import github
from ..errors import ReadError, ValidationError
from ..models import CandidateManifest
from ..serialization import canonical_json
from ..validation import is_expired
from ..validation import validate as validate_record

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED_INPUT_KEYS = ("source", "build", "artifacts", "frontend", "tests")


def validate(args: argparse.Namespace) -> int:
    """Validate a candidate manifest and print the canonical record on success."""
    path = Path(args.manifest)
    raw = _load_manifest(path)
    try:
        manifest = CandidateManifest.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(f"manifest {path} failed schema validation: {error}") from error
    errors = validate_record(manifest)
    if errors:
        raise ValidationError(f"manifest {path} is invalid: {'; '.join(errors)}")
    _enforce_expiry(manifest, args.max_age_days)
    if args.candidate_class is not None and manifest.candidateClass != args.candidate_class:
        raise ValidationError(
            "candidate class "
            f"{manifest.candidateClass!r} does not match required {args.candidate_class!r}"
        )
    if args.require_production_eligible and not manifest.productionEligible:
        raise ValidationError("candidate is not productionEligible")
    print(f"candidate {manifest.candidateId} ({manifest.candidateClass}) is valid")
    print(canonical_json(manifest.model_dump(mode="json")))
    return 0


def manifest(args: argparse.Namespace) -> int:
    """Build a candidate manifest from structured inputs and write it to --out."""
    path = Path(args.inputs)
    raw = _load_inputs(path)
    source = raw["source"]
    build = raw["build"]
    artifacts = raw["artifacts"]
    if (
        not isinstance(source, dict)
        or not isinstance(build, dict)
        or not isinstance(artifacts, dict)
    ):
        raise ValidationError("inputs source, build, and artifacts must be JSON objects")
    full_sha = source.get("fullSha")
    if not isinstance(full_sha, str) or not _FULL_SHA.fullmatch(full_sha):
        raise ValidationError("source.fullSha must be 40 lowercase hex characters")
    run_id, run_attempt = github.assert_run_attempt_shape(
        build.get("workflowRunId"), build.get("workflowRunAttempt")
    )
    items = artifacts.get("items")
    if not isinstance(items, dict) or items.get("commonSourceSha") != full_sha:
        raise ValidationError("artifacts.items.commonSourceSha must equal source.fullSha")
    data = {
        "schemaVersion": "1.0",
        "candidateId": github.candidate_id(run_id, run_attempt, full_sha),
        "candidateClass": args.candidate_class,
        "source": source,
        "build": build,
        "artifacts": {**artifacts, "frontend": raw["frontend"]},
        "tests": raw["tests"],
        "productionEligible": args.candidate_class == "main",
    }
    try:
        manifest_record = CandidateManifest.model_validate(data)
    except PydanticValidationError as error:
        raise ValidationError(f"inputs {path} failed schema validation: {error}") from error
    errors = validate_record(manifest_record)
    if errors:
        raise ValidationError(f"inputs {path} are invalid: {'; '.join(errors)}")
    _enforce_expiry(manifest_record, args.max_age_days)
    _write_out(args.out, manifest_record)
    print(
        f"candidate {manifest_record.candidateId} "
        f"({manifest_record.candidateClass}) written to {args.out}"
    )
    return 0


def _load_manifest(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text())
    except OSError as error:
        raise ReadError(f"cannot read manifest {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"manifest {path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ValidationError(f"manifest {path} must contain a JSON object")
    return raw


def _load_inputs(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text())
    except OSError as error:
        raise ReadError(f"cannot read inputs file {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"inputs file {path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ValidationError(f"inputs file {path} must contain a JSON object")
    missing = sorted(set(_REQUIRED_INPUT_KEYS) - raw.keys())
    if missing:
        raise ValidationError(f"inputs file {path} missing keys: {', '.join(missing)}")
    return raw


def _write_out(path: str, manifest_record: CandidateManifest) -> None:
    try:
        Path(path).write_text(canonical_json(manifest_record.model_dump(mode="json")) + "\n")
    except OSError as error:
        raise ReadError(f"cannot write manifest to {path}: {error}") from error


def _enforce_expiry(manifest_record: CandidateManifest, max_age_days: int | None) -> None:
    if max_age_days is None:
        return
    now = datetime.now(UTC)
    if is_expired(manifest_record, max_age_days, now):
        age = now - manifest_record.build.completedAt
        raise ValidationError(
            f"candidate {manifest_record.candidateId} is expired: age {age} "
            f"exceeds --max-age-days {max_age_days}"
        )
