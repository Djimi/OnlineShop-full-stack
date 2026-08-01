# Cost Optimization Research Report

> **Date:** 2026-07-27  
> **Scope:** OnlineShop playground microservices project on AWS eu-north-1  
> **Current state:** Paused (desiredCount=0, ALB deleted), at ~$1.25/month  
> **Running cost:** ~$49.00/month (Fargate Spot + IPv4 + ALB), ~$24.76 without ALB

---

## 1. Additional Cost Optimizations Beyond What's Documented

### 1.1 Fargate ARM (Graviton) Instead of x86

The current task definitions use Linux/x86. Switching to ARM64 (Graviton):

| vCPU-hr (Spot) | x86 | ARM | Savings |
|---|---|---|---|
| eu-north-1 | $0.01417 | $0.01134 | **20%** |
| GB-hr (Spot) | $0.001556 | $0.001245 | **20%** |

| Service | Current x86/mo | ARM/mo | Savings |
|---|---|---|---|
| Auth (0.25vCPU/512MB) | $3.15 | $2.52 | $0.63 |
| Items (0.25vCPU/512MB) | $3.15 | $2.52 | $0.63 |
| API Gateway (0.5vCPU/1024MB) | $6.31 | $5.05 | $1.26 |
| **Total Fargate** | **$12.61** | **$10.09** | **$2.52** |

**Requirement:** Docker images must be built for `linux/arm64`. Spring Boot + Java 25 on ARM is fully supported. The GitHub Actions runner is x86, so we'd need `docker buildx` with QEMU emulation or multi-platform builds (adds ~2 min to build time).

**Verdict:** Free 20% savings, one-time build config change. Do this.

### 1.2 Replace Secrets Manager with SSM Parameter Store (SecureString)

| Cost item | Current | Alternative |
|---|---|---|
| Secrets Manager (2 secrets) | $0.80/month | — |
| SSM Parameter Store SecureString (2 params) | $0/month | Standard tier, secure strings free |

**Caveat:** Secrets Manager has automatic rotation (unused here). Parameter Store SecureString has a 4 KB value limit (fine for DB creds). You lose the `:password::` suffix auto-resolution; ECS task definitions reference the full secret ARN path directly.

**Savings:** $0.80/month (67% of paused cost)

### 1.3 ECR Lifecycle Policy for Image Retention

Currently all images stay forever. Add a lifecycle policy to each repo:

```json
{
  "rules": [{
    "rulePriority": 1,
    "description": "Keep last 5 images, expire rest",
    "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 5},
    "action": {"type": "expire"}
  }]
}
```

Savings: negligible (~$0.02/month) but prevents future accumulation. Each Spring Boot image is ~150-200 MB. At 50 images across 3 repos: ~7.5 GB = ~$0.75/month if unchecked.

### 1.4 CloudWatch Logs Retention Policy

Currently logs accumulate forever (5 GB free tier). Add retention:

```bash
aws logs put-retention-policy --log-group-name /ecs/onlineshop-auth --retention-in-days 7
aws logs put-retention-policy --log-group-name /ecs/onlineshop-items --retention-in-days 7
aws logs put-retention-policy --log-group-name /ecs/onlineshop-api-gateway --retention-in-days 7
```

Prevents hitting the 5 GB free tier limit (currently at ~31 MB — not urgent but good hygiene).

### 1.5 Co-locate Services Into One Fargate Task (Multi-Container)

Currently 3 separate Fargate tasks = 3× IPv4 charge + 3× task overhead. With ECS multi-container task definition:

| Approach | Tasks | Fargate Cost/mo | IPv4 Cost/mo |
|---|---|---|---|
| Current (3 tasks) | 3 | $12.61 | $10.95 |
| Co-located (1 task, 4 containers) | 1 | $10.09 (ARM) | $3.65 |

