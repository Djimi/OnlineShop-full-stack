# WHAT WAS DONE — MVP Deploy Pass 1

> Track every action so future automation can reproduce it.

---

## Step 1.1 — AWS Account & OIDC Foundation ✅

- AWS account `799111666795`, region `eu-north-1` (Stockholm)
- IAM user `admin` for manual CLI operations (profile: `dpm-profile`)
- GitHub → AWS OIDC trust configured via IAM role

## Step 1.2 — Container Registry (ECR) ✅

Three ECR repositories created:
| Repository | URI |
|---|---|
| `onlineshop-auth` | `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth` |
| `onlineshop-items` | `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items` |
| `onlineshop-api-gateway` | `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-api-gateway` |

Naming convention: `onlineshop-<service>` (no slashes, fixed from initial mistake).

## Step 1.3 — GitHub Actions Build & Push ✅

Workflow: `.github/workflows/build-and-deploy.yml`
- Triggers: pushes to `feature/**` and `main`, pull requests to `main`, and `workflow_dispatch`
- Selective test gates, OIDC auth, ECR login, Maven build, Docker build, and push with `sha-<FULL_SHA>` tags
- Items job builds `common` first (dependency)
- Maven caching via `actions/cache@v4` with `pom.xml` hash keys
- Docker layer caching via BuildKit (`setup-buildx-action` + `type=gha`)

IAM role for GitHub Actions: `arn:aws:iam::799111666795:role/github-actions-onlineshop`
- Applied trust policy: OIDC from configured subject `repo:Djimi@8793507/OnlineShop-full-stack@1097550215` on `main` and `feature/*` branch refs
- Inline policy: `ecr-push-pull` for ECR operations

## Step 1.4a — RDS Provisioning ✅

| Property | Value |
|---|---|
| Instance ID | `onlineshop-postgres-db` |
| Endpoint | `onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com` |
| Port | 5432 |
| Engine | PostgreSQL 18.4 |
| Instance class | db.t4g.micro |
| Storage | 20 GB, encrypted |
| Initial DB | `auth` |
| Public access | **No** (false) |
| Security group | `sg-04ba95188d8374d96` |
| Subnet group | `default-vpc-06eeb0bc47ecdbd61` |
| Multi-AZ | No |

No credentials in AWS Secrets Manager yet.

---

## Step 1.4b — Apply init-db scripts ✅

- [x] Create `items` database on RDS
- [x] Apply Auth DDL: `Auth/init-db/01-schema.sql` → table `users` + `sessions` on database `auth`
- [x] Apply Auth seed: `Auth/init-db/02-seed-data.sql` → 1 test user (`testuser`)
- [x] Apply Items DDL: `Items/init-db/01-schema.sql` → table `items` on database `items`
- [x] Apply Items seed: `Items/init-db/02-data.sql` → 5 seed products
- [x] Create least-privilege service accounts (not root `dbadmin`):
  - `auth_app` — access only to `auth` database (SELECT/INSERT/UPDATE/DELETE on all tables)
  - `items_app` — access only to `items` database (SELECT/INSERT/UPDATE/DELETE on all tables)
- [x] Store service credentials in AWS Secrets Manager:
  - `onlineshop/auth/db` — ARN: `arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db-umtxh1`
  - `onlineshop/items/db` — ARN: `arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/items/db-bM5eSY`

**Connectivity method:** `docker run --rm -e PGPASSWORD=$PASS postgres:18-alpine psql -h $HOST -U $USER -d $DB`
No `psql` client installed locally; used Postgres Docker image as client.
Master user: `dbadmin`, password stored in local `.env` (`POSTGRES_AWS_SECRET`). Root user NOT used by apps.

---

## Step 1.5 — Pre-Requisite Code Changes ✅

### Items: Spring Boot Actuator added
- Added `spring-boot-starter-actuator` dependency to `Items/pom.xml`
- Added management endpoints config to `Items/src/main/resources/application.yml`:
  - `/actuator/health` with `show-details: always`
  - Liveness/readiness probe groups
  - Custom health group: `[db]`
  - Metrics tagged with `application: items`
- **Security fixes** from code review:
  - Restricted exposure from `"*"` → `health,metrics` (prevents DB password leak via `/actuator/env`)
  - `env.show-values` / `configprops.show-values` → `never`
  - Removed dead singular `endpoint` config block
  - Removed `prometheus` from exposure (no dependency)
