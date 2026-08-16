"""Shared fake AWS clients for staging command tests (offline only)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from conftest import client_error

from delivery.errors import ValidationError
from delivery.models import OwnershipMarker
from delivery.staging_marker import marker_value

ACCOUNT = "799111666795"
REGION = "eu-north-1"
CLUSTER = "onlineshop-staging-cluster"
SERVICES = [
    "onlineshop-auth-staging",
    "onlineshop-items-staging",
    "onlineshop-api-gateway-staging",
]
DB_INSTANCE = "onlineshop-staging-postgres"
MARKER_TAG_KEY = "onlineshop:staging-owner"
AWS_TAG_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_.:/=+@-]{0,256}$")

DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"
DIGEST_C = f"sha256:{'c' * 64}"
DIGEST_D = f"sha256:{'d' * 64}"

REGISTRY = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
REPOSITORIES = {
    "auth": "onlineshop-auth",
    "items": "onlineshop-items",
    "gateway": "onlineshop-api-gateway",
}

MASTER_SECRET_ARN = (
    f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:rds!db-master-abcdef"
)
AUTH_SECRET_ARN = (
    f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:onlineshop/auth/db-staging-abc123"
)
ITEMS_SECRET_ARN = (
    f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:onlineshop/items/db-staging-abc123"
)


def staging_identifiers(extra: dict | None = None) -> dict:
    ids = {
        "environment": "staging",
        "accountId": ACCOUNT,
        "cluster": CLUSTER,
        "services": list(SERVICES),
        "ecrRepositories": dict(REPOSITORIES),
        "dbInstance": DB_INSTANCE,
        "albName": "onlineshop-staging-v2-alb",
        "dbSecrets": {
            "auth": "onlineshop/auth/db-staging",
            "items": "onlineshop/items/db-staging",
        },
        "sqlRunnerFamily": "onlineshop-staging-sql-runner",
        "sqlLogGroup": "/ecs/onlineshop-staging-sql-runner",
        "sqlSubnets": ["subnet-aaaa", "subnet-bbbb"],
        "sqlSecurityGroup": "sg-staging",
        "executionRoleArn": f"arn:aws:iam::{ACCOUNT}:role/ecsTaskExecutionRole",
        "compatFrontendBucket": "onlineshop-frontend-799111666795",
        "compatFrontendReleasesPrefix": "_releases/",
        "e2eBaseUrl": "http://staging-alb.example.com",
    }
    if extra:
        ids.update(extra)
    return ids


def write_identifiers(tmp_path, ids: dict | None = None):
    path = tmp_path / "staging-identifiers.json"
    path.write_text(json.dumps(ids or staging_identifiers()))
    return path


class FakeSts:
    def get_caller_identity(self):
        return {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/tester"}


class FakeRds:
    def __init__(self, status="stopped", tags=None, error=None, stop_result="stopped"):
        self.status = status
        self.tags = dict(tags or {})
        self.error = error
        self.stop_result = stop_result
        self.tag_error = None
        self.calls = []

    def _check(self, name):
        self.calls.append(name)
        if self.error is not None:
            raise self.error

    def _check_tags(self, name):
        self._check(name)
        if self.tag_error is not None:
            raise self.tag_error

    def describe_db_instances(self, DBInstanceIdentifier):
        self._check("describe_db_instances")
        if DBInstanceIdentifier != DB_INSTANCE:
            raise client_error("DBInstanceNotFound")
        return {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": DB_INSTANCE,
                    "DBInstanceStatus": self.status,
                    "DBInstanceArn": (
                        f"arn:aws:rds:{REGION}:{ACCOUNT}:db:{DB_INSTANCE}"
                    ),
                    "Engine": "postgres",
                    "EngineVersion": "18.1",
                    "DBInstanceClass": "db.t4g.micro",
                    "Endpoint": {
                        "Address": "onlineshop-staging-postgres.internal.example.com"
                    },
                    "MasterUserSecret": {"SecretArn": MASTER_SECRET_ARN},
                    "StorageEncrypted": True,
                    "PubliclyAccessible": False,
                }
            ]
        }

    def start_db_instance(self, DBInstanceIdentifier):
        self._check("start_db_instance")
        self.status = "available"

    def stop_db_instance(self, DBInstanceIdentifier):
        self._check("stop_db_instance")
        self.status = self.stop_result

    def list_tags_for_resource(self, ResourceName):
        self._check_tags("list_tags_for_resource")
        return {"TagList": [{"Key": k, "Value": v} for k, v in self.tags.items()]}

    def add_tags_to_resource(self, ResourceName, Tags):
        self._check("add_tags_to_resource")
        for tag in Tags:
            if AWS_TAG_VALUE_PATTERN.fullmatch(tag["Value"]) is None:
                raise client_error(
                    "InvalidParameterValue",
                    "tag value contains forbidden characters or exceeds 256 characters",
                )
            self.tags[tag["Key"]] = tag["Value"]

    def remove_tags_from_resource(self, ResourceName, TagKeys):
        self._check("remove_tags_from_resource")
        for key in TagKeys:
            self.tags.pop(key, None)


def marker_tag_value(
    operation_id: str = "stg-4712-2",
    run_id: int = 4712,
    run_attempt: int = 2,
    owner: str = "tester",
    expires_in: timedelta = timedelta(hours=1),
):
    now = datetime.now(UTC).replace(microsecond=0)
    return marker_value(
        OwnershipMarker(
            operationId=operation_id,
            workflowRunId=run_id,
            workflowRunAttempt=run_attempt,
            owner=owner,
            acquiredAt=now,
            expiresAt=now + expires_in,
        )
    )


class FakeEcs:
    """Stateful ECS fake: services with task definitions, deployments, tasks."""

    def __init__(self, digests=None, fail_describe=None):
        self.fail_describe = fail_describe
        self.digests = digests or {key: DIGEST_A for key in SERVICES}
        self.td_counter = 1
        self.task_counter = 1
        self.update_calls = []
        self.register_calls = []
        self.deleted = []
        self.desired_counts = {service: 1 for service in SERVICES}
        self.running_counts = {service: 1 for service in SERVICES}
        self.td_store = {}
        self.tasks = {}
        for service in SERVICES:
            task_arn = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{service}/run-{self.task_counter}"
            self.task_counter += 1
            self.tasks[service] = [task_arn]

    def _service(self, service):
        return {
            "serviceName": service,
            "clusterArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/{CLUSTER}",
            "taskDefinition": (
                f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/"
                f"{service}:{self._current_revision(service)}"
            ),
            "desiredCount": self.desired_counts[service],
            "runningCount": self.running_counts[service],
            "deployments": [
                {
                    "id": f"deploy-{service}",
                    "status": "PRIMARY",
                    "rolloutState": "COMPLETED",
                }
            ],
        }

    def _current_revision(self, family: str) -> int:
        if family in self.td_store:
            return max(int(arn.rsplit(":", 1)[1]) for arn in self.td_store[family])
        return 1

    def describe_services(self, cluster, services):
        if self.fail_describe is not None:
            raise self.fail_describe
        return {"services": [self._service(s) for s in services]}

    def describe_task_definition(self, taskDefinition):
        family = taskDefinition.rsplit(":", 1)[0].rsplit("/", 1)[-1]
        revision = int(taskDefinition.rsplit(":", 1)[1])
        if family in self.td_store:
            if taskDefinition not in self.td_store[family]:
                raise client_error("ResourceNotFoundException")
            return {"taskDefinition": self.td_store[family][taskDefinition]}
        repository = family.replace("-staging", "")
        image = f"{REGISTRY}/{repository}:{family}-oldtag"
        td = {
            "taskDefinitionArn": taskDefinition,
            "revision": revision,
            "status": "ACTIVE",
            "family": family,
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
            "executionRoleArn": f"arn:aws:iam::{ACCOUNT}:role/ecsTaskExecutionRole",
            "containerDefinitions": [
                {
                    "name": family,
                    "image": image,
                    "essential": True,
                    "secrets": [
                        {
                            "name": "DB_PASSWORD",
                            "valueFrom": f"{AUTH_SECRET_ARN}:password::",
                        }
                    ],
                }
            ],
        }
        return {"taskDefinition": td}

    def register_task_definition(self, **td):
        self.register_calls.append(td)
        family = td["family"]
        self.td_counter += 1
        arn = (
            f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/"
            f"{family}:{self.td_counter}"
        )
        stored = {
            "taskDefinitionArn": arn,
            "revision": self.td_counter,
            "status": "ACTIVE",
            **{key: value for key, value in td.items() if key != "family"},
        }
        self.td_store.setdefault(family, {})[arn] = stored
        return {"taskDefinition": stored}

    def update_service(self, cluster, service, **kwargs):
        self.update_calls.append((service, kwargs))
        if "desiredCount" in kwargs:
            self.desired_counts[service] = kwargs["desiredCount"]
            self.running_counts[service] = kwargs["desiredCount"]
        return self._service(service)

    def list_tasks(self, cluster, serviceName):
        if self.running_counts[serviceName] == 0:
            return {"taskArns": []}
        return {"taskArns": list(self.tasks[serviceName])}

    def describe_tasks(self, cluster, tasks):
        described = []
        for task_arn in tasks:
            service = task_arn.split("/")[-2]
            described.append(
                {
                    "taskArn": task_arn,
                    "lastStatus": "RUNNING",
                    "containers": [
                        {
                            "name": service,
                            "imageDigest": self.digests[service],
                        }
                    ],
                }
            )
        return {"tasks": described}

    def deregister_task_definition(self, taskDefinition):
        family = taskDefinition.rsplit(":", 1)[0].rsplit("/", 1)[-1]
        stored = self.td_store.get(family, {}).get(taskDefinition)
        if stored is not None:
            stored["status"] = "INACTIVE"
        return {"taskDefinition": {"taskDefinitionArn": taskDefinition, "status": "INACTIVE"}}

    def delete_task_definitions(self, taskDefinitions):
        self.deleted.extend(taskDefinitions)
        for arn in taskDefinitions:
            family = arn.rsplit(":", 1)[0].rsplit("/", 1)[-1]
            self.td_store.get(family, {}).pop(arn, None)
        return {}

    def run_task(self, **kwargs):
        arn = (
            f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/sql-runner/{self.task_counter}"
        )
        self.task_counter += 1
        return {"tasks": [{"taskArn": arn}]}


class FakeEcr:
    def __init__(self, digests: dict[str, str] | None = None):
        self.digests = digests or {
            "auth": DIGEST_A,
            "items": DIGEST_B,
            "gateway": DIGEST_C,
        }

    def describe_images(self, repositoryName, imageIds=None, **kwargs):
        key = next(
            (name for name, repo in REPOSITORIES.items() if repo == repositoryName), None
        )
        if key is None:
            return {"imageDetails": []}
        details = []
        for spec in imageIds or []:
            requested = spec.get("imageDigest")
            if self.digests.get(key) != requested:
                return {"imageDetails": []}
            details.append(
                {
                    "imageDigest": requested,
                    "imageTags": [],
                    "imageManifestMediaType": "application/vnd.oci.image.index.v1+json",
                }
            )
        return {"imageDetails": details}


class FakeElb:
    def __init__(self, dns_name="staging-alb-1234.eu-north-1.elb.amazonaws.com"):
        self.dns_name = dns_name

    def describe_load_balancers(self, Names=None):
        if not Names or Names[0] != "onlineshop-staging-v2-alb":
            return {"LoadBalancers": []}
        return {"LoadBalancers": [{"LoadBalancerName": Names[0], "DNSName": self.dns_name}]}

    def describe_target_health(self, TargetGroupArn):
        return {"TargetHealthDescriptions": []}


class FakeS3:
    def __init__(self, objects: dict[str, bytes] | None = None, error=None):
        self.objects = objects or {}
        self.error = error

    def get_object(self, Bucket, Key):
        if self.error is not None:
            raise self.error
        if Key not in self.objects:
            raise client_error("NoSuchKey")
        return {"Body": _BytesIO(self.objects[Key])}


class _BytesIO:
    def __init__(self, data: bytes):
        self.data = data

    def read(self):
        return self.data


class FakeSecrets:
    def describe_secret(self, SecretId):
        for arn in (AUTH_SECRET_ARN, ITEMS_SECRET_ARN, MASTER_SECRET_ARN):
            if SecretId in arn:
                return {"ARN": arn}
        raise client_error("ResourceNotFoundException")


class FakeLogs:
    def __init__(self):
        self.groups = set()

    def describe_log_groups(self, logGroupNamePrefix):
        return {"logGroups": [{"logGroupName": g} for g in self.groups]}

    def create_log_group(self, logGroupName):
        self.groups.add(logGroupName)

    def get_log_events(self, logGroupName, logStreamName, startFromHead=False):
        return {
            "events": [
                {"message": "=== SQL_OK ===\n"},
                {"message": "verification output\n"},
            ]
        }


class FakeGitHub:
    """Stub of the GitHubApi surface used by the staging machine."""

    def __init__(self, repository=None, token=None, artifacts=None, releases=None):
        self.artifacts = artifacts if artifacts is not None else {}
        self.releases = releases if releases is not None else []
        self._assets = {}

    def list_run_artifacts(self, run_id, run_attempt, expected_names):
        artifacts = []
        for name in self.artifacts.get((run_id, run_attempt), []):
            if name in expected_names:
                artifacts.append({"id": 1000 + len(artifacts), "name": name})
        missing = expected_names - {artifact["name"] for artifact in artifacts}
        if missing:
            raise ValidationError(
                f"missing artifacts {', '.join(sorted(missing))} "
                f"for run {run_id} attempt {run_attempt}"
            )
        return artifacts

    def list_releases(self):
        return self.releases

    def download_asset(self, url):
        return self._assets[url]


def standard_artifact_names(run_id, run_attempt):
    return [
        f"candidate-manifest-{run_id}-{run_attempt}",
        f"frontend-archive-{run_id}-{run_attempt}",
        f"sboms-{run_id}-{run_attempt}",
        f"test-results-{run_id}-{run_attempt}",
    ]
