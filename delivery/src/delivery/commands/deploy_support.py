"""Shared ECS deployment helpers for the forward deploy and backward recover
commands (OP-DEP-02): digest-pinned registration verification, secret-safety
read-back, deployment visibility, and bounded running-digest verification.

Extracted from ``deploy`` in Phase 6 so ``recover`` reuses the exact same
mechanisms instead of duplicating them. ``deploy`` keeps thin module-level
wrappers with its historical names so its callers and tests stay unchanged.
"""

from __future__ import annotations

import re
import time

from ..aws import describe_services, describe_task_definition
from ..aws.ecs import wait_for_running_digests
from ..errors import MutationVerificationError, ReadError

DEPLOYMENT_TIMEOUT = 600
DIGEST_VERIFY_TIMEOUT = 120.0
DIGEST_VERIFY_INTERVAL = 10.0
DEPLOYMENT_VISIBILITY_RETRIES = 3
DEPLOYMENT_VISIBILITY_DELAY = 1.0

_FULL_SECRET_ARN = re.compile(
    r"^arn:aws:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@:-]+$"
)


def assert_full_arn_secrets(ecs_client, revision_arn: str) -> None:
    """Secrets must stay full-ARN ``secrets[].valueFrom`` references (never plaintext)."""
    observed = describe_task_definition(ecs_client, revision_arn)["taskDefinition"]
    for container in observed.get("containerDefinitions") or []:
        for secret in container.get("secrets") or []:
            value_from = secret.get("valueFrom")
            if not isinstance(value_from, str) or not _FULL_SECRET_ARN.fullmatch(value_from):
                raise MutationVerificationError(
                    f"task definition {revision_arn} container {container.get('name')} "
                    f"carries a non-full-ARN secrets[].valueFrom: {value_from!r}"
                )


def deployment_for_revision(
    ecs_client,
    cluster: str,
    service: str,
    revision_arn: str,
    *,
    retries: int = DEPLOYMENT_VISIBILITY_RETRIES,
    delay_seconds: float = DEPLOYMENT_VISIBILITY_DELAY,
) -> str:
    """Return the deployment id for a just-registered revision (bounded retries)."""
    for attempt in range(1, retries + 1):
        observed = describe_services(ecs_client, cluster, [service])[service]
        for deployment in observed.get("deployments") or []:
            if deployment.get("taskDefinition") == revision_arn:
                deployment_id = deployment.get("id")
                if not deployment_id:
                    raise ReadError(f"deployment for {revision_arn} of {service} has no id")
                return deployment_id
        if attempt < retries:
            time.sleep(delay_seconds)
    raise ReadError(
        f"service {service} has no deployment for the just-registered revision "
        f"{revision_arn} after {retries} attempts; deployment visibility did not converge"
    )


def verify_running_digests(
    ecs_client,
    cluster: str,
    service: str,
    expected: str,
    *,
    timeout_seconds: float = DIGEST_VERIFY_TIMEOUT,
    interval_seconds: float = DIGEST_VERIFY_INTERVAL,
) -> None:
    """Wait until every running container digest equals ``expected`` (bounded)."""
    wait_for_running_digests(
        ecs_client,
        cluster,
        service,
        expected,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )
