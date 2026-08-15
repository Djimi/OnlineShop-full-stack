"""Retention audit/preview/apply records (OP-RET-01/02/03)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import StrictRecord, UtcDateTime


class RetentionAuditFailure(StrictRecord):
    kind: Literal[
        "GITHUB_RELEASE_NOT_FOUND",
        "MANIFEST_NOT_FOUND",
        "MANIFEST_INVALID",
        "MANIFEST_ID_MISMATCH",
        "ECR_TAG_NOT_FOUND",
        "ECR_DIGEST_MISMATCH",
        "PREFIX_MARKER_NOT_FOUND",
        "FINGERPRINT_MISMATCH",
        "READ_ERROR",
    ]
    message: str = ""


class RetentionAuditEntry(StrictRecord):
    releaseId: str = Field(pattern=r"^release-\d{4}$")
    inWindow: bool
    complete: bool
    releaseFingerprint: str | None = None
    failures: list[RetentionAuditFailure] = Field(default_factory=list)
    detail: str = ""


class RetentionAuditReport(StrictRecord):
    schemaVersion: str = "1.0"
    reportId: str
    producedAt: UtcDateTime
    environment: Literal["production"]
    currentReleaseId: str = Field(pattern=r"^release-\d{4}$")
    currentFingerprint: str
    windowComplete: bool
    releases: list[RetentionAuditEntry]


class RetentionPreviewRepo(StrictRecord):
    repository: str
    kind: Literal["live", "modeled"]
    reason: str
    protectedTags: list[str] = Field(default_factory=list)
    expiringDigests: list[str] = Field(default_factory=list)
    protectedExpiring: list[str] = Field(default_factory=list)
    agreement: Literal["agree", "disagree"] | None = None


class RetentionPreviewReport(StrictRecord):
    schemaVersion: str = "1.0"
    reportId: str
    producedAt: UtcDateTime
    environment: Literal["production"]
    policyKind: Literal["desired", "provided"]
    referenceDate: UtcDateTime
    windowComplete: bool
    protectedReleases: list[str] = Field(default_factory=list)
    repositories: list[RetentionPreviewRepo]


class RetentionApplyRepo(StrictRecord):
    repository: str
    action: Literal["put", "unchanged"]
    readBackVerified: bool


class RetentionApplyReport(StrictRecord):
    schemaVersion: str = "1.0"
    reportId: str
    producedAt: UtcDateTime
    environment: Literal["production"]
    policyKind: Literal["desired", "provided"]
    repositories: list[RetentionApplyRepo]
    preAuditWindowComplete: bool
    postAuditWindowComplete: bool
