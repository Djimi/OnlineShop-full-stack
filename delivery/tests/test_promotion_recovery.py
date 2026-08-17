"""Offline gates for recover (AD-13, OP-REC-01/02, VR-REC-01).

The fake production state is the POST-promotion state: services point at new
digest-pinned revisions running promoted digests and the live marker names
the promoted candidate. The pre-mutation snapshot records the previous
official release. recover must restore every changed component from the
snapshot — never from the promoted state — and never touch the database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import client_error
from fakes_production import (
    ACCOUNT,
    CLUSTER,
    DIGESTS,
    REGISTRY,
    SECRET_ARN,
    SERVICES,
    FakeCloudFront,
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

SNAPSHOT_DIGESTS = DIGESTS  # keyed auth/items/gateway
PROMOTED_DIGESTS = {
    "onlineshop-auth": f"sha256:{'1' * 64}",
    "onlineshop-items": f"sha256:{'2' * 64}",
    "onlineshop-api-gateway": f"sha256:{'3' * 64}",
}
PROMOTED_MARKER_DOC = live_marker.marker_document(
    live_marker.build_candidate_marker(
        candidate_id="cand-new-000000000000",
        source_sha="c" * 40,
        frontend_sha256="d" * 64,
    )
)


def _official_marker(content_checksum: str) -> live_marker.LiveMarker:
    return live_marker.LiveMarker(
        releaseId="release-0001",
        candidateId="cand-old-000000000000",
        sourceSha="a" * 40,
        frontendSha256=content_checksum,
    )


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


class RecoveryEcs:
    """Post-promotion ECS state: services run promoted revisions; the snapshot
    revisions remain describable so recover can re-register them."""

    def __init__(self, snapshot_td_images: dict[str, str] | None = None):
        snapshot_td_images = snapshot_td_images or {
            name: f"{REGISTRY}/{name}@{SNAPSHOT_DIGESTS[key]}"
            for key, name in zip(("auth", "items", "gateway"), SERVICES, strict=True)
        }
        self.snapshot_arns = default_task_definition_arns()
        self.promoted_arns = {
            name: f"arn:aws:ecs:eu-north-1:{ACCOUNT}:task-definition/{name}:99"
            for name in SERVICES
        }
        self.td_store: dict[str, dict[str, dict]] = {
            name: {
                self.snapshot_arns[name]: _td_body(
                    name, self.snapshot_arns[name], snapshot_td_images[name]
                ),
                self.promoted_arns[name]: _td_body(
                    name,
                    self.promoted_arns[name],
                    f"{REGISTRY}/{name}@{PROMOTED_DIGESTS[name]}",
                ),
            }
            for name in SERVICES
        }
        self.service_td = {name: self.promoted_arns[name] for name in SERVICES}
        self.digests = dict(PROMOTED_DIGESTS)
        self.register_calls: list[dict] = []
        self.update_calls: list[tuple] = []
        self.td_counter = 100
        self.read_error = None

    def describe_services(self, cluster, services):
        if self.read_error is not None:
            raise self.read_error
        return {
            "services": [
                {
                    "serviceName": name,
                    "clusterArn": f"arn:aws:ecs:eu-north-1:{ACCOUNT}:cluster/{CLUSTER}",
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
        arn = f"arn:aws:ecs:eu-north-1:{ACCOUNT}:task-definition/{family}:{self.td_counter}"
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
        family = service
        image = self.td_store[family][arn]["containerDefinitions"][0]["image"]
        self.digests[service] = image.rsplit("@", 1)[-1]
        return {}

    def list_tasks(self, cluster, serviceName):
        return {"taskArns": [f"arn:aws:ecs:eu-north-1:{ACCOUNT}:task/{serviceName}/1"]}

    def describe_tasks(self, cluster, tasks):
        return {
            "tasks": [
                {
                    "taskArn": task_arn,
                    "taskDefinitionArn": self.service_td[task_arn.split("/")[-2]],
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


class RecoverEnv:
    def __init__(self, monkeypatch, tmp_path, snapshot_raw: dict | None = None):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.ids = production_identifiers()
        self.identifiers_file = write_identifiers(tmp_path, self.ids)
        _archive, _digest, self.content_checksum = make_frontend_archive(tmp_path)
        dist = tmp_path / "frontend-dist"
        self.index_bytes = (dist / "index.html").read_bytes()
        self.app_bytes = (dist / "assets" / "app.js").read_bytes()
        self.official_marker = _official_marker(self.content_checksum)
        self.official_marker_doc = live_marker.marker_document(self.official_marker)
        self.prefix = f"{self.ids['frontendReleasesPrefix']}release-0001/"
        self.snapshot = write_snapshot(
            tmp_path,
            self.ids,
            digests=SNAPSHOT_DIGESTS,
            marker_doc=self.official_marker_doc,
            release_id="release-0001",
            task_definition_arns=default_task_definition_arns(),
        )
        if snapshot_raw is not None:
            self.snapshot.write_text(json.dumps(snapshot_raw))
        self.sts = FakeSts()
        self.ecs = RecoveryEcs()
        self.s3 = FakeS3(
            {
                self.ids["frontendLiveMarker"]: PROMOTED_MARKER_DOC.encode(),
                f"{self.prefix}index.html": self.index_bytes,
                f"{self.prefix}assets/app.js": self.app_bytes,
                f"{self.prefix}frontend.tar.gz": b"bundle-bytes",
                f"{self.prefix}release.json": self.official_marker_doc.encode(),
            }
        )
        self.cf = FakeCloudFront()
        self._install()

    def _install(self):
        clients = {
            "sts": self.sts,
            "ecs": self.ecs,
            "s3": self.s3,
            "cloudfront": self.cf,
        }
        self.monkeypatch.setattr(
            "delivery.aws.context.client_for", lambda ctx, service: clients[service]
        )

    def changed_file(self, names) -> Path:
        path = self.tmp_path / "changed.json"
        if isinstance(names, (list, dict)):
            path.write_text(json.dumps(names))
        else:
            path.write_text(names)
        return path

    def argv(self, names, *extra):
        return [
            "recover",
            "--snapshot",
            str(self.snapshot),
            "--changed",
            str(self.changed_file(names)),
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.tmp_path / "recovery-result.json"),
            *extra,
        ]

    def result(self) -> dict:
        return json.loads((self.tmp_path / "recovery-result.json").read_text())


@pytest.fixture
def env(monkeypatch, tmp_path):
    return RecoverEnv(monkeypatch, tmp_path)


def test_recover_backends_restores_snapshot_revisions(env, capsys):
    code = main(env.argv(["auth", "items"]))
    assert code == 0, capsys.readouterr().err
    assert len(env.ecs.register_calls) == 2
    families = {call["family"] for call in env.ecs.register_calls}
    assert families == {"onlineshop-auth", "onlineshop-items"}
    for call in env.ecs.register_calls:
        container = call["containerDefinitions"][0]
        assert container["image"].rsplit("@", 1)[-1] in {
            SNAPSHOT_DIGESTS["auth"],
            SNAPSHOT_DIGESTS["items"],
        }
        assert container["secrets"][0]["valueFrom"].startswith(
            "arn:aws:secretsmanager:eu-north-1:799111666795:secret:"
        )
    updated = {service for service, _kwargs in env.ecs.update_calls}
    assert updated == {"onlineshop-auth", "onlineshop-items"}
    # running state observed == snapshot digests (the promoted digests are gone)
    assert env.ecs.digests["onlineshop-auth"] == SNAPSHOT_DIGESTS["auth"]
    assert env.ecs.digests["onlineshop-items"] == SNAPSHOT_DIGESTS["items"]
    result = env.result()
    assert result["outcome"] == "completed"
    assert [c["component"] for c in result["components"]] == ["auth", "items"]
    assert {c["conclusion"] for c in result["components"]} == {"restored"}
    assert result["originalFailure"] == "unknown"


def test_recover_gateway_restores_snapshot_revision(env, capsys):
    code = main(env.argv(["gateway"]))
    assert code == 0, capsys.readouterr().err
    assert {call["family"] for call in env.ecs.register_calls} == {
        "onlineshop-api-gateway"
    }
    assert {service for service, _ in env.ecs.update_calls} == {
        "onlineshop-api-gateway"
    }
    assert env.ecs.digests["onlineshop-api-gateway"] == SNAPSHOT_DIGESTS["gateway"]
    assert env.result()["outcome"] == "completed"


def test_recover_frontend_restores_live_root_from_retained_prefix(env, capsys):
    code = main(env.argv(["frontend"]))
    assert code == 0, capsys.readouterr().err
    # live-root dist files restored from the retained prefix
    assert env.s3.objects["index.html"] == env.index_bytes
    assert env.s3.objects["assets/app.js"] == env.app_bytes
    # index.html last among dist files, marker after index.html
    put_keys = list(env.s3.put_calls)
    assert put_keys == [
        "assets/app.js",
        "index.html",
        env.ids["frontendLiveMarker"],
    ]
    # official marker restored from the snapshot identity
    assert env.s3.objects[env.ids["frontendLiveMarker"]].decode() == env.official_marker_doc
    assert env.cf.invalidations == [
        {
            "Paths": {"Quantity": 1, "Items": ["/*"]},
            "CallerReference": env.cf.invalidations[0]["CallerReference"],
        }
    ]
    result = env.result()
    assert result["outcome"] == "completed"
    assert result["components"][0]["component"] == "frontend"
    assert result["components"][0]["conclusion"] == "restored"
    assert "restored from the retained prefix" in result["components"][0]["detail"]


def test_recover_frontend_checksum_mismatch_fails_before_live_switch(env, capsys):
    env.s3.objects[f"{env.prefix}index.html"] = b"tampered-bytes"
    original_marker = env.s3.objects[env.ids["frontendLiveMarker"]]
    code = main(env.argv(["frontend"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "live entry point was NOT switched" in err
    # no partial live switch: no dist file copied, marker untouched, no
    # invalidation, and the outcome is honestly failed (never completed)
    assert env.s3.put_calls == []
    assert env.s3.objects[env.ids["frontendLiveMarker"]] == original_marker
    assert "index.html" not in env.s3.objects
    assert env.cf.invalidations == []
    result = env.result()
    assert result["outcome"] == "failed"
    assert result["components"][0]["conclusion"] == "failed"
    assert "NOT switched" in result["failureDetail"]


def test_recover_frontend_missing_prefix_fails_closed(env, capsys):
    for key in list(env.s3.objects):
        if key.startswith(env.prefix):
            del env.s3.objects[key]
    original_marker = env.s3.objects[env.ids["frontendLiveMarker"]]
    code = main(env.argv(["frontend"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "no index.html" in err
    assert env.s3.put_calls == []
    assert env.s3.objects[env.ids["frontendLiveMarker"]] == original_marker
    assert env.cf.invalidations == []
    assert env.result()["outcome"] == "failed"


def test_recover_frontend_without_official_release_fails_closed(env, capsys):
    import hashlib

    candidate_marker_doc = live_marker.marker_document(
        live_marker.build_candidate_marker(
            candidate_id="cand-old-000000000000",
            source_sha="a" * 40,
            frontend_sha256=env.content_checksum,
        )
    )
    raw = json.loads(env.snapshot.read_text())
    raw["release"] = {"status": "none", "releaseId": None, "manifestSha256": None}
    raw["frontend"]["immutableIdentity"] = candidate_marker_doc
    raw["frontend"]["checksum"] = hashlib.sha256(candidate_marker_doc.encode()).hexdigest()
    env.snapshot.write_text(json.dumps(raw))
    code = main(env.argv(["frontend"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "no official release identity" in err
    assert env.s3.put_calls == []
    assert env.cf.invalidations == []
    assert env.result()["outcome"] == "failed"


def test_recover_frontend_failure_never_hides_backend_restores(env, capsys):
    # the frontend restore fails closed while backends before it are still
    # restored and reported honestly per component
    import hashlib

    candidate_marker_doc = live_marker.marker_document(
        live_marker.build_candidate_marker(
            candidate_id="cand-old-000000000000",
            source_sha="a" * 40,
            frontend_sha256=env.content_checksum,
        )
    )
    raw = json.loads(env.snapshot.read_text())
    raw["release"] = {"status": "none", "releaseId": None, "manifestSha256": None}
    raw["frontend"]["immutableIdentity"] = candidate_marker_doc
    raw["frontend"]["checksum"] = hashlib.sha256(candidate_marker_doc.encode()).hexdigest()
    env.snapshot.write_text(json.dumps(raw))
    code = main(env.argv(["auth", "frontend"]))
    assert code == 1
    assert capsys.readouterr().err
    result = env.result()
    assert result["outcome"] == "failed"
    conclusions = {c["component"]: c["conclusion"] for c in result["components"]}
    assert conclusions["auth"] == "restored"
    assert conclusions["frontend"] == "failed"


def test_recover_all_components_canonical_order(env, capsys):
    code = main(env.argv(["frontend", "auth"]))
    assert code == 0, capsys.readouterr().err
    result = env.result()
    assert [c["component"] for c in result["components"]] == ["auth", "frontend"]
    assert {c["conclusion"] for c in result["components"]} == {"restored"}


def test_recover_records_the_original_failure_separately(env, capsys):
    code = main(
        [
            *env.argv(["gateway"]),
            "--original-failure",
            "promotion failed (run 4711, attempt 1)",
        ]
    )
    assert code == 0, capsys.readouterr().err
    assert (
        env.result()["originalFailure"] == "promotion failed (run 4711, attempt 1)"
    )


def test_recover_already_at_snapshot_revision_verifies_without_mutation(env, capsys):
    for name in SERVICES:
        env.ecs.service_td[name] = env.ecs.snapshot_arns[name]
        env.ecs.digests[name] = {
            "onlineshop-auth": SNAPSHOT_DIGESTS["auth"],
            "onlineshop-items": SNAPSHOT_DIGESTS["items"],
            "onlineshop-api-gateway": SNAPSHOT_DIGESTS["gateway"],
        }[name]
    code = main(env.argv(["auth", "items", "gateway"]))
    assert code == 0, capsys.readouterr().err
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []
    assert all("already at snapshot revision" in c["detail"] for c in env.result()["components"])


@pytest.mark.parametrize(
    "names",
    [
        ["authh"],
        ["frontnd"],
        ["db"],
        [],
        ["auth", "auth"],
        ["auth", "items", "auth"],
        {"auth": True},
        [42],
    ],
)
def test_recover_rejects_invalid_changed_arrays(env, capsys, names):
    code = main(env.argv(names))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []
    assert env.s3.put_calls == []


def test_recover_rejects_malformed_changed_file(env, capsys):
    path = env.tmp_path / "changed.json"
    path.write_text("{not json")
    code = main(
        [
            "recover",
            "--snapshot",
            str(env.snapshot),
            "--changed",
            str(path),
            "--environment",
            "production",
            "--identifiers",
            str(env.identifiers_file),
        ]
    )
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_recover_rejects_snapshot_of_staging_environment(env, capsys):
    raw = json.loads(env.snapshot.read_text())
    raw["environment"] = "staging"
    env.snapshot.write_text(json.dumps(raw))
    code = main(env.argv(["auth"]))
    assert code == 1
    assert "environment 'production'" in capsys.readouterr().err
    assert env.ecs.register_calls == []


def test_recover_rejects_staging_environment_flag(env, capsys):
    code = main(env.argv(["auth"], "--environment", "staging"))
    assert code == 1
    assert "environment 'staging'" in capsys.readouterr().err


def test_recover_ambiguous_snapshot_td_digest_mismatch_stops_without_mutation(
    env, capsys
):
    for name in SERVICES:
        env.ecs.td_store[name][env.ecs.snapshot_arns[name]]["containerDefinitions"][0][
            "image"
        ] = f"{REGISTRY}/{name}@{PROMOTED_DIGESTS[name]}"
    code = main(env.argv(["auth", "items"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "inconsistent restore target" in err
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []


def test_recover_ambiguous_snapshot_multiple_running_digests_stops(env, capsys):
    raw = json.loads(env.snapshot.read_text())
    raw["services"]["auth"]["runningDigests"] = [SNAPSHOT_DIGESTS["auth"], "sha256:99" * 9]
    env.snapshot.write_text(json.dumps(raw))
    code = main(env.argv(["auth"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "exactly one observed digest" in err
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []


def test_recover_ambiguous_snapshot_unpinned_td_stops(env, capsys):
    for name in SERVICES:
        env.ecs.td_store[name][env.ecs.snapshot_arns[name]]["containerDefinitions"][0][
            "image"
        ] = f"{REGISTRY}/{name}:old-tag"
    code = main(env.argv(["auth"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "not digest-pinned" in err
    assert env.ecs.register_calls == []


def test_recover_ambiguous_snapshot_frontend_checksum_stops_before_write(env, capsys):
    raw = json.loads(env.snapshot.read_text())
    raw["frontend"]["checksum"] = "0" * 64
    env.snapshot.write_text(json.dumps(raw))
    code = main(env.argv(["frontend"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "checksum does not match" in err
    assert env.s3.put_calls == []
    assert env.cf.invalidations == []


def test_recover_ambiguous_snapshot_marker_release_mismatch_stops(env, capsys):
    raw = json.loads(env.snapshot.read_text())
    raw["frontend"]["immutableIdentity"] = live_marker.marker_document(
        live_marker.LiveMarker(
            releaseId="release-0009",
            candidateId=env.official_marker.candidateId,
            sourceSha=env.official_marker.sourceSha,
            frontendSha256=env.official_marker.frontendSha256,
        )
    )
    import hashlib

    raw["frontend"]["checksum"] = hashlib.sha256(
        raw["frontend"]["immutableIdentity"].encode()
    ).hexdigest()
    env.snapshot.write_text(json.dumps(raw))
    code = main(env.argv(["frontend"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "inconsistent snapshot" in err
    assert env.s3.put_calls == []


def test_recover_ambiguous_snapshot_frontend_identifier_drift_stops(env, capsys):
    raw = json.loads(env.snapshot.read_text())
    raw["frontend"]["cloudfrontDistributionId"] = "E9999999999"
    env.snapshot.write_text(json.dumps(raw))
    code = main(env.argv(["frontend"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "does not match identifiers" in err
    assert env.s3.put_calls == []


def test_recover_snapshot_missing_service_observation_stops(env, capsys):
    raw = json.loads(env.snapshot.read_text())
    del raw["services"]["items"]
    env.snapshot.write_text(json.dumps(raw))
    code = main(env.argv(["items"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "no observation" in err
    assert env.ecs.register_calls == []


def test_recover_never_touches_rds_or_database(env, capsys):
    clients = {
        "sts": env.sts,
        "ecs": env.ecs,
        "s3": env.s3,
        "cloudfront": env.cf,
    }

    def guarded(ctx, service):
        if service == "rds":
            raise AssertionError("recover must never create an RDS client")
        return clients[service]

    env.monkeypatch.setattr("delivery.aws.context.client_for", guarded)
    code = main(env.argv(["auth", "items", "gateway", "frontend"]))
    assert code == 0, capsys.readouterr().err
    assert env.result()["outcome"] == "completed"


def test_recover_frontend_read_back_checksum_mismatch_never_invalidates(env, capsys):
    from delivery.commands import recover as recover_module

    real_checksum = recover_module.get_object_sha256

    def tampered(client, bucket, key):
        if key == env.ids["frontendLiveMarker"]:
            return "9" * 64
        return real_checksum(client, bucket, key)

    env.monkeypatch.setattr("delivery.commands.recover.get_object_sha256", tampered)
    code = main(env.argv(["frontend"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR MUTATION_VERIFY" in err
    assert "checksum read-back" in err
    assert env.cf.invalidations == []
    assert env.result()["outcome"] == "failed"


def test_recover_service_describe_read_error_stops_with_evidence(env, capsys):
    env.ecs.read_error = client_error("InternalFailure", "describe exploded")
    code = main(env.argv(["auth"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR READ_ERROR" in err
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []
    result = env.result()
    assert result["outcome"] == "failed"
    assert "READ_ERROR" in result["failureDetail"]
    assert result["components"][0]["conclusion"] == "failed"


def test_recover_task_definition_read_error_is_never_absence(env, capsys):
    original = env.ecs.describe_task_definition

    def failing(taskDefinition):
        if taskDefinition == env.ecs.snapshot_arns["onlineshop-auth"]:
            raise client_error("AccessDenied", "denied")
        return original(taskDefinition)

    env.ecs.describe_task_definition = failing
    code = main(env.argv(["auth"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR READ_ERROR" in err
    assert env.ecs.register_calls == []
    assert env.result()["outcome"] == "failed"


def test_recover_non_full_arn_secret_never_updates_service(env, capsys):
    for name in SERVICES:
        env.ecs.td_store[name][env.ecs.snapshot_arns[name]]["containerDefinitions"][0][
            "secrets"
        ] = [{"name": "DB_PASSWORD", "valueFrom": "plaintext-password"}]
    code = main(env.argv(["auth"]))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR MUTATION_VERIFY" in err
    assert "non-full-ARN secrets[].valueFrom" in err
    assert len(env.ecs.register_calls) == 1
    assert env.ecs.update_calls == []
    assert env.result()["outcome"] == "failed"


def test_recover_dry_run_plans_without_mutation(env, capsys):
    code = main(env.argv(["auth", "frontend"], "--dry-run"))
    assert code == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "would re-register the snapshot revision" in out
    assert "would restore the live-root dist files" in out
    assert "would restore the live marker" not in out
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []
    assert env.s3.put_calls == []
    assert env.cf.invalidations == []
    assert not (env.tmp_path / "recovery-result.json").exists()


def test_recover_failure_result_is_written_for_evidence(env, capsys):
    env.ecs.read_error = client_error("InternalFailure", "describe exploded")
    code = main(env.argv(["auth", "items"]))
    assert code == 1
    result = env.result()
    assert result["outcome"] == "failed"
    assert [c["component"] for c in result["components"]] == ["auth", "items"]
    assert result["components"][0]["conclusion"] == "failed"
    assert result["components"][1]["conclusion"] == "not-attempted"
    assert result["completedAt"] is not None


def test_recover_invalid_produced_result_fails_before_write(env, capsys):
    from delivery.commands import recover as recover_module
    from delivery.models import RecoveryResult

    captured = {}

    def rejecting_validate(record):
        if isinstance(record, RecoveryResult):
            captured["record"] = record
            return ["crafted: outcome must be completed or failed"]
        return []

    env.monkeypatch.setattr(recover_module, "validate_record", rejecting_validate)
    code = main(env.argv(["auth"]))
    assert code == 1
    assert isinstance(captured["record"], RecoveryResult)
    assert captured["record"].outcome == "completed"
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "produced recovery result is invalid" in err
    assert not (env.tmp_path / "recovery-result.json").exists()
