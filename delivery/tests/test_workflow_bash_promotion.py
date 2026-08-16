"""Execution gates for the promote-release-greenfield.yml shell snippets.

The YAML shape is static-asserted in test_workflows_promotion.py; here the
untrusted-input validation step, the legacy bring-up guard, and the approval
evidence step are extracted from the workflow and executed with hostile or
synthetic values (VR-SEC-02, CT-AUDIT-01).
"""

import json
import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "promote-release-greenfield.yml"

VALIDATE_STEP_NAME = "Validate dispatch inputs"
GUARD_STEP_NAME = "Bring-up guard: refuse while a legacy production-mutation path is active"
APPROVAL_STEP_NAME = "Resolve the environment approver and approval timestamp"


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def _named_step(workflow: Path, name: str, job_name: str | None = None) -> dict:
    data = _load(workflow)
    if job_name:
        steps = data["jobs"][job_name]["steps"]
    else:
        job = next(iter(data["jobs"].values()))
        steps = job["steps"]
    return next(step for step in steps if step.get("name") == name)


def _bash(
    script: str, env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    merged = {key: value for key, value in os.environ.items() if key != "GH_TOKEN"}
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=merged,
        cwd=cwd or REPO_ROOT,
        check=False,
    )


VALIDATE_SCRIPT = _named_step(WORKFLOW, VALIDATE_STEP_NAME)["run"]

HOSTILE_VALUES = (
    "12a34",
    "12 34",
    "-1",
    "0x10",
    "1.5",
    "",
    "\n",
    "12\n34",
    "$(touch marker)",
    "$(id)",
    "`touch marker`",
    "12$(touch marker)",
    "${PATH}",
    "12\n$(touch marker)",
    "$(touch marker)\n",
    "'; touch marker; #",
    '"$(touch marker)"',
)


