# Pass 2 — CI Pipeline Hardening & Staging: Implementation Details

> Historical implementation record: the shared-cluster staging design below
> was replaced on 2026-08-04. The authoritative design is the fully isolated
> staging environment in [02_CI_PIPELINE_HARDENING.md](../02_CI_PIPELINE_HARDENING.md)
> and [executed/INFO.md](../executed/INFO.md). The legacy
> `scripts/setup-staging-env.sh` now exits before making changes.

## Overview

Pass 2 transformed the manual MVP deployment (Pass 1) into an automated CI/CD pipeline with proper test gates, selective service builds, branch protection, and a staging environment. This document explains every change in detail.

---

## 1. Auth JaCoCo Coverage Threshold (2.4)

**File:** `Auth/pom.xml`

**Before:**
```xml
<minimum>0.30</minimum>  <!-- LINE -->
<minimum>0.30</minimum>  <!-- BRANCH -->
```

**After:**
```xml
<minimum>0.50</minimum>  <!-- LINE -->
<minimum>0.50</minimum>  <!-- BRANCH -->
```

Auth has a sophisticated JaCoCo setup: unit test and integration test executions are merged (`jacoco-merged.exec`), then checked at the `verify` phase. The old 30% threshold was a placeholder. 50% is the minimum required by the plan.

**Note:** Items already has a 90% LINE threshold. API Gateway has no JaCoCo (out of scope for Pass 2).

---

## 2. Auth Actuator Security Fix (2.8)

**File:** `Auth/src/main/resources/application.yml`

**Problem:** Auth accidentally exposed every actuator endpoint including `/actuator/env` and `/actuator/configprops`, which can leak database passwords and connection strings in plaintext.

The issue was at two levels:
1. `endpoints.web.exposure.include: "*"` (global, overrides individual) — exposed ALL endpoints
2. `env.show-values: always` and `configprops.show-values: always` — showed actual secret values

**Fix:**

| Property | Before | After |
|----------|--------|-------|
| `endpoints.web.exposure.include` | `"*"` | `health,metrics` |
| `health.show-details` | `always` | `when-authorized` |
| `health.group.*.show-details` | `always` | `when-authorized` |
| `env.show-values` | `always` | `never` |
| `configprops.show-values` | `always` | `never` |

This matches the pattern already applied to Items in Pass 1. The ECS health check (`curl -f http://localhost:9001/actuator/health/liveness`) is unaffected — liveness probes are part of the `health` endpoint group and only check HTTP status codes.

---

## 3. CI/CD Workflow Rewrite (2.2—2.6)

**New file:** `.github/workflows/build-and-deploy.yml`
**Old file:** `.github/workflows/build-and-push.yml` (removed; superseded by the tested pipeline)

### 3.1 Trigger Matrix

| Event | Action |
|-------|--------|
| `push` to `feature/**` | Detect changes → test affected → build → push `sha-<SHA>` + `branch-<name>` |
| `pull_request` to `main` | Detect changes → test affected → build → push `sha-<SHA>` only |
| `push` to `main` | Build ALL services → test → push `sha-<SHA>` + `main-latest` → deploy staging → E2E |
| `workflow_dispatch` | Manual service selection (`auth`/`items`/`api-gateway`/`all`) |

### 3.2 Change Detection (`dorny/paths-filter@v3`)

```yaml
auth:
  - 'Auth/**'
items:
  - 'Items/**'
  - 'common/**'        # Items depends on common
api-gateway:
  - 'api-gateway/**'
frontend:
  - 'frontend/**'
```

The paths-filter analyzes the git diff and outputs `true`/`false` per service. On push to main, ALL services are forced to `true` (ensures a complete deploy). On `workflow_dispatch`, the selection is manual.

**Design decision:** No `root-config` catch-all filter. Changes to `docker-compose.yml`, `.env`, etc. don't trigger rebuilds automatically — use `workflow_dispatch` with `all` for those rare cases.

### 3.3 Test Gates

All services run `./mvnw verify` instead of the old `mvnw clean package -DskipTests`:

