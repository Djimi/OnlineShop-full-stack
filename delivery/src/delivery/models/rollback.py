"""Rollback preflight report and rollback result record (OP-REC-03/04).

The preflight report is the read-only OP-REC-03 evidence produced before
approval and re-derived inside ``rollback execute`` after the production
lock; its ``approvalIdentity`` (SHA-256 of the byte-stable identity subset)
is compared byte-for-byte, so any drift between approval and mutation
aborts before the first mutation.

The rollback result is the SEPARATE OP-REC-04 outcome record: official
history is never edited (CT-REL-02). It carries the mandatory requester and
approver (never defaulted to the run actor), the exact from/to component
identities (digests + frontend checksum, CT-AUDIT-01), the workflow run
identity, timestamps, and the outcome.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import PositiveInt, StrictRecord, UtcDateTime
from .promotion import PreflightSnapshotSummary


class RollbackComponentIdentity(StrictRecord):
    """Exact component bytes of one side of a rollback (CT-AUDIT-01).

    ``frontendChecksum`` is the exact checksum each side records: the target
    side carries the release manifest's frontend content checksum, and the
    from side carries the snapshot's observed live-marker checksum.
    """

    authDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    itemsDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gatewayDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    frontendChecksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class RollbackComponent(StrictRecord):
    """Per-component rollback conclusion for compensation and audit.

    Only ``passed`` components (fully deployed AND verified) are ever
    compensated automatically; ``failed`` is ambiguous and ``not-attempted``
    was never touched, so neither is guessed.
    """

    component: Literal["auth", "items", "gateway", "frontend"]
    conclusion: Literal["passed", "failed", "not-attempted"]


class RollbackPreflightReport(StrictRecord):
    """Read-only OP-REC-03 preflight evidence; also the job A -> job B
    drift-comparison source (``approvalIdentity``)."""

    schemaVersion: str = "1.0"
    reportId: str
    producedAt: UtcDateTime
    releaseId: str = Field(pattern=r"^release-\d{4}$")
    target: RollbackComponentIdentity
    targetFrontendIdentity: str
    targetCompatibilityFingerprint: str
    productionSnapshot: PreflightSnapshotSummary
    snapshotReleaseId: str = Field(pattern=r"^release-\d{4}$")
    schemaChange: Literal["present", "absent"]
    migrationReviewed: bool
    approvalSummary: str
    approvalIdentity: str = Field(pattern=r"^[0-9a-f]{64}$")


class RollbackResult(StrictRecord):
    schemaVersion: str = "1.0"
    rollbackId: str
    releaseId: str = Field(pattern=r"^release-\d{4}$")
    requester: str
    approver: str
    fromReleaseId: str = Field(pattern=r"^release-\d{4}$")
    workflowRunId: PositiveInt
    workflowRunAttempt: PositiveInt
    startedAt: UtcDateTime
    completedAt: UtcDateTime | None = None
    outcome: Literal["completed", "failed"]
    deploymentConclusion: str
    verificationConclusion: str
    restoreConclusion: str
    workflowUrl: str = Field(pattern=r"^https://")
    snapshotId: str | None = None
    fromRelease: RollbackComponentIdentity | None = None
    toRelease: RollbackComponentIdentity | None = None
    components: list[RollbackComponent] = Field(default_factory=list)


__all__ = [
    "RollbackComponent",
    "RollbackComponentIdentity",
    "RollbackPreflightReport",
    "RollbackResult",
]
