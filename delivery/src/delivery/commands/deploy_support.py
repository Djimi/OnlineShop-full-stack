"""Shared deployment helpers for the backward restores (OP-REC-02/04).

Two groups of helpers, both single implementations reused by the forward and
backward commands instead of duplicating them:

- ECS (extracted from ``deploy`` in Phase 6, reused by ``recover``):
  digest-pinned registration verification, secret-safety read-back,
  deployment visibility, and bounded running-digest verification.
- frontend live-root restoration from a retained immutable prefix (extracted
  from ``rollback`` in Phase 6, reused by ``recover``): dist-file listing,
  aggregate content checksum proof BEFORE the live switch, copy with
  index.html last, official-marker write and read-back (OP-REC-04).
  Callers own the CloudFront invalidation.

``deploy`` and ``rollback`` keep thin module-level wrappers with their
historical names so their callers and tests stay unchanged.
"""

from __future__ import annotations

import re
import time

from botocore.exceptions import ClientError

from ..aws import (
    describe_services,
    describe_task_definition,
    get_object_sha256,
    list_objects,
    put_object,
)
from ..aws.ecs import wait_for_running_digests
from ..errors import MutationVerificationError, ReadError, ValidationError
from ..records import read_s3_text
from ..serialization import sha256_hex

_FRONTEND_BUNDLE = "frontend.tar.gz"
_PREFIX_MARKER = "release.json"

DEPLOYMENT_TIMEOUT = 600
DIGEST_VERIFY_TIMEOUT = 120.0
DIGEST_VERIFY_INTERVAL = 10.0
DEPLOYMENT_VISIBILITY_RETRIES = 3
DEPLOYMENT_VISIBILITY_DELAY = 1.0

_FULL_SECRET_ARN = re.compile(
    r"^arn:aws:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@:-]+$"
)


def assert_full_arn_secrets(ecs_client, revision_arn: str) -> None:
    """Secrets must stay full-ARN ``secrets[].valueFrom`` references (never plaintext)."""
    observed = describe_task_definition(ecs_client, revision_arn)["taskDefinition"]
    for container in observed.get("containerDefinitions") or []:
        for secret in container.get("secrets") or []:
            value_from = secret.get("valueFrom")
            if not isinstance(value_from, str) or not _FULL_SECRET_ARN.fullmatch(value_from):
                raise MutationVerificationError(
                    f"task definition {revision_arn} container {container.get('name')} "
                    f"carries a non-full-ARN secrets[].valueFrom: {value_from!r}"
                )


def deployment_for_revision(
    ecs_client,
    cluster: str,
    service: str,
    revision_arn: str,
    *,
    retries: int = DEPLOYMENT_VISIBILITY_RETRIES,
    delay_seconds: float = DEPLOYMENT_VISIBILITY_DELAY,
) -> str:
    """Return the deployment id for a just-registered revision (bounded retries)."""
    for attempt in range(1, retries + 1):
        observed = describe_services(ecs_client, cluster, [service])[service]
        for deployment in observed.get("deployments") or []:
            if deployment.get("taskDefinition") == revision_arn:
                deployment_id = deployment.get("id")
                if not deployment_id:
                    raise ReadError(f"deployment for {revision_arn} of {service} has no id")
                return deployment_id
        if attempt < retries:
            time.sleep(delay_seconds)
    raise ReadError(
        f"service {service} has no deployment for the just-registered revision "
        f"{revision_arn} after {retries} attempts; deployment visibility did not converge"
    )


def verify_running_digests(
    ecs_client,
    cluster: str,
    service: str,
    expected: str,
    *,
    timeout_seconds: float = DIGEST_VERIFY_TIMEOUT,
    interval_seconds: float = DIGEST_VERIFY_INTERVAL,
) -> None:
    """Wait until every running container digest equals ``expected`` (bounded)."""
    wait_for_running_digests(
        ecs_client,
        cluster,
        service,
        expected,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


# ---------------------------------------------------------------------------
# frontend live-root restoration from a retained immutable prefix
# ---------------------------------------------------------------------------


def restore_frontend_from_retained_prefix(
    s3_client,
    *,
    bucket: str,
    prefix: str,
    expected_checksum: str,
    live_marker_key: str,
    marker_doc: str,
    release_label: str,
) -> None:
    """Restore the live root from a retained immutable prefix (OP-REC-04).

    The aggregate content checksum of the retained dist files is proven
    against ``expected_checksum`` BEFORE the live entry point is touched,
    files are copied with index.html last, and the live marker is written
    and read back (never reporting completion for a hybrid frontend).
    """
    files = _retained_dist_files(s3_client, bucket, prefix, release_label)
    observed_checksum = _aggregate_checksum(s3_client, bucket, prefix, files)
    if observed_checksum != expected_checksum:
        raise ValidationError(
            f"retained frontend prefix {prefix} content checksum {observed_checksum} "
            f"does not match the recorded checksum {expected_checksum} for "
            f"{release_label}; the live entry point was NOT switched"
        )
    ordered = sorted(files, key=lambda rel: (rel == "index.html", rel))
    for rel in ordered:
        body = _read_object_bytes(s3_client, bucket, f"{prefix}{rel}", release_label)
        put_object(s3_client, bucket, rel, body)
    put_object(s3_client, bucket, live_marker_key, marker_doc.encode())
    current = read_s3_text(
        s3_client, bucket, live_marker_key, "frontend live marker"
    ).strip()
    if current != marker_doc:
        raise MutationVerificationError(
            "live marker read-back does not match the restored identity"
        )


def _retained_dist_files(
    s3_client, bucket: str, prefix: str, release_label: str
) -> list[str]:
    """List the retained dist files (bundle and prefix marker excluded)."""
    entries = list_objects(s3_client, bucket, prefix)
    files: list[str] = []
    allowed_extras = {f"{prefix}{_FRONTEND_BUNDLE}", f"{prefix}{_PREFIX_MARKER}"}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReadError(f"prefix listing for {release_label} has a malformed entry")
        key = entry.get("Key")
        if not isinstance(key, str) or not key or not key.startswith(prefix):
            raise ReadError(f"prefix listing for {release_label} has an unsafe key")
        if key in allowed_extras:
            continue
        rel = key[len(prefix) :]
        if not rel or rel in (_FRONTEND_BUNDLE, _PREFIX_MARKER) or ".." in rel.split("/"):
            raise ReadError(f"unexpected retained object {key!r} for {release_label}")
        files.append(rel)
    if "index.html" not in files:
        raise ValidationError(
            f"retained frontend prefix {prefix} for {release_label} has no index.html"
        )
    return files


def _aggregate_checksum(s3_client, bucket: str, prefix: str, files: list[str]) -> str:
    lines = []
    for rel in sorted(files):
        observed = get_object_sha256(s3_client, bucket, f"{prefix}{rel}")
        lines.append(f"{observed}\n")
    return sha256_hex("".join(lines).encode())


def _read_object_bytes(s3_client, bucket: str, key: str, release_label: str) -> bytes:
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key).get("Body")
    except ClientError as error:
        raise ReadError(
            f"cannot read retained object s3://{bucket}/{key} for {release_label}"
        ) from error
    if body is None:
        raise ReadError(
            f"retained object s3://{bucket}/{key} for {release_label} has no body"
        )
    return body.read()
