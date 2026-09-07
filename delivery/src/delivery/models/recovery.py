"""Automatic-recovery result record (AD-13, OP-REC-02, VR-REC-01).

Records the ORIGINAL failure and the RECOVERY outcome as separate fields;
a recovery that fails reports outcome ``failed`` with the recovery failure
detail and never claims success.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import StrictRecord, UtcDateTime


class ComponentRecovery(StrictRecord):
    component: Literal["auth", "items", "gateway", "frontend"]
    conclusion: Literal["restored", "failed", "not-attempted"]
    detail: str = ""


class RecoveryResult(StrictRecord):
    schemaVersion: str = "1.0"
    recoveryId: str
    snapshotId: str
    snapshotCapturedAt: UtcDateTime
    environment: Literal["production"]
    originalFailure: str = Field(min_length=1)
    startedAt: UtcDateTime
    completedAt: UtcDateTime | None = None
    outcome: Literal["completed", "failed"]
    components: list[ComponentRecovery]
    failureDetail: str | None = None
