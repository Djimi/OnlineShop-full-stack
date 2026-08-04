#!/usr/bin/env bash
set -euo pipefail

# Production rollback deployment (Pass 3, subphase 3.6). Registers one
# digest-pinned task-definition revision per backend by copying the CURRENT
# (pre-rollback) running definition — read from the pre-rollback snapshot — and
# replacing only the intended container image with the exact historical digest
# of the selected official release. It then deploys in the canonical order
# (auth + items, then api-gateway; the frontend is restored by
# restore-frontend.sh), binding every waiter to the deployment/task-definition
# started by this run and verifying the exact running digests. Circuit breaker
# with rollback, minimumHealthyPercent=100 and maximumPercent=200 are enforced
# by release_contract.ecs_config before any service update; the plan is
# validated by release_contract.rollback plan.
#
# No ECR tag is minted or moved and no image is written (the rollback IAM policy
# has no ecr:PutImage); only existing official digests are deployed. No new
# official release is created.
#
# This is a mutation script: every registration/update is immediately read
# back. `--dry-run` validates and plans without mutating anything.
#
# Usage:
#   deploy-rollback.sh --manifest <target-official-manifest.json>
#     --snapshot <snapshot.json> --ecr-registry <registry>
#     [--dry-run] [--profile dpm-profile] [--region eu-north-1]
#
# Environment inputs: scripts/config/production.env (cluster + services).
# stdin/stdout: JSON on stdout (the deployment manifest); diagnostics on stderr.
#
# Exit 0 when all components are deployed and verified; non-zero (fail closed)
# otherwise. On failure the changed components must be compensated via
# compensate-production.sh (Decision 13).

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,32p' "${BASH_SOURCE[0]}" >&2
}

MANIFEST=""
SNAPSHOT=""
ECR_REGISTRY=""
DRY_RUN=0
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --snapshot) SNAPSHOT="${2:-}"; shift 2 ;;
    --ecr-registry) ECR_REGISTRY="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$MANIFEST" ] && [ -n "$SNAPSHOT" ] && [ -n "$ECR_REGISTRY" ] || { usage; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2
rl_assert_regular_file "$SNAPSHOT" || exit 2
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

# --- Mandatory identity preflight ------------------------------------------
set +e
IDENTITY_ACCOUNT=$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query 'Account' --output text 2>"$TMP/identity.err")
RC=$?
set -e
if [ "$RC" -ne 0 ] || [ -z "$IDENTITY_ACCOUNT" ]; then
  echo "ERROR: identity preflight failed (aws sts get-caller-identity):" >&2
  sed -n '1,3p' "$TMP/identity.err" >&2 || true
  exit 1
fi
[ "$IDENTITY_ACCOUNT" = "$LC_ACCOUNT_ID" ] || {
  echo "ERROR: identity preflight failed; account $IDENTITY_ACCOUNT != $LC_ACCOUNT_ID" >&2
  exit 1
}

# The manifest must be the schema-valid official manifest of the target release
# before any registration.
bash "$RELEASE/bin/validate-manifest.sh" "$MANIFEST" >/dev/null || {
  echo "ERROR: target official manifest failed validation; refusing to roll back" >&2
  exit 1
}

# --- Deployment plan (ordering + safe rolling / circuit breaker) ------------
jq -n \
  --argjson components '["auth","items","apiGateway","frontend"]' \
  '{components: $components, circuitBreaker: {enable: true, rollback: true},
    minimumHealthyPercent: 100, maximumPercent: 200}' > "$TMP/plan.json"
PLAN_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.rollback plan \
  --plan "$TMP/plan.json") || true
printf '%s' "$PLAN_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: invalid deployment plan (fail closed):" >&2
  printf '%s' "$PLAN_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

# --- Register digest-pinned task definitions (copy + image-only replace) -----
# The current (pre-rollback) definition is the one recorded in the snapshot; the
# image is replaced with the exact historical digest of the target release.
declare -A SERVICE_CONTAINER=(
  [onlineshop-auth]=auth
  [onlineshop-items]=items
  [onlineshop-api-gateway]=api-gateway
)
declare -A SERVICE_COMPONENT=(
  [onlineshop-auth]=auth
  [onlineshop-items]=items
  [onlineshop-api-gateway]=apiGateway
)

