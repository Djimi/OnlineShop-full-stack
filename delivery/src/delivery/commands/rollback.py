"""rollback preflight and execute: owner-approved OP-REC-03/04 rollback.

preflight (read-only; runs in BOTH workflow jobs — informational before
approval, authorizing after the lock):
  - input is exactly one ``release-NNNN`` id (argparse type + engine check);
    digests, tags, ARNs, manifests, and URLs are never entered by hand;
  - the target must exist as a published official GitHub Release carrying
    the ``release-manifest.json`` asset (fail-closed on API errors);
  - the target must not be the currently running release (observed from the
    live production snapshot) and must sit in the advertised rollback window
    (older than current; within current + the three most recent previous
    releases, OP-RET-01);
  - completeness reuses the retention window audit entry (chunk 6C, no
    duplication): every backend ECR ``release-<NNNN>`` tag resolves to the
    manifest's exact digest, the immutable frontend prefix marker exists AND
    names the target identity, and the recorded compatibilityFingerprint
    matches the current runtime fingerprint (mismatch -> INCOMPATIBLE,
    rejected);
  - internally consistent live state/snapshot: the fresh live-marker read
    equals the snapshot, the snapshot's official release identity agrees
    with its own marker, and all three services are observed;
  - OP-DB-02: ``--schema-change present`` always fails closed — rollback
    never reverses database schema or data; the ``--migration-reviewed``
    flag is recorded for the future additive-migration path but grants
    nothing today;
  - writes a report whose ``approvalIdentity`` is the SHA-256 of the
    byte-stable identity subset; ``execute`` (and ``--previous-report``)
    re-derive and compare it.

execute (mutation; the approval-gated job only):
  - consumes the target release manifest + the fresh pre-mutation snapshot +
    the pre-approval report + the approval evidence;
  - re-runs the FULL preflight first and requires the approval identity to
    match byte-for-byte; the consumed manifest must equal the GitHub-hosted
    official manifest (CT-GEN-04). Any drift aborts before mutation;
  - deploys the COMPLETE target set from the release manifest's exact
    digests: backends (Auth+Items) -> gateway -> frontend restored from the
    retained immutable prefix (aggregate content checksum proven BEFORE the
    live switch; the live marker names the official target release);
  - runs read-only production verification against the release manifest;
  - records a SEPARATE rollback result (requester + approver mandatory,
    from/to identities with exact digests/checksum, run/attempt, workflow
    URL, timestamps, per-component conclusions, outcome — never defaulted to
    the run actor). Never creates a GitHub Release, never edits any
    manifest, never mints/moves ECR tags (existing digests only), never
    touches RDS (OP-DB-02). Failure writes the failed result first, then
    re-raises; compensation is the workflow's automatic job below.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

from .. import live_marker
from ..aws import context as aws_context
from ..aws import (
    create_invalidation,
    describe_services,
    describe_task_definition,
    get_object_sha256,
    register_task_definition,
    replace_container_images,
    task_definition_images,
    update_service,
    wait_for_deployment,
)
from ..errors import (
    AbsentResourceError,
    AmbiguousStateError,
    DeliveryError,
    IncompatibleRollbackTarget,
    MutationVerificationError,
    ReadError,
    ValidationError,
)
from ..github import GitHubApi
from ..models import (
    ApprovalEvidenceFile,
    PreflightSnapshotSummary,
    ReleaseManifest,
    RollbackComponent,
    RollbackComponentIdentity,
    RollbackPreflightReport,
    RollbackResult,
)
from ..records import load_release_manifest, load_snapshot, read_s3_text, write_json
from ..serialization import canonical_json, sha256_hex
from ..validation import validate as validate_record
from .deploy_support import (
    DEPLOYMENT_TIMEOUT,
    DEPLOYMENT_VISIBILITY_DELAY,
    DEPLOYMENT_VISIBILITY_RETRIES,
    DIGEST_VERIFY_INTERVAL,
    DIGEST_VERIFY_TIMEOUT,
    assert_full_arn_secrets,
    deployment_for_revision,
    restore_frontend_from_retained_prefix,
    verify_running_digests,
)
from .retention import audit_entry
from .verify import production as verify_production

_SERVICE_KEYS = ("auth", "items", "gateway")
_CHANGED_COMPONENTS = ("auth", "items", "gateway", "frontend")
_RELEASE_ID = re.compile(r"^release-\d{4}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9@_.-]+/[A-Za-z0-9@_.-]+$")
_RUN_NUMBER = re.compile(r"^[0-9]+$")
_MANIFEST_ASSET = "release-manifest.json"
_PREFIX_MARKER = "release.json"
_ROLLBACK_WINDOW = 3  # current + up to three previous releases (OP-RET-01)

# Historical module-level names: rollback's tests monkeypatch these.
_DEPLOYMENT_TIMEOUT = DEPLOYMENT_TIMEOUT
_DIGEST_VERIFY_TIMEOUT = DIGEST_VERIFY_TIMEOUT
_DIGEST_VERIFY_INTERVAL = DIGEST_VERIFY_INTERVAL
_DEPLOYMENT_VISIBILITY_RETRIES = DEPLOYMENT_VISIBILITY_RETRIES
_DEPLOYMENT_VISIBILITY_DELAY = DEPLOYMENT_VISIBILITY_DELAY


# ---------------------------------------------------------------------------
# preflight (read-only)
# ---------------------------------------------------------------------------


def preflight(args: argparse.Namespace) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    ids = args.identifiers_data
    snapshot = load_snapshot(args.snapshot, require_environment="production")
    repository = _repository(args)
    api = GitHubApi(repository)
    schema_change = args.schema_change or "absent"
    migration_reviewed = args.migration_reviewed == "true"
    _schema_change_guard(schema_change)

    releases = api.list_releases()
    manifest = _load_official_target(api, releases, args.release_id)
    _assert_non_current_target(manifest.releaseId, snapshot, releases)
    _assert_in_advertised_window(manifest.releaseId, snapshot, releases)
    entry = audit_entry(ctx, ids, api, manifest.releaseId, snapshot, releases)
    _raise_for_audit_failures(entry)
    _assert_prefix_marker_matches_target(ctx, ids, manifest)
    _assert_snapshot_services(ids, snapshot)
    _assert_live_marker_matches_snapshot(ctx, ids, snapshot)
    _assert_snapshot_release_consistency(snapshot)

    identity_summary = _approval_identity(manifest, snapshot, schema_change, migration_reviewed)
    approval_identity = sha256_hex(canonical_json(identity_summary).encode())
    if args.previous_report is not None:
        _assert_no_post_approval_drift(
            args.previous_report, approval_identity, manifest.releaseId
        )

    approval_summary = (
        f"APPROVAL SUMMARY: roll back production to official release {manifest.releaseId}\n"
        f"  source SHA: {manifest.source.fullSha}\n"
        f"  auth digest: {manifest.artifacts.auth.digest}\n"
        f"  items digest: {manifest.artifacts.items.digest}\n"
        f"  gateway digest: {manifest.artifacts.gateway.digest}\n"
        f"  frontend checksum: {manifest.artifacts.frontend.checksum}\n"
        f"  compatibility fingerprint: {manifest.compatibilityFingerprint[:19]}...\n"
        f"  current release: {snapshot.release.releaseId} "
        "(restored automatically on defined post-mutation failure)\n"
        f"  database: never reversed (OP-DB-02); schema change: {schema_change}"
    )
    report = RollbackPreflightReport(
        reportId=f"rbf-{uuid4().hex[:16]}",
        producedAt=datetime.now(UTC),
        releaseId=manifest.releaseId,
        target=RollbackComponentIdentity(
            authDigest=manifest.artifacts.auth.digest,
            itemsDigest=manifest.artifacts.items.digest,
            gatewayDigest=manifest.artifacts.gateway.digest,
            frontendChecksum=manifest.artifacts.frontend.checksum,
        ),
        targetFrontendIdentity=manifest.artifacts.frontend.immutableIdentity,
        targetCompatibilityFingerprint=manifest.compatibilityFingerprint,
        productionSnapshot=_snapshot_summary(snapshot),
        snapshotReleaseId=snapshot.release.releaseId,
        schemaChange=schema_change,
        migrationReviewed=migration_reviewed,
        approvalSummary=approval_summary,
        approvalIdentity=approval_identity,
    )
    errors = validate_record(report)
    if errors:
        raise ValidationError(
            f"preflight report failed internal validation: {'; '.join(errors)}"
        )
    write_json(args.out, report)
    write_json(args.manifest_out, manifest)
    print(approval_summary)
    print(
        f"preflight PASSED for release {manifest.releaseId}; report written to {args.out} "
        f"and validated manifest to {args.manifest_out}"
    )
    return 0


def _load_official_target(
    api: GitHubApi, releases: list[dict], release_id: str
) -> ReleaseManifest:
    """The target must be a published official GitHub Release with the manifest asset."""
    matching = [release for release in releases if release["tag_name"] == release_id]
    if not matching:
        raise ValidationError(
            f"no published official GitHub Release for {release_id}; rollback targets "
            "must be published official releases (drafts/prereleases are never accepted)"
        )
    assets = {asset["name"]: asset["url"] for asset in matching[0]["assets"]}
    if _MANIFEST_ASSET not in assets:
        raise ValidationError(f"release {release_id} has no {_MANIFEST_ASSET} asset")
    try:
        raw = api.download_asset(assets[_MANIFEST_ASSET]).decode("utf-8")
        manifest = ReleaseManifest.model_validate(json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError, PydanticValidationError) as error:
        raise ValidationError(f"release {release_id} manifest asset is invalid: {error}") from error
    errors = validate_record(manifest)
    if errors:
        raise ValidationError(
            f"release {release_id} manifest is invalid: {'; '.join(errors)}"
        )
    if manifest.releaseId != release_id:
        raise ValidationError(
            f"release {release_id} manifest declares {manifest.releaseId} (CT-GEN-03)"
        )
    return manifest


def _assert_non_current_target(release_id: str, snapshot, releases: list[dict]) -> None:
    if snapshot.release.status != "official" or snapshot.release.releaseId is None:
        raise ValidationError(
            "the snapshot records no official current release; rollback requires an "
            "observed official current release identity (CT-AUDIT-01)"
        )
    current = snapshot.release.releaseId
    if release_id == current:
        raise ValidationError(
            f"{release_id} is the currently running release; rollback targets must be "
            "non-current (OP-REC-03)"
        )
    if current not in [release["tag_name"] for release in releases]:
        raise ValidationError(
            f"the snapshot records official release {current} but no published GitHub "
            "Release carries that identity; internally inconsistent live state"
        )


def _assert_in_advertised_window(release_id: str, snapshot, releases: list[dict]) -> None:
    current = snapshot.release.releaseId
    current_number = int(current.split("-", 1)[1])
    target_number = int(release_id.split("-", 1)[1])
    if target_number >= current_number:
        raise ValidationError(
            f"{release_id} is not older than the currently running release {current}"
        )
    previous = sorted(
        (
            release
            for release in releases
            if _RELEASE_ID.fullmatch(release["tag_name"])
            and int(release["tag_name"].split("-", 1)[1]) < current_number
        ),
        key=lambda release: release["tag_name"],
        reverse=True,
    )[:_ROLLBACK_WINDOW]
    if release_id not in [release["tag_name"] for release in previous]:
        raise ValidationError(
            f"{release_id} is outside the advertised rollback window (current {current} "
            f"plus the {_ROLLBACK_WINDOW} most recent previous releases); retained bytes "
            "may have expired and the release is not advertised rollback-capable (OP-RET-01)"
        )


def _raise_for_audit_failures(entry) -> None:
    if entry.complete:
        return
    kinds = {failure.kind for failure in entry.failures}
    if "FINGERPRINT_MISMATCH" in kinds:
        raise IncompatibleRollbackTarget(
            f"release {entry.releaseId} is INCOMPATIBLE with the current runtime "
            f"configuration: {entry.detail}"
        )
    if "READ_ERROR" in kinds:
        raise ReadError(f"rollback preflight read failed for {entry.releaseId}: {entry.detail}")
    raise ValidationError(
        f"release {entry.releaseId} is not a complete retained release: {entry.detail}"
    )


def _assert_prefix_marker_matches_target(ctx, ids: dict, manifest: ReleaseManifest) -> None:
    s3_client = aws_context.client_for(ctx, "s3")
    bucket = ids["frontendBucket"]
    prefix = f"{ids['frontendReleasesPrefix']}{manifest.releaseId}/"
    key = f"{prefix}{_PREFIX_MARKER}"
    try:
        raw = read_s3_text(
            s3_client, bucket, key, "retained frontend prefix marker"
        ).strip()
    except AbsentResourceError as error:
        raise ValidationError(
            f"immutable frontend prefix marker missing for {manifest.releaseId}: {key}"
        ) from error
    expected = _official_marker(manifest)
    parsed = live_marker.parse_live_marker(raw)
    if parsed is None or not live_marker.markers_identity_equivalent(parsed, expected):
        raise ValidationError(
            f"retained frontend prefix marker {key} does not name the target release "
            f"{manifest.releaseId} identity"
        )


def _official_marker(manifest: ReleaseManifest) -> live_marker.LiveMarker:
    return live_marker.build_official_marker(
        live_marker.build_candidate_marker(
            candidate_id=manifest.candidateId,
            source_sha=manifest.source.fullSha,
            frontend_sha256=manifest.artifacts.frontend.checksum,
        ),
        manifest.releaseId,
    )


def _assert_snapshot_services(ids: dict, snapshot) -> None:
    if len(ids["services"]) != len(_SERVICE_KEYS):
        raise ValidationError(
            "identifiers services must contain exactly 3 names in order auth, items, api-gateway"
        )
    for key, name in zip(_SERVICE_KEYS, ids["services"], strict=True):
        if key not in snapshot.services:
            raise ValidationError(f"snapshot has no observation for service {name} ({key})")


def _assert_live_marker_matches_snapshot(ctx, ids: dict, snapshot) -> None:
    """Fresh read of the live marker must equal the just-captured snapshot."""
    s3_client = aws_context.client_for(ctx, "s3")
    bucket = ids["frontendBucket"]
    marker_key = ids["frontendLiveMarker"]
    current = read_s3_text(s3_client, bucket, marker_key, "frontend live marker").strip()
    if current != snapshot.frontend.immutableIdentity:
        raise ValidationError(
            "live marker drift: the snapshot recorded a different marker than the fresh "
            "read; production changed after the snapshot was captured"
        )
    current_checksum = get_object_sha256(s3_client, bucket, marker_key)
    if current_checksum != snapshot.frontend.checksum:
        raise ValidationError(
            "live marker checksum drift: production changed after the snapshot was captured"
        )


def _assert_snapshot_release_consistency(snapshot) -> None:
    release_id = snapshot.release.releaseId
    marker_release = live_marker.marker_release_id(snapshot.frontend.immutableIdentity)
    if marker_release != release_id:
        raise ValidationError(
            f"snapshot records official release {release_id} but its live marker names "
            f"{marker_release!r}; internally inconsistent live state (CT-AUDIT-01)"
        )


def _schema_change_guard(schema_change: str) -> None:
    """OP-DB-02: rollback NEVER reverses database schema or data.

    ``--schema-change present`` fails closed today. The flag pair exists for
    the future additive-migration path (OP-DB-01): when versioned migration
    ownership exists, an approved rollback may span an additive migration
    but still never reverses it — that handling must be implemented here
    before ``present`` can ever pass, and the production IAM boundary grants
    no RDS mutation either way.
    """
    if schema_change == "present":
        raise ValidationError(
            "schema-changing rollback is never permitted (OP-DB-02 / AD-14): rollback "
            "changes the complete application artifact set only and never reverses "
            "database schema or data"
        )


def _repository(args: argparse.Namespace) -> str:
    repository = getattr(args, "repository", None) or os.environ.get("GITHUB_REPOSITORY")
    if not repository or not _REPOSITORY.fullmatch(repository):
        raise ValidationError("rollback requires --repository OWNER/NAME or GITHUB_REPOSITORY")
    return repository


# ---------------------------------------------------------------------------
# approval identity and post-approval drift
# ---------------------------------------------------------------------------


def _approval_identity(
    manifest: ReleaseManifest,
    snapshot,
    schema_change: str,
    migration_reviewed: bool,
) -> dict:
    return {
        "target": {
            "releaseId": manifest.releaseId,
            "authDigest": manifest.artifacts.auth.digest,
            "itemsDigest": manifest.artifacts.items.digest,
            "gatewayDigest": manifest.artifacts.gateway.digest,
            "frontendIdentity": manifest.artifacts.frontend.immutableIdentity,
            "frontendChecksum": manifest.artifacts.frontend.checksum,
            "compatibilityFingerprint": manifest.compatibilityFingerprint,
        },
        "current": {
            "releaseId": snapshot.release.releaseId,
            "compatibilityFingerprint": snapshot.compatibilityFingerprint,
            "services": {
                key: {
                    "taskDefinitionArn": snapshot.services[key].taskDefinitionArn,
                    "runningDigests": list(snapshot.services[key].runningDigests),
                }
                for key in _SERVICE_KEYS
            },
            "frontendMarker": snapshot.frontend.immutableIdentity,
            "frontendChecksum": snapshot.frontend.checksum,
        },
        "schemaChange": schema_change,
        "migrationReviewed": bool(migration_reviewed),
    }


def _assert_no_post_approval_drift(
    previous_report_path: str, approval_identity: str, release_id: str
) -> None:
    previous = _load_preflight_report(previous_report_path)
    if previous.releaseId != release_id:
        raise ValidationError(
            f"previous preflight report targets {previous.releaseId}, not {release_id}"
        )
    if previous.approvalIdentity != approval_identity:
        raise ValidationError(
            "POST-APPROVAL DRIFT: the preflight result changed after approval (target, "
            "compatibility, or production state); aborting requires a new operator "
            "decision (OP-REC-03)"
        )


def _load_preflight_report(path: str) -> RollbackPreflightReport:
    if not path:
        raise ValidationError(
            "rollback execute requires --preflight-report FILE (the pre-approval report)"
        )
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read preflight report {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"preflight report {path} is not valid JSON: {error}") from error
    try:
        report = RollbackPreflightReport.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(f"preflight report {path} is invalid: {error}") from error
    errors = validate_record(report)
    if errors:
        raise ValidationError(f"preflight report {path} is invalid: {'; '.join(errors)}")
    return report


def _snapshot_summary(snapshot) -> PreflightSnapshotSummary:
    return PreflightSnapshotSummary(
        snapshotId=snapshot.snapshotId,
        serviceTaskDefinitionArns={
            key: snapshot.services[key].taskDefinitionArn for key in _SERVICE_KEYS
        },
        serviceRunningDigests={
            key: list(snapshot.services[key].runningDigests) for key in _SERVICE_KEYS
        },
        frontendMarker=snapshot.frontend.immutableIdentity,
        frontendChecksum=snapshot.frontend.checksum,
    )


# ---------------------------------------------------------------------------
# execute (mutation)
# ---------------------------------------------------------------------------


def execute(args: argparse.Namespace) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    ids = args.identifiers_data
    manifest = load_release_manifest(args.manifest)
    snapshot = load_snapshot(args.snapshot, require_environment="production")
    report = _load_preflight_report(args.preflight_report)
    approval = _load_approval(args.approval)
    workflow_run_id, workflow_run_attempt = _workflow_run_identity(args)
    repository = _repository(args)
    api = GitHubApi(repository)

    if report.releaseId != manifest.releaseId:
        raise ValidationError(
            f"preflight report targets {report.releaseId} but the consumed manifest "
            f"declares {manifest.releaseId}"
        )
    _schema_change_guard(report.schemaChange)

    # Re-run the FULL preflight against the fresh post-lock snapshot; any
    # drift aborts before mutation (OP-REC-03). The consumed manifest must
    # equal the GitHub-hosted official manifest (CT-GEN-04).
    releases = api.list_releases()
    official = _load_official_target(api, releases, manifest.releaseId)
    if canonical_json(official.model_dump(mode="json")) != canonical_json(
        manifest.model_dump(mode="json")
    ):
        raise ValidationError(
            "the consumed release manifest differs from the GitHub-hosted official "
            "manifest; refusing to roll back from hand-carried bytes (CT-GEN-04)"
        )
    _assert_non_current_target(manifest.releaseId, snapshot, releases)
    _assert_in_advertised_window(manifest.releaseId, snapshot, releases)
    entry = audit_entry(ctx, ids, api, manifest.releaseId, snapshot, releases)
    _raise_for_audit_failures(entry)
    _assert_prefix_marker_matches_target(ctx, ids, manifest)
    _assert_snapshot_services(ids, snapshot)
    _assert_live_marker_matches_snapshot(ctx, ids, snapshot)
    _assert_snapshot_release_consistency(snapshot)
    approval_identity = sha256_hex(
        canonical_json(
            _approval_identity(manifest, snapshot, report.schemaChange, report.migrationReviewed)
        ).encode()
    )
    if approval_identity != report.approvalIdentity:
        raise ValidationError(
            "POST-APPROVAL DRIFT: the rollback preflight result changed after approval "
            "(target, compatibility, or production state); aborting before any mutation "
            "(OP-REC-03)"
        )
    if not args.dry_run and not args.out:
        raise ValidationError("rollback execute requires --out FILE (the rollback result)")

    digests = {key: getattr(manifest.artifacts, key).digest for key in _SERVICE_KEYS}
    if args.dry_run:
        _print_execute_plan(ids, manifest, digests)
        return 0

    result = RollbackResult(
        rollbackId=f"rbl-{uuid4().hex[:16]}",
        releaseId=manifest.releaseId,
        requester=approval.requester,
        approver=approval.approver,
        fromReleaseId=snapshot.release.releaseId,
        workflowRunId=workflow_run_id,
        workflowRunAttempt=workflow_run_attempt,
        startedAt=datetime.now(UTC),
        outcome="completed",
        deploymentConclusion="",
        verificationConclusion="",
        restoreConclusion="",
        workflowUrl=approval.workflowUrl,
        snapshotId=snapshot.snapshotId,
        fromRelease=_from_release_identity(snapshot),
        toRelease=RollbackComponentIdentity(
            authDigest=manifest.artifacts.auth.digest,
            itemsDigest=manifest.artifacts.items.digest,
            gatewayDigest=manifest.artifacts.gateway.digest,
            frontendChecksum=manifest.artifacts.frontend.checksum,
        ),
    )
    progress: dict[str, str] = {name: "not-attempted" for name in _CHANGED_COMPONENTS}
    stage = "backends"
    verification_conclusion = "not-attempted"
    try:
        _deploy_release_services(ctx, ids, snapshot, digests, ("auth", "items"), "backends")
        progress["auth"] = "passed"
        progress["items"] = "passed"
        stage = "gateway"
        _deploy_release_services(ctx, ids, snapshot, digests, ("gateway",), "gateway")
        progress["gateway"] = "passed"
        stage = "frontend"
        _restore_frontend_from_prefix(ctx, ids, manifest, snapshot)
        progress["frontend"] = "passed"
        stage = "verification"
        _verify_after_rollback(args)
        verification_conclusion = "passed"
        result.deploymentConclusion = "passed"
        result.verificationConclusion = "passed"
        result.restoreConclusion = "passed"
    except DeliveryError:
        if stage in ("backends", "gateway", "frontend"):
            failed = ("auth", "items") if stage == "backends" else (stage,)
            for name in failed:
                progress[name] = "failed"
        if stage == "verification":
            verification_conclusion = "failed"
        result.outcome = "failed"
        result.completedAt = datetime.now(UTC)
        result.deploymentConclusion = (
            "passed"
            if all(progress[name] == "passed" for name in ("auth", "items", "gateway"))
            else "failed"
        )
        result.restoreConclusion = progress["frontend"]
        result.verificationConclusion = verification_conclusion
        result.components = [
            RollbackComponent(component=name, conclusion=progress[name])
            for name in _CHANGED_COMPONENTS
        ]
        _fail_invalid_record(result, "rollback result")
        write_json(args.out, result)
        raise
    result.completedAt = datetime.now(UTC)
    result.components = [
        RollbackComponent(component=name, conclusion=progress[name])
        for name in _CHANGED_COMPONENTS
    ]
    _fail_invalid_record(result, "rollback result")
    write_json(args.out, result)
    _print_result_summary(result)
    return 0


def _load_approval(path: str) -> ApprovalEvidenceFile:
    """Approval evidence carries the mandatory approver; never defaulted."""
    if not path:
        raise ValidationError(
            "rollback execute requires --approval FILE (approval evidence); the "
            "approver is never defaulted to the run actor"
        )
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read approval evidence {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"approval evidence {path} is not valid JSON: {error}") from error
    try:
        return ApprovalEvidenceFile.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(f"approval evidence {path} is invalid: {error}") from error


def _workflow_run_identity(args: argparse.Namespace) -> tuple[int, int]:
    run_id = getattr(args, "workflow_run_id", None)
    if run_id is None:
        raw = os.environ.get("GITHUB_RUN_ID", "")
        if not _RUN_NUMBER.fullmatch(raw):
            raise ValidationError(
                "rollback execute requires --workflow-run-id or $GITHUB_RUN_ID (digits only)"
            )
        run_id = int(raw)
    attempt = getattr(args, "workflow_run_attempt", None)
    if attempt is None:
        raw = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if not _RUN_NUMBER.fullmatch(raw):
            raise ValidationError(
                "rollback execute requires --workflow-run-attempt or $GITHUB_RUN_ATTEMPT "
                "(digits only)"
            )
        attempt = int(raw)
    if run_id <= 0 or attempt <= 0:
        raise ValidationError("workflow run id and attempt must be positive integers")
    return run_id, attempt


def _from_release_identity(snapshot) -> RollbackComponentIdentity:
    digests: dict[str, str] = {}
    for key in _SERVICE_KEYS:
        observation = snapshot.services[key]
        if len(observation.runningDigests) != 1:
            raise AmbiguousStateError(
                f"snapshot service {key} records {len(observation.runningDigests)} running "
                "digests; cannot record an exact from-release identity"
            )
        digests[key] = observation.runningDigests[0]
    return RollbackComponentIdentity(
        authDigest=digests["auth"],
        itemsDigest=digests["items"],
        gatewayDigest=digests["gateway"],
        frontendChecksum=snapshot.frontend.checksum,
    )


def _print_execute_plan(ids: dict, manifest: ReleaseManifest, digests: dict[str, str]) -> None:
    print(f"rollback execute: dry-run plan for official release {manifest.releaseId}")
    for key, service in zip(_SERVICE_KEYS, ids["services"], strict=True):
        print(
            f"  {service}: would register a digest-pinned revision for {digests[key]} "
            "and update the service"
        )
    print(
        f"  frontend: would restore the live root from retained prefix "
        f"{manifest.artifacts.frontend.immutableIdentity} (checksum "
        f"{manifest.artifacts.frontend.checksum}), switch the live marker to "
        f"{manifest.releaseId}, and invalidate CloudFront /*"
    )
    print("  then: read-only production verification against the release manifest")
    print("dry-run complete; no mutation performed")


def _fail_invalid_record(record, label: str) -> None:
    errors = validate_record(record)
    if errors:
        raise ValidationError(f"produced {label} is invalid: {'; '.join(errors)}")


def _print_result_summary(result: RollbackResult) -> None:
    print(
        f"rollback result {result.rollbackId}: {result.releaseId} from "
        f"{result.fromReleaseId}; outcome {result.outcome}"
    )
    for component in result.components:
        print(f"  {component.component}: {component.conclusion}")


# ---------------------------------------------------------------------------
# backend deployment (deploys the target release's exact digests)
# ---------------------------------------------------------------------------


def _deploy_release_services(
    ctx,
    ids: dict,
    snapshot,
    digests: dict[str, str],
    keys: tuple[str, ...],
    label: str,
) -> None:
    """Register/update each service to the target digest, then wait and verify.

    Image-only diff against the OBSERVED snapshot revision (fresh state,
    OP-GEN-01); secrets stay full-ARN ``secrets[].valueFrom`` references.
    """
    registry = f"{ids['accountId']}.dkr.ecr.{ctx.region}.amazonaws.com"
    ecs_client = aws_context.client_for(ctx, "ecs")
    index = {key: position for position, key in enumerate(_SERVICE_KEYS)}
    deployments: dict[str, tuple[str, str]] = {}
    for key in keys:
        service = ids["services"][index[key]]
        expected_digest = digests[key]
        repository = ids["ecrRepositories"][key]
        observed = describe_services(ecs_client, ids["cluster"], [service])[service]
        _assert_observed_td_unchanged(observed, snapshot, key, service)
        td_arn = observed.get("taskDefinition")
        if not td_arn:
            raise ReadError(f"service {service} has no taskDefinition")
        td = describe_task_definition(ecs_client, td_arn)["taskDefinition"]
        target = f"{registry}/{repository}@{expected_digest}"
        matches = [
            name
            for name, image in task_definition_images(td).items()
            if image.rsplit("@", 1)[0].split(":", 1)[0].endswith(f"/{repository}")
        ]
        if len(matches) != 1:
            raise ReadError(
                f"service {service} must have exactly one container for "
                f"repository {repository}, found {matches}"
            )
        current_image = task_definition_images(td)[matches[0]]
        if current_image == target:
            _verify_running_digests(ecs_client, ids, service, expected_digest)
            deployments[key] = (service, "unchanged")
            continue
        revision_arn = register_task_definition(
            ecs_client, replace_container_images(td, {matches[0]: target})
        )
        assert_full_arn_secrets(ecs_client, revision_arn)
        update_service(ecs_client, ids["cluster"], service, revision_arn)
        deployment_id = _deployment_for_revision(
            ecs_client, ids["cluster"], service, revision_arn
        )
        deployments[key] = (service, deployment_id)

    for key in keys:
        service, deployment_id = deployments[key]
        if deployment_id == "unchanged":
            print(f"{service}: already running the target digest; verified")
            continue
        wait_for_deployment(
            ecs_client,
            ids["cluster"],
            service,
            deployment_id,
            timeout_seconds=_DEPLOYMENT_TIMEOUT,
        )
        _verify_running_digests(ecs_client, ids, service, digests[key])
        print(f"{service}: deployment {deployment_id} complete; running digest verified")
    print(f"rollback {label}: complete")


def _assert_observed_td_unchanged(
    observed: dict, snapshot, key: str, service: str
) -> None:
    """The service must still point at the snapshot revision (fresh state)."""
    expected_arn = snapshot.services[key].taskDefinitionArn
    if observed.get("taskDefinition") != expected_arn:
        raise MutationVerificationError(
            f"service {service} taskDefinition changed since the snapshot "
            f"(expected {expected_arn}, observed {observed.get('taskDefinition')!r}); "
            "aborting before mutation (OP-GEN-01)"
        )


def _deployment_for_revision(ecs_client, cluster: str, service: str, revision_arn: str) -> str:
    return deployment_for_revision(
        ecs_client,
        cluster,
        service,
        revision_arn,
        retries=_DEPLOYMENT_VISIBILITY_RETRIES,
        delay_seconds=_DEPLOYMENT_VISIBILITY_DELAY,
    )


def _verify_running_digests(ecs_client, ids: dict, service: str, expected: str) -> None:
    verify_running_digests(
        ecs_client,
        ids["cluster"],
        service,
        expected,
        timeout_seconds=_DIGEST_VERIFY_TIMEOUT,
        interval_seconds=_DIGEST_VERIFY_INTERVAL,
    )


# ---------------------------------------------------------------------------
# frontend restoration from the retained immutable prefix
# ---------------------------------------------------------------------------


def _restore_frontend_from_prefix(
    ctx, ids: dict, manifest: ReleaseManifest, snapshot
) -> None:
    """Restore the live root from the retained immutable prefix (OP-REC-04).

    The prefix identity must be the manifest's recorded frontend identity,
    the aggregate content checksum is proven BEFORE the live entry point is
    touched, files are copied with index.html last, the live marker names
    the OFFICIAL target release, and CloudFront is invalidated.
    """
    s3_client = aws_context.client_for(ctx, "s3")
    bucket = ids["frontendBucket"]
    prefix = manifest.artifacts.frontend.immutableIdentity
    expected_prefix = f"{ids['frontendReleasesPrefix']}{manifest.releaseId}/"
    if prefix != expected_prefix:
        raise ValidationError(
            f"release {manifest.releaseId} frontend identity {prefix!r} does not match "
            f"the retained prefix {expected_prefix!r}"
        )
    _assert_live_marker_matches_snapshot(ctx, ids, snapshot)
    marker_doc = live_marker.marker_document(_official_marker(manifest))
    restore_frontend_from_retained_prefix(
        s3_client,
        bucket=bucket,
        prefix=prefix,
        expected_checksum=manifest.artifacts.frontend.checksum,
        live_marker_key=ids["frontendLiveMarker"],
        marker_doc=marker_doc,
        release_label=manifest.releaseId,
    )
    cloudfront_client = aws_context.client_for(ctx, "cloudfront")
    create_invalidation(cloudfront_client, ids["cloudfrontDistributionId"], ["/*"])
    print(f"frontend live entry point restored to official release {manifest.releaseId}")


# ---------------------------------------------------------------------------
# read-only verification against the release manifest
# ---------------------------------------------------------------------------


def _verify_after_rollback(args: argparse.Namespace) -> None:
    """Reuse the read-only production verification against the release manifest."""
    verify_args = argparse.Namespace(**vars(args))
    verify_args.manifest = args.manifest
    verify_args.candidate = None
    verify_args.out = str(
        Path(args.out).with_name(Path(args.out).stem + "-verification.json")
    )
    verify_production(verify_args)
