# Cost Explanation — Why $11+ in "Past Days"

> **Date of analysis:** 2026-07-25 (updated 2026-07-27)  
> **MTD Cost Explorer API total:** $0.00 (billing data not yet finalized in Cost Explorer)  
> **Confirmed running:** 3 ECS services (Fargate Spot for items + gateway, Fargate On-Demand for auth), 1 ALB, 1 RDS db.t4g.micro, 3 public IPv4 addresses (in-use), 2 Secrets, 2 KMS keys, Cloud Map NS, ECR repos  
> **Note:** Auth ECS service has `desiredCount=1` but `runningCount=0` — task fails with `CannotPullContainerError` (image tag not found in ECR). Auth compute cost is currently $0.

---

## 1. What's Running (and Burning Money)

| Resource | Spec | Hourly Cost | Daily Cost | Monthly Burn |
|---|---|---|---|---|
| Auth ECS task | 0.25 vCPU / 512 MB (Fargate Spot *) | $0.0043 | $0.10 | $3.15 |
| Items ECS task | 0.25 vCPU / 512 MB (Fargate Spot) | $0.0043 | $0.10 | $3.15 |
| API Gateway ECS task | 0.5 vCPU / 1024 MB (Fargate Spot) | $0.0086 | $0.21 | $6.31 |
| **Fargate subtotal** | **1.0 vCPU + 2 GB total (Spot)** | **$0.0173** | **$0.41** | **$12.61** |
| Public IPv4 addresses | 3 in-use IPs ($0.005/hr each) | $0.015 | $0.36 | $10.95 |
| ALB | application, internet-facing | $0.0336 | $0.81 | $24.19 |
| RDS | db.t4g.micro, 20 GB gp2 | $0 (free tier) | $0 | $0 |
| Secrets Manager | 2 secrets | <$0.001 | $0.03 | $0.80 |
| ECR | ~1.5 GB images | <$0.001 | $0.01 | $0.15 |
| KMS | 2 AWS-managed keys (free) | $0 | $0 | $0 |
| Cloud Map | private DNS namespace + 3 services | <$0.001 | $0.01 | $0.30 |
| CloudWatch Logs | ~31 MB stored | $0 (5 GB free tier) | $0 | $0 |
| **TOTAL (design: all 3 running)** | | **~$0.066** | **~$1.58** | **~$48.00** |
| **TOTAL (actual: 2 running, auth broken)** | | **~$0.057** | **~$1.37** | **~$41.75** |

\* Auth is currently configured as Fargate On-Demand but broken (0 running tasks). Table shows Spot pricing for all 3 as the intended target state. See section 4 for Spot switch commands.

### Pricing source (eu-north-1, Linux/x86)

```
Fargate per vCPU-hr (On-Demand):  $0.04048
Fargate per GB-hr (On-Demand):    $0.004445
Fargate Spot discount:             ~65% off On-Demand
Public IPv4 per in-use IP-hr:      $0.005  (since Feb 1, 2024)
ALB per hour:                      $0.0264
ALB per LCU-hr:                    $0.0072 (min 1 LCU)
Secrets per secret/mo:             $0.40
```

### Timeline

| Resource | Created | Days Running | Cost Incurred |
|---|---|---|---|---|
| ECS services + tasks | 2026-07-21 | ~6 days (auth broken ~1 day) | ~$4.75 |
| ALB | 2026-07-26 (recreated) | ~1 day | ~$0.81 |
| RDS | 2026-07-18 | ~9 days | $0 (free tier) |
| Secrets Manager | 2026-07-21 | ~6 days | ~$0.16 |
| ECR (repos + images) | 2026-07-06 | ~21 days | ~$0.11 |
| KMS | alongside RDS | ~9 days | $0 (AWS-managed, free) |
| Public IPv4 addresses | 2026-07-21 (tasks created) | ~6 days (2 IPs × 6 days, 3 IPs × brief periods) | ~$0.50 |
| **Subtotal (calculated)** | | | **~$6.33** |
| **+ Deployment churn** (revisions, overlapping tasks during deploy) | | | **~$0.50** |
| **+ Any earlier test resources** (ECR repos created July 6, possible test tasks) | | | **~$1-2** |
| **GRAND TOTAL (estimated)** | | | **~$8-9** |

