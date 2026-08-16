"""Offline gates for the Production Deployer desired-state IAM policy and the
production identifiers file (AD-17 boundary 3, VR-SEC-01)."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "delivery" / "production-iam" / "production-deploy-policy.json"
IDENTIFIERS = REPO_ROOT / "scripts" / "config" / "production-identifiers.json"

ACCOUNT = "799111666795"
REGION = "eu-north-1"
ECR_ARNS = {
    f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/onlineshop-auth",
    f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/onlineshop-items",
    f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/onlineshop-api-gateway",
}
PRODUCTION_DB_ARN = f"arn:aws:rds:{REGION}:{ACCOUNT}:db:onlineshop-postgres-db"


def load_policy() -> dict:
    return json.loads(POLICY.read_text())


def all_actions(policy: dict) -> set[str]:
    actions: set[str] = set()
    for statement in policy["Statement"]:
        action = statement["Action"]
        if isinstance(action, str):
            actions.add(action)
        else:
            actions.update(action)
    return actions


def test_policy_is_valid_json_and_has_statements():
    policy = load_policy()
    assert policy["Version"] == "2012-10-17"
    assert policy["Statement"]


def test_policy_grants_ecr_read_on_the_three_repositories_only():
    policy = load_policy()
    statement = next(s for s in policy["Statement"] if s["Sid"] == "ProductionEcrRead")
    assert set(statement["Resource"]) == ECR_ARNS
    assert {"ecr:BatchGetImage", "ecr:DescribeImages"} <= set(statement["Action"])


def test_policy_grants_put_image_on_the_three_repositories_only():
    policy = load_policy()
    statement = next(
        s for s in policy["Statement"] if s["Sid"] == "ProductionEcrReleaseTagMinting"
    )
    assert statement["Action"] == "ecr:PutImage"
    assert set(statement["Resource"]) == ECR_ARNS


def test_policy_has_no_layer_upload_actions():
    forbidden = {
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetAuthorizationToken",
        "ecr:GetDownloadUrlForLayer",
    }
    assert not (all_actions(load_policy()) & forbidden)


def test_rds_describe_is_read_only_and_scoped_to_the_production_db():
    policy = load_policy()
    statement = next(
        s for s in policy["Statement"] if s["Sid"] == "DescribeProductionDatabaseReadOnly"
    )
    assert statement["Action"] == "rds:DescribeDBInstances"
    assert statement["Resource"] == PRODUCTION_DB_ARN


def test_policy_has_no_rds_mutation_actions():
    rds_actions = [action for action in all_actions(load_policy()) if action.startswith("rds:")]
    assert rds_actions == ["rds:DescribeDBInstances"]
    mutation_prefixes = (
        "rds:Create",
        "rds:Modify",
        "rds:Delete",
        "rds:Start",
        "rds:Stop",
        "rds:Reboot",
        "rds:Add",
        "rds:Remove",
        "rds:Copy",
        "rds:Restore",
    )
    assert not any(action.startswith(mutation_prefixes) for action in rds_actions)


def test_policy_has_no_staging_resources():
    text = POLICY.read_text()
    for forbidden in (
        "staging",
        "onlineshop-staging",
        "sql-runner",
    ):
        assert forbidden not in text


def test_policy_has_no_logs_or_secretsmanager_actions():
    actions = all_actions(load_policy())
    assert not any(
        action.startswith(("logs:", "secretsmanager:", "ec2:", "sns:")) for action in actions
    )


def test_pass_role_is_exact_roles_with_passed_to_service():
    policy = load_policy()
    statement = next(s for s in policy["Statement"] if s["Sid"] == "PassRoleToEcsOnly")
    assert statement["Action"] == "iam:PassRole"
    resources = set(statement["Resource"])
    assert resources == {
        f"arn:aws:iam::{ACCOUNT}:role/ecsTaskExecutionRole",
    }
    condition = statement["Condition"]["StringEquals"]
    assert condition["iam:PassedToService"] == "ecs-tasks.amazonaws.com"
    # PassRole is the ONLY iam action
    assert not any(
        action.startswith("iam:") for action in all_actions(policy) if action != "iam:PassRole"
    )


def test_mutating_actions_never_use_wildcard_resources():
    mutating = {"ecs:RegisterTaskDefinition", "ecs:UpdateService", "ecr:PutImage", "iam:PassRole"}
    for statement in load_policy()["Statement"]:
        actions = (
            {statement["Action"]}
            if isinstance(statement["Action"], str)
            else set(statement["Action"])
        )
        if actions & mutating:
            assert "*" not in statement["Resource"]


def test_ecs_actions_scoped_to_production_cluster_and_families():
    policy = load_policy()
    inspect = next(
        s for s in policy["Statement"] if s["Sid"] == "InspectProductionServicesAndTasks"
    )
    assert set(inspect["Resource"]) == {
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/onlineshop-cluster",
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/onlineshop-cluster/onlineshop-auth",
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/onlineshop-cluster/onlineshop-items",
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/onlineshop-cluster/onlineshop-api-gateway",
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/onlineshop-cluster/*",
    }
    register = next(
        s for s in policy["Statement"] if s["Sid"] == "RegisterProductionTaskDefinitions"
    )
    assert set(register["Resource"]) == {
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/onlineshop-auth:*",
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/onlineshop-items:*",
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/onlineshop-api-gateway:*",
    }
    update = next(s for s in policy["Statement"] if s["Sid"] == "UpdateProductionServicesOnly")
    assert set(update["Resource"]) == {
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/onlineshop-cluster/onlineshop-auth",
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/onlineshop-cluster/onlineshop-items",
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/onlineshop-cluster/onlineshop-api-gateway",
    }


def test_describe_tasks_is_granted_on_the_task_arn():
    """ECS authorizes DescribeTasks against the TASK ARN, not the cluster or
    service ARNs (running_digests reads in snapshot/deploy/verify)."""
    policy = load_policy()
    statement = next(
        s for s in policy["Statement"] if s["Sid"] == "InspectProductionServicesAndTasks"
    )
    assert "ecs:DescribeTasks" in statement["Action"]
    assert f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/onlineshop-cluster/*" in statement["Resource"]


def test_s3_scoped_to_frontend_bucket_only():
    policy = load_policy()
    statement = next(s for s in policy["Statement"] if s["Sid"] == "FrontendBucketAccess")
    assert set(statement["Resource"]) == {
        "arn:aws:s3:::onlineshop-frontend-799111666795",
        "arn:aws:s3:::onlineshop-frontend-799111666795/*",
    }


def test_cloudfront_scoped_to_production_distribution():
    policy = load_policy()
    statement = next(
        s for s in policy["Statement"] if s["Sid"] == "CloudFrontInvalidationAndInspection"
    )
    assert statement["Resource"] == f"arn:aws:cloudfront::{ACCOUNT}:distribution/EPS8MI3FV3B7X"


def test_elb_is_read_only():
    policy = load_policy()
    statement = next(
        s for s in policy["Statement"] if s["Sid"] == "ReadOnlyElbForVerificationJourneys"
    )
    assert all(
        action.startswith("elasticloadbalancing:Describe") for action in statement["Action"]
    )
    assert "onlineshop-gateway-tg" in statement["Resource"][1]


def test_production_identifiers_shape_and_values():
    ids = json.loads(IDENTIFIERS.read_text())
    assert ids["environment"] == "production"
    assert ids["accountId"] == ACCOUNT
    assert ids["cluster"] == "onlineshop-cluster"
    assert ids["services"] == [
        "onlineshop-auth",
        "onlineshop-items",
        "onlineshop-api-gateway",
    ]
    assert ids["ecrRepositories"] == {
        "auth": "onlineshop-auth",
        "items": "onlineshop-items",
        "gateway": "onlineshop-api-gateway",
    }
    assert ids["dbInstance"] == "onlineshop-postgres-db"
    assert ids["frontendBucket"] == "onlineshop-frontend-799111666795"
    assert ids["frontendLiveMarker"] == "release.json"
    assert ids["frontendReleasesPrefix"] == "_releases/"
    assert ids["cloudfrontDistributionId"] == "EPS8MI3FV3B7X"
    assert ids["albName"] == "onlineshop-alb"


def test_production_identifiers_match_production_env_reference():
    """The identifiers derive from scripts/config/production.env (read-only
    explicit non-secret reference); the values must agree."""
    env = (REPO_ROOT / "scripts" / "config" / "production.env").read_text()
    ids = json.loads(IDENTIFIERS.read_text())
    assert f'LC_CLUSTER="{ids["cluster"]}"' in env
    assert f'LC_DB_INSTANCE="{ids["dbInstance"]}"' in env
    assert f'LC_FRONTEND_BUCKET="{ids["frontendBucket"]}"' in env
    assert f'LC_CLOUDFRONT_DISTRIBUTION="{ids["cloudfrontDistributionId"]}"' in env
    assert f'LC_ALB_NAME="{ids["albName"]}"' in env
    for service in ids["services"]:
        assert f'"{service}"' in env


def test_policy_readme_documents_live_pass_and_tag_naming():
    readme = (REPO_ROOT / "delivery" / "production-iam" / "README.md").read_text()
    assert "github-actions-production" in readme
    assert "consolidated verification pass" in readme
    assert "PutImage" in readme
    assert "no condition key on the" in readme or "condition key" in readme


def test_policy_matches_role_layout_scope():
    layout = (
        REPO_ROOT / "plans" / "AUTOMATIC-BUILDS-AND-DEPLOY" / "github-actions-role-layout.md"
    ).read_text()
    assert "github-actions-production" in layout
    assert "ecsTaskExecutionRole" in layout
    assert "iam:PassedToService=ecs-tasks.amazonaws.com" in layout
    assert "CloudFront invalidation" in layout
