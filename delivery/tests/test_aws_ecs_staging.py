"""Tests for the Phase-4 ECS helpers: scaling, TD image-only changes, cleanup."""

import pytest
from fakes_staging import FakeEcs

from delivery.aws.ecs import (
    delete_task_definition,
    deregister_task_definition,
    replace_container_images,
    sanitize_task_definition,
    scale_service,
    task_definition_images,
    wait_for_service_running_count,
    wait_for_tasks_stopped,
)
from delivery.errors import (
    MutationVerificationError,
    ReadError,
    ValidationError,
    WaiterTimeoutError,
)

CLUSTER = "onlineshop-staging-cluster"
SERVICE = "onlineshop-auth-staging"
DIGEST = f"sha256:{'e' * 64}"


def _td(extra_container=None, image="registry.example.com/repo:old"):
    td = {
        "taskDefinitionArn": "arn:aws:ecs:eu-north-1:799111666795:task-definition/fam:1",
        "revision": 1,
        "status": "ACTIVE",
        "family": "fam",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512",
        "executionRoleArn": "arn:aws:iam::799111666795:role/ecsTaskExecutionRole",
        "registeredAt": "2026-08-15T10:00:00Z",
        "containerDefinitions": [
            {
                "name": "app",
                "image": image,
                "secrets": [
                    {
                        "name": "DB_PASSWORD",
                        "valueFrom": (
                            "arn:aws:secretsmanager:eu-north-1:799111666795:"
                            "secret:x:password::"
                        ),
                    }
                ],
            }
        ],
    }
    if extra_container:
        td["containerDefinitions"].append(extra_container)
    return td


def test_scale_service_reads_back_desired_count():
    fake = FakeEcs()
    observed = scale_service(fake, CLUSTER, SERVICE, 0)
    assert observed["desiredCount"] == 0


def test_scale_service_rejects_non_integer():
    with pytest.raises(ValidationError):
        scale_service(FakeEcs(), CLUSTER, SERVICE, "0")  # type: ignore[arg-type]


def test_scale_service_fails_when_readback_differs():
    class BrokenScale(FakeEcs):
        def update_service(self, cluster, service, **kwargs):
            return None

    with pytest.raises(MutationVerificationError):
        scale_service(BrokenScale(), CLUSTER, SERVICE, 0)


def test_wait_for_service_running_count_matches():
    fake = FakeEcs()
    assert wait_for_service_running_count(
        fake, CLUSTER, SERVICE, 1, timeout_seconds=5, interval_seconds=0.5
    )


def test_wait_for_service_running_count_times_out():
    fake = FakeEcs()
    fake.running_counts[SERVICE] = 3
    with pytest.raises(WaiterTimeoutError):
        wait_for_service_running_count(
            fake, CLUSTER, SERVICE, 1, timeout_seconds=0.6, interval_seconds=0.5
        )


def test_sanitize_task_definition_drops_readonly_fields():
    td = _td()
    sanitized = sanitize_task_definition(td)
    for key in (
        "taskDefinitionArn",
        "revision",
        "status",
        "registeredAt",
    ):
        assert key not in sanitized
    assert sanitized["family"] == "fam"
    assert sanitized["containerDefinitions"][0]["secrets"]


def test_replace_container_images_is_image_only():
    td = _td()
    replaced = replace_container_images(td, {"app": f"registry.example.com/repo@{DIGEST}"})
    assert replaced["containerDefinitions"][0]["image"] == f"registry.example.com/repo@{DIGEST}"
    # secrets survive untouched as references
    assert (
        replaced["containerDefinitions"][0]["secrets"]
        == td["containerDefinitions"][0]["secrets"]
    )


def test_replace_container_images_rejects_unknown_container():
    with pytest.raises(ValidationError):
        replace_container_images(_td(), {"nope": f"registry/repo@{DIGEST}"})


def test_replace_container_images_rejects_identical_image():
    with pytest.raises(ValidationError):
        replace_container_images(_td(), {"app": "registry.example.com/repo:old"})


def test_task_definition_images_maps_names():
    td = _td(extra_container={"name": "sidecar", "image": "sidecar:1"})
    images = task_definition_images(td)
    assert images == {"app": "registry.example.com/repo:old", "sidecar": "sidecar:1"}


def test_task_definition_images_missing_name_fails():
    with pytest.raises(ReadError):
        task_definition_images({"containerDefinitions": [{"image": "x:1"}]})


def _registered_arn(fake: FakeEcs) -> str:
    return fake.register_task_definition(
        family="onlineshop-auth-staging",
        containerDefinitions=[{"name": "sql", "image": "postgres:18-alpine"}],
    )["taskDefinition"]["taskDefinitionArn"]


def test_deregister_task_definition_verifies_inactive():
    fake = FakeEcs()
    arn = _registered_arn(fake)
    deregister_task_definition(fake, arn)
    assert fake.td_store["onlineshop-auth-staging"][arn]["status"] == "INACTIVE"


def test_delete_task_definition_verifies_absence():
    fake = FakeEcs()
    arn = _registered_arn(fake)
    deregister_task_definition(fake, arn)
    delete_task_definition(fake, arn)
    assert arn in fake.deleted
    # B017: the fake raises a botocore ClientError for a deleted task
    # definition, which is exactly the case this assertion guards.
    with pytest.raises(Exception):  # noqa: B017
        fake.describe_task_definition(arn)


def test_wait_for_tasks_stopped_waits_for_all():
    fake = FakeEcs()
    task_arns = fake.tasks[SERVICE]

    class EventuallyStopped:
        def __init__(self):
            self.count = 0

        def describe_tasks(self, cluster, tasks):
            self.count += 1
            if self.count < 2:
                return {"tasks": [{"taskArn": arn, "lastStatus": "RUNNING"} for arn in tasks]}
            return {
                "tasks": [
                    {"taskArn": arn, "lastStatus": "STOPPED", "containers": []}
                    for arn in tasks
                ]
            }

    stopped = wait_for_tasks_stopped(
        EventuallyStopped(), CLUSTER, task_arns, timeout_seconds=5, interval_seconds=0.5
    )
    assert len(stopped) == 1
