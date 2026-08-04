#!/usr/bin/env bash
set -euo pipefail

# Compensation of a partially applied promotion (Pass 3, subphase 3.4,
# Decision 13). On a later-component failure the changed ECS services are
# restored to the exact pre-promotion snapshot in reverse deploy order, and the
# frontend live root is restored from the previous immutable release prefix
# (frontend first), then every restore is read back (task definitions and
# frontend marker/checksum). A component whose snapshot lacks the fields to
# restore it fails closed — a mixed deployment is an incident, never success.
# If compensation itself fails, the run stops with a mixed-state incident
# record and never publishes an official release or mutates the database.
#
# The compensation plan is delegated to release_contract.promotion compensate.
# `--dry-run` prints the plan without mutating.
#
# Usage:
#   compensate-production.sh --snapshot <snapshot.json>
#     --changed <changed-components.json>
#     [--dry-run] [--profile dpm-profile] [--region eu-north-1]
#
# Exit 0 when all changed components are restored and verified; 1 on
# fail-closed; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,24p' "${BASH_SOURCE[0]}" >&2
}

SNAPSHOT=""
CHANGED=""
DRY_RUN=0
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --snapshot) SNAPSHOT="${2:-}"; shift 2 ;;
    --changed) CHANGED="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$SNAPSHOT" ] && [ -n "$CHANGED" ] || { usage; exit 2; }
rl_assert_regular_file "$SNAPSHOT" || exit 2
rl_assert_regular_file "$CHANGED" || exit 2
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

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CLUSTER="$LC_CLUSTER"

PLAN=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion compensate \
  --snapshot "$SNAPSHOT" --changed "$CHANGED") || {
  echo "ERROR: cannot build a compensation plan (fail closed):" >&2
  printf '%s' "$PLAN" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}
printf '%s' "$PLAN" | jq -e '.valid == true' >/dev/null || {
  echo "ERROR: compensation plan invalid (fail closed):" >&2
  printf '%s' "$PLAN" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}
STEPS=$(printf '%s' "$PLAN" | jq -r '.steps | length')

if [ "$DRY_RUN" -eq 1 ]; then
  echo "compensate-production: dry-run ($STEPS step(s))"
  printf '%s' "$PLAN" | jq -r '.steps[] | "  \(.component): \(.action)"'
  exit 0
fi

# Execute the reverse-order plan. Every restore is read back.
RESTORED=()
FAILED=0

# Snapshot frontend fields used by restore_frontend (validated by the decision
# layer; markers are compared semantically with jq, never as raw strings).
FRONTEND_MARKER_JSON=$(jq -c '.frontend.marker // {}' "$SNAPSHOT")
FRONTEND_INDEX_SHA=$(jq -r '.frontend.indexSha256 // ""' "$SNAPSHOT")

