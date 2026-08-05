"""Owner-approved application rollback decisions (Pass 3, subphase 3.6).

Subphase 3.6 is the approved, approval-gated rollback of production to an
existing immutable official release. It is the reverse of subphase 3.4
promotion: the exact historical ECR digests and the retained immutable frontend
prefix of an already-published official release are re-deployed — nothing is
rebuilt, no new official release is created, and mutable ``release-*``/``sha-*``
tags are never minted or moved (the rollback IAM policy deliberately has no
``ecr:PutImage``).

The decisions cover:

- ``dispatch_issues``           — the dispatch input (the target ``version``)
                                  before any GitHub/AWS read.
- ``selection_issues``          — resolve the target only from the intersection
                                  of the latest 10 complete official release
                                  sets across all backend ECR repositories and
                                  the frontend prefix. Rejects unknown,
                                  non-official, draft/tampered, metadata-only /
                                  partially retained, outside-window, and
                                  already-running releases.
- ``schema_compatibility_issues`` — the Decision 8 database-compatibility guard:
                                  a rollback involving a schema change is
                                  blocked until the forward/backward-compatible
                                  migration and recovery procedure has been
                                  reviewed. The database is never reversed.
- ``snapshot_issues``           — pre-rollback snapshot (identical contract to
                                  promotion; reused).
- ``deployment_plan_issues``    — deploy ordering + safe-rolling parameters
                                  (identical contract to promotion; reused).
- ``waiter_verified``           — a deployment bound to the task-definition/
                                  deployment started by this run (reused).
- ``frontend_restore_issues``   — restore-only frontend plan: the live root is
                                  re-pointed to the retained immutable prefix,
                                  no ``--delete``, no new prefix, marker + index
                                  last, invalidation required.
- ``verification_issues``       — post-rollback verification (identical contract
                                  to promotion, against the deployment manifest
                                  that carries the newly registered
                                  task-definition ARNs; reused).
- ``result_issues``             — the rollback result/audit artifact recording
                                  requester, approver, from/to releases, exact
                                  artifacts, timestamps, workflow URL and
                                  outcome. Idempotently resumable, any conflict
                                  fails closed. The immutable original release
                                  manifest is never edited.
- ``compensation_steps``        — on failure, restore changed components to the
                                  pre-rollback snapshot (reused).

Shell wrappers gather live state (GitHub Releases via ``gh``, ECR/ECS/S3/ALB via
``aws``) and pass validated JSON files into this module; nothing
security-sensitive is parsed with regex or ad-hoc shell string concatenation.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from . import components as rc
from .promotion import (
    compensation_steps,
    deployment_plan_issues,
    snapshot_issues,
    verification_issues,
    waiter_verified,
)
from .semver import is_valid as is_valid_semver
from .traceability import validate_index
from .validate import validate_data

# Canonical production service name -> manifest component key (identical to
# promotion; the ECS services are named after the microservice).
_SERVICE_TO_COMPONENT = {
    "onlineshop-auth": "auth",
    "onlineshop-items": "items",
    "onlineshop-api-gateway": "apiGateway",
}

# ECS container name -> manifest component key.
_CONTAINER_TO_KEY = {
    "auth": "auth",
    "items": "items",
    "api-gateway": "apiGateway",
    "apiGateway": "apiGateway",
}

# The rollback window: a target must be one of the latest N complete official
# release sets (retention keeps the immediate 10-release rollback window).
ROLLBACK_WINDOW = 10

# Valid outcomes for the rollback result/audit record.
RESULT_OUTCOMES = ("success", "compensated", "mixed-state-incident")


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


@dataclass
class Decision:
    """Result of a rollback decision.

    ``valid`` is False when any issue exists. Some decisions carry an ``action``
    (e.g. ``write``/``resume``) for the caller to branch on; an issue always
    means fail closed regardless of the action.
    """

    valid: bool
    action: str = ""
    issues: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "valid": self.valid,
            "action": self.action,
            "issues": self.issues,
        }
        return output


# ---------------------------------------------------------------------------
# Dispatch input validation
# ---------------------------------------------------------------------------


def dispatch_issues(version: Any) -> Decision:
    """Validate the manual rollback dispatch input.

    ``version`` must be canonical v1 SemVer naming an *existing* official
    release. An image tag, digest, SHA, or arbitrary version is never accepted
    as a dispatch input (Decision 2: SHA/digest are authoritative and never
    typed by hand; a release label only selects a release, never bytes).
    """
    issues: list[dict[str, str]] = []
    if not is_valid_semver(version):
        issues.append(_issue("INVALID_VERSION", "version", f"invalid canonical SemVer {version!r}"))
    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Rollback-window selection
# ---------------------------------------------------------------------------


def _officials_sorted(index: Any) -> list[dict[str, Any]]:
    """Official manifests sorted newest-first by numeric SemVer.

    Ordering is computed from numeric version keys (never index order and never
    string comparison) so the window is stable regardless of how the index was
    assembled.
    """
    manifests = index.get("manifests", []) if isinstance(index, dict) else []
    officials = [
        m
        for m in manifests
        if isinstance(m, dict) and m.get("release", {}).get("status") == "official"
    ]

    def _version_key(manifest: dict[str, Any]) -> tuple[int, int, int]:
        parts = str(manifest.get("release", {}).get("version", "")).split(".")
        return tuple(int(part) for part in parts[:3])  # type: ignore[return-value]

    return sorted(officials, key=_version_key, reverse=True)


def _frontend_matches(manifest: dict[str, Any], marker: Any) -> bool:
    """The prefix marker must match the manifest's frontend identity."""
    expected = {
        "version": manifest.get("release", {}).get("version"),
        "sourceSha": manifest.get("release", {}).get("sourceSha"),
        "frontendSha256": manifest.get("components", {}).get("frontend", {}).get("sha256"),
    }
    return isinstance(marker, dict) and all(
        marker.get(key) == value for key, value in expected.items()
    )


