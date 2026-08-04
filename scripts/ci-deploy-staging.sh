#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="${1:-}"

if [ -z "$IMAGE_TAG" ]; then
  echo "Usage: $0 <image-tag>"
  echo "Example: $0 sha-abc123def456"
  exit 1
fi

CLUSTER="onlineshop-staging-cluster"
ECR_BASE="799111666795.dkr.ecr.eu-north-1.amazonaws.com"
AWS_ARGS="--profile dpm-profile --region eu-north-1"

echo "=== Deploying tag '$IMAGE_TAG' to staging ==="

SERVICES=(
  "onlineshop-auth-staging:onlineshop-auth:auth"
  "onlineshop-items-staging:onlineshop-items:items"
  "onlineshop-api-gateway-staging:onlineshop-api-gateway:api-gateway"
)

for svc_entry in "${SERVICES[@]}"; do
  IFS=':' read -r SERVICE_NAME ECR_REPO CONTAINER_NAME <<< "$svc_entry"

  echo ""
  echo "--- $SERVICE_NAME ($ECR_REPO) ---"

  TD_ARN=$(aws ecs describe-services $AWS_ARGS \
    --cluster "$CLUSTER" \
    --services "$SERVICE_NAME" \
    --query 'services[0].taskDefinition' \
    --output text 2>/dev/null || echo "")

  if [ -z "$TD_ARN" ] || [ "$TD_ARN" = "None" ]; then
    echo "ERROR: Service '$SERVICE_NAME' not found in cluster '$CLUSTER'"
    echo "Run the isolated staging provisioning/lifecycle scripts first."
    exit 1
  fi

  echo "Current task definition: $TD_ARN"

  TD_FAMILY=$(echo "$TD_ARN" | awk -F'/' '{print $2}' | awk -F':' '{print $1}')
  echo "Task definition family: $TD_FAMILY"

  CONTAINER_DEFS=$(aws ecs describe-task-definition $AWS_ARGS \
    --task-definition "$TD_FAMILY" \
    --query 'taskDefinition.containerDefinitions' \
    --output json)

  NEW_IMAGE="$ECR_BASE/$ECR_REPO:$IMAGE_TAG"
  echo "New image: $NEW_IMAGE"

  UPDATED_DEFS=$(echo "$CONTAINER_DEFS" | jq --arg name "$CONTAINER_NAME" --arg img "$NEW_IMAGE" \
    'map(if .name == $name then .image = $img else . end)')

  FULL_TD=$(aws ecs describe-task-definition $AWS_ARGS --task-definition "$TD_FAMILY" --output json)
  TR_ARN=$(echo "$FULL_TD" | jq -r '.taskDefinition.taskRoleArn // ""')
  EXEC_ARN=$(echo "$FULL_TD" | jq -r '.taskDefinition.executionRoleArn')
  TD_CPU=$(echo "$FULL_TD" | jq -r '.taskDefinition.cpu')
  TD_MEMORY=$(echo "$FULL_TD" | jq -r '.taskDefinition.memory')

  REG_ARGS=(--family "$TD_FAMILY" --execution-role-arn "$EXEC_ARN" --network-mode awsvpc --requires-compatibilities FARGATE --cpu "$TD_CPU" --memory "$TD_MEMORY")
  if [ -n "$TR_ARN" ] && [ "$TR_ARN" != "None" ]; then
    REG_ARGS+=(--task-role-arn "$TR_ARN")
  fi

  NEW_TD_ARN=$(aws ecs register-task-definition $AWS_ARGS \
    "${REG_ARGS[@]}" \
    --container-definitions "$UPDATED_DEFS" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)

  echo "New task definition: $NEW_TD_ARN"

  echo "Updating service '$SERVICE_NAME' to use new task definition..."
  aws ecs update-service $AWS_ARGS \
    --cluster "$CLUSTER" \
    --service "$SERVICE_NAME" \
    --task-definition "$NEW_TD_ARN" \
    --desired-count 1 \
    --no-cli-pager > /dev/null

  echo "Waiting for deployment to stabilize..."
  if ! aws ecs wait services-stable $AWS_ARGS \
    --cluster "$CLUSTER" \
    --services "$SERVICE_NAME"; then
    echo "ERROR: '$SERVICE_NAME' did not stabilize. Checking task status..."
    TASK_ARN=$(aws ecs list-tasks $AWS_ARGS --cluster "$CLUSTER" --service-name "$SERVICE_NAME" --query 'taskArns[0]' --output text)
    if [ -n "$TASK_ARN" ] && [ "$TASK_ARN" != "None" ]; then
      aws ecs describe-tasks $AWS_ARGS --cluster "$CLUSTER" --tasks "$TASK_ARN" \
        --query 'tasks[0].{lastStatus:lastStatus,healthStatus:healthStatus,reason:stopReason}' \
        --output table
    fi
    exit 1
  fi

  echo "'$SERVICE_NAME' deployment is stable."
done

echo ""
echo "=== Staging deployment complete ==="
