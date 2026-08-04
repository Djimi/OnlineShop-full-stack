"""Release traceability lookups and consistency audit (Pass 3, subphase 3.7).

Read-only operator queries that answer, in both directions:

- ``by-sha``     commit SHA   -> candidate run, ECR digests, any official releases;
- ``by-version`` release      -> source SHA, components, evidence, SBOMs, artifacts
  (and the live cross-check of ECR ``release-<version>`` tags + the frontend marker);
- ``running``    running env  -> task-definition ARNs, **running** image digests
  (from ECS ``tasks[].containers[].imageDigest``, never only the task-definition
  tag/URI), release identity + approver/deployment run, and the frontend identity
  from the deployed immutable version marker (never cache headers). When
  production is intentionally paused and has no running tasks, the lookup
  reports the paused state, resolves the selected task-definition digests, and
  reports the last verified deployment evidence from the index -- it never
  fabricates a running digest;
- ``by-digest``  image digest -> ECR tags, OCI revision (cross-referenced from
  the release manifest, never claimed as an observed label read), candidate
  run, release identity;
- ``audit``      manifest <-> ECR digests/tags <-> ECS running digest <->
  frontend checksum consistency, reported without modifying anything.

Every lookup emits machine-readable JSON and fails closed: missing, ambiguous,
or contradictory mappings exit non-zero with deterministic
``{code, field, message}`` issues. A live AWS read that failed is recorded in
the observed state as an ``error`` marker and fails closed as
``OBSERVED_READ_ERROR`` (never disguised as a missing resource or drift).

All functions are pure and fixture-tested; the shell wrapper
(``release/bin/trace.sh``) gathers the live state and passes validated JSON
files. Only the exact manifest contract (``validate_data``) and the canonical
component map are trusted -- nothing security-sensitive is parsed with regex or
ad-hoc shell string concatenation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from . import components as rc
from .releaseid import frontend_marker as release_frontend_marker
from .semver import is_valid as is_valid_semver
from .validate import validate_data

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# ECS container name -> manifest component key (containers are named after the
# service: auth, items, api-gateway).
_CONTAINER_TO_KEY = {
    "auth": "auth",
    "items": "items",
    "api-gateway": "apiGateway",
    "apiGateway": "apiGateway",
}


@dataclass
class TraceResult:
    """Result of a traceability lookup.

    ``valid`` means the lookup is internally consistent and the live
    cross-checks (when observed state was supplied) agree; ``found`` means a
    mapping was actually located. ``data`` carries the machine-readable result;
    a non-empty ``issues`` list means fail closed regardless of ``found``.
    """

    valid: bool
    found: bool
    kind: str
    key: str
    data: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, str]] = field(default_factory=list)


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _digest_hex(digest: str) -> str:
    """Strip the ``sha256:`` prefix for checksum comparison helpers."""
    return digest[len("sha256:") :] if digest.startswith("sha256:") else digest


# ---------------------------------------------------------------------------
# Observed-state validation
# ---------------------------------------------------------------------------


def validate_index(index: Any) -> list[dict[str, str]]:
    """Validate the manifest index shape and each manifest against the contract.

    ``index``: ``{"repository": "<owner/repo>", "manifests": [<manifest>, ...]}``.
    Every manifest must be schema-valid and at most one manifest per
    (version, status) pair may exist.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(index, dict) or not isinstance(index.get("manifests"), list):
        return [_issue("INVALID_INDEX", "$", "index must be {repository, manifests: [...]}")]
    seen: set[tuple[str, str]] = set()
    for position, manifest in enumerate(index.get("manifests", [])):
        field_path = f"manifests[{position}]"
        result = validate_data(manifest)
        if not result.valid:
            issues.append(
                _issue(
                    "INDEX_MANIFEST_INVALID",
                    field_path,
                    f"index manifest is not schema-valid: {result.issues!r}",
                )
            )
            continue
        release = manifest.get("release", {})
        pair = (release.get("version"), release.get("status"))
        if pair in seen:
            issues.append(
                _issue(
                    "INDEX_DUPLICATE_MANIFEST",
                    field_path,
                    f"index contains more than one {release.get('status')} "
                    f"manifest for version {release.get('version')}",
                )
            )
        seen.add(pair)
    return issues


def _observed_error(observed: Any) -> str | None:
    """Return the first ``error`` marker in the observed state, if any.

    A live AWS read that failed is recorded by the shell as ``error: "<msg>"``
    (a genuine not-found is recorded as ``exists: false`` instead). Any such
    marker fails the lookup closed so an auth/throttle/network failure is never
    disguised as a missing resource or as drift.
    """

    def scan(value: Any, path: str) -> str | None:
        if isinstance(value, dict):
            error = value.get("error")
            if isinstance(error, str) and error:
                return f"{path}.error: {error}"
            for key, item in value.items():
                found = scan(item, f"{path}.{key}")
                if found:
                    return found
        elif isinstance(value, list):
            for index, item in enumerate(value):
                found = scan(item, f"{path}[{index}]")
                if found:
                    return found
        return None

    if observed is None:
        return None
    return scan(observed, "observed")


