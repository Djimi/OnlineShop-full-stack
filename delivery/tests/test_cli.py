"""Tests for the delivery CLI: parsing, exit codes, and the snapshot command."""

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from conftest import client_error

from delivery.cli import main
from delivery.models import ProductionSnapshot
from delivery.serialization import canonical_json, sha256_hex

FIXTURES = Path(__file__).parent / "fixtures"
ACCOUNT = "799111666795"

IDENTIFIERS = {
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
    "frontendBucket": "onlineshop-frontend-799111666795",
    "frontendLiveMarker": "release.json",
    "frontendReleasesPrefix": "_releases/v",
    "cloudfrontDistributionId": "E123456789ABCD",
}

SERVICE_SPECS = {
    "onlineshop-auth": {
        "taskDefinition": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:12",
        "deploymentId": "deploy-auth-0001",
        "rolloutState": "COMPLETED",
        "tasks": ["arn:aws:ecs:eu-north-1:799111666795:task/auth-11111111"],
    },
    "onlineshop-items": {
        "taskDefinition": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:9",
        "deploymentId": "deploy-items-0001",
        "rolloutState": "COMPLETED",
        "tasks": ["arn:aws:ecs:eu-north-1:799111666795:task/items-22222222"],
    },
    "onlineshop-api-gateway": {
        "taskDefinition": (
            "arn:aws:ecs:eu-north-1:799111666795:task-definition/"
            "onlineshop-api-gateway:7"
        ),
        "deploymentId": "deploy-gateway-0001",
        "rolloutState": "COMPLETED",
        "tasks": ["arn:aws:ecs:eu-north-1:799111666795:task/gateway-33333333"],
    },
}