def test_validation_rejects_hostile_candidate_run_ids(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    for value in HOSTILE_VALUES:
        result = _bash(
            VALIDATE_SCRIPT,
            {
                "CANDIDATE_RUN_ID": value,
                "CANDIDATE_RUN_ATTEMPT": "1",
                "STAGING_RUN_ID": "1",
            },
            cwd=tmp_path,
        )
        assert result.returncode == 1, (value, result.stdout, result.stderr)
        assert not marker.exists(), value


def test_validation_rejects_hostile_attempts(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    for value in HOSTILE_VALUES:
        result = _bash(
            VALIDATE_SCRIPT,
            {
                "CANDIDATE_RUN_ID": "123",
                "CANDIDATE_RUN_ATTEMPT": value,
                "STAGING_RUN_ID": "1",
            },
            cwd=tmp_path,
        )
        assert result.returncode == 1, (value, result.stdout, result.stderr)
        assert not marker.exists(), value


def test_validation_rejects_hostile_staging_run_ids(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    for value in HOSTILE_VALUES:
        result = _bash(
            VALIDATE_SCRIPT,
            {
                "CANDIDATE_RUN_ID": "123",
                "CANDIDATE_RUN_ATTEMPT": "1",
                "STAGING_RUN_ID": value,
            },
            cwd=tmp_path,
        )
        assert result.returncode == 1, (value, result.stdout, result.stderr)
        assert not marker.exists(), value


def test_validation_accepts_digit_only_inputs(tmp_path: Path) -> None:
    for run_id, attempt, staging in (
        ("1", "1", "1"),
        ("0", "7", "42"),
        ("12345678901", "42", "9001"),
    ):
        result = _bash(
            VALIDATE_SCRIPT,
            {
                "CANDIDATE_RUN_ID": run_id,
                "CANDIDATE_RUN_ATTEMPT": attempt,
                "STAGING_RUN_ID": staging,
            },
            cwd=tmp_path,
        )
        assert result.returncode == 0, (run_id, attempt, result.stdout, result.stderr)


GUARD_SCRIPT = _named_step(WORKFLOW, GUARD_STEP_NAME)["run"]


def _legacy_tree(root: Path, content: str = "finalize-release.sh\n") -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    legacy = workflows / "promote-release.yml"
    legacy.write_text(content)
    return legacy


def _legacy_rollback_tree(root: Path, content: str = "deploy-rollback.sh\n") -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    legacy = workflows / "rollback-release.yml"
    legacy.write_text(content)
    return legacy


def test_guard_refuses_when_legacy_promotion_path_is_active(tmp_path: Path) -> None:
    _legacy_tree(tmp_path)
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "OP-CUT-01" in result.stderr
    assert "No AWS credentials were used" in result.stderr


def test_guard_proceeds_when_legacy_workflows_are_absent(tmp_path: Path) -> None:
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0
    assert "legacy production-mutation paths inactive" in result.stdout


def test_guard_proceeds_when_legacy_workflow_has_no_finalize_path(tmp_path: Path) -> None:
    _legacy_tree(
        tmp_path, content="name: legacy\njobs:\n  preflight:\n    runs-on: ubuntu-latest\n"
    )
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0
    assert "legacy production-mutation paths inactive" in result.stdout


def test_guard_refuses_when_legacy_rollback_mutation_path_is_active(tmp_path: Path) -> None:
    # F7: the legacy rollback-release.yml mutation path (deploy-rollback.sh
    # entry point) refuses the greenfield promotion even when the legacy
    # promotion workflow is already removed.
    _legacy_rollback_tree(tmp_path)
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "rollback-release.yml production-mutation path" in result.stderr
    assert "OP-CUT-01" in result.stderr
    assert "No AWS credentials were used" in result.stderr


def test_guard_proceeds_when_legacy_rollback_has_no_mutation_path(tmp_path: Path) -> None:
    _legacy_rollback_tree(
        tmp_path, content="name: legacy\njobs:\n  preflight:\n    runs-on: ubuntu-latest\n"
    )
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0
    assert "legacy production-mutation paths inactive" in result.stdout


def test_guard_refuses_when_either_legacy_mutation_path_exists(tmp_path: Path) -> None:
    _legacy_tree(tmp_path)
    _legacy_rollback_tree(tmp_path)
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    # the promotion check fires first in this setup
    assert "promote-release.yml production-mutation path" in result.stderr


def test_guard_refusal_declares_no_mutation(tmp_path: Path) -> None:
    _legacy_rollback_tree(tmp_path)
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "nothing was mutated" in result.stderr
    assert "No AWS credentials were used" in result.stderr


def test_both_jobs_carry_identical_guard_and_validation_scripts() -> None:
    data = _load(WORKFLOW)
    preflight = data["jobs"]["preflight"]["steps"]
    promote = data["jobs"]["promote"]["steps"]
    guard_a = next(step for step in preflight if step.get("name") == GUARD_STEP_NAME)
    guard_b = next(step for step in promote if step.get("name") == GUARD_STEP_NAME)
    for required in (
        'LEGACY_PROMOTE_WORKFLOW=".github/workflows/promote-release.yml"',
        'LEGACY_ROLLBACK_WORKFLOW=".github/workflows/rollback-release.yml"',
        "finalize-release.sh",
        "deploy-rollback.sh",
        "exit 1",
    ):
        assert required in guard_a["run"]
        assert required in guard_b["run"]
    assert guard_a["run"].count("exit 1") == 2
    assert guard_b["run"].count("exit 1") == 2
    validate_a = next(step for step in preflight if step.get("name") == VALIDATE_STEP_NAME)
    validate_b = next(step for step in promote if step.get("name") == VALIDATE_STEP_NAME)
    assert validate_a["run"] == validate_b["run"]


APPROVAL_SCRIPT = _named_step(WORKFLOW, APPROVAL_STEP_NAME, job_name="promote")["run"]


def _fake_gh(tmp_path: Path, response: list[dict]) -> dict[str, str]:
    """A stand-in for `gh api ... --jq <expr>` that applies the jq filter to a
    fixture response, mirroring gh's pass-through of the filter result and its
    non-zero exit when the filter calls error()."""
    response_file = tmp_path / "approvals-response.json"
    response_file.write_text(json.dumps(response))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'jq_expr="."\n'
        'prev=""\n'
        'for arg in "$@"; do\n'
        '  if [ "$prev" = "--jq" ]; then jq_expr="$arg"; break; fi\n'
        '  prev="$arg"\n'
        "done\n"
        f'jq "$jq_expr" "{response_file}"\n'
    )
    fake.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _approval_env(tmp_path: Path, response: list[dict]) -> dict[str, str]:
    env = _fake_gh(tmp_path, response)
    env.update(
        {
            "GH_TOKEN": "fake-token",
            "REPOSITORY": "octo-org/octo-repo",
            "RUN_ACTOR": "operator",
            "RUN_ID": "42",
        }
    )
    return env


def _approved_response(overrides: dict | None = None, drop: tuple[str, ...] = ()) -> list[dict]:
    event: dict = {
        "environments": [{"name": "production"}],
        "state": "approved",
        "user": {"login": "owner-reviewer"},
        "comment": "",
        "created_at": "2026-08-16T10:30:00Z",
    }
    if overrides:
        event.update(overrides)
    for field in drop:
        event.pop(field, None)
    return [event]


def test_approval_evidence_uses_api_timestamp_not_the_clock(tmp_path: Path) -> None:
    result = _bash(
        APPROVAL_SCRIPT,
        _approval_env(tmp_path, _approved_response()),
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "approval-evidence.json").read_text())
    assert evidence == {
        "schemaVersion": "1.0",
        "approver": "owner-reviewer",
        "requester": "operator",
        "workflowUrl": "https://github.com/octo-org/octo-repo/actions/runs/42",
        # F16: created_at from the API response — not the shell clock
        "approvedAt": "2026-08-16T10:30:00Z",
    }


def test_approval_evidence_prefers_approved_at_over_created_at(tmp_path: Path) -> None:
    result = _bash(
        APPROVAL_SCRIPT,
        _approval_env(
            tmp_path,
            _approved_response(
                {"approved_at": "2026-08-16T09:00:00Z", "created_at": "2026-08-16T10:30:00Z"}
            ),
        ),
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "approval-evidence.json").read_text())
    assert evidence["approvedAt"] == "2026-08-16T09:00:00Z"


