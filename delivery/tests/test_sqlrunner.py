"""Tests for the one-off ECS SQL runner (OP-STG-02)."""

import base64

import pytest
from fakes_staging import (
    AUTH_SECRET_ARN,
    MASTER_SECRET_ARN,
    FakeEcs,
    FakeLogs,
    staging_identifiers,
)

from delivery.aws.context import AwsContext
from delivery.aws.sqlrunner import (
    SQL_OK_MARKER,
    SqlStep,
    build_runner_command,
    build_runner_task_definition,
    execute_sql_steps,
)
from delivery.errors import MutationVerificationError, ValidationError

CTX = AwsContext(region="eu-north-1", account_id="799111666795", environment="staging")


def test_sql_step_requires_verify_for_mutations():
    with pytest.raises(ValidationError):
        SqlStep(database="auth_staging", sql="DROP TABLE x;", secret_arn=MASTER_SECRET_ARN)


def test_sql_step_rejects_unsafe_database_names():
    with pytest.raises(ValidationError):
        SqlStep(
            database="auth_staging; DROP",
            sql="SELECT 1",
            verify_sql="SELECT 1",
            secret_arn=MASTER_SECRET_ARN,
        )


def test_sql_step_rejects_unsafe_env_names():
    with pytest.raises(ValidationError):
        SqlStep(
            database="postgres",
            sql="SELECT 1",
            verify_sql="SELECT 1",
            secret_arn=MASTER_SECRET_ARN,
            extra_secrets={"BAD-NAME": AUTH_SECRET_ARN},
        )


def test_sql_step_rejects_non_arn_secrets():
    with pytest.raises(ValidationError):
        SqlStep(
            database="postgres",
            sql="SELECT 1",
            verify_sql="SELECT 1",
            secret_arn=MASTER_SECRET_ARN,
            extra_secrets={"AUTH_PASSWORD": "plain"},
        )


def test_sql_step_negative_forbids_verify():
    with pytest.raises(ValidationError):
        SqlStep(
            database="postgres",
            sql="SELECT 1",
            verify_sql="SELECT 1",
            secret_arn=MASTER_SECRET_ARN,
            expect_success=False,
        )


def test_build_runner_command_never_contains_secret_values():
    command = build_runner_command(
        SqlStep(
            database="postgres",
            sql="SELECT 1;",
            verify_sql="SELECT 2;",
            secret_arn=MASTER_SECRET_ARN,
        ),
        db_host="db.internal",
        secret_arn=MASTER_SECRET_ARN,
        extra_secrets={"AUTH_PASSWORD": AUTH_SECRET_ARN},
    )
    # no secret ARN or value appears; only the env-var NAME is referenced
    assert MASTER_SECRET_ARN not in command
    assert AUTH_SECRET_ARN not in command
    assert '-v "AUTH_PASSWORD=$AUTH_PASSWORD"' in command
    assert SQL_OK_MARKER in command


def test_build_runner_command_encodes_sql_as_base64():
    command = build_runner_command(
        SqlStep(
            database="postgres",
            sql="SELECT 1;",
            verify_sql="SELECT 2;",
            secret_arn=MASTER_SECRET_ARN,
        ),
        db_host="db.internal",
        secret_arn=MASTER_SECRET_ARN,
        extra_secrets={},
    )
    main_b64 = base64.b64encode(b"SELECT 1;").decode()
    verify_b64 = base64.b64encode(b"SELECT 2;").decode()
    assert main_b64 in command
    assert verify_b64 in command


def test_build_runner_task_definition_uses_secret_references_only():
    td = build_runner_task_definition(
        staging_identifiers(),
        "eu-north-1",
        "echo ok",
        MASTER_SECRET_ARN,
        {"AUTH_PASSWORD": AUTH_SECRET_ARN},
    )
    container = td["containerDefinitions"][0]
    assert container["image"] == "postgres:18.1-alpine"
    assert td["networkMode"] == "awsvpc"
    secrets = {entry["name"]: entry["valueFrom"] for entry in container["secrets"]}
    assert secrets["PGPASSWORD"] == f"{MASTER_SECRET_ARN}:password::"
    assert secrets["AUTH_PASSWORD"] == f"{AUTH_SECRET_ARN}:password::"


class StepRunnerFakeEcs(FakeEcs):
    """FakeEcs where run_task tasks stop immediately with a configurable exit code."""

    def __init__(self, exit_code=0, digests=None, fail_describe=None):
        super().__init__(digests=digests, fail_describe=fail_describe)
        self.exit_code = exit_code
        self.run_tasks = []

    def run_task(self, **kwargs):
        self.run_tasks.append(kwargs)
        arn = f"arn:aws:ecs:eu-north-1:799111666795:task/sql-runner/{len(self.run_tasks)}"
        return {"tasks": [{"taskArn": arn}]}

    def describe_tasks(self, cluster, tasks):
        return {
            "tasks": [
                {
                    "taskArn": arn,
                    "lastStatus": "STOPPED",
                    "containers": [{"name": "sql", "exitCode": self.exit_code}],
                }
                for arn in tasks
            ]
        }


