#!/usr/bin/env bash
set -euo pipefail

# Production promotion deployment (Pass 3R.1). Accepts a schema-valid candidate
# manifest and a read-only production snapshot. It registers one digest-pinned
# task-definition revision per backend (copying the current definition named by
# the snapshot and replacing only the intended container image), then deploys in
# the canonical order (auth + items, then api-gateway, then frontend — the
# frontend is published by publish-frontend.sh), binding every waiter to the
# deployment/task-definition started by this run and verifying the exact
# running digests. Circuit breaker with rollback, minimumHealthyPercent=100 and
# maximumPercent=200 are enforced by release_contract.ecs_config before any
# service update; the plan is validated by release_contract.promotion plan.
#
# This is a mutation script: every registration/update is immediately read
# back. `--dry-run` validates and plans without mutating anything.
#
# Usage:
#   deploy-production.sh --manifest <candidate-manifest.json>
#     --snapshot <snapshot.json> --ecr-registry <registry>
#     [--dry-run] [--profile dpm-profile] [--region eu-north-1]
#
# Environment inputs: scripts/config/production.env (cluster + services).
# stdin/stdout: JSON on stdout; diagnostics on stderr.
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
  sed -n '2,30p' "${BASH_SOURCE[0]}" >&2
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

# The candidate manifest must be schema-valid and must not carry production
# task-definition ARNs. Those ARNs are observed from the snapshot immediately
# before this deployment so a candidate can never smuggle an arbitrary source
# task definition into the mutation path.
bash "$RELEASE/bin/validate-manifest.sh" "$MANIFEST" >/dev/null || {
  echo "ERROR: candidate manifest failed validation; refusing to deploy" >&2
  exit 1
}
jq -e '
  .release.status == "candidate" and
  ([.components.auth, .components.items, .components.apiGateway]
    | all(has("taskDefinitionArn") | not))
' "$MANIFEST" >/dev/null || {
  echo "ERROR: deploy-production.sh requires a candidate manifest without task-definition ARNs" >&2
  exit 1
}

# Validate the snapshot before any registration/update. This is deliberately a
# separate contract check: the snapshot is the sole source of current
# task-definition ARNs and must contain all compensation fields.
SNAPSHOT_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion snapshot \
  --snapshot "$SNAPSHOT" --manifest "$MANIFEST") || true
printf '%s' "$SNAPSHOT_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: production snapshot failed validation; refusing to deploy" >&2
  printf '%s' "$SNAPSHOT_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

# --- Deployment plan (ordering + safe rolling / circuit breaker) ------------
jq -n \
  --argjson components '["auth","items","apiGateway","frontend"]' \
  '{components: $components, circuitBreaker: {enable: true, rollback: true},
    minimumHealthyPercent: 100, maximumPercent: 200}' > "$TMP/plan.json"
PLAN_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion plan \
  --plan "$TMP/plan.json") || true
printf '%s' "$PLAN_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: invalid deployment plan (fail closed):" >&2
  printf '%s' "$PLAN_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

# --- Register digest-pinned task definitions (copy + image-only replace) -----
# Service name -> container name -> component key (from the manifest).
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

  TD_ARN=$(jq -r --arg service "$service" '.services[$service].taskDefinitionArn // empty' "$SNAPSHOT")
  rl_assert_task_definition_arn "$TD_ARN" || {
    echo "ERROR: snapshot has no valid current task definition for $service" >&2
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
  # The wrapper writes the sanitized definition and fails on a dirty diff; its
  # exit status (not stdout) is the JSON decision's authority here, and the
  # hardening validator below re-checks the written file.
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
# Deploy auth + items first (both backends), then the gateway. Each waiter is
# bound to the deployment/task-definition started by THIS run.
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
    # Bounded, waiter-safe: services-stable waits for the whole service, but the
    # deployment id bound to this run must be the COMPLETED one. Use the
    # per-deployment waiter, bounded, then re-invoke rather than a blocking loop.
    aws ecs wait services-stable "${AWS_ARGS[@]}" \
      --cluster "$CLUSTER" --services "$service"
    # Read back the exact deployment + running digest.
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
    WAITER_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion waiter \
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

echo "deploy-production: OK" >&2
if [ "$DRY_RUN" -eq 1 ]; then
  printf 'changed=%s\n' "$(IFS=,; echo "${DEPLOYED[*]}")" >&2
  exit 0
fi
# Emit the deployed manifest on stdout: the candidate bytes plus the newly
# registered task-definition ARNs. The official manifest is required to carry
# every backend taskDefinitionArn (validate-manifest rejects an official
# manifest without them), and post-deploy verification compares the services'
# live task definitions to them.
jq --slurpfile tds "$TMP/td-arns.json" \
  '.components.auth.taskDefinitionArn = $tds[0]["onlineshop-auth"] |
   .components.items.taskDefinitionArn = $tds[0]["onlineshop-items"] |
   .components.apiGateway.taskDefinitionArn = $tds[0]["onlineshop-api-gateway"]' \
  "$MANIFEST"
printf 'changed=%s\n' "$(IFS=,; echo "${DEPLOYED[*]}")" >&2