Single task with 4 containers: auth + items + api-gateway + redis-sidecar, 1 vCPU / 2 GB ARM Spot:
- Fargate: $0.02834/hr × 730 hr = ~$10.34/month (Spot)
- IPv4: $0.005/hr × 730 = $3.65 (1 IP vs 3)
- **Total compute+IPv4: ~$13.99/month** (vs current $23.56)

**Savings:** ~$9.57/month (41% reduction in compute + IPv4)

**Trade-offs:**
- All services share fate — if the API gateway crashes, auth and items go down too
- Scaling is all-or-nothing (OK for playground)
- Slightly more complex task definition
- Service Connect no longer needed for inter-service communication (they share localhost)
- **This eliminates Cloud Map namespace cost** (~$0.30/month)

### 1.6 Use API Gateway HTTP API Instead of ALB

ALB is the single biggest cost at $24.19/month. HTTP API (API Gateway v2) pricing:

| Pricing dimension | Cost |
|---|---|
| Per million requests | $1.00 |
| Per hour | $0 (no hourly charge!) |
| Data transfer | Standard AWS rates |

For a playground with near-zero traffic: **$0/month for the gateway layer**.

Architecture: HTTP API → direct integration to ECS Service Connect → private DNS `api-gateway.onlineshop.local` (or VPC Link).

Actually simpler: the co-located single task approach (1.5) only needs a way to expose port 10000. Options:
- **HTTP API + VPC Link**: HTTP API → VPC Link → NLB → ECS task. NLB costs ~$23/month — defeats the purpose.
- **Just expose the task directly via public IP** (assignPublicIp=ENABLED, restrict SG to your home IP): $0 extra cost but no domain/TLS. Good enough for a playground.
- **HTTP API with private integration to ALB**: Requires ALB anyway.
- **CloudFront → S3 (frontend) + direct ECS IP (API)**: Frontend in S3+CloudFront, API calls go to hardcoded task public IP. $0 gateway cost.

**Best option for playground:** If frontend is deployed to S3+CloudFront (within free tier), the frontend can call the API gateway's public IP directly. This eliminates the ALB entirely, even when running.

Savings: $24.19/month vs current ALB approach.

### 1.7 Compute Savings Plans — Not Worth It Yet

| Term | Discount | Monthly commit | Annual cost |
|---|---|---|---|
| 1-year, no upfront | ~23% | $10/month | $120 |
| Pay-as-you-go Spot | 0% | — | $12.61/month = $151/year |

At $12.61/month Fargate, a 1-year Savings Plan saves ~$2.90/month = ~$35/year. But you're locked in. Not worth it for a playground that may change architecture.

### 1.8 RDS Post-Free-Tier Strategy (July 2027)

After RDS free tier expires:
- db.t4g.micro: $0.016/hr + 20 GB gp2 (~$2.30) = ~**$14/month**
- RDS Reserved Instance (1-year): ~$9/month (saves ~$5/month)
- **Containerized PostgreSQL as ECS sidecar**: $0 additional compute (runs in same task). Data persists on EBS... but Fargate tasks are ephemeral. Would need EFS ($0.30/GB/month + $3.00 for 100 GB infrequent access) or S3 backups.
- EFS for DB data: 1 GB = $0.30/month for standard, $0.025/GB for infrequent access. A 1 GB DB on EFS IA = $0.33/month.
- **Containerized PG + EFS: ~$0.33/month vs $14/month for RDS**

**Recommendation:** Before July 2027, migrate to containerized PostgreSQL on the same ECS task with EFS persistence. This is ~42× cheaper than RDS.

---

## 2. Absolute Minimum Cost — Theoretical Floor

### Optimized Running Architecture (24/7)

| Resource | Spec | Cost/mo |
|---|---|---|
| Single ECS Fargate Spot task (ARM) | 1 vCPU / 2 GB, 4 containers | $10.34 |
| Public IPv4 (single IP) | 1 in-use IP | $3.65 |
| EFS (DB data) | 1 GB standard | $0.30 |
| ECR (3 repos + lifecycle policy) | ~500 MB images | $0.10 |
| SSM Parameter Store (2 params) | SecureString | $0 |
| **TOTAL (running 24/7, no ALB)** | | **$14.39** |

