# Pass 2 — CI Pipeline Hardening & Staging

**Goal:** Transform the manual MVP into an automated CI/CD pipeline with proper test gates, selective builds, branch protection, and a staging environment.

**Prerequisite:** Pass 1 complete (services running on AWS).

**Exit criteria:** Pushing to a feature branch triggers automated build+test for affected services. PRs to `main` are gated by tests. A staging environment exists for pre-production validation.

---

## Tasks

### 2.1 Branch Protection & Merge Policy

- [x] Enable `main` branch protection completely:
  - [x] Require passing status checks
  - [x] Require at least one approval
  - [x] Dismiss stale approvals on new pushes
  - [x] Require linear history with squash merge only
- [x] Forbid direct pushes to `main` (including administrators)
- [x] Configure concurrency groups so older builds on the same branch are canceled when new commits arrive

### 2.2 Automated CI Triggers

- [x] Trigger CI on pushes to `feature/*` branches
- [x] Trigger CI on pull requests targeting `main`
- [x] Keep `workflow_dispatch` for manual runs

### 2.3 Change Detection & Selective Builds

- [x] Implement automated change detection (e.g., `dorny/paths-filter` or custom script analyzing `git diff`)
- [x] Define dependency graph in the workflow:
  - `common` changes → rebuild `common` + `Items`
  - `Auth` changes → rebuild `Auth` only
  - `api-gateway` changes → rebuild `api-gateway` only
  - `frontend` changes → rebuild `frontend` only
  - Root-level / shared config changes → rebuild all affected services
- [x] Ensure `common` + `Items` are validated against the same repository snapshot (req 01 §5.2)
- [x] No full-repository rebuild as default fallback — dependency-aware selective rebuild only

### 2.4 Test Gates

- [x] **Feature-branch pushes:** run unit + integration tests for affected services
  - Target: under 5 minutes; hard limit: 10 minutes
- [x] **PRs to `main`:** run unit + integration + frontend validation for affected components
- [x] **Auth:** enforce 50% minimum coverage gate (JaCoCo — already configured)
- [x] **E2E gate on PR:** blocking e2e validation must pass before merge
  - Run against merge-queue candidate or equivalent pre-merge integration candidate
  - Does NOT need to run on every feature-branch push

### 2.5 Docker Tagging Model

- [x] Every image gets an immutable tag: `sha-<FULL_OR_SHORT_SHA>`
- [x] Feature-branch images: SHA tag + optional `branch-<name>` convenience tag
- [x] `main` candidate images: SHA tag + mutable `main-latest` convenience pointer
- [x] Feature-branch and `main`-candidate images are non-official artifacts
- [x] Push all images to ECR on successful build

### 2.6 Caching Optimization

- [x] Verify GitHub Actions Maven dependency cache is working (hit rate in logs)
- [x] Verify Docker layer cache is working
- [x] Confirm cache miss does not break builds (correctness requirement — req 01 §7.4)

### 2.7 Staging Environment

- [x] Create a staging ECS cluster (fully isolated from production: separate VPC, subnets, security groups, RDS, namespace, services, and ALB)
- [x] Staging should be **on-demand** (prefer short-lived to reduce cost — req 02 §3.2):
  - Option A: Scale staging services to 0 when idle, scale up on deploy
  - Option B: Spin up staging via IaC on demand, tear down after validation
- [x] Deploy `main` candidate images to staging automatically after successful `main` build
- [x] Run e2e tests against staging after deployment
- [x] Staging database: isolated `db.t4g.micro`, snapshot-and-delete while idle, with seeded test data

### 2.8 CI Security

- [x] Confirm OIDC is the only auth mechanism for GitHub → AWS (no long-lived keys)
- [x] Confirm secrets are fetched from AWS Secrets Manager at runtime
- [x] Verify secret masking in GitHub Actions logs

### 2.7 Lifecycle commands

- Production start: `bash scripts/resume-playground.sh`
- Production stop: `bash scripts/pause-playground.sh`
- Staging start: `bash scripts/resume-staging.sh`
- Staging stop: `bash scripts/pause-staging.sh`

Staging stop scales ECS to zero, deletes its ALB, and replaces its RDS instance
with an encrypted final snapshot. This avoids RDS's automatic restart after a
seven-day stop and preserves environment independence and test data.

### 2.9 Staging Redeployment & Lifecycle Script Refactoring

- [ ] Replace snapshot-based staging restoration with deterministic,
  from-scratch deployment:
  - [ ] Create a new empty staging RDS instance on every staging start
  - [ ] Create the staging databases, restricted application users, and grants
  - [ ] Apply version-controlled schemas or migrations
  - [ ] Load deterministic test seed data
  - [ ] Verify schema, permissions, and seed data before deploying services
  - [ ] Run E2E against the clean environment
  - [ ] Delete staging RDS without retaining a data snapshot after validation
- [ ] Make clean bootstrap failure-safe: if initialization, deployment, or E2E
  fails, run staging teardown while preserving logs and failure diagnostics.
- [ ] Remove the staging runtime dependency on
  `onlineshop-staging-latest`; keep snapshots only for explicitly requested
  debugging or disaster-recovery workflows.
- [ ] Refactor duplicated production/staging lifecycle logic into shared,
  testable helpers for ALB management, ECS scaling, AWS waiters, readiness
  checks, and post-mutation verification.
- [ ] Keep thin environment-specific start/stop entry points so staging-only
  destructive database initialization cannot be invoked against production.
- [ ] Store environment configuration explicitly and validate the account,
  region, cluster, VPC, database, and service identifiers before mutations.
- [ ] Exercise and verify all four resulting paths: production start,
  production stop, clean staging deploy, and staging teardown.

---

## Cost Impact

| Addition | Estimated Cost |
|---|---|
| GitHub Actions minutes (Free Tier: 2000 min/month) | $0 (within limits) |
| Staging ECS (on-demand, short-lived) | ~$0.50–1.00 |
| Additional ECR images (feature branches) | ~$0.10 |
| **Incremental total** | **~$0.50–1.00** |

---

## Out of Scope for Pass 2

- Official release promotion flow (Pass 3)
- Release manifests, SBOM, provenance (Pass 3)
- ECR lifecycle / retention policies (Pass 3)
- Rollback mechanism (Pass 3)
- Notifications (Pass 4)
- Dashboards & runbooks (Pass 4)
- Nightly full validation (Pass 4)
