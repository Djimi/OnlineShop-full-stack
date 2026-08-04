#!/usr/bin/env bash
set -euo pipefail

# Read-only release-identity preflight (Pass 3, subphase 3.3). Before any
# promotion mutation the workflow must prove the release identity it is about
# to create is free or already exactly matches the validated manifest:
#
#   - GitHub `v<version>` tag (must point at the candidate SHA, or be absent);
#   - ECR `release-<version>` tag per backend (must resolve to the recorded
#     digest, or be absent);
#   - frontend release-prefix `release.json` version marker (must match the
#     manifest version/SHA/checksum, or be absent).
#
# The decision is delegated to the fixture-tested `release_contract.releaseid`
# module: `action=proceed` (nothing exists), `action=resume` (every existing
# partial object exactly matches the manifest), or a non-zero exit (fail
# closed) on any collision. This script never mutates anything.
#
# Usage:
#   check-release-identity.sh --manifest <manifest.json> --bucket <s3-bucket> \
#     [--profile dpm-profile] [--region eu-north-1]
#
# Environment inputs: GITHUB_REPOSITORY (owner/repo), GITHUB_TOKEN (optional,
# read by `gh`).
#
# Exit 0 on proceed/resume; 1 on any collision; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,24p' "${BASH_SOURCE[0]}" >&2
}

MANIFEST=""
BUCKET=""
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --bucket) BUCKET="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$MANIFEST" ] && [ -n "$BUCKET" ] || { usage; exit 2; }
[ -n "${GITHUB_REPOSITORY:-}" ] || { echo "ERROR: GITHUB_REPOSITORY is required" >&2; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2

# The manifest must be schema-valid before its identity may be checked.
bash "$RELEASE/bin/validate-manifest.sh" "$MANIFEST" || {
  echo "ERROR: manifest failed validation; refusing to check release identity" >&2
  exit 1
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

VERSION=$(jq -r '.release.version' "$MANIFEST")
GIT_TAG=$(jq -r '.release.gitTag' "$MANIFEST")
SOURCE_SHA=$(jq -r '.release.sourceSha' "$MANIFEST")
RELEASE_TAG=$(jq -r '.components.auth.releaseTag' "$MANIFEST")
RELEASE_PREFIX=$(jq -r '.components.frontend.releasePrefix' "$MANIFEST")
VERSION_MARKER=$(jq -r '.components.frontend.versionMarker' "$MANIFEST")
rl_assert_semver "$VERSION" || exit 2
rl_assert_full_sha "$SOURCE_SHA" || exit 2

# --- GitHub v<version> tag -------------------------------------------------
set +e
GIT_REF_JSON=$(gh api "repos/${GITHUB_REPOSITORY}/git/refs/tags/${GIT_TAG}" 2>"$TMP/gh.err")
GH_RC=$?
set -e
if [ "$GH_RC" -eq 0 ] && [ -n "$GIT_REF_JSON" ]; then
  GIT_TAG_EXISTS=true
  GIT_TAG_SHA=$(printf '%s' "$GIT_REF_JSON" | jq -r '.object.sha // ""')
  GIT_TAG_TYPE=$(printf '%s' "$GIT_REF_JSON" | jq -r '.object.type // ""')
  [ -n "$GIT_TAG_SHA" ] || { echo "ERROR: git tag $GIT_TAG exists but has no SHA" >&2; exit 1; }
  # GitHub Release tags can be annotated tag objects: `.object.sha` is then the
  # tag object, not the commit. Peel it so the identity comparison below always
  # compares the commit the tag points at (a lightweight tag is already the
  # commit and needs no peel).
  if [ "$GIT_TAG_TYPE" = "tag" ]; then
    set +e
    GIT_TAG_OBJ=$(gh api "repos/${GITHUB_REPOSITORY}/git/tags/${GIT_TAG_SHA}" 2>"$TMP/gh-peel.err")
    PEEL_RC=$?
    set -e
    if [ "$PEEL_RC" -ne 0 ] || [ -z "$GIT_TAG_OBJ" ]; then
      echo "ERROR: cannot dereference annotated tag $GIT_TAG (gh api exit $PEEL_RC):" >&2
      cat "$TMP/gh-peel.err" >&2 || true
      exit 1
    fi
    GIT_TAG_SHA=$(printf '%s' "$GIT_TAG_OBJ" | jq -r '.object.sha // ""')
    [[ "$GIT_TAG_SHA" =~ ^[0-9a-f]{40}$ ]] || {
      echo "ERROR: annotated tag $GIT_TAG does not resolve to a commit" >&2
      exit 1
    }
  fi
elif grep -q "404" "$TMP/gh.err" 2>/dev/null; then
  GIT_TAG_EXISTS=false
  GIT_TAG_SHA=null
else
  echo "ERROR: cannot check git tag $GIT_TAG (gh api exit $GH_RC):" >&2
  cat "$TMP/gh.err" >&2 || true
  exit 1
fi

jq -n --arg exists "$GIT_TAG_EXISTS" --arg sha "${GIT_TAG_SHA:-}" \
  '{exists: ($exists == "true"), sha: $sha}' > "$TMP/observed-git.json"

# --- ECR release-<version> tags ---------------------------------------------
jq -n '{}' > "$TMP/observed-ecr.json"
while IFS= read -r repo; do
  digest=$(aws ecr describe-images "${AWS_ARGS[@]}" \
    --repository-name "$repo" \
    --image-ids "imageTag=$RELEASE_TAG" \
    --query 'imageDetails[0].imageDigest' \
    --output text 2>/dev/null || true)
  case "$digest" in
    "" | "None" | "null") digest="null" ;;
    *) digest="\"$digest\"" ;;
  esac
  jq --arg repo "$repo" --argjson releaseDigest "$digest" \
    '. + {($repo): {releaseDigest: $releaseDigest}}' "$TMP/observed-ecr.json" > "$TMP/observed-ecr-next.json"
  mv "$TMP/observed-ecr-next.json" "$TMP/observed-ecr.json"
