"""Argparse CLI entry point for the delivery engine.

Exit codes: 0 on success, 1 on any DeliveryError (printed as
"ERROR <code>: <message>" on stderr), 2 on argparse usage errors.
AwsContext cannot carry the structured identifiers (services list and
ecrRepositories map exceed its strictly typed dict[str, str] field), so
build_context stashes the validated raw identifiers on args.identifiers_data
and hands the handlers a context holding the flat string subset.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from botocore.exceptions import ClientError

from .aws.context import AwsContext
from .commands import (
    candidate,
    deploy,
    finalize,
    promote,
    recover,
    retention,
    rollback,
    snapshot,
    staging,
    verify,
)
from .errors import DeliveryError, ValidationError

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

_AWS_ENVIRONMENTS = ("production", "staging")
_SERVICE_KEYS = ("auth", "items", "gateway")
_RELEASE_ID_PATTERN = re.compile(r"^release-\d{4}$")
_ACCOUNT_ID_PATTERN = re.compile(r"^\d{12}$")
_PRODUCTION_IDENTIFIER_KEYS = (
    "cluster",
    "dbInstance",
    "frontendBucket",
    "frontendLiveMarker",
    "frontendReleasesPrefix",
    "cloudfrontDistributionId",
)
_PRODUCTION_ONLY_KEYS = (
    "frontendBucket",
    "frontendLiveMarker",
    "frontendReleasesPrefix",
    "cloudfrontDistributionId",
)
_STAGING_REQUIRED_STRING_KEYS = (
    "cluster",
    "dbInstance",
    "albName",
    "sqlRunnerFamily",
    "sqlLogGroup",
    "sqlSecurityGroup",
    "executionRoleArn",
    "compatFrontendBucket",
    "compatFrontendReleasesPrefix",
)
_STAGING_OPTIONAL_STRING_KEYS = ("e2eBaseUrl", "targetGroupArn")
_COMMON_IDENTIFIER_KEYS = frozenset(
    {
        "environment",
        "accountId",
        "cluster",
        "services",
        "ecrRepositories",
        "dbInstance",
    }
)


def main(argv: list[str] | None = None) -> int:
    """Parse argv, dispatch to the command handler, and return the exit code."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    try:
        _validate_snapshot_environment(args)
        _validate_staging_arguments(args)
        return int(args.func(args))
    except DeliveryError as error:
        print(f"ERROR {error.code}: {error}", file=sys.stderr)
        return EXIT_FAILURE
    except ClientError as error:
        print(f"ERROR READ_ERROR: {error}", file=sys.stderr)
        return EXIT_FAILURE


