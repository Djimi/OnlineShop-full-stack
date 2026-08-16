"""Tests for the desired Staging Deployer policy artifact (D9)."""

import json
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[1] / "staging-iam" / "staging-deploy-policy.json"

STAGING_DB_ARN = "arn:aws:rds:eu-north-1:799111666795:db:onlineshop-staging-postgres"
FRONTEND_BUCKET_PREFIX = "arn:aws:s3:::onlineshop-frontend-799111666795/_releases/*"
STAGING_CLUSTER_SERVICE = (
    "arn:aws:ecs:eu-north-1:799111666795:service/onlineshop-staging-cluster/onlineshop-*-staging"
)
STAGING_CLUSTER_ARN = "arn:aws:ecs:eu-north-1:799111666795:cluster/onlineshop-staging-cluster"
ECR_REPOSITORY_ARNS = [
    "arn:aws:ecr:eu-north-1:799111666795:repository/onlineshop-auth",
    "arn:aws:ecr:eu-north-1:799111666795:repository/onlineshop-items",
    "arn:aws:ecr:eu-north-1:799111666795:repository/onlineshop-api-gateway",
]
STAGING_TD_RESOURCES = [
    "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-*-staging-v2:*",
    "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-staging-sql-runner:*",
]
TD_SCOPED_ACTIONS = {
    "ecs:RegisterTaskDefinition",
    "ecs:DeleteTaskDefinitions",
}
TD_UNSCOPED_ACTIONS = {
    "ecs:DescribeTaskDefinition",
    "ecs:DeregisterTaskDefinition",
}


def _policy():
    return json.loads(POLICY_PATH.read_text())


def _actions(policy):
    actions = set()
    for statement in policy["Statement"]:
        action = statement.get("Action")
        if isinstance(action, str):
            actions.add(action)
        elif isinstance(action, list):
            actions.update(action)
    return actions


def test_policy_is_valid_json():
    policy = _policy()
    assert policy["Version"] == "2012-10-17"
    assert policy["Statement"]


def test_policy_grants_scoped_read_only_ecr():
    policy = _policy()
    actions = _actions(policy)
    assert "ecr:BatchGetImage" in actions
    assert "ecr:DescribeImages" in actions
    assert "ecr:PutImage" not in actions
    ecr_actions = {action for action in actions if action.startswith("ecr:")}
    assert ecr_actions == {"ecr:BatchGetImage", "ecr:DescribeImages"}
    for statement in policy["Statement"]:
        stmt_actions = statement.get("Action")
        if isinstance(stmt_actions, str):
            stmt_actions = [stmt_actions]
        if any(action.startswith("ecr:") for action in stmt_actions):
            assert set(statement["Resource"]) == set(ECR_REPOSITORY_ARNS)
            assert statement["Effect"] == "Allow"


def test_policy_never_grants_cloudfront_mutation():
    actions = _actions(_policy())
    assert not any(action.lower().startswith("cloudfront") for action in actions)


def test_policy_grants_persistent_staging_db_lifecycle():
    actions = _actions(_policy())
    for action in (
        "rds:StartDBInstance",
        "rds:StopDBInstance",
        "rds:ListTagsForResource",
        "rds:RemoveTagsFromResource",
    ):
        assert action in actions
    lifecycle = next(
        statement
        for statement in _policy()["Statement"]
        if statement["Sid"] == "PersistentStagingDatabaseLifecycle"
    )
    assert lifecycle["Resource"] == [STAGING_DB_ARN]


def test_policy_rds_lifecycle_actions_on_staging_db_only():
    policy = _policy()
    actions = _actions(policy)
    for action in (
        "rds:StartDBInstance",
        "rds:StopDBInstance",
        "rds:ListTagsForResource",
        "rds:AddTagsToResource",
        "rds:RemoveTagsFromResource",
    ):
        assert action in actions
    for statement in policy["Statement"]:
        stmt_actions = statement.get("Action")
        if isinstance(stmt_actions, str):
            stmt_actions = [stmt_actions]
        if any(
            action in {"rds:StartDBInstance", "rds:StopDBInstance", "rds:ListTagsForResource",
                       "rds:RemoveTagsFromResource"}
            for action in stmt_actions
        ):
            assert statement["Resource"] == [STAGING_DB_ARN]
        if "rds:AddTagsToResource" in stmt_actions:
            assert STAGING_DB_ARN in statement["Resource"]
            assert all("staging" in resource for resource in statement["Resource"])
    text = POLICY_PATH.read_text()
    assert "db:onlineshop-postgres-db" not in text


def test_policy_s3_read_only_on_frontend_bucket_prefix():
    s3 = next(
        statement
        for statement in _policy()["Statement"]
        if statement["Sid"] == "ReadPreviousOfficialFrontendBundle"
    )
    assert set(s3["Action"]) == {"s3:GetObject"}
    assert s3["Resource"] == [FRONTEND_BUCKET_PREFIX]
    assert s3["Effect"] == "Allow"


def test_policy_s3_never_writes_or_deletes():
    actions = _actions(_policy())
    s3_actions = {action for action in actions if action.startswith("s3:")}
    assert s3_actions == {"s3:GetObject"}
    assert not any(
        action.startswith(("s3:Put", "s3:Delete", "s3:Create")) for action in actions
    )


def test_policy_update_service_scoped_to_staging_cluster_only():
    update = next(
        statement
        for statement in _policy()["Statement"]
        if statement["Sid"] == "UpdateStagingServicesOnly"
    )
    assert update["Action"] == "ecs:UpdateService"
    assert update["Resource"] == STAGING_CLUSTER_SERVICE


