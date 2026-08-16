"""Offline gates for rollback preflight and execute (VR-REC-02, OP-REC-03/04, AD-14).

The fake production state runs CURRENT release-0003; the retained target is
release-0002. preflight and execute must accept only that non-current,
complete, compatible, retained official release, reject every other shape
BEFORE mutation, deploy the COMPLETE target set from the release manifest's
exact digests, never mint/move ECR tags, never touch RDS, and record a
separate rollback result with a mandatory approver.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import client_error
from fakes_production import (
    ACCOUNT,
    CLUSTER,
    REGION,
    REGISTRY,
    SECRET_ARN,
    SERVICES,
    FakeCloudFront,
    FakeEcr,
    FakeElb,
    FakeGithub,
    FakeS3,
    FakeSts,
    default_task_definition_arns,
    make_frontend_archive,
    production_identifiers,
    write_identifiers,
    write_snapshot,
)

from delivery import live_marker
from delivery.cli import main
from delivery.errors import ReadError
from delivery.models import RollbackResult

CURRENT_RELEASE = "release-0003"
TARGET_RELEASE = "release-0002"
TARGET_DIGESTS = {
    "auth": f"sha256:{'1' * 64}",
    "items": f"sha256:{'2' * 64}",
    "gateway": f"sha256:{'3' * 64}",
}
CURRENT_DIGESTS = {
    "auth": f"sha256:{'9' * 64}",
    "items": f"sha256:{'8' * 64}",
    "gateway": f"sha256:{'7' * 64}",
}
FINGERPRINT = f"{'f' * 64}"
CURRENT_MARKER = live_marker.LiveMarker(
    releaseId=CURRENT_RELEASE,
    candidateId="cand-current-000000000000",
    sourceSha="3" * 40,
    frontendSha256="4" * 64,
)


def _official_marker_doc(marker: live_marker.LiveMarker, release_id: str) -> str:
    return live_marker.marker_document(live_marker.build_official_marker(marker, release_id))


def release_manifest(
    tmp_path: Path,
    *,
    release_id: str = TARGET_RELEASE,
    digests: dict[str, str] | None = None,
    frontend_checksum: str | None = None,
    immutable_identity: str | None = None,
    fingerprint: str = FINGERPRINT,
) -> tuple[Path, dict]:
    digests = digests or TARGET_DIGESTS
    manifest = {
        "schemaVersion": "1.0",
        "releaseId": release_id,
        "candidateId": f"cand-{release_id}-111111111111",
        "source": {"fullSha": "2" * 40, "branch": "main"},
        "previousReleaseId": "release-0001",
        "promotedAt": "2026-08-10T13:00:00Z",
        "requester": "requester-login",
        "approval": {
            "evidence": "environment production approval by approver-login",
            "workflowUrl": "https://github.com/x/y/actions/runs/4711",
        },
        "artifacts": {
            "auth": {"repository": f"{REGISTRY}/onlineshop-auth", "digest": digests["auth"]},
            "items": {"repository": f"{REGISTRY}/onlineshop-items", "digest": digests["items"]},
            "gateway": {
                "repository": f"{REGISTRY}/onlineshop-api-gateway",
                "digest": digests["gateway"],
            },
            "frontend": {
                "immutableIdentity": immutable_identity or f"_releases/{release_id}/",
                "checksum": frontend_checksum or f"{'e' * 64}",
            },
            "sbom": {
                "auth": {"assetName": "auth.spdx.json", "sha256": f"{'a' * 64}"},
                "items": {"assetName": "items.spdx.json", "sha256": f"{'b' * 64}"},
                "gateway": {"assetName": "api-gateway.spdx.json", "sha256": f"{'c' * 64}"},
                "frontend": {"assetName": "frontend.spdx.json", "sha256": f"{'e' * 64}"},
            },
        },
        "compatibilityFingerprint": fingerprint,
        "staging": {"evidenceIdentity": "staging-evidence-1", "conclusion": "passed"},
        "productionVerification": {"evidenceIdentity": "vrf-1", "conclusion": "passed"},
        "rollbackCapableAtPublication": True,
    }
    path = tmp_path / f"{release_id}-manifest.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


def approval_evidence(tmp_path: Path, *, approver: str = "approver-login") -> Path:
    path = tmp_path / "approval-evidence.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "approver": approver,
                "requester": "requester-login",
                "workflowUrl": "https://github.com/x/y/actions/runs/4713",
                "approvedAt": "2026-08-16T10:00:00Z",
            }
        )
    )
    return path


def _td_body(family: str, arn: str, image: str) -> dict:
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
        "registeredAt": "2026-08-01T00:00:00Z",
        "registeredBy": f"arn:aws:iam::{ACCOUNT}:root",
        "containerDefinitions": [
            {
                "name": family,
                "image": image,
                "essential": True,
                "secrets": [
                    {"name": "DB_PASSWORD", "valueFrom": f"{SECRET_ARN}:password::"}
                ],
            }
        ],
    }


class RollbackEcs:
    """Stateful ECS: snapshot revisions pin the CURRENT digests; update_service
    switches the running digests to the registered revision's image."""

    def __init__(self):
        self.snapshot_arns = default_task_definition_arns()
        self.td_store: dict[str, dict[str, dict]] = {
            name: {
                self.snapshot_arns[name]: _td_body(
                    name,
                    self.snapshot_arns[name],
                    f"{REGISTRY}/{name}@{CURRENT_DIGESTS[key]}",
                )
            }
            for key, name in zip(("auth", "items", "gateway"), SERVICES, strict=True)
        }
        self.service_td = dict(self.snapshot_arns)
        self.digests = {
            name: CURRENT_DIGESTS[key]
            for key, name in zip(("auth", "items", "gateway"), SERVICES, strict=True)
        }
        self.register_calls: list[dict] = []
        self.update_calls: list[tuple] = []
        self.td_counter = 100

    def describe_services(self, cluster, services):
        return {
            "services": [
                {
                    "serviceName": name,
                    "clusterArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/{CLUSTER}",
                    "taskDefinition": self.service_td[name],
                    "desiredCount": 1,
                    "runningCount": 1,
                    "deployments": [
                        {
                            "id": f"deploy-{name}-1",
                            "status": "PRIMARY",
                            "rolloutState": "COMPLETED",
                            "taskDefinition": self.service_td[name],
                        }
                    ],
                }
                for name in services
            ]
        }

    def describe_task_definition(self, taskDefinition):
        family = taskDefinition.rsplit(":", 1)[0].rsplit("/", 1)[-1]
        if family not in self.td_store or taskDefinition not in self.td_store[family]:
            raise client_error("ResourceNotFoundException")
        return {"taskDefinition": self.td_store[family][taskDefinition]}

    def register_task_definition(self, **td):
        self.register_calls.append(td)
        family = td["family"]
        self.td_counter += 1
        arn = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{family}:{self.td_counter}"
        stored = {
            "taskDefinitionArn": arn,
            "revision": self.td_counter,
            "status": "ACTIVE",
            **{key: value for key, value in td.items() if key != "family"},
        }
        self.td_store[family][arn] = stored
        return {"taskDefinition": stored}

    def update_service(self, cluster, service, **kwargs):
        self.update_calls.append((service, kwargs))
        arn = kwargs["taskDefinition"]
        self.service_td[service] = arn
        image = self.td_store[service][arn]["containerDefinitions"][0]["image"]
        self.digests[service] = image.rsplit("@", 1)[-1]
        return {}

    def list_tasks(self, cluster, serviceName):
        return {"taskArns": [f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{serviceName}/1"]}

    def describe_tasks(self, cluster, tasks):
        return {
            "tasks": [
                {
                    "taskArn": task_arn,
                    "lastStatus": "RUNNING",
                    "containers": [
                        {
                            "name": task_arn.split("/")[-2],
                            "imageDigest": self.digests[task_arn.split("/")[-2]],
                        }
                    ],
                }
                for task_arn in tasks
            ]
        }


class RollbackEcr(FakeEcr):
    def __init__(self, tags: dict):
        super().__init__()
        self.tags = tags
        self.read_error = None

    def batch_get_image(self, repositoryName, imageIds):
        if self.read_error is not None:
            raise self.read_error
        return super().batch_get_image(repositoryName, imageIds)


class RollbackGithub(FakeGithub):
    def __init__(self, releases: list[dict], assets: dict[str, bytes]):
        super().__init__(releases=releases)
        self._assets = assets
        self.download_error = None
        self.downloaded: list[str] = []

    def download_asset(self, url):
        if self.download_error is not None:
            raise self.download_error
        self.downloaded.append(url)
        return super().download_asset(url)


class RollbackEnv:
    def __init__(self, monkeypatch, tmp_path):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.ids = production_identifiers()
        self.identifiers_file = write_identifiers(tmp_path, self.ids)
        _archive, _digest, self.content_checksum = make_frontend_archive(tmp_path)
        dist = tmp_path / "frontend-dist"
        index_bytes = (dist / "index.html").read_bytes()
        app_bytes = (dist / "assets" / "app.js").read_bytes()
        self.manifest_file, self.manifest = release_manifest(
            tmp_path, frontend_checksum=self.content_checksum
        )
        self.target_marker_doc = _official_marker_doc(
            live_marker.build_candidate_marker(
                candidate_id=self.manifest["candidateId"],
                source_sha=self.manifest["source"]["fullSha"],
                frontend_sha256=self.content_checksum,
            ),
            TARGET_RELEASE,
        )
        self.current_marker_doc = _official_marker_doc(CURRENT_MARKER, CURRENT_RELEASE)
        self.snapshot = write_snapshot(
            tmp_path,
            self.ids,
            digests=CURRENT_DIGESTS,
            marker_doc=self.current_marker_doc,
            release_id=CURRENT_RELEASE,
        )
        self.sts = FakeSts()
        self.ecs = RollbackEcs()
        self.ecr = RollbackEcr(
            {
                (key, TARGET_RELEASE): TARGET_DIGESTS[key]
                for key in ("auth", "items", "gateway")
            }
        )
        self.s3 = FakeS3(
            {
                self.ids["frontendLiveMarker"]: self.current_marker_doc.encode(),
                f"_releases/{TARGET_RELEASE}/index.html": index_bytes,
                f"_releases/{TARGET_RELEASE}/assets/app.js": app_bytes,
                f"_releases/{TARGET_RELEASE}/frontend.tar.gz": b"bundle-bytes",
                f"_releases/{TARGET_RELEASE}/release.json": self.target_marker_doc.encode(),
            }
        )
        self.cf = FakeCloudFront()
        self.elb = FakeElb()
        _other, release_0001 = release_manifest(tmp_path, release_id="release-0001")
        _current, release_0003 = release_manifest(tmp_path, release_id=CURRENT_RELEASE)
        self.github = RollbackGithub(
            releases=[
                {
                    "tag_name": CURRENT_RELEASE,
                    "id": 3,
                    "assets": [
                        {
                            "name": "release-manifest.json",
                            "url": "https://example.com/assets/release-manifest-0003.json",
                        }
                    ],
                },
                {
                    "tag_name": TARGET_RELEASE,
                    "id": 2,
                    "assets": [
                        {
                            "name": "release-manifest.json",
                            "url": "https://example.com/assets/release-manifest-0002.json",
                        }
                    ],
                },
                {
                    "tag_name": "release-0001",
                    "id": 1,
                    "assets": [
                        {
                            "name": "release-manifest.json",
                            "url": "https://example.com/assets/release-manifest-0001.json",
                        }
                    ],
                },
            ],
            assets={
                "asset://release-manifest-0002.json": json.dumps(self.manifest).encode(),
                "asset://release-manifest-0001.json": json.dumps(release_0001).encode(),
                "asset://release-manifest-0003.json": json.dumps(release_0003).encode(),
            },
        )
        self._install()

    def set_github_manifest(self, manifest: dict) -> None:
        self.github._assets["asset://release-manifest-0002.json"] = json.dumps(
            manifest
        ).encode()

    def _install(self):
        clients = {
            "sts": self.sts,
            "ecs": self.ecs,
            "ecr": self.ecr,
            "s3": self.s3,
            "cloudfront": self.cf,
            "elb": self.elb,
        }
        self.monkeypatch.setattr(
            "delivery.aws.context.client_for", lambda ctx, service: clients[service]
        )
        self.monkeypatch.setattr(
            "delivery.commands.rollback.GitHubApi", lambda repository, token=None: self.github
        )
        self.monkeypatch.setattr("delivery.commands.rollback._DIGEST_VERIFY_TIMEOUT", 0.6)
        self.monkeypatch.setattr("delivery.commands.rollback._DIGEST_VERIFY_INTERVAL", 0.5)
        self.monkeypatch.setattr(
            "delivery.commands.rollback._DEPLOYMENT_VISIBILITY_DELAY", 0.01
        )
        self.monkeypatch.setattr("delivery.commands.verify._PUBLIC_MARKER_TIMEOUT", 1)
        self.monkeypatch.setattr("delivery.commands.verify._FRONTEND_INDEX_TIMEOUT", 1)

        def fake_fetch(url):
            responses = {
                "http://onlineshop-alb.example.com/actuator/health": (
                    200,
                    {"Content-Type": "application/json"},
                    b'{"status":"UP"}',
                ),
                "http://onlineshop-alb.example.com/api/v1/items": (
                    200,
                    {"Content-Type": "application/json"},
                    b"[]",
                ),
                f"https://{self.cf.domain_name}/release.json": (
                    200,
                    {"Content-Type": "application/json"},
                    self.target_marker_doc.encode(),
                ),
                f"https://{self.cf.domain_name}/": (
                    200,
                    {"Content-Type": "text/html"},
                    b'<html><body><div id="root"></div></body></html>',
                ),
            }
            return responses.get(url, (404, {}, b""))

        self.monkeypatch.setattr("delivery.commands.verify._fetch", fake_fetch)

    def preflight_argv(self, *extra):
        return [
            "rollback",
            "preflight",
            "--release-id",
            TARGET_RELEASE,
            "--snapshot",
            str(self.snapshot),
            "--repository",
            "owner/repo",
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.tmp_path / "preflight-report.json"),
            "--manifest-out",
            str(self.tmp_path / "preflight-manifest.json"),
            *extra,
        ]

    def run_preflight(self) -> dict:
        assert main(self.preflight_argv()) == 0
        return json.loads((self.tmp_path / "preflight-report.json").read_text())

    def execute_argv(self, *extra):
        return [
            "rollback",
            "execute",
            "--manifest",
            str(self.manifest_file),
            "--snapshot",
            str(self.snapshot),
            "--preflight-report",
            str(self.tmp_path / "preflight-report.json"),
            "--approval",
            str(approval_evidence(self.tmp_path)),
            "--workflow-run-id",
            "4713",
            "--workflow-run-attempt",
            "1",
            "--repository",
            "owner/repo",
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.tmp_path / "rollback-result.json"),
            *extra,
        ]

    def result(self) -> dict:
        return json.loads((self.tmp_path / "rollback-result.json").read_text())


