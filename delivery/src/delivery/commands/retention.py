"""retention audit, preview, and apply: ECR-centric rollback-window retention.

audit (read-only, OP-RET-01): verify the current plus up to three previous
complete releases against their official manifests — every backend ECR
``release-<NNNN>`` tag resolves to the manifest's exact digest (tags are
retention/operator anchors, never deployment inputs), the immutable frontend
prefix marker exists in production S3 AND its content is identity-equivalent
to the official marker derivable from the release manifest, and each
release's compatibilityFingerprint still matches the current runtime
fingerprint from the live production snapshot. Missing/mismatched/read-error
state fails closed (distinct per-entry failure kinds, never silent drift);
incomplete older sets are historical and not audited. The report is printed
as machine-readable JSON and the command exits 0 only when the window is
complete and consistent.

preview (read-only, OP-RET-03): after a successful window audit, resolve the
desired/provided ECR lifecycle policy and, per backend repository, either
compare ECR's live lifecycle preview against the first-match-wins model
(PREVIEW_DISAGREEMENT) or — when no policy is applied, or the applied policy
differs from the resolved one — evaluate the resolved policy locally and
label the result honestly as a modeled preview. Any protected image expiring
(a window release tag, or any release-* tag inside the newest-10 keep margin)
fails closed (PROTECTED_IMAGE_EXPIRING). The modeled path is validation, not
a retention simulator: a live preview is always used whenever the applied
policy equals the resolved policy.

apply (OP-RET-02/03): requires ``--apply`` AND
``DELIVERY_RETENTION_LIVE_APPLY=1`` (set explicitly by the live pass).
``--reference-date`` is rejected on a real apply (ECR's evaluator uses its
own clock; the flag is honored by ``preview`` and ``apply --dry-run`` only).
The full audit + preview must pass first, then the policy is put on the
three backend repositories — each put immediately followed by a byte-for-byte
get-lifecycle-policy read-back (fail-closed drift); an already identical
policy is left unchanged. A mid-loop failure writes the partial apply report
(repositories processed so far plus the failed repository) to ``--out FILE``
before re-raising, so a partial application is never silent. A post-apply
window audit is recorded. Never deletes images itself: ECR lifecycle handles
delayed expiration, we only configure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from uuid import uuid4

from botocore.exceptions import ClientError
from pydantic import ValidationError as PydanticValidationError

from .. import live_marker
from .. import retention as retention_engine
from ..aws import context as aws_context
from ..aws import (
    get_lifecycle_policy,
    get_lifecycle_policy_preview,
    list_images,
    object_exists,
    put_lifecycle_policy,
    repository_digest,
    start_lifecycle_policy_preview,
)
from ..aws.waiters import bounded_waiter
from ..errors import (
    AbsentResourceError,
    LiveApplyRefused,
    MutationVerificationError,
    PreviewDisagreement,
    ProtectedImageExpiring,
    ReadError,
    ValidationError,
    WindowIncompleteError,
)
from ..github import GitHubApi
from ..models import (
    ReleaseManifest,
    RetentionApplyRepo,
    RetentionApplyReport,
    RetentionAuditEntry,
    RetentionAuditFailure,
    RetentionAuditReport,
    RetentionPreviewRepo,
    RetentionPreviewReport,
)
from ..records import load_snapshot, read_s3_text, write_json
from ..serialization import canonical_json
from ..validation import validate as validate_record

_SERVICE_KEYS = ("auth", "items", "gateway")
_RELEASE_ID = re.compile(r"^release-\d{4}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9@_.-]+/[A-Za-z0-9@_.-]+$")
_MANIFEST_ASSET = "release-manifest.json"
_PREFIX_MARKER = "release.json"
_LIVE_APPLY_ENV = "DELIVERY_RETENTION_LIVE_APPLY"
_PREVIEW_TIMEOUT = 180


def audit(args: argparse.Namespace) -> int:
    """Audit the four-release complete rollback window (read-only, OP-RET-01)."""
    ctx, ids, api, snapshot = _read_only_context(args)
    report = _audit_window(ctx, ids, api, snapshot)
    _fail_invalid_record(report, "retention audit report")
    print(canonical_json(report.model_dump(mode="json")))
    if getattr(args, "human", False):
        print(_human_audit(report))
    if not report.windowComplete:
        raise WindowIncompleteError(
            f"rollback window incomplete for current release {report.currentReleaseId}"
        )
    return 0


def preview(args: argparse.Namespace) -> int:
    """Preview the lifecycle policy effect against the protected release-tag set."""
    ctx, ids, api, snapshot = _read_only_context(args)
    audit_report = _audit_window(ctx, ids, api, snapshot)
    if not audit_report.windowComplete:
        raise WindowIncompleteError(
            "preview requires a complete rollback window; run `retention audit` for details"
        )
    policy_text, policy, policy_kind = retention_engine.load_policy_text(
        getattr(args, "policy", None)
    )
    report = _preview_window(
        ctx, ids, audit_report, policy_text, policy, policy_kind, _reference_datetime(args)
    )
    _fail_invalid_record(report, "retention preview report")
    print(canonical_json(report.model_dump(mode="json")))
    _fail_preview(report)
    return 0


def apply(args: argparse.Namespace) -> int:
    """Apply the lifecycle policy to the three backend repositories."""
    if getattr(args, "dry_run", False):
        return preview(args)
    if getattr(args, "reference_date", None) is not None:
        raise ValidationError(
            "--reference-date is not honored by `retention apply`: a real apply "
            "configures the live policy and ECR's lifecycle evaluator uses its own "
            "clock. Use `retention preview --reference-date ISO` or "
            "`retention apply --dry-run --reference-date ISO` for a modeled evaluation"
        )
    if os.environ.get(_LIVE_APPLY_ENV) != "1":
        raise LiveApplyRefused(
            f"apply requires {_LIVE_APPLY_ENV}=1 (set explicitly by the live pass)"
        )
    ctx, ids, api, snapshot = _read_only_context(args)
    pre_audit = _audit_window(ctx, ids, api, snapshot)
    if not pre_audit.windowComplete:
        raise WindowIncompleteError("apply requires a complete pre-mutation rollback window")
    policy_text, policy, policy_kind = retention_engine.load_policy_text(
        getattr(args, "policy", None)
    )
    preview_report = _preview_window(
        ctx, ids, pre_audit, policy_text, policy, policy_kind, datetime.now(UTC)
    )
    _fail_invalid_record(preview_report, "retention preview report")
    _fail_preview(preview_report)
    ecr_client = aws_context.client_for(ctx, "ecr")
    repositories = []
    try:
        for key in _SERVICE_KEYS:
            repository = ids["ecrRepositories"][key]
            try:
                current = get_lifecycle_policy(ecr_client, repository)
            except AbsentResourceError:
                current = None
            if current == policy_text:
                repositories.append(
                    RetentionApplyRepo(
                        repository=repository, action="unchanged", readBackVerified=True
                    )
                )
                continue
            put_lifecycle_policy(ecr_client, repository, policy_text)
            repositories.append(
                RetentionApplyRepo(repository=repository, action="put", readBackVerified=True)
            )
    except (ReadError, MutationVerificationError, ClientError) as error:
        failed_entry = RetentionApplyRepo(
            repository=repository,
            action="failed",
            readBackVerified=False,
            failureDetail=f"{type(error).__name__}: {error}",
        )
        _emit_apply_report(args, policy_kind, [*repositories, failed_entry], pre_audit, False)
        raise
    post_audit = _audit_window(ctx, ids, api, snapshot)
    _emit_apply_report(args, policy_kind, repositories, pre_audit, post_audit.windowComplete)
    if not post_audit.windowComplete:
        raise WindowIncompleteError("post-apply rollback window audit incomplete")
    return 0


def _fail_invalid_record(record, label: str) -> None:
    errors = validate_record(record)
    if errors:
        raise ValidationError(f"produced {label} is invalid: {'; '.join(errors)}")


def _emit_apply_report(
    args: argparse.Namespace,
    policy_kind: str,
    repositories: list[RetentionApplyRepo],
    pre_audit: RetentionAuditReport,
    post_audit_window_complete: bool,
) -> None:
    report = RetentionApplyReport(
        reportId=f"ret-{uuid4().hex[:16]}",
        producedAt=datetime.now(UTC),
        environment="production",
        policyKind=policy_kind,
        repositories=repositories,
        preAuditWindowComplete=pre_audit.windowComplete,
        postAuditWindowComplete=post_audit_window_complete,
    )
    _fail_invalid_record(report, "retention apply report")
    print(canonical_json(report.model_dump(mode="json")))
    out = getattr(args, "out", None)
    if out:
        write_json(out, report)


# ---------------------------------------------------------------------------
# shared read-only context
# ---------------------------------------------------------------------------


def _read_only_context(args: argparse.Namespace):
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    repository = getattr(args, "repository", None) or os.environ.get("GITHUB_REPOSITORY")
    if not repository or not _REPOSITORY.fullmatch(repository):
        raise ValidationError(
            "retention requires --repository OWNER/NAME or GITHUB_REPOSITORY"
        )
    snapshot_path = getattr(args, "snapshot", None)
    if not snapshot_path:
        raise ValidationError(
            "retention requires --snapshot FILE (a fresh production snapshot from "
            "`delivery snapshot production`)"
        )
    snapshot = load_snapshot(snapshot_path, require_environment="production")
    return ctx, args.identifiers_data, GitHubApi(repository), snapshot


def _reference_datetime(args: argparse.Namespace) -> datetime:
    value = getattr(args, "reference_date", None)
    if value is None:
        return datetime.now(UTC)
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def _audit_window(ctx, ids: dict, api: GitHubApi, snapshot) -> RetentionAuditReport:
    """Verify the current + up to 3 previous complete releases (OP-RET-01)."""
    current = snapshot.release.releaseId
    if snapshot.release.status != "official" or current is None:
        raise ValidationError(
            "snapshot records no official current release; cannot audit the rollback window"
        )
    releases = api.list_releases()
    official = sorted(
        (release for release in releases if _RELEASE_ID.fullmatch(release["tag_name"])),
        key=lambda release: release["tag_name"],
        reverse=True,
    )
    current_number = int(current.split("-", 1)[1])
    previous = [
        release
        for release in official
        if int(release["tag_name"].split("-", 1)[1]) < current_number
    ][:3]
    window_ids = [current] + [release["tag_name"] for release in previous]
    entries = [
        audit_entry(ctx, ids, api, release_id, snapshot, official, verify_marker_content=True)
        for release_id in window_ids
    ]
    window_complete = all(entry.complete for entry in entries)
    historical = []
    for release in official:
        release_id = release["tag_name"]
        if release_id in window_ids:
            continue
        detail = (
            "newer than the currently running release; outside the audit window"
            if int(release_id.split("-", 1)[1]) > current_number
            else "older than the four-release window; historical, not audited"
        )
        historical.append(
            RetentionAuditEntry(
                releaseId=release_id, inWindow=False, complete=False, detail=detail
            )
        )
    return RetentionAuditReport(
        reportId=f"ret-{uuid4().hex[:16]}",
        producedAt=datetime.now(UTC),
        environment="production",
        currentReleaseId=current,
        currentFingerprint=snapshot.compatibilityFingerprint,
        windowComplete=window_complete,
        releases=sorted(entries + historical, key=lambda entry: entry.releaseId, reverse=True),
    )


def audit_entry(
    ctx,
    ids: dict,
    api: GitHubApi,
    release_id: str,
    snapshot,
    releases,
    *,
    verify_marker_content: bool = False,
) -> RetentionAuditEntry:
    """Verify one release against its official manifest; read errors fail closed.

    Shared between the four-release window audit and the rollback preflight
    (OP-REC-03): the rollback target must be a complete retained release per
    exactly these checks — ECR ``release-<NNNN>`` tag-to-digest anchors,
    frontend prefix marker existence, and the compatibility fingerprint.

    ``verify_marker_content`` additionally reads and parses the prefix marker
    and requires identity-equivalence with the official marker derivable from
    the release manifest (OP-RET-01); a wrong-content marker fails with
    ``PREFIX_MARKER_MISMATCH``, distinct from absence and read errors. The
    rollback preflight keeps it off because it performs its own marker-content
    assertion with a tailored error after the completeness audit.
    """
    failures: list[RetentionAuditFailure] = []
    manifest: ReleaseManifest | None = None
    matching = [release for release in releases if release["tag_name"] == release_id]
    if not matching:
        failures.append(
            RetentionAuditFailure(
                kind="GITHUB_RELEASE_NOT_FOUND",
                message=f"no published GitHub Release for {release_id}",
            )
        )
    else:
        assets = {asset["name"]: asset["url"] for asset in matching[0]["assets"]}
        if _MANIFEST_ASSET not in assets:
            failures.append(
                RetentionAuditFailure(
                    kind="MANIFEST_NOT_FOUND",
                    message=f"release-manifest.json asset missing for {release_id}",
                )
            )
        else:
            try:
                raw = api.download_asset(assets[_MANIFEST_ASSET]).decode("utf-8")
                manifest = ReleaseManifest.model_validate(json.loads(raw))
                errors = validate_record(manifest)
                if errors:
                    failures.append(
                        RetentionAuditFailure(
                            kind="MANIFEST_INVALID", message="; ".join(errors)
                        )
                    )
                    manifest = None
            except (UnicodeDecodeError, json.JSONDecodeError, PydanticValidationError) as error:
                failures.append(
                    RetentionAuditFailure(
                        kind="MANIFEST_INVALID", message=f"manifest unreadable: {error}"
                    )
                )
            except ReadError as error:
                failures.append(
                    RetentionAuditFailure(
                        kind="READ_ERROR", message=f"manifest download failed: {error}"
                    )
                )
    if manifest is not None:
        if manifest.releaseId != release_id:
            failures.append(
                RetentionAuditFailure(
                    kind="MANIFEST_ID_MISMATCH",
                    message=f"manifest declares {manifest.releaseId}, GitHub tag is {release_id}",
                )
            )
        if manifest.compatibilityFingerprint != snapshot.compatibilityFingerprint:
            failures.append(
                RetentionAuditFailure(
                    kind="FINGERPRINT_MISMATCH",
                    message=(
                        f"recorded {manifest.compatibilityFingerprint} is incompatible with "
                        f"current runtime {snapshot.compatibilityFingerprint}"
                    ),
                )
            )
        ecr_client = aws_context.client_for(ctx, "ecr")
        for key in _SERVICE_KEYS:
            repository = ids["ecrRepositories"][key]
            expected = getattr(manifest.artifacts, key).digest
            try:
                observed = repository_digest(ecr_client, repository, release_id)
            except AbsentResourceError as error:
                failures.append(
                    RetentionAuditFailure(kind="ECR_TAG_NOT_FOUND", message=str(error))
                )
                continue
            except ReadError as error:
                failures.append(
                    RetentionAuditFailure(kind="READ_ERROR", message=str(error))
                )
                continue
            if observed != expected:
                failures.append(
                    RetentionAuditFailure(
                        kind="ECR_DIGEST_MISMATCH",
                        message=(
                            f"{repository}:{release_id} resolves to {observed}, "
                            f"manifest expects {expected}"
                        ),
                    )
                )
    s3_client = aws_context.client_for(ctx, "s3")
    prefix = f"{ids['frontendReleasesPrefix']}{release_id}/"
    marker_key = f"{prefix}{_PREFIX_MARKER}"
    if verify_marker_content and manifest is not None:
        _verify_prefix_marker_content(failures, s3_client, ids, marker_key, manifest, release_id)
    elif not object_exists(s3_client, ids["frontendBucket"], marker_key):
        failures.append(
            RetentionAuditFailure(
                kind="PREFIX_MARKER_NOT_FOUND",
                message=f"immutable frontend prefix marker missing: {marker_key}",
            )
        )
    detail = (
        "; ".join(f"{failure.kind}: {failure.message}" for failure in failures)
        if failures
        else "ECR tags, frontend prefix, and compatibility fingerprint verified"
    )
    return RetentionAuditEntry(
        releaseId=release_id,
        inWindow=True,
        complete=not failures,
        releaseFingerprint=manifest.compatibilityFingerprint if manifest is not None else None,
        failures=failures,
        detail=detail,
    )


def _official_marker_for(manifest: ReleaseManifest, release_id: str) -> live_marker.LiveMarker:
    """The official marker derivable from the release manifest (CT-PROD-02)."""
    return live_marker.build_official_marker(
        live_marker.build_candidate_marker(
            candidate_id=manifest.candidateId,
            source_sha=manifest.source.fullSha,
            frontend_sha256=manifest.artifacts.frontend.checksum,
        ),
        release_id,
    )


def _verify_prefix_marker_content(
    failures: list[RetentionAuditFailure],
    s3_client,
    ids: dict,
    marker_key: str,
    manifest: ReleaseManifest,
    release_id: str,
) -> None:
    """Read the prefix marker and require identity-equivalent content (OP-RET-01)."""
    try:
        raw = read_s3_text(
            s3_client, ids["frontendBucket"], marker_key, "immutable frontend prefix marker"
        ).strip()
        parsed = live_marker.parse_live_marker(raw)
    except AbsentResourceError as error:
        failures.append(
            RetentionAuditFailure(kind="PREFIX_MARKER_NOT_FOUND", message=str(error))
        )
        return
    except ReadError as error:
        failures.append(RetentionAuditFailure(kind="READ_ERROR", message=str(error)))
        return
    except ValidationError as error:
        failures.append(
            RetentionAuditFailure(kind="PREFIX_MARKER_MISMATCH", message=str(error))
        )
        return
    expected = _official_marker_for(manifest, release_id)
    if parsed is None or not live_marker.markers_identity_equivalent(parsed, expected):
        failures.append(
            RetentionAuditFailure(
                kind="PREFIX_MARKER_MISMATCH",
                message=(
                    f"immutable frontend prefix marker {marker_key} does not name the "
                    f"official {release_id} identity"
                ),
            )
        )


def _human_audit(report: RetentionAuditReport) -> str:
    lines = [
        f"rollback window: {'complete' if report.windowComplete else 'INCOMPLETE'}",
        f"current release: {report.currentReleaseId} "
        f"(fingerprint {report.currentFingerprint[:12]}…)",
    ]
    for entry in report.releases:
        scope = "window    " if entry.inWindow else "historical"
        lines.append(f"  {entry.releaseId} [{scope}] {entry.detail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def _preview_window(
    ctx,
    ids: dict,
    audit_report: RetentionAuditReport,
    policy_text: str,
    policy: dict,
    policy_kind: str,
    reference: datetime,
) -> RetentionPreviewReport:
    window_ids = [entry.releaseId for entry in audit_report.releases if entry.inWindow]
    ecr_client = aws_context.client_for(ctx, "ecr")
    repositories = []
    for key in _SERVICE_KEYS:
        repository = ids["ecrRepositories"][key]
        images = list_images(ecr_client, repository)
        protected = retention_engine.protected_release_tags(window_ids, images)
        try:
            applied = get_lifecycle_policy(ecr_client, repository)
        except AbsentResourceError:
            applied = None
        if applied is None:
            repositories.append(
                _modeled_repo(
                    repository,
                    policy,
                    images,
                    protected,
                    reference,
                    "no lifecycle policy applied yet; modeled evaluation of the resolved policy",
                )
            )
        elif applied == policy_text:
            observed = _live_preview(ecr_client, repository)
            decisions = retention_engine.model_expirations(policy, images, reference)
            expected = {decision["digest"] for decision in decisions if decision["expire"]}
            agreement = "agree" if expected == set(observed) else "disagree"
            protected_expiring = sorted(
                f"{digest} {tag}"
                for digest, tags in observed.items()
                for tag in sorted(set(tags) & set(protected))
            )
            repositories.append(
                RetentionPreviewRepo(
                    repository=repository,
                    kind="live",
                    reason="ECR live lifecycle preview compared against the modeled expectation",
                    protectedTags=protected,
                    expiringDigests=sorted(observed),
                    protectedExpiring=protected_expiring,
                    agreement=agreement,
                )
            )
        else:
            repositories.append(
                _modeled_repo(
                    repository,
                    policy,
                    images,
                    protected,
                    reference,
                    "the applied policy differs from the resolved policy; "
                    "modeled evaluation only",
                )
            )
    return RetentionPreviewReport(
        reportId=f"ret-{uuid4().hex[:16]}",
        producedAt=datetime.now(UTC),
        environment="production",
        policyKind=policy_kind,
        referenceDate=reference,
        windowComplete=audit_report.windowComplete,
        protectedReleases=window_ids,
        repositories=repositories,
    )


def _modeled_repo(repository, policy, images, protected, reference, reason) -> RetentionPreviewRepo:
    decisions = retention_engine.model_expirations(policy, images, reference)
    expiring = [decision for decision in decisions if decision["expire"]]
    protected_expiring = sorted(
        f"{decision['digest']} {tag}"
        for decision in expiring
        for tag in sorted(set(decision["tags"]) & set(protected))
    )
    return RetentionPreviewRepo(
        repository=repository,
        kind="modeled",
        reason=reason,
        protectedTags=protected,
        expiringDigests=sorted(decision["digest"] for decision in expiring),
        protectedExpiring=protected_expiring,
    )


def _live_preview(ecr_client, repository: str) -> dict[str, list[str]]:
    """Run ECR's live lifecycle preview and return the expiring digest -> tags map."""
    preview_id = start_lifecycle_policy_preview(ecr_client, repository)
    bounded_waiter(
        lambda: get_lifecycle_policy_preview(ecr_client, repository, preview_id).get("status")
        == "COMPLETE",
        label=f"lifecycle preview for {repository}",
        timeout_seconds=_PREVIEW_TIMEOUT,
        interval_seconds=5,
    )
    result = get_lifecycle_policy_preview(ecr_client, repository, preview_id)
    status = result.get("status")
    if status != "COMPLETE":
        raise ReadError(f"lifecycle preview for {repository} ended in status {status!r}")
    expiring: dict[str, list[str]] = {}
    for image in result.get("previewResults") or []:
        if not isinstance(image, dict):
            raise ReadError(f"lifecycle preview for {repository} has a malformed entry")
        action = image.get("action")
        if not isinstance(action, dict) or action.get("type") != "EXPIRE":
            continue
        digest = image.get("imageDigest")
        tags = image.get("imageTags")
        priority = image.get("appliedRulePriority")
        if not isinstance(digest, str) or not digest:
            raise ReadError(f"lifecycle preview for {repository}: expiring entry without digest")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ReadError(f"lifecycle preview for {repository}: expiring entry without tags")
        if isinstance(priority, bool) or not isinstance(priority, int) or priority <= 0:
            raise ReadError(
                f"lifecycle preview for {repository}: expiring entry without a rule priority"
            )
        expiring[digest] = tags
    return expiring


def _fail_preview(report: RetentionPreviewReport) -> None:
    protected = [repo for repo in report.repositories if repo.protectedExpiring]
    if protected:
        raise ProtectedImageExpiring(
            "; ".join(
                f"{repo.repository}: {', '.join(repo.protectedExpiring)}" for repo in protected
            )
        )
    disagreements = [repo for repo in report.repositories if repo.agreement == "disagree"]
    if disagreements:
        raise PreviewDisagreement(
            "; ".join(
                f"{repo.repository}: live ECR preview disagrees with the modeled expectation"
                for repo in disagreements
            )
        )