def release_artifacts_issues(manifest: Any, observed: Any) -> list[dict[str, str]]:
    """Cross-check one official release's artifacts against the observed state.

    A release is a *complete official set* only when every backend ECR
    ``release-<version>`` tag resolves to the exact digest recorded in the
    manifest AND the immutable frontend prefix marker exists and matches the
    manifest. A missing artifact is ``TARGET_ARTIFACT_MISSING``; an artifact
    present at different bytes is ``TARGET_ARTIFACT_MISMATCH`` (tampered or
    drifted) and fails closed.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(manifest, dict) or not isinstance(observed, dict):
        return [_issue("OBSERVED_MISSING", "observed", "no observed state was provided")]

    components = manifest.get("components", {})
    for key in rc.BACKEND_KEYS:
        component = components.get(key)
        if not isinstance(component, dict):
            continue
        repository = component.get("repository")
        release_tag = component.get("releaseTag")
        expected_digest = component.get("imageDigest")
        repo_state = observed.get("ecr", {}).get(repository, {})
        actual_digest = (
            repo_state.get("releaseTags", {}).get(release_tag)
            if isinstance(repo_state, dict)
            else None
        )
        if not actual_digest:
            issues.append(
                _issue(
                    "TARGET_ARTIFACT_MISSING",
                    f"ecr.{repository}.{release_tag}",
                    f"release tag {release_tag} for {repository} is absent; the release is not "
                    f"immediately rollback-capable",
                )
            )
        elif actual_digest != expected_digest:
            issues.append(
                _issue(
                    "TARGET_ARTIFACT_MISMATCH",
                    f"ecr.{repository}.{release_tag}",
                    f"release tag {release_tag} for {repository} resolves to {actual_digest}, "
                    f"expected {expected_digest}; the release bytes do not match the manifest "
                    "(tampered or drifted)",
                )
            )

    frontend = components.get("frontend")
    if isinstance(frontend, dict):
        prefix_key = f"{frontend.get('releasePrefix', '')}{frontend.get('versionMarker', '')}"
        prefix_marker = observed.get("frontend", {}).get("prefixMarkers", {}).get(prefix_key)
        if not isinstance(prefix_marker, dict) or not prefix_marker.get("exists"):
            issues.append(
                _issue(
                    "TARGET_ARTIFACT_MISSING",
                    f"frontend.{prefix_key}",
                    f"immutable frontend prefix marker {prefix_key} is absent; the release is "
                    "not immediately rollback-capable",
                )
            )
        elif not _frontend_matches(manifest, prefix_marker.get("marker")):
            issues.append(
                _issue(
                    "TARGET_ARTIFACT_MISMATCH",
                    f"frontend.{prefix_key}",
                    f"immutable frontend prefix marker {prefix_key} does not match the manifest",
                )
            )

    return issues


def latest_complete_officials(index: Any, observed: Any, limit: int = ROLLBACK_WINDOW) -> list[str]:
    """The newest ``limit`` complete official release versions.

    Completeness is the intersection across every backend ECR repository and
    the frontend prefix (``release_artifacts_issues``). When fewer than
    ``limit`` complete releases exist, all of them are returned (retention
    keeps "10 or all existing releases when fewer than 10 exist").
    """
    if not isinstance(index, dict) or not isinstance(observed, dict):
        return []
    complete: list[str] = []
    for manifest in _officials_sorted(index):
        if not release_artifacts_issues(manifest, observed):
            complete.append(manifest.get("release", {}).get("version"))
        if len(complete) >= limit:
            break
    return complete


def selection_issues(index: Any, observed: Any, version: Any) -> Decision:
    """Resolve the rollback target from the latest complete official sets.

    ``index`` is the set of release manifests (GitHub Release assets);
    ``observed`` carries the ECR ``release-<version>`` tag digests, the frontend
    prefix markers, and the currently running release identity
    (``currentRelease``). The target must:

    - be an existing official release (``TARGET_NOT_FOUND``/``TARGET_NOT_OFFICIAL``);
    - be one of the latest ``ROLLBACK_WINDOW`` complete official sets
      (``TARGET_ARTIFACT_MISSING``/``TARGET_ARTIFACT_MISMATCH``/
      ``TARGET_OUTSIDE_ROLLBACK_WINDOW``);
    - not be the release currently running in production (``TARGET_IS_CURRENT``).
    """
    issues: list[dict[str, str]] = []
    if not isinstance(index, dict) or not isinstance(observed, dict):
        return Decision(
            False, "", [_issue("OBSERVED_MISSING", "observed", "no observed state was provided")]
        )

    issues.extend(validate_index(index))

    if not is_valid_semver(version):
        issues.append(_issue("INVALID_VERSION", "version", f"invalid canonical SemVer {version!r}"))
        return Decision(False, "", issues)

    target = None
    manifests = index.get("manifests", []) if isinstance(index, dict) else []
    for manifest in manifests:
        if isinstance(manifest, dict) and manifest.get("release", {}).get("version") == version:
            target = manifest
            break
    if target is None:
        issues.append(
            _issue(
                "TARGET_NOT_FOUND",
                "version",
                f"no release {version!r} exists in the official index",
            )
        )
        return Decision(False, "", issues)
    if target.get("release", {}).get("status") != "official":
        issues.append(
            _issue(
                "TARGET_NOT_OFFICIAL",
                "release.status",
                f"release {version!r} is not an official release",
            )
        )
        return Decision(False, "", issues)

    # Validate the target manifest schema/contract (tamper detection): a draft,
    # malformed, or tampered manifest fails here and the selection fails closed.
    validated = validate_data(target)
    if not validated.valid:
        issues.append(
            _issue(
                "TARGET_MANIFEST_INVALID",
                "$",
                f"target release manifest failed validation: {json.dumps(validated.issues)}",
            )
        )
        return Decision(False, "", issues)

    artifact_issues = release_artifacts_issues(target, observed)
    if artifact_issues:
        issues.extend(artifact_issues)

    # Position among ALL officials (newest-first) decides the window even when
    # the target is complete but older than the newest 10 complete releases.
    officials = _officials_sorted(index)
    official_versions = [m.get("release", {}).get("version") for m in officials]
    position = official_versions.index(version) if version in official_versions else None
    complete_versions = latest_complete_officials(index, observed)
    if version not in complete_versions and not artifact_issues:
        if position is not None and position >= ROLLBACK_WINDOW:
            issues.append(
                _issue(
                    "TARGET_OUTSIDE_ROLLBACK_WINDOW",
                    "version",
                    f"release {version!r} is official and complete but outside the latest "
                    f"{ROLLBACK_WINDOW} releases; only the immediate rollback window is selectable",
                )
            )
        else:
            issues.append(
                _issue(
                    "TARGET_OUTSIDE_ROLLBACK_WINDOW",
                    "version",
                    f"release {version!r} is not one of the latest {ROLLBACK_WINDOW} complete "
                    "official sets",
                )
            )

    current = observed.get("currentRelease")
    if isinstance(current, dict) and current.get("version") == version:
        issues.append(
            _issue(
                "TARGET_IS_CURRENT",
                "currentRelease.version",
                f"release {version!r} is already the release running in production; "
                "rolling to the current release is a no-op and is rejected",
            )
        )

    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Database / schema compatibility guard (Decision 8)
# ---------------------------------------------------------------------------


def schema_compatibility_issues(state: Any) -> Decision:
    """The rollback database-compatibility guard.

    ``state``: ``{targetSchemaChange: bool, migrationReviewed: bool}``. A
    rollback never reverses the database. When the rollback involves a
    database/schema change (declared by the operator on dispatch), it is
    blocked until a forward/backward-compatible migration and recovery
    procedure has been reviewed — exactly the Decision 8 gate forward promotion
    applies, mirrored for the reverse direction.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(state, dict):
        return Decision(
            False,
            "",
            [_issue("SCHEMA_STATE_MISSING", "schemaCompatibility", "no schema state provided")],
        )
    present = state.get("targetSchemaChange") is True
    if present and state.get("migrationReviewed") is not True:
        issues.append(
            _issue(
                "SCHEMA_COMPATIBILITY_UNREVIEWED",
                "schemaCompatibility",
                "the rollback target includes a database/schema change but the Decision 8 "
                "migration review has not been recorded; schema-changing releases are only "
                "rollback-capable with a reviewed forward/backward-compatible migration and "
                "recovery procedure (the database is never reversed automatically)",
            )
        )
    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Frontend restore plan
