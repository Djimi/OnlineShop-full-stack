"""Production inventory and production/staging separation checks
(Pass 3, subphase 3.5).

Production and staging are independent environments: dedicated VPCs, ECS
clusters, RDS instances, security groups, Cloud Map namespaces, Secrets Manager
entries, services, and target groups. This module enforces that contract over
the explicit non-secret identifiers in ``scripts/config/{production,staging}
.env`` and over read-only ``aws`` observed state:

- :func:`separation_issues` compares the two environments' identifier sets and
  fails when any environment-scoped resource is shared. Account/profile/region
  and the shared ECS execution role are intentionally excluded (they are not
  environment-scoped).
- :func:`inventory_issues` compares an expected production identifier set
  against live observed state (from ``scripts/inventory-production.sh``) and
  reports drift. Expected identifiers are non-secret names/ARNs only; secret
  **values** never enter these files or reports.

The 3.5 mandate "do not create duplicate prod" is enforced structurally by the
inventory tool's read-back: it only ever describes/gets/lists existing
resources and never creates anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

# Identifiers that are environment-scoped and therefore MUST differ between
# production and staging. The shared ECS execution role and the account/
# profile/region are intentionally excluded.
ENVIRONMENT_SCOPED_KEYS = (
    "vpcId",
    "cluster",
    "dbInstance",
    "dbSubnetGroup",
    "dbSecurityGroup",
    "ecsSecurityGroup",
    "albName",
    "albSecurityGroup",
    "targetGroupArn",
    "gatewayService",
    "namespace",
    "services",
    "subnets",
    "secrets",
    "logGroups",
)

# Keys present in every environment config that are not environment-scoped and
# must never be compared for separation.
NON_ENVIRONMENT_KEYS = ("environment", "accountId", "profile", "region")


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


@dataclass
class EnvironmentOutcome:
    """Result of a separation or inventory check."""

    valid: bool
    issues: list[dict[str, str]] = field(default_factory=list)


def _iter_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, (int, float)):
        return [str(value)]
    return []


def _read_error_markers(observed: Any) -> list[tuple[str, str]]:
    """Collect (key, marker) pairs where an AWS read failed.

    The shell helpers report a genuinely missing resource as "missing" (or an
    element suffix "-MISSING") and an API read failure as "error" (or an
    element suffix "-ERROR"). An "error" is NOT a missing resource: it must
    fail the check with a distinct, honest message so an auth/throttle/network
    failure is never disguised as drift or silence.
    """
    if not isinstance(observed, dict):
        return []
    markers: list[tuple[str, str]] = []
    for key, value in observed.items():
        if key == "serviceNamespaces" and isinstance(value, dict):
            for namespace in value.values():
                if namespace == "error":
                    markers.append((key, str(namespace)))
            continue
        for item in _iter_values(value):
            if item == "error" or item.endswith("-ERROR"):
                markers.append((key, item))
    return markers


def _shared(prod_value: Any, staging_value: Any) -> list[str]:
    prod_set = set(_iter_values(prod_value))
    staging_set = set(_iter_values(staging_value))
    shared: set[str] = set()
    for value in prod_set:
        if value and value in staging_set:
            shared.add(value)
    # An empty/absent value in one environment is a config gap, not a share.
    return sorted(v for v in shared if v != "")


def separation_issues(prod: Any, staging: Any) -> EnvironmentOutcome:
    """Fail closed when production and staging share any environment-scoped
    resource identifier.

    ``prod`` and ``staging`` are flat mappings of identifier key -> value or
    list of values (as sourced from the two non-secret ``.env`` files or from
    observed live state). Keys present in only one config are skipped; a
    missing key in either side is reported as a gap so the config cannot
    silently omit a resource.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(prod, dict) or not isinstance(staging, dict):
        return EnvironmentOutcome(
            False, [_issue("INVALID_INPUT", "$", "prod and staging must be objects")]
        )

    for key in ENVIRONMENT_SCOPED_KEYS:
        in_prod = key in prod
        in_staging = key in staging
        if not in_prod or not in_staging:
            missing = "staging" if not in_staging else "production"
            issues.append(
                _issue(
                    "MISSING_IDENTIFIER",
                    key,
                    f"identifier {key!r} is absent from the {missing} config; add it explicitly",
                )
            )
            continue
        shared = _shared(prod[key], staging[key])
        for value in shared:
            issues.append(
                _issue(
                    "SHARED_RESOURCE",
                    key,
                    f"production and staging share {key}={value}; environments must be isolated",
                )
            )

    return EnvironmentOutcome(not issues, issues)


