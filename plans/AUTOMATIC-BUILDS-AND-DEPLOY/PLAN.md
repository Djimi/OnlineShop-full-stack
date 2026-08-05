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
4. [04_OPERATIONAL_MATURITY.md](./04_OPERATIONAL_MATURITY.md) — Notifications, dashboards, audit, merge queue, nightly builds, runbooks, cost monitoring, PR/branch-protection policy review
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

## Cross-Plan Maintenance Contract

> **Every plan** that touches deployment or infrastructure (e.g., `plans/DDDItemsImprovement/PLAN.md`) **MUST** update these files whenever making changes that affect runtime behavior:

| File | When to Update |
|------|---------------|
| [`WHAT-WAS-DONE.md`](./WHAT-WAS-DONE.md) | Any infrastructure change, deployment fix, configuration update, or code change that affects runtime |
| [`scripts/config/production.env`](../../scripts/config/production.env) | If production identifiers, services, ports, or security groups change |
| [`scripts/config/staging.env`](../../scripts/config/staging.env) | If staging identifiers, services, database, ports, or security groups change |
| [`scripts/lib/lifecycle.sh`](../../scripts/lib/lifecycle.sh) | If shared ALB, ECS, RDS, waiter, readiness, or verification behavior changes |

**Why:** These files are the source of truth for automation. Drift = broken automation = manual work.

---

## Progress

- [x] **Pass 1** — MVP: Running on AWS (DONE — ECS + RDS + CI/CD + S3 + CloudFront + frontend deployed; ALB active during verification, now paused)
- [x] **Pass 2** — CI Pipeline Hardening & Staging (DONE — workflow with change detection + test gates + selective builds + Docker tagging; staging infra provisioned + smoke tested; branch protection applied via `gh api`)
- [ ] **Pass 3** — Release, Traceability & Promotion
  - [x] 3.1 Release contract and local validation foundation
  - [x] 3.2 Candidate build evidence and immutable artifacts (offline implementation + gate green in `tests/scripts/candidate_evidence_test.sh`; live ECR/GitHub/Syft verification deferred to the consolidated pass)
  - [x] 3.3 ECR release tagging, immutability, and least privilege (offline implementation + gate green in `tests/scripts/ecr_release_tagging_test.sh`; live ECR settings read-back, real put-image behavior, OIDC environment subject, and IAM Access Analyzer deferred to the consolidated pass)
  - [x] 3.4 Controlled staging-to-production promotion workflow (offline implementation + gate green in `tests/scripts/promotion_test.sh`: the `release_contract.promotion` decision layer — dispatch/run/ancestry/preflight/snapshot/plan/waiter/frontend/verify/finalize/compensate; `promote-release.yml` static checks incl. attempt-pinned candidate-evidence consumption and `approvedBy` from `actions/runs/{run}/approvals` (never `github.actor`); `promotion-preflight.sh`/`snapshot-production.sh`/`deploy-production.sh`/`verify-production.sh`/`publish-frontend.sh`/`finalize-release.sh`/`compensate-production.sh` offline runs with a stateful AWS + `gh` stub, incl. immutable prefix-marker publication and frontend restoration; mandatory profile/region + mutation read-back + no-secrets scan; ruff/shellcheck/`git diff --check`. The live owner-approved promotion, the real `production` Environment approval, real ECR/ECS/S3/CloudFront mutations and read-backs, and the real GitHub Release publication are deferred to the consolidated pass)
  - [x] 3.5 Production hardening (offline implementation + gate green in `tests/scripts/production_hardening_test.sh`: task-definition/service-config validation, sanitized task-definition transforms, read-only inventory + production/staging separation + CloudTrail coverage tooling, frontend S3 REST + OAC migration tool, lifecycle environment guards, and the Fargate Spot/backup-limitation decisions in `explanations/PRODUCTION-HARDENING-DECISIONS.md`; live inventory read-back, OAC migration, CloudTrail read-back, and service/SG/IAM tightening deferred to the consolidated pass)
  - [x] 3.7 Release traceability (offline implementation + gate green in `tests/scripts/release_traceability_test.sh`: the four read-only lookups — commit/release/running/digest — plus the manifest↔ECR↔ECS-running-digest↔frontend consistency audit via `release/bin/trace.sh` + `release_contract.traceability`, offline fixtures for consistent/paused/drift state, a stateful AWS-stub proof of the live gather path (identity preflight + read-only), the read-only GitHub Releases index auto-fetch, and mandatory profile/region fail-closed semantics; the read-only live smoke test against real AWS/GitHub deferred to the consolidated pass)
  - [x] 3.6 Owner-approved rollback (offline implementation + gate green in `tests/scripts/rollback_test.sh`: the `release_contract.rollback` decision layer — dispatch/select/schema/frontend-restore/result plus the reused snapshot/plan/waiter/verify/compensate promotion contract; `rollback-release.yml` static checks incl. `production` Environment, shared non-cancelling `production-mutation` concurrency, pre-approval read-only preflight + post-approval full revalidation, `approvedBy` from `actions/runs/{run}/approvals` (never `github.actor`), run-pinned target-manifest consumption, automatic compensation, and no rebuild/tag-minting/release-publication; `rollback-preflight.sh`/`deploy-rollback.sh`/`restore-frontend.sh`/`verify-rollback.sh`/`record-rollback-result.sh` offline runs with a stateful AWS + `gh` stub incl. pre-approval current-vs-target summary, digest-pinned ECS revisions via sanitize/validate, no-`--delete` frontend restoration from the retained immutable prefix, paused-environment fail-closed verification, and idempotent rollback-result write/resume/conflict; mandatory profile/region + mutation read-back + no-secrets + no-tag-minting scan; ruff/shellcheck/`git diff --check`. The real owner-approved rollback, the real `production` Environment approval, real ECR/ECS/S3/CloudFront mutations and read-backs, real frontend restoration, and the real rollback-result artifact are deferred to the consolidated pass)