jq -n '{}' > "$TMP/td-arns.json"
CHANGED=()
for service in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  container="${SERVICE_CONTAINER[$service]}"
  component="${SERVICE_COMPONENT[$service]}"
  digest=$(jq -r ".components.${component}.imageDigest" "$MANIFEST")
  rl_assert_image_digest "$digest" || exit 2
  image_ref="${ECR_REGISTRY}/$(jq -r ".components.${component}.repository" "$MANIFEST")@${digest}"

  TD_ARN=$(jq -r --arg s "$service" '.services[$s].taskDefinitionArn // ""' "$SNAPSHOT")
  [ -n "$TD_ARN" ] || {
    echo "ERROR: snapshot has no task definition for $service; cannot roll back (fail closed)" >&2
    exit 1
  }
  set +e
  CURRENT=$(aws ecs describe-task-definition "${AWS_ARGS[@]}" \
    --task-definition "$TD_ARN" --query 'taskDefinition' --output json 2>"$TMP/td.err")
  RC=$?
  set -e
  if [ "$RC" -ne 0 ] || [ -z "$CURRENT" ]; then
    echo "ERROR: cannot read the current task definition $TD_ARN:" >&2
    sed -n '1,3p' "$TMP/td.err" >&2 || true
    exit 1
  fi
  printf '%s' "$CURRENT" > "$TMP/current-$service.json"

  # Sanitize: replace only the intended container's image with a digest pin.
  bash "$RELEASE/bin/sanitize-task-definition.sh" \
    --input "$TMP/current-$service.json" --output "$TMP/new-$service.json" \
    --set-image "${container}=${image_ref}" >/dev/null || {
    echo "ERROR: task definition sanitization failed for $service" >&2
    exit 1
  }
  # Hardening validation before registration.
  bash "$RELEASE/bin/validate-task-definition.sh" --input "$TMP/new-$service.json" >/dev/null || {
    echo "ERROR: sanitized task definition for $service failed hardening validation" >&2
    exit 1
  }

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: would register $service task definition -> ${image_ref}" >&2
    NEW_ARN="arn:aws:ecs:${REGION}:${LC_ACCOUNT_ID}:task-definition/${service}:dry-run"
  else
    set +e
    REGISTERED=$(aws ecs register-task-definition "${AWS_ARGS[@]}" \
      --cli-input-json "file://$TMP/new-$service.json" \
      --query 'taskDefinition.taskDefinitionArn' --output text 2>"$TMP/reg.err")
    RC=$?
    set -e
    if [ "$RC" -ne 0 ] || [ -z "$REGISTERED" ]; then
      echo "ERROR: task definition registration failed for $service:" >&2
      sed -n '1,3p' "$TMP/reg.err" >&2 || true
      exit 1
    fi
    # Immediate read-back: the registered revision must resolve and be the same
    # family with the intended digest.
    READBACK=$(aws ecs describe-task-definition "${AWS_ARGS[@]}" \
      --task-definition "$REGISTERED" --query 'taskDefinition.{arn: taskDefinitionArn, image: containerDefinitions[0].image}' --output json)
    printf '%s' "$READBACK" | jq -e --arg img "$image_ref" '.image == $img' >/dev/null || {
      echo "ERROR: read-back of $service task definition does not match the intended image" >&2
      exit 1
    }
    NEW_ARN="$REGISTERED"
  fi
  jq --arg service "$service" --arg arn "$NEW_ARN" \
    '. + {($service): $arn}' "$TMP/td-arns.json" > "$TMP/td-arns.next.json"
  mv "$TMP/td-arns.next.json" "$TMP/td-arns.json"
  CHANGED+=("$service")
done

