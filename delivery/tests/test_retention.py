"""Offline gates for retention audit/preview/apply (OP-RET-01/02/03)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import client_error
from fakes_production import (
    DIGESTS,
    REPOSITORIES,
    FakeEcr,
    FakeGithub,
    FakeS3,
    FakeSts,
    production_identifiers,
    write_identifiers,
    write_snapshot,
)

from delivery import live_marker
from delivery import retention as engine
from delivery.cli import main
from delivery.errors import PolicyTagPrefixMulti

REPOSITORY = "Djimi@8793507/OnlineShop-full-stack"
FINGERPRINT = "f" * 64
OTHER_FINGERPRINT = "a" * 64


# ---------------------------------------------------------------------------
# engine: policy asset, validator, and first-match-wins model
# ---------------------------------------------------------------------------


def _image(digest: str, tags: list[str], pushed_days_ago: float) -> dict:
    return {
        "digest": digest,
        "tags": tags,
        "pushedAt": (datetime.now(UTC) - timedelta(days=pushed_days_ago)).isoformat(),
    }


def _ecr_image(digest: str, tags: list[str], pushed_days_ago: float) -> dict:
    return {
        "imageDigest": digest,
        "imageTags": tags,
        "imagePushedAt": (datetime.now(UTC) - timedelta(days=pushed_days_ago)).isoformat(),
    }


def _to_ecr(images: list[dict]) -> list[dict]:
    return [
        {
            "imageDigest": image["digest"],
            "imageTags": image["tags"],
            "imagePushedAt": image["pushedAt"],
        }
        for image in images
    ]


def test_policy_asset_matches_desired_properties():
    text, policy, kind = engine.load_policy_text(None)
    assert kind == "desired"
    assert json.loads(text) == policy
    rules = policy["rules"]
    assert [rule["rulePriority"] for rule in rules] == [1, 2, 3, 4, 5]
    keep = rules[0]
    assert keep["selection"] == {
        "tagStatus": "tagged",
        "tagPrefixList": ["release-"],
        "countType": "imageCountMoreThan",
        "countNumber": 10,
    }
    for rule, prefix in zip(rules[1:4], ("sha-", "main-latest", "branch-"), strict=True):
        assert rule["selection"]["tagPrefixList"] == [prefix]
        assert rule["selection"]["countType"] == "sinceImagePushed"
        assert rule["selection"]["countUnit"] == "days"
        assert rule["selection"]["countNumber"] == 30
    assert rules[4]["selection"]["tagStatus"] == "untagged"
    assert rules[4]["selection"]["countNumber"] == 14
    for rule in rules:
        assert rule["action"] == {"type": "expire"}


def test_validator_rejects_merged_tag_prefix_list():
    policy = {
        "rules": [
            {
                "rulePriority": 1,
                "description": "merged candidate rule",
                "selection": {
                    "tagStatus": "tagged",
                    "tagPrefixList": ["main-latest", "branch-"],
                    "countType": "sinceImagePushed",
                    "countUnit": "days",
                    "countNumber": 30,
                },
                "action": {"type": "expire"},
            }
        ]
    }
    with pytest.raises(PolicyTagPrefixMulti):
        engine.validate_policy(policy)


def test_validator_rejects_tagged_rule_without_prefix_list():
    policy = {
        "rules": [
            {
                "rulePriority": 1,
                "description": "no prefix",
                "selection": {
                    "tagStatus": "tagged",
                    "countType": "imageCountMoreThan",
                    "countNumber": 5,
                },
                "action": {"type": "expire"},
            }
        ]
    }
    with pytest.raises(Exception) as error:
        engine.validate_policy(policy)
    assert error.value.code == "POLICY_INVALID"


def test_validator_rejects_nonconsecutive_priorities():
    _, policy, _ = engine.load_policy_text(None)
    policy["rules"][1]["rulePriority"] = 3
    with pytest.raises(Exception) as error:
        engine.validate_policy(policy)
    assert error.value.code == "POLICY_INVALID"


def test_model_first_match_wins_keeps_multi_tag_release_image():
    _, policy, _ = engine.load_policy_text(None)
    images = [
        _image("sha256:" + "1" * 64, ["sha-abc", "release-0001"], 90),
        _image("sha256:" + "2" * 64, ["sha-def"], 1),
    ]
    decisions = engine.model_expirations(policy, images)
    release = next(d for d in decisions if d["digest"] == "sha256:" + "1" * 64)
    assert release["expire"] is False
    assert release["rulePriority"] == 1


def test_model_release_image_beyond_newest_10_expires_by_rule_1():
    _, policy, _ = engine.load_policy_text(None)
    images = [
        _image(f"sha256:{i:064x}", [f"release-{i:04d}"], 60 - i) for i in range(1, 12)
    ]
    decisions = engine.model_expirations(policy, images)
    assert decisions[0]["expire"] is True
    assert decisions[0]["rulePriority"] == 1
    for decision in decisions[1:]:
        assert decision["expire"] is False


def test_model_candidate_and_untagged_age_thresholds():
    _, policy, _ = engine.load_policy_text(None)
    images = [
        _image("sha256:" + "1" * 64, ["sha-old"], 40),
        _image("sha256:" + "2" * 64, ["main-latest"], 5),
        _image("sha256:" + "3" * 64, ["branch-dev"], 31),
        _image("sha256:" + "4" * 64, [], 20),
        _image("sha256:" + "5" * 64, [], 2),
        _image("sha256:" + "6" * 64, ["v1"], 100),
    ]
    decisions = {d["digest"]: d for d in engine.model_expirations(policy, images)}
    assert decisions["sha256:" + "1" * 64]["expire"] is True
    assert decisions["sha256:" + "1" * 64]["rulePriority"] == 2
    assert decisions["sha256:" + "2" * 64]["expire"] is False
    assert decisions["sha256:" + "3" * 64]["expire"] is True
    assert decisions["sha256:" + "3" * 64]["rulePriority"] == 4
    assert decisions["sha256:" + "4" * 64]["expire"] is True
    assert decisions["sha256:" + "4" * 64]["rulePriority"] == 5
    assert decisions["sha256:" + "5" * 64]["expire"] is False
    assert decisions["sha256:" + "6" * 64]["rulePriority"] is None
    assert decisions["sha256:" + "6" * 64]["expire"] is False


def test_protected_release_tags_window_plus_newest_10_margin():
    images = [
        _image(f"sha256:{i:064x}", [f"release-{i:04d}"], 30 - i) for i in range(1, 13)
    ]
    protected = engine.protected_release_tags(["release-0011"], images)
    assert "release-0011" in protected
    assert "release-0012" in protected
    assert "release-0003" in protected
    assert "release-0002" not in protected
    assert "release-0001" not in protected


# ---------------------------------------------------------------------------
# command tests
# ---------------------------------------------------------------------------


class ErrorEcr(FakeEcr):
    def batch_get_image(self, repositoryName, imageIds):
        raise client_error("InternalError")


class ErrorS3(FakeS3):
    def __init__(self, error):
        super().__init__()
        self.error = error


class FailItemsPutEcr(FakeEcr):
    def put_lifecycle_policy(self, repositoryName, lifecyclePolicyText):
        if repositoryName == REPOSITORIES["items"]:
            raise client_error("InternalError")
        return super().put_lifecycle_policy(repositoryName, lifecyclePolicyText)


class RetentionEnv:
    def __init__(self, monkeypatch, tmp_path, current="release-0005"):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.ids = production_identifiers()
        self.identifiers_file = write_identifiers(tmp_path, self.ids)
        self.sts = FakeSts()
        self.ecr = FakeEcr(digests=DIGESTS)
        self.s3 = FakeS3()
        self.github = FakeGithub()
        self.snapshot = write_snapshot(tmp_path, self.ids, release_id=current)
        self.current = current
        clients = {"sts": self.sts, "ecr": self.ecr, "s3": self.s3}
        monkeypatch.setattr(
            "delivery.aws.context.client_for", lambda ctx, service: clients[service]
        )
        monkeypatch.setattr(
            "delivery.commands.retention.GitHubApi", lambda repository, token=None: self.github
        )

    def argv(self, command: str, *extra: str) -> list[str]:
        return [
            "retention",
            command,
            "--snapshot",
            str(self.snapshot),
            "--repository",
            REPOSITORY,
            "--environment",
            "production",
            "--identifiers",
            str(self.identifiers_file),
            *extra,
        ]

    def official_manifest(self, release_id: str, fingerprint: str = FINGERPRINT) -> dict:
        return {
            "schemaVersion": "1.0",
            "releaseId": release_id,
            "candidateId": f"cand-{release_id}",
            "source": {"fullSha": "1" * 40, "branch": "main"},
            "previousReleaseId": None,
            "promotedAt": "2026-08-15T10:00:00Z",
            "requester": "owner",
            "approval": {"evidence": "env", "workflowUrl": "https://github.com/x/y"},
            "artifacts": {
                key: {"repository": REPOSITORIES[key], "digest": DIGESTS[key]}
                for key in ("auth", "items", "gateway")
            }
            | {
                "frontend": {
                    "immutableIdentity": f"_releases/{release_id}/",
                    "checksum": "d" * 64,
                },
                "sbom": {
                    component: {"assetName": f"{component}.spdx.json", "sha256": "e" * 64}
                    for component in ("auth", "items", "gateway", "frontend")
                },
            },
            "compatibilityFingerprint": fingerprint,
            "staging": {"evidenceIdentity": "staging-record-1-1", "conclusion": "passed"},
            "productionVerification": {"evidenceIdentity": "vrf-1", "conclusion": "passed"},
            "rollbackCapableAtPublication": True,
        }

    def seed_release(
        self,
        release_id: str,
        *,
        fingerprint: str = FINGERPRINT,
        ecr_tags: bool = True,
        prefix: bool = True,
        github: bool = True,
    ) -> None:
        if github:
            self.github.releases.append(
                {
                    "tag_name": release_id,
                    "id": len(self.github.releases) + 1,
                    "assets": [
                        {
                            "name": "release-manifest.json",
                            "url": f"https://example.com/assets/{release_id}-release-manifest.json",
                        }
                    ],
                }
            )
        manifest = self.official_manifest(release_id, fingerprint)
        self.github._assets[f"asset://{release_id}-release-manifest.json"] = json.dumps(
            manifest
        ).encode()
        if ecr_tags:
            for key in ("auth", "items", "gateway"):
                self.ecr.tags[(key, release_id)] = manifest["artifacts"][key]["digest"]
        if prefix:
            marker = live_marker.build_official_marker(
                live_marker.build_candidate_marker(
                    candidate_id=f"cand-{release_id}",
                    source_sha="1" * 40,
                    frontend_sha256="d" * 64,
                ),
                release_id,
            )
            self.s3.objects[f"_releases/{release_id}/release.json"] = (
                live_marker.marker_document(marker).encode()
            )

    def seed_window(self) -> None:
        for release_id in (
            "release-0005",
            "release-0004",
            "release-0003",
            "release-0002",
            "release-0001",
        ):
            self.seed_release(release_id)


def _report(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# audit ---------------------------------------------------------------


def test_audit_complete_window_exits_zero(env, capsys):
    env.seed_window()
    assert main(env.argv("audit")) == 0
    report = _report(capsys)
    assert report["windowComplete"] is True
    assert report["currentReleaseId"] == "release-0005"
    assert [entry["releaseId"] for entry in report["releases"]] == [
        "release-0005",
        "release-0004",
        "release-0003",
        "release-0002",
        "release-0001",
    ]
    assert all(entry["complete"] for entry in report["releases"] if entry["inWindow"])
    assert not report["releases"][-1]["inWindow"]


def test_audit_incomplete_historical_release_is_not_an_error(env, capsys):
    for release_id in ("release-0005", "release-0004", "release-0003", "release-0002"):
        env.seed_release(release_id)
    env.seed_release("release-0001", ecr_tags=False, prefix=False)
    assert main(env.argv("audit")) == 0
    assert _report(capsys)["windowComplete"] is True


def test_audit_missing_ecr_tag_fails_closed(env, capsys):
    for release_id in ("release-0005", "release-0004", "release-0002", "release-0001"):
        env.seed_release(release_id)
    env.seed_release("release-0003", ecr_tags=False)
    assert main(env.argv("audit")) == 1
    output = capsys.readouterr()
    assert "ERROR WINDOW_INCOMPLETE" in output.err
    report = json.loads(output.out)
    entry = next(e for e in report["releases"] if e["releaseId"] == "release-0003")
    assert entry["complete"] is False
    # a missing tag is now a ReadError: bounded retries exhausted (absence
    # after a push is not provable), not a definitive ECR_TAG_NOT_FOUND
    assert [failure["kind"] for failure in entry["failures"]] == ["READ_ERROR"] * 3


def test_audit_digest_mismatch_fails_closed(env, capsys):
    env.seed_window()
    env.ecr.tags[("auth", "release-0004")] = "sha256:" + "9" * 64
    assert main(env.argv("audit")) == 1
    output = capsys.readouterr()
    assert "ERROR WINDOW_INCOMPLETE" in output.err
    report = json.loads(output.out)
    entry = next(e for e in report["releases"] if e["releaseId"] == "release-0004")
    kinds = {failure["kind"] for failure in entry["failures"]}
    assert "ECR_DIGEST_MISMATCH" in kinds


def test_audit_missing_frontend_prefix_fails_closed(env, capsys):
    for release_id in ("release-0005", "release-0004", "release-0003", "release-0001"):
        env.seed_release(release_id)
    env.seed_release("release-0002", prefix=False)
    assert main(env.argv("audit")) == 1
    output = capsys.readouterr()
    assert "ERROR WINDOW_INCOMPLETE" in output.err
    report = json.loads(output.out)
    entry = next(e for e in report["releases"] if e["releaseId"] == "release-0002")
    kinds = {failure["kind"] for failure in entry["failures"]}
    assert kinds == {"PREFIX_MARKER_NOT_FOUND"}


def test_audit_prefix_marker_identity_equivalent_content_passes(env, capsys):
    env.seed_window()
    marker = live_marker.build_official_marker(
        live_marker.build_candidate_marker(
            candidate_id="cand-release-0004",
            source_sha="1" * 40,
            frontend_sha256="d" * 64,
        ),
        "release-0004",
    )
    env.s3.objects["_releases/release-0004/release.json"] = (
        live_marker.marker_document(marker).encode()
    )
    assert main(env.argv("audit")) == 0
    report = _report(capsys)
    entry = next(e for e in report["releases"] if e["releaseId"] == "release-0004")
    assert entry["complete"] is True


def test_audit_prefix_marker_wrong_content_fails_closed(env, capsys):
    env.seed_window()
    wrong = live_marker.build_official_marker(
        live_marker.build_candidate_marker(
            candidate_id="cand-tampered",
            source_sha="9" * 40,
            frontend_sha256="e" * 64,
        ),
        "release-0003",
    )
    env.s3.objects["_releases/release-0003/release.json"] = (
        live_marker.marker_document(wrong).encode()
    )
    assert main(env.argv("audit")) == 1
    output = capsys.readouterr()
    assert "ERROR WINDOW_INCOMPLETE" in output.err
    report = json.loads(output.out)
    entry = next(e for e in report["releases"] if e["releaseId"] == "release-0003")
    kinds = {failure["kind"] for failure in entry["failures"]}
    assert kinds == {"PREFIX_MARKER_MISMATCH"}


def test_audit_prefix_marker_read_error_is_distinct(env, capsys, monkeypatch):
    env.seed_window()
    error_s3 = ErrorS3(client_error("InternalError"))
    monkeypatch.setattr(
        "delivery.aws.context.client_for",
        lambda ctx, service: {"sts": env.sts, "ecr": env.ecr, "s3": error_s3}[service],
    )
    assert main(env.argv("audit")) == 1
    output = capsys.readouterr()
    assert "ERROR WINDOW_INCOMPLETE" in output.err
    report = json.loads(output.out)
    entry = next(e for e in report["releases"] if e["releaseId"] == "release-0005")
    kinds = {failure["kind"] for failure in entry["failures"]}
    assert kinds == {"READ_ERROR"}


def test_audit_fingerprint_mismatch_fails_closed(env, capsys):
    for release_id in ("release-0005", "release-0003", "release-0002", "release-0001"):
        env.seed_release(release_id)
    env.seed_release("release-0004", fingerprint=OTHER_FINGERPRINT)
    assert main(env.argv("audit")) == 1
    output = capsys.readouterr()
    assert "ERROR WINDOW_INCOMPLETE" in output.err
    report = json.loads(output.out)
    entry = next(e for e in report["releases"] if e["releaseId"] == "release-0004")
    kinds = {failure["kind"] for failure in entry["failures"]}
    assert kinds == {"FINGERPRINT_MISMATCH"}
    assert entry["releaseFingerprint"] == OTHER_FINGERPRINT


def test_audit_read_error_is_distinct_from_absence(env, capsys, monkeypatch):
    env.seed_window()
    error_ecr = ErrorEcr(digests=DIGESTS)
    error_ecr.tags = dict(env.ecr.tags)
    monkeypatch.setattr(
        "delivery.aws.context.client_for",
        lambda ctx, service: {"sts": env.sts, "ecr": error_ecr, "s3": env.s3}[service],
    )
    assert main(env.argv("audit")) == 1
    output = capsys.readouterr()
    assert "ERROR WINDOW_INCOMPLETE" in output.err
    report = json.loads(output.out)
    entry = next(e for e in report["releases"] if e["releaseId"] == "release-0005")
    kinds = {failure["kind"] for failure in entry["failures"]}
    assert kinds == {"READ_ERROR"}
    assert "NOT_FOUND" not in kinds


def test_audit_missing_github_release_fails_closed(env, capsys):
    for release_id in ("release-0004", "release-0003", "release-0002", "release-0001"):
        env.seed_release(release_id)
    env.seed_release("release-0005", github=False)
    assert main(env.argv("audit")) == 1
    output = capsys.readouterr()
    assert "ERROR WINDOW_INCOMPLETE" in output.err
    report = json.loads(output.out)
    entry = next(e for e in report["releases"] if e["releaseId"] == "release-0005")
    kinds = {failure["kind"] for failure in entry["failures"]}
    assert kinds == {"GITHUB_RELEASE_NOT_FOUND"}


def test_audit_requires_official_current_release(env, capsys):
    env.seed_window()
    env.snapshot = write_snapshot(env.tmp_path, env.ids)
    assert main(env.argv("audit")) == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err


def test_audit_human_view(env, capsys):
    env.seed_window()
    assert main(env.argv("audit", "--human")) == 0
    output = capsys.readouterr().out
    assert "rollback window: complete" in output


# preview --------------------------------------------------------------


def _desired_text() -> str:
    return engine.load_policy_text(None)[0]


def test_preview_modeled_when_no_policy_applied(env, capsys):
    env.seed_window()
    images = [
        _image("sha256:" + "1" * 64, ["release-0005"], 1),
        _image("sha256:" + "2" * 64, ["sha-old"], 40),
        _image("sha256:" + "3" * 64, [], 20),
        _image("sha256:" + "4" * 64, [], 2),
    ]
    env.ecr.image_details["onlineshop-auth"] = _to_ecr(images)
    assert main(env.argv("preview")) == 0
    report = _report(capsys)
    assert report["windowComplete"] is True
    repo = next(r for r in report["repositories"] if r["repository"] == "onlineshop-auth")
    assert repo["kind"] == "modeled"
    assert repo["expiringDigests"] == ["sha256:" + "2" * 64, "sha256:" + "3" * 64]
    assert repo["protectedExpiring"] == []
    assert "release-0005" in repo["protectedTags"]
    assert env.ecr.preview_started == []


def test_preview_protected_image_expiring_fails_closed(env, capsys):
    env.seed_window()
    policy_file = env.tmp_path / "aggressive-policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "rulePriority": 1,
                        "description": "expire release images after one day",
                        "selection": {
                            "tagStatus": "tagged",
                            "tagPrefixList": ["release-"],
                            "countType": "sinceImagePushed",
                            "countUnit": "days",
                            "countNumber": 1,
                        },
                        "action": {"type": "expire"},
                    }
                ]
            }
        )
    )
    env.ecr.image_details["onlineshop-auth"] = [
        _ecr_image("sha256:" + "1" * 64, ["release-0005"], 5),
    ]
    assert main(env.argv("preview", "--policy", str(policy_file))) == 1
    output = capsys.readouterr()
    assert "ERROR PROTECTED_IMAGE_EXPIRING" in output.err
    report = json.loads(output.out)
    repo = next(r for r in report["repositories"] if r["repository"] == "onlineshop-auth")
    assert repo["protectedExpiring"]


def test_preview_live_agreement(env, capsys, monkeypatch):
    env.seed_window()
    monkeypatch.setattr("delivery.aws.waiters.time.sleep", lambda _: None)
    text, policy, _ = engine.load_policy_text(None)
    for repository in REPOSITORIES.values():
        env.ecr.lifecycle_policies[repository] = text
    images = [
        _image("sha256:" + "1" * 64, ["release-0005"], 1),
        _image("sha256:" + "2" * 64, ["sha-old"], 40),
    ]
    env.ecr.image_details["onlineshop-auth"] = _to_ecr(images)
    expected = {d["digest"] for d in engine.model_expirations(policy, images) if d["expire"]}
    env.ecr.preview_results["onlineshop-auth"] = [
        {
            "imageDigest": digest,
            "imageTags": ["sha-old"],
            "imagePushedAt": (datetime.now(UTC) - timedelta(days=40)).isoformat(),
            "action": {"type": "EXPIRE"},
            "appliedRulePriority": 2,
        }
        for digest in expected
    ]
    env.ecr.preview_statuses = ["IN_PROGRESS", "COMPLETE"]
    assert main(env.argv("preview")) == 0
    report = _report(capsys)
    repo = next(r for r in report["repositories"] if r["repository"] == "onlineshop-auth")
    assert repo["kind"] == "live"
    assert repo["agreement"] == "agree"
    assert set(repo["expiringDigests"]) == expected


def test_preview_disagreement_fails_closed(env, capsys):
    env.seed_window()
    text, _, _ = engine.load_policy_text(None)
    for repository in REPOSITORIES.values():
        env.ecr.lifecycle_policies[repository] = text
    images = [_image("sha256:" + "1" * 64, ["release-0005"], 1)]
    env.ecr.image_details["onlineshop-auth"] = _to_ecr(images)
    env.ecr.preview_results["onlineshop-auth"] = [
        {
            "imageDigest": "sha256:" + "9" * 64,
            "imageTags": ["sha-ghost"],
            "imagePushedAt": (datetime.now(UTC) - timedelta(days=40)).isoformat(),
            "action": {"type": "EXPIRE"},
            "appliedRulePriority": 2,
        }
    ]
    assert main(env.argv("preview")) == 1
    output = capsys.readouterr()
    assert "ERROR PREVIEW_DISAGREEMENT" in output.err
    repo = next(
        r for r in json.loads(output.out)["repositories"] if r["repository"] == "onlineshop-auth"
    )
    assert repo["agreement"] == "disagree"


def test_preview_modeled_when_applied_policy_differs(env, capsys):
    env.seed_window()
    for repository in REPOSITORIES.values():
        env.ecr.lifecycle_policies[repository] = "a different applied policy"
    env.ecr.image_details["onlineshop-auth"] = [_ecr_image("sha256:" + "1" * 64, ["sha-old"], 40)]
    assert main(env.argv("preview")) == 0
    report = _report(capsys)
    repo = next(r for r in report["repositories"] if r["repository"] == "onlineshop-auth")
    assert repo["kind"] == "modeled"
    assert "differs" in repo["reason"]
    assert env.ecr.preview_started == []


def test_preview_requires_complete_window(env, capsys):
    for release_id in ("release-0005", "release-0004", "release-0003", "release-0001"):
        env.seed_release(release_id)
    env.seed_release("release-0002", ecr_tags=False)
    assert main(env.argv("preview")) == 1
    assert "ERROR WINDOW_INCOMPLETE" in capsys.readouterr().err


# apply ----------------------------------------------------------------


def test_apply_refused_without_live_env_var(env, capsys, monkeypatch):
    env.seed_window()
    monkeypatch.delenv("DELIVERY_RETENTION_LIVE_APPLY", raising=False)
    assert main(env.argv("apply", "--apply")) == 1
    assert "ERROR LIVE_APPLY_REFUSED" in capsys.readouterr().err
    assert env.ecr.lifecycle_put_calls == []


def test_apply_dry_run_runs_preview_only(env, capsys, monkeypatch):
    env.seed_window()
    monkeypatch.delenv("DELIVERY_RETENTION_LIVE_APPLY", raising=False)
    env.ecr.image_details["onlineshop-auth"] = [_ecr_image("sha256:" + "1" * 64, ["sha-old"], 40)]
    assert main(env.argv("apply", "--dry-run")) == 0
    report = _report(capsys)
    assert report["repositories"][0]["kind"] == "modeled"
    assert env.ecr.lifecycle_put_calls == []


def test_apply_puts_policy_with_read_back(env, capsys, monkeypatch):
    env.seed_window()
    monkeypatch.setenv("DELIVERY_RETENTION_LIVE_APPLY", "1")
    assert main(env.argv("apply", "--apply")) == 0
    report = _report(capsys)
    assert sorted(report_repo["repository"] for report_repo in report["repositories"]) == sorted(
        REPOSITORIES.values()
    )
    assert all(report_repo["action"] == "put" for report_repo in report["repositories"])
    assert all(report_repo["readBackVerified"] for report_repo in report["repositories"])
    assert report["preAuditWindowComplete"] is True
    assert report["postAuditWindowComplete"] is True
    assert sorted(env.ecr.lifecycle_put_calls) == sorted(REPOSITORIES.values())
    for repository in REPOSITORIES.values():
        assert env.ecr.lifecycle_policies[repository] == _desired_text()


def test_apply_read_back_mismatch_fails_closed(env, capsys, monkeypatch):
    env.seed_window()
    monkeypatch.setenv("DELIVERY_RETENTION_LIVE_APPLY", "1")
    env.ecr.lifecycle_drift = "drifted policy text"
    assert main(env.argv("apply", "--apply")) == 1
    assert "ERROR MUTATION_VERIFY" in capsys.readouterr().err


def test_apply_skips_identical_policy(env, capsys, monkeypatch):
    env.seed_window()
    monkeypatch.setenv("DELIVERY_RETENTION_LIVE_APPLY", "1")
    for repository in REPOSITORIES.values():
        env.ecr.lifecycle_policies[repository] = _desired_text()
    assert main(env.argv("apply", "--apply")) == 0
    report = _report(capsys)
    assert all(report_repo["action"] == "unchanged" for report_repo in report["repositories"])
    assert env.ecr.lifecycle_put_calls == []


def test_apply_requires_complete_window(env, capsys, monkeypatch):
    for release_id in ("release-0005", "release-0004", "release-0002", "release-0001"):
        env.seed_release(release_id)
    env.seed_release("release-0003", ecr_tags=False)
    monkeypatch.setenv("DELIVERY_RETENTION_LIVE_APPLY", "1")
    assert main(env.argv("apply", "--apply")) == 1
    assert "ERROR WINDOW_INCOMPLETE" in capsys.readouterr().err
    assert env.ecr.lifecycle_put_calls == []


def test_apply_rejects_reference_date(env, capsys, monkeypatch):
    env.seed_window()
    monkeypatch.setenv("DELIVERY_RETENTION_LIVE_APPLY", "1")
    assert (
        main(env.argv("apply", "--apply", "--reference-date", "2026-08-01T00:00:00Z"))
        == 1
    )
    output = capsys.readouterr()
    assert "ERROR VALIDATION" in output.err
    assert "--reference-date" in output.err
    assert env.ecr.lifecycle_put_calls == []


def test_apply_dry_run_reference_date_reaches_preview(env, capsys, monkeypatch):
    env.seed_window()
    monkeypatch.delenv("DELIVERY_RETENTION_LIVE_APPLY", raising=False)
    assert (
        main(env.argv("apply", "--dry-run", "--reference-date", "2026-08-01T00:00:00Z"))
        == 0
    )
    report = _report(capsys)
    assert report["referenceDate"] == "2026-08-01T00:00:00Z"
    assert env.ecr.lifecycle_put_calls == []


def test_apply_mid_loop_failure_writes_partial_report(env, capsys, monkeypatch):
    env.seed_window()
    monkeypatch.setenv("DELIVERY_RETENTION_LIVE_APPLY", "1")
    failing_ecr = FailItemsPutEcr(digests=DIGESTS)
    failing_ecr.tags = dict(env.ecr.tags)
    monkeypatch.setattr(
        "delivery.aws.context.client_for",
        lambda ctx, service: {"sts": env.sts, "ecr": failing_ecr, "s3": env.s3}[service],
    )
    out = env.tmp_path / "apply-report.json"
    assert main(env.argv("apply", "--apply", "--out", str(out))) == 1
    assert "ERROR READ_ERROR" in capsys.readouterr().err
    assert out.exists()
    report = json.loads(out.read_text())
    by_repo = {entry["repository"]: entry for entry in report["repositories"]}
    assert sorted(by_repo) == sorted([REPOSITORIES["auth"], REPOSITORIES["items"]])
    assert by_repo[REPOSITORIES["auth"]]["action"] == "put"
    assert by_repo[REPOSITORIES["auth"]]["readBackVerified"] is True
    assert by_repo[REPOSITORIES["items"]]["action"] == "failed"
    assert by_repo[REPOSITORIES["items"]]["failureDetail"]
    assert report["preAuditWindowComplete"] is True
    assert failing_ecr.lifecycle_put_calls == [REPOSITORIES["auth"]]
    # the first repository put really happened: its policy was stored
    assert failing_ecr.lifecycle_policies[REPOSITORIES["auth"]] == _desired_text()


# env fixture -----------------------------------------------------------


@pytest.fixture
def env(monkeypatch, tmp_path):
    return RetentionEnv(monkeypatch, tmp_path)
