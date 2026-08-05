#!/usr/bin/env bash
set -euo pipefail

# Read-only retention audit of the immediate rollback window (Pass 3, subphase
# 3.8). Lists the exact 10 (or all when fewer exist) immediately
# rollback-capable official releases and FAILS (exit 1, machine-readable JSON)
# when any required backend/frontend artifact of a window release is missing
# or mismatched. An older metadata-only release is reported in outsideWindow
# and is never claimed to be immediately rollback-capable.
#
# The tool also runs the keep-10 coverage check: every window release's
# backend digests must be protected by the policy's push-order keep-10 rule
# (an out-of-order promotion or backport would otherwise let the policy expire
# a window release — POLICY_WINDOW_GAP).
#
# Usage:
#   audit-retention-window.sh [--index <index.json>] [--observed <observed.json>]
#                             [--profile dpm-profile] [--region eu-north-1]
#                             [--human]
#
# --observed <file> supplies a pre-built observed state (offline/fixture mode;
# skips the AWS preflight). Without it, the state is gathered read-only from
# AWS (identity preflight + ECR describe-images + frontend markers) and the
# index is taken from --index or fetched read-only from the GitHub Releases of
# $GITHUB_REPOSITORY via `gh`. Every live AWS command runs with the MANDATORY
# non-overridable --profile dpm-profile --region eu-north-1.
#
# Output is machine-readable JSON on stdout; exit 0 only when the window is
# complete AND covered, exit 1 on any missing/mismatched artifact or gap,
# exit 2 on usage/IO errors. ECR lifecycle evaluation is delayed (up to 24
# hours) — this audit checks the CURRENT state and never assumes an immediate
# policy effect.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" >&2
}

INDEX=""
OBSERVED=""
PROFILE="dpm-profile"
REGION="eu-north-1"
HUMAN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --index) INDEX="${2:-}"; shift 2 ;;
    --observed) OBSERVED="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --human) HUMAN="--human"; shift ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

# The mandatory profile/region are non-overridable (AGENTS.md rule).
if [ "$PROFILE" != "dpm-profile" ] || [ "$REGION" != "eu-north-1" ]; then
  echo "ERROR: --profile must be dpm-profile and --region must be eu-north-1 (mandatory)" >&2
  exit 2
fi
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Manifest index ----------------------------------------------------------
retention_gather_index() {
  if [ -n "$INDEX" ]; then
    rl_assert_regular_file "$INDEX" || exit 2
    if jq -e 'type == "object" and has("manifests")' "$INDEX" >/dev/null 2>&1; then
      cp "$INDEX" "$TMP/index.json"
    elif jq -e 'type == "object"' "$INDEX" >/dev/null 2>&1; then
      jq --arg repository "${GITHUB_REPOSITORY:-unknown}" \
        '{repository: $repository, manifests: [.]}' "$INDEX" > "$TMP/index.json"
    else
      echo "ERROR: --index is neither a manifest index nor a single manifest" >&2
      exit 2
    fi
    return 0
  fi
  [ -n "${GITHUB_REPOSITORY:-}" ] || {
    echo "ERROR: no --index supplied and GITHUB_REPOSITORY is unset" >&2
    exit 2
  }
  command -v gh >/dev/null 2>&1 || {
    echo "ERROR: no --index supplied and gh is unavailable" >&2
    exit 2
  }
  local release_tags tag assets_json asset_id manifest_path
  set +e
  release_tags=$(gh api "repos/${GITHUB_REPOSITORY}/releases" --paginate --jq '.[].tag_name' 2>"$TMP/gh.err")
  local gh_rc=$?
  set -e
  if [ "$gh_rc" -ne 0 ]; then
    echo "ERROR: cannot list GitHub releases (read-only):" >&2
    cat "$TMP/gh.err" >&2 || true
    exit 1
  fi
  jq -n --arg repository "$GITHUB_REPOSITORY" '{repository: $repository, manifests: []}' \
    > "$TMP/index.json"
  [ -n "$release_tags" ] || { echo "WARNING: no GitHub releases found" >&2; return 0; }
  while IFS= read -r tag; do
    [ -n "$tag" ] || continue
    manifest_path="$TMP/release-manifest-${tag}.json"
    if ! assets_json=$(gh api "repos/${GITHUB_REPOSITORY}/releases/tags/${tag}" \
      --jq '.assets[] | {name, id}' 2>"$TMP/gh.err"); then
      echo "WARNING: cannot read release $tag metadata; skipping" >&2
      continue
    fi
    # The canonical release manifest asset is named release-manifest.json
    # (3.4 publication contract); a decoy whose name merely contains
    # "manifest" is never consumed.
    asset_id=$(printf '%s' "$assets_json" | jq -r 'select(.name == "release-manifest.json") | .id' | head -1)
    [ -n "$asset_id" ] && [ "$asset_id" != "null" ] || {
      echo "WARNING: release $tag has no release-manifest.json asset; it cannot be audited and is NOT included in the index (the window is computed from indexed releases only)" >&2
      continue
    }
    if ! gh api "repos/${GITHUB_REPOSITORY}/releases/assets/${asset_id}" \
      -H "Accept: application/octet-stream" > "$manifest_path" 2>"$TMP/gh.err"; then
      echo "ERROR: cannot download the manifest asset of release $tag (read-only):" >&2
      cat "$TMP/gh.err" >&2 || true
      exit 1
    fi
    jq -e 'type == "object"' "$manifest_path" >/dev/null 2>&1 || {
      echo "ERROR: release $tag manifest asset is not a JSON object" >&2
      exit 1
    }
    jq --argjson manifest "$(cat "$manifest_path")" '.manifests += [$manifest]' \
      "$TMP/index.json" > "$TMP/index.next.json"
    mv "$TMP/index.next.json" "$TMP/index.json"
  done <<< "$release_tags"
}

