"""deploy backends | gateway | frontend: OP-DEP-01..03 production deployment.

- ``backends`` deploys Auth + Items (registered and updated first so both
  deployments roll in parallel), then ``gateway`` after both backends, then
  ``frontend`` changes the live entry point last (AD-12).
- Every backend deployment proves the task-definition diff is image-only
  against the OBSERVED snapshot revision, keeps secrets as full-ARN
  ``secrets[].valueFrom`` references, binds its waiter to the deployment it
  started, and compares the observed running-task digests with the candidate
  digests (OP-DEP-02 / CT-PROD-01).
- ``frontend`` publishes the candidate bundle under the immutable
  ``_releases/<release-NNNN>/`` prefix, proves the S3 aggregate content
  checksum BEFORE switching the live entry point, writes the live marker
  naming the CANDIDATE (OP-DEP-03), invalidates CloudFront, and records the
  publication evidence for ``finalize``.

Recovery and compensation are Phase 6: a post-mutation failure fails the
command honestly; the workflow preserves the snapshot and evidence artifacts.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .. import frontend as frontend_utils
from .. import live_marker
from ..aws import context as aws_context
from ..aws import (
    create_invalidation,
    describe_services,
    describe_task_definition,
    get_object_sha256,
    put_object,
    register_task_definition,
    replace_container_images,
    task_definition_images,
    update_service,
    wait_for_deployment,
)
from ..errors import (
    AbsentResourceError,
    MutationVerificationError,
    ReadError,
    ValidationError,
)
from ..github import GitHubApi
from ..models import FrontendPublishRecord, ProductionSnapshot
from ..records import load_candidate, load_snapshot, read_s3_text, write_json
from ..serialization import sha256_hex
from .deploy_support import (
    DEPLOYMENT_TIMEOUT,
    DEPLOYMENT_VISIBILITY_DELAY,
    DEPLOYMENT_VISIBILITY_RETRIES,
    DIGEST_VERIFY_INTERVAL,
    DIGEST_VERIFY_TIMEOUT,
    assert_full_arn_secrets,
    deployment_for_revision,
    verify_running_digests,
)

_SERVICE_KEYS = ("auth", "items", "gateway")
# Historical module-level names: deploy's tests and callers monkeypatch these.
_DIGEST_VERIFY_TIMEOUT = DIGEST_VERIFY_TIMEOUT
_DIGEST_VERIFY_INTERVAL = DIGEST_VERIFY_INTERVAL
_DEPLOYMENT_VISIBILITY_RETRIES = DEPLOYMENT_VISIBILITY_RETRIES
_DEPLOYMENT_VISIBILITY_DELAY = DEPLOYMENT_VISIBILITY_DELAY
_DEPLOYMENT_TIMEOUT = DEPLOYMENT_TIMEOUT
_FRONTEND_BUNDLE = "frontend.tar.gz"
_PREFIX_MARKER = "release.json"


def backends(args: argparse.Namespace) -> int:
    return _deploy_services(args, ("auth", "items"), "backends")


def gateway(args: argparse.Namespace) -> int:
    return _deploy_services(args, ("gateway",), "gateway")


# ---------------------------------------------------------------------------
# backends / gateway
# ---------------------------------------------------------------------------


def _deploy_services(args: argparse.Namespace, keys: tuple[str, ...], label: str) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    ids = args.identifiers_data
    manifest = load_candidate(args.candidate)
    snapshot = load_snapshot(args.snapshot, require_environment="production")
    _assert_candidate_matches_snapshot_services(ids, snapshot)
    registry = f"{ids['accountId']}.dkr.ecr.{ctx.region}.amazonaws.com"
    ecs_client = aws_context.client_for(ctx, "ecs")

    index = {key: position for position, key in enumerate(_SERVICE_KEYS)}
    deployments: dict[str, tuple[str, str]] = {}
    for key in keys:
        service = ids["services"][index[key]]
        expected_digest = getattr(manifest.artifacts, key).digest
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
            if args.dry_run:
                print(f"{service}: already runs {expected_digest}; nothing to register")
            else:
                _verify_running_digests(ecs_client, ids, service, expected_digest)
            deployments[key] = (service, "unchanged")
            continue
        if args.dry_run:
            print(
                f"{service}: would register a digest-pinned revision for "
                f"{expected_digest} and update the service"
            )
            deployments[key] = (service, "unchanged")
            continue
        revision_arn = register_task_definition(
            ecs_client,
            replace_container_images(td, {matches[0]: target}),
        )
        _assert_full_arn_secrets(revision_arn, ecs_client)
        update_service(ecs_client, ids["cluster"], service, revision_arn)
        deployment_id = _deployment_for_revision(
            ecs_client, ids["cluster"], service, revision_arn
        )
        deployments[key] = (service, deployment_id)

    if args.dry_run:
        print(f"deploy {label}: dry-run complete; no mutation performed")
        return 0

    for key in keys:
        service, deployment_id = deployments[key]
        if deployment_id == "unchanged":
            print(f"{service}: already running the candidate digest; verified")
            continue
        wait_for_deployment(
            ecs_client,
            ids["cluster"],
            service,
            deployment_id,
            timeout_seconds=_DEPLOYMENT_TIMEOUT,
        )
        expected_digest = getattr(manifest.artifacts, key).digest
        _verify_running_digests(ecs_client, ids, service, expected_digest)
        print(f"{service}: deployment {deployment_id} complete; running digest verified")
    print(f"deploy {label}: complete")
    return 0


def _assert_candidate_matches_snapshot_services(ids: dict, snapshot: ProductionSnapshot) -> None:
    if len(ids["services"]) != len(_SERVICE_KEYS):
        raise ValidationError(
            "identifiers services must contain exactly 3 names in order auth, items, api-gateway"
        )
    for key, name in zip(_SERVICE_KEYS, ids["services"], strict=True):
        if key not in snapshot.services:
            raise ValidationError(f"snapshot has no observation for service {name} ({key})")


def _assert_observed_td_unchanged(
    observed: dict, snapshot: ProductionSnapshot, key: str, service: str
) -> None:
    """The service must still point at the snapshot revision (fresh state)."""
    expected_arn = snapshot.services[key].taskDefinitionArn
    if observed.get("taskDefinition") != expected_arn:
        raise MutationVerificationError(
            f"service {service} taskDefinition changed since the snapshot "
            f"(expected {expected_arn}, observed {observed.get('taskDefinition')!r}); "
            "aborting before mutation (OP-GEN-01)"
        )


def _assert_full_arn_secrets(revision_arn: str, ecs_client) -> None:
    """Secrets must stay full-ARN ``secrets[].valueFrom`` references (never plaintext)."""
    assert_full_arn_secrets(ecs_client, revision_arn)


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
# frontend
# ---------------------------------------------------------------------------


def frontend(args: argparse.Namespace) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    ids = args.identifiers_data
    manifest = load_candidate(args.candidate)
    snapshot = load_snapshot(args.snapshot, require_environment="production")
    archive_bytes = _read_archive(args.frontend_archive)
    dist_dir = frontend_utils.verify_frontend_archive(args.frontend_archive, manifest)
    _require_index(dist_dir)

    api = GitHubApi(manifest.source.repository)
    provisional = _next_release_id(api)
    prefix = f"{ids['frontendReleasesPrefix']}{provisional}/"
    bucket = ids["frontendBucket"]
    s3_client = aws_context.client_for(ctx, "s3")
    _assert_live_marker_matches_snapshot(s3_client, ids, snapshot)

    files = sorted(
        path for path in dist_dir.rglob("*") if path.is_file()
    )
    plan = [f"{prefix}{path.relative_to(dist_dir).as_posix()}" for path in files]
    plan.append(f"{prefix}{_FRONTEND_BUNDLE}")
    plan.append(f"{prefix}{_PREFIX_MARKER}")

    candidate_marker = live_marker.build_candidate_marker(
        candidate_id=manifest.candidateId,
        source_sha=manifest.source.fullSha,
        frontend_sha256=manifest.artifacts.frontend.contentChecksum,
    )

    # OP-DEP-03: never overwrite an existing provisional prefix with a
    # different identity — the prefix marker must either be absent or name
    # exactly this candidate (finalize enforces the same protection for the
    # official prefix; this guard is not weaker).
    _assert_prefix_marker_matches_candidate(s3_client, bucket, prefix, candidate_marker)

    if not args.dry_run:
        for path in files:
            relative = path.relative_to(dist_dir).as_posix()
            put_object(s3_client, bucket, f"{prefix}{relative}", path.read_bytes())
        put_object(s3_client, bucket, f"{prefix}{_FRONTEND_BUNDLE}", archive_bytes)
        put_object(
            s3_client,
            bucket,
            f"{prefix}{_PREFIX_MARKER}",
            live_marker.marker_document(candidate_marker).encode(),
        )
        # CT-PROD-02: prove the immutable S3 identity (aggregate content
        # checksum recomputed from S3 full-object checksums) BEFORE touching
        # the live entry point (OP-DEP-03).
        observed_checksum = _prefix_content_checksum(
            s3_client, bucket, prefix, files, dist_dir
        )
        if observed_checksum != manifest.artifacts.frontend.contentChecksum:
            raise MutationVerificationError(
                f"published frontend prefix checksum {observed_checksum} does not match "
                f"candidate {manifest.artifacts.frontend.contentChecksum}; "
                "the live entry point was NOT switched"
            )

    if args.dry_run:
        print(f"deploy frontend: dry-run plan for {provisional}:")
        for entry in plan:
            print(f"  s3://{bucket}/{entry}")
        print("live switch: dist files (index.html last), then live marker, then invalidation")
        return 0

    # Live entry point switch: files first, index.html last, marker last.
    ordered = sorted(files, key=lambda path: (path.name == "index.html", path.as_posix()))
    for path in ordered:
        relative = path.relative_to(dist_dir).as_posix()
        put_object(s3_client, bucket, relative, path.read_bytes())
    put_object(
        s3_client,
        bucket,
        ids["frontendLiveMarker"],
        live_marker.marker_document(candidate_marker).encode(),
    )
    _verify_live_marker(s3_client, ids, candidate_marker)
    cloudfront_client = aws_context.client_for(ctx, "cloudfront")
    create_invalidation(cloudfront_client, ids["cloudfrontDistributionId"], ["/*"])
    print(f"frontend live entry point switched to candidate {manifest.candidateId}")

    record = FrontendPublishRecord(
        candidateId=manifest.candidateId,
        provisionalReleaseId=provisional,
        prefixKey=prefix,
        liveMarkerKey=ids["frontendLiveMarker"],
        contentChecksum=manifest.artifacts.frontend.contentChecksum,
    )
    write_json(args.out, record)
    print(f"deploy frontend: complete; publish evidence written to {args.out}")
    return 0


def _assert_prefix_marker_matches_candidate(
    s3_client, bucket: str, prefix: str, candidate_marker
) -> None:
    marker_key = f"{prefix}{_PREFIX_MARKER}"
    try:
        existing_raw = read_s3_text(
            s3_client, bucket, marker_key, "provisional prefix marker"
        ).strip()
    except AbsentResourceError:
        return
    existing = live_marker.parse_live_marker(existing_raw)
    if existing is None or not live_marker.markers_identity_equivalent(
        existing, candidate_marker
    ):
        raise MutationVerificationError(
            f"immutable prefix marker {marker_key} already exists and does not "
            "name the promoted candidate; refusing to overwrite the provisional "
            f"prefix {prefix} (OP-DEP-03)"
        )


def _read_archive(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as error:
        raise ReadError(f"cannot read frontend archive {path}: {error}") from error


def _require_index(dist_dir: Path) -> None:
    if not (dist_dir / "index.html").is_file():
        raise ValidationError("frontend archive does not contain index.html")


def _next_release_id(api: GitHubApi) -> str:
    """Provisional next release id (AD-07): highest official number + 1.

    Final allocation happens in ``finalize`` under the production lock and
    must equal this provisional id; a mismatch fails closed.
    """
    releases = api.list_releases()
    highest = 0
    for release in releases:
        match = re.fullmatch(r"release-(\d{4})", release["tag_name"])
        if match:
            highest = max(highest, int(match.group(1)))
    if highest >= 9999:
        raise ValidationError("release id space exhausted (release-9999 reached)")
    return f"release-{highest + 1:04d}"


def _prefix_content_checksum(
    s3_client, bucket: str, prefix: str, files: list[Path], dist_dir: Path
) -> str:
    lines = []
    for path in sorted(files, key=lambda p: p.relative_to(dist_dir).as_posix()):
        relative = path.relative_to(dist_dir).as_posix()
        observed = get_object_sha256(s3_client, bucket, f"{prefix}{relative}")
        lines.append(f"{observed}\n")
    return sha256_hex("".join(lines).encode())


def _verify_live_marker(s3_client, ids: dict, candidate_marker) -> None:
    current = read_s3_text(
        s3_client, ids["frontendBucket"], ids["frontendLiveMarker"], "live marker"
    )
    expected = live_marker.marker_document(candidate_marker)
    if current.strip() != expected:
        raise MutationVerificationError("live marker read-back does not name the candidate")


def _assert_live_marker_matches_snapshot(s3_client, ids: dict, snapshot) -> None:
    """The live marker must still match the just-captured snapshot before the
    live entry point switch (fresh state, OP-GEN-01)."""
    current = read_s3_text(
        s3_client, ids["frontendBucket"], ids["frontendLiveMarker"], "live marker"
    ).strip()
    if current != snapshot.frontend.immutableIdentity:
        raise MutationVerificationError(
            "live marker changed since the snapshot; aborting before the frontend switch"
        )
    if get_object_sha256(s3_client, ids["frontendBucket"], ids["frontendLiveMarker"]) != (
        snapshot.frontend.checksum
    ):
        raise MutationVerificationError(
            "live marker checksum changed since the snapshot; aborting before the frontend switch"
        )
