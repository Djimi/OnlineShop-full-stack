"""Offline gates for deploy backends/gateway/frontend (VR-PRO-02, OP-DEP-01..03)."""

from __future__ import annotations

import json

import pytest
from fakes_production import (
    DIGESTS,
    REGISTRY,
    FakeCloudFront,
    FakeEcr,
    FakeEcs,
    FakeGithub,
    FakeS3,
    FakeSts,
    default_task_definition_arns,
    main_candidate,
    make_frontend_archive,
    production_identifiers,
    write_identifiers,
    write_snapshot,
)

from delivery.cli import main
from delivery.errors import MutationVerificationError


class DeployEnv:
    def __init__(self, monkeypatch, tmp_path):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.ids = production_identifiers()
        self.identifiers_file = write_identifiers(tmp_path, self.ids)
        self.archive, self.artifact_digest, self.content_checksum = make_frontend_archive(tmp_path)
        self.candidate = main_candidate(
            tmp_path,
            artifact_digest=self.artifact_digest,
            content_checksum_value=self.content_checksum,
        )
        self.candidate_raw = json.loads(self.candidate.read_text())
        self.snapshot = write_snapshot(
            tmp_path,
            self.ids,
            digests=DIGESTS,
            marker_doc=self._marker(),
        )
        self.sts = FakeSts()
        self.ecs = FakeEcs(digests=DIGESTS)
        self.ecr = FakeEcr(digests=DIGESTS)
        self.s3 = FakeS3({self.ids["frontendLiveMarker"]: self._marker().encode()})
        self.cf = FakeCloudFront()
        self.github = FakeGithub(releases=[])
        self._install()

    def _marker(self) -> str:
        from fakes_production import CANDIDATE_SHA

        from delivery.live_marker import LiveMarker, marker_document

        marker = LiveMarker(
            releaseId=None,
            candidateId=self.candidate_raw["candidateId"],
            sourceSha=CANDIDATE_SHA,
            frontendSha256=self.content_checksum,
        )
        return marker_document(marker)

    def _install(self):
        clients = {
            "sts": self.sts,
            "ecs": self.ecs,
            "ecr": self.ecr,
            "s3": self.s3,
            "cloudfront": self.cf,
        }
        self.monkeypatch.setattr(
            "delivery.aws.context.client_for", lambda ctx, service: clients[service]
        )
        self.monkeypatch.setattr(
            "delivery.commands.deploy.GitHubApi",
            lambda repository, token=None: self.github,
        )
        self.monkeypatch.setattr("delivery.commands.deploy._DIGEST_VERIFY_TIMEOUT", 0.6)
        self.monkeypatch.setattr("delivery.commands.deploy._DIGEST_VERIFY_INTERVAL", 0.5)
        self.monkeypatch.setattr(
            "delivery.commands.deploy._DEPLOYMENT_VISIBILITY_DELAY", 0.01
        )

    def backends_argv(self, *extra):
        return [
            "deploy",
            "backends",
            "--candidate",
            str(self.candidate),
            "--snapshot",
            str(self.snapshot),
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            *extra,
        ]

    def gateway_argv(self, *extra):
        return [
            "deploy",
            "gateway",
            "--candidate",
            str(self.candidate),
            "--snapshot",
            str(self.snapshot),
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            *extra,
        ]

    def frontend_argv(self, *extra):
        return [
            "deploy",
            "frontend",
            "--candidate",
            str(self.candidate),
            "--snapshot",
            str(self.snapshot),
            "--frontend-archive",
            str(self.archive),
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.tmp_path / "frontend-publish.json"),
            *extra,
        ]


@pytest.fixture
def env(monkeypatch, tmp_path):
    return DeployEnv(monkeypatch, tmp_path)