@pytest.fixture
def env(monkeypatch, tmp_path):
    return RollbackEnv(monkeypatch, tmp_path)


# ---------------------------------------------------------------------------
# preflight: valid target
# ---------------------------------------------------------------------------


def test_preflight_valid_target_passes_without_mutation(env, capsys):
    code = main(env.preflight_argv())
    assert code == 0, capsys.readouterr().err
    report = json.loads((env.tmp_path / "preflight-report.json").read_text())
    assert report["releaseId"] == TARGET_RELEASE
    assert report["target"]["authDigest"] == TARGET_DIGESTS["auth"]
    assert report["targetFrontendIdentity"] == f"_releases/{TARGET_RELEASE}/"
    assert report["snapshotReleaseId"] == CURRENT_RELEASE
    assert len(report["approvalIdentity"]) == 64
    out = capsys.readouterr().out
    assert f"roll back production to official release {TARGET_RELEASE}" in out
    # preflight is read-only everywhere
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []
    assert env.ecr.put_calls == []
    assert env.s3.put_calls == []
    # the validated official manifest is written for job B
    saved = json.loads((env.tmp_path / "preflight-manifest.json").read_text())
    assert saved["releaseId"] == TARGET_RELEASE


def test_preflight_rejects_current_release(env, capsys):
    code = main(env.preflight_argv("--release-id", CURRENT_RELEASE))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "currently running release" in err


