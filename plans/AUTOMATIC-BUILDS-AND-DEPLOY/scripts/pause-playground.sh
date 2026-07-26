#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# pause-playground.sh
# Scales ECS services to 0, then deletes ALB + listener + target group.
# Cost when paused: ~\$1.25/month (Secrets + ECR + Cloud Map + RDS free tier; KMS keys are AWS-managed = free)
###############################################################################

PROFILE="--profile dpm-profile --region eu-north-1"
CLUSTER="onlineshop-cluster"
ALB_NAME="onlineshop-alb"
TG_NAME="onlineshop-gateway-tg"

SERVICES=("onlineshop-auth" "onlineshop-items" "onlineshop-api-gateway")

echo "=== PAUSE PLAYGROUND ==="
echo "This will scale ECS services to 0 and delete ALB infrastructure."
echo "Cost after pause: ~\$1.25/month (Secrets, ECR, Cloud Map, RDS free tier; KMS = free)"
echo ""

# --- Step 1: Scale all ECS services to 0 ---
echo "[1/4] Scaling ECS services to desired-count=0..."

ALREADY_SCALED=true
for svc in "${SERVICES[@]}"; do
  CURRENT_COUNT=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].desiredCount' --output text 2>/dev/null || echo "UNKNOWN")

  if [ "$CURRENT_COUNT" = "0" ]; then
    echo "  $svc: already at 0, skipping"
  else
    ALREADY_SCALED=false
    aws ecs update-service --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --service "$svc" --desired-count 0
    echo "  $svc: scaled to 0"
  fi
done

echo "Waiting for tasks to drain (up to 60s)..."
MAX_WAIT=60
WAITED=0
INTERVAL=5
while [ $WAITED -lt $MAX_WAIT ]; do
  ALL_DRAINED=true
  for svc in "${SERVICES[@]}"; do
    RUNNING=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --services "$svc" \
      --query 'services[0].runningCount' --output text 2>/dev/null || echo "0")
    if [ "$RUNNING" != "0" ]; then
      ALL_DRAINED=false
    fi
  done
  if [ "$ALL_DRAINED" = true ]; then
    echo "  All tasks drained."
    break
  fi
  sleep $INTERVAL
  WAITED=$((WAITED + INTERVAL))
done
if [ "$ALL_DRAINED" != true ]; then
  echo "  WARNING: Not all tasks drained after ${MAX_WAIT}s. Proceeding anyway."
fi

# --- Step 2: Delete listener ---
echo ""
echo "[2/4] Deleting ALB listener..."

ALB_ARN=$(aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 \
  --names "$ALB_NAME" --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || echo "")

if [ -z "$ALB_ARN" ]; then
  echo "  ALB '$ALB_NAME' not found — already deleted. Skipping listener & ALB deletion."
else
  LISTENER_ARN=$(aws elbv2 describe-listeners --profile dpm-profile --region eu-north-1 \
    --load-balancer-arn "$ALB_ARN" \
    --query 'Listeners[0].ListenerArn' --output text 2>/dev/null || echo "")

  if [ -n "$LISTENER_ARN" ] && [ "$LISTENER_ARN" != "None" ]; then
    aws elbv2 delete-listener --profile dpm-profile --region eu-north-1 \
      --listener-arn "$LISTENER_ARN"
    echo "  Listener deleted: $LISTENER_ARN"
  else
    echo "  No listener found — already deleted."
  fi

  # --- Step 3: Delete target group ---
  echo ""
  echo "[3/4] Deleting target group..."

  TG_ARN=$(aws elbv2 describe-target-groups --profile dpm-profile --region eu-north-1 \
    --names "$TG_NAME" --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || echo "")

  if [ -n "$TG_ARN" ] && [ "$TG_ARN" != "None" ]; then
    aws elbv2 delete-target-group --profile dpm-profile --region eu-north-1 \
      --target-group-arn "$TG_ARN"
    echo "  Target group deleted: $TG_ARN"
  else
    echo "  Target group '$TG_NAME' not found — already deleted."
  fi

  # --- Step 4: Delete ALB ---
  echo ""
  echo "[4/4] Deleting ALB..."

  aws elbv2 delete-load-balancer --profile dpm-profile --region eu-north-1 \
    --load-balancer-arn "$ALB_ARN"
  echo "  ALB deleted: $ALB_NAME"
fi

echo ""
echo "=== PAUSE COMPLETE ==="
echo "ECS services: scaled to 0 (3 services @ 0 tasks)"
echo "ALB infrastructure: deleted (ALB + listener + target group)"
echo "RDS:        still running (free tier, auto-restarts after 7 days if stopped)"
echo "Secrets:    2 secrets retained (~\$0.80/month)"
echo "KMS:        2 AWS-managed keys retained (\$0.00/month, free)"
echo "ECR:        3 repos retained (~\$0.15/month)"
echo "Cloud Map:  namespace retained (~\$0.30/month)"
echo "---"
echo "Estimated paused cost: ~\$1.25/month"
echo ""
echo "To resume: bash plans/AUTOMATIC-BUILDS-AND-DEPLOY/scripts/resume-playground.sh"