### With Scheduled Pause (8 hours/day, 5 days/week = 160 hrs/month)

| Resource | 160 hrs active | 570 hrs idle |
|---|---|---|
| Fargate Spot (ARM) | $2.27 | $0 |
| IPv4 | $0.80 | $0 |
| EFS | $0.07 | $0.23 |
| ECR | $0.02 | $0.08 |
| **TOTAL** | | **$3.47** |

### Optimized Paused State (no services running)

| Resource | Cost/mo |
|---|---|
| ECR (3 repos) | $0.10 |
| EFS (persistent) | $0.30 |
| **TOTAL (paused)** | **$0.40** |

**Bottom line:** With all optimizations, the theoretical floor is ~$14.39/month running 24/7, ~$3.47/month with 8h×5d scheduling, and $0.40/month fully paused.

---

## 3. AWS Region Pricing Comparison (eu-north-1 vs eu-central-1 vs eu-west-1)

All prices as of 2026-07-27 for Fargate On-Demand (Linux/x86):

| Resource | eu-north-1 (Stockholm) | eu-central-1 (Frankfurt) | eu-west-1 (Ireland) |
|---|---|---|---|
| **Fargate vCPU-hr** | $0.04048 | $0.04656 | $0.04656 |
| **Fargate GB-hr** | $0.004445 | $0.00511175 | $0.00511175 |
| **ALB per hour** | $0.0264 | $0.0288 | $0.0270 |
| **ALB per LCU-hr** | $0.0072 | $0.0072 | $0.0072 |
| **RDS db.t4g.micro/hr** | $0.016 | $0.016 | $0.016 |
| **Public IPv4/hr** | $0.005 | $0.005 | $0.005 |
| **Secrets Manager/mo** | $0.40 | $0.40 | $0.40 |
| **ECR per GB/mo** | $0.10 | $0.10 | $0.10 |
| **Data transfer out (first 100 GB)** | $0.09/GB | $0.09/GB | $0.09/GB |

### Monthly Cost Comparison (current architecture, all 3 services, Spot 24/7)

| Cost Item | eu-north-1 | eu-central-1 | eu-west-1 |
|---|---|---|---|
| Fargate Spot (3 tasks) | $12.61 | $14.52 | $14.52 |
| Public IPv4 (3 IPs) | $10.95 | $10.95 | $10.95 |
| ALB | $24.19 | $26.28 | $24.96 |
| RDS (free tier) | $0 | $0 | $0 |
| Other (secrets, ECR, etc.) | $1.25 | $1.25 | $1.25 |
| **TOTAL** | **$49.00** | **$53.00** | **$51.68** |

**eu-north-1 is ~8% cheaper than eu-central-1 and ~5% cheaper than eu-west-1.** Stockholm is indeed the cheapest EU region for this workload. The Fargate compute discount in Stockholm is the primary driver (~13% on compute). The ALB is also slightly cheaper.

**Note:** Latency from central Europe to Stockholm is ~20-30ms. From Ireland: ~40-50ms. For a playground, this is irrelevant.

---

## 4. Alternatives to Fargate — Cost Comparison

### 4.1 Single EC2 Instance with Docker Compose (Spot)

| Instance | vCPU | RAM | On-Demand/hr | Spot/hr (~65% off) | Monthly Spot |
|---|---|---|---|---|---|
| t4g.nano | 2 | 0.5 GB | $0.0048 | $0.00168 | **$1.22** |
| t4g.micro | 2 | 1 GB | $0.0096 | $0.00336 | **$2.45** |
| t4g.small | 2 | 2 GB | $0.0192 | $0.00672 | **$4.90** |
| t4a.nano (AMD) | 2 | 0.5 GB | $0.0045 | $0.00158 | **$1.15** |
| t4a.micro (AMD) | 2 | 1 GB | $0.0090 | $0.00315 | **$2.30** |
| t4a.small (AMD) | 2 | 2 GB | $0.0180 | $0.00630 | **$4.60** |

