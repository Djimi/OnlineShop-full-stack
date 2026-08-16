"""End-to-end tests for the staging lifecycle machine (offline fakes)."""

import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
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
    FakeElb,
    FakeGitHub,
    FakeLogs,
    FakeRds,
    FakeS3,
    FakeSecrets,
    FakeSts,
    marker_tag_value,
    staging_identifiers,
    standard_artifact_names,
    write_identifiers,
)

from delivery.cli import main
from delivery.commands.staging import _content_checksum
from delivery.serialization import sha256_hex
from delivery.serving import JourneyResult
from delivery.staging_marker import parse_marker

RUN_ID = 4712
RUN_ATTEMPT = 2
SHA = "2222222222222222222222222222222222222222"
CANDIDATE_ID = f"cand-{RUN_ID}-{RUN_ATTEMPT}-{SHA[:12]}"
OPERATION_ID = f"stg-{RUN_ID}-{RUN_ATTEMPT}"

FRONTEND_CHECKSUM = f"{'d' * 64}"

# The reset SQL sources live in the repository checkout; the CLI takes the
# checkout as --repo-path (the wheel-installed engine has no source tree).
REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_frontend_archive(tmp_path: Path) -> tuple[Path, str, str]:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<html><body><div id="root"></div></body></html>')
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('candidate')")
    content_checksum = _content_checksum(dist)
    archive = tmp_path / "frontend.tar"
    with tarfile.open(archive, "w") as bundle:
        for path in sorted(dist.rglob("*")):
            if path.is_file():
                bundle.add(path, arcname=path.relative_to(dist).as_posix())
    artifact_digest = f"sha256:{sha256_hex(archive.read_bytes())}"
    return archive, artifact_digest, content_checksum


