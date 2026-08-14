#!/usr/bin/env bash
set -euo pipefail

# Controlled staging-to-production promotion workflow (Pass 3, subphase 3.4)
# verification gate.
#
# Runs the offline parts of the 3.4 gate:
#   [ 1/10] Python syntax + unit tests (all suites, incl. promotion)
#   [ 2/10] promotion decision-layer CLI against fixtures: dispatch, run,
#           ancestry, preflight, snapshot, plan, waiter, frontend, verify,
#           finalize, compensate (valid passes; every invalid fixture fails
#           closed with its intended issue code)
#   [ 3/10] promote-release.yml workflow static checks (manual dispatch inputs,
#           protected `production` Environment, shared non-cancelling
#           concurrency group, no rebuild, preflight repeated post-approval,
#           compensation on failure, SHA-pinned Actions)
#   [ 4/10] promotion-preflight.sh offline (fixture run/ancestry/identity):
#           passes on a clean candidate, fails closed on a bad run / bad
#           ancestry / unreviewed schema change
#   [ 5/10] snapshot-production.sh + verify-production.sh with a stateful AWS
#           stub: snapshot passes, verification passes on consistent state and
#           fails closed on digest/marker/ALB drift; both are read-only
#   [ 6/10] finalize-release.sh: dry-run refuses publication before production
#           verification; publish action planned; resume action planned
#   [ 7/10] compensate-production.sh: reverse-order restore plan; missing
#           snapshot fails closed
#   [ 8/10] deploy-production.sh dry-run: validates the plan + sanitized
#           task-definition transform without mutating
#   [ 9/10] Static scan: mandatory profile/region on every aws call, mutation
#           read-backs, no plaintext secrets
#   [10/10] lint: ruff + shellcheck + git diff --check
#
# The live half of the gate — the actual owner-approved promotion against real
# AWS/GitHub, the real `production` Environment approval, real ECR/ECS/S3/
# CloudFront mutations and read-backs, and the real GitHub Release publication
# — is **deferred** to the consolidated verification pass and is NOT claimed
# here.

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
FX="$RELEASE/fixtures/promotion"
VALID="$RELEASE/fixtures/valid"
PROMOTE_WF="$REPO_ROOT/.github/workflows/promote-release.yml"
SHA="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
AUTH_DIGEST="sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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

run_promotion() {
  PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion "$@"
}

