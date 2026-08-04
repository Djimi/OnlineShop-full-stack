#!/usr/bin/env bash
set -euo pipefail

# Read-only rollback preflight (Pass 3, subphase 3.6). Resolves the rollback
# target release from the latest complete official release sets, confirms the
# exact ECR digests and frontend prefix marker still exist, enforces the
# database-compatibility guard (Decision 8), and prints a current-versus-target
# summary. It never mutates anything.
#
# The index (the set of official release manifests) comes from `--index` or is
# fetched from GitHub Releases (the `release-manifest.json` asset, selected by
# exact name). The observed state (ECR `release-<version>` tag digests, frontend
# prefix markers, and the currently running release) comes from `--observed` or
# is gathered live (read-only). All decisions are delegated to the fixture-tested
# `release_contract.rollback` module.
#
# Usage:
#   rollback-preflight.sh --version <semver>
#     [--index <index.json>] [--observed <observed.json>]
#     [--target-manifest <output.json>]
#     [--schema-change present|absent] [--migration-reviewed true|false]
#     [--profile dpm-profile] [--region eu-north-1]
#
# Environment inputs: scripts/config/production.env (cluster, services, frontend
# bucket) and GITHUB_REPOSITORY/GITHUB_TOKEN for the live index fetch.
#
# Exit 0 when the target is selectable and the schema guard passes; 1 on any
# fail-closed check; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,28p' "${BASH_SOURCE[0]}" >&2
}

VERSION=""
INDEX=""
OBSERVED=""
TARGET_MANIFEST=""
SCHEMA_CHANGE="absent"
MIGRATION_REVIEWED="false"
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --index) INDEX="${2:-}"; shift 2 ;;
    --observed) OBSERVED="${2:-}"; shift 2 ;;
    --target-manifest) TARGET_MANIFEST="${2:-}"; shift 2 ;;
    --schema-change) SCHEMA_CHANGE="${2:-}"; shift 2 ;;
    --migration-reviewed) MIGRATION_REVIEWED="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$VERSION" ] || { usage; exit 2; }
rl_assert_semver "$VERSION" || exit 2
case "$SCHEMA_CHANGE" in
  present) ;;
  absent) ;;
  *) echo "ERROR: --schema-change must be present|absent" >&2; exit 2 ;;
esac
case "$MIGRATION_REVIEWED" in
  true) ;;
  false) ;;
  *) echo "ERROR: --migration-reviewed must be true|false" >&2; exit 2 ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Mandatory identity preflight ------------------------------------------
if [ -f "$REPO_ROOT/scripts/config/production.env" ]; then
  # shellcheck source=/dev/null
  source "$REPO_ROOT/scripts/config/production.env"
  [ "$LC_PROFILE" = "dpm-profile" ] && [ "$LC_REGION" = "eu-north-1" ] || {
    echo "ERROR: scripts/config/production.env profile/region drift" >&2
    exit 1
  }
  ACCOUNT_ID="$LC_ACCOUNT_ID"
  FRONTEND_BUCKET="$LC_FRONTEND_BUCKET"
else
  echo "ERROR: missing scripts/config/production.env" >&2
  exit 1
fi
set +e
IDENTITY_ACCOUNT=$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query 'Account' --output text 2>"$TMP/identity.err")
RC=$?
set -e
if [ "$RC" -ne 0 ] || [ -z "$IDENTITY_ACCOUNT" ]; then
  echo "ERROR: identity preflight failed (aws sts get-caller-identity):" >&2
  sed -n '1,3p' "$TMP/identity.err" >&2 || true
  exit 1
fi
[ "$IDENTITY_ACCOUNT" = "$ACCOUNT_ID" ] || {
  echo "ERROR: identity preflight failed; account $IDENTITY_ACCOUNT != $ACCOUNT_ID" >&2
  exit 1
}

# --- The official release index --------------------------------------------
if [ -n "$INDEX" ]; then
  rl_assert_regular_file "$INDEX" || exit 2
  cp "$INDEX" "$TMP/index.json"
