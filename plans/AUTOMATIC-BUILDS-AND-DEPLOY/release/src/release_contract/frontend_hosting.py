"""S3 REST origin + CloudFront Origin Access Control hardening rules
(Pass 3, subphase 3.5).

The v1 frontend is served through CloudFront with an S3 **website** origin and
a public-read bucket policy. Subphase 3.5 replaces that model with an S3
**REST** origin protected by an Origin Access Control (OAC), blocks direct
public bucket access, and preserves the SPA fallback through CloudFront's
custom error response — so ``/login`` still resolves to ``/index.html`` while
the bucket itself is private.

This module is the pure, fixture-tested decision layer:

- :func:`verify` checks a distribution config + bucket policy + public access
  block + website config against the hardened desired state and fails closed
  on any drift (website origin, missing OAC, public-read policy, open public
  access, missing SPA fallback).
- :func:`migration_plan` returns the ordered, non-secret mutation list the
  ``scripts/migrate-frontend-oac.sh`` operator tool executes. The mutation
  tool is implemented and tested against stubs but is **not applied live** in
  subphase 3.5; application happens in the consolidated verification pass after
  a fail-closed ``verify`` gate.

Note on the CloudFront endpoint: CloudFront is a global service with a single
global endpoint (``cloudfront.amazonaws.com``, signing region ``us-east-1``),
so every CloudFront CLI command in this project keeps the mandatory
``--profile dpm-profile --region eu-north-1`` flags and still reaches the
global control plane.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

# Project frontend identity (non-secret; mirrors scripts/config/production.env
# and plans/AUTOMATIC-BUILDS-AND-DEPLOY/executed/INFO.md).
FRONTEND_BUCKET = "onlineshop-frontend-799111666795"
FRONTEND_DISTRIBUTION_ID = "EPS8MI3FV3B7X"
OAC_NAME = "onlineshop-frontend-oac"
DISTRIBUTION_ARN = f"arn:aws:cloudfront::799111666795:distribution/{FRONTEND_DISTRIBUTION_ID}"

# S3 REST origin endpoints are the regional REST endpoints; website endpoints
# always contain `.s3-website.` and must never be used as an origin.
_WEBSITE_ORIGIN_RE = re.compile(r"\.s3-website\.")
_REST_ORIGIN_RE = re.compile(r"^[a-z0-9.-]+\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com$")

SPA_FALLBACK_ERROR_CODE = 404
SPA_FALLBACK_PAGE = "/index.html"
SPA_FALLBACK_RESPONSE = 200

PUBLIC_ACCESS_KEYS = (
    "BlockPublicAcls",
    "IgnorePublicAcls",
    "BlockPublicPolicy",
    "RestrictPublicBuckets",
)


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


@dataclass
class VerificationOutcome:
    """Result of verifying the frontend hosting model."""

    valid: bool
    issues: list[dict[str, str]] = field(default_factory=list)


def is_rest_origin(domain: Any) -> bool:
    return (
        isinstance(domain, str)
        and bool(_REST_ORIGIN_RE.match(domain))
        and not _WEBSITE_ORIGIN_RE.search(domain)
    )


def is_website_origin(domain: Any) -> bool:
    return isinstance(domain, str) and bool(_WEBSITE_ORIGIN_RE.search(domain))


def _s3_origins(distribution_config: Any) -> list[dict[str, Any]]:
    """Return only the S3 frontend origins (the ALB/API origin is excluded).

    An origin is an S3 origin when its Id is ``s3-frontend`` or its domain is
    an S3 REST/website endpoint (contains the frontend bucket).
    """
    origins = (
        distribution_config.get("Origins", {}) if isinstance(distribution_config, dict) else {}
    )
    items = origins.get("Items", []) if isinstance(origins, dict) else []
    return [
        o
        for o in items
        if isinstance(o, dict)
        and (
            o.get("Id") == "s3-frontend"
            or FRONTEND_BUCKET in str(o.get("DomainName", ""))
            or is_website_origin(o.get("DomainName"))
            or is_rest_origin(o.get("DomainName"))
        )
    ]


def _has_oac_policy(bucket_policy: Any) -> bool:
    if not isinstance(bucket_policy, dict):
        return False
    statements = bucket_policy.get("Statement")
    if not isinstance(statements, list):
        return False
    for statement in statements:
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue
        principal = statement.get("Principal")
        service = None
        if isinstance(principal, dict):
            service = principal.get("Service")
        if isinstance(service, str) and service == "cloudfront.amazonaws.com":
            action = statement.get("Action")
            actions = (
                {action}
                if isinstance(action, str)
                else set(action)
                if isinstance(action, list)
                else set()
            )
            condition = statement.get("Condition") or {}
            source_arn = None
            for _op, values in condition.items():
                if isinstance(values, dict) and "aws:SourceArn" in values:
                    source_arn = values["aws:SourceArn"]
            if "s3:GetObject" in actions and source_arn == DISTRIBUTION_ARN:
                return True
    return False


def _has_public_read_policy(bucket_policy: Any) -> bool:
    if not isinstance(bucket_policy, dict):
        return False
    statements = bucket_policy.get("Statement")
    if not isinstance(statements, list):
        return False
    for statement in statements:
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue
        principal = statement.get("Principal")
        if principal == "*" or (isinstance(principal, dict) and principal.get("AWS") == "*"):
            return True
    return False


def _has_spa_fallback(distribution_config: Any) -> bool:
    errors = (
        distribution_config.get("CustomErrorResponses", {})
        if isinstance(distribution_config, dict)
        else {}
    )
    items = errors.get("Items", []) if isinstance(errors, dict) else []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("ErrorCode") == SPA_FALLBACK_ERROR_CODE
            and entry.get("ResponsePagePath") == SPA_FALLBACK_PAGE
            and entry.get("ResponseCode") == SPA_FALLBACK_RESPONSE
        ):
            return True
    return False


def verify(
    distribution_config: Any,
    bucket_policy: Any,
    public_access_block: Any,
    website_config: Any,
) -> VerificationOutcome:
    """Fail-closed verification of the hardened S3 REST + OAC frontend model.

    ``bucket_policy`` / ``public_access_block`` / ``website_config`` are the
    decoded JSON from ``s3api get-bucket-policy`` / ``get-public-access-block``
    / ``get-bucket-website``, or ``None`` when absent/unconfigured.
    """
    issues: list[dict[str, str]] = []

    if not isinstance(distribution_config, dict):
        issues.append(
            _issue(
                "MISSING_DISTRIBUTION",
                "DistributionConfig",
                "no CloudFront distribution config was provided",
            )
        )
        return VerificationOutcome(False, issues)

    s3_origins = _s3_origins(distribution_config)
    if not s3_origins:
        issues.append(
            _issue(
                "S3_ORIGIN_MISSING",
                "DistributionConfig.Origins",
                "no S3 origin found in the distribution config",
            )
        )
    for origin in s3_origins:
        domain = origin.get("DomainName")
        if is_website_origin(domain):
            issues.append(
                _issue(
                    "WEBSITE_ORIGIN",
                    "DistributionConfig.Origins[].DomainName",
                    f"S3 origin uses the website endpoint {domain!r}; a REST endpoint "
                    "with Origin Access Control is required",
                )
            )
        elif not is_rest_origin(domain):
            issues.append(
                _issue(
                    "UNKNOWN_ORIGIN",
                    "DistributionConfig.Origins[].DomainName",
                    f"origin domain {domain!r} is neither a recognized "
                    "S3 REST nor website endpoint",
                )
            )
        if not origin.get("OriginAccessControlId"):
            issues.append(
                _issue(
                    "OAC_MISSING",
                    "DistributionConfig.Origins[].OriginAccessControlId",
                    f"S3 origin {origin.get('Id')!r} has no Origin Access Control",
                )
            )

    if bucket_policy is None:
        issues.append(
            _issue("BUCKET_POLICY_MISSING", "s3:GetBucketPolicy", "no bucket policy is configured")
        )
        issues.append(
            _issue(
                "OAC_POLICY_MISSING",
                "Policy.Statement",
                "bucket policy must allow cloudfront.amazonaws.com s3:GetObject "
                f"with aws:SourceArn={DISTRIBUTION_ARN}",
            )
        )
    else:
        if _has_public_read_policy(bucket_policy):
            issues.append(
                _issue(
                    "PUBLIC_READ_POLICY",
                    "Policy.Statement",
                    "bucket policy still grants public (Principal '*') read access",
                )
            )
        if not _has_oac_policy(bucket_policy):
            issues.append(
                _issue(
                    "OAC_POLICY_MISSING",
                    "Policy.Statement",
                    "bucket policy must allow cloudfront.amazonaws.com s3:GetObject "
                    f"with aws:SourceArn={DISTRIBUTION_ARN}",
                )
            )

    if public_access_block is None:
        issues.append(
            _issue(
                "PUBLIC_ACCESS_BLOCK_MISSING",
                "s3:GetPublicAccessBlock",
                "bucket public access block is not configured",
            )
        )
    else:
        config = (
            public_access_block.get("PublicAccessBlockConfiguration", {})
            if isinstance(public_access_block, dict)
            else {}
        )
        for key in PUBLIC_ACCESS_KEYS:
            if config.get(key) is not True:
                issues.append(
                    _issue(
                        "PUBLIC_ACCESS_OPEN",
                        f"PublicAccessBlockConfiguration.{key}",
                        f"{key} must be true to block direct public bucket access",
                    )
                )

    if website_config is not None:
        issues.append(
            _issue(
                "WEBSITE_ENABLED",
                "s3:GetBucketWebsite",
                "bucket website configuration must be removed (REST origin + OAC replaces it)",
            )
        )

    if not _has_spa_fallback(distribution_config):
        issues.append(
            _issue(
                "SPA_FALLBACK_MISSING",
                "DistributionConfig.CustomErrorResponses",
                f"custom error response for {SPA_FALLBACK_ERROR_CODE} -> "
                f"{SPA_FALLBACK_RESPONSE} {SPA_FALLBACK_PAGE} is required to preserve SPA routing",
            )
        )

    return VerificationOutcome(not issues, issues)


def migration_preconditions(bucket_policy: Any) -> VerificationOutcome:
    """Fail closed *before any mutation* when the current bucket policy would
    create a lockout window.

    The origin switch to an S3 REST endpoint signed by the OAC must succeed
    under the policy that is already in place, otherwise CloudFront loses the
    ability to fetch between the distribution update and the policy replace.
    A safe starting policy either grants anonymous public read (the v1
    pre-migration state, which also permits the OAC's signed requests) or
    already allows the CloudFront OAC service principal with this
    distribution's ARN (idempotent re-run of an already-migrated bucket).
    """
    issues: list[dict[str, str]] = []
    if bucket_policy is None:
        issues.append(
            _issue(
                "PRECONDITION_NO_POLICY",
                "s3:GetBucketPolicy",
                "no bucket policy is configured; switching the origin to a private "
                "REST endpoint would lock out CloudFront",
            )
        )
    elif not (_has_public_read_policy(bucket_policy) or _has_oac_policy(bucket_policy)):
        issues.append(
            _issue(
                "PRECONDITION_LOCKOUT",
                "Policy.Statement",
                "current bucket policy neither grants public read nor allows the "
                "CloudFront OAC (aws:SourceArn=" + DISTRIBUTION_ARN + "); switching "
                "the origin would create a lockout window",
            )
        )
    return VerificationOutcome(not issues, issues)


def migration_plan(distribution_config: Any) -> list[dict[str, str]]:
    """Return the ordered, non-secret mutation plan for the OAC migration.

    The plan is executed only by ``scripts/migrate-frontend-oac.sh`` (with an
    immediate read-back after every mutation) and is never applied in subphase
    3.5. Each step is idempotent and fail-closed: a read-back that does not
    match stops the run.

    Step 0 is a no-mutation precondition gate (no lockout window). When the
    origin must be re-pointed, step 3 waits for the asynchronous CloudFront
    deployment to reach ``Deployed`` before the bucket policy is tightened, so
    the operator tool never claims success while the edge is still serving the
    old origin.
    """
    plan: list[dict[str, str]] = []

    if not isinstance(distribution_config, dict):
        return plan

    s3_origins = _s3_origins(distribution_config)
    needs_oac = any(not o.get("OriginAccessControlId") for o in s3_origins)
    website_present = any(is_website_origin(o.get("DomainName")) for o in s3_origins)

    plan.append(
        {
            "step": "0",
            "mutation": "verify migration preconditions (current bucket policy "
            "grants public read or the CloudFront OAC) so the origin switch "
            "cannot create a lockout window",
            "readBack": "s3api get-bucket-policy (evaluated by this module's "
            "migration_preconditions)",
        }
    )
    if needs_oac:
        plan.append(
            {
                "step": "1",
                "mutation": f"cloudfront create-origin-access-control (name={OAC_NAME})",
                "readBack": "cloudfront get-origin-access-control",
            }
        )
    if website_present or needs_oac:
        plan.append(
            {
                "step": "2",
                "mutation": f"cloudfront update-distribution "
                f"(origin {FRONTEND_BUCKET}.s3.eu-north-1.amazonaws.com + OriginAccessControlId)",
                "readBack": "cloudfront get-distribution",
            }
        )
        plan.append(
            {
                "step": "3",
                "mutation": "wait for CloudFront distribution status Deployed "
                "(asynchronous deployment)",
                "readBack": "cloudfront get-distribution (Distribution.Status)",
            }
        )
    plan.append(
        {
            "step": "4",
            "mutation": "s3api put-bucket-policy "
            "(cloudfront.amazonaws.com service principal + aws:SourceArn)",
            "readBack": "s3api get-bucket-policy",
        }
    )
    plan.append(
        {
            "step": "5",
            "mutation": "s3api put-public-access-block "
            "(BlockPublicAcls, IgnorePublicAcls, BlockPublicPolicy, RestrictPublicBuckets = true)",
            "readBack": "s3api get-public-access-block",
        }
    )
    plan.append(
        {
            "step": "6",
            "mutation": "s3api delete-bucket-website",
            "readBack": "s3api get-bucket-website (must report absent)",
        }
    )
    plan.append(
        {
            "step": "7",
            "mutation": "full read-back via verify "
            "(distribution config + policy + public access block + SPA fallback)",
            "readBack": "this module's verify()",
        }
    )
    return plan


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _cmd_verify(args: argparse.Namespace) -> int:
    distribution = _read_json(args.distribution)
    bucket_policy = _read_json(args.bucket_policy) if args.bucket_policy else None
    public_access_block = _read_json(args.public_access_block) if args.public_access_block else None
    website = _read_json(args.website) if args.website else None
    outcome = verify(distribution, bucket_policy, public_access_block, website)
    _print_json({"valid": outcome.valid, "issues": outcome.issues})
    return 0 if outcome.valid else 1


def _cmd_plan(args: argparse.Namespace) -> int:
    distribution = _read_json(args.distribution)
    _print_json({"plan": migration_plan(distribution)})
    return 0


def _cmd_preconditions(args: argparse.Namespace) -> int:
    bucket_policy = _read_json(args.bucket_policy) if args.bucket_policy else None
    outcome = migration_preconditions(bucket_policy)
    _print_json({"valid": outcome.valid, "issues": outcome.issues})
    return 0 if outcome.valid else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.frontend_hosting",
        description="S3 REST origin + CloudFront OAC hardening verification and migration plan.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify_cmd = sub.add_parser(
        "verify", help="fail-closed verification of the hardened frontend hosting model"
    )
    verify_cmd.add_argument(
        "--distribution",
        required=True,
        metavar="FILE",
        help="CloudFront DistributionConfig JSON file",
    )
    verify_cmd.add_argument(
        "--bucket-policy",
        metavar="FILE",
        help="decoded S3 bucket policy JSON (omit when unconfigured)",
    )
    verify_cmd.add_argument(
        "--public-access-block",
        metavar="FILE",
        help="S3 public access block JSON (omit when unconfigured)",
    )
    verify_cmd.add_argument(
        "--website", metavar="FILE", help="S3 website configuration JSON (omit when absent)"
    )
    verify_cmd.set_defaults(func=_cmd_verify)

    plan_cmd = sub.add_parser("plan", help="ordered OAC migration mutation plan (dry-run)")
    plan_cmd.add_argument(
        "--distribution",
        required=True,
        metavar="FILE",
        help="CloudFront DistributionConfig JSON file",
    )
    plan_cmd.set_defaults(func=_cmd_plan)

    pre_cmd = sub.add_parser(
        "preconditions",
        help="no-lockout preconditions for the OAC migration (read-only, before any mutation)",
    )
    pre_cmd.add_argument(
        "--bucket-policy",
        metavar="FILE",
        help="decoded S3 bucket policy JSON (omit when unconfigured)",
    )
    pre_cmd.set_defaults(func=_cmd_preconditions)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
