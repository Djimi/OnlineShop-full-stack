# Automatic Builds & Deploy — PLAN

## Strategy: Iterative, Fast Feedback

This plan is split into **4 passes**, each building on the previous one. The guiding principle is **deploy to AWS as fast as possible**, then harden and polish incrementally.

| Pass | Name | What You Get |
|------|------|--------------|
| 1 | **MVP: Running on AWS** | All services live on AWS. Manual deploys. Proves the system works in the cloud. |
| 2 | **CI Pipeline Hardening & Staging** | Automated CI on push/PR. Test gates. Selective builds. Branch protection. Staging environment. |
| 3 | **Release, Traceability & Promotion** | Official release model. Staging → production promotion with approval. Rollback. Full traceability. |
| 4 | **Operational Maturity** | Notifications. Dashboards. Runbooks. Nightly validation. Merge queue. Cost guardrails. |

After Pass 1 you have a **working deployment**. After Pass 4 you satisfy **every v1 requirement**.

---

## Subplans

1. [01_MVP_DEPLOY.md](./01_MVP_DEPLOY.md) — AWS account, ECR, minimal GH Actions, ECS Fargate, S3+CloudFront, databases
2. [02_CI_PIPELINE_HARDENING.md](./02_CI_PIPELINE_HARDENING.md) — Branch protection, selective builds, test gates, Docker tagging, caching, staging
3. [03_RELEASE_TRACEABILITY.md](./03_RELEASE_TRACEABILITY.md) — Release identity, promotion flow, production env, rollback, traceability chain, ECR retention
4. [04_OPERATIONAL_MATURITY.md](./04_OPERATIONAL_MATURITY.md) — Notifications, dashboards, audit, merge queue, nightly builds, runbooks, cost monitoring
5. [05_FUTURE_IMPROVEMENTS.md](./05_FUTURE_IMPROVEMENTS.md) — Non-mandatory improvements for later (Dependabot, etc.)

---

## Requirements Source

All requirements come from these three documents in this directory:

- [01_REQUIREMENTS_BUILD.md](./01_REQUIREMENTS_BUILD.md) — CI, test gating, versioning, tagging, traceability, retention
- [02_REQUIREMENTS_DEPLOY.md](./02_REQUIREMENTS_DEPLOY.md) — Staging/production, approvals, rollback, notifications
- [03_REQUIREMENTS_HOSTING.md](./03_REQUIREMENTS_HOSTING.md) — Frontend hosting, domains, HTTPS, dashboards

---

## Cost Trajectory

**Note:** The original Pass 1 estimate (~$2-4) was overly optimistic — it assumed Fargate Spot pricing but did not account for the ALB ($24.19/month) or the minimum baseline cost of Secrets + ECR + Cloud Map (~$1.25/month; KMS keys are AWS-managed = free). After switching to Spot on 2026-07-25, the real costs are:

| After Pass | Estimated Monthly Cost | Notes |
|---|---|---|
| 1 — MVP (running 24/7 Spot) | ~$17–42 | Spot + ALB 24/7 = $49.00; Spot + ALB daily pause = ~$17 |
| 2 — + Staging | ~$20–45 | Staging adds duplicate infra when active |
| 3 — + Production + Release infra | ~$22–47 | Production adds ALB + extra tasks when active |
| 4 — + Monitoring/notifications | ~$22–47 | No incremental AWS cost |

The original $5/month ceiling required both Spot AND pausing the ALB when idle. See [COST-EXPLANATION.md](./explanations/COST-EXPLANATION.md) for detailed analysis.

Cost control strategies:
- Fargate Spot pricing (switched 2026-07-25 — saves ~60% on compute)
- Pause scripts: `pause-playground.sh` / `resume-playground.sh` (cuts idle cost to ~$1.25/month)
- Making staging on-demand (scale to 0 or tear down when idle)
- Leveraging RDS Free Tier (12 months, expires July 2027)
- Using GitHub Actions free tier (2000 min/month)

---

## Key Decisions to Make During Implementation

| Decision | When | Options |
|---|---|---|
| AWS region | Pass 1 | `eu-north-1` (Stockholm) vs `eu-central-1` (Frankfurt) — pick cheapest |
| Database: RDS vs containerized PG | Pass 1 | RDS Free Tier preferred; containerized PG if post-Free-Tier cost is a concern |
| Routing: ALB vs Service Connect | Pass 1 | ALB is simpler; Service Connect is cheaper — evaluate during implementation |
| Staging lifecycle model | Pass 2 | Scale-to-zero vs on-demand teardown |
| Release label strategy | Pass 3 | Auto-generated (semantic-release) vs manually assigned during promotion |

---

## Execution Traceability

Every step executed in this plan **MUST** update [`executed/INFO.md`](./executed/INFO.md) with:
- Every AWS resource created (ARNs, IDs, security groups, policies, secrets)
- Every command run (with full parameters)
- Every configuration change (files, env vars, overrides)
- Every issue encountered and its resolution
- Every credential/secret placeholder (never the actual secret value)

**Purpose:** When the entire plan is executed, `INFO.md` must contain everything needed to replicate the environment from scratch — pipelines, infrastructure, databases, networking, and all. No tribal knowledge, no forgotten steps.

---

## Progress

- [ ] **Pass 1** — MVP: Running on AWS (IN PROGRESS — ECS + RDS + CI/CD done, ALB paused, frontend not deployed)
- [ ] **Pass 2** — CI Pipeline Hardening & Staging (basic caching from Pass 1, nothing else started)
- [ ] **Pass 3** — Release, Traceability & Promotion
- [ ] **Pass 4** — Operational Maturity
- [ ] **Pass 5** — Future Improvements (non-mandatory)
