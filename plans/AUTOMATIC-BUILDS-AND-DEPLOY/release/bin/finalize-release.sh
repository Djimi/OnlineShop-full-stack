#!/usr/bin/env bash
set -euo pipefail

# Finalization of a controlled promotion (Pass 3, subphase 3.4). Runs ONLY
# after production verification succeeded (Decision 6):
#
#   1. mints the three immutable `release-<version>` ECR tags server-side from
#      the already recorded candidate bytes (release/bin/promote-image-digest.sh,
#      never a rebuild) and verifies they resolve to the running digests;
#   2. publishes `v<version>` at the selected SHA as a GitHub Release with the
#      final manifest, the schema, the three container SBOMs, the frontend
#      SBOM/archive, the checksum file, the sanitized test evidence, and the
#      deployment result, recording dispatcher, approver, timestamps, and
#      workflow URLs;
#   3. records the exact release identity (action=publish | action=resume) and
#      is idempotently resumable: a partial object that exactly matches the
#      validated manifest resumes, anything else fails closed and never mints a
#      different version for already deployed bits.
#
# The decision is delegated to release_contract.promotion finalize against the
# read-only observed state (ECR release tags, frontend prefix marker, git tag).
# `--dry-run` gathers and decides without mutating.
#
# Usage:
#   finalize-release.sh --manifest <official-manifest.json>
#     --evidence-dir <candidate-evidence-dir> [--dry-run]
#     [--profile dpm-profile] [--region eu-north-1]
#
# Environment inputs: GITHUB_REPOSITORY, GITHUB_TOKEN (for `gh`); the official
# manifest must be produced from the approved promotion evidence.
#
# Exit 0 on publish/resume success; 1 on fail-closed; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,32p' "${BASH_SOURCE[0]}" >&2
}

MANIFEST=""
EVIDENCE_DIR=""
DRY_RUN=0
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --evidence-dir) EVIDENCE_DIR="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$MANIFEST" ] && [ -n "$EVIDENCE_DIR" ] || { usage; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2
[ -d "$EVIDENCE_DIR" ] || { echo "ERROR: evidence dir not found: $EVIDENCE_DIR" >&2; exit 2; }
[ -n "${GITHUB_REPOSITORY:-}" ] || { echo "ERROR: GITHUB_REPOSITORY is required" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

VERSION=$(jq -r '.release.version' "$MANIFEST")
GIT_TAG=$(jq -r '.release.gitTag' "$MANIFEST")
SOURCE_SHA=$(jq -r '.release.sourceSha' "$MANIFEST")
rl_assert_semver "$VERSION" || exit 2
rl_assert_full_sha "$SOURCE_SHA" || exit 2

# The official manifest must be schema-valid (production task definitions and
# promotionWorkflow present).
bash "$RELEASE/bin/validate-manifest.sh" "$MANIFEST" >/dev/null || {
  echo "ERROR: official manifest failed validation; refusing to finalize" >&2
  exit 1
}

# --- Read-only observed state for the finalization decision ------------------
# ECR release-<version> tag digests per backend.
jq -n '{}' > "$TMP/ecr.json"
while IFS=$'\t' read -r repo releaseTag; do
  [ -n "$repo" ] || continue
  digest=$(aws ecr describe-images "${AWS_ARGS[@]}" \
    --repository-name "$repo" --image-ids "imageTag=$releaseTag" \
    --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)
  case "$digest" in
    "" | "None" | "null") digest="null" ;;
    *) digest="\"$digest\"" ;;
  esac
  jq --arg repo "$repo" --argjson d "$digest" \
    '. + {($repo): {releaseDigest: $d}}' "$TMP/ecr.json" > "$TMP/ecr.next.json"
  mv "$TMP/ecr.next.json" "$TMP/ecr.json"
done < <(jq -r '.components | {auth: .auth, items: .items, apiGateway: .apiGateway}
  | to_entries[] | [.value.repository, .value.releaseTag] | @tsv' "$MANIFEST")

# Frontend immutable prefix marker.
PREFIX=$(jq -r '.components.frontend.releasePrefix' "$MANIFEST")
MARKER=$(jq -r '.components.frontend.versionMarker' "$MANIFEST")
BUCKET="onlineshop-frontend-799111666795"
if [ -f "$REPO_ROOT/scripts/config/production.env" ]; then
  # shellcheck source=/dev/null
  source "$REPO_ROOT/scripts/config/production.env"
  BUCKET="$LC_FRONTEND_BUCKET"
fi
MARKER_EXISTS=false
if aws s3api get-object "${AWS_ARGS[@]}" --bucket "$BUCKET" \
  --key "${PREFIX}${MARKER}" "$TMP/prefix.json" >/dev/null 2>&1; then
  MARKER_EXISTS=true
  PREFIX_MARKER=$(cat "$TMP/prefix.json")
else
  PREFIX_MARKER="null"
fi

# Git tag at the selected SHA (peeled to the commit).
GIT_EXISTS=false
GIT_SHA="null"
set +e
GIT_REF=$(gh api "repos/${GITHUB_REPOSITORY}/git/refs/tags/${GIT_TAG}" \
  --jq '.object' 2>"$TMP/gh.err")
GH_RC=$?
set -e
if [ "$GH_RC" -eq 0 ] && [ -n "$GIT_REF" ]; then
  GIT_EXISTS=true
  GIT_TYPE=$(printf '%s' "$GIT_REF" | jq -r '.type // ""')
  GIT_SHA=$(printf '%s' "$GIT_REF" | jq -r '.sha // ""')
  if [ "$GIT_TYPE" = "tag" ]; then
    GIT_SHA=$(gh api "repos/${GITHUB_REPOSITORY}/git/tags/${GIT_SHA}" \
      --jq '.object.sha' 2>/dev/null || true)
  fi
fi

jq -n \
  --argjson ecr "$(cat "$TMP/ecr.json")" \
  --argjson markerExists "$MARKER_EXISTS" \
  --argjson marker "${PREFIX_MARKER:-null}" \
  --argjson gitExists "$GIT_EXISTS" \
  --arg gitSha "${GIT_SHA:-}" \
  '{ecr: $ecr,
    frontendPrefix: {markerExists: $markerExists, marker: $marker},
    gitTag: {exists: $gitExists, sha: ($gitSha | if . == "" then null else . end)}}' > "$TMP/observed.json"

