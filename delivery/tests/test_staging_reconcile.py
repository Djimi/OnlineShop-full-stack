"""Tests for OP-STG-05 reconcile and the standalone staging apply command."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import client_error
from fakes_staging import (
    ACCOUNT,
    DIGEST_A,
    DIGEST_B,
    DIGEST_C,
    MARKER_TAG_KEY,
    SERVICES,
    FakeEcr,
    FakeEcs,
    FakeRds,
    FakeSts,
    marker_tag_value,
    staging_identifiers,
    write_identifiers,
)

from delivery.cli import main

# The engine resolves the reset SQL sources against the --repo-path checkout.
REPO_ROOT = Path(__file__).resolve().parents[2]


class ReconcileRunner:
    def __init__(self, monkeypatch, tmp_path):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.ids = staging_identifiers()
        self.rds = FakeRds(status="stopped")
        self.sts = FakeSts()
        self.identifiers_file = write_identifiers(tmp_path, self.ids)
        self.out = tmp_path / "reconcile-record.json"
        self.touched = []

        class PoisonEcs:
            def __getattr__(self, name):
                def boom(*args, **kwargs):
                    raise AssertionError(f"reconcile must never touch ECS ({name})")

                return boom

        self.poison_ecs = PoisonEcs()
        monkeypatch.setattr(
            "delivery.aws.context.client_for",
            lambda ctx, service: {"sts": self.sts, "rds": self.rds, "ecs": self.poison_ecs}[
                service
            ],
        )

    def argv(self):
        return [
            "staging",
            "reconcile",
            "--environment",
            "staging",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.out),
        ]

    def record(self):
        return json.loads(self.out.read_text())


@pytest.fixture
def reconcile_runner(monkeypatch, tmp_path):
    return ReconcileRunner(monkeypatch, tmp_path)


def test_reconcile_stopped_db_is_success(reconcile_runner, capsys):
    code = main(reconcile_runner.argv())
    assert code == 0
    assert reconcile_runner.record()["action"] == "none"
    assert "nothing to do" in capsys.readouterr().out


def test_reconcile_running_with_active_marker_is_no_op(reconcile_runner):
    reconcile_runner.rds.status = "available"
    reconcile_runner.rds.tags[MARKER_TAG_KEY] = marker_tag_value()
    assert main(reconcile_runner.argv()) == 0
    record = reconcile_runner.record()
    assert record["action"] == "none"
    assert record["marker"]["operationId"] == "stg-4712-2"
    assert "stop_db_instance" not in reconcile_runner.rds.calls


def test_reconcile_running_without_marker_stops_and_fails_visibly(reconcile_runner, capsys):
    reconcile_runner.rds.status = "available"
    code = main(reconcile_runner.argv())
    assert code == 1
    captured = capsys.readouterr()
    assert "OWNERLESS_STAGING_RDS" in captured.out
    assert "ERROR OWNERLESS_STOPPED" in captured.err
    record = reconcile_runner.record()
    assert record["action"] == "stopped"
    assert "no ownership marker" in record["conclusion"]
    # stop was issued and the stopped state verified
    assert "stop_db_instance" in reconcile_runner.rds.calls
    assert reconcile_runner.rds.status == "stopped"


def test_reconcile_running_with_expired_marker_stops(reconcile_runner, capsys):
    reconcile_runner.rds.status = "available"
    reconcile_runner.rds.tags[MARKER_TAG_KEY] = marker_tag_value(expires_in=timedelta(hours=-1))
    code = main(reconcile_runner.argv())
    assert code == 1
    captured = capsys.readouterr()
    assert "OWNERLESS_STAGING_RDS" in captured.out
    assert "expired marker" in captured.out
    record = reconcile_runner.record()
    assert record["action"] == "stopped"
    assert reconcile_runner.rds.status == "stopped"


def test_reconcile_stop_not_verified_fails(reconcile_runner, monkeypatch, capsys):
    monkeypatch.setattr("delivery.commands.staging.RDS_STOP_TIMEOUT", 0.6)
    reconcile_runner.rds.status = "available"
    reconcile_runner.rds.stop_result = "stopping"
    code = main(reconcile_runner.argv())
    assert code == 1
    assert "ERROR" in capsys.readouterr().err


def test_reconcile_read_error_is_visible_failure(reconcile_runner, capsys):
    reconcile_runner.rds.error = client_error("AccessDenied")
    code = main(reconcile_runner.argv())
    assert code == 1
    assert "ERROR READ_ERROR" in capsys.readouterr().err
    assert not reconcile_runner.out.exists()


def test_reconcile_absent_db_is_successful_no_op(reconcile_runner, capsys):
    class AbsentRds(FakeRds):
        def describe_db_instances(self, DBInstanceIdentifier):
            raise client_error("DBInstanceNotFound")

    reconcile_runner.rds = AbsentRds()
    code = main(reconcile_runner.argv())
    assert code == 0
    record = reconcile_runner.record()
    # a genuinely absent DB is a REAL read result, not a read error
    assert record["dbStatus"] == "absent"
    assert record["action"] == "none"
    assert "nothing to stop" in record["conclusion"]
    assert "absent" in capsys.readouterr().out


def test_reconcile_never_touches_production_identifiers(monkeypatch, tmp_path, capsys):
    runner = ReconcileRunner(monkeypatch, tmp_path)
    production_ids = {
        "environment": "production",
        "accountId": ACCOUNT,
        "cluster": "onlineshop-cluster",
        "services": ["onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"],
        "ecrRepositories": {
            "auth": "onlineshop-auth",
            "items": "onlineshop-items",
            "gateway": "onlineshop-api-gateway",
        },
        "dbInstance": "onlineshop-postgres-db",
        "frontendBucket": "onlineshop-frontend",
        "frontendLiveMarker": "release.json",
        "frontendReleasesPrefix": "_releases/v",
        "cloudfrontDistributionId": "E123",
    }
    runner.identifiers_file = write_identifiers(tmp_path, production_ids)
    code = main(
        [
            "staging",
            "reconcile",
            "--environment",
            "production",
            "--identifiers",
            str(runner.identifiers_file),
            "--out",
            str(runner.out),
        ]
    )
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err


class ApplyRunner:
    def __init__(self, monkeypatch, tmp_path):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.ids = staging_identifiers()
        self.rds = FakeRds(status="available")
        self.ecs = FakeEcs(
            digests={SERVICES[0]: DIGEST_A, SERVICES[1]: DIGEST_B, SERVICES[2]: DIGEST_C}
        )
        self.ecr = FakeEcr()
        self.sts = FakeSts()
        self.identifiers_file = write_identifiers(tmp_path, self.ids)
        self.out = tmp_path / "apply-record.json"
        self.manifest = tmp_path / "candidate-manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "candidateId": "cand-4712-2-222222222222",
                    "candidateClass": "feature",
                    "source": {
                        "repository": "Djimi@8793507/OnlineShop-full-stack",
                        "branch": "feature/x",
                        "ref": "refs/heads/feature/x",
                        "fullSha": "2222222222222222222222222222222222222222",
                    },
                    "build": {
                        "workflowRunId": 4712,
                        "workflowRunAttempt": 2,
                        "workflowUrl": "https://github.com/x/y/actions/runs/4712",
                        "createdAt": "2026-08-15T10:00:00Z",
                        "completedAt": "2026-08-15T10:11:00Z",
                    },
                    "artifacts": {
                        "auth": {"repository": "onlineshop-auth", "digest": DIGEST_A},
                        "items": {
                            "repository": "onlineshop-items",
                            "digest": DIGEST_B,
                            "commonSourceSha": "2222222222222222222222222222222222222222",
                        },
                        "gateway": {
                            "repository": "onlineshop-api-gateway",
                            "digest": DIGEST_C,
                        },
                        "frontend": {
                            "artifactId": "frontend-archive-4712-2",
                            "artifactDigest": f"sha256:{'e' * 64}",
                            "contentChecksum": f"{'f' * 64}",
                        },
                    },
                    "tests": {
                        "unit": "passed",
                        "integration": "passed",
                        "frontend": "passed",
                        "localE2E": "passed",
                    },
                    "productionEligible": False,
                }
            )
        )
        monkeypatch.setattr(
            "delivery.aws.context.client_for",
            lambda ctx, service: {
                "sts": self.sts,
                "ecs": self.ecs,
                "ecr": self.ecr,
                "rds": self.rds,
            }[service],
        )

    def argv(self):
        return [
            "staging",
            "apply",
            "--candidate",
            str(self.manifest),
            "--environment",
            "staging",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.out),
            "--repo-path",
            str(REPO_ROOT),
        ]


@pytest.fixture
def apply_runner(monkeypatch, tmp_path):
    return ApplyRunner(monkeypatch, tmp_path)


def test_apply_deploys_exact_digests_and_records(apply_runner):
    code = main(apply_runner.argv())
    assert code == 0
    record = json.loads(apply_runner.out.read_text())
    assert record["phase"] == "DEPLOYING"
    assert record["artifactsObserved"]["authDigest"] == DIGEST_A
    assert record["artifactsObserved"]["itemsDigest"] == DIGEST_B
    assert record["artifactsObserved"]["gatewayDigest"] == DIGEST_C
    # conclusions the apply machine did not reach are honestly not-run
    assert record["database"]["resetConclusion"] == "not-run"
    assert record["e2e"]["conclusion"] == "not-run"
    assert record["cleanup"]["conclusion"] == "not-run"
    # digest-pinned revisions registered for all three services
    registered = [call["family"] for call in apply_runner.ecs.register_calls]
    assert registered == SERVICES
    # image-only change: the registered container image is the pinned digest
    for call in apply_runner.ecs.register_calls:
        container = call["containerDefinitions"][0]
        assert (
            container["image"].endswith("@sha256:" + "a" * 64)
            or container["image"].endswith("@sha256:" + "b" * 64)
            or container["image"].endswith("@sha256:" + "c" * 64)
        )
        # secrets remain references
        assert all(secret["valueFrom"].startswith("arn:") for secret in container["secrets"])
    # services updated in order auth, items, gateway
    assert [service for service, _ in apply_runner.ecs.update_calls] == SERVICES
    # nothing was started or stopped
    assert "start_db_instance" not in apply_runner.rds.calls
    assert "stop_db_instance" not in apply_runner.rds.calls


def test_apply_ecr_digest_missing_fails_without_mutation(apply_runner, capsys):
    apply_runner.ecr.digests["auth"] = f"sha256:{'f' * 64}"
    code = main(apply_runner.argv())
    assert code == 1
    assert "ERROR NOT_FOUND" in capsys.readouterr().err
    assert apply_runner.ecs.register_calls == []


def test_apply_does_not_clean_up_a_running_environment(apply_runner, capsys, monkeypatch):
    monkeypatch.setattr("delivery.commands.staging.DEPLOYMENT_TIMEOUT", 0.6)
    apply_runner.ecs.digests[SERVICES[0]] = f"sha256:{'f' * 64}"
    code = main(apply_runner.argv())
    assert code == 1
    assert "ERROR MUTATION_VERIFY" in capsys.readouterr().err
    # the environment belongs to whoever is running it: no cleanup, no stop
    assert "stop_db_instance" not in apply_runner.rds.calls
    assert all(apply_runner.ecs.desired_counts[s] == 1 for s in SERVICES)


def test_apply_environment_guard(apply_runner, capsys):
    production_ids = {
        "environment": "production",
        "accountId": ACCOUNT,
        "cluster": "onlineshop-cluster",
        "services": ["onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"],
        "ecrRepositories": {
            "auth": "onlineshop-auth",
            "items": "onlineshop-items",
            "gateway": "onlineshop-api-gateway",
        },
        "dbInstance": "onlineshop-postgres-db",
        "frontendBucket": "onlineshop-frontend",
        "frontendLiveMarker": "release.json",
        "frontendReleasesPrefix": "_releases/v",
        "cloudfrontDistributionId": "E123",
    }
    apply_runner.identifiers_file = write_identifiers(apply_runner.tmp_path, production_ids)
    code = main(
        [
            "staging",
            "apply",
            "--candidate",
            str(apply_runner.manifest),
            "--environment",
            "production",
            "--identifiers",
            str(apply_runner.identifiers_file),
            "--out",
            str(apply_runner.out),
            "--repo-path",
            str(REPO_ROOT),
        ]
    )
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err
