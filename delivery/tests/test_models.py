"""Tests for pydantic record models: parsing, strictness, and UTC datetimes."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from delivery.models import (
    CandidateManifest,
    EvidenceRecord,
    ProductionSnapshot,
    ReleaseManifest,
    RollbackResult,
    StagingOperationRecord,
)
from delivery.serialization import sha256_hex

FIXTURES = Path(__file__).parent / "fixtures"

VALID_CASES = {
    "valid_candidate.json": CandidateManifest,
    "valid_candidate_main.json": CandidateManifest,
    "valid_staging.json": StagingOperationRecord,
    "valid_snapshot.json": ProductionSnapshot,
    "valid_release.json": ReleaseManifest,
    "valid_rollback.json": RollbackResult,
    "valid_evidence.json": EvidenceRecord,
}

INVALID_CASES = {
    "invalid_candidate_bad_digest.json": CandidateManifest,
    "invalid_candidate_naive_datetime.json": CandidateManifest,
    "invalid_candidate_feature_eligible.json": CandidateManifest,
    "invalid_candidate_bad_fullsha.json": CandidateManifest,
    "invalid_release_bad_id.json": ReleaseManifest,
    "invalid_snapshot_bad_checksum.json": ProductionSnapshot,
    "invalid_snapshot_release_mismatch.json": ProductionSnapshot,
    "invalid_rollback_naive_datetime.json": RollbackResult,
    "invalid_evidence_no_phases.json": EvidenceRecord,
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize("fixture,model", VALID_CASES.items())
def test_valid_fixture_parses(fixture, model):
    assert model.model_validate(load(fixture))


@pytest.mark.parametrize("fixture,model", INVALID_CASES.items())
def test_malformed_fixture_fails_validation(fixture, model):
    with pytest.raises(PydanticValidationError):
        model.model_validate(load(fixture))


def test_candidate_datetimes_are_utc_aware():
    record = CandidateManifest.model_validate(load("valid_candidate_main.json"))
    for value in (record.build.createdAt, record.build.completedAt):
        assert value.tzinfo is not None
        assert value.utcoffset() is not None
        assert value.utcoffset().total_seconds() == 0


def test_naive_datetime_string_is_rejected():
    data = load("valid_candidate.json")
    data["build"]["createdAt"] = "2026-08-11T08:00:00"
    with pytest.raises(PydanticValidationError):
        CandidateManifest.model_validate(data)


def test_non_utc_aware_datetime_is_rejected():
    data = load("valid_candidate.json")
    data["build"]["createdAt"] = "2026-08-11T10:00:00+02:00"
    with pytest.raises(PydanticValidationError):
        CandidateManifest.model_validate(data)


def test_feature_candidate_cannot_be_production_eligible():
    data = load("valid_candidate.json")
    data["productionEligible"] = True
    with pytest.raises(PydanticValidationError):
        CandidateManifest.model_validate(data)


def test_model_dump_field_order_matches_definition_order():
    record = CandidateManifest.model_validate(load("valid_candidate.json"))
    assert list(record.model_dump()) == [
        "schemaVersion",
        "candidateId",
        "candidateClass",
        "source",
        "build",
        "artifacts",
        "tests",
        "productionEligible",
    ]


def test_candidate_identity_is_exact_and_shared():
    record = CandidateManifest.model_validate(load("valid_candidate_main.json"))
    assert record.candidateId == "cand-4711-1-111111111111"
    assert record.source.fullSha == record.artifacts.items.commonSourceSha
    assert record.build.workflowRunId == 4711
    assert record.build.workflowRunAttempt == 1


def _mutated(fixture, path, value):
    data = load(fixture)
    node = data
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return data


POSITIVE_INT_CASES = [
    ("valid_candidate.json", CandidateManifest, "build.workflowRunId"),
    ("valid_candidate.json", CandidateManifest, "build.workflowRunAttempt"),
    ("valid_staging.json", StagingOperationRecord, "candidate.workflowRunId"),
    ("valid_staging.json", StagingOperationRecord, "candidate.workflowRunAttempt"),
    ("valid_rollback.json", RollbackResult, "workflowRunId"),
    ("valid_rollback.json", RollbackResult, "workflowRunAttempt"),
]


@pytest.mark.parametrize("fixture,model,path", POSITIVE_INT_CASES)
@pytest.mark.parametrize("value", [0, -1, True, False])
def test_workflow_run_ids_reject_non_positive(fixture, model, path, value):
    with pytest.raises(PydanticValidationError):
        model.model_validate(_mutated(fixture, path, value))


@pytest.mark.parametrize("value", ["release-1", "v1.0.0", "release-0001 ", "0001"])
def test_rollback_from_release_id_requires_release_pattern(value):
    with pytest.raises(PydanticValidationError):
        RollbackResult.model_validate(_mutated("valid_rollback.json", "fromReleaseId", value))


@pytest.mark.parametrize("value", ["v1.0.0", "release-1", "release-0001 ", 1])
def test_snapshot_release_id_requires_release_pattern(value):
    with pytest.raises(PydanticValidationError):
        ProductionSnapshot.model_validate(
            _mutated("valid_snapshot.json", "release.releaseId", value)
        )


def test_snapshot_official_status_requires_release_id():
    data = load("valid_snapshot.json")
    data["release"]["releaseId"] = None
    with pytest.raises(PydanticValidationError):
        ProductionSnapshot.model_validate(data)


def test_snapshot_none_status_rejects_release_identity():
    data = load("valid_snapshot.json")
    data["release"]["status"] = "none"
    with pytest.raises(PydanticValidationError):
        ProductionSnapshot.model_validate(data)


def test_snapshot_none_status_allows_explicit_absence():
    data = load("valid_snapshot.json")
    data["release"] = {"status": "none", "releaseId": None, "manifestSha256": None}
    record = ProductionSnapshot.model_validate(data)
    assert record.release.status == "none"
    assert record.release.releaseId is None


def test_snapshot_fixture_frontend_matches_producer_semantics():
    record = ProductionSnapshot.model_validate(load("valid_snapshot.json"))
    assert record.frontend.liveMarker == "release.json"
    assert record.frontend.immutableIdentity == "release-0001"
    assert record.frontend.checksum == sha256_hex(record.frontend.immutableIdentity.encode())