DIGESTS = {
    "arn:aws:ecs:eu-north-1:799111666795:task/auth-11111111": (
        "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ),
    "arn:aws:ecs:eu-north-1:799111666795:task/items-22222222": (
        "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ),
    "arn:aws:ecs:eu-north-1:799111666795:task/gateway-33333333": (
        "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    ),
}

DB_INSTANCE = {"Engine": "postgres", "EngineVersion": "18.1", "DBInstanceClass": "db.t4g.micro"}

MISSING_ARGS_CASES = [
    ["candidate"],
    ["candidate", "validate"],
    ["candidate", "manifest"],
    ["snapshot"],
    ["snapshot", "production"],
    ["staging"],
    ["staging", "lifecycle"],
    ["staging", "apply"],
    ["deploy"],
    ["deploy", "backends"],
    ["deploy", "gateway"],
    ["deploy", "frontend"],
    ["verify"],
    ["verify", "production"],
    ["verify", "staging"],
    ["finalize"],
    ["recover"],
    ["rollback"],
    ["rollback", "preflight"],
    ["rollback", "execute"],
    ["retention"],
    ["retention", "audit"],
    ["retention", "preview"],
    ["retention", "apply"],
]

NOT_IMPLEMENTED_CASES = [
    (["staging", "lifecycle"], ["--out", "record.json"]),
    (["staging", "apply"], ["--candidate", "cand.json", "--out", "record.json"]),
    (
        ["deploy", "backends"],
        ["--candidate", "cand.json", "--snapshot", str(FIXTURES / "valid_snapshot.json")],
    ),
    (
        ["deploy", "gateway"],
        ["--candidate", "cand.json", "--snapshot", str(FIXTURES / "valid_snapshot.json")],
    ),
    (
        ["deploy", "frontend"],
        ["--candidate", "cand.json", "--snapshot", str(FIXTURES / "valid_snapshot.json")],
    ),
    (["verify", "production"], ["--manifest", "rel.json"]),
    (["verify", "staging"], ["--candidate", "cand.json"]),
    (["finalize"], ["--manifest", "rel.json", "--evidence-dir", "evidence"]),
    (
        ["recover"],
        ["--snapshot", str(FIXTURES / "valid_snapshot.json"), "--changed", "changed.json"],
    ),
    (["rollback", "preflight"], ["--release-id", "release-0002"]),
    (
        ["rollback", "execute"],
        ["--manifest", "rel.json", "--snapshot", str(FIXTURES / "valid_snapshot.json")],
    ),
    (["retention", "audit"], []),
    (["retention", "preview"], []),
    (["retention", "apply"], ["--dry-run"]),
    (["retention", "apply"], ["--apply"]),
]

AWS_FLAGS = ["--environment", "production", "--identifiers", "identifiers.json"]


def _identifiers_file(tmp_path, environment="production"):
    identifiers = dict(IDENTIFIERS, environment=environment)
    path = tmp_path / f"identifiers-{environment}.json"
    path.write_text(json.dumps(identifiers))
    return path


class FakeSts:
    def get_caller_identity(self):
        return {
            "Account": ACCOUNT,
            "Arn": f"arn:aws:iam::{ACCOUNT}:user/tester",
            "UserId": "AIDAEXAMPLE",
        }


class FakeEcs:
    def __init__(self, specs, digests):
        self.services = {}
        self.tasks = {}
        self.service_tasks = {}
        for name, spec in specs.items():
            deployments = spec.get("deployments") or [
                {
                    "id": spec["deploymentId"],
                    "status": "PRIMARY",
                    "rolloutState": spec["rolloutState"],
                }
            ]
            self.services[name] = {
                "serviceName": name,
                "taskDefinition": spec["taskDefinition"],
                "deployments": deployments,
            }
            self.service_tasks[name] = list(spec["tasks"])
            for arn in spec["tasks"]:
                self.tasks[arn] = {
                    "taskArn": arn,
                    "containers": [{"name": "app", "imageDigest": digests[arn]}],
                }

    def describe_services(self, cluster, services):
        return {"services": [self.services[name] for name in services]}

    def list_tasks(self, cluster, serviceName):
        return {"taskArns": list(self.service_tasks[serviceName])}

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [self.tasks[arn] for arn in tasks]}


class FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise client_error("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise client_error("NoSuchKey")
        body = self.objects[Key]
        return {
            "ContentLength": len(body),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(body).digest()).decode(),
        }


class FakeRds:
    def __init__(self, instance):
        self.instance = instance

    def describe_db_instances(self, DBInstanceIdentifier=None):
        return {"DBInstances": [self.instance]}


def run_snapshot(monkeypatch, tmp_path, marker_content="release-0001", objects=None, specs=None):
    ids_file = tmp_path / "identifiers.json"
    ids_file.write_text(json.dumps(IDENTIFIERS))
    clients = {
        "sts": FakeSts(),
        "ecs": FakeEcs(specs or SERVICE_SPECS, DIGESTS),
        "s3": FakeS3(
            {IDENTIFIERS["frontendLiveMarker"]: marker_content.encode()}
            if objects is None
            else objects
        ),
        "rds": FakeRds(DB_INSTANCE),
    }
    monkeypatch.setattr("delivery.aws.context.client_for", lambda ctx, service: clients[service])
    out = tmp_path / "snapshot.json"
    code = main(
        [
            "snapshot",
            "production",
            "--out",
            str(out),
            "--environment",
            "production",
            "--identifiers",
            str(ids_file),
        ]
    )
    return code, out


def test_help_exits_zero(capsys):
    assert main(["-h"]) == 0


def test_unknown_command_exits_usage(capsys):
    assert main(["bogus"]) == 2


def test_no_arguments_exits_usage(capsys):
    assert main([]) == 2


@pytest.mark.parametrize("command", MISSING_ARGS_CASES)
def test_missing_required_args_exit_usage(capsys, command):
    assert main(command) == 2


@pytest.mark.parametrize("command,flags", NOT_IMPLEMENTED_CASES)
def test_not_implemented_commands_fail_closed(capsys, command, flags):
    assert main(list(command) + flags + AWS_FLAGS) == 1
    assert "ERROR NOT_IMPLEMENTED" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value",
    ["1.2.3", "release-12", "release-12345", "release-abc", "release-", "main", "sha256:abc"],
)
def test_rollback_preflight_rejects_non_release_id(capsys, value):
    assert main(["rollback", "preflight", "--release-id", value, *AWS_FLAGS]) == 2


def test_retention_apply_requires_mode(capsys):
    assert main(["retention", "apply", *AWS_FLAGS]) == 2


def test_retention_preview_rejects_bad_reference_date(capsys):
    assert main(["retention", "preview", "--reference-date", "not-a-date", *AWS_FLAGS]) == 2


@pytest.mark.parametrize(
    "value", ["2026-08-15T10:00:00", "2026-08-15", "2026-08-15T10:00:00.5"]
)
def test_retention_reference_date_rejects_naive_datetimes(capsys, value):
    assert main(["retention", "preview", "--reference-date", value, *AWS_FLAGS]) == 2


@pytest.mark.parametrize("value", ["2026-08-15T10:00:00+00:00", "2026-08-15T10:00:00Z"])
def test_retention_reference_date_accepts_utc_aware_datetimes(capsys, value):
    assert main(["retention", "preview", "--reference-date", value, *AWS_FLAGS]) == 1
    assert "ERROR NOT_IMPLEMENTED" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["2026-08-15T10:00:00+02:00", "2026-08-15T10:00:00-05:00"])
