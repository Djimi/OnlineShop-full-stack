#!/usr/bin/env bash
set -euo pipefail

# ECR release tagging, immutability, and least privilege (Pass 3, subphase 3.3)
# verification gate.
#
# Runs the offline parts of the 3.3 gate:
#   [ 1/10] Python unit + validation tests (ecr, releaseid, iam, all suites)
#   [ 2/10] Workflow YAML static checks (job-permission split, no latest/release-* tags in the build workflow)
#   [ 3/10] Immutable-repository desired-state config vs canonical component map
#   [ 4/10] verify-immutable-repositories.sh: read-only read-back, drift fails closed, no mutation
#   [ 5/10] apply-immutable-repositories.sh: put-image-tag-mutability + immediate read-back
#   [ 6/10] promote-image-digest.sh: server-side mint / idempotent reuse / conflict fail-closed / dry-run
#   [ 7/10] check-release-identity.sh: proceed / resume / collision fail-closed
#   [ 8/10] IAM least-privilege + OIDC trust policy validation (real files + invalid fixtures)
#   [ 9/10] Static scan: every aws invocation carries --profile dpm-profile --region eu-north-1; mutation paths read back
#   [10/10] lint: ruff + shellcheck (report if unavailable)
#
# Live ECR/IAM checks (repository settings read-back against real repositories,
# real put-image-tag-mutability/put-image behavior, a real OIDC environment
# subject) are deferred to the consolidated verification pass and are NOT
# claimed here.

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
PLAN_DIR="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY"
WORKFLOW="$REPO_ROOT/.github/workflows/build-and-deploy.yml"
SHA="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
CANDIDATE_TAG="sha-$SHA"
RELEASE_TAG="release-1.2.1"
AUTH_DIGEST="sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
ITEMS_DIGEST="sha256:1a78d43d2aaaca3cafe41539f5fe211f4ef45152a1fa6af5b85006e93b019452"
GATEWAY_DIGEST="sha256:31f9bfdf4d15f460167ba2b7ce9ada23278be1a9f115f06c01e5242901e8948e"
MANIFEST="$RELEASE/fixtures/valid/candidate-v1.2.1.json"

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
# Stub AWS + gh CLIs. The aws stub emulates the exact ECR/S3 calls the 3.3
# scripts issue, backed by a JSON state file ($TMP/state.json), records every
# call in $TMP/calls.txt, and honors STUB_IGNORE_MUTATIONS=1 to simulate
# read-back drift. STUB_STATE/STUB_CALLS/PATH are exported so all script
# invocations transparently use the stub.
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


def pair(name):
    prefix = name + "="
    for item in args:
        if item.startswith(prefix):
            return item[len(prefix):]
    return None


def persist():
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


service = args[0] if args else ""
sub = args[1] if len(args) > 1 else ""
query = arg("--query") or ""

if service == "ecr" and sub == "describe-repositories":
    repo = arg("--repository-name") or ""
    record(f"ecr describe-repositories {repo}")
    print(json.dumps(state.get("repositories", {}).get(repo, {})))
elif service == "ecr" and sub == "put-image-tag-mutability":
    repo = arg("--repository-name") or ""
    mutability = arg("--image-tag-mutability") or ""
    filters = json.loads(arg("--image-tag-mutability-exclusion-filters") or "[]")
    record(f"ecr put-image-tag-mutability {repo} {mutability}")
    if os.environ.get("STUB_IGNORE_MUTATIONS") != "1":
        state.setdefault("repositories", {})[repo] = {
            "imageTagMutability": mutability,
            "imageTagMutabilityExclusionFilters": filters,
        }
    print(json.dumps({"repositoryName": repo, "imageTagMutability": mutability}))
elif service == "ecr" and sub == "describe-images":
    repo = arg("--repository-name") or ""
    tag = pair("imageTag") or ""
    record(f"ecr describe-images {repo} {tag}")
    digest = state.get("tags", {}).get(repo, {}).get(tag)
    print(digest if digest else "")