**Memory analysis:** 3 Spring Boot apps need ~200-300 MB each at idle. PostgreSQL needs ~50 MB. Redis ~50 MB. Total minimum RAM: ~750 MB. **t4g.micro (1 GB) is borderline** — possible with aggressive JVM tuning (`-Xmx220m` each) but risky. **t4g.small (2 GB) is safe.**

| Resource | t4g.small Spot | t4a.small Spot |
|---|---|---|
| EC2 instance | $4.90 | $4.60 |
| 20 GB gp2 EBS | $2.20 | $2.20 |
| Public IPv4 (attached to instance) | $3.65 | $3.65 |
| Elastic IP (free, attached to running instance) | $0 | $0 |
| **TOTAL (running 24/7)** | **$10.75** | **$10.45** |

Docker Compose config: auth, items, api-gateway, postgres, redis all run as containers on one instance. One public IP, exposed port 10000 (or 80 via nginx). No ALB needed. No Cloud Map. No Service Connect.

**Savings vs current Fargate Spot 24/7:** $49.00 − $10.75 = **$38.25/month (78% reduction)**
**Savings vs current Fargate Spot without ALB:** $24.76 − $10.75 = **$14.01/month (57% reduction)**

**Trade-offs:**
- EC2 Spot can be reclaimed with 2-min notice → instance terminated → 5-10 min downtime for restart
- You manage the OS (security patches, Docker updates)
- No auto-scaling (irrelevant for playground)
- No managed service discovery (Docker Compose network handles DNS)
- EBS persists data across restarts (unlike Fargate ephemeral storage)
- RDS free tier is lost (or keep RDS separately for managed DB)

**Verdict:** The most cost-effective option for a playground running 24/7. Combine with RDS (free tier until July 2027) for a hybrid: EC2 for apps + RDS for DB.

### 4.2 AWS App Runner

| Service | Config | Monthly Cost |
|---|---|---|
| Auth | 0.5 GB / 0.25 vCPU | ~$12.65 |
| Items | 0.5 GB / 0.25 vCPU | ~$12.65 |
| API Gateway | 1 GB / 0.5 vCPU | ~$25.30 |
| **TOTAL (3 services)** | | **~$50.60** |

App Runner includes a built-in load balancer (no ALB cost), but minimum per-service charge is high. **2× more expensive than current Fargate Spot + ALB combined.** Not suitable for this use case.

### 4.3 AWS Lambda (Spring Boot)

- Spring Boot 4.x + Java 25 on Lambda: Possible via `aws-serverless-java-container-springboot` or Spring Cloud Function
- **Cold start:** 5-10 seconds for Spring Boot context initialization
- **SnapStart:** Available for Java 11/17/21 (not yet for 25). SnapStart caches the initialized Firecracker microVM — reduces cold start to ~500ms
- **Cost with 0 traffic:** $0/month (free tier: 1M requests, 400K GB-seconds)
- **Cost with demo traffic:** ~$0.01/month

| Approach | Complexity | Cold Start | Monthly Cost |
|---|---|---|---|
| Spring Boot on Lambda | High (major refactor) | 5-10s (unusable) | ~$0 |
| Spring Boot + SnapStart | High | ~500ms | ~$0 |
| Spring Cloud Function | Medium | ~2-3s | ~$0 |

**Verdict:** Technically possible and cheapest ($0 with no traffic). But requires a major architectural refactor — each endpoint becomes a Lambda function or a single Lambda with Spring Boot as a fat function (cold starts kill UX). **Not practical for this project without a complete rewrite.** The Spring Boot apps would need to be split into function-per-endpoint Lambdas, which defeats the learning purpose.

### 4.4 ECS on EC2 (Single Instance)

Use a single EC2 instance as the ECS cluster host (EC2 launch type):

