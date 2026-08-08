#!/usr/bin/env bash
set -euo pipefail

# Candidate build evidence (Pass 3, subphase 3.2) verification gate.
#
# Runs the offline parts of the 3.2 gate:
#   [1/9] Python tests for candidate/artifact/serialization/frontend modules
#   [2/9] Workflow YAML static checks (serialization, teardown ownership,
#         OCI labels, evidence job, SHA-pinned actions)
#   [3/9] package-frontend.sh: reproducibility, sorted manifest, reject links
#   [4/9] unpack-frontend.sh: safe extraction + checksum verification
#   [5/9] publish-candidate-image.sh: push/reuse/fail-closed decisions
#   [6/9] generate-sbom.sh with a stub pinned tool
#   [7/9] emit-candidate-evidence.sh + emit-candidate-manifest.sh fixture flow
#   [8/9] record-artifact.sh with upload-artifact step outputs
#   [9/9] lint: ruff + shellcheck (report if unavailable)
#
# Live ECR/GitHub/artifact checks (OCI label read-back from ECR, artifact ID
# from a real run, SBOMs from real digests) are deferred to the consolidated
# verification pass and are NOT claimed here.

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
WORKFLOW="$REPO_ROOT/.github/workflows/build-and-deploy.yml"
SHA="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"

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