def test_preflight_rejects_unknown_github_release(env, capsys):
    code = main(env.preflight_argv("--release-id", "release-0004"))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "no published official GitHub Release for release-0004" in err


def test_preflight_rejects_missing_manifest_asset(env, capsys):
    env.github.releases[1]["assets"] = []
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "has no release-manifest.json asset" in err


def test_preflight_rejects_ecr_digest_mismatch(env, capsys):
    env.ecr.tags[("auth", TARGET_RELEASE)] = f"sha256:{'a' * 64}"
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "not a complete retained release" in err
    assert "ECR_DIGEST_MISMATCH" in err


def test_preflight_rejects_missing_ecr_tag(env, capsys):
    env.ecr.tags = {}
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    # a missing tag is now a ReadError: bounded retries exhausted (absence
    # after a push is not provable), not a definitive ECR_TAG_NOT_FOUND
    assert "ERROR READ_ERROR" in err


def test_preflight_rejects_missing_frontend_prefix_marker(env, capsys):
    env.s3.objects.pop(f"_releases/{TARGET_RELEASE}/release.json")
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "PREFIX_MARKER_NOT_FOUND" in err


def test_preflight_rejects_prefix_marker_naming_other_release(env, capsys):
    env.s3.objects[f"_releases/{TARGET_RELEASE}/release.json"] = _official_marker_doc(
        live_marker.build_candidate_marker(
            candidate_id="cand-other-000000000000",
            source_sha="5" * 40,
            frontend_sha256=env.content_checksum,
        ),
        "release-0001",
    ).encode()
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "does not name the target release" in err