| Service | Test command | Coverage gate | Notes |
|---------|-------------|---------------|-------|
| Auth | `./mvnw verify` | JaCoCo 50% LINE + BRANCH | Runs unit + integration tests (Testcontainers) |
| Items | `./mvnw verify` | JaCoCo 90% LINE | Builds `common` first (`./mvnw install -DskipTests`) |
| API Gateway | `./mvnw verify` | None | Unit + MockServer integration tests |

Test reports are uploaded as build artifacts even on failure (`if: always()`).

### 3.4 Docker Tagging Model

```yaml
Compute Docker tags:
  if $ref starts with "refs/heads/feature/":
    → sha-$SHA, branch-$(ref_name with / replaced by -)
  if $ref == "refs/heads/main":
    → sha-$SHA, main-latest
  else (PR, workflow_dispatch on non-main):
    → sha-$SHA only
```

**Immutability:** `sha-<FULL_40_CHAR_COMMIT_HASH>` is always pushed — immutable and traceable.
**Convenience:** `branch-<name>` and `main-latest` are mutable convenience pointers.
**Non-official:** Feature-branch and `main-latest` images are non-official artifacts (official releases are Pass 3).

### 3.5 Concurrency

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

Older in-progress builds on the same branch/PR are automatically cancelled when new commits arrive. This saves GitHub Actions minutes and prevents race conditions.

### 3.6 Service-Specific Build Details

**Auth** (`Auth/Dockerfile` — multi-stage):
1. Host-side Maven test: `./mvnw verify`
2. Docker build: `context: Auth`, compiles the source in a Maven build stage, then copies the resulting JAR into the runtime image
3. Maven cache: `~/.m2/repository` via `actions/cache@v4` for the host-side test job

**Items** (`Items/Dockerfile` — multi-stage):
1. Host-side: build `common` (`./mvnw install -DskipTests`)
2. Host-side Maven test: `./mvnw verify`
3. Docker multi-stage build: `context: ., file: Items/Dockerfile`
   - Build stage: `maven:3.9-eclipse-temurin-25`, builds `common` + `Items` with `-DskipTests` (tests already ran)
   - Run stage: `eclipse-temurin:25.0.1_8-jre-alpine-3.23`
4. Docker layer cache: `cache-from: type=gha, cache-to: type=gha,mode=max`
5. The Docker Maven cache is separate from GHA's host-side Maven cache. Maven dependencies inside the Docker build are not shared across CI runs unless the Docker layer cache is restored.

**API Gateway** (`api-gateway/Dockerfile` — multi-stage):
1. Host-side Maven test: `./mvnw verify`
2. Docker build: `context: api-gateway`, compiles the source in a Maven build stage, then copies the resulting JAR into the runtime image

### 3.7 Caching Strategy

| Cache | Mechanism | Scope |
|-------|-----------|-------|
| Host Maven deps | `actions/cache@v4` with `pom.xml` hash key | Auth, API Gateway, Items (host-side only) |
| Docker layers | `docker/setup-buildx-action@v3` + `type=gha` | All 3 Docker builds |
| Docker Maven | `--mount=type=cache` in each Java Dockerfile | Docker build only (separate from host Maven cache) |

**Cache miss correctness:** Each cache uses `restore-keys` with progressive fallback (`*-maven-auth-` → `*-maven-`). If exact key misses, partial match restores best-effort cache. Maven re-downloads only missing dependencies.

---

## 4. Staging Environment (2.7)

> **Current implementation (2026-08-04):** The original shared-cluster design
> below is historical. Staging now has its own VPC, ECS cluster, Cloud Map
> namespace, RDS, security groups, and ALB. Every run creates empty RDS, applies
> repository schemas and deterministic seeds, verifies restricted users, runs
> E2E, and deletes RDS without a snapshot. Runtime code is in
> `scripts/resume-staging.sh`, `scripts/bootstrap-staging-db.sh`,
> `scripts/pause-staging.sh`, and `scripts/lib/lifecycle.sh`. All four lifecycle
> entry points now emit UTC timestamped numbered steps, typical duration ranges,
> resource-level waiter/no-op/verification progress, and actual total runtime.

