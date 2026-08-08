#!/usr/bin/env bash
set -euo pipefail

# Release traceability queries and operator evidence (Pass 3, subphase 3.7)
# verification gate.
#
# Runs the offline parts of the 3.7 gate:
#   [1/9] Python syntax + unit tests (all suites, incl. traceability)
#   [2/9] trace.sh offline fixture mode: commit / release / running / digest /
#         audit against the traceability fixtures (ok + paused pass; every
#         drift fixture fails closed with its intended issue code)
#   [3/9] trace.sh input handling: single-manifest index, usage errors,
#         non-overridable profile/region
#   [4/9] trace.sh LIVE gathering path against a stateful AWS stub: identity
#         preflight, ECR/ECS/frontend reads, running + paused environments
#   [5/9] Identity preflight: a wrong account fails closed before any lookup
#   [6/9] Index auto-fetch from GitHub Releases via a `gh` stub (read-only)
#   [7/9] Read-only proof: the live path issues no mutating AWS call
#   [8/9] Static scan: every aws invocation carries the mandatory profile/
#         region; no secrets
#   [9/9] lint: ruff + shellcheck + git diff --check
#
# Live lookups against real AWS/GitHub (the read-only live smoke test) are
# deferred to the consolidated verification pass and are NOT claimed here.

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
FX="$RELEASE/fixtures/traceability"
VALID="$RELEASE/fixtures/valid"
TRACE="$RELEASE/bin/trace.sh"
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

