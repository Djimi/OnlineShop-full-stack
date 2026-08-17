"""Offline gates for promote preflight (VR-PRO-01 / OP-PRO-02).

Every failure path is exercised through the real CLI: candidate eligibility,
expiry, the exact staging gate, ECR existence, AD-11 newer-candidate
reachability/warnings, post-approval drift, the OP-DB migration-ownership
gate, and read-error-versus-absence semantics.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import client_error
from fakes_production import (
    CANDIDATE_SHA,
    DIGESTS,
    RUN_ATTEMPT,
    RUN_ID,
    STAGING_ATTEMPT,
    STAGING_RUN_ID,
    FakeEcr,
    FakeEcs,
    FakeElb,
    FakeGithub,
    FakeRds,
    FakeS3,
    FakeSts,
    candidate_artifact_names,
    complete_staging_record,
    main_candidate,
    make_frontend_archive,
    make_staging_record_zip,
    production_identifiers,
    write_identifiers,
    write_snapshot,
)

from delivery.cli import main
from delivery.live_marker import LiveMarker, marker_document

REPO_ROOT = Path(__file__).resolve().parents[2]


class PreflightEnv:
    def __init__(self, monkeypatch, tmp_path, *, repo_path=None):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.repo_path = repo_path or tmp_path
        self.ids = production_identifiers()
        self.identifiers_file = write_identifiers(tmp_path, self.ids)
        self.archive, self.artifact_digest, self.content_checksum = make_frontend_archive(tmp_path)
        self.candidate = main_candidate(
            tmp_path,
            artifact_digest=self.artifact_digest,
            content_checksum_value=self.content_checksum,
        )
        self.candidate_raw = json.loads(self.candidate.read_text())
        self.marker_doc = self._marker()
        self.snapshot = write_snapshot(
            tmp_path, self.ids, digests=DIGESTS, marker_doc=self.marker_doc
        )
        self.sts = FakeSts()
        self.ecs = FakeEcs(digests=DIGESTS)
        self.ecr = FakeEcr(digests=DIGESTS)
        self.s3 = FakeS3({self.ids["frontendLiveMarker"]: self.marker_doc.encode()})
        self.elb = FakeElb()
        self.rds = FakeRds()
        self.cf = None
        record = complete_staging_record(self.candidate_raw)
        self.github = FakeGithub(
            run_artifacts=candidate_artifact_names(),
            artifacts_by_run={
                STAGING_RUN_ID: [
                    (f"staging-record-{STAGING_RUN_ID}-{STAGING_ATTEMPT}", STAGING_ATTEMPT)
                ]
            },
        )
        self.github.artifact_zips[
            f"staging-record-{STAGING_RUN_ID}-{STAGING_ATTEMPT}"
        ] = make_staging_record_zip(record)
        self.out = tmp_path / "preflight-report.json"
        self._install()

    def _marker(self) -> str:
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
            "elbv2": self.elb,
            "rds": self.rds,
        }
        self.monkeypatch.setattr(
            "delivery.aws.context.client_for", lambda ctx, service: clients[service]
        )
        self.monkeypatch.setattr(
            "delivery.commands.promote.GitHubApi",
            lambda repository, token=None: self.github,
        )

    def argv(self, *extra):
        return [
            "promote",
            "preflight",
            "--candidate",
            str(self.candidate),
            "--frontend-archive",
            str(self.archive),
            "--sbom-dir",
            str(self.sbom_dir()),
            "--staging-run",
            str(STAGING_RUN_ID),
            "--snapshot",
            str(self.snapshot),
            "--repo-path",
            str(self.repo_path),
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.out),
            *extra,
        ]

    def sbom_dir(self) -> Path:
        directory = self.tmp_path / "sboms"
        directory.mkdir(exist_ok=True)
        for name in ("auth", "items", "api-gateway", "frontend"):
            (directory / f"{name}.spdx.json").write_text('{"spdxVersion": "SPDX-2.3"}')
        return directory

    def report(self) -> dict:
        return json.loads(self.out.read_text())


@pytest.fixture
def env(monkeypatch, tmp_path):
    return PreflightEnv(monkeypatch, tmp_path)


def test_preflight_happy_path(env, capsys):
    code = main(
        env.argv(
            "--staging-record-out",
            str(env.tmp_path / "staging-record-copy.json"),
        )
    )
    assert code == 0, capsys.readouterr().err
    report = env.report()
    assert report["candidate"]["candidateId"] == env.candidate_raw["candidateId"]
    assert report["candidate"]["fullSha"] == CANDIDATE_SHA
    assert report["candidate"]["candidateClass"] == "main"
    assert report["newerCandidateWarning"] == "none"
    assert report["stagingGate"]["e2eConclusion"] == "passed"
    assert report["stagingGate"]["cleanupConclusion"] == "passed"
    assert report["stagingGate"]["evidenceIdentity"] == (
        f"staging-record-{RUN_ID}-{RUN_ATTEMPT}"
    )
    assert "staging compatibility: bootstrap-exception" in report["approvalSummary"]
    assert len(report["approvalIdentity"]) == 64
    captured = capsys.readouterr()
    assert CANDIDATE_SHA in captured.out
    assert "APPROVAL SUMMARY" in captured.out
    # the validated staging record was copied for finalize
    assert (env.tmp_path / "staging-record-copy.json").exists()


def test_preflight_rejects_feature_candidate(env, capsys):
    feature_dir = env.tmp_path / "feature"
    feature_dir.mkdir()
    _archive, digest, checksum = make_frontend_archive(feature_dir)
    env.candidate = main_candidate(
        feature_dir,
        artifact_digest=digest,
        content_checksum_value=checksum,
        candidate_class="feature",
        production_eligible=False,
    )
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "main-class" in err


def test_preflight_rejects_not_production_eligible(env, capsys):
    env.candidate = main_candidate(
        env.tmp_path, production_eligible=False, artifact_digest=env.artifact_digest,
        content_checksum_value=env.content_checksum,
    )
    code = main(env.argv())
    assert code == 1
    assert "productionEligible" in capsys.readouterr().err


def test_preflight_rejects_non_main_branch(env, capsys):
    env.candidate = main_candidate(
        env.tmp_path, branch="feature/x", artifact_digest=env.artifact_digest,
        content_checksum_value=env.content_checksum,
    )
    code = main(env.argv())
    assert code == 1
    assert "branch" in capsys.readouterr().err


def test_preflight_rejects_expired_candidate(env, capsys):
    env.candidate = main_candidate(
        env.tmp_path,
        completed_days_ago=31,
        artifact_digest=env.artifact_digest,
        content_checksum_value=env.content_checksum,
    )
    code = main(env.argv())
    assert code == 1
    assert "expired" in capsys.readouterr().err


def test_preflight_rejects_staging_record_for_other_candidate(env, capsys):
    other_dir = env.tmp_path / "other"
    other_dir.mkdir()
    other = main_candidate(other_dir, run_id=9999)
    record = complete_staging_record(json.loads(other.read_text()))
    env.github.artifact_zips[
        f"staging-record-{STAGING_RUN_ID}-{STAGING_ATTEMPT}"
    ] = make_staging_record_zip(record)
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "does not reference the provided candidate" in err


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.__setitem__("e2e", {"conclusion": "failed"}),
        lambda record: record.__setitem__("cleanup", {"conclusion": "failed"}),
        lambda record: record.__setitem__("phase", "E2E"),
        lambda record: record.__setitem__("failure", {"mutationBegan": True}),
    ],
)
def test_preflight_rejects_invalid_staging_gate(env, capsys, mutate):
    record = complete_staging_record(env.candidate_raw)
    mutate(record)
    env.github.artifact_zips[
        f"staging-record-{STAGING_RUN_ID}-{STAGING_ATTEMPT}"
    ] = make_staging_record_zip(record)
    code = main(env.argv())
    assert code == 1, capsys.readouterr().err
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_preflight_rejects_staging_run_without_record(env, capsys):
    env.github.artifacts_by_run = {STAGING_RUN_ID: []}
    code = main(env.argv())
    assert code == 1
    assert "no staging-record" in capsys.readouterr().err


def test_preflight_rejects_incomplete_candidate_artifacts(env, capsys):
    env.github.run_artifacts = candidate_artifact_names()[1:]
    code = main(env.argv())
    assert code == 1
    assert "incomplete (CT-CAND-03)" in capsys.readouterr().err


def test_preflight_rejects_missing_ecr_digest(env, capsys):
    env.ecr.digests = {"auth": DIGESTS["auth"], "items": DIGESTS["items"]}
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR NOT_FOUND" in err
    assert "does not exist in repository" in err


def test_preflight_ecr_read_error_is_not_absence(env, capsys):
    class BrokenEcr:
        def describe_images(self, repositoryName, imageIds=None, **kwargs):
            raise client_error("ThrottlingException")

    env.ecr = BrokenEcr()
    env._install()
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR READ_ERROR" in err
    assert "NOT_FOUND" not in err


def test_preflight_newer_candidate_reachable_emits_warning(env, capsys):
    env.github.newer_candidates = [
        {
            "id": RUN_ID + 7,
            "run_attempt": 1,
            "head_sha": "3" * 40,
            "created_at": datetime.now(UTC).isoformat(),
            "html_url": "https://github.com/x/y/actions/runs/4718",
        }
    ]
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    report = env.report()
    assert report["newerCandidateWarning"] != "none"
    assert "newer complete main candidate" in report["newerCandidateWarning"]
    assert "AD-11" in report["approvalSummary"]


def test_preflight_newer_candidate_diverged_rejected(env, capsys):
    env.github.newer_candidates = [
        {
            "id": RUN_ID + 7,
            "run_attempt": 1,
            "head_sha": "3" * 40,
            "created_at": datetime.now(UTC).isoformat(),
            "html_url": "https://github.com/x/y/actions/runs/4718",
        }
    ]
    env.github.compare = {("main", CANDIDATE_SHA): "diverged"}
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "reachability (AD-11)" in err


def test_preflight_rejects_candidate_older_than_production(env, capsys):
    production_sha = "4" * 40
    marker = LiveMarker(
        releaseId="release-0007",
        candidateId="cand-old",
        sourceSha=production_sha,
        frontendSha256=env.content_checksum,
    )
    env.marker_doc = marker_document(marker)
    env.snapshot = write_snapshot(
        env.tmp_path, env.ids, digests=DIGESTS, marker_doc=env.marker_doc, release_id="release-0007"
    )
    env.s3.objects[env.ids["frontendLiveMarker"]] = env.marker_doc.encode()
    env.github.newer_candidates = [
        {
            "id": RUN_ID + 7,
            "run_attempt": 1,
            "head_sha": "3" * 40,
            "created_at": datetime.now(UTC).isoformat(),
            "html_url": "https://github.com/x/y/actions/runs/4718",
        }
    ]
    env.github.compare = {
        ("main", CANDIDATE_SHA): "behind",
        (production_sha, CANDIDATE_SHA): "behind",
    }
    code = main(env.argv())
    assert code == 1
    assert "older than" in capsys.readouterr().err


def test_preflight_post_approval_drift_aborts(env, capsys):
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    approved = env.report()
    # mutate production state AFTER approval: the fresh snapshot drifts
    drifted = f"sha256:{'9' * 64}"
    env.snapshot = write_snapshot(
        env.tmp_path,
        env.ids,
        digests={"auth": drifted, "items": DIGESTS["items"], "gateway": DIGESTS["gateway"]},
        marker_doc=env.marker_doc,
    )
    report_file = env.tmp_path / "approved-report.json"
    report_file.write_text(json.dumps(approved))
    code = main(env.argv("--previous-report", str(report_file)))
    assert code == 1
    err = capsys.readouterr().err
    assert "POST-APPROVAL DRIFT" in err


def test_preflight_post_approval_identical_state_passes(env, capsys):
    code = main(env.argv())
    assert code == 0
    approved = env.report()
    report_file = env.tmp_path / "approved-report.json"
    report_file.write_text(json.dumps(approved))
    code = main(env.argv("--previous-report", str(report_file)))
    assert code == 0, capsys.readouterr().err


def test_preflight_op_db_gate_rejects_migration_ownership(env, capsys):
    migrations = env.repo_path / "Auth" / "db" / "migration"
    migrations.mkdir(parents=True)
    (migrations / "V1__init.sql").write_text("SELECT 1;\n")
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "migration-ownership files" in err
    assert "OP-DB-01" in err or "AD-15" in err


def test_preflight_op_db_gate_documents_block_when_clean(env, capsys):
    code = main(env.argv())
    assert code == 0
    assert "schema changes remain blocked" in env.report()["opDbGate"]


def test_preflight_live_marker_drift_aborts(env, capsys):
    env.s3.objects[env.ids["frontendLiveMarker"]] = b"changed-marker"
    code = main(env.argv())
    assert code == 1
    assert "live marker drift" in capsys.readouterr().err


def test_preflight_requires_production_environment(env, capsys):
    # the snapshot guard rejects a production snapshot against --environment
    # staging before any AWS work
    code = main(env.argv("--environment", "staging"))
    assert code == 1
    assert "environment 'staging'" in capsys.readouterr().err


def test_preflight_snapshot_environment_guard(env, capsys):
    env.snapshot = env.tmp_path / "snap.json"
    env.snapshot.write_text('{"environment": "staging"}')
    code = main(env.argv())
    assert code == 1
    assert "snapshot declares 'staging'" in capsys.readouterr().err


def test_preflight_rejects_malformed_sbom_dir(env, capsys):
    directory = env.tmp_path / "bad-sboms"
    directory.mkdir()
    (directory / "auth.spdx.json").write_text("{not json")
    code = main(
        env.argv("--sbom-dir", str(directory))
    )
    assert code == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_preflight_rejects_unsafe_staging_run_id(env, capsys):
    code = main(
        env.argv("--staging-run", "0")
    )
    assert code == 1


def test_preflight_staging_compatibility_passed(env, capsys):
    record = complete_staging_record(env.candidate_raw)
    record["compatibility"] = {
        "conclusion": "previous official frontend release-0001 against "
        "candidate backends: passed",
        "bootstrapException": False,
    }
    env.github.artifact_zips[
        f"staging-record-{STAGING_RUN_ID}-{STAGING_ATTEMPT}"
    ] = make_staging_record_zip(record)
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    assert "staging compatibility: passed" in env.report()["approvalSummary"]


def test_preflight_staging_bootstrap_without_prior_official_passes(env, capsys):
    # the default complete record carries a bootstrap exception and no
    # official releases exist yet — the AD-15 journey was legitimately skipped
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    assert "staging compatibility: bootstrap-exception" in env.report()["approvalSummary"]


def test_preflight_staging_bootstrap_with_older_prior_official_rejected(env, capsys):
    record = complete_staging_record(env.candidate_raw)
    completed = datetime.fromisoformat(
        record["completedAt"].replace("Z", "+00:00")
    )
    env.github.releases = [
        {
            "tag_name": "release-0001",
            "id": 1,
            "assets": [],
            "published_at": (completed - timedelta(hours=1)).isoformat(),
        }
    ]
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "bootstrap compatibility exception" in err
    assert "was required but never executed" in err
    assert "AD-15" in err


def test_preflight_staging_bootstrap_with_newer_prior_official_passes(env, capsys):
    record = complete_staging_record(env.candidate_raw)
    completed = datetime.fromisoformat(
        record["completedAt"].replace("Z", "+00:00")
    )
    env.github.releases = [
        {
            "tag_name": "release-0001",
            "id": 1,
            "assets": [],
            "published_at": (completed + timedelta(hours=1)).isoformat(),
        }
    ]
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err


def test_preflight_staging_compatibility_not_run_rejected(env, capsys):
    record = complete_staging_record(env.candidate_raw)
    record["compatibility"] = {"conclusion": "not-run", "bootstrapException": False}
    env.github.artifact_zips[
        f"staging-record-{STAGING_RUN_ID}-{STAGING_ATTEMPT}"
    ] = make_staging_record_zip(record)
    code = main(env.argv())
    assert code == 1
    assert "compatibility conclusion is 'not-run'" in capsys.readouterr().err


def test_preflight_post_approval_warning_drift_aborts(env, capsys):
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    approved = env.report()
    # a newer main candidate appears AFTER approval: the recomputed identity
    # (which now embeds the AD-11 warning text) differs from what the
    # approver saw — abort before any mutation (OP-PRO-02)
    env.github.newer_candidates = [
        {
            "id": RUN_ID + 7,
            "run_attempt": 1,
            "head_sha": "3" * 40,
            "created_at": datetime.now(UTC).isoformat(),
            "html_url": "https://github.com/x/y/actions/runs/4718",
        }
    ]
    report_file = env.tmp_path / "approved-report.json"
    report_file.write_text(json.dumps(approved))
    code = main(env.argv("--previous-report", str(report_file)))
    assert code == 1
    err = capsys.readouterr().err
    assert "POST-APPROVAL DRIFT" in err
