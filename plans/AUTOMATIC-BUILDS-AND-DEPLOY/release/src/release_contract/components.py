"""Canonical monorepo component/repository mapping and identity derivation.

One SemVer input identifies one source commit and all four deployable
components. Human identities are component-scoped (``auth/1.2.1``,
``items/1.2.1``, ``api-gateway/1.2.1``, ``frontend/1.2.1``) while the GitHub
release/git tag is ``v1.2.1`` (Decision 1). These helpers keep every derived
identity, tag, and repository value deterministic and shared across phases.
"""

from __future__ import annotations

# Canonical component keys as they appear under ``manifest.components``.
COMPONENT_KEYS = ("auth", "items", "apiGateway", "frontend")

# Backend components are published as ECR images; frontend is a static archive.
BACKEND_KEYS = ("auth", "items", "apiGateway")

# Component key -> identity prefix (identity = "<prefix>/<version>").
IDENTITY_PREFIXES = {
    "auth": "auth",
    "items": "items",
    "apiGateway": "api-gateway",
    "frontend": "frontend",
}

# Component key -> canonical ECR repository name (frontend has none).
REPOSITORIES = {
    "auth": "onlineshop-auth",
    "items": "onlineshop-items",
    "apiGateway": "onlineshop-api-gateway",
}

# Component key -> SPDX SBOM asset file name.
SBOM_FILES = {
    "auth": "auth.spdx.json",
    "items": "items.spdx.json",
    "apiGateway": "api-gateway.spdx.json",
    "frontend": "frontend.spdx.json",
}

# Frontend archive and marker constants from the manifest contract.
FRONTEND_ARTIFACT = "frontend-dist.tar.gz"
VERSION_MARKER = "release.json"

# --- OCI image labels (subphase 3.2) ---
# Standard Open Containers annotations:
# https://github.com/opencontainers/image-spec/blob/main/annotations.md
OCI_REVISION = "org.opencontainers.image.revision"
OCI_SOURCE = "org.opencontainers.image.source"
OCI_CREATED = "org.opencontainers.image.created"
OCI_TITLE = "org.opencontainers.image.title"

# Project-specific labels: component identity, a dynamic build-run label, and
# the canonical-producer identity (Decision 11). A rerun may reuse an existing
# ``sha-<full-sha>`` image only when these producer labels identify a trusted
# successful ``main`` push; ``created``/``build-run`` are intentionally dynamic
# so they are never compared for reuse decisions.
COMPONENT_LABEL = "org.onlineshop.component"
BUILD_RUN_LABEL = "org.onlineshop.build-run"
PRODUCER_RUN_ID = "org.onlineshop.producer.run-id"
PRODUCER_RUN_ATTEMPT = "org.onlineshop.producer.run-attempt"
PRODUCER_EVENT = "org.onlineshop.producer.event"
PRODUCER_REF = "org.onlineshop.producer.ref"

# Items embeds the same monorepo SHA as the included `common` library revision.
COMMON_REVISION_LABEL = "org.onlineshop.common-revision"

# Human-readable title per backend component.
COMPONENT_TITLES = {
    "auth": "OnlineShop auth service",
    "items": "OnlineShop items service",
    "apiGateway": "OnlineShop API gateway",
}

# The only producer identity accepted for reuse is a push on the default branch.
TRUSTED_EVENT = "push"
TRUSTED_REF = "refs/heads/main"

# --- ECR tag families (subphase 3.3) ---
# Backend repositories are configured `IMMUTABLE_WITH_EXCLUSION`: every tag is
# immutable by default (SHA and `release-*` must never be overwritten), with
# narrowly scoped mutable exclusions only for the convenience tags below. These
# constants are the single source of truth for the desired ECR repository
# configuration, the workflow tag computation, and the offline gate.
MUTABLE_CONVENIENCE_TAGS = ("main-latest", "branch-*")
IMMUTABLE_TAG_PREFIXES = ("sha-", "release-")
LATEST_TAG = "latest"
# Decision 4: `latest` is absent for v1. If it is ever added it must become an
# explicit mutable exclusion and move only after the official GitHub Release is
# published and production verification succeeds.
LATEST_ABSENT = True


