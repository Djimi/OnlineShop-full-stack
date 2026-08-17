"""verify production: read-only CT-PROD-01..04 verification (OP-DEP-04).

Verifies the observed production state against an expected identity set —
an official release manifest (post-finalization, rollback, recovery), a
candidate manifest (post-deployment, pre-finalization), or a pre-mutation
production snapshot (post-compensation, OP-REC-02):

- every backend service: PRIMARY deployment health plus observed running-task
  digests equal to the expected digest (never task-definition text alone);
- frontend: live marker content equality, S3 full-object checksum, and the
  public CloudFront-visible marker/index identity;
- read-only journeys: gateway health, read-only GET /items through the
  gateway, and the frontend marker/index observed through CloudFront
  (CT-PROD-03).

No business-data mutation exists anywhere in this path. Any mismatch or read
error writes the failed report first and then fails the command.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from uuid import uuid4

from .. import live_marker
from ..aws import context as aws_context
from ..aws import (
    describe_load_balancer,
    describe_services,
    get_distribution,
    get_object_sha256,
    primary_deployment,
    running_digests,
)
from ..aws.waiters import bounded_waiter
from ..errors import AmbiguousStateError, ReadError, ValidationError
from ..models import (
    CandidateManifest,
    ReleaseManifest,
    VerificationJourney,
    VerificationReport,
)
from ..records import (
    load_candidate,
    load_release_manifest,
    load_snapshot,
    read_s3_text,
    write_json,
)
from ..serialization import sha256_hex
from ..serving import _fetch

_SERVICE_KEYS = ("auth", "items", "gateway")
_PUBLIC_MARKER_TIMEOUT = 240
_FRONTEND_INDEX_TIMEOUT = 60


class _Expected:
    def __init__(self, digests: dict[str, str], marker: live_marker.LiveMarker):
        self.digests = digests
        self.marker = marker


def production(args: argparse.Namespace) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    ids = args.identifiers_data
    expected = _load_expected(args)
    report_id = f"vrf-{uuid4().hex[:16]}"
    services: dict[str, dict] = {}
    journeys: list[VerificationJourney] = []
    failures: list[str] = []

    ecs_client = aws_context.client_for(ctx, "ecs")
    for key, name in zip(_SERVICE_KEYS, ids["services"], strict=True):
        observed = describe_services(ecs_client, ids["cluster"], [name])[name]
        primary = primary_deployment(observed, name)
        health = primary.get("rolloutState") or "UNKNOWN"
        digests = running_digests(ecs_client, ids["cluster"], name)
        match = digests == [expected.digests[key]]
        services[key] = {
            "service": name,
            "deploymentId": primary.get("id"),
            "taskDefinitionArn": observed.get("taskDefinition"),
            "health": health,
            "expectedDigest": expected.digests[key],
            "runningDigests": digests,
            "match": match,
        }
        if not match:
            failures.append(
                f"service {name} running digests {digests} != expected "
                f"[{expected.digests[key]}]"
            )
        # CT-PROD-01: a FAILED rollout can never be verified production.
        # COMPLETED is the healthy state; IN_PROGRESS and the UNKNOWN
        # fallback are recorded honestly but treated as non-fatal here
        # because the running-digest identity check still applies.
        if health == "FAILED":
            failures.append(
                f"service {name} PRIMARY deployment rolloutState is FAILED"
            )

    frontend, frontend_failures = _verify_frontend(ctx, ids, expected)
    failures.extend(frontend_failures)

    base_url = _resolve_base_url(ctx, ids)
    journeys.append(_journey_backend_health(base_url))
    journeys.append(_journey_items_api(base_url))
    cloudfront_url = _cloudfront_url(ctx, ids["cloudfrontDistributionId"])
    journeys.append(_journey_public_marker(cloudfront_url, expected.marker))
    journeys.append(_journey_frontend_index(cloudfront_url))
    for journey in journeys:
        if journey.conclusion != "passed":
            failures.append(f"journey {journey.name}: {journey.detail or journey.conclusion}")

    conclusion = "passed" if not failures else "failed"
    report = VerificationReport(
        reportId=report_id,
        producedAt=datetime.now(UTC),
        environment="production",
        services=services,
        frontend=frontend,
        journeys=journeys,
        conclusion=conclusion,
    )
    write_json(args.out, report)
    print(f"verification report {report_id} written to {args.out}: {conclusion}")
    if failures:
        raise ValidationError(
            "production verification failed: " + "; ".join(failures[:8])
        )
    return 0


def _load_expected(args: argparse.Namespace) -> _Expected:
    if args.manifest is not None and args.candidate is None:
        release: ReleaseManifest = load_release_manifest(args.manifest)
        marker = live_marker.build_official_marker(
            live_marker.build_candidate_marker(
                candidate_id=release.candidateId,
                source_sha=release.source.fullSha,
                frontend_sha256=release.artifacts.frontend.checksum,
            ),
            release.releaseId,
        )
        return _Expected(
            digests={
                "auth": release.artifacts.auth.digest,
                "items": release.artifacts.items.digest,
                "gateway": release.artifacts.gateway.digest,
            },
            marker=marker,
        )
    if args.candidate is not None and args.manifest is None:
        candidate: CandidateManifest = load_candidate(args.candidate)
        marker = live_marker.build_candidate_marker(
            candidate_id=candidate.candidateId,
            source_sha=candidate.source.fullSha,
            frontend_sha256=candidate.artifacts.frontend.contentChecksum,
        )
        return _Expected(
            digests={
                "auth": candidate.artifacts.auth.digest,
                "items": candidate.artifacts.items.digest,
                "gateway": candidate.artifacts.gateway.digest,
            },
            marker=marker,
        )
    if (
        args.snapshot is not None
        and args.manifest is None
        and args.candidate is None
    ):
        return _expected_from_snapshot(args)
    raise ValidationError(
        "exactly one of --manifest, --candidate, or --snapshot is required"
    )


def _expected_from_snapshot(args: argparse.Namespace) -> _Expected:
    """Expected identity from the pre-mutation snapshot (post-compensation).

    Digests come from the snapshot's recorded running digests (exactly one
    per service — an ambiguous snapshot fails closed) and the marker is the
    snapshot's recorded frontend identity, whose recorded checksum must
    match the marker bytes.
    """
    snapshot = load_snapshot(args.snapshot, require_environment="production")
    digests: dict[str, str] = {}
    for key in _SERVICE_KEYS:
        if key not in snapshot.services:
            raise AmbiguousStateError(
                f"snapshot has no observation for service {key}; cannot verify "
                "production against an incomplete snapshot"
            )
        running = snapshot.services[key].runningDigests
        if len(running) != 1:
            raise AmbiguousStateError(
                f"snapshot service {key} records {len(running)} running digests; "
                "cannot verify production against an ambiguous snapshot"
            )
        digests[key] = running[0]
    identity = snapshot.frontend.immutableIdentity
    marker = live_marker.parse_live_marker(identity)
    if marker is None:
        raise AmbiguousStateError(
            "snapshot frontend immutableIdentity is not a live marker document; "
            "cannot verify production against an inconsistent snapshot"
        )
    if sha256_hex(identity.encode()) != snapshot.frontend.checksum:
        raise AmbiguousStateError(
            "snapshot frontend identity checksum does not match the recorded marker "
            "bytes; the snapshot is inconsistent"
        )
    return _Expected(digests=digests, marker=marker)


def _verify_frontend(ctx, ids: dict, expected: _Expected) -> tuple[dict, list[str]]:
    s3_client = aws_context.client_for(ctx, "s3")
    bucket = ids["frontendBucket"]
    marker_key = ids["frontendLiveMarker"]
    raw = read_s3_text(s3_client, bucket, marker_key, "frontend live marker").strip()
    checksum = get_object_sha256(s3_client, bucket, marker_key)
    parsed = live_marker.parse_live_marker(raw)
    failures: list[str] = []
    expected_doc = live_marker.marker_document(expected.marker)
    if raw != expected_doc:
        failures.append(
            f"live marker content mismatch: expected {expected.marker.releaseId or 'candidate'} "
            f"marker for {expected.marker.candidateId}, got {raw[:120]!r}"
        )
    if checksum != sha256_hex(expected_doc.encode()):
        failures.append(
            "live marker object checksum does not match the expected canonical marker"
        )
    if parsed is not None and not live_marker.markers_identity_equivalent(parsed, expected.marker):
        failures.append("live marker identity differs from the expected candidate identity")
    return (
        {
            "liveMarkerKey": marker_key,
            "liveMarkerChecksum": checksum,
            "cloudfrontDistributionId": ids["cloudfrontDistributionId"],
        },
        failures,
    )


def _resolve_base_url(ctx, ids: dict) -> str:
    override = ids.get("gatewayBaseUrl")
    if override:
        if not override.startswith(("http://", "https://")):
            raise ValidationError(f"gatewayBaseUrl must be http(s), got {override!r}")
        return override
    alb_name = ids.get("albName")
    if not alb_name:
        raise ValidationError(
            "production identifiers carry neither gatewayBaseUrl nor albName; "
            "the read-only journeys cannot be resolved"
        )
    elb_client = aws_context.client_for(ctx, "elbv2")
    dns_name = describe_load_balancer(elb_client, alb_name).get("DNSName")
    if not isinstance(dns_name, str) or not dns_name:
        raise ReadError(f"load balancer {alb_name} has no DNSName")
    return f"http://{dns_name}"


def _cloudfront_url(ctx, distribution_id: str) -> str:
    client = aws_context.client_for(ctx, "cloudfront")
    observed = get_distribution(client, distribution_id)
    domain = (observed.get("Distribution") or {}).get("DomainName")
    if not isinstance(domain, str) or not domain:
        raise ReadError(f"distribution {distribution_id} has no DomainName")
    return f"https://{domain}"


def _journey_backend_health(base_url: str) -> VerificationJourney:
    status, headers, _body = _fetch(f"{base_url.rstrip('/')}/actuator/health")
    detail = f"HTTP {status}, content-type {headers.get('Content-Type', '')}"
    return VerificationJourney(
        name="gateway-health",
        conclusion="passed" if status == 200 else "failed",
        detail=detail,
    )


def _journey_items_api(base_url: str) -> VerificationJourney:
    status, headers, _body = _fetch(f"{base_url.rstrip('/')}/items")
    detail = f"HTTP {status}, content-type {headers.get('Content-Type', '')}"
    passed = status in (200, 401, 403) and "application/json" in headers.get(
        "Content-Type", ""
    )
    return VerificationJourney(
        name="items-api",
        conclusion="passed" if passed else "failed",
        detail=detail,
    )


def _journey_public_marker(
    cloudfront_url: str, expected: live_marker.LiveMarker
) -> VerificationJourney:
    expected_doc = live_marker.marker_document(expected)
    last_status = ""

    def poll() -> bool:
        nonlocal last_status
        status, _headers, body = _fetch(f"{cloudfront_url}/release.json")
        last_status = f"HTTP {status}"
        return status == 200 and body.decode("utf-8", errors="replace").strip() == expected_doc

    try:
        reached = bounded_waiter(
            poll,
            label=f"public marker at {cloudfront_url}/release.json",
            timeout_seconds=_PUBLIC_MARKER_TIMEOUT,
            interval_seconds=15,
        )
    except Exception as error:  # bounded waiter timeout or read error
        return VerificationJourney(
            name="frontend-marker-public",
            conclusion="failed",
            detail=f"{last_status}; {error}",
        )
    if reached:
        return VerificationJourney(
            name="frontend-marker-public", conclusion="passed", detail=last_status
        )
    return VerificationJourney(
        name="frontend-marker-public", conclusion="failed", detail=last_status
    )


def _journey_frontend_index(cloudfront_url: str) -> VerificationJourney:
    def poll() -> bool:
        status, headers, body = _fetch(f"{cloudfront_url}/")
        content_type = headers.get("Content-Type", "")
        return (
            status == 200
            and "text/html" in content_type
            and 'id="root"' in body.decode("utf-8", errors="replace")
        )

    try:
        reached = bounded_waiter(
            poll,
            label=f"public frontend index at {cloudfront_url}/",
            timeout_seconds=_FRONTEND_INDEX_TIMEOUT,
            interval_seconds=15,
        )
    except Exception as error:
        return VerificationJourney(
            name="frontend-index-public", conclusion="failed", detail=str(error)
        )
    if reached:
        return VerificationJourney(
            name="frontend-index-public", conclusion="passed", detail="HTTP 200, mount present"
        )
    return VerificationJourney(
        name="frontend-index-public", conclusion="failed", detail="index not verified publicly"
    )
