#!/usr/bin/env bash
set -euo pipefail

# Read-only post-deploy production verification (Pass 3, subphase 3.4). Runs
# before official publication (Decision 6): the three backends must be running
# the exact manifest digests on the exact release task definitions, the
# frontend live release.json marker must match the manifest, and the ALB target
# must be healthy. Never mutates anything.
#
# Usage:
#   verify-production.sh --manifest <official-manifest.json>
#     [--profile dpm-profile] [--region eu-north-1]
#
# Environment inputs: scripts/config/production.env (cluster + services +
# frontend bucket + ALB target group).
#
# Exit 0 when verification passes; 1 when any check fails closed; 2 on usage.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,16p' "${BASH_SOURCE[0]}" >&2
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

# Running tasks (read-only) -> containers[].imageDigest.
RUNNING_JSON=$(aws ecs list-tasks "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
  --query 'taskArns' --output json 2>/dev/null || echo '[]')
COUNT=$(printf '%s' "$RUNNING_JSON" | jq 'length')
if [ "$COUNT" -gt 0 ]; then
  mapfile -t TASK_ARNS < <(printf '%s' "$RUNNING_JSON" | jq -r '.[]')
  TASKS=$(aws ecs describe-tasks "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
    --tasks "${TASK_ARNS[@]}" --query 'tasks[]' --output json)
  printf '%s' "$TASKS" | jq '[.[] | {taskArn, taskDefinitionArn, lastStatus, containers: [.containers[]? | {name, imageDigest}]}]' \
    > "$TMP/running.json"
else
  printf '[]' > "$TMP/running.json"
fi

# Service task-definition ARNs.
SERVICES_JSON=$(aws ecs describe-services "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
  --services "${LC_SERVICES[@]}" \
  --query 'services[].{serviceName: serviceName, taskDefinition: taskDefinition}' --output json)
printf '%s' "$SERVICES_JSON" | jq '[.[] | {key: .serviceName, value: {taskDefinition: .taskDefinition}}] | from_entries' \
  > "$TMP/services.json"

# Frontend live marker.
LIVE_JSON="null"
if aws s3api get-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
  --key release.json "$TMP/live.json" >/dev/null 2>&1; then
  LIVE_JSON=$(cat "$TMP/live.json")
fi

# ALB target health.
ALB_JSON="null"
if [ -n "${LC_TARGET_GROUP_ARN:-}" ]; then
  ALB_JSON=$(aws elbv2 describe-target-health "${AWS_ARGS[@]}" \
    --target-group-arn "$LC_TARGET_GROUP_ARN" \
    --query 'TargetHealthDescriptions[].{target: target, targetHealth: targetHealth}' --output json 2>/dev/null || echo '[]')
fi

jq -n \
  --argjson running "$(cat "$TMP/running.json")" \
  --argjson services "$(cat "$TMP/services.json")" \
  --argjson live "${LIVE_JSON:-null}" \
  --argjson alb "${ALB_JSON:-null}" \
  '{running: $running, services: $services,
    frontend: {liveMarker: {exists: ($live != null), marker: $live}},
    alb: {targetHealth: $alb}}' > "$TMP/observed.json"

VERIFY_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion verify \
  --observed "$TMP/observed.json" --manifest "$MANIFEST") || true
printf '%s' "$VERIFY_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: production verification failed (fail closed; do not publish):" >&2
  printf '%s' "$VERIFY_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

echo "verify-production: OK"
echo "runningTasks=$COUNT"