# ---------------------------------------------------------------------------


def frontend_restore_issues(plan: Any) -> Decision:
    """Validate the restore-only frontend plan.

    ``plan``: ``{steps: [..], deleteFlag: bool, fromPrefix, marker, indexHtml}``.
    The live root is re-pointed to the retained immutable prefix of the target
    release: no ``--delete`` (old hashed assets are retained), ``fromPrefix``
    must be present, the marker + index.html must be published last, and a
    CloudFront invalidation of the SPA entry paths is required. No new prefix
    is written and no archive is uploaded during a restore.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(plan, dict):
        return Decision(
            False, "", [_issue("FRONTEND_PLAN_MISSING", "frontendPlan", "no frontend plan")]
        )

    if plan.get("deleteFlag"):
        issues.append(
            _issue(
                "FRONTEND_DELETE_FORBIDDEN",
                "frontendPlan.deleteFlag",
                "frontend restore must not use --delete; old hashed assets are retained",
            )
        )
    if plan.get("fromPrefix") in (None, ""):
        issues.append(
            _issue(
                "FRONTEND_PREFIX_MISSING",
                "frontendPlan.fromPrefix",
                "the immutable release prefix to restore from is required",
            )
        )

    steps = plan.get("steps")
    if not isinstance(steps, list):
        issues.append(
            _issue(
                "FRONTEND_STEPS_MISSING",
                "frontendPlan.steps",
                "an ordered restore step list is required",
            )
        )
        steps = []
    if "invalidate" not in steps and "invalidate" not in " ".join(str(s) for s in steps):
        issues.append(
            _issue(
                "FRONTEND_INVALIDATION_MISSING",
                "frontendPlan.steps",
                "a CloudFront invalidation of the SPA entry paths is required",
            )
        )
    marker_index = next((i for i, s in enumerate(steps) if str(s) == "live-marker-index"), None)
    if marker_index is None:
        issues.append(
            _issue(
                "FRONTEND_RESTORE_STEP_MISSING",
                "frontendPlan.steps",
                "the live root marker + index.html restore step is required",
            )
        )
    else:
        invalidate_index = next((i for i, s in enumerate(steps) if str(s) == "invalidate"), None)
        verify_index = next((i for i, s in enumerate(steps) if str(s) == "verify"), None)
        # The live root marker/index must be restored after the prefix is
        # fetched (the restore source) and before the read-back verification.
        fetch_index = next(
            (
                i
                for i, s in enumerate(steps)
                if str(s) in ("restore-prefix", "fetch-prefix", "live-marker-index")
            ),
            None,
        )
        if fetch_index is not None and marker_index < fetch_index:
            issues.append(
                _issue(
                    "FRONTEND_ORDER_INVALID",
                    "frontendPlan.steps",
                    "the live root marker/index.html must be restored after the immutable prefix "
                    "source is available (restore-source-first)",
                )
            )
        if verify_index is not None and verify_index < marker_index:
            issues.append(
                _issue(
                    "FRONTEND_ORDER_INVALID",
                    "frontendPlan.steps",
                    "read-back verification must run after the live root marker restore",
                )
            )
        if invalidate_index is not None and invalidate_index < marker_index:
            issues.append(
                _issue(
                    "FRONTEND_ORDER_INVALID",
                    "frontendPlan.steps",
                    "CloudFront invalidation must run after the live root marker/index restore",
                )
            )

    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Rollback result / audit record
# ---------------------------------------------------------------------------


def _component_identity(state: Any, key: str) -> dict[str, Any]:
    components = state.get("components", {}) if isinstance(state, dict) else {}
    return components.get(key, {}) if isinstance(components, dict) else {}


def _result_side_issues(side: Any, label: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(side, dict):
        issues.append(
            _issue(
                f"RESULT_{label.upper()}_MISSING",
                f"result.{label}",
                f"{label} release identity is required",
            )
        )
        return issues
    for required in ("version", "gitTag", "sourceSha", "frontendSha256"):
        if side.get(required) in (None, ""):
            issues.append(
                _issue(
                    f"RESULT_{label.upper()}_MISSING",
                    f"result.{label}.{required}",
                    f"result {label}.{required} is required",
                )
            )
    for key in rc.BACKEND_KEYS:
        digest = side.get("digests", {}).get(key) if isinstance(side.get("digests"), dict) else None
        if not digest:
            issues.append(
                _issue(
                    f"RESULT_{label.upper()}_MISSING",
                    f"result.{label}.digests.{key}",
                    f"result {label} digest for {key} is required",
                )
            )
    return issues


def result_issues(state: Any) -> Decision:
    """Validate the rollback result / audit record.

    ``state``: ``{manifest: <target official manifest>, result: {requester,
    approver, runId, workflowUrl, from: {...}, to: {...}, timestamps: {...},
    outcome, productionVerified, auditAnnotation: {written, path}},
    snapshot: {officialRelease}?, existingResult: {..}?}``.

    The result records requester, approver (from GitHub environment-approval
    evidence, never ``github.actor``), the from/to releases and exact
    artifacts, timestamps, workflow URL, and outcome. It annotates the
    deployment/audit record without editing the immutable original release
    manifest. ``existingResult`` enables idempotent resume: an identical prior
    record for the same run resumes; a conflicting record fails closed.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(state, dict) or not isinstance(state.get("manifest"), dict):
        return Decision(
            False, "", [_issue("RESULT_STATE_MISSING", "result", "result state missing")]
        )
    result = state.get("result")
    if not isinstance(result, dict):
        return Decision(
            False, "", [_issue("RESULT_MISSING", "result", "no result record provided")]
        )

    manifest = state["manifest"]
    target_version = manifest.get("release", {}).get("version")

    if result.get("outcome") not in RESULT_OUTCOMES:
        issues.append(
            _issue(
                "INVALID_OUTCOME",
                "result.outcome",
                f"outcome {result.get('outcome')!r} must be one of {RESULT_OUTCOMES}",
            )
        )
    if result.get("to", {}).get("version") != target_version:
        issues.append(
            _issue(
                "RESULT_TARGET_MISMATCH",
                "result.to.version",
                f"result target {result.get('to', {}).get('version')!r} does not match the "
                f"manifest release {target_version!r}",
            )
        )
    from_side = result.get("from")
    to_side = result.get("to")
    if (
        isinstance(from_side, dict)
        and isinstance(to_side, dict)
        and from_side.get("version") == to_side.get("version")
    ):
        issues.append(
            _issue(
                "RESULT_SAME_RELEASE",
                "result.from.version",
                "from and to versions are identical; a rollback must change the running release",
            )
        )

    requester = result.get("requester")
    approver = result.get("approver")
    for field_name, value in (("requester", requester), ("approver", approver)):
        if not isinstance(value, str) or len(value) > 39 or value != value.strip():
            issues.append(
                _issue(
                    "INVALID_LOGIN",
                    f"result.{field_name}",
                    f"{field_name} {value!r} is not a valid GitHub login",
                )
            )
    run_id = result.get("runId")
    if not (isinstance(run_id, int) and run_id >= 1):
        issues.append(
            _issue(
                "INVALID_RUN_ID",
                "result.runId",
                f"run id {run_id!r} must be a positive integer",
            )
        )
    if result.get("workflowUrl") in (None, ""):
        issues.append(
            _issue("RESULT_WORKFLOW_MISSING", "result.workflowUrl", "the workflow URL is required")
        )
    timestamps = result.get("timestamps")
    if (
        not isinstance(timestamps, dict)
        or not timestamps.get("startedAt")
        or not timestamps.get("completedAt")
    ):
        issues.append(
            _issue(
                "RESULT_TIMESTAMPS_MISSING",
                "result.timestamps",
                "startedAt and completedAt are required",
            )
        )
    if result.get("productionVerified") is not True:
        issues.append(
            _issue(
                "RESULT_NOT_VERIFIED",
                "result.productionVerified",
                "the rollback result may only be recorded after the exact target artifacts are "
                "verified healthy in production",
            )
        )
    audit = result.get("auditAnnotation")
    if not isinstance(audit, dict) or audit.get("written") is not True:
        issues.append(
            _issue(
                "RESULT_AUDIT_NOT_ANNOTATED",
                "result.auditAnnotation.written",
                "the deployment/audit record must be annotated without editing the immutable "
                "original release manifest",
            )
        )

    issues.extend(_result_side_issues(from_side, "from"))
    issues.extend(_result_side_issues(to_side, "to"))

    snapshot = state.get("snapshot")
    if isinstance(snapshot, dict) and isinstance(snapshot.get("officialRelease"), dict):
        official = snapshot["officialRelease"]
        if (
            isinstance(from_side, dict)
            and official.get("version")
            and from_side.get("version") != official.get("version")
        ):
            issues.append(
                _issue(
                    "RESULT_FROM_SNAPSHOT_MISMATCH",
                    "result.from.version",
                    f"result from version {from_side.get('version')!r} does not match the "
                    f"pre-rollback snapshot release {official.get('version')!r}",
                )
            )

    existing = state.get("existingResult")
    existing_match = (
        isinstance(existing, dict)
        and isinstance(existing.get("result"), dict)
        and existing["result"].get("runId") == result.get("runId")
        and existing["result"].get("to", {}).get("version") == result.get("to", {}).get("version")
        and existing["result"].get("from", {}).get("version")
        == result.get("from", {}).get("version")
    )
    if isinstance(existing, dict) and existing.get("exists") and not existing_match:
        issues.append(
            _issue(
                "RESULT_CONFLICT",
                "existingResult",
                "a rollback result already exists for this run with different from/to releases; "
                "an idempotent resume must match the recorded result",
            )
        )

    if issues:
        return Decision(False, "fail-closed", issues)
    if isinstance(existing, dict) and existing.get("exists"):
        return Decision(True, "resume", [])
    return Decision(True, "write", [])


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _emit(decision: Decision) -> int:
    _print_json(decision.to_dict())
    return 0 if decision.valid else 1


