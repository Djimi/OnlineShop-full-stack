"""Offline gates for the Phase-4 staging workflows.

Static YAML assertions run unconditionally; actionlint and zizmor runs are
skipped when the binaries are not installed.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
STAGE_WORKFLOW = WORKFLOWS_DIR / "stage-candidate.yml"
RECONCILE_WORKFLOW = WORKFLOWS_DIR / "reconcile-staging.yml"

STAGING_ROLE = "arn:aws:iam::799111666795:role/github-actions-staging"
FORBIDDEN_ROLES = (
    "github-actions-onlineshop",
    "github-actions-candidate-build",
    "github-actions-production-deploy",
    "github-actions-promotion",
    "github-actions-rollback",
)

ACTIONLINT = shutil.which("/tmp/opencode/actionlint") or shutil.which("actionlint")
ZIZMOR = shutil.which(str(Path.home() / ".local/bin/zizmor")) or shutil.which("zizmor")

needs_actionlint = pytest.mark.skipif(ACTIONLINT is None, reason="actionlint not installed")
needs_zizmor = pytest.mark.skipif(ZIZMOR is None, reason="zizmor not installed")

PIN_LINE_RE = re.compile(
    r"^\s*uses:\s+(?P<action>\S+)@(?P<ref>[^\s#]+)(?:\s+#\s+v(?P<version>\d+\.\d+\.\d+))?\s*$",
    re.MULTILINE,
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def _steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def _run(tool: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [tool, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "GH_TOKEN"},
        check=False,
    )


# ---------------------------------------------------------------------------
# Tool gates
# ---------------------------------------------------------------------------


@needs_actionlint
def test_actionlint_stage_candidate_workflow() -> None:
    result = _run(ACTIONLINT, [str(STAGE_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


@needs_actionlint
def test_actionlint_reconcile_workflow() -> None:
    result = _run(ACTIONLINT, [str(RECONCILE_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


@needs_zizmor
def test_zizmor_stage_candidate_workflow() -> None:
    result = _run(ZIZMOR, [str(STAGE_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


@needs_zizmor
def test_zizmor_reconcile_workflow() -> None:
    result = _run(ZIZMOR, [str(RECONCILE_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Static YAML assertions
# ---------------------------------------------------------------------------


def test_stage_candidate_is_dispatch_only_with_exact_inputs() -> None:
    workflow = _load(STAGE_WORKFLOW)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert "push" not in workflow["on"]
    assert "pull_request" not in workflow["on"]
    assert "schedule" not in workflow["on"]
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"candidate_run_id", "candidate_run_attempt"}
    assert inputs["candidate_run_id"]["required"] == "true"
    assert inputs["candidate_run_attempt"]["required"] == "true"


def test_reconcile_triggers_are_schedule_and_dispatch_only() -> None:
    workflow = _load(RECONCILE_WORKFLOW)
    assert set(workflow["on"]) == {"schedule", "workflow_dispatch"}
    cron = workflow["on"]["schedule"][0]["cron"]
    assert cron == "*/15 * * * *"


def test_both_workflows_share_the_staging_concurrency_group() -> None:
    # GitHub serializes jobs across workflows that share a concurrency group
    # name in the same repository; both mutations must wait on each other.
    groups: dict[Path, str] = {}
    for path in (STAGE_WORKFLOW, RECONCILE_WORKFLOW):
        workflow = _load(path)
        for job in workflow["jobs"].values():
            concurrency = job.get("concurrency", {})
            groups[path] = concurrency.get("group", "")
            assert concurrency.get("group") == "staging", path
            assert concurrency.get("cancel-in-progress") == "false", path
    assert set(groups.values()) == {"staging"}


def test_workflow_permissions_are_contents_read() -> None:
    for path in (STAGE_WORKFLOW, RECONCILE_WORKFLOW):
        workflow = _load(path)
        assert workflow["permissions"] == {"contents": "read"}, path


def test_job_permissions_scoped_to_id_token_and_actions() -> None:
    stage = _load(STAGE_WORKFLOW)
    for name, job in stage["jobs"].items():
        permissions = job.get("permissions", {})
        assert permissions.get("id-token") == "write", name
        assert permissions.get("contents") == "read", name
        assert permissions.get("actions") == "read", name
    reconcile = _load(RECONCILE_WORKFLOW)
    for name, job in reconcile["jobs"].items():
        permissions = job.get("permissions", {})
        assert permissions.get("id-token") == "write", name
        assert permissions.get("contents") == "read", name
        assert "actions" not in permissions


def test_only_the_staging_role_is_assumed() -> None:
    for path in (STAGE_WORKFLOW, RECONCILE_WORKFLOW):
        text = path.read_text()
        workflow = _load(path)
        assert workflow["env"]["AWS_ROLE_ARN_STAGING"] == STAGING_ROLE, path
        assert STAGING_ROLE in text
        for role in FORBIDDEN_ROLES:
            assert role not in text, (path, role)
        aws_steps = [
            step
            for job in workflow["jobs"].values()
            for step in _steps(job)
            if "configure-aws-credentials" in str(step.get("uses", ""))
        ]
        assert len(aws_steps) == 1, path
        assert aws_steps[0]["with"]["role-to-assume"] == "${{ env.AWS_ROLE_ARN_STAGING }}", path


def test_no_production_identifiers_in_staging_workflows() -> None:
    for path in (STAGE_WORKFLOW, RECONCILE_WORKFLOW):
        text = path.read_text()
        for forbidden in (
            "onlineshop-cluster",
            "onlineshop-postgres-db",
            "cloudfront",
            "CloudFront",
        ):
            assert forbidden not in text, (path, forbidden)


def test_ecr_never_pushed_by_staging_workflows() -> None:
    for path in (STAGE_WORKFLOW, RECONCILE_WORKFLOW):
        text = path.read_text()
        assert "docker/build-push-action" not in text, path
        assert "ecr get-login-password" not in text, path
        assert "build-push-action" not in text, path


def test_every_action_pinned_by_full_sha_with_version_comment() -> None:
    for path in (STAGE_WORKFLOW, RECONCILE_WORKFLOW):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            match = PIN_LINE_RE.match(line)
            if match is None or match.group("action").startswith("./"):
                continue
            ref = match.group("ref")
            version = match.group("version")
            assert FULL_SHA_RE.fullmatch(ref), f"{path}:{line_number}: {ref} is not a full SHA"
            assert version is not None, f"{path}:{line_number}: missing '# vX.Y.Z' comment"


def test_stage_candidate_downloads_exact_run_attempt_artifacts() -> None:
    workflow = _load(STAGE_WORKFLOW)
    downloads = [
        step
        for job in workflow["jobs"].values()
        for step in _steps(job)
        if "download-artifact" in str(step.get("uses", ""))
    ]
    assert len(downloads) == 4
    for step in downloads:
        with_values = step.get("with", {})
        assert with_values["run-id"] == "${{ github.event.inputs.candidate_run_id }}"
        assert with_values["github-token"] == "${{ github.token }}"
        name = with_values["name"]
        assert "${{ github.event.inputs.candidate_run_id }}" in name
        assert "${{ github.event.inputs.candidate_run_attempt }}" in name
    names = {step["with"]["name"] for step in downloads}
    assert len(names) == 4


def test_stage_candidate_validates_inputs_in_shell_before_use() -> None:
    workflow = _load(STAGE_WORKFLOW)
    job = next(iter(workflow["jobs"].values()))
    validate = next(step for step in _steps(job) if step.get("name") == "Validate dispatch inputs")
    assert validate["env"]["CANDIDATE_RUN_ID"] == "${{ github.event.inputs.candidate_run_id }}"
    assert (
        validate["env"]["CANDIDATE_RUN_ATTEMPT"]
        == "${{ github.event.inputs.candidate_run_attempt }}"
    )
    assert "^[0-9]+$" in validate["run"]


def test_stage_candidate_checks_out_without_persisted_credentials() -> None:
    workflow = _load(STAGE_WORKFLOW)
    for job in workflow["jobs"].values():
        for step in _steps(job):
            if "actions/checkout" in str(step.get("uses", "")):
                assert step.get("with", {}).get("persist-credentials") == "false"


def test_stage_candidate_runs_lifecycle_twice_with_continue() -> None:
    workflow = _load(STAGE_WORKFLOW)
    job = next(iter(workflow["jobs"].values()))
    lifecycle_steps = [
        step
        for step in _steps(job)
        if "staging lifecycle" in str(step.get("run", ""))
    ]
    assert len(lifecycle_steps) == 2
    first, second = lifecycle_steps
    assert "--candidate candidate/candidate-manifest.json" in first["run"]
    assert "--e2e-url-out e2e-url.txt" in first["run"]
    assert "--continue" in second["run"]
    assert "--e2e-conclusion \"$E2E_CONCLUSION\"" in second["run"]


def test_stage_candidate_cloud_e2e_runs_against_resolved_url() -> None:
    workflow = _load(STAGE_WORKFLOW)
    job = next(iter(workflow["jobs"].values()))
    e2e = next(step for step in _steps(job) if step.get("name") == "Run cloud E2E suite")
    assert e2e["working-directory"] == "e2e-tests"
    assert e2e["env"]["E2E_BASE_URL"] == "${{ steps.e2e-url.outputs.base_url }}"
    java = next(step for step in _steps(job) if "setup-java" in str(step.get("uses", "")))
    assert java["with"]["java-version"] == "25"


def test_stage_candidate_verifies_frontend_byte_identity() -> None:
    workflow = _load(STAGE_WORKFLOW)
    job = next(iter(workflow["jobs"].values()))
    verify = next(
        step for step in _steps(job) if step.get("name") == "Verify frontend archive identity"
    )
    assert "artifactDigest" in verify["run"]
    assert "contentChecksum" in verify["run"]
    expected = (
        "CONTENT_CHECKSUM=$(cd candidate/frontend-dist && find . -type f -print0 "
        "| LC_ALL=C sort -z | xargs -0 sha256sum | cut -d' ' -f1 | sha256sum "
        "| cut -d' ' -f1)"
    )
    assert expected in verify["run"]


def test_stage_candidate_uploads_record_with_14_day_retention() -> None:
    workflow = _load(STAGE_WORKFLOW)
    job = next(iter(workflow["jobs"].values()))
    upload = next(
        step
        for step in _steps(job)
        if "upload-artifact" in str(step.get("uses", ""))
    )
    assert upload["with"]["retention-days"] == "14"
    assert "staging-record.json" in upload["with"]["path"]


def test_reconcile_uploads_record_best_effort() -> None:
    workflow = _load(RECONCILE_WORKFLOW)
    job = next(iter(workflow["jobs"].values()))
    upload = next(
        step
        for step in _steps(job)
        if "upload-artifact" in str(step.get("uses", ""))
    )
    assert upload["with"]["if-no-files-found"] == "warn"
    assert upload["with"]["retention-days"] == "14"


def test_reconcile_legacy_bring_up_guard_precedes_aws_credentials() -> None:
    # OP-CUT-01 / AD-09: while the legacy e2e-staging path exists, reconcile
    # must no-op BEFORE any AWS step (no credentials, no mutation, exit 0).
    workflow = _load(RECONCILE_WORKFLOW)
    job = next(iter(workflow["jobs"].values()))
    steps = _steps(job)
    guard = next(
        step
        for step in steps
        if step.get("name") == "Bring-up guard: skip while legacy staging path is active"
    )
    assert "uses" not in guard
    aws_steps = [
        step
        for step in steps
        if "configure-aws-credentials" in str(step.get("uses", ""))
    ]
    assert len(aws_steps) == 1
    assert steps.index(guard) < steps.index(aws_steps[0])
    run = guard["run"]
    assert '.github/workflows/build-and-deploy.yml' in run
    assert "grep -q -F -- 'e2e-staging'" in run
    assert '"$LEGACY_WORKFLOW"' in run
    assert "exit 0" in run
    assert "aws " not in run


def test_stage_candidate_timeout_minutes_cover_lifecycle_worst_case() -> None:
    # OP-STG-03/04: 90 minutes > the engine's worst-case bounded lifecycle so
    # a hard kill can never strand a valid marker (TTL 3h) with RDS running.
    workflow = _load(STAGE_WORKFLOW)
    for name, job in workflow["jobs"].items():
        assert job.get("timeout-minutes") == "90", name


def test_stage_candidate_lifecycle_invocations_pass_repo_path() -> None:
    # The engine CLI requires --repo-path on staging lifecycle/apply; the
    # workflow passes the checkout root explicitly (reconcile never takes it).
    workflow = _load(STAGE_WORKFLOW)
    job = next(iter(workflow["jobs"].values()))
    lifecycle_steps = [
        step
        for step in _steps(job)
        if "staging lifecycle" in str(step.get("run", ""))
    ]
    assert len(lifecycle_steps) == 2
    for step in lifecycle_steps:
        assert '--repo-path "$GITHUB_WORKSPACE"' in step["run"], step.get("name")


def test_reconcile_never_passes_repo_path() -> None:
    workflow = _load(RECONCILE_WORKFLOW)
    for job in workflow["jobs"].values():
        for step in _steps(job):
            assert "--repo-path" not in str(step.get("run", ""))


def test_stage_candidate_e2e_url_output_uses_multiline_delimiter() -> None:
    # VR-SEC-02: GITHUB_OUTPUT is written with the heredoc-style delimiter so
    # a hostile E2E URL can never inject a second key=value line.
    workflow = _load(STAGE_WORKFLOW)
    job = next(iter(workflow["jobs"].values()))
    step = next(step for step in _steps(job) if step.get("id") == "e2e-url")
    run = step["run"]
    assert "base_url<<EOF" in run
    assert 'echo "$E2E_BASE_URL"' in run
    assert "EOF" in run
    assert 'echo "base_url=' not in run
    assert "base_url=$E2E_BASE_URL" not in run


def test_staging_identifiers_json_shape() -> None:
    import json

    ids = json.loads((REPO_ROOT / "scripts/config/staging-identifiers.json").read_text())
    assert ids["environment"] == "staging"
    assert ids["accountId"] == "799111666795"
    assert ids["services"] == [
        "onlineshop-auth-staging",
        "onlineshop-items-staging",
        "onlineshop-api-gateway-staging",
    ]
    assert ids["dbInstance"] == "onlineshop-staging-postgres"
    assert ids["albName"] == "onlineshop-staging-v2-alb"
    for key in (
        "frontendBucket",
        "frontendLiveMarker",
        "frontendReleasesPrefix",
        "cloudfrontDistributionId",
    ):
        assert key not in ids
