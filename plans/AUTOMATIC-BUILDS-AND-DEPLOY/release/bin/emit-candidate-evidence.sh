#!/usr/bin/env bash
set -euo pipefail

# Assembles the candidate evidence bundle (Pass 3, subphase 3.2).
#
# A successful `main` push workflow run emits exactly one evidence bundle after
# Auth, Items, API Gateway, frontend, and the cloud staging E2E job all pass.
# The bundle records the immutable facts (run id/attempt, event, ref, full SHA,
# actor, per-job test conclusions, ECR digests for each backend, frontend
# archive checksum, staging validation evidence) in `candidate-evidence.json`
# plus the frontend archive, its sorted checksum manifest, the four SPDX
# SBOMs, and a sorted `checksums.txt`.
#
# The bundle is a facts index, not a release manifest: the SemVer is assigned
# at promotion time (Decision 3), so the schema-valid candidate manifest is
# rendered by `emit-candidate-manifest.sh` (or `release_contract.candidate
# build-manifest`) when the owner selects a version. See
# plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/README.md.
#
# The bundle attributes the *artifact-producing* run (Decision 11) separately
# from the run that performed staging validation and emitted the evidence:
#   candidateWorkflow = the run that produced the candidate images/frontend
#                       (--producer-run-id/--producer-run-attempt; defaults to
#                       the current run, which is correct for the first attempt)
#   artifactWorkflow  = the current run (it validated staging and emitted this
#                       bundle)
# On a rerun the current run reuses the original producer's bytes, so the
# candidate workflow must point at the original producer, never the rerun.
#
# Usage:
#   emit-candidate-evidence.sh \
#     --bundle-dir <dir> \
#     --artifact-name candidate-evidence-<sha>-<attempt> \
#     --auth-digest sha256:... --items-digest sha256:... --api-gateway-digest sha256:... \
#     --frontend-sha256 <archive-sha256> \
#     --validated-at <RFC3339-UTC> \
#     [--producer-run-id <run> --producer-run-attempt <attempt>] \
#     [--conclusions <json-file>]
#
# Environment: GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_ACTOR, GITHUB_RUN_ID,
# GITHUB_RUN_ATTEMPT, GITHUB_EVENT_NAME, GITHUB_REF, ECR_REGISTRY.
#
# Exit codes: 0 success, 1 evidence error, 2 usage error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" >&2
}

BUNDLE_DIR=""
ARTIFACT_NAME=""
AUTH_DIGEST=""
ITEMS_DIGEST=""
GATEWAY_DIGEST=""
FRONTEND_SHA256=""
VALIDATED_AT=""
PRODUCER_RUN_ID=""
PRODUCER_RUN_ATTEMPT=""
CONCLUSIONS=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle-dir) BUNDLE_DIR="${2:-}"; shift 2 ;;
    --artifact-name) ARTIFACT_NAME="${2:-}"; shift 2 ;;
    --auth-digest) AUTH_DIGEST="${2:-}"; shift 2 ;;
    --items-digest) ITEMS_DIGEST="${2:-}"; shift 2 ;;
    --api-gateway-digest) GATEWAY_DIGEST="${2:-}"; shift 2 ;;
    --frontend-sha256) FRONTEND_SHA256="${2:-}"; shift 2 ;;
    --validated-at) VALIDATED_AT="${2:-}"; shift 2 ;;
    --producer-run-id) PRODUCER_RUN_ID="${2:-}"; shift 2 ;;
    --producer-run-attempt) PRODUCER_RUN_ATTEMPT="${2:-}"; shift 2 ;;
    --conclusions) CONCLUSIONS="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[ -n "$BUNDLE_DIR" ] && [ -n "$ARTIFACT_NAME" ] || { usage; exit 2; }
[ -d "$BUNDLE_DIR" ] || { echo "ERROR: --bundle-dir does not exist: $BUNDLE_DIR" >&2; exit 1; }

for var in GITHUB_REPOSITORY GITHUB_SHA GITHUB_ACTOR GITHUB_RUN_ID GITHUB_RUN_ATTEMPT \
  GITHUB_EVENT_NAME GITHUB_REF ECR_REGISTRY; do
  [ -n "${!var:-}" ] || { echo "ERROR: required environment variable $var is not set" >&2; exit 2; }
done

