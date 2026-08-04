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


def is_backend(component: str) -> bool:
    """Return True for backend components (auth, items, apiGateway)."""
    return component in BACKEND_KEYS


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