def _candidate_manifest(tmp_path, archive_digest, content_checksum) -> Path:
    manifest = {
        "schemaVersion": "1.0",
        "candidateId": CANDIDATE_ID,
        "candidateClass": "feature",
        "source": {
            "repository": "Djimi@8793507/OnlineShop-full-stack",
            "branch": "feature/checkout-flow",
            "ref": "refs/heads/feature/checkout-flow",
            "fullSha": SHA,
        },
        "build": {
            "workflowRunId": RUN_ID,
            "workflowRunAttempt": RUN_ATTEMPT,
            "workflowUrl": f"https://github.com/x/y/actions/runs/{RUN_ID}",
            "createdAt": datetime.now(UTC).isoformat(),
            "completedAt": datetime.now(UTC).isoformat(),
        },
        "artifacts": {
            "auth": {"repository": "onlineshop-auth", "digest": DIGEST_A},
            "items": {
                "repository": "onlineshop-items",
                "digest": DIGEST_B,
                "commonSourceSha": SHA,
            },
            "gateway": {"repository": "onlineshop-api-gateway", "digest": DIGEST_C},
            "frontend": {
                "artifactId": f"frontend-archive-{RUN_ID}-{RUN_ATTEMPT}",
                "artifactDigest": archive_digest,
                "contentChecksum": content_checksum,
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
    path = tmp_path / "candidate-manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def _passed_journeys(base_url, upstream_url):
    return [
        JourneyResult(name="frontend-index", conclusion="passed", detail="HTTP 200"),
        JourneyResult(name="items-api", conclusion="passed", detail="HTTP 401"),
        JourneyResult(name="gateway-health", conclusion="passed", detail="HTTP 200"),
    ]


class Runner:
    def __init__(self, monkeypatch, tmp_path, ids=None, repo_path=None):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.ids = ids or staging_identifiers()
        self.repo_path = repo_path or REPO_ROOT
        self.rds = FakeRds(status="stopped")
        self.ecs = FakeEcs(digests={s: DIGEST_A for s in SERVICES})
        self.ecs.digests = {
            SERVICES[0]: DIGEST_A,
            SERVICES[1]: DIGEST_B,
            SERVICES[2]: DIGEST_C,
        }
        self.ecr = FakeEcr()
        self.elb = FakeElb()
        self.s3 = FakeS3()
        self.secrets = FakeSecrets()
        self.logs = FakeLogs()
        self.sts = FakeSts()
        self.sql_steps = []
        self.archive, self.artifact_digest, self.content_checksum = _make_frontend_archive(tmp_path)
        self.candidate = _candidate_manifest(tmp_path, self.artifact_digest, self.content_checksum)
        self.identifiers_file = write_identifiers(tmp_path, self.ids)
        self.out = tmp_path / "staging-record.json"
        self.github = FakeGitHub(
            artifacts={(RUN_ID, RUN_ATTEMPT): standard_artifact_names(RUN_ID, RUN_ATTEMPT)}
        )
        self._install()

    def _install(self):
        clients = {
            "sts": self.sts,
            "ecs": self.ecs,
            "ecr": self.ecr,
            "rds": self.rds,
            "elb": self.elb,
            "s3": self.s3,
            "secretsmanager": self.secrets,
            "logs": self.logs,
        }
        self.monkeypatch.setattr(
            "delivery.aws.context.client_for", lambda ctx, service: clients[service]
        )
        self.monkeypatch.setattr(
            "delivery.commands.staging.GitHubApi",
            lambda repository, token=None: self.github,
        )
        self.monkeypatch.setattr(
            "delivery.commands.staging.run_readonly_journeys", _passed_journeys
        )

        def fake_sql(ctx, ids, steps, db_host):
            self.sql_steps.append(list(steps))
            return [{} for _ in steps]

        self.monkeypatch.setattr("delivery.commands.staging.execute_sql_steps", fake_sql)

    def lifecycle_argv(self, *extra):
        return [
            "staging",
            "lifecycle",
            "--candidate",
            str(self.candidate),
            "--frontend-archive",
            str(self.archive),
            "--owner",
            "tester",
            "--environment",
            "staging",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.out),
            "--e2e-url-out",
            str(self.tmp_path / "e2e-url.txt"),
            "--repo-path",
            str(self.repo_path),
            *extra,
        ]

    def continue_argv(self, conclusion):
        return [
            "staging",
            "lifecycle",
            "--continue",
            "--e2e-conclusion",
            conclusion,
            "--environment",
            "staging",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.out),
            "--repo-path",
            str(self.repo_path),
        ]

    def record(self):
        return json.loads(self.out.read_text())


@pytest.fixture
def runner(monkeypatch, tmp_path):
    return Runner(monkeypatch, tmp_path)


def test_lifecycle_first_invocation_happy_path(runner, capsys):
    code = main(runner.lifecycle_argv())
    assert code == 0
    record = runner.record()
    assert record["operationId"] == OPERATION_ID
    assert record["phase"] == "E2E"
    assert record["e2e"]["conclusion"] == "pending"
    assert record["e2eUrl"] == "http://staging-alb.example.com"
    assert record["compatibility"]["bootstrapException"] is True
    assert record["cleanup"]["conclusion"] == "not-run"
    # expected vs observed artifacts recorded distinctly
    assert record["artifactsExpected"]["authDigest"] == DIGEST_A
    assert record["artifactsObserved"]["authDigest"] == DIGEST_A
    assert record["artifactsObserved"]["frontendChecksum"] == runner.content_checksum
    # environment started: RDS available, services at desiredCount 1
    assert runner.rds.status == "available"
    assert all(runner.ecs.desired_counts[s] == 1 for s in SERVICES)
    # ownership marker acquired with the right identity
    marker = parse_marker(runner.rds.tags[MARKER_TAG_KEY])
    assert marker.operationId == OPERATION_ID
    assert marker.workflowRunId == RUN_ID
    assert marker.workflowRunAttempt == RUN_ATTEMPT
    # reset plan ran with schema/seed/grants/connectivity/negative steps
    steps = runner.sql_steps[0]
    assert len(steps) == 12
    assert any(
        step.database == "auth_staging" and step.user == "auth_app_staging" and step.read_only
        for step in steps
    )
    assert any(not step.expect_success for step in steps)
    # E2E URL file emitted for the workflow
    assert (runner.tmp_path / "e2e-url.txt").read_text().strip() == "http://staging-alb.example.com"
    # phase log covers every phase through E2E
    assert [entry["name"] for entry in record["phaseLog"]] == [
        "QUEUED",
        "OWNED",
        "STARTING",
        "RESETTING",
        "DEPLOYING",
        "COMPATIBILITY",
        "E2E",
    ]
    # candidate-frontend journeys recorded (D6)
    assert any(j["name"] == "candidate-frontend:items-api" for j in record["journeys"])
    assert "prepared for candidate" in capsys.readouterr().out


def test_lifecycle_keeps_services_stopped_until_reset_finishes(runner, monkeypatch):
    def fake_sql(ctx, ids, steps, db_host):
        assert all(runner.ecs.desired_counts[service] == 0 for service in SERVICES)
        runner.sql_steps.append(list(steps))
        return [{} for _ in steps]

    monkeypatch.setattr("delivery.commands.staging.execute_sql_steps", fake_sql)

    assert main(runner.lifecycle_argv()) == 0
    assert all(runner.ecs.desired_counts[service] == 1 for service in SERVICES)


def test_lifecycle_continuation_passed_completes(runner):
    assert main(runner.lifecycle_argv()) == 0
    code = main(runner.continue_argv("passed"))
    assert code == 0
    record = runner.record()
    assert record["phase"] == "COMPLETE"
    assert record["e2e"]["conclusion"] == "passed"
    assert record["cleanup"]["conclusion"] == "passed"
    assert record["failure"] is None
    # environment verified stopped and marker released
    assert runner.rds.status == "stopped"
    assert all(runner.ecs.desired_counts[s] == 0 for s in SERVICES)
    assert MARKER_TAG_KEY not in runner.rds.tags
    assert [entry["name"] for entry in record["phaseLog"]][-3:] == [
        "EVIDENCE",
        "STOPPING",
        "CLEANUP_VERIFY",
    ]


def test_lifecycle_continuation_e2e_failed_is_visible_failure(runner, capsys):
    assert main(runner.lifecycle_argv()) == 0
    code = main(runner.continue_argv("failed"))
    assert code == 1
    assert "ERROR E2E_FAILED" in capsys.readouterr().err
    record = runner.record()
    assert record["e2e"]["conclusion"] == "failed"
    assert record["cleanup"]["conclusion"] == "passed"
    assert record["phase"] == "CLEANUP_VERIFY"
    assert MARKER_TAG_KEY not in runner.rds.tags


def test_lifecycle_continuation_marker_lost_fails_closed(runner, capsys):
    assert main(runner.lifecycle_argv()) == 0
    runner.rds.tags.pop(MARKER_TAG_KEY, None)
    code = main(runner.continue_argv("passed"))
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR STG_MARKER_CONFLICT" in captured.err
    record = runner.record()
    assert record["failure"]["failedPhase"] == "E2E"
    assert "skipped" in record["failure"]["cleanupConclusion"]
    # ownership unverified: the running environment was not touched
    assert "stop_db_instance" not in runner.rds.calls
    assert all(runner.ecs.desired_counts[s] == 1 for s in SERVICES)


def test_lifecycle_continuation_foreign_marker_fails_closed(runner, capsys):
    assert main(runner.lifecycle_argv()) == 0
    runner.rds.tags[MARKER_TAG_KEY] = marker_tag_value(
        operation_id="stg-9999-9", run_id=9999, run_attempt=9
    )
    code = main(runner.continue_argv("passed"))
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR STG_MARKER_CONFLICT" in captured.err
    record = runner.record()
    # the foreign environment was not touched: cleanup skipped, marker intact
    assert "skipped" in record["failure"]["cleanupConclusion"]
    assert parse_marker(runner.rds.tags[MARKER_TAG_KEY]).operationId == "stg-9999-9"
    assert runner.rds.status == "available"


def test_lifecycle_continuation_requires_pending_e2e(runner, capsys):
    assert main(runner.lifecycle_argv()) == 0
    assert main(runner.continue_argv("passed")) == 0
    # a second continuation is refused: the conclusion is already decided
    code = main(runner.continue_argv("passed"))
    assert code == 1
    assert "requires a record at phase E2E" in capsys.readouterr().err


def test_lifecycle_marker_conflict_fails_closed_without_mutation(runner, capsys):
    runner.rds.tags[MARKER_TAG_KEY] = marker_tag_value()
    code = main(runner.lifecycle_argv())
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR STG_MARKER_CONFLICT" in captured.err
    record = runner.record()
    assert record["failure"]["failedPhase"] == "OWNED"
    assert record["failure"]["mutationBegan"] is False
    assert "skipped" in record["failure"]["cleanupConclusion"]
    # never stole the marker, never started the DB, never touched services
    assert parse_marker(runner.rds.tags[MARKER_TAG_KEY]).operationId == "stg-4712-2"
    assert "start_db_instance" not in runner.rds.calls
    assert all(runner.ecs.desired_counts[s] == 1 for s in SERVICES)
    assert not any(
        "onlineshop-auth-staging" in call["family"] for call in runner.ecs.register_calls
    )


def test_lifecycle_expired_marker_is_reacquired(runner):
    runner.rds.tags[MARKER_TAG_KEY] = marker_tag_value(expires_in=timedelta(hours=-1))
    assert main(runner.lifecycle_argv()) == 0
    marker = parse_marker(runner.rds.tags[MARKER_TAG_KEY])
    assert marker.operationId == OPERATION_ID


def test_lifecycle_ecr_digest_missing_fails_before_ecs_mutation(runner, capsys):
    runner.ecr.digests["items"] = f"sha256:{'f' * 64}"
    code = main(runner.lifecycle_argv())
    assert code == 1
    assert "ERROR NOT_FOUND" in capsys.readouterr().err
    assert runner.ecs.register_calls == []
    assert "start_db_instance" not in runner.rds.calls
    record = runner.record()
    assert record["failure"]["failedPhase"] == "OWNED"


def test_lifecycle_ecr_read_error_is_error_not_absence(runner, capsys):
    class BrokenEcr(FakeEcr):
        def describe_images(self, repositoryName, imageIds=None, **kwargs):
            raise client_error("AccessDenied")

    runner.ecr = BrokenEcr()
    runner._install()
    code = main(runner.lifecycle_argv())
    assert code == 1
    assert "ERROR READ_ERROR" in capsys.readouterr().err


def test_lifecycle_running_digest_mismatch_fails_and_cleans_up(runner, capsys):
    runner.ecs.digests[SERVICES[1]] = f"sha256:{'f' * 64}"
    code = main(runner.lifecycle_argv())
    assert code == 1
    assert "ERROR MUTATION_VERIFY" in capsys.readouterr().err
    record = runner.record()
    assert record["failure"]["failedPhase"] == "DEPLOYING"
    assert record["failure"]["mutationBegan"] is True
    assert record["failure"]["cleanupConclusion"] == "passed"
    assert runner.rds.status == "stopped"
    assert all(runner.ecs.desired_counts[s] == 0 for s in SERVICES)
    assert MARKER_TAG_KEY not in runner.rds.tags
    assert runner.out.with_name(runner.out.name + ".diagnostics.json").exists()


def test_lifecycle_cleanup_failure_is_distinct_code(runner, capsys, monkeypatch):
    monkeypatch.setattr("delivery.commands.staging.RDS_STOP_TIMEOUT", 0.6)
    assert main(runner.lifecycle_argv()) == 0
    runner.rds.stop_result = "stopping"
    code = main(runner.continue_argv("passed"))
    assert code == 1
    assert "ERROR CLEANUP_FAILED" in capsys.readouterr().err
    record = runner.record()
    assert record["e2e"]["conclusion"] == "passed"
    assert record["cleanup"]["conclusion"] == "failed"
    assert record["failure"]["cleanupConclusion"] == "failed"
    assert runner.out.with_name(runner.out.name + ".diagnostics.json").exists()


def test_lifecycle_cleanup_failure_after_successful_phases_blocks(runner, capsys, monkeypatch):
    monkeypatch.setattr("delivery.commands.staging.RDS_STOP_TIMEOUT", 0.6)
    assert main(runner.lifecycle_argv()) == 0
    runner.rds.stop_result = "stopping"
    code = main(runner.continue_argv("passed"))
    assert code == 1
    assert "ERROR CLEANUP_FAILED" in capsys.readouterr().err
    record = runner.record()
    assert record["e2e"]["conclusion"] == "passed"
    assert record["cleanup"]["conclusion"] == "failed"
    assert record["phase"] != "COMPLETE"


def test_lifecycle_marker_read_error_is_visible_failure(runner, capsys):
    runner.rds.tag_error = client_error("AccessDenied")
    code = main(runner.lifecycle_argv())
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR READ_ERROR" in captured.err
    record = runner.record()
    assert record["failure"]["failedPhase"] == "OWNED"
    assert record["failure"]["mutationBegan"] is False
    assert record["failure"]["cleanupConclusion"] == "skipped"
    assert record["cleanup"]["conclusion"] == "skipped"
    assert record["cleanup"]["reason"] == "ownership unverified"
    # ownership unverified: a possibly foreign environment is never touched
    assert "stop_db_instance" not in runner.rds.calls
    assert "start_db_instance" not in runner.rds.calls
    assert all(runner.ecs.desired_counts[s] == 1 for s in SERVICES)


def test_lifecycle_previous_official_release_journey(runner):

    release_manifest = {
        "schemaVersion": "1.0",
        "releaseId": "release-0007",
        "candidateId": "cand-1-1-111111111111",
        "source": {"fullSha": "1" * 40, "branch": "main"},
        "promotedAt": datetime.now(UTC).isoformat(),
        "requester": "owner",
        "approval": {"evidence": "env", "workflowUrl": "https://github.com/x"},
        "artifacts": {
            "auth": {"repository": "onlineshop-auth", "digest": DIGEST_A},
            "items": {"repository": "onlineshop-items", "digest": DIGEST_B},
            "gateway": {"repository": "onlineshop-api-gateway", "digest": DIGEST_C},
            "frontend": {"immutableIdentity": "release-0007", "checksum": f"{'a' * 64}"},
            "sbom": {
                "auth": {"assetName": "auth.spdx.json", "sha256": f"{'b' * 64}"},
                "items": {"assetName": "items.spdx.json", "sha256": f"{'b' * 64}"},
                "gateway": {"assetName": "api-gateway.spdx.json", "sha256": f"{'b' * 64}"},
                "frontend": {"assetName": "frontend.spdx.json", "sha256": f"{'b' * 64}"},
            },
        },
        "compatibilityFingerprint": f"{'c' * 64}",
        "staging": {"evidenceIdentity": "stg-1-1", "conclusion": "passed"},
        "productionVerification": {"evidenceIdentity": "pv-1", "conclusion": "passed"},
        "rollbackCapableAtPublication": True,
    }
    # previous official frontend bundle in S3 with a matching checksum
    prev_dir = runner.tmp_path / "prev-dist"
    prev_dir.mkdir()
    (prev_dir / "index.html").write_text('<html><body><div id="root"></div></body></html>')
    checksum = _content_checksum(prev_dir)
    release_manifest["artifacts"]["frontend"]["checksum"] = checksum
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
        bundle.add(prev_dir / "index.html", arcname="index.html")
    runner.s3.objects["_releases/release-0007/frontend.tar.gz"] = payload.getvalue()
    runner.github._assets = {
        "https://example.com/manifest.json": json.dumps(release_manifest).encode()
    }
    runner.github.releases = [
        {
            "tag_name": "release-0007",
            "id": 7,
            "assets": [
                {
                    "name": "release-manifest.json",
                    "url": "https://example.com/manifest.json",
                }
            ],
        }
    ]
    assert main(runner.lifecycle_argv()) == 0
    record = runner.record()
    assert record["compatibility"]["bootstrapException"] is False
    assert "release-0007" in record["compatibility"]["conclusion"]
    assert any(journey["name"].startswith("previous-frontend:") for journey in record["journeys"])
    assert any(journey["name"].startswith("candidate-frontend:") for journey in record["journeys"])


def test_lifecycle_previous_frontend_checksum_mismatch_fails(runner, capsys):
    release_manifest = {
        "schemaVersion": "1.0",
        "releaseId": "release-0007",
        "candidateId": "cand-1-1-111111111111",
        "source": {"fullSha": "1" * 40, "branch": "main"},
        "promotedAt": datetime.now(UTC).isoformat(),
        "requester": "owner",
        "approval": {"evidence": "env", "workflowUrl": "https://github.com/x"},
        "artifacts": {
            "auth": {"repository": "onlineshop-auth", "digest": DIGEST_A},
            "items": {"repository": "onlineshop-items", "digest": DIGEST_B},
            "gateway": {"repository": "onlineshop-api-gateway", "digest": DIGEST_C},
            "frontend": {"immutableIdentity": "release-0007", "checksum": f"{'e' * 64}"},
            "sbom": {
                "auth": {"assetName": "auth.spdx.json", "sha256": f"{'b' * 64}"},
                "items": {"assetName": "items.spdx.json", "sha256": f"{'b' * 64}"},
                "gateway": {"assetName": "api-gateway.spdx.json", "sha256": f"{'b' * 64}"},
                "frontend": {"assetName": "frontend.spdx.json", "sha256": f"{'b' * 64}"},
            },
        },
        "compatibilityFingerprint": f"{'c' * 64}",
        "staging": {"evidenceIdentity": "stg-1-1", "conclusion": "passed"},
        "productionVerification": {"evidenceIdentity": "pv-1", "conclusion": "passed"},
        "rollbackCapableAtPublication": True,
    }
    prev_dir = runner.tmp_path / "prev-dist"
    prev_dir.mkdir()
    (prev_dir / "index.html").write_text('<html><body><div id="root"></div></body></html>')
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
        bundle.add(prev_dir / "index.html", arcname="index.html")
    runner.s3.objects["_releases/release-0007/frontend.tar.gz"] = payload.getvalue()
    runner.github._assets = {
        "https://example.com/manifest.json": json.dumps(release_manifest).encode()
    }
    runner.github.releases = [
        {
            "tag_name": "release-0007",
            "id": 7,
            "assets": [
                {"name": "release-manifest.json", "url": "https://example.com/manifest.json"}
            ],
        }
    ]
    code = main(runner.lifecycle_argv())
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_lifecycle_previous_frontend_missing_from_s3_fails(runner, capsys):
    release_manifest = {
        "schemaVersion": "1.0",
        "releaseId": "release-0007",
        "candidateId": "cand-1-1-111111111111",
        "source": {"fullSha": "1" * 40, "branch": "main"},
        "promotedAt": datetime.now(UTC).isoformat(),
        "requester": "owner",
        "approval": {"evidence": "env", "workflowUrl": "https://github.com/x"},
        "artifacts": {
            "auth": {"repository": "onlineshop-auth", "digest": DIGEST_A},
            "items": {"repository": "onlineshop-items", "digest": DIGEST_B},
            "gateway": {"repository": "onlineshop-api-gateway", "digest": DIGEST_C},
            "frontend": {"immutableIdentity": "release-0007", "checksum": f"{'a' * 64}"},
            "sbom": {
                "auth": {"assetName": "auth.spdx.json", "sha256": f"{'b' * 64}"},
                "items": {"assetName": "items.spdx.json", "sha256": f"{'b' * 64}"},
                "gateway": {"assetName": "api-gateway.spdx.json", "sha256": f"{'b' * 64}"},
                "frontend": {"assetName": "frontend.spdx.json", "sha256": f"{'b' * 64}"},
            },
        },
        "compatibilityFingerprint": f"{'c' * 64}",
        "staging": {"evidenceIdentity": "stg-1-1", "conclusion": "passed"},
        "productionVerification": {"evidenceIdentity": "pv-1", "conclusion": "passed"},
        "rollbackCapableAtPublication": True,
    }
    runner.github._assets = {
        "https://example.com/manifest.json": json.dumps(release_manifest).encode()
    }
    runner.github.releases = [
        {
            "tag_name": "release-0007",
            "id": 7,
            "assets": [
                {"name": "release-manifest.json", "url": "https://example.com/manifest.json"}
            ],
        }
    ]
    code = main(runner.lifecycle_argv())
    assert code == 1
    assert "ERROR NOT_FOUND" in capsys.readouterr().err


def test_lifecycle_frontend_archive_digest_mismatch_rejected(runner, capsys):
    runner.archive.write_bytes(runner.archive.read_bytes() + b"tampered")
    code = main(runner.lifecycle_argv())
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR VALIDATION" in captured.err
    assert "digest" in captured.err
    assert not runner.out.exists()


def test_lifecycle_incomplete_github_artifacts_fail_closed(runner, capsys):
    runner.github.artifacts[(RUN_ID, RUN_ATTEMPT)] = standard_artifact_names(RUN_ID, RUN_ATTEMPT)[
        :-1
    ]
    code = main(runner.lifecycle_argv())
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR VALIDATION" in captured.err
    assert "incomplete" in captured.err
    assert "start_db_instance" not in runner.rds.calls


def test_lifecycle_identifiers_environment_mismatch_rejected(monkeypatch, tmp_path, capsys):
    runner = Runner(monkeypatch, tmp_path)
    bad = staging_identifiers(extra={"environment": "production"})
    bad.pop("frontendBucket", None)
    runner.identifiers_file = write_identifiers(tmp_path, bad)
    runner._install()
    code = main(runner.lifecycle_argv())
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_lifecycle_production_environment_rejected(monkeypatch, tmp_path, capsys):
    runner = Runner(monkeypatch, tmp_path)
    identifiers = {
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
    runner.identifiers_file = write_identifiers(tmp_path, identifiers)
    runner._install()
    code = main(
        [
            "staging",
            "lifecycle",
            "--candidate",
            str(runner.candidate),
            "--frontend-archive",
            str(runner.archive),
            "--owner",
            "tester",
            "--environment",
            "production",
            "--identifiers",
            str(runner.identifiers_file),
            "--out",
            str(runner.out),
            "--repo-path",
            str(runner.repo_path),
        ]
    )
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_lifecycle_unsafe_owner_rejected(runner, capsys):
    code = main(
        [
            "staging",
            "lifecycle",
            "--candidate",
            str(runner.candidate),
            "--frontend-archive",
            str(runner.archive),
            "--owner",
            "bad owner; rm -rf /",
            "--environment",
            "staging",
            "--identifiers",
            str(runner.identifiers_file),
            "--out",
            str(runner.out),
            "--repo-path",
            str(runner.repo_path),
        ]
    )
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR VALIDATION" in captured.err
    assert "unsafe owner" in captured.err


def test_lifecycle_expired_candidate_rejected(runner, capsys, monkeypatch):
    from datetime import timedelta

    manifest = json.loads(runner.candidate.read_text())
    manifest["build"]["completedAt"] = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    runner.candidate.write_text(json.dumps(manifest))
    code = main([*runner.lifecycle_argv(), "--max-age-days", "30"])
    assert code == 1
    assert "expired" in capsys.readouterr().err


def test_lifecycle_continue_flag_combinations_rejected(runner, capsys):
    code = main(
        [
            "staging",
            "lifecycle",
            "--continue",
            "--e2e-conclusion",
            "passed",
            "--candidate",
            str(runner.candidate),
            "--environment",
            "staging",
            "--identifiers",
            str(runner.identifiers_file),
            "--out",
            str(runner.out),
            "--repo-path",
            str(runner.repo_path),
        ]
    )
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# F1: --repo-path SQL source resolution (wheel-installed engine)
# ---------------------------------------------------------------------------

_SQL_FIXTURE_CONTENT = {
    "scripts/sql/staging-bootstrap.sql": "-- fixture staging-bootstrap",
    "scripts/sql/staging-auth-grants.sql": "-- fixture auth grants",
    "scripts/sql/staging-items-grants.sql": "-- fixture items grants",
    "Auth/init-db/01-schema.sql": "-- fixture auth schema",
    "Auth/init-db/02-seed-data.sql": "-- fixture auth seed",
    "Items/init-db/01-schema.sql": "-- fixture items schema",
    "Items/init-db/02-data.sql": "-- fixture items seed",
}


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture-repo"
    for relative, content in _SQL_FIXTURE_CONTENT.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return repo


def test_lifecycle_repo_path_resolves_reset_sql_sources(monkeypatch, tmp_path):
    repo = _fixture_repo(tmp_path)
    runner = Runner(monkeypatch, tmp_path, repo_path=repo)
    assert main(runner.lifecycle_argv()) == 0
    steps = runner.sql_steps[0]
    assert len(steps) == 12
    # the RESETTING plan was built from the checkout's SQL sources
    assert steps[1].sql == "-- fixture staging-bootstrap"
    assert steps[2].sql == "-- fixture auth schema"
    assert steps[5].sql == "-- fixture items schema"


def test_lifecycle_missing_sql_source_fails_closed_before_mutation(monkeypatch, tmp_path, capsys):
    repo = _fixture_repo(tmp_path)
    (repo / "scripts/sql/staging-bootstrap.sql").unlink()
    runner = Runner(monkeypatch, tmp_path, repo_path=repo)
    code = main(runner.lifecycle_argv())
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR VALIDATION" in captured.err
    assert "required SQL source" in captured.err
    assert "staging-bootstrap.sql" in captured.err
    # fail-fast before any AWS mutation or record write
    assert "start_db_instance" not in runner.rds.calls
    assert "add_tags_to_resource" not in runner.rds.calls
    assert not runner.out.exists()


def test_lifecycle_unreadable_repo_path_fails_closed(monkeypatch, tmp_path, capsys):
    runner = Runner(monkeypatch, tmp_path, repo_path=tmp_path / "no-such-checkout")
    code = main(runner.lifecycle_argv())
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR VALIDATION" in captured.err
    assert "required SQL source" in captured.err


# ---------------------------------------------------------------------------
# F3: draft/prerelease filtering for the previous-official-frontend journey
# ---------------------------------------------------------------------------


def test_lifecycle_draft_only_releases_bootstrap(runner):
    runner.github.releases = [
        {
            "tag_name": "release-0001",
            "id": 1,
            "draft": True,
            "prerelease": False,
            "assets": [],
        },
        {
            "tag_name": "release-0002",
            "id": 2,
            "draft": False,
            "prerelease": True,
            "assets": [],
        },
    ]
    assert main(runner.lifecycle_argv()) == 0
    record = runner.record()
    assert record["compatibility"]["bootstrapException"] is True


def test_lifecycle_newest_draft_falls_back_to_older_published(runner):
    release_manifest = {
        "schemaVersion": "1.0",
        "releaseId": "release-0007",
        "candidateId": "cand-1-1-111111111111",
        "source": {"fullSha": "1" * 40, "branch": "main"},
        "promotedAt": datetime.now(UTC).isoformat(),
        "requester": "owner",
        "approval": {"evidence": "env", "workflowUrl": "https://github.com/x"},
        "artifacts": {
            "auth": {"repository": "onlineshop-auth", "digest": DIGEST_A},
            "items": {"repository": "onlineshop-items", "digest": DIGEST_B},
            "gateway": {"repository": "onlineshop-api-gateway", "digest": DIGEST_C},
            "frontend": {"immutableIdentity": "release-0007", "checksum": f"{'a' * 64}"},
            "sbom": {
                "auth": {"assetName": "auth.spdx.json", "sha256": f"{'b' * 64}"},
                "items": {"assetName": "items.spdx.json", "sha256": f"{'b' * 64}"},
                "gateway": {"assetName": "api-gateway.spdx.json", "sha256": f"{'b' * 64}"},
                "frontend": {"assetName": "frontend.spdx.json", "sha256": f"{'b' * 64}"},
            },
        },
        "compatibilityFingerprint": f"{'c' * 64}",
        "staging": {"evidenceIdentity": "stg-1-1", "conclusion": "passed"},
        "productionVerification": {"evidenceIdentity": "pv-1", "conclusion": "passed"},
        "rollbackCapableAtPublication": True,
    }
    prev_dir = runner.tmp_path / "prev-dist"
    prev_dir.mkdir()
    (prev_dir / "index.html").write_text('<html><body><div id="root"></div></body></html>')
    checksum = _content_checksum(prev_dir)
    release_manifest["artifacts"]["frontend"]["checksum"] = checksum
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as bundle:
        bundle.add(prev_dir / "index.html", arcname="index.html")
    runner.s3.objects["_releases/release-0007/frontend.tar.gz"] = payload.getvalue()
    runner.github._assets = {
        "https://example.com/manifest.json": json.dumps(release_manifest).encode()
    }
    # newest is an unpublished draft; the published release below it is used
    runner.github.releases = [
        {
            "tag_name": "release-0008",
            "id": 8,
            "draft": True,
            "prerelease": False,
            "assets": [],
        },
        {
            "tag_name": "release-0007",
            "id": 7,
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "release-manifest.json",
                    "url": "https://example.com/manifest.json",
                }
            ],
        },
    ]
    assert main(runner.lifecycle_argv()) == 0
    record = runner.record()
    assert record["compatibility"]["bootstrapException"] is False
    assert "release-0007" in record["compatibility"]["conclusion"]