def test_deploy_backends_registers_and_updates_auth_and_items(env, capsys):
    code = main(env.backends_argv())
    assert code == 0, capsys.readouterr().err
    assert len(env.ecs.register_calls) == 2
    families = {call["family"] for call in env.ecs.register_calls}
    assert families == {"onlineshop-auth", "onlineshop-items"}
    updated = {service for service, _kwargs in env.ecs.update_calls}
    assert updated == {"onlineshop-auth", "onlineshop-items"}
    for call in env.ecs.register_calls:
        family = call["family"]
        container = call["containerDefinitions"][0]
        key = {"onlineshop-auth": "auth", "onlineshop-items": "items"}[family]
        assert container["image"] == f"{REGISTRY}/{family}@{DIGESTS[key]}"
        # secrets stay full-ARN valueFrom references
        assert container["secrets"][0]["valueFrom"].startswith(
            "arn:aws:secretsmanager:eu-north-1:799111666795:secret:"
        )


def test_deploy_gateway_runs_after_backends(env, capsys):
    assert main(env.backends_argv()) == 0
    assert main(env.gateway_argv()) == 0, capsys.readouterr().err
    updated = [service for service, _kwargs in env.ecs.update_calls]
    assert updated == ["onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"]
    assert len(env.ecs.register_calls) == 3


def test_deploy_backends_dry_run_plans_without_mutation(env, capsys):
    code = main(env.backends_argv("--dry-run"))
    assert code == 0, capsys.readouterr().err
    assert env.ecs.register_calls == []
    assert env.ecs.update_calls == []


def test_deploy_backends_already_deployed_is_idempotent(env, capsys):
    assert main(env.backends_argv()) == 0
    registered = len(env.ecs.register_calls)
    # a fresh snapshot after the first deploy observes the new revisions
    arns = {name: env.ecs._service(name)["taskDefinition"] for name in env.ids["services"]}
    env.snapshot = write_snapshot(
        env.tmp_path,
        env.ids,
        digests=DIGESTS,
        marker_doc=env._marker(),
        task_definition_arns=arns,
    )
    code = main(env.backends_argv())
    assert code == 0, capsys.readouterr().err
    assert len(env.ecs.register_calls) == registered
    assert "already running" in capsys.readouterr().out


