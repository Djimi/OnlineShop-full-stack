"""Cross-field identity agreement rules for release manifests.

JSON Schema cannot express relationships between fields, so these rules are the
authoritative implementation of Decision 1 and the atomic-release identity
decisions: every component SHA equals ``release.sourceSha``, ``items`` also
records the same SHA for the included ``common`` library, and component
identities, versions, repositories, and tags all agree. Each rule emits a
deterministic issue (code, field, message) or None.
"""

from __future__ import annotations

from typing import Any

from . import components as rc

# Every rule returns None (pass) or an issue dict.
RuleResult = dict[str, str] | None


def _release_field(manifest: Any, field: str):
    if not isinstance(manifest, dict):
        return None
    release = manifest.get("release")
    if not isinstance(release, dict):
        return None
    return release.get(field)


def _component(manifest: Any, key: str):
    if not isinstance(manifest, dict):
        return None
    comps = manifest.get("components")
    if not isinstance(comps, dict):
        return None
    return comps.get(key)


def _rule(component: str, field: str, message: str, code: str = "FIELD_MISMATCH") -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _git_tag(manifest: Any) -> RuleResult:
    version = _release_field(manifest, "version")
    git_tag = _release_field(manifest, "gitTag")
    if not isinstance(version, str) or not isinstance(git_tag, str):
        return None
    expected = rc.git_tag_for(version)
    if git_tag != expected:
        return _rule(
            "release",
            "release.gitTag",
            f"git tag {git_tag!r} must equal v{version!r} ({expected!r})",
            "GIT_TAG_MISMATCH",
        )
    return None


