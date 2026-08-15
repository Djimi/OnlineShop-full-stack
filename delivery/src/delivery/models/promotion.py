"""Promotion records: preflight report, approval evidence, verification,
frontend publication, and finalization reports (OP-PRO / OP-DEP / OP-FIN)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import StrictRecord, UtcDateTime


class PreflightCandidateIdentity(StrictRecord):
    """The identity subset the approval must display and job B must re-prove."""

    candidateId: str
    candidateClass: Literal["main"]
    branch: Literal["main"]
    fullSha: str = Field(pattern=r"^[0-9a-f]{40}$")
    workflowRunId: int
    workflowRunAttempt: int
    authDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    itemsDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gatewayDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    frontendChecksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class PreflightStagingGate(StrictRecord):
    evidenceIdentity: str
    phase: str
    e2eConclusion: str
    cleanupConclusion: str


class PreflightSnapshotSummary(StrictRecord):
    snapshotId: str
    serviceTaskDefinitionArns: dict[str, str]
    serviceRunningDigests: dict[str, list[str]]
    frontendMarker: str
    frontendChecksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class PreflightReport(StrictRecord):
    """Read-only OP-PRO-02 preflight evidence; also the job A -> job B
    drift-comparison source (``approvalIdentity``)."""

    schemaVersion: str = "1.0"
    reportId: str
    producedAt: UtcDateTime
    candidate: PreflightCandidateIdentity
    candidateReachability: Literal["reachable", "identical", "diverged", "ahead"]
    newerCandidateWarning: str
    stagingGate: PreflightStagingGate
    productionSnapshot: PreflightSnapshotSummary
    opDbGate: str
    approvalSummary: str
    approvalIdentity: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflowUrl: str = Field(pattern=r"^https://")


class ApprovalEvidenceFile(StrictRecord):
    """Approval evidence written by the workflow after the protected
    ``production`` Environment review; ``approver`` is derived from the
    approvals API, never from ``github.actor`` or user input (AD-10)."""

    schemaVersion: str = "1.0"
    approver: str = Field(pattern=r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$")
    requester: str = Field(pattern=r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$")
    workflowUrl: str = Field(pattern=r"^https://")
    approvedAt: UtcDateTime


class FrontendPublishRecord(StrictRecord):
    """Evidence written by ``deploy frontend``: the immutable prefix and the
    provisional release identity used before official allocation (AD-07)."""

    schemaVersion: str = "1.0"
    candidateId: str
    provisionalReleaseId: str = Field(pattern=r"^release-\d{4}$")
    prefixKey: str
    liveMarkerKey: str
    contentChecksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerificationJourney(StrictRecord):
    name: str
    conclusion: str
    detail: str = ""


class VerificationReport(StrictRecord):
    """CT-PROD-01..04 read-only verification evidence (OP-DEP-04)."""

    schemaVersion: str = "1.0"
    reportId: str
    producedAt: UtcDateTime
    environment: Literal["production"]
    services: dict[str, dict]
    frontend: dict
    journeys: list[VerificationJourney] = Field(default_factory=list)
    conclusion: str


class FinalizationStep(StrictRecord):
    name: str
    action: Literal["created", "resumed", "verified", "skipped", "failed"]
    conclusion: str


class RollbackWindowEntry(StrictRecord):
    releaseId: str = Field(pattern=r"^release-\d{4}$")
    complete: bool
    detail: str = ""


class FinalizationReport(StrictRecord):
    """OP-FIN evidence: every mutation read back, window audit recorded."""

    schemaVersion: str = "1.0"
    reportId: str
    producedAt: UtcDateTime
    releaseId: str = Field(pattern=r"^release-\d{4}$")
    resumed: bool
    steps: list[FinalizationStep]
    rollbackCapableAtPublication: bool
    window: list[RollbackWindowEntry] = Field(default_factory=list)


__all__ = [
    "ApprovalEvidenceFile",
    "FinalizationReport",
    "FinalizationStep",
    "FrontendPublishRecord",
    "PreflightCandidateIdentity",
    "PreflightReport",
    "PreflightSnapshotSummary",
    "PreflightStagingGate",
    "RollbackWindowEntry",
    "VerificationJourney",
    "VerificationReport",
]