def _run_steps(monkeypatch, ecs_client, logs_client, steps, ids=None):
    monkeypatch.setattr(
        "delivery.aws.sqlrunner.client_for",
        lambda ctx, service: {"ecs": ecs_client, "logs": logs_client}[service],
    )
    return execute_sql_steps(CTX, ids or staging_identifiers(), steps, "db.internal")


def test_execute_sql_steps_happy_path_and_td_cleanup(monkeypatch):
    ecs = StepRunnerFakeEcs(exit_code=0)
    logs = FakeLogs()
    steps = [
        SqlStep(
            database="postgres",
            sql="SELECT 1;",
            verify_sql="SELECT 2;",
            secret_arn=MASTER_SECRET_ARN,
        )
    ]
    results = _run_steps(monkeypatch, ecs, logs, steps)
    assert len(results) == 1
    assert results[0]["exitCode"] == 0
    # every one-off TD is deregistered AND deleted
    assert len(ecs.register_calls) == 1
    assert len(ecs.deleted) == 1
    registered = ecs.register_calls[0]
    # no plaintext secrets anywhere in the registered TD
    secrets = registered["containerDefinitions"][0]["secrets"]
    assert all(s["valueFrom"].endswith("::") for s in secrets)
    assert "PGPASSWORD" in [s["name"] for s in secrets]
    # run_task used the staging cluster + awsvpc config
    run = ecs.run_tasks[0]
    assert run["cluster"] == "onlineshop-staging-cluster"
    assert run["launchType"] == "FARGATE"
    assert run["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == [
        "subnet-aaaa",
        "subnet-bbbb",
    ]


def test_execute_sql_steps_nonzero_exit_fails(monkeypatch):
    ecs = StepRunnerFakeEcs(exit_code=1)
    steps = [
        SqlStep(
            database="postgres",
            sql="SELECT 1;",
            verify_sql="SELECT 2;",
            secret_arn=MASTER_SECRET_ARN,
        )
    ]
    with pytest.raises(MutationVerificationError):
        _run_steps(monkeypatch, ecs, FakeLogs(), steps)
    # TD still cleaned up on failure
    assert len(ecs.deleted) == 1


def test_execute_sql_steps_negative_step_success_is_failure(monkeypatch):
    ecs = StepRunnerFakeEcs(exit_code=0)
    steps = [
        SqlStep(
            database="items_staging",
            sql="SELECT 1;",
            user="auth_app_staging",
            secret_arn=AUTH_SECRET_ARN,
            expect_success=False,
        )
    ]
    with pytest.raises(MutationVerificationError):
        _run_steps(monkeypatch, ecs, FakeLogs(), steps)


def test_execute_sql_steps_negative_step_failure_is_success(monkeypatch):
    ecs = StepRunnerFakeEcs(exit_code=3)
    steps = [
        SqlStep(
            database="items_staging",
            sql="SELECT 1;",
            user="auth_app_staging",
            secret_arn=AUTH_SECRET_ARN,
            expect_success=False,
        )
    ]
    results = _run_steps(monkeypatch, ecs, FakeLogs(), steps)
    assert results[0]["expectedSuccess"] is False
    assert results[0]["exitCode"] == 3


def test_execute_sql_steps_read_only_step_has_no_verify_part(monkeypatch):
    ecs = StepRunnerFakeEcs(exit_code=0)
    steps = [
        SqlStep(
            database="auth_staging",
            sql="SELECT count(*) FROM users;",
            user="auth_app_staging",
            secret_arn=AUTH_SECRET_ARN,
            read_only=True,
        )
    ]
    results = _run_steps(monkeypatch, ecs, FakeLogs(), steps)
    assert len(results) == 1
    command = ecs.register_calls[0]["containerDefinitions"][0]["command"][2]
    assert base64.b64encode(b"SELECT count(*) FROM users;").decode() in command


def test_execute_sql_steps_td_cleanup_failure_is_visible(monkeypatch):
    class BrokenCleanup(StepRunnerFakeEcs):
        def deregister_task_definition(self, taskDefinition):
            raise RuntimeError("boom")

    with pytest.raises(MutationVerificationError):
        _run_steps(
            monkeypatch,
            BrokenCleanup(exit_code=0),
            FakeLogs(),
            [
                SqlStep(
                    database="postgres",
                    sql="SELECT 1;",
                    verify_sql="SELECT 2;",
                    secret_arn=MASTER_SECRET_ARN,
                )
            ],
        )


