#!/usr/bin/env bash
set -euo pipefail

# Candidate build evidence (Pass 3, subphase 3.2) verification gate.
#
# Runs the offline parts of the 3.2 gate:
#   [1/9] Python tests for candidate/artifact/serialization/frontend modules
#   [2/9] Workflow YAML static checks (ci.yml producer + stage-candidate.yml:
#         run/attempt-scoped artifacts, retention, candidate manifest emit,
#         ECR digest read-back, SHA-pinned actions)
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
WORKFLOW="$REPO_ROOT/.github/workflows/ci.yml"
STAGING_WORKFLOW="$REPO_ROOT/.github/workflows/stage-candidate.yml"
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

echo "[2/9] Workflow YAML static checks (ci.yml producer + stage-candidate.yml)"
python3 - "$WORKFLOW" "$STAGING_WORKFLOW" <<'PY' || fail "workflow YAML checks failed"
import re
import sys

import yaml

ci_path, staging_path = sys.argv[1], sys.argv[2]
with open(ci_path, encoding="utf-8") as handle:
    wf = yaml.safe_load(handle)
with open(staging_path, encoding="utf-8") as handle:
    staging_wf = yaml.safe_load(handle)

jobs = wf.get("jobs", {})
problems = []

# The candidate producer is the single publish job on push only; it must
# depend on every validation gate and never run with pull-request credentials.
publish = jobs.get("publish")
if publish is None:
    problems.append("publish job missing")
else:
    if str(publish.get("if", "")) != "github.event_name == 'push'":
        problems.append("publish must run only on push (if: github.event_name == 'push')")
    needs = publish.get("needs", [])
    for required in ("test-auth", "test-items", "test-gateway", "test-frontend", "e2e"):
        if required not in needs:
            problems.append(f"publish does not depend on {required}")
    publish_permissions = publish.get("permissions") or {}
    if publish_permissions.get("actions") != "read":
        problems.append("publish job permissions must include actions: read")
    steps = [s for s in publish.get("steps", []) if isinstance(s, dict)]

    # The four run/attempt-scoped evidence artifacts: candidate manifest,
    # frontend archive, SBOMs, and the test-results bundle. Each upload must
    # fail closed when the file is missing and carry the 30-day candidate
    # retention class.
    uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
    if not uploads:
        problems.append("publish job has no actions/upload-artifact uploads")
    else:
        upload_names = [str(s.get("with", {}).get("name", "")) for s in uploads]
        for artifact in ("candidate-manifest", "frontend-archive", "sboms", "test-results"):
            if not any(artifact in name for name in upload_names):
                problems.append(f"publish does not upload the {artifact} artifact")
        for step in uploads:
            with_values = step.get("with", {})
            if with_values.get("retention-days") != 30:
                problems.append("publish evidence upload must set retention-days: 30")
        # The manifest is the evidence anchor and must fail closed; the aux
        # artifacts (frontend archive, SBOMs, test results) intentionally use
        # the upload-artifact default (warn) because the staging gate enforces
        # the full-set existence contract (CT-CAND-03) before any deployment.
        manifest_uploads = [
            s for s in uploads if "candidate-manifest" in str(s.get("with", {}).get("name", ""))
        ]
        if not manifest_uploads:
            problems.append("publish does not upload the candidate-manifest artifact")
        elif manifest_uploads[0].get("with", {}).get("if-no-files-found") != "error":
            problems.append("candidate-manifest upload must fail closed (if-no-files-found: error)")
        artifact_names = " ".join(upload_names)
        if "${{ github.run_id }}" not in artifact_names:
            problems.append("publish evidence artifacts must embed github.run_id")
        if "${{ github.run_attempt }}" not in artifact_names:
            problems.append("publish evidence artifacts must embed github.run_attempt")

    # The candidate manifest must be emitted and validated by the delivery
    # engine (never assembled by hand), with the run/attempt inputs coming
    # from step env (the security contract forbids contexts in run: scripts).
    run_text = " ".join(str(s.get("run", "")) for s in steps)
    if "candidate manifest" not in run_text:
        problems.append("publish does not emit the candidate manifest via the delivery engine")
    if "candidate validate" not in run_text:
        problems.append("publish does not validate the candidate manifest via the delivery engine")
    if "--producer-run-id" in run_text:
        problems.append("publish must not use the legacy --producer-run-id contract")
    if "publish-candidate-image.sh" in run_text:
        problems.append("publish must not invoke the legacy publish-candidate-image.sh")
    steps_text = " ".join(str(s) for s in steps)
    if "steps.upload.outputs.artifact-digest" in steps_text:
        problems.append("publish must not use the legacy record-artifact.sh output contract")

    # ECR digests must be read back from the service (describe-images, never
    # batch-get, which returns a null imageDigest for multi-arch manifests).
    digest_step = next((s for s in steps if s.get("id") == "digests"), None)
    if digest_step is None:
        problems.append("publish has no id: digests ECR read-back step")
    elif "describe-images" not in str(digest_step.get("run", "")):
        problems.append("digests step must use aws ecr describe-images")

# The staging lifecycle owns the exact-candidate deployment and the staging
# record (14-day retention); it is not part of the push workflow.
staging_jobs = staging_wf.get("jobs", {})
stage = staging_jobs.get("stage")
if stage is None:
    problems.append("stage-candidate.yml stage job missing")
else:
    steps = [s for s in stage.get("steps", []) if isinstance(s, dict)]
    uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
    if not any(str(s.get("with", {}).get("name", "")).startswith("staging-record") for s in uploads):
        problems.append("stage job does not upload the staging-record artifact")
    for step in uploads:
        if str(step.get("with", {}).get("name", "")).startswith("staging-record"):
            if step.get("with", {}).get("retention-days") != 14:
                problems.append("staging-record upload must set retention-days: 14")
            if step.get("with", {}).get("if-no-files-found") != "error":
                problems.append("staging-record upload must fail closed (if-no-files-found: error)")
    run_text = " ".join(str(s.get("run", "")) for s in steps)
    if "resume-staging.sh" in run_text or "pause-staging.sh" in run_text:
        problems.append("stage job must not use the legacy lifecycle scripts")

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
for wf_name, wf_jobs in (("ci.yml", jobs), ("stage-candidate.yml", staging_jobs)):
    for name, job in wf_jobs.items():
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
                problems.append(f"action not pinned by SHA: {uses} (job {name} in {wf_name})")
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
