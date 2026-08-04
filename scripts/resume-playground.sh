#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# resume-playground.sh
# Recreates ALB infrastructure, wires it to API Gateway ECS service,
# and scales all services to desired-count=1.
#
# Usage:  bash resume-playground.sh [--on-demand]
#
#   (none)       Use FARGATE_SPOT (cost-optimized default).
#   --on-demand  Use regular FARGATE for deterministic short validation sessions.
#
# Cost (FARGATE):        ~$76.00/month  (3 tasks × $0.05/hr + ALB + IPs)
# Cost (FARGATE_SPOT):   ~$49.00/month  (3 tasks × $0.02/hr + ALB + IPs)
# Both include ~$10.95/month public IPv4 charge for 3 in-use IPs.
###############################################################################

# --- Argument parsing ---
USE_SPOT=true
for arg in "$@"; do
  case "$arg" in
    --on-demand) USE_SPOT=false ;;
    --help) echo "Usage: bash resume-playground.sh [--on-demand]"; exit 0 ;;
    *) echo "Unknown argument: $arg"; echo "Usage: bash resume-playground.sh [--on-demand]"; exit 1 ;;
  esac
done

if [ "$USE_SPOT" = true ]; then
  CAPACITY_PROVIDER="FARGATE_SPOT"
else
  CAPACITY_PROVIDER="FARGATE"
fi

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
echo "Capacity provider: $CAPACITY_PROVIDER (use --on-demand for regular FARGATE)"
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

# --- Steps 3-4: Reuse the free target group; recreate only missing hourly ALB pieces ---
TG_ARN=$(aws elbv2 describe-target-groups --profile dpm-profile --region eu-north-1 \
  --names "$TG_NAME" --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || true)
if [ -z "$TG_ARN" ] || [ "$TG_ARN" = "None" ]; then
  TG_ARN=$(aws elbv2 create-target-group --profile dpm-profile --region eu-north-1 \
    --name "$TG_NAME" --protocol HTTP --port 10000 --target-type ip --vpc-id "$VPC_ID" \
    --health-check-path "/actuator/health" --health-check-protocol HTTP \
    --health-check-port traffic-port --health-check-interval-seconds 30 \
    --healthy-threshold-count 3 --unhealthy-threshold-count 3 --health-check-timeout-seconds 5 \
    --query 'TargetGroups[0].TargetGroupArn' --output text)
  aws elbv2 describe-target-groups --profile dpm-profile --region eu-north-1 \
    --target-group-arns "$TG_ARN" --query 'TargetGroups[0].{Arn:TargetGroupArn,Vpc:VpcId}'
fi

LISTENER_ARN=$(aws elbv2 describe-listeners --profile dpm-profile --region eu-north-1 \
  --load-balancer-arn "$ALB_ARN" --query 'Listeners[0].ListenerArn' --output text 2>/dev/null || true)
if [ -z "$LISTENER_ARN" ] || [ "$LISTENER_ARN" = "None" ]; then
  LISTENER_ARN=$(aws elbv2 create-listener --profile dpm-profile --region eu-north-1 \
    --load-balancer-arn "$ALB_ARN" --protocol HTTP --port 80 \
    --default-actions Type=forward,TargetGroupArn="$TG_ARN" \
    --query 'Listeners[0].ListenerArn' --output text)
  aws elbv2 describe-listeners --profile dpm-profile --region eu-north-1 \
    --listener-arns "$LISTENER_ARN" --query 'Listeners[0].{Arn:ListenerArn,Port:Port}'
fi

# --- Step 5: Wire API Gateway service to target group ---
: ${NEEDS_WIRING:=false}
if [ "$NEEDS_ALB" = true ]; then
  NEEDS_WIRING=true
fi

if [ "$NEEDS_WIRING" = true ]; then
  echo ""
  echo "[5/8] Wiring API Gateway ECS service to target group..."
  aws ecs update-service --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" \
    --service "$GW_SERVICE" \
    --load-balancers targetGroupArn="$TG_ARN",containerName=api-gateway,containerPort=10000 \
    --force-new-deployment >/dev/null
  aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$GW_SERVICE" \
    --query 'services[0].{Name:serviceName,LoadBalancers:loadBalancers}'
  echo "  API Gateway service updated with target group $TG_ARN"
