"""Execution gates for the Phase-4 staging workflow shell snippets.

The YAML shape is static-asserted in test_workflows_staging.py; here the
untrusted-input validation step and the legacy bring-up guard are extracted
from the workflows and executed with hostile values, proving the shell itself
is safe: bad inputs exit 1 without being evaluated, and the guard no-ops or
proceeds deterministically without touching AWS.
"""

import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
STAGE_WORKFLOW = WORKFLOWS_DIR / "stage-candidate.yml"
RECONCILE_WORKFLOW = WORKFLOWS_DIR / "reconcile-staging.yml"

VALIDATE_STEP_NAME = "Validate dispatch inputs"
GUARD_STEP_NAME = "Bring-up guard: skip while legacy staging path is active"


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def _named_step(workflow: Path, name: str) -> dict:
    data = _load(workflow)
    job = next(iter(data["jobs"].values()))
    return next(step for step in job["steps"] if step.get("name") == name)


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


# ---------------------------------------------------------------------------
# stage-candidate.yml: Validate dispatch inputs
# ---------------------------------------------------------------------------

VALIDATE_SCRIPT = _named_step(STAGE_WORKFLOW, VALIDATE_STEP_NAME)["run"]

HOSTILE_RUN_IDS = (
    "12a34",  # non-digit
    "12 34",  # space
    "-1",  # sign
    "0x10",  # hex-looking
    "1.5",  # float-looking
    "",  # empty
    "\n",  # bare newline
    "12\n34",  # embedded newline
    "$(touch marker)",  # command substitution
    "$(id)",  # command substitution with output
    "`touch marker`",  # backtick substitution
    "12$(touch marker)",  # trailing substitution
    "${PATH}",  # parameter expansion
    "12\n$(touch marker)",  # newline then substitution
)


def test_validation_rejects_hostile_run_ids(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    for value in HOSTILE_RUN_IDS:
        result = _bash(
            VALIDATE_SCRIPT,
            {
                "CANDIDATE_RUN_ID": value,
                "CANDIDATE_RUN_ATTEMPT": "1",
                "MARKER": str(marker),
            },
            cwd=tmp_path,
        )
        assert result.returncode == 1, (value, result.stdout, result.stderr)
        # No evaluation: values carrying $(...) or backticks must never run.
        assert not marker.exists(), value


def test_validation_rejects_hostile_attempts(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    for value in ("abc", "1 2", "$(touch marker)", "1\n$(touch marker)", ""):
        result = _bash(
            VALIDATE_SCRIPT,
            {
                "CANDIDATE_RUN_ID": "123",
                "CANDIDATE_RUN_ATTEMPT": value,
                "MARKER": str(marker),
            },
            cwd=tmp_path,
        )
        assert result.returncode == 1, (value, result.stdout, result.stderr)
        assert not marker.exists(), value


def test_validation_accepts_digit_only_inputs(tmp_path: Path) -> None:
    for run_id, attempt in (("1", "1"), ("0", "7"), ("12345678901", "42")):
        result = _bash(
            VALIDATE_SCRIPT,
            {"CANDIDATE_RUN_ID": run_id, "CANDIDATE_RUN_ATTEMPT": attempt},
            cwd=tmp_path,
        )
        assert result.returncode == 0, (run_id, attempt, result.stdout, result.stderr)


# ---------------------------------------------------------------------------
# reconcile-staging.yml: legacy bring-up guard
# ---------------------------------------------------------------------------

GUARD_SCRIPT = _named_step(RECONCILE_WORKFLOW, GUARD_STEP_NAME)["run"]


def _legacy_tree(root: Path, content: str = "e2e-staging:\n") -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    legacy = workflows / "build-and-deploy.yml"
    legacy.write_text(content)
    return legacy


def test_guard_noops_when_legacy_e2e_staging_is_active(tmp_path: Path) -> None:
    _legacy_tree(tmp_path)
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0
    assert "legacy" in result.stdout
    assert "inactive" not in result.stdout


def test_guard_proceeds_when_legacy_workflow_is_absent(tmp_path: Path) -> None:
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0
    assert "inactive" in result.stdout


def test_guard_proceeds_when_legacy_workflow_has_no_e2e_staging(tmp_path: Path) -> None:
    _legacy_tree(tmp_path, content="name: legacy\njobs:\n  build:\n    runs-on: ubuntu-latest\n")
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0
    assert "inactive" in result.stdout


def test_guard_noop_notice_declares_no_mutation(tmp_path: Path) -> None:
    _legacy_tree(tmp_path)
    result = _bash(GUARD_SCRIPT, cwd=tmp_path)
    assert result.returncode == 0
    assert "nothing was mutated" in result.stdout
    assert "No AWS credentials were used" in result.stdout