def test_retention_reference_date_rejects_non_utc_offsets(capsys, value):
    assert main(["retention", "preview", "--reference-date", value, *AWS_FLAGS]) == 2


SNAPSHOT_GUARD_COMMANDS = [
    pytest.param(["deploy", "backends", "--candidate", "cand.json"], id="deploy-backends"),
    pytest.param(["recover", "--changed", "changed.json"], id="recover"),
    pytest.param(["rollback", "execute", "--manifest", "rel.json"], id="rollback-execute"),
]


@pytest.mark.parametrize("command", SNAPSHOT_GUARD_COMMANDS)
def test_snapshot_guard_rejects_snapshot_of_other_environment(capsys, tmp_path, command):
    assert (
        main(
            [
                *command,
                "--snapshot",
                str(FIXTURES / "valid_snapshot.json"),
                "--environment",
                "staging",
                "--identifiers",
                str(_identifiers_file(tmp_path, environment="staging")),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "ERROR VALIDATION" in captured.err
    assert "environment 'staging'" in captured.err
    assert "NOT_IMPLEMENTED" not in captured.err


@pytest.mark.parametrize("command", SNAPSHOT_GUARD_COMMANDS)
def test_snapshot_guard_rejects_snapshot_without_environment(capsys, tmp_path, command):
    snapshot = json.loads((FIXTURES / "valid_snapshot.json").read_text())
    del snapshot["environment"]
    snapshot_file = tmp_path / "snapshot.json"
    snapshot_file.write_text(json.dumps(snapshot))
    ids_file = _identifiers_file(tmp_path)
    assert (
        main(
            [
                *command,
                "--snapshot",
                str(snapshot_file),
                "--environment",
                "production",
                "--identifiers",
                str(ids_file),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "ERROR VALIDATION" in captured.err
    assert "environment 'production'" in captured.err
    assert "NOT_IMPLEMENTED" not in captured.err


@pytest.mark.parametrize("command", SNAPSHOT_GUARD_COMMANDS)
def test_snapshot_guard_rejects_unreadable_snapshot(capsys, tmp_path, command):
    ids_file = _identifiers_file(tmp_path)
    assert (
        main(
            [
                *command,
                "--snapshot",
                str(tmp_path / "does-not-exist.json"),
                "--environment",
                "production",
                "--identifiers",
                str(ids_file),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "ERROR VALIDATION" in captured.err
    assert "NOT_IMPLEMENTED" not in captured.err


def test_candidate_validate_valid_fixture(capsys):
    argv = ["candidate", "validate", "--manifest", str(FIXTURES / "valid_candidate.json")]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert "cand-4712-2-222222222222" in captured.out
    assert '"candidateId":"cand-4712-2-222222222222"' in captured.out


def test_candidate_validate_valid_main_fixture_with_class(capsys):
    assert (
        main(
            [
                "candidate",
                "validate",
                "--manifest",
                str(FIXTURES / "valid_candidate_main.json"),
                "--class",
                "main",
            ]
        )
        == 0
    )


def test_candidate_validate_invalid_fixture(capsys):
    assert (
        main(
            [
                "candidate",
                "validate",
                "--manifest",
                str(FIXTURES / "invalid_candidate_bad_digest.json"),
            ]
        )
        == 1
    )
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_candidate_validate_class_mismatch(capsys):
    assert (
        main(
            [
                "candidate",
                "validate",
                "--manifest",
                str(FIXTURES / "valid_candidate.json"),
                "--class",
                "main",
            ]
        )
        == 1
    )
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_candidate_validate_requires_production_eligible(capsys):
    assert (
        main(
            [
                "candidate",
                "validate",
                "--manifest",
                str(FIXTURES / "valid_candidate.json"),
                "--require-production-eligible",
            ]
        )
        == 1
    )
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_candidate_validate_unreadable_manifest(capsys):
    assert (
        main(["candidate", "validate", "--manifest", str(FIXTURES / "does-not-exist.json")]) == 1
    )
    assert "ERROR READ_ERROR" in capsys.readouterr().err


def test_snapshot_production_writes_expected_record(monkeypatch, tmp_path):
    code, out = run_snapshot(monkeypatch, tmp_path)
    assert code == 0
    snapshot = ProductionSnapshot.model_validate(json.loads(out.read_text()))
    assert snapshot.environment == "production"
    assert snapshot.services["auth"].deploymentId == "deploy-auth-0001"
    auth_spec = SERVICE_SPECS["onlineshop-auth"]
    assert snapshot.services["auth"].taskDefinitionArn == auth_spec["taskDefinition"]
    assert snapshot.services["auth"].runningDigests == [
        DIGESTS["arn:aws:ecs:eu-north-1:799111666795:task/auth-11111111"]
    ]
    assert snapshot.services["auth"].health == "COMPLETED"
    assert snapshot.services["items"].deploymentId == "deploy-items-0001"
    assert snapshot.services["gateway"].deploymentId == "deploy-gateway-0001"
    assert snapshot.release.status == "official"
    assert snapshot.release.releaseId == "release-0001"
    assert snapshot.release.manifestSha256 is None
    assert snapshot.frontend.immutableIdentity == "release-0001"
    assert snapshot.frontend.liveMarker == "release.json"
    assert snapshot.frontend.checksum == sha256_hex(b"release-0001")
    assert snapshot.frontend.cloudfrontDistributionId == "E123456789ABCD"
    expected_fingerprint = sha256_hex(
        canonical_json(
            {
                "taskDefinitionArns": sorted(
                    spec["taskDefinition"] for spec in SERVICE_SPECS.values()
                ),
                "db": {
                    "engine": "postgres",
                    "engineVersion": "18.1",
                    "dbInstanceClass": "db.t4g.micro",
                },
            }
        ).encode()
    )
    assert snapshot.compatibilityFingerprint == expected_fingerprint


def test_snapshot_production_records_no_official_release_for_unknown_marker(
    monkeypatch, tmp_path
):
    code, out = run_snapshot(monkeypatch, tmp_path, marker_content="cand-4712-2-222222222222")
    assert code == 0
    snapshot = ProductionSnapshot.model_validate(json.loads(out.read_text()))
    assert snapshot.release.status == "none"
    assert snapshot.release.releaseId is None
    assert snapshot.release.manifestSha256 is None
    assert snapshot.frontend.immutableIdentity == "cand-4712-2-222222222222"


def test_snapshot_production_absent_marker_fails_closed(monkeypatch, tmp_path, capsys):
    code, out = run_snapshot(monkeypatch, tmp_path, objects={})
    assert code == 1
    assert "ERROR NOT_FOUND" in capsys.readouterr().err
    assert not out.exists()


def test_snapshot_production_empty_running_digests_fails_closed(monkeypatch, tmp_path, capsys):
    specs = {name: dict(spec) for name, spec in SERVICE_SPECS.items()}
    specs["onlineshop-items"]["tasks"] = []
    code, out = run_snapshot(monkeypatch, tmp_path, specs=specs)
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR READ_ERROR" in captured.err
    assert "no running tasks" in captured.err
    assert not out.exists()


def test_snapshot_production_reads_primary_deployment_not_first_entry(
    monkeypatch, tmp_path
):
    specs = {name: dict(spec) for name, spec in SERVICE_SPECS.items()}
    specs["onlineshop-auth"]["deployments"] = [
        {"id": "deploy-auth-9999", "status": "ACTIVE", "rolloutState": "IN_PROGRESS"},
        {"id": "deploy-auth-0001", "status": "PRIMARY", "rolloutState": "COMPLETED"},
    ]
    code, out = run_snapshot(monkeypatch, tmp_path, specs=specs)
    assert code == 0
    snapshot = ProductionSnapshot.model_validate(json.loads(out.read_text()))
    assert snapshot.services["auth"].deploymentId == "deploy-auth-0001"
    assert snapshot.services["auth"].health == "COMPLETED"


def test_snapshot_production_without_primary_deployment_fails_closed(
    monkeypatch, tmp_path, capsys
):
    specs = {name: dict(spec) for name, spec in SERVICE_SPECS.items()}
    specs["onlineshop-auth"]["deployments"] = [
        {"id": "deploy-auth-9999", "status": "ACTIVE", "rolloutState": "IN_PROGRESS"}
    ]
    code, out = run_snapshot(monkeypatch, tmp_path, specs=specs)
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR READ_ERROR" in captured.err
    assert "no PRIMARY deployment" in captured.err
    assert not out.exists()


def test_raw_client_error_maps_to_read_error(capsys, monkeypatch, tmp_path):
    def boom(args):
        raise client_error("ThrottlingException")

    monkeypatch.setattr("delivery.commands.snapshot.snapshot_production", boom)
    ids_file = tmp_path / "identifiers.json"
    ids_file.write_text(json.dumps(IDENTIFIERS))
    out = tmp_path / "snapshot.json"
    code = main(
        [
            "snapshot",
            "production",
            "--out",
            str(out),
            "--environment",
            "production",
            "--identifiers",
            str(ids_file),
        ]
    )
    assert code == 1
    assert "ERROR READ_ERROR" in capsys.readouterr().err


WRONG_IDENTIFIER_TYPE_CASES = [
    ("accountId", 799111666795),
    ("accountId", "7991"),
    ("cluster", 42),
    ("dbInstance", None),
    ("frontendBucket", ["onlineshop-frontend"]),
    ("frontendLiveMarker", 1.5),
    ("frontendReleasesPrefix", True),
    ("cloudfrontDistributionId", {}),
    ("services", []),
    ("services", "onlineshop-auth"),
    ("services", [1, 2, 3]),
    ("ecrRepositories", ["onlineshop-auth"]),
    ("ecrRepositories", {"auth": "onlineshop-auth"}),
    ("ecrRepositories", {"auth": 1, "items": 2, "gateway": 3}),
]


@pytest.mark.parametrize("key,value", WRONG_IDENTIFIER_TYPE_CASES)
def test_snapshot_production_rejects_wrong_identifier_types(tmp_path, capsys, key, value):
    ids_file = tmp_path / "identifiers.json"
    bad = dict(IDENTIFIERS)
    bad[key] = value
    ids_file.write_text(json.dumps(bad))
    out = tmp_path / "snapshot.json"
    code = main(
        [
            "snapshot",
            "production",
            "--out",
            str(out),
            "--environment",
            "production",
            "--identifiers",
            str(ids_file),
        ]
    )
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_snapshot_production_rejects_malformed_identifiers(tmp_path, capsys):
    ids_file = tmp_path / "identifiers.json"
    bad = dict(IDENTIFIERS)
    del bad["dbInstance"]
    ids_file.write_text(json.dumps(bad))
    out = tmp_path / "snapshot.json"
    code = main(
        [
            "snapshot",
            "production",
            "--out",
            str(out),
            "--environment",
            "production",
            "--identifiers",
            str(ids_file),
        ]
    )
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_snapshot_production_rejects_environment_mismatch(tmp_path, capsys):
    ids_file = tmp_path / "identifiers.json"
    ids_file.write_text(json.dumps(IDENTIFIERS))
    out = tmp_path / "snapshot.json"
    code = main(
        [
            "snapshot",
            "production",
            "--out",
            str(out),
            "--environment",
            "staging",
            "--identifiers",
            str(ids_file),
        ]
    )
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err