- Fixed pre-existing test bugs (import paths, missing mocks) — 72 tests pass

### API Gateway: Redis resilience for sidecar
- `RateLimitFilter.java`: fail-open on Redis errors (was returning 500s)
- `RateLimitConfig.java`: `@Lazy` proxy manager + `RedisURI` with bounded timeouts (was unlimited)
- `application.yml`: added `spring.data.redis.connect-timeout: 10s`
- 10 tests pass

---

## Step 1.5 — ECS Infrastructure ✅ (provisioned, not fully working)

### ECS Cluster
- `onlineshop-cluster` (Fargate), ACTIVE

### IAM
- `ecsTaskExecutionRole` — ECR pull + CloudWatch logs + Secrets Manager read

### Security Groups
| SG | ID | Rules |
|----|-------|-------|
| ALB | sg-0b5427a6a3bf31c29 | inbound :80 from 0.0.0.0/0 |
| ECS | sg-0b209104a6b15b157 | inbound :0-65535 from ALB SG |
| DB | sg-04ba95188d8374d96 | inbound :5432 from ECS SG |

**MISSING:** ECS SG self-referencing rule for ports 9000-9001 (blocks API Gateway → Auth/Items)

### Cloud Map
- Namespace `onlineshop.local` (private DNS, VPC vpc-06eeb0bc47ecdbd61)
- Services: `auth-port`, `items-port`, API Gateway client

### Task Definitions
| Service | Latest Rev | Image | Notes |
|---------|------------|-------|-------|
| Auth | 3 | sha-befc22... | HikariCP 10, Secrets Manager |
| Items | 4 | sha-ba7905d | Actuator, HikariCP 10 |
| API Gateway | 12 | sha-ba7905d | Redis sidecar, rate-limit off, Service Connect (`auth`/`items`) |

### ALB
- `onlineshop-alb` → DNS: To get current DNS: `aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 --names onlineshop-alb --query 'LoadBalancers[0].DNSName' --output text`
- Target group: `onlineshop-gateway-tg` (port 10000, IP type)
- Listener: :80 → forward to gateway-tg

### ECS Services
All 3: 1 running, HEALTHY, Service Connect enabled

### Fixes Applied During Deployment

1. **Self-referencing SG rules** — Added inbound tcp:9000-9001 + tcp:6379 on `sg-0b209104a6b15b157` from itself → API Gateway now reaches Auth/Items/Redis
2. **Service Connect DNS** — FIXED (2026-07-26). Enabled Service Connect on all 3 services, gateway uses `http://auth:9001` and `http://items:9000`
3. **Resilience4j TimeLimiter** — Auth validation timeout: 3s → 5s in `ResilienceConfig.java` (ECS task-to-task latency higher than localhost)
4. **Rate limiting disabled** — `GATEWAY_RATELIMIT_ENABLED=false` because `RateLimitConfig.bucket4jProxyManager` connects to Redis eagerly
5. **HikariCP pool** — Auth: 100 → 10 connections (RDS `db.t4g.micro` max ~25 connections)

### Verified Working
- Register new user: `POST /auth/register` → 201
- Login: `POST /auth/login` → 200 with token
- List items: `GET /items` with Bearer token → 200, 5 products
- Token validation: `GET /auth/validate` → 200
- ALB health check: `GET /actuator/health` → 200 UP
- API Gateway rev 11 (sha-ba7905d), Auth rev 3, Items rev 4

## Step 1.6 — Frontend: S3 + CloudFront ✅

### S3 Bucket
| Property | Value |
|---|---|
| Bucket name | `onlineshop-frontend-799111666795` |
| Bucket ARN | `arn:aws:s3:::onlineshop-frontend-799111666795` |
| Region | `eu-north-1` |
| Website endpoint | `onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com` |
| Access | Public read (bucket policy allows `s3:GetObject` from `*`) |

**Commands:**
```bash
aws s3api create-bucket --bucket onlineshop-frontend-799111666795 --region eu-north-1 --create-bucket-configuration LocationConstraint=eu-north-1
aws s3api delete-public-access-block --bucket onlineshop-frontend-799111666795 --region eu-north-1
aws s3api put-bucket-policy --bucket onlineshop-frontend-799111666795 --policy '{...PublicReadGetObject...}'
aws s3api put-bucket-website --bucket onlineshop-frontend-799111666795 --website-configuration '{"IndexDocument":{"Suffix":"index.html"},"ErrorDocument":{"Key":"index.html"}}'
aws s3 sync frontend/dist s3://onlineshop-frontend-799111666795/ --delete
```