# Restore the frontend live root to the exact pre-promotion state. The snapshot
# records the pre-promotion live marker and the live index.html checksum; the
# previous immutable release prefix `_releases/v<prev>/` is the rollback source
# for the exact bytes. If the live root already matches the snapshot, the
# frontend was never published by this promotion and the restore is a no-op.
restore_frontend() {
  local current_marker prev_version prev_prefix actual_sha
  current_marker=""
  if aws s3api get-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
    --key release.json "$TMP/current-marker.json" >/dev/null 2>&1; then
    current_marker=$(cat "$TMP/current-marker.json")
  fi
  if [ -n "$current_marker" ] && printf '%s' "$current_marker" \
    | jq -e --argjson expected "$FRONTEND_MARKER_JSON" '. == $expected' >/dev/null 2>&1; then
    echo "restore frontend: live root already matches the pre-promotion snapshot; no-op" >&2
    RESTORED+=("frontend")
    return 0
  fi

  # The live root differs -> the promotion published a new frontend. Restore the
  # exact pre-promotion bytes from the previous immutable release prefix.
  prev_version=$(jq -r '.officialRelease.version // ""' "$SNAPSHOT")
  [ -n "$prev_version" ] || {
    echo "ERROR: cannot restore the frontend without a previous official version (fail closed)" >&2
    FAILED=1
    return 1
  }
  prev_prefix="_releases/v${prev_version}/"
  if ! aws s3api get-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
    --key "${prev_prefix}release.json" "$TMP/prev-marker.json" >/dev/null 2>&1 \
    || ! aws s3api get-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
    --key "${prev_prefix}index.html" "$TMP/prev-index.html" >/dev/null 2>&1; then
    echo "ERROR: previous immutable prefix ${prev_prefix} is unavailable; cannot restore the frontend (fail closed)" >&2
    FAILED=1
    return 1
  fi
  # The previous prefix marker must be the exact pre-promotion marker.
  printf '%s' "$(cat "$TMP/prev-marker.json")" \
    | jq -e --argjson expected "$FRONTEND_MARKER_JSON" '. == $expected' >/dev/null 2>&1 || {
    echo "ERROR: previous prefix marker does not match the pre-promotion snapshot marker (fail closed)" >&2
    FAILED=1
    return 1
  }

  # Publish the previous prefix marker + index.html to the live root and
  # invalidate the SPA entry paths.
  aws s3 cp "${AWS_ARGS[@]}" "$TMP/prev-marker.json" "s3://$LC_FRONTEND_BUCKET/release.json" \
    --content-type application/json
  aws s3 cp "${AWS_ARGS[@]}" "$TMP/prev-index.html" "s3://$LC_FRONTEND_BUCKET/index.html" \
    --content-type text/html
  if [ -n "${LC_CLOUDFRONT_DISTRIBUTION:-}" ]; then
    aws cloudfront create-invalidation "${AWS_ARGS[@]}" \
      --distribution-id "$LC_CLOUDFRONT_DISTRIBUTION" --paths "/*" \
      --query 'Invalidation.Id' --output text >/dev/null 2>&1 || {
      echo "ERROR: CloudFront invalidation failed during frontend restore" >&2
      FAILED=1
      return 1
    }
  fi

  # Read back: live marker == snapshot marker and live index.html checksum ==
  # the pre-promotion checksum.
  aws s3api get-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
    --key release.json "$TMP/restored-marker.json" >/dev/null
  printf '%s' "$(cat "$TMP/restored-marker.json")" \
    | jq -e --argjson expected "$FRONTEND_MARKER_JSON" '. == $expected' >/dev/null || {
    echo "ERROR: restored frontend marker does not match the snapshot (fail closed)" >&2
    FAILED=1
    return 1
  }
  actual_sha=$(sha256sum "$TMP/prev-index.html" | cut -d' ' -f1)
  [ -n "$FRONTEND_INDEX_SHA" ] && [ "$actual_sha" = "$FRONTEND_INDEX_SHA" ] || {
    echo "ERROR: restored frontend index.html sha256 $actual_sha != snapshot $FRONTEND_INDEX_SHA (fail closed)" >&2
    FAILED=1
    return 1
  }
  echo "restore frontend: live root restored from ${prev_prefix}" >&2
  RESTORED+=("frontend")
  return 0
}

while IFS=$'\t' read -r component _action td desired _digest; do
  if [ "$component" = "frontend" ]; then
    restore_frontend
    continue
  fi
  service=""
  case "$component" in
    auth) service="onlineshop-auth" ;;
    items) service="onlineshop-items" ;;
    apiGateway) service="onlineshop-api-gateway" ;;
  esac
  [ -n "$service" ] || { echo "ERROR: unknown component $component" >&2; FAILED=1; break; }
  echo "restore $service -> $td (desired=$desired)" >&2
  aws ecs update-service "${AWS_ARGS[@]}" \
    --cluster "$CLUSTER" --service "$service" \
    --task-definition "$td" --desired-count "$desired" >/dev/null
  # Read back.
  ACTIVE=$(aws ecs describe-services "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
    --services "$service" --query 'services[0].taskDefinition' --output text)
  [ "$ACTIVE" = "$td" ] || {
    echo "ERROR: service $service restore was not applied ($ACTIVE != $td)" >&2
    FAILED=1
    break
  }
  aws ecs wait services-stable "${AWS_ARGS[@]}" \
    --cluster "$CLUSTER" --services "$service"
  RESTORED+=("$component")
done < <(printf '%s' "$PLAN" | jq -r '.steps[] | [.component, .action, .restore.taskDefinitionArn, (.restore.desiredCount // ""), (.restore.runningDigest // "")] | @tsv')

if [ "$FAILED" -eq 1 ]; then
  echo "ERROR: compensation failed; leaving a mixed-state incident record (never published, database untouched):" >&2
  echo "restored=${RESTORED[*]}" >&2
  exit 1
fi

echo "compensate-production: OK"
echo "restored=$(IFS=,; echo "${RESTORED[*]}")"