def test_lifecycle_newest_published_without_manifest_fails_closed(runner, capsys):
    runner.github.releases = [
        {
            "tag_name": "release-0007",
            "id": 7,
            "draft": False,
            "prerelease": False,
            "assets": [],
        },
        {
            "tag_name": "release-0006",
            "id": 6,
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "release-manifest.json",
                    "url": "https://example.com/manifest.json",
                }
            ],
        },
    ]
    code = main(runner.lifecycle_argv())
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR NOT_FOUND" in captured.err
    assert "release-0007" in captured.err


# ---------------------------------------------------------------------------
# F5: not-run E2E conclusion
# ---------------------------------------------------------------------------


def test_lifecycle_continuation_not_run_suite_is_not_recorded_as_failed(runner, capsys):
    assert main(runner.lifecycle_argv()) == 0
    record = json.loads(runner.out.read_text())
    # the previous invocation reached phase E2E but never recorded a suite
    # result (e.g. it died resolving the E2E URL)
    record["e2e"]["conclusion"] = "not-run"
    runner.out.write_text(json.dumps(record))
    code = main(runner.continue_argv("passed"))
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR E2E_FAILED" in captured.err
    assert "not-run" in captured.err
    final = runner.record()
    # the CLI "passed" is discarded: the record never claims a suite ran
    assert final["e2e"]["conclusion"] == "not-run"
    assert final["phase"] == "CLEANUP_VERIFY"
    # the owned environment was still cleaned up and the marker released
    assert final["cleanup"]["conclusion"] == "passed"
    assert runner.rds.status == "stopped"
    assert MARKER_TAG_KEY not in runner.rds.tags


# ---------------------------------------------------------------------------
# F9: owner length bound (GitHub logins are <= 39 characters)
# ---------------------------------------------------------------------------


def test_lifecycle_owner_over_39_chars_rejected(runner, capsys):
    code = main([*runner.lifecycle_argv(), "--owner", "x" * 40])
    assert code == 1
    captured = capsys.readouterr()
    assert "ERROR VALIDATION" in captured.err
    assert "unsafe owner" in captured.err
