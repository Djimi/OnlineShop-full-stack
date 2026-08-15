"""snapshot production: capture the read-only production state before mutation.

Identifiers JSON shape (--identifiers):
{
  "environment": "production",
  "accountId": "799111666795",
  "cluster": "onlineshop-cluster",
  "services": ["onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"],
  "ecrRepositories": {"auth": "onlineshop-auth", "items": "onlineshop-items",
                      "gateway": "onlineshop-api-gateway"},
  "dbInstance": "onlineshop-postgres-db",
  "frontendBucket": "onlineshop-frontend-799111666795",
  "frontendLiveMarker": "release.json",
  "frontendReleasesPrefix": "_releases/v",
  "cloudfrontDistributionId": "EPS8MI3FV3B7X"
}
All values are non-secret identifiers. "services" lists exactly three ECS
service names in order auth, items, api-gateway. The raw identifiers dict is
also stashed on args.identifiers_data by the CLI context builder because
AwsContext.identifiers is strictly dict[str, str] and cannot carry the
services list or the ecrRepositories map.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from botocore.exceptions import ClientError

from ..aws import context as aws_context
from ..aws import (
    describe_db_instance,
    describe_services,
    get_object_sha256,
    primary_deployment,
    running_digests,
)
from ..aws.readback import absent_or_read
from ..errors import AbsentResourceError, ReadError, ValidationError
from ..models import FrontendObservation, ProductionSnapshot, ReleaseIdentity, ServiceObservation
from ..serialization import canonical_json, sha256_hex
from ..validation import validate as validate_record

_RELEASE_ID = re.compile(r"^release-\d{4}$")
_SERVICE_KEYS = ("auth", "items", "gateway")


def snapshot_production(args: argparse.Namespace) -> int:
    """Capture the production state and write the canonical snapshot record."""
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    ids = args.identifiers_data
    service_names = ids["services"]
    if len(service_names) != len(_SERVICE_KEYS):
        raise ValidationError(
            "identifiers services must contain exactly 3 names in order auth, items, api-gateway"
        )
    cluster = ids["cluster"]
    ecs_client = aws_context.client_for(ctx, "ecs")
    services: dict[str, ServiceObservation] = {}
    for key, name in zip(_SERVICE_KEYS, service_names, strict=True):
        observed = describe_services(ecs_client, cluster, [name])[name]
        task_definition_arn = observed.get("taskDefinition")
        if not task_definition_arn:
            raise ReadError(f"service {name} has no taskDefinition")
        primary = primary_deployment(observed, name)
        deployment_id = primary.get("id")
        if not deployment_id:
            raise ReadError(f"PRIMARY deployment of service {name} has no id")
        services[key] = ServiceObservation(
            deploymentId=deployment_id,
            taskDefinitionArn=task_definition_arn,
            runningDigests=running_digests(ecs_client, cluster, name),
            health=primary.get("rolloutState") or "UNKNOWN",
        )
    s3_client = aws_context.client_for(ctx, "s3")
    bucket = ids["frontendBucket"]
    marker_key = ids["frontendLiveMarker"]
    marker = _read_marker(s3_client, bucket, marker_key)
    frontend = FrontendObservation(
        immutableIdentity=marker,
        liveMarker=marker_key,
        checksum=get_object_sha256(s3_client, bucket, marker_key),
        cloudfrontDistributionId=ids["cloudfrontDistributionId"],
    )
    rds_client = aws_context.client_for(ctx, "rds")
    db = describe_db_instance(rds_client, ids["dbInstance"])
    fingerprint = sha256_hex(
        canonical_json(
            {
                "taskDefinitionArns": sorted(
                    service.taskDefinitionArn for service in services.values()
                ),
                "db": {
                    "engine": db["Engine"],
                    "engineVersion": db["EngineVersion"],
                    "dbInstanceClass": db["DBInstanceClass"],
                },
            }
        ).encode()
    )
    release_id = marker if _RELEASE_ID.fullmatch(marker) else None
    snapshot = ProductionSnapshot(
        snapshotId=f"snap-{uuid4().hex[:16]}",
        environment=ctx.environment,
        capturedAt=datetime.now(UTC),
        release=ReleaseIdentity(
            status="official" if release_id is not None else "none",
            releaseId=release_id,
        ),
        services=services,
        frontend=frontend,
        compatibilityFingerprint=fingerprint,
    )
    errors = validate_record(snapshot)
    if errors:
        raise ValidationError(
            f"captured production snapshot failed internal validation: {'; '.join(errors)}"
        )
    _write_out(args.out, snapshot)
    return 0


def _read_marker(s3_client, bucket: str, key: str) -> str:
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key).get("Body")
    except ClientError as error:
        if absent_or_read(error):
            raise AbsentResourceError(
                f"frontend live marker s3://{bucket}/{key} not found"
            ) from error
        raise ReadError(f"get_object failed for s3://{bucket}/{key}") from error
    if body is None:
        raise ReadError(f"get_object returned no body for s3://{bucket}/{key}")
    return body.read().decode("utf-8").strip()


def _write_out(path: str, snapshot: ProductionSnapshot) -> None:
    try:
        Path(path).write_text(canonical_json(snapshot.model_dump(mode="json")) + "\n")
    except OSError as error:
        raise ReadError(f"cannot write snapshot to {path}: {error}") from error