### 4.1 Architecture

The staging environment runs in the same ECS cluster (`onlineshop-cluster`) and Cloud Map namespace (`onlineshop.local`) as production, but uses different:

- **DNS names:** `auth-staging`, `items-staging`, `gateway-staging` (vs `auth`, `items`, `gateway`)
- **Port mapping names:** `auth-staging-port`, `items-staging-port`, `gateway-staging-port`
- **Databases:** `auth_staging`, `items_staging` (on the same RDS)
- **DB users:** `auth_app_staging`, `items_app_staging`
- **Secrets Manager entries:** `onlineshop/auth/db-staging`, `onlineshop/items/db-staging`
- **ALB:** Separate `onlineshop-staging-alb` with its own target group and listener

### 4.2 Why Unique Port Names?

Service Connect maps the `portName` in the ECS service config to a Cloud Map service name. Multiple ECS services in the same namespace CANNOT use the same port name. Production already uses `auth-port`, `items-port`, `gateway-port`. Staging uses `auth-staging-port`, etc.

This required updating the staging task definitions' container `portMappings[].name` to match, so Spring Boot's internal port (9001, 9000, 10000) stays the same — only the ECS/Cloud Map metadata name changed.

### 4.3 On-Demand Model

All 3 staging ECS services have `desiredCount: 0` by default. When a deployment is triggered (push to `main`), the `ci-deploy-staging.sh` script:
1. Registers new task definitions with the new image tag
2. Updates each service to `desiredCount: 1`
3. Waits up to 60s for services to stabilize
4. The `e2e-staging` job runs E2E tests against the staging ALB

After verification, the services can be manually scaled back to 0. An automated teardown step can be added in Pass 3.

### 4.4 Provisioning Issues & Fixes

**Issue 1: Schema validation failure**
All auth-staging tasks crashed with `Schema validation: missing table [sessions]`. Root cause: The `echo <schema> | psql` approach for applying SQL via ECS task had string escaping issues — the SQL was applied to the wrong database or not at all. **Fix:** Dropped and re-created tables using explicit `CREATE TABLE` statements without `IF NOT EXISTS`, via `psql -c` individual commands.

**Issue 2: ECS stopped starting new tasks after repeated failures**
After 10+ task crashes, ECS entered steady state with `desired: 1, running: 0`. **Fix:** Used `--force-new-deployment` to reset the deployment counter.

### 4.5 Infrastructure ARN Reference

| Resource | ARN / ID |
|----------|----------|
| Staging ALB | `arn:aws:elasticloadbalancing:eu-north-1:799111666795:loadbalancer/app/onlineshop-staging-alb/095c9e98dbbe762e` |
| Staging DNS | `onlineshop-staging-alb-615176433.eu-north-1.elb.amazonaws.com` |
| Staging TG | `arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-staging-tg/201ace94eec44688` |
| Auth staging TD | `arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth-staging:2` |
| Items staging TD | `arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items-staging:2` |
| API Gateway staging TD | `arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway-staging:2` |
| Auth secret | `arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db-staging-Dkh7wC` |
| Items secret | `arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/items/db-staging-LaYr9R` |

### 4.6 Cost

Staging services are Fargate Spot and scaled to 0. The staging ALB costs ~$24.19/month while running. To pause it (same pattern as production):

```bash
# Pause
aws elbv2 delete-listener --listener-arn <staging-listener-arn>
aws elbv2 delete-target-group --target-group-arn <staging-tg-arn>
aws elbv2 delete-load-balancer --load-balancer-arn <staging-alb-arn>

# Resume (reverse)
# Create ALB, TG, listener, wire API Gateway staging service to TG
```

---

## 5. IAM Permission Changes (2.8)

**Added to `github-actions-onlineshop` role:**