---

## 2. Cost Drivers — What Costs the Most

### 2.1 The ALB is the biggest cost, not Fargate

At Spot pricing, Fargate compute costs only ~$12.61/month. The **ALB alone costs $24.19/month** — nearly double. For a playground that gets zero traffic 95% of the time, this is the #1 waste.

### 2.2 Public IPv4 addresses — the hidden cost

Every Fargate task with `assignPublicIp=ENABLED` incurs a $0.005/hr charge per in-use public IPv4 address (since Feb 1, 2024). With 3 tasks: **~$10.95/month**. This is almost as much as all Fargate Spot compute combined.

### 2.3 Three separate tasks, each with minimum spec

Even idle, each task bills at its configured CPU/memory. Consolidating into fewer tasks or reducing specs would help, but the current split (3 separate Spring Boot apps) requires at least 256 MB each.

### 2.4 No NAT Gateway — correctly avoided

The deployment uses `assignPublicIp=ENABLED` on Fargate tasks, avoiding a NAT Gateway ($0.045/hr = ~$32/month). Even with the IPv4 charge ($10.95/month for 3 IPs), this is still **3x cheaper** than a NAT Gateway. Right call for a playground.

---

## 3. Strategies to Pause / Reduce Cost

Ranked from **least invasive** (0 code changes, 0 architectural changes) to more involved.

### Strategy A: Scale ECS Services to 0 (Least Invasive)

**What:** Set `desiredCount=0` on all 3 ECS services. Tasks stop. ALB, RDS, Secrets, KMS stay.

**Cost when paused:**
- ALB: $0.81/day ($24/month)
- RDS: $0 (free tier)
- Secrets + ECR + Cloud Map: ~$0.04/day ($1.25/month)
- **Total paused: ~$0.85/day ($25.44/month)**

**Commands:**

```bash
# Pause
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service onlineshop-auth --desired-count 0
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service onlineshop-items --desired-count 0
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service onlineshop-api-gateway --desired-count 0

# Resume (same commands with --desired-count 1)
```

**Pros:** 1 second to pause, 1 second + Spring Boot startup (~3 min) to resume. No infrastructure recreation.
**Cons:** ALB still costs $24/month. When RDS free tier expires (July 2027), RDS adds ~$15/month.

**Verdict:** Good for daily on/off. The ALB is a silent cost leak you won't notice until the bill arrives.

---

### Strategy B: Scale ECS to 0 + Remove ALB

**What:** Same as A, plus delete the ALB, target group, and listener. Recreate on resume.

**Cost when paused:**
- ALB: $0
- RDS: $0 (free tier)
- Secrets + ECR + Cloud Map: ~$0.04/day ($1.25/month)
- **Total paused: ~$0.04/day ($1.25/month)**

**Commands:**

```bash
# Pause — scale services to 0, then delete ALB infrastructure
for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  aws ecs update-service --profile dpm-profile --region eu-north-1 \
    --cluster onlineshop-cluster --service $svc --desired-count 0
done

# Delete listener
LISTENER_ARN=$(aws elbv2 describe-listeners --profile dpm-profile --region eu-north-1 \
  --load-balancer-arn $(aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 \
    --names onlineshop-alb --query 'LoadBalancers[0].LoadBalancerArn' --output text) \
  --query 'Listeners[0].ListenerArn' --output text)
aws elbv2 delete-listener --profile dpm-profile --region eu-north-1 --listener-arn $LISTENER_ARN

# Delete target group
aws elbv2 delete-target-group --profile dpm-profile --region eu-north-1 \
  --target-group-arn $(aws elbv2 describe-target-groups --profile dpm-profile --region eu-north-1 \
    --names onlineshop-gateway-tg --query 'TargetGroups[0].TargetGroupArn' --output text)

# Delete ALB  
aws elbv2 delete-load-balancer --profile dpm-profile --region eu-north-1 \
  --load-balancer-arn $(aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 \
    --names onlineshop-alb --query 'LoadBalancers[0].LoadBalancerArn' --output text)

# Resume — recreate ALB infrastructure (full commands in executed/INFO.md lines 688-851)
# 1. Create ALB, target group, listener
# 2. Update API Gateway service to register with new target group
# 3. Scale all services to desired-count 1
```

