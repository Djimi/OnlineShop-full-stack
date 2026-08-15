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
_STR_IDENTIFIER_KEYS = (
    "cluster",
    "dbInstance",
    "frontendBucket",
    "frontendLiveMarker",
    "frontendReleasesPrefix",
    "cloudfrontDistributionId",
)
_IDENTIFIER_KEYS = frozenset(
    {
        "environment",
        "accountId",
        "cluster",
        "services",
        "ecrRepositories",
        "dbInstance",
        "frontendBucket",
        "frontendLiveMarker",
        "frontendReleasesPrefix",
        "cloudfrontDistributionId",
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
    if command in ("deploy", "recover"):
        label = command
    elif command == "rollback" and getattr(args, "subcommand", None) == "execute":
        label = "rollback execute"
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
    _add_deploy(subparsers)
    _add_verify(subparsers)
    _add_finalize(subparsers)
    _add_recover(subparsers)
    _add_rollback(subparsers)
    _add_retention(subparsers)
    return parser


def build_context(args: argparse.Namespace) -> AwsContext:
    """Load and validate the identifiers JSON and return the AwsContext."""
    raw = _load_identifiers(args.identifiers)
    if raw["environment"] != args.environment:
        raise ValidationError(
            "identifiers environment "
            f"{raw['environment']!r} does not match --environment {args.environment!r}"
        )
    account_id = raw["accountId"]
    if not isinstance(account_id, str) or not _ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise ValidationError("identifiers accountId must be a string of exactly 12 digits")
    for key in _STR_IDENTIFIER_KEYS:
        value = raw[key]
        if not isinstance(value, str) or not value:
            raise ValidationError(f"identifiers {key} must be a non-empty string")
    services = raw["services"]
    if not isinstance(services, list) or not services or not all(
        isinstance(service, str) and service for service in services
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


def _load_identifiers(path: str) -> dict:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ValidationError(f"cannot read identifiers file {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"identifiers file {path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ValidationError("identifiers file must contain a JSON object")
    missing = sorted(_IDENTIFIER_KEYS - raw.keys())
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
        "--out", required=True, metavar="FILE", help="output staging record JSON file"
    )
    _add_aws_flags(lifecycle)
    lifecycle.set_defaults(func=staging.lifecycle)
    apply = sub.add_parser("apply", help="apply a candidate to the staging environment")
    apply.add_argument(
        "--candidate", required=True, metavar="FILE", help="candidate manifest JSON file"
    )
    apply.add_argument(
        "--out", required=True, metavar="FILE", help="output staging record JSON file"
    )
    _add_aws_flags(apply)
    apply.set_defaults(func=staging.apply)


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
        _add_aws_flags(component)
        component.set_defaults(func=handler)


def _add_verify(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("verify", help="verify a deployed environment")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    production = sub.add_parser("production", help="verify production against an official manifest")
    production.add_argument(
        "--manifest", required=True, metavar="FILE", help="official release manifest JSON file"
    )
    _add_aws_flags(production)
    production.set_defaults(func=verify.production)
    staging = sub.add_parser("staging", help="verify staging against a candidate")
    staging.add_argument(
        "--candidate", required=True, metavar="FILE", help="candidate manifest JSON file"
    )
    _add_aws_flags(staging)
    staging.set_defaults(func=verify.staging)


def _add_finalize(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("finalize", help="finalize an approved release")
    parser.add_argument(
        "--manifest", required=True, metavar="FILE", help="official release manifest JSON file"
    )
    parser.add_argument(
        "--evidence-dir", required=True, metavar="DIR", help="candidate evidence directory"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="resolve and plan without mutating"
    )
    _add_aws_flags(parser)
    parser.set_defaults(func=finalize.finalize)


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
        "--dry-run", action="store_true", help="resolve and plan without mutating"
    )
    _add_aws_flags(parser)
    parser.set_defaults(func=recover.recover)


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
        "--schema-change", choices=("present", "absent"), help="schema change guard input"
    )
    preflight.add_argument(
        "--migration-reviewed", choices=("true", "false"), help="migration review guard input"
    )
    _add_aws_flags(preflight)
    preflight.set_defaults(func=rollback.preflight)
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
        "--dry-run", action="store_true", help="resolve and plan without mutating"
    )
    _add_aws_flags(execute)
    execute.set_defaults(func=rollback.execute)


def _add_retention(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("retention", help="ECR lifecycle retention operations")
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="SUBCOMMAND")
    audit = sub.add_parser("audit", help="audit the 10-release rollback window")
    audit.add_argument("--human", action="store_true", help="add a concise human view")
    _add_aws_flags(audit)
    audit.set_defaults(func=retention.audit)
    preview = sub.add_parser("preview", help="preview a lifecycle policy effect")
    preview.add_argument("--policy", metavar="FILE", help="lifecycle policy JSON file")
    preview.add_argument(
        "--reference-date", type=_iso_datetime, metavar="ISO", help="evaluation reference date"
    )
    _add_aws_flags(preview)
    preview.set_defaults(func=retention.preview)
    apply = sub.add_parser("apply", help="apply a lifecycle policy")
    mode = apply.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="preview without applying")
    mode.add_argument("--apply", action="store_true", help="apply the lifecycle policy")
    apply.add_argument("--policy", metavar="FILE", help="lifecycle policy JSON file")
    apply.add_argument(
        "--reference-date", type=_iso_datetime, metavar="ISO", help="evaluation reference date"
    )
    _add_aws_flags(apply)
    apply.set_defaults(func=retention.apply)


if __name__ == "__main__":
    sys.exit(main())
