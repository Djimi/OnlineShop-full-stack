#!/usr/bin/env bash
set -euo pipefail

# Pass 3R.1 CI security regression gate. This gate is intentionally offline:
# it parses only the trusted workflow files, exercises argv-based validators
# with shell-hostile values, and proves no validator path evaluates input.

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
INPUTS="$RELEASE/bin/release-input.sh"
MARKER=$(mktemp)
rm -f "$MARKER"
trap 'rm -f "$MARKER"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

echo "[ 1/6] workflow YAML parses, rejects duplicate keys, and shell does not contain inline contexts"
python3 - "$REPO_ROOT" <<'PY' || exit 1
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError

root = Path(sys.argv[1])
workflow_paths = [
    root / ".github/workflows/build-and-deploy.yml",
    root / ".github/workflows/promote-release.yml",
    root / ".github/workflows/rollback-release.yml",
    root / ".github/workflows/promote-release-greenfield.yml",
    root / ".github/workflows/rollback-release-greenfield.yml",
]
expression_in_run = re.compile(r"\$\{\{\s*(?:github|inputs|needs|steps|matrix|vars)\.")
action_sha = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
problems = []

class UniqueKeyLoader(yaml.SafeLoader):
    """PyYAML loader that fails closed instead of silently overwriting keys."""


def construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)

# A normal yaml.safe_load call would silently keep only the second RELEASE_DIR
# value. Keep this fixture so that duplicate-key protection is itself tested.
try:
    yaml.load("env:\n  RELEASE_DIR: one\n  RELEASE_DIR: two\n", Loader=UniqueKeyLoader)
except ConstructorError:
    pass
else:
    problems.append("strict YAML loader accepted a duplicate mapping key")

for path in workflow_paths:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except ConstructorError as exc:
        problems.append(f"{path.name}: duplicate YAML mapping key: {exc}")
        continue
    jobs = data.get("jobs", {})
    top_permissions = data.get("permissions", {})
    if top_permissions != {"contents": "read"}:
        problems.append(f"{path.name}: workflow permissions must be exactly contents: read")
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            run = step.get("run", "")
            if expression_in_run.search(run):
                problems.append(f"{path.name}:{job_name}: GitHub context interpolated in run")
            uses = step.get("uses")
            if uses and not uses.startswith("./") and not action_sha.fullmatch(uses):
                problems.append(f"{path.name}:{job_name}: action is not pinned by full SHA: {uses}")

        permissions = job.get("permissions", {}) or {}
        if any(k in permissions for k in ("id-token", "contents", "actions")):
            if permissions.get("contents") not in ("read", "write"):
                problems.append(f"{path.name}:{job_name}: job must explicitly scope contents")

    # Every AWS credential bootstrap must be paired with job-scoped OIDC.
    for job_name, job in jobs.items():
        text = str(job)
        if "configure-aws-credentials" in text:
            permissions = (job.get("permissions") or {}) if isinstance(job, dict) else {}
            if permissions.get("id-token") != "write":
                problems.append(f"{path.name}:{job_name}: AWS job lacks job-scoped id-token: write")

build = yaml.load(
    (root / ".github/workflows/build-and-deploy.yml").read_text(encoding="utf-8"),
    Loader=UniqueKeyLoader,
)

changes = build.get("jobs", {}).get("changes", {})
changes_permissions = changes.get("permissions") or {}
if changes_permissions != {"contents": "read", "pull-requests": "read"}:
    problems.append(
        "changes job permissions must be exactly contents: read and pull-requests: read"
    )
resolve_steps = [
    step
    for step in changes.get("steps", [])
    if isinstance(step, dict) and step.get("id") == "resolve"
]
if len(resolve_steps) != 1:
    problems.append("changes job must have exactly one resolve step")