### CloudFront Distribution
| Property | Value |
|---|---|
| Distribution ID | `EPS8MI3FV3B7X` |
| Domain Name | `d2akuwv5pxgajc.cloudfront.net` |
| Status | `Deployed` |
| Price Class | `PriceClass_All` |
| HTTPS | Enabled (CloudFront default certificate) |

**Origins:**
| ID | Type | Domain | Protocol |
|---|---|---|---|
| `s3-frontend` | Custom (S3 website) | `onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com` | HTTP |
| `alb-api` | Custom (ALB) | `onlineshop-alb-1163734147.eu-north-1.elb.amazonaws.com` | HTTP |

**Cache Behaviors:**
| Path Pattern | Origin | TTL | Methods | Headers Forwarded |
|---|---|---|---|---|
| `Default (*)` | `s3-frontend` | 86400 | GET, HEAD, OPTIONS | None |
| `/auth*` | `alb-api` | 0 (no cache) | ALL | Authorization, Content-Type |
| `/items*` | `alb-api` | 0 (no cache) | ALL | Authorization, Content-Type |

**Custom Error Response:**
- 404 → 200 with `/index.html` (enables SPA deep-linking)

### Frontend Build Configuration
- Built with `VITE_API_URL=''` so API calls use relative URLs (same-origin through CloudFront)
- Fixed `frontend/src/services/api.ts` to use `??` instead of `||` so empty string is preserved:
  ```ts
  const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:10000';
  ```

## Step 1.7 — Smoke Test ✅

### Verified via CloudFront
```bash
CF="https://d2akuwv5pxgajc.cloudfront.net"

# 1. Frontend loads
curl -s -o /dev/null -w "%{http_code}" "$CF/"
# → 200 OK (text/html)

# 2. API: register
curl -s -X POST "$CF/auth/register" -H "Content-Type: application/json" -d '{"username":"finaltest","password":"finaltest123"}'
# → 201 Created

# 3. API: login
curl -s -X POST "$CF/auth/login" -H "Content-Type: application/json" -d '{"username":"finaltest","password":"finaltest123"}'
# → 200 OK + Bearer token

# 4. API: items with auth
curl -s "$CF/items" -H "Authorization: Bearer $TOKEN"
# → 200 OK, 5 products

# 5. CORS preflight
curl -s -o /dev/null -w "%{http_code}" -X OPTIONS "$CF/auth/login" -H "Origin: https://d2akuwv5pxgajc.cloudfront.net" -H "Access-Control-Request-Method: POST"
# → 200 OK
```

### Verified via Browser (Playwright)
1. Navigate to `https://d2akuwv5pxgajc.cloudfront.net/login` → page loads
2. Fill username/password → click Sign in
3. Redirects to `/items` → catalog renders with 5 products (Laptop, Mouse, Keyboard, Monitor, Headphones)
4. No console errors after login

## Fixes Applied During Frontend Deployment

### CORS Fix
**Problem:** Browser login requests to CloudFront origin returned 403 "Invalid CORS request" because Spring Boot gateway only allowed `localhost` origins.

**Fix:**
- `api-gateway/src/main/java/com/onlineshop/gateway/config/CorsConfig.java`: changed `allowedOrigins` to `"*"`, `allowCredentials(false)`
- `api-gateway/src/main/resources/application.yml`: changed `allowed-origins` to `["*"]`, `allow-credentials: false`
- Rebuilt gateway JAR, built Docker image `cors-fix`, pushed to ECR
- Registered new task definition revision 13, deployed to ECS

**Result:** CORS preflight and cross-origin requests now succeed.

### CloudFront Path Pattern Fix
**Problem:** `/items` (exact path) was falling through to S3 origin because `/items/*` only matched paths with additional segments.

**Fix:** Changed CloudFront cache behaviors from `/auth/*` and `/items/*` to `/auth*` and `/items*` so exact paths also route to ALB.

### Remaining Tech Debt
- Rate limiter lazy Redis connection (pass 2)
- API path prefixing (`/api/*`) to separate frontend routes from API endpoints (fixes direct-navigation collision on `/items`)
- CloudFront cache invalidation is manual — integrate into CI/CD pipeline