def _tag_digest_map(observed: Any) -> dict[str, dict[str, str]]:
    """Build ``repo -> tag -> imageDigest`` from observed ECR images."""
    result: dict[str, dict[str, str]] = {}
    ecr = observed.get("ecr") if isinstance(observed, dict) else None
    if not isinstance(ecr, dict):
        return result
    for repository, entry in ecr.items():
        result[repository] = {}
        images = entry.get("images") if isinstance(entry, dict) else entry
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            digest = image.get("imageDigest")
            if not isinstance(digest, str):
                continue
            for tag in image.get("imageTags") or []:
                if isinstance(tag, str):
                    result[repository][tag] = digest
    return result


def _index_manifests(index: Any) -> list[dict[str, Any]]:
    if not isinstance(index, dict) or not isinstance(index.get("manifests"), list):
        return []
    return [m for m in index["manifests"] if isinstance(m, dict)]


def _official_manifests(index: Any) -> list[dict[str, Any]]:
    return [m for m in _index_manifests(index) if m.get("release", {}).get("status") == "official"]


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, str]:
    release = manifest.get("release", {})
    return {
        "version": release.get("version", ""),
        "gitTag": release.get("gitTag", ""),
        "status": release.get("status", ""),
        "sourceSha": release.get("sourceSha", ""),
    }


def _run_summary(workflow: Any) -> dict[str, Any] | None:
    if not isinstance(workflow, dict):
        return None
    return {
        "runId": workflow.get("runId"),
        "runAttempt": workflow.get("runAttempt"),
        "url": workflow.get("url"),
        "event": workflow.get("event"),
        "ref": workflow.get("ref"),
    }


