"""finalize: OP-FIN-01 official finalization under the production lock.

Sequence: allocate the next never-reused ``release-NNNN`` (must equal the
provisional id recorded by ``deploy frontend``; AD-07) -> add backend
retention/operator ECR tags from the recorded manifest bytes (never
pull/rebuild) -> verify the immutable frontend prefix -> prepare the exact
CT-REL-01 manifest -> replace the candidate live marker with the
identity-equivalent official marker and verify it publicly -> publish the
GitHub Release with the manifest and the four pinned SBOM assets -> audit the
read-only rollback window (current plus up to three previous complete
releases) and fail if it is incomplete at publication time.

Exact-match resume (OP-FIN-02 / CT-GEN-04): when ``--manifest`` already
exists, every existing component (ECR tags, frontend prefix, live marker,
GitHub release, assets, manifest bytes) must match the intended release
exactly — mismatches fail closed with a clear list; missing components are
created. Mismatch never resumes: restoring the previous official release is
Phase 6 recovery.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from botocore.exceptions import ClientError
from pydantic import ValidationError as PydanticValidationError

from .. import frontend as frontend_utils
from .. import live_marker
from ..aws import context as aws_context
from ..aws import create_invalidation, get_distribution, put_image, put_object
from ..aws.readback import absent_or_read
from ..aws.waiters import bounded_waiter
from ..errors import AbsentResourceError, MutationVerificationError, ReadError, ValidationError
from ..github import GitHubApi
from ..models import (
    ApprovalEvidence,
    ApprovalEvidenceFile,
    FinalizationReport,
    FinalizationStep,
    FrontendPublishRecord,
    ProductionSnapshot,
    ProductionVerification,
    ReleaseFrontend,
    ReleaseManifest,
    ReleaseSource,
    RollbackWindowEntry,
    SbomAsset,
    SbomSet,
    StagingEvidence,
    StagingOperationRecord,
    VerificationReport,
)
from ..records import (
    load_candidate,
    load_snapshot,
    load_staging_record,
    read_s3_text,
    write_json,
)
from ..serialization import canonical_json, sha256_hex
from ..serving import _fetch
from ..validation import validate as validate_record
from ..validation import validate_release_against_candidate

_SERVICE_KEYS = ("auth", "items", "gateway")
_SBOM_FILES = {
    "auth": "auth.spdx.json",
    "items": "items.spdx.json",
    "gateway": "api-gateway.spdx.json",
    "frontend": "frontend.spdx.json",
}
_MANIFEST_ASSET = "release-manifest.json"
_PREFIX_MARKER = "release.json"
_STAGING_RECORD_IDENTITY = re.compile(r"^staging-record-\d+-\d+$")
_PUBLIC_MARKER_TIMEOUT = 240


def finalize(args: argparse.Namespace) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    ids = args.identifiers_data
    candidate = load_candidate(args.candidate)
    snapshot = load_snapshot(args.snapshot, require_environment="production")
    _validated_staging_gate(args, candidate)
    verification = _validated_verification(args, candidate)
    approval = _validated_approval(args)
    publish = _validated_publish(args, candidate)
    frontend_utils.verify_frontend_archive(args.frontend_archive, candidate)
    sboms = _load_sboms(args.sbom_dir)
    api = GitHubApi(candidate.source.repository)

    release_id = _allocate_release_id(api, publish, args.manifest)
    prefix = f"{ids['frontendReleasesPrefix']}{release_id}/"
    ecr_client = aws_context.client_for(ctx, "ecr")
    resumed = Path(args.manifest).exists()
    candidate_marker = live_marker.build_candidate_marker(
        candidate_id=candidate.candidateId,
        source_sha=candidate.source.fullSha,
        frontend_sha256=candidate.artifacts.frontend.contentChecksum,
    )
    official_marker = live_marker.build_official_marker(candidate_marker, release_id)

    # OP-FIN-01: 1 allocate -> 2 retention/operator ECR tags -> 3 frontend
    # protection -> 4 prepare manifest (rollbackCapableAtPublication needs the
    # window audit, so the audit runs read-only before the manifest is fixed)
    # -> 5 official marker switch -> 6 GitHub Release -> 7 window audit.
    steps: list[FinalizationStep] = []
    steps.append(
        _mint_ecr_tags(ecr_client, ids, candidate, release_id, dry_run=args.dry_run)
    )
    steps.append(_protect_frontend(ctx, ids, prefix, candidate_marker))
    previous_releases = _window_previous_releases(api, release_id)
    if args.dry_run:
        pre_window_complete, _window = False, []
    else:
        pre_window_complete, _window = _window_audit(
            ctx, ids, api, previous_releases, release_id, candidate
        )
    intended = _build_manifest(
        candidate=candidate,
        snapshot=snapshot,
        staging_identity=args.staging_record_identity,
        verification=verification,
        approval=approval,
        publish=publish,
        sboms=sboms,
        release_id=release_id,
        rollback_capable=pre_window_complete,
        promoted_at=_existing_promoted_at(args.manifest) if resumed else None,
    )
    errors = validate_record(intended) + validate_release_against_candidate(intended, candidate)
    if errors:
        raise ValidationError(f"prepared release manifest is invalid: {'; '.join(errors)}")
    intended_bytes = (canonical_json(intended.model_dump(mode="json")) + "\n").encode()
    if resumed:
        _assert_exact_resume(args.manifest, intended)

    if args.dry_run:
        print(f"finalize: dry-run plan for {release_id}")
        print("  1. allocate release id (never reused, must equal the provisional id)")
        print("  2. ECR release-* tags from the recorded manifest bytes (never pull/rebuild)")
        print("  3. frontend immutable prefix protection")
        print("  4. prepare CT-REL manifest (rollbackCapableAtPublication=computed)")
        print("  5. candidate marker -> identity-equivalent official marker + public verify")
        print("  6. GitHub Release + manifest + 4 pinned SBOM assets")
        print("  7. read-only rollback-window audit")
        for step in steps:
            print(f"  resolved: {step.name}: {step.action} ({step.conclusion})")
        return 0

    # OP-FIN-02 / CT-GEN-04: persist the fixed manifest bytes locally BEFORE
    # any publication mutation, so a failed local write can never leave a
    # published GitHub Release without its resume anchor (a later resume
    # would collide in _allocate_release_id on the never-reused id).
    write_json(args.manifest, intended)

    steps.append(_switch_official_marker(ctx, ids, prefix, official_marker))
    steps.append(_publish_release(api, release_id, intended, intended_bytes, sboms, candidate))
    post_window_complete, post_window = _window_audit(
        ctx, ids, api, _window_previous_releases(api, release_id),
        release_id, candidate,
    )
    steps.append(
        FinalizationStep(
            name="rollback-window-audit",
            action="verified",
            conclusion="complete" if post_window_complete else "incomplete",
        )
    )
    report = FinalizationReport(
        reportId=f"fin-{uuid4().hex[:16]}",
        producedAt=datetime.now(UTC),
        releaseId=release_id,
        resumed=resumed,
        steps=steps,
        rollbackCapableAtPublication=post_window_complete,
        window=post_window,
    )
    write_json(args.out, report)
    print(f"finalize: {release_id} published; report written to {args.out}")
    if not post_window_complete:
        raise MutationVerificationError(
            f"release {release_id} published but the rollback window is incomplete at "
            "publication time; evidence is preserved and recovery is manual (Phase 6)"
        )
    return 0


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def _validated_staging_gate(args, candidate) -> StagingOperationRecord:
    record = load_staging_record(args.staging_record)
    if not _STAGING_RECORD_IDENTITY.fullmatch(args.staging_record_identity):
        raise ValidationError(
            f"unsafe staging record identity {args.staging_record_identity!r}"
        )
    expected_identity = (
        f"staging-record-{record.candidate.workflowRunId}-"
        f"{record.candidate.workflowRunAttempt}"
    )
    if args.staging_record_identity != expected_identity:
        raise ValidationError(
            f"staging record identity {args.staging_record_identity!r} does not "
            f"match the record's embedded candidate run/attempt ({expected_identity}); "
            "foreign evidence identity refused (CT-STG-02)"
        )
    from ..validation import validate_staging_against_candidate

    errors = validate_staging_against_candidate(record, candidate)
    if errors:
        raise ValidationError(f"staging record is invalid: {'; '.join(errors)}")
    if record.phase.value != "COMPLETE":
        raise ValidationError(f"staging record phase is {record.phase.value}, not COMPLETE")
    if record.e2e.conclusion != "passed" or record.cleanup.conclusion != "passed":
        raise ValidationError("staging gate requires E2E and cleanup both passed (AD-09)")
    if record.failure is not None:
        raise ValidationError("staging record carries a failure entry")
    return record


def _validated_verification(args, candidate) -> VerificationReport:
    try:
        raw = json.loads(Path(args.verification_report).read_text())
    except OSError as error:
        raise ReadError(
            f"cannot read verification report {args.verification_report}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"verification report {args.verification_report} is not valid JSON: {error}"
        ) from error
    try:
        report = VerificationReport.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(
            f"verification report {args.verification_report} is invalid: {error}"
        ) from error
    if report.environment != "production":
        raise ValidationError("verification report is not for production")
    if report.conclusion != "passed":
        raise ValidationError(
            f"verification conclusion is {report.conclusion!r}; finalization requires "
            "observed verification passed (AD-06)"
        )
    # CT-REL-01: the recorded per-service expected digests must name exactly
    # the promoted candidate; a foreign report with conclusion "passed" but
    # different digests is never official verification evidence.
    for key in _SERVICE_KEYS:
        observation = report.services.get(key)
        if not isinstance(observation, dict):
            raise ValidationError(
                f"verification report has no services.{key} observation"
            )
        expected_digest = observation.get("expectedDigest")
        candidate_digest = getattr(candidate.artifacts, key).digest
        if expected_digest != candidate_digest:
            raise ValidationError(
                f"verification report services.{key}.expectedDigest "
                f"{expected_digest!r} does not match the promoted candidate digest "
                f"{candidate_digest} (CT-REL-01)"
            )
    return report


def _validated_approval(args) -> ApprovalEvidenceFile:
    try:
        raw = json.loads(Path(args.approval).read_text())
    except OSError as error:
        raise ReadError(f"cannot read approval evidence {args.approval}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"approval evidence {args.approval} is not valid JSON: {error}"
        ) from error
    try:
        return ApprovalEvidenceFile.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(f"approval evidence {args.approval} is invalid: {error}") from error


def _validated_publish(args, candidate) -> FrontendPublishRecord:
    try:
        raw = json.loads(Path(args.frontend_publish).read_text())
    except OSError as error:
        raise ReadError(
            f"cannot read frontend publish record {args.frontend_publish}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"frontend publish record {args.frontend_publish} is not valid JSON: {error}"
        ) from error
    try:
        record = FrontendPublishRecord.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(
            f"frontend publish record {args.frontend_publish} is invalid: {error}"
        ) from error
    if record.candidateId != candidate.candidateId:
        raise ValidationError(
            f"frontend publish record names {record.candidateId!r}, not the promoted "
            f"candidate {candidate.candidateId}"
        )
    return record


def _load_sboms(sbom_dir: str) -> dict[str, tuple[str, str, bytes]]:
    directory = Path(sbom_dir)
    loaded: dict[str, tuple[str, str, bytes]] = {}
    for component, filename in _SBOM_FILES.items():
        path = directory / filename
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise ReadError(f"SBOM file {path} is missing or unreadable: {error}") from error
        if not raw.strip():
            raise ValidationError(f"SBOM file {path} is empty")
        loaded[component] = (filename, sha256_hex(raw), raw)
    return loaded


# ---------------------------------------------------------------------------
# release id allocation and window audit
# ---------------------------------------------------------------------------


def _allocate_release_id(
    api: GitHubApi, publish: FrontendPublishRecord, manifest_path: str
) -> str:
    """AD-07: allocate the next never-reused id; exact-resume reuses the id
    recorded in the existing manifest file; any other mismatch fails closed.
    """
    if Path(manifest_path).exists():
        existing = _load_manifest_raw(manifest_path)
        if existing.releaseId != publish.provisionalReleaseId:
            raise ValidationError(
                f"existing manifest releaseId {existing.releaseId} does not match the "
                f"provisional id {publish.provisionalReleaseId} recorded by deploy frontend"
            )
        return existing.releaseId
    releases = api.list_releases()
    highest = 0
    for release in releases:
        match = re.fullmatch(r"release-(\d{4})", release["tag_name"])
        if match:
            highest = max(highest, int(match.group(1)))
    allocated = f"release-{highest + 1:04d}"
    if highest >= 9999:
        raise ValidationError("release id space exhausted (release-9999 reached)")
    for release in releases:
        if release["tag_name"] == allocated:
            raise ValidationError(
                f"release {allocated} already exists as a published GitHub Release; "
                "release ids are never reused (AD-07)"
            )
    if allocated != publish.provisionalReleaseId:
        raise ValidationError(
            f"the next release id {allocated} does not match the provisional id "
            f"{publish.provisionalReleaseId} used by deploy frontend; the deployed "
            "frontend prefix does not match the official release (AD-07)"
        )
    return allocated


def _load_manifest_raw(path: str) -> ReleaseManifest:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read release manifest {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"release manifest {path} is not valid JSON: {error}") from error
    try:
        return ReleaseManifest.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(f"release manifest {path} is invalid: {error}") from error


def _existing_promoted_at(path: str):
    return _load_manifest_raw(path).promotedAt


def _build_manifest(
    *,
    candidate,
    snapshot: ProductionSnapshot,
    staging_identity: str,
    verification: VerificationReport,
    approval: ApprovalEvidenceFile,
    publish: FrontendPublishRecord,
    sboms: dict[str, tuple[str, str, bytes]],
    release_id: str,
    rollback_capable: bool,
    promoted_at,
) -> ReleaseManifest:
    now = datetime.now(UTC) if promoted_at is None else promoted_at
    return ReleaseManifest(
        releaseId=release_id,
        candidateId=candidate.candidateId,
        source=ReleaseSource(fullSha=candidate.source.fullSha, branch="main"),
        previousReleaseId=snapshot.release.releaseId,
        promotedAt=now,
        requester=approval.requester,
        approval=ApprovalEvidence(
            evidence=(
                f"environment production approval by {approval.approver} "
                "(actions/runs approvals API)"
            ),
            workflowUrl=approval.workflowUrl,
        ),
        artifacts={
            "auth": {
                "repository": candidate.artifacts.auth.repository,
                "digest": candidate.artifacts.auth.digest,
            },
            "items": {
                "repository": candidate.artifacts.items.repository,
                "digest": candidate.artifacts.items.digest,
            },
            "gateway": {
                "repository": candidate.artifacts.gateway.repository,
                "digest": candidate.artifacts.gateway.digest,
            },
            "frontend": ReleaseFrontend(
                immutableIdentity=publish.prefixKey,
                checksum=candidate.artifacts.frontend.contentChecksum,
            ),
            "sbom": SbomSet(
                **{
                    component: SbomAsset(assetName=name, sha256=digest)
                    for component, (name, digest, _raw) in sboms.items()
                }
            ),
        },
        compatibilityFingerprint=snapshot.compatibilityFingerprint,
        staging=StagingEvidence(
            evidenceIdentity=staging_identity,
            conclusion="passed (exact-candidate cloud E2E passed, cleanup passed)",
        ),
        productionVerification=ProductionVerification(
            evidenceIdentity=verification.reportId,
            conclusion=verification.conclusion,
        ),
        rollbackCapableAtPublication=rollback_capable,
    )


def _window_previous_releases(api: GitHubApi, release_id: str) -> list[dict]:
    releases = api.list_releases()
    official = [
        release
        for release in releases
        if re.fullmatch(r"release-\d{4}", release["tag_name"])
        and release["tag_name"] != release_id
    ]
    official.sort(key=lambda release: release["tag_name"], reverse=True)
    return official[:3]


def _window_audit(
    ctx,
    ids: dict,
    api: GitHubApi,
    previous_releases: list[dict],
    release_id: str,
    candidate,
) -> tuple[bool, list[RollbackWindowEntry]]:
    """Read-only rollback-window audit (OP-RET-01, Phase 5 scope).

    ``current`` (the release being finalized) is complete when its ECR tags
    and immutable frontend prefix exist. Each previous release is complete
    when its manifest asset parses and validates AND its three ECR
    ``release-*`` tags resolve to the manifest digests AND its immutable
    frontend prefix marker exists. Full retention policy enforcement is
    Phase 6; this audit only proves the window is complete at publication.
    """
    entries: list[RollbackWindowEntry] = []
    current_complete = _current_window_entry(ctx, ids, release_id, candidate)
    entries.append(current_complete)
    for release in previous_releases:
        entries.append(_previous_window_entry(ctx, ids, api, release["tag_name"]))
    return all(entry.complete for entry in entries), entries


def _current_window_entry(ctx, ids, release_id, candidate) -> RollbackWindowEntry:
    failures: list[str] = []
    ecr_client = aws_context.client_for(ctx, "ecr")
    for key in _SERVICE_KEYS:
        repository = ids["ecrRepositories"][key]
        expected = getattr(candidate.artifacts, key).digest
        try:
            observed = ecr_client.batch_get_image(
                repositoryName=repository, imageIds=[{"imageTag": release_id}]
            )
            images = observed.get("images") or []
            if not images or images[0].get("imageDigest") != expected:
                failures.append(f"ECR tag {repository}:{release_id} does not resolve to {expected}")
        except ClientError as error:
            failures.append(f"ECR read error for {repository}:{release_id}: {error}")
    s3_client = aws_context.client_for(ctx, "s3")
    prefix = f"{ids['frontendReleasesPrefix']}{release_id}/"
    try:
        s3_client.head_object(Bucket=ids["frontendBucket"], Key=f"{prefix}{_PREFIX_MARKER}")
    except ClientError as error:
        failures.append(f"frontend prefix marker missing for {release_id}: {error}")
    return RollbackWindowEntry(
        releaseId=release_id,
        complete=not failures,
        detail="; ".join(failures) if failures else "current release bytes verified",
    )


def _previous_window_entry(ctx, ids, api: GitHubApi, release_id: str) -> RollbackWindowEntry:
    failures: list[str] = []
    releases = [release for release in api.list_releases() if release["tag_name"] == release_id]
    if not releases:
        return RollbackWindowEntry(
            releaseId=release_id, complete=False, detail="GitHub Release not found"
        )
    assets = {asset["name"]: asset["url"] for asset in releases[0]["assets"]}
    if _MANIFEST_ASSET not in assets:
        return RollbackWindowEntry(
            releaseId=release_id, complete=False, detail="release manifest asset missing"
        )
    try:
        manifest_raw = api.download_asset(assets[_MANIFEST_ASSET]).decode("utf-8")
        manifest = ReleaseManifest.model_validate(json.loads(manifest_raw))
    except (UnicodeDecodeError, json.JSONDecodeError, PydanticValidationError) as error:
        return RollbackWindowEntry(
            releaseId=release_id, complete=False, detail=f"manifest unreadable: {error}"
        )
    errors = validate_record(manifest)
    if errors:
        return RollbackWindowEntry(
            releaseId=release_id, complete=False, detail="; ".join(errors)
        )
    ecr_client = aws_context.client_for(ctx, "ecr")
    for key in _SERVICE_KEYS:
        repository = ids["ecrRepositories"][key]
        expected = getattr(manifest.artifacts, key).digest
        try:
            observed = ecr_client.batch_get_image(
                repositoryName=repository, imageIds=[{"imageTag": release_id}]
            )
            images = observed.get("images") or []
            if not images or images[0].get("imageDigest") != expected:
                failures.append(f"ECR tag {repository}:{release_id} mismatch")
        except ClientError as error:
            failures.append(f"ECR read error for {repository}:{release_id}: {error}")
    s3_client = aws_context.client_for(ctx, "s3")
    prefix = f"{ids['frontendReleasesPrefix']}{release_id}/"
    try:
        s3_client.head_object(Bucket=ids["frontendBucket"], Key=f"{prefix}{_PREFIX_MARKER}")
    except ClientError as error:
        failures.append(f"frontend prefix marker missing for {release_id}: {error}")
    return RollbackWindowEntry(
        releaseId=release_id,
        complete=not failures,
        detail="; ".join(failures) if failures else "complete release verified",
    )


# ---------------------------------------------------------------------------
# mutation steps (each with immediate read-back)
# ---------------------------------------------------------------------------


def _mint_ecr_tags(
    ecr_client, ids: dict, candidate, release_id: str, *, dry_run: bool = False
) -> FinalizationStep:
    for key in _SERVICE_KEYS:
        repository = ids["ecrRepositories"][key]
        expected = getattr(candidate.artifacts, key).digest
        existing = ecr_client.batch_get_image(
            repositoryName=repository, imageIds=[{"imageTag": release_id}]
        )
        images = existing.get("images") or []
        if images:
            observed_digest = images[0].get("imageDigest")
            if observed_digest != expected:
                raise MutationVerificationError(
                    f"ECR tag {repository}:{release_id} already resolves to "
                    f"{observed_digest}, not the candidate {expected}; refusing to "
                    "overwrite (AD-07)"
                )
            continue
        if dry_run:
            continue
        source = ecr_client.batch_get_image(
            repositoryName=repository, imageIds=[{"imageDigest": expected}]
        )
        source_images = source.get("images") or []
        manifest_bytes = (source_images[0].get("imageManifest") or "").encode("utf-8")
        if not manifest_bytes:
            raise MutationVerificationError(
                f"no imageManifest recorded for {expected} in {repository}"
            )
        put_image(ecr_client, repository, release_id, manifest_bytes)
        readback = ecr_client.batch_get_image(
            repositoryName=repository, imageIds=[{"imageTag": release_id}]
        )
        readback_images = readback.get("images") or []
        if not readback_images or readback_images[0].get("imageDigest") != expected:
            raise MutationVerificationError(
                f"ECR tag read-back failed for {repository}:{release_id}"
            )
    action = "created" if not dry_run else "verified"
    conclusion = (
        f"{release_id} tags resolve to the candidate digests"
        if not dry_run
        else f"{release_id} tags would be minted from the recorded manifest bytes"
    )
    return FinalizationStep(name="ecr-release-tags", action=action, conclusion=conclusion)


def _protect_frontend(ctx, ids: dict, prefix: str, candidate_marker) -> FinalizationStep:
    s3_client = aws_context.client_for(ctx, "s3")
    bucket = ids["frontendBucket"]
    for key in (f"{prefix}{_PREFIX_MARKER}", f"{prefix}index.html", f"{prefix}frontend.tar.gz"):
        try:
            s3_client.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if absent_or_read(error):
                raise AbsentResourceError(
                    f"immutable frontend object s3://{bucket}/{key} not found; "
                    "deploy frontend owns the immutable prefix"
                ) from error
            raise ReadError(f"head_object failed for s3://{bucket}/{key}") from error
    prefix_marker_raw = read_s3_text(
        s3_client, bucket, f"{prefix}{_PREFIX_MARKER}", "prefix marker"
    )
    prefix_marker = live_marker.parse_live_marker(prefix_marker_raw.strip())
    if prefix_marker is None or not live_marker.markers_identity_equivalent(
        prefix_marker, candidate_marker
    ):
        raise MutationVerificationError(
            "immutable prefix marker does not name the promoted candidate"
        )
    return FinalizationStep(
        name="frontend-protection",
        action="verified",
        conclusion="immutable prefix marker, index.html, and bundle verified",
    )


def _switch_official_marker(ctx, ids: dict, prefix: str, official_marker) -> FinalizationStep:
    s3_client = aws_context.client_for(ctx, "s3")
    bucket = ids["frontendBucket"]
    marker_key = ids["frontendLiveMarker"]
    official_doc = live_marker.marker_document(official_marker)
    current = read_s3_text(s3_client, bucket, marker_key, "live marker").strip()
    if current == official_doc:
        return FinalizationStep(
            name="official-marker", action="resumed", conclusion="official marker already live"
        )
    put_object(s3_client, bucket, f"{prefix}{_PREFIX_MARKER}", official_doc.encode())
    put_object(s3_client, bucket, marker_key, official_doc.encode())
    readback = read_s3_text(s3_client, bucket, marker_key, "live marker").strip()
    if readback != official_doc:
        raise MutationVerificationError("live marker read-back does not name the official release")
    cloudfront_client = aws_context.client_for(ctx, "cloudfront")
    create_invalidation(cloudfront_client, ids["cloudfrontDistributionId"], ["/release.json"])
    domain = (
        get_distribution(cloudfront_client, ids["cloudfrontDistributionId"])
        .get("Distribution") or {}
    ).get("DomainName")
    if not domain:
        raise ReadError("distribution has no DomainName")
    _wait_for_public_marker(f"https://{domain}", official_doc)
    return FinalizationStep(
        name="official-marker",
        action="created",
        conclusion="candidate marker replaced with the official marker and verified publicly",
    )


def _wait_for_public_marker(cloudfront_url: str, official_doc: str) -> None:
    last_status = ""

    def poll() -> bool:
        nonlocal last_status
        status, _headers, body = _fetch(f"{cloudfront_url}/release.json")
        last_status = f"HTTP {status}"
        return status == 200 and body.decode("utf-8", errors="replace").strip() == official_doc

    bounded_waiter(
        poll,
        label=f"public marker at {cloudfront_url}/release.json",
        timeout_seconds=_PUBLIC_MARKER_TIMEOUT,
        interval_seconds=15,
    )
    if last_status != "HTTP 200":
        raise MutationVerificationError(
            f"public marker verification failed after switch ({last_status})"
        )


def _publish_release(
    api: GitHubApi,
    release_id: str,
    intended: ReleaseManifest,
    intended_bytes: bytes,
    sboms: dict[str, tuple[str, str, bytes]],
    candidate,
) -> FinalizationStep:
    body = (
        f"Official release {release_id} for OnlineShop.\n\n"
        f"- candidate: {candidate.candidateId}\n"
        f"- source SHA: {candidate.source.fullSha}\n"
        f"- promoted at: {intended.promotedAt.isoformat().replace('+00:00', 'Z')}\n"
        f"- requester: {intended.requester}\n"
        f"- approval workflow: {intended.approval.workflowUrl}\n"
    )
    releases = api.list_releases()
    existing = [release for release in releases if release["tag_name"] == release_id]
    if existing:
        current_assets = {asset["name"]: asset["url"] for asset in existing[0]["assets"]}
        if _MANIFEST_ASSET not in current_assets:
            raise MutationVerificationError(
                f"GitHub Release {release_id} exists without its manifest asset; "
                "mismatch never resumes (OP-FIN-02)"
            )
        observed = api.download_asset(current_assets[_MANIFEST_ASSET])
        if observed != intended_bytes:
            raise MutationVerificationError(
                f"GitHub Release {release_id} manifest bytes differ from the intended "
                "manifest; mismatch never resumes (OP-FIN-02)"
            )
        release_info = existing[0]
    else:
        release_info = api.create_release(
            tag=release_id,
            name=f"OnlineShop {release_id}",
            body=body,
        )
    assets_uploaded = []
    asset_payloads = [(_MANIFEST_ASSET, intended_bytes)] + [
        (name, raw) for _component, (name, _digest, raw) in sboms.items()
    ]
    for asset_name, payload in asset_payloads:
        observed = _find_asset(api, release_info, asset_name)
        if observed is not None:
            if observed != payload:
                raise MutationVerificationError(
                    f"release asset {asset_name} bytes differ from the intended bytes; "
                    "mismatch never resumes (OP-FIN-02)"
                )
            continue
        api.upload_release_asset(release_info["id"], asset_name, payload)
        assets_uploaded.append(asset_name)
    conclusion = "release published with manifest + 4 SBOM assets"
    if assets_uploaded:
        conclusion += f" (uploaded {len(assets_uploaded)})"
    return FinalizationStep(
        name="github-release",
        action="created" if not existing else "resumed",
        conclusion=conclusion,
    )


def _find_asset(api: GitHubApi, release_info: dict, asset_name: str) -> bytes | None:
    releases = [
        release
        for release in api.list_releases()
        if release["tag_name"] == release_info["tag_name"]
    ]
    if not releases:
        return None
    for asset in releases[0]["assets"]:
        if asset["name"] == asset_name:
            return api.download_asset(asset["url"])
    return None


# ---------------------------------------------------------------------------
# exact-match resume
# ---------------------------------------------------------------------------


def _assert_exact_resume(manifest_path: str, intended: ReleaseManifest) -> None:
    try:
        existing_bytes = Path(manifest_path).read_bytes()
    except OSError as error:
        raise ReadError(f"cannot read existing manifest {manifest_path}: {error}") from error
    intended_bytes = (canonical_json(intended.model_dump(mode="json")) + "\n").encode()
    if existing_bytes == intended_bytes:
        return
    try:
        existing = json.loads(existing_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError(
            f"existing manifest {manifest_path} is not valid JSON; resume refused: {error}"
        ) from error
    differences = _dict_differences(existing, json.loads(intended_bytes.decode("utf-8")))
    raise ValidationError(
        f"existing manifest {manifest_path} does not match the intended release; "
        "exact-match resume refused (OP-FIN-02). Differences: "
        + ("; ".join(differences[:10]) or "unknown")
    )


def _dict_differences(existing: dict, intended: dict, prefix: str = "") -> list[str]:
    differences: list[str] = []
    for key in sorted(set(existing) | set(intended)):
        path = f"{prefix}.{key}" if prefix else key
        if key not in existing:
            differences.append(f"{path}: missing in existing")
        elif key not in intended:
            differences.append(f"{path}: extra in existing")
        elif isinstance(existing[key], dict) and isinstance(intended[key], dict):
            differences.extend(_dict_differences(existing[key], intended[key], path))
        elif existing[key] != intended[key]:
            differences.append(f"{path}: {existing[key]!r} != {intended[key]!r}")
    return differences