| Resource | Cost/mo |
|---|---|
| t4g.small EC2 Spot | $4.90 |
| 20 GB gp2 EBS | $2.20 |
| Public IPv4 (attached to instance) | $3.65 |
| **TOTAL (no ALB)** | **$10.75** |

Same as Docker Compose in cost, but with ECS orchestration (task definitions, health checks, CloudWatch logging preserved). You get ECS benefits without Fargate premium.

**Verdict:** Extremely competitive. Same cost as Docker Compose but retains ECS/CI-CD integration.

### 4.5 Summary: All Alternatives vs Current

| Approach | Running 24/7 | Paused | Spin-up Time | Complexity |
|---|---|---|---|---|
| **Current: Fargate Spot + ALB** | $49.00 | $1.25 | 5-7 min | Medium |
| **Fargate Spot, no ALB** | $24.76 | $1.25 | 3-5 min | Medium |
| **Fargate Spot ARM + co-located** | $13.99 | $0.40 | 3-5 min | Low-Medium |
| **EC2 Spot + Docker Compose** | $10.45 | $2.50 (EBS) | 2-3 min | Low |
| **EC2 Spot + ECS on EC2** | $10.75 | $2.50 (EBS) | 2-3 min | Medium |
| **Lambda (theoretical)** | ~$0 | ~$0 | 0 sec | Very High |

---

## 5. Non-AWS Alternatives (For Reference)

### 5.1 Hetzner VPS (Helsinki/Nuremberg — EU-based)

| Plan | Spec | Price |
|---|---|---|
| **CX22** | 2 vCPU, 4 GB RAM, 40 GB SSD | **€3.99/mo** (~$4.35) |
| CX32 | 4 vCPU, 8 GB RAM, 80 GB SSD | €7.99/mo (~$8.70) |
| CPX11 (AMD) | 2 vCPU, 2 GB RAM, 40 GB SSD | €3.29/mo (~$3.58) |

Each includes: IPv4 + IPv6, 20 TB traffic, DDoS protection. Docker Compose with all 3 apps + PostgreSQL + Redis easily fits on CX22.

**Total: $4.35/month all-in, 24/7. No multiplexer costs. No pause/resume scripts. No IPv4 surcharges. No ALB.**

### 5.2 Oracle Cloud Free Tier (Always Free)

Always-free resources:
- 2 AMD VM (VM.Standard.E2.1.Micro): 1/8 OCPU, 1 GB RAM each
- Up to 4 ARM cores (Ampere A1): 24 GB RAM total (e.g., 1×4-core/24GB)
- 200 GB block storage (boot volumes)
- 10 TB outbound data transfer/month
- Autonomous Database (free tier): 20 GB, or run PostgreSQL as container

**Total: $0/month forever.** All 3 Spring Boot apps + PostgreSQL + Redis fit comfortably on a single 4-core ARM instance with 24 GB RAM.

**Caveats:** Oracle may reclaim idle resources. Complex signup. The ARM allocation can be hard to get (high demand in some regions). The "always free" is genuine but Oracle's reputation for killing free tenants exists.

### 5.3 Railway

- $5/month Hobby Plan: 8 GB RAM, 8 vCPU, unlimited services
- PostgreSQL included (512 MB RAM, 1 GB storage) — free with plan
- GitHub integration, auto-deploy
- TLS + domain included

**Total: $5/month.** You'd deploy each service as a separate Railway service. No Docker registry needed (builds from Dockerfile). No load balancer config.

**Limitation:** 8 GB shared RAM across all services. Spring Boot on 3 services might be tight at higher load but fine for playground.

### 5.4 Render

- Web Service: Free tier (512 MB, 1 vCPU) — **spins down after 15 min idle, cold starts**
- Paid: $7/service/month (512 MB) or $25/service/month (1 GB)
- PostgreSQL: Free tier (256 MB, 1 GB) for 90 days, then $7/month
- 3 services at $7 = $21/month + DB $7 = $28/month

**Not competitive for 3 Spring Boot services.**

### 5.5 Fly.io