def _backend_digests(manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
    components = manifest.get("components", {})
    result: dict[str, dict[str, str]] = {}
    for key in rc.BACKEND_KEYS:
        comp = components.get(key, {})
        if comp.get("imageDigest"):
            result[key] = {
                "repository": comp.get("repository", ""),
                "imageDigest": comp.get("imageDigest", ""),
                "releaseTag": comp.get("releaseTag", ""),
            }
    return result


def _version_key(manifest: dict[str, Any]) -> tuple[int, int, int]:
    """Numeric ``(major, minor, patch)`` sort key for a schema-valid manifest.

    ``compare_semver`` returns only a sign (-1/0/1), so it cannot order more
    than two distinct versions; every official manifest in the index is already
    schema-valid canonical SemVer, so parsing numerically is safe.
    """
    parts = str(manifest.get("release", {}).get("version", "0.0.0")).split(".")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _latest_official(index: Any) -> dict[str, Any] | None:
    officials = _official_manifests(index)
    if not officials:
        return None
    return max(officials, key=_version_key)


def _frontend_matches(manifest: dict[str, Any], marker: Any) -> bool:
    if not isinstance(marker, dict):
        return False
    expected = release_frontend_marker(manifest)
    return all(marker.get(key) == expected[key] for key in expected)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def lookup_by_sha(index: Any, observed: Any, sha: str) -> TraceResult:
    """Commit SHA -> candidate run, ECR digests, and any official releases."""
    if not isinstance(sha, str) or not _FULL_SHA_RE.match(sha):
        return TraceResult(
            False,
            False,
            "by-sha",
            sha,
            {},
            [_issue("INVALID_SHA", "sha", f"invalid full commit SHA {sha!r}")],
        )
    issues = list(validate_index(index))
    read_error = _observed_error(observed)
    if read_error:
        issues.append(_issue("OBSERVED_READ_ERROR", "observed", read_error))

    candidate_tag = rc.candidate_tag_for(sha)
    tag_map = _tag_digest_map(observed)
    digests: dict[str, dict[str, str]] = {}
    for key in rc.BACKEND_KEYS:
        repository = rc.REPOSITORIES[key]
        digest = tag_map.get(repository, {}).get(candidate_tag)
        if digest:
            digests[key] = {"repository": repository, "imageDigest": digest}

    manifests = [m for m in _index_manifests(index) if m.get("release", {}).get("sourceSha") == sha]
    officials = [m for m in manifests if m.get("release", {}).get("status") == "official"]

    candidate_runs: list[dict[str, Any]] = []
    for manifest in manifests:
        workflow = manifest.get("release", {}).get("candidateWorkflow")
        if not isinstance(workflow, dict):
            continue
        summary = _run_summary(workflow)
        if summary not in candidate_runs:
            candidate_runs.append(summary)
    candidate_run = candidate_runs[0] if candidate_runs else None
    if len(candidate_runs) > 1:
        issues.append(
            _issue(
                "CANDIDATE_RUN_CONFLICT",
                "release.candidateWorkflow",
                f"manifests recording {sha} disagree on the candidate run: {candidate_runs!r}",
            )
        )

    if not digests and not manifests:
        issues.append(
            _issue(
                "NOT_FOUND",
                "sha",
                f"no ECR sha-<sha> image and no release manifest record the SHA {sha}",
            )
        )
        return TraceResult(False, False, "by-sha", sha, {}, issues)

    # A release manifest that records the SHA but whose ECR sha tag is absent
    # (e.g. retention expired) or resolves to different bytes is drift.
    for manifest in manifests:
        for key, comp in _backend_digests(manifest).items():
            observed_digest = digests.get(key, {}).get("imageDigest")
            if observed_digest is None:
                issues.append(
                    _issue(
                        "ECR_SHA_TAG_MISSING",
                        f"ecr.{comp['repository']}",
                        f"release {manifest['release']['version']} records "
                        f"{comp['imageDigest']} but no sha-<sha> tag resolves in ECR",
                    )
                )
            elif observed_digest != comp["imageDigest"]:
                issues.append(
                    _issue(
                        "ECR_SHA_DIGEST_MISMATCH",
                        f"ecr.{comp['repository']}.{candidate_tag}",
                        f"candidate tag {candidate_tag} resolves to {observed_digest}, "
                        f"release {manifest['release']['version']} records {comp['imageDigest']}",
                    )
                )

    data = {
        "sha": sha,
        "candidateTag": candidate_tag,
        "digests": digests,
        "candidateRun": candidate_run,
        "officialReleases": [_manifest_summary(m) for m in officials],
    }
    return TraceResult(not issues, True, "by-sha", sha, data, issues)


def lookup_by_version(index: Any, observed: Any, version: str) -> TraceResult:
    """Release version -> source SHA, components, evidence, SBOMs, artifacts."""
    if not is_valid_semver(version):
        return TraceResult(
            False,
            False,
            "by-version",
            version,
            {},
            [_issue("INVALID_VERSION", "version", f"invalid canonical SemVer {version!r}")],
        )
    issues = list(validate_index(index))
    read_error = _observed_error(observed)
    if read_error:
        issues.append(_issue("OBSERVED_READ_ERROR", "observed", read_error))

    matches = [m for m in _index_manifests(index) if m.get("release", {}).get("version") == version]
    if not matches:
        issues.append(
            _issue("RELEASE_NOT_FOUND", "version", f"no release manifest records version {version}")
        )
        return TraceResult(False, False, "by-version", version, {}, issues)
    if len(matches) > 1:
        issues.append(
            _issue(
                "AMBIGUOUS_VERSION",
                "version",
                f"more than one release manifest records version {version}",
            )
        )
        return TraceResult(False, False, "by-version", version, {}, issues)

    manifest = matches[0]
    release = manifest.get("release", {})
    components = manifest.get("components", {})
    tag_map = _tag_digest_map(observed)

    backend_components: dict[str, Any] = {}
    for key in rc.BACKEND_KEYS:
        comp = components.get(key, {})
        repository = comp.get("repository", "")
        release_tag = comp.get("releaseTag", "")
        expected_digest = comp.get("imageDigest", "")
        observed_digest = tag_map.get(repository, {}).get(release_tag)
        ecr_verified = bool(observed_digest) and observed_digest == expected_digest
        if observed_digest is not None and observed_digest != expected_digest:
            issues.append(
                _issue(
                    "ECR_RELEASE_DIGEST_MISMATCH",
                    f"ecr.{repository}.{release_tag}",
                    f"release tag {release_tag} resolves to {observed_digest}, "
                    f"manifest records {expected_digest}",
                )
            )
        elif observed_digest is None and isinstance(observed, dict) and "ecr" in observed:
            issues.append(
                _issue(
                    "ECR_RELEASE_TAG_MISSING",
                    f"ecr.{repository}",
                    f"release tag {release_tag} is absent in ECR; manifest "
                    f"records {expected_digest}",
                )
            )
        backend_components[key] = dict(comp)
        backend_components[key]["ecrVerified"] = ecr_verified

    frontend = components.get("frontend", {})
    frontend_marker_verified: bool | None = None
    frontend_marker_exists = False
    frontend_live = (
        observed.get("frontend", {}).get("liveMarker") if isinstance(observed, dict) else None
    )
    live_marker = frontend_live.get("marker") if isinstance(frontend_live, dict) else None
    # The deployed live marker belongs to the currently deployed release. For a
    # version that is not deployed, the live marker is intentionally n/a (the
    # immutable release-prefix marker is the per-release record) and never an
    # issue.
    if isinstance(frontend_live, dict) and frontend_live.get("exists"):
        if not isinstance(live_marker, dict):
            frontend_marker_exists = True
            issues.append(
                _issue(
                    "FRONTEND_MARKER_MISMATCH",
                    "frontend.liveMarker",
                    f"deployed release.json marker exists but is not a JSON object: "
                    f"{live_marker!r}",
                )
            )
        else:
            live_version = live_marker.get("version")
            if live_version == version:
                frontend_marker_exists = True
                frontend_marker_verified = _frontend_matches(manifest, live_marker)
                if not frontend_marker_verified:
                    issues.append(
                        _issue(
                            "FRONTEND_MARKER_MISMATCH",
                            "frontend.liveMarker",
                            f"deployed release.json marker does not match the release "
                            f"{version}: {live_marker!r}",
                        )
                    )

    # The immutable per-release prefix marker is the release's own record and
    # must exist and match regardless of what is deployed.
    prefix_marker_verified: bool | None = None
    frontend_observed = observed.get("frontend") if isinstance(observed, dict) else None
    if isinstance(frontend_observed, dict) and isinstance(
        frontend_observed.get("prefixMarkers"), dict
    ):
        prefix_key = (
            f"{frontend.get('releasePrefix', '')}{frontend.get('versionMarker', 'release.json')}"
        )
        prefix_entry = frontend_observed["prefixMarkers"].get(prefix_key)
        if isinstance(prefix_entry, dict):
            if not prefix_entry.get("exists"):
                issues.append(
                    _issue(
                        "FRONTEND_PREFIX_MARKER_MISSING",
                        f"frontend.prefixMarkers.{prefix_key}",
                        f"immutable frontend prefix marker {prefix_key} is absent for "
                        f"release {version}",
                    )
                )
            else:
                prefix_marker_verified = _frontend_matches(manifest, prefix_entry.get("marker"))
                if not prefix_marker_verified:
                    issues.append(
                        _issue(
                            "FRONTEND_PREFIX_MARKER_MISMATCH",
                            f"frontend.prefixMarkers.{prefix_key}",
                            f"immutable frontend prefix marker {prefix_key} does not match "
                            f"release {version}: {prefix_entry.get('marker')!r}",
                        )
                    )

    data = {
        "version": version,
        "status": release.get("status"),
        "gitTag": release.get("gitTag"),
        "sourceSha": release.get("sourceSha"),
        "repository": release.get("repository"),
        "components": {
            **backend_components,
            "frontend": {
                "identity": frontend.get("identity"),
                "sourceSha": frontend.get("sourceSha"),
                "artifact": frontend.get("artifact"),
                "sha256": frontend.get("sha256"),
                "sbom": frontend.get("sbom"),
                "releasePrefix": frontend.get("releasePrefix"),
                "versionMarker": frontend.get("versionMarker"),
            },
        },
        "evidence": {
            "candidateWorkflow": release.get("candidateWorkflow"),
            "artifactWorkflow": release.get("artifactWorkflow"),
            "stagingValidation": release.get("stagingValidation"),
            "promotionWorkflow": release.get("promotionWorkflow"),
        },
        "artifacts": {
            "sboms": [comp.get("sbom") for comp in components.values() if isinstance(comp, dict)],
            "frontend": {
                "artifact": frontend.get("artifact"),
                "sha256": frontend.get("sha256"),
                "releasePrefix": frontend.get("releasePrefix"),
                "versionMarker": frontend.get("versionMarker"),
            },
        },
        "live": {
            "ecrVerified": all(comp.get("ecrVerified") for comp in backend_components.values()),
            "frontendMarkerExists": frontend_marker_exists,
            "frontendMarkerVerified": frontend_marker_verified,
            "frontendPrefixMarkerVerified": prefix_marker_verified,
        },
    }
    return TraceResult(not issues, True, "by-version", version, data, issues)


def _collect_running_digests(running: Any) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Collapse ``tasks[].containers[].imageDigest`` into one digest per component.

    Returns ``(digests, issues)``. A component that reports more than one
    distinct digest across tasks (a mixed in-flight deployment) is an issue;
    a non-empty digest set that does not cover every backend component is
    incomplete and can never identify a release. Both fail closed so an
    operator is never shown a fabricated or ambiguous identity.
    """
    digests: dict[str, str] = {}
    issues: list[dict[str, str]] = []
    seen: dict[str, set[str]] = {}
    for task in running if isinstance(running, list) else []:
        if not isinstance(task, dict):
            continue
        for container in task.get("containers") or []:
            if not isinstance(container, dict):
                continue
            key = _CONTAINER_TO_KEY.get(container.get("name"))
            digest = container.get("imageDigest")
            if key is None or not isinstance(digest, str) or not _IMAGE_DIGEST_RE.match(digest):
                continue
            seen.setdefault(key, set()).add(digest)
            digests[key] = digest
    for key, values in seen.items():
        if len(values) > 1:
            issues.append(
                _issue(
                    "RUNNING_MIXED_DIGESTS",
                    f"ecs.running.{key}",
                    f"running tasks report more than one digest for component "
                    f"{key}: {sorted(values)!r}",
                )
            )
    if digests and set(digests) != set(rc.BACKEND_KEYS):
        issues.append(
            _issue(
                "RUNNING_DIGEST_INCOMPLETE",
                "ecs.running",
                f"running digests cover only {sorted(digests)!r}; all of "
                f"{list(rc.BACKEND_KEYS)!r} are required to identify a release",
            )
        )
    return digests, issues


def _running_release_match(index: Any, running_digests: dict[str, str]) -> list[dict[str, Any]]:
    if set(running_digests) != set(rc.BACKEND_KEYS):
        return []
    matches: list[dict[str, Any]] = []
    for manifest in _official_manifests(index):
        comps = manifest.get("components", {})
        if all(
            comps.get(key, {}).get("imageDigest") == digest
            for key, digest in running_digests.items()
        ):
            matches.append(manifest)
    return matches


def lookup_running(index: Any, observed: Any) -> TraceResult:
    """Running environment -> TD ARNs, running digests, release identity, approver.

    Running digests come from ECS ``tasks[].containers[].imageDigest`` -- never
    the task-definition tag or URI. Frontend identity comes from the deployed
    immutable ``release.json`` marker, never cache headers. Paused production is
    reported honestly (no running digest is fabricated).
    """
    issues = list(validate_index(index))
    read_error = _observed_error(observed)
    if read_error:
        issues.append(_issue("OBSERVED_READ_ERROR", "observed", read_error))

    if not isinstance(observed, dict):
        issues.append(_issue("INVALID_OBSERVED", "$", "observed state must be an object"))
        return TraceResult(False, False, "running", "production", {}, issues)

    ecs = observed.get("ecs", {})
    cluster = ecs.get("cluster") if isinstance(ecs, dict) else None
    services = ecs.get("services") if isinstance(ecs, dict) else {}
    running = ecs.get("running") if isinstance(ecs, dict) else None
    task_definitions = ecs.get("taskDefinitions") if isinstance(ecs, dict) else {}

    service_tds: dict[str, dict[str, str]] = {}
    if isinstance(services, dict):
        for service, info in services.items():
            if isinstance(info, dict) and info.get("taskDefinition"):
                service_tds[service] = {"taskDefinitionArn": info["taskDefinition"]}

    frontend_live = observed.get("frontend", {}).get("liveMarker")
    frontend_marker = None
    if isinstance(frontend_live, dict) and frontend_live.get("exists"):
        frontend_marker = frontend_live.get("marker")

    if not isinstance(running, list):
        issues.append(
            _issue(
                "INVALID_OBSERVED",
                "ecs.running",
                "ecs.running must be a list (an empty list is the paused signal)",
            )
        )
        return TraceResult(False, False, "running", "production", {}, issues)

    if not running:
        return _lookup_running_paused(
            index, cluster, service_tds, task_definitions, frontend_marker, issues
        )

    task_records: list[dict[str, Any]] = []
    for task in running:
        if not isinstance(task, dict):
            continue
        task_records.append(
            {
                "taskArn": task.get("taskArn"),
                "taskDefinitionArn": task.get("taskDefinitionArn"),
                "lastStatus": task.get("lastStatus"),
                "containers": task.get("containers"),
            }
        )
    running_digests, digest_issues = _collect_running_digests(running)
    issues.extend(digest_issues)

    release = None
    matches: list[dict[str, Any]] = []
    if not running_digests:
        issues.append(
            _issue(
                "RUNNING_DIGEST_UNAVAILABLE", "ecs.running", "no running container digest readable"
            )
        )
    elif not digest_issues:
        matches = _running_release_match(index, running_digests)
        if len(matches) > 1:
            issues.append(
                _issue(
                    "RUNNING_AMBIGUOUS",
                    "ecs.running",
                    f"running digests match more than one official release: "
                    f"{[m['release']['version'] for m in matches]!r}",
                )
            )
        release = matches[0] if matches else None
        if not release:
            issues.append(
                _issue(
                    "RUNNING_DIGEST_UNMATCHED",
                    "ecs.running",
                    f"running digests {running_digests!r} do not match any indexed "
                    f"official release",
                )
            )

    if release and frontend_marker is not None and not _frontend_matches(release, frontend_marker):
        issues.append(
            _issue(
                "FRONTEND_RUNNING_MISMATCH",
                "frontend.liveMarker",
                f"running containers match release {release['release']['version']} but the "
                f"deployed frontend marker reports {frontend_marker!r}",
            )
        )

    promotion = release.get("release", {}).get("promotionWorkflow") if release else None
    data: dict[str, Any] = {
        "cluster": cluster,
        "paused": False,
        "services": service_tds,
        "runningTasks": task_records,
        "runningDigests": running_digests,
        "frontend": {
            "markerExists": isinstance(frontend_live, dict) and frontend_live.get("exists"),
            "marker": frontend_marker,
        },
    }
    if release:
        data["releaseIdentity"] = {
            "version": release["release"]["version"],
            "gitTag": release["release"]["gitTag"],
            "sourceSha": release["release"]["sourceSha"],
            "componentIdentities": sorted(
                f"{rc.identity_prefix(k)}/{release['release']['version']}"
                for k in rc.COMPONENT_KEYS
            ),
            "deploymentRunId": promotion.get("runId") if promotion else None,
            "approver": promotion.get("approvedBy") if promotion else None,
            "deployedAt": promotion.get("deployedAt") if promotion else None,
        }
    return TraceResult(not issues, True, "running", "production", data, issues)


def _lookup_running_paused(
    index: Any,
    cluster: str | None,
    service_tds: dict[str, dict[str, str]],
    task_definitions: Any,
    frontend_marker: Any,
    issues: list[dict[str, str]],
) -> TraceResult:
    """Paused production: report the state, TD digests, and last verified evidence.

    No running digest is ever fabricated. Task-definition digests are resolved
    from the services' current task-definition revisions; "last verified
    deployment evidence" is the most recent official release in the index
    (cross-checked against the deployed frontend marker when present).
    """
    td_digests: dict[str, dict[str, Any]] = {}
    for service, info in service_tds.items():
        td_arn = info.get("taskDefinitionArn", "")
        family_rev = td_arn.rsplit("task-definition/", 1)[-1] if td_arn else None
        digest = None
        if isinstance(task_definitions, dict) and family_rev:
            digest = task_definitions.get(family_rev, {}).get("imageDigest")
        td_digests[service] = {"taskDefinitionArn": td_arn, "imageDigest": digest}
        if not digest:
            issues.append(
                _issue(
                    "TASK_DEFINITION_DIGEST_UNAVAILABLE",
                    f"ecs.taskDefinitions.{family_rev or '?'}",
                    f"cannot resolve the image digest of the current task definition for {service}",
                )
            )

    latest = _latest_official(index)
    last_verified: dict[str, Any] | None = None
    if latest:
        promotion = latest.get("release", {}).get("promotionWorkflow", {})
        last_verified = {
            "version": latest["release"]["version"],
            "gitTag": latest["release"]["gitTag"],
            "sourceSha": latest["release"]["sourceSha"],
            "frontendSha256": latest.get("components", {}).get("frontend", {}).get("sha256"),
            "deploymentRunId": promotion.get("runId"),
            "approver": promotion.get("approvedBy"),
            "deployedAt": promotion.get("deployedAt"),
            "markerMatchesDeployed": (
                _frontend_matches(latest, frontend_marker) if frontend_marker is not None else None
            ),
        }

    data: dict[str, Any] = {
        "cluster": cluster,
        "paused": True,
        "services": service_tds,
        "taskDefinitions": td_digests,
        "lastVerifiedDeployment": last_verified,
        "frontend": {"markerExists": frontend_marker is not None, "marker": frontend_marker},
    }
    return TraceResult(not issues, True, "running", "production", data, issues)


def lookup_by_digest(index: Any, observed: Any, digest: str) -> TraceResult:
    """Image digest -> ECR tags, OCI revision, candidate run, release identity."""
    if not isinstance(digest, str) or not _IMAGE_DIGEST_RE.match(digest):
        return TraceResult(
            False,
            False,
            "by-digest",
            digest,
            {},
            [_issue("INVALID_DIGEST", "digest", f"invalid image digest {digest!r}")],
        )
    issues = list(validate_index(index))
    read_error = _observed_error(observed)
    if read_error:
        issues.append(_issue("OBSERVED_READ_ERROR", "observed", read_error))

    ecr_matches: list[dict[str, Any]] = []
    ecr = observed.get("ecr") if isinstance(observed, dict) else None
    if isinstance(ecr, dict):
        for repository, entry in ecr.items():
            images = entry.get("images") if isinstance(entry, dict) else entry
            if not isinstance(images, list):
                continue
            for image in images:
                if isinstance(image, dict) and image.get("imageDigest") == digest:
                    ecr_matches.append(
                        {
                            "repository": repository,
                            "tags": sorted(image.get("imageTags") or []),
                            "imagePushedAt": image.get("imagePushedAt"),
                        }
                    )
    ecr_matches.sort(key=lambda item: (item["repository"], item["imagePushedAt"] or ""))

    releases: list[dict[str, str]] = []
    for manifest in _official_manifests(index):
        if any(
            comp.get("imageDigest") == digest
            for comp in manifest.get("components", {}).values()
            if isinstance(comp, dict)
        ):
            releases.append(_manifest_summary(manifest))

    if not ecr_matches and not releases:
        issues.append(
            _issue(
                "NOT_FOUND",
                "digest",
                f"no ECR tag and no release manifest record the digest {digest}",
            )
        )
        return TraceResult(False, False, "by-digest", digest, {}, issues)

    source_shas = {m["sourceSha"] for m in releases}
    if len(source_shas) > 1:
        issues.append(
            _issue(
                "AMBIGUOUS_DIGEST",
                "digest",
                f"digest {digest} is recorded by releases with different source SHAs: "
                f"{sorted(source_shas)!r}",
            )
        )

    oci_revision = None
    oci_revision_source = None
    candidate_run = None
    if releases:
        sha = releases[0]["sourceSha"]
        # ECR describe-images cannot read the image config blob, so the
        # org.opencontainers.image.revision label is NOT observed here. The
        # 3.2 build contract records revision == release.sourceSha on every
        # backend image, so the source SHA is cross-referenced from the release
        # manifest and attributed as such -- it is never claimed to be a live
        # label read (a real label read-back is a consolidated-pass task).
        oci_revision = sha
        oci_revision_source = "release-manifest"
        for manifest in _index_manifests(index):
            if manifest.get("release", {}).get("sourceSha") == sha:
                workflow = manifest.get("release", {}).get("candidateWorkflow")
                if isinstance(workflow, dict):
                    candidate_run = _run_summary(workflow)
                    break

    data = {
        "digest": digest,
        "ecr": ecr_matches,
        "ociRevision": oci_revision,
        "ociRevisionSource": oci_revision_source,
        "ociRevisionObservedFromImage": False,
        "candidateRun": candidate_run,
        "releaseIdentity": releases,
    }
    return TraceResult(not issues, True, "by-digest", digest, data, issues)


def audit_consistency(index: Any, observed: Any, version: str | None = None) -> TraceResult:
    """Manifest <-> ECR <-> ECS running digest <-> frontend checksum audit.

    Read-only: reports drift with deterministic issue codes and never modifies
    anything. Without ``version`` every official release in the index is
    audited; the latest official release is also compared against the running
    environment (when running tasks exist) and the deployed frontend marker.
    """
    issues = list(validate_index(index))
    read_error = _observed_error(observed)
    if read_error:
        issues.append(_issue("OBSERVED_READ_ERROR", "observed", read_error))

    officials = _official_manifests(index)
    if version is not None:
        if not is_valid_semver(version):
            issues.append(_issue("INVALID_VERSION", "version", f"invalid SemVer {version!r}"))
            return TraceResult(False, False, "audit", version or "all", {}, issues)
        officials = [m for m in officials if m["release"]["version"] == version]
        if not officials:
            issues.append(
                _issue("RELEASE_NOT_FOUND", "version", f"no official release records {version}")
            )
            return TraceResult(False, False, "audit", version, {}, issues)

    tag_map = _tag_digest_map(observed)
    ecs = observed.get("ecs", {}) if isinstance(observed, dict) else {}
    running = ecs.get("running") if isinstance(ecs, dict) else None
    if running is not None and not isinstance(running, list):
        issues.append(
            _issue(
                "INVALID_OBSERVED",
                "ecs.running",
                "ecs.running must be a list when present",
            )
        )
        running = []
    running_digests, digest_issues = _collect_running_digests(running)
    issues.extend(digest_issues)

    # The running environment can only match one release at a time. A matched
    # release is the ECS-consistent one; a complete digest set that matches NO
    # official release is drift (top-level issue). A mixed or incomplete set
    # already failed closed above and is never matched. Older releases are not
    # expected to match the running environment, so they are never flagged.
    running_matches: list[dict[str, Any]] = []
    if running_digests and not digest_issues:
        running_matches = _running_release_match(index, running_digests)
        if not running_matches:
            issues.append(
                _issue(
                    "RUNNING_DIGEST_UNMATCHED",
                    "ecs.running",
                    f"running digests {running_digests!r} do not match any indexed "
                    f"official release",
                )
            )
    running_match_versions = {m["release"]["version"] for m in running_matches}

    frontend = observed.get("frontend", {}) if isinstance(observed, dict) else {}
    live_marker = frontend.get("liveMarker") if isinstance(frontend, dict) else None
    prefix_markers = frontend.get("prefixMarkers") if isinstance(frontend, dict) else {}
    if not isinstance(prefix_markers, dict):
        prefix_markers = {}

    per_release: list[dict[str, Any]] = []
    # Newest release first.
    for manifest in sorted(officials, key=_version_key, reverse=True):
        release_issues: list[dict[str, str]] = []
        version_label = manifest["release"]["version"]
        comps = manifest.get("components", {})

        for key in rc.BACKEND_KEYS:
            comp = comps.get(key, {})
            repository = comp.get("repository", "")
            expected_digest = comp.get("imageDigest", "")
            sha_tag = comp.get("candidateTag", "")
            release_tag = comp.get("releaseTag", "")
            repo_map = tag_map.get(repository, {})
            if repo_map.get(sha_tag) != expected_digest:
                observed_digest = repo_map.get(sha_tag)
                if observed_digest is None:
                    code = "ECR_CANDIDATE_TAG_MISSING"
                    message = f"candidate tag {sha_tag} is absent in {repository}"
                else:
                    code = "ECR_CANDIDATE_DIGEST_MISMATCH"
                    message = (
                        f"candidate tag {sha_tag} resolves to {observed_digest}, "
                        f"expected {expected_digest}"
                    )
                release_issues.append(_issue(code, f"ecr.{repository}.{sha_tag}", message))
            if repo_map.get(release_tag) != expected_digest:
                observed_digest = repo_map.get(release_tag)
                if observed_digest is None:
                    code = "ECR_RELEASE_TAG_MISSING"
                    message = f"release tag {release_tag} is absent in {repository}"
                else:
                    code = "ECR_RELEASE_DIGEST_MISMATCH"
                    message = (
                        f"release tag {release_tag} resolves to {observed_digest}, "
                        f"expected {expected_digest}"
                    )
                release_issues.append(_issue(code, f"ecr.{repository}.{release_tag}", message))

        frontend_comp = comps.get("frontend", {})
        prefix = frontend_comp.get("releasePrefix", "")
        marker_key = f"{prefix}{frontend_comp.get('versionMarker', 'release.json')}"
        # The deployed (live) marker only belongs to the release currently
        # deployed; for every other release it is intentionally n/a and never
        # an issue. The immutable per-release prefix marker is checked always.
        if isinstance(live_marker, dict) and live_marker.get("exists"):
            live_marker_version = (
                live_marker.get("marker", {}).get("version")
                if isinstance(live_marker.get("marker"), dict)
                else None
            )
            if not isinstance(live_marker.get("marker"), dict):
                release_issues.append(
                    _issue(
                        "FRONTEND_MARKER_MISMATCH",
                        "frontend.liveMarker",
                        f"deployed release.json marker exists but is not a JSON object: "
                        f"{live_marker.get('marker')!r}",
                    )
                )
            elif live_marker_version == version_label and not _frontend_matches(
                manifest, live_marker.get("marker")
            ):
                release_issues.append(
                    _issue(
                        "FRONTEND_MARKER_MISMATCH",
                        "frontend.liveMarker",
                        f"deployed release.json marker does not match release {version_label}",
                    )
                )
        prefix_entry = prefix_markers.get(marker_key)
        if isinstance(prefix_entry, dict):
            if not prefix_entry.get("exists"):
                release_issues.append(
                    _issue(
                        "FRONTEND_PREFIX_MARKER_MISSING",
                        f"frontend.prefixMarkers.{marker_key}",
                        f"immutable frontend prefix marker {marker_key} is absent",
                    )
                )
            elif not _frontend_matches(manifest, prefix_entry.get("marker")):
                release_issues.append(
                    _issue(
                        "FRONTEND_PREFIX_MARKER_MISMATCH",
                        f"frontend.prefixMarkers.{marker_key}",
                        f"immutable frontend prefix marker {marker_key} does not match "
                        f"release {version_label}",
                    )
                )
        elif isinstance(observed, dict) and "frontend" in observed:
            release_issues.append(
                _issue(
                    "FRONTEND_PREFIX_MARKER_UNVERIFIABLE",
                    f"frontend.prefixMarkers.{marker_key}",
                    f"no observed state for the immutable frontend prefix marker {marker_key}",
                )
            )

        per_release.append(
            {
                "version": version_label,
                "status": "official",
                "valid": not release_issues,
                "issues": release_issues,
                "checks": {
                    "ecr": not any(issue["code"].startswith("ECR_") for issue in release_issues),
                    # True = this is the release the running environment matches;
                    # False = running exists but is a different release (informational);
                    # None = paused (no running tasks).
                    "ecs": ((version_label in running_match_versions) if running_digests else None),
                    "frontend": not any(
                        issue["code"].startswith("FRONTEND_") for issue in release_issues
                    ),
                },
            }
        )
        issues.extend(release_issues)

    latest = _latest_official(index)
    running_summary = None
    if running_digests:
        running_summary = {
            "digests": running_digests,
            "matches": sorted(m["release"]["version"] for m in running_matches),
        }
        if latest and not running_matches:
            running_summary["closestLatest"] = all(
                latest.get("components", {}).get(key, {}).get("imageDigest") == digest
                for key, digest in running_digests.items()
            )

    data = {
        "audited": per_release,
        "running": running_summary,
        "frontendLiveMarker": (
            live_marker if isinstance(live_marker, dict) and live_marker.get("exists") else None
        ),
    }
    return TraceResult(not issues, True, "audit", version or "all", data, issues)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print_result(result: TraceResult, human: bool) -> None:
    output = {
        "kind": result.kind,
        "key": result.key,
        "valid": result.valid,
        "found": result.found,
        "data": result.data,
        "issues": result.issues,
    }
    if human:
        for issue in result.issues:
            print(f"[{issue['code']}] {issue['field']}: {issue['message']}", file=sys.stderr)
        if result.valid and result.found:
            print(f"{result.kind} lookup succeeded (key={result.key})", file=sys.stderr)
    print(json.dumps(output, indent=2, sort_keys=True))


def _cmd(args: argparse.Namespace) -> int:
    if not os.path.isfile(args.index):
        print(f"ERROR: index file not found: {args.index}", file=sys.stderr)
        return 2
    if not os.path.isfile(args.observed):
        print(f"ERROR: observed-state file not found: {args.observed}", file=sys.stderr)
        return 2
    try:
        index = _read_json(args.index)
        observed = _read_json(args.observed)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in index/observed input: {exc}", file=sys.stderr)
        return 2
    if args.command == "running":
        result = lookup_running(index, observed)
    elif args.command == "audit":
        result = audit_consistency(index, observed, args.version)
    else:
        result = globals()[f"lookup_{args.command.replace('-', '_')}"](index, observed, args.key)
    _print_result(result, args.human)
    return 0 if result.valid and result.found else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.traceability",
        description="Release traceability lookups and consistency audit (Pass 3, subphase 3.7).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("by-sha", "by-version", "running", "by-digest", "audit"):
        subparser = sub.add_parser(name, help=f"{name} lookup/audit")
        if name == "audit":
            subparser.add_argument(
                "--version", metavar="SEMVER", help="audit only this official release version"
            )
        elif name != "running":
            subparser.add_argument("key", metavar="KEY", help="lookup key (SHA/version/digest)")
        subparser.add_argument("--index", required=True, metavar="JSON", help="manifest index file")
        subparser.add_argument(
            "--observed", required=True, metavar="JSON", help="observed-state file"
        )
        subparser.add_argument(
            "--human", action="store_true", help="also print a human view to stderr"
        )
        subparser.set_defaults(func=_cmd)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