def test_approval_evidence_fails_closed_without_api_timestamp(tmp_path: Path) -> None:
    result = _bash(
        APPROVAL_SCRIPT,
        _approval_env(tmp_path, _approved_response(drop=("created_at",))),
        cwd=tmp_path,
    )
    assert result.returncode == 1
    assert "no valid ISO-8601 approval timestamp" in result.stderr
    assert not (tmp_path / "approval-evidence.json").exists()


def test_approval_evidence_fails_closed_on_malformed_timestamp(tmp_path: Path) -> None:
    result = _bash(
        APPROVAL_SCRIPT,
        _approval_env(tmp_path, _approved_response({"created_at": "not-a-timestamp"})),
        cwd=tmp_path,
    )
    assert result.returncode == 1
    assert "no valid ISO-8601 approval timestamp" in result.stderr
    assert not (tmp_path / "approval-evidence.json").exists()


def test_approval_evidence_fails_closed_when_no_production_approval(tmp_path: Path) -> None:
    result = _bash(
        APPROVAL_SCRIPT,
        _approval_env(tmp_path, []),
        cwd=tmp_path,
    )
    # the gh --jq error() surfaces as a non-zero exit (gh propagates jq's
    # failure); any non-zero exit fails the step closed
    assert result.returncode != 0
    assert not (tmp_path / "approval-evidence.json").exists()


def test_approval_evidence_rejects_hostile_actor_logins(tmp_path: Path) -> None:
    result = _bash(
        APPROVAL_SCRIPT,
        _approval_env(tmp_path, _approved_response()) | {"RUN_ACTOR": "$(touch marker)"},
        cwd=tmp_path,
    )
    assert result.returncode == 1
    assert not (tmp_path / "marker").exists()
    assert not (tmp_path / "approval-evidence.json").exists()


# ---------------------------------------------------------------------------
# Phase 6: compensate job inline scripts (AD-13 / OP-REC-01/02)
# ---------------------------------------------------------------------------

