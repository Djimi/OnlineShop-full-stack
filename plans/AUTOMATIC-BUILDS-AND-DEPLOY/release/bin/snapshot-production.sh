#!/usr/bin/env bash
set -euo pipefail

# Read-only pre-promotion snapshot of the production environment (Pass 3,
# subphase 3.4). Captures the exact state that promotion may mutate so that a
# later failure can compensate (Decision 13) and an interrupted promotion can
# resume deterministically:
#
#   - per-service desired count, capacity-provider strategy, current
#     task-definition ARN, running container digest, ALB load-balancer wiring,
#     and the active deployment id;
#   - the deployed frontend release.json marker and the live index.html checksum;
#   - the current official release identity (version/git tag/source SHA).
#
# The snapshot is written to stdout as JSON and validated by
# `release_contract.promotion snapshot` against the candidate manifest. This
# script never mutates anything.
#
# Usage:
#   snapshot-production.sh --manifest <candidate-manifest.json> [--json]
#     [--profile dpm-profile] [--region eu-north-1]
#
# Environment inputs: scripts/config/production.env is loaded for the cluster,
# services, and frontend bucket.
#
# Exit 0 when the snapshot is gathered AND valid; 1 when any read or validation
# fails closed; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" >&2
}

MANIFEST=""
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$MANIFEST" ] || { usage; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2
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

# Gather each service's pre-promotion state (read-only).
jq -n '{}' > "$TMP/services.json"
RUNNING_COUNT=0
for service in "${LC_SERVICES[@]}"; do
  set +e
  DESCRIBE=$(aws ecs describe-services "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
    --services "$service" --query 'services[0]' --output json 2>"$TMP/ecs.err")
  RC=$?
  set -e
  if [ "$RC" -ne 0 ] || [ -z "$DESCRIBE" ] || [ "$DESCRIBE" = "null" ]; then
    echo "ERROR: cannot describe production service $service (read failed, not absent):" >&2
    sed -n '1,3p' "$TMP/ecs.err" >&2 || true
    exit 1
  fi
  TD_ARN=$(printf '%s' "$DESCRIBE" | jq -r '.taskDefinition // ""')
  DESIRED=$(printf '%s' "$DESCRIBE" | jq -r '.desiredCount // 0')
  DEPLOY_ID=$(printf '%s' "$DESCRIBE" | jq -r '.deployments[0].id // ""')
  DEPLOY_ROLLOUT=$(printf '%s' "$DESCRIBE" | jq -r '.deployments[0].rolloutState // ""')
  RUNNING_DIGEST=""
  set +e
  TASK_LIST=$(aws ecs list-tasks "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
    --service-name "$service" --query 'taskArns' --output json 2>/dev/null)
  RC=$?
  set -e
  if [ "$RC" -eq 0 ]; then
    COUNT=$(printf '%s' "${TASK_LIST:-[]}" | jq 'length')
    RUNNING_COUNT=$((RUNNING_COUNT + COUNT))
    if [ "$COUNT" -gt 0 ]; then
      FIRST_TASK=$(printf '%s' "${TASK_LIST:-[]}" | jq -r '.[0]')
      set +e
      TASK_JSON=$(aws ecs describe-tasks "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
        --tasks "$FIRST_TASK" --query 'tasks[0]' --output json 2>/dev/null)
      RC=$?
      set -e
      if [ "$RC" -eq 0 ] && [ -n "$TASK_JSON" ]; then
        RUNNING_DIGEST=$(printf '%s' "$TASK_JSON" | jq -r '.containers[0].imageDigest // ""')
      fi
    fi
  fi
  if [ -z "$RUNNING_DIGEST" ]; then
    RUNNING_DIGEST="null"
  else
    RUNNING_DIGEST="\"$RUNNING_DIGEST\""
  fi

  jq --arg service "$service" --argjson desired "$DESIRED" \
    --arg td "$TD_ARN" --argjson runningDigest "$RUNNING_DIGEST" \
    --arg deployId "$DEPLOY_ID" --arg rollout "$DEPLOY_ROLLOUT" \
    --argjson lb "$(printf '%s' "$DESCRIBE" | jq '.loadBalancers // []')" \
    --argjson cps "$(printf '%s' "$DESCRIBE" | jq '.capacityProviderStrategy // []')" \
    '. + {($service): {desiredCount: $desired, capacityProviderStrategy: $cps,
      taskDefinitionArn: $td, runningDigest: $runningDigest, loadBalancers: $lb,
      deployments: [{id: $deployId, rolloutState: ($rollout | if . == "" then "UNKNOWN" else . end)}]}}' \
    "$TMP/services.json" > "$TMP/services.next.json"
  mv "$TMP/services.next.json" "$TMP/services.json"
