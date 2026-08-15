"""Staging operation record (CT-STG-01) and phase values (OP-STG-01)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BeforeValidator, Field

from .common import PositiveInt, StrictRecord, UtcDateTime


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
    conclusion: str


class CleanupConclusion(StrictRecord):
    conclusion: str


class StagingOperationRecord(StrictRecord):
    schemaVersion: str = "1.0"
    operationId: str
    candidate: StagingCandidateIdentity
    owner: str
    acquiredAt: UtcDateTime
    phase: Annotated[Phase, BeforeValidator(_coerce_phase)]
    completedAt: UtcDateTime | None = None
    database: DatabaseConclusions
    artifactsExpected: ExpectedArtifacts
    artifactsObserved: ObservedArtifacts
    compatibility: CompatibilityConclusion
    e2e: E2EConclusion
    cleanup: CleanupConclusion