# ---------------------------------------------------------------------------
echo "[1/9] Python syntax + unit tests (all suites, incl. traceability)"
python3 -m py_compile "$RELEASE"/src/release_contract/*.py "$RELEASE"/tests/*.py || fail "Python syntax check failed"
(
  cd "$RELEASE" && PYTHONPATH="$RELEASE/src" python3 -m unittest discover -s tests
) || fail "Python validation tests failed"

# ---------------------------------------------------------------------------
echo "[2/9] trace.sh offline fixture mode: all four lookups + audit"
# Every lookup succeeds in the consistent environment.
OUT=$(bash "$TRACE" commit --sha "$SHA" --index "$FX/index.json" --observed "$FX/observed-ok.json")
jq -e '.valid == true and .found == true' <<<"$OUT" >/dev/null || fail "by-sha must find the SHA"
jq -e '.data.digests.auth.imageDigest == "'"$AUTH_DIGEST"'"' <<<"$OUT" >/dev/null || fail "by-sha digest wrong"
jq -e '.data.officialReleases | map(.version) == ["1.2.1"]' <<<"$OUT" >/dev/null || fail "by-sha official releases wrong"

OUT=$(bash "$TRACE" release --version 1.2.1 --index "$FX/index.json" --observed "$FX/observed-ok.json")
jq -e '.valid == true and .data.sourceSha == "'"$SHA"'"' <<<"$OUT" >/dev/null || fail "by-version must resolve the release"
jq -e '.data.live.ecrVerified == true and .data.live.frontendMarkerVerified == true' <<<"$OUT" >/dev/null || fail "by-version live verification must pass"

OUT=$(bash "$TRACE" running --index "$FX/index.json" --observed "$FX/observed-ok.json")
jq -e '.valid == true and .data.paused == false' <<<"$OUT" >/dev/null || fail "running lookup must find the environment"
jq -e '.data.releaseIdentity.version == "1.2.1" and .data.releaseIdentity.approver == "djimi"' <<<"$OUT" >/dev/null || fail "running release identity/approver wrong"
# Running digests must come from tasks[].containers[].imageDigest, not the TD URI.
jq -e '.data.runningDigests.auth == "'"$AUTH_DIGEST"'"' <<<"$OUT" >/dev/null || fail "running digest must come from the task containers"

OUT=$(bash "$TRACE" digest --digest "$AUTH_DIGEST" --index "$FX/index.json" --observed "$FX/observed-ok.json")
jq -e '.valid == true and .data.ociRevision == "'"$SHA"'"' <<<"$OUT" >/dev/null || fail "by-digest must resolve the digest"
jq -e '.data.releaseIdentity | map(.version) == ["1.2.1"]' <<<"$OUT" >/dev/null || fail "by-digest release identity wrong"

OUT=$(bash "$TRACE" audit --index "$FX/index.json" --observed "$FX/observed-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "audit must pass on a consistent environment"

# Paused production: reported honestly, no fabricated running digest.
OUT=$(bash "$TRACE" running --index "$FX/index.json" --observed "$FX/observed-paused.json")
jq -e '.valid == true and .data.paused == true' <<<"$OUT" >/dev/null || fail "paused running lookup must report paused"
jq -e 'has("data.runningDigests") | not' <<<"$OUT" >/dev/null || fail "paused lookup must never fabricate a running digest"
jq -e '.data.lastVerifiedDeployment.version == "1.2.1"' <<<"$OUT" >/dev/null || fail "paused lookup must resolve last verified deployment evidence"
jq -e '.data.taskDefinitions["onlineshop-auth"].imageDigest == "'"$AUTH_DIGEST"'"' <<<"$OUT" >/dev/null || fail "paused lookup must resolve task-definition digests"

# Audit of a paused environment also passes (ECS leg is n/a, not drift).
OUT=$(bash "$TRACE" audit --index "$FX/index.json" --observed "$FX/observed-paused.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "audit must pass on a paused environment"

# Drift fixtures fail closed with their intended issue codes.
OUT=$(bash "$TRACE" audit --index "$FX/index.json" --observed "$FX/observed-drift-ecr.json" 2>&1) \
  && fail "ECR drift must fail the audit"
assert_contains "$OUT" "ECR_RELEASE_DIGEST_MISMATCH"

OUT=$(bash "$TRACE" audit --index "$FX/index.json" --observed "$FX/observed-drift-ecs.json" 2>&1) \
  && fail "ECS drift must fail the audit"
assert_contains "$OUT" "RUNNING_DIGEST_UNMATCHED"

OUT=$(bash "$TRACE" audit --index "$FX/index.json" --observed "$FX/observed-drift-frontend.json" 2>&1) \
  && fail "frontend drift must fail the audit"
assert_contains "$OUT" "FRONTEND_MARKER_MISMATCH"

# Newest-first ordering and last-verified selection must not depend on the
# manifest index order (compare_semver returns only a sign).
jq '.manifests = (.manifests | reverse)' "$FX/index.json" > "$TMP/reversed-index.json"
OUT=$(bash "$TRACE" audit --index "$TMP/reversed-index.json" --observed "$FX/observed-ok.json")
jq -e '.valid == true and (.data.audited | map(.version)) == ["1.2.1", "1.1.0"]' <<<"$OUT" >/dev/null \
  || fail "audit newest-first ordering must not depend on index order"
OUT=$(bash "$TRACE" running --index "$TMP/reversed-index.json" --observed "$FX/observed-paused.json")
jq -e '.valid == true and .data.lastVerifiedDeployment.version == "1.2.1"' <<<"$OUT" >/dev/null \
  || fail "paused last-verified must be the newest release regardless of index order"

# by-version also verifies the immutable per-release prefix marker.
python3 - "$FX/observed-ok.json" "$TMP/obs-prefix-missing.json" <<'PY'
import json, sys
observed = json.load(open(sys.argv[1]))
observed["frontend"]["prefixMarkers"]["_releases/v1.2.1/release.json"] = {"exists": False, "marker": None}
json.dump(observed, open(sys.argv[2], "w"), indent=2)
PY
OUT=$(bash "$TRACE" release --version 1.2.1 --index "$FX/index.json" --observed "$TMP/obs-prefix-missing.json" 2>&1) \
  && fail "a missing immutable prefix marker must fail by-version"
assert_contains "$OUT" "FRONTEND_PREFIX_MARKER_MISSING"

# Running digests that are mixed (two tasks, different bytes) fail closed.
python3 - "$FX/observed-ok.json" "$TMP/obs-mixed.json" <<'PY'
import json, sys
observed = json.load(open(sys.argv[1]))
observed["ecs"]["running"].append({
    "taskArn": "arn:aws:ecs:eu-north-1:799111666795:task/onlineshop-auth/xyz789",
    "taskDefinitionArn": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:6",
    "lastStatus": "RUNNING",
    "containers": [{"name": "auth", "imageDigest": "sha256:" + "a" * 64}],
})
json.dump(observed, open(sys.argv[2], "w"), indent=2)
PY
OUT=$(bash "$TRACE" running --index "$FX/index.json" --observed "$TMP/obs-mixed.json" 2>&1) \
  && fail "mixed running digests must fail the running lookup"
assert_contains "$OUT" "RUNNING_MIXED_DIGESTS"

# by-sha must not merely report the sha tag: a sha tag at different bytes is drift.
python3 - "$FX/observed-ok.json" "$TMP/obs-sha-drift.json" <<'PY'
import json, sys
observed = json.load(open(sys.argv[1]))
observed["ecr"]["onlineshop-auth"]["images"][0]["imageDigest"] = "sha256:" + "e" * 64
json.dump(observed, open(sys.argv[2], "w"), indent=2)
PY
OUT=$(bash "$TRACE" commit --sha "$SHA" --index "$FX/index.json" --observed "$TMP/obs-sha-drift.json" 2>&1) \
  && fail "sha-tag digest drift must fail by-sha"
assert_contains "$OUT" "ECR_SHA_DIGEST_MISMATCH"

# by-digest attributes the OCI revision to the release manifest, never to a
# live label read (describe-images cannot read the config blob).
OUT=$(bash "$TRACE" digest --digest "$AUTH_DIGEST" --index "$FX/index.json" --observed "$FX/observed-ok.json")
jq -e '.data.ociRevisionSource == "release-manifest" and .data.ociRevisionObservedFromImage == false' <<<"$OUT" >/dev/null \
  || fail "by-digest OCI revision must be manifest-attributed, not a label read"

# Missing/unknown keys exit non-zero.
assert_failure bash "$TRACE" release --version 9.9.9 --index "$FX/index.json" --observed "$FX/observed-ok.json"
assert_failure bash "$TRACE" commit --sha 0000000000000000000000000000000000000000 --index "$FX/index.json" --observed "$FX/observed-ok.json"
assert_failure bash "$TRACE" digest --digest "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" --index "$FX/index.json" --observed "$FX/observed-ok.json"

# ---------------------------------------------------------------------------
echo "[3/9] trace.sh input handling"
# A single manifest is accepted as an index.
OUT=$(bash "$TRACE" release --version 1.2.1 --index "$VALID/official-v1.2.1.json" --observed "$FX/observed-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "single-manifest index must be accepted"
# Usage/validation errors exit 2.
assert_failure bash "$TRACE" release --version not-semver --index "$FX/index.json" --observed "$FX/observed-ok.json"
assert_failure bash "$TRACE" commit --sha short --index "$FX/index.json" --observed "$FX/observed-ok.json"
assert_failure bash "$TRACE" digest --digest bad --index "$FX/index.json" --observed "$FX/observed-ok.json"
assert_failure bash "$TRACE" nonsense --index "$FX/index.json" --observed "$FX/observed-ok.json"
# The mandatory profile/region are not overridable.
assert_failure bash "$TRACE" running --index "$FX/index.json" --observed "$FX/observed-ok.json" --profile other
assert_failure bash "$TRACE" running --index "$FX/index.json" --observed "$FX/observed-ok.json" --region us-east-1

# ---------------------------------------------------------------------------
# Stateful AWS stub. Backed by $TMP/state.json, records every call in
# $TMP/calls.txt. STUB_FIXTURE names a traceability observed fixture whose data
# the stub serves; STUB_IDENTITY overrides the account; STUB_IGNORE_MUTATIONS=1
# simulates read-back drift (unused here: the tool is read-only).
# ---------------------------------------------------------------------------
write_stub_clis() {
  mkdir -p "$TMP/bin"
  cat > "$TMP/bin/aws" <<'PY'
#!/usr/bin/env python3
import json
import os
import re
import sys

state_path = os.environ["STUB_STATE"]
calls_path = os.environ["STUB_CALLS"]
state = json.load(open(state_path, encoding="utf-8"))
args = sys.argv[1:]


def record(line):
    with open(calls_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def arg(name):
    for index, item in enumerate(args):
        if item == name and index + 1 < len(args):
            return args[index + 1]
    return None


def query():
    return arg("--query") or ""


def positionals(argv):
    result = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("--"):
            if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                index += 2
            else:
                index += 1
        else:
            result.append(token)
            index += 1
    return result


def text(value):
    print(value)


def emit(value):
    print(json.dumps(value, sort_keys=True))


def fail_not_found():
    print("An error occurred (NotFound) when calling the operation: the resource "
          "does not exist", file=sys.stderr)
    sys.exit(255)


pos = positionals(args)
service = pos[0] if len(pos) > 0 else ""
sub = pos[1] if len(pos) > 1 else ""
q = query()

if service == "sts" and sub == "get-caller-identity":
    record("sts get-caller-identity")
    text(state["identity"])

elif service == "ecr" and sub == "describe-images":
    repo = arg("--repository-name") or ""
    record(f"ecr describe-images {repo}")
    if "imageDetails[]" in q or "imageDetails" in q:
        emit(state["ecr"].get(repo, {}).get("images", []))
    else:
        emit(state["ecr"].get(repo, {}).get("images", []))

elif service == "ecs" and sub == "list-tasks":
    record("ecs list-tasks")
    emit(state["ecs"]["taskArns"])

elif service == "ecs" and sub == "describe-tasks":
    record("ecs describe-tasks")
    if "tasks[]" in q:
        emit(state["ecs"]["tasks"])
    else:
        emit(state["ecs"]["tasks"])

elif service == "ecs" and sub == "describe-services":
    record("ecs describe-services")
    if "services[].{serviceName" in q:
        emit(state["ecs"]["serviceSummaries"])
    else:
        emit(state["ecs"]["services"])

elif service == "ecs" and sub == "describe-task-definition":
    td = arg("--task-definition") or ""
    record(f"ecs describe-task-definition {td}")
    image = state["ecs"].get("tdImages", {}).get(td)
    if image is None:
        fail_not_found()
    if "containerDefinitions[0].image" in q:
        text(image)
    else:
        emit({"taskDefinition": {"containerDefinitions": [{"image": image}]}})

elif service == "s3api" and sub == "get-object":
    key = arg("--key") or ""
    record(f"s3api get-object {key}")
    content = state["frontend"].get(key)
    if content is None:
        fail_not_found()
    out = [item for item in args if not item.startswith("-")][-1]
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(content, handle)

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

# `gh api <url> ...` (the --jq/--paginate/-H args are informational; the
# trace.sh shell post-processes the output, so this stub prints the exact
# shapes the shell expects):
#   /releases                  -> one git tag per line
#   /releases/tags/<tag>       -> one asset {name, id} object per line
#   /releases/assets/<id>      -> the full manifest object
with open(os.environ["STUB_GH_MANIFESTS"], encoding="utf-8") as handle:
    manifests = json.load(handle)  # list of manifest objects

url = sys.argv[2] if len(sys.argv) > 2 else ""
if re.search(r"/releases$", url):
    for manifest in manifests:
        print(manifest["release"]["gitTag"])
    sys.exit(0)

match = re.search(r"/releases/tags/([^/]+)$", url)
if match:
    tag = match.group(1)
    found = next((m for m in manifests if m["release"]["gitTag"] == tag), None)
    if found is None:
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        sys.exit(1)
    asset_id = manifests.index(found) + 1
    # A decoy asset whose name merely contains "manifest" is emitted FIRST to
    # prove trace.sh selects exactly release-manifest.json and never consumes
    # a checksums file as the manifest.
    print(json.dumps({"name": "manifest.checksums", "id": 9900 + asset_id}))
    print(json.dumps({"name": "release-manifest.json", "id": asset_id}))
    sys.exit(0)

match = re.search(r"/releases/assets/(\d+)$", url)
if match:
    asset_id = int(match.group(1))
    print(json.dumps(manifests[asset_id - 1]))
    sys.exit(0)

print("gh: Not Found (HTTP 404)", file=sys.stderr)
sys.exit(1)
PY
  chmod +x "$TMP/bin/aws" "$TMP/bin/gh"
}

# Build the stub state from a traceability observed fixture file.
stub_state() {
  local fixture="$1"
  python3 - "$fixture" "$TMP" <<'PY'
import json
import os
import sys

fixture, tmp = sys.argv[1], sys.argv[2]
observed = json.load(open(fixture, encoding="utf-8"))

ecr = {}
for repo, entry in observed.get("ecr", {}).items():
    ecr[repo] = {"images": entry.get("images", [])}

task_arns = [t.get("taskArn") for t in observed.get("ecs", {}).get("running", []) if t.get("taskArn")]
services = observed.get("ecs", {}).get("services", {})
service_summaries = [{"serviceName": name, "taskDefinition": info.get("taskDefinition")}
                     for name, info in services.items()]
td_images = {}
td_digests = observed.get("ecs", {}).get("taskDefinitions", {})
for famrev, info in td_digests.items():
    td_images["arn:aws:ecs:eu-north-1:799111666795:task-definition/%s" % famrev] = (
        "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth@%s" % info.get("imageDigest")
    )

frontend = {}
live = observed.get("frontend", {}).get("liveMarker", {})
if live.get("exists"):
    frontend["release.json"] = live.get("marker")
for key, entry in observed.get("frontend", {}).get("prefixMarkers", {}).items():
    if entry.get("exists"):
        frontend[key] = entry.get("marker")

state = {
    "identity": os.environ.get("STUB_IDENTITY", "799111666795"),
    "ecr": ecr,
    "ecs": {
        "taskArns": task_arns,
        "tasks": observed.get("ecs", {}).get("running", []),
        "services": services,
        "serviceSummaries": service_summaries,
        "tdImages": td_images,
    },
    "frontend": frontend,
}
with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
  : > "$TMP/calls.txt"
}

write_stub_clis

# ---------------------------------------------------------------------------
echo "[4/9] trace.sh LIVE gathering path with a stateful AWS stub"
export PATH="$TMP/bin:$PATH"
export STUB_STATE="$TMP/state.json"
export STUB_CALLS="$TMP/calls.txt"
export GITHUB_REPOSITORY=Djimi/OnlineShop-full-stack
# The offline mode passed; the live path must now gather the SAME state and
# reach the same conclusions (identity preflight + read-only reads only).
stub_state "$FX/observed-ok.json"
OUT=$(bash "$TRACE" running --index "$FX/index.json")
jq -e '.valid == true and .data.paused == false' <<<"$OUT" >/dev/null || fail "live running lookup failed"
grep -q "sts get-caller-identity" "$TMP/calls.txt" || fail "live path must preflight identity"
grep -q "ecr describe-images" "$TMP/calls.txt" || fail "live path must read ECR"
grep -q "ecs list-tasks" "$TMP/calls.txt" || fail "live path must list ECS tasks"
grep -q "ecs describe-tasks" "$TMP/calls.txt" || fail "live path must describe running tasks"
grep -q "ecs describe-services" "$TMP/calls.txt" || fail "live path must describe services"
grep -q "s3api get-object release.json" "$TMP/calls.txt" || fail "live path must read the frontend marker"

# Paused live environment: no tasks -> task-definition digests resolved, no
# fabricated running digest.
stub_state "$FX/observed-paused.json"
OUT=$(bash "$TRACE" running --index "$FX/index.json")
jq -e '.valid == true and .data.paused == true' <<<"$OUT" >/dev/null || fail "live paused lookup failed"
grep -q "ecs describe-task-definition" "$TMP/calls.txt" || fail "paused live path must resolve task-definition digests"

# A configured service omitted by describe-services (a partial API response)
# must fail closed instead of silently losing that service's evidence.
stub_state "$FX/observed-ok.json"
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["ecs"]["serviceSummaries"] = [
    s for s in state["ecs"]["serviceSummaries"] if s["serviceName"] != "onlineshop-auth"
]
state["ecs"]["services"] = {
    k: v for k, v in state["ecs"]["services"].items() if k != "onlineshop-auth"
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
: > "$TMP/calls.txt"
OUT=$(bash "$TRACE" running --index "$FX/index.json" 2>&1) \
  && fail "a service omitted by describe-services must fail closed"
assert_contains "$OUT" "OBSERVED_READ_ERROR"

# ---------------------------------------------------------------------------
echo "[5/9] Identity preflight: wrong account fails closed"
export STUB_IDENTITY=000000000000
stub_state "$FX/observed-ok.json"
OUT=$(bash "$TRACE" running --index "$FX/index.json" 2>&1) && fail "wrong account must fail the identity preflight"
assert_contains "$OUT" "identity preflight failed"
unset STUB_IDENTITY

# ---------------------------------------------------------------------------
echo "[6/9] Index auto-fetch from GitHub Releases via a gh stub"
python3 - "$FX/index.json" "$TMP/manifests.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    index = json.load(handle)
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(index["manifests"], handle)
PY
export STUB_GH_MANIFESTS="$TMP/manifests.json"
stub_state "$FX/observed-ok.json"
OUT=$(bash "$TRACE" release --version 1.2.1 --observed "$FX/observed-ok.json")
jq -e '.valid == true and .data.sourceSha == "'"$SHA"'"' <<<"$OUT" >/dev/null \
  || fail "gh-fetched index must resolve the release"
unset STUB_GH_MANIFESTS

# ---------------------------------------------------------------------------
echo "[7/9] Read-only proof: no mutating AWS call"
stub_state "$FX/observed-ok.json"
set +e
OUT=$(bash "$TRACE" audit --index "$FX/index.json" 2>&1)
RC=$?
set -e
if [ "$RC" -ne 0 ]; then
  printf '%s\n' "$OUT" | jq -c '{valid, issues: [.issues[]?.code]}' 2>/dev/null || printf '%s\n' "$OUT" | head -20
  fail "live audit must pass on a consistent environment (rc=$RC)"
fi
stub_state "$FX/observed-paused.json"
set +e
OUT=$(bash "$TRACE" audit --index "$FX/index.json" 2>&1)
RC=$?
set -e
if [ "$RC" -ne 0 ]; then
  printf '%s\n' "$OUT" | jq -c '{valid, issues: [.issues[]?.code]}' 2>/dev/null || printf '%s\n' "$OUT" | head -20
  fail "live audit must pass on a paused environment (rc=$RC)"
fi
if grep -Eq ' (put|create|update|delete|register|run|apply|invoke)-' "$TMP/calls.txt"; then
  fail "trace.sh must be read-only; got a mutating call: $(grep -E ' (put|create|update|delete|register|run|apply)-' "$TMP/calls.txt" | head -1)"
fi

# ---------------------------------------------------------------------------
echo "[8/9] Static scan: mandatory profile/region on every aws call, no secrets"
# The AWS_ARGS default is the mandatory profile/region; the search is a
# literal string pattern, so SC2016 (single-quote expansion) is intentional.
# shellcheck disable=SC2016
grep -q 'AWS_ARGS=(--profile "$PROFILE" --region "$REGION")' "$TRACE" \
  || fail "trace.sh must default AWS_ARGS to dpm-profile/eu-north-1"
while IFS= read -r line; do
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  if [[ "$line" =~ (^|[^a-zA-Z_])(aws|"aws")[[:space:]] ]]; then
    # shellcheck disable=SC2016  # literal pattern
    [[ "$line" == *'${AWS_ARGS[@]}'* ]] || fail "trace.sh aws call missing AWS_ARGS: $line"
  fi
done < "$TRACE"
if rg -n 'PGPASSWORD|password.*[=:]|s3cr3t|plaintext-secret' "$TRACE"; then
  fail "a secret-looking value appears in trace.sh"
fi
# The fixture markers carry no secret values.
if rg -n 'password' "$FX"/observed-*.json; then
  fail "a secret-looking value appears in the traceability observed fixtures"
fi

# ---------------------------------------------------------------------------
echo "[9/9] lint"
if command -v ruff >/dev/null 2>&1; then
  (cd "$RELEASE" && ruff check src tests) || fail "ruff lint failed"
else
  echo "ruff not found; skipping (report this)"
fi
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck "$TRACE" "${BASH_SOURCE[0]}" || fail "shellcheck failed"
else
  echo "shellcheck not found; skipping (report this)"
fi
if ! git -C "$REPO_ROOT" diff --check >/dev/null 2>&1; then
  fail "git diff --check reports whitespace errors"
fi

echo "Release traceability tests passed."