def _cmd_dispatch(args: argparse.Namespace) -> int:
    return _emit(dispatch_issues(args.version))


def _cmd_select(args: argparse.Namespace) -> int:
    return _emit(selection_issues(_read_json(args.index), _read_json(args.observed), args.version))


def _cmd_schema(args: argparse.Namespace) -> int:
    return _emit(schema_compatibility_issues(_read_json(args.state)))


def _cmd_snapshot(args: argparse.Namespace) -> int:
    return _emit(snapshot_issues(_read_json(args.snapshot), _read_json(args.manifest)))


def _cmd_plan(args: argparse.Namespace) -> int:
    return _emit(deployment_plan_issues(_read_json(args.plan)))


def _cmd_waiter(args: argparse.Namespace) -> int:
    return _emit(waiter_verified(_read_json(args.waiter), _read_json(args.expected)))


def _cmd_frontend_restore(args: argparse.Namespace) -> int:
    return _emit(frontend_restore_issues(_read_json(args.plan)))


def _cmd_verify(args: argparse.Namespace) -> int:
    return _emit(verification_issues(_read_json(args.observed), _read_json(args.manifest)))


def _cmd_compensate(args: argparse.Namespace) -> int:
    return _emit(compensation_steps(_read_json(args.snapshot), _read_json(args.changed)))


