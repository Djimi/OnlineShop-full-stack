"""Tests for ECS service, task definition, and deployment adapters."""

import copy

import pytest
from conftest import client_error

from delivery.aws.ecs import (
    describe_services,
    describe_task_definition,
    primary_deployment,
    register_task_definition,
    running_digests,
    service_deployment,
    update_service,
    wait_for_deployment,
)
from delivery.errors import (
    AbsentResourceError,
    MutationVerificationError,
    ReadError,
    WaiterTimeoutError,
)

CLUSTER = "onlineshop-production"
SERVICE = "auth"
DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"


def _service(name=SERVICE, task_definition="td-1", deployments=None, **overrides):
    service = {
        "serviceName": name,
        "serviceArn": f"arn:aws:ecs:eu-north-1:123456789012:service/{name}",
        "taskDefinition": task_definition,
        "deployments": deployments or [{"id": "dep-1", "status": "PRIMARY"}],
    }
    service.update(overrides)
    return service


def _task(task_arn, digest=None, containers=None):
    return {
        "taskArn": task_arn,
        "containers": (
            containers
            if containers is not None
            else [{"name": "auth", "imageDigest": digest or DIGEST_A}]
        ),
    }


def _td(arn="arn:aws:ecs:eu-north-1:123456789012:task-definition/auth:1", images=None):
    return {
        "taskDefinitionArn": arn,
        "containerDefinitions": [
            {"name": f"container-{index}", "image": image}
            for index, image in enumerate(images or [f"repo@sha256:{'c' * 64}"])
        ],
    }


class FakeEcs:
    def __init__(self, services=None, task_definitions=None, tasks=None):
        self.services = {name: copy.deepcopy(service) for name, service in (services or {}).items()}
        self.task_definitions = {
            arn: copy.deepcopy(td) for arn, td in (task_definitions or {}).items()
        }
        self.tasks = {
            service: copy.deepcopy(task_list) for service, task_list in (tasks or {}).items()
        }
        self.error = None
        self.tasks_error = None
        self.list_tasks_error = None

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def describe_services(self, cluster, services):
        self._maybe_fail()
        return {
            "services": [
                copy.deepcopy(self.services[name])
                for name in services
                if name in self.services
            ]
        }

    def list_tasks(self, cluster, serviceName):
        self._maybe_fail()
        if self.list_tasks_error is not None:
            raise self.list_tasks_error
        return {"taskArns": [task["taskArn"] for task in self.tasks.get(serviceName, [])]}

    def describe_task_definition(self, taskDefinition):
        self._maybe_fail()
        td = self.task_definitions.get(taskDefinition)
        if td is None:
            raise client_error("ResourceNotFoundException", "no such task definition")
        return {"taskDefinition": copy.deepcopy(td)}

    def register_task_definition(self, **kwargs):
        self._maybe_fail()
        arn = (
            f"arn:aws:ecs:eu-north-1:123456789012:task-definition/"
            f"{kwargs['family']}:{len(self.task_definitions) + 1}"
        )
        td = {
            "taskDefinitionArn": arn,
            "containerDefinitions": copy.deepcopy(kwargs.get("containerDefinitions") or []),
        }
        self.task_definitions[arn] = td
        return {"taskDefinition": copy.deepcopy(td)}

    def update_service(self, cluster, service, taskDefinition):
        self._maybe_fail()
        self.services[service]["taskDefinition"] = taskDefinition
        return {"service": self.services[service]}

    def describe_tasks(self, cluster, tasks):
        self._maybe_fail()
        if self.tasks_error is not None:
            raise self.tasks_error
        known = {
            task["taskArn"]: task for task_list in self.tasks.values() for task in task_list
        }
        return {"tasks": copy.deepcopy([known[arn] for arn in tasks if arn in known])}


class TamperingFakeEcs(FakeEcs):
    def describe_task_definition(self, taskDefinition):
        response = super().describe_task_definition(taskDefinition)
        containers = response["taskDefinition"]["containerDefinitions"]
        if containers:
            containers[0] = {**containers[0], "image": containers[0]["image"] + "-tampered"}
            response["taskDefinition"]["containerDefinitions"] = containers
        return response


class StaleFakeEcs(FakeEcs):
    def update_service(self, cluster, service, taskDefinition):
        return {"service": self.services[service]}


def test_describe_services_keys_by_name():
    fake = FakeEcs(
        services={"auth": _service("auth"), "items": _service("items", task_definition="td-2")}
    )
    observed = describe_services(fake, CLUSTER, ["auth", "items"])
    assert set(observed) == {"auth", "items"}


def test_describe_services_missing_service_is_read_error():
    fake = FakeEcs(services={"auth": _service("auth")})
    with pytest.raises(ReadError):
        describe_services(fake, CLUSTER, ["auth", "items"])