- [ ] **Pass 4** — Operational Maturity
- [ ] **Pass 5** — Future Improvements (non-mandatory)

---

## Known Issues & Resolutions

### Resolved ✅

| Issue | Resolution |
|-------|-----------|
| ✅ Private RDS unreachable for SQL ops (no public access, private subnets) | One-off Fargate psql task pattern, codified in `scripts/ecs-run-sql.sh` (see [AWS_COMMANDS_GUIDE.md Part D](./AWS_COMMANDS_GUIDE.md)) |
| ✅ Schema apply reported exit 0 but tables missing (`missing table [sessions]` crash loop) | Caused by JSON-escaping bugs in hand-built container commands + no read-back verification. Fixed by `ecs-run-sql.sh` (base64 SQL transport) + mandatory `--verify` in the same run |
| ✅ Service Connect `SC service is already used` / `portName does not refer to any named PortMapping` | SC portName must match container `portMappings[].name` AND be unique per Cloud Map namespace. Staging TDs use `*-staging-port` names |
| ✅ `launch type and capacity provider strategy` conflict on service create | Mutually exclusive flags — staging/prod services use `--capacity-provider-strategy FARGATE_SPOT` only |
| ✅ Container `secrets` rejected without `executionRoleArn` | All TDs include `executionRoleArn: ecsTaskExecutionRole` |
| ✅ ECS stopped launching tasks after crash loop (`desired:1, running:0`) | ECS deployment circuit-breaker behavior; resolved with `--force-new-deployment` after root-cause fix |
| ✅ Master DB password plaintext in 11 helper TD revisions; staging passwords in session logs (2026-08-02) | Master creds moved to `onlineshop/rds/master` secret; exec role policy extended; both staging passwords rotated (verified by connecting as each service account); 11 revisions permanently purged via `delete-task-definitions` |
| ✅ Long blocking poll loops hit shell timeouts (120s + 600s lost) | Rule added to `AGENTS.md`: no blocking polls; use `aws ecs wait services-stable` or short bounded loops |

### Open

| Issue | Notes |
|-------|-------|
| ✅ Duplicate `build-and-push.yml` workflow | Removed; `build-and-deploy.yml` is the single active CI/CD workflow after merge |
| ✅ Staging remained billable after E2E | Main CI tears staging down in an `always()` step: ECS→0, ALB deletion, and RDS deletion without snapshot retention |
| ✅ Snapshot restoration preserved drift and stale test data | Every staging start now creates empty RDS, applies repository schemas/seeds, verifies grants and data as restricted users, then deploys services |