done

# Frontend state: the deployed live marker + the live index.html checksum.
MARKER_JSON="null"
set +e
aws s3api get-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
  --key release.json "$TMP/live.json" >/dev/null 2>"$TMP/frontend.err"
RC=$?
set -e
if [ "$RC" -eq 0 ]; then
  if jq -e 'type == "object"' "$TMP/live.json" >/dev/null 2>&1; then
    MARKER_JSON=$(cat "$TMP/live.json")
  else
    echo "ERROR: deployed release.json marker is not a JSON object" >&2
    exit 1
  fi
elif grep -qiE 'not ?found|does not exist|NoSuchKey|not be found' "$TMP/frontend.err"; then
  :
else
  echo "ERROR: cannot read the deployed frontend marker (read failed):" >&2
  sed -n '1,3p' "$TMP/frontend.err" >&2 || true
  exit 1
fi

INDEX_SHA=""
set +e
aws s3api head-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
  --key index.html --query 'ChecksumSHA256 // ETag' --output text >"$TMP/index-sha" 2>/dev/null
RC=$?
set -e
if [ "$RC" -eq 0 ]; then
  INDEX_SHA=$(cat "$TMP/index-sha" | tr -d '"' || true)
fi

# Current official release identity: the newest v* tag from GitHub (read-only),
# or a local override for offline testing.
OFFICIAL_JSON="null"
set +e
if [ -n "${GITHUB_REPOSITORY:-}" ] && command -v gh >/dev/null 2>&1; then
  TAG=$(gh api "repos/${GITHUB_REPOSITORY}/tags" \
    --jq '[.[].name | select(startswith("v"))] | sort | last // ""' 2>/dev/null || true)
  if [ -n "$TAG" ]; then
    OFFICIAL_VERSION=$(printf '%s' "$TAG" | sed 's/^v//')
    OFFICIAL_SHA=$(gh api "repos/${GITHUB_REPOSITORY}/git/ref/tags/${TAG}" \
      --jq '.object.sha' 2>/dev/null || true)
    jq -n --arg version "$OFFICIAL_VERSION" --arg tag "$TAG" --arg sha "$OFFICIAL_SHA" \
      '{version: $version, gitTag: $tag, sourceSha: $sha}' > "$TMP/official.json"
    OFFICIAL_JSON=$(cat "$TMP/official.json")
  fi
fi

# The snapshot must be schema-complete; a paused environment is recorded
# honestly with its current task-definition digests.
PAUSED=false
[ "$RUNNING_COUNT" -eq 0 ] && PAUSED=true

jq -n \
  --argjson paused "$PAUSED" \
  --argjson services "$(cat "$TMP/services.json")" \
  --argjson marker "${MARKER_JSON:-null}" \
  --arg indexSha "$INDEX_SHA" \
  --argjson official "${OFFICIAL_JSON:-null}" \
  '{paused: $paused, services: $services,
    frontend: {marker: $marker, indexSha256: $indexSha},
    officialRelease: $official}' > "$TMP/snapshot.json"

SNAPSHOT_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion snapshot \
  --snapshot "$TMP/snapshot.json" --manifest "$MANIFEST") || true
printf '%s' "$SNAPSHOT_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: pre-promotion snapshot is incomplete (fail closed):" >&2
  printf '%s' "$SNAPSHOT_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

cat "$TMP/snapshot.json"
echo "snapshot-production: OK (paused=$PAUSED, services=${#LC_SERVICES[@]})" >&2