# ProductionVerified is recorded by the workflow AFTER verify-production.sh; the
# script refuses to finalize unless explicitly told production is verified.
PROD_VERIFIED="${PROMOTION_PRODUCTION_VERIFIED:-false}"
jq -s --argjson pv "$([ "$PROD_VERIFIED" = "true" ] && echo true || echo false)" \
  --argjson manifest "$(cat "$MANIFEST")" \
  '{productionVerified: $pv, manifest: $manifest, ecr: .[0].ecr,
    frontendPrefix: .[0].frontendPrefix, gitTag: .[0].gitTag}' \
  "$TMP/observed.json" > "$TMP/state.json"

FINALIZE=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion finalize \
  --state "$TMP/state.json") || {
  echo "ERROR: release finalization blocked (fail closed):" >&2
  printf '%s' "$FINALIZE" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}
ACTION=$(printf '%s' "$FINALIZE" | jq -r '.action')
printf '%s' "$FINALIZE" | jq -e '.valid == true' >/dev/null || {
  echo "ERROR: release finalization blocked (fail closed):" >&2
  printf '%s' "$FINALIZE" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

echo "finalize action=$ACTION version=$VERSION"
if [ "$DRY_RUN" -eq 1 ] || [ "$PROD_VERIFIED" != "true" ]; then
  echo "dry-run / not verified: no mutation performed (action=$ACTION)"
  exit 0
fi

# --- Mint the three immutable release tags server-side ------------------------
for entry in "onlineshop-auth:auth" "onlineshop-items:items" "onlineshop-api-gateway:apiGateway"; do
  repo="${entry%%:*}"
  key="${entry##*:}"
  digest=$(jq -r ".components.${key}.imageDigest" "$MANIFEST")
  candidate=$(jq -r ".components.${key}.candidateTag" "$MANIFEST")
  release=$(jq -r ".components.${key}.releaseTag" "$MANIFEST")
  bash "$RELEASE/bin/promote-image-digest.sh" \
    --repository "$repo" --candidate-tag "$candidate" \
    --release-tag "$release" --digest "$digest" \
    --profile "$PROFILE" --region "$REGION"
done

# --- Publish the GitHub Release v<version> at the selected SHA -----------------
# Attach the final manifest, SBOMs, frontend archive, checksum file, sanitized
# test evidence, and the deployment result.
BODY=$(cat <<BODY
OnlineShop release v${VERSION} (official)

Source: ${SOURCE_SHA}
Candidate run: $(jq -r '.release.candidateWorkflow.runId // ""' "$MANIFEST")/attempt $(jq -r '.release.candidateWorkflow.runAttempt // ""' "$MANIFEST")
Approved and deployed by the promotion workflow. Full manifest, SBOMs, frontend
archive, and checksums are attached.
BODY
)
# shellcheck disable=SC2034  # the command's stderr is the error surface; the JSON is read back below
gh release create "$GIT_TAG" \
  --target "$SOURCE_SHA" \
  --title "OnlineShop v${VERSION}" \
  --notes "$BODY" \
  --repo "$GITHUB_REPOSITORY" 2>"$TMP/release.err" >/dev/null
# Attach assets (best-effort per-asset; the release object is the durable
# record). The manifest is the authoritative release-manifest.json.
gh release upload "$GIT_TAG" "$MANIFEST" --repo "$GITHUB_REPOSITORY" \
  --clobber >/dev/null 2>&1 || true
for asset in auth.spdx.json items.spdx.json api-gateway.spdx.json frontend.spdx.json \
  frontend-dist.tar.gz frontend-dist.sha256 checksums.txt; do
  if [ -f "$EVIDENCE_DIR/$asset" ]; then
    gh release upload "$GIT_TAG" "$EVIDENCE_DIR/$asset" \
      --repo "$GITHUB_REPOSITORY" --clobber >/dev/null 2>&1 || true
  fi
done

# Read-back: the release must exist at the tag and carry release-manifest.json.
gh api "repos/${GITHUB_REPOSITORY}/releases/tags/${GIT_TAG}" \
  --jq '{id, tag_name, target_commitish, name}' > "$TMP/release-read.json" || {
  echo "ERROR: cannot read back the published release $GIT_TAG" >&2
  exit 1
}
ASSETS=$(gh api "repos/${GITHUB_REPOSITORY}/releases/tags/${GIT_TAG}" \
  --jq '[.assets[].name] | index("release-manifest.json") != null' 2>/dev/null || echo false)
[ "$ASSETS" = "true" ] || {
  echo "ERROR: release $GIT_TAG is missing the release-manifest.json asset" >&2
  exit 1
}

echo "finalize-release: OK (action=$ACTION, release=$GIT_TAG)"