Policy `ecs-deploy-staging`:
```json
{
  "Effect": "Allow",
  "Action": [
    "ecs:DescribeTaskDefinition",
    "ecs:RegisterTaskDefinition",
    "ecs:UpdateService",
    "ecs:DescribeServices",
    "ecs:ListTasks",
    "ecs:DescribeTasks"
  ],
  "Resource": "*"
}
```

Plus ELB describe for E2E test step:
```json
{
  "Effect": "Allow",
  "Action": [
    "elasticloadbalancing:DescribeLoadBalancers",
    "elasticloadbalancing:DescribeTargetGroups"
  ],
  "Resource": "*"
}
```

The existing `ecr-push-pull` policy already covers ECR operations. The `ecsTaskExecutionRole` already has `secretsmanager-read-onlineshop` with `db*` wildcard covering both production and staging secrets.

---

## 6. Branch Protection (2.1)

**Applied on `main` via GitHub API:**

| Setting | Value |
|---------|-------|
| Required status checks | `auth`, `items`, `api-gateway`, `e2e-staging` |
| Strict mode | `true` (must be up-to-date with base before merge) |
| Required approvals | 1 |
| Dismiss stale reviews | `true` |
| Linear history | `true` (squash merge only) |
| Force pushes | Disabled |
| Branch deletions | Disabled |

**Important:** The status checks (`auth`, `items`, `api-gateway`, `e2e-staging`) are the job names in the new `build-and-deploy.yml` workflow. These won't exist on PRs until the workflow is merged to `main` and runs against the PR. Temporary workaround during development: add a `push` trigger to the workflow on a feature branch, test it, then remove before merging.

---

## 7. Scripts Created

### `scripts/ci-deploy-staging.sh`

CI-friendly deploy script that:
1. Takes an image tag argument (e.g., `sha-abc123`)
2. For each staging service, describes the current task definition
3. Updates the container image in the definition
4. Registers a new task definition revision
5. Updates the ECS service to use it (desired: 1)
6. Waits 60s for deployment stability

Called from the `e2e-staging` job in the workflow.

### `scripts/setup-staging-env.sh`

Retired compatibility entry point. Deterministic lifecycle behavior is owned by
the scripts listed in the current-implementation note above.

---

## 8. What Was NOT Done (Deliberately)

| Item | Reason |
|------|--------|
| API Gateway JaCoCo | Out of scope for Pass 2 |
| Frontend CI test (Playwright) | No `test` script in `package.json` yet |
| E2E tests in CI (non-staging) | No staging → no E2E gate. Runs against staging after main deploy |
| ECR image tag immutability | Pass 3 (release traceability) |
| ECR resource scoping | Pass 3 (tighten `Resource: "*"` to specific ARNs) |
| Staging ALB auto-pause | Completed by the isolated staging teardown path |
| Old workflow removal | Completed by removing the duplicate `build-and-push.yml`; verify the replacement after merge to `main` |
| Verify Auth 50% coverage | Run `cd Auth && ./mvnw verify` locally (requires Docker for Testcontainers) |

---

## 9. Verification Checklist

- [x] Auth JaCoCo threshold at 50% in `pom.xml`
- [x] Auth actuator only exposes `health,metrics` with `show-values: never`
- [x] Workflow triggers: push to `feature/**` and `main`, PR to `main`, `workflow_dispatch`
- [x] Change detection via `dorny/paths-filter@v3`
- [x] Tests run via `./mvnw verify` (not `-DskipTests`)
- [x] Docker tags: `sha-<SHA>`, `branch-<name>`, `main-latest`
- [x] Concurrency groups with cancel-in-progress
- [x] Test reports uploaded as artifacts
- [x] Staging ALB + TG + listener created
- [x] 3 staging task definitions at revision 2
- [x] 3 staging ECS services at desired:0, FARGATE_SPOT
- [x] Staging databases with schemas + seed data
- [x] Staging Secrets Manager entries
- [x] Smoke test: register → login → items → validate (all pass)
- [x] IAM permissions: ECS deploy + ELB describe
- [x] Branch protection on `main`
