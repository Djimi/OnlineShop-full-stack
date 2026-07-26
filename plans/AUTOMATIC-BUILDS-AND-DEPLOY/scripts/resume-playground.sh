#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# resume-playground.sh
# Recreates ALB infrastructure, wires it to API Gateway ECS service,
# and scales all services to desired-count=1.
# Cost when running: ~$49.00/month (Spot) or ~$17-40/month depending on usage.
###############################################################################

PROFILE="--profile dpm-profile --region eu-north-1"
CLUSTER="onlineshop-cluster"

# --- Hardcoded infrastructure IDs (captured 2026-07-25) ---
VPC_ID="vpc-06eeb0bc47ecdbd61"
SUBNET_A="subnet-03b318e59490a891a"
SUBNET_B="subnet-041e4cf18bfce06f8"
SUBNET_C="subnet-0a009040ef6bce7cc"
ALB_SG="sg-0b5427a6a3bf31c29"
ECS_SG="sg-0b209104a6b15b157"

ALB_NAME="onlineshop-alb"
TG_NAME="onlineshop-gateway-tg"
GW_SERVICE="onlineshop-api-gateway"
AUTH_TD="onlineshop-auth"
ITEMS_TD="onlineshop-items"
GW_TD="onlineshop-api-gateway"

echo "=== RESUME PLAYGROUND ==="
echo "This will recreate ALB infrastructure and scale ECS services to 1."
echo ""

# --- Pre-flight: verify hardcoded infrastructure IDs still exist ---
echo "[0/8] Verifying infrastructure IDs..."
aws ec2 describe-vpcs --profile dpm-profile --region eu-north-1 --vpc-ids "$VPC_ID" > /dev/null 2>&1 || { echo "FATAL: VPC $VPC_ID not found"; exit 1; }
aws ec2 describe-security-groups --profile dpm-profile --region eu-north-1 --group-ids "$ALB_SG" > /dev/null 2>&1 || { echo "FATAL: Security group $ALB_SG not found"; exit 1; }
aws ec2 describe-security-groups --profile dpm-profile --region eu-north-1 --group-ids "$ECS_SG" > /dev/null 2>&1 || { echo "FATAL: Security group $ECS_SG not found"; exit 1; }
aws ec2 describe-subnets --profile dpm-profile --region eu-north-1 --subnet-ids "$SUBNET_A" "$SUBNET_B" "$SUBNET_C" > /dev/null 2>&1 || { echo "FATAL: One or more subnets not found"; exit 1; }
echo "  All infrastructure IDs verified."
echo ""

# --- Step 1: Check if ALB already exists ---
echo "[1/8] Checking if ALB already exists..."
EXISTING_ALB_ARN=$(aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 \
  --names "$ALB_NAME" --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || echo "")

if [ -n "$EXISTING_ALB_ARN" ]; then
  EXISTING_DNS=$(aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 \
    --names "$ALB_NAME" --query 'LoadBalancers[0].DNSName' --output text)
  echo "  ALB already exists: $EXISTING_ALB_ARN"
  echo "  DNS: $EXISTING_DNS"
  ALB_ARN="$EXISTING_ALB_ARN"
  ALB_DNS="$EXISTING_DNS"

  # Check if target group exists
  EXISTING_TG_ARN=$(aws elbv2 describe-target-groups --profile dpm-profile --region eu-north-1 \
    --names "$TG_NAME" --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || echo "")

  if [ -n "$EXISTING_TG_ARN" ] && [ "$EXISTING_TG_ARN" != "None" ]; then
    echo "  Target group already exists: $EXISTING_TG_ARN"
    TG_ARN="$EXISTING_TG_ARN"

    # Check if listener exists
    EXISTING_LISTENER_ARN=$(aws elbv2 describe-listeners --profile dpm-profile --region eu-north-1 \
      --load-balancer-arn "$ALB_ARN" --query 'Listeners[0].ListenerArn' --output text 2>/dev/null || echo "")

    if [ -n "$EXISTING_LISTENER_ARN" ] && [ "$EXISTING_LISTENER_ARN" != "None" ]; then
      echo "  Listener already exists: $EXISTING_LISTENER_ARN"
      echo "  Infrastructure already present. Skipping to service scaling."
    else
      echo "  Listener missing. Creating listener..."
      ALREADY_HAS_TG=false
      TG_ARN="$EXISTING_TG_ARN"
    fi
  else
    echo "  Target group missing. Creating target group and listener..."
    ALREADY_HAS_TG=false
  fi
else
  echo "  ALB not found. Creating full infrastructure..."
  NEEDS_ALB=true
fi

# --- Step 2: Create ALB ---
: ${NEEDS_ALB:=false}
if [ "$NEEDS_ALB" = true ]; then
  echo ""
  echo "[2/8] Creating ALB ($ALB_NAME)..."
  ALB_ARN=$(aws elbv2 create-load-balancer --profile dpm-profile --region eu-north-1 \
    --name "$ALB_NAME" \
    --subnets "$SUBNET_A" "$SUBNET_B" "$SUBNET_C" \
    --security-groups "$ALB_SG" \
    --scheme internet-facing \
    --type application \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text)
  ALB_DNS=$(aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 \
    --load-balancer-arns "$ALB_ARN" \
    --query 'LoadBalancers[0].DNSName' --output text)
  echo "  ALB created: $ALB_ARN"
  echo "  DNS: $ALB_DNS"