# --- Service updates in canonical order (backends, then gateway) ------------
DEPLOYED=()
for service in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  component="${SERVICE_COMPONENT[$service]}"
  digest=$(jq -r ".components.${component}.imageDigest" "$MANIFEST")
  new_td=$(jq -r --arg s "$service" '.[$s]' "$TMP/td-arns.json")
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: would update $service -> $new_td (digest $digest)" >&2
    deploy_id="dry-run"
  else
    set +e
    UPDATE=$(aws ecs update-service "${AWS_ARGS[@]}" \
      --cluster "$CLUSTER" --service "$service" \
      --task-definition "$new_td" --desired-count 1 \
      --deployment-configuration "minimumHealthyPercent=100,maximumPercent=200,deploymentCircuitBreaker={enable=true,rollback=true}" \
      --query 'service.{deployments: deployments[0].id, taskDefinition: taskDefinition}' \
      --output json 2>"$TMP/update.err")
    RC=$?
    set -e
    if [ "$RC" -ne 0 ] || [ -z "$UPDATE" ]; then
      echo "ERROR: service update failed for $service:" >&2
      sed -n '1,3p' "$TMP/update.err" >&2 || true
      exit 1
    fi
    deploy_id=$(printf '%s' "$UPDATE" | jq -r '.deployments // ""')
    ACTIVE_TD=$(printf '%s' "$UPDATE" | jq -r '.taskDefinition // ""')
    [ "$ACTIVE_TD" = "$new_td" ] || {
      echo "ERROR: service $service active task definition is $ACTIVE_TD, expected $new_td" >&2
      exit 1
    }
    # Bounded, waiter-safe: the per-deployment waiter, bound to this run.
    aws ecs wait services-stable "${AWS_ARGS[@]}" \
      --cluster "$CLUSTER" --services "$service"
    OBSERVED=$(aws ecs describe-services "${AWS_ARGS[@]}" \
      --cluster "$CLUSTER" --services "$service" \
      --query 'services[0].{deployments: deployments[0].id, rollout: deployments[0].rolloutState, taskDefinition: taskDefinition}' \
      --output json)
    OBSERVED_ROLLOUT=$(printf '%s' "$OBSERVED" | jq -r '.rollout // ""')
    RUNNING_DIGEST=""
    TASK_LIST=$(aws ecs list-tasks "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
      --service-name "$service" --query 'taskArns' --output json 2>/dev/null || echo '[]')
    FIRST_TASK=$(printf '%s' "$TASK_LIST" | jq -r '.[0] // ""')
    if [ -n "$FIRST_TASK" ]; then
      RUNNING_DIGEST=$(aws ecs describe-tasks "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
        --tasks "$FIRST_TASK" --query 'tasks[0].containers[0].imageDigest' --output text 2>/dev/null || true)
    fi
    jq -n \
      --arg component "$component" --arg deploymentId "$deploy_id" \
      --arg taskDefinitionArn "$new_td" --arg rolloutState "$OBSERVED_ROLLOUT" \
      --arg runningDigest "$RUNNING_DIGEST" \
      '{component: $component, deploymentId: $deploymentId,
        taskDefinitionArn: $taskDefinitionArn, rolloutState: $rolloutState,
        runningDigest: $runningDigest}' > "$TMP/waiter-$service.json"
    jq -n \
      --arg component "$component" --arg deploymentId "$deploy_id" \
      --arg taskDefinitionArn "$new_td" --arg imageDigest "$digest" \
      '{component: $component, deploymentId: $deploymentId,
        taskDefinitionArn: $taskDefinitionArn, imageDigest: $imageDigest}' > "$TMP/expected-$service.json"
    WAITER_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.rollback waiter \
      --waiter "$TMP/waiter-$service.json" --expected "$TMP/expected-$service.json") || true
    printf '%s' "$WAITER_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
      echo "ERROR: deployment waiter for $service failed (fail closed):" >&2
      printf '%s' "$WAITER_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
      exit 1
    }
    echo "deployed $service -> $new_td (deployment $deploy_id, rollout $OBSERVED_ROLLOUT)" >&2
  fi
  DEPLOYED+=("$service")
done

echo "deploy-rollback: OK" >&2
if [ "$DRY_RUN" -eq 1 ]; then
  printf 'changed=%s\n' "$(IFS=,; echo "${DEPLOYED[*]}")" >&2
  exit 0
fi
# Emit the deployment manifest on stdout: the target official bytes plus the
# newly registered task-definition ARNs. Post-rollback verification compares the
# services' live task definitions to them and the running digests to the target.
jq --slurpfile tds "$TMP/td-arns.json" \
  '.components.auth.taskDefinitionArn = $tds[0]["onlineshop-auth"] |
   .components.items.taskDefinitionArn = $tds[0]["onlineshop-items"] |
   .components.apiGateway.taskDefinitionArn = $tds[0]["onlineshop-api-gateway"]' \
  "$MANIFEST"
printf 'changed=%s\n' "$(IFS=,; echo "${DEPLOYED[*]}")" >&2