CHANGED_BUILDER = _named_step(
    WORKFLOW,
    "Build the changed component array from completed mutation steps",
    job_name="compensate",
)["run"]

RESTORE_SCRIPT = _named_step(
    WORKFLOW,
    "Restore the pre-mutation snapshot (automatic recovery)",
    job_name="compensate",
)["run"]

# the report script embeds GitHub template expressions; substitute trusted
# values before executing it locally (the expressions are resolved by GitHub,
# never by the shell)
REPORT_SCRIPT = (
    _named_step(
        WORKFLOW,
        "Report the original failure and the recovery outcome",
        job_name="compensate",
    )["run"]
    .replace("${{ needs.promote.result }}", "failure")
    .replace("${{ github.run_id }}", "4711")
)


def _changed_builder(
    tmp_path: Path,
    backends: str = "",
    gateway: str = "",
    frontend: str = "",
) -> subprocess.CompletedProcess[str]:
    return _bash(
        CHANGED_BUILDER,
        {"BACKENDS": backends, "GATEWAY": gateway, "FRONTEND": frontend},
        cwd=tmp_path,
    )


def test_changed_builder_maps_all_completed_steps(tmp_path: Path) -> None:
    result = _changed_builder(tmp_path, "success", "success", "success")
    assert result.returncode == 0, result.stdout + result.stderr
    changed = json.loads((tmp_path / "changed.json").read_text())
    assert changed == ["auth", "items", "gateway", "frontend"]


def test_changed_builder_maps_only_completed_steps(tmp_path: Path) -> None:
    result = _changed_builder(tmp_path, "success", "", "success")
    assert result.returncode == 0
    changed = json.loads((tmp_path / "changed.json").read_text())
    assert changed == ["auth", "items", "frontend"]


def test_changed_builder_frontend_only(tmp_path: Path) -> None:
    result = _changed_builder(tmp_path, "", "", "success")
    assert result.returncode == 0
    changed = json.loads((tmp_path / "changed.json").read_text())
    assert changed == ["frontend"]


def test_changed_builder_fails_closed_without_completed_steps(tmp_path: Path) -> None:
    result = _changed_builder(tmp_path)
    assert result.returncode == 1
    assert "no completed mutation step" in result.stderr
    assert not (tmp_path / "changed.json").exists()