else
  echo "  Using existing ALB: $ALB_ARN"
fi

# --- Step 3: Create target group ---
: ${ALREADY_HAS_TG:=true}
: ${TG_ARN:=""}
if [ "$ALREADY_HAS_TG" = false ] || [ "$NEEDS_ALB" = true ]; then
  if [ -z "$TG_ARN" ]; then
    echo ""
    echo "[3/8] Creating target group ($TG_NAME)..."
    TG_ARN=$(aws elbv2 create-target-group --profile dpm-profile --region eu-north-1 \
      --name "$TG_NAME" \
      --protocol HTTP \
      --port 10000 \
      --target-type ip \
      --vpc-id "$VPC_ID" \
      --health-check-path "/actuator/health" \
      --health-check-protocol HTTP \
      --health-check-port "10000" \
      --health-check-interval-seconds 30 \
      --healthy-threshold-count 3 \
      --unhealthy-threshold-count 3 \
      --health-check-timeout-seconds 5 \
      --query 'TargetGroups[0].TargetGroupArn' --output text)
    echo "  Target group created: $TG_ARN"
  else
    echo ""
    echo "[3/8] Using existing target group ($TG_NAME): $TG_ARN"
  fi

  # --- Step 4: Create listener ---
  echo ""
  echo "[4/8] Creating listener (port 80 → $TG_NAME)..."
  aws elbv2 create-listener --profile dpm-profile --region eu-north-1 \
    --load-balancer-arn "$ALB_ARN" \
    --protocol HTTP \
    --port 80 \
    --default-actions Type=forward,TargetGroupArn="$TG_ARN"
  echo "  Listener created on port 80"
else
  echo "  Target group and listener already exist, skipping creation."
  # Ensure we have the right TG_ARN from the existing setup
  if [ -z "$TG_ARN" ]; then
    TG_ARN=$(aws elbv2 describe-target-groups --profile dpm-profile --region eu-north-1 \
      --names "$TG_NAME" --query 'TargetGroups[0].TargetGroupArn' --output text)
  fi
fi

# --- Step 5: Wire API Gateway service to target group ---
: ${NEEDS_WIRING:=false}
if [ "$NEEDS_ALB" = true ] || [ "$ALREADY_HAS_TG" = false ]; then
  NEEDS_WIRING=true
fi

if [ "$NEEDS_WIRING" = true ]; then
  echo ""
  echo "[5/8] Wiring API Gateway ECS service to target group..."
  aws ecs update-service --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" \
    --service "$GW_SERVICE" \
    --load-balancers targetGroupArn="$TG_ARN",containerName=api-gateway,containerPort=10000 \
    --force-new-deployment
  echo "  API Gateway service updated with target group $TG_ARN"
else
  echo ""
  echo "[5/8] Infrastructure already wired, skipping."
fi

# --- Step 6: Scale all services to desired-count=1 ---
echo ""
echo "[6/8] Scaling all ECS services to desired-count=1..."

for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  CURRENT_COUNT=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].desiredCount' --output text 2>/dev/null || echo "0")

  if [ "$CURRENT_COUNT" = "1" ]; then
    echo "  $svc: already at 1, skipping"
  else
    aws ecs update-service --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --service "$svc" --desired-count 1
    echo "  $svc: scaled to 1"
  fi
done

# --- Step 7: Wait for all services to be healthy ---
echo ""
echo "[7/8] Waiting for all services to reach steady state (up to 5 minutes)..."
echo "  Spring Boot startup takes ~3 minutes per task."

MAX_WAIT=300
WAITED=0
INTERVAL=15

while [ $WAITED -lt $MAX_WAIT ]; do
  ALL_STEADY=true
  for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
    STATUS=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --services "$svc" \
      --query 'services[0].deployments[0].rolloutState' --output text 2>/dev/null || echo "UNKNOWN")

    RUNNING=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --services "$svc" \
      --query 'services[0].runningCount' --output text 2>/dev/null || echo "0")

    if [ "$STATUS" = "FAILED" ]; then
      echo "  FATAL: $svc deployment FAILED"
      exit 1
    fi
    if [ "$STATUS" != "COMPLETED" ] || [ "$RUNNING" != "1" ]; then
      ALL_STEADY=false
      echo "  $svc: rolloutState=$STATUS running=$RUNNING"
    fi
  done

  if [ "$ALL_STEADY" = true ]; then
    echo "  All services steady!"
    break
  fi

  sleep $INTERVAL
  WAITED=$((WAITED + INTERVAL))
  echo "  Waited ${WAITED}s / ${MAX_WAIT}s..."
done

echo ""
echo "=== RESUME COMPLETE ==="
echo "ALB DNS: $ALB_DNS"
echo ""
echo "ALB ARN:       $ALB_ARN"
echo "Target Group:  $TG_ARN"
echo ""
echo "Services (all desired count 1, FARGATE_SPOT):"
for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  RUNNING=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].runningCount' --output text)
  HEALTH=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].deployments[0].rolloutState' --output text)
  echo "  $svc: running=$RUNNING rollout=$HEALTH"
done

echo ""
echo "Estimated running cost (Spot + ALB 24/7): ~\$49.00/month"
echo "  (includes ~\$10.95/month public IPv4 charge for 3 in-use IPs)"
echo "Test endpoint: curl http://$ALB_DNS/items"
echo ""
echo "To pause: bash plans/AUTOMATIC-BUILDS-AND-DEPLOY/scripts/pause-playground.sh"
