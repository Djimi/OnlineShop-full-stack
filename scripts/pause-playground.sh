#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# pause-playground.sh
# Scales production ECS services to 0, then deletes the hourly ALB and listener.
#
# Usage:  bash pause-playground.sh
#
# Cost when paused: ~$1.25/month
#   (Secrets + ECR + Cloud Map + RDS free tier; KMS keys are AWS-managed = free)
###############################################################################

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
      --cluster "$CLUSTER" --service "$svc" --desired-count 0 >/dev/null
    DESIRED=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --services "$svc" --query 'services[0].desiredCount' --output text)
    [ "$DESIRED" = "0" ] || { echo "FATAL: $svc did not scale to zero" >&2; exit 1; }
    echo "  $svc: scaled to 0"
  fi
done

for attempt in {1..20}; do
  ACTIVE=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "${SERVICES[@]}" \
    --query 'services[].[runningCount,pendingCount]' --output text | \
    awk '{ total += $1 + $2 } END { print total + 0 }')
  [ "$ACTIVE" = "0" ] && break
  [ "$attempt" = "20" ] && { echo "FATAL: service tasks did not stop" >&2; exit 1; }
  sleep 5
done

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

# The target group is free and remains associated with the ECS service. Keeping
# it makes resume deterministic and avoids deleting a resource still in use.

  # --- Step 3: Delete ALB ---
  echo ""
  echo "[3/3] Deleting ALB..."

  aws elbv2 delete-load-balancer --profile dpm-profile --region eu-north-1 \
    --load-balancer-arn "$ALB_ARN"
  if aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 \
    --load-balancer-arns "$ALB_ARN" >/dev/null 2>&1; then
    echo "FATAL: ALB still exists after delete" >&2
    exit 1
  fi
  echo "  ALB deleted: $ALB_NAME"
fi

echo ""
echo "=== PAUSE COMPLETE ==="
echo "ECS services: scaled to 0 (3 services @ 0 tasks)"
echo "ALB hourly infrastructure: deleted (ALB + listener; free target group retained)"
echo "RDS:        still running (free tier, auto-restarts after 7 days if stopped)"
echo "Secrets:    2 secrets retained (~\$0.80/month)"
echo "KMS:        2 AWS-managed keys retained (\$0.00/month, free)"
echo "ECR:        3 repos retained (~\$0.15/month)"
echo "Cloud Map:  namespace retained (~\$0.30/month)"
echo "---"
echo "Estimated paused cost: ~\$1.25/month"
echo ""
echo "To resume: bash scripts/resume-playground.sh"
