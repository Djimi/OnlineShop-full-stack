"""Tests for the candidate manifest command and candidate expiry checks."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from delivery.cli import main
from delivery.models import CandidateManifest
from delivery.validation import is_expired

SHA = "1" * 40
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
FRONTEND_ARTIFACT_DIGEST = "sha256:" + "e" * 64
CHECKSUM = "d" * 64


def inputs() -> dict:
    return {
        "source": {
            "repository": "Djimi@8793507/OnlineShop-full-stack",
            "branch": "main",
            "ref": "refs/heads/main",
            "fullSha": SHA,
        },
        "build": {
            "workflowRunId": 4711,
            "workflowRunAttempt": 1,
            "workflowUrl": "https://github.com/Djimi@8793507/OnlineShop-full-stack/actions/runs/4711",
            "createdAt": "2026-08-10T09:00:00Z",
            "completedAt": "2026-08-10T09:12:00Z",
        },
        "artifacts": {
            "auth": {"repository": "onlineshop-auth", "digest": DIGEST_A},
            "items": {
                "repository": "onlineshop-items",
                "digest": DIGEST_B,
                "commonSourceSha": SHA,
            },
            "gateway": {"repository": "onlineshop-gateway", "digest": DIGEST_C},
        },
        "frontend": {
            "artifactId": "frontend-4711-1",
            "artifactDigest": FRONTEND_ARTIFACT_DIGEST,
            "contentChecksum": CHECKSUM,
        },
        "tests": {
            "unit": "passed",
            "integration": "passed",
            "frontend": "passed",
            "localE2E": "passed",
        },
    }


def run_manifest(tmp_path, data: dict, candidate_class: str = "main", extra=None):
    inputs_file = tmp_path / "inputs.json"
    inputs_file.write_text(json.dumps(data))
    out = tmp_path / "manifest.json"
    argv = [
        "candidate",
        "manifest",
        "--inputs",
        str(inputs_file),
        "--out",
        str(out),
        "--class",
        candidate_class,
    ]
    if extra:
        argv += extra
    return main(argv), out


def manifest(completed_at: datetime) -> CandidateManifest:
    data = inputs()
    data["build"]["createdAt"] = (completed_at - timedelta(minutes=10)).isoformat()
    data["build"]["completedAt"] = completed_at.isoformat()
    return CandidateManifest.model_validate(
        {
            "schemaVersion": "1.0",
            "candidateId": f"cand-4711-1-{SHA[:12]}",
            "candidateClass": "main",
            "source": data["source"],
            "build": data["build"],
            "artifacts": {**data["artifacts"], "frontend": data["frontend"]},
            "tests": data["tests"],
            "productionEligible": True,
        }
    )


def test_manifest_main_happy_path(tmp_path, capsys):
    code, out = run_manifest(tmp_path, inputs())
    assert code == 0
    record = CandidateManifest.model_validate(json.loads(out.read_text()))
    assert record.candidateId == f"cand-4711-1-{SHA[:12]}"
    assert record.candidateClass == "main"
    assert record.productionEligible is True
    captured = capsys.readouterr()
    assert f"candidate cand-4711-1-{SHA[:12]} (main) written to" in captured.out


def test_manifest_feature_class_not_production_eligible(tmp_path, capsys):
    data = inputs()
    data["source"]["branch"] = "feature/checkout-flow"
    data["source"]["ref"] = "refs/heads/feature/checkout-flow"
    code, out = run_manifest(tmp_path, data, candidate_class="feature")
    assert code == 0
    record = CandidateManifest.model_validate(json.loads(out.read_text()))
    assert record.candidateClass == "feature"
    assert record.productionEligible is False


def test_manifest_ignores_production_eligible_override(tmp_path, capsys):
    data = inputs()
    data["productionEligible"] = True
    code, out = run_manifest(tmp_path, data, candidate_class="feature")
    assert code == 0
    record = CandidateManifest.model_validate(json.loads(out.read_text()))
    assert record.productionEligible is False


def test_manifest_common_source_sha_mismatch(tmp_path, capsys):
    data = inputs()
    data["artifacts"]["items"]["commonSourceSha"] = "2" * 40
    code, out = run_manifest(tmp_path, data)
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("key", ["workflowRunId", "workflowRunAttempt"])
@pytest.mark.parametrize("value", [0, -1, "abc"])
def test_manifest_rejects_invalid_run_identity(tmp_path, capsys, key, value):
    data = inputs()
    data["build"][key] = value
    code, out = run_manifest(tmp_path, data)
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize(
    "path,value",
    [
        ("artifacts.auth.digest", "sha256:zz"),
        ("artifacts.items.digest", "latest"),
        ("artifacts.gateway.digest", "1.2.3"),
        ("artifacts.gateway.digest", "sha256:" + "0" * 63),
        ("frontend.artifactDigest", "d41d8cd98f00b204e9800998ecf8427e"),
        ("frontend.artifactDigest", "a" * 64),
        ("frontend.artifactDigest", "sha256:" + "A" * 64),
        ("frontend.artifactDigest", "sha256:" + "a" * 63),
        ("frontend.contentChecksum", "0" * 63),
    ],
)
def test_manifest_rejects_bad_artifact_identity(tmp_path, capsys, path, value):
    data = inputs()
    node = data
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    code, out = run_manifest(tmp_path, data)
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("key", ["source", "build", "artifacts", "frontend", "tests"])
def test_manifest_missing_top_level_key(tmp_path, capsys, key):
    data = inputs()
    del data[key]
    code, out = run_manifest(tmp_path, data)
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err
    assert not out.exists()


def test_manifest_missing_nested_key(tmp_path, capsys):
    data = inputs()
    del data["artifacts"]["gateway"]
    code, out = run_manifest(tmp_path, data)
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err
    assert not out.exists()


def test_manifest_unreadable_inputs(tmp_path, capsys):
    code = main(
        [
            "candidate",
            "manifest",
            "--inputs",
            str(tmp_path / "does-not-exist.json"),
            "--out",
            str(tmp_path / "manifest.json"),
            "--class",
            "main",
        ]
    )
    assert code == 1
    assert "ERROR READ_ERROR" in capsys.readouterr().err
    assert not (tmp_path / "manifest.json").exists()


def test_manifest_rejects_non_positive_max_age_days(tmp_path, capsys):
    code, out = run_manifest(tmp_path, inputs(), extra=["--max-age-days", "0"])
    assert code == 2
    assert not out.exists()


def test_validate_expired_candidate_rejects(tmp_path, capsys):
    data = inputs()
    data["build"]["completedAt"] = (
        datetime.now(UTC) - timedelta(days=30, minutes=1)
    ).isoformat()
    code, out = run_manifest(tmp_path, data)
    assert code == 0
    code = main(
        ["candidate", "validate", "--manifest", str(out), "--max-age-days", "30"]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR VALIDATION" in err
    assert "expired" in err
    assert "30" in err


def test_validate_expiry_boundary_is_inclusive(tmp_path, capsys):
    data = inputs()
    data["build"]["completedAt"] = (
        datetime.now(UTC) - timedelta(days=30, hours=-1)
    ).isoformat()
    code, out = run_manifest(tmp_path, data)
    assert code == 0
    code = main(
        ["candidate", "validate", "--manifest", str(out), "--max-age-days", "30"]
    )
    assert code == 0


def test_validate_without_max_age_days_passes(tmp_path, capsys):
    data = inputs()
    data["build"]["completedAt"] = (
        datetime.now(UTC) - timedelta(days=60)
    ).isoformat()
    code, out = run_manifest(tmp_path, data)
    assert code == 0
    assert main(["candidate", "validate", "--manifest", str(out)]) == 0


def test_manifest_command_rejects_expired_inputs(tmp_path, capsys):
    data = inputs()
    data["build"]["completedAt"] = (
        datetime.now(UTC) - timedelta(days=31)
    ).isoformat()
    code, out = run_manifest(tmp_path, data, extra=["--max-age-days", "30"])
    assert code == 1
    assert "ERROR VALIDATION" in capsys.readouterr().err
    assert not out.exists()


def test_is_expired_with_injected_now():
    completed = datetime(2026, 8, 1, tzinfo=UTC)
    record = manifest(completed)
    now = datetime(2026, 8, 31, tzinfo=UTC)
    assert is_expired(record, 30, now) is False
    assert is_expired(record, 29, now) is True


def test_is_expired_without_limit_is_never_expired():
    record = manifest(datetime(2026, 1, 1, tzinfo=UTC))
    assert is_expired(record, None) is False
