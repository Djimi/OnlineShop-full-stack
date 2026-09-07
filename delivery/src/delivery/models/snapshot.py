"""Production pre-mutation snapshot record (CT-AUDIT-01)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictRecord, UtcDateTime


class ServiceObservation(StrictRecord):
    deploymentId: str
    taskDefinitionArn: str
    runningDigests: list[str]
    health: str


class FrontendObservation(StrictRecord):
    immutableIdentity: str
    liveMarker: str
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    cloudfrontDistributionId: str


class ReleaseIdentity(StrictRecord):
    """Observed official release identity of the live frontend marker.

    ``status`` records honestly whether the live marker names an official
    release (CT-AUDIT-01). ``manifestSha256`` is set only when the observer
    can read the GitHub-owned official manifest bytes (CT-AUTH); it is never
    fabricated from the marker alone, so an unset value means "not observed
    by this flow", never "no manifest exists".
    """

    status: Literal["official", "none"]
    releaseId: str | None = Field(default=None, pattern=r"^release-\d{4}$")
    manifestSha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _status_matches_identity(self) -> ReleaseIdentity:
        if self.status == "official" and self.releaseId is None:
            raise ValueError("release status official requires releaseId")
        if self.status == "none" and (
            self.releaseId is not None or self.manifestSha256 is not None
        ):
            raise ValueError("release status none must not claim release identity")
        return self


class ProductionSnapshot(StrictRecord):
    schemaVersion: str = "1.0"
    environment: Literal["production", "staging"]
    snapshotId: str
    capturedAt: UtcDateTime
    release: ReleaseIdentity
    services: dict[str, ServiceObservation]
    frontend: FrontendObservation
    compatibilityFingerprint: str
