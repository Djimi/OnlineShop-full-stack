"""Execution gates for the rollback-release-greenfield.yml shell snippets.

The YAML shape is static-asserted in test_workflows_rollback.py; here the
untrusted-input validation step, the legacy bring-up guard, the changed-
component derivation, and the approval evidence step are extracted from the
workflow and executed with hostile or synthetic values (VR-SEC-02).
"""

import json
import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "rollback-release-greenfield.yml"

VALIDATE_STEP_NAME = "Validate dispatch inputs"
GUARD_STEP_NAME = "Bring-up guard: refuse while a legacy production-mutation path is active"
APPROVAL_STEP_NAME = "Resolve the environment approver and approval timestamp"
DERIVE_STEP_NAME = "Derive changed components from the rollback result"
BUILD_CHANGED_STEP_NAME = "Build the changed component array from the rollback result"


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
    "1.2.3",
    "release-12",
    "release-12345",
    "release-abc",
    "release-0002 extra",
    "release-0002\n$(touch marker)",
    "$(touch marker)",
    "`touch marker`",
    "release-0002;touch marker;",
    "release-0002\n",
    "",
    "main",
    "sha256:deadbeef",
)


def test_validation_rejects_hostile_versions(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    for value in HOSTILE_VALUES:
        result = _bash(VALIDATE_SCRIPT, {"VERSION": value}, cwd=tmp_path)
        assert result.returncode == 1, (value, result.stdout, result.stderr)
        assert not marker.exists(), value


def test_validation_accepts_release_id_inputs(tmp_path: Path) -> None:
    for value in ("release-0001", "release-0002", "release-9999"):
        result = _bash(VALIDATE_SCRIPT, {"VERSION": value}, cwd=tmp_path)
        assert result.returncode == 0, (value, result.stdout, result.stderr)


GUARD_SCRIPT = _named_step(WORKFLOW, GUARD_STEP_NAME)["run"]


def _legacy_rollback_tree(root: Path, content: str = "deploy-rollback.sh\n") -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    legacy = workflows / "rollback-release.yml"
    legacy.write_text(content)
    return legacy


def _legacy_promote_tree(root: Path, content: str = "finalize-release.sh\n") -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    legacy = workflows / "promote-release.yml"
    legacy.write_text(content)
    return legacy


def test_guard_refuses_when_legacy_rollback_path_is_active(tmp_path: Path) -> None:
    _legacy_rollback_tree(tmp_path)
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "rollback-release.yml production-mutation path" in result.stderr
    assert "OP-CUT-01" in result.stderr
    assert "No AWS credentials were used" in result.stderr


def test_guard_refuses_when_legacy_promotion_path_is_active(tmp_path: Path) -> None:
    _legacy_promote_tree(tmp_path)
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "promote-release.yml production-mutation path" in result.stderr
    assert "OP-CUT-01" in result.stderr
    assert "No AWS credentials were used" in result.stderr


def test_guard_proceeds_when_legacy_workflows_are_absent(tmp_path: Path) -> None:
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0
    assert "legacy production-mutation paths inactive" in result.stdout


def test_guard_proceeds_when_legacy_workflow_has_no_mutation_path(tmp_path: Path) -> None:
    _legacy_rollback_tree(
        tmp_path, content="name: legacy\njobs:\n  preflight:\n    runs-on: ubuntu-latest\n"
    )
    _legacy_promote_tree(
        tmp_path, content="name: legacy\njobs:\n  preflight:\n    runs-on: ubuntu-latest\n"
    )
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0
    assert "legacy production-mutation paths inactive" in result.stdout


APPROVAL_SCRIPT = _named_step(WORKFLOW, APPROVAL_STEP_NAME, job_name="rollback")["run"]

def _fixture_gh(tmp_path: Path, fixtures: dict[str, object]) -> dict[str, str]:
    """A stand-in for `gh api <url> --jq <expr>` that applies the jq filter to
    the fixture file for the requested URL, mirroring gh's pass-through of the
    filter result and its non-zero exit when the filter calls error()."""
    fixture_dir = tmp_path / "gh-fixtures"
    fixture_dir.mkdir(exist_ok=True)
    for url, payload in fixtures.items():
        key = url.replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "_")
        (fixture_dir / f"{key}.json").write_text(json.dumps(payload))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "gh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'url="$2"\n'
        'jq_expr="."\n'
        'prev=""\n'
        'for arg in "$@"; do\n'
        '  if [ "$prev" = "--jq" ]; then jq_expr="$arg"; fi\n'
        '  prev="$arg"\n'
        "done\n"
        'key=$(printf "%s" "$url" | tr "/?&=" "____")\n'
        'jq "$jq_expr" "$GH_FIXTURE_DIR/$key.json"\n'
    )
    fake.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "GH_FIXTURE_DIR": str(fixture_dir)}


RUN_SHA = "b" * 40

APPROVALS_URL = "repos/owner/repo/actions/runs/4713/approvals"
DEPLOYMENTS_URL = (
    f"repos/owner/repo/deployments?environment=production&sha={RUN_SHA}&per_page=1"
)
STATUSES_URL = "repos/owner/repo/deployments/987654/statuses"