def _cmd_result(args: argparse.Namespace) -> int:
    return _emit(result_issues(_read_json(args.state)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.rollback",
        description="Owner-approved application rollback decisions (Pass 3, subphase 3.6).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    dispatch = sub.add_parser(
        "dispatch", help="validate the rollback dispatch input (target version)"
    )
    dispatch.add_argument("--version", required=True, metavar="SEMVER")
    dispatch.set_defaults(func=_cmd_dispatch)

    select = sub.add_parser("select", help="resolve the target from the complete official sets")
    select.add_argument("--index", required=True, metavar="FILE")
    select.add_argument("--observed", required=True, metavar="FILE")
    select.add_argument("--version", required=True, metavar="SEMVER")
    select.set_defaults(func=_cmd_select)

    schema = sub.add_parser("schema", help="database-compatibility guard (Decision 8)")
    schema.add_argument("--state", required=True, metavar="FILE")
    schema.set_defaults(func=_cmd_schema)

    snapshot = sub.add_parser("snapshot", help="validate the pre-rollback snapshot (reused)")
    snapshot.add_argument("--snapshot", required=True, metavar="FILE")
    snapshot.add_argument("--manifest", required=True, metavar="FILE")
    snapshot.set_defaults(func=_cmd_snapshot)

    plan = sub.add_parser("plan", help="validate the rollback deployment plan (reused)")
    plan.add_argument("--plan", required=True, metavar="FILE")
    plan.set_defaults(func=_cmd_plan)

    waiter = sub.add_parser("waiter", help="verify one deployment waiter bound to this run")
    waiter.add_argument("--waiter", required=True, metavar="FILE")
    waiter.add_argument("--expected", required=True, metavar="FILE")
    waiter.set_defaults(func=_cmd_waiter)

    frontend_restore = sub.add_parser(
        "frontend-restore", help="validate the restore-only frontend plan"
    )
    frontend_restore.add_argument("--plan", required=True, metavar="FILE")
    frontend_restore.set_defaults(func=_cmd_frontend_restore)

    verify = sub.add_parser("verify", help="verify production after rollback (reused)")
    verify.add_argument("--observed", required=True, metavar="FILE")
    verify.add_argument("--manifest", required=True, metavar="FILE")
    verify.set_defaults(func=_cmd_verify)

    compensate = sub.add_parser(
        "compensate", help="build the reverse-order compensation plan (reused)"
    )
    compensate.add_argument("--snapshot", required=True, metavar="FILE")
    compensate.add_argument("--changed", required=True, metavar="FILE")
    compensate.set_defaults(func=_cmd_compensate)

    result = sub.add_parser("result", help="validate the rollback result/audit record")
    result.add_argument("--state", required=True, metavar="FILE")
    result.set_defaults(func=_cmd_result)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