# ---------------------------------------------------------------------------
echo "[ 1/10] Python syntax + unit tests (all suites, incl. promotion)"
python3 -m py_compile "$RELEASE"/src/release_contract/*.py "$RELEASE"/tests/*.py || fail "Python syntax check failed"
(
  cd "$RELEASE" && PYTHONPATH="$RELEASE/src" python3 -m unittest discover -s tests
) || fail "Python validation tests failed"

# ---------------------------------------------------------------------------
echo "[ 2/10] promotion decision-layer CLI against fixtures"
# dispatch
OUT=$(run_promotion dispatch --version 1.2.1 --run-id 123456789)
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "dispatch must accept valid inputs"
OUT=$(run_promotion dispatch --version 1.2.1-beta --run-id 123456789 2>&1) \
  && fail "dispatch must reject a prerelease version"
expect_issue_code "$OUT" INVALID_VERSION
OUT=$(run_promotion dispatch --version 1.2.1 --run-id not-a-number 2>&1) \
  && fail "dispatch must reject a non-numeric run id"
expect_issue_code "$OUT" INVALID_RUN_ID

# Optional source_sha is a selector, but when supplied it must bind exactly to
# the sourceSha recorded by the downloaded candidate evidence.
OUT=$(run_promotion source-sha --requested "$SHA" --evidence "$VALID/candidate-v1.2.1.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null \
  || fail "matching dispatch source SHA must be accepted"
OUT=$(run_promotion source-sha --requested "ffffffffffffffffffffffffffffffffffffffff" \
  --evidence "$VALID/candidate-v1.2.1.json" 2>&1) \
  && fail "mismatching dispatch source SHA must fail closed"
expect_issue_code "$OUT" SOURCE_SHA_MISMATCH

# run
OUT=$(run_promotion run --run "$FX/run-ok.json" --source-sha "$SHA")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "run evidence must pass on the ok fixture"
for fx in run-wrong-event:RUN_EVENT_MISMATCH run-wrong-ref:RUN_REF_MISMATCH \
  run-wrong-sha:RUN_SHA_MISMATCH run-failed:RUN_UNSUCCESSFUL \
  run-staging-failed:RUN_STAGING_UNSUCCESSFUL; do
  name="${fx%%:*}"
  code="${fx##*:}"
  OUT=$(run_promotion run --run "$FX/$name.json" --source-sha "$SHA" 2>&1) \
    && fail "$name must fail the run evidence"
  expect_issue_code "$OUT" "$code"
done

# ancestry
OUT=$(run_promotion ancestry --ancestry "$FX/ancestry-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "ancestry must pass on the ok fixture"
OUT=$(run_promotion ancestry --ancestry "$FX/ancestry-first.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "first-release ancestry must pass"
for fx in ancestry-behind-official:CANDIDATE_BEHIND_OFFICIAL \
  ancestry-not-on-main:CANDIDATE_NOT_ON_MAIN ancestry-same-version:VERSION_NOT_INCREASING; do
  name="${fx%%:*}"
  code="${fx##*:}"
  OUT=$(run_promotion ancestry --ancestry "$FX/$name.json" 2>&1) \
    && fail "$name must fail the ancestry check"
  expect_issue_code "$OUT" "$code"
done

# preflight (candidate manifest)
OUT=$(run_promotion preflight \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --observed "$FX/observed-preflight-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "preflight must pass on a clean candidate"
OUT=$(run_promotion preflight \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --observed "$FX/observed-preflight-db-unreviewed.json" 2>&1) \
  && fail "preflight must reject an unreviewed schema change"
expect_issue_code "$OUT" SCHEMA_CHANGE_UNREVIEWED
OUT=$(run_promotion preflight \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --observed "$FX/observed-preflight-identity-blocked.json" 2>&1) \
  && fail "preflight must reject a blocked release identity"
expect_issue_code "$OUT" GIT_TAG_CONFLICT

# snapshot
OUT=$(run_promotion snapshot \
  --snapshot "$FX/snapshot-ok.json" --manifest "$VALID/candidate-v1.2.1.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "snapshot must pass on the ok fixture"
OUT=$(run_promotion snapshot \
  --snapshot "$FX/snapshot-missing-fields.json" --manifest "$VALID/candidate-v1.2.1.json" 2>&1) \
  && fail "an incomplete snapshot must fail closed"
expect_issue_code "$OUT" SNAPSHOT_MISSING_FIELD

# plan
OUT=$(run_promotion plan --plan "$FX/plan-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "deployment plan must pass on the ok fixture"
OUT=$(run_promotion plan --plan "$FX/plan-wrong-order.json" 2>&1) \
  && fail "a misordered deployment plan must fail closed"
expect_issue_code "$OUT" PLAN_ORDER_INVALID
OUT=$(run_promotion plan --plan "$FX/plan-unsafe.json" 2>&1) \
  && fail "an unsafe deployment plan must fail closed"
for code in CIRCUIT_BREAKER_DISABLED ROLLBACK_DISABLED MIN_HEALTHY_PERCENT MAX_PERCENT; do
  expect_issue_code "$OUT" "$code"
done

# waiter
jq -n --arg id "ecs-svc/7000000000000000001" \
  --arg td "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5" \
  --arg d "$AUTH_DIGEST" \
  '{component: "auth", deploymentId: $id, taskDefinitionArn: $td, imageDigest: $d}' \
  > "$TMP/waiter-expected.json"
OUT=$(run_promotion waiter --waiter "$FX/waiter-ok.json" --expected "$TMP/waiter-expected.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "waiter must pass on the ok fixture"
OUT=$(run_promotion waiter --waiter "$FX/waiter-wrong-deployment.json" --expected "$TMP/waiter-expected.json" 2>&1) \
  && fail "an unbound deployment must fail the waiter"
expect_issue_code "$OUT" DEPLOYMENT_ID_MISMATCH
OUT=$(run_promotion waiter --waiter "$FX/waiter-in-progress.json" --expected "$TMP/waiter-expected.json" 2>&1) \
  && fail "an in-progress deployment must fail the waiter"
expect_issue_code "$OUT" DEPLOYMENT_NOT_COMPLETED
OUT=$(run_promotion waiter --waiter "$FX/waiter-wrong-digest.json" --expected "$TMP/waiter-expected.json" 2>&1) \
  && fail "a wrong running digest must fail the waiter"
expect_issue_code "$OUT" WAITER_DIGEST_MISMATCH

# frontend publication
OUT=$(run_promotion frontend --plan "$FX/frontend-plan-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "frontend plan must pass on the ok fixture"
OUT=$(run_promotion frontend --plan "$FX/frontend-plan-unsafe.json" 2>&1) \
  && fail "a --delete frontend plan must fail closed"
expect_issue_code "$OUT" FRONTEND_DELETE_FORBIDDEN
OUT=$(run_promotion frontend --plan "$FX/frontend-plan-no-prefix.json" 2>&1) \
  && fail "a prefix-less frontend plan must fail closed"
expect_issue_code "$OUT" FRONTEND_PREFIX_MISSING

# verify (official manifest)
OUT=$(run_promotion verify --observed "$FX/verify-ok.json" \
  --manifest "$VALID/official-v1.2.1.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "verification must pass on a consistent production"
OUT=$(run_promotion verify --observed "$FX/verify-digest-mismatch.json" \
  --manifest "$VALID/official-v1.2.1.json" 2>&1) \
  && fail "a running-digest mismatch must fail verification"
expect_issue_code "$OUT" RUNNING_DIGEST_MISMATCH
OUT=$(run_promotion verify --observed "$FX/verify-alb-unhealthy.json" \
  --manifest "$VALID/official-v1.2.1.json" 2>&1) \
  && fail "an unhealthy ALB target must fail verification"
expect_issue_code "$OUT" ALB_UNHEALTHY
OUT=$(run_promotion verify --observed "$FX/verify-marker-mismatch.json" \
  --manifest "$VALID/official-v1.2.1.json" 2>&1) \
  && fail "a frontend marker mismatch must fail verification"
expect_issue_code "$OUT" FRONTEND_MARKER_MISMATCH

# finalize
OUT=$(run_promotion finalize --state "$FX/finalize-publish.json")
jq -e '.valid == true and .action == "publish"' <<<"$OUT" >/dev/null \
  || fail "finalize must plan a publish after production verification"
OUT=$(run_promotion finalize --state "$FX/finalize-resume.json")
jq -e '.valid == true and .action == "resume"' <<<"$OUT" >/dev/null \
  || fail "finalize must plan an idempotent resume on exact partial objects"
OUT=$(run_promotion finalize --state "$FX/finalize-before-verify.json" 2>&1) \
  && fail "finalize must refuse publication before production verification"
expect_issue_code "$OUT" PUBLICATION_BEFORE_VERIFICATION
OUT=$(run_promotion finalize --state "$FX/finalize-conflict.json" 2>&1) \
  && fail "a release-tag digest conflict must fail finalize closed"
expect_issue_code "$OUT" RELEASE_TAG_CONFLICT

# compensate
OUT=$(run_promotion compensate --snapshot "$FX/snapshot-ok.json" \
  --changed "$FX/changed-partial.json")
jq -e '.valid == true and (.steps | map(.component)) == ["apiGateway", "items", "auth"]' \
  <<<"$OUT" >/dev/null || fail "compensation must restore changed components in reverse order"
OUT=$(run_promotion compensate --snapshot "$FX/snapshot-ok.json" \
  --changed "$FX/changed-all.json")
jq -e '.valid == true and (.steps | map(.component)) == ["frontend", "apiGateway", "items", "auth"]' \
  <<<"$OUT" >/dev/null || fail "compensation must include frontend first when changed"

# ---------------------------------------------------------------------------
echo "[ 3/10] promote-release.yml workflow static checks"
python3 - "$PROMOTE_WF" "$RELEASE" <<'PY' || fail "promote workflow YAML checks failed"
import sys

import yaml

workflow_path, release_root = sys.argv[1], sys.argv[2]
with open(workflow_path, encoding="utf-8") as handle:
    wf = yaml.safe_load(handle)

problems = []

# Manual dispatch only, with the two required inputs. PyYAML parses the YAML
# `on:` trigger key as boolean True (YAML 1.1), so read it that way.
trigger = wf.get("on") or wf.get(True) or {}
dispatch_inputs = (trigger.get("workflow_dispatch") or {}).get("inputs", {}) if isinstance(trigger, dict) else {}
if "version" not in dispatch_inputs or "run_id" not in dispatch_inputs:
    problems.append("promote-release.yml must take version + run_id dispatch inputs")
if "source_sha" not in dispatch_inputs:
    problems.append("promote-release.yml must preserve the optional source_sha selector")

# The production mutation job must use the protected production Environment
# and the shared non-cancelling production concurrency group.
jobs = wf.get("jobs", {})
promote = jobs.get("promote")
if promote is None:
    problems.append("promote job missing")
else:
    env = promote.get("environment")
    env_name = env if isinstance(env, str) else (env or {}).get("name")
    if env_name != "production":
        problems.append("promote job must use the production Environment")
concurrency = wf.get("concurrency", {})
if concurrency.get("group") != "production-mutation":
    problems.append("workflow must use the shared production-mutation concurrency group")
if concurrency.get("cancel-in-progress") is not False:
    problems.append("production concurrency must set cancel-in-progress: false")

# No rebuild: the workflow must consume the candidate evidence (download) and
# never invoke a build/push action.
text = str(wf)
if "build-push-action" in text or "publish-candidate-image.sh" in text:
    problems.append("promote-release.yml must never rebuild; it consumes candidate evidence")

# Preflight repeated post-approval: the promote job must re-run the preflight.
promote_text = str(promote)
if "promotion-preflight.sh" not in promote_text:
    problems.append("promote job must run the full preflight after approval")

# The pre-approval preflight job is lighter: it must NOT call the full preflight
# (which needs AWS) but must validate inputs + render the candidate manifest.
preflight = jobs.get("preflight")
if preflight is None:
    problems.append("preflight job missing")
else:
    if "promotion-preflight.sh" in str(preflight):
        problems.append("the pre-approval preflight job must not require AWS (promotion-preflight.sh)")
    if "emit-candidate-manifest.sh" not in str(preflight) or "validate-manifest.sh" not in str(preflight):
        problems.append("the pre-approval preflight job must render and validate the candidate manifest")

outputs = (preflight or {}).get("outputs", {}) if isinstance(preflight, dict) else {}
if outputs.get("source_sha") != "${{ steps.inputs.outputs.source_sha }}":
    problems.append("preflight must carry the validated source_sha into the approved job")
for label, block in (("preflight", str(preflight)), ("promote", promote_text)):
    if "source-sha" not in block:
        problems.append(f"{label} must bind source_sha to candidate evidence via the release decision")
    if '--requested "$SOURCE_SHA_VALUE"' not in block:
        problems.append(f"{label} must pass the validated source_sha through quoted argv")
    if "--evidence candidate-evidence/candidate-evidence.json" not in block:
        problems.append(f"{label} must compare against the downloaded candidate evidence")
    if 'rl_assert_full_sha "$SOURCE_SHA_VALUE"' not in block:
        problems.append(f"{label} must validate source_sha before the semantic binding")

# approvedBy is derived from the environment-approval evidence via the
# actions/runs/{run}/approvals API, never from github.actor or user input.
if "approvals" not in promote_text:
    problems.append("promote job must derive approvedBy from actions/runs/{run}/approvals")
# The jq that builds promotionWorkflow must not wire approvedBy to the actor.
import re as _re
m = _re.search(r"promotionWorkflow = \{[^}]*\}", promote_text)
if m and _re.search(r"approvedBy:\s*\$actor", m.group(0)):
    problems.append("approvedBy must never be set from the run actor (github.actor)")

# The candidate evidence artifact is consumed by the exact producing run
# attempt, never the latest; duplicates/ambiguity fail closed.
for block in (promote_text, str(preflight)):
    if "--attempt" not in block or "gh run download" not in block:
        problems.append("candidate evidence must be downloaded with gh run download --attempt <exact attempt>")
    if "length > 1" not in block and "length > 1 then error" not in block:
        problems.append("ambiguous candidate evidence artifacts must fail closed")
if "unpack-frontend.sh" not in promote_text:
    problems.append("promote job must unpack and verify the candidate frontend before publishing")

# The compensate job restores the frontend too (the promotion can mutate it).
compensate = jobs.get("compensate")
if compensate is None:
    problems.append("compensate job missing")
else:
    if "compensate-production.sh" not in str(compensate):
        problems.append("compensate job must call compensate-production.sh")
    if str(compensate.get("if", "")) != "failure() && needs.promote.result == 'failure'":
        problems.append("compensate job must run only when promote failed")
    comp_env = compensate.get("environment")
    comp_env_name = comp_env if isinstance(comp_env, str) else (comp_env or {}).get("name")
    if comp_env_name == "production":
        problems.append("compensate job must not be approval-gated by the production Environment (automatic restore)")
    if '"frontend"' not in str(compensate):
        problems.append("compensate job must include frontend in the changed components")

# The dead `revalidate` input was removed (no staging rebuild in this workflow).
if "revalidate" in text:
    problems.append("promote-release.yml must not declare an unused revalidate input")

# The official manifest is rendered from the deployed manifest (candidate bytes
# + registered task-definition ARNs) and verification/finalization use it.
if "deployment-manifest.json" not in promote_text:
    problems.append("promote job must capture the deployed manifest with the registered task definitions")
verify_step_text = " ".join(
    str(step) for step in promote.get("steps", []) if str(step).startswith("{'id': 'verify'")
)
if verify_step_text and "official-manifest.json" not in verify_step_text:
    problems.append("verify job must verify against the official manifest")

# Release-critical Actions pinned by full commit SHA.
sha_ref = __import__("re").compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40})$")
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
# Stateful AWS + gh stubs for the shell-script checks below. The aws stub
# serves ECR/ECS/S3/ELB reads from $TMP/state.json and records every call;
# the gh stub resolves runs/compare/tags.
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
                # A test may inject malformed object metadata; do not repair it
                # by deriving a checksum from the object's content.
                checksum = checksum_hex
    emit({
        "checksum": checksum,
        "checksumType": frontend.get("headChecksumType", "FULL_OBJECT"),
    })
elif service == "s3" and sub == "cp":
    # aws s3 cp <local-file> s3://bucket/key [--content-type X]
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
                # An overwrite without the requested SHA-256 must not retain
                # stale metadata that would make a later snapshot pass.
                checksums.pop(key, None)
            frontend.pop("headChecksumSha256", None)
            persist()
    text("")
elif service == "s3" and sub == "sync":
    # aws s3 sync <local-dir>/ s3://bucket/prefix [--exclude name ...]
    checksum_algorithm = arg("--checksum-algorithm")
    record(f"s3 sync checksum-algorithm={checksum_algorithm or '<missing>'}")
    uri = [a for a in args if a.startswith("s3://")]
    excludes = []
    for index, item in enumerate(args):
        if item == "--exclude" and index + 1 < len(args):
            excludes.append(args[index + 1])
    if uri:
        uri_index = args.index(uri[0])
        src = args[uri_index - 1] if uri_index > 0 else ""
        bucket, prefix = uri[0][len("s3://"):].split("/", 1)
        if src:
            root = src.rstrip("/")
            for dirpath, _dirnames, filenames in os.walk(root):
                for filename in filenames:
                    if any(filename == e.strip("'\"") for e in excludes):
                        continue
                    full = os.path.join(dirpath, filename)
                    rel = os.path.relpath(full, root)
                    key = (prefix.rstrip("/") + "/" + rel) if prefix else rel
                    with open(full, encoding="utf-8") as handle:
                        content = handle.read()
                    raw = content.encode("utf-8")
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
elif service == "s3api" and sub == "sync":
    record("s3api sync")
    text("")
elif service == "s3api" and sub == "cp":
    record("s3api cp")
    text("")
elif service == "elbv2" and sub == "describe-target-health":
    record("elbv2 describe-target-health")
    emit(state["alb"]["targetHealth"])
elif service == "cloudfront" and sub == "create-invalidation":
    record("cloudfront create-invalidation")
    text("I123456789")
else:
    record(f"unhandled {service} {sub}")
    fail_not_found()
PY
  cat > "$TMP/bin/gh" <<'PY'
#!/usr/bin/env python3
import json
import os
import re
import sys

url = sys.argv[2] if len(sys.argv) > 2 else ""
data = json.load(open(os.environ["STUB_GH_DATA"], encoding="utf-8"))

calls_path = os.environ.get("STUB_GH_CALLS")
if calls_path:
    with open(calls_path, "a", encoding="utf-8") as handle:
        handle.write(url + "\n")


def print_jobs(jobs):
    # Match `gh api --jq '.jobs[] | ...'`: one JSON object per selected job.
    for name, conclusion in jobs.items():
        print(json.dumps({"name": name, "conclusion": conclusion}))

attempt_jobs = re.search(r"/actions/runs/(\d+)/attempts/(\d+)/jobs$", url)
if attempt_jobs:
    attempt = attempt_jobs.group(2)
    jobs = data.get("jobsByAttempt", {}).get(attempt)
    if jobs is None:
        print(f"no fixture jobs for attempt {attempt}", file=sys.stderr)
        sys.exit(1)
    print_jobs(jobs)
elif re.search(r"/actions/runs/(\d+)/attempts/(\d+)$", url):
    run = data["run"]
    print(json.dumps({
        "id": data.get("runIdResponse", run["runId"]),
        "run_attempt": data.get("runAttemptResponse", run["runAttempt"]),
        "html_url": run.get("url", ""), "event": run["event"],
        # GitHub's workflow-run REST API reports the branch name ("main"),
        # not the fully-qualified ref stored by the release contract.
        "head_branch": run["head_branch"], "head_sha": run["headSha"],
        "conclusion": run["conclusion"],
    }))
elif "/actions/runs/" in url and "/jobs" in url:
    # Deliberately model the unscoped endpoint as the latest attempt. The
    # regression below must prove that this data cannot satisfy attempt 1.
    print_jobs(data.get("latestJobs", data["run"]["jobs"]))
elif "/artifacts" in url:
    print(json.dumps({"artifacts": [{"name": "candidate-evidence-" + run["headSha"] + "-1", "expired": False}]}))
elif re.search(r"/git/matching-refs/tags/v", url):
    print(json.dumps([{"object": {"sha": data["lastOfficialSha"]}}]))
elif "/git/ref/heads/main" in url:
    print(json.dumps({"object": {"sha": data["mainSha"]}}))
elif "/compare/" in url:
    if "..." not in url:
        print(json.dumps({}))
        sys.exit(0)
    if "refs/heads/main" in url or "main...head" in url:
        print(json.dumps(data["reachableFromMain"]))
    else:
        print(json.dumps(data["descendantOfOfficial"]))
elif "/releases/tags/" in url or "/releases" in url:
    print(json.dumps(data.get("release", {})))
elif "/git/refs/tags/" in url:
    # The scripts filter with `--jq '.object'`, so return the object shape.
    print(json.dumps({"sha": data.get("gitTagSha", ""), "type": "commit"}))
elif "/git/ref/tags/v1.2.1" in url:
    print(json.dumps({
        "ref": "refs/tags/v1.2.1",
        "object": {
            "sha": data.get("gitTagObjectSha", data.get("gitTagSha", "")),
            "type": data.get("gitTagType", "commit"),
        },
    }))
elif "/git/tags/" in url:
    print(json.dumps({
        "object": {
            "sha": data.get("gitTagPeeledSha", data.get("gitTagSha", "")),
            "type": "commit",
        }
    }))
elif "/tags" in url:
    live_tag = [] if data.get("omitLiveTag") else [{
        "name": "v1.2.1",
        "commit": {"sha": data.get("wrongLiveTagSha", data.get("gitTagSha", ""))},
    }]
    print(json.dumps([
        [
            {"name": "v2.0.0", "commit": {"sha": "b" * 40}},
            {"name": "v1.1.0", "commit": {"sha": data["lastOfficialSha"]}},
        ],
        live_tag,
    ]))
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

stub_state_ok() {
  python3 - "$TMP/state.json" "$TMP/gh-data.json" <<PY
import json
import hashlib
import sys

sha = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
index_html = "<html>live-index</html>"
index_sha = hashlib.sha256(index_html.encode()).hexdigest()
state = {
    "identity": "799111666795",
    "ecr": {
        "onlineshop-auth": {"tags": {}},
        "onlineshop-items": {"tags": {}},
        "onlineshop-api-gateway": {"tags": {}},
    },
    "ecs": {
        "services": [
            {
                "serviceName": "onlineshop-auth",
                "taskDefinition": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5",
                "desiredCount": 1,
                "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1, "base": 1}],
                "deployments": [{"id": "ecs-svc/1", "rolloutState": "COMPLETED"}],
                "loadBalancers": [],
            },
            {
                "serviceName": "onlineshop-items",
                "taskDefinition": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:4",
                "desiredCount": 1,
                "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1, "base": 1}],
                "deployments": [{"id": "ecs-svc/2", "rolloutState": "COMPLETED"}],
                "loadBalancers": [],
            },
            {
                "serviceName": "onlineshop-api-gateway",
                "taskDefinition": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:13",
                "desiredCount": 1,
                "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1, "base": 1}],
                "deployments": [{"id": "ecs-svc/3", "rolloutState": "COMPLETED"}],
                "loadBalancers": [{"targetGroupArn": "tg-arn", "containerName": "api-gateway", "containerPort": 10000}],
            },
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
        "taskDefinitions": {
            "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5": {
                "family": "onlineshop-auth",
                "taskDefinitionArn": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5",
                "containerDefinitions": [
                    {
                        "name": "auth",
                        "image": "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth:sha-" + sha,
                        "imageDigest": "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0",
                        "versionConsistency": "enabled",
                        "healthCheck": {"command": ["CMD-SHELL", "curl -f http://localhost/actuator/health || exit 1"]},
                        "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/onlineshop-auth", "awslogs-region": "eu-north-1"}},
                        "portMappings": [{"name": "auth-port", "containerPort": 9001}],
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
                "taskRoleArn": "arn:aws:iam::799111666795:role/onlineshop-auth-task",
                "image": "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth:sha-" + sha,
            },
            "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:4": {
                "family": "onlineshop-items",
                "taskDefinitionArn": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:4",
                "containerDefinitions": [
                    {
                        "name": "items",
                        "image": "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items:sha-" + sha,
                        "imageDigest": "sha256:1a78d43d2aaaca3cafe41539f5fe211f4ef45152a1fa6af5b85006e93b019452",
                        "versionConsistency": "enabled",
                        "healthCheck": {"command": ["CMD-SHELL", "curl -f http://localhost/actuator/health || exit 1"]},
                        "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/onlineshop-items", "awslogs-region": "eu-north-1"}},
                        "portMappings": [{"name": "items-port", "containerPort": 9000}],
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
                "taskRoleArn": "arn:aws:iam::799111666795:role/onlineshop-items-task",
                "image": "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items:sha-" + sha,
            },
            "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:13": {
                "family": "onlineshop-api-gateway",
                "taskDefinitionArn": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:13",
                "containerDefinitions": [
                    {
                        "name": "api-gateway",
                        "image": "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-api-gateway:sha-" + sha,
                        "imageDigest": "sha256:31f9bfdf4d15f460167ba2b7ce9ada23278be1a9f115f06c01e5242901e8948e",
                        "versionConsistency": "enabled",
                        "healthCheck": {"command": ["CMD-SHELL", "curl -f http://localhost/actuator/health || exit 1"]},
                        "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/onlineshop-api-gateway", "awslogs-region": "eu-north-1"}},
                        "portMappings": [{"name": "gateway-port", "containerPort": 10000}],
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
                "taskRoleArn": "arn:aws:iam::799111666795:role/onlineshop-gateway-task",
                "image": "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-api-gateway:sha-" + sha,
            },
        },
    },
    "frontend": {
        "release.json": {"version": "1.2.1", "sourceSha": sha, "frontendSha256": "b9debb6b25ee6e6e534f7738d27f53f4153dbf361f097336741ae9fb54939ee4"},
        "_releases/v1.2.1/release.json": {"version": "1.2.1", "sourceSha": sha, "frontendSha256": "b9debb6b25ee6e6e534f7738d27f53f4153dbf361f097336741ae9fb54939ee4"},
        "_releases/v1.2.1/index.html": index_html,
        "index.html": index_html,
        "indexSha256": index_sha,
        "checksums": {
            "_releases/v1.2.1/index.html": index_sha,
            "index.html": index_sha,
        },
    },
    "alb": {"targetHealth": [{"target": {"id": "10.0.0.1"}, "targetHealth": {"state": "healthy"}}]},
}
json.dump(state, open(sys.argv[1], "w"), indent=2, sort_keys=True)

gh_data = {
    "run": {
        "runId": 123456789, "runAttempt": 1, "url": "https://github.com/x/actions/runs/123456789/attempts/1",
        "event": "push", "head_branch": "main",
        "headSha": sha, "conclusion": "success",
        "jobs": {"auth": "success", "items": "success", "api-gateway": "success",
                 "frontend": "success", "e2e-staging": "success"},
    },
    "jobsByAttempt": {
        "1": {"auth": "success", "items": "success", "api-gateway": "success",
              "frontend": "success", "e2e-staging": "success"},
        "2": {"auth": "success", "items": "success", "api-gateway": "success",
              "frontend": "success", "e2e-staging": "success"},
    },
    "latestJobs": {"auth": "success", "items": "success", "api-gateway": "success",
                   "frontend": "success", "e2e-staging": "success"},
    "lastOfficialSha": "deadbeefcafebabe1234567890abcdef12345678",
    "mainSha": sha,
    "descendantOfOfficial": {"status": "ahead", "aheadBy": 5, "behindBy": 0},
    "reachableFromMain": {"status": "identical", "aheadBy": 0, "behindBy": 0},
    "gitTagSha": sha,
    "gitTagType": "commit",
    "gitTagObjectSha": sha,
    "gitTagPeeledSha": sha,
    "release": {"id": 1, "tag_name": "v1.2.1", "target_commitish": sha,
                "assets": [{"name": "release-manifest.json"}]},
}
json.dump(gh_data, open(sys.argv[2], "w"), indent=2, sort_keys=True)
PY
  : > "$TMP/calls.txt"
  : > "$TMP/gh-calls.txt"
}

write_stub_clis
export PATH="$TMP/bin:$PATH"
export STUB_STATE="$TMP/state.json"
export STUB_CALLS="$TMP/calls.txt"
export STUB_GH_DATA="$TMP/gh-data.json"
export STUB_GH_CALLS="$TMP/gh-calls.txt"
export GITHUB_REPOSITORY=Djimi/OnlineShop-full-stack
export GITHUB_TOKEN=t

# ---------------------------------------------------------------------------
echo "[ 4/10] promotion-preflight.sh (offline run/ancestry/identity inputs)"
stub_state_ok
# Clean candidate passes using fixture run/ancestry/identity.
assert_success bash "$RELEASE/bin/promotion-preflight.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --run "$FX/run-ok.json" \
  --ancestry "$FX/ancestry-ok.json" \
  --identity <(jq -n '{action: "proceed", issues: []}') \
  --db-change absent --migration-reviewed false \
  --profile dpm-profile --region eu-north-1
# The GitHub API gather path must also accept its real `head_branch: "main"`
# shape and normalize it to the contract's `refs/heads/main`.
stub_state_ok
OUT=$(bash "$RELEASE/bin/promotion-preflight.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --ancestry "$FX/ancestry-ok.json" \
  --identity <(jq -n '{action: "proceed", issues: []}') \
  --db-change absent --migration-reviewed false \
  --profile dpm-profile --region eu-north-1 2>&1) \
  || { printf '%s\n' "$OUT" >&2; fail "real GitHub head_branch main must pass promotion preflight"; }
assert_contains "$OUT" "promotion-preflight: OK"
# A contract-shaped `refs/heads/main` from a faulty API stub is not a valid
# REST response and must not accidentally authorize the candidate.
stub_state_ok
python3 - "$TMP/gh-data.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
data["run"]["head_branch"] = "refs/heads/main"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/promotion-preflight.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --ancestry "$FX/ancestry-ok.json" \
  --identity <(jq -n '{action: "proceed", issues: []}') \
  --db-change absent --migration-reviewed false \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "contract-shaped head_branch must fail closed"
assert_contains "$OUT" "RUN_REF_MISMATCH"
# A different (but syntactically plausible) branch must also fail closed.
stub_state_ok
python3 - "$TMP/gh-data.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
data["run"]["head_branch"] = "feature/release"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/promotion-preflight.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --ancestry "$FX/ancestry-ok.json" \
  --identity <(jq -n '{action: "proceed", issues: []}') \
  --db-change absent --migration-reviewed false \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "wrong head_branch must fail closed"
assert_contains "$OUT" "RUN_REF_MISMATCH"
# A rerun-safe gather must use the exact attempt jobs endpoint. The unscoped
# jobs endpoint is fixture-modeled as the latest attempt and reports success;
# only the selected attempt 1 reports the staging failure, so accepting the
# latest attempt would incorrectly authorize this candidate.
stub_state_ok
python3 - "$TMP/gh-data.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
data["jobsByAttempt"]["1"]["e2e-staging"] = "failure"
data["latestJobs"]["e2e-staging"] = "success"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/promotion-preflight.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --ancestry "$FX/ancestry-ok.json" \
  --identity <(jq -n '{action: "proceed", issues: []}') \
  --db-change absent --migration-reviewed false \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "latest-attempt jobs must not satisfy the selected candidate attempt"
assert_contains "$OUT" "RUN_STAGING_UNSUCCESSFUL"
grep -Fxq "repos/Djimi/OnlineShop-full-stack/actions/runs/123456789/attempts/1/jobs" "$TMP/gh-calls.txt" \
  || fail "promotion preflight must call the exact attempt-scoped jobs endpoint"
if grep -Eq '/actions/runs/[0-9]+/jobs$' "$TMP/gh-calls.txt"; then
  fail "promotion preflight must not call the unscoped latest-attempt jobs endpoint"
fi
# The attempt-scoped run endpoint must also identify the requested run before
# any attempt metadata is consumed. Keep attempt, jobs, and all other evidence
# valid so only this exact-run binding check can reject the response.
stub_state_ok
python3 - "$TMP/gh-data.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
data["runIdResponse"] = 987654321
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/promotion-preflight.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --ancestry "$FX/ancestry-ok.json" \
  --identity <(jq -n '{action: "proceed", issues: []}') \
  --db-change absent --migration-reviewed false \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "attempt-scoped run response with a different id must fail closed"
assert_contains "$OUT" "does not match requested run ID 123456789"
grep -Fxq "repos/Djimi/OnlineShop-full-stack/actions/runs/123456789/attempts/1" "$TMP/gh-calls.txt" \
  || fail "promotion preflight must call the exact attempt-scoped run endpoint"
if grep -Fq "/actions/runs/123456789/attempts/1/jobs" "$TMP/gh-calls.txt"; then
  fail "promotion preflight must reject a mismatched run id before reading attempt jobs"
fi
# The attempt-scoped run endpoint itself must identify the requested attempt.
# Keep jobs and all other run evidence valid so only this binding check can
# reject the response.
stub_state_ok
python3 - "$TMP/gh-data.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
data["runAttemptResponse"] = 2
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/promotion-preflight.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --ancestry "$FX/ancestry-ok.json" \
  --identity <(jq -n '{action: "proceed", issues: []}') \
  --db-change absent --migration-reviewed false \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "attempt-scoped run response with a different attempt must fail closed"
assert_contains "$OUT" "does not match requested attempt 1"
# Unreviewed schema change fails closed.
OUT=$(bash "$RELEASE/bin/promotion-preflight.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --run "$FX/run-ok.json" \
  --ancestry "$FX/ancestry-ok.json" \
  --identity <(jq -n '{action: "proceed", issues: []}') \
  --db-change present --migration-reviewed false \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "unreviewed schema change must fail the preflight"
assert_contains "$OUT" "SCHEMA_CHANGE_UNREVIEWED"
# A non-main run fails closed.
assert_failure bash "$RELEASE/bin/promotion-preflight.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --run "$FX/run-wrong-event.json" \
  --ancestry "$FX/ancestry-ok.json" \
  --identity <(jq -n '{action: "proceed", issues: []}') \
  --db-change absent --migration-reviewed false \
  --profile dpm-profile --region eu-north-1

# ---------------------------------------------------------------------------
echo "[ 5/10] snapshot-production.sh + verify-production.sh with a stateful AWS stub"
stub_state_ok
# snapshot-production reads the stub ECS/frontend state and must pass.
SNAP=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1)
echo "$SNAP" | jq -e '.services["onlineshop-auth"].taskDefinitionArn != ""' >/dev/null \
  || fail "snapshot must capture the auth task definition"
echo "$SNAP" | jq -e '
  .officialRelease.version == .frontend.marker.version and
  .officialRelease.gitTag == ("v" + .frontend.marker.version) and
  .officialRelease.sourceSha == .frontend.marker.sourceSha and
  .frontend.indexSha256 != ""
' >/dev/null || fail "snapshot official identity must match the live frontend marker"
grep -q "ecs describe-services" "$TMP/calls.txt" || fail "snapshot must read ECS services"
grep -q "s3api head-object index.html checksum-mode=ENABLED" "$TMP/calls.txt" \
  || fail "snapshot must request S3 checksum mode"
if grep -Eq ' (put|create|update|delete|register|run)-' "$TMP/calls.txt"; then
  fail "snapshot-production.sh must be read-only"
fi
# A newer unrelated tag is present in the stub, but it must not override the
# release named by the live frontend marker.
jq -e '.officialRelease.version == "1.2.1" and .officialRelease.gitTag == "v1.2.1"' \
  <<<"$SNAP" >/dev/null || fail "newer unrelated tag must not become the previous release"
# Missing and mismatched canonical tags fail closed before snapshot output.
stub_state_ok
python3 - "$TMP/gh-data.json" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["omitLiveTag"] = True
json.dump(data, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must reject a missing canonical live-release tag"
assert_contains "$OUT" "TAG_NOT_FOUND"

stub_state_ok
python3 - "$TMP/gh-data.json" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["wrongLiveTagSha"] = "f" * 40
json.dump(data, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must reject a canonical tag with the wrong source SHA"
assert_contains "$OUT" "TAG_SHA_MISMATCH"

# The immutable rollback source must agree with the captured live marker and
# live index checksum. Exercise marker, bytes, and recorded checksum drift.
stub_state_ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["frontend"]["_releases/v1.2.1/release.json"]["sourceSha"] = "f" * 40
json.dump(state, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must reject a mismatched immutable prefix marker"
assert_contains "$OUT" "prefix marker does not match"

stub_state_ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["frontend"]["_releases/v1.2.1/index.html"] = "<html>wrong-prefix-index</html>"
json.dump(state, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must reject immutable prefix index drift"
assert_contains "$OUT" "prefix index checksum"

stub_state_ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["frontend"]["indexSha256"] = "e" * 64
state["frontend"]["checksums"]["index.html"] = "e" * 64
json.dump(state, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must reject a wrong live index checksum"
assert_contains "$OUT" "prefix index checksum"

# S3 HeadObject exposes ChecksumSHA256 as base64. Missing, malformed, and
# unsupported composite metadata must fail closed; an ETag cannot substitute.
stub_state_ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    state = json.load(handle)
state["frontend"]["headChecksumSha256"] = None
state["frontend"]["indexETag"] = '"etag-not-sha256"'
json.dump(state, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must reject missing checksum metadata and ETag fallback"
assert_contains "$OUT" "ChecksumSHA256 is absent"

stub_state_ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    state = json.load(handle)
state["frontend"]["headChecksumSha256"] = "not-base64"
json.dump(state, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must reject malformed base64 checksum metadata"
assert_contains "$OUT" "not canonical base64"

stub_state_ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    state = json.load(handle)
state["frontend"]["headChecksumType"] = "COMPOSITE"
json.dump(state, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must reject composite checksum metadata"
assert_contains "$OUT" "checksum type is not FULL_OBJECT"

# Annotated tags are accepted only after the tag object is peeled to the same
# commit SHA named by the live marker; a wrong peeled commit fails closed.
stub_state_ok
python3 - "$TMP/gh-data.json" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["gitTagType"] = "tag"
data["gitTagObjectSha"] = "c" * 40
data["gitTagPeeledSha"] = data["gitTagSha"]
json.dump(data, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
SNAP_ANNOTATED=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1) \
  || fail "snapshot must support a correctly peeled annotated canonical tag"
jq -e '.officialRelease.sourceSha == .frontend.marker.sourceSha' <<<"$SNAP_ANNOTATED" >/dev/null \
  || fail "annotated tag snapshot must retain the live source SHA"

stub_state_ok
python3 - "$TMP/gh-data.json" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["gitTagType"] = "tag"
data["gitTagObjectSha"] = "c" * 40
data["gitTagPeeledSha"] = "d" * 40
json.dump(data, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must reject an annotated tag peeled to the wrong commit"
assert_contains "$OUT" "canonical Git tag SHA disagrees"

# verify-production against the stub (consistent state) must pass.
: > "$TMP/calls.txt"
assert_success bash "$RELEASE/bin/verify-production.sh" \
  --manifest "$VALID/official-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1
# Drift: make the gateway running digest differ -> verification fails closed.
stub_state_ok
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["ecs"]["tasks"][2]["containers"][0]["imageDigest"] = "sha256:" + "e" * 64
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/verify-production.sh" \
  --manifest "$VALID/official-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "running-digest drift must fail verification"
assert_contains "$OUT" "RUNNING_DIGEST_MISMATCH"

# ---------------------------------------------------------------------------
echo "[ 6/10] finalize-release.sh: dry-run + production-verified gate"
stub_state_ok
# Without PROMOTION_PRODUCTION_VERIFIED=true the finalize must refuse mutation.
OUT=$(bash "$RELEASE/bin/finalize-release.sh" \
  --manifest "$VALID/official-v1.2.1.json" \
  --evidence-dir "$TMP/evidence" \
  --profile dpm-profile --region eu-north-1 2>&1) || true
mkdir -p "$TMP/evidence"
assert_failure bash "$RELEASE/bin/finalize-release.sh" \
  --manifest "$VALID/official-v1.2.1.json" \
  --evidence-dir "$TMP/evidence" \
  --profile dpm-profile --region eu-north-1
# Dry-run with production verified plans an action without mutating.
OUT=$(PROMOTION_PRODUCTION_VERIFIED=true bash "$RELEASE/bin/finalize-release.sh" \
  --manifest "$VALID/official-v1.2.1.json" \
  --evidence-dir "$TMP/evidence" \
  --dry-run \
  --profile dpm-profile --region eu-north-1)
assert_contains "$OUT" "dry-run"

# ---------------------------------------------------------------------------
echo "[ 7/10] compensate-production.sh: reverse-order restore plan"
assert_success bash "$RELEASE/bin/compensate-production.sh" \
  --snapshot "$FX/snapshot-ok.json" \
  --changed "$FX/changed-partial.json" \
  --dry-run \
  --profile dpm-profile --region eu-north-1
OUT=$(bash "$RELEASE/bin/compensate-production.sh" \
  --snapshot "$FX/snapshot-ok.json" \
  --changed "$FX/changed-partial.json" \
  --dry-run \
  --profile dpm-profile --region eu-north-1)
assert_contains "$OUT" "apiGateway"
assert_failure bash "$RELEASE/bin/compensate-production.sh" \
  --snapshot /nonexistent \
  --changed "$FX/changed-partial.json" \
  --dry-run \
  --profile dpm-profile --region eu-north-1
# The workflow passes the changed-component set inline as a literal JSON array;
# the tool must accept it, and a typo'd component key must fail closed.
assert_success bash "$RELEASE/bin/compensate-production.sh" \
  --snapshot "$FX/snapshot-ok.json" \
  --changed '["frontend","auth","items","apiGateway"]' \
  --dry-run \
  --profile dpm-profile --region eu-north-1
assert_failure bash "$RELEASE/bin/compensate-production.sh" \
  --snapshot "$FX/snapshot-ok.json" \
  --changed '["frontend","auth","items","apiGatewayg"]' \
  --dry-run \
  --profile dpm-profile --region eu-north-1

# Real frontend restore: the live root carries the new (v1.2.1) marker and the
# previous immutable prefix holds the pre-promotion (v1.1.0) bytes.
stub_state_ok
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["frontend"]["release.json"] = {
    "version": "1.2.1", "sourceSha": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4",
    "frontendSha256": "b9debb6b25ee6e6e534f7738d27f53f4153dbf361f097336741ae9fb54939ee4",
}
state["frontend"]["index.html"] = "<html>new-index</html>"
state["frontend"]["_releases/v1.1.0/release.json"] = {
    "version": "1.1.0", "sourceSha": "deadbeefcafebabe1234567890abcdef12345678",
    "frontendSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
}
state["frontend"]["_releases/v1.1.0/index.html"] = "<html>old-index</html>"
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
python3 - "$FX/snapshot-ok.json" "$TMP/snapshot-frontend.json" <<'PY'
import hashlib
import json
import sys

snap = json.load(open(sys.argv[1], encoding="utf-8"))
snap["frontend"]["marker"] = {
    "version": "1.1.0", "sourceSha": "deadbeefcafebabe1234567890abcdef12345678",
    "frontendSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
}
snap["frontend"]["indexSha256"] = hashlib.sha256(b"<html>old-index</html>").hexdigest()
json.dump(snap, open(sys.argv[2], "w"), indent=2, sort_keys=True)
PY
jq -n '["frontend"]' > "$TMP/changed-frontend.json"
: > "$TMP/calls.txt"
OUT=$(bash "$RELEASE/bin/compensate-production.sh" \
  --snapshot "$TMP/snapshot-frontend.json" \
  --changed "$TMP/changed-frontend.json" \
  --profile dpm-profile --region eu-north-1 2>&1)
assert_contains "$OUT" "restore frontend"
jq -e '.frontend["release.json"].version == "1.1.0"' "$TMP/state.json" >/dev/null \
  || fail "frontend restore did not put the pre-promotion marker live"
RESTORED_INDEX_SHA=$(printf '%s' '<html>old-index</html>' | sha256sum | awk '{print $1}')
jq -e --arg sha "$RESTORED_INDEX_SHA" '.frontend.checksums["index.html"] == $sha' \
  "$TMP/state.json" >/dev/null \
  || fail "frontend compensation must establish SHA-256 metadata for live index.html"
grep -q "s3 cp checksum-algorithm=SHA256" "$TMP/calls.txt" \
  || fail "frontend restore must publish the previous bytes with SHA-256"

# No-op frontend restore: live root already matches the snapshot.
stub_state_ok
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["frontend"]["release.json"] = {
    "version": "1.1.0", "sourceSha": "deadbeefcafebabe1234567890abcdef12345678",
    "frontendSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/compensate-production.sh" \
  --snapshot "$TMP/snapshot-frontend.json" \
  --changed "$TMP/changed-frontend.json" \
  --profile dpm-profile --region eu-north-1 2>&1)
assert_contains "$OUT" "no-op"

# ---------------------------------------------------------------------------
echo "[ 8/10] deploy-production.sh dry-run (plan + sanitize, no mutation)"
stub_state_ok
OUT=$(bash "$RELEASE/bin/deploy-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --snapshot "$FX/snapshot-ok.json" \
  --ecr-registry 799111666795.dkr.ecr.eu-north-1.amazonaws.com \
  --dry-run \
  --profile dpm-profile --region eu-north-1 2>&1)
assert_contains "$OUT" "dry-run"
if grep -q "ecs register-task-definition" "$TMP/calls.txt"; then
  fail "deploy-production.sh --dry-run must not register task definitions"
fi
# A schema-valid but unknown source ARN must still fail closed at the AWS read;
# keep this separate from the aligned snapshot fixture so the happy-path stub
# does not hide an invalid production snapshot.
jq '.services["onlineshop-auth"].taskDefinitionArn = "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:999"' \
  "$FX/snapshot-ok.json" > "$TMP/snapshot-unknown-arn.json"
OUT=$(bash "$RELEASE/bin/deploy-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --snapshot "$TMP/snapshot-unknown-arn.json" \
  --ecr-registry 799111666795.dkr.ecr.eu-north-1.amazonaws.com \
  --dry-run \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "deploy-production.sh must reject an unknown snapshot task-definition ARN"
assert_contains "$OUT" "cannot read the current task definition"

# publish-frontend.sh functional: assets-first/index-last writes BOTH the live
# root marker and the immutable per-release prefix marker, then reads them back.
stub_state_ok
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["frontend"]["release.json"] = {
    "version": "1.1.0", "sourceSha": "deadbeefcafebabe1234567890abcdef12345678",
    "frontendSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
}
state["frontend"].pop("_releases/v1.2.1/release.json", None)
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
mkdir -p "$TMP/dist"
printf '<html>v1.2.1</html>' > "$TMP/dist/index.html"
printf 'var a=1;' > "$TMP/dist/assets-app.js"
: > "$TMP/calls.txt"
assert_success bash "$RELEASE/bin/publish-frontend.sh" \
  --manifest "$VALID/official-v1.2.1.json" \
  --dist "$TMP/dist" \
  --bucket onlineshop-frontend-799111666795 \
  --distribution EPS8MI3FV3B7X \
  --profile dpm-profile --region eu-north-1
jq -e '.frontend["release.json"].version == "1.2.1"' "$TMP/state.json" >/dev/null \
  || fail "publish-frontend must update the live root marker"
jq -e '.frontend["_releases/v1.2.1/release.json"].version == "1.2.1"' "$TMP/state.json" >/dev/null \
  || fail "publish-frontend must write the immutable prefix marker"
PUBLISHED_INDEX_SHA=$(sha256sum "$TMP/dist/index.html" | awk '{print $1}')
jq -e --arg sha "$PUBLISHED_INDEX_SHA" '
  .frontend.checksums["index.html"] == $sha and
  .frontend.checksums["_releases/v1.2.1/index.html"] == $sha
' "$TMP/state.json" >/dev/null \
  || fail "publish-frontend must establish SHA-256 metadata for both index objects"
grep -q "s3 sync checksum-algorithm=SHA256" "$TMP/calls.txt" \
  || fail "publish-frontend must request SHA-256 on sync uploads"
grep -q "s3 cp checksum-algorithm=SHA256" "$TMP/calls.txt" \
  || fail "publish-frontend must request SHA-256 on index/marker uploads"
grep -q "cloudfront create-invalidation" "$TMP/calls.txt" \
  || fail "publish-frontend must invalidate CloudFront"

# A later overwrite that omits --checksum-algorithm must remove the metadata;
# the checksum-requiring snapshot then fails closed instead of synthesizing a
# digest from the object's bytes (or falling back to ETag).
printf '<html>omitted-checksum</html>' > "$TMP/omitted-index.html"
aws s3 cp --profile dpm-profile --region eu-north-1 \
  "$TMP/omitted-index.html" "s3://onlineshop-frontend-799111666795/index.html" \
  --content-type text/html >/dev/null
OUT=$(bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$VALID/candidate-v1.2.1.json" \
  --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "snapshot must fail after an index overwrite omits SHA-256"
assert_contains "$OUT" "ChecksumSHA256 is absent"

# ---------------------------------------------------------------------------
echo "[ 9/10] Static scan: mandatory profile/region + read-backs + no secrets"
for script in \
  "$RELEASE/bin/promotion-preflight.sh" \
  "$RELEASE/bin/snapshot-production.sh" \
  "$RELEASE/bin/deploy-production.sh" \
  "$RELEASE/bin/verify-production.sh" \
  "$RELEASE/bin/publish-frontend.sh" \
  "$RELEASE/bin/finalize-release.sh" \
  "$RELEASE/bin/compensate-production.sh"; do
  # shellcheck disable=SC2016  # literal pattern
  grep -q 'AWS_ARGS=(--profile "$PROFILE" --region "$REGION")' "$script" \
    || fail "$(basename "$script") must default AWS_ARGS to dpm-profile/eu-north-1"
  # shellcheck disable=SC2094  # read-only scan; $script is only read, never written
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^[[:space:]]*aws[[:space:]] ]] || [[ "$line" =~ \$\(aws[[:space:]] ]]; then
      # shellcheck disable=SC2016
      [[ "$line" == *'${AWS_ARGS[@]}'* ]] || fail "$(basename "$script") aws call missing AWS_ARGS: $line"
    fi
  done < "$script"
done
grep -q "promote-image-digest.sh" "$RELEASE/bin/finalize-release.sh" \
  || fail "finalize-release.sh must mint release tags via promote-image-digest.sh"
# Mutation scripts must read back after mutating.
grep -q "validate-task-definition.sh" "$RELEASE/bin/deploy-production.sh" \
  || fail "deploy-production.sh must validate sanitized task definitions"
grep -q "waiter" "$RELEASE/bin/deploy-production.sh" \
  || fail "deploy-production.sh must bind waiters to the deployment started by this run"
# shellcheck disable=SC2016  # literal pattern
grep -q 'taskDefinitionArn = \$tds\[0\]' "$RELEASE/bin/deploy-production.sh" \
  || fail "deploy-production.sh must emit the deployed manifest with the registered task definitions"
# Frontend publication must write AND read back the immutable prefix marker.
# shellcheck disable=SC2016  # literal pattern
grep -q '\$BUCKET/\$PREFIX\$MARKER' "$RELEASE/bin/publish-frontend.sh" \
  || fail "publish-frontend.sh must publish the immutable prefix marker"
# shellcheck disable=SC2016  # literal pattern
grep -q -- '--key "\$PREFIX\$MARKER"' "$RELEASE/bin/publish-frontend.sh" \
  || fail "publish-frontend.sh must read back the immutable prefix marker"
# Compensation must really restore the frontend (no no-op echo).
grep -q "restore_frontend" "$RELEASE/bin/compensate-production.sh" \
  || fail "compensate-production.sh must implement a real frontend restore"
grep -q "prev_prefix" "$RELEASE/bin/compensate-production.sh" \
  || fail "compensate-production.sh must restore the frontend from the previous immutable prefix"
if rg -n 'PGPASSWORD|password.*[=:]|s3cr3t|plaintext-secret|ghp_[A-Za-z0-9]' \
  "$RELEASE/bin"/promotion-preflight.sh "$RELEASE/bin"/snapshot-production.sh \
  "$RELEASE/bin"/deploy-production.sh "$RELEASE/bin"/verify-production.sh \
  "$RELEASE/bin"/publish-frontend.sh "$RELEASE/bin"/finalize-release.sh \
  "$RELEASE/bin"/compensate-production.sh "$PROMOTE_WF"; then
  fail "a secret-looking value appears in the promotion tooling"
fi

# ---------------------------------------------------------------------------
echo "[10/10] lint"
if command -v ruff >/dev/null 2>&1; then
  (cd "$RELEASE" && ruff check src tests) || fail "ruff lint failed"
else
  echo "ruff not found; skipping (report this)"
fi
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck \
    "$RELEASE"/bin/promotion-preflight.sh \
    "$RELEASE"/bin/snapshot-production.sh \
    "$RELEASE"/bin/deploy-production.sh \
    "$RELEASE"/bin/verify-production.sh \
    "$RELEASE"/bin/publish-frontend.sh \
    "$RELEASE"/bin/finalize-release.sh \
    "$RELEASE"/bin/compensate-production.sh \
    "${BASH_SOURCE[0]}" || fail "shellcheck failed"
else
  echo "shellcheck not found; skipping (report this)"
fi
if ! git -C "$REPO_ROOT" diff --check >/dev/null 2>&1; then
  fail "git diff --check reports whitespace errors"
fi

echo "Promotion tests passed."
