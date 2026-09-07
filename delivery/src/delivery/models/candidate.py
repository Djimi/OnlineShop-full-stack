"""Candidate manifest record (CT-CAND-01)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import ArtifactRef, Build, Source, StrictRecord


class ItemsArtifact(ArtifactRef):
    commonSourceSha: str = Field(pattern=r"^[0-9a-f]{40}$")


class FrontendArtifact(StrictRecord):
    artifactId: str
    artifactDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contentChecksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class CandidateArtifacts(StrictRecord):
    auth: ArtifactRef
    items: ItemsArtifact
    gateway: ArtifactRef
    frontend: FrontendArtifact


class CandidateTests(StrictRecord):
    unit: str
    integration: str
    frontend: str
    localE2E: str


class CandidateManifest(StrictRecord):
    schemaVersion: str = "1.0"
    candidateId: str
    candidateClass: Literal["feature", "main"]
    source: Source
    build: Build
    artifacts: CandidateArtifacts
    tests: CandidateTests
    productionEligible: bool

    @model_validator(mode="after")
    def _feature_forbids_production(self) -> CandidateManifest:
        if self.candidateClass == "feature" and self.productionEligible:
            raise ValueError("candidateClass feature requires productionEligible false")
        return self