def test_deploy_backends_rejects_service_drift_from_snapshot(env, capsys):
    drifted = {
        "onlineshop-auth": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:99",
        "onlineshop-items": default_task_definition_arns()["onlineshop-items"],
        "onlineshop-api-gateway": default_task_definition_arns()["onlineshop-api-gateway"],
    }
    env.snapshot = write_snapshot(
        env.tmp_path,
        env.ids,
        digests=DIGESTS,
        marker_doc=env._marker(),
        task_definition_arns=drifted,
    )
    code = main(env.backends_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR MUTATION_VERIFY" in err
    assert "changed since the snapshot" in err
    assert env.ecs.register_calls == []


def test_deploy_backends_rejects_non_image_only_change(env, capsys):
    def refuse(td, images):
        raise MutationVerificationError("not an image-only change")

    env.monkeypatch.setattr("delivery.commands.deploy.replace_container_images", refuse)
    code = main(env.backends_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR MUTATION_VERIFY" in err
    assert "image-only" in err
    assert env.ecs.update_calls == []


def test_deploy_backends_rejects_running_digest_mismatch(env, capsys):
    env.ecs.digests = {
        "onlineshop-auth": f"sha256:{'9' * 64}",
        "onlineshop-items": f"sha256:{'8' * 64}",
        "onlineshop-api-gateway": f"sha256:{'7' * 64}",
    }
    code = main(env.backends_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR MUTATION_VERIFY" in err
    assert "running digests" in err


def test_deploy_requires_production_environment(env, capsys):
    code = main(env.backends_argv("--environment", "staging"))
    assert code == 1
    assert "environment 'staging'" in capsys.readouterr().err


def test_deploy_frontend_publishes_prefix_and_switches_live(env, capsys):
    code = main(env.frontend_argv())
    assert code == 0, capsys.readouterr().err
    keys = set(env.s3.objects)
    assert "_releases/release-0001/index.html" in keys
    assert "_releases/release-0001/assets/app.js" in keys
    assert "_releases/release-0001/frontend.tar.gz" in keys
    assert "_releases/release-0001/release.json" in keys
    # live entry point switched: root index + assets + marker (candidate-named)
    assert "index.html" in keys
    assert "assets/app.js" in keys
    live_marker = env.s3.objects[env.ids["frontendLiveMarker"]].decode()
    assert env.candidate_raw["candidateId"] in live_marker
    assert '"releaseId":null' in live_marker
    assert env.cf.invalidations == [
        {
            "Paths": {"Quantity": 1, "Items": ["/*"]},
            "CallerReference": env.cf.invalidations[0]["CallerReference"],
        }
    ]
    publish = json.loads((env.tmp_path / "frontend-publish.json").read_text())
    assert publish["provisionalReleaseId"] == "release-0001"
    assert publish["candidateId"] == env.candidate_raw["candidateId"]
    assert publish["prefixKey"] == "_releases/release-0001/"


def test_deploy_frontend_checksum_mismatch_never_switches_live(env, capsys):
    original = env.s3.objects.get(env.ids["frontendLiveMarker"])
    from delivery.commands import deploy as deploy_module

    real_get = deploy_module.get_object_sha256

    def tampered(client, bucket, key):
        if key.startswith("_releases/"):
            return "0" * 64
        return real_get(client, bucket, key)

    env.monkeypatch.setattr("delivery.commands.deploy.get_object_sha256", tampered)
    code = main(env.frontend_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR MUTATION_VERIFY" in err
    assert "live entry point was NOT switched" in err
    # no root objects were written; the previous live marker is untouched
    assert "index.html" not in env.s3.objects
    assert env.s3.objects.get(env.ids["frontendLiveMarker"]) == original


def test_deploy_frontend_marker_drift_aborts_before_publish(env, capsys):
    env.s3.objects[env.ids["frontendLiveMarker"]] = b"drifted-marker"
    code = main(env.frontend_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR MUTATION_VERIFY" in err
    assert "changed since the snapshot" in err
    assert not any(key.startswith("_releases/") for key in env.s3.objects)


def test_deploy_frontend_dry_run_mutates_nothing(env, capsys):
    code = main(env.frontend_argv("--dry-run"))
    assert code == 0, capsys.readouterr().err
    assert env.s3.put_calls == []
    assert env.cf.invalidations == []


def test_deploy_frontend_uses_next_provisional_release_id(env, capsys):
    env.github.releases = [
        {"tag_name": "release-0003", "id": 1, "assets": []},
        {"tag_name": "release-0001", "id": 2, "assets": []},
    ]
    code = main(env.frontend_argv())
    assert code == 0, capsys.readouterr().err
    assert "_releases/release-0004/index.html" in env.s3.objects
    publish = json.loads((env.tmp_path / "frontend-publish.json").read_text())
    assert publish["provisionalReleaseId"] == "release-0004"


def test_deploy_frontend_rejects_foreign_prefix_marker(env, capsys):
    from delivery.live_marker import LiveMarker, marker_document

    foreign = marker_document(
        LiveMarker(
            releaseId=None,
            candidateId="cand-other",
            sourceSha="3" * 40,
            frontendSha256="c" * 64,
        )
    )
    env.s3.objects["_releases/release-0001/release.json"] = foreign.encode()
    code = main(env.frontend_argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR MUTATION_VERIFY" in err
    assert "refusing to overwrite the provisional prefix" in err
    # nothing else of the provisional prefix was written and the live entry
    # point was never switched
    assert "_releases/release-0001/index.html" not in env.s3.objects
    assert env.cf.invalidations == []
    assert env.s3.objects["_releases/release-0001/release.json"].decode() == foreign


def test_deploy_frontend_resumes_identity_matching_prefix_marker(env, capsys):
    env.s3.objects["_releases/release-0001/release.json"] = env._marker().encode()
    code = main(env.frontend_argv())
    assert code == 0, capsys.readouterr().err
    assert "_releases/release-0001/index.html" in env.s3.objects


def test_deploy_backends_accepts_transient_draining_overlap(env, capsys):
    """OP-DEP-02: after the deployment waiter succeeds, a min-100%/max-200%
    overlap (new PRIMARY + old draining task) converges to the candidate."""

    class DrainingEcs(FakeEcs):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.draining = True

        def list_tasks(self, cluster, serviceName):
            if self.draining:
                return {
                    "taskArns": [
                        f"arn:aws:ecs:eu-north-1:799111666795:task/{serviceName}/1",
                        f"arn:aws:ecs:eu-north-1:799111666795:task/{serviceName}/2",
                    ]
                }
            return {
                "taskArns": [f"arn:aws:ecs:eu-north-1:799111666795:task/{serviceName}/1"]
            }

        def describe_tasks(self, cluster, tasks):
            described = []
            for task_arn in tasks:
                service = task_arn.split("/")[-2]
                digest = self.digests[service]
                if self.draining and task_arn.endswith("/2"):
                    digest = f"sha256:{'1' * 64}"
                described.append(
                    {
                        "taskArn": task_arn,
                        "lastStatus": "RUNNING",
                        "containers": [{"name": service, "imageDigest": digest}],
                    }
                )
            self.draining = False
            return {"tasks": described}

    env.ecs = DrainingEcs(digests=DIGESTS)
    env._install()
    code = main(env.backends_argv())
    assert code == 0, capsys.readouterr().err
    updated = {service for service, _kwargs in env.ecs.update_calls}
    assert updated == {"onlineshop-auth", "onlineshop-items"}


def test_deployment_for_revision_retries_until_visible(env, monkeypatch):
    from delivery.commands import deploy as deploy_module

    class SlowVisibilityEcs(FakeEcs):
        def __init__(self, *args, delay_calls=2, **kwargs):
            super().__init__(*args, **kwargs)
            self.delay_calls = delay_calls
            self.calls = 0

        def describe_services(self, cluster, services):
            self.calls += 1
            observed = super().describe_services(cluster, services)
            if self.calls < self.delay_calls:
                for svc in observed["services"]:
                    svc["deployments"] = [
                        {
                            "id": "deploy-stale",
                            "status": "PRIMARY",
                            "rolloutState": "COMPLETED",
                            "taskDefinition": self.task_definition_arns[svc["serviceName"]],
                        }
                    ]
            return observed

    revision_arn = (
        "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:99"
    )
    fake = SlowVisibilityEcs(digests=DIGESTS, delay_calls=2)
    fake.td_store["onlineshop-auth"] = {
        revision_arn: {
            **fake._initial_td("onlineshop-auth"),
            "taskDefinitionArn": revision_arn,
        }
    }
    found = deploy_module._deployment_for_revision(
        fake, "onlineshop-cluster", "onlineshop-auth", revision_arn
    )
    assert found == "deploy-onlineshop-auth-1"
    assert fake.calls == 2


def test_deployment_for_revision_gives_up_after_bounded_retries(env, monkeypatch):
    from delivery.commands import deploy as deploy_module
    from delivery.errors import ReadError

    class NeverVisibleEcs(FakeEcs):
        def describe_services(self, cluster, services):
            observed = super().describe_services(cluster, services)
            for svc in observed["services"]:
                svc["deployments"] = [
                    {
                        "id": "deploy-stale",
                        "status": "PRIMARY",
                        "rolloutState": "COMPLETED",
                        "taskDefinition": self.task_definition_arns[svc["serviceName"]],
                    }
                ]
            return observed

    revision_arn = (
        "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:99"
    )
    fake = NeverVisibleEcs(digests=DIGESTS)
    fake.td_store["onlineshop-auth"] = {
        revision_arn: {
            **fake._initial_td("onlineshop-auth"),
            "taskDefinitionArn": revision_arn,
        }
    }
    with pytest.raises(ReadError, match="did not converge"):
        deploy_module._deployment_for_revision(
            fake, "onlineshop-cluster", "onlineshop-auth", revision_arn
        )
