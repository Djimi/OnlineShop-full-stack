"""Model tests for the Phase-4 staging records and fixtures."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from delivery import staging_marker
from delivery.models import (
    DiagnosticsRecord,
    E2EConclusion,
    OwnershipMarker,
    ReconcileRecord,
    Source,
    StagingOperationRecord,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_ownership_marker_fixture_parses():
    raw = json.loads((FIXTURES / "valid_ownership_marker.json").read_text())
    marker = OwnershipMarker.model_validate(raw)
    assert marker.operationId == "stg-4711-1"
    assert marker.expiresAt > marker.acquiredAt


def test_ownership_marker_requires_positive_run_ids():
    base = json.loads((FIXTURES / "valid_ownership_marker.json").read_text())
    base["workflowRunId"] = 0
    with pytest.raises(PydanticValidationError):
        OwnershipMarker.model_validate(base)


def test_ownership_marker_rejects_naive_datetimes():
    base = json.loads((FIXTURES / "valid_ownership_marker.json").read_text())
    base["acquiredAt"] = "2026-08-10T10:00:00"
    with pytest.raises(PydanticValidationError):
        OwnershipMarker.model_validate(base)


def test_reconcile_fixture_parses():
    raw = json.loads((FIXTURES / "valid_reconcile.json").read_text())
    record = ReconcileRecord.model_validate(raw)
    assert record.action == "none"
    assert record.marker is not None
    assert record.marker.operationId == "stg-4711-1"


def test_reconcile_record_rejects_unknown_action():
    base = json.loads((FIXTURES / "valid_reconcile.json").read_text())
    base["action"] = "deleted"
    with pytest.raises(PydanticValidationError):
        ReconcileRecord.model_validate(base)


def test_diagnostics_record_redacts_by_construction():
    diagnostics = DiagnosticsRecord(
        capturedAt=datetime.now(UTC),
        environment="staging",
        cluster="onlineshop-staging-cluster",
        services=[{"serviceName": "onlineshop-auth-staging", "desiredCount": 0}],
        dbInstance={"DBInstanceStatus": "stopped"},
    )
    assert diagnostics.redacted is True
    dumped = diagnostics.model_dump(mode="json")
    assert "MasterUserSecret" not in json.dumps(dumped)


def test_diagnostics_record_keeps_only_sanitized_observations():
    diagnostics = DiagnosticsRecord(
        capturedAt=datetime.now(UTC),
        environment="staging",
        cluster="onlineshop-staging-cluster",
        errors=["rds read failed"],
    )
    assert diagnostics.services == []
    assert "rds read failed" in diagnostics.errors


def test_staging_fixture_still_valid_with_extended_fields():
    raw = json.loads((FIXTURES / "valid_staging.json").read_text())
    raw["phaseLog"] = [
        {
            "name": "QUEUED",
            "startedAt": "2026-08-10T10:00:00Z",
            "endedAt": "2026-08-10T10:00:01Z",
            "conclusion": "passed",
        }
    ]
    raw["journeys"] = [
        {"name": "candidate-frontend:items-api", "conclusion": "passed", "detail": "HTTP 200"}
    ]
    raw["e2eUrl"] = "http://staging-alb.example.com"
    record = StagingOperationRecord.model_validate(raw)
    assert record.phaseLog[0].name == "QUEUED"
    assert record.e2eUrl == "http://staging-alb.example.com"


def test_e2e_conclusion_not_run_is_a_valid_value():
    conclusion = E2EConclusion(conclusion="not-run")
    assert conclusion.conclusion == "not-run"
    for value in ("pending", "passed", "failed"):
        assert E2EConclusion(conclusion=value).conclusion == value


def test_e2e_conclusion_rejects_unbounded_values():
    with pytest.raises(PydanticValidationError):
        E2EConclusion(conclusion="skipped")


def test_ownership_marker_owner_bounded_to_39_chars():
    with pytest.raises(PydanticValidationError):
        OwnershipMarker(
            schemaVersion="1.0",
            operationId="stg-1-1",
            workflowRunId=1,
            workflowRunAttempt=1,
            owner="x" * 40,
            acquiredAt=datetime.now(UTC),
            expiresAt=datetime.now(UTC),
        )
    marker = OwnershipMarker(
        schemaVersion="1.0",
        operationId="stg-1-1",
        workflowRunId=1,
        workflowRunAttempt=1,
        owner="x" * 39,
        acquiredAt=datetime.now(UTC),
        expiresAt=datetime.now(UTC),
    )
    assert marker.owner == "x" * 39


def test_marker_tag_value_fits_rds_limit_with_worst_case_owner():
    # RDS tag values are limited to 256 characters; prove the worst case
    # (a 39-character GitHub login) serializes within the limit.
    marker = staging_marker.build_marker("stg-4712-2", 4712, 2, "x" * 39)
    value = staging_marker.marker_value(marker)
    assert len(value) <= 256


def test_source_repository_pattern_rejects_unsafe_paths():
    with pytest.raises(PydanticValidationError):
        Source(
            repository="owner/repo/../../etc",
            branch="main",
            ref="refs/heads/main",
            fullSha="1" * 40,
        )
    with pytest.raises(PydanticValidationError):
        Source(
            repository="owner/repo?query=1",
            branch="main",
            ref="refs/heads/main",
            fullSha="1" * 40,
        )
    source = Source(
        repository="Djimi@8793507/OnlineShop-full-stack",
        branch="main",
        ref="refs/heads/main",
        fullSha="1" * 40,
    )
    assert source.repository == "Djimi@8793507/OnlineShop-full-stack"