def test_policy_run_task_scoped_to_staging_cluster_only():
    policy = _policy()
    run_statements = [
        statement
        for statement in policy["Statement"]
        if (isinstance(statement.get("Action"), str) and statement["Action"] == "ecs:RunTask")
        or (isinstance(statement.get("Action"), list) and "ecs:RunTask" in statement["Action"])
    ]
    assert len(run_statements) == 1
    statement = run_statements[0]
    assert statement["Resource"] == STAGING_CLUSTER_ARN
    assert statement["Effect"] == "Allow"
    assert "Condition" not in statement


def test_policy_scopes_supported_td_actions_to_staging_families():
    statement = next(
        entry
        for entry in _policy()["Statement"]
        if entry["Sid"] == "RegisterAndDeleteStagingTaskDefinitions"
    )
    assert set(statement["Action"]) == TD_SCOPED_ACTIONS
    assert set(statement["Resource"]) == set(STAGING_TD_RESOURCES)


def test_policy_wildcards_only_td_actions_without_resource_support():
    statement = next(
        entry
        for entry in _policy()["Statement"]
        if entry["Sid"] == "InspectAndDeregisterTaskDefinitionsWithoutResourceSupport"
    )
    assert set(statement["Action"]) == TD_UNSCOPED_ACTIONS
    assert statement["Resource"] == "*"
    assert TD_SCOPED_ACTIONS.isdisjoint(statement["Action"])


def test_policy_ecs_actions_never_touch_production_cluster():
    text = POLICY_PATH.read_text()
    assert "onlineshop-cluster" not in text
    for statement in _policy()["Statement"]:
        stmt_actions = statement.get("Action")
        if isinstance(stmt_actions, str):
            stmt_actions = [stmt_actions]
        if any(action.startswith("ecs:") for action in stmt_actions):
            resources = statement["Resource"]
            if resources == "*":
                assert set(stmt_actions) == TD_UNSCOPED_ACTIONS
                continue
            if isinstance(resources, str):
                resources = [resources]
            for resource in resources:
                assert "staging" in resource


def test_policy_pass_role_grants_carry_service_conditions():
    policy = _policy()
    statements = [
        statement
        for statement in policy["Statement"]
        if isinstance(statement.get("Action"), str) and statement["Action"] == "iam:PassRole"
    ]
    assert len(statements) == 2
    by_resource = {statement["Resource"]: statement for statement in statements}
    ecs_pass = by_resource["arn:aws:iam::799111666795:role/ecsTaskExecutionRole"]
    assert (
        ecs_pass["Condition"]["StringEquals"]["iam:PassedToService"]
        == "ecs-tasks.amazonaws.com"
    )
    rds_pass = by_resource["arn:aws:iam::799111666795:role/rds-monitoring-role"]
    assert rds_pass["Condition"]["StringEquals"]["iam:PassedToService"] == "rds.amazonaws.com"


def test_policy_never_grants_production_ecs_or_rds_mutation():
    text = POLICY_PATH.read_text()
    assert "onlineshop-cluster/service/onlineshop-auth" not in text
    assert "db:onlineshop-postgres-db" not in text
    update_resources = [
        statement["Resource"]
        for statement in _policy()["Statement"]
        if isinstance(statement.get("Action"), str) and statement["Action"] == "ecs:UpdateService"
    ]
    for resource in update_resources:
        assert "onlineshop-staging-cluster" in resource


def test_policy_iam_actions_are_only_scoped_pass_role():
    for statement in _policy()["Statement"]:
        action = statement.get("Action")
        if isinstance(action, str) and action.startswith("iam:"):
            assert action == "iam:PassRole"
            assert "iam:PassedToService" in statement.get("Condition", {}).get(
                "StringEquals", {}
            )
            assert statement["Resource"].startswith("arn:aws:iam::799111666795:role/")
        elif isinstance(action, list):
            assert not any(entry.startswith("iam:") for entry in action)
    actions = _actions(_policy())
    assert not any(action == "iam:*" or action.startswith("iam:Create") for action in actions)
    assert not any(
        action.startswith("iam:Delete") or action.startswith("iam:Put") for action in actions
    )


def test_policy_grants_log_reads_for_staging_log_groups():
    actions = _actions(_policy())
    assert "logs:GetLogEvents" in actions
    assert "logs:DescribeLogStreams" in actions
    # log read resources are staging-only
    for statement in _policy()["Statement"]:
        action = statement.get("Action")
        has_log_read = (
            (isinstance(action, str) and action in {"logs:GetLogEvents", "logs:DescribeLogStreams"})
            or (
                isinstance(action, list)
                and bool({"logs:GetLogEvents", "logs:DescribeLogStreams"} & set(action))
            )
        )
        if has_log_read:
            for resource in statement["Resource"]:
                assert "staging" in resource


def test_policy_grants_secrets_describe_only():
    for statement in _policy()["Statement"]:
        action = statement.get("Action")
        if isinstance(action, str) and action.startswith("secretsmanager:"):
            assert action == "secretsmanager:DescribeSecret" or statement["Sid"] in {
                "CreateStagingRdsManagedSecret",
            }
        elif isinstance(action, list):
            for entry in action:
                if entry.startswith("secretsmanager:"):
                    assert entry in {
                        "secretsmanager:DescribeSecret",
                        "secretsmanager:CreateSecret",
                        "secretsmanager:TagResource",
                    }


def test_policy_grants_ecs_run_and_td_cleanup():
    actions = _actions(_policy())
    for action in (
        "ecs:RegisterTaskDefinition",
        "ecs:DeregisterTaskDefinition",
        "ecs:DeleteTaskDefinitions",
        "ecs:RunTask",
        "ecs:DescribeTaskDefinition",
        "ecs:DescribeServices",
        "ecs:ListTasks",
        "ecs:DescribeTasks",
    ):
        assert action in actions
