"""ECS service, task definition, and deployment operations with read-back."""

from __future__ import annotations

import json
import re
from typing import Any

from botocore.exceptions import ClientError

from ..errors import (
    AbsentResourceError,
    MutationVerificationError,
    ReadError,
    ValidationError,
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
    """Return running application digests, excluding ECS-managed runtime proxies."""
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
        task_digests = []
        for container in containers:
            name = container.get("name") or ""
            if name.startswith("ecs-service-connect-"):
                continue
            digest = container.get("imageDigest")
            if not digest:
                raise ReadError(
                    f"task {task.get('taskArn')} container {name} "
                    "has no imageDigest"
                )
            if not _IMAGE_DIGEST.fullmatch(digest):
                raise ReadError(
                    f"task {task.get('taskArn')} container {name} "
                    f"has malformed imageDigest {digest}"
                )
            task_digests.append(digest)
        if not task_digests:
            raise ReadError(f"task {task.get('taskArn')} has no application containers")
        digests.extend(task_digests)
    return sorted(digests)


def wait_for_running_digests(
    client: Any,
    cluster: str,
    service: str,
    expected: str,
    *,
    timeout_seconds: float,
    interval_seconds: float = 10,
) -> None:
    """Wait until every running container digest equals ``expected``.

    OP-DEP-02: after the deployment-bound waiter completes, a min-100% /
    max-200% rolling deployment can transiently overlap tasks (the new
    PRIMARY plus an old draining task). The overlap is tolerated only while
    it drains: the accepted end state is exactly ``{expected}`` — a digest
    other than the expected one is never accepted, and the wait fails
    closed when the set does not converge before the bound.
    """
    observed: list[str] = []

    def poll() -> bool:
        digests = running_digests(client, cluster, service)
        observed.clear()
        observed.extend(sorted(set(digests)))
        return set(digests) == {expected}

    try:
        bounded_waiter(
            poll,
            label=f"running digests == {{{expected}}} for service {service}",
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
        )
    except WaiterTimeoutError as error:
        raise MutationVerificationError(
            f"service {service} running digests {observed} did not converge to "
            f"{{{expected}}} before the bound"
        ) from error


def scale_service(client: Any, cluster: str, service: str, desired_count: int) -> dict:
    """Set the desiredCount of a service and verify the read-back."""
    if not isinstance(desired_count, int) or desired_count < 0:
        raise ValidationError(
            f"desired_count must be a non-negative integer, got {desired_count!r}"
        )
    client.update_service(cluster=cluster, service=service, desiredCount=desired_count)
    observed = describe_services(client, cluster, [service])[service]
    if observed.get("desiredCount") != desired_count:
        raise MutationVerificationError(
            f"service {service} desiredCount read-back mismatch: "
            f"expected {desired_count}, observed {observed.get('desiredCount')!r}"
        )
    return observed


def wait_for_service_running_count(
    client: Any,
    cluster: str,
    service: str,
    expected_count: int,
    *,
    timeout_seconds: float,
    interval_seconds: float = 10,
) -> bool:
    """Wait until the service runningCount equals the expected value."""

    def poll() -> bool:
        observed = describe_services(client, cluster, [service])[service]
        return observed.get("runningCount") == expected_count

    return bounded_waiter(
        poll,
        label=f"runningCount {expected_count} for service {service}",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def wait_for_tasks_stopped(
    client: Any,
    cluster: str,
    task_arns: list[str],
    *,
    timeout_seconds: float,
    interval_seconds: float = 10,
) -> list[dict]:
    """Wait until every task reports lastStatus STOPPED and return the tasks."""
    stopped: list[dict] = []

    def poll() -> bool:
        try:
            response = client.describe_tasks(cluster=cluster, tasks=task_arns)
        except ClientError as error:
            if absent_or_read(error):
                raise AbsentResourceError(
                    f"tasks not found while waiting: {', '.join(task_arns)}"
                ) from error
            raise ReadError(f"describe_tasks failed while waiting for {task_arns}") from error
        tasks = response.get("tasks") or []
        if len(tasks) != len(task_arns):
            raise ReadError(
                f"describe_tasks returned {len(tasks)} of {len(task_arns)} tasks"
            )
        for task in tasks:
            if task.get("lastStatus") != "STOPPED":
                return False
        stopped.extend(tasks)
        return True

    bounded_waiter(
        poll,
        label=f"tasks stopped: {', '.join(task_arns)}",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )
    return stopped


_TD_READ_ONLY_KEYS = frozenset(
    {
        "taskDefinitionArn",
        "revision",
        "status",
        "requiresAttributes",
        "compatibilities",
        "registeredAt",
        "registeredBy",
        "deregisteredAt",
    }
)


def sanitize_task_definition(td: dict) -> dict:
    """Return a register-able copy of an observed task definition.

    Read-only describe output fields are dropped; everything else (including
    secrets[].valueFrom references) is preserved byte for byte so the diff
    against the observed definition proves an image-only change.
    """
    body = {key: value for key, value in td.items() if key not in _TD_READ_ONLY_KEYS}
    containers = body.get("containerDefinitions")
    if not isinstance(containers, list) or not containers:
        raise ReadError("task definition has no containerDefinitions")
    body["containerDefinitions"] = [
        {
            key: value
            for key, value in container.items()
            if key not in {"imageDigest", "containerArn"}
        }
        for container in containers
    ]
    return body


def task_definition_images(td: dict) -> dict[str, str]:
    """Map container name to image for every container of a task definition."""
    containers = td.get("containerDefinitions") or []
    images = {}
    for container in containers:
        name = container.get("name")
        image = container.get("image")
        if not isinstance(name, str) or not name or not isinstance(image, str) or not image:
            raise ReadError("task definition container is missing name or image")
        images[name] = image
    return images


def replace_container_images(td: dict, images: dict[str, str]) -> dict:
    """Return a sanitized task definition with container images replaced.

    Fails closed when a requested container does not exist or the resulting
    definition differs from the original in anything but image fields.
    """
    if not images:
        raise ValidationError("replace_container_images requires at least one image")
    original = sanitize_task_definition(td)
    replaced = json.loads(json.dumps(original))
    for name, image in images.items():
        found = False
        for container in replaced["containerDefinitions"]:
            if container.get("name") == name:
                if container.get("image") == image:
                    raise ValidationError(
                        f"container {name} already runs image {image}; nothing to change"
                    )
                container["image"] = image
                found = True
                break
        if not found:
            raise ValidationError(f"container {name} does not exist in the task definition")
    _assert_image_only_change(original, replaced)
    return replaced


def _assert_image_only_change(original: dict, replaced: dict) -> None:
    for container_index, (before, after) in enumerate(
        zip(original.get("containerDefinitions") or [], replaced.get("containerDefinitions") or [],
        strict=False)
    ):
        if len(original.get("containerDefinitions") or []) != len(
            replaced.get("containerDefinitions") or []
        ):
            raise MutationVerificationError(
                "task definition container count changed; not an image-only change"
            )
        for key in set(before) | set(after):
            if key == "image":
                continue
            if before.get(key) != after.get(key):
                raise MutationVerificationError(
                    f"task definition container {container_index} changed beyond image "
                    f"(key {key})"
                )
    for key in set(original) | set(replaced):
        if key == "containerDefinitions":
            continue
        if original.get(key) != replaced.get(key):
            raise MutationVerificationError(
                f"task definition changed beyond container images (key {key})"
            )


def deregister_task_definition(client: Any, task_definition_arn: str) -> None:
    """Deregister a task definition and verify it becomes INACTIVE."""
    client.deregister_task_definition(taskDefinition=task_definition_arn)
    observed = describe_task_definition(client, task_definition_arn)
    status = observed.get("taskDefinition", {}).get("status")
    if status != "INACTIVE":
        raise MutationVerificationError(
            f"task definition {task_definition_arn} did not become INACTIVE "
            f"after deregistration (observed {status!r})"
        )


def delete_task_definition(client: Any, task_definition_arn: str) -> None:
    """Delete a deregistered task definition and verify it is gone."""
    client.delete_task_definitions(taskDefinitions=[task_definition_arn])
    try:
        observed = client.describe_task_definition(taskDefinition=task_definition_arn)
    except ClientError as error:
        if absent_or_read(error):
            return
        raise ReadError(
            f"describe_task_definition failed for {task_definition_arn} after deletion"
        ) from error
    status = observed.get("taskDefinition", {}).get("status")
    if status != "DELETE_IN_PROGRESS":
        raise MutationVerificationError(
            f"task definition {task_definition_arn} not deleted "
            f"(observed status {status!r})"
        )
