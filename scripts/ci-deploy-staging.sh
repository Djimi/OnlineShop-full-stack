#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="${1:-}"

if [ -z "$IMAGE_TAG" ]; then
  echo "Usage: $0 <image-tag>"
  echo "Example: $0 sha-abc123def456"
  exit 1
fi

CLUSTER="onlineshop-cluster"
ECR_BASE="799111666795.dkr.ecr.eu-north-1.amazonaws.com"
AWS_ARGS="--region eu-north-1"

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
    echo "Run scripts/setup-staging-env.sh first to create staging infrastructure."
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

  NEW_TD_ARN=$(aws ecs register-task-definition $AWS_ARGS \
    --family "$TD_FAMILY" \
    --task-role-arn "$(aws ecs describe-task-definition $AWS_ARGS --task-definition "$TD_FAMILY" --query 'taskDefinition.taskRoleArn' --output text)" \
    --execution-role-arn "$(aws ecs describe-task-definition $AWS_ARGS --task-definition "$TD_FAMILY" --query 'taskDefinition.executionRoleArn' --output text)" \
    --network-mode awsvpc \
    --requires-compatibilities FARGATE \
    --cpu "$(aws ecs describe-task-definition $AWS_ARGS --task-definition "$TD_FAMILY" --query 'taskDefinition.cpu' --output text)" \
    --memory "$(aws ecs describe-task-definition $AWS_ARGS --task-definition "$TD_FAMILY" --query 'taskDefinition.memory' --output text)" \
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

  echo "Waiting for deployment to stabilize (60s timeout)..."
  aws ecs wait services-stable $AWS_ARGS \
    --cluster "$CLUSTER" \
    --services "$SERVICE_NAME" \
    --max-wait 60 2>/dev/null || {
    echo "WARNING: Timed out waiting for '$SERVICE_NAME'. Checking task status..."
    TASK_ARN=$(aws ecs list-tasks $AWS_ARGS --cluster "$CLUSTER" --service-name "$SERVICE_NAME" --query 'taskArns[0]' --output text)
    if [ -n "$TASK_ARN" ] && [ "$TASK_ARN" != "None" ]; then
      aws ecs describe-tasks $AWS_ARGS --cluster "$CLUSTER" --tasks "$TASK_ARN" \
        --query 'tasks[0].{lastStatus:lastStatus,healthStatus:healthStatus,reason:stopReason}' \
        --output table
    fi
  }

  echo "'$SERVICE_NAME' deployment initiated."
done

echo ""
echo "=== Staging deployment complete ==="