**Pros:** Cuts paused cost from ~$25/month to ~$1.25/month.  
**Cons:** ~5 minutes to recreate ALB infrastructure on resume. Need to wire the new target group ARN into the API Gateway service.

**Verdict:** Best for usage every few days. Worth creating a `pause.sh` and `resume.sh` script that handles this automatically.

---

### Strategy C: Full Freeze — Stop Everything (Most Savings)

**What:** Scale ECS to 0, delete ALB, stop RDS instance, keep only data-tier resources (Secrets, ECR — these cost $1.25/month combined).

**Additional step:** Stop the RDS instance (RDS auto-restarts after 7 days — AWS limitation on stopped instances).

**Cost when fully paused: ~$0.04/day ($1.25/month)**

```bash
# Pause RDS (add to pause script)
aws rds stop-db-instance --profile dpm-profile --region eu-north-1 \
  --db-instance-identifier onlineshop-postgres-db

# Resume — start RDS first (takes ~2-5 minutes)
aws rds start-db-instance --profile dpm-profile --region eu-north-1 \
  --db-instance-identifier onlineshop-postgres-db
```

**Pros:** Near-zero cost when idle.  
**Cons:** RDS start takes 2-5 min. RDS auto-restarts after 7 days (must re-stop weekly). More complex resume flow.

**Verdict:** Use when you won't touch the project for a week or more.

---

### Strategy D: Switch to Fargate Spot (Runtime Saving)

**What:** Change ECS services from `FARGATE` (On-Demand) to `FARGATE_SPOT`. This is an **independent optimization** — apply it alongside any pause strategy.

**Impact:** Cuts Fargate compute costs by ~65% while running. The $35.54/month On-Demand becomes ~$12.61/month Spot.

```bash
# Update each service's launch type to FARGATE_SPOT
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service onlineshop-auth \
  --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1

# Repeat for onlineshop-items and onlineshop-api-gateway
```

**⚠️ Caveat with Spot:** AWS can reclaim Spot capacity with a 2-minute warning. For a playground, this is fine — a task dying is an annoyance, not an outage. However:
- Spring Boot startup in ECS takes ~3 minutes (startPeriod=180). If Spot reclaim happens, you'll have a 3+ minute gap.
- If you need 100% uptime while demoing, this is not suitable (but neither is a playground).

**Verdict:** Combine with Strategy A for the best balance. Spot when running + scale to 0 when not.

---

### Strategy E: Scheduled On/Off (Automated)

**What:** Use EventBridge Scheduler to scale services to 0 at night and back to 1 in the morning. Only pay during active hours.

```
"Office hours" 08:00-22:00 → 14 active hours/day
Instead of 24 hours/day

Full-load monthly (Spot, all 3): $49.00/month
With 14hr/24hr active window:
  Active 14hr: Fargate $5.19 + IPv4 $4.50 + ALB $13.92 + Other $0.72 = $24.33
  Idle 10hr:   ALB $10.07 + Other $0.53 = $10.60
  Total (ALB stays 24/7): ~$34.93/month

With ALB deleted when off (14hr only):
  ALB + Fargate + IPv4 14hr: $5.30 + $5.19 + $4.50 = $15.00
  Other (secrets etc) 24/7:  $1.25
  Total: ~$16.25/month

Saves ~$14-33/month vs 24/7
```

**Verdict:** Best for long-term passive cost control. Build after Strategy D.

---

## 4. Recommended Approach

### Immediate (today — 0 effort)