# --- Observed state (read-only) ----------------------------------------------
retention_load_config() {
  [ -f "$REPO_ROOT/scripts/config/production.env" ] || {
    echo "ERROR: missing scripts/config/production.env" >&2
    exit 1
  }
  # shellcheck source=/dev/null
  source "$REPO_ROOT/scripts/config/production.env"
  [ "$LC_PROFILE" = "dpm-profile" ] && [ "$LC_REGION" = "eu-north-1" ] || {
    echo "ERROR: scripts/config/production.env profile/region drift" >&2
    exit 1
  }
  [ "$LC_ACCOUNT_ID" = "799111666795" ] || {
    echo "ERROR: scripts/config/production.env account drift" >&2
    exit 1
  }
}

# describe-images returns everything (tagged + untagged) because the retention
# evaluation needs untagged images too. The releaseTags map is derived from
# the same response so the audit and the coverage check share one read.
retention_gather_ecr() {
  jq -n '{}' > "$TMP/ecr.json"
  local repo images rc err
  for repo in "${LC_ECR_REPOSITORIES[@]:-}"; do
    set +e
    images=$(aws ecr describe-images "${AWS_ARGS[@]}" --repository-name "$repo" \
      --query 'imageDetails[]' --output json 2>"$TMP/ecr.err")
    rc=$?
    set -e
    images=${images:-[]}
    if [ "$rc" -eq 0 ]; then
      jq --arg repo "$repo" --argjson images "$images" \
        '. + {($repo): {images: $images}}' "$TMP/ecr.json" > "$TMP/ecr.next.json"
      mv "$TMP/ecr.next.json" "$TMP/ecr.json"
      # Derive the releaseTag -> digest map from the same describe-images
      # response (the audit consumes releaseTags, the coverage check images).
      jq --arg repo "$repo" \
        '.[$repo].releaseTags = ([.[$repo].images[]? | select(.imageTags != null) | . as $im | (.imageTags[] | select(startswith("release-"))) | {key: ., value: $im.imageDigest}] | from_entries)' \
        "$TMP/ecr.json" > "$TMP/ecr.next.json"
      mv "$TMP/ecr.next.json" "$TMP/ecr.json"
    else
      err=$(head -1 "$TMP/ecr.err" 2>/dev/null || true)
      [ -n "$err" ] || err="describe-images exited $rc"
      jq --arg repo "$repo" --arg err "$err" \
        '. + {($repo): {images: [], releaseTags: {}, error: $err}}' "$TMP/ecr.json" > "$TMP/ecr.next.json"
      mv "$TMP/ecr.next.json" "$TMP/ecr.json"
    fi
  done
}