| Resource | Cost |
|---|---|
| shared-cpu-1x (1 vCPU, 256 MB) | Free (up to 3 VMs) |
| shared-cpu-1x (1 vCPU, 512 MB) | $1.94/VM/month |
| shared-cpu-1x (1 vCPU, 1 GB) | $3.87/VM/month |
| Fly Postgres (256 MB) | $1.94/month |

Spring Boot needs at least 512 MB. 3 services × $1.94 = $5.82/month + PostgreSQL $1.94 = **$7.76/month**.

**Note:** Fly.io bills per-resource, not flat-rate. So 3 separate apps with their own VMs. Can also deploy as a single multi-process app (Docker Compose unsupported, but you can use supervisord).

### 5.6 GitHub Codespaces

- 4-core / 8 GB: $0.36/hr
- Auto-shuts down after 30 min idle
- For 160 hours/month: $57.60
- Not designed for hosting — no static IP, no domain, ports need forwarding
- **Not suitable for hosting. At all.**

### 5.7 Summary: Non-AWS Cost Comparison

| Platform | Monthly Cost (running 24/7) | Cold Start | Infrastructure Management |
|---|---|---|---|
| **Oracle Cloud Free Tier** | **$0.00** | No | Medium (Linux VM admin) |
| **Hetzner CX22** | **$4.35** | No | Medium (Linux VM admin) |
| **Railway** | **$5.00** | No | Minimal |
| **Fly.io** | **$7.76** | No | Minimal |
| **Render** | **$28.00** | Yes (free tier) | Minimal |
| **Current AWS (Fargate Spot + ALB)** | **$49.00** | No | Medium |
| **Optimized AWS (EC2 Spot)** | **$10.45** | No (risk of Spot reclaim) | Medium |
| **Optimized AWS (Fargate co-located)** | **$13.99** | No (risk of Spot reclaim) | Low-Medium |

---

## 6. Recommended Cost Ceiling Strategy

### Goal: Keep alive in a state that can be spun up in 5-10 minutes for demo/learning sessions.

### Recommended: Enhanced Strategy B with Optimizations

**Phase 1 — Implement now (no architectural changes):**

| Action | Paused Cost Impact | Running Cost Impact | Effort |
|---|---|---|---|
| Switch Fargate to ARM64 | $0 | −$2.52/mo | 1 hour |
| Add ECR lifecycle policies | −$0.02/mo | −$0.02/mo | 5 min |
| Add CloudWatch retention (7 days) | $0 | $0 (prevents future cost) | 5 min |
| Replace Secrets Manager with Parameter Store | −$0.80/mo | −$0.80/mo | 30 min |

**New paused cost: $0.43/month** (ECR $0.13 + Cloud Map $0.30)  
**New running cost (Spot ARM, no ALB): $21.38/month**  
**New running cost (Spot ARM + ALB): $45.58/month**

**Phase 2 — Co-locate services into 1 Fargate task (architectural change, Pass 3+):**

| Action | Cost Impact |
|---|---|
| Merge 3 tasks into 1 multi-container task (ARM) | −$3.06/mo (Fargate) + −$7.30/mo (IPv4) = **−$10.36/mo** |
| Drop Cloud Map (localhost inter-service) | −$0.30/mo |

**New paused cost: $0.13/month** (ECR only)  
**New running cost (1 Fargate Spot ARM task, no ALB): $13.99/month**

**Phase 3 — Evaluate EC2 Spot migration (before RDS free tier expires, July 2027):**

| Action | Cost Impact |
|---|---|
| Migrate to EC2 t4g.small Spot + Docker Compose | −$3.24/mo vs Fargate ARM co-located |
| Move PostgreSQL to container on EC2 (post-free-tier) | −$14/mo (vs RDS) |

**New running cost (EC2 Spot, all-in-one): $10.45/month**  
**Paused cost: $2.50/month** (EBS volume persists)

### Recommended Execution Plan

