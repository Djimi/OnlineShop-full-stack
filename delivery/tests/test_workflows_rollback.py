"""Offline gates for rollback-release-greenfield.yml (VR-REC-02, VR-SEC-01/02/03).

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
ROLLBACK_WORKFLOW = WORKFLOWS_DIR / "rollback-release-greenfield.yml"

PRODUCTION_ROLE = "arn:aws:iam::799111666795:role/github-actions-production"
PRODUCTION_PREFLIGHT_ROLE = (
    "arn:aws:iam::799111666795:role/github-actions-production-preflight"
)
FORBIDDEN_ROLES = (
    "github-actions-onlineshop",
    "github-actions-staging",
    "github-actions-candidate-build",
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
def test_actionlint_rollback_workflow() -> None:
    result = _run(ACTIONLINT, [str(ROLLBACK_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


@needs_zizmor
def test_zizmor_rollback_workflow() -> None:
    result = _run(ZIZMOR, [str(ROLLBACK_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Static YAML assertions
# ---------------------------------------------------------------------------


def test_dispatch_only_with_single_version_input() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"version"}
    assert inputs["version"]["required"] == "true"


def test_workflow_permissions_are_contents_read() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    assert workflow["permissions"] == {"contents": "read"}


def test_job_permissions_are_job_scoped() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    for job_name in ("preflight", "rollback", "compensate"):
        assert workflow["jobs"][job_name]["permissions"] == {
            "contents": "read",
            "actions": "read",
            "id-token": "write",
        }


def test_only_the_rollback_job_requires_the_protected_environment() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    rollback = workflow["jobs"]["rollback"]
    assert rollback.get("environment", {}).get("name") == "production"
    assert "environment" not in workflow["jobs"]["preflight"]
    assert "environment" not in workflow["jobs"]["compensate"]


def test_rollback_job_holds_the_production_concurrency_group() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    rollback = workflow["jobs"]["rollback"]
    assert rollback["concurrency"] == {"group": "production", "cancel-in-progress": "false"}
    assert "concurrency" not in workflow["jobs"]["preflight"]
    compensate = workflow["jobs"]["compensate"]
    assert compensate["concurrency"] == {"group": "production", "cancel-in-progress": "false"}


def test_legacy_bring_up_guard_precedes_aws_credentials_in_all_jobs() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    for job_name, job in workflow["jobs"].items():
        steps = _steps(job)
        guard = next(
            step
            for step in steps
            if step.get("name")
            == "Bring-up guard: refuse while a legacy production-mutation path is active"
        )
        assert "uses" not in guard, job_name
        aws_steps = [
            step for step in steps if "configure-aws-credentials" in str(step.get("uses", ""))
        ]
        assert len(aws_steps) == 1, job_name
        assert steps.index(guard) < steps.index(aws_steps[0]), job_name
        run = guard["run"]
        assert ".github/workflows/promote-release.yml" in run
        assert ".github/workflows/rollback-release.yml" in run
        assert "finalize-release.sh" in run
        assert "deploy-rollback.sh" in run
        assert run.count("exit 1") == 2, job_name
        assert "aws " not in run
        assert "No AWS credentials were used" in run


def test_job_scoped_role_boundaries_preflight_vs_rollback() -> None:
    text = ROLLBACK_WORKFLOW.read_text()
    workflow = _load(ROLLBACK_WORKFLOW)
    assert workflow["env"]["AWS_ROLE_ARN_PRODUCTION"] == PRODUCTION_ROLE
    assert workflow["env"]["PRODUCTION_PREFLIGHT_ROLE_ARN"] == PRODUCTION_PREFLIGHT_ROLE
    assert PRODUCTION_ROLE in text
    assert PRODUCTION_PREFLIGHT_ROLE in text
    for role in FORBIDDEN_ROLES:
        assert role not in text, role
    preflight = workflow["jobs"]["preflight"]
    rollback = workflow["jobs"]["rollback"]
    preflight_aws = next(
        step
        for step in _steps(preflight)
        if "configure-aws-credentials" in str(step.get("uses", ""))
    )
    rollback_aws = next(
        step
        for step in _steps(rollback)
        if "configure-aws-credentials" in str(step.get("uses", ""))
    )
    # the unapproved read-only preflight job assumes ONLY the separate
    # read-only preflight role; the mutation job assumes ONLY the full
    # production Deployer role.
    assert preflight_aws["with"]["role-to-assume"] == "${{ env.PRODUCTION_PREFLIGHT_ROLE_ARN }}"
    assert rollback_aws["with"]["role-to-assume"] == "${{ env.AWS_ROLE_ARN_PRODUCTION }}"
    preflight_yaml = yaml.dump(preflight)
    rollback_yaml = yaml.dump(rollback)
    assert "AWS_ROLE_ARN_PRODUCTION" not in preflight_yaml
    assert "PRODUCTION_PREFLIGHT_ROLE_ARN" not in rollback_yaml
    assert PRODUCTION_ROLE not in preflight_yaml
    assert PRODUCTION_PREFLIGHT_ROLE not in rollback_yaml


def test_compensate_uses_only_the_production_role() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    aws_steps = [
        step
        for step in _steps(compensate)
        if "configure-aws-credentials" in str(step.get("uses", ""))
    ]
    assert len(aws_steps) == 1
    assert aws_steps[0]["with"]["role-to-assume"] == "${{ env.AWS_ROLE_ARN_PRODUCTION }}"
    compensate_yaml = yaml.dump(compensate)
    assert "PRODUCTION_PREFLIGHT_ROLE_ARN" not in compensate_yaml


def test_every_action_pinned_by_full_sha_with_version_comment() -> None:
    for line_number, line in enumerate(ROLLBACK_WORKFLOW.read_text().splitlines(), start=1):
        match = PIN_LINE_RE.match(line)
        if match is None or match.group("action").startswith("./"):
            continue
        ref = match.group("ref")
        version = match.group("version")
        assert FULL_SHA_RE.fullmatch(ref), f"line {line_number}: {ref} is not a full SHA"
        assert version is not None, f"line {line_number}: missing '# vX.Y.Z' comment"


def test_inputs_validated_in_shell_before_use_in_all_jobs() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    for job in workflow["jobs"].values():
        validate = next(
            step for step in _steps(job) if step.get("name") == "Validate dispatch inputs"
        )
        assert validate["env"]["VERSION"] == "${{ github.event.inputs.version }}"
        assert "^release-[0-9]{4}$" in validate["run"]


def test_no_build_steps_in_the_rollback_workflow() -> None:
    text = ROLLBACK_WORKFLOW.read_text()
    for forbidden in (
        "docker/build-push-action",
        "build-push-action",
        "mvn",
        "ecr get-login-password",
        "docker build",
        "npm run build",
    ):
        assert forbidden not in text, forbidden


def test_no_hand_entered_identifiers_or_mutations() -> None:
    # AD-14: digests, tags, ARNs, and URLs are never entered by hand; the
    # engine deploys existing digests from the official release manifest.
    # Comments documenting the read-only preflight role scope are excluded.
    text = "\n".join(
        line
        for line in ROLLBACK_WORKFLOW.read_text().splitlines()
        if not line.strip().startswith("#")
    )
    for forbidden in (
        "sha256:",
        "put-image",
        "task-definition/",
        "docker push",
        "ecr get-login-password",
    ):
        assert forbidden not in text, forbidden


def test_rollback_job_downloads_preflight_report_from_same_run() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    job = workflow["jobs"]["rollback"]
    download = next(
        step
        for step in _steps(job)
        if "preflight-report" in str(step.get("with", {}).get("name", ""))
    )
    assert download["with"]["name"] == (
        "preflight-report-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert download["with"]["run-id"] == "${{ github.run_id }}"


def test_rollback_job_executes_with_report_snapshot_and_approval() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    job = workflow["jobs"]["rollback"]
    step = next(
        step for step in _steps(job) if step.get("name") == "Execute the approved rollback"
    )
    run = step["run"]
    assert "python -m delivery.cli rollback execute" in run
    assert "--manifest preflight/release-manifest.json" in run
    assert "--snapshot production-snapshot.json" in run
    assert "--preflight-report preflight/preflight-report.json" in run
    assert "--approval approval-evidence.json" in run
    assert '--workflow-run-id "$RUN_ID"' in run
    assert '--workflow-run-attempt "$RUN_ATTEMPT"' in run
    assert "--environment production" in run
    assert "scripts/config/production-identifiers.json" in run
    assert "--out rollback-result.json" in run


def test_preflight_job_runs_engine_preflight_with_snapshot() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    job = workflow["jobs"]["preflight"]
    step = next(
        step for step in _steps(job) if step.get("name") == "Run the read-only rollback preflight"
    )
    run = step["run"]
    assert "python -m delivery.cli rollback preflight" in run
    assert '--release-id "$VERSION"' in run
    assert "--snapshot production-snapshot.json" in run
    assert "--schema-change absent" in run
    assert "--migration-reviewed false" in run
    assert '--repository "$REPOSITORY"' in run
    assert "--out preflight-report.json" in run
    assert "--manifest-out release-manifest.json" in run
    assert step["env"]["VERSION"] == "${{ github.event.inputs.version }}"
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"


def test_snapshot_capture_precedes_execution_in_rollback_job() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    job = workflow["jobs"]["rollback"]
    names = [step.get("name") for step in _steps(job)]
    capture = names.index("Capture the pre-mutation production snapshot")
    upload = names.index("Upload the pre-mutation snapshot for automatic compensation")
    execute = names.index("Execute the approved rollback")
    assert capture < upload < execute


def test_approver_derived_from_approvals_api_not_actor() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    job = workflow["jobs"]["rollback"]
    step = next(
        step
        for step in _steps(job)
        if step.get("name") == "Resolve the environment approver and approval timestamp"
    )
    run = step["run"]
    assert 'gh api "repos/$REPOSITORY/actions/runs/$RUN_ID/approvals"' in run
    assert '.state == "approved"' in run
    assert 'any(.name == "production")' in run
    assert ".user.login" in run
    assert ".approved_at // .created_at // empty" in run
    assert "--arg approvedAt \"$APPROVED_AT\"" in run
    assert "date" not in run
    assert (
        "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$" in run
    )
    assert "$RUN_ACTOR" in run
    assert '--arg approver "$APPROVED_BY"' in run
    assert '--arg requester "$RUN_ACTOR"' in run
    # github.actor is only ever the requester, never the approver
    assert "GITHUB_OUTPUT" not in run
    assert run.count("^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$") == 2


def test_evidence_artifacts_uploaded_with_14_day_retention() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    job = workflow["jobs"]["rollback"]
    upload = next(
        step
        for step in _steps(job)
        if step.get("name") == "Upload rollback evidence (snapshot + reports)"
    )
    assert upload.get("if") == "always()"
    assert upload["with"]["retention-days"] == "14"
    path_block = upload["with"]["path"]
    for evidence in (
        "production-snapshot.json",
        "preflight/preflight-report.json",
        "preflight/release-manifest.json",
        "approval-evidence.json",
        "rollback-result.json",
        "changed.json",
        "rollback-failure-context.json",
    ):
        assert evidence in path_block


def test_rollback_job_derives_changed_components_from_result() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    job = workflow["jobs"]["rollback"]
    step = next(
        step
        for step in _steps(job)
        if step.get("name") == "Derive changed components from the rollback result"
    )
    assert step["id"] == "derive-changed"
    assert step.get("if") == "always()"
    run = step["run"]
    assert (
        'jq -c \'[.components[] | select(.conclusion == "passed") | .component]\''
        in run
    )
    assert 'echo "changed-count=$CHANGED_COUNT" >> "$GITHUB_OUTPUT"' in run
    assert job["outputs"]["changed-count"] == "${{ steps.derive-changed.outputs.changed-count }}"


def test_compensate_gated_on_failure_and_completed_components() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    assert compensate["needs"] == "rollback"
    condition = compensate["if"]
    assert "failure()" in condition
    assert "needs.rollback.outputs.changed-count != '0'" in condition


def test_compensate_builds_changed_only_from_the_rollback_result() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    step = next(
        step
        for step in _steps(compensate)
        if step.get("name") == "Build the changed component array from the rollback result"
    )
    run = step["run"]
    assert (
        "jq -c '[.components[] | select(.conclusion == \"passed\") | .component]' "
        "rollback-evidence/rollback-result.json > changed.json" in run
    )
    assert 'jq -e \'length > 0\' changed.json' in run
    assert "automatic compensation must not run" in run


def test_compensate_runs_recover_with_snapshot_and_out() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    step = next(
        step
        for step in _steps(compensate)
        if step.get("name") == "Restore the pre-mutation snapshot (automatic compensation)"
    )
    run = step["run"]
    assert "python -m delivery.cli recover" in run
    assert "--snapshot rollback-snapshot/production-snapshot.json" in run
    assert "--changed changed.json" in run
    assert "--out recovery-result.json" in run
    assert '--original-failure "$ORIGINAL_FAILURE"' in run
    assert "--environment production" in run
    assert "scripts/config/production-identifiers.json" in run
    assert 'jq -e \'.conclusion == "failed"\'' in run


def test_compensate_downloads_snapshot_and_evidence_of_the_same_run() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    downloads = [
        step for step in _steps(compensate) if "download-artifact" in str(step.get("uses", ""))
    ]
    assert len(downloads) == 2
    names = {download["with"]["name"] for download in downloads}
    assert names == {
        "rollback-snapshot-${{ github.run_id }}-${{ github.run_attempt }}",
        "rollback-evidence-${{ github.run_id }}-${{ github.run_attempt }}",
    }
    for download in downloads:
        assert download["with"]["run-id"] == "${{ github.run_id }}"


def test_compensate_uploads_recovery_result_with_14_day_retention() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    upload = next(
        step
        for step in _steps(compensate)
        if step.get("name") == "Upload the recovery result"
    )
    assert upload.get("if") == "always()"
    assert upload["with"]["retention-days"] == "14"
    assert "recovery-result.json" in upload["with"]["path"]
    assert "recovery-verification.json" in upload["with"]["path"]


def test_compensate_verifies_restored_state_before_outcome_report() -> None:
    # F2 (OP-REC-02 "repeat OP-DEP-04"): after recover, the compensate job
    # re-runs the read-only production verification against the SAME snapshot
    # recover consumed, and the verification precedes the outcome report.
    workflow = _load(ROLLBACK_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    names = [step.get("name") for step in _steps(compensate)]
    restore = names.index("Restore the pre-mutation snapshot (automatic compensation)")
    verify = names.index("Verify the restored production state read-only")
    report = names.index("Report the original failure and the recovery outcome")
    assert restore < verify < report
    step = _steps(compensate)[verify]
    run = step["run"]
    assert "python -m delivery.cli verify production" in run
    assert "--snapshot rollback-snapshot/production-snapshot.json" in run
    assert "--out recovery-verification.json" in run
    assert "--environment production" in run
    assert "scripts/config/production-identifiers.json" in run
    assert "--manifest" not in run
    assert "--candidate" not in run


def test_compensate_reports_both_outcomes_and_fails_on_recovery_failure() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    step = next(
        step
        for step in _steps(compensate)
        if step.get("name") == "Report the original failure and the recovery outcome"
    )
    assert step.get("if") == "always()"
    run = step["run"]
    assert "ORIGINAL FAILURE" in run
    assert "needs.rollback.result" in run
    assert "RECOVERY OUTCOME" in run
    assert "the original failure is UNRESOLVED" in run
    # a failed recovery, a missing verification report, and a failed
    # verification each flip the job outcome
    assert run.count("exit 1") == 4
    assert "VERIFICATION OUTCOME" in run
    assert "post-recovery verification" in run
    assert "NOT confirmed" in run


def test_no_official_release_creation_in_rollback() -> None:
    # OP-REC-04: rollback creates no official release and edits no history;
    # the workflow never calls the engine's release-creation paths.
    text = ROLLBACK_WORKFLOW.read_text()
    for forbidden in ("delivery.cli finalize", "create-release", "releases/", "gh release"):
        assert forbidden not in text, forbidden


def test_job_timeouts_cover_worst_case() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    assert workflow["jobs"]["rollback"]["timeout-minutes"] == "90"
    assert workflow["jobs"]["compensate"]["timeout-minutes"] == "60"
    assert workflow["jobs"]["preflight"]["timeout-minutes"] == "30"


def test_checkout_without_persisted_credentials() -> None:
    workflow = _load(ROLLBACK_WORKFLOW)
    for job in workflow["jobs"].values():
        for step in _steps(job):
            if "actions/checkout" in str(step.get("uses", "")):
                assert step.get("with", {}).get("persist-credentials") == "false"


def test_untrusted_inputs_only_in_env_blocks() -> None:
    # github.event.inputs must only appear in env: mappings, never directly
    # in run: scripts or action inputs.
    for line_number, line in enumerate(ROLLBACK_WORKFLOW.read_text().splitlines(), start=1):
        if "github.event.inputs" not in line:
            continue
        stripped = line.strip()
        assert stripped.startswith("VERSION:"), (
            f"line {line_number}: untrusted input outside an env block: {stripped}"
        )
