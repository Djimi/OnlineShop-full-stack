#!/usr/bin/env bash
# Offline verification gate for Pass 3, subphase 3.6 — owner-approved rollback.
#
# The live half of the gate — the real owner-approved rollback against live
# AWS/GitHub, the real `production` Environment approval, real ECR/ECS/S3/
# CloudFront mutations and read-backs — is deferred to the consolidated Pass 3
# verification pass and is NOT claimed here. This gate proves the offline
# implementation: the `release_contract.rollback` decision layer, the
# `rollback-release-greenfield.yml` static checks, the rollback shell scripts
# against a stateful AWS stub, the mandatory profile/region + no-secrets +
# no-tag-minting scans, and ruff/shellcheck/`git diff --check`.
set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
FX="$RELEASE/fixtures/rollback"
ROLLBACK_WF="$REPO_ROOT/.github/workflows/rollback-release-greenfield.yml"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_success() {
  "$@" >/dev/null 2>&1 || fail "expected success: $*"
}

assert_failure() {
  if "$@" >/dev/null 2>&1; then
    fail "expected failure: $*"
  fi
}

assert_contains() {
  local value="$1" expected="$2"
  [[ "$value" == *"$expected"* ]] || fail "expected output to contain: $expected"
}

expect_issue_code() {
  local value="$1" expected="$2"
  printf '%s' "$value" | jq -e --arg code "$expected" '.issues[] | select(.code == $code)' >/dev/null \
    || fail "expected issue code $expected not present in: $value"
}