retention_gather_frontend() {
  jq -n '{liveMarker: {exists: false, marker: null}, prefixMarkers: {}}' > "$TMP/frontend.json"
  [ -n "${LC_FRONTEND_BUCKET:-}" ] || { echo "WARNING: no frontend bucket configured" >&2; return 0; }
  local prefix marker_name key rc
  while IFS=$'\t' read -r prefix marker_name; do
    [ -n "$prefix" ] && [ -n "$marker_name" ] || continue
    key="${prefix}${marker_name}"
    set +e
    aws s3api get-object "${AWS_ARGS[@]}" --bucket "${LC_FRONTEND_BUCKET:-}" \
      --key "$key" "$TMP/prefix.json" >/dev/null 2>"$TMP/frontend.err"
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
      if printf '%s' "$(cat "$TMP/prefix.json")" | jq -e 'type == "object"' >/dev/null 2>&1; then
        jq --arg key "$key" --argjson marker "$(cat "$TMP/prefix.json")" \
          '.prefixMarkers[$key] = {exists: true, marker: $marker}' "$TMP/frontend.json" > "$TMP/frontend.next.json"
      else
        jq --arg key "$key" --arg err "prefix release.json marker is not a JSON object" \
          '.prefixMarkers[$key] = {exists: true, marker: null, error: $err}' "$TMP/frontend.json" > "$TMP/frontend.next.json"
      fi
    elif grep -qiE 'not ?found|does not exist|NoSuchKey|not be found' "$TMP/frontend.err"; then
      jq --arg key "$key" '.prefixMarkers[$key] = {exists: false, marker: null}' "$TMP/frontend.json" > "$TMP/frontend.next.json"
    else
      err=$(head -1 "$TMP/frontend.err" 2>/dev/null || true)
      [ -n "$err" ] || err="get-object $key exited $rc"
      jq --arg key "$key" --arg err "$err" \
        '.prefixMarkers[$key] = {exists: false, marker: null, error: $err}' "$TMP/frontend.json" > "$TMP/frontend.next.json"
    fi
    mv "$TMP/frontend.next.json" "$TMP/frontend.json"
  done < <(jq -r '.manifests[] | select(.release.status == "official") | .components.frontend | [.releasePrefix, .versionMarker] | @tsv' "$TMP/index.json")
}

retention_gather_observed() {
  if [ -n "$OBSERVED" ]; then
    rl_assert_regular_file "$OBSERVED" || exit 2
    cp "$OBSERVED" "$TMP/observed.json"
    return 0
  fi
  local identity
  identity=$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query 'Account' --output text)
  [ "$identity" = "799111666795" ] || {
    echo "ERROR: identity preflight failed: unexpected AWS account $identity (expected 799111666795)" >&2
    exit 1
  }
  retention_load_config
  retention_gather_ecr
  retention_gather_frontend
  jq -s '{ecr: .[0], frontend: .[1]}' "$TMP/ecr.json" "$TMP/frontend.json" > "$TMP/observed.json"
}

# --- Main --------------------------------------------------------------------
retention_gather_index
retention_gather_observed

POLICY="$RELEASE/ecr/lifecycle-policy.json"
[ -f "$POLICY" ] || { echo "ERROR: missing desired policy: $POLICY" >&2; exit 2; }

set +e
AUDIT_OUT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.retention audit \
  --index "$TMP/index.json" --observed "$TMP/observed.json" 2>"$TMP/audit.err")
AUDIT_RC=$?
COVERAGE_OUT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.retention coverage \
  --policy "$POLICY" --index "$TMP/index.json" --observed "$TMP/observed.json" 2>"$TMP/coverage.err")
COVERAGE_RC=$?
set -e

printf '%s\n' "$AUDIT_OUT"
printf '%s\n' "$COVERAGE_OUT"
if [ -s "$TMP/audit.err" ]; then cat "$TMP/audit.err" >&2 || true; fi
if [ -s "$TMP/coverage.err" ]; then cat "$TMP/coverage.err" >&2 || true; fi
if [ -n "$HUMAN" ]; then
  echo "retention audit: window=$(printf '%s' "$AUDIT_OUT" | jq -r '.data.window') releases, rollback-capable: $(printf '%s' "$AUDIT_OUT" | jq -c '.data.rollbackCapable')" >&2
fi
[ "$AUDIT_RC" -eq 0 ] && [ "$COVERAGE_RC" -eq 0 ]
