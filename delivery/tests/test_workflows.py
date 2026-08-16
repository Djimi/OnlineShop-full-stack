"""Offline gates for the CI workflows (ci.yml and _java-service.yml).

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
CI_WORKFLOW = WORKFLOWS_DIR / "ci.yml"
JAVA_SERVICE_WORKFLOW = WORKFLOWS_DIR / "_java-service.yml"

# Expected build contexts per component, derived from the Dockerfiles and the
# repository layout (NOT from any legacy workflow): Items/Dockerfile COPYs
# common/ and Items/, so it needs the repository root as context with an
# explicit file path; Auth and api-gateway build from their own directories.
EXPECTED_BUILD_CONTEXTS: list[tuple[str | None, str | None]] = [
    ("Auth", None),
    (".", "Items/Dockerfile"),
    ("api-gateway", None),
]

CANDIDATE_BUILD_ROLE = "arn:aws:iam::799111666795:role/github-actions-candidate-build"
LEGACY_ROLE = "github-actions-onlineshop"

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
    # BaseLoader keeps all scalars as strings, so the YAML 1.1 "on:" boolean
    # quirk does not corrupt the trigger key (GitHub parses YAML 1.2).
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def _steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def _upload_steps(job: dict) -> list[dict]:
    return [step for step in _steps(job) if "upload-artifact" in str(step.get("uses", ""))]


def _build_push_steps(job: dict) -> list[dict]:
    return [step for step in _steps(job) if "build-push-action" in str(step.get("uses", ""))]


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
def test_actionlint_ci_workflow() -> None:
    result = _run(ACTIONLINT, [str(CI_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


@needs_actionlint
def test_actionlint_java_service_workflow() -> None:
    result = _run(ACTIONLINT, [str(JAVA_SERVICE_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


@needs_zizmor
def test_zizmor_ci_workflow() -> None:
    result = _run(ZIZMOR, [str(CI_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


@needs_zizmor
def test_zizmor_java_service_workflow() -> None:
    result = _run(ZIZMOR, [str(JAVA_SERVICE_WORKFLOW)])
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Static YAML assertions
# ---------------------------------------------------------------------------


def test_workflow_level_permissions_are_exactly_contents_read() -> None:
    ci = _load(CI_WORKFLOW)
    assert ci["permissions"] == {"contents": "read"}


def test_publish_is_the_only_job_with_id_token_write() -> None:
    ci = _load(CI_WORKFLOW)
    for name, job in ci["jobs"].items():
        permissions = job.get("permissions", {})
        if name == "publish":
            assert permissions.get("id-token") == "write"
        else:
            assert "id-token" not in permissions


def test_java_service_workflow_has_no_aws_identity() -> None:
    java = _load(JAVA_SERVICE_WORKFLOW)
    for job in java["jobs"].values():
        assert "id-token" not in job.get("permissions", {})
    text = JAVA_SERVICE_WORKFLOW.read_text()
    assert "configure-aws-credentials" not in text
    assert "id-token" not in text


def test_publish_runs_only_on_push_and_never_with_pull_request_credentials() -> None:
    ci = _load(CI_WORKFLOW)
    publish = ci["jobs"]["publish"]
    assert publish["if"] == "github.event_name == 'push'"
    aws_steps = [
        step
        for job in ci["jobs"].values()
        for step in _steps(job)
        if "configure-aws-credentials" in str(step.get("uses", ""))
    ]
    assert len(aws_steps) == 1
    assert aws_steps[0] in publish["steps"]
    for job in ci["jobs"].values():
        for step in _steps(job):
            assert "pull_request" not in str(step.get("if", ""))


def test_publish_depends_on_all_validation_gates() -> None:
    ci = _load(CI_WORKFLOW)
    assert ci["jobs"]["publish"]["needs"] == [
        "test-auth",
        "test-items",
        "test-gateway",
        "test-frontend",
        "e2e",
    ]


def test_e2e_job_has_no_aws_credentials_or_commands() -> None:
    ci = _load(CI_WORKFLOW)
    e2e = ci["jobs"]["e2e"]
    assert "id-token" not in e2e.get("permissions", {})
    for step in _steps(e2e):
        assert "configure-aws-credentials" not in str(step.get("uses", ""))
        assert "aws " not in str(step.get("run", ""))


def test_every_action_pinned_by_full_sha_with_version_comment() -> None:
    for path in (CI_WORKFLOW, JAVA_SERVICE_WORKFLOW):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            match = PIN_LINE_RE.match(line)
            if match is None:
                continue
            action = match.group("action")
            if action.startswith("./"):
                continue
            ref = match.group("ref")
            version = match.group("version")
            assert FULL_SHA_RE.fullmatch(ref), f"{path}:{line_number}: {ref} is not a full SHA"
            assert version is not None, f"{path}:{line_number}: missing '# vX.Y.Z' comment"


def test_artifact_names_embed_run_id_and_attempt() -> None:
    for path in (CI_WORKFLOW, JAVA_SERVICE_WORKFLOW):
        ci = _load(path)
        for job in ci["jobs"].values():
            for step in _upload_steps(job):
                name = step.get("with", {}).get("name", "")
                assert "github.run_id" in name, f"{path}: {step.get('name')} lacks run id"
                assert "github.run_attempt" in name, f"{path}: {step.get('name')} lacks run attempt"


def test_candidate_manifest_upload_fails_closed_when_missing() -> None:
    ci = _load(CI_WORKFLOW)
    upload = next(
        step
        for step in _upload_steps(ci["jobs"]["publish"])
        if "candidate-manifest-" in str(step.get("with", {}).get("name", ""))
    )
    assert upload["with"]["if-no-files-found"] == "error"


def test_candidate_build_role_default_never_legacy_role() -> None:
    ci = _load(CI_WORKFLOW)
    assert ci["env"]["AWS_ROLE_ARN_CANDIDATE_BUILD"] == CANDIDATE_BUILD_ROLE
    text = CI_WORKFLOW.read_text()
    assert LEGACY_ROLE not in text


def test_build_contexts_match_dockerfile_layout() -> None:
    def contexts(path: Path) -> list[tuple[str, str | None]]:
        workflow = _load(path)
        pairs = []
        for job in workflow["jobs"].values():
            for step in _build_push_steps(job):
                with_values = step.get("with", {})
                pairs.append((with_values.get("context"), with_values.get("file")))
        return pairs

    assert contexts(CI_WORKFLOW) == EXPECTED_BUILD_CONTEXTS


def test_ci_push_trigger_is_isolated_from_legacy_branches() -> None:
    # OP-CUT-01: legacy build-and-deploy.yml still owns main and feature/**
    # pushes; overlapping triggers would push identical sha-<fullsha> tags
    # into the same immutable ECR repositories. The push path runs on
    # greenfield/** only until cutover expands it (after legacy triggers are
    # disabled).
    ci = _load(CI_WORKFLOW)
    assert ci["on"]["push"]["branches"] == ["greenfield/**"]
    assert ci["on"]["pull_request"]["branches"] == ["main"]


def test_publish_frontend_packaging_is_preceded_by_setup_node_24() -> None:
    ci = _load(CI_WORKFLOW)
    steps = _steps(ci["jobs"]["publish"])
    node_steps = [step for step in steps if "setup-node" in str(step.get("uses", ""))]
    assert len(node_steps) == 1
    assert node_steps[0]["with"]["node-version"] == "24"
    packaging = next(
        step for step in steps if step.get("name") == "Build and package frontend candidate"
    )
    assert steps.index(node_steps[0]) < steps.index(packaging)


def _tags_step() -> dict:
    ci = _load(CI_WORKFLOW)
    publish = ci["jobs"]["publish"]
    return next(step for step in _steps(publish) if step.get("id") == "tags")


def _run_tags_script(script: str, ref: str, sha: str = "0" * 40) -> dict[str, str]:
    import tempfile

    with tempfile.NamedTemporaryFile() as output:
        env = {
            **os.environ,
            "WORKFLOW_SHA": sha,
            "WORKFLOW_REF": ref,
            "GITHUB_OUTPUT": output.name,
        }
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
        )
        assert result.returncode == 0, result.stderr
        return dict(line.split("=", 1) for line in Path(output.name).read_text().splitlines())


def test_build_push_steps_tag_sha_plus_mutable_never_latest_or_release() -> None:
    ci = _load(CI_WORKFLOW)
    push_steps = _build_push_steps(ci["jobs"]["publish"])
    assert len(push_steps) == 3
    for step in push_steps:
        tags = step["with"]["tags"]
        assert "${{ steps.tags.outputs.sha_tag }}" in tags
        assert "${{ steps.tags.outputs.mutable_tag }}" in tags
        assert ":latest" not in tags
        assert "release-" not in tags
    tags = _tags_step()
    assert tags["env"]["WORKFLOW_SHA"] == "${{ github.sha }}"
    assert "sha_tag=sha-" in tags["run"]


def test_candidate_tag_case_mapping_covers_main_feature_and_greenfield() -> None:
    script = _tags_step()["run"]
    assert "refs/heads/main)" in script
    assert "refs/heads/feature/*|refs/heads/greenfield/*)" in script
    main = _run_tags_script(script, "refs/heads/main")
    assert main["class"] == "main"
    assert main["mutable_tag"] == "main-latest"
    assert main["sha_tag"] == "sha-0000000000000000000000000000000000000000"
    feature = _run_tags_script(script, "refs/heads/feature/FOO.Bar_baz")
    assert feature["class"] == "feature"
    assert feature["mutable_tag"] == "branch-feature-foo.bar_baz"
    sanitized = _run_tags_script(script, "refs/heads/feature/CI+Test")
    assert sanitized["class"] == "feature"
    assert sanitized["mutable_tag"] == "branch-feature-ci-test"
    greenfield = _run_tags_script(script, "refs/heads/greenfield/bring-up")
    assert greenfield["class"] == "feature"
    assert greenfield["mutable_tag"] == "branch-greenfield-bring-up"


def test_candidate_validate_step_has_max_age_30() -> None:
    ci = _load(CI_WORKFLOW)
    validate = next(
        step
        for step in _steps(ci["jobs"]["publish"])
        if "candidate validate" in str(step.get("run", ""))
    )
    assert "--max-age-days 30" in validate["run"]


def test_candidate_validate_step_passes_class() -> None:
    ci = _load(CI_WORKFLOW)
    validate = next(
        step
        for step in _steps(ci["jobs"]["publish"])
        if "candidate validate" in str(step.get("run", ""))
    )
    assert validate["env"]["CANDIDATE_CLASS"] == "${{ steps.tags.outputs.class }}"
    assert '--class "$CANDIDATE_CLASS"' in validate["run"]


def test_ecr_digest_read_back_step_present() -> None:
    ci = _load(CI_WORKFLOW)
    readbacks = [
        step
        for step in _steps(ci["jobs"]["publish"])
        if "batch-get-image" in str(step.get("run", ""))
    ]
    assert len(readbacks) == 1
    step = readbacks[0]
    assert step["env"]["WORKFLOW_SHA"] == "${{ github.sha }}"
    assert "imageTag=sha-$WORKFLOW_SHA" in step["run"]
    # A freshly pushed multi-arch index can lag batch-get-image visibility, so
    # the read-back must re-query bounded instead of failing on the first miss.
    assert "for attempt in 1 2 3 4 5 6" in step["run"]
    assert "sleep 5" in step["run"]
    assert "not visible" in step["run"]


def test_manifest_records_what_the_frontend_gate_actually_ran() -> None:
    # CT-GEN-01: the frontend gate is lint + build only (package.json has no
    # test script), so the recorded conclusion must not claim "passed" tests.
    ci = _load(CI_WORKFLOW)
    inputs = next(
        step for step in _steps(ci["jobs"]["publish"]) if step.get("id") == "inputs"
    )
    assert 'frontend: "lint+build"' in inputs["run"]


def test_emit_step_derives_class_from_tags_output() -> None:
    ci = _load(CI_WORKFLOW)
    emit = next(
        step
        for step in _steps(ci["jobs"]["publish"])
        if step.get("name") == "Emit candidate manifest"
    )
    assert emit["env"]["CANDIDATE_CLASS"] == "${{ steps.tags.outputs.class }}"
    assert '--class "$CANDIDATE_CLASS"' in emit["run"]


def test_all_checkout_steps_disable_persist_credentials() -> None:
    for path in (CI_WORKFLOW, JAVA_SERVICE_WORKFLOW):
        workflow = _load(path)
        for job in workflow["jobs"].values():
            for step in _steps(job):
                if "actions/checkout" in str(step.get("uses", "")):
                    # BaseLoader keeps scalars as strings: false != False
                    assert step.get("with", {}).get("persist-credentials") == "false", (
                        f"{path}: {step.get('name')} leaks credentials"
                    )
