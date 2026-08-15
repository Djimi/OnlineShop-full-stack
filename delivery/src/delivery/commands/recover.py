"""recover: automatic compensation from the pre-mutation snapshot (AD-13, OP-REC-02).

Restores ONLY from the snapshot — never the failed candidate or hand-written
inputs — and never touches the database (OP-DB-02):

backends auth|items|gateway  re-register the snapshot's exact digest-pinned
                              task-definition revision (image-only; secrets
                              stay full-ARN ``secrets[].valueFrom``), update
                              the service, bound the deployment waiter, and
                              verify observed running digests equal the
                              snapshot digests.
frontend                      rewrite the live marker from the snapshot
                              frontend identity (checksum proven BEFORE the
                              write), invalidate CloudFront, read back both.

Ambiguous input — inconsistent snapshot internals, missing restore fields,
or AWS read errors — stops with evidence and never guesses; a read error is
never treated as absence. The recovery result records the original failure
and the recovery outcome separately (OP-REC-02); a failed recovery is
reported as failed and never as success.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .. import live_marker
from ..aws import context as aws_context
from ..aws import (
    create_invalidation,
    describe_services,
    describe_task_definition,
    get_object_sha256,
    put_object,
    register_task_definition,
    update_service,
    wait_for_deployment,
)
from ..aws.ecs import sanitize_task_definition, task_definition_images
from ..errors import (
    AmbiguousStateError,
    DeliveryError,
    MutationVerificationError,
    ReadError,
    ValidationError,
)
from ..models import ComponentRecovery, ProductionSnapshot, RecoveryResult
from ..records import load_snapshot, read_s3_text, write_json
from ..serialization import sha256_hex
from .deploy_support import (
    DEPLOYMENT_TIMEOUT,
    DIGEST_VERIFY_INTERVAL,
    DIGEST_VERIFY_TIMEOUT,
    assert_full_arn_secrets,
    deployment_for_revision,
    verify_running_digests,
)

_CHANGED_COMPONENTS = ("auth", "items", "gateway", "frontend")
_SERVICE_KEYS = ("auth", "items", "gateway")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TD_ARN = re.compile(r"^arn:aws:ecs:[a-z0-9-]+:\d{12}:task-definition/[A-Za-z0-9_-]+:\d+$")


def recover(args: argparse.Namespace) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    ids = args.identifiers_data
    snapshot = load_snapshot(args.snapshot, require_environment="production")
    changed = _load_changed(args.changed)
    _validate_snapshot_for_recovery(snapshot, changed, ids)
    ordered = [name for name in _CHANGED_COMPONENTS if name in changed]

    if args.dry_run:
        for component in ordered:
            _print_dry_run_plan(component, snapshot, ids)
        print("recover: dry-run complete; no mutation performed")
        return 0

    result = RecoveryResult(
        recoveryId=f"rec-{uuid4().hex[:16]}",
        snapshotId=snapshot.snapshotId,
        snapshotCapturedAt=snapshot.capturedAt,
        environment="production",
        originalFailure=args.original_failure or "unknown",
        startedAt=datetime.now(UTC),
        outcome="completed",
        components=[],
    )
    current: str | None = None
    try:
        for current in ordered:
            detail = (
                _restore_frontend(ctx, ids, snapshot)
                if current == "frontend"
                else _restore_service(ctx, ids, snapshot, current)
            )
            result.components.append(
                ComponentRecovery(component=current, conclusion="restored", detail=detail)
            )
    except DeliveryError as error:
        failed_component = current if current is not None else ordered[-1]
        result.outcome = "failed"
        result.completedAt = datetime.now(UTC)
        result.failureDetail = f"{error.code}: {error}"
        result.components.append(
            ComponentRecovery(
                component=failed_component,
                conclusion="failed",
                detail=f"{error.code}: {error}",
            )
        )
        for remaining in ordered[ordered.index(failed_component) + 1 :]:
            result.components.append(
                ComponentRecovery(
                    component=remaining,
                    conclusion="not-attempted",
                    detail="recovery stopped before this component",
                )
            )
        if args.out:
            write_json(args.out, result)
        raise
    result.completedAt = datetime.now(UTC)
    if args.out:
        write_json(args.out, result)
    _print_summary(result)
    return 0


# ---------------------------------------------------------------------------
# changed-array input
# ---------------------------------------------------------------------------


def _load_changed(path: str) -> list[str]:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read changed components file {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"changed components file {path} is not valid JSON: {error}"
        ) from error
    if not isinstance(raw, list) or not raw:
        raise ValidationError("--changed must be a non-empty JSON array of component names")
    if not all(isinstance(item, str) for item in raw):
        raise ValidationError("--changed must contain only strings")
    unknown = sorted({item for item in raw if item not in _CHANGED_COMPONENTS})
    if unknown:
        raise ValidationError(
            f"--changed contains unknown component names: {', '.join(unknown)}; "
            f"allowed components are {', '.join(_CHANGED_COMPONENTS)}"
        )
    if len(set(raw)) != len(raw):
        raise ValidationError(f"--changed contains duplicate components: {sorted(raw)}")
    return list(raw)


# ---------------------------------------------------------------------------
# snapshot consistency (fail closed, never guess)
# ---------------------------------------------------------------------------


def _validate_snapshot_for_recovery(
    snapshot: ProductionSnapshot, changed: list[str], ids: dict
) -> None:
    for key in _SERVICE_KEYS:
        if key not in changed:
            continue
        observation = snapshot.services.get(key)
        if observation is None:
            raise ValidationError(
                f"snapshot has no observation for service {key}; cannot restore it"
            )
        if not _TD_ARN.fullmatch(observation.taskDefinitionArn):
            raise AmbiguousStateError(
                f"snapshot service {key} taskDefinitionArn "
                f"{observation.taskDefinitionArn!r} is not a valid ECS task-definition ARN; "
                "cannot restore from an inconsistent snapshot"
            )
        if len(observation.runningDigests) != 1:
            raise AmbiguousStateError(
                f"snapshot service {key} records {len(observation.runningDigests)} running "
                "digests; anything but exactly one observed digest is ambiguous and must "
                "not be restored automatically"
            )
        if not _IMAGE_DIGEST.fullmatch(observation.runningDigests[0]):
            raise AmbiguousStateError(
                f"snapshot service {key} running digest "
                f"{observation.runningDigests[0]!r} is malformed; inconsistent snapshot"
            )
    if "frontend" in changed:
        _validate_frontend_observation(snapshot, ids)


def _validate_frontend_observation(snapshot: ProductionSnapshot, ids: dict) -> None:
    frontend = snapshot.frontend
    marker = frontend.immutableIdentity
    if not isinstance(marker, str) or not marker.strip():
        raise ValidationError(
            "snapshot frontend immutableIdentity is empty; cannot restore the live marker"
        )
    if sha256_hex(marker.encode()) != frontend.checksum:
        raise AmbiguousStateError(
            "snapshot frontend identity checksum does not match the recorded marker bytes; "
            "the snapshot is inconsistent and must not be restored automatically"
        )
    if frontend.liveMarker != ids.get("frontendLiveMarker"):
        raise AmbiguousStateError(
            f"snapshot live marker key {frontend.liveMarker!r} does not match identifiers "
            f"frontendLiveMarker {ids.get('frontendLiveMarker')!r}"
        )
    if frontend.cloudfrontDistributionId != ids.get("cloudfrontDistributionId"):
        raise AmbiguousStateError(
            f"snapshot CloudFront distribution {frontend.cloudfrontDistributionId!r} does "
            f"not match identifiers cloudfrontDistributionId "
            f"{ids.get('cloudfrontDistributionId')!r}"
        )
    marker_release = live_marker.marker_release_id(marker)
    if snapshot.release.status == "official":
        if marker_release != snapshot.release.releaseId:
            raise AmbiguousStateError(
                f"snapshot release status is official ({snapshot.release.releaseId}) but the "
                f"recorded marker names {marker_release!r}; inconsistent snapshot"
            )
    elif marker_release is not None:
        raise AmbiguousStateError(
            f"snapshot release status is none but the recorded marker names "
            f"{marker_release!r}; inconsistent snapshot"
        )


# ---------------------------------------------------------------------------
# backend restoration
# ---------------------------------------------------------------------------


def _restore_service(ctx, ids: dict, snapshot: ProductionSnapshot, key: str) -> str:
    ecs_client = aws_context.client_for(ctx, "ecs")
    service_name = ids["services"][_SERVICE_KEYS.index(key)]
    observation = snapshot.services[key]
    snapshot_td = describe_task_definition(ecs_client, observation.taskDefinitionArn)[
        "taskDefinition"
    ]
    target_digest = _pinned_digest(
        snapshot_td, ids["ecrRepositories"][key], key, observation.taskDefinitionArn
    )
    if target_digest != observation.runningDigests[0]:
        raise AmbiguousStateError(
            f"snapshot service {key} task definition {observation.taskDefinitionArn} pins "
            f"{target_digest} but the snapshot records running digests "
            f"{observation.runningDigests}; inconsistent restore target"
        )
    observed = describe_services(ecs_client, ids["cluster"], [service_name])[service_name]
    if observed.get("taskDefinition") == observation.taskDefinitionArn:
        verify_running_digests(
            ecs_client,
            ids["cluster"],
            service_name,
            target_digest,
            timeout_seconds=DIGEST_VERIFY_TIMEOUT,
            interval_seconds=DIGEST_VERIFY_INTERVAL,
        )
        return (
            f"{service_name}: already at snapshot revision "
            f"{observation.taskDefinitionArn}; running digests verified"
        )
    revision_arn = register_task_definition(
        ecs_client, sanitize_task_definition(snapshot_td)
    )
    assert_full_arn_secrets(ecs_client, revision_arn)
    update_service(ecs_client, ids["cluster"], service_name, revision_arn)
    deployment_id = deployment_for_revision(
        ecs_client, ids["cluster"], service_name, revision_arn
    )
    wait_for_deployment(
        ecs_client,
        ids["cluster"],
        service_name,
        deployment_id,
        timeout_seconds=DEPLOYMENT_TIMEOUT,
    )
    verify_running_digests(
        ecs_client,
        ids["cluster"],
        service_name,
        target_digest,
        timeout_seconds=DIGEST_VERIFY_TIMEOUT,
        interval_seconds=DIGEST_VERIFY_INTERVAL,
    )
    return (
        f"{service_name}: restored from snapshot revision {observation.taskDefinitionArn} "
        f"(new revision {revision_arn}); running digests verified"
    )


def _pinned_digest(td: dict, repository: str, key: str, td_arn: str) -> str:
    images = task_definition_images(td)
    matches = [
        name
        for name, image in images.items()
        if image.rsplit("@", 1)[0].split(":", 1)[0].endswith(f"/{repository}")
    ]
    if len(matches) != 1:
        raise AmbiguousStateError(
            f"snapshot task definition {td_arn} for {key} must have exactly one container "
            f"for repository {repository}, found {matches}; inconsistent snapshot"
        )
    image = images[matches[0]]
    digest = image.rsplit("@", 1)[-1] if "@" in image else ""
    if not _IMAGE_DIGEST.fullmatch(digest):
        raise AmbiguousStateError(
            f"snapshot task definition {td_arn} for {key} is not digest-pinned "
            f"(image {image!r}); cannot restore exact bytes"
        )
    return digest


# ---------------------------------------------------------------------------
# frontend restoration
# ---------------------------------------------------------------------------


def _restore_frontend(ctx, ids: dict, snapshot: ProductionSnapshot) -> str:
    s3_client = aws_context.client_for(ctx, "s3")
    bucket = ids["frontendBucket"]
    marker_key = ids["frontendLiveMarker"]
    body = snapshot.frontend.immutableIdentity.encode()
    put_object(s3_client, bucket, marker_key, body)
    current = read_s3_text(s3_client, bucket, marker_key, "frontend live marker").strip()
    if current != snapshot.frontend.immutableIdentity:
        raise MutationVerificationError(
            "live marker read-back does not match the snapshot identity"
        )
    if get_object_sha256(s3_client, bucket, marker_key) != snapshot.frontend.checksum:
        raise MutationVerificationError(
            "live marker checksum read-back does not match the snapshot"
        )
    cloudfront_client = aws_context.client_for(ctx, "cloudfront")
    create_invalidation(cloudfront_client, ids["cloudfrontDistributionId"], ["/*"])
    return (
        f"live marker s3://{bucket}/{marker_key} restored to the snapshot identity; "
        "CloudFront invalidation issued"
    )


# ---------------------------------------------------------------------------
# dry-run / reporting
# ---------------------------------------------------------------------------


def _print_dry_run_plan(component: str, snapshot: ProductionSnapshot, ids: dict) -> None:
    if component == "frontend":
        print(
            "frontend: would restore the live marker "
            f"s3://{ids['frontendBucket']}/{ids['frontendLiveMarker']} to the snapshot "
            f"identity (checksum {snapshot.frontend.checksum}) and invalidate CloudFront /*"
        )
        return
    observation = snapshot.services[component]
    print(
        f"{component} ({ids['services'][_SERVICE_KEYS.index(component)]}): would re-register "
        f"the snapshot revision {observation.taskDefinitionArn}, update the service, and "
        f"verify running digests {observation.runningDigests}"
    )


def _print_summary(result: RecoveryResult) -> None:
    print(
        "recovery result "
        f"{result.recoveryId}: outcome {result.outcome}; "
        f"original failure: {result.originalFailure}"
    )
    for component in result.components:
        print(f"  {component.component}: {component.conclusion}")
