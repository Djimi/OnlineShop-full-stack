"""CloudTrail management-event coverage audit (Pass 3, subphase 3.5).

The audit planes for release evidence are GitHub Actions and CloudTrail. The
subphase 3.5 requirement is to prove CloudTrail management-event coverage for
ECS, ECR, S3, CloudFront, IAM, and Secrets Manager mutations, and to retain
sanitized AWS request IDs with the GitHub evidence so the two planes can be
correlated.

**What this audit can and cannot prove (honesty contract):**

- Management-event selectors do **not** enumerate per-service events: when
  ``IncludeManagementEvents`` is on, *every* AWS control-plane API event
  (including the ECS/ECR/S3/CloudFront/IAM/Secrets Manager mutations this
  release pipeline makes) is logged. ``covered_services`` below is therefore a
  derived consequence of management events being enabled — it is **not** a
  claim that CloudTrail enumerated those six services.
- CloudFront and IAM events are **global** (delivered from ``us-east-1``),
  which is why this audit also requires a multi-region trail — a single-region
  trail in ``eu-north-1`` would miss them.
- "Delivery" is proven two ways: a configured S3/CloudWatch Logs target *and* a
  ``LatestDeliveryTime`` in ``get-trail-status`` (evidence the trail has
  actually delivered). Delivery *error* state (``LatestDeliveryError``) is
  reported as an issue when present.
- **Request-ID correlation is deferred:** capturing and retaining sanitized
  AWS request IDs next to the GitHub evidence is a promotion-phase behaviour,
  not part of this read-only audit. This module only proves the trail exists,
  is logging management events multi-region, and delivers.

This module is the pure, fixture-tested decision layer over the JSON gathered
by ``scripts/verify-cloudtrail-coverage.sh`` (read-only ``cloudtrail
describe-trails`` / ``get-trail-status`` / ``get-event-selectors`` calls). It
never mutates anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

# The services whose management-plane mutations the release pipeline makes.
COVERED_SERVICES = ("ecs", "ecr", "s3", "cloudfront", "iam", "secretsmanager")


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


@dataclass
class CoverageOutcome:
    """Result of a CloudTrail management-event coverage audit."""

    valid: bool
    issues: list[dict[str, str]] = field(default_factory=list)
    covered_services: list[str] = field(default_factory=list)


def _trail_names(trails: Any) -> list[str]:
    if not isinstance(trails, list):
        return []
    names: list[str] = []
    for trail in trails:
        if isinstance(trail, dict) and trail.get("Name"):
            names.append(trail["Name"])
    return names


def verify(trails: Any, statuses: Any, selectors: Any) -> CoverageOutcome:
    """Audit management-event coverage.

    ``trails``: list of trail objects from ``describe-trails``.
    ``statuses``: mapping trail name -> status object from ``get-trail-status``.
    ``selectors``: mapping trail name -> event selector list from
    ``get-event-selectors`` (each selector has ``IncludeManagementEvents`` and
    ``ReadWriteType``).
    """
    issues: list[dict[str, str]] = []
    names = _trail_names(trails)
    if not names:
        return CoverageOutcome(
            False, [_issue("TRAIL_MISSING", "trail", "no CloudTrail trail exists")]
        )

    status_map = statuses if isinstance(statuses, dict) else {}
    selector_map = selectors if isinstance(selectors, dict) else {}

    logging_trail = None
    management_trail = None
    multi_region_trail = None
    delivery_trail = None

    trail_summary: dict[str, dict[str, Any]] = {}
    for trail in trails:
        if not isinstance(trail, dict) or not trail.get("Name"):
            continue
        name = trail["Name"]
        status = status_map.get(name, {}) if isinstance(status_map.get(name), dict) else {}
        entry: dict[str, Any] = {"IsLogging": status.get("IsLogging") is True}
        if status.get("LatestDeliveryTime"):
            entry["LatestDeliveryTime"] = status["LatestDeliveryTime"]
        # A delivery error (e.g. bucket policy denied the trail) is a gap even
        # when a target is configured.
        if status.get("LatestDeliveryError"):
            entry["LatestDeliveryError"] = status["LatestDeliveryError"]
        entry["IsMultiRegionTrail"] = trail.get("IsMultiRegionTrail") is True
        entry["S3BucketName"] = trail.get("S3BucketName")
        entry["CloudWatchLogsLogGroupArn"] = trail.get("CloudWatchLogsLogGroupArn")
        entry["HasManagementEvents"] = False
        for selector in (
            selector_map.get(name, []) if isinstance(selector_map.get(name), list) else []
        ):
            if isinstance(selector, dict) and selector.get("IncludeManagementEvents") is True:
                read_write = selector.get("ReadWriteType")
                if read_write in (None, "All", "WriteOnly"):
                    entry["HasManagementEvents"] = True
        trail_summary[name] = entry

        if entry["IsLogging"] and logging_trail is None:
            logging_trail = name
        if entry["HasManagementEvents"] and management_trail is None:
            management_trail = name
        if entry["IsMultiRegionTrail"] and multi_region_trail is None:
            multi_region_trail = name
        has_target = bool(entry["S3BucketName"] or entry["CloudWatchLogsLogGroupArn"])
        # A delivery target alone proves nothing; require evidence the trail has
        # actually delivered (LatestDeliveryTime) and no active delivery error.
        delivered = bool(entry.get("LatestDeliveryTime")) and not entry.get("LatestDeliveryError")
        if has_target and delivered and delivery_trail is None:
            delivery_trail = name

    if logging_trail is None:
        issues.append(
            _issue("NOT_LOGGING", "trailStatus.IsLogging", "no trail is currently logging")
        )
    if management_trail is None:
        issues.append(
            _issue(
                "MANAGEMENT_EVENTS_DISABLED",
                "eventSelectors.IncludeManagementEvents",
                "no trail logs management events "
                "(management events cover the ECS/ECR/S3/CloudFront/IAM/"
                "Secrets Manager control-plane mutations)",
            )
        )
    if multi_region_trail is None:
        issues.append(
            _issue(
                "NOT_MULTI_REGION",
                "trail.IsMultiRegionTrail",
                "no multi-region trail; global IAM/CloudFront management events would be missed",
            )
        )
    if delivery_trail is None:
        issues.append(
            _issue(
                "NO_DELIVERY_TARGET",
                "trail",
                "no trail delivers to an S3 bucket or CloudWatch Logs group with a "
                "confirmed LatestDeliveryTime and no delivery error",
            )
        )

    covered_services = list(COVERED_SERVICES) if not issues else []

    return CoverageOutcome(not issues, issues, covered_services)


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _cmd_verify(args: argparse.Namespace) -> int:
    trails = _read_json(args.trails)
    statuses = _read_json(args.statuses) if args.statuses else {}
    selectors = _read_json(args.selectors) if args.selectors else {}
    outcome = verify(trails, statuses, selectors)
    _print_json(
        {
            "valid": outcome.valid,
            "issues": outcome.issues,
            "coveredServices": outcome.covered_services,
        }
    )
    return 0 if outcome.valid else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.cloudtrail",
        description="CloudTrail management-event coverage audit for the release evidence plane.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    verify_cmd = sub.add_parser("verify", help="audit management-event coverage")
    verify_cmd.add_argument(
        "--trails", required=True, metavar="FILE", help="describe-trails JSON file (list)"
    )
    verify_cmd.add_argument("--statuses", metavar="FILE", help="get-trail-status mapping JSON file")
    verify_cmd.add_argument(
        "--selectors", metavar="FILE", help="get-event-selectors mapping JSON file"
    )
    verify_cmd.set_defaults(func=_cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
