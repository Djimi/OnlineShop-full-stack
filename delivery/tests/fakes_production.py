"""Shared fake AWS/GitHub clients for Phase-5 promotion command tests."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import client_error

from delivery import live_marker
from delivery.errors import ValidationError
from delivery.frontend import content_checksum

ACCOUNT = "799111666795"
REGION = "eu-north-1"
CLUSTER = "onlineshop-cluster"
SERVICES = ["onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"]
REPOSITORIES = {
    "auth": "onlineshop-auth",
    "items": "onlineshop-items",
    "gateway": "onlineshop-api-gateway",
}
FRONTEND_BUCKET = "onlineshop-frontend-799111666795"
MARKER_KEY = "release.json"
DISTRIBUTION = "EPS8MI3FV3B7X"
ALB_NAME = "onlineshop-alb"

DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"
DIGEST_C = f"sha256:{'c' * 64}"
DIGESTS = {"auth": DIGEST_A, "items": DIGEST_B, "gateway": DIGEST_C}
REDIS_DIGEST = f"sha256:{'e' * 64}"

REGISTRY = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"

SECRET_ARN = (
    f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:onlineshop/auth/db-abcdef"
)

RUN_ID = 4711
RUN_ATTEMPT = 1
STAGING_RUN_ID = 9001
STAGING_ATTEMPT = 2
CANDIDATE_SHA = "2" * 40


def production_identifiers(extra: dict | None = None) -> dict:
    ids = {
        "environment": "production",
        "accountId": ACCOUNT,
        "cluster": CLUSTER,
        "services": list(SERVICES),
        "ecrRepositories": dict(REPOSITORIES),
        "dbInstance": "onlineshop-postgres-db",
        "frontendBucket": FRONTEND_BUCKET,
        "frontendLiveMarker": MARKER_KEY,
        "frontendReleasesPrefix": "_releases/",
        "cloudfrontDistributionId": DISTRIBUTION,
        "albName": ALB_NAME,
    }
    if extra:
        ids.update(extra)
    return ids


def write_identifiers(tmp_path, ids: dict | None = None) -> Path:
    path = tmp_path / "production-identifiers.json"
    path.write_text(json.dumps(ids or production_identifiers()))
    return path


def make_frontend_archive(tmp_path: Path) -> tuple[Path, str, str]:
    dist = tmp_path / "frontend-dist"
    dist.mkdir()
    (dist / "index.html").write_text('<html><body><div id="root"></div></body></html>')
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('ok');\n")
    archive = tmp_path / "frontend.tar"
    with tarfile.open(archive, "w") as bundle:
        for path in sorted(dist.rglob("*")):
            bundle.add(path, arcname=path.relative_to(dist).as_posix())
    payload = archive.read_bytes()
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    return archive, digest, content_checksum(dist)


def main_candidate(
    tmp_path: Path,
    digests: dict[str, str] | None = None,
    archive: Path | None = None,
    content_checksum_value: str | None = None,
    artifact_digest: str | None = None,
    completed_days_ago: int = 1,
    production_eligible: bool = True,
    candidate_class: str = "main",
    branch: str = "main",
    run_id: int = RUN_ID,
    run_attempt: int = RUN_ATTEMPT,
) -> Path:
    manifest = {
        "schemaVersion": "1.0",
        "candidateId": f"cand-{run_id}-{run_attempt}-{CANDIDATE_SHA[:12]}",
        "candidateClass": candidate_class,
        "source": {
            "repository": "Djimi@8793507/OnlineShop-full-stack",
            "branch": branch,
            "ref": "refs/heads/main",
            "fullSha": CANDIDATE_SHA,
        },
        "build": {
            "workflowRunId": run_id,
            "workflowRunAttempt": run_attempt,
            "workflowUrl": f"https://github.com/x/y/actions/runs/{run_id}",
            "createdAt": (datetime.now(UTC) - timedelta(days=completed_days_ago)).isoformat(),
            "completedAt": (
                datetime.now(UTC) - timedelta(days=completed_days_ago)
            ).isoformat(),
        },
        "artifacts": {
            "auth": {
                "repository": f"{REGISTRY}/onlineshop-auth",
                "digest": (digests or DIGESTS)["auth"],
            },
            "items": {
                "repository": f"{REGISTRY}/onlineshop-items",
                "digest": (digests or DIGESTS)["items"],
                "commonSourceSha": CANDIDATE_SHA,
            },
            "gateway": {
                "repository": f"{REGISTRY}/onlineshop-api-gateway",
                "digest": (digests or DIGESTS)["gateway"],
            },
            "frontend": {
                "artifactId": f"frontend-archive-{run_id}-{run_attempt}",
                "artifactDigest": artifact_digest
                or f"sha256:{'d' * 64}",
                "contentChecksum": content_checksum_value or f"{'e' * 64}",
            },
        },
        "tests": {
            "unit": "passed",
            "integration": "passed",
            "frontend": "passed",
            "localE2E": "passed",
        },
        "productionEligible": production_eligible,
    }
    path = tmp_path / "candidate-manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def candidate_marker_doc(candidate_id: str | None = None) -> str:
    candidate_id = candidate_id or f"cand-{RUN_ID}-{RUN_ATTEMPT}-{CANDIDATE_SHA[:12]}"
    marker = live_marker.build_candidate_marker(
        candidate_id=candidate_id,
        source_sha=CANDIDATE_SHA,
        frontend_sha256=f"{'e' * 64}",
    )
    return live_marker.marker_document(marker)


def write_snapshot(
    tmp_path: Path,
    ids: dict,
    *,
    digests: dict[str, str] | None = None,
    marker_doc: str | None = None,
    release_id: str | None = None,
    task_definition_arns: dict[str, str] | None = None,
) -> Path:
    digests = digests or DIGESTS
    marker_doc = marker_doc if marker_doc is not None else candidate_marker_doc()
    td_arns = task_definition_arns or default_task_definition_arns()
    services = {}
    for key, name in zip(("auth", "items", "gateway"), SERVICES, strict=True):
        services[key] = {
            "deploymentId": f"deploy-{name}-1",
            "taskDefinitionArn": td_arns[name],
            "runningDigests": [digests[key]],
            "health": "COMPLETED",
        }
    release = (
        {"status": "official", "releaseId": release_id, "manifestSha256": None}
        if release_id
        else {"status": "none", "releaseId": None, "manifestSha256": None}
    )
    snapshot = {
        "schemaVersion": "1.0",
        "environment": "production",
        "snapshotId": "snap-0000000000000001",
        "capturedAt": datetime.now(UTC).isoformat(),
        "release": release,
        "services": services,
        "frontend": {
            "immutableIdentity": marker_doc.strip(),
            "liveMarker": ids["frontendLiveMarker"],
            "checksum": hashlib.sha256(marker_doc.encode()).hexdigest(),
            "cloudfrontDistributionId": ids["cloudfrontDistributionId"],
        },
        "compatibilityFingerprint": f"{'f' * 64}",
    }
    path = tmp_path / "production-snapshot.json"
    path.write_text(json.dumps(snapshot))
    return path


def default_task_definition_arns() -> dict[str, str]:
    return {
        "onlineshop-auth": f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/onlineshop-auth:12",
        "onlineshop-items": f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/onlineshop-items:9",
        "onlineshop-api-gateway": (
            f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/onlineshop-api-gateway:7"
        ),
    }


def candidate_artifact_names(run_id: int = RUN_ID, run_attempt: int = RUN_ATTEMPT) -> list[str]:
    return [
        f"candidate-manifest-{run_id}-{run_attempt}",
        f"frontend-archive-{run_id}-{run_attempt}",
        f"sboms-{run_id}-{run_attempt}",
        f"test-results-{run_id}-{run_attempt}",
    ]


def make_staging_record_zip(record: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("staging-record.json", json.dumps(record))
    return buffer.getvalue()


def complete_staging_record(candidate_manifest: dict) -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "schemaVersion": "1.0",
        "operationId": f"stg-{candidate_manifest['build']['workflowRunId']}-1",
        "candidate": {
            "candidateId": candidate_manifest["candidateId"],
            "branch": candidate_manifest["source"]["branch"],
            "fullSha": candidate_manifest["source"]["fullSha"],
            "workflowRunId": candidate_manifest["build"]["workflowRunId"],
            "workflowRunAttempt": candidate_manifest["build"]["workflowRunAttempt"],
        },
        "owner": "tester",
        "acquiredAt": now,
        "phase": "COMPLETE",
        "completedAt": now,
        "database": {
            "resetConclusion": "passed",
            "seedConclusion": "passed",
            "accessVerificationConclusion": "passed",
        },
        "artifactsExpected": {
            "authDigest": candidate_manifest["artifacts"]["auth"]["digest"],
            "itemsDigest": candidate_manifest["artifacts"]["items"]["digest"],
            "gatewayDigest": candidate_manifest["artifacts"]["gateway"]["digest"],
            "frontendChecksum": candidate_manifest["artifacts"]["frontend"]["contentChecksum"],
        },
        "artifactsObserved": {
            "authDigest": candidate_manifest["artifacts"]["auth"]["digest"],
            "itemsDigest": candidate_manifest["artifacts"]["items"]["digest"],
            "gatewayDigest": candidate_manifest["artifacts"]["gateway"]["digest"],
            "frontendChecksum": candidate_manifest["artifacts"]["frontend"]["contentChecksum"],
        },
        "compatibility": {
            "conclusion": "bootstrap-exception: no previous official release exists yet",
            "bootstrapException": True,
        },
        "e2e": {"conclusion": "passed"},
        "cleanup": {"conclusion": "passed"},
        "phaseLog": [],
        "journeys": [],
    }


class FakeSts:
    def get_caller_identity(self):
        return {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:role/github-actions-production"}


class FakeEcs:
    """Stateful production ECS fake: services, TDs, deployments, tasks."""

    def __init__(self, digests: dict[str, str] | None = None):
        digests = digests or DIGESTS
        if set(digests) == {"auth", "items", "gateway"}:
            digests = {
                "onlineshop-auth": digests["auth"],
                "onlineshop-items": digests["items"],
                "onlineshop-api-gateway": digests["gateway"],
            }
        self.digests = digests
        self.td_store: dict[str, dict] = {}
        self.register_calls = []
        self.update_calls = []
        self.td_counter = 100
        self.task_definition_arns = default_task_definition_arns()

    def _initial_td(self, family: str) -> dict:
        arn = self.task_definition_arns[family]
        repository = family
        definitions = [
            {
                "name": family,
                "image": f"{REGISTRY}/{repository}:{family}-oldtag",
                "essential": True,
                "secrets": [
                    {
                        "name": "DB_PASSWORD",
                        "valueFrom": f"{SECRET_ARN}:password::",
                    }
                ],
            }
        ]
        if family == "onlineshop-api-gateway":
            definitions.append(
                {
                    "name": "redis-sidecar",
                    "image": "public.ecr.aws/docker/library/redis:7.4-alpine",
                    "essential": False,
                }
            )
        return {
            "taskDefinitionArn": arn,
            "revision": int(arn.rsplit(":", 1)[1]),
            "status": "ACTIVE",
            "family": family,
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
            "executionRoleArn": f"arn:aws:iam::{ACCOUNT}:role/ecsTaskExecutionRole",
            "containerDefinitions": definitions,
        }

    def _current_td(self, family: str) -> dict:
        if family in self.td_store:
            return self.td_store[family][max(self.td_store[family])]
        return self._initial_td(family)

    def _service(self, service: str) -> dict:
        td = self._current_td(service)
        return {
            "serviceName": service,
            "clusterArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/{CLUSTER}",
            "taskDefinition": td["taskDefinitionArn"],
            "desiredCount": 1,
            "runningCount": 1,
            "deployments": [
                {
                    "id": f"deploy-{service}-1",
                    "status": "PRIMARY",
                    "rolloutState": "COMPLETED",
                    "taskDefinition": td["taskDefinitionArn"],
                }
            ],
        }

    def describe_services(self, cluster, services):
        return {"services": [self._service(name) for name in services]}

    def describe_task_definition(self, taskDefinition):
        family = taskDefinition.rsplit(":", 1)[0].rsplit("/", 1)[-1]
        if family in self.td_store:
            if taskDefinition not in self.td_store[family]:
                raise client_error("ResourceNotFoundException")
            return {"taskDefinition": self.td_store[family][taskDefinition]}
        if taskDefinition != self.task_definition_arns[family]:
            raise client_error("ResourceNotFoundException")
        return {"taskDefinition": self._initial_td(family)}

    def register_task_definition(self, **td):
        self.register_calls.append(td)
        family = td["family"]
        self.td_counter += 1
        arn = (
            f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{family}:{self.td_counter}"
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
        return self._service(service)

    def list_tasks(self, cluster, serviceName):
        return {"taskArns": [f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{serviceName}/1"]}

    def describe_tasks(self, cluster, tasks):
        described = []
        for task_arn in tasks:
            service = task_arn.split("/")[-2]
            td_arn = self._current_td(service)["taskDefinitionArn"]
            containers = [
                {"name": service, "imageDigest": self.digests[service]}
            ]
            if service == "onlineshop-api-gateway":
                containers.append(
                    {
                        "name": "redis-sidecar",
                        "imageDigest": REDIS_DIGEST,
                    }
                )
            described.append(
                {
                    "taskArn": task_arn,
                    "taskDefinitionArn": td_arn,
                    "lastStatus": "RUNNING",
                    "containers": containers,
                }
            )
        return {"tasks": described}


class FakeEcr:
    def __init__(self, digests: dict[str, str] | None = None, tags: dict[str, str] | None = None):
        self.digests = digests or DIGESTS
        self.tags = tags or {}
        self.put_calls = []
        self.lifecycle_policies: dict[str, str] = {}
        self.lifecycle_put_calls: list[str] = []
        self.lifecycle_drift: str | None = None
        self.image_details: dict[str, list[dict]] = {}
        self.preview_results: dict[str, list[dict]] = {}
        self.preview_statuses: list[str] = []
        self.preview_started: list[str] = []

    def _key(self, repository):
        return next(
            (name for name, repo in REPOSITORIES.items() if repo == repository), None
        )

    def batch_get_image(self, repositoryName, imageIds):
        key = self._key(repositoryName)
        if key is None:
            return {"images": []}
        spec = imageIds[0]
        if "imageDigest" in spec:
            if self.digests.get(key) != spec["imageDigest"]:
                return {"images": []}
            return {
                "images": [
                    {
                        "imageDigest": spec["imageDigest"],
                        "imageManifest": json.dumps(
                            {"schemaVersion": 2, "config": spec["imageDigest"]}
                        ),
                    }
                ]
            }
        tag = spec.get("imageTag")
        if self.tags.get((key, tag)) is None:
            return {"images": []}
        digest = self.tags[(key, tag)]
        return {"images": [{"imageTag": tag, "imageDigest": digest}]}

    def describe_images(self, repositoryName, imageIds=None, **kwargs):
        key = self._key(repositoryName)
        if key is None:
            raise client_error("RepositoryNotFoundException")
        if not imageIds:
            return {"imageDetails": list(self.image_details.get(repositoryName, []))}
        details = []
        for spec in imageIds:
            if "imageDigest" in spec:
                if self.digests.get(key) != spec["imageDigest"]:
                    raise client_error("ImageNotFoundException")
                details.append(
                    {
                        "imageDigest": spec["imageDigest"],
                        "imageTags": [],
                        "imageManifestMediaType": "application/vnd.oci.image.index.v1+json",
                    }
                )
            else:
                tag = spec.get("imageTag")
                digest = self.tags.get((key, tag))
                if digest is None:
                    raise client_error("ImageNotFoundException")
                details.append(
                    {
                        "imageDigest": digest,
                        "imageTags": [tag],
                        "imageManifestMediaType": "application/vnd.oci.image.index.v1+json",
                    }
                )
        return {"imageDetails": details}

    def put_image(self, repositoryName, imageTag, imageManifest):
        self.put_calls.append((repositoryName, imageTag))
        key = self._key(repositoryName)
        digest = json.loads(imageManifest.decode())["config"]
        self.tags[(key, imageTag)] = digest
        return {}

    def get_lifecycle_policy(self, repositoryName):
        if self.lifecycle_drift is not None and repositoryName in self.lifecycle_policies:
            return {"lifecyclePolicyText": self.lifecycle_drift}
        if repositoryName not in self.lifecycle_policies:
            raise client_error("LifecyclePolicyNotFoundException")
        return {"lifecyclePolicyText": self.lifecycle_policies[repositoryName]}

    def put_lifecycle_policy(self, repositoryName, lifecyclePolicyText):
        self.lifecycle_put_calls.append(repositoryName)
        self.lifecycle_policies[repositoryName] = lifecyclePolicyText
        return {}

    def start_lifecycle_policy_preview(self, repositoryName):
        self.preview_started.append(repositoryName)
        return {"lifecyclePolicyPreviewId": f"preview-{repositoryName}"}

    def get_lifecycle_policy_preview(self, repositoryName, lifecyclePolicyPreviewId):
        status = self.preview_statuses.pop(0) if self.preview_statuses else "COMPLETE"
        return {
            "status": status,
            "previewResults": list(self.preview_results.get(repositoryName, [])),
        }


class FakeS3:
    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects = objects or {}
        self.put_calls = []
        self.error = None

    def get_object(self, Bucket, Key):
        if self.error is not None:
            raise self.error
        if Key not in self.objects:
            raise client_error("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key, ChecksumMode=None):
        if self.error is not None:
            raise self.error
        if Key not in self.objects:
            raise client_error("NoSuchKey")
        body = self.objects[Key]
        return {
            "ContentLength": len(body),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(body).digest()).decode(),
        }

    def put_object(self, Bucket, Key, Body, ChecksumAlgorithm=None, ContentType=None):
        self.put_calls.append(Key)
        self.objects[Key] = Body
        return {}

    def list_objects_v2(self, Bucket, Prefix):
        contents = [
            {"Key": key, "Size": len(body)}
            for key, body in self.objects.items()
            if key.startswith(Prefix)
        ]
        return {"Contents": contents}


class FakeCloudFront:
    def __init__(self, domain_name="d1234.cloudfront.net", invalidation_id="inv-1"):
        self.domain_name = domain_name
        self.invalidation_id = invalidation_id
        self.invalidations = []

    def get_distribution(self, Id):
        return {"Distribution": {"Id": Id, "DomainName": self.domain_name}}

    def create_invalidation(self, DistributionId, InvalidationBatch):
        self.invalidations.append(InvalidationBatch)
        return {"Invalidation": {"Id": self.invalidation_id}}

    def get_invalidation(self, DistributionId, Id):
        return {"Invalidation": {"Id": Id, "Status": "Completed"}}


class FakeElb:
    def describe_load_balancers(self, Names=None):
        if not Names or Names[0] != ALB_NAME:
            raise client_error("LoadBalancerNotFound")
        return {
            "LoadBalancers": [
                {"LoadBalancerName": Names[0], "DNSName": "onlineshop-alb.example.com"}
            ]
        }

    def describe_target_health(self, TargetGroupArn):
        return {"TargetHealthDescriptions": []}


class FakeRds:
    def describe_db_instances(self, DBInstanceIdentifier=None):
        return {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "onlineshop-postgres-db",
                    "DBInstanceStatus": "available",
                    "Engine": "postgres",
                    "EngineVersion": "18.1",
                    "DBInstanceClass": "db.t4g.micro",
                }
            ]
        }


class FakeGithub:
    """Stub of the GitHubApi surface used by the promotion engine."""

    def __init__(
        self,
        *,
        run=None,
        run_artifacts=None,
        artifacts_by_run=None,
        newer_candidates=None,
        compare=None,
        releases=None,
    ):
        self.run = run or {
            "id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "head_sha": CANDIDATE_SHA,
            "head_branch": "main",
            "run_number": 4711,
            "html_url": f"https://github.com/x/y/actions/runs/{RUN_ID}",
        }
        self.run_artifacts = run_artifacts or candidate_artifact_names()
        self.artifacts_by_run = artifacts_by_run or {}
        self.newer_candidates = newer_candidates or []
        self.compare = compare or {}
        self.main_head_sha = "1" * 40
        self.releases = releases or []
        self.created_releases = []
        self.uploaded_assets = []
        self._assets = {}
        self.artifact_zips = {}

    def get_run(self, run_id):
        if run_id == STAGING_RUN_ID:
            return {
                "id": STAGING_RUN_ID,
                "run_attempt": STAGING_ATTEMPT,
                "head_sha": CANDIDATE_SHA,
                "head_branch": "main",
                "run_number": 9001,
                "html_url": f"https://github.com/x/y/actions/runs/{STAGING_RUN_ID}",
            }
        return dict(self.run, id=run_id)

    def list_run_artifacts(self, run_id, run_attempt, expected_names):
        artifacts = [
            {"id": 1000 + index, "name": name}
            for index, name in enumerate(self.run_artifacts)
            if name in expected_names
        ]
        missing = expected_names - {artifact["name"] for artifact in artifacts}
        if missing:
            raise ValidationError(
                f"missing artifacts {', '.join(sorted(missing))} "
                f"for run {run_id} attempt {run_attempt}"
            )
        return artifacts

    def list_artifacts_for_run(self, run_id, run_attempt, expected_names):
        artifacts = []
        for name, attempt in self.artifacts_by_run.get(run_id, []):
            if name in expected_names and attempt == run_attempt:
                artifacts.append(
                    {
                        "id": 2000 + len(artifacts),
                        "name": name,
                        "archive_download_url": f"https://example.com/artifacts/{name}",
                    }
                )
        missing = expected_names - {artifact["name"] for artifact in artifacts}
        if missing:
            raise ValidationError(
                f"missing artifacts {', '.join(sorted(missing))} "
                f"for run {run_id} attempt {run_attempt}"
            )
        return artifacts

    def download_artifact_zip(self, archive_url):
        name = archive_url.rsplit("/", 1)[1]
        return self.artifact_zips[name]

    def list_main_candidate_runs(self, selected_run_id, limit=10):
        return self.newer_candidates

    def get_branch_head_sha(self, branch):
        return self.main_head_sha

    def compare_commits(self, base, head):
        key = (base, head)
        if key in self.compare:
            status = self.compare[key]
            return {"status": status, "ahead_by": 0, "behind_by": 0 if status != "behind" else 1}
        if base == head:
            return {"status": "identical", "ahead_by": 0, "behind_by": 0}
        return {"status": "behind", "ahead_by": 0, "behind_by": 1}

    def list_releases(self):
        return self.releases

    def create_release(self, tag, name, body):
        release = {
            "tag_name": tag,
            "id": 3000 + len(self.created_releases),
            "html_url": f"https://github.com/x/y/releases/tag/{tag}",
            "assets": [],
        }
        self.releases.append(release)
        self.created_releases.append(tag)
        return {"id": release["id"], "html_url": release["html_url"], "tag_name": tag}

    def upload_release_asset(self, release_id, name, content):
        self.uploaded_assets.append(name)
        self._assets[f"asset://{name}"] = content
        for release in self.releases:
            if release["id"] == release_id:
                release["assets"].append(
                    {"name": name, "url": f"https://example.com/assets/{name}"}
                )
        return {"name": name, "url": f"https://example.com/assets/{name}"}

    def download_asset(self, url):
        name = url.rsplit("/", 1)[1]
        return self._assets[f"asset://{name}"]