else
  echo ""
  echo "[5/8] Infrastructure already wired, skipping."
fi

# --- Step 6: Scale all services to desired-count=1 and set capacity provider ---
echo ""
echo "[6/8] Scaling all ECS services to desired-count=1 (capacity: $CAPACITY_PROVIDER)..."

for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  CURRENT_COUNT=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].desiredCount' --output text 2>/dev/null || echo "0")

  CURRENT_CAP=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].capacityProviderStrategy[0].capacityProvider' --output text 2>/dev/null || echo "")

  if [ "$CURRENT_COUNT" = "1" ] && [ "$CURRENT_CAP" = "$CAPACITY_PROVIDER" ]; then
    echo "  $svc: already at 1 ($CAPACITY_PROVIDER), skipping"
  elif [ "$CURRENT_COUNT" = "1" ]; then
    echo "  $svc: at 1 but capacity is $CURRENT_CAP, switching to $CAPACITY_PROVIDER..."
    aws ecs update-service --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --service "$svc" \
      --capacity-provider-strategy "capacityProvider=$CAPACITY_PROVIDER,weight=1,base=1" \
      --force-new-deployment >/dev/null
    aws ecs describe-services --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --services "$svc" \
      --query 'services[0].{Name:serviceName,Desired:desiredCount,Capacity:capacityProviderStrategy}'
    echo "  $svc: capacity updated"
  else
    aws ecs update-service --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --service "$svc" \
      --desired-count 1 \
      --capacity-provider-strategy "capacityProvider=$CAPACITY_PROVIDER,weight=1,base=1" \
      --force-new-deployment >/dev/null
    aws ecs describe-services --profile dpm-profile --region eu-north-1 \
      --cluster "$CLUSTER" --services "$svc" \
      --query 'services[0].{Name:serviceName,Desired:desiredCount,Capacity:capacityProviderStrategy}'
    echo "  $svc: scaled to 1 ($CAPACITY_PROVIDER)"
  fi
done

# --- Step 7: Wait for all services to be healthy ---
echo ""
echo "[7/8] Waiting for all services to reach steady state..."
echo "  Spring Boot startup takes ~3 minutes per task."

aws ecs wait services-stable --profile dpm-profile --region eu-north-1 \
  --cluster "$CLUSTER" \
  --services onlineshop-auth onlineshop-items onlineshop-api-gateway
aws ecs describe-services --profile dpm-profile --region eu-north-1 \
  --cluster "$CLUSTER" \
  --services onlineshop-auth onlineshop-items onlineshop-api-gateway \
  --query 'services[].{Name:serviceName,Desired:desiredCount,Running:runningCount,Rollout:deployments[0].rolloutState}'

echo ""
echo "=== RESUME COMPLETE ==="
echo "ALB DNS: $ALB_DNS"
echo ""
echo "ALB ARN:       $ALB_ARN"
echo "Target Group:  $TG_ARN"
echo ""
echo "Services (all desired count 1):"
for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  RUNNING=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].runningCount' --output text)
  HEALTH=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].deployments[0].rolloutState' --output text)
  CAP=$(aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].capacityProviderStrategy[*].capacityProvider[]' --output text | tr '\t' ',')
  echo "  $svc: running=$RUNNING rollout=$HEALTH capacity=$CAP"
done

echo ""
if [ "$USE_SPOT" = true ]; then
  echo "Estimated running cost: ~\$49.00/month (FARGATE_SPOT + ALB 24/7)"
else
  echo "Estimated running cost: ~\$76.00/month (FARGATE + ALB 24/7)"
fi
echo "  (includes ~\$10.95/month public IPv4 charge for 3 in-use IPs)"
echo ""
echo "Test endpoint: curl http://$ALB_DNS/items"
ready=false
for attempt in $(seq 1 12); do
  status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --header 'Authorization: Bearer production-readiness-invalid-token' \
    "http://$ALB_DNS/items" || true)
  if [ "$status" = "401" ]; then ready=true; break; fi
  sleep 5
done
[ "$ready" = true ] || { echo "FATAL: production API did not become ready within 60 seconds" >&2; exit 1; }
echo ""
echo "To pause: bash scripts/pause-playground.sh"
