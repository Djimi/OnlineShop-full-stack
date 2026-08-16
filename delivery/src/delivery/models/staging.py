"""Staging operation record (CT-STG-01), phase values (OP-STG-01),
ownership marker (D1/OP-STG-05), diagnostics, and reconcile records."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from .common import PositiveInt, StrictRecord, UtcDateTime
from .evidence import FailureInfo, PhaseLog


class Phase(StrEnum):
    QUEUED = "QUEUED"
    OWNED = "OWNED"
    STARTING = "STARTING"
    RESETTING = "RESETTING"
    DEPLOYING = "DEPLOYING"
    COMPATIBILITY = "COMPATIBILITY"
    E2E = "E2E"
    EVIDENCE = "EVIDENCE"
    STOPPING = "STOPPING"
    CLEANUP_VERIFY = "CLEANUP_VERIFY"
    COMPLETE = "COMPLETE"


def _coerce_phase(value):
    if isinstance(value, str):
        return Phase(value)
    return value


class StagingCandidateIdentity(StrictRecord):
    candidateId: str
    branch: str
    fullSha: str = Field(pattern=r"^[0-9a-f]{40}$")
    workflowRunId: PositiveInt
    workflowRunAttempt: PositiveInt


class DatabaseConclusions(StrictRecord):
    resetConclusion: str
    seedConclusion: str
    accessVerificationConclusion: str


class ExpectedArtifacts(StrictRecord):
    authDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    itemsDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gatewayDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    frontendChecksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class ObservedArtifacts(StrictRecord):
    authDigest: str | None = None
    itemsDigest: str | None = None
    gatewayDigest: str | None = None
    frontendChecksum: str | None = None


class CompatibilityConclusion(StrictRecord):
    conclusion: str
    bootstrapException: bool


class E2EConclusion(StrictRecord):
    """``pending`` is written by the first invocation before the cloud suite
    runs; ``not-run`` is recorded by the continuation when the previous
    invocation reached phase E2E but never recorded an actual suite result —
    recording ``failed`` for a suite that never ran would be a lie.
    """

    conclusion: Literal["pending", "passed", "failed", "not-run"]


class CleanupConclusion(StrictRecord):
    conclusion: str
    reason: str = ""


class JourneyConclusion(StrictRecord):
    name: str
    conclusion: str
    detail: str = ""


class OwnershipMarker(StrictRecord):
    """D1 ownership marker stored as a compact versioned RDS tag value.

    The tag key is ``onlineshop:staging-owner``; the value uses the AWS-safe
    ``v1:<operation>:<run>:<attempt>:<owner>:<acquired-epoch>:<expires-epoch>``
    encoding. A marker is only valid while ``expiresAt`` is in the future; the
    TTL is a generous bound on one staging lifecycle (3h), chosen so the
    15-minute reconcile can reclaim a genuinely lost run without ever stealing
    from a live owner.
    """

    schemaVersion: str = "1.0"
    operationId: str = Field(pattern=r"^stg-[1-9][0-9]*-[1-9][0-9]*$")
    workflowRunId: PositiveInt
    workflowRunAttempt: PositiveInt
    owner: str = Field(pattern=r"^[A-Za-z0-9@._-]{1,39}$")
    acquiredAt: UtcDateTime
    expiresAt: UtcDateTime


class DiagnosticsRecord(StrictRecord):
    """Redacted pre-cleanup environment snapshot (OP-STG-04 / CT-AUDIT-02).

    Only sanitized observations are kept: container ``secrets`` entries are
    replaced by counts and the RDS ``MasterUserSecret`` object is dropped.
    """

    schemaVersion: str = "1.0"
    capturedAt: UtcDateTime
    environment: str
    cluster: str
    services: list[dict] = Field(default_factory=list)
    dbInstance: dict = Field(default_factory=dict)
    albTargetHealth: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    redacted: bool = True


class ReconcileRecord(StrictRecord):
    """OP-STG-05 reconcile observation and action (visibility only)."""

    schemaVersion: str = "1.0"
    observedAt: UtcDateTime
    dbInstance: str
    dbStatus: str
    marker: OwnershipMarker | None = None
    action: Literal["none", "stopped"]
    conclusion: str


class StagingOperationRecord(StrictRecord):
    schemaVersion: str = "1.0"
    operationId: str
    candidate: StagingCandidateIdentity
    owner: str = Field(pattern=r"^[A-Za-z0-9@._-]{1,39}$")
    acquiredAt: UtcDateTime
    phase: Annotated[Phase, BeforeValidator(_coerce_phase)]
    completedAt: UtcDateTime | None = None
    database: DatabaseConclusions
    artifactsExpected: ExpectedArtifacts
    artifactsObserved: ObservedArtifacts
    compatibility: CompatibilityConclusion
    e2e: E2EConclusion
    cleanup: CleanupConclusion
    phaseLog: list[PhaseLog] = Field(default_factory=list)
    journeys: list[JourneyConclusion] = Field(default_factory=list)
    failure: FailureInfo | None = None
    diagnosticsPath: str | None = None
    e2eUrl: str | None = None