```bash
# Items & API Gateway are already on Fargate Spot.
# Switch auth to Fargate Spot (and fix the broken image tag):
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service onlineshop-auth \
  --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1
```

This plus fixing the auth container image completes the Spot migration. All 3 services on Spot = ~$12.61/month Fargate (down from ~$35.54/month On-Demand).

### When not actively developing (Strategy A + B)

Create two scripts in the `plans/AUTOMATIC-BUILDS-AND-DEPLOY/scripts/` directory:

| Script | What it does | Cost when idle |
|---|---|---|
| `pause-playground.sh` | Scale ECS to 0, delete ALB infrastructure | $1.25/month |
| `resume-playground.sh` | Recreate ALB + target group + listener, scale ECS to 1, wire up API Gateway | Full cost when running |

Run `pause-playground.sh` before going to sleep / weekend. Run `resume-playground.sh` when starting development.

Staging follows the same on-demand cost discipline but does not retain database
state: `resume-staging.sh` creates and bootstraps empty RDS, while
`pause-staging.sh` deletes it without a final snapshot. Snapshot storage is
incurred only when a debugging/DR snapshot is explicitly requested.

### Later (Pass 4)

Add EventBridge scheduled scaling and a cost anomaly alarm (as planned in `04_OPERATIONAL_MATURITY.md`).

---

## 5. Cost Projections by Strategy

| Strategy | Fargate/mo | IPv4/mo | ALB/mo | Other/mo | Total/mo | Paused cost/mo |
|---|---|---|---|---|---|---|---|
| All On-Demand 24/7 (original deploy) | $35.54 | $10.95 | $24.19 | $1.25 | **$71.93** | N/A |
| All Spot 24/7 (current design) | $12.61 | $10.95 | $24.19 | $1.25 | **$49.00** | N/A |
| Spot + Scale to 0 (D+A) | varies | varies | $24.19 | $1.25 | varies | **$25.44** |
| Spot + Scale 0 + No ALB (D+B) | varies | varies | $0 | $1.25 | varies | **$1.25** |
| Spot + Scale 0 + No ALB + Stop RDS (D+C) | varies | varies | $0 | $1.25 | varies | **$1.25** |

**If you use the playground 8 hours/day, 5 days/week (160 hrs/month):**

| Strategy | Monthly Cost |
|---|---|
| All Spot 24/7 | $49.00 |
| Spot + Scale to 0 daily (D+A) | $10.74 (Fargate 160hr) + $8.66 (IPv4 160hr) + $24.19 (ALB 24/7) + $1.25 = **$44.84** |
| Spot + Delete ALB daily (D+B) | $10.74 (Fargate 160hr) + $8.66 (IPv4 160hr) + $5.30 (ALB 160hr) + $1.25 = **$25.95** |
| Spot + Full freeze weekly (D+C) | $10.74 + $8.66 + $5.30 + $1.25 = **~$25.95** |

**Key insight:** The ALB costs more than Fargate Spot compute. Pausing tasks alone saves little if the ALB stays running. Delete the ALB when idle to make a real dent.

---

## 6. Quick Wins Checklist

- [x] **Switch to Fargate Spot** — done for items + gateway. Auth needs Spot switch + image fix.
- [ ] **Fix auth: push missing image tag to ECR** — auth task stuck on `CannotPullContainerError` since ~July 27.
- [x] **Create `scripts/pause-playground.sh` and `scripts/resume-playground.sh`** — manual on/off when not coding
- [ ] **Add billing alert at $10/month** — AWS Budgets, free, catches cost surprises
- [ ] **Be aware of public IPv4 charge** — $0.005/hr per IP ($10.95/month for 3 tasks). Scales to 0 with tasks — no charge when paused.
- [ ] **Consider consolidating API Gateway + Auth into one service** — reduces Fargate task count from 3 to 2, saves ~$3.15/month Spot + $3.65/month IPv4 = ~$6.80/month (architectural change, evaluate in Pass 2)
- [ ] **Delete the S3 bucket `dpm.test-bucket`** if unused — it has no objects but bucket itself is free