else:
    resolve_run = resolve_steps[0].get("run", "")

    def resolve_selection(event, ref, dispatch_service, filters):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "github-output"
            environment = os.environ.copy()
            environment.update(
                {
                    "GITHUB_OUTPUT": str(output),
                    "EVENT_NAME": event,
                    "WORKFLOW_REF": ref,
                    "DISPATCH_SERVICE": dispatch_service,
                    "FILTER_AUTH": filters["auth"],
                    "FILTER_ITEMS": filters["items"],
                    "FILTER_API_GATEWAY": filters["api-gateway"],
                    "FILTER_FRONTEND": filters["frontend"],
                }
            )
            result = subprocess.run(
                ["bash", "-c", resolve_run],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return None, f"resolver failed for {event}/{dispatch_service}: {result.stderr.strip()}"
            values = {}
            for line in output.read_text(encoding="utf-8").splitlines():
                key, value = line.split("=", 1)
                values[key] = value
            return values, None

    dispatch_values, error = resolve_selection(
        "workflow_dispatch",
        "refs/heads/main",
        "auth",
        {"auth": "false", "items": "false", "api-gateway": "false", "frontend": "false"},
    )
    if error:
        problems.append(error)
    elif dispatch_values != {
        "auth": "true",
        "items": "false",
        "api-gateway": "false",
        "frontend": "false",
    }:
        problems.append(
            "workflow_dispatch auth on refs/heads/main must select only auth: "
            f"{dispatch_values}"
        )

    push_values, error = resolve_selection(
        "push",
        "refs/heads/main",
        "",
        {"auth": "false", "items": "false", "api-gateway": "false", "frontend": "false"},
    )
    if error:
        problems.append(error)
    elif push_values != {
        "auth": "true",
        "items": "true",
        "api-gateway": "true",
        "frontend": "true",
    }:
        problems.append(
            "push on refs/heads/main must select all components: " f"{push_values}"
        )

for backend in ("auth", "items", "api-gateway"):
    job = build.get("jobs", {}).get(backend, {})
    steps = [step for step in job.get("steps", []) if isinstance(step, dict)]
    tag_steps = [step for step in steps if step.get("id") == "tags"]
    label_steps = [step for step in steps if step.get("name") == "Compute OCI labels"]
    if not tag_steps or not label_steps:
        problems.append(f"{backend}: missing tag/label security steps")
        continue
    tag_run = str(tag_steps[0].get("run", ""))
    label_run = str(label_steps[0].get("run", ""))
    for name, run in (("tags", tag_run), ("labels", label_run)):
        if 'case "$WORKFLOW_EVENT" in' not in run:
            problems.append(f"{backend} {name}: event-specific ref validation is missing")
        if 'push|workflow_dispatch) rl_assert_ci_ref "$WORKFLOW_REF"' not in run:
            problems.append(f"{backend} {name}: branch refs are not validated")
        if 'pull_request) rl_assert_ci_pr_ref "$WORKFLOW_REF"' not in run:
            problems.append(f"{backend} {name}: pull-request refs are not explicitly validated")
    if '"$WORKFLOW_EVENT" != "pull_request"' not in tag_run:
        problems.append(f"{backend} tags: branch/convenience tags are not excluded for PRs")
    if "sha-$WORKFLOW_SHA" not in tag_run:
        problems.append(f"{backend} tags: PR-safe SHA-local tag is missing")

if problems:
    print("\n".join(problems))
    raise SystemExit(1)
print("workflow security structure: OK")
PY

echo "[ 2/6] hostile argv values are rejected without evaluation"
expect_rejected() {
  local helper="$1" payload="$2"
  rm -f "$MARKER"
  if bash -c 'source "$1"; "$2" "$3"' _ "$INPUTS" "$helper" "$payload"; then
    fail "$helper accepted hostile input: $payload"
  fi
  [ ! -e "$MARKER" ] || fail "$helper evaluated hostile input: $payload"
}

# shellcheck disable=SC2016  # these values intentionally contain literal command syntax
HOSTILE_VALUES=(
  "'single quote'"
  '"double quote"'
  # shellcheck disable=SC2016  # intentionally literal command syntax for argv testing
  '$(touch '"$MARKER"')'
  '`touch '"$MARKER"'`'
  '; touch '"$MARKER"
  '> '"$MARKER"
  $'line1\nline2'
)
for value in "${HOSTILE_VALUES[@]}"; do
  expect_rejected rl_assert_semver "$value"
  expect_rejected rl_assert_full_sha "$value"
  expect_rejected rl_assert_positive_integer "$value"
  expect_rejected rl_assert_github_login "$value"
  expect_rejected rl_assert_ci_ref "$value"
  expect_rejected rl_assert_task_definition_arn "$value"
done

expect_rejected rl_assert_ci_ref 'refs/pull/123/merge'
# shellcheck disable=SC2016  # these values intentionally contain literal command syntax
PR_HOSTILE_VALUES=(
  'refs/pull/0/merge'
  'refs/pull/../merge'
  # shellcheck disable=SC2016  # intentionally literal command syntax for argv testing
  'refs/pull/123/merge$(touch '"$MARKER"')'
  'refs/pull/123/merge`touch '"$MARKER"'`'
  $'refs/pull/123/merge\nrefs/heads/main'
)
for value in "${PR_HOSTILE_VALUES[@]}"; do
  expect_rejected rl_assert_ci_pr_ref "$value"
done

echo "[ 3/6] validated values remain safe through argv"
bash -c 'source "$1"; rl_assert_semver "$2"; rl_assert_full_sha "$3"; rl_assert_positive_integer "$4"; rl_assert_github_login "$5"; rl_assert_ci_ref "$6"; rl_assert_task_definition_arn "$7"' _ \
  "$INPUTS" 1.2.3 a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4 123 djimi refs/heads/feature/security-hardening \
  arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:7
[ ! -e "$MARKER" ] || fail "safe argv validation created a marker"

bash -c 'source "$1"; rl_assert_ci_pr_ref "$2"' _ \
  "$INPUTS" refs/pull/123/merge
[ ! -e "$MARKER" ] || fail "safe pull-request ref validation created a marker"

echo "[ 4/6] promotion handoff has a dedicated stateful offline test"
[ -x "$REPO_ROOT/tests/scripts/promotion_handoff_test.sh" ] || fail "promotion_handoff_test.sh is missing or not executable"

echo "[ 5/6] strict YAML duplicate regression is active"
# Parse the rollback workflow once more with a duplicate-rejecting loader. This
# keeps the regression assertion close to the exact workflow that had the
# duplicate `env` mapping.
python3 - "$REPO_ROOT/.github/workflows/rollback-release.yml" <<'PY' || exit 1
import sys
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError


class Loader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ConstructorError("mapping", node.start_mark, "duplicate key", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
yaml.load(Path(sys.argv[1]).read_text(encoding="utf-8"), Loader=Loader)
PY

echo "[ 6/6] stateful handoff executes the real deployment path"
bash "$REPO_ROOT/tests/scripts/promotion_handoff_test.sh"

echo "CI security contract passed."
