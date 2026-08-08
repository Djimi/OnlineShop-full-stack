"""IAM least-privilege and OIDC trust policy validation (Pass 3, subphase 3.3).

GitHub Actions AWS access is split by job purpose. The offline gate validates
the source-controlled policy documents (``plans/AUTOMATIC-BUILDS-AND-DEPLOY/
github-actions-*-policy.json``) before they are ever applied live:

- ECR actions are scoped to the three backend repository ARNs; the only ECR
  action allowed on ``Resource: "*"`` is the inherently unscopable
  ``ecr:GetAuthorizationToken`` (``BatchGetImage`` and
  ``GetDownloadUrlForLayer`` support the ``repository`` resource type, so they
  are scoped to the three backend ARNs too).
- ``iam:PassRole`` is limited to the ECS execution/task roles and only when
  passed to ``ecs-tasks.amazonaws.com``.
- Mutating actions must never carry ``Resource: "*"``.

The OIDC trust policy must require the ``sts.amazonaws.com`` audience and scope
subjects to the ``main``/``feature/*`` refs plus the exact protected
``environment:production`` subject used by the production job. The exact
subject strings must be confirmed against a real run's JWT before the trust
policy is applied live (never guessed); the gate only checks that the policy
contains the expected immutable-format subjects.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from . import components as rc

# --- Project identity (mirrors scripts/config/production.env + executed/INFO.md) ---
ACCOUNT_ID = "799111666795"
REGION = "eu-north-1"
# Immutable GitHub OIDC subject base (owner@owner-id/repo@repo-id). The exact
# `sub` is validated live from a real job's JWT; the gate only checks structure.
OIDC_SUBJECT_BASE = "Djimi@8793507/OnlineShop-full-stack@1097550215"
OIDC_PROVIDER = "token.actions.githubusercontent.com"
OIDC_PROVIDER_ARN = f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/{OIDC_PROVIDER}"
STS_AUD = "sts.amazonaws.com"
PRODUCTION_ENVIRONMENT = "production"

# ECR repositories and their least-privilege ARN scope.
ECR_REPOSITORIES = list(rc.REPOSITORIES.values())
ECR_REPOSITORY_ARNS = rc.ecr_repository_arns(REGION, ACCOUNT_ID)

# iam:PassRole is allowed only for ECS service principals and only to these
# roles. The production task role ARN (if the production task definitions carry
# one) must be confirmed during subphase 3.5 hardening and added before apply.
ECS_EXECUTION_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/ecsTaskExecutionRole"
PASS_ROLE_ALLOWED = (ECS_EXECUTION_ROLE_ARN,)

# Actions that may only ever target the three backend repository ARNs.
ECR_REPO_SCOPED_ACTIONS = {
    "ecr:BatchCheckLayerAvailability",
    "ecr:BatchGetImage",
    "ecr:CompleteLayerUpload",
    "ecr:DescribeImages",
    "ecr:DescribeRepositories",
    "ecr:GetDownloadUrlForLayer",
    "ecr:InitiateLayerUpload",
    "ecr:ListImages",
    "ecr:PutImage",
    "ecr:UploadLayerPart",
}

# The only ECR action inherently unscopable by repository.
ECR_GLOBAL_ACTIONS = {"ecr:GetAuthorizationToken"}

# Promotion/rollback tag-minting must never upload new layers; these actions
# belong to the candidate-build role only.
ECR_LAYER_UPLOAD_ACTIONS = {
    "ecr:BatchCheckLayerAvailability",
    "ecr:CompleteLayerUpload",
    "ecr:InitiateLayerUpload",
    "ecr:UploadLayerPart",
}

# Actions that must never be granted with Resource: "*".
MUTATING_ACTIONS = {
    "ecr:PutImage",
    "ecs:RegisterTaskDefinition",
    "ecs:UpdateService",
    "ecs:RunTask",
    "ecs:DeleteTaskDefinitions",
    "ecs:DeregisterTaskDefinition",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:PutBucketPolicy",
    "cloudfront:CreateInvalidation",
    "rds:CreateDBInstance",
    "rds:DeleteDBInstance",
}


@dataclass
class ValidationOutcome:
    """Result of validating one IAM policy or trust document."""

    valid: bool
    issues: list[dict[str, str]] = field(default_factory=list)


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _normalize_entries(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _statement_actions(statement: dict[str, Any]) -> set[str]:
    return set(_normalize_entries(statement.get("Action")))


def _statement_resources(statement: dict[str, Any]) -> list[str]:
    return _normalize_entries(statement.get("Resource"))


def _is_mutation_action(action: str) -> bool:
    return action in MUTATING_ACTIONS


def iam_policy_issues(policy: Any) -> ValidationOutcome:
    """Structural + least-privilege invariants for a project IAM policy."""
    issues: list[dict[str, str]] = []
    if not isinstance(policy, dict) or not isinstance(policy.get("Statement"), list):
        return ValidationOutcome(
            False,
            [_issue("INVALID_POLICY", "$", "policy must be an object with a Statement array")],
        )

    for index, statement in enumerate(policy["Statement"]):
        prefix = f"Statement[{index}]"
        if not isinstance(statement, dict):
            issues.append(_issue("INVALID_STATEMENT", prefix, "statement must be an object"))
            continue
        effect = statement.get("Effect")
        if effect not in ("Allow", "Deny"):
            issues.append(
                _issue("INVALID_EFFECT", f"{prefix}.Effect", f"invalid Effect {effect!r}")
            )
        actions = _statement_actions(statement)
        resources = _statement_resources(statement)
        if not actions:
            issues.append(_issue("MISSING_ACTION", f"{prefix}.Action", "statement has no actions"))
        if not resources:
            issues.append(
                _issue("MISSING_RESOURCE", f"{prefix}.Resource", "statement has no resources")
            )

        for action in sorted(actions):
            if not isinstance(action, str) or ":" not in action:
                issues.append(
                    _issue("INVALID_ACTION", f"{prefix}.Action", f"invalid action {action!r}")
                )
                continue
            if action in ECR_GLOBAL_ACTIONS:
                if "*" not in resources:
                    issues.append(
                        _issue(
                            "GLOBAL_ACTION_SCOPED",
                            f"{prefix}.Resource",
                            f'{action} is inherently unscopable and must use Resource: ["*"]',
                        )
                    )
                continue
            if action.startswith("ecr:"):
                if "*" in resources:
                    issues.append(
                        _issue(
                            "BROAD_ECR_RESOURCE",
                            f"{prefix}.Resource",
                            f"{action} must be scoped to the backend repository ARNs, not '*'",
                        )
                    )
                else:
                    unknown = [r for r in resources if r not in ECR_REPOSITORY_ARNS]
                    if unknown:
                        issues.append(
                            _issue(
                                "ECR_RESOURCE_OUT_OF_SCOPE",
                                f"{prefix}.Resource",
                                f"{action} targets non-backend resources: {unknown!r}",
                            )
                        )
            if action == "iam:PassRole":
                if any(r == "*" for r in resources) or not resources:
                    issues.append(
                        _issue(
                            "PASSROLE_UNSCOPED",
                            f"{prefix}.Resource",
                            "iam:PassRole must target specific role ARNs, not '*'",
                        )
                    )
                else:
                    unknown = [r for r in resources if r not in PASS_ROLE_ALLOWED]
                    if unknown:
                        issues.append(
                            _issue(
                                "PASSROLE_OUT_OF_SCOPE",
                                f"{prefix}.Resource",
                                f"iam:PassRole targets unexpected roles: {unknown!r}",
                            )
                        )
                condition = statement.get("Condition") or {}
                passed_to = None
                for op in condition.values():
                    if isinstance(op, dict) and "iam:PassedToService" in op:
                        passed_to = op["iam:PassedToService"]
                if passed_to != "ecs-tasks.amazonaws.com":
                    issues.append(
                        _issue(
                            "PASSROLE_NO_SERVICE_CONDITION",
                            f"{prefix}.Condition",
                            "iam:PassRole requires StringEquals "
                            "iam:PassedToService=ecs-tasks.amazonaws.com",
                        )
                    )
            if _is_mutation_action(action) and "*" in resources:
                issues.append(
                    _issue(
                        "MUTATION_ON_WILDCARD",
                        f"{prefix}.Resource",
                        f"{action} is a mutating action and must not use Resource: '*'",
                    )
                )

    return ValidationOutcome(not issues, issues)


def trust_policy_issues(policy: Any) -> ValidationOutcome:
    """OIDC trust invariants: aud, provider, and expected subject set."""
    issues: list[dict[str, str]] = []
    if not isinstance(policy, dict) or not isinstance(policy.get("Statement"), list):
        return ValidationOutcome(
            False,
            [
                _issue(
                    "INVALID_POLICY",
                    "$",
                    "trust policy must be an object with a Statement array",
                )
            ],
        )

    subjects: set[str] = set()
    has_sts_assume = False
    aud_seen = False
    provider_ok = True
    for statement in policy["Statement"]:
        if not isinstance(statement, dict):
            continue
        actions = _statement_actions(statement)
        if "sts:AssumeRoleWithWebIdentity" in actions:
            has_sts_assume = True
        principal = statement.get("Principal")
        federated = principal.get("Federated") if isinstance(principal, dict) else None
        if federated is not None and federated != OIDC_PROVIDER_ARN:
            provider_ok = False
        condition = statement.get("Condition") or {}
        for _op, values in condition.items():
            if isinstance(values, dict):
                if "token.actions.githubusercontent.com:aud" in values:
                    aud_seen = True
                    aud = values["token.actions.githubusercontent.com:aud"]
                    auds = {aud} if isinstance(aud, str) else set(_normalize_entries(aud))
                    if STS_AUD not in auds:
                        issues.append(
                            _issue(
                                "OIDC_AUD_MISMATCH",
                                "Condition",
                                f"audience {auds!r} does not include {STS_AUD!r}",
                            )
                        )
                if "token.actions.githubusercontent.com:sub" in values:
                    sub = values["token.actions.githubusercontent.com:sub"]
                    if isinstance(sub, str):
                        subjects.add(sub)
                    else:
                        subjects.update(_normalize_entries(sub))

    if not has_sts_assume:
        issues.append(
            _issue(
                "MISSING_STS_ASSUME",
                "Statement",
                "no sts:AssumeRoleWithWebIdentity action found",
            )
        )
    if not aud_seen:
        issues.append(
            _issue(
                "OIDC_AUD_MISMATCH",
                "Condition",
                "trust policy must require "
                "token.actions.githubusercontent.com:aud = sts.amazonaws.com",
            )
        )
    if not provider_ok:
        issues.append(
            _issue(
                "WRONG_PROVIDER",
                "Principal",
                f"Federated principal must be {OIDC_PROVIDER_ARN}",
            )
        )

    base = f"repo:{OIDC_SUBJECT_BASE}"
    required = {
        f"{base}:ref:refs/heads/main",
        f"{base}:ref:refs/heads/feature/*",
        f"{base}:environment:{PRODUCTION_ENVIRONMENT}",
    }
    for subject in sorted(required - subjects):
        issues.append(
            _issue(
                "MISSING_SUBJECT",
                "Condition",
                f"trust policy is missing the subject {subject!r}",
            )
        )

    return ValidationOutcome(not issues, issues)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _cmd_validate_policy(args: argparse.Namespace) -> int:
    with open(args.policy, encoding="utf-8") as handle:
        policy = json.load(handle)
    outcome = iam_policy_issues(policy)
    _print_json({"valid": outcome.valid, "issues": outcome.issues})
    return 0 if outcome.valid else 1


def _cmd_validate_trust(args: argparse.Namespace) -> int:
    with open(args.policy, encoding="utf-8") as handle:
        policy = json.load(handle)
    outcome = trust_policy_issues(policy)
    _print_json({"valid": outcome.valid, "issues": outcome.issues})
    return 0 if outcome.valid else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.iam",
        description="Least-privilege IAM policy and OIDC trust validation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    policy = sub.add_parser("validate-policy", help="validate an IAM policy document")
    policy.add_argument("--policy", required=True, metavar="FILE", help="IAM policy JSON file")
    policy.set_defaults(func=_cmd_validate_policy)

    trust = sub.add_parser("validate-trust", help="validate an OIDC trust policy document")
    trust.add_argument("--policy", required=True, metavar="FILE", help="trust policy JSON file")
    trust.set_defaults(func=_cmd_validate_trust)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
