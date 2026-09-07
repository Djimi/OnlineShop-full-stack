"""CLI validation tests for the staging identifiers shape and lifecycle flags."""

from pathlib import Path

import pytest
from fakes_staging import staging_identifiers, write_identifiers

from delivery.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]

STAGING_KEYS = {
    "cluster",
    "dbInstance",
    "albName",
    "dbSecrets",
    "sqlRunnerFamily",
    "sqlLogGroup",
    "sqlSubnets",
    "sqlSecurityGroup",
    "executionRoleArn",
    "compatFrontendBucket",
    "compatFrontendReleasesPrefix",
}
PRODUCTION_ONLY_KEYS = {
    "frontendBucket",
    "frontendLiveMarker",
    "frontendReleasesPrefix",
    "cloudfrontDistributionId",
}


def run_reconcile_with(monkeypatch, tmp_path, ids, capsys):
    from fakes_staging import FakeRds, FakeSts

    monkeypatch.setattr(
        "delivery.aws.context.client_for",
        lambda ctx, service: {"sts": FakeSts(), "rds": FakeRds(status="stopped")}[service],
    )
    path = write_identifiers(tmp_path, ids)
    return main(
        [
            "staging",
            "reconcile",
            "--environment",
            "staging",
            "--identifiers",
            str(path),
            "--out",
            str(tmp_path / "reconcile.json"),
        ]
    ), capsys


def test_staging_identifiers_valid_shape_accepted(monkeypatch, tmp_path, capsys):
    code, _ = run_reconcile_with(monkeypatch, tmp_path, staging_identifiers(), capsys)
    assert code == 0


@pytest.mark.parametrize("key", sorted(STAGING_KEYS))
def test_staging_identifiers_missing_key_rejected(monkeypatch, tmp_path, capsys, key):
    ids = staging_identifiers()
    del ids[key]
    code, captured = run_reconcile_with(monkeypatch, tmp_path, ids, capsys)
    assert code == 1
    assert "ERROR VALIDATION" in captured.readouterr().err


@pytest.mark.parametrize("key", sorted(PRODUCTION_ONLY_KEYS))
def test_staging_identifiers_reject_production_only_keys(monkeypatch, tmp_path, capsys, key):
    ids = staging_identifiers()
    ids[key] = "production-value"
    code, captured = run_reconcile_with(monkeypatch, tmp_path, ids, capsys)
    assert code == 1
    assert "production-only keys" in captured.readouterr().err


def test_staging_identifiers_reject_bad_db_secrets(monkeypatch, tmp_path, capsys):
    ids = staging_identifiers()
    ids["dbSecrets"] = {"auth": "onlineshop/auth/db-staging"}
    code, captured = run_reconcile_with(monkeypatch, tmp_path, ids, capsys)
    assert code == 1
    assert "dbSecrets" in captured.readouterr().err


def test_staging_identifiers_reject_bad_subnets(monkeypatch, tmp_path, capsys):
    ids = staging_identifiers()
    ids["sqlSubnets"] = "subnet-aaaa"
    code, captured = run_reconcile_with(monkeypatch, tmp_path, ids, capsys)
    assert code == 1
    assert "sqlSubnets" in captured.readouterr().err


def test_staging_identifiers_environment_mismatch_rejected(monkeypatch, tmp_path, capsys):
    ids = staging_identifiers()
    ids["environment"] = "production"
    from fakes_staging import FakeRds, FakeSts

    monkeypatch.setattr(
        "delivery.aws.context.client_for",
        lambda ctx, service: {"sts": FakeSts(), "rds": FakeRds(status="stopped")}[service],
    )
    path = write_identifiers(tmp_path, ids)
    code = main(
        [
            "staging",
            "reconcile",
            "--environment",
            "staging",
            "--identifiers",
            str(path),
            "--out",
            str(tmp_path / "reconcile.json"),
        ]
    )
    assert code == 1
    assert "does not match" in capsys.readouterr().err