def test_ensure_log_group_creates_and_reads_back():
    from delivery.aws.sqlrunner import ensure_log_group

    logs = FakeLogs()
    ensure_log_group(logs, "/ecs/onlineshop-staging-sql-runner")
    assert "/ecs/onlineshop-staging-sql-runner" in logs.groups


# ---------------------------------------------------------------------------
# OP-STG-02: the captured output must prove the observed count (F10)
# ---------------------------------------------------------------------------


class CountFakeLogs(FakeLogs):
    def __init__(self, messages=None):
        super().__init__()
        self.messages = messages

    def get_log_events(self, logGroupName, logStreamName, startFromHead=False):
        return {"events": [{"message": m} for m in (self.messages or [])]}


def _connectivity_step():
    return SqlStep(
        database="items_staging",
        sql="SELECT '=== COUNT seeded_items=5 ===' WHERE (SELECT count(*) FROM items) = 5;",
        user="items_app_staging",
        secret_arn=AUTH_SECRET_ARN,
        read_only=True,
        expect_output="=== COUNT seeded_items=5 ===",
    )


def test_execute_sql_steps_expected_output_match_passes(monkeypatch):
    ecs = StepRunnerFakeEcs(exit_code=0)
    logs = CountFakeLogs(messages=["=== COUNT seeded_items=5 ===\n", "=== SQL_OK ===\n"])
    results = _run_steps(monkeypatch, ecs, logs, [_connectivity_step()])
    assert len(results) == 1


def test_execute_sql_steps_wrong_count_fails_the_step(monkeypatch):
    ecs = StepRunnerFakeEcs(exit_code=0)
    # the SQL emitted the marker for 6 seeded items: exit 0 is not success
    logs = CountFakeLogs(messages=["=== COUNT seeded_items=6 ===\n", "=== SQL_OK ===\n"])
    with pytest.raises(MutationVerificationError):
        _run_steps(monkeypatch, ecs, logs, [_connectivity_step()])


def test_sql_step_negative_forbids_expect_output():
    with pytest.raises(ValidationError):
        SqlStep(
            database="postgres",
            sql="SELECT 1",
            secret_arn=MASTER_SECRET_ARN,
            expect_success=False,
            expect_output="=== X ===",
        )


# ---------------------------------------------------------------------------
# Bounded reset waits (F7): per-step cap and total phase cap
# ---------------------------------------------------------------------------


class NeverStoppingFakeEcs(StepRunnerFakeEcs):
    def describe_tasks(self, cluster, tasks):
        return {
            "tasks": [
                {
                    "taskArn": arn,
                    "lastStatus": "RUNNING",
                    "containers": [{"name": "sql", "exitCode": None}],
                }
                for arn in tasks
            ]
        }


def test_execute_sql_steps_per_step_wait_is_bounded(monkeypatch):
    monkeypatch.setattr("delivery.aws.sqlrunner.SQL_STEP_TIMEOUT", 0.6)
    from delivery.errors import WaiterTimeoutError

    with pytest.raises(WaiterTimeoutError):
        _run_steps(
            monkeypatch,
            NeverStoppingFakeEcs(exit_code=None),
            FakeLogs(),
            [
                SqlStep(
                    database="postgres",
                    sql="SELECT 1;",
                    verify_sql="SELECT 2;",
                    secret_arn=MASTER_SECRET_ARN,
                )
            ],
        )


def test_execute_sql_steps_total_phase_budget_is_enforced(monkeypatch):
    import time as time_module

    clock = {"now": time_module.monotonic()}
    monkeypatch.setattr("delivery.aws.sqlrunner.time.monotonic", lambda: clock["now"])
    from delivery.errors import WaiterTimeoutError

    ecs = StepRunnerFakeEcs(exit_code=0)
    original_run_task = ecs.run_task
    runs = {"count": 0}

    def run_task(**kwargs):
        runs["count"] += 1
        if runs["count"] >= 2:
            clock["now"] += 3600  # exhaust the phase budget between steps
        return original_run_task(**kwargs)

    ecs.run_task = run_task
    steps = [
        SqlStep(
            database="postgres",
            sql="SELECT 1;",
            verify_sql="SELECT 2;",
            secret_arn=MASTER_SECRET_ARN,
        ),
        SqlStep(
            database="postgres",
            sql="SELECT 3;",
            verify_sql="SELECT 4;",
            secret_arn=MASTER_SECRET_ARN,
        ),
    ]
    with pytest.raises(WaiterTimeoutError, match="phase exceeded"):
        _run_steps(monkeypatch, ecs, FakeLogs(), steps)