def test_preflight_rejects_incompatible_fingerprint(env, capsys):
    env.manifest["compatibilityFingerprint"] = f"{'d' * 64}"
    env.set_github_manifest(env.manifest)
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR INCOMPATIBLE" in err
    assert "INCOMPATIBLE with the current runtime" in err


def test_preflight_rejects_target_outside_window(env, capsys):
    marker_doc = _official_marker_doc(
        live_marker.LiveMarker(
            releaseId="release-0005",
            candidateId="cand-5-000000000000",
            sourceSha="6" * 40,
            frontendSha256="7" * 64,
        ),
        "release-0005",
    )
    env.snapshot = write_snapshot(
        env.tmp_path,
        env.ids,
        digests=CURRENT_DIGESTS,
        marker_doc=marker_doc,
        release_id="release-0005",
    )
    _five, release_0005 = release_manifest(env.tmp_path, release_id="release-0005")
    _four, release_0004 = release_manifest(env.tmp_path, release_id="release-0004")
    env.github.releases.extend(
        [
            {
                "tag_name": "release-0005",
                "id": 5,
                "assets": [
                    {
                        "name": "release-manifest.json",
                        "url": "https://example.com/assets/release-manifest-0005.json",
                    }
                ],
            },
            {
                "tag_name": "release-0004",
                "id": 4,
                "assets": [
                    {
                        "name": "release-manifest.json",
                        "url": "https://example.com/assets/release-manifest-0004.json",
                    }
                ],
            },
        ]
    )
    env.github._assets["asset://release-manifest-0005.json"] = json.dumps(
        release_0005
    ).encode()
    env.github._assets["asset://release-manifest-0004.json"] = json.dumps(
        release_0004
    ).encode()
    # previous-3 window of release-0005 is {0004, 0003, 0002}: release-0001
    # is outside it and must be rejected before any mutation.
    code = main(env.preflight_argv("--release-id", "release-0001"))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "outside the advertised rollback window" in err