APPROVED_EVENT = [
    {
        "state": "approved",
        "environments": [{"name": "production"}],
        "user": {"login": "owner-login"},
        "comment": "",
    }
]

NOT_APPROVED_EVENT = [
    {
        "state": "pending",
        "environments": [{"name": "production"}],
        "user": {"login": "owner-login"},
        "comment": "",
    }
]

WRONG_ENV_EVENT = [
    {
        "state": "approved",
        "environments": [{"name": "staging"}],
        "user": {"login": "owner-login"},
        "comment": "",
    }
]

IN_PROGRESS_STATUSES = [
    {
        "state": "waiting",
        "created_at": "2026-08-16T08:55:00Z",
        "creator": {"login": "octocat"},
    },
    {
        "state": "in_progress",
        "created_at": "2026-08-16T09:00:00Z",
        "creator": {"login": "octocat"},
    },
]


def _approval_bash(
    tmp_path: Path, approvals: list[dict], statuses: list[dict] | None = None
) -> subprocess.CompletedProcess[str]:
    env = _fixture_gh(
        tmp_path,
        {
            APPROVALS_URL: approvals,
            DEPLOYMENTS_URL: [
                {"id": 987654, "sha": RUN_SHA, "environment": "production"}
            ],
            STATUSES_URL: statuses if statuses is not None else IN_PROGRESS_STATUSES,
        },
    )
    env.update(
        {
            "GH_TOKEN": "fake-token",
            "REPOSITORY": "owner/repo",
            "RUN_ACTOR": "requester-login",
            "RUN_ID": "4713",
            "RUN_SHA": RUN_SHA,
        }
    )
    return _bash(APPROVAL_SCRIPT, env, cwd=tmp_path)


def test_approval_resolves_approver_and_deployment_status_timestamp(
    tmp_path: Path,
) -> None:
    result = _approval_bash(tmp_path, APPROVED_EVENT)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "approval-evidence.json").read_text())
    assert evidence["approver"] == "owner-login"
    assert evidence["requester"] == "requester-login"
    assert evidence["approvedAt"] == "2026-08-16T09:00:00Z"
    assert evidence["workflowUrl"] == "https://github.com/owner/repo/actions/runs/4713"


def test_approval_fails_when_no_approved_production_review(tmp_path: Path) -> None:
    for event in (NOT_APPROVED_EVENT, WRONG_ENV_EVENT):
        result = _approval_bash(tmp_path, event)
        assert result.returncode != 0, event
        assert "no approved production environment review" in result.stderr


def test_approval_fails_when_deployment_status_missing_or_malformed(
    tmp_path: Path,
) -> None:
    result = _approval_bash(
        tmp_path, APPROVED_EVENT, statuses=[{"state": "waiting", "created_at": "x"}]
    )
    assert result.returncode != 0
    assert "no in_progress deployment status" in result.stderr
    malformed = [
        {
            "state": "in_progress",
            "created_at": "2026-08-16T09:00:00+02:00",
            "creator": {"login": "octocat"},
        }
    ]
    result = _approval_bash(tmp_path, APPROVED_EVENT, statuses=malformed)
    assert result.returncode == 1
    assert "ISO-8601 approval timestamp" in result.stderr


DERIVE_SCRIPT = _named_step(WORKFLOW, DERIVE_STEP_NAME, job_name="rollback")["run"]


def _rollback_result(conclusions: dict[str, str]) -> dict:
    return {
        "components": [
            {"component": name, "conclusion": conclusion}
            for name, conclusion in conclusions.items()
        ]
    }