else
  [ -n "${GITHUB_REPOSITORY:-}" ] || {
    echo "ERROR: --index is required when GITHUB_REPOSITORY is not set" >&2
    exit 2
  }
  # Every published v<version> release contributes its release-manifest.json
  # asset (selected by exact name — an asset whose name merely contains
  # "manifest" is never consumed). Drafts are not part of the releases list.
  jq -n --arg repo "${GITHUB_REPOSITORY}" '{repository: $repo, manifests: []}' > "$TMP/index.json"
  RELEASES=$(gh api "repos/${GITHUB_REPOSITORY}/releases" \
    --paginate --jq '[.[] | select(.draft == false) | {tag_name, assets: [.assets[] | {name, id}]}]' 2>"$TMP/gh.err") || {
    echo "ERROR: cannot list GitHub Releases:" >&2
    sed -n '1,3p' "$TMP/gh.err" >&2 || true
    exit 1
  }
  printf '%s' "$RELEASES" | jq -c '.[]' | while IFS= read -r release; do
    tag=$(printf '%s' "$release" | jq -r '.tag_name')
    asset_id=$(printf '%s' "$release" | jq -r '[.assets[] | select(.name == "release-manifest.json") | .id] | if length == 1 then .[0] else "" end')
    [ -n "$asset_id" ] || continue
    if ! gh api "repos/${GITHUB_REPOSITORY}/releases/assets/${asset_id}" -H "Accept: application/octet-stream" \
      > "$TMP/manifest-${tag}.json" 2>/dev/null; then
      continue
    fi
    if jq -e --arg v "${tag#v}" '.release.version == $v and .release.status == "official"' \
      "$TMP/manifest-${tag}.json" >/dev/null 2>&1; then
      jq --slurpfile m "$TMP/manifest-${tag}.json" \
        '.manifests += $m' "$TMP/index.json" > "$TMP/index.next.json"
      mv "$TMP/index.next.json" "$TMP/index.json"
    fi
  done
  MANIFEST_COUNT=$(jq '.manifests | length' "$TMP/index.json")
  [ "$MANIFEST_COUNT" -gt 0 ] || {
    echo "ERROR: no official release manifests found on GitHub" >&2
    exit 1
  }
fi

# --- Observed state (ECR release tags + frontend prefix markers + current) --
if [ -n "$OBSERVED" ]; then
  rl_assert_regular_file "$OBSERVED" || exit 2
  cp "$OBSERVED" "$TMP/observed.json"