def test_changed_builder_rejects_hostile_step_outputs(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    hostile = (
        "$(touch marker)",
        "`touch marker`",
        "'; touch marker; #",
        "success\n$(touch marker)",
        "${PATH}",
        "success; touch marker",
    )
    for value in hostile:
        result = _changed_builder(tmp_path, value, "success", "")
        assert result.returncode == 0, (value, result.stderr)
        assert not marker.exists(), value
        changed = json.loads((tmp_path / "changed.json").read_text())
        assert changed == ["gateway"], value


def test_restore_step_passes_quoted_original_failure_to_recover(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "python-argv.txt"
    shim = bin_dir / "python"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$@" > "$SHIM_ARGS"\n'
        'cat > recovery-result.json <<\'JSON\'\n'
        '{"outcome": "completed"}\n'
        "JSON\n"
    )
    shim.chmod(0o755)
    marker = tmp_path / "marker"
    result = _bash(
        RESTORE_SCRIPT,
        {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ORIGINAL_RESULT": "$(touch marker)",
            "SHIM_ARGS": str(argv_file),
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()
    argv = argv_file.read_text().splitlines()
    assert "--original-failure" in argv
    assert argv[argv.index("--original-failure") + 1] == (
        "promotion job result: $(touch marker)"
    )
    assert "--snapshot" in argv and "--changed" in argv and "--out" in argv


def test_restore_step_prefers_the_failure_context(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "python-argv.txt"
    shim = bin_dir / "python"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$@" > "$SHIM_ARGS"\n'
        'cat > recovery-result.json <<\'JSON\'\n'
        '{"outcome": "completed"}\n'
        "JSON\n"
    )
    shim.chmod(0o755)
    evidence = tmp_path / "promotion-evidence"
    evidence.mkdir()
    (evidence / "promotion-failure-context.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "workflowRunId": 4711,
                "workflowRunAttempt": 1,
                "workflowUrl": "https://github.com/x/y/actions/runs/4711",
                "conclusion": "failed",
            }
        )
    )
    result = _bash(
        RESTORE_SCRIPT,
        {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ORIGINAL_RESULT": "failure",
            "SHIM_ARGS": str(argv_file),
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    argv = argv_file.read_text().splitlines()
    assert argv[argv.index("--original-failure") + 1] == (
        "promotion failed (run 4711, attempt 1)"
    )


def test_restore_step_fails_closed_on_malformed_failure_context(tmp_path: Path) -> None:
    evidence = tmp_path / "promotion-evidence"
    evidence.mkdir()
    (evidence / "promotion-failure-context.json").write_text("{not json")
    result = _bash(
        RESTORE_SCRIPT,
        {"ORIGINAL_RESULT": "failure"},
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert not (tmp_path / "recovery-result.json").exists()


def test_report_step_prints_both_outcomes_on_success(tmp_path: Path) -> None:
    (tmp_path / "recovery-result.json").write_text(
        json.dumps({"outcome": "completed", "components": [], "originalFailure": "x"})
    )
    (tmp_path / "recovery-verification.json").write_text(
        json.dumps({"conclusion": "passed"})
    )
    result = _bash(REPORT_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ORIGINAL FAILURE" in result.stdout
    assert "VERIFICATION OUTCOME: passed" in result.stdout
    assert "RECOVERY OUTCOME" in result.stdout


def test_report_step_fails_when_verification_missing(tmp_path: Path) -> None:
    (tmp_path / "recovery-result.json").write_text(
        json.dumps({"outcome": "completed", "components": [], "originalFailure": "x"})
    )
    result = _bash(REPORT_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "post-recovery verification produced no report" in result.stderr
    assert "NOT confirmed" in result.stderr


def test_report_step_fails_when_verification_failed(tmp_path: Path) -> None:
    (tmp_path / "recovery-result.json").write_text(
        json.dumps({"outcome": "completed", "components": [], "originalFailure": "x"})
    )
    (tmp_path / "recovery-verification.json").write_text(
        json.dumps({"conclusion": "failed"})
    )
    result = _bash(REPORT_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "post-recovery verification FAILED" in result.stderr
    assert "NOT confirmed" in result.stderr


VERIFY_SCRIPT = _named_step(
    WORKFLOW,
    "Verify the restored production state read-only",
    job_name="compensate",
)["run"]


def test_verify_step_runs_production_verification_against_the_snapshot(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "python-argv.txt"
    shim = bin_dir / "python"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$@" > "$SHIM_ARGS"\n'
        'cat > recovery-verification.json <<\'JSON\'\n'
        '{"conclusion": "passed"}\n'
        "JSON\n"
    )
    shim.chmod(0o755)
    result = _bash(
        VERIFY_SCRIPT,
        {"PATH": f"{bin_dir}:{os.environ['PATH']}", "SHIM_ARGS": str(argv_file)},
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    argv = argv_file.read_text().splitlines()
    assert argv[0] == "-m"
    assert argv[1] == "delivery.cli"
    assert argv[2] == "verify"
    assert argv[3] == "production"
    assert "--snapshot" in argv
    assert argv[argv.index("--snapshot") + 1] == "promotion-snapshot/production-snapshot.json"
    assert "--out" in argv
    assert argv[argv.index("--out") + 1] == "recovery-verification.json"


def test_report_step_fails_when_recovery_failed(tmp_path: Path) -> None:
    (tmp_path / "recovery-result.json").write_text(
        json.dumps(
            {
                "outcome": "failed",
                "components": [],
                "originalFailure": "promotion failed",
                "failureDetail": "READ_ERROR: boom",
            }
        )
    )
    result = _bash(REPORT_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "ORIGINAL FAILURE" in result.stdout
    assert "UNRESOLVED" in result.stderr
    assert "READ_ERROR: boom" in result.stderr


def test_report_step_fails_when_no_result_exists(tmp_path: Path) -> None:
    result = _bash(REPORT_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "UNRESOLVED" in result.stderr