def is_mutable_convenience_tag(tag: str) -> bool:
    """Return True for the only tags allowed to advance: ``main-latest`` and
    ``branch-*``."""
    return tag == "main-latest" or tag.startswith("branch-")


def is_immutable_tag(tag: str) -> bool:
    """Return True for the immutable tag families ``sha-*`` and ``release-*``."""
    return tag.startswith("sha-") or tag.startswith("release-")


def ecr_repository_arn(repository: str, region: str, account_id: str) -> str:
    """Return the ECR repository ARN for a canonical backend repository."""
    return f"arn:aws:ecr:{region}:{account_id}:repository/{repository}"


def ecr_repository_arns(region: str, account_id: str) -> list[str]:
    """Return the ARNs of all backend ECR repositories (least privilege
    scoping target for every ECR action except ``ecr:GetAuthorizationToken``)."""
    return [
        ecr_repository_arn(repository, region, account_id) for repository in REPOSITORIES.values()
    ]


def is_backend(component: str) -> bool:
    """Return True for backend components (auth, items, apiGateway)."""
    return component in BACKEND_KEYS


def oci_labels(
    component: str,
    *,
    sha: str,
    source: str,
    created: str,
    run_id: int,
    run_attempt: int,
    event: str,
    ref: str,
) -> dict[str, str]:
    """Return the OCI + project labels embedded in a backend image.

    ``created`` is dynamic per build (the image digest therefore cannot be
    reproduced by a rerun, which is why canonical bytes are reused, never
    rebuilt). For Items the same monorepo SHA is recorded as the ``common``
    library revision.
    """
    if component not in COMPONENT_TITLES:
        raise ValueError(f"unknown backend component: {component!r}")
    labels = {
        OCI_REVISION: sha,
        OCI_SOURCE: source,
        OCI_CREATED: created,
        OCI_TITLE: COMPONENT_TITLES[component],
        COMPONENT_LABEL: identity_prefix(component),
        BUILD_RUN_LABEL: build_run_label(run_id, run_attempt),
        PRODUCER_RUN_ID: str(run_id),
        PRODUCER_RUN_ATTEMPT: str(run_attempt),
        PRODUCER_EVENT: event,
        PRODUCER_REF: ref,
    }
    if component == "items":
        labels[COMMON_REVISION_LABEL] = sha
    return labels


def build_run_label(run_id: int, run_attempt: int) -> str:
    """Return the ``<run-id>-<run-attempt>`` build-run label value."""
    return f"{run_id}-{run_attempt}"


def run_url(repository: str, run_id: int, run_attempt: int | None = None) -> str:
    """Return the GitHub Actions run URL for the given run id/attempt."""
    base = f"https://github.com/{repository}/actions/runs/{run_id}"
    return f"{base}/attempts/{run_attempt}" if run_attempt is not None else base


def identity_prefix(component: str) -> str:
    """Return the identity prefix for a component key."""
    try:
        return IDENTITY_PREFIXES[component]
    except KeyError as exc:
        raise ValueError(f"unknown component: {component!r}") from exc


def identity_for(component: str, version: str) -> str:
    """Return the component-scoped identity, e.g. ``auth/1.2.1``."""
    return f"{identity_prefix(component)}/{version}"


def repository_for(component: str) -> str | None:
    """Return the canonical ECR repository name, or None for the frontend."""
    return REPOSITORIES.get(component)


def sbom_for(component: str) -> str:
    """Return the canonical SPDX SBOM asset name for a component."""
    try:
        return SBOM_FILES[component]
    except KeyError as exc:
        raise ValueError(f"unknown component: {component!r}") from exc


def candidate_tag_for(source_sha: str) -> str:
    """Return the immutable candidate ECR tag ``sha-<full-sha>``."""
    return f"sha-{source_sha}"


def release_tag_for(version: str) -> str:
    """Return the immutable ECR release tag ``release-<version>``."""
    return f"release-{version}"


def git_tag_for(version: str) -> str:
    """Return the GitHub release/git tag ``v<version>``."""
    return f"v{version}"


def release_prefix_for(version: str) -> str:
    """Return the immutable frontend S3 release prefix ``_releases/v<version>/``."""
    return f"_releases/v{version}/"