else
  jq -n '{}' > "$TMP/observed.json"
  # Current running release from the live frontend release.json marker.
  set +e
  aws s3api get-object "${AWS_ARGS[@]}" --bucket "$FRONTEND_BUCKET" \
    --key release.json "$TMP/live-marker.json" >/dev/null 2>&1
  RC=$?
  set -e
  if [ "$RC" -eq 0 ] && jq -e 'type == "object"' "$TMP/live-marker.json" >/dev/null 2>&1; then
    jq --slurpfile m "$TMP/live-marker.json" \
      '.currentRelease = {version: $m[0].version, sourceSha: $m[0].sourceSha, frontendSha256: $m[0].frontendSha256}' \
      "$TMP/observed.json" > "$TMP/observed.next.json"
    mv "$TMP/observed.next.json" "$TMP/observed.json"
  else
    jq '.currentRelease = null' "$TMP/observed.json" > "$TMP/observed.next.json"
    mv "$TMP/observed.next.json" "$TMP/observed.json"
  fi
  # Per-backend ECR release tags for every official release in the index.
  jq -n '{}' > "$TMP/ecr.json"
  while IFS=$'\t' read -r repo release_tag; do
    set +e
    DIGEST=$(aws ecr describe-images "${AWS_ARGS[@]}" --repository-name "$repo" \
      --image-ids "imageTag=$release_tag" --query 'imageDetails[0].imageDigest' \
      --output text 2>/dev/null)
    RC=$?
    set -e
    if [ "$RC" -eq 0 ] && [ -n "$DIGEST" ] && [ "$DIGEST" != "None" ]; then
      jq --arg repo "$repo" --arg tag "$release_tag" --arg digest "$DIGEST" \
        '.[$repo].releaseTags[$tag] = $digest' "$TMP/ecr.json" > "$TMP/ecr.next.json"
      mv "$TMP/ecr.next.json" "$TMP/ecr.json"
    fi
  done < <(jq -r '.manifests[] | select(.release.status == "official") |
    .components | to_entries[] | select(.key != "frontend") |
    [.value.repository, .value.releaseTag] | @tsv' "$TMP/index.json")
  # Immutable frontend prefix markers per official release.
  jq -n '{}' > "$TMP/prefix.json"
  while IFS=$'\t' read -r prefix marker; do
    KEY="${prefix}${marker}"
    set +e
    aws s3api get-object "${AWS_ARGS[@]}" --bucket "$FRONTEND_BUCKET" \
      --key "$KEY" "$TMP/prefix-marker.json" >/dev/null 2>&1
    RC=$?
    set -e
    if [ "$RC" -eq 0 ] && jq -e 'type == "object"' "$TMP/prefix-marker.json" >/dev/null 2>&1; then
      jq --arg key "$KEY" --slurpfile m "$TMP/prefix-marker.json" \
        '. + {($key): {exists: true, marker: $m[0]}}' "$TMP/prefix.json" > "$TMP/prefix.next.json"
      mv "$TMP/prefix.next.json" "$TMP/prefix.json"
    fi
  done < <(jq -r '.manifests[] | select(.release.status == "official") |
    [.components.frontend.releasePrefix, .components.frontend.versionMarker] | @tsv' "$TMP/index.json")
  jq --slurpfile ecr "$TMP/ecr.json" --slurpfile prefix "$TMP/prefix.json" \
    '.ecr = $ecr[0] | .frontend.prefixMarkers = $prefix[0]' \
    "$TMP/observed.json" > "$TMP/observed.next.json"
  mv "$TMP/observed.next.json" "$TMP/observed.json"
fi

# --- Selection decision (latest complete official sets) ---------------------
SELECT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.rollback select \
  --index "$TMP/index.json" --observed "$TMP/observed.json" --version "$VERSION") || true
printf '%s' "$SELECT" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: rollback target is not selectable (fail closed):" >&2
  printf '%s' "$SELECT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

# --- Database-compatibility guard (Decision 8) ------------------------------
jq -n --argjson present "$([ "$SCHEMA_CHANGE" = "present" ] && echo true || echo false)" \
  --argjson reviewed "$([ "$MIGRATION_REVIEWED" = "true" ] && echo true || echo false)" \
  '{targetSchemaChange: $present, migrationReviewed: $reviewed}' > "$TMP/schema.json"
SCHEMA=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.rollback schema \
  --state "$TMP/schema.json") || true
printf '%s' "$SCHEMA" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: rollback database-compatibility guard failed (fail closed):" >&2
  printf '%s' "$SCHEMA" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

# --- Current versus target summary ------------------------------------------
TARGET=$(jq -c --arg v "$VERSION" '.manifests[] | select(.release.version == $v)' "$TMP/index.json")
if [ -n "$TARGET_MANIFEST" ]; then
  printf '%s' "$TARGET" > "$TARGET_MANIFEST"
fi
CURRENT=$(jq -c '.currentRelease // {}' "$TMP/observed.json")
echo "rollback-preflight: OK"
echo "target version=$VERSION"
printf '%s' "$TARGET" | jq -r '"  to:      version=\(.release.version) gitTag=\(.release.gitTag) sourceSha=\(.release.sourceSha)"'
printf '%s' "$TARGET" | jq -r '"  to:      auth=\(.components.auth.imageDigest) items=\(.components.items.imageDigest) gateway=\(.components.apiGateway.imageDigest)"'
printf '%s' "$TARGET" | jq -r '"  to:      frontendSha256=\(.components.frontend.sha256) prefix=\(.components.frontend.releasePrefix)"'
if [ -n "$CURRENT" ] && [ "$CURRENT" != "{}" ]; then
  printf '%s' "$CURRENT" | jq -r '"  from:    version=\(.version) sourceSha=\(.sourceSha) frontendSha256=\(.frontendSha256)"'
else
  echo "  from:    <no current release marker observed>"
fi
if [ "$SCHEMA_CHANGE" = "present" ]; then
  echo "  db:      schema change present and reviewed=$MIGRATION_REVIEWED"
else
  echo "  db:      no schema change declared"
fi
