"""Tests for fail-closed schema and cross-identity validation rules."""

import json
from pathlib import Path

from delivery.models import (
    CandidateArtifacts,
    CandidateManifest,
    EvidenceRecord,
    ProductionSnapshot,
    ReleaseManifest,
    RollbackResult,
    StagingOperationRecord,
)
from delivery.validation import (
    validate,
    validate_release_against_candidate,
    validate_staging_against_candidate,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def candidate() -> CandidateManifest:
    return CandidateManifest.model_validate(load("valid_candidate_main.json"))


def staging() -> StagingOperationRecord:
    return StagingOperationRecord.model_validate(load("valid_staging.json"))


def snapshot() -> ProductionSnapshot:
    return ProductionSnapshot.model_validate(load("valid_snapshot.json"))


def release() -> ReleaseManifest:
    return ReleaseManifest.model_validate(load("valid_release.json"))


def rollback() -> RollbackResult:
    return RollbackResult.model_validate(load("valid_rollback.json"))


def evidence() -> EvidenceRecord:
    return EvidenceRecord.model_validate(load("valid_evidence.json"))


def test_valid_records_pass_validation():
    assert validate(candidate()) == []
    assert validate(staging()) == []
    assert validate(snapshot()) == []
    assert validate(release()) == []
    assert validate(rollback()) == []
    assert validate(evidence()) == []


def test_validate_rejects_unsupported_records():
    assert validate(object()) != []


def test_candidate_unsupported_schema_version():
    record = candidate()
    record.schemaVersion = "0.9"
    errors = validate(record)
    assert any("schemaVersion" in error for error in errors)


def test_candidate_bad_full_sha():
    record = candidate()
    record.source.fullSha = "not-a-sha"
    errors = validate(record)
    assert any("fullSha" in error for error in errors)


def test_candidate_bad_auth_digest():
    record = candidate()
    record.artifacts.auth.digest = "sha256:zz"
    errors = validate(record)
    assert any("auth" in error for error in errors)


def test_candidate_bad_items_digest():
    record = candidate()
    record.artifacts.items.digest = "md5:abc"
    errors = validate(record)
    assert any("items" in error for error in errors)


def test_candidate_bad_gateway_digest():
    record = candidate()
    record.artifacts.gateway.digest = "sha256:" + "g" * 63
    errors = validate(record)
    assert any("gateway" in error for error in errors)


def test_candidate_bad_frontend_checksum():
    record = candidate()
    record.artifacts.frontend.contentChecksum = "0" * 63
    errors = validate(record)
    assert any("contentChecksum" in error for error in errors)


def test_candidate_bad_frontend_artifact_digest():
    record = candidate()
    record.artifacts.frontend.artifactDigest = "d41d8cd98f00b204e9800998ecf8427e"
    errors = validate(record)
    assert any("artifactDigest" in error for error in errors)


def test_candidate_common_sha_must_equal_full_sha():
    record = candidate()
    record.artifacts.items.commonSourceSha = "2" * 40
    errors = validate(record)
    assert any("commonSourceSha" in error for error in errors)


def test_candidate_feature_cannot_be_production_eligible():
    record = candidate().model_copy(update={"candidateClass": "feature"})
    errors = validate(record)
    assert any("productionEligible" in error for error in errors)


def test_candidate_missing_artifact_is_reported():
    record = candidate()
    artifacts = CandidateArtifacts.model_construct(
        auth=record.artifacts.auth,
        items=record.artifacts.items,
        frontend=record.artifacts.frontend,
    )
    record = record.model_copy(update={"artifacts": artifacts})
    errors = validate(record)
    assert any("gateway" in error for error in errors)


def test_candidate_id_must_start_with_cand_prefix():
    record = candidate()
    record.candidateId = "feature-4711"
    errors = validate(record)
    assert any("cand-" in error for error in errors)


def test_staging_expected_digest_patterns():
    record = staging()
    record.artifactsExpected.authDigest = "not-a-digest"
    errors = validate(record)
    assert any("authDigest" in error for error in errors)


def test_staging_observed_equal_to_expected_passes():
    assert validate(staging()) == []


def test_staging_observed_mismatch_fails():
    record = staging()
    record.artifactsObserved.itemsDigest = "sha256:" + "0" * 64
    errors = validate(record)
    assert any("itemsDigest" in error for error in errors)


def test_staging_cross_check_passes_for_referenced_candidate():
    assert validate_staging_against_candidate(staging(), candidate()) == []


def test_staging_cross_check_candidate_id_mismatch():
    record = staging()
    record.candidate.candidateId = "cand-other-1-000000000000"
    errors = validate_staging_against_candidate(record, candidate())
    assert any("candidateId" in error for error in errors)


def test_staging_cross_check_full_sha_mismatch():
    record = staging()
    record.candidate.fullSha = "2" * 40
    errors = validate_staging_against_candidate(record, candidate())
    assert any("fullSha" in error for error in errors)


def test_staging_cross_check_branch_mismatch():
    record = staging()
    record.candidate.branch = "feature/x"
    errors = validate_staging_against_candidate(record, candidate())
    assert any("branch" in error for error in errors)


def test_staging_cross_check_run_id_mismatch():
    record = staging()
    record.candidate.workflowRunId = 9999
    errors = validate_staging_against_candidate(record, candidate())
    assert any("workflowRunId" in error for error in errors)


def test_staging_cross_check_run_attempt_mismatch():
    record = staging()
    record.candidate.workflowRunAttempt = 7
    errors = validate_staging_against_candidate(record, candidate())
    assert any("workflowRunAttempt" in error for error in errors)


def test_staging_cross_check_observed_mismatch():
    record = staging()
    record.artifactsObserved.frontendChecksum = "0" * 64
    errors = validate_staging_against_candidate(record, candidate())
    assert any("frontendChecksum" in error for error in errors)


def test_release_unsupported_schema_version():
    record = release()
    record.schemaVersion = "2.0"
    errors = validate(record)
    assert any("schemaVersion" in error for error in errors)


def test_release_id_pattern():
    record = release()
    record.releaseId = "release-00001"
    errors = validate(record)
    assert any("releaseId" in error for error in errors)


def test_release_branch_must_be_main():
    record = release()
    record.source.branch = "feature"
    errors = validate(record)
    assert any("main" in error for error in errors)


def test_release_bad_full_sha():
    record = release()
    record.source.fullSha = "zz"
    errors = validate(record)
    assert any("fullSha" in error for error in errors)


def test_release_bad_sbom_sha256():
    record = release()
    record.artifacts.sbom.sha256 = "0" * 63
    errors = validate(record)
    assert any("sbom" in error for error in errors)


def test_release_vs_candidate_passes_for_promoted_candidate():
    assert validate_release_against_candidate(release(), candidate()) == []


def test_release_vs_candidate_auth_digest_mismatch():
    record = release()
    record.artifacts.auth.digest = "sha256:" + "0" * 64
    errors = validate_release_against_candidate(record, candidate())
    assert any("auth" in error for error in errors)


def test_release_vs_candidate_items_digest_mismatch():
    record = release()
    record.artifacts.items.digest = "sha256:" + "0" * 64
    errors = validate_release_against_candidate(record, candidate())
    assert any("items" in error for error in errors)


def test_release_vs_candidate_gateway_digest_mismatch():
    record = release()
    record.artifacts.gateway.digest = "sha256:" + "0" * 64
    errors = validate_release_against_candidate(record, candidate())
    assert any("gateway" in error for error in errors)


def test_release_vs_candidate_frontend_checksum_mismatch():
    record = release()
    record.artifacts.frontend.checksum = "0" * 64
    errors = validate_release_against_candidate(record, candidate())
    assert any("frontend" in error for error in errors)


def test_release_vs_candidate_id_mismatch():
    record = release()
    record.candidateId = "cand-4712-2-222222222222"
    errors = validate_release_against_candidate(record, candidate())
    assert any("candidateId" in error for error in errors)


def test_release_vs_candidate_full_sha_mismatch():
    record = release()
    record.source.fullSha = "2" * 40
    errors = validate_release_against_candidate(record, candidate())
    assert any("fullSha" in error for error in errors)


def test_snapshot_requires_at_least_one_service():
    record = snapshot()
    record.services = {}
    errors = validate(record)
    assert any("service" in error for error in errors)


def test_snapshot_requires_non_empty_running_digests():
    record = snapshot()
    record.services["auth"].runningDigests = []
    errors = validate(record)
    assert any("runningDigests" in error for error in errors)


def test_snapshot_frontend_checksum_pattern():
    record = snapshot()
    record.frontend.checksum = "zz"
    errors = validate(record)
    assert any("checksum" in error for error in errors)


def test_rollback_from_and_target_release_must_differ():
    record = rollback()
    record.fromReleaseId = record.releaseId
    errors = validate(record)
    assert any("fromReleaseId" in error for error in errors)


def test_rollback_completed_requires_non_empty_conclusions():
    record = rollback()
    record.deploymentConclusion = ""
    errors = validate(record)
    assert any("deploymentConclusion" in error for error in errors)


def test_rollback_failed_may_have_empty_conclusions():
    record = rollback()
    record.outcome = "failed"
    record.deploymentConclusion = ""
    record.verificationConclusion = ""
    record.restoreConclusion = ""
    assert validate(record) == []


def test_evidence_requires_at_least_one_phase():
    record = evidence()
    record.phases = []
    errors = validate(record)
    assert any("phase" in error for error in errors)


def test_evidence_requires_positive_run_identity():
    record = evidence()
    record.workflowRunId = 0
    record.workflowRunAttempt = -1
    errors = validate(record)
    assert any("workflowRunId" in error for error in errors)
    assert any("workflowRunAttempt" in error for error in errors)