def test_describe_services_client_error_is_read_error():
    fake = FakeEcs(services={"auth": _service("auth")})
    fake.error = client_error("ThrottlingException")
    with pytest.raises(ReadError):
        describe_services(fake, CLUSTER, ["auth"])


def test_running_digests_sorted_from_multiple_tasks():
    fake = FakeEcs(
        services={"auth": _service()},
        tasks={
            "auth": [
                _task("arn:...:task/1", DIGEST_B),
                _task("arn:...:task/2", DIGEST_A),
            ]
        },
    )
    assert running_digests(fake, CLUSTER, SERVICE) == sorted([DIGEST_A, DIGEST_B])


def test_running_digests_includes_every_container_of_every_task():
    fake = FakeEcs(
        services={"auth": _service()},
        tasks={
            "auth": [
                _task(
                    "arn:...:task/1",
                    containers=[
                        {"name": "auth", "imageDigest": DIGEST_A},
                        {"name": "sidecar", "imageDigest": DIGEST_B},
                    ],
                )
            ]
        },
    )
    assert running_digests(fake, CLUSTER, SERVICE) == sorted([DIGEST_A, DIGEST_B])


def test_running_digests_empty_task_list_is_read_error():
    fake = FakeEcs(services={"auth": _service()})
    with pytest.raises(ReadError, match="no running tasks"):
        running_digests(fake, CLUSTER, SERVICE)


def test_running_digests_list_tasks_error_is_read_error():
    fake = FakeEcs(services={"auth": _service()})
    fake.list_tasks_error = client_error("ThrottlingException")
    with pytest.raises(ReadError, match="list_tasks failed"):
        running_digests(fake, CLUSTER, SERVICE)


def test_running_digests_client_error_is_read_error():
    fake = FakeEcs(
        services={"auth": _service()},
        tasks={"auth": [_task("arn:...:task/1", DIGEST_A)]},
    )
    fake.tasks_error = client_error("ServerException")
    with pytest.raises(ReadError, match="describe_tasks failed"):
        running_digests(fake, CLUSTER, SERVICE)


def test_running_digests_absent_tasks_error_is_absent():
    fake = FakeEcs(
        services={"auth": _service()},
        tasks={"auth": [_task("arn:...:task/1", DIGEST_A)]},
    )
    fake.tasks_error = client_error("ResourceNotFoundException")
    with pytest.raises(AbsentResourceError):
        running_digests(fake, CLUSTER, SERVICE)


def test_running_digests_describe_failures_fail_closed():
    class FailingFakeEcs(FakeEcs):
        def describe_tasks(self, cluster, tasks):
            response = super().describe_tasks(cluster, tasks)
            response["failures"] = [{"arn": tasks[0], "reason": "MISSING"}]
            return response

    fake = FailingFakeEcs(
        services={"auth": _service()},
        tasks={"auth": [_task("arn:...:task/1", DIGEST_A)]},
    )
    with pytest.raises(ReadError, match="failures"):
        running_digests(fake, CLUSTER, SERVICE)


def test_running_digests_missing_described_task_fails_closed():
    class VanishingFakeEcs(FakeEcs):
        def describe_tasks(self, cluster, tasks):
            return {"tasks": []}

    fake = VanishingFakeEcs(
        services={"auth": _service()},
        tasks={"auth": [_task("arn:...:task/1", DIGEST_A)]},
    )
    with pytest.raises(ReadError, match="returned 0 of 1 tasks"):
        running_digests(fake, CLUSTER, SERVICE)


def test_running_digests_missing_image_digest_is_read_error():
    fake = FakeEcs(
        services={"auth": _service()},
        tasks={"auth": [_task("arn:...:task/1", containers=[{"name": "auth"}])]},
    )
    with pytest.raises(ReadError, match="no imageDigest"):
        running_digests(fake, CLUSTER, SERVICE)


def test_running_digests_task_without_containers_is_read_error():
    fake = FakeEcs(
        services={"auth": _service()},
        tasks={"auth": [{"taskArn": "arn:...:task/1", "containers": []}]},
    )
    with pytest.raises(ReadError, match="no containers"):
        running_digests(fake, CLUSTER, SERVICE)


@pytest.mark.parametrize(
    "digest",
    [
        "9e107d9d372bb6826bd81d3542a419d6",
        "sha256:" + "A" * 64,
        "a" * 64,
        "sha256:" + "a" * 63,
    ],
)
def test_running_digests_malformed_digest_is_read_error(digest):
    fake = FakeEcs(
        services={"auth": _service()},
        tasks={"auth": [_task("arn:...:task/1", digest)]},
    )
    with pytest.raises(ReadError, match="malformed imageDigest"):
        running_digests(fake, CLUSTER, SERVICE)