```
Now:           Phase 1            ─── Paused $0.43 | Running $21.38 (no ALB)
Pass 3:        Phase 2            ─── Paused $0.13 | Running $13.99 (no ALB)  
July 2027:     Phase 3 (if needed) ── Running $10.45 (EC2, all-in-one)
```

### Cost Ceiling Comparison

| Strategy | Paused/mo | Running 8h×5d/mo | Spin-up Time | Which Script |
|---|---|---|---|---|
| **Current (Strategy B)** | **$1.25** | **$25.95** | 5-7 min | pause/resume-playground.sh |
| **Phase 1 (ARM + ParamStore)** | **$0.43** | **$9.50** | 5-7 min | Same scripts, updated |
| **Phase 2 (Co-located)** | **$0.13** | **$4.20** | 5-7 min | Updated scripts |
| **Phase 3 (EC2 Spot)** | **$2.50** | **$4.50** | 2-3 min | Docker Compose up/down |

### Key Insight

**Phase 1 gives you 66% cost reduction ($1.25 → $0.43 paused, $49 → $21.38 running without ALB) with zero architectural changes.** Just swap CPU arch, swap Secret storage, and add lifecycle policies. One afternoon of work.

**Phase 2 is the sweet spot:** $0.13/month paused ($1.56/year!) and $13.99/month running without ALB. Spin-up in 5-7 minutes. This is probably where the playground should settle.

**Phase 3 (EC2) is only worth it if you're running 24/7. For part-time use (8h×5d), the difference between Phase 2 and Phase 3 is ~$0.30/month running and ~$2.37/month more when paused (EBS is not free). Phase 2 is actually cheaper for part-time use.**

---

## Appendix A: Quick Wins Summary (Ranked by ROI)

| # | Optimization | Savings/mo | Effort | Priority |
|---|---|---|---|---|
| 1 | Delete ALB when idle (already done — Strategy B) | $24.19 | Done | — |
| 2 | Switch to Fargate ARM64 | $2.52 | 1 hour, 0 risk | ⭐⭐⭐ |
| 3 | Replace Secrets Manager → SSM Parameter Store | $0.80 | 30 min, low risk | ⭐⭐⭐ |
| 4 | Co-locate 3 services into 1 Fargate task | $10.36 | 3-4 hours, needs testing | ⭐⭐ |
| 5 | ECR lifecycle policies | $0.02 | 5 min, 0 risk | ⭐ |
| 6 | CloudWatch log retention | $0 (preventive) | 5 min, 0 risk | ⭐ |
| 7 | Migrate to EC2 Spot + Docker Compose | $3.24 (vs Phase 2) | Full day | ⭐ (later) |
| 8 | Containerized PG instead of RDS (post-free-tier) | $14.00 | 2-3 hours | ⭐ (July 2027) |

## Appendix B: Total Cost Trajectory

| Milestone | Running 24/7 | Running 8h×5d/mo | Paused | RDS |
|---|---|---|---|---|
| **Original deploy (On-Demand)** | $71.93 | — | $25.44 | Free tier |
| **Current (Fargate Spot)** | $49.00 | $25.95 | $1.25 | Free tier |
| **+ Phase 1 (ARM + ParamStore)** | $45.58 | $19.25 | $0.43 | Free tier |
| **+ Phase 2 (Co-located)** | $35.22 | $17.50 | $0.13 | Free tier |
| **+ Phase 2, no ALB** | $13.99 | $4.20 | $0.13 | Free tier |
| **+ Phase 3 (EC2 Spot, no RDS)** | $10.45 | $4.50 | $2.50 | Containerized |
| **Oracle Cloud Free Tier** | $0.00 | $0.00 | $0.00 | Containerized |
| **Hetzner CX22** | $4.35 | $4.35 | $4.35 | Containerized |

**The most cost-effective practical option on AWS is Phase 2 without ALB: $0.13/month paused, ~$5/month with typical usage.** The absolute minimum is Oracle Cloud at $0, but AWS stays in your existing skill ecosystem.