def _validate_snapshot_environment(args: argparse.Namespace) -> None:
    """Require snapshot-consuming commands to carry a snapshot of the target environment."""
    command = getattr(args, "command", None)
    label = None
    if command in ("deploy", "recover", "finalize"):
        label = command
    elif command == "promote" and getattr(args, "subcommand", None) == "preflight":
        label = "promote preflight"
    elif command == "rollback" and getattr(args, "subcommand", None) in (
        "preflight",
        "execute",
    ):
        label = f"rollback {args.subcommand}"
    if label is None:
        return
    path = args.snapshot
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ValidationError(f"cannot read snapshot file {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"snapshot file {path} is not valid JSON: {error}") from error
    snapshot_environment = raw.get("environment") if isinstance(raw, dict) else None
    if snapshot_environment != args.environment:
        raise ValidationError(
            f"{label} requires a snapshot of environment "
            f"{args.environment!r}, snapshot declares {snapshot_environment!r}"
        )


def build_parser() -> argparse.ArgumentParser:
    """Build the full argparse tree with all registered subcommands."""
    parser = argparse.ArgumentParser(
        prog="delivery",
        description="Delivery engine for the OnlineShop CI/CD release system",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    _add_candidate(subparsers)
    _add_snapshot(subparsers)
    _add_staging(subparsers)
    _add_promote(subparsers)
    _add_deploy(subparsers)
    _add_verify(subparsers)
    _add_finalize(subparsers)
    _add_recover(subparsers)
    _add_rollback(subparsers)
    _add_retention(subparsers)
    return parser


def _validate_staging_arguments(args: argparse.Namespace) -> None:
    """Enforce the two-invocation lifecycle split at the CLI boundary."""
    if (
        getattr(args, "command", None) != "staging"
        or getattr(args, "subcommand", None) != "lifecycle"
    ):
        return
    if args.continue_lifecycle:
        if args.candidate is not None or args.frontend_archive is not None:
            raise ValidationError(
                "--continue cannot be combined with --candidate or --frontend-archive"
            )
        if args.e2e_conclusion is None:
            raise ValidationError("--continue requires --e2e-conclusion passed|failed")
        if args.max_age_days is not None or args.e2e_url_out is not None:
            raise ValidationError(
                "--continue cannot be combined with --max-age-days or --e2e-url-out"
            )
    else:
        if args.e2e_conclusion is not None:
            raise ValidationError("--e2e-conclusion is only valid together with --continue")
        if args.candidate is None or args.frontend_archive is None:
            raise ValidationError(
                "the first lifecycle invocation requires --candidate and --frontend-archive"
            )


def build_context(args: argparse.Namespace) -> AwsContext:
    """Load and validate the identifiers JSON and return the AwsContext.

    Production identifiers keep the exact original required-key validation.
    Staging identifiers validate the staging shape instead: the four
    frontend/CloudFront keys are production-only and are rejected in staging
    files; staging instead requires the ECS/RDS/ALB identifiers plus the
    read-only compatibility-frontend location and the SQL runner identifiers.
    """
    raw = _load_identifiers(args.identifiers)
    if raw["environment"] != args.environment:
        raise ValidationError(
            "identifiers environment "
            f"{raw['environment']!r} does not match --environment {args.environment!r}"
        )
    account_id = raw["accountId"]
    if not isinstance(account_id, str) or not _ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise ValidationError("identifiers accountId must be a string of exactly 12 digits")
    if args.environment == "production":
        _validate_production_identifiers(raw)
    else:
        _validate_staging_identifiers(raw)
    services = raw["services"]
    if (
        not isinstance(services, list)
        or not services
        or not all(isinstance(service, str) and service for service in services)
    ):
        raise ValidationError("identifiers services must be a non-empty list of non-empty strings")
    repositories = raw["ecrRepositories"]
    if not isinstance(repositories, dict) or sorted(repositories) != sorted(_SERVICE_KEYS):
        raise ValidationError("identifiers ecrRepositories must map auth, items, gateway")
    if not all(isinstance(repository, str) and repository for repository in repositories.values()):
        raise ValidationError("identifiers ecrRepositories values must be non-empty strings")
    args.identifiers_data = raw
    return AwsContext(
        profile=args.profile,
        region=args.region,
        account_id=raw["accountId"],
        environment=args.environment,
        identifiers={key: value for key, value in raw.items() if isinstance(value, str)},
    )


def _validate_production_identifiers(raw: dict) -> None:
    for key in _PRODUCTION_IDENTIFIER_KEYS:
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            raise ValidationError(f"identifiers {key} must be a non-empty string")


def _validate_staging_identifiers(raw: dict) -> None:
    forbidden = [key for key in _PRODUCTION_ONLY_KEYS if key in raw]
    if forbidden:
        raise ValidationError(
            f"staging identifiers must not carry production-only keys: {', '.join(forbidden)}"
        )
    for key in _STAGING_REQUIRED_STRING_KEYS:
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            raise ValidationError(f"identifiers {key} must be a non-empty string")
    for key in _STAGING_OPTIONAL_STRING_KEYS:
        if key in raw and not isinstance(raw[key], str):
            raise ValidationError(f"identifiers {key} must be a string when present")
    db_secrets = raw.get("dbSecrets")
    if not isinstance(db_secrets, dict) or sorted(db_secrets) != ["auth", "items"]:
        raise ValidationError("identifiers dbSecrets must map auth and items")
    if not all(isinstance(secret, str) and secret for secret in db_secrets.values()):
        raise ValidationError("identifiers dbSecrets values must be non-empty strings")
    subnets = raw.get("sqlSubnets")
    if (
        not isinstance(subnets, list)
        or not subnets
        or not all(isinstance(subnet, str) and subnet for subnet in subnets)
    ):
        raise ValidationError(
            "identifiers sqlSubnets must be a non-empty list of non-empty strings"
        )


def _load_identifiers(path: str) -> dict:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ValidationError(f"cannot read identifiers file {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"identifiers file {path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ValidationError("identifiers file must contain a JSON object")
    missing = sorted(_COMMON_IDENTIFIER_KEYS - raw.keys())
    if missing:
        raise ValidationError(f"identifiers file missing keys: {', '.join(missing)}")
    return raw


def _add_aws_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", default=None, metavar="NAME", help="AWS profile name")
    parser.add_argument("--region", default="eu-north-1", metavar="REGION", help="AWS region")
    parser.add_argument(
        "--environment",
        choices=_AWS_ENVIRONMENTS,
        required=True,
        help="target environment",
    )
    parser.add_argument(
        "--identifiers",
        required=True,
        metavar="FILE",
        help="JSON file of explicit non-secret identifiers",
    )


def _release_id(value: str) -> str:
    if not _RELEASE_ID_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("release-id must match release-NNNN (e.g. release-0002)")
    return value


def _positive_age(value: str) -> int:
    try:
        days = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("max-age-days must be a positive integer") from error
    if days <= 0:
        raise argparse.ArgumentTypeError("max-age-days must be a positive integer")
    return days


def _iso_datetime(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("reference-date must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "reference-date must be timezone-aware (naive datetimes are rejected)"
        )
    if parsed.utcoffset() != timedelta(0):
        raise argparse.ArgumentTypeError("reference-date must have a UTC offset of zero")
    return value


def _add_candidate(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("candidate", help="candidate manifest operations")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    validate = sub.add_parser("validate", help="validate a candidate manifest")
    validate.add_argument(
        "--manifest", required=True, metavar="FILE", help="candidate manifest JSON file"
    )
    validate.add_argument(
        "--class",
        dest="candidate_class",
        choices=("feature", "main"),
        help="required candidate class",
    )
    validate.add_argument(
        "--require-production-eligible",
        action="store_true",
        help="require productionEligible true",
    )
    validate.add_argument(
        "--max-age-days",
        type=_positive_age,
        metavar="DAYS",
        help="reject candidates completed more than DAYS ago",
    )
    validate.set_defaults(func=candidate.validate)
    manifest = sub.add_parser("manifest", help="build a candidate manifest from structured inputs")
    manifest.add_argument(
        "--inputs", required=True, metavar="FILE", help="structured build inputs JSON file"
    )
    manifest.add_argument(
        "--out", required=True, metavar="FILE", help="output candidate manifest JSON file"
    )
    manifest.add_argument(
        "--class",
        dest="candidate_class",
        choices=("feature", "main"),
        required=True,
        help="candidate class",
    )
    manifest.add_argument(
        "--max-age-days",
        type=_positive_age,
        metavar="DAYS",
        help="reject candidates completed more than DAYS ago",
    )
    manifest.set_defaults(func=candidate.manifest)


def _add_snapshot(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("snapshot", help="production snapshot operations")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    production = sub.add_parser("production", help="capture the read-only production snapshot")
    production.add_argument(
        "--out", required=True, metavar="FILE", help="output snapshot JSON file"
    )
    _add_aws_flags(production)
    production.set_defaults(func=snapshot.snapshot_production, context_builder=build_context)


def _add_staging(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("staging", help="staging environment operations")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    lifecycle = sub.add_parser("lifecycle", help="run the staging lifecycle")
    lifecycle.add_argument(
        "--candidate", metavar="FILE", help="candidate manifest JSON file (first invocation)"
    )
    lifecycle.add_argument(
        "--frontend-archive",
        metavar="FILE",
        help="candidate frontend archive (first invocation)",
    )
    lifecycle.add_argument(
        "--continue",
        dest="continue_lifecycle",
        action="store_true",
        help="resume from the record written by the first invocation",
    )
    lifecycle.add_argument(
        "--e2e-conclusion",
        choices=("passed", "failed"),
        help="cloud E2E conclusion (required with --continue)",
    )
    lifecycle.add_argument(
        "--e2e-url-out",
        metavar="FILE",
        help="write the resolved E2E base URL to FILE",
    )
    lifecycle.add_argument(
        "--owner",
        metavar="NAME",
        help="staging operator identity recorded in the ownership marker",
    )
    lifecycle.add_argument(
        "--max-age-days",
        type=_positive_age,
        metavar="DAYS",
        help="reject candidates completed more than DAYS ago",
    )
    lifecycle.add_argument(
        "--out", required=True, metavar="FILE", help="output staging record JSON file"
    )
    lifecycle.add_argument(
        "--repo-path",
        required=True,
        metavar="DIR",
        help="repository checkout containing the reset SQL sources "
        "(scripts/sql/*.sql, Auth/init-db/*.sql, Items/init-db/*.sql)",
    )
    _add_aws_flags(lifecycle)
    lifecycle.set_defaults(func=staging.lifecycle, context_builder=build_context)
    apply = sub.add_parser("apply", help="apply a candidate to the staging environment")
    apply.add_argument(
        "--candidate", required=True, metavar="FILE", help="candidate manifest JSON file"
    )
    apply.add_argument(
        "--max-age-days",
        type=_positive_age,
        metavar="DAYS",
        help="reject candidates completed more than DAYS ago",
    )
    apply.add_argument(
        "--owner",
        metavar="NAME",
        help="staging operator identity recorded in the staging record",
    )
    apply.add_argument(
        "--out", required=True, metavar="FILE", help="output staging record JSON file"
    )
    apply.add_argument(
        "--repo-path",
        required=True,
        metavar="DIR",
        help="repository checkout containing the reset SQL sources",
    )
    _add_aws_flags(apply)
    apply.set_defaults(func=staging.apply, context_builder=build_context)
    reconcile = sub.add_parser(
        "reconcile", help="detect and stop ownerless running staging RDS (OP-STG-05)"
    )
    reconcile.add_argument(
        "--out", required=True, metavar="FILE", help="output reconcile record JSON file"
    )
    _add_aws_flags(reconcile)
    reconcile.set_defaults(func=staging.reconcile, context_builder=build_context)


def _add_promote(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("promote", help="production promotion operations")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    preflight = sub.add_parser("preflight", help="read-only production promotion preflight")
    preflight.add_argument(
        "--candidate", required=True, metavar="FILE", help="candidate manifest JSON file"
    )
    preflight.add_argument(
        "--frontend-archive",
        required=True,
        metavar="FILE",
        help="candidate frontend archive file",
    )
    preflight.add_argument(
        "--sbom-dir", required=True, metavar="DIR", help="candidate SBOM artifact directory"
    )
    preflight.add_argument(
        "--staging-run",
        required=True,
        type=int,
        metavar="RUN_ID",
        help="exact staging workflow run whose staging-record artifact is the gate evidence",
    )
    preflight.add_argument(
        "--snapshot", required=True, metavar="FILE", help="fresh production snapshot JSON file"
    )
    preflight.add_argument(
        "--repo-path",
        required=True,
        metavar="DIR",
        help="repository checkout for the OP-DB migration-ownership scan",
    )
    preflight.add_argument(
        "--max-age-days",
        type=_positive_age,
        default=30,
        metavar="DAYS",
        help="reject candidates completed more than DAYS ago (default 30)",
    )
    preflight.add_argument(
        "--previous-report",
        metavar="FILE",
        help="pre-approval preflight report for post-approval drift comparison",
    )
    preflight.add_argument(
        "--staging-record-out",
        metavar="FILE",
        help="write the validated staging record to FILE (consumed by finalize)",
    )
    preflight.add_argument(
        "--out", required=True, metavar="FILE", help="output preflight report JSON file"
    )
    _add_aws_flags(preflight)
    preflight.set_defaults(func=promote.preflight, context_builder=build_context)


def _add_deploy(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("deploy", help="deploy components to an environment")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    for name, handler in (
        ("backends", deploy.backends),
        ("gateway", deploy.gateway),
        ("frontend", deploy.frontend),
    ):
        component = sub.add_parser(name, help=f"deploy the {name} component")
        component.add_argument(
            "--candidate", required=True, metavar="FILE", help="candidate manifest JSON file"
        )
        component.add_argument(
            "--snapshot",
            required=True,
            metavar="FILE",
            help="snapshot JSON file of the environment being deployed to",
        )
        component.add_argument(
            "--dry-run", action="store_true", help="resolve and plan without mutating"
        )
        if name == "frontend":
            component.add_argument(
                "--frontend-archive",
                required=True,
                metavar="FILE",
                help="candidate frontend archive file",
            )
            component.add_argument(
                "--out",
                required=True,
                metavar="FILE",
                help="output frontend publish record JSON file",
            )
        _add_aws_flags(component)
        component.set_defaults(func=handler, context_builder=build_context)


def _add_verify(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("verify", help="verify a deployed environment")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    production = sub.add_parser("production", help="verify production read-only (CT-PROD)")
    group = production.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--manifest", metavar="FILE", help="official release manifest JSON file"
    )
    group.add_argument("--candidate", metavar="FILE", help="candidate manifest JSON file")
    production.add_argument(
        "--out", required=True, metavar="FILE", help="output verification report JSON file"
    )
    _add_aws_flags(production)
    production.set_defaults(func=verify.production, context_builder=build_context)
    staging = sub.add_parser("staging", help="verify staging against a candidate")
    staging.add_argument(
        "--candidate", required=True, metavar="FILE", help="candidate manifest JSON file"
    )
    _add_aws_flags(staging)
    staging.set_defaults(func=verify.staging, context_builder=build_context)


def _add_finalize(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("finalize", help="finalize an approved release (OP-FIN)")
    parser.add_argument(
        "--candidate", required=True, metavar="FILE", help="candidate manifest JSON file"
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        metavar="FILE",
        help="pre-mutation production snapshot JSON file",
    )
    parser.add_argument(
        "--staging-record", required=True, metavar="FILE", help="validated staging record JSON file"
    )
    parser.add_argument(
        "--staging-record-identity",
        required=True,
        metavar="ID",
        help="staging-record artifact identity (staging-record-<run>-<attempt>)",
    )
    parser.add_argument(
        "--verification-report",
        required=True,
        metavar="FILE",
        help="production verification report JSON file",
    )
    parser.add_argument(
        "--approval", required=True, metavar="FILE", help="approval evidence JSON file"
    )
    parser.add_argument(
        "--frontend-publish",
        required=True,
        metavar="FILE",
        help="frontend publish record JSON file from deploy frontend",
    )
    parser.add_argument(
        "--frontend-archive", required=True, metavar="FILE", help="candidate frontend archive file"
    )
    parser.add_argument(
        "--sbom-dir", required=True, metavar="DIR", help="candidate SBOM artifact directory"
    )
    parser.add_argument(
        "--manifest",
        required=True,
        metavar="FILE",
        help="official release manifest JSON file (output; exact-resume input)",
    )
    parser.add_argument(
        "--out", required=True, metavar="FILE", help="output finalization report JSON file"
    )
    parser.add_argument("--dry-run", action="store_true", help="resolve and plan without mutating")
    _add_aws_flags(parser)
    parser.set_defaults(func=finalize.finalize, context_builder=build_context)


def _add_recover(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("recover", help="compensate changed components from a snapshot")
    parser.add_argument(
        "--snapshot",
        required=True,
        metavar="FILE",
        help="pre-mutation production snapshot JSON file",
    )
    parser.add_argument(
        "--changed", required=True, metavar="FILE", help="JSON array of changed component names"
    )
    parser.add_argument(
        "--out",
        metavar="FILE",
        help="output recovery result JSON file (original + recovery outcomes)",
    )
    parser.add_argument(
        "--original-failure",
        metavar="TEXT",
        help="description of the original failure (recorded in the recovery result)",
    )
    parser.add_argument("--dry-run", action="store_true", help="resolve and plan without mutating")
    _add_aws_flags(parser)
    parser.set_defaults(func=recover.recover, context_builder=build_context)


def _add_rollback(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("rollback", help="rollback to an official release")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    preflight = sub.add_parser("preflight", help="select and guard a rollback target")
    preflight.add_argument(
        "--release-id",
        required=True,
        type=_release_id,
        metavar="release-NNNN",
        help="target official release id (e.g. release-0002)",
    )
    preflight.add_argument(
        "--snapshot",
        required=True,
        metavar="FILE",
        help="fresh production snapshot JSON file (the current live release identity)",
    )
    preflight.add_argument(
        "--schema-change", choices=("present", "absent"), help="schema change guard input"
    )
    preflight.add_argument(
        "--migration-reviewed", choices=("true", "false"), help="migration review guard input"
    )
    preflight.add_argument(
        "--repository",
        metavar="OWNER/NAME",
        help="GitHub repository (default: $GITHUB_REPOSITORY)",
    )
    preflight.add_argument(
        "--previous-report",
        metavar="FILE",
        help="pre-approval preflight report for post-approval drift comparison",
    )
    preflight.add_argument(
        "--out", required=True, metavar="FILE", help="output preflight report JSON file"
    )
    preflight.add_argument(
        "--manifest-out",
        required=True,
        metavar="FILE",
        help="write the validated official release manifest to FILE",
    )
    _add_aws_flags(preflight)
    preflight.set_defaults(func=rollback.preflight, context_builder=build_context)
    execute = sub.add_parser("execute", help="deploy the approved rollback target")
    execute.add_argument(
        "--manifest",
        required=True,
        metavar="FILE",
        help="target official release manifest JSON file",
    )
    execute.add_argument(
        "--snapshot",
        required=True,
        metavar="FILE",
        help="pre-rollback production snapshot JSON file",
    )
    execute.add_argument(
        "--preflight-report",
        metavar="FILE",
        help="pre-approval rollback preflight report (re-preflight identity match)",
    )
    execute.add_argument(
        "--approval",
        metavar="FILE",
        help="approval evidence JSON file (approver, requester, workflow URL, timestamp)",
    )
    execute.add_argument(
        "--workflow-run-id",
        type=int,
        metavar="RUN_ID",
        help="workflow run id for the rollback result (default: $GITHUB_RUN_ID)",
    )
    execute.add_argument(
        "--workflow-run-attempt",
        type=int,
        metavar="ATTEMPT",
        help="workflow run attempt for the rollback result (default: $GITHUB_RUN_ATTEMPT)",
    )
    execute.add_argument(
        "--repository",
        metavar="OWNER/NAME",
        help="GitHub repository (default: $GITHUB_REPOSITORY)",
    )
    execute.add_argument(
        "--out", metavar="FILE", help="output rollback result JSON file"
    )
    execute.add_argument("--dry-run", action="store_true", help="resolve and plan without mutating")
    _add_aws_flags(execute)
    execute.set_defaults(func=rollback.execute, context_builder=build_context)


def _add_retention(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("retention", help="ECR lifecycle retention operations")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    audit = sub.add_parser("audit", help="audit the four-release rollback window")
    audit.add_argument("--human", action="store_true", help="add a concise human view")
    audit.add_argument(
        "--snapshot",
        required=True,
        metavar="FILE",
        help="fresh production snapshot JSON file (from `delivery snapshot production`)",
    )
    audit.add_argument(
        "--repository",
        metavar="OWNER/NAME",
        help="GitHub repository (default: $GITHUB_REPOSITORY)",
    )
    _add_aws_flags(audit)
    audit.set_defaults(func=retention.audit, context_builder=build_context)
    preview = sub.add_parser("preview", help="preview a lifecycle policy effect")
    preview.add_argument("--policy", metavar="FILE", help="lifecycle policy JSON file")
    preview.add_argument(
        "--reference-date", type=_iso_datetime, metavar="ISO", help="evaluation reference date"
    )
    preview.add_argument(
        "--snapshot",
        required=True,
        metavar="FILE",
        help="fresh production snapshot JSON file (from `delivery snapshot production`)",
    )
    preview.add_argument(
        "--repository",
        metavar="OWNER/NAME",
        help="GitHub repository (default: $GITHUB_REPOSITORY)",
    )
    _add_aws_flags(preview)
    preview.set_defaults(func=retention.preview, context_builder=build_context)
    apply = sub.add_parser("apply", help="apply a lifecycle policy")
    mode = apply.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="preview without applying")
    mode.add_argument("--apply", action="store_true", help="apply the lifecycle policy")
    apply.add_argument("--policy", metavar="FILE", help="lifecycle policy JSON file")
    apply.add_argument(
        "--reference-date", type=_iso_datetime, metavar="ISO", help="evaluation reference date"
    )
    apply.add_argument(
        "--snapshot",
        required=True,
        metavar="FILE",
        help="fresh production snapshot JSON file (from `delivery snapshot production`)",
    )
    apply.add_argument(
        "--repository",
        metavar="OWNER/NAME",
        help="GitHub repository (default: $GITHUB_REPOSITORY)",
    )
    _add_aws_flags(apply)
    apply.set_defaults(func=retention.apply, context_builder=build_context)


if __name__ == "__main__":
    sys.exit(main())
