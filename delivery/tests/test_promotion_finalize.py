"""Offline gates for finalize (VR-PRO-03 / OP-FIN-01/02)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fakes_production import (
    CANDIDATE_SHA,
    DIGESTS,
    REPOSITORIES,
    RUN_ATTEMPT,
    RUN_ID,
    FakeCloudFront,
    FakeEcr,
    FakeGithub,
    FakeS3,
    FakeSts,
    main_candidate,
    make_frontend_archive,
    production_identifiers,
    write_identifiers,
    write_snapshot,
)

from delivery import live_marker
from delivery.cli import main
from delivery.live_marker import LiveMarker, marker_document


class FinalizeEnv:
    def __init__(self, monkeypatch, tmp_path, provisional="release-0001"):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.provisional = provisional
        self.ids = production_identifiers()
        self.identifiers_file = write_identifiers(tmp_path, self.ids)
        self.archive, self.artifact_digest, self.content_checksum = make_frontend_archive(tmp_path)
        self.candidate = main_candidate(
            tmp_path,
            artifact_digest=self.artifact_digest,
            content_checksum_value=self.content_checksum,
        )
        self.candidate_raw = json.loads(self.candidate.read_text())
        self.candidate_marker = LiveMarker(
            releaseId=None,
            candidateId=self.candidate_raw["candidateId"],
            sourceSha=CANDIDATE_SHA,
            frontendSha256=self.content_checksum,
        )
        self.candidate_marker_doc = marker_document(self.candidate_marker)
        self.snapshot = write_snapshot(
            tmp_path, self.ids, digests=DIGESTS, marker_doc=self.candidate_marker_doc
        )
        self.sts = FakeSts()
        self.ecr = FakeEcr(digests=DIGESTS)
        self.s3 = FakeS3()
        self.cf = FakeCloudFront()
        self.github = FakeGithub(releases=[])
        self._populate_prefix()
        self._install()

    def _populate_prefix(self):
        prefix = f"_releases/{self.provisional}/"
        self.s3.objects[f"{prefix}index.html"] = b'<html><body><div id="root"></div></body></html>'
        self.s3.objects[f"{prefix}assets/app.js"] = b"console.log('ok');\n"
        self.s3.objects[f"{prefix}frontend.tar.gz"] = self.archive.read_bytes()
        self.s3.objects[f"{prefix}release.json"] = self.candidate_marker_doc.encode()
        self.s3.objects[self.ids["frontendLiveMarker"]] = self.candidate_marker_doc.encode()

    def _install(self):
        clients = {
            "sts": self.sts,
            "ecr": self.ecr,
            "s3": self.s3,
            "cloudfront": self.cf,
        }
        self.monkeypatch.setattr(
            "delivery.aws.context.client_for", lambda ctx, service: clients[service]
        )
        self.monkeypatch.setattr(
            "delivery.commands.finalize.GitHubApi",
            lambda repository, token=None: self.github,
        )
        self.monkeypatch.setattr("delivery.commands.finalize._PUBLIC_MARKER_TIMEOUT", 1)

        def fake_fetch(url):
            if url.endswith("/release.json"):
                return (
                    200,
                    {"Content-Type": "application/json"},
                    self.s3.objects.get(self.ids["frontendLiveMarker"], b""),
                )
            return 404, {}, b""

        self.monkeypatch.setattr("delivery.commands.finalize._fetch", fake_fetch)

    def staging_record(self) -> Path:
        from fakes_production import complete_staging_record

        path = self.tmp_path / "staging-record.json"
        path.write_text(json.dumps(complete_staging_record(self.candidate_raw)))
        return path

    def staging_identity(self) -> str:
        record = json.loads(self.staging_record().read_text())
        return (
            f"staging-record-{record['candidate']['workflowRunId']}-"
            f"{record['candidate']['workflowRunAttempt']}"
        )

    def verification_report(self) -> Path:
        path = self.tmp_path / "verification-report.json"
        if path.exists():
            return path
        services = {
            key: {
                "service": service,
                "deploymentId": f"deploy-{service}-1",
                "taskDefinitionArn": (
                    "arn:aws:ecs:eu-north-1:799111666795:"
                    f"task-definition/{service}:1"
                ),
                "health": "COMPLETED",
                "expectedDigest": DIGESTS[key],
                "runningDigests": [DIGESTS[key]],
                "match": True,
            }
            for key, service in zip(
                ("auth", "items", "gateway"),
                ("onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"),
                strict=True,
            )
        }
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "reportId": "vrf-0000000000000001",
                    "producedAt": "2026-08-16T10:00:00Z",
                    "environment": "production",
                    "services": services,
                    "frontend": {},
                    "journeys": [],
                    "conclusion": "passed",
                }
            )
        )
        return path

    def approval(self) -> Path:
        path = self.tmp_path / "approval-evidence.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "approver": "owner-login",
                    "requester": "requester-login",
                    "workflowUrl": "https://github.com/x/y/actions/runs/4711",
                    "approvedAt": "2026-08-16T10:00:00Z",
                }
            )
        )
        return path

    def publish(self) -> Path:
        path = self.tmp_path / "frontend-publish.json"
        if path.exists():
            return path
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "candidateId": self.candidate_raw["candidateId"],
                    "provisionalReleaseId": self.provisional,
                    "prefixKey": f"_releases/{self.provisional}/",
                    "liveMarkerKey": self.ids["frontendLiveMarker"],
                    "contentChecksum": self.content_checksum,
                }
            )
        )
        return path

    def sbom_dir(self) -> Path:
        directory = self.tmp_path / "sboms"
        directory.mkdir(exist_ok=True)
        for name in ("auth", "items", "api-gateway", "frontend"):
            (directory / f"{name}.spdx.json").write_text(
                f'{{"spdxVersion": "SPDX-2.3", "component": "{name}"}}'
            )
        return directory

    def argv(self, *extra):
        return [
            "finalize",
            "--candidate",
            str(self.candidate),
            "--snapshot",
            str(self.snapshot),
            "--staging-record",
            str(self.staging_record()),
            "--staging-record-identity",
            self.staging_identity(),
            "--verification-report",
            str(self.verification_report()),
            "--approval",
            str(self.approval()),
            "--frontend-publish",
            str(self.publish()),
            "--frontend-archive",
            str(self.archive),
            "--sbom-dir",
            str(self.sbom_dir()),
            "--manifest",
            str(self.tmp_path / "release-manifest.json"),
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            "--out",
            str(self.tmp_path / "finalize-report.json"),
            *extra,
        ]

    def manifest(self) -> dict:
        return json.loads((self.tmp_path / "release-manifest.json").read_text())

    def report(self) -> dict:
        return json.loads((self.tmp_path / "finalize-report.json").read_text())


@pytest.fixture
def env(monkeypatch, tmp_path):
    return FinalizeEnv(monkeypatch, tmp_path)


def _previous_manifest() -> dict:
    return {
        "schemaVersion": "1.0",
        "releaseId": "release-0001",
        "candidateId": "cand-old",
        "source": {"fullSha": "1" * 40, "branch": "main"},
        "previousReleaseId": None,
        "promotedAt": "2026-08-15T10:00:00Z",
        "requester": "owner",
        "approval": {"evidence": "env", "workflowUrl": "https://github.com/x/y"},
        "artifacts": {
            "auth": {"repository": "onlineshop-auth", "digest": f"sha256:{'a' * 64}"},
            "items": {"repository": "onlineshop-items", "digest": f"sha256:{'b' * 64}"},
            "gateway": {"repository": "onlineshop-api-gateway", "digest": f"sha256:{'c' * 64}"},
            "frontend": {"immutableIdentity": "_releases/release-0001/", "checksum": "d" * 64},
            "sbom": {
                component: {"assetName": f"{component}.spdx.json", "sha256": "e" * 64}
                for component in ("auth", "items", "gateway", "frontend")
            },
        },
        "compatibilityFingerprint": "f" * 64,
        "staging": {"evidenceIdentity": "staging-record-1-1", "conclusion": "passed"},
        "productionVerification": {"evidenceIdentity": "vrf-1", "conclusion": "passed"},
        "rollbackCapableAtPublication": True,
    }


def _previous_marker_doc() -> str:
    return marker_document(
        live_marker.build_official_marker(
            live_marker.build_candidate_marker(
                candidate_id="cand-old",
                source_sha="1" * 40,
                frontend_sha256="d" * 64,
            ),
            "release-0001",
        )
    )


def _seed_previous_release(
    env, *, marker_doc: str | None = None, ecr_tags: bool = True
) -> dict:
    manifest = _previous_manifest()
    asset_url = "https://example.com/assets/release-0001-release-manifest.json"
    env.github.releases = [
        {
            "tag_name": "release-0001",
            "id": 1,
            "assets": [{"name": "release-manifest.json", "url": asset_url}],
        }
    ]
    env.github._assets["asset://release-0001-release-manifest.json"] = (
        json.dumps(manifest).encode()
    )
    if ecr_tags:
        for key in ("auth", "items", "gateway"):
            env.ecr.tags[(key, "release-0001")] = manifest["artifacts"][key]["digest"]
    env.s3.objects["_releases/release-0001/release.json"] = (
        _previous_marker_doc() if marker_doc is None else marker_doc
    ).encode()
    return manifest


def test_finalize_full_sequence(env, capsys):
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    manifest = env.manifest()
    assert manifest["releaseId"] == "release-0001"
    assert manifest["candidateId"] == env.candidate_raw["candidateId"]
    assert manifest["source"]["fullSha"] == CANDIDATE_SHA
    assert manifest["source"]["branch"] == "main"
    assert manifest["previousReleaseId"] is None
    assert manifest["requester"] == "requester-login"
    assert manifest["approval"]["workflowUrl"].startswith("https://")
    assert "owner-login" in manifest["approval"]["evidence"]
    for key in ("auth", "items", "gateway"):
        assert manifest["artifacts"][key]["digest"] == DIGESTS[key]
    assert manifest["artifacts"]["frontend"]["immutableIdentity"] == "_releases/release-0001/"
    assert manifest["artifacts"]["frontend"]["checksum"] == env.content_checksum
    # SBOM asset identity + SHA-256 per component
    for component in ("auth", "items", "gateway", "frontend"):
        filename = "api-gateway.spdx.json" if component == "gateway" else f"{component}.spdx.json"
        expected_hash = hashlib.sha256(
            (env.tmp_path / "sboms" / filename).read_bytes()
        ).hexdigest()
        assert manifest["artifacts"]["sbom"][component]["assetName"] == filename
        assert manifest["artifacts"]["sbom"][component]["sha256"] == expected_hash
    assert manifest["compatibilityFingerprint"] == "f" * 64
    assert manifest["staging"]["evidenceIdentity"] == (
        f"staging-record-{RUN_ID}-{RUN_ATTEMPT}"
    )
    assert manifest["productionVerification"]["evidenceIdentity"] == "vrf-0000000000000001"
    assert manifest["rollbackCapableAtPublication"] is True
    # ECR tags minted from the recorded manifest bytes with read-back
    assert len(env.ecr.put_calls) == 3
    for repository in REPOSITORIES.values():
        assert (repository, "release-0001") in {
            (repo, tag) for repo, tag in env.ecr.put_calls
        }
    # official marker live + prefix marker replaced; public verification passed
    live = env.s3.objects[env.ids["frontendLiveMarker"]].decode()
    assert '"releaseId":"release-0001"' in live
    prefix = env.s3.objects["_releases/release-0001/release.json"].decode()
    assert '"releaseId":"release-0001"' in prefix
    assert env.cf.invalidations
    # GitHub release published with manifest + 4 SBOM assets
    assert env.github.created_releases == ["release-0001"]
    assert sorted(env.github.uploaded_assets) == [
        "api-gateway.spdx.json",
        "auth.spdx.json",
        "frontend.spdx.json",
        "items.spdx.json",
        "release-manifest.json",
    ]
    report = env.report()
    assert report["releaseId"] == "release-0001"
    assert report["resumed"] is False
    assert report["rollbackCapableAtPublication"] is True
    assert [entry["releaseId"] for entry in report["window"]] == ["release-0001"]


def test_finalize_exact_resume_reuses_everything(env, capsys):
    assert main(env.argv()) == 0, capsys.readouterr().err
    first_manifest = env.manifest()
    created = list(env.github.created_releases)
    uploaded = list(env.github.uploaded_assets)
    put_tags = list(env.ecr.put_calls)
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    assert env.manifest() == first_manifest
    assert env.github.created_releases == created
    assert env.github.uploaded_assets == uploaded
    assert env.ecr.put_calls == put_tags
    report = env.report()
    assert report["resumed"] is True
    assert any(step["action"] == "resumed" for step in report["steps"])


def test_finalize_resume_mismatch_fails_closed(env, capsys):
    assert main(env.argv()) == 0
    manifest = env.manifest()
    manifest["requester"] = "tampered"
    (env.tmp_path / "release-manifest.json").write_text(json.dumps(manifest))
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "exact-match resume refused" in err
    assert "requester" in err
    assert env.github.created_releases == ["release-0001"]


def test_finalize_duplicate_release_id_fails_closed(env, capsys):
    # a release was published after deploy frontend: the provisional id no
    # longer matches the next allocated id (AD-07: never reused)
    env.provisional = "release-0001"
    env.github.releases = [
        {"tag_name": "release-0001", "id": 1, "assets": []}
    ]
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "does not match the provisional id" in err
    assert env.github.created_releases == []
    assert env.ecr.put_calls == []


def test_finalize_existing_ecr_tag_mismatch_fails_closed(env, capsys):
    env.ecr.tags = {("auth", "release-0001"): f"sha256:{'9' * 64}"}
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "already resolves to" in err
    assert env.github.created_releases == []


def test_finalize_incomplete_previous_window_fails_after_publication(env, capsys):
    env.provisional = "release-0002"
    env._populate_prefix()
    env.s3.objects[env.ids["frontendLiveMarker"]] = env.candidate_marker_doc.encode()
    # the previous release's ECR tags are missing -> incomplete window
    _seed_previous_release(env, ecr_tags=False)
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "incomplete at publication time" in err
    # the release itself WAS published (honest failure, evidence preserved)
    assert env.github.created_releases == ["release-0002"]
    report = env.report()
    assert report["rollbackCapableAtPublication"] is False
    assert report["window"][1]["releaseId"] == "release-0001"
    assert report["window"][1]["complete"] is False


def test_finalize_previous_release_window_complete(env, capsys):
    env.provisional = "release-0002"
    env._populate_prefix()
    env.s3.objects[env.ids["frontendLiveMarker"]] = env.candidate_marker_doc.encode()
    # previous ECR tags resolve + the prefix marker is identity-equivalent
    # to the manifest-derived official marker -> strong audit passes
    _seed_previous_release(env)
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    report = env.report()
    assert report["rollbackCapableAtPublication"] is True
    assert [entry["releaseId"] for entry in report["window"]] == [
        "release-0002",
        "release-0001",
    ]


def test_finalize_previous_release_with_wrong_prefix_marker_fails_at_publication(
    env, capsys
):
    env.provisional = "release-0002"
    env._populate_prefix()
    env.s3.objects[env.ids["frontendLiveMarker"]] = env.candidate_marker_doc.encode()
    # a wrong-content marker previously passed the existence-only check; the
    # strong shared audit now fails closed at publication time
    wrong = marker_document(
        live_marker.build_official_marker(
            live_marker.build_candidate_marker(
                candidate_id="cand-tampered",
                source_sha="9" * 40,
                frontend_sha256="e" * 64,
            ),
            "release-0001",
        )
    )
    _seed_previous_release(env, marker_doc=wrong)
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "incomplete at publication time" in err
    # the release itself WAS published (honest failure, evidence preserved)
    assert env.github.created_releases == ["release-0002"]
    report = env.report()
    assert report["rollbackCapableAtPublication"] is False
    assert report["window"][1]["complete"] is False
    assert "PREFIX_MARKER_MISMATCH" in report["window"][1]["detail"]


def test_finalize_dry_run_mutates_nothing(env, capsys):
    code = main(env.argv("--dry-run"))
    assert code == 0, capsys.readouterr().err
    assert env.ecr.put_calls == []
    assert env.github.created_releases == []
    assert env.github.uploaded_assets == []
    assert not (env.tmp_path / "release-manifest.json").exists()
    # live marker still names the candidate
    live = env.s3.objects[env.ids["frontendLiveMarker"]].decode()
    assert '"releaseId":null' in live


def test_finalize_rejects_failed_verification(env, capsys):
    report = env.verification_report()
    data = json.loads(report.read_text())
    data["conclusion"] = "failed"
    report.write_text(json.dumps(data))
    code = main(env.argv())
    assert code == 1
    assert "verification conclusion is 'failed'" in capsys.readouterr().err


def test_finalize_rejects_unsafe_staging_identity(env, capsys):
    code = main(
        env.argv("--staging-record-identity", "staging-record-$(touch pwned)")
    )
    assert code == 1
    assert "unsafe staging record identity" in capsys.readouterr().err


def test_finalize_rejects_publish_record_for_other_candidate(env, capsys):
    record = env.publish()
    data = json.loads(record.read_text())
    data["candidateId"] = "cand-other"
    record.write_text(json.dumps(data))
    code = main(env.argv())
    assert code == 1
    assert "not the promoted candidate" in capsys.readouterr().err


def test_finalize_rejects_verification_report_with_foreign_digests(env, capsys):
    report = env.verification_report()
    data = json.loads(report.read_text())
    data["services"]["auth"]["expectedDigest"] = f"sha256:{'9' * 64}"
    report.write_text(json.dumps(data))
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "services.auth.expectedDigest" in err
    assert "does not match the promoted candidate digest" in err
    assert env.github.created_releases == []


def test_finalize_rejects_verification_report_without_service_observation(env, capsys):
    report = env.verification_report()
    data = json.loads(report.read_text())
    del data["services"]["items"]
    report.write_text(json.dumps(data))
    code = main(env.argv())
    assert code == 1
    assert "no services.items observation" in capsys.readouterr().err


def test_finalize_verification_report_with_matching_digests_passes(env, capsys):
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    assert env.manifest()["productionVerification"]["conclusion"] == "passed"


def test_finalize_rejects_staging_identity_for_foreign_run(env, capsys):
    code = main(env.argv("--staging-record-identity", "staging-record-9999-9"))
    assert code == 1
    err = capsys.readouterr().err
    assert "does not match the record's embedded candidate run/attempt" in err
    assert env.github.created_releases == []
    assert env.ecr.put_calls == []


def test_finalize_manifest_write_failure_never_publishes(env, capsys):
    from delivery.commands import finalize as finalize_module
    from delivery.errors import ReadError

    real_write = finalize_module.write_json

    def failing_write(path, record):
        raise ReadError("cannot write release-manifest.json: simulated disk failure")

    env.monkeypatch.setattr("delivery.commands.finalize.write_json", failing_write)
    code = main(env.argv())
    assert code == 1
    err = capsys.readouterr().err
    assert "simulated disk failure" in err
    # nothing was published: no GitHub Release, no official marker switch
    assert env.github.created_releases == []
    assert env.github.uploaded_assets == []
    assert env.cf.invalidations == []
    live = env.s3.objects[env.ids["frontendLiveMarker"]].decode()
    assert '"releaseId":null' in live
    # a retry after the write recovers publishes the release normally
    env.monkeypatch.setattr("delivery.commands.finalize.write_json", real_write)
    code = main(env.argv())
    assert code == 0, capsys.readouterr().err
    assert env.github.created_releases == ["release-0001"]


def test_finalize_invalid_produced_report_fails_before_write(env, capsys):
    from delivery.commands import finalize as finalize_module
    from delivery.models import FinalizationReport

    captured = {}

    def rejecting_validate(record):
        if isinstance(record, FinalizationReport):
            captured["record"] = record
            return ["crafted: steps must be non-empty"]
        return []

    env.monkeypatch.setattr(finalize_module, "validate_record", rejecting_validate)
    code = main(env.argv())
    assert code == 1
    assert isinstance(captured["record"], FinalizationReport)
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "produced finalization report is invalid" in err
    assert not (env.tmp_path / "finalize-report.json").exists()