# Strict input validation (subphase 3.1 helpers) — dispatch/run values are
# validated before any use and never interpolated into command strings.
rl_assert_full_sha "$GITHUB_SHA"
rl_assert_positive_integer "$GITHUB_RUN_ID"
rl_assert_positive_integer "$GITHUB_RUN_ATTEMPT"
rl_assert_sha256_hex "${FRONTEND_SHA256:-}" || { echo "ERROR: invalid frontend archive checksum" >&2; exit 1; }
rl_assert_github_login "$GITHUB_ACTOR"
[ "$GITHUB_EVENT_NAME" = "push" ] || { echo "ERROR: candidate evidence requires a push event, got $GITHUB_EVENT_NAME" >&2; exit 1; }
[ "$GITHUB_REF" = "refs/heads/main" ] || { echo "ERROR: candidate evidence requires refs/heads/main, got $GITHUB_REF" >&2; exit 1; }
[ -n "$VALIDATED_AT" ] || { echo "ERROR: --validated-at is empty" >&2; exit 1; }
[[ "$VALIDATED_AT" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || {
  echo "ERROR: --validated-at is not an RFC3339 UTC timestamp: $VALIDATED_AT" >&2
  exit 1
}

# The artifact-producing run defaults to the current run (correct for the first
# attempt); a rerun that reused the original producer's bytes must pass the
# original run id/attempt so the evidence never attributes the bytes to the rerun.
if [ -n "$PRODUCER_RUN_ID" ]; then
  rl_assert_positive_integer "$PRODUCER_RUN_ID" || { echo "ERROR: invalid --producer-run-id" >&2; exit 1; }
else
  PRODUCER_RUN_ID="$GITHUB_RUN_ID"
fi
if [ -n "$PRODUCER_RUN_ATTEMPT" ]; then
  rl_assert_positive_integer "$PRODUCER_RUN_ATTEMPT" || { echo "ERROR: invalid --producer-run-attempt" >&2; exit 1; }
else
  PRODUCER_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT"
fi

for var in AUTH_DIGEST ITEMS_DIGEST GATEWAY_DIGEST; do
  [ -n "${!var:-}" ] || { echo "ERROR: $var is not set" >&2; exit 1; }
  rl_assert_sha256_hex "${!var#sha256:}" || { echo "ERROR: invalid $var" >&2; exit 1; }
done

# Every expected file must already be staged in the bundle dir.
for file in frontend-dist.tar.gz frontend-dist.sha256 frontend.spdx.json \
  auth.spdx.json items.spdx.json api-gateway.spdx.json; do
  [ -f "$BUNDLE_DIR/$file" ] || { echo "ERROR: staged evidence file missing: $BUNDLE_DIR/$file" >&2; exit 1; }
done

# Read-back: the archive checksum the workflow recorded must match the archive.
ACTUAL_ARCHIVE_SHA=$(sha256sum "$BUNDLE_DIR/frontend-dist.tar.gz")
ACTUAL_ARCHIVE_SHA=${ACTUAL_ARCHIVE_SHA%% *}
[ "$ACTUAL_ARCHIVE_SHA" = "$FRONTEND_SHA256" ] || {
  echo "ERROR: frontend archive sha256 $ACTUAL_ARCHIVE_SHA does not match recorded $FRONTEND_SHA256" >&2
  exit 1
}

CONCLUSIONS_JSON="{}"
if [ -n "$CONCLUSIONS" ]; then
  [ -f "$CONCLUSIONS" ] || { echo "ERROR: conclusions file not found: $CONCLUSIONS" >&2; exit 1; }
  CONCLUSIONS_JSON=$(cat "$CONCLUSIONS")
  # The bundle is emitted only after Auth, Items, API Gateway, frontend, and
  # cloud staging E2E all pass; any non-success conclusion must fail the emit.
  printf '%s' "$CONCLUSIONS_JSON" | jq -e '(.auth == "success") and (.items == "success") and (.apiGateway == "success") and (.frontend == "success") and (.e2eStaging == "success")' >/dev/null 2>&1 || {
    echo "ERROR: not every required job concluded success:" >&2
    printf '%s' "$CONCLUSIONS_JSON" | jq -r 'to_entries[] | "  \(.key)=\(.value)"' >&2
    exit 1
  }
fi

CREATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

CANDIDATE_TAG="sha-$GITHUB_SHA"
jq -n \
  --argjson schemaVersion 1 \
  --arg type "candidate-evidence" \
  --arg createdAt "$CREATED_AT" \
  --arg sourceSha "$GITHUB_SHA" \
  --arg repository "$GITHUB_REPOSITORY" \
  --arg actor "$GITHUB_ACTOR" \
  --argjson candidateRunId "$PRODUCER_RUN_ID" \
  --argjson candidateRunAttempt "$PRODUCER_RUN_ATTEMPT" \
  --arg candidateUrl "https://github.com/${GITHUB_REPOSITORY}/actions/runs/${PRODUCER_RUN_ID}/attempts/${PRODUCER_RUN_ATTEMPT}" \
  --argjson artifactRunId "$GITHUB_RUN_ID" \
  --argjson artifactRunAttempt "$GITHUB_RUN_ATTEMPT" \
  --arg artifactUrl "https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}/attempts/${GITHUB_RUN_ATTEMPT}" \
  --arg validatedAt "$VALIDATED_AT" \
  --argjson conclusions "$CONCLUSIONS_JSON" \
  --arg authDigest "$AUTH_DIGEST" \
  --arg itemsDigest "$ITEMS_DIGEST" \
  --arg gatewayDigest "$GATEWAY_DIGEST" \
  --arg registry "$ECR_REGISTRY" \
  --arg candidateTag "$CANDIDATE_TAG" \
  --arg frontendSha256 "$FRONTEND_SHA256" \
  --arg artifactName "$ARTIFACT_NAME" \
  '{
    schemaVersion: $schemaVersion,
    type: $type,
    createdAt: $createdAt,
    release: {
      sourceSha: $sourceSha,
      repository: $repository,
      actor: $actor,
      candidateWorkflow: { runId: $candidateRunId, runAttempt: $candidateRunAttempt, url: $candidateUrl, event: "push", ref: "refs/heads/main", conclusion: "success" },
      artifactWorkflow: { runId: $artifactRunId, runAttempt: $artifactRunAttempt, url: $artifactUrl, event: "push", ref: "refs/heads/main", conclusion: "success" },
      stagingValidation: { job: "e2e-staging", conclusion: "success", validatedAt: $validatedAt },
      conclusions: $conclusions
    },
    components: {
      auth: { repository: "onlineshop-auth", candidateTag: $candidateTag, imageDigest: $authDigest, sbom: "auth.spdx.json" },
      items: { repository: "onlineshop-items", candidateTag: $candidateTag, imageDigest: $itemsDigest, sbom: "items.spdx.json" },
      apiGateway: { repository: "onlineshop-api-gateway", candidateTag: $candidateTag, imageDigest: $gatewayDigest, sbom: "api-gateway.spdx.json" },
      frontend: { artifact: "frontend-dist.tar.gz", sha256: $frontendSha256, sbom: "frontend.spdx.json" }
    },
    artifact: { name: $artifactName, retentionDays: 30 },
    registry: $registry
  }' > "$BUNDLE_DIR/candidate-evidence.json"

# Sorted checksum manifest for every other file in the bundle (evidence assets
# are durable audit records; ordering by path keeps the manifest
# deterministic). Written outside the bundle first so it never hashes itself.
TMP_CHECKSUMS=$(mktemp)
(
  cd "$BUNDLE_DIR" && find . -maxdepth 1 -type f -print0 | sort -z \
    | xargs -0 sha256sum | sed 's|\./||'
) > "$TMP_CHECKSUMS"
mv "$TMP_CHECKSUMS" "$BUNDLE_DIR/checksums.txt"

VERIFY=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.artifact verify \
  --bundle-dir "$BUNDLE_DIR" --frontend-sha256 "$FRONTEND_SHA256") || {
  echo "ERROR: evidence bundle verification failed:" >&2
  printf '%s' "$VERIFY" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}
printf '%s' "$VERIFY" | jq -e '.valid == true' >/dev/null || {
  echo "ERROR: evidence bundle verification failed:" >&2
  printf '%s' "$VERIFY" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

echo "Emitted candidate evidence bundle in $BUNDLE_DIR"
echo "Artifact name: $ARTIFACT_NAME"
echo "Source SHA: $GITHUB_SHA"
echo "Staging validated at: $VALIDATED_AT"
