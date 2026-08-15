"""Fail-closed schema and cross-identity validation rules for delivery records."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from .models.candidate import CandidateManifest
from .models.evidence import EvidenceRecord
from .models.promotion import PreflightReport, VerificationReport
from .models.release import ReleaseManifest
from .models.rollback import RollbackResult
from .models.snapshot import ProductionSnapshot
from .models.staging import StagingOperationRecord

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID = re.compile(r"^release-\d{4}$")
_SUPPORTED_SCHEMA_VERSIONS = ("1.0",)


def _schema_version_error(record, label: str) -> list[str]:
    if record.schemaVersion in _SUPPORTED_SCHEMA_VERSIONS:
        return []
    supported = ", ".join(_SUPPORTED_SCHEMA_VERSIONS)
    return [f"{label} schemaVersion must be one of: {supported}"]


def is_expired(
    manifest: CandidateManifest, max_age_days: int | None, now: datetime | None = None
) -> bool:
    if max_age_days is None:
        return False
    reference = now if now is not None else datetime.now(UTC)
    return reference - manifest.build.completedAt > timedelta(days=max_age_days)


def validate(record) -> list[str]:
    if isinstance(record, CandidateManifest):
        return _validate_candidate(record)
    if isinstance(record, StagingOperationRecord):
        return _validate_staging(record)
    if isinstance(record, ReleaseManifest):
        return _validate_release(record)
    if isinstance(record, ProductionSnapshot):
        return _validate_snapshot(record)
    if isinstance(record, RollbackResult):
        return _validate_rollback(record)
    if isinstance(record, EvidenceRecord):
        return _validate_evidence(record)
    if isinstance(record, PreflightReport):
        return _validate_preflight(record)
    if isinstance(record, VerificationReport):
        return _validate_verification(record)
    return [f"unsupported record type: {type(record).__name__}"]


def _validate_preflight(record: PreflightReport) -> list[str]:
    errors = _schema_version_error(record, "preflight report")
    if record.candidate.candidateClass != "main":
        errors.append("preflight candidate class must be main")
    if record.candidate.branch != "main":
        errors.append("preflight candidate branch must be main")
    return errors


def _validate_verification(record: VerificationReport) -> list[str]:
    errors = _schema_version_error(record, "verification report")
    if record.environment != "production":
        errors.append("verification report environment must be production")
    if record.conclusion not in ("passed", "failed"):
        errors.append("verification report conclusion must be passed or failed")
    return errors


def _validate_candidate(record: CandidateManifest) -> list[str]:
    errors = _schema_version_error(record, "candidate")
    if not _FULL_SHA.fullmatch(record.source.fullSha):
        errors.append("candidate source.fullSha must be 40 lowercase hex characters")
    if not record.candidateId.startswith("cand-"):
        errors.append("candidateId must start with cand-")
    for name in ("auth", "items", "gateway"):
        ref = getattr(record.artifacts, name, None)
        if ref is None:
            errors.append(f"candidate artifact {name} is missing")
        elif not _DIGEST.fullmatch(ref.digest):
            errors.append(f"candidate artifacts.{name}.digest must match sha256:<64 hex>")
    items = getattr(record.artifacts, "items", None)
    if items is not None and items.commonSourceSha != record.source.fullSha:
        errors.append("candidate artifacts.items.commonSourceSha must equal source.fullSha")
    frontend = getattr(record.artifacts, "frontend", None)
    if frontend is None:
        errors.append("candidate artifact frontend is missing")
    else:
        if not _DIGEST.fullmatch(frontend.artifactDigest):
            errors.append("candidate frontend artifactDigest must match sha256:<64 hex>")
        if not _HEX64.fullmatch(frontend.contentChecksum):
            errors.append("candidate frontend contentChecksum must be 64 lowercase hex characters")
    if record.candidateClass == "feature" and record.productionEligible:
        errors.append("candidateClass feature must set productionEligible false")
    return errors


def _expected_vs_observed(record: StagingOperationRecord) -> list[str]:
    errors = []
    expected = record.artifactsExpected
    observed = record.artifactsObserved
    for key in ("authDigest", "itemsDigest", "gatewayDigest"):
        observed_value = getattr(observed, key)
        if observed_value is not None and observed_value != getattr(expected, key):
            errors.append(f"staging observed {key} does not match expected")
    if (
        observed.frontendChecksum is not None
        and observed.frontendChecksum != expected.frontendChecksum
    ):
        errors.append("staging observed frontendChecksum does not match expected")
    return errors


def _validate_staging(record: StagingOperationRecord) -> list[str]:
    errors = _schema_version_error(record, "staging operation")
    expected = record.artifactsExpected
    for key in ("authDigest", "itemsDigest", "gatewayDigest"):
        if not _DIGEST.fullmatch(getattr(expected, key)):
            errors.append(f"staging expected {key} must match sha256:<64 hex>")
    if not _HEX64.fullmatch(expected.frontendChecksum):
        errors.append("staging expected frontendChecksum must be 64 lowercase hex characters")
    errors.extend(_expected_vs_observed(record))
    return errors


def validate_staging_against_candidate(
    record: StagingOperationRecord, candidate: CandidateManifest
) -> list[str]:
    errors = _validate_staging(record) + _validate_candidate(candidate)
    identity = record.candidate
    if identity.candidateId != candidate.candidateId:
        errors.append("staging candidateId does not reference the provided candidate")
    if identity.fullSha != candidate.source.fullSha:
        errors.append("staging candidate fullSha does not match the provided candidate")
    if identity.branch != candidate.source.branch:
        errors.append("staging candidate branch does not match the provided candidate")
    if identity.workflowRunId != candidate.build.workflowRunId:
        errors.append("staging candidate workflowRunId does not match the provided candidate")
    if identity.workflowRunAttempt != candidate.build.workflowRunAttempt:
        errors.append("staging candidate workflowRunAttempt does not match the provided candidate")
    expected = record.artifactsExpected
    for key in ("authDigest", "itemsDigest", "gatewayDigest"):
        component = key.removesuffix("Digest")
        if getattr(expected, key) != getattr(candidate.artifacts, component).digest:
            errors.append(
                f"staging expected {key} does not match the provided candidate digest"
            )
    if expected.frontendChecksum != candidate.artifacts.frontend.contentChecksum:
        errors.append(
            "staging expected frontendChecksum does not match the provided candidate checksum"
        )
    return errors


def _validate_release(record: ReleaseManifest) -> list[str]:
    errors = _schema_version_error(record, "release")
    if not _RELEASE_ID.fullmatch(record.releaseId):
        errors.append("releaseId must match release-NNNN")
    if record.source.branch != "main":
        errors.append("release source.branch must be main")
    if not _FULL_SHA.fullmatch(record.source.fullSha):
        errors.append("release source.fullSha must be 40 lowercase hex characters")
    for name in ("auth", "items", "gateway"):
        if not _DIGEST.fullmatch(getattr(record.artifacts, name).digest):
            errors.append(f"release artifacts.{name}.digest must match sha256:<64 hex>")
    if not _HEX64.fullmatch(record.artifacts.frontend.checksum):
        errors.append("release frontend checksum must be 64 lowercase hex characters")
    for component in ("auth", "items", "gateway", "frontend"):
        sbom = getattr(record.artifacts.sbom, component, None)
        if sbom is None:
            errors.append(f"release sbom.{component} is missing")
        elif not _HEX64.fullmatch(sbom.sha256):
            errors.append(f"release sbom.{component} sha256 must be 64 lowercase hex characters")
    return errors


def validate_release_against_candidate(
    release: ReleaseManifest, candidate: CandidateManifest
) -> list[str]:
    errors = _validate_release(release) + _validate_candidate(candidate)
    if release.candidateId != candidate.candidateId:
        errors.append("release candidateId must equal the promoted candidate")
    if release.source.fullSha != candidate.source.fullSha:
        errors.append("release source.fullSha must equal the promoted candidate")
    for name in ("auth", "items", "gateway"):
        release_digest = getattr(release.artifacts, name).digest
        candidate_digest = getattr(candidate.artifacts, name).digest
        if release_digest != candidate_digest:
            errors.append(
                f"release artifacts.{name}.digest must equal the promoted candidate digest"
            )
    if release.artifacts.frontend.checksum != candidate.artifacts.frontend.contentChecksum:
        errors.append("release frontend checksum must equal the promoted candidate checksum")
    return errors


def _validate_snapshot(record: ProductionSnapshot) -> list[str]:
    errors = _schema_version_error(record, "production snapshot")
    if not record.services:
        errors.append("production snapshot must contain at least one service observation")
    for name, service in record.services.items():
        if not service.runningDigests:
            errors.append(f"production snapshot service {name} must have non-empty runningDigests")
    if not _HEX64.fullmatch(record.frontend.checksum):
        errors.append("production snapshot frontend checksum must be 64 lowercase hex characters")
    return errors


def _validate_rollback(record: RollbackResult) -> list[str]:
    errors = _schema_version_error(record, "rollback result")
    if record.fromReleaseId == record.releaseId:
        errors.append("rollback fromReleaseId must differ from releaseId")
    if record.outcome == "completed":
        for name in ("deploymentConclusion", "verificationConclusion", "restoreConclusion"):
            if not getattr(record, name):
                errors.append(f"rollback {name} must be non-empty when outcome is completed")
    return errors


def _validate_evidence(record: EvidenceRecord) -> list[str]:
    errors = _schema_version_error(record, "evidence")
    if not record.phases:
        errors.append("evidence must contain at least one phase")
    if record.workflowRunId <= 0:
        errors.append("evidence workflowRunId must be positive")
    if record.workflowRunAttempt <= 0:
        errors.append("evidence workflowRunAttempt must be positive")
    return errors
