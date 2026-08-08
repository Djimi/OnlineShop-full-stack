# ACTIONS-DONE.md — Cost Optimization (2026-07-25)

## What Was Done

### 1. Fargate Spot Switch

Switched all 3 ECS services from `FARGATE` (On-Demand) to `FARGATE_SPOT` on 2026-07-25.

| Service | Before | After | Status |
|---|---|---|---|
| `onlineshop-auth` | `--launch-type FARGATE` | `FARGATE_SPOT` | IN_PROGRESS (rolling) |
| `onlineshop-items` | `--launch-type FARGATE` | `FARGATE_SPOT` | IN_PROGRESS (rolling) |
| `onlineshop-api-gateway` | `--launch-type FARGATE` | `FARGATE_SPOT` | IN_PROGRESS (rolling) |

**Commands run:**
```bash
for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  aws ecs update-service --profile dpm-profile --region eu-north-1 \
    --cluster onlineshop-cluster --service $svc \
    --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
    --force-new-deployment
done
```

**Impact:**
- Fargate compute costs reduced by ~60-70%
- Old projection: ~$62.98/month → New projection: ~$49.00/month (running 24/7)
- With pause scripts: ~$17-40/month (depends on usage pattern)

### 2. Pause/Resume Scripts Created

Created `scripts/` directory with two self-contained bash scripts:

| Script | Path | Purpose | Paused Cost |
|---|---|---|---|
| `pause-playground.sh` | `scripts/pause-playground.sh` | Scale ECS to 0 + delete ALB infrastructure | ~$1.25/month |
| `resume-playground.sh` | `scripts/resume-playground.sh` | Recreate ALB + TG + listener, scale ECS to 1, wire up API Gateway | Running cost |

**Hardcoded IDs embedded in scripts:**
- VPC: `vpc-06eeb0bc47ecdbd61`
- Subnets: `subnet-03b318e59490a891a`, `subnet-041e4cf18bfce06f8`, `subnet-0a009040ef6bce7cc`
- ALB Security Group: `sg-0b5427a6a3bf31c29`
- ECS Security Group: `sg-0b209104a6b15b157`

**Design decisions:**
- No runtime AWS queries for IDs (all hardcoded) — scripts work even with expired sessions
- No `jq` dependency — uses `--output text` and `--query` exclusively
- Error handling: scripts detect already-paused/already-resumed state and skip redundant operations
- `resume-playground.sh` waits up to 5 minutes for Spring Boot startup + health checks

### 3. Documentation Updated

- `AGENTS.md` — added Start/Stop Playground section
- `PLAN.md` — corrected cost trajectory, marked Pass 1 as IN PROGRESS
- `executed/INFO.md` — appended cost optimization changelog

## Verification Commands

### Check Spot Status
```bash
for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  aws ecs describe-services --profile dpm-profile --region eu-north-1 \
    --cluster onlineshop-cluster --services $svc \
    --query 'services[0].{name:serviceName,provider:capacityProviderStrategy[0].capacityProvider,running:runningCount,rollout:deployments[0].rolloutState}' \
    --output text
done
```

### Check ALB
```bash
aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 \
  --names onlineshop-alb --query 'LoadBalancers[0].{DNS:DNSName,State:State.Code}' --output text
```

### Smoke Test
```bash
ALB_DNS=$(aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 --names onlineshop-alb --query 'LoadBalancers[0].DNSName' --output text)
ALB="http://$ALB_DNS"
curl -s $ALB/items
```

### Run Pause/Resume
```bash
# Pause (from repo root)
bash scripts/pause-playground.sh

# Resume
bash scripts/resume-playground.sh
```

## Current Cost Projection (After Changes)

| Scenario | Monthly Cost |
|---|---|
| Running 24/7 on Spot (current) | ~$49.00 |
| Running 8hr/day, 5 days/week on Spot + pause ALB | ~$17-18 |
| Fully paused | ~$1.25 |

## What Was NOT Done

- System left **running** (not paused) — all 3 services + ALB + RDS are up
- No data deleted (RDS, Secrets, KMS, ECR all intact)
- Frontend not deployed (S3 + CloudFront not set up — Pass 1 incomplete)
- No EventBridge scheduled scaling (deferred to Pass 4)
- No billing alarm at $10/month set up (recommended but deferred)
