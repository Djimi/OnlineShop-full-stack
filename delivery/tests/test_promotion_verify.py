"""Offline gates for verify production (VR-PRO-03 / CT-PROD-01..04)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import client_error
from fakes_production import (
    CANDIDATE_SHA,
    DIGESTS,
    REGISTRY,
    FakeCloudFront,
    FakeEcs,
    FakeElb,
    FakeS3,
    FakeSts,
    main_candidate,
    make_frontend_archive,
    production_identifiers,
    write_identifiers,
    write_snapshot,
)

from delivery.cli import main
from delivery.live_marker import LiveMarker, marker_document


class VerifyEnv:
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
        self.marker = LiveMarker(
            releaseId=None,
            candidateId=self.candidate_raw["candidateId"],
            sourceSha=CANDIDATE_SHA,
            frontendSha256=self.content_checksum,
        )
        self.marker_doc = marker_document(self.marker)
        self.sts = FakeSts()
        self.ecs = FakeEcs(digests=DIGESTS)
        self.s3 = FakeS3({self.ids["frontendLiveMarker"]: self.marker_doc.encode()})
        self.elb = FakeElb()
        self.cf = FakeCloudFront()
        self.responses: dict[str, tuple[int, dict, bytes]] = {}
        self._install()

    def _install(self):
        clients = {
            "sts": self.sts,
            "ecs": self.ecs,
            "s3": self.s3,
            "elb": self.elb,
            "cloudfront": self.cf,
        }
        self.monkeypatch.setattr(
            "delivery.aws.context.client_for", lambda ctx, service: clients[service]
        )
        self.monkeypatch.setattr(
            "delivery.commands.verify._PUBLIC_MARKER_TIMEOUT", 1
        )
        self.monkeypatch.setattr(
            "delivery.commands.verify._FRONTEND_INDEX_TIMEOUT", 1
        )

        def fake_fetch(url):
            if url in self.responses:
                return self.responses[url]
            status, headers, body = self.responses.get("*", (404, {}, b""))
            return status, headers, body

        self.monkeypatch.setattr("delivery.commands.verify._fetch", fake_fetch)

    def pass_all_journeys(self):
        self.responses = {
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
                self.marker_doc.encode(),
            ),
            f"https://{self.cf.domain_name}/": (
                200,
                {"Content-Type": "text/html"},
                b'<html><body><div id="root"></div></body></html>',
            ),
        }

    def argv(self, *extra):
        return [
            "verify",
            "production",
            "--candidate",
            str(self.candidate),
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.tmp_path / "verification-report.json"),
            *extra,
        ]

    def report(self) -> dict:
        return json.loads((self.tmp_path / "verification-report.json").read_text())

    def snapshot_file(self, *, digests=None, marker_doc=None, release_id=None) -> str:
        return str(
            write_snapshot(
                self.tmp_path,
                self.ids,
                digests=digests or DIGESTS,
                marker_doc=marker_doc or self.marker_doc,
                release_id=release_id,
            )
        )

    def snapshot_argv(self, snapshot: str) -> list[str]:
        return [
            "verify",
            "production",
            "--snapshot",
            snapshot,
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.tmp_path / "verification-report.json"),
        ]


@pytest.fixture
def env(monkeypatch, tmp_path):
    return VerifyEnv(monkeypatch, tmp_path)


def test_verify_candidate_happy_path(env, capsys):
    env.pass_all_journeys()
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    report = env.report()
    assert report["conclusion"] == "passed"
    for key in ("auth", "items", "gateway"):
        assert report["services"][key]["match"] is True
        assert report["services"][key]["runningDigests"] == [DIGESTS[key]]
    names = {journey["name"] for journey in report["journeys"]}
    assert names == {
        "gateway-health",
        "items-api",
        "frontend-marker-public",
        "frontend-index-public",
    }
    assert all(journey["conclusion"] == "passed" for journey in report["journeys"])


def test_verify_release_manifest_happy_path(env, capsys):
    release = {
        "schemaVersion": "1.0",
        "releaseId": "release-0009",
        "candidateId": env.candidate_raw["candidateId"],
        "source": {"fullSha": CANDIDATE_SHA, "branch": "main"},
        "previousReleaseId": None,
        "promotedAt": "2026-08-16T10:00:00Z",
        "requester": "owner",
        "approval": {"evidence": "env", "workflowUrl": "https://github.com/x/y/actions/runs/1"},
        "artifacts": {
            "auth": {"repository": f"{REGISTRY}/onlineshop-auth", "digest": DIGESTS["auth"]},
            "items": {"repository": f"{REGISTRY}/onlineshop-items", "digest": DIGESTS["items"]},
            "gateway": {
                "repository": f"{REGISTRY}/onlineshop-api-gateway",
                "digest": DIGESTS["gateway"],
            },
            "frontend": {
                "immutableIdentity": "_releases/release-0009/",
                "checksum": env.content_checksum,
            },
            "sbom": {
                component: {"assetName": f"{component}.spdx.json", "sha256": "a" * 64}
                for component in ("auth", "items", "gateway", "frontend")
            },
        },
        "compatibilityFingerprint": "f" * 64,
        "staging": {"evidenceIdentity": "staging-record-9001-2", "conclusion": "passed"},
        "productionVerification": {"evidenceIdentity": "vrf-1", "conclusion": "passed"},
        "rollbackCapableAtPublication": True,
    }
    manifest = env.tmp_path / "release-manifest.json"
    manifest.write_text(json.dumps(release))
    official = LiveMarker(
        releaseId="release-0009",
        candidateId=env.candidate_raw["candidateId"],
        sourceSha=CANDIDATE_SHA,
        frontendSha256=env.content_checksum,
    )
    env.marker_doc = marker_document(official)
    env.s3.objects[env.ids["frontendLiveMarker"]] = env.marker_doc.encode()
    env.pass_all_journeys()
    code = main(
        [
            "verify",
            "production",
            "--manifest",
            str(manifest),
            "--environment",
            "production",
            "--identifiers",
            str(env.identifiers_file),
            "--out",
            str(env.tmp_path / "verification-report.json"),
        ]
    )
    assert code == 0, capsys.readouterr().err
    assert env.report()["conclusion"] == "passed"


def test_verify_requires_exactly_one_of_manifest_or_candidate(env, capsys):
    assert main(env.argv("--manifest", "rel.json")) == 2


def test_verify_running_digest_mismatch_fails_with_report(env, capsys):
    env.pass_all_journeys()
    env.ecs.digests = {
        "onlineshop-auth": f"sha256:{'9' * 64}",
        "onlineshop-items": DIGESTS["items"],
        "onlineshop-api-gateway": DIGESTS["gateway"],
    }
    code = main(env.argv())
    assert code == 1
    assert "running digests" in capsys.readouterr().err
    report = env.report()
    assert report["conclusion"] == "failed"
    assert report["services"]["auth"]["match"] is False


def test_verify_frontend_marker_mismatch_fails(env, capsys):
    env.pass_all_journeys()
    env.s3.objects[env.ids["frontendLiveMarker"]] = b'{"wrong": true}'
    code = main(env.argv())
    assert code == 1
    assert "live marker content mismatch" in capsys.readouterr().err
    assert env.report()["conclusion"] == "failed"


def test_verify_public_marker_mismatch_fails(env, capsys):
    env.pass_all_journeys()
    env.responses[f"https://{env.cf.domain_name}/release.json"] = (
        200,
        {"Content-Type": "application/json"},
        b'{"releaseId":"release-0001","candidateId":"other","sourceSha":"'
        + b"1" * 40
        + b'","frontendSha256":"'
        + b"1" * 64
        + b'"}',
    )
    code = main(env.argv())
    assert code == 1
    report = env.report()
    assert report["conclusion"] == "failed"
    marker_journey = next(
        journey for journey in report["journeys"] if journey["name"] == "frontend-marker-public"
    )
    assert marker_journey["conclusion"] == "failed"


def test_verify_gateway_health_failure_fails(env, capsys):
    env.pass_all_journeys()
    env.responses["http://onlineshop-alb.example.com/actuator/health"] = (
        503,
        {"Content-Type": "application/json"},
        b'{"status":"DOWN"}',
    )
    code = main(env.argv())
    assert code == 1
    assert "gateway-health" in capsys.readouterr().err
    assert env.report()["conclusion"] == "failed"


def test_verify_ecs_read_error_is_visible_failure(env, capsys):
    class BrokenEcs:
        def describe_services(self, cluster, services):
            raise client_error("ThrottlingException")

    env.ecs = BrokenEcs()
    env._install()
    code = main(env.argv())
    assert code == 1
    assert "ERROR READ_ERROR" in capsys.readouterr().err


def test_verify_environment_guard(env, capsys):
    code = main(env.argv("--environment", "staging"))
    assert code == 1
    assert "does not match" in capsys.readouterr().err


def test_verify_failed_rollout_state_fails(env, capsys):
    from fakes_production import FakeEcs

    class FailedRolloutEcs(FakeEcs):
        def _service(self, service):
            observed = super()._service(service)
            observed["deployments"][0]["rolloutState"] = "FAILED"
            return observed

    env.pass_all_journeys()
    env.ecs = FailedRolloutEcs(digests=DIGESTS)
    env._install()
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "rolloutState is FAILED" in err
    report = env.report()
    assert report["conclusion"] == "failed"
    for key in ("auth", "items", "gateway"):
        assert report["services"][key]["health"] == "FAILED"


# ---------------------------------------------------------------------------
# verify production --snapshot (post-compensation, OP-REC-02)
# ---------------------------------------------------------------------------


def test_verify_snapshot_happy_path(env, capsys):
    env.pass_all_journeys()
    code = main(env.snapshot_argv(env.snapshot_file()))
    assert code == 0, capsys.readouterr().err
    report = env.report()
    assert report["conclusion"] == "passed"
    for key in ("auth", "items", "gateway"):
        assert report["services"][key]["match"] is True
        assert report["services"][key]["expectedDigest"] == DIGESTS[key]
    assert all(journey["conclusion"] == "passed" for journey in report["journeys"])


def test_verify_snapshot_official_marker_happy_path(env, capsys):
    official = LiveMarker(
        releaseId="release-0001",
        candidateId=env.candidate_raw["candidateId"],
        sourceSha=CANDIDATE_SHA,
        frontendSha256=env.content_checksum,
    )
    env.marker_doc = marker_document(official)
    env.s3.objects[env.ids["frontendLiveMarker"]] = env.marker_doc.encode()
    env.pass_all_journeys()
    snapshot = env.snapshot_file(marker_doc=env.marker_doc, release_id="release-0001")
    code = main(env.snapshot_argv(snapshot))
    assert code == 0, capsys.readouterr().err
    assert env.report()["conclusion"] == "passed"


def test_verify_snapshot_running_digest_mismatch_fails(env, capsys):
    env.pass_all_journeys()
    env.ecs.digests = {
        "onlineshop-auth": f"sha256:{'9' * 64}",
        "onlineshop-items": DIGESTS["items"],
        "onlineshop-api-gateway": DIGESTS["gateway"],
    }
    code = main(env.snapshot_argv(env.snapshot_file()))
    assert code == 1
    assert "running digests" in capsys.readouterr().err
    report = env.report()
    assert report["conclusion"] == "failed"
    assert report["services"]["auth"]["match"] is False


def test_verify_snapshot_marker_mismatch_fails(env, capsys):
    env.pass_all_journeys()
    env.s3.objects[env.ids["frontendLiveMarker"]] = b'{"wrong": true}'
    code = main(env.snapshot_argv(env.snapshot_file()))
    assert code == 1
    assert "live marker content mismatch" in capsys.readouterr().err
    assert env.report()["conclusion"] == "failed"


def test_verify_snapshot_public_marker_mismatch_fails(env, capsys):
    env.pass_all_journeys()
    env.responses[f"https://{env.cf.domain_name}/release.json"] = (
        200,
        {"Content-Type": "application/json"},
        b'{"candidateId":"other","sourceSha":"' + b"1" * 40 + b'","frontendSha256":"'
        + b"1" * 64
        + b'"}',
    )
    code = main(env.snapshot_argv(env.snapshot_file()))
    assert code == 1
    report = env.report()
    assert report["conclusion"] == "failed"
    marker_journey = next(
        journey for journey in report["journeys"] if journey["name"] == "frontend-marker-public"
    )
    assert marker_journey["conclusion"] == "failed"


def test_verify_snapshot_ambiguous_running_digests_fails_closed(env, capsys):
    snapshot = env.tmp_path / "ambiguous-snapshot.json"
    raw = json.loads(Path(env.snapshot_file()).read_text())
    raw["services"]["auth"]["runningDigests"] = [DIGESTS["auth"], f"sha256:{'9' * 64}"]
    snapshot.write_text(json.dumps(raw))
    code = main(env.snapshot_argv(str(snapshot)))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "ambiguous snapshot" in err
    assert not (env.tmp_path / "verification-report.json").exists()


def test_verify_snapshot_missing_service_observation_fails_closed(env, capsys):
    snapshot = env.tmp_path / "incomplete-snapshot.json"
    raw = json.loads(Path(env.snapshot_file()).read_text())
    del raw["services"]["items"]
    snapshot.write_text(json.dumps(raw))
    code = main(env.snapshot_argv(str(snapshot)))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "no observation" in err


def test_verify_snapshot_checksum_inconsistency_fails_closed(env, capsys):
    snapshot = env.tmp_path / "tampered-snapshot.json"
    raw = json.loads(Path(env.snapshot_file()).read_text())
    raw["frontend"]["checksum"] = "0" * 64
    snapshot.write_text(json.dumps(raw))
    code = main(env.snapshot_argv(str(snapshot)))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR AMBIGUOUS" in err
    assert "checksum does not match" in err


def test_verify_snapshot_rejects_staging_snapshot(env, capsys):
    snapshot = env.tmp_path / "staging-snapshot.json"
    raw = json.loads(Path(env.snapshot_file()).read_text())
    raw["environment"] = "staging"
    snapshot.write_text(json.dumps(raw))
    code = main(env.snapshot_argv(str(snapshot)))
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "expected 'production'" in err