run_rollback() {
  PYTHONPATH="$RELEASE/src" python3 -m release_contract.rollback "$@"
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
echo "[ 1/9] Python syntax + unit tests"
python3 -m py_compile "$RELEASE"/src/release_contract/*.py "$RELEASE"/tests/*.py || fail "Python syntax check failed"
(
  cd "$RELEASE" && PYTHONPATH="$RELEASE/src" python3 -m unittest discover -s tests
) || fail "Python validation tests failed"

# ---------------------------------------------------------------------------
echo "[ 2/9] rollback decision-layer CLI against fixtures"
# dispatch
OUT=$(run_rollback dispatch --version 1.1.0)
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "dispatch valid version must pass"
OUT=$(run_rollback dispatch --version 1.1.0-beta 2>&1) && fail "dispatch invalid version must fail"
expect_issue_code "$OUT" "INVALID_VERSION"

# select
OUT=$(run_rollback select --index "$FX/index.json" --observed "$FX/observed-ok.json" --version 1.1.0)
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "select valid target must pass"
OUT=$(run_rollback select --index "$FX/index.json" --observed "$FX/observed-current.json" --version 1.1.0 2>&1) \
  && fail "select current release must fail"
expect_issue_code "$OUT" "TARGET_IS_CURRENT"
OUT=$(run_rollback select --index "$FX/index.json" --observed "$FX/observed-missing.json" --version 1.1.0 2>&1) \
  && fail "select with a missing artifact must fail"
expect_issue_code "$OUT" "TARGET_ARTIFACT_MISSING"
OUT=$(run_rollback select --index "$FX/index.json" --observed "$FX/observed-tampered.json" --version 1.1.0 2>&1) \
  && fail "select with a tampered artifact must fail"
expect_issue_code "$OUT" "TARGET_ARTIFACT_MISMATCH"
OUT=$(run_rollback select --index "$FX/index-candidate.json" --observed "$FX/observed-ok.json" --version 1.1.0 2>&1) \
  && fail "select a non-official release must fail"
expect_issue_code "$OUT" "TARGET_NOT_OFFICIAL"
OUT=$(run_rollback select --index "$FX/index.json" --observed "$FX/observed-ok.json" --version 9.9.9 2>&1) \
  && fail "select an unknown release must fail"
expect_issue_code "$OUT" "TARGET_NOT_FOUND"
# The rollback window (latest 10 complete official sets) is proven in the unit
# tests with a generated 12-release index; here the top-2 fixtures stay green.
OUT=$(run_rollback select --index "$FX/index.json" --observed "$FX/observed-ok.json" --version 1.2.1 2>&1) \
  && fail "selecting the currently running release must fail"
expect_issue_code "$OUT" "TARGET_IS_CURRENT"

# schema
OUT=$(run_rollback schema --state "$FX/schema-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "schema ok must pass"
OUT=$(run_rollback schema --state "$FX/schema-unreviewed.json" 2>&1) && fail "unreviewed schema change must fail"
expect_issue_code "$OUT" "SCHEMA_COMPATIBILITY_UNREVIEWED"
OUT=$(run_rollback schema --state "$FX/schema-reviewed.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "reviewed schema change must pass"

# frontend-restore
OUT=$(run_rollback frontend-restore --plan "$FX/frontend-restore-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "frontend restore plan must pass"
OUT=$(run_rollback frontend-restore --plan "$FX/frontend-restore-unsafe.json" 2>&1) && fail "deleteFlag restore must fail"
expect_issue_code "$OUT" "FRONTEND_DELETE_FORBIDDEN"
OUT=$(run_rollback frontend-restore --plan "$FX/frontend-restore-no-prefix.json" 2>&1) && fail "missing prefix must fail"
expect_issue_code "$OUT" "FRONTEND_PREFIX_MISSING"
OUT=$(run_rollback frontend-restore --plan "$FX/frontend-restore-no-invalidate.json" 2>&1) && fail "missing invalidation must fail"
expect_issue_code "$OUT" "FRONTEND_INVALIDATION_MISSING"

# result
OUT=$(run_rollback result --state "$FX/result-ok.json")
jq -e '.valid == true and .action == "write"' <<<"$OUT" >/dev/null || fail "result ok must write"
OUT=$(run_rollback result --state "$FX/result-resume.json")
jq -e '.valid == true and .action == "resume"' <<<"$OUT" >/dev/null || fail "idempotent resume must pass"
OUT=$(run_rollback result --state "$FX/result-conflict.json" 2>&1) && fail "result conflict must fail"
expect_issue_code "$OUT" "RESULT_CONFLICT"
OUT=$(run_rollback result --state "$FX/result-invalid.json" 2>&1) && fail "invalid result must fail"
expect_issue_code "$OUT" "RESULT_NOT_VERIFIED"
expect_issue_code "$OUT" "RESULT_SAME_RELEASE"
expect_issue_code "$OUT" "RESULT_AUDIT_NOT_ANNOTATED"

# Reused promotion rules stay wired through the rollback CLI (snapshot/plan/
# verify/compensate).
OUT=$(run_rollback snapshot --snapshot "$FX/snapshot.json" --manifest "$FX/official-1.1.0.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "rollback snapshot must pass"
OUT=$(run_rollback plan --plan "$RELEASE/fixtures/promotion/plan-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "rollback plan must pass"
OUT=$(run_rollback verify --observed "$FX/verify-ok.json" --manifest "$FX/deployment-manifest.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "rollback verify must pass"
OUT=$(run_rollback verify --observed "$FX/verify-drift.json" --manifest "$FX/deployment-manifest.json" 2>&1) \
  && fail "rollback verify with drift must fail"
expect_issue_code "$OUT" "RUNNING_DIGEST_MISMATCH"
OUT=$(run_rollback compensate --snapshot "$FX/snapshot.json" --changed "$RELEASE/fixtures/promotion/changed-all.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "rollback compensate must pass"

# ---------------------------------------------------------------------------
echo "[ 3/9] rollback-release-greenfield.yml workflow static checks"
python3 - "$ROLLBACK_WF" "$RELEASE" <<'PY' || fail "rollback workflow YAML checks failed"
import re
import sys

import yaml

workflow_path, release_root = sys.argv[1], sys.argv[2]
with open(workflow_path, encoding="utf-8") as handle:
    wf = yaml.safe_load(handle)

problems = []

# Manual dispatch only, with the single version input. PyYAML parses the YAML
# `on:` trigger key as boolean True (YAML 1.1), so read it that way.
trigger = wf.get("on") or wf.get(True) or {}
dispatch_inputs = (trigger.get("workflow_dispatch") or {}).get("inputs", {}) if isinstance(trigger, dict) else {}
if "version" not in dispatch_inputs:
    problems.append("rollback-release-greenfield.yml must take a version dispatch input")
if "requester" in dispatch_inputs or "schema_change" in dispatch_inputs or "migration_reviewed" in dispatch_inputs:
    problems.append("rollback-release-greenfield.yml must not take requester/schema_change/migration_reviewed inputs (they are engine decisions)")

# The production mutation job must use the protected production Environment
# and the shared non-cancelling production concurrency group.
jobs = wf.get("jobs", {})
rollback = jobs.get("rollback")
if rollback is None:
    problems.append("rollback job missing")
else:
    env = rollback.get("environment")
    env_name = env if isinstance(env, str) else (env or {}).get("name")
    if env_name != "production":
        problems.append("rollback job must use the production Environment")
    if not rollback.get("timeout-minutes"):
        problems.append("rollback job must set a timeout")
    rollback_concurrency = rollback.get("concurrency", {})
    if rollback_concurrency.get("group") != "production":
        problems.append("rollback job must hold the shared production concurrency group")
    if rollback_concurrency.get("cancel-in-progress") is not False:
        problems.append("production concurrency must set cancel-in-progress: false")

# The pre-approval preflight job is read-only: it must not hold the production
# concurrency group and must not be behind the production Environment.
preflight = jobs.get("preflight")
if preflight is None:
    problems.append("preflight job missing")
else:
    if preflight.get("concurrency"):
        problems.append("the pre-approval preflight job must not hold the production concurrency group")
    preflight_env = preflight.get("environment")
    preflight_env_name = preflight_env if isinstance(preflight_env, str) else (preflight_env or {}).get("name")
    if preflight_env_name == "production":
        problems.append("the pre-approval preflight job must not be behind the production Environment")

# Every job that assumes the AWS role must be able to mint an OIDC token: a
# job-level `permissions:` block REPLACES the workflow-level permissions, so a
# configure-aws-credentials job needs `id-token: write` declared explicitly.
workflow_permissions = wf.get("permissions") or {}
for job_name, job in jobs.items():
    if not isinstance(job, dict):
        continue
    steps = job.get("steps") or []
    assumes_aws = any(
        isinstance(step, dict)
        and str(step.get("uses", "")).startswith("aws-actions/configure-aws-credentials@")
        for step in steps
    )
    if not assumes_aws:
        continue
    job_permissions = job.get("permissions")
    if isinstance(job_permissions, dict):
        if job_permissions.get("id-token") != "write":
            problems.append(
                job_name + " job assumes the AWS role but its permissions block "
                "lacks id-token: write"
            )
    elif workflow_permissions.get("id-token") != "write":
        problems.append(
            job_name + " job assumes the AWS role but the workflow permissions "
            "lack id-token: write"
        )

# No rebuild and no tag minting: rollback consumes existing official bytes.
text = str(wf)
for forbidden in ("build-push-action", "publish-candidate-image.sh", "promote-image-digest.sh",
                  "gh release create", "put-image", "ecr:PutImage", "batch-get-image"):
    if forbidden in text:
        problems.append(f"rollback-release-greenfield.yml must never {forbidden}")

# The full preflight is repeated post-approval (time-of-check race closure):
# rollback execute repeats it internally against the fresh snapshot.
rollback_text = str(rollback)
if "rollback execute" not in rollback_text:
    problems.append("rollback job must run the approved rollback execute (which repeats the full preflight)")
if "delivery.cli rollback execute" not in rollback_text:
    problems.append("rollback job must execute the rollback through the engine")

# The pre-approval preflight job is read-only: it runs the engine preflight but
# never mutates (no execute/restore).
preflight_text = str(preflight)
if "delivery.cli rollback preflight" not in preflight_text:
    problems.append("the pre-approval preflight job must run the engine rollback preflight")
for forbidden in ("rollback execute", "delivery.cli recover", "compensate"):
    if forbidden in preflight_text:
        problems.append(f"the pre-approval preflight job must be read-only (no {forbidden})")

# Bring-up guard: the greenfield workflow must refuse while either legacy
# production-mutation workflow still declares its path (OP-CUT-01). The guard
# must exist in preflight, rollback, and compensate.
for label, block in (("preflight", preflight_text), ("rollback", rollback_text),
                     ("compensate", str(jobs.get("compensate") or {}))):
    if "finalize-release.sh" not in block or "deploy-rollback.sh" not in block:
        problems.append(f"{label} must run the legacy-mutation bring-up guard (both markers)")

# approvedBy is derived from the environment-approval evidence via the
# actions/runs/{run}/approvals API, never from github.actor or user input.
if "approvals" not in rollback_text:
    problems.append("rollback job must derive approvedBy from actions/runs/{run}/approvals")
if re.search(r"approvedBy:\s*\$\{\{\s*github\.actor", text):
    problems.append("approvedBy must never be set from the run actor (github.actor)")
if "approved_at" not in rollback_text and "approvedAt" not in rollback_text:
    problems.append("rollback job must derive approvedAt from the approval evidence, never the runner clock")

# The pre-approval preflight report is consumed from the exact producing run
# (download-artifact pinned to this run). `str(wf)` is a Python repr where
# string values are quoted, so a literal "run-id: ${{ github.run_id }}" search
# can never match; read the parsed structure instead.
download_steps = [
    (job_name, step)
    for job_name, job in jobs.items()
    if isinstance(job, dict)
    for step in (job.get("steps") or [])
    if isinstance(step, dict)
    and str(step.get("uses", "")).startswith("actions/download-artifact@")
]
if "download-artifact" not in text or not download_steps:
    problems.append("the pre-approval preflight report must be consumed from the exact producing run")
for job_name, step in download_steps:
    download_with = step.get("with") or {}
    if download_with.get("run-id") != "${{ github.run_id }}":
        problems.append(
            "the " + job_name + " download-artifact step must be pinned to run-id: "
            "${{ github.run_id }} of this exact run"
        )

# Snapshot + full target set (backends -> gateway -> frontend) are engine
# responsibilities (delivery.cli): the workflow drives the engine and consumes
# the release manifest whose components include the frontend checksum.
if "delivery.cli snapshot production" not in rollback_text:
    problems.append("rollback job must snapshot pre-rollback state with the engine snapshot")
if "--manifest preflight/release-manifest.json" not in rollback_text:
    problems.append("rollback job must consume the target release manifest (complete component set incl. frontend)")

# The compensate job is automatic (not approval-gated) and derives the changed
# array from the honest per-component conclusions of the rollback result —
# including the frontend when it completed — never hardcoded or guessed.
compensate = jobs.get("compensate")
if compensate is None:
    problems.append("compensate job missing")
else:
    if "delivery.cli recover" not in str(compensate):
        problems.append("compensate job must call the recover engine")
    if "changed-count" not in str(compensate) or "needs.rollback.outputs" not in str(compensate):
        problems.append("compensate job must run only when the rollback job reports completed components")
    if "conclusion == \"passed\"" not in str(compensate):
        problems.append("compensate job must derive changed components only from passed conclusions")
    comp_env = compensate.get("environment")
    comp_env_name = comp_env if isinstance(comp_env, str) else (comp_env or {}).get("name")
    if comp_env_name == "production":
        problems.append("compensate job must not be approval-gated by the production Environment (automatic restore)")

# Release-critical Actions pinned by full commit SHA.
sha_ref = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40})$")
for name, job in jobs.items():
    if not isinstance(job, dict):
        continue
    for step in job.get("steps", []) if isinstance(job.get("steps"), list) else []:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses", "")
        if not uses or uses.startswith("./"):
            continue
        if not sha_ref.match(uses):
            problems.append(f"action not pinned by SHA: {uses} (job {name})")

if problems:
    print("\n".join(problems))
    sys.exit(1)
PY

# ---------------------------------------------------------------------------
# Stateful AWS + gh stubs for the shell-script checks below.
# ---------------------------------------------------------------------------
write_stub_clis() {
  mkdir -p "$TMP/bin"
  cat > "$TMP/bin/aws" <<'PY'
#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import sys

state_path = os.environ["STUB_STATE"]
calls_path = os.environ["STUB_CALLS"]
state = json.load(open(state_path, encoding="utf-8"))
args = sys.argv[1:]


def record(line):
    with open(calls_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def persist():
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def arg(name):
    for index, item in enumerate(args):
        if item == name and index + 1 < len(args):
            return args[index + 1]
    return None


def query():
    return arg("--query") or ""


def emit(value):
    print(json.dumps(value, sort_keys=True))


def text(value):
    print(value)


def fail_not_found():
    print("An error occurred (NotFound): not found", file=sys.stderr)
    sys.exit(255)


pos = [a for a in args if not a.startswith("-") and a != "--json"]
service = pos[0] if len(pos) > 0 else ""
sub = pos[1] if len(pos) > 1 else ""
q = query()

if service == "sts" and sub == "get-caller-identity":
    record("sts get-caller-identity")
    text(state["identity"])
elif service == "ecr" and sub == "describe-images":
    repo = arg("--repository-name") or ""
    tag = ""
    for index, item in enumerate(args):
        if item.startswith("imageTag="):
            tag = item[len("imageTag="):]
    record(f"ecr describe-images {repo} {tag}")
    digest = state.get("ecr", {}).get(repo, {}).get("tags", {}).get(tag)
    if not digest:
        print("")
    else:
        text(digest)
elif service == "ecs" and sub == "describe-services":
    record("ecs describe-services")
    services = state["ecs"]["services"]
    requested = []
    for index, item in enumerate(args):
        if item == "--services":
            index += 1
            while index < len(args) and not args[index].startswith("--"):
                requested.extend(x for x in args[index].split() if x)
                index += 1
    matched = [s for s in services if not requested or s.get("serviceName") in requested]
    if "services[0]" in q:
        emit(matched[0] if matched else {})
    elif "services[].{serviceName" in q:
        emit([{"serviceName": s.get("serviceName"), "taskDefinition": s.get("taskDefinition")} for s in matched])
    else:
        emit(matched)
elif service == "ecs" and sub == "list-tasks":
    svc = arg("--service-name") or ""
    record(f"ecs list-tasks {svc}")
    if svc:
        emit(state["ecs"].get("taskArns", {}).get(svc, []))
    else:
        all_arns = [a for arns in state["ecs"].get("taskArns", {}).values() for a in arns]
        emit(all_arns)
elif service == "ecs" and sub == "describe-tasks":
    record("ecs describe-tasks")
    tasks = state["ecs"]["tasks"]
    if "tasks[0]" in q:
        emit(tasks[0] if tasks else {})
    else:
        emit(tasks)
elif service == "ecs" and sub == "describe-task-definition":
    td = arg("--task-definition") or ""
    record(f"ecs describe-task-definition {td}")
    entry = state["ecs"].get("taskDefinitions", {}).get(td)
    if entry is None:
        fail_not_found()
    if "containerDefinitions[0].image" in q:
        text(entry["image"])
    else:
        emit(entry)
elif service == "ecs" and sub == "register-task-definition":
    record("ecs register-task-definition")
    text(state["ecs"].get("nextTaskDefinitionArn", "arn:aws:ecs:eu-north-1:799111666795:task-definition/x:99"))
elif service == "ecs" and sub == "wait":
    record("ecs wait")
    text("")
elif service == "s3api" and sub == "get-object":
    key = arg("--key") or ""
    record(f"s3api get-object {key}")
    content = state["frontend"].get(key)
    out = [a for a in args if not a.startswith("-")][-1]
    if content is None:
        fail_not_found()
    if isinstance(content, dict):
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(content, handle)
    else:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(str(content))
elif service == "s3api" and sub == "head-object":
    key = arg("--key") or ""
    checksum_mode = arg("--checksum-mode")
    record(f"s3api head-object {key} checksum-mode={checksum_mode or '<missing>'}")
    if checksum_mode != "ENABLED":
        fail_not_found()
    frontend = state["frontend"]
    if "headChecksumSha256" in frontend:
        checksum = frontend["headChecksumSha256"]
    else:
        checksum_hex = frontend.get("checksums", {}).get(key)
        if checksum_hex is None:
            checksum = None
        else:
            try:
                checksum = base64.b64encode(bytes.fromhex(checksum_hex)).decode("ascii")
            except ValueError:
                checksum = checksum_hex
    emit({
        "checksum": checksum,
        "checksumType": frontend.get("headChecksumType", "FULL_OBJECT"),
    })
elif service == "s3" and sub == "cp":
    checksum_algorithm = arg("--checksum-algorithm")
    record(f"s3 cp checksum-algorithm={checksum_algorithm or '<missing>'}")
    uri = [a for a in args if a.startswith("s3://")]
    if uri:
        uri_index = args.index(uri[0])
        src = args[uri_index - 1] if uri_index > 0 else ""
        bucket, key = uri[0][len("s3://"):].split("/", 1)
        if src:
            with open(src, "rb") as handle:
                raw = handle.read()
            content = raw.decode("utf-8")
            try:
                content = json.loads(content)
            except ValueError:
                pass
            frontend = state.setdefault("frontend", {})
            frontend[key] = content
            checksums = frontend.setdefault("checksums", {})
            if checksum_algorithm == "SHA256":
                checksums[key] = hashlib.sha256(raw).hexdigest()
            else:
                checksums.pop(key, None)
            frontend.pop("headChecksumSha256", None)
            persist()
    text("")
elif service == "s3" and sub == "sync":
    record("s3 sync")
    text("")
elif service == "elbv2" and sub == "describe-target-health":
    record("elbv2 describe-target-health")
    emit(state["alb"]["targetHealth"])
elif service == "cloudfront" and sub == "create-invalidation":
    record("cloudfront create-invalidation")
    text("I123456789")
else:
    record(f"unhandled {service} {sub}")
PY
  cat > "$TMP/bin/gh" <<'PY'
#!/usr/bin/env python3
import json
import os
import re
import sys

url = sys.argv[2] if len(sys.argv) > 2 else ""
data = json.load(open(os.environ["STUB_GH_DATA"], encoding="utf-8"))

if re.search(r"/releases/(\d+)/assets", url):
    print(json.dumps({}))
elif "/releases" in url and "assets" not in url:
    print(json.dumps(data.get("releases", [])))
elif "/approvals" in url:
    print(json.dumps([
        {"state": "approved", "environments": [{"name": "production"}],
         "user": {"login": "djimi"}, "comment": ""}
    ]))
else:
    print(json.dumps({}))
PY
  chmod +x "$TMP/bin/aws" "$TMP/bin/gh"
}

# A task-definition entry that passes the hardening validator once the image is
# replaced with a digest pin (sanitize-task-definition.sh handles the image).
# Usage: td_entry <state.json> <task-definition-arn> <image>
td_entry() {
  python3 - "$1" "$2" "$3" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
arn, image = sys.argv[2], sys.argv[3]
name = arn.split("/")[-1].split(":")[0]
container = {"onlineshop-auth": "auth", "onlineshop-items": "items", "onlineshop-api-gateway": "api-gateway"}.get(name, name)
port = {"onlineshop-auth": 9001, "onlineshop-items": 9000, "onlineshop-api-gateway": 10000}.get(name, 10000)
entry = {
    "family": name,
    "taskDefinitionArn": arn,
    "containerDefinitions": [
        {
            "name": container,
            "image": image,
            "imageDigest": "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0",
            "versionConsistency": "enabled",
            "healthCheck": {"command": ["CMD-SHELL", "curl -f http://localhost/actuator/health || exit 1"]},
            "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/" + name, "awslogs-region": "eu-north-1"}},
            "portMappings": [{"name": name + "-port", "containerPort": port}],
            "stopTimeout": 30,
            "environment": [],
            "secrets": [],
            "essential": True,
        }
    ],
    "cpu": "512",
    "memory": "1024",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "executionRoleArn": "arn:aws:iam::799111666795:role/ecsTaskExecutionRole",
    "taskRoleArn": "arn:aws:iam::799111666795:role/" + name + "-task",
    "image": image,
}
state["ecs"]["taskDefinitions"][arn] = entry
json.dump(state, open(sys.argv[1], "w"), indent=2, sort_keys=True)
PY
}

# Pre-rollback production state: current = 1.2.1 running on its task
# definitions, with the 1.1.0 immutable prefix retained (the rollback source).
stub_state_rollback() {
  python3 - "$TMP/state.json" "$TMP/gh-data.json" <<'PY'
import json
import sys

sha121 = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
sha110 = "deadbeefcafebabe1234567890abcdef12345678"
fe121 = "b9debb6b25ee6e6e534f7738d27f53f4153dbf361f097336741ae9fb54939ee4"
fe110 = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
state = {
    "identity": "799111666795",
    "ecr": {
        "onlineshop-auth": {"tags": {"release-1.2.1": "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0", "release-1.1.0": "sha256:1111111111111111111111111111111111111111111111111111111111111111"}},
        "onlineshop-items": {"tags": {"release-1.2.1": "sha256:1a78d43d2aaaca3cafe41539f5fe211f4ef45152a1fa6af5b85006e93b019452", "release-1.1.0": "sha256:2222222222222222222222222222222222222222222222222222222222222222"}},
        "onlineshop-api-gateway": {"tags": {"release-1.2.1": "sha256:31f9bfdf4d15f460167ba2b7ce9ada23278be1a9f115f06c01e5242901e8948e", "release-1.1.0": "sha256:3333333333333333333333333333333333333333333333333333333333333333"}},
    },
    "ecs": {
        "services": [
            {"serviceName": "onlineshop-auth", "taskDefinition": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5", "desiredCount": 1,
             "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1, "base": 1}],
             "deployments": [{"id": "ecs-svc/1", "rolloutState": "COMPLETED"}], "loadBalancers": []},
            {"serviceName": "onlineshop-items", "taskDefinition": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:4", "desiredCount": 1,
             "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1, "base": 1}],
             "deployments": [{"id": "ecs-svc/2", "rolloutState": "COMPLETED"}], "loadBalancers": []},
            {"serviceName": "onlineshop-api-gateway", "taskDefinition": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:13", "desiredCount": 1,
             "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1, "base": 1}],
             "deployments": [{"id": "ecs-svc/3", "rolloutState": "COMPLETED"}], "loadBalancers": []},
        ],
        "taskArns": {
            "onlineshop-auth": ["arn:aws:ecs:eu-north-1:799111666795:task/t1"],
            "onlineshop-items": ["arn:aws:ecs:eu-north-1:799111666795:task/t2"],
            "onlineshop-api-gateway": ["arn:aws:ecs:eu-north-1:799111666795:task/t3"],
        },
        "tasks": [
            {"taskArn": "t1", "taskDefinitionArn": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5", "lastStatus": "RUNNING",
             "containers": [{"name": "auth", "imageDigest": "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"}]},
            {"taskArn": "t2", "taskDefinitionArn": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:4", "lastStatus": "RUNNING",
             "containers": [{"name": "items", "imageDigest": "sha256:1a78d43d2aaaca3cafe41539f5fe211f4ef45152a1fa6af5b85006e93b019452"}]},
            {"taskArn": "t3", "taskDefinitionArn": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:13", "lastStatus": "RUNNING",
             "containers": [{"name": "api-gateway", "imageDigest": "sha256:31f9bfdf4d15f460167ba2b7ce9ada23278be1a9f115f06c01e5242901e8948e"}]},
        ],
        "taskDefinitions": {},
        "nextTaskDefinitionArn": "arn:aws:ecs:eu-north-1:799111666795:task-definition/x:99",
    },
    "frontend": {
        "release.json": {"version": "1.2.1", "sourceSha": sha121, "frontendSha256": fe121},
        "_releases/v1.2.1/release.json": {"version": "1.2.1", "sourceSha": sha121, "frontendSha256": fe121},
        "_releases/v1.1.0/release.json": {"version": "1.1.0", "sourceSha": sha110, "frontendSha256": fe110},
        "_releases/v1.1.0/index.html": "<html>v1.1.0</html>",
    },
    "alb": {"targetHealth": [{"target": {"id": "10.0.0.1"}, "targetHealth": {"state": "healthy"}}]},
}
json.dump(state, open(sys.argv[1], "w"), indent=2, sort_keys=True)

gh_data = {
    "releases": [
        {"tag_name": "v1.2.1", "draft": False,
         "assets": [{"name": "release-manifest.json", "id": 101}]},
        {"tag_name": "v1.1.0", "draft": False,
         "assets": [{"name": "release-manifest.json", "id": 102}]},
    ],
    "approvals": [{"state": "approved", "environments": [{"name": "production"}], "user": {"login": "djimi"}}],
}
json.dump(gh_data, open(sys.argv[2], "w"), indent=2, sort_keys=True)
PY
  # The snapshot fixture records the pre-rollback current TDs (auth:6 etc.);
  # register full valid task definitions for deploy-rollback.sh --dry-run.
  td_entry "$TMP/state.json" "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:6" "old-img"
  td_entry "$TMP/state.json" "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:5" "old-img"
  td_entry "$TMP/state.json" "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:14" "old-img"
  : > "$TMP/calls.txt"
}

# Post-rollback production state: the target (1.1.0) digests running on the new
# rollback task definitions, matching the deployment-manifest fixture.
stub_state_rolled() {
  stub_state_rollback
  python3 - "$TMP/state.json" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
tds = {
    "onlineshop-auth": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:7",
    "onlineshop-items": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:6",
    "onlineshop-api-gateway": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:15",
}
digests = {
    "onlineshop-auth": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "onlineshop-items": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
    "onlineshop-api-gateway": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
}
containers = {"onlineshop-auth": "auth", "onlineshop-items": "items", "onlineshop-api-gateway": "api-gateway"}
for service in tds:
    for s in state["ecs"]["services"]:
        if s["serviceName"] == service:
            s["taskDefinition"] = tds[service]
    for t in state["ecs"]["tasks"]:
        if "onlineshop-auth" in service and "auth" in t.get("taskArn", ""):
            pass
state["ecs"]["tasks"] = [
    {"taskArn": "t1", "taskDefinitionArn": tds["onlineshop-auth"], "lastStatus": "RUNNING",
     "containers": [{"name": "auth", "imageDigest": digests["onlineshop-auth"]}]},
    {"taskArn": "t2", "taskDefinitionArn": tds["onlineshop-items"], "lastStatus": "RUNNING",
     "containers": [{"name": "items", "imageDigest": digests["onlineshop-items"]}]},
    {"taskArn": "t3", "taskDefinitionArn": tds["onlineshop-api-gateway"], "lastStatus": "RUNNING",
     "containers": [{"name": "api-gateway", "imageDigest": digests["onlineshop-api-gateway"]}]},
]
state["frontend"]["release.json"] = {
    "version": "1.1.0", "sourceSha": "deadbeefcafebabe1234567890abcdef12345678",
    "frontendSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
}
json.dump(state, open(sys.argv[1], "w"), indent=2, sort_keys=True)
PY
  : > "$TMP/calls.txt"
}

write_stub_clis
export PATH="$TMP/bin:$PATH"
export STUB_STATE="$TMP/state.json"
export STUB_CALLS="$TMP/calls.txt"
export STUB_GH_DATA="$TMP/gh-data.json"
export GITHUB_REPOSITORY=Djimi/OnlineShop-full-stack
export GITHUB_TOKEN=t

# ---------------------------------------------------------------------------
echo "[ 4/9] rollback-preflight.sh (offline index/observed inputs)"
stub_state_rollback
OUT=$(bash "$RELEASE/bin/rollback-preflight.sh" \
  --version 1.1.0 \
  --index "$FX/index.json" \
  --observed "$FX/observed-ok.json" \
  --schema-change absent --migration-reviewed false \
  --target-manifest "$TMP/target.json" \
  --profile dpm-profile --region eu-north-1)
jq -e '.release.version == "1.1.0"' "$TMP/target.json" >/dev/null \
  || fail "rollback-preflight must emit the validated target manifest"
# The pre-approval summary shows current versus target identities, digests,
# task definitions, frontend checksum, source SHAs, and the db warning.
assert_contains "$OUT" "to:      version=1.1.0 gitTag=v1.1.0 sourceSha=deadbeefcafebabe1234567890abcdef12345678"
assert_contains "$OUT" "to:      auth=sha256:1111111111111111111111111111111111111111111111111111111111111111 items=sha256:2222222222222222222222222222222222222222222222222222222222222222 gateway=sha256:3333333333333333333333333333333333333333333333333333333333333333"
assert_contains "$OUT" "to:      taskDefs auth=arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:3 items=arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:3 gateway=arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:11"
assert_contains "$OUT" "from:    version=1.2.1 sourceSha=a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
assert_contains "$OUT" "from:    digests auth=sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
assert_contains "$OUT" "db:      no schema change declared"
# Unreviewed schema change fails closed.
OUT=$(bash "$RELEASE/bin/rollback-preflight.sh" \
  --version 1.1.0 \
  --index "$FX/index.json" \
  --observed "$FX/observed-ok.json" \
  --schema-change present --migration-reviewed false \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "unreviewed schema change must fail the preflight"
assert_contains "$OUT" "SCHEMA_COMPATIBILITY_UNREVIEWED"
# A target outside the rollback window / with missing artifacts fails closed.
OUT=$(bash "$RELEASE/bin/rollback-preflight.sh" \
  --version 1.1.0 \
  --index "$FX/index.json" \
  --observed "$FX/observed-missing.json" \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "missing artifact must fail the preflight"
assert_contains "$OUT" "TARGET_ARTIFACT_MISSING"
# Wrong identity account fails the preflight.
python3 - "$TMP/state.json" <<PY
import json, sys
with open(sys.argv[1], encoding="utf-8") as h:
    state = json.load(h)
state["identity"] = "000000000000"
with open(sys.argv[1], "w", encoding="utf-8") as h:
    json.dump(state, h, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/rollback-preflight.sh" \
  --version 1.1.0 \
  --index "$FX/index.json" \
  --observed "$FX/observed-ok.json" \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "wrong identity must fail the preflight"
assert_contains "$OUT" "identity preflight failed"
stub_state_rollback

# ---------------------------------------------------------------------------
echo "[ 5/9] deploy-rollback.sh dry-run (plan + sanitize, no mutation)"
stub_state_rollback
OUT=$(bash "$RELEASE/bin/deploy-rollback.sh" \
  --manifest "$FX/official-1.1.0.json" \
  --snapshot "$FX/snapshot.json" \
  --ecr-registry 799111666795.dkr.ecr.eu-north-1.amazonaws.com \
  --dry-run \
  --profile dpm-profile --region eu-north-1 2>&1)
assert_contains "$OUT" "dry-run"
if grep -q "ecs register-task-definition" "$TMP/calls.txt"; then
  fail "deploy-rollback.sh --dry-run must not register task definitions"
fi
if grep -Eq ' (put|create|update|delete)-' "$TMP/calls.txt"; then
  fail "deploy-rollback.sh --dry-run must not mutate"
fi
# Identity preflight runs before anything else.
grep -q "sts get-caller-identity" "$TMP/calls.txt" || fail "deploy-rollback.sh must run the identity preflight"

# ---------------------------------------------------------------------------
echo "[ 6/9] verify-rollback.sh (read-only, ok + drift)"
stub_state_rolled
assert_success bash "$RELEASE/bin/verify-rollback.sh" \
  --manifest "$FX/deployment-manifest.json" \
  --profile dpm-profile --region eu-north-1
# A drifted running digest fails closed.
python3 - "$TMP/state.json" <<PY
import json, sys
with open(sys.argv[1], encoding="utf-8") as h:
    state = json.load(h)
state["ecs"]["tasks"][0]["containers"][0]["imageDigest"] = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
with open(sys.argv[1], "w", encoding="utf-8") as h:
    json.dump(state, h, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/verify-rollback.sh" \
  --manifest "$FX/deployment-manifest.json" \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "verify-rollback with drift must fail"
assert_contains "$OUT" "RUNNING_DIGEST_MISMATCH"
if grep -Eq ' (put|create|update|delete|register|run)-' "$TMP/calls.txt"; then
  fail "verify-rollback.sh must be read-only"
fi
# A paused production environment (no running tasks) cannot be verified as a
# successful rollback — the same fail-closed rule as forward promotion
# (RUNNING_TASKS_MISSING), never fabricated success.
stub_state_rollback
python3 - "$TMP/state.json" <<PY
import json, sys
with open(sys.argv[1], encoding="utf-8") as h:
    state = json.load(h)
state["ecs"]["taskArns"] = {}
state["ecs"]["tasks"] = []
with open(sys.argv[1], "w", encoding="utf-8") as h:
    json.dump(state, h, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/verify-rollback.sh" \
  --manifest "$FX/deployment-manifest.json" \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "verify-rollback on a paused environment must fail closed"
assert_contains "$OUT" "RUNNING_TASKS_MISSING"

# ---------------------------------------------------------------------------
echo "[ 7/9] restore-frontend.sh (restore live root from the immutable prefix)"
stub_state_rollback
assert_success bash "$RELEASE/bin/restore-frontend.sh" \
  --manifest "$FX/official-1.1.0.json" \
  --bucket onlineshop-frontend-799111666795 \
  --distribution EPS8MI3FV3B7X \
  --profile dpm-profile --region eu-north-1
jq -e '.frontend["release.json"].version == "1.1.0"' "$TMP/state.json" >/dev/null \
  || fail "restore-frontend must re-point the live root to the target release"
RESTORED_INDEX_SHA=$(printf '%s' '<html>v1.1.0</html>' | sha256sum | awk '{print $1}')
jq -e --arg sha "$RESTORED_INDEX_SHA" '
  (.frontend.checksums["release.json"] | type == "string" and test("^[0-9a-f]{64}$")) and
  .frontend.checksums["index.html"] == $sha
' "$TMP/state.json" >/dev/null \
  || fail "restore-frontend must establish SHA-256 metadata for the live marker and index"
EXPECTED_INDEX_CHECKSUM=$(python3 - "$RESTORED_INDEX_SHA" <<'PY'
import base64
import sys

print(base64.b64encode(bytes.fromhex(sys.argv[1])).decode("ascii"))
PY
)
LIVE_INDEX_HEAD=$(aws s3api head-object --profile dpm-profile --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795 --key index.html \
  --checksum-mode ENABLED \
  --query '{checksum: ChecksumSHA256, checksumType: ChecksumType}' --output json)
printf '%s' "$LIVE_INDEX_HEAD" | jq -e --arg checksum "$EXPECTED_INDEX_CHECKSUM" \
  '.checksum == $checksum and .checksumType == "FULL_OBJECT"' >/dev/null \
  || fail "restore-frontend must leave a valid live ChecksumSHA256"
[[ "$(grep -c 's3 cp checksum-algorithm=SHA256' "$TMP/calls.txt")" -eq 2 ]] \
  || fail "restore-frontend must request SHA-256 for both live-root writes"
grep -q "cloudfront create-invalidation" "$TMP/calls.txt" \
  || fail "restore-frontend must invalidate the SPA entry paths"
if grep -q -- "--delete" "$TMP/calls.txt"; then
  fail "restore-frontend must never use --delete"
fi
# The stub must not retain stale checksum metadata when a later overwrite omits
# the checksum request; this makes a missing --checksum-algorithm catchable.
printf '%s' '<html>omitted-checksum</html>' > "$TMP/omitted-index.html"
aws s3 cp --profile dpm-profile --region eu-north-1 \
  "$TMP/omitted-index.html" "s3://onlineshop-frontend-799111666795/index.html" \
  --content-type text/html >/dev/null
OUT=$(aws s3api head-object --profile dpm-profile --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795 --key index.html \
  --checksum-mode ENABLED --output json)
printf '%s' "$OUT" | jq -e '.checksum == null' >/dev/null \
  || fail "rollback stub must remove checksum metadata when SHA-256 is omitted"
# Dry-run must not mutate.
stub_state_rollback
OUT=$(bash "$RELEASE/bin/restore-frontend.sh" \
  --manifest "$FX/official-1.1.0.json" \
  --bucket onlineshop-frontend-799111666795 \
  --distribution EPS8MI3FV3B7X \
  --dry-run \
  --profile dpm-profile --region eu-north-1 2>&1)
assert_contains "$OUT" "dry-run"
if grep -q "s3 cp" "$TMP/calls.txt"; then
  fail "restore-frontend.sh --dry-run must not mutate"
fi

# ---------------------------------------------------------------------------
echo "[ 8/9] record-rollback-result.sh (audit record + idempotent resume)"
stub_state_rollback
# The result record is JSON on stdout; diagnostics (including the decision
# action) go to stderr. Capture the streams separately.
OUT=$(bash "$RELEASE/bin/record-rollback-result.sh" \
  --manifest "$FX/deployment-manifest.json" \
  --snapshot "$FX/snapshot.json" \
  --run-id 123456791 \
  --workflow-url "https://github.com/Djimi/OnlineShop-full-stack/actions/runs/123456791" \
  --requester djimi --approver djimi \
  --outcome success \
  --profile dpm-profile --region eu-north-1 2>"$TMP/result.err")
assert_contains "$(cat "$TMP/result.err")" "action=write"
printf '%s' "$OUT" | jq -e '.result.from.version == "1.2.1" and .result.to.version == "1.1.0"' >/dev/null \
  || fail "record-rollback-result must record from/to releases"
printf '%s' "$OUT" | jq -e '.result.requester == "djimi" and .result.approver == "djimi" and .result.outcome == "success"' >/dev/null \
  || fail "record-rollback-result must record requester/approver/outcome"
printf '%s' "$OUT" | jq -e '.result.runId == 123456791 and (.result.workflowUrl | contains("123456791"))' >/dev/null \
  || fail "record-rollback-result must record the run id and workflow URL"
# Idempotent resume: the same record resumes instead of writing a conflict.
OUT=$(bash "$RELEASE/bin/record-rollback-result.sh" \
  --manifest "$FX/deployment-manifest.json" \
  --snapshot "$FX/snapshot.json" \
  --run-id 123456791 \
  --workflow-url "https://github.com/Djimi/OnlineShop-full-stack/actions/runs/123456791" \
  --requester djimi --approver djimi \
  --outcome success \
  --existing-result "$FX/result-ok.json" \
  --profile dpm-profile --region eu-north-1 2>"$TMP/result.err")
assert_contains "$(cat "$TMP/result.err")" "action=resume"
# A conflicting existing record fails closed.
OUT=$(bash "$RELEASE/bin/record-rollback-result.sh" \
  --manifest "$FX/deployment-manifest.json" \
  --snapshot "$FX/snapshot.json" \
  --run-id 123456791 \
  --workflow-url "https://github.com/Djimi/OnlineShop-full-stack/actions/runs/123456791" \
  --requester djimi --approver djimi \
  --outcome success \
  --existing-result "$FX/result-conflict.json" \
  --profile dpm-profile --region eu-north-1 2>"$TMP/result.err") && fail "conflicting existing result must fail"
assert_contains "$(cat "$TMP/result.err")" "RESULT_CONFLICT"

# ---------------------------------------------------------------------------
echo "[ 9/9] Static scan: mandatory profile/region, no secrets, no tag minting"
for script in \
  "$RELEASE/bin/rollback-preflight.sh" \
  "$RELEASE/bin/deploy-rollback.sh" \
  "$RELEASE/bin/restore-frontend.sh" \
  "$RELEASE/bin/verify-rollback.sh" \
  "$RELEASE/bin/record-rollback-result.sh"; do
  # shellcheck disable=SC2016  # literal pattern
  grep -q 'AWS_ARGS=(--profile "$PROFILE" --region "$REGION")' "$script" \
    || fail "$(basename "$script") must default AWS_ARGS to dpm-profile/eu-north-1"
  # shellcheck disable=SC2094  # read-only scan; $script is only read, never written
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    # Match only real invocations (line-start or $() command substitution),
    # never `aws` mentioned inside an echo/diagnostic string.
    if [[ "$line" =~ ^[[:space:]]*aws[[:space:]] ]] || [[ "$line" =~ \$\(aws[[:space:]] ]]; then
      # shellcheck disable=SC2016
      [[ "$line" == *'${AWS_ARGS[@]}'* ]] || fail "$(basename "$script") aws call missing AWS_ARGS: $line"
    fi
  done < "$script"
  # Every rollback script must start with an identity preflight.
  grep -q "get-caller-identity" "$script" \
    || fail "$(basename "$script") must run the mandatory identity preflight"
done
# Rollback never mints tags or publishes releases. Comments may legitimately
# document the read-only role scope (e.g. the preflight role's batch-get-image
# read), so strip comment lines before scanning, like the --delete check below.
# shellcheck disable=SC2094  # read-only scan; $ROLLBACK_WF is only read, never written
if rg -n 'promote-image-digest|gh release create|put-image|ecr:PutImage|batch-get-image' \
  <(grep -v '^[[:space:]]*#' "$ROLLBACK_WF") \
  "$RELEASE"/bin/rollback-preflight.sh "$RELEASE"/bin/deploy-rollback.sh \
  "$RELEASE"/bin/restore-frontend.sh "$RELEASE"/bin/verify-rollback.sh \
  "$RELEASE"/bin/record-rollback-result.sh; then
  fail "a rollback tool mints images/tags or publishes releases"
fi
# No secrets anywhere in the rollback tooling.
if rg -n 'PGPASSWORD|password.*[=:]|s3cr3t|plaintext-secret|ghp_[A-Za-z0-9]' \
  "$RELEASE"/bin/rollback-preflight.sh "$RELEASE"/bin/deploy-rollback.sh \
  "$RELEASE"/bin/restore-frontend.sh "$RELEASE"/bin/verify-rollback.sh \
  "$RELEASE"/bin/record-rollback-result.sh "$ROLLBACK_WF"; then
  fail "a secret-looking value appears in the rollback tooling"
fi
# The restore plan must be restore-only: no actual command may use --delete
# (the word may legitimately appear in comments that document the constraint).
# shellcheck disable=SC2094  # read-only scan; $script is only read, never written
if grep -- "--delete" <(grep -v '^[[:space:]]*#' "$RELEASE/bin/restore-frontend.sh"); then
  fail "restore-frontend.sh must never use --delete"
fi
# The rollback workflow must use the engine snapshot/recovery path (the legacy
# shell tooling is retired; the greenfield workflow drives delivery.cli).
if ! grep -q "delivery.cli snapshot production" "$ROLLBACK_WF" || ! grep -q "delivery.cli recover" "$ROLLBACK_WF"; then
  fail "the rollback workflow must drive the engine snapshot and recovery path"
fi
# The compensate wiring must work end-to-end with the same literal JSON array
# the rollback workflow passes as --changed (compensate-production.sh accepts
# an inline array as well as a file; a typo'd component key fails closed).
stub_state_rollback
OUT=$(bash "$RELEASE/bin/compensate-production.sh" \
  --snapshot "$FX/snapshot.json" \
  --changed '["frontend","auth","items","apiGateway"]' \
  --dry-run \
  --profile dpm-profile --region eu-north-1 2>&1)
assert_contains "$OUT" "dry-run"
assert_contains "$OUT" "apiGateway"
OUT=$(bash "$RELEASE/bin/compensate-production.sh" \
  --snapshot "$FX/snapshot.json" \
  --changed '["frontend","auth","items","apiGatewayg"]' \
  --dry-run \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "unknown changed component must fail closed"
assert_contains "$OUT" "unknown component"

# Lint (ruff + shellcheck + git diff --check).
if command -v ruff >/dev/null 2>&1; then
  (cd "$RELEASE" && ruff check src tests) || fail "ruff lint failed"
else
  echo "ruff not found; skipping (report this)"
fi
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck \
    "$RELEASE"/bin/rollback-preflight.sh \
    "$RELEASE"/bin/deploy-rollback.sh \
    "$RELEASE"/bin/restore-frontend.sh \
    "$RELEASE"/bin/verify-rollback.sh \
    "$RELEASE"/bin/record-rollback-result.sh \
    "${BASH_SOURCE[0]}" || fail "shellcheck failed"
else
  echo "shellcheck not found; skipping (report this)"
fi
if ! git -C "$REPO_ROOT" diff --check >/dev/null 2>&1; then
  fail "git diff --check reports whitespace errors"
fi

echo "Rollback tests passed."