---

## Pass 2 — CI Pipeline Hardening & Staging (2026-08-02)

### 2.4 — Auth JaCoCo Coverage Threshold Bump ✅

- LINE and BRANCH minimums: `0.30` → `0.50` in `Auth/pom.xml`

### 2.4 — Auth Actuator Security Fix ✅

- **Problem:** `endpoints.web.exposure.include: "*"` with `env.show-values: always` — potential DB password leak via `/actuator/env`
- **Fix:** Restricted exposure to `health,metrics`, changed `show-values` to `never`, aligned with Items pattern

### 2.2-2.5 — CI/CD Workflow Rewrite ✅

- **New file:** `.github/workflows/build-and-deploy.yml`
- **Triggers:** push to `feature/**` and `main`, PR to `main`, `workflow_dispatch`
- **Change detection:** `dorny/paths-filter@v3` with dependency-aware filters
- **Test gates:** `./mvnw verify` (was `mvnw package -DskipTests` in old workflow)
- **Concurrency:** Cancel in-progress on same branch/PR
- **Docker tags:** `sha-<SHA>` always, `branch-<name>` on feature, `main-latest` on main
- **E2E staging job:** Deploy to staging + run E2E tests (on push to main)

### 2.7 — Staging Scripts Created ✅

- `scripts/ci-deploy-staging.sh` — Deploys images to staging ECS services
- `scripts/resume-staging.sh` — Creates and verifies a clean database before ECS startup
- `scripts/pause-staging.sh` — Tears down ECS, ALB, and RDS without retaining a snapshot
- `scripts/setup-staging-env.sh` — Retired compatibility entry point

### Phase 2 completion audit (2026-08-04) ✅

- [x] Branch protection enforced for administrators; squash-only; required
  checks are `auth`, `items`, `api-gateway`, `frontend`, and `e2e-pr`.
- [x] Frontend lint/build validation added; four unsafe `any` error handlers
  replaced with a typed shared Axios error helper.
- [x] Blocking PR E2E runs against a disposable Docker Compose candidate.
- [x] GitHub OIDC role has scoped staging deployment and `iam:PassRole`
  permission restricted to `ecsTaskExecutionRole` and ECS Tasks.
- [x] Old staging resources detached from the production cluster and ALB removed.
- [x] Independent staging environment provisioned: dedicated VPC, subnets,
  route table/IGW, three security groups, ECS cluster, Cloud Map namespace,
  RDS, ALB/TG, task definitions, services, logs, and staging databases.
- [x] Staging lifecycle is independently startable/stoppable. Start creates a
  clean database from repository SQL and verifies least-privilege access; stop
  deletes the DB and ALB without snapshot retention by default.
- [x] Independent staging E2E passed after initial provisioning.
- [x] Snapshot restore path passed readiness and cloud E2E 3/3; both production
  and staging were then independently paused and verified.
- [x] Production lifecycle hardened and stale deleted ECR image references
  replaced with immutable `sha-06658a68e7ce6583e59069bc004065cc0b541e39` task revisions.
- [x] Exposed plaintext RDS master credential rotated; the replacement exists
  only in Secrets Manager and the plaintext `.env` entry was removed.
- [x] Production and staging lifecycle scripts moved to repository-level
  `scripts/`; obsolete shared-staging setup is hard-disabled.

### Phase 2.9 deterministic staging and lifecycle refactor (2026-08-04) ✅

- [x] Removed the runtime dependency on `onlineshop-staging-latest`.
- [x] RDS master credentials are generated and managed by RDS for every run;
  application passwords remain injected from the staging-only secrets.
- [x] Auth/Items databases, roles, schemas, grants, and deterministic seeds are
  applied and read back before ECS services start.
- [x] Shared ALB/ECS/RDS/wait/readiness helpers and explicit production/staging
  configs replace duplicated lifecycle implementations.
- [x] All production/staging pause and resume paths now emit UTC timestamped,
  numbered steps with typical duration ranges. Shared lifecycle helpers expose
  resource mutations, no-ops, waiters, readiness retries, and verification;
  completion logs report actual total runtime.
- [x] CI captures failure diagnostics, preserves them as an artifact, and runs
  staging teardown under `if: always()`.
- [x] Staging deploy preflights the immutable tag across all three ECR
  repositories, preventing partial service updates when an artifact is missing.
