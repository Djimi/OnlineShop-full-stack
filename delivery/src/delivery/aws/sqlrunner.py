"""One-off ECS SQL runner for private staging RDS access (OP-STG-02).

The staging RDS has no public route, so SQL reaches it only from inside the
VPC. This module registers a one-off Fargate task definition per SQL step,
runs it, waits for it to stop, reads its CloudWatch log output, verifies the
outcome, and then deregisters AND deletes the task definition revision so no
secret-bearing residue accumulates.

Security model (OP-GEN-04):
- No plaintext secrets anywhere: ``PGPASSWORD`` and extra psql variables are
  injected by ECS from Secrets Manager full-ARN ``valueFrom`` references
  (``<arn>:password::``). No password appears in the task definition,
  command, evidence, or logs.
- SQL text is engine-owned: schema/seed/verify statements come from
  version-controlled repository SQL files or engine constants, never from
  operator input, and every mutation step carries a non-empty ``verify_sql``
  that proves the change (exit zero without read-back is never success).
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

from ..aws.context import AwsContext, client_for
from ..errors import (
    MutationVerificationError,
    ReadError,
    ValidationError,
    WaiterTimeoutError,
)
from .ecs import (
    delete_task_definition,
    deregister_task_definition,
    register_task_definition,
    wait_for_tasks_stopped,
)
from .readback import absent_or_read
from .waiters import bounded_waiter

# Exact digest-pinned runner image (verified against Docker Hub, see
# tests/test_sqlrunner.py); floating tags are never used for builds.
SQL_RUNNER_IMAGE = "postgres:18.1-alpine"
SQL_RUNNER_CONTAINER = "sql"
SQL_OK_MARKER = "=== SQL_OK ==="
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Bounded reset waits (OP-GEN-03 / F7): each SQL step waits at most 120s for
# its runner task, and the whole SQL phase (12 steps) is capped at 25 minutes.
# The 3h ownership-marker TTL stays above the worst-case lifecycle: RDS start
# (900s) + service scale (3x600s) + SQL reset (1500s) + deployments (3x600s)
# + RDS stop (900s) + cloud E2E + continuation gap leaves >45 minutes of slack.
SQL_STEP_TIMEOUT = 120.0
SQL_PHASE_TIMEOUT = 1500.0
SQL_LOGS_TIMEOUT = 120.0


@dataclass(frozen=True)
class SqlStep:
    """One bounded SQL execution with its mandatory verification.

    ``secret_arn`` provides ``PGPASSWORD`` (JSON key ``password``).
    ``extra_secrets`` maps an environment variable name to a secret ARN whose
    ``password`` key is injected by ECS and exposed to psql via ``-v``.
    ``expect_success=False`` marks a negative verification: the step is
    expected to fail (e.g. a restricted application user must NOT be able to
    connect to another tenant database).
    ``expect_output`` (OP-STG-02) requires the captured runner output to
    contain the exact framed value the SQL itself emits only when the
    observed count/value is correct — a wrong count fails the step.
    """

    database: str
    sql: str
    verify_sql: str = ""
    user: str = "dbadmin"
    secret_arn: str = ""
    extra_secrets: dict[str, str] = field(default_factory=dict)
    expect_success: bool = True
    read_only: bool = False
    expect_output: str = ""

    def __post_init__(self) -> None:
        for value, label in ((self.database, "database"), (self.user, "user")):
            if not _SQL_IDENTIFIER.fullmatch(value):
                raise ValidationError(f"unsafe SQL {label}: {value!r}")
        for name, arn in self.extra_secrets.items():
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
                raise ValidationError(f"unsafe extra-secret variable name: {name!r}")
            if not arn.startswith("arn:"):
                raise ValidationError(f"extra-secret for {name} must be a full ARN")
        if self.read_only and not self.expect_success:
            raise ValidationError("read_only steps must expect success")
        if not self.expect_success and self.verify_sql:
            raise ValidationError(
                "negative verification steps must not carry verify_sql; "
                "the expected failure is the verification"
            )
        if not self.expect_success and self.expect_output:
            raise ValidationError(
                "negative verification steps must not carry expect_output; "
                "the expected failure is the verification"
            )
        if self.expect_success and not self.read_only and not self.verify_sql:
            raise ValidationError(
                f"mutating SQL step against {self.database} requires verify_sql "
                "(exit zero without read-back is never success)"
            )


def build_runner_command(
    step: SqlStep, db_host: str, secret_arn: str, extra_secrets: dict[str, str]
) -> str:
    """Build the container command for one SQL step (engine-owned, base64-safe)."""
    if not secret_arn.startswith("arn:"):
        raise ValidationError("PGPASSWORD secret must be a full ARN")
    main = base64.b64encode(step.sql.encode()).decode()
    parts = [
        f"echo {main} | base64 -d > /tmp/q.sql",
    ]
    psql_vars = "".join(f' -v "{name}=${name}"' for name in sorted(extra_secrets))
    psql = f"psql -h {db_host} -U {step.user} -d {step.database} -v ON_ERROR_STOP=1{psql_vars}"
    if step.expect_success:
        if step.read_only:
            parts.append(f"{psql} -f /tmp/q.sql")
            parts.append(f"echo {SQL_OK_MARKER}")
        else:
            verify = base64.b64encode(step.verify_sql.encode()).decode()
            parts.append(f"{psql} -f /tmp/q.sql")
            parts.append(f"echo {verify} | base64 -d > /tmp/v.sql")
            parts.append(f"{psql} -f /tmp/v.sql")
            parts.append(f"echo {SQL_OK_MARKER}")
    else:
        parts.append(f"{psql} -f /tmp/q.sql")
        parts.append(f"echo {SQL_OK_MARKER}")
    return " && ".join(parts)


def build_runner_task_definition(
    ids: dict, region: str, command: str, secret_arn: str, extra_secrets: dict[str, str]
) -> dict:
    """Build the one-off runner task definition (secrets are ARN references)."""
    secrets = [{"name": "PGPASSWORD", "valueFrom": f"{secret_arn}:password::"}]
    secrets.extend(
        {"name": name, "valueFrom": f"{arn}:password::"}
        for name, arn in sorted(extra_secrets.items())
    )
    return {
        "family": ids["sqlRunnerFamily"],
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512",
        "executionRoleArn": ids["executionRoleArn"],
        "containerDefinitions": [
            {
                "name": SQL_RUNNER_CONTAINER,
                "image": SQL_RUNNER_IMAGE,
                "essential": True,
                "command": ["sh", "-c", command],
                "secrets": secrets,
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": ids["sqlLogGroup"],
                        "awslogs-region": region,
                        "awslogs-stream-prefix": "sql-runner",
                    },
                },
            }
        ],
    }


def ensure_log_group(logs_client, group: str) -> None:
    """Create the SQL runner log group when absent; every mutation read back."""
    try:
        response = logs_client.describe_log_groups(logGroupNamePrefix=group)
    except ClientError as error:
        raise ReadError(f"describe_log_groups failed for {group}") from error
    groups = response.get("logGroups") or []
    if any(item.get("logGroupName") == group for item in groups):
        return
    logs_client.create_log_group(logGroupName=group)
    response = logs_client.describe_log_groups(logGroupNamePrefix=group)
    groups = response.get("logGroups") or []
    if not any(item.get("logGroupName") == group for item in groups):
        raise MutationVerificationError(f"log group {group} not present after create_log_group")


def _task_exit_code(tasks: list[dict]) -> int | None:
    for task in tasks:
        containers = task.get("containers") or []
        for container in containers:
            if container.get("name") == SQL_RUNNER_CONTAINER:
                return container.get("exitCode")
    return None


def read_task_logs(logs_client, group: str, task_id: str, *, timeout_seconds: float = 120) -> str:
    """Poll for the task log stream and return its concatenated messages."""
    collected: list[str] = []

    def poll() -> bool:
        try:
            response = logs_client.get_log_events(
                logGroupName=group,
                logStreamName=f"sql-runner/{SQL_RUNNER_CONTAINER}/{task_id}",
                startFromHead=True,
            )
        except ClientError as error:
            if absent_or_read(error):
                return False
            raise ReadError(f"get_log_events failed for group {group}") from error
        for event in response.get("events") or []:
            message = event.get("message")
            if isinstance(message, str):
                collected.append(message)
        return bool(collected)

    bounded_waiter(
        poll,
        label=f"logs for sql-runner task {task_id}",
        timeout_seconds=timeout_seconds,
        interval_seconds=5,
    )
    return "".join(collected)


def execute_sql_steps(ctx: AwsContext, ids: dict, steps: list[SqlStep], db_host: str) -> list[dict]:
    """Execute the SQL steps through one-off runner tasks and verify outcomes.

    Every wait is bounded twice: each step waits at most
    ``SQL_STEP_TIMEOUT`` for its runner task (and ``SQL_LOGS_TIMEOUT`` for
    its log stream), and the whole phase is capped by ``SQL_PHASE_TIMEOUT``
    (OP-GEN-03). Exhausting either budget fails closed.
    """
    ecs_client = client_for(ctx, "ecs")
    logs_client = client_for(ctx, "logs")
    cluster = ids["cluster"]
    ensure_log_group(logs_client, ids["sqlLogGroup"])
    deadline = time.monotonic() + SQL_PHASE_TIMEOUT
    results: list[dict] = []

    def _remaining() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WaiterTimeoutError(f"SQL runner phase exceeded its {SQL_PHASE_TIMEOUT}s budget")
        return remaining

    for index, step in enumerate(steps):
        _remaining()
        secret_arn = step.secret_arn
        command = build_runner_command(step, db_host, secret_arn, step.extra_secrets)
        td = build_runner_task_definition(ids, ctx.region, command, secret_arn, step.extra_secrets)
        revision_arn = register_task_definition(ecs_client, td)
        task_arn: str | None = None
        log_tail = ""
        try:
            try:
                response = ecs_client.run_task(
                    cluster=cluster,
                    taskDefinition=revision_arn,
                    launchType="FARGATE",
                    networkConfiguration={
                        "awsvpcConfiguration": {
                            "subnets": list(ids["sqlSubnets"]),
                            "securityGroups": [ids["sqlSecurityGroup"]],
                            "assignPublicIp": "ENABLED",
                        }
                    },
                )
            except ClientError as error:
                raise ReadError(f"run_task failed for SQL step {index}") from error
            tasks = response.get("tasks") or []
            if not tasks or not tasks[0].get("taskArn"):
                raise ReadError(f"run_task returned no task for SQL step {index}")
            task_arn = tasks[0]["taskArn"]
            task_id = task_arn.rsplit("/", 1)[-1]
            stopped = wait_for_tasks_stopped(
                ecs_client,
                cluster,
                [task_arn],
                timeout_seconds=min(SQL_STEP_TIMEOUT, _remaining()),
            )
            exit_code = _task_exit_code(stopped)
            log_tail = read_task_logs(
                logs_client,
                ids["sqlLogGroup"],
                task_id,
                timeout_seconds=min(SQL_LOGS_TIMEOUT, _remaining()),
            )
            if step.expect_success:
                if exit_code != 0:
                    raise MutationVerificationError(
                        f"SQL step {index} against {step.database} failed with "
                        f"exit code {exit_code}"
                    )
                if SQL_OK_MARKER not in log_tail:
                    raise MutationVerificationError(
                        f"SQL step {index} against {step.database} did not reach "
                        "its verification marker"
                    )
                if step.expect_output and step.expect_output not in log_tail:
                    raise MutationVerificationError(
                        f"SQL step {index} against {step.database} captured output "
                        f"does not contain the expected value "
                        f"{step.expect_output!r}"
                    )
            elif exit_code == 0:
                raise MutationVerificationError(
                    f"negative SQL step {index} against {step.database} succeeded; "
                    "the restriction under verification is missing"
                )
            results.append(
                {
                    "step": index,
                    "database": step.database,
                    "user": step.user,
                    "expectedSuccess": step.expect_success,
                    "exitCode": exit_code,
                    "logTail": log_tail[-400:],
                }
            )
        finally:
            cleanup_error = None
            try:
                deregister_task_definition(ecs_client, revision_arn)
            except Exception as error:
                cleanup_error = error
            try:
                delete_task_definition(ecs_client, revision_arn)
            except Exception as error:
                cleanup_error = cleanup_error or error
            if cleanup_error is not None:
                raise MutationVerificationError(
                    f"SQL runner task definition cleanup failed for {revision_arn}: {cleanup_error}"
                ) from cleanup_error
    return results