elif service == "ecr" and sub == "batch-get-image":
    repo = arg("--repository-name") or ""
    tag = pair("imageTag") or ""
    record(f"ecr batch-get-image {repo} {tag}")
    digest = state.get("tags", {}).get(repo, {}).get(tag)
    if not digest:
        print("null" if "images[0]" in query else json.dumps({"images": []}))
    else:
        state["lastBatchDigest"] = digest
        image = {
            "imageId": {"imageDigest": digest},
            "imageManifest": json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                    "config": {
                        "mediaType": "application/vnd.docker.container.image.v1+json",
                        "size": 1,
                        "digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                    },
                    "layers": [],
                }
            ),
            "imageManifestMediaType": "application/vnd.docker.distribution.manifest.v2+json",
        }
        print(json.dumps(image) if "images[0]" in query else json.dumps({"images": [image]}))
elif service == "ecr" and sub == "put-image":
    repo = arg("--repository-name") or ""
    tag = arg("--image-tag") or ""
    record(f"ecr put-image {repo} {tag}")
    if os.environ.get("STUB_IGNORE_MUTATIONS") != "1":
        state.setdefault("tags", {}).setdefault(repo, {})[tag] = state.get("lastBatchDigest")
    print(json.dumps({"repositoryName": repo, "imageId": {"imageTag": tag}}))
elif service == "s3api" and sub == "list-objects-v2":
    record("s3api list-objects-v2")
    marker = state.get("frontendMarker", {})
    print(marker.get("key", "_releases/v1.2.1/release.json") if marker.get("exists") else "")
elif service == "s3api" and sub == "get-object":
    record("s3api get-object")
    positional = [item for item in args if not item.startswith("-")]
    out_path = positional[-1] if positional else "/dev/null"
    marker = state.get("frontendMarker", {})
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(marker.get("content") if marker.get("exists") else {}, handle)
else:
    record(f"unhandled {service} {sub}")

persist()
PY
  cat > "$TMP/bin/gh" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

# `gh api <url>`: argv is [gh, api, <url>].
path = sys.argv[2] if len(sys.argv) > 2 else ""
sha = os.environ.get("STUB_GH_REF_SHA", "")
if path.endswith("/git/refs/tags/v1.2.1"):
    if not sha:
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("STUB_GH_ANNOTATED") == "1":
        print(json.dumps({"ref": "refs/tags/v1.2.1", "object": {"sha": sha, "type": "tag"}}))
    else:
        print(json.dumps({"ref": "refs/tags/v1.2.1", "object": {"sha": sha, "type": "commit"}}))
elif path.endswith("/git/tags/" + sha) and sha:
    peeled = os.environ.get("STUB_GH_PEELED_SHA", "")
    if peeled:
        print(json.dumps({"object": {"sha": peeled, "type": "commit"}}))
    else:
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        sys.exit(1)
else:
    print("gh: Not Found (HTTP 404)", file=sys.stderr)
    sys.exit(1)
PY
  chmod +x "$TMP/bin/aws" "$TMP/bin/gh"
}

# Build a fresh stub state file. Modes: ok (immutable config), drift (mutable),
# candidates (ok + sha-<full-sha> candidate tags for all three backends).
stub_state() {
  local mode="${1:-ok}"
  python3 - "$mode" <<PY
import json
import sys

mode = sys.argv[1]
repos = ["onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"]
repository_config = {
    "imageTagMutability": "IMMUTABLE_WITH_EXCLUSION",
    "imageTagMutabilityExclusionFilters": [
        {"filterType": "WILDCARD", "filter": "main-latest"},
        {"filterType": "WILDCARD", "filter": "branch-*"},
    ],
}
state = {
    "repositories": {
        repo: repository_config if mode == "ok" else {
            "imageTagMutability": "MUTABLE", "imageTagMutabilityExclusionFilters": []
        }
        for repo in repos
    },
    "tags": {"onlineshop-auth": {}, "onlineshop-items": {}, "onlineshop-api-gateway": {}},
    "frontendMarker": {"exists": False, "content": None},
    "lastBatchDigest": None,
}
if mode == "candidates":
    state["tags"]["onlineshop-auth"]["$CANDIDATE_TAG"] = "$AUTH_DIGEST"
    state["tags"]["onlineshop-items"]["$CANDIDATE_TAG"] = "$ITEMS_DIGEST"
    state["tags"]["onlineshop-api-gateway"]["$CANDIDATE_TAG"] = "$GATEWAY_DIGEST"
json.dump(state, open("$TMP/state.json", "w"), indent=2, sort_keys=True)
PY
  : > "$TMP/calls.txt"
}

