"""Offline gates for promote-release-greenfield.yml (VR-PRO / VR-SEC-01/02/03).

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
PROMOTE_WORKFLOW = WORKFLOWS_DIR / "promote-release-greenfield.yml"

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
def test_actionlint_promotion_workflow() -> None:
    result = _run(ACTIONLINT, [str(PROMOTE_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


@needs_zizmor
def test_zizmor_promotion_workflow() -> None:
    result = _run(ZIZMOR, [str(PROMOTE_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Static YAML assertions
# ---------------------------------------------------------------------------


def test_dispatch_only_with_exact_stable_inputs() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"candidate_run_id", "candidate_run_attempt", "staging_run_id"}
    for name in inputs:
        assert inputs[name]["required"] == "true"


def test_workflow_permissions_are_contents_read() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    assert workflow["permissions"] == {"contents": "read"}


def test_job_permissions_are_job_scoped() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    preflight = workflow["jobs"]["preflight"]
    assert preflight["permissions"] == {
        "contents": "read",
        "actions": "read",
        "id-token": "write",
    }
    promote = workflow["jobs"]["promote"]
    assert promote["permissions"] == {
        "contents": "write",
        "actions": "read",
        "deployments": "read",
        "id-token": "write",
    }


def test_promote_job_requires_protected_production_environment() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    promote = workflow["jobs"]["promote"]
    assert promote.get("environment", {}).get("name") == "production"
    assert "environment" not in workflow["jobs"]["preflight"]


def test_promote_job_holds_the_production_concurrency_group() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    promote = workflow["jobs"]["promote"]
    assert promote["concurrency"] == {"group": "production", "cancel-in-progress": "false"}
    assert "concurrency" not in workflow["jobs"]["preflight"]


def test_legacy_bring_up_guard_precedes_aws_credentials_in_both_jobs() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
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
        # F7: both legacy production-mutation workflows are refused: the
        # legacy promotion (finalize-release.sh marker) and the legacy
        # rollback (deploy-rollback.sh marker), whose production-mutation
        # concurrency group does not serialize with the greenfield
        # `production` group.
        assert ".github/workflows/promote-release.yml" in run
        assert ".github/workflows/rollback-release.yml" in run
        assert "finalize-release.sh" in run
        assert "deploy-rollback.sh" in run
        assert run.count("exit 1") == 2, job_name
        assert "aws " not in run
        assert "No AWS credentials were used" in run


def test_job_scoped_role_boundaries_preflight_vs_promote() -> None:
    text = PROMOTE_WORKFLOW.read_text()
    workflow = _load(PROMOTE_WORKFLOW)
    assert workflow["env"]["AWS_ROLE_ARN_PRODUCTION"] == PRODUCTION_ROLE
    assert workflow["env"]["PRODUCTION_PREFLIGHT_ROLE_ARN"] == PRODUCTION_PREFLIGHT_ROLE
    assert PRODUCTION_ROLE in text
    assert PRODUCTION_PREFLIGHT_ROLE in text
    for role in FORBIDDEN_ROLES:
        assert role not in text, role
    preflight = workflow["jobs"]["preflight"]
    promote = workflow["jobs"]["promote"]
    preflight_aws = next(
        step
        for step in _steps(preflight)
        if "configure-aws-credentials" in str(step.get("uses", ""))
    )
    promote_aws = next(
        step
        for step in _steps(promote)
        if "configure-aws-credentials" in str(step.get("uses", ""))
    )
    # F6: the unapproved read-only preflight job assumes ONLY the separate
    # read-only preflight role; the mutation job assumes ONLY the full
    # production Deployer role.
    assert preflight_aws["with"]["role-to-assume"] == "${{ env.PRODUCTION_PREFLIGHT_ROLE_ARN }}"
    assert promote_aws["with"]["role-to-assume"] == "${{ env.AWS_ROLE_ARN_PRODUCTION }}"
    # neither job references the other job's role env var (or its ARN value
    # outside the env block)
    preflight_yaml = yaml.dump(preflight)
    promote_yaml = yaml.dump(promote)
    assert "AWS_ROLE_ARN_PRODUCTION" not in preflight_yaml
    assert "PRODUCTION_PREFLIGHT_ROLE_ARN" not in promote_yaml
    assert PRODUCTION_ROLE not in preflight_yaml
    assert PRODUCTION_PREFLIGHT_ROLE not in promote_yaml


def test_every_action_pinned_by_full_sha_with_version_comment() -> None:
    for line_number, line in enumerate(PROMOTE_WORKFLOW.read_text().splitlines(), start=1):
        match = PIN_LINE_RE.match(line)
        if match is None or match.group("action").startswith("./"):
            continue
        ref = match.group("ref")
        version = match.group("version")
        assert FULL_SHA_RE.fullmatch(ref), f"line {line_number}: {ref} is not a full SHA"
        assert version is not None, f"line {line_number}: missing '# vX.Y.Z' comment"


def test_inputs_validated_in_shell_before_use_in_both_jobs() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    for job in workflow["jobs"].values():
        validate = next(
            step for step in _steps(job) if step.get("name") == "Validate dispatch inputs"
        )
        assert validate["env"]["CANDIDATE_RUN_ID"] == "${{ github.event.inputs.candidate_run_id }}"
        assert (
            validate["env"]["CANDIDATE_RUN_ATTEMPT"]
            == "${{ github.event.inputs.candidate_run_attempt }}"
        )
        assert validate["env"]["STAGING_RUN_ID"] == "${{ github.event.inputs.staging_run_id }}"
        assert validate["run"].count("^[0-9]+$") == 3


def test_no_build_steps_in_the_promotion_workflow() -> None:
    text = PROMOTE_WORKFLOW.read_text()
    for forbidden in (
        "docker/build-push-action",
        "build-push-action",
        "mvn",
        "ecr get-login-password",
        "docker build",
        "npm run build",
    ):
        assert forbidden not in text, forbidden


def test_candidate_artifacts_downloaded_from_exact_run_and_attempt() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    for job in workflow["jobs"].values():
        downloads = [
            step
            for step in _steps(job)
            if "download-artifact" in str(step.get("uses", ""))
        ]
        for step in downloads:
            with_values = step.get("with", {})
            assert with_values["github-token"] == "${{ github.token }}"
            name = with_values["name"]
            if "candidate-" in name or "frontend-archive-" in name or "sboms-" in name:
                assert with_values["run-id"] == "${{ github.event.inputs.candidate_run_id }}"
                assert "${{ github.event.inputs.candidate_run_id }}" in name
                assert "${{ github.event.inputs.candidate_run_attempt }}" in name


def test_promote_job_downloads_preflight_report_from_same_run() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    job = workflow["jobs"]["promote"]
    download = next(
        step
        for step in _steps(job)
        if "preflight-report" in str(step.get("with", {}).get("name", ""))
    )
    assert download["with"]["name"] == (
        "preflight-report-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert download["with"]["run-id"] == "${{ github.run_id }}"


def test_promote_job_repeats_full_preflight_with_previous_report() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    job = workflow["jobs"]["promote"]
    repeat = next(
        step
        for step in _steps(job)
        if step.get("name") == "Repeat the full preflight after approval and lock"
    )
    assert "promote preflight" in repeat["run"]
    assert "--previous-report preflight/preflight-report.json" in repeat["run"]
    assert "--snapshot production-snapshot.json" in repeat["run"]
    assert "--repo-path \"$GITHUB_WORKSPACE\"" in repeat["run"]


def test_ordered_deploy_sequence_backends_gateway_frontend() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    job = workflow["jobs"]["promote"]
    names = [step.get("name") for step in _steps(job)]
    backends = names.index("Deploy backends (Auth + Items)")
    gateway = names.index("Deploy API Gateway")
    frontend = names.index("Deploy frontend (immutable prefix + live switch)")
    verify = names.index("Verify production read-only")
    finalize = names.index("Finalize the official release")
    assert backends < gateway < frontend < verify < finalize


def test_approver_derived_from_approvals_api_not_actor() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    job = workflow["jobs"]["promote"]
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
    # F16 (CT-AUDIT-01): approvedAt comes from the approvals API response
    # (approved_at, falling back to created_at), strictly validated as
    # non-empty ISO-8601; the runner clock (`date`) is never consulted.
    assert ".approved_at // .created_at // empty" in run
    assert "--arg approvedAt \"$APPROVED_AT\"" in run
    assert "date" not in run
    assert "NOW=" not in run
    assert (
        "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$" in run
    )
    assert (
        "ERROR: the approved production review carries no valid ISO-8601 approval timestamp"
        in run
    )
    assert '.approver != "" and .requester != "" and .approvedAt != ""' in run
    # the approvals-API fetch happens BEFORE the approval-evidence assembly
    # (the file write), never the other way around
    assert run.index('repos/$REPOSITORY/actions/runs/$RUN_ID/approvals') < run.index(
        "> approval-evidence.json"
    )
    # github.actor is only ever the requester, never the approver
    assert "$RUN_ACTOR" in run
    assert '--arg approver "$APPROVED_BY"' in run
    assert '--arg requester "$RUN_ACTOR"' in run
    # GITHUB_OUTPUT is never used with key=value injection
    assert "GITHUB_OUTPUT" not in run
    # both logins are regex-validated before the JSON is written
    assert run.count("^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$") == 2


def test_finalize_uses_validated_staging_identity_from_report() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    job = workflow["jobs"]["promote"]
    step = next(
        step for step in _steps(job) if step.get("name") == "Finalize the official release"
    )
    run = step["run"]
    assert "STAGING_IDENTITY=$(jq -r '.stagingGate.evidenceIdentity' preflight-report.json)" in run
    assert '"$STAGING_IDENTITY"' in run
    assert "--manifest release-manifest.json" in run
    assert "--out finalize-report.json" in run


def test_evidence_artifacts_uploaded_with_14_day_retention_on_failure() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    job = workflow["jobs"]["promote"]
    upload = next(
        step
        for step in _steps(job)
        if step.get("name") == "Upload promotion evidence (snapshot + reports)"
    )
    assert upload.get("if") == "always()"
    assert upload["with"]["retention-days"] == "14"
    path_block = upload["with"]["path"]
    for evidence in (
        "production-snapshot.json",
        "preflight-report.json",
        "staging-record.json",
        "frontend-publish.json",
        "verification-report.json",
        "release-manifest.json",
        "finalize-report.json",
    ):
        assert evidence in path_block


def test_promotion_compensation_is_a_dedicated_job() -> None:
    # Phase 6: compensation is a dedicated job; the promote/preflight jobs
    # never run the recover CLI themselves (they only preserve evidence).
    workflow = _load(PROMOTE_WORKFLOW)
    assert set(workflow["jobs"]) == {"preflight", "promote", "compensate"}
    for job_name in ("preflight", "promote"):
        for step in _steps(workflow["jobs"][job_name]):
            name = str(step.get("name", "")).lower()
            assert "compensat" not in name, job_name
            assert re.search(r"\brecover\b", name) is None, job_name
            run = str(step.get("run", "")).lower()
            assert "delivery.cli recover" not in run, job_name


# ---------------------------------------------------------------------------
# Phase 6 automatic recovery (AD-13 / OP-REC-01/02)
# ---------------------------------------------------------------------------


def test_compensate_job_exists_and_is_gated_on_post_mutation_failure() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    assert compensate["needs"] == "promote"
    condition = compensate["if"]
    assert "failure()" in condition
    assert "needs.promote.outputs.backends == 'success'" in condition
    assert "needs.promote.outputs.gateway == 'success'" in condition
    assert "needs.promote.outputs.frontend == 'success'" in condition


def test_promote_job_publishes_mutation_step_completion_outputs() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    promote = workflow["jobs"]["promote"]
    assert promote["outputs"] == {
        "backends": "${{ steps.deploy-backends.outputs.outcome }}",
        "gateway": "${{ steps.deploy-gateway.outputs.outcome }}",
        "frontend": "${{ steps.deploy-frontend.outputs.outcome }}",
    }
    for step_name, step_id in (
        ("Deploy backends (Auth + Items)", "deploy-backends"),
        ("Deploy API Gateway", "deploy-gateway"),
        ("Deploy frontend (immutable prefix + live switch)", "deploy-frontend"),
    ):
        step = next(step for step in _steps(promote) if step.get("name") == step_name)
        assert step["id"] == step_id
        assert 'echo "outcome=success" >> "$GITHUB_OUTPUT"' in step["run"]
        # the output is set AFTER the mutation command, never before it
        run = step["run"]
        assert run.index("outcome=success") > run.index("python -m delivery.cli")


def test_compensate_requires_no_environment_approval() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    assert "environment" not in compensate


def test_compensate_holds_non_canceling_production_concurrency_group() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    assert compensate["concurrency"] == {"group": "production", "cancel-in-progress": "false"}


def test_compensate_uses_only_the_production_role() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
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


def test_compensate_downloads_the_snapshot_uploaded_before_mutation() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    promote = workflow["jobs"]["promote"]
    names = [step.get("name") for step in _steps(promote)]
    capture = names.index("Capture the pre-mutation production snapshot")
    snapshot_upload = names.index(
        "Upload the pre-mutation snapshot for automatic recovery"
    )
    first_mutation = names.index("Deploy backends (Auth + Items)")
    assert capture < snapshot_upload < first_mutation
    assert _steps(promote)[snapshot_upload]["with"] == {
        "name": "promotion-snapshot-${{ github.run_id }}-${{ github.run_attempt }}",
        "path": "production-snapshot.json",
        "if-no-files-found": "warn",
        "retention-days": "14",
    }
    compensate = workflow["jobs"]["compensate"]
    download = next(
        step
        for step in _steps(compensate)
        if "promotion-snapshot-" in str(step.get("with", {}).get("name", ""))
    )
    assert download["with"]["name"] == (
        "promotion-snapshot-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert download["with"]["run-id"] == "${{ github.run_id }}"


def test_promote_job_records_failure_context_before_evidence_upload() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    promote = workflow["jobs"]["promote"]
    context = next(
        step
        for step in _steps(promote)
        if step.get("name") == "Record the promotion failure context"
    )
    assert context["if"] == "failure()"
    assert "conclusion" in context["run"]
    assert '"failed"' in context["run"]
    upload = next(
        step
        for step in _steps(promote)
        if step.get("name") == "Upload promotion evidence (snapshot + reports)"
    )
    assert "promotion-failure-context.json" in upload["with"]["path"]
    assert _steps(promote).index(context) < _steps(promote).index(upload)


def test_compensate_builds_changed_array_only_from_completed_steps() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    step = next(
        step
        for step in _steps(compensate)
        if step.get("name") == "Build the changed component array from completed mutation steps"
    )
    assert step["env"] == {
        "BACKENDS": "${{ needs.promote.outputs.backends }}",
        "GATEWAY": "${{ needs.promote.outputs.gateway }}",
        "FRONTEND": "${{ needs.promote.outputs.frontend }}",
    }
    run = step["run"]
    assert 'CHANGED=$(jq -c \'. + ["auth", "items"]\' <<<"$CHANGED")' in run
    assert 'CHANGED=$(jq -c \'. + ["gateway"]\' <<<"$CHANGED")' in run
    assert 'CHANGED=$(jq -c \'. + ["frontend"]\' <<<"$CHANGED")' in run
    assert 'jq -e \'length > 0\' <<<"$CHANGED"' in run
    assert "printf '%s\\n' \"$CHANGED\" > changed.json" in run


def test_compensate_runs_recover_with_snapshot_and_out() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    step = next(
        step
        for step in _steps(compensate)
        if step.get("name") == "Restore the pre-mutation snapshot (automatic recovery)"
    )
    run = step["run"]
    assert "python -m delivery.cli recover" in run
    assert "--snapshot promotion-snapshot/production-snapshot.json" in run
    assert "--changed changed.json" in run
    assert "--out recovery-result.json" in run
    assert '--original-failure "$ORIGINAL_FAILURE"' in run
    assert "--environment production" in run
    assert "scripts/config/production-identifiers.json" in run
    assert 'jq -e \'.conclusion == "failed"\'' in run


def test_compensate_uploads_recovery_result_with_14_day_retention() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    upload = next(
        step
        for step in _steps(compensate)
        if step.get("name") == "Upload the recovery result"
    )
    assert upload.get("if") == "always()"
    assert upload["with"]["retention-days"] == "14"
    assert upload["with"]["name"] == (
        "recovery-result-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert "recovery-result.json" in upload["with"]["path"]
    assert "recovery-verification.json" in upload["with"]["path"]


def test_compensate_verifies_restored_state_before_outcome_report() -> None:
    # F2 (OP-REC-02 "repeat OP-DEP-04"): after recover, the compensate job
    # re-runs the read-only production verification against the SAME snapshot
    # recover consumed, and the verification precedes the outcome report.
    workflow = _load(PROMOTE_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    names = [step.get("name") for step in _steps(compensate)]
    restore = names.index("Restore the pre-mutation snapshot (automatic recovery)")
    verify = names.index("Verify the restored production state read-only")
    report = names.index("Report the original failure and the recovery outcome")
    assert restore < verify < report
    step = _steps(compensate)[verify]
    run = step["run"]
    assert "python -m delivery.cli verify production" in run
    assert "--snapshot promotion-snapshot/production-snapshot.json" in run
    assert "--out recovery-verification.json" in run
    assert "--environment production" in run
    assert "scripts/config/production-identifiers.json" in run
    # the verify step never uses the candidate or a release manifest
    assert "--manifest" not in run
    assert "--candidate" not in run


def test_compensate_reports_both_outcomes_and_fails_on_recovery_failure() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    step = next(
        step
        for step in _steps(compensate)
        if step.get("name") == "Report the original failure and the recovery outcome"
    )
    assert step.get("if") == "always()"
    env = step.get("env", {})
    assert env.get("ORIGINAL_RESULT") == "${{ needs.promote.result }}"
    assert env.get("RUN_ID") == "${{ github.run_id }}"
    run = step["run"]
    assert "ORIGINAL FAILURE" in run
    assert "$ORIGINAL_RESULT" in run
    assert "$RUN_ID" in run
    assert "${{" not in run
    assert "RECOVERY OUTCOME" in run or "recovery-result.json" in run
    assert "the original failure is UNRESOLVED" in run
    # a failed recovery, a missing verification report, and a failed
    # verification each flip the job outcome
    assert run.count("exit 1") == 4
    assert "VERIFICATION OUTCOME" in run
    assert "post-recovery verification" in run
    assert "NOT confirmed" in run


def test_compensate_job_permissions_are_job_scoped() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    compensate = workflow["jobs"]["compensate"]
    assert compensate["permissions"] == {
        "contents": "read",
        "actions": "read",
        "id-token": "write",
    }
    assert "environment" not in compensate


def test_promote_job_timeout_covers_worst_case() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    assert workflow["jobs"]["promote"]["timeout-minutes"] == "90"


def test_checkout_without_persisted_credentials() -> None:
    workflow = _load(PROMOTE_WORKFLOW)
    for job in workflow["jobs"].values():
        for step in _steps(job):
            if "actions/checkout" in str(step.get("uses", "")):
                assert step.get("with", {}).get("persist-credentials") == "false"


def test_staging_record_is_never_entered_by_hand() -> None:
    # the staging record artifact name is derived inside the engine from the
    # staging run id; the workflow never downloads it by a hand-written name
    text = PROMOTE_WORKFLOW.read_text()
    scrubbed = text.replace("staging-record.json", "").replace("staging-record-identity", "")
    assert "staging-record-$" not in scrubbed
    workflow = _load(PROMOTE_WORKFLOW)
    for job in workflow["jobs"].values():
        for step in _steps(job):
            for value in step.get("with", {}).values():
                assert "staging-record-${{ github.event.inputs.staging_run_id }}" not in str(value)
