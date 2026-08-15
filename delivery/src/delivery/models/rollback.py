"""Rollback result record."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import PositiveInt, StrictRecord, UtcDateTime


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