def inventory_issues(expected: Any, observed: Any) -> EnvironmentOutcome:
    """Compare the expected production identifier set against live observed
    state and report any drift.

    ``expected`` is the non-secret config (key -> value or list). ``observed``
    is what the read-only ``scripts/inventory-production.sh`` gathered from
    AWS. Every expected identifier must be present in observed with the same
    value. Observed-only resources are informational and are not drift.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return EnvironmentOutcome(
            False, [_issue("INVALID_INPUT", "$", "expected and observed must be objects")]
        )

    for key, expected_value in expected.items():
        if key in NON_ENVIRONMENT_KEYS:
            continue
        if key not in observed:
            issues.append(
                _issue(
                    "OBSERVED_MISSING", key, f"no observed value for expected identifier {key!r}"
                )
            )
            continue
        observed_value = observed[key]
        expected_set = set(_iter_values(expected_value))
        observed_set = set(_iter_values(observed_value))
        if expected_set != observed_set:
            issues.append(
                _issue(
                    "INVENTORY_DRIFT",
                    key,
                    f"expected {sorted(expected_set)}, observed {sorted(observed_set)}",
                )
            )

    # An AWS read that failed is never a missing resource; fail with a distinct
    # message so the operator fixes the API error instead of trusting a report.
    for key, marker in _read_error_markers(observed):
        issues.append(
            _issue(
                "OBSERVED_READ_ERROR",
                key,
                f"AWS read failed (not a missing resource): observed {marker!r}; "
                "resolve the API error before relying on this inventory",
            )
        )

    # Database privacy is a hardening invariant: the production DB must not be
    # publicly accessible.
    if observed.get("dbPublicAccessible") == "true":
        issues.append(
            _issue(
                "DB_PUBLIC_ACCESSIBLE",
                "dbPublicAccessible",
                "production database instance is publicly accessible",
            )
        )

    return EnvironmentOutcome(not issues, issues)


def topology_overlap_issues(prod: Any, staging: Any) -> EnvironmentOutcome:
    """Fail closed when the two environments' live network topology overlaps.

    ``prod``/``staging`` are the read-only topology observations gathered by
    ``scripts/lib/identifiers.sh`` ``topology_observed``:

    .. code-block:: json

        {
          "vpcId": "vpc-...",
          "sgVpcs": ["vpc-...", "vpc-..."],
          "subnetVpcs": ["vpc-..."],
          "dbSubnetGroupVpc": "vpc-...",
          "serviceNamespaces": {"onlineshop-auth": "onlineshop.local"}
        }

    This catches drift the identifier-identity check cannot: a staging security
    group or subnet that was accidentally placed in the production VPC, or a
    staging service still registered in the production Cloud Map namespace.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(prod, dict) or not isinstance(staging, dict):
        return EnvironmentOutcome(
            False, [_issue("INVALID_INPUT", "$", "prod and staging topology must be objects")]
        )

    shared_vpcs = _shared(prod.get("vpcId"), staging.get("vpcId"))
    shared_vpcs += _shared(prod.get("sgVpcs", []), staging.get("sgVpcs", []))
    shared_vpcs += _shared(prod.get("subnetVpcs", []), staging.get("subnetVpcs", []))
    shared_vpcs += _shared(prod.get("dbSubnetGroupVpc"), staging.get("dbSubnetGroupVpc"))
    for vpc in sorted(set(shared_vpcs)):
        if vpc == "missing" or vpc == "":
            continue
        issues.append(
            _issue(
                "SHARED_VPC",
                "topology",
                f"production and staging both place resources in VPC {vpc}",
            )
        )

    shared_namespaces = _shared(
        list(prod.get("serviceNamespaces", {}).values()),
        list(staging.get("serviceNamespaces", {}).values()),
    )
    for namespace in shared_namespaces:
        if namespace == "missing" or namespace == "":
            continue
        issues.append(
            _issue(
                "SHARED_NAMESPACE",
                "topology",
                f"production and staging services share Cloud Map namespace {namespace}",
            )
        )

    # A failed read ("error") is not a missing resource: it cannot prove
    # isolation either way, so the check must fail closed with an honest
    # message instead of silently treating the value as disjoint.
    for source, source_name in ((prod, "production"), (staging, "staging")):
        for key, marker in _read_error_markers(source):
            issues.append(
                _issue(
                    "TOPO_READ_ERROR",
                    key,
                    f"{source_name} topology read failed (not a missing resource): "
                    f"observed {marker!r}; resolve the API error before relying on "
                    "the separation check",
                )
            )

    return EnvironmentOutcome(not issues, issues)


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _cmd_separation(args: argparse.Namespace) -> int:
    prod = _read_json(args.prod)
    staging = _read_json(args.staging)
    outcome = separation_issues(prod, staging)
    _print_json({"valid": outcome.valid, "issues": outcome.issues})
    return 0 if outcome.valid else 1


def _cmd_inventory(args: argparse.Namespace) -> int:
    expected = _read_json(args.expected)
    observed = _read_json(args.observed)
    outcome = inventory_issues(expected, observed)
    _print_json({"valid": outcome.valid, "issues": outcome.issues})
    return 0 if outcome.valid else 1


def _cmd_topology(args: argparse.Namespace) -> int:
    prod = _read_json(args.prod)
    staging = _read_json(args.staging)
    outcome = topology_overlap_issues(prod, staging)
    _print_json({"valid": outcome.valid, "issues": outcome.issues})
    return 0 if outcome.valid else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.environments",
        description="Production/staging separation and production inventory consistency.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    separation = sub.add_parser(
        "separation", help="fail closed when prod and staging share a resource identifier"
    )
    separation.add_argument(
        "--prod", required=True, metavar="FILE", help="production identifiers JSON file"
    )
    separation.add_argument(
        "--staging", required=True, metavar="FILE", help="staging identifiers JSON file"
    )
    separation.set_defaults(func=_cmd_separation)

    inventory = sub.add_parser(
        "inventory", help="compare expected production identifiers to observed state"
    )
    inventory.add_argument(
        "--expected", required=True, metavar="FILE", help="expected identifiers JSON file"
    )
    inventory.add_argument(
        "--observed", required=True, metavar="FILE", help="observed inventory JSON file"
    )
    inventory.set_defaults(func=_cmd_inventory)

    topology = sub.add_parser(
        "topology", help="fail closed when prod and staging share VPC/namespace topology"
    )
    topology.add_argument(
        "--prod", required=True, metavar="FILE", help="production topology JSON file"
    )
    topology.add_argument(
        "--staging", required=True, metavar="FILE", help="staging topology JSON file"
    )
    topology.set_defaults(func=_cmd_topology)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