def _component_shas(manifest: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    source_sha = _release_field(manifest, "sourceSha")
    if not isinstance(source_sha, str):
        return issues
    for key in rc.COMPONENT_KEYS:
        component = _component(manifest, key)
        if not isinstance(component, dict):
            continue
        for field in ("sourceSha", "commonSourceSha"):
            if field not in component:
                continue
            value = component[field]
            if value != source_sha:
                issues.append(
                    _rule(
                        key,
                        f"components.{key}.{field}",
                        f"{field} {value!r} must equal release.sourceSha {source_sha!r}",
                        "SHA_MISMATCH",
                    )
                )
    return issues


def _identities(manifest: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    version = _release_field(manifest, "version")
    if not isinstance(version, str):
        return issues
    for key in rc.COMPONENT_KEYS:
        component = _component(manifest, key)
        if not isinstance(component, dict):
            continue
        identity = component.get("identity")
        if not isinstance(identity, str):
            continue
        expected = rc.identity_for(key, version)
        if identity != expected:
            issues.append(
                _rule(
                    key,
                    f"components.{key}.identity",
                    f"identity {identity!r} must equal {expected!r} for "
                    f"component {key!r} at version {version!r}",
                    "IDENTITY_MISMATCH",
                )
            )
    return issues


def _repositories(manifest: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for key in rc.BACKEND_KEYS:
        component = _component(manifest, key)
        if not isinstance(component, dict):
            continue
        repository = component.get("repository")
        if not isinstance(repository, str):
            continue
        expected = rc.repository_for(key)
        if repository != expected:
            issues.append(
                _rule(
                    key,
                    f"components.{key}.repository",
                    f"repository {repository!r} must equal the canonical "
                    f"{expected!r} for component {key!r}",
                    "REPOSITORY_MISMATCH",
                )
            )
    return issues


def _sboms(manifest: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for key in rc.COMPONENT_KEYS:
        component = _component(manifest, key)
        if not isinstance(component, dict):
            continue
        sbom = component.get("sbom")
        if not isinstance(sbom, str):
            continue
        expected = rc.sbom_for(key)
        if sbom != expected:
            issues.append(
                _rule(
                    key,
                    f"components.{key}.sbom",
                    f"sbom {sbom!r} must equal the canonical {expected!r} for component {key!r}",
                    "SBOM_MISMATCH",
                )
            )
    return issues


def _candidate_tags(manifest: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    source_sha = _release_field(manifest, "sourceSha")
    if not isinstance(source_sha, str):
        return issues
    expected = rc.candidate_tag_for(source_sha)
    for key in rc.BACKEND_KEYS:
        component = _component(manifest, key)
        if not isinstance(component, dict):
            continue
        candidate_tag = component.get("candidateTag")
        if not isinstance(candidate_tag, str):
            continue
        if candidate_tag != expected:
            issues.append(
                _rule(
                    key,
                    f"components.{key}.candidateTag",
                    f"candidate tag {candidate_tag!r} must equal {expected!r} "
                    f"for source SHA {source_sha!r}",
                    "CANDIDATE_TAG_MISMATCH",
                )
            )
    return issues


def _release_tags(manifest: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    version = _release_field(manifest, "version")
    if not isinstance(version, str):
        return issues
    expected = rc.release_tag_for(version)
    for key in rc.BACKEND_KEYS:
        component = _component(manifest, key)
        if not isinstance(component, dict):
            continue
        release_tag = component.get("releaseTag")
        if not isinstance(release_tag, str):
            continue
        if release_tag != expected:
            issues.append(
                _rule(
                    key,
                    f"components.{key}.releaseTag",
                    f"release tag {release_tag!r} must equal {expected!r} for version {version!r}",
                    "RELEASE_TAG_MISMATCH",
                )
            )
    return issues


def _release_prefix(manifest: Any) -> RuleResult:
    frontend = _component(manifest, "frontend")
    version = _release_field(manifest, "version")
    if not isinstance(frontend, dict) or not isinstance(version, str):
        return None
    release_prefix = frontend.get("releasePrefix")
    if not isinstance(release_prefix, str):
        return None
    expected = rc.release_prefix_for(version)
    if release_prefix != expected:
        return _rule(
            "frontend",
            "components.frontend.releasePrefix",
            f"release prefix {release_prefix!r} must equal {expected!r} for version {version!r}",
            "RELEASE_PREFIX_MISMATCH",
        )
    return None


def _artifact(manifest: Any) -> RuleResult:
    frontend = _component(manifest, "frontend")
    if not isinstance(frontend, dict):
        return None
    artifact = frontend.get("artifact")
    if not isinstance(artifact, str):
        return None
    if artifact != rc.FRONTEND_ARTIFACT:
        return _rule(
            "frontend",
            "components.frontend.artifact",
            f"artifact {artifact!r} must equal the canonical {rc.FRONTEND_ARTIFACT!r}",
            "ARTIFACT_MISMATCH",
        )
    return None


def _version_marker(manifest: Any) -> RuleResult:
    frontend = _component(manifest, "frontend")
    if not isinstance(frontend, dict):
        return None
    marker = frontend.get("versionMarker")
    if not isinstance(marker, str):
        return None
    if marker != rc.VERSION_MARKER:
        return _rule(
            "frontend",
            "components.frontend.versionMarker",
            f"version marker {marker!r} must equal the canonical {rc.VERSION_MARKER!r}",
            "VERSION_MARKER_MISMATCH",
        )
    return None


def apply_cross_field_rules(manifest: Any) -> list[dict[str, str]]:
    """Apply all identity-agreement rules and return deterministic issues.

    Rules tolerate missing fields (early ``None``/skip) so callers may run them
    on structurally imperfect documents without index errors.
    """
    issues: list[dict[str, str]] = []
    single_rules = (
        _git_tag,
        _release_prefix,
        _artifact,
        _version_marker,
    )
    for rule in single_rules:
        issue = rule(manifest)
        if issue is not None:
            issues.append(issue)
    for batch in (
        _component_shas,
        _identities,
        _repositories,
        _sboms,
        _candidate_tags,
        _release_tags,
    ):
        issues.extend(batch(manifest))
    issues.sort(key=lambda item: (item["field"], item["code"]))
    return issues