def test_derive_changed_uses_only_passed_components(tmp_path: Path) -> None:
    (tmp_path / "rollback-result.json").write_text(
        json.dumps(
            _rollback_result(
                {
                    "auth": "passed",
                    "items": "passed",
                    "gateway": "failed",
                    "frontend": "not-attempted",
                }
            )
        )
    )
    env = {"GITHUB_OUTPUT": str(tmp_path / "outputs.txt")}
    result = _bash(DERIVE_SCRIPT, env, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    changed = json.loads((tmp_path / "changed.json").read_text())
    assert changed == ["auth", "items"]
    outputs = (tmp_path / "outputs.txt").read_text()
    assert "changed-count=2" in outputs


def test_derive_changed_counts_zero_without_a_result(tmp_path: Path) -> None:
    env = {"GITHUB_OUTPUT": str(tmp_path / "outputs.txt")}
    result = _bash(DERIVE_SCRIPT, env, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    changed = json.loads((tmp_path / "changed.json").read_text())
    assert changed == []
    outputs = (tmp_path / "outputs.txt").read_text()
    assert "changed-count=0" in outputs


BUILD_CHANGED_SCRIPT = _named_step(WORKFLOW, BUILD_CHANGED_STEP_NAME, job_name="compensate")[
    "run"
]


def test_compensate_build_changed_fails_closed_on_empty(tmp_path: Path) -> None:
    (tmp_path / "rollback-evidence").mkdir()
    (tmp_path / "rollback-evidence" / "rollback-result.json").write_text(
        json.dumps(
            _rollback_result(
                {
                    "auth": "not-attempted",
                    "items": "not-attempted",
                    "gateway": "not-attempted",
                    "frontend": "not-attempted",
                }
            )
        )
    )
    result = _bash(BUILD_CHANGED_SCRIPT, cwd=tmp_path)
    assert result.returncode == 1
    assert "automatic compensation must not run" in result.stderr


def test_compensate_build_changed_accepts_passed_components(tmp_path: Path) -> None:
    (tmp_path / "rollback-evidence").mkdir()
    (tmp_path / "rollback-evidence" / "rollback-result.json").write_text(
        json.dumps(
            _rollback_result(
                {"auth": "passed", "items": "passed", "gateway": "passed", "frontend": "passed"}
            )
        )
    )
    result = _bash(BUILD_CHANGED_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    changed = json.loads((tmp_path / "changed.json").read_text())
    assert changed == ["auth", "items", "gateway", "frontend"]


def test_compensate_build_changed_never_includes_ambiguous_components(tmp_path: Path) -> None:
    # A `failed` component is ambiguous (partial mutation): it must never be
    # selected for automatic compensation, only `passed` is.
    (tmp_path / "rollback-evidence").mkdir()
    (tmp_path / "rollback-evidence" / "rollback-result.json").write_text(
        json.dumps(
            _rollback_result(
                {"auth": "passed", "items": "passed", "gateway": "failed", "frontend": "failed"}
            )
        )
    )
    result = _bash(BUILD_CHANGED_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    changed = json.loads((tmp_path / "changed.json").read_text())
    assert changed == ["auth", "items"]


# ---------------------------------------------------------------------------
# Phase 6 compensation outcome report + post-recovery verification (F2)
# ---------------------------------------------------------------------------

# the report script reads the trusted values through step env (GitHub
# resolves the template expressions, never the shell); supply them here
REPORT_SCRIPT = _named_step(
    WORKFLOW,
    "Report the original failure and the recovery outcome",
    job_name="compensate",
)["run"]

VERIFY_SCRIPT = _named_step(
    WORKFLOW,
    "Verify the restored production state read-only",
    job_name="compensate",
)["run"]


def test_report_step_prints_both_outcomes_on_success(tmp_path: Path) -> None:
    (tmp_path / "recovery-result.json").write_text(
        json.dumps({"outcome": "completed", "components": [], "originalFailure": "x"})
    )
    (tmp_path / "recovery-verification.json").write_text(
        json.dumps({"conclusion": "passed"})
    )
    result = _bash(REPORT_SCRIPT, {"ORIGINAL_RESULT": "failure", "RUN_ID": "4713"}, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ORIGINAL FAILURE" in result.stdout
    assert "VERIFICATION OUTCOME: passed" in result.stdout
    assert "RECOVERY OUTCOME" in result.stdout


def test_report_step_fails_when_recovery_failed(tmp_path: Path) -> None:
    (tmp_path / "recovery-result.json").write_text(
        json.dumps(
            {
                "outcome": "failed",
                "components": [],
                "originalFailure": "rollback failed",
                "failureDetail": "READ_ERROR: boom",
            }
        )
    )
    result = _bash(REPORT_SCRIPT, {"ORIGINAL_RESULT": "failure", "RUN_ID": "4713"}, cwd=tmp_path)
    assert result.returncode == 1
    assert "ORIGINAL FAILURE" in result.stdout
    assert "UNRESOLVED" in result.stderr
    assert "READ_ERROR: boom" in result.stderr


def test_report_step_fails_when_no_result_exists(tmp_path: Path) -> None:
    result = _bash(REPORT_SCRIPT, {"ORIGINAL_RESULT": "failure", "RUN_ID": "4713"}, cwd=tmp_path)
    assert result.returncode == 1
    assert "UNRESOLVED" in result.stderr


def test_report_step_fails_when_verification_missing(tmp_path: Path) -> None:
    (tmp_path / "recovery-result.json").write_text(
        json.dumps({"outcome": "completed", "components": [], "originalFailure": "x"})
    )
    result = _bash(REPORT_SCRIPT, {"ORIGINAL_RESULT": "failure", "RUN_ID": "4713"}, cwd=tmp_path)
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
    result = _bash(REPORT_SCRIPT, {"ORIGINAL_RESULT": "failure", "RUN_ID": "4713"}, cwd=tmp_path)
    assert result.returncode == 1
    assert "post-recovery verification FAILED" in result.stderr
    assert "NOT confirmed" in result.stderr


def test_verify_step_runs_production_verification_against_the_snapshot(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    argv_file = tmp_path / "python-argv.txt"
    shim = bin_dir / "python"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$@" > "$SHIM_ARGS"\n'
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
    assert argv[argv.index("--snapshot") + 1] == "rollback-snapshot/production-snapshot.json"
    assert "--out" in argv
    assert argv[argv.index("--out") + 1] == "recovery-verification.json"
