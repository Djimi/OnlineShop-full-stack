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

APPROVED_EVENT = json.dumps(
    [
        {
            "state": "approved",
            "environments": [{"name": "production"}],
            "user": {"login": "owner-login"},
            "approved_at": "2026-08-16T09:00:00Z",
        }
    ]
)

NOT_APPROVED_EVENT = json.dumps(
    [
        {
            "state": "pending",
            "environments": [{"name": "production"}],
            "user": {"login": "owner-login"},
            "approved_at": "2026-08-16T09:00:00Z",
        }
    ]
)

WRONG_ENV_EVENT = json.dumps(
    [
        {
            "state": "approved",
            "environments": [{"name": "staging"}],
            "user": {"login": "owner-login"},
            "approved_at": "2026-08-16T09:00:00Z",
        }
    ]
)

NO_TIMESTAMP_EVENT = json.dumps(
    [
        {
            "state": "approved",
            "environments": [{"name": "production"}],
            "user": {"login": "owner-login"},
        }
    ]
)


def _approval_bash(tmp_path: Path, event: str) -> subprocess.CompletedProcess[str]:
    gh_stub = tmp_path / "gh"
    gh_stub.write_text(
        "#!/bin/bash\n"
        "FILTER=\"\"\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  if [[ \"$1\" == \"--jq\" ]]; then FILTER=\"$2\"; shift 2; else shift; fi\n"
        "done\n"
        "printf \"%s\\n\" \"$EVENT_PAYLOAD\" | jq -r \"$FILTER\"\n"
    )
    gh_stub.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "EVENT_PAYLOAD": event,
        "REPOSITORY": "owner/repo",
        "RUN_ACTOR": "requester-login",
        "RUN_ID": "4713",
    }
    return _bash(APPROVAL_SCRIPT, env, cwd=tmp_path)


def test_approval_resolves_approver_and_strict_timestamp(tmp_path: Path) -> None:
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


def test_approval_fails_when_timestamp_missing_or_malformed(tmp_path: Path) -> None:
    result = _approval_bash(tmp_path, NO_TIMESTAMP_EVENT)
    assert result.returncode == 1
    assert "ISO-8601 approval timestamp" in result.stderr
    malformed = json.loads(APPROVED_EVENT)
    malformed[0]["approved_at"] = "2026-08-16T09:00:00+02:00"
    result = _approval_bash(tmp_path, json.dumps(malformed))
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