def test_preflight_rejects_schema_change_present(env, capsys):
    code = main(env.preflight_argv("--schema-change", "present"))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "schema-changing rollback is never permitted" in err


def test_preflight_read_errors_fail_closed(env, capsys):
    env.ecr.read_error = client_error("ThrottlingException")
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR READ_ERROR" in err


def test_preflight_github_download_error_fails_closed(env, capsys):
    env.github.download_error = ReadError("network down")
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR READ_ERROR" in err


def test_preflight_rejects_snapshot_without_official_release(env, capsys):
    env.snapshot = write_snapshot(
        env.tmp_path, env.ids, digests=CURRENT_DIGESTS, marker_doc=env.current_marker_doc
    )
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "no official current release" in err


def test_preflight_rejects_live_marker_drift(env, capsys):
    env.s3.objects[env.ids["frontendLiveMarker"]] = b"drifted-marker"
    code = main(env.preflight_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "live marker drift" in err


def test_preflight_post_approval_drift_aborts(env, capsys):
    env.run_preflight()
    # a NEW valid snapshot with different task-definition ARNs: every
    # consistency check passes, but the approval identity must differ.
    drifted_arns = {
        name: f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{name}:99"
        for name in SERVICES
    }
    env.snapshot = write_snapshot(
        env.tmp_path,
        env.ids,
        digests=CURRENT_DIGESTS,
        marker_doc=env.current_marker_doc,
        release_id=CURRENT_RELEASE,
        task_definition_arns=drifted_arns,
    )
    code = main(
        env.preflight_argv(
            "--previous-report", str(env.tmp_path / "preflight-report.json")
        )
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "POST-APPROVAL DRIFT" in err


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


def test_execute_deploys_complete_target_set_and_records_result(env, capsys):
    env.run_preflight()
    code = main(env.execute_argv())
    assert code == 0, capsys.readouterr().err
    # all three backends registered with the target release's exact digests
    assert len(env.ecs.register_calls) == 3
    families = {call["family"] for call in env.ecs.register_calls}
    assert families == set(SERVICES)
    for call in env.ecs.register_calls:
        family = call["family"]
        key = {
            "onlineshop-auth": "auth",
            "onlineshop-items": "items",
            "onlineshop-api-gateway": "gateway",
        }[family]
        assert call["containerDefinitions"][0]["image"] == (
            f"{REGISTRY}/{family}@{TARGET_DIGESTS[key]}"
        )
        assert call["containerDefinitions"][0]["secrets"][0]["valueFrom"].startswith(
            "arn:aws:secretsmanager:"
        )
    updated = [service for service, _kwargs in env.ecs.update_calls]
    assert updated == SERVICES
    # frontend: live root files restored from the retained prefix, official
    # marker switched, CloudFront invalidated
    assert env.s3.objects["index.html"] == env.tmp_path.joinpath(
        "frontend-dist", "index.html"
    ).read_bytes()
    assert env.s3.objects["assets/app.js"] == env.tmp_path.joinpath(
        "frontend-dist", "assets", "app.js"
    ).read_bytes()
    live_marker_doc = env.s3.objects[env.ids["frontendLiveMarker"]].decode()
    assert live_marker_doc == env.target_marker_doc
    assert env.cf.invalidations == [
        {
            "Paths": {"Quantity": 1, "Items": ["/*"]},
            "CallerReference": env.cf.invalidations[0]["CallerReference"],
        }
    ]
    result = env.result()
    assert result["outcome"] == "completed"
    assert result["releaseId"] == TARGET_RELEASE
    assert result["fromReleaseId"] == CURRENT_RELEASE
    assert result["requester"] == "requester-login"
    assert result["approver"] == "approver-login"
    assert result["workflowRunId"] == 4713
    assert result["deploymentConclusion"] == "passed"
    assert result["verificationConclusion"] == "passed"
    assert result["restoreConclusion"] == "passed"
    assert result["fromRelease"]["authDigest"] == CURRENT_DIGESTS["auth"]
    assert result["toRelease"]["authDigest"] == TARGET_DIGESTS["auth"]
    assert result["toRelease"]["frontendChecksum"] == env.content_checksum
    assert [component["component"] for component in result["components"]] == [
        "auth",
        "items",
        "gateway",
        "frontend",
    ]
    assert {component["conclusion"] for component in result["components"]} == {"passed"}
    verification = json.loads(
        (env.tmp_path / "rollback-result-verification.json").read_text()
    )
    assert verification["conclusion"] == "passed"


def test_execute_never_mints_or_moves_ecr_tags(env, capsys):
    env.run_preflight()
    before = dict(env.ecr.tags)
    code = main(env.execute_argv())
    assert code == 0, capsys.readouterr().err
    assert env.ecr.put_calls == []
    assert env.ecr.tags == before


def test_execute_never_touches_rds(env, capsys):
    # the client_for map carries NO rds client: any RDS call would KeyError
    # and fail the command loudly.
    env.run_preflight()
    assert main(env.execute_argv()) == 0


def test_execute_dry_run_plans_without_mutation(env, capsys):
    env.run_preflight()
    code = main(env.execute_argv("--dry-run"))
    assert code == 0, capsys.readouterr().err
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []
    assert env.s3.put_calls == []
    assert env.cf.invalidations == []
    out = capsys.readouterr().out
    assert "dry-run plan for official release release-0002" in out


def test_execute_missing_approval_fails_closed(env, capsys):
    env.run_preflight()
    code = main(env.execute_argv("--approval", str(env.tmp_path / "absent.json")))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR READ_ERROR" in err
    assert env.ecs.register_calls == []
    code = main(env.execute_argv("--approval", ""))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "approver is never defaulted" in err
    assert env.ecs.register_calls == []


def test_execute_rejects_approval_without_approver(env, capsys):
    env.run_preflight()
    approval = env.tmp_path / "empty-approver.json"
    approval.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "approver": "",
                "requester": "requester-login",
                "workflowUrl": "https://github.com/x/y/actions/runs/4713",
                "approvedAt": "2026-08-16T10:00:00Z",
            }
        )
    )
    code = main(env.execute_argv("--approval", str(approval)))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert env.ecs.register_calls == []


