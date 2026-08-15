"""Common workflow evidence record (CT-AUDIT-01)."""

from __future__ import annotations

from pydantic import Field

from .common import StrictRecord, UtcDateTime


class PhaseLog(StrictRecord):
    name: str
    startedAt: UtcDateTime
    endedAt: UtcDateTime | None = None
    conclusion: str


class ExpectedObserved(StrictRecord):
    kind: str
    expected: str | None = None
    observed: str | None = None
    equal: bool


class FailureInfo(StrictRecord):
    environment: str | None = None
    failedPhase: str | None = None
    mutationBegan: bool
    cleanupConclusion: str | None = None
    recoveryConclusion: str | None = None


class EvidenceRecord(StrictRecord):
    schemaVersion: str = "1.0"
    evidenceId: str
    workflowRunId: int
    workflowRunAttempt: int
    workflowUrl: str = Field(pattern=r"^https://")
    candidateId: str | None = None
    releaseId: str | None = None
    owner: str | None = None
    requester: str | None = None
    approver: str | None = None
    phases: list[PhaseLog]
    expectedObserved: list[ExpectedObserved]
    awsRequestIds: list[str]
    failure: FailureInfo | None = None


def evidence_for_failure(
    *,
    environment: str | None = None,
    failedPhase: str | None = None,
    mutationBegan: bool = False,
    cleanupConclusion: str | None = None,
    recoveryConclusion: str | None = None,
) -> FailureInfo:
    return FailureInfo(
        environment=environment,
        failedPhase=failedPhase,
        mutationBegan=mutationBegan,
        cleanupConclusion=cleanupConclusion,
        recoveryConclusion=recoveryConclusion,
    )