def test_primary_deployment_returns_primary_regardless_of_order():
    service = _service(
        deployments=[
            {"id": "dep-2", "status": "ACTIVE"},
            {"id": "dep-1", "status": "PRIMARY", "rolloutState": "COMPLETED"},
        ]
    )
    fake = FakeEcs(services={"auth": service})
    observed = describe_services(fake, CLUSTER, [SERVICE])[SERVICE]
    assert primary_deployment(observed, SERVICE)["id"] == "dep-1"


def test_primary_deployment_without_primary_is_read_error():
    service = _service(deployments=[{"id": "dep-2", "status": "ACTIVE"}])
    fake = FakeEcs(services={"auth": service})
    observed = describe_services(fake, CLUSTER, [SERVICE])[SERVICE]
    with pytest.raises(ReadError, match="no PRIMARY deployment"):
        primary_deployment(observed, SERVICE)


def test_service_deployment_returns_primary_id():
    fake = FakeEcs(services={"auth": _service()})
    assert service_deployment(fake, CLUSTER, SERVICE) == "dep-1"


def test_service_deployment_primary_without_id_is_read_error():
    service = _service(deployments=[{"id": "", "status": "PRIMARY"}])
    fake = FakeEcs(services={"auth": service})
    with pytest.raises(ReadError, match="no id"):
        service_deployment(fake, CLUSTER, SERVICE)


def test_service_deployment_without_primary_is_read_error():
    service = _service(deployments=[{"id": "dep-2", "status": "ACTIVE"}])
    fake = FakeEcs(services={"auth": service})
    with pytest.raises(ReadError):
        service_deployment(fake, CLUSTER, SERVICE)


def test_describe_task_definition_ok():
    td = _td()
    fake = FakeEcs(task_definitions={td["taskDefinitionArn"]: td})
    observed = describe_task_definition(fake, td["taskDefinitionArn"])
    assert observed["taskDefinition"]["taskDefinitionArn"] == td["taskDefinitionArn"]


def test_describe_task_definition_absent_is_absent():
    fake = FakeEcs()
    with pytest.raises(AbsentResourceError):
        describe_task_definition(fake, "arn:aws:ecs:eu-north-1:123456789012:task-definition/gone:1")


def test_describe_task_definition_other_error_is_read_error():
    fake = FakeEcs()
    fake.error = client_error("ServerException")
    with pytest.raises(ReadError):
        describe_task_definition(fake, "arn:aws:ecs:eu-north-1:123456789012:task-definition/auth:1")


def test_register_task_definition_ok_with_read_back():
    fake = FakeEcs()
    td = {
        "family": "auth",
        "containerDefinitions": [{"name": "auth", "image": f"repo@{DIGEST_A}"}],
    }
    revision_arn = register_task_definition(fake, td)
    assert revision_arn.endswith("/auth:1")
    registered = fake.task_definitions[revision_arn]["containerDefinitions"][0]["image"]
    assert registered == f"repo@{DIGEST_A}"


def test_register_task_definition_read_back_mismatch_raises():
    fake = TamperingFakeEcs()
    td = {
        "family": "auth",
        "containerDefinitions": [{"name": "auth", "image": f"repo@{DIGEST_A}"}],
    }
    with pytest.raises(MutationVerificationError):
        register_task_definition(fake, td)


def test_update_service_ok_with_read_back():
    fake = FakeEcs(services={"auth": _service()})
    new_td = "arn:aws:ecs:eu-north-1:123456789012:task-definition/auth:2"
    observed = update_service(fake, CLUSTER, SERVICE, new_td)
    assert observed["taskDefinition"] == new_td


def test_update_service_read_back_mismatch_raises():
    fake = StaleFakeEcs(services={"auth": _service()})
    new_td = "arn:aws:ecs:eu-north-1:123456789012:task-definition/auth:2"
    with pytest.raises(MutationVerificationError):
        update_service(fake, CLUSTER, SERVICE, new_td)


def _wait(fake, deployment_id):
    return wait_for_deployment(
        fake, CLUSTER, SERVICE, deployment_id, timeout_seconds=1, interval_seconds=0.5
    )


def test_wait_for_deployment_success():
    service = _service(deployments=[{"id": "dep-1", "status": "PRIMARY"}])
    fake = FakeEcs(services={"auth": service})
    assert _wait(fake, "dep-1")


def test_wait_for_deployment_rolled_back_fails():
    service = _service(deployments=[{"id": "dep-1", "status": "ROLLED_BACK"}])
    fake = FakeEcs(services={"auth": service})
    with pytest.raises(WaiterTimeoutError):
        _wait(fake, "dep-1")


def test_wait_for_deployment_times_out():
    service = _service(deployments=[{"id": "dep-1", "status": "ACTIVE"}])
    fake = FakeEcs(services={"auth": service})
    with pytest.raises(WaiterTimeoutError):
        _wait(fake, "dep-1")