def test_production_identifiers_validation_unchanged(monkeypatch, tmp_path, capsys):
    # the production shape still requires the four frontend/CloudFront keys
    production_ids = {
        "environment": "production",
        "accountId": "799111666795",
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
    from fakes_staging import FakeRds, FakeSts

    monkeypatch.setattr(
        "delivery.aws.context.client_for",
        lambda ctx, service: {"sts": FakeSts(), "rds": FakeRds(status="stopped")}[service],
    )
    path = write_identifiers(tmp_path, production_ids)
    code = main(
        [
            "staging",
            "reconcile",
            "--environment",
            "production",
            "--identifiers",
            str(path),
            "--out",
            str(tmp_path / "reconcile.json"),
        ]
    )
    # build_context passes; the staging guard rejects the environment
    assert code == 1
    assert "not in allowed set" in capsys.readouterr().err


def test_production_identifiers_missing_frontend_key_still_rejected(monkeypatch, tmp_path, capsys):
    production_ids = {
        "environment": "production",
        "accountId": "799111666795",
        "cluster": "onlineshop-cluster",
        "services": ["onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"],
        "ecrRepositories": {
            "auth": "onlineshop-auth",
            "items": "onlineshop-items",
            "gateway": "onlineshop-api-gateway",
        },
        "dbInstance": "onlineshop-postgres-db",
        "frontendLiveMarker": "release.json",
        "frontendReleasesPrefix": "_releases/v",
        "cloudfrontDistributionId": "E123",
    }
    path = write_identifiers(tmp_path, production_ids)
    code = main(
        [
            "staging",
            "reconcile",
            "--environment",
            "production",
            "--identifiers",
            str(path),
            "--out",
            str(tmp_path / "reconcile.json"),
        ]
    )
    assert code == 1
    assert "frontendBucket" in capsys.readouterr().err


def test_lifecycle_requires_candidate_and_archive(tmp_path, capsys):
    path = write_identifiers(tmp_path, staging_identifiers())
    code = main(
        [
            "staging",
            "lifecycle",
            "--environment",
            "staging",
            "--identifiers",
            str(path),
            "--out",
            str(tmp_path / "record.json"),
            "--repo-path",
            str(REPO_ROOT),
        ]
    )
    assert code == 1
    assert "requires --candidate and --frontend-archive" in capsys.readouterr().err


def test_lifecycle_continue_requires_conclusion(tmp_path, capsys):
    path = write_identifiers(tmp_path, staging_identifiers())
    code = main(
        [
            "staging",
            "lifecycle",
            "--continue",
            "--environment",
            "staging",
            "--identifiers",
            str(path),
            "--out",
            str(tmp_path / "record.json"),
            "--repo-path",
            str(REPO_ROOT),
        ]
    )
    assert code == 1
    assert "--continue requires --e2e-conclusion" in capsys.readouterr().err


def test_lifecycle_e2e_conclusion_without_continue_rejected(tmp_path, capsys):
    path = write_identifiers(tmp_path, staging_identifiers())
    code = main(
        [
            "staging",
            "lifecycle",
            "--e2e-conclusion",
            "passed",
            "--environment",
            "staging",
            "--identifiers",
            str(path),
            "--out",
            str(tmp_path / "record.json"),
            "--repo-path",
            str(REPO_ROOT),
        ]
    )
    assert code == 1
    assert "only valid together with --continue" in capsys.readouterr().err


def test_lifecycle_requires_repo_path(tmp_path, capsys):
    path = write_identifiers(tmp_path, staging_identifiers())
    code = main(
        [
            "staging",
            "lifecycle",
            "--environment",
            "staging",
            "--identifiers",
            str(path),
            "--out",
            str(tmp_path / "record.json"),
        ]
    )
    assert code == 2
    assert "--repo-path" in capsys.readouterr().err


def test_apply_requires_repo_path(tmp_path, capsys):
    code = main(["staging", "apply", "--candidate", "cand.json"])
    assert code == 2
    assert "--repo-path" in capsys.readouterr().err


def test_reconcile_requires_out(capsys):
    assert main(["staging", "reconcile", "--environment", "staging"]) == 2


def test_staging_identifiers_unknown_keys_tolerated(monkeypatch, tmp_path, capsys):
    ids = staging_identifiers()
    ids["futureKey"] = "ok"
    code, _ = run_reconcile_with(monkeypatch, tmp_path, ids, capsys)
    assert code == 0