echo "[1/9] Python syntax + unit/validation tests (candidate, artifact, serialization, frontend)"
python3 -m py_compile "$RELEASE"/src/release_contract/*.py "$RELEASE"/tests/*.py || fail "Python syntax check failed"
(
  cd "$RELEASE" && PYTHONPATH="$RELEASE/src" python3 -m unittest discover -s tests
) || fail "Python validation tests failed"

echo "[2/9] Workflow YAML static checks (build-and-deploy.yml)"
python3 - "$WORKFLOW" "$RELEASE" <<'PY' || fail "workflow YAML checks failed"
import json
import re
import sys

import yaml

workflow_path, release_root = sys.argv[1], sys.argv[2]
with open(workflow_path, encoding="utf-8") as handle:
    wf = yaml.safe_load(handle)

jobs = wf.get("jobs", {})
problems = []

staging = jobs.get("e2e-staging")
if staging is None:
    problems.append("e2e-staging job missing")
else:
    concurrency = staging.get("concurrency")
    if not concurrency:
        problems.append("e2e-staging has no job-level concurrency")
    elif concurrency.get("cancel-in-progress") is not False:
        problems.append("e2e-staging concurrency must set cancel-in-progress: false")
    steps = [s for s in staging.get("steps", []) if isinstance(s, dict)]
    resume = [s for s in steps if isinstance(s.get("run"), str) and "resume-staging.sh" in s.get("run", "")]
    teardown = [s for s in steps if isinstance(s.get("run"), str) and "pause-staging.sh" in s.get("run", "")]
    if not resume:
        problems.append("e2e-staging job does not own resume-staging.sh")
    if not teardown:
        problems.append("e2e-staging job does not own pause-staging.sh teardown")
    elif teardown[0].get("if") != "always()":
        problems.append("e2e-staging teardown step must be `if: always()`")

evidence = jobs.get("candidate-evidence")
if evidence is None:
    problems.append("candidate-evidence job missing")
else:
    needs = evidence.get("needs", [])
    for required in ("auth", "items", "api-gateway", "frontend", "e2e-staging"):
        if required not in needs:
            problems.append(f"candidate-evidence does not depend on {required}")
    steps = [s for s in evidence.get("steps", []) if isinstance(s, dict)]
    uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
    if not any(s.get("with", {}).get("retention-days") == 30 for s in uploads):
        problems.append("candidate-evidence must upload evidence with retention-days: 30")
    if not any(isinstance(s.get("run"), str) and "emit-candidate-evidence.sh" in s.get("run", "") for s in steps):
        problems.append("candidate-evidence does not call emit-candidate-evidence.sh")
    evidence_if = str(evidence.get("if", ""))
    for required in ("auth", "items", "api-gateway", "frontend", "e2e-staging"):
        if f"needs.{required}.result == 'success'" not in evidence_if:
            problems.append(
                f"candidate-evidence if must require needs.{required}.result == 'success'"
            )
    if "emit-candidate-evidence.sh" in " ".join(
        str(s.get("run", "")) for s in steps
    ) and "--producer-run-id" not in " ".join(str(s.get("run", "")) for s in steps):
        problems.append("candidate-evidence emit step must pass --producer-run-id")

    # The evidence bundle upload step must expose its outputs (artifact-id,
    # artifact-url, artifact-digest) and the record step must consume them so
    # the GitHub service-reported digest is recorded, not just a local checksum.
    if not any(s.get("id") == "upload" for s in steps):
        problems.append("candidate-evidence evidence upload step must have id: upload")
    if "record-artifact.sh" not in " ".join(str(s.get("run", "")) for s in steps):
        problems.append("candidate-evidence must call record-artifact.sh")
    if "steps.upload.outputs.artifact-digest" not in " ".join(str(s.get("run", "")) for s in steps):
        problems.append("candidate-evidence record step must consume steps.upload.outputs.artifact-digest")

perm = wf.get("permissions", {})
if perm.get("actions") != "read":
    problems.append("workflow permissions must include actions: read")

for backend in ("auth", "items", "api-gateway"):
    job = jobs.get(backend)
    if job is None:
        problems.append(f"{backend} job missing")
        continue
    steps = [s for s in job.get("steps", []) if isinstance(s, dict)]
    push_steps = [s for s in steps if s.get("uses", "").startswith("docker/build-push-action@")]
    if not push_steps:
        problems.append(f"{backend} job has no docker/build-push-action")
        continue
    labels_input = str(push_steps[0].get("with", {}).get("labels", ""))
    if "steps.labels.outputs.labels" not in labels_input:
        problems.append(f"{backend} push does not consume the computed OCI labels")
    label_steps = [s for s in steps if s.get("name") == "Compute OCI labels"]
    if not label_steps:
        problems.append(f"{backend} job has no Compute OCI labels step")
        continue
    labels_text = label_steps[0].get("run", "")
    for expected_label in (
        "org.opencontainers.image.revision",
        "org.opencontainers.image.source",
        "org.opencontainers.image.created",
        "org.opencontainers.image.title",
        "org.onlineshop.producer.run-id",
        "org.onlineshop.producer.event",
        "org.onlineshop.producer.ref",
    ):
        if expected_label not in labels_text:
            problems.append(f"{backend} Compute OCI labels does not set {expected_label}")
    if backend == "items" and "org.onlineshop.common-revision" not in labels_text:
        problems.append("items Compute OCI labels does not set org.onlineshop.common-revision")
    if not any(isinstance(s.get("run"), str) and "publish-candidate-image.sh" in s.get("run", "") for s in steps):
        problems.append(f"{backend} job does not call publish-candidate-image.sh")

# Release-critical third-party Actions must be pinned by full commit SHA.
# `uses` references in every job must be owner/repo@<40-hex> for the critical
# set; everything else (the only exception) is a local path action.
critical = {
    "actions/checkout", "actions/setup-java", "actions/setup-node", "actions/cache",
    "actions/upload-artifact", "actions/download-artifact",
    "docker/setup-buildx-action", "docker/build-push-action",
    "aws-actions/configure-aws-credentials", "aws-actions/amazon-ecr-login",
    "dorny/paths-filter",
}
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
        match = sha_ref.match(uses)
        if not match:
            problems.append(f"action not pinned by SHA: {uses} (job {name})")
            continue
        if match.group(1) in critical:
            pass  # pinned by SHA as required

if problems:
    print("\n".join(problems))
    sys.exit(1)
PY

echo "[3/9] package-frontend.sh: reproducible archive, sorted manifest, reject links"
mkdir -p "$TMP/dist/assets"
printf '<html>hi</html>' > "$TMP/dist/index.html"
printf 'console.log(1)' > "$TMP/dist/assets/app.js"
assert_success bash "$RELEASE/bin/package-frontend.sh" --dist "$TMP/dist" --out "$TMP/out1"
assert_success bash "$RELEASE/bin/package-frontend.sh" --dist "$TMP/dist" --out "$TMP/out2"
SHA1=$(sha256sum "$TMP/out1/frontend-dist.tar.gz" | awk '{print $1}')
SHA2=$(sha256sum "$TMP/out2/frontend-dist.tar.gz" | awk '{print $1}')
[ "$SHA1" = "$SHA2" ] || fail "frontend archive is not reproducible across builds"
PKG_SHA=$(jq -r '.sha256' "$TMP/out1/frontend-package.json")
[ "$PKG_SHA" = "$SHA1" ] || fail "frontend-package.json sha256 does not match archive"
# Sorted by relative path (assets/ before index.html), entries are <sha>  <path>.
[ "$(jq -r '.fileCount' "$TMP/out1/frontend-package.json")" = "2" ] || fail "fileCount mismatch"
while IFS= read -r line; do
  [[ "$line" =~ ^[0-9a-f]{64}[[:space:]]{2}\./(assets/.*|index\.html)$ ]] || fail "malformed manifest line: $line"
done < "$TMP/out1/frontend-dist.sha256"
# Symlink must be rejected.
mkdir -p "$TMP/bad"
ln -s /etc/passwd "$TMP/bad/link"
printf 'x' > "$TMP/bad/f"
assert_failure bash "$RELEASE/bin/package-frontend.sh" --dist "$TMP/bad" --out "$TMP/badout"
# Usage error exit code.
assert_failure bash "$RELEASE/bin/package-frontend.sh" --dist /nonexistent --out "$TMP/x"

echo "[4/9] unpack-frontend.sh: safe extraction + checksum verification"
assert_success bash "$RELEASE/bin/unpack-frontend.sh" \
  --archive "$TMP/out1/frontend-dist.tar.gz" \
  --manifest "$TMP/out1/frontend-dist.sha256" \
  --dest "$TMP/extracted"
[ -f "$TMP/extracted/index.html" ] || fail "clean archive did not extract"
# Malicious archives must be rejected before extraction.
python3 - "$TMP/evil-traversal.tar.gz" <<'PY'
import sys
import tarfile

with tarfile.open(sys.argv[1], "w:gz") as tar:
    info = tarfile.TarInfo("app/../../escape")
    info.size = 0
    tar.addfile(info)
PY
assert_failure bash "$RELEASE/bin/unpack-frontend.sh" \
  --archive "$TMP/evil-traversal.tar.gz" --manifest "$TMP/out1/frontend-dist.sha256" --dest "$TMP/evilext"
[ ! -e "$TMP/evilext/app" ] || fail "malicious archive was extracted"
python3 - "$TMP/evil-symlink.tar.gz" <<'PY'
import sys
import tarfile

with tarfile.open(sys.argv[1], "w:gz") as tar:
    info = tarfile.TarInfo("app/link")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/passwd"
    tar.addfile(info)
PY
assert_failure bash "$RELEASE/bin/unpack-frontend.sh" \
  --archive "$TMP/evil-symlink.tar.gz" --manifest "$TMP/out1/frontend-dist.sha256" --dest "$TMP/evilext2"

echo "[5/9] publish-candidate-image.sh: push / reuse / fail-closed"
mkdir -p "$TMP/stub/bin"
CANONICAL_LABELS='{"org.opencontainers.image.revision":"'$SHA'","org.onlineshop.producer.run-id":"123456789","org.onlineshop.producer.run-attempt":"1","org.onlineshop.producer.event":"push","org.onlineshop.producer.ref":"refs/heads/main"}'
cat > "$TMP/stub/bin/aws" <<'EOF'
#!/usr/bin/env bash
echo "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
EOF
# image-labels.sh reads the config blob via `docker buildx imagetools inspect
# --format '{{json .Image}}'`; the stub ignores args and returns the config.
# The labels JSON is passed via LABELS_JSON so inner quotes survive.
cat > "$TMP/stub/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '{"config":{"Labels":%s}}\n' "$LABELS_JSON"
EOF
cat > "$TMP/stub/bin/gh" <<'EOF'
#!/usr/bin/env bash
echo '{"conclusion":"success","head_sha":"a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"}'
EOF
chmod +x "$TMP/stub/bin/aws" "$TMP/stub/bin/docker" "$TMP/stub/bin/gh"
COMMON_ENV=(GITHUB_REPOSITORY=Djimi/OnlineShop-full-stack GITHUB_TOKEN=t)
# Feature branch -> always push (current behavior preserved).
OUT=$(GITHUB_EVENT_NAME=push GITHUB_REF=refs/heads/feature/x GITHUB_SHA="$SHA" GITHUB_RUN_ID=1 GITHUB_RUN_ATTEMPT=1 \
  env "${COMMON_ENV[@]}" bash "$RELEASE/bin/publish-candidate-image.sh" --profile dpm-profile --region eu-north-1)
assert_contains "$OUT" "decision=push"
# Main + no existing tag (image-labels.sh exits 3) -> push.
cat > "$TMP/stub/bin/aws" <<'EOF'
#!/usr/bin/env bash
echo ""
EOF
OUT=$(PATH="$TMP/stub/bin:$PATH" \
GITHUB_EVENT_NAME=push GITHUB_REF=refs/heads/main GITHUB_SHA="$SHA" GITHUB_RUN_ID=9 GITHUB_RUN_ATTEMPT=1 \
ECR_REGISTRY=r ECR_REPOSITORY=onlineshop-auth CANDIDATE_TAG="sha-$SHA" \
  env "${COMMON_ENV[@]}" bash "$RELEASE/bin/publish-candidate-image.sh" --profile p --region eu-north-1)
assert_contains "$OUT" "decision=push"
# Main + canonical existing -> reuse.
cat > "$TMP/stub/bin/aws" <<'EOF'
#!/usr/bin/env bash
echo "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
EOF
OUT=$(PATH="$TMP/stub/bin:$PATH" LABELS_JSON="$CANONICAL_LABELS" \
GITHUB_EVENT_NAME=push GITHUB_REF=refs/heads/main GITHUB_SHA="$SHA" GITHUB_RUN_ID=123456789 GITHUB_RUN_ATTEMPT=2 \
ECR_REGISTRY=799111666795.dkr.ecr.eu-north-1.amazonaws.com ECR_REPOSITORY=onlineshop-auth CANDIDATE_TAG="sha-$SHA" \
  env "${COMMON_ENV[@]}" bash "$RELEASE/bin/publish-candidate-image.sh" --profile dpm-profile --region eu-north-1)
assert_contains "$OUT" "decision=reuse"
# Manual dispatch on main with an existing trusted canonical tag -> reuse, never overwrite.
OUT=$(PATH="$TMP/stub/bin:$PATH" LABELS_JSON="$CANONICAL_LABELS" \
GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF=refs/heads/main GITHUB_SHA="$SHA" GITHUB_RUN_ID=555 GITHUB_RUN_ATTEMPT=1 \
ECR_REGISTRY=799111666795.dkr.ecr.eu-north-1.amazonaws.com ECR_REPOSITORY=onlineshop-auth CANDIDATE_TAG="sha-$SHA" \
  env "${COMMON_ENV[@]}" bash "$RELEASE/bin/publish-candidate-image.sh" --profile dpm-profile --region eu-north-1)
assert_contains "$OUT" "decision=reuse"
# Producer run failed -> fail closed.
cat > "$TMP/stub/bin/gh" <<'EOF'
#!/usr/bin/env bash
echo '{"conclusion":"failure","head_sha":"a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"}'
EOF
assert_failure env PATH="$TMP/stub/bin:$PATH" LABELS_JSON="$CANONICAL_LABELS" \
GITHUB_EVENT_NAME=push GITHUB_REF=refs/heads/main GITHUB_SHA="$SHA" GITHUB_RUN_ID=123456789 GITHUB_RUN_ATTEMPT=2 \
ECR_REGISTRY=799111666795.dkr.ecr.eu-north-1.amazonaws.com ECR_REPOSITORY=onlineshop-auth CANDIDATE_TAG="sha-$SHA" \
  "${COMMON_ENV[@]}" bash "$RELEASE/bin/publish-candidate-image.sh" --profile dpm-profile --region eu-north-1

# verify-producer-set.sh: canonical set passes; split producer fails closed.
cat > "$TMP/stub/bin/aws" <<'EOF'
#!/usr/bin/env bash
echo "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
EOF
# canonical (all three from the same run id)
cat > "$TMP/stub/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "{\"config\":{\"Labels\":{\"org.opencontainers.image.revision\":\"${CANONICAL_SHA}\",\"org.onlineshop.producer.run-id\":\"123456789\",\"org.onlineshop.producer.run-attempt\":\"1\",\"org.onlineshop.producer.event\":\"push\",\"org.onlineshop.producer.ref\":\"refs/heads/main\",\"org.onlineshop.common-revision\":\"${CANONICAL_SHA}\"}}}"
EOF
chmod +x "$TMP/stub/bin/aws" "$TMP/stub/bin/docker"
assert_success env PATH="$TMP/stub/bin:$PATH" CANONICAL_SHA="$SHA" bash "$RELEASE/bin/verify-producer-set.sh" \
  --sha "$SHA" --registry r --profile p --region eu-north-1
# split producer (gateway from a different run)
cat > "$TMP/stub/bin/docker" <<'EOF'
#!/usr/bin/env bash
RID="123456789"
for a in "$@"; do
  case "$a" in
    *onlineshop-api-gateway*) RID="424242" ;;
  esac
done
echo "{\"config\":{\"Labels\":{\"org.opencontainers.image.revision\":\"${CANONICAL_SHA}\",\"org.onlineshop.producer.run-id\":\"$RID\",\"org.onlineshop.producer.run-attempt\":\"1\",\"org.onlineshop.producer.event\":\"push\",\"org.onlineshop.producer.ref\":\"refs/heads/main\",\"org.onlineshop.common-revision\":\"${CANONICAL_SHA}\"}}}"
EOF
assert_failure env PATH="$TMP/stub/bin:$PATH" CANONICAL_SHA="$SHA" bash "$RELEASE/bin/verify-producer-set.sh" \
  --sha "$SHA" --registry r --profile p --region eu-north-1

echo "[6/9] generate-sbom.sh with a stub pinned tool"
cat > "$TMP/fake-syft" <<'EOF'
#!/usr/bin/env bash
out=""
for a in "$@"; do
  case "$a" in
    spdx-json=*) out="${a#spdx-json=}" ;;
  esac
done
printf '{"spdxVersion":"SPDX-2.3","name":"fixture"}' > "$out"
EOF
chmod +x "$TMP/fake-syft"
assert_success env SYFT_TOOL="$TMP/fake-syft" bash "$RELEASE/bin/generate-sbom.sh" \
  --target "registry:r/onlineshop-auth@sha256:abc" --output "$TMP/auth.spdx.json"
jq -e '.spdxVersion == "SPDX-2.3"' "$TMP/auth.spdx.json" >/dev/null || fail "stub SBOM not written"
assert_failure bash "$RELEASE/bin/generate-sbom.sh" --target x

echo "[7/9] emit-candidate-evidence.sh + emit-candidate-manifest.sh fixture flow"
mkdir -p "$TMP/bundle"
assert_success bash "$RELEASE/bin/package-frontend.sh" --dist "$TMP/dist" --out "$TMP/bundle"
for sbom in frontend.spdx.json auth.spdx.json items.spdx.json api-gateway.spdx.json; do
  printf '{"spdxVersion":"SPDX-2.3"}' > "$TMP/bundle/$sbom"
done
FRONTEND_SHA=$(jq -r '.sha256' "$TMP/bundle/frontend-package.json")
printf '{"auth":"success","items":"success","apiGateway":"success","frontend":"success","e2eStaging":"success"}' > "$TMP/conclusions.json"
EVENT_ENV=(GITHUB_REPOSITORY=Djimi/OnlineShop-full-stack GITHUB_SHA="$SHA" GITHUB_ACTOR=djimi GITHUB_RUN_ID=123456789 GITHUB_RUN_ATTEMPT=1 GITHUB_EVENT_NAME=push GITHUB_REF=refs/heads/main ECR_REGISTRY=799111666795.dkr.ecr.eu-north-1.amazonaws.com)
assert_success env "${EVENT_ENV[@]}" bash "$RELEASE/bin/emit-candidate-evidence.sh" \
  --bundle-dir "$TMP/bundle" --artifact-name "candidate-evidence-$SHA-1" \
  --auth-digest "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0" \
  --items-digest "sha256:1a78d43d2aaaca3cafe41539f5fe211f4ef45152a1fa6af5b85006e93b019452" \
  --api-gateway-digest "sha256:31f9bfdf4d15f460167ba2b7ce9ada23278be1a9f115f06c01e5242901e8948e" \
  --frontend-sha256 "$FRONTEND_SHA" --validated-at "2026-08-04T13:00:00Z" --conclusions "$TMP/conclusions.json"
jq -e '.release.sourceSha == "'$SHA'"' "$TMP/bundle/candidate-evidence.json" >/dev/null || fail "evidence sourceSha wrong"
jq -e '.release.stagingValidation.job == "e2e-staging"' "$TMP/bundle/candidate-evidence.json" >/dev/null || fail "staging evidence missing"
assert_success env "${EVENT_ENV[@]}" bash "$RELEASE/bin/emit-candidate-manifest.sh" \
  --evidence "$TMP/bundle/candidate-evidence.json" --version 1.2.1 --output "$TMP/manifest.json"
assert_success bash "$RELEASE/bin/validate-manifest.sh" "$TMP/manifest.json"
jq -e '.release.status == "candidate" and .release.version == "1.2.1"' "$TMP/manifest.json" >/dev/null || fail "rendered manifest wrong"
assert_failure env "${EVENT_ENV[@]}" bash "$RELEASE/bin/emit-candidate-manifest.sh" \
  --evidence "$TMP/bundle/candidate-evidence.json" --version 1.2.1-beta --output "$TMP/badmanifest.json"
# A failing conclusion must prevent the evidence bundle from being emitted.
mkdir -p "$TMP/bundle-fail"
assert_success bash "$RELEASE/bin/package-frontend.sh" --dist "$TMP/dist" --out "$TMP/bundle-fail"
for sbom in frontend.spdx.json auth.spdx.json items.spdx.json api-gateway.spdx.json; do
  printf '{"spdxVersion":"SPDX-2.3"}' > "$TMP/bundle-fail/$sbom"
done
FRONTEND_SHA_FAIL=$(jq -r '.sha256' "$TMP/bundle-fail/frontend-package.json")
printf '{"auth":"success","items":"failure","apiGateway":"success","frontend":"success","e2eStaging":"success"}' > "$TMP/conclusions-fail.json"
assert_failure env "${EVENT_ENV[@]}" bash "$RELEASE/bin/emit-candidate-evidence.sh" \
  --bundle-dir "$TMP/bundle-fail" --artifact-name "candidate-evidence-$SHA-1" \
  --auth-digest "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0" \
  --items-digest "sha256:1a78d43d2aaaca3cafe41539f5fe211f4ef45152a1fa6af5b85006e93b019452" \
  --api-gateway-digest "sha256:31f9bfdf4d15f460167ba2b7ce9ada23278be1a9f115f06c01e5242901e8948e" \
  --frontend-sha256 "$FRONTEND_SHA_FAIL" --validated-at "2026-08-04T13:00:00Z" --conclusions "$TMP/conclusions-fail.json"
# Rerun attribution: a rerun reuses the original producer's bytes, so the
# evidence must point candidateWorkflow at the artifact-producing run (attempt 2)
# and artifactWorkflow at the current staging-validation run (attempt 1).
mkdir -p "$TMP/bundle-rerun"
assert_success bash "$RELEASE/bin/package-frontend.sh" --dist "$TMP/dist" --out "$TMP/bundle-rerun"
for sbom in frontend.spdx.json auth.spdx.json items.spdx.json api-gateway.spdx.json; do
  printf '{"spdxVersion":"SPDX-2.3"}' > "$TMP/bundle-rerun/$sbom"
done
FRONTEND_SHA_RERUN=$(jq -r '.sha256' "$TMP/bundle-rerun/frontend-package.json")
assert_success env "${EVENT_ENV[@]}" bash "$RELEASE/bin/emit-candidate-evidence.sh" \
  --bundle-dir "$TMP/bundle-rerun" --artifact-name "candidate-evidence-$SHA-1" \
  --auth-digest "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0" \
  --items-digest "sha256:1a78d43d2aaaca3cafe41539f5fe211f4ef45152a1fa6af5b85006e93b019452" \
  --api-gateway-digest "sha256:31f9bfdf4d15f460167ba2b7ce9ada23278be1a9f115f06c01e5242901e8948e" \
  --frontend-sha256 "$FRONTEND_SHA_RERUN" --validated-at "2026-08-04T13:00:00Z" \
  --producer-run-id 123456789 --producer-run-attempt 2 --conclusions "$TMP/conclusions.json"
jq -e '.release.candidateWorkflow.runId == 123456789 and .release.candidateWorkflow.runAttempt == 2' "$TMP/bundle-rerun/candidate-evidence.json" >/dev/null \
  || fail "evidence candidateWorkflow must name the artifact-producing run (producer attempt)"
jq -e '.release.artifactWorkflow.runId == 123456789 and .release.artifactWorkflow.runAttempt == 1' "$TMP/bundle-rerun/candidate-evidence.json" >/dev/null \
  || fail "evidence artifactWorkflow must name the current staging-validation run"
# Evidence bundle verification.
assert_success env PYTHONPATH="$RELEASE/src" python3 -m release_contract.artifact verify \
  --bundle-dir "$TMP/bundle" --frontend-sha256 "$FRONTEND_SHA"

echo "[8/9] record-artifact.sh records upload-artifact step outputs"
DIGEST="50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
assert_success bash "$RELEASE/bin/record-artifact.sh" \
  --run-id 123456789 --run-attempt 1 \
  --artifact-id 987 \
  --artifact-url "https://github.com/Djimi/OnlineShop-full-stack/actions/runs/123456789/artifacts/987" \
  --artifact-digest "$DIGEST" \
  --artifact-name "candidate-evidence-$SHA-1" \
  --output "$TMP/artifact-id.json"
jq -e '.runId == 123456789 and .runAttempt == 1 and .artifactId == 987 and .artifactDigest == "'"$DIGEST"'" and .name == "candidate-evidence-'"$SHA"'-1"' \
  "$TMP/artifact-id.json" >/dev/null || fail "artifact record not written correctly"
# Invalid inputs fail closed.
assert_failure bash "$RELEASE/bin/record-artifact.sh" \
  --run-id 0 --run-attempt 1 --artifact-id 987 --artifact-url "https://x" --artifact-digest "$DIGEST" \
  --artifact-name "candidate-evidence-$SHA-1" --output "$TMP/bad.json"
assert_failure bash "$RELEASE/bin/record-artifact.sh" \
  --run-id 123456789 --run-attempt 1 --artifact-id 987 --artifact-url "not-a-url" \
  --artifact-digest "$DIGEST" --artifact-name "candidate-evidence-$SHA-1" --output "$TMP/bad.json"
assert_failure bash "$RELEASE/bin/record-artifact.sh" \
  --run-id 123456789 --run-attempt 1 --artifact-id 987 --artifact-url "https://x" \
  --artifact-digest "nothex" --artifact-name "candidate-evidence-$SHA-1" --output "$TMP/bad.json"

echo "[9/9] lint"
if command -v ruff >/dev/null 2>&1; then
  (cd "$RELEASE" && ruff check src tests) || fail "ruff lint failed"
else
  echo "ruff not found; skipping (report this)"
fi
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck \
    "$RELEASE"/bin/package-frontend.sh \
    "$RELEASE"/bin/unpack-frontend.sh \
    "$RELEASE"/bin/generate-sbom.sh \
    "$RELEASE"/bin/image-labels.sh \
    "$RELEASE"/bin/verify-producer-set.sh \
    "$RELEASE"/bin/publish-candidate-image.sh \
    "$RELEASE"/bin/emit-candidate-evidence.sh \
    "$RELEASE"/bin/emit-candidate-manifest.sh \
    "$RELEASE"/bin/record-artifact.sh \
    "${BASH_SOURCE[0]}" || fail "shellcheck failed"
else
  echo "shellcheck not found; skipping (report this)"
fi

echo "Candidate evidence tests passed."