done < <(jq -r '.components | {auth: .auth.repository, items: .items.repository, apiGateway: .apiGateway.repository} | to_entries | map(.value)[]' "$MANIFEST")

# --- Frontend release-prefix version marker ----------------------------------
MARKER_EXISTS=false
set +e
LISTED=$(aws s3api list-objects-v2 "${AWS_ARGS[@]}" \
  --bucket "$BUCKET" \
  --prefix "${RELEASE_PREFIX}${VERSION_MARKER}" --max-items 1 \
  --query 'Contents[0].Key' --output text 2>/dev/null)
S3_RC=$?
set -e
if [ "$S3_RC" -eq 0 ] && [ -n "$LISTED" ] && [ "$LISTED" != "None" ]; then
  MARKER_EXISTS=true
  aws s3api get-object "${AWS_ARGS[@]}" \
    --bucket "$BUCKET" --key "${RELEASE_PREFIX}${VERSION_MARKER}" \
    "$TMP/marker.json" >/dev/null 2>&1 || {
    echo "ERROR: cannot read frontend version marker ${RELEASE_PREFIX}${VERSION_MARKER}" >&2
    exit 1
  }
fi
if [ "$MARKER_EXISTS" = "true" ]; then
  MARKER_JSON=$(cat "$TMP/marker.json")
  # The marker must be a JSON object; its version/SHA/checksum are compared
  # against the manifest by the releaseid module.
  printf '%s' "$MARKER_JSON" | jq -e 'type == "object"' >/dev/null 2>&1 || {
    echo "ERROR: frontend version marker is not a JSON object" >&2
    exit 1
  }
  jq -n --argjson markerExists true --argjson marker "$MARKER_JSON" \
    '{markerExists: $markerExists, marker: $marker}' > "$TMP/observed-frontend.json"
else
  jq -n '{markerExists: false, marker: null}' > "$TMP/observed-frontend.json"
fi

# --- Assemble observed state and decide --------------------------------------
jq -s \
  --slurpfile git "$TMP/observed-git.json" \
  --slurpfile ecr "$TMP/observed-ecr.json" \
  --slurpfile frontend "$TMP/observed-frontend.json" \
  '{gitTag: $git[0], ecr: $ecr[0], frontend: $frontend[0]}' \
  /dev/null > "$TMP/observed.json"

DECISION=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.releaseid decide \
  --manifest "$MANIFEST" --observed "$TMP/observed.json") || {
  echo "ERROR: release identity collision (fail closed):" >&2
  printf '%s' "$DECISION" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

ACTION=$(printf '%s' "$DECISION" | jq -r '.action')
echo "action=$ACTION"
echo "version=$VERSION gitTag=$GIT_TAG sourceSha=$SOURCE_SHA"
echo "gitTagExists=$GIT_TAG_EXISTS ecrReleaseTag=$RELEASE_TAG frontendPrefix=${RELEASE_PREFIX}${VERSION_MARKER} markerExists=$MARKER_EXISTS"
if [ "$ACTION" = "resume" ]; then
  echo "Every existing release identity object matches the manifest; promotion may resume idempotently."
else
  echo "Release identity is free; promotion may proceed."
fi