def test_execute_re_preflight_drift_aborts_before_mutation(env, capsys):
    env.run_preflight()
    # a NEW valid snapshot whose task-definition ARNs differ: all checks
    # pass but the approval identity no longer matches the approved one.
    drifted_arns = {
        name: f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{name}:99"
        for name in SERVICES
    }
    env.snapshot = write_snapshot(
        env.tmp_path,
        env.ids,
        digests=CURRENT_DIGESTS,
        marker_doc=env.current_marker_doc,
        release_id=CURRENT_RELEASE,
        task_definition_arns=drifted_arns,
    )
    code = main(env.execute_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "POST-APPROVAL DRIFT" in err
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []
    assert env.s3.put_calls == []


def test_execute_rejects_tampered_consumed_manifest(env, capsys):
    env.run_preflight()
    tampered = json.loads(json.dumps(env.manifest))
    tampered["artifacts"]["auth"]["digest"] = f"sha256:{'b' * 64}"
    tampered_path = env.tmp_path / "tampered-manifest.json"
    tampered_path.write_text(json.dumps(tampered))
    code = main(env.execute_argv("--manifest", str(tampered_path)))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "differs from the GitHub-hosted official manifest" in err
    assert env.ecs.register_calls == []
    assert env.s3.put_calls == []


def test_execute_rejects_report_for_another_release(env, capsys):
    env.run_preflight()
    other_manifest, _other = release_manifest(env.tmp_path, release_id="release-0001")
    code = main(env.execute_argv("--manifest", str(other_manifest)))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "preflight report targets" in err
    assert env.ecs.register_calls == []


def test_execute_rejects_service_td_drift_before_mutation(env, capsys):
    env.run_preflight()
    env.ecs.service_td["onlineshop-auth"] = (
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/onlineshop-auth:99"
    )
    code = main(env.execute_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR MUTATION_VERIFY" in err
    assert "changed since the snapshot" in err
    assert env.ecs.register_calls == []
    result = env.result()
    assert result["outcome"] == "failed"
    assert result["components"][0]["conclusion"] == "failed"


def test_execute_requires_out(env, capsys):
    env.run_preflight()
    code = main(env.execute_argv("--out", ""))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "requires --out" in err


def test_execute_frontend_checksum_mismatch_never_switches_live(env, capsys):
    env.run_preflight()
    env.s3.objects[f"_releases/{TARGET_RELEASE}/index.html"] = b"tampered-bytes"
    original_marker = env.s3.objects[env.ids["frontendLiveMarker"]]
    code = main(env.execute_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "live entry point was NOT switched" in err
    # backends were deployed, but the live frontend entry point is untouched
    assert env.s3.objects[env.ids["frontendLiveMarker"]] == original_marker
    assert "index.html" not in env.s3.objects
    result = env.result()
    assert result["outcome"] == "failed"
    conclusions = {c["component"]: c["conclusion"] for c in result["components"]}
    assert conclusions["auth"] == "passed"
    assert conclusions["items"] == "passed"
    assert conclusions["gateway"] == "passed"
    assert conclusions["frontend"] == "failed"


def test_execute_verification_failure_fails_honestly(env, capsys):
    env.run_preflight()

    def fail_verify(args):
        raise ReadError("verification journey failed")

    env.monkeypatch.setattr("delivery.commands.rollback.verify_production", fail_verify)
    code = main(env.execute_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR READ_ERROR" in err
    result = env.result()
    assert result["outcome"] == "failed"
    assert result["deploymentConclusion"] == "passed"
    assert result["restoreConclusion"] == "passed"
    assert result["verificationConclusion"] == "failed"
    conclusions = {c["component"]: c["conclusion"] for c in result["components"]}
    assert set(conclusions.values()) == {"passed"}


def test_execute_result_validates_against_model(env, capsys):
    env.run_preflight()
    assert main(env.execute_argv()) == 0
    parsed = RollbackResult.model_validate(env.result())
    assert parsed.outcome == "completed"
    assert parsed.fromReleaseId == CURRENT_RELEASE
    assert parsed.releaseId == TARGET_RELEASE


def test_execute_invalid_produced_result_fails_before_write(env, capsys):
    from delivery.commands import rollback as rollback_module

    env.run_preflight()
    captured = {}

    def rejecting_validate(record):
        if isinstance(record, RollbackResult):
            captured["record"] = record
            return ["crafted: rollback result is invalid"]
        return []

    env.monkeypatch.setattr(rollback_module, "validate_record", rejecting_validate)
    code = main(env.execute_argv())
    assert code == 1
    assert isinstance(captured["record"], RollbackResult)
    assert captured["record"].outcome == "completed"
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "produced rollback result is invalid" in err
    assert not (env.tmp_path / "rollback-result.json").exists()
