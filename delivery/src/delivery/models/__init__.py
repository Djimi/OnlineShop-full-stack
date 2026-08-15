"""Delivery record models: candidate, staging, snapshot, release, rollback, evidence."""

from .candidate import (
    CandidateArtifacts,
    CandidateManifest,
    CandidateTests,
    FrontendArtifact,
    ItemsArtifact,
)
from .common import ArtifactRef, Build, PositiveInt, Source, StrictRecord, UtcDateTime
from .evidence import EvidenceRecord, ExpectedObserved, FailureInfo, PhaseLog, evidence_for_failure
from .release import (
    ApprovalEvidence,
    ProductionVerification,
    ReleaseArtifacts,
    ReleaseFrontend,
    ReleaseManifest,
    ReleaseSource,
    SbomAsset,
    StagingEvidence,
)
from .rollback import RollbackResult
from .snapshot import FrontendObservation, ProductionSnapshot, ReleaseIdentity, ServiceObservation
from .staging import (
    CleanupConclusion,
    CompatibilityConclusion,
    DatabaseConclusions,
    E2EConclusion,
    ExpectedArtifacts,
    ObservedArtifacts,
    Phase,
    StagingCandidateIdentity,
    StagingOperationRecord,
)

__all__ = [
    "ApprovalEvidence",
    "ArtifactRef",
    "Build",
    "CandidateArtifacts",
    "CandidateManifest",
    "CandidateTests",
    "CleanupConclusion",
    "CompatibilityConclusion",
    "DatabaseConclusions",
    "E2EConclusion",
    "EvidenceRecord",
    "ExpectedArtifacts",
    "ExpectedObserved",
    "FailureInfo",
    "FrontendArtifact",
    "FrontendObservation",
    "ItemsArtifact",
    "ObservedArtifacts",
    "Phase",
    "PhaseLog",
    "PositiveInt",
    "ProductionSnapshot",
    "ProductionVerification",
    "ReleaseArtifacts",
    "ReleaseFrontend",
    "ReleaseIdentity",
    "ReleaseManifest",
    "ReleaseSource",
    "RollbackResult",
    "SbomAsset",
    "ServiceObservation",
    "Source",
    "StagingCandidateIdentity",
    "StagingEvidence",
    "StagingOperationRecord",
    "StrictRecord",
    "UtcDateTime",
    "evidence_for_failure",
]
