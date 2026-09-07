"""Official release manifest record (CT-REL-01)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import ArtifactRef, StrictRecord, UtcDateTime


class ReleaseSource(StrictRecord):
    fullSha: str = Field(pattern=r"^[0-9a-f]{40}$")
    branch: Literal["main"]


class ApprovalEvidence(StrictRecord):
    evidence: str
    workflowUrl: str = Field(pattern=r"^https://")


class ReleaseFrontend(StrictRecord):
    immutableIdentity: str
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class SbomAsset(StrictRecord):
    assetName: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SbomSet(StrictRecord):
    """Pinned Syft-generated SPDX JSON identity + SHA-256 per component
    (CT-REL-01 / SPEC §4.5.5): one asset for each of auth, items, gateway,
    and frontend."""

    auth: SbomAsset
    items: SbomAsset
    gateway: SbomAsset
    frontend: SbomAsset


class ReleaseArtifacts(StrictRecord):
    auth: ArtifactRef
    items: ArtifactRef
    gateway: ArtifactRef
    frontend: ReleaseFrontend
    sbom: SbomSet


class StagingEvidence(StrictRecord):
    evidenceIdentity: str
    conclusion: str


class ProductionVerification(StrictRecord):
    evidenceIdentity: str
    conclusion: str


class ReleaseManifest(StrictRecord):
    schemaVersion: str = "1.0"
    releaseId: str = Field(pattern=r"^release-\d{4}$")
    candidateId: str
    source: ReleaseSource
    previousReleaseId: str | None = None
    promotedAt: UtcDateTime
    requester: str
    approval: ApprovalEvidence
    artifacts: ReleaseArtifacts
    compatibilityFingerprint: str
    staging: StagingEvidence
    productionVerification: ProductionVerification
    rollbackCapableAtPublication: bool