# ---------------------------------------------------------------------------

echo "[ 1/10] Python syntax + unit/validation tests (ecr, releaseid, iam, all suites)"
python3 -m py_compile "$RELEASE"/src/release_contract/*.py "$RELEASE"/tests/*.py || fail "Python syntax check failed"
(
  cd "$RELEASE" && PYTHONPATH="$RELEASE/src" python3 -m unittest discover -s tests
) || fail "Python validation tests failed"

echo "[ 2/10] Workflow YAML static checks (job-permission split, no latest/release-* build tags)"
python3 - "$WORKFLOW" <<'PY' || fail "workflow YAML checks failed"
import re
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as handle:
    wf = yaml.safe_load(handle)

jobs = wf.get("jobs", {})
problems = []

# Validation jobs must not be able to request an OIDC token or AWS credentials.
for job_id in ("frontend", "e2e-pr"):
    job = jobs.get(job_id)
    if job is None:
        problems.append(f"{job_id} job missing")
        continue
    perm = job.get("permissions") or {}
    if perm.get("id-token") == "write":
        problems.append(f"{job_id} validation job must not grant id-token: write")
    steps = [s for s in job.get("steps", []) if isinstance(s, dict)]
    if any("configure-aws-credentials" in str(s.get("uses", "")) for s in steps):
        problems.append(f"{job_id} validation job must not configure AWS credentials")

# The build workflow only ever tags sha-* / main-latest / branch-*; it must
# never produce `latest` or a `release-*` tag, and must never invoke the
# promotion script (release tags are minted server-side only by promotion).
for backend in ("auth", "items", "api-gateway"):
    job = jobs.get(backend)
    if job is None:
        problems.append(f"{backend} job missing")
        continue
    steps = [s for s in job.get("steps", []) if isinstance(s, dict)]
    tag_steps = [s for s in steps if s.get("id") == "tags"]
    if not tag_steps:
        problems.append(f"{backend} job has no Compute Docker tags step")
        continue
    tags_text = tag_steps[0].get("run", "")
    if ":latest" in tags_text:
        problems.append(f"{backend} tags computation must not produce `latest` (Decision 4)")
    # `release-input.sh` is sourced by the validator and contains the literal
    # substring `release-` in its filename. Inspect actual tag positions so the
    # job-level validator does not mistake that helper path for a release tag.
    if re.search(r"(?<![A-Za-z0-9_/-])release-", tags_text):
        problems.append(f"{backend} build workflow must never push a release-* tag")

for job in jobs.values():
    if not isinstance(job, dict):
        continue
    for step in job.get("steps", []) if isinstance(job.get("steps"), list) else []:
        if isinstance(step, dict) and "promote-image-digest.sh" in str(step.get("run", "")):
            problems.append("build-and-deploy.yml must not invoke promote-image-digest.sh")

if problems:
    print("\n".join(problems))
    sys.exit(1)
PY

echo "[ 3/10] Immutable-repository desired-state config vs canonical component map"
python3 - "$RELEASE" <<'PY' || fail "immutable config checks failed"
import json
import os
import sys

sys.path.insert(0, os.path.join(sys.argv[1], "src"))
from release_contract import components as rc

with open(os.path.join(sys.argv[1], "ecr", "immutable-repositories.json"), encoding="utf-8") as handle:
    config = json.load(handle)

problems = []
if config.get("accountId") != "799111666795":
    problems.append("config accountId mismatch")
if config.get("region") != "eu-north-1":
    problems.append("config region mismatch")
if sorted(config.get("repositories", [])) != sorted(rc.REPOSITORIES.values()):
    problems.append("config repositories do not match the canonical component map")
if config.get("imageTagMutability") != "IMMUTABLE_WITH_EXCLUSION":
    problems.append("config imageTagMutability must be IMMUTABLE_WITH_EXCLUSION")
filters = sorted(f["filter"] for f in config.get("exclusionFilters", []))
if filters != ["branch-*", "main-latest"]:
    problems.append("config exclusionFilters must be exactly branch-* and main-latest")
if config.get("latestAbsent") is not True:
    problems.append("config latestAbsent must be true (Decision 4)")

if problems:
    print("\n".join(problems))
    sys.exit(1)
PY

write_stub_clis
export PATH="$TMP/bin:$PATH"
export STUB_STATE="$TMP/state.json"
export STUB_CALLS="$TMP/calls.txt"

echo "[ 4/10] verify-immutable-repositories.sh: read-only read-back, drift fails closed, no mutation"
stub_state ok
assert_success bash "$RELEASE/bin/verify-immutable-repositories.sh" --profile dpm-profile --region eu-north-1
grep -q "ecr describe-repositories" "$TMP/calls.txt" || fail "verify must read back describe-repositories"
if grep -q "put-image-tag-mutability" "$TMP/calls.txt"; then
  fail "verify-immutable-repositories.sh must not mutate"
fi
# Mutability drift fails closed.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["repositories"]["onlineshop-auth"]["imageTagMutability"] = "MUTABLE"
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
assert_failure bash "$RELEASE/bin/verify-immutable-repositories.sh" --profile dpm-profile --region eu-north-1
# Missing exclusion filters fail closed.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
del state["repositories"]["onlineshop-items"]["imageTagMutabilityExclusionFilters"]
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
assert_failure bash "$RELEASE/bin/verify-immutable-repositories.sh" --profile dpm-profile --region eu-north-1

echo "[ 5/10] apply-immutable-repositories.sh: put-image-tag-mutability + immediate read-back"
stub_state drift
assert_success bash "$RELEASE/bin/apply-immutable-repositories.sh" --profile dpm-profile --region eu-north-1
[ "$(grep -c "ecr put-image-tag-mutability" "$TMP/calls.txt")" -eq 3 ] || fail "apply must call put-image-tag-mutability for all three repos"
[ "$(grep -c "ecr describe-repositories" "$TMP/calls.txt")" -ge 3 ] || fail "apply must read back every repository after mutation"
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
for repo, cfg in state["repositories"].items():
    assert cfg["imageTagMutability"] == "IMMUTABLE_WITH_EXCLUSION", repo
    filters = sorted(f["filter"] for f in cfg["imageTagMutabilityExclusionFilters"])
    assert filters == ["branch-*", "main-latest"], repo
PY
# Read-back drift (mutation not reflected) fails closed.
stub_state drift
assert_failure env STUB_IGNORE_MUTATIONS=1 bash "$RELEASE/bin/apply-immutable-repositories.sh" --profile dpm-profile --region eu-north-1

echo "[ 6/10] promote-image-digest.sh: mint / idempotent reuse / conflict / dry-run"
PROMOTE=(bash "$RELEASE/bin/promote-image-digest.sh" \
  --repository onlineshop-auth --candidate-tag "$CANDIDATE_TAG" \
  --release-tag "$RELEASE_TAG" --digest "$AUTH_DIGEST" \
  --profile dpm-profile --region eu-north-1)
# mint: release tag absent -> put-image + read-back verification.
stub_state candidates
OUT=$("${PROMOTE[@]}")
assert_contains "$OUT" "action=mint"
assert_contains "$OUT" "verified"
grep -q "ecr put-image onlineshop-auth $RELEASE_TAG" "$TMP/calls.txt" || fail "mint must call ecr put-image"
grep -q "ecr describe-images onlineshop-auth $RELEASE_TAG" "$TMP/calls.txt" || fail "mint must read back the release tag"
# reuse: release tag already resolves to the recorded digest -> no mutation.
stub_state candidates
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["tags"]["onlineshop-auth"]["$RELEASE_TAG"] = "$AUTH_DIGEST"
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$("${PROMOTE[@]}")
assert_contains "$OUT" "action=reuse"
if grep -q "ecr put-image" "$TMP/calls.txt"; then
  fail "idempotent reuse must not mutate"
fi
# conflict: release tag exists at different bytes -> fail closed, no mutation.
stub_state candidates
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["tags"]["onlineshop-auth"]["$RELEASE_TAG"] = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$("${PROMOTE[@]}" 2>&1) && fail "release-tag conflict must fail closed"
assert_contains "$OUT" "RELEASE_TAG_CONFLICT"
if grep -q "ecr put-image" "$TMP/calls.txt"; then
  fail "conflict must never call put-image"
fi
# candidate tag missing -> fail closed.
stub_state candidates
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["tags"]["onlineshop-auth"].pop("$CANDIDATE_TAG")
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
assert_failure "${PROMOTE[@]}"
# dry-run: decision only, no mutation.
stub_state candidates
OUT=$("${PROMOTE[@]}" --dry-run)
assert_contains "$OUT" "dry-run"
if grep -q "ecr put-image" "$TMP/calls.txt"; then
  fail "dry-run must not mutate"
fi

echo "[ 7/10] check-release-identity.sh: proceed / resume / collision fail-closed"
export GITHUB_REPOSITORY=Djimi/OnlineShop-full-stack
IDENTITY=(bash "$RELEASE/bin/check-release-identity.sh" \
  --manifest "$MANIFEST" --bucket onlineshop-frontend-799111666795 \
  --profile dpm-profile --region eu-north-1)
# clean -> proceed
stub_state candidates
OUT=$("${IDENTITY[@]}")
assert_contains "$OUT" "action=proceed"
# resume from ECR release tags -> resume
stub_state candidates
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["tags"]["onlineshop-auth"]["$RELEASE_TAG"] = "$AUTH_DIGEST"
state["tags"]["onlineshop-items"]["$RELEASE_TAG"] = "$ITEMS_DIGEST"
state["tags"]["onlineshop-api-gateway"]["$RELEASE_TAG"] = "$GATEWAY_DIGEST"
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$("${IDENTITY[@]}")
assert_contains "$OUT" "action=resume"
# resume from git tag at the candidate SHA -> resume
stub_state candidates
OUT=$(env STUB_GH_REF_SHA="$SHA" "${IDENTITY[@]}")
assert_contains "$OUT" "action=resume"
# resume from a matching frontend version marker -> resume
stub_state candidates
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["frontendMarker"] = {
    "exists": True,
    "key": "_releases/v1.2.1/release.json",
    "content": {"version": "1.2.1", "sourceSha": "$SHA", "frontendSha256": "b9debb6b25ee6e6e534f7738d27f53f4153dbf361f097336741ae9fb54939ee4"},
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$("${IDENTITY[@]}")
assert_contains "$OUT" "action=resume"
# git tag at a different SHA -> fail closed
stub_state candidates
OUT=$(env STUB_GH_REF_SHA="ffffffffffffffffffffffffffffffffffffffff" "${IDENTITY[@]}" 2>&1) \
  && fail "git-tag conflict must fail closed"
assert_contains "$OUT" "GIT_TAG_CONFLICT"
# annotated git tag (tag object) at the candidate SHA -> peeled to the commit, resume
stub_state candidates
OUT=$(env STUB_GH_REF_SHA="$SHA" STUB_GH_ANNOTATED=1 STUB_GH_PEELED_SHA="$SHA" "${IDENTITY[@]}")
assert_contains "$OUT" "action=resume"
# annotated git tag that peels to a different commit -> fail closed
stub_state candidates
OUT=$(env STUB_GH_REF_SHA="$SHA" STUB_GH_ANNOTATED=1 STUB_GH_PEELED_SHA="ffffffffffffffffffffffffffffffffffffffff" "${IDENTITY[@]}" 2>&1) \
  && fail "annotated git-tag peel conflict must fail closed"
assert_contains "$OUT" "GIT_TAG_CONFLICT"
# one ECR release tag at different bytes -> fail closed
stub_state candidates
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["tags"]["onlineshop-auth"]["$RELEASE_TAG"] = "$AUTH_DIGEST"
state["tags"]["onlineshop-items"]["$RELEASE_TAG"] = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$("${IDENTITY[@]}" 2>&1) && fail "ECR release-tag conflict must fail closed"
assert_contains "$OUT" "ECR_RELEASE_TAG_CONFLICT"
# frontend marker with wrong checksum -> fail closed
stub_state candidates
python3 - "$TMP/state.json" <<PY
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["frontendMarker"] = {
    "exists": True,
    "key": "_releases/v1.2.1/release.json",
    "content": {
        "version": "1.2.1",
        "sourceSha": "$SHA",
        "frontendSha256": "e" * 64,
    },
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$("${IDENTITY[@]}" 2>&1) && fail "frontend marker conflict must fail closed"
assert_contains "$OUT" "FRONTEND_PREFIX_CONFLICT"

echo "[ 8/10] IAM least-privilege + OIDC trust policy validation"
for policy in github-actions-candidate-build-policy.json github-actions-promotion-policy.json \
  github-actions-production-deploy-policy.json github-actions-rollback-policy.json; do
  assert_success env PYTHONPATH="$RELEASE/src" python3 -m release_contract.iam validate-policy --policy "$PLAN_DIR/$policy"
done
assert_success env PYTHONPATH="$RELEASE/src" python3 -m release_contract.iam validate-trust --policy "$PLAN_DIR/github-actions-oidc-trust-policy.json"
for fx in invalid-broad-ecr-resource invalid-getauthtoken-scoped invalid-passrole-unscoped \
  invalid-passrole-no-condition invalid-mutation-wildcard; do
  assert_failure env PYTHONPATH="$RELEASE/src" python3 -m release_contract.iam validate-policy --policy "$RELEASE/fixtures/iam/$fx.json"
done
for fx in invalid-trust-no-aud invalid-trust-no-env-subject invalid-trust-wrong-aud; do
  assert_failure env PYTHONPATH="$RELEASE/src" python3 -m release_contract.iam validate-trust --policy "$RELEASE/fixtures/iam/$fx.json"
done

echo "[ 9/10] Static scan: profile/region on every aws call + mutation read-back"
scripts=(
  "$RELEASE/bin/apply-immutable-repositories.sh"
  "$RELEASE/bin/verify-immutable-repositories.sh"
  "$RELEASE/bin/promote-image-digest.sh"
  "$RELEASE/bin/check-release-identity.sh"
)
# shellcheck disable=SC2094  # read-only scan; $script is only ever read, never written
for script in "${scripts[@]}"; do
  # shellcheck disable=SC2016  # the AWS_ARGS pattern is searched literally
  grep -q 'AWS_ARGS=(--profile "$PROFILE" --region "$REGION")' "$script" \
    || fail "$(basename "$script") must default AWS_ARGS to dpm-profile/eu-north-1"
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^[[:space:]]*aws[[:space:]] ]] || [[ "$line" =~ \$\(aws[[:space:]] ]]; then
      # shellcheck disable=SC2016  # literal pattern; expansion is intended by the searched scripts
      [[ "$line" == *'${AWS_ARGS[@]}'* ]] || fail "$(basename "$script") aws call missing AWS_ARGS: $line"
    fi
  done < "$script"
done
grep -q "verify-immutable-repositories.sh" "$RELEASE/bin/apply-immutable-repositories.sh" \
  || fail "apply-immutable-repositories.sh must read back after mutation"
grep -q "release_contract.ecr verify" "$RELEASE/bin/promote-image-digest.sh" \
  || fail "promote-image-digest.sh must verify both tags after mutation"

echo "[10/10] lint"
if command -v ruff >/dev/null 2>&1; then
  (cd "$RELEASE" && ruff check src tests) || fail "ruff lint failed"
else
  echo "ruff not found; skipping (report this)"
fi
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck \
    "$RELEASE"/bin/apply-immutable-repositories.sh \
    "$RELEASE"/bin/verify-immutable-repositories.sh \
    "$RELEASE"/bin/promote-image-digest.sh \
    "$RELEASE"/bin/check-release-identity.sh \
    "${BASH_SOURCE[0]}" || fail "shellcheck failed"
else
  echo "shellcheck not found; skipping (report this)"
fi

echo "ECR release tagging tests passed."
