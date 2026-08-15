"""ECS service, task definition, and deployment operations with read-back."""

from __future__ import annotations

import re
from typing import Any

from botocore.exceptions import ClientError

from ..errors import (
    AbsentResourceError,
    MutationVerificationError,
    ReadError,
    WaiterTimeoutError,
)
from .readback import absent_or_read
from .waiters import bounded_waiter

_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def describe_services(client: Any, cluster: str, services: list[str]) -> dict[str, dict]:
    """Describe services keyed by name, failing closed on any missing service."""
    try:
        response = client.describe_services(cluster=cluster, services=services)
    except ClientError as error:
        raise ReadError(f"describe_services failed for cluster {cluster}") from error
    observed = {service["serviceName"]: service for service in response.get("services") or []}
    missing = [name for name in services if name not in observed]
    if missing:
        raise ReadError(f"services missing from describe_services response: {missing}")
    return observed


def primary_deployment(observed: dict, service: str) -> dict:
    """Return the PRIMARY deployment of an observed service, failing closed."""
    for deployment in observed.get("deployments") or []:
        if deployment.get("status") == "PRIMARY":
            return deployment
    raise ReadError(f"no PRIMARY deployment for service {service}")


def service_deployment(client: Any, cluster: str, service: str) -> str:
    """Return the id of the PRIMARY deployment of a service."""
    observed = describe_services(client, cluster, [service])[service]
    deployment_id = primary_deployment(observed, service).get("id")
    if not deployment_id:
        raise ReadError(f"PRIMARY deployment of service {service} has no id")
    return deployment_id


def describe_task_definition(client: Any, task_definition_arn: str) -> dict:
    """Describe a task definition, absent only when it genuinely does not exist."""
    try:
        return client.describe_task_definition(taskDefinition=task_definition_arn)
    except ClientError as error:
        if absent_or_read(error):
            raise AbsentResourceError(f"task definition {task_definition_arn} not found") from error
        raise ReadError(f"describe_task_definition failed for {task_definition_arn}") from error


def register_task_definition(client: Any, td: dict) -> str:
    """Register a digest-pinned task definition and verify the read-back images."""
    response = client.register_task_definition(**td)
    registered = response.get("taskDefinition") or {}
    revision_arn = registered.get("taskDefinitionArn")
    if not revision_arn:
        raise ReadError("register_task_definition returned no taskDefinitionArn")
    registered_images = [
        container.get("image") for container in registered.get("containerDefinitions") or []
    ]
    observed = describe_task_definition(client, revision_arn)
    observed_images = [
        container.get("image")
        for container in (observed.get("taskDefinition") or {}).get("containerDefinitions") or []
    ]
    if registered_images != observed_images:
        raise MutationVerificationError(
            f"task definition {revision_arn} image digest read-back mismatch"
        )
    return revision_arn


def update_service(client: Any, cluster: str, service: str, task_definition_arn: str) -> dict:
    """Point a service at a task definition and verify the read-back."""
    client.update_service(
        cluster=cluster, service=service, taskDefinition=task_definition_arn
    )
    observed = describe_services(client, cluster, [service])[service]
    if observed.get("taskDefinition") != task_definition_arn:
        raise MutationVerificationError(f"service {service} taskDefinition read-back mismatch")
    return observed


def wait_for_deployment(
    client: Any,
    cluster: str,
    service: str,
    deployment_id: str,
    *,
    timeout_seconds: float,
    interval_seconds: float = 10,
) -> bool:
    """Wait for a deployment to become PRIMARY, failing fast on rollback."""

    def poll() -> bool:
        observed = describe_services(client, cluster, [service])[service]
        for deployment in observed.get("deployments") or []:
            if deployment.get("id") != deployment_id:
                continue
            status = deployment.get("status")
            if status == "PRIMARY":
                return True
            if status in {"ROLLED_BACK", "FAILED"}:
                raise WaiterTimeoutError(
                    f"deployment {deployment_id} for service {service} ended with {status}"
                )
        return False

    return bounded_waiter(
        poll,
        label=f"deployment {deployment_id} for service {service}",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def running_digests(client: Any, cluster: str, service: str) -> list[str]:
    """Return the sorted image digests of the running containers of a service."""
    try:
        listed = client.list_tasks(cluster=cluster, serviceName=service)
    except ClientError as error:
        raise ReadError(f"list_tasks failed for service {service}") from error
    task_arns = listed.get("taskArns") or []
    if not task_arns:
        raise ReadError(f"service {service} has no running tasks")
    try:
        response = client.describe_tasks(cluster=cluster, tasks=task_arns)
    except ClientError as error:
        if absent_or_read(error):
            raise AbsentResourceError(f"tasks not found for service {service}") from error
        raise ReadError(f"describe_tasks failed for service {service}") from error
    if response.get("failures"):
        raise ReadError(f"describe_tasks reported failures for service {service}")
    tasks = response.get("tasks") or []
    if len(tasks) != len(task_arns):
        raise ReadError(
            f"describe_tasks returned {len(tasks)} of {len(task_arns)} tasks "
            f"for service {service}"
        )
    digests: list[str] = []
    for task in tasks:
        containers = task.get("containers") or []
        if not containers:
            raise ReadError(f"task {task.get('taskArn')} has no containers")
        for container in containers:
            digest = container.get("imageDigest")
            if not digest:
                raise ReadError(
                    f"task {task.get('taskArn')} container {container.get('name')} "
                    "has no imageDigest"
                )
            if not _IMAGE_DIGEST.fullmatch(digest):
                raise ReadError(
                    f"task {task.get('taskArn')} container {container.get('name')} "
                    f"has malformed imageDigest {digest}"
                )
            digests.append(digest)
    return sorted(digests)
