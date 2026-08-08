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

---

## Pass 3 — Release, Traceability & Promotion (2026-08-04)

### 3.1 Release contract and local validation foundation ✅

Implemented entirely under `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/` (see
`README.md` there for the full contract and usage):

- **Versioned JSON Schema** `schema/release-manifest.schema.json` (Draft-07):
  candidate vs official state discrimination via `anyOf`
  (`candidateManifest` / `officialManifest`), `additionalProperties: false` at
  every level, and strict patterns for SemVer, 40-char SHAs, image digests,
  SHA-256 checksums, RFC-3339 UTC timestamps, ECR tags, task-definition ARNs,
  and URLs. State rules live in the schema: candidates forbid
  `promotionWorkflow` and backend `taskDefinitionArn`; officials require them.
- **Deterministic local validator** in Python (`src/release_contract/`):
  strict `json.load` parsing (no regex JSON parsing), a control-character scan
  that rejects escaped `\u0000` etc. before schema validation, the pinned
  `jsonschema` engine (draft-07, `referencing` registry, no deprecated
  `RefResolver`), and normalized `{code, field, message}` issues that are
  stable across jsonschema versions. Every invalid fixture fails with its
  documented primary error code.
- **Helpers**: `semver.py` (validate/compare/strict-increase — rejects leading
  `v`, prerelease, build metadata, leading zeroes, non-ASCII shell metachar
  characters), `checksums.py` (file SHA-256, canonical sorted-key manifest
  checksum), `components.py` (component→repository/identity/tag/prefix mapping,
  single source of truth for `sha-*`, `release-*`, `v*`, `_releases/v*/`
  derivations), `crossrules.py` (atomic-identity rules: every component SHA and
  `items.commonSourceSha` equals `release.sourceSha`, identities/versions/
  repositories/tags agree).
- **Fixtures**: 2 valid (candidate + official `1.2.1`) and 37 invalid fixtures
  under `fixtures/`, with `fixtures/invalid/EXPECTED.md` as the authoritative
  fixture→primary-error-code table that the tests parse (docs and tests cannot
  drift).
- **Strict shell input helpers** `bin/release-input.sh` (SemVer, full SHA,
  SHA-256 hex, positive int, GitHub login, HTTP(S) URL, regular file) and an
  argv-only wrapper `bin/validate-manifest.sh` that never interpolates input
  into command strings.
- **Automated tests**: 61 Python `unittest` tests plus
  `tests/scripts/release_contract_test.sh` (repo-level gate that runs the
  Python suite, CLI fixture checks, determinism, and the checksum guard, and
  lints with `ruff` and `shellcheck`).

**Verification:** the full gate passes — 61/61 Python tests; every valid
fixture accepted; every invalid fixture rejected with its expected primary code;
CLI output deterministic; `--check-checksum` guard verified; `ruff check` and
`shellcheck` clean (installed via `pip install ruff shellcheck-py`;
`requirements.txt` pins `jsonschema==4.26.0`). No AWS CLI commands were run.

### 3.2 Candidate build evidence and immutable artifacts ✅ (offline; live checks deferred)

Implemented on top of 3.1 in `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/` and
`.github/workflows/build-and-deploy.yml`:

- **Staging serialization**: `e2e-staging` now owns resume → deploy → E2E →
  teardown in one job with job-level
  `concurrency: {group: ${{ github.workflow }}-staging-${{ github.ref }},
  cancel-in-progress: false}` and `if: always()` teardown. `serialization.py`
  models the semantics; `fixtures/serialization/*` prove a newer `main` push
  is queued and cannot race an older run's cleanup (no preemption, no foreign
  teardown).
- **One candidate evidence bundle** emitted by the new `candidate-evidence`
  job after `auth`, `items`, `api-gateway`, `frontend`, and `e2e-staging` all
  pass (uploaded as a 30-day GitHub artifact `candidate-evidence-<sha>-<attempt>`).
- **OCI labels** on all three backend images: `org.opencontainers.image.revision`,
  `.source`, `.created`, `.title`, plus project labels
  `org.onlineshop.component`, `org.onlineshop.build-run`,
  `org.onlineshop.producer.{run-id,run-attempt,event,ref}`; Items additionally
  records `org.onlineshop.common-revision=<monorepo-sha>`.
- **Idempotent SHA publishing**: `publish-candidate-image.sh` decides push /
  reuse / fail-closed from the existing image's labels and a GitHub-API-verified
  successful producer run; `verify-producer-set.sh` proves all three backends
  form one canonical producer set (same producer run id AND attempt, revision
  == SHA, Items `common` revision == SHA). Feature branches always push (Pass 2
  behavior); a `workflow_dispatch` on `main` pushes only when no `sha-<sha>`
  tag exists and otherwise reuses/fails closed so canonical bytes are never
  overwritten. A failed label read is a fail-closed error, never treated as
  "tag absent". `components.oci_labels()` is the single source of truth for
  label names; labels are read from the image config via
  `docker buildx imagetools inspect` (no layer pull), never
  `docker manifest inspect --verbose`.
- **Reproducible frontend packaging**: `package-frontend.sh` produces a
  byte-identical `frontend-dist.tar.gz` (normalized metadata, `gzip -n`), a
  sorted per-file checksum manifest, and the archive SHA-256, rejecting
  links/devices. `unpack-frontend.sh` + `frontend.py` reject traversal/links/
  device entries before extraction and verify the sorted manifest.
- **SBOMs**: `generate-sbom.sh` generates SPDX JSON with pinned Syft
  `v1.50.0` whose `linux_amd64` archive SHA-256 is verified before use
  (`SYFT_TOOL` overrides for tests).
- **Evidence + artifact identity**: `emit-candidate-evidence.sh` writes the facts
  index (`candidate-evidence.json`: run id/attempt, event, ref, full SHA,
  actor, conclusions, digests, frontend checksum, staging validation) plus a
  sorted `checksums.txt`. On a rerun it attributes the bytes to the original
  producer run (`candidateWorkflow` from `--producer-run-id/--producer-run-attempt`)
  and the current staging-validation run as `artifactWorkflow`; it refuses to
  emit unless all five job conclusions are `success`. The pinned
  `actions/upload-artifact@v4` returns `artifact-id`, `artifact-url`, and
  `artifact-digest` (GitHub service-reported SHA-256 of the uploaded archive)
  as step outputs; `record-artifact.sh` records
  `{runId, runAttempt, artifactId, artifactUrl, artifactDigest, name}` in a
  separate pointer artifact (the bundle's own identity cannot be embedded
  inside the bundle — circular self-checksum). `artifact.py` keeps the
  consumption-side rejection of duplicates/expired plus
  `verify_artifact_digest`.
- **Version at promotion**: `emit-candidate-manifest.sh` renders a
  schema-valid candidate manifest from the evidence + owner-assigned SemVer
  (Decision 3); the fixture flow reproduces the 3.1 valid candidate fixture.
- **Pinned Actions**: all release-critical third-party Actions in
  `build-and-deploy.yml` are pinned by full commit SHA with a version comment;
  the gate enforces it.
- **Tests**: new Python suites (`test_candidate.py`, `test_artifact.py`,
  `test_frontend.py`, `test_serialization.py`; 117 total) plus
  `tests/scripts/candidate_evidence_test.sh` (9 sections: Python tests,
  workflow YAML static checks, packaging reproducibility, safe extraction,
  publish/reuse/fail-closed, SBOM stub, evidence→manifest flow, artifact
  identity/digest recording, lint).

**Verification:** `tests/scripts/candidate_evidence_test.sh` and
`tests/scripts/release_contract_test.sh` both pass (117 Python tests, all
fixture flows, workflow static checks, `ruff` + `shellcheck` clean). No AWS
mutations and no staging/production lifecycle commands were run.

**Deferred to the consolidated verification pass (NOT claimed here):** real ECR
label read-back of pushed images, the three real ECR digests, a real GitHub
artifact ID and its service-reported digest from a real workflow run, real Syft
scans of the registry digests, and a live rerun proving reuse instead of
rebuild.

### 3.3 ECR release tagging, immutability, and least privilege ✅ (offline; live checks deferred)

Implemented on top of 3.2 in `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/`,
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/github-actions-{candidate-build,promotion,
production-deploy,rollback}-policy.json`, the updated OIDC trust policy, and
`.github/workflows/build-and-deploy.yml`:

- **Immutable repositories with narrow mutable exclusions.** `ecr/
  immutable-repositories.json` encodes the desired state for all three backend
  repositories: `imageTagMutability: IMMUTABLE_WITH_EXCLUSION` with exclusion
  filters exactly `main-latest` and `branch-*`, so `sha-*` and `release-*` tags
  can never be overwritten and `latest` stays absent (Decision 4). ECR tag
  mutability is repository-scoped, but supports exclusions (the newer
  `IMMUTABLE_WITH_EXCLUSION` + `imageTagMutabilityExclusionFilters` settings,
  read back via `describe-repositories`). `verify-immutable-repositories.sh`
  is read-only and fails closed on drift; `apply-immutable-repositories.sh`
  mutates via `put-image-tag-mutability` and immediately reads every repository
  back (exit 0 is not proof).
- **Server-side digest-preserving promotion.** `promote-image-digest.sh` mints
  `release-<version>` from `sha-<full-sha>` using `ecr:batch-get-image` (the
  exact manifest bytes already in ECR + the service-reported digest) and
  `ecr:put-image` — never pull/rebuild. `release_contract.ecr` (fixture-tested)
  decides `mint` / `reuse` (idempotent resume) / fail-closed (release tag at
  different bytes, candidate missing/mismatched). After a mint the script reads
  both tags back and runs `release_contract.ecr verify`; `--dry-run` decides
  without mutating.
- **Release identity collisions and resume.** `check-release-identity.sh` is a
  read-only preflight (GitHub `v<version>` tag via `gh`, ECR `release-<version>`
  tags, frontend `_releases/v<version>/release.json` marker via `s3api`) feeding
  the fixture-tested `release_contract.releaseid` module: `proceed` when nothing
  exists, `resume` when every existing partial object exactly matches the
  validated manifest, fail-closed on any collision.
- **Least privilege by job purpose.** Four source-controlled policy documents +
  `github-actions-role-layout.md`: candidate-build (ECR push scoped to the three
  repository ARNs), promotion (server-side `ecr:PutImage` but **no layer-upload
  actions**), production-deploy (ECR read + ECS + S3 + CloudFront +
  `iam:PassRole` to `ecsTaskExecutionRole` with the `ecs-tasks.amazonaws.com`
  condition), and rollback (deploy scope **minus `ecr:PutImage`**).
  `ecr:GetAuthorizationToken` is the only ECR action on `Resource: "*"`. The
  publication job gets `contents: write` only; validation jobs (frontend,
  e2e-pr) get job-level `permissions: {contents: read}` with no `id-token:
  write` and no AWS credentials.
- **OIDC trust policy** updated with the protected `environment:production`
  subject (`repo:Djimi@8793507/OnlineShop-full-stack@1097550215:environment:
  production`) in addition to `main`/`feature/*`. The exact `sub` is validated
  from a real job's JWT in the consolidated pass — never guessed.
- **`release_contract.iam`** structurally validates every policy: ECR scoped to
  the three ARNs, GetAuthorizationToken only on `*`, PassRole scoped +
  conditioned, no mutating action on `*`, promotion without layer upload,
  rollback without `ecr:PutImage`, candidate-build without deploy actions, and
  the trust policy requiring `sts.amazonaws.com` plus the three subjects.
- **Workflow static checks** (in the gate): validation jobs cannot request an
  OIDC token; the build workflow never computes `latest` or `release-*` tags;
  the build workflow never invokes the promotion script.
- **Tests**: new Python suites (`test_ecr.py`, `test_releaseid.py`,
  `test_iam.py`; 158 total across all suites) plus
  `tests/scripts/ecr_release_tagging_test.sh` (10 sections: Python tests,
  workflow static checks, config consistency, immutable-repo verify/apply with
  stub AWS, promote mint/reuse/conflict/dry-run, release-identity
  proceed/resume/collision, IAM+trust validation, profile/region + read-back
  static scan, lint). The gate uses a stateful stub `aws`/`gh` so every
  mutation path's read-back is exercised offline.

**Verification:** `tests/scripts/ecr_release_tagging_test.sh`,
`tests/scripts/candidate_evidence_test.sh`, and
`tests/scripts/release_contract_test.sh` all pass (158 Python tests, all
fixture flows, workflow static checks, `ruff` + `shellcheck` clean). No AWS
mutations and no staging/production lifecycle commands were run.

**Deferred to the consolidated verification pass (NOT claimed here):** ECR
repository settings read back against the real repositories, real
`put-image-tag-mutability`/`batch-get-image`/`put-image` behavior (including
attempts to overwrite SHA/release tags failing and convenience tags advancing),
the real OIDC `environment:production` subject decoded from an actual job's
JWT, and the IAM Access Analyzer (`aws iam validate-policy`) run before live
application of the policy documents.

### Independent 3.3 review corrections (applied after the above)

Review corrections applied to the uncommitted 3.3 work (no behavior regression,
all three offline gates re-verified green):

- **`release_contract.ecr`** now rejects an `expected.repository` outside the
  canonical `onlineshop-{auth,items,api-gateway}` set (previously any
  `onlineshop-*` prefix was accepted).
- **`check-release-identity.sh`** now dereferences annotated GitHub tag objects
  to the commit before comparing the `v<version>` tag SHA (a GitHub Release tag
  created as an annotated tag has `object.sha` = tag-object SHA, which produced
  a false `GIT_TAG_CONFLICT` on resume). The gate stub `gh` now routes on the
  API URL (`sys.argv[2]`) and covers annotated-tag peel → resume and peel
  conflict → fail-closed.
- **`release_contract.iam`** docstring no longer claims
  `ecr:GetDownloadUrlForLayer` is unscopable by repository (the Service
  Authorization Reference lists it with the `repository` resource type); only
  `ecr:GetAuthorizationToken` is unscopable.
- **Plan checkbox honesty:** the 3.3 checklist items whose live half is
  deferred (repository mutation, role split/switch-over, actual OIDC `sub`
  validation, IAM Access Analyzer run) are annotated as offline-only/not-yet-run
  instead of appearing fully complete.
- **Doc wording:** root/service `AGENTS.md`, `release/README.md`, and
  `docs/CI_CD_GOTCHAS.md` no longer state that the repositories "are
  configured" `IMMUTABLE_WITH_EXCLUSION` or that the account "uses" the
  `environment:production` OIDC subject — they now say the desired state is
  defined and the live mutation/role split/subject are applied in the
  consolidated verification pass.

### 3.4 Controlled staging-to-production promotion workflow ✅ (offline; live checks deferred)

Implemented the offline half of subphase 3.4 — the approved, approval-gated
promotion of one verified monorepo snapshot from staging to production —
without any AWS mutation, GitHub mutation, workflow run, or lifecycle
start/stop:

- **Decision layer** — `release_contract.promotion` (pure, fixture-tested)
  encodes every promotion rule: `dispatch` (SemVer + numeric candidate run id;
  a hand-typed image tag/digest is rejected), `run` evidence (successful
  `push` on `refs/heads/main` at the exact SHA with a successful cloud staging
  `e2e-staging` job), `ancestry` (candidate SHA descendant of the last
  official release and reachable from current `main`; `CANDIDATE_BEHIND_
  OFFICIAL`/`CANDIDATE_NOT_ON_MAIN`/`VERSION_NOT_INCREASING` fail closed),
  `preflight` (manifest schema, run evidence, ancestry, staging gate, release-
  name uniqueness, and the Decision 8 database-change review —
  `SCHEMA_CHANGE_UNREVIEWED`), `snapshot` (every field needed for compensation/
  resume), `plan` (canonical auth+items → api-gateway → frontend order plus
  circuit breaker + `minimumHealthyPercent=100`/`maximumPercent=200`), `waiter`
  (bound to the task-definition/deployment this run started, COMPLETED, exact
  digests), `frontend` (assets-first/index-last, no `--delete`, immutable
  per-release prefix, CloudFront invalidation), `verify` (running digests,
  service task-definition ARNs, frontend marker/checksum, ALB health), `finalize`
  (mint three `release-<version>` tags + publish `v<version>` only after
  production verification; `PUBLICATION_BEFORE_VERIFICATION`/
  `RELEASE_TAG_CONFLICT` fail closed; exact partial objects resume
  idempotently), and `compensate` (reverse-order restore plan).
- **Workflow** — `.github/workflows/promote-release.yml`:
  `workflow_dispatch` inputs `version` + `run_id` (+ optional `source_sha`/
  `database_change`/`migration_reviewed`); a read-only `preflight` job
  (dispatch inputs + candidate manifest contract) before the protected
  `production` Environment; the approved `promote` job (shared non-cancelling
  `production-mutation` concurrency group) runs the full preflight after
  approval/lock, then registers digest-pinned task definitions, publishes the
  frontend, verifies production, renders the official manifest, and finalizes;
  a `compensate` job (`if: failure()` on `promote`) restores the pre-promotion
  snapshot artifact including the frontend live root. It consumes the candidate
  evidence by the exact producing run attempt (never the latest) and never
  rebuilds (static check proves no `build-push-action`/
  `publish-candidate-image.sh`); `approvedBy` is derived from the
  environment-approval evidence via `actions/runs/{run}/approvals`, never
  `github.actor`.
- **Shell wrappers** — `release/bin/promotion-preflight.sh`,
  `snapshot-production.sh` (read-only), `deploy-production.sh` (copy +
  `sanitize-task-definition.sh` + `validate-task-definition.sh` + register with
  read-back + waiter binding), `verify-production.sh` (read-only),
  `publish-frontend.sh`, `finalize-release.sh` (calls `promote-image-digest.sh`
  server-side mint and `gh release create`; refuses publication without
  `PROMOTION_PRODUCTION_VERIFIED=true`), `compensate-production.sh`, and
  `check-release-identity.sh` (release-identity proceed/resume/collision).
  All enforce the mandatory non-overridable `--profile dpm-profile --region
  eu-north-1` on every `aws` call and read back after every mutation.
- **Role split policy** — `github-actions-promotion-policy.json` adds the
  promotion-purpose role to `github-actions-role-layout.md` (server-side
  `ecr:PutImage`/`BatchGetImage` scoped to the three repository ARNs, no layer-
  upload actions).
- **Tests** — `release/tests/test_promotion.py` (51 unit tests) plus
  `tests/scripts/promotion_test.sh` (10 sections: Python suites; the
  decision-layer CLI against valid/invalid promotion fixtures; `promote-release.yml`
  static checks; stateful AWS + `gh` stub runs of preflight/snapshot/verify/
  finalize/compensate/deploy-dry-run; a mandatory profile/region + mutation
  read-back + no-secrets static scan; ruff/shellcheck/`git diff --check`).

**Verification:** `tests/scripts/promotion_test.sh` passes, and the existing
`release_contract_test.sh`, `candidate_evidence_test.sh`,
`ecr_release_tagging_test.sh`, `production_hardening_test.sh`,
`release_traceability_test.sh`, and `lifecycle_test.sh` gates all still pass
(360 Python tests across all suites, ruff + shellcheck + `git diff --check`
clean). No AWS CLI commands and no staging/production lifecycle commands were
run.

**Deferred to the consolidated verification pass (NOT claimed here):** the real
owner-approved promotion against live AWS/GitHub — the actual `production`
Environment approval and required-reviewer entitlement check, real ECR/ECS/S3/
CloudFront mutations and read-backs, the real GitHub Release publication, and
the switch of the workflow to the per-purpose roles. The live plan checkboxes in
`03_RELEASE_TRACEABILITY.md` are annotated accordingly.

## CI staging permission incident (2026-08-08)

The failed [main CI run 31259210183, `e2e-staging` job
93107532753](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/31259210183/job/93107532753)
was investigated from the Actions log. The failure occurred during clean
staging RDS creation, before deployment or E2E execution:

```text
rds:CreateDBInstance ... not authorized ... resource ...:subgrp:onlineshop-staging-db-subnets
```

The source-controlled `ManageEphemeralStagingDatabase` statement already
allowed `rds:CreateDBInstance` for the staging DB and snapshots, but omitted
the subnet-group resource that RDS evaluates for this create operation. The
policy now includes only the isolated staging subnet group ARN, and
`release/tests/test_iam.py` verifies both the DB and subnet-group scopes.

After AWS re-authentication, the live inline policy was applied and read back
after each mutation. The first corrected run reached clean RDS bootstrap,
candidate deployment, healthy ALB targets, and teardown. It exposed two more
issues, both fixed in source: the missing read-only network/ELB actions were
added with the least scope supported by the APIs (target-group attributes
require `Resource: "*"`), and CI was starting stale ECS images before
candidate deployment. `resume-staging.sh --defer-services` now keeps ECS at
zero until `ci-deploy-staging.sh` installs the candidate (PR #39).

The corrected run was [31265257478](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/31265257478), job
`93122636659`. Infrastructure and deployment passed; E2E then identified a
cold Auth lookup that exceeded the gateway's effective timeout and was
misreported as 502. The gateway now uses an explicit annotation-backed
5-second `TimeLimiterRegistry` and unwraps `CompletionException`/`ExecutionException`
so genuine timeouts retain 504 classification. The merged-main verification
run [31267620402](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/31267620402),
job `93128495549`, passed all 3 cloud E2E tests, including invalid-token → 401,
and completed staging teardown.

The same run's candidate-evidence job then failed before emitting its bundle
because the runner had not installed the release contract's pinned Python
requirements (`referencing` was missing). The workflow now sets up Python and
installs `release/requirements.txt` before producer-set validation. The first
merged-main rerun also exposed an unresolvable copied `actions/setup-python`
SHA, which was replaced with the verified v5 commit SHA. Final merged-main
verification [31271458491](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/31271458491)
passed staging resume, ordered deployment, all cloud E2E tests, teardown, and
candidate evidence (job `93142263971`), including dependency installation and
artifact emission.

### 3.5 Existing production environment hardening ✅ (offline; live checks deferred)

Implemented the offline half of subphase 3.5 — the hardened production target
and its tooling, without any AWS mutation, GitHub mutation, or lifecycle
start/stop:

- **Task-definition/service-config hardening** — `release_contract.ecs_config`
  validates: Fargate `awsvpc` + the CPU/memory matrix; digest-pinned images
  (never a floating tag); `versionConsistency=enabled`; container health
  checks; `awslogs`; named Service Connect port mappings; positive
  `stopTimeout`; an execution role; full-ARN `secrets[].valueFrom` with no
  secret repeated in `environment`/`command`; and service-level
  circuit-breaker enable+rollback, `minimumHealthyPercent=100`,
  `maximumPercent=200`, capacity-provider strategy, and Service Connect port
  names. CLI wrapper: `release/bin/validate-task-definition.sh`.
- **Sanitized task-definition transforms** — `release_contract.sanitize` +
  `release/bin/sanitize-task-definition.sh` copy a task definition, replace
  only the intended container image with `@sha256:<digest>`, and prove the diff
  is image-only (no container added/removed, no unrelated drift) while every
  secret stays in `secrets[].valueFrom` with a full ARN and never appears as
  plaintext. This is the subphase 3.4 "register by copying and replacing only
  the image" contract.
- **Read-only inventory + separation** — `scripts/inventory-production.sh`
  compares every explicit non-secret identifier in
  `scripts/config/production.env` (VPC, subnets, SGs, cluster, services,
  namespace, ALB/TG, RDS, secrets, log groups, execution role, ECR, frontend
  S3/CloudFront) against live observed state and fails closed on drift.
  `scripts/verify-production-staging-separation.sh` proves production and
  staging share no VPC, cluster, RDS, SGs, namespace, secrets, services, or
  target group — against both the configs and live identifier + topology
  state (`release_contract.environments`: `separation` + `topology`). Shared
  helpers in `scripts/lib/identifiers.sh`.
- **Frontend S3 REST origin + CloudFront OAC** —
  `release_contract.frontend_hosting` + `scripts/verify-frontend-oac.sh`
  (read-only fail-closed) + `scripts/migrate-frontend-oac.sh` (`--dry-run`/
  `--apply` with a read-back after every mutation). **Not applied live.**
- **CloudTrail coverage** — `release_contract.cloudtrail` +
  `scripts/verify-cloudtrail-coverage.sh` (read-only) audit management-event,
  multi-region, logging, and delivery coverage for ECS/ECR/S3/CloudFront/IAM/
  Secrets Manager.
- **Lifecycle environment guards (pre-existing unsafe assumption fixed)** — the
  staging-only DB helpers (`lc_create_clean_staging_db`, `lc_delete_staging_db`,
  `lc_staging_db_status`, `lc_staging_master_secret_arn`) previously relied on
  `lc_require_environment staging` returning 1, which did NOT stop execution
  when a helper was invoked in a conditional (e.g. `if lc_create_clean_staging_db`)
  context — the function continued and could reach a production mutation. They
  now `lc_require_environment staging || return 1` (fail fast). The gate proves
  production entry points never invoke them and that they refuse under
  `LC_ENVIRONMENT=production`.
- **Config** — `scripts/config/production.env` now carries the explicit
  non-secret namespace (`onlineshop.local`), execution role ARN, log groups,
  secret names, ECR repositories, frontend bucket, and CloudFront distribution;
  `staging.env` carries the staging namespace (`staging.onlineshop.local`),
  log groups, and secret names.
- **Decisions doc** — `explanations/PRODUCTION-HARDENING-DECISIONS.md` records
  the Fargate Spot + desired-1 non-HA tradeoff, the backup limitation and the
  Flyway/migration gate (no schema-changing production release), the frontend
  OAC target + constraint record, and the audit-correlation requirement.
- **Tests** — new Python suites (`test_ecs_config.py`, `test_sanitize.py`,
  `test_frontend_hosting.py`, `test_cloudtrail.py`, `test_environments.py`;
  235 total across all suites) plus `tests/scripts/production_hardening_test.sh`
  (11 sections: Python tests, task-definition/service fixtures, sanitize diff,
  CLI error paths, stateful AWS-stub runs of inventory/separation/OAC/
  CloudTrail, the OAC migration apply with read-back and fail-closed drift,
  lifecycle guards, and a profile/region + read-back + no-secrets static scan
  with ruff/shellcheck/git diff --check).

**Verification:** `tests/scripts/production_hardening_test.sh` passes, and the
existing `release_contract_test.sh`, `candidate_evidence_test.sh`,
`ecr_release_tagging_test.sh`, and `lifecycle_test.sh` gates all still pass
(235 Python tests, ruff + shellcheck clean, `git diff --check` clean). No AWS
mutations and no staging/production lifecycle commands were run.

**Deferred to the consolidated verification pass (NOT claimed here):** the real
production inventory read-back, the real production/staging separation
read-back, the live frontend S3 REST + OAC migration, real CloudTrail
read-back, live service/task-definition verification, and security-group/IAM
tightening mutations. The live plan checkboxes in
`03_RELEASE_TRACEABILITY.md` are annotated accordingly.

### Independent 3.5 review corrections (applied after the above)

A fresh independent review of the 3.5 implementation (offline; no AWS, GitHub,
or lifecycle actions) applied the following corrections. All four offline gates
were re-verified green afterwards (now 248 Python tests):

- **Documentation honesty — service AGENTS files no longer overclaim.** The
  `Auth/AGENTS.md`, `Items/AGENTS.md`, and `api-gateway/AGENTS.md` Pass 3.5
  sections claimed the production task definitions/services "are" hardened and
  "require" the circuit-breaker settings as if they were live state. No live
  task-definition/service mutation has occurred, so they now state the
  hardening **contract** ("must be", enforced by the validators before any
  registration/promotion) and explicitly say it is **not** a claim about the
  current live production state. `frontend/AGENTS.md` likewise clarifies the
  current delivery is still the v1 website origin.
- **Mandatory profile/region can no longer be overridden.** `lc_init` now
  refuses to run unless `LC_PROFILE=dpm-profile` and `LC_REGION=eu-north-1`;
  `scripts/lib/identifiers.sh` refuses to build an `aws` call otherwise; the
  gate asserts both `scripts/config/{production,staging}.env` files carry
  exactly those values. The static scan was tightened so an identifier like
  `lc_require_canonical_aws` does not false-positive.
- **API read errors are no longer disguised as missing resources.** `id_value`
  and the topology `value()` helper now classify a failed AWS read as `error`
  (printing the real error) and only a genuine not-found as `missing`;
  `release_contract.environments` emits `OBSERVED_READ_ERROR` /
  `TOPO_READ_ERROR` for `error` markers. The gate's AWS stub distinguishes
  NotFound from AccessDenied and proves the inventory fails closed on an API
  read error with the honest code (not fake drift).
- **Inventory completeness.** The identifier schema now also verifies the
  execution role (`iam get-role`), the ECR repositories
  (`ecr describe-repositories`), and RDS non-public accessibility
  (`DBInstances[0].PubliclyAccessible`, rejected as `DB_PUBLIC_ACCESSIBLE`).
  Execution role + ECR remain intentionally excluded from prod/staging
  separation (shared infrastructure).
- **OAC migration is outage-safe and waits for deployment.** `--apply` now runs
  a no-lockout precondition gate (`release_contract.frontend_hosting
  preconditions`): the current bucket policy must already grant public read or
  the CloudFront OAC, or the run refuses to start before any mutation. After
  the distribution update it waits (bounded, default 20×15s) for the
  asynchronous CloudFront deployment to reach `Deployed` before tightening the
  bucket policy. The plan now documents steps 0 (preconditions) and 3
  (deployment wait).
- **CloudTrail honesty.** `release_contract.cloudtrail` now proves delivery by
  a configured target **plus** a confirmed `LatestDeliveryTime` with no
  delivery error (a bare target is not proof); the module and scripts state
  that management-event selectors cover *all* control-plane APIs (not a
  per-service enumeration) and that request-ID capture remains a promotion-
  phase behaviour, not part of this read-only audit.
- **Execution-role vs task-role duties.** `validate_task_definition` rejects a
  `taskRoleArn` equal to the `executionRoleArn` (`ROLE_NOT_DISTINCT`).
- **The decision layer's failures are loud.** The inventory/separation/OAC/
  CloudTrail scripts now fail loudly (with the real stderr) when the Python
  decision layer produces no valid JSON, instead of swallowing it with `||
  true` and reporting confusing empty drift.
- **Lifecycle guard tests now prove no AWS call.** The gate clears the stub's
  call log before each guard-failure assertion and asserts no `rds
  create/delete/modify` call is issued after a staging-only DB helper's
  environment guard fails — including in `if helper; then ...` conditional
  contexts (the original unsafe assumption) — and covers
  `lc_staging_master_secret_arn` too.

### 3.7 Traceability queries and operator evidence ✅ (offline; live checks deferred)

Implemented the offline half of subphase 3.7 — the read-only release
traceability lookups and consistency audit — without any AWS mutation, GitHub
mutation, or lifecycle start/stop:

- **Decision layer** — `release_contract.traceability` (pure, fixture-tested)
  answers all four queries in both directions plus the consistency audit:
  `lookup_by_sha` (commit SHA → candidate run, per-backend ECR digests, any
  official releases), `lookup_by_version` (version → source SHA, components,
  evidence, SBOMs, artifacts + live ECR/frontend cross-check),
  `lookup_running` (task-definition ARNs, **running** digests from
  `tasks[].containers[].imageDigest`, release identity + approver + deployment
  run, frontend identity from the deployed immutable `release.json` marker;
  paused production is reported honestly with selected task-definition digests
  and last verified deployment evidence, never a fabricated running digest),
  `lookup_by_digest` (digest → ECR tags, OCI revision, candidate run, release
  identity), and `audit_consistency` (manifest ↔ ECR `sha-*`/`release-*` tags ↔
  ECS running digest ↔ frontend checksum; read-only drift reporting). Output is
  machine-readable JSON; `NOT_FOUND`/`AMBIGUOUS_*`/`*_MISMATCH`/
  `OBSERVED_READ_ERROR` exit non-zero, and a failed live AWS read is recorded
  as an `error` marker and fails closed (never disguised as drift/missing).
- **Operator CLI** — `release/bin/trace.sh` (`commit`/`release`/`running`/
  `digest`/`audit`) gathers the observed state with strictly read-only AWS
  reads (ECR `describe-images`, ECS `list-tasks`/`describe-tasks`/
  `describe-services`/`describe-task-definition`, S3 `get-object`), the
  manifest index from `--index` or the GitHub Releases of `$GITHUB_REPOSITORY`
  (read-only `gh api`), enforces the mandatory non-overridable
  `--profile dpm-profile --region eu-north-1` and an `sts get-caller-identity`
  preflight (account `799111666795`), and prints JSON with an optional
  `--human` view. `TRACE_KEEP_TMP=1` keeps the scratch directory for operator
  debugging.
- **Fixtures** — `release/fixtures/traceability/`: `index.json` (official
  1.2.1 + 1.1.0 manifests), `observed-ok.json` (consistent ECR/ECS/frontend),
  `observed-paused.json`, and `observed-drift-{ecr,ecs,frontend}.json`.
- **Tests** — `release/tests/test_traceability.py` (61 tests; 309 total across
  all suites) plus `tests/scripts/release_traceability_test.sh` (9 sections:
  Python suites; all four lookups + audit against fixtures; paused state; drift
  fixtures fail closed with their intended codes; input handling + mandatory
  profile/region refusal; a stateful AWS-stub run of the LIVE gather path
  proving the identity preflight, the exact read-only ECR/ECS/S3 calls, and no
  mutating call; identity-preflight failure on a wrong account; the read-only
  GitHub Releases index auto-fetch via a `gh` stub; mandatory profile/region +
  no-secrets static scan; ruff/shellcheck/git diff --check).
- **Independent review hardening** (applied on top of the initial 3.7 work):
  newest-official selection and audit ordering are now numeric-version based
  (index-order independent — `compare_semver`'s sign cannot order more than two
  versions); mixed/incomplete running digest sets fail closed
  (`RUNNING_MIXED_DIGESTS`/`RUNNING_DIGEST_INCOMPLETE`) instead of
  last-writer-wins fabrication; `trace.sh release` verifies the immutable
  per-release prefix marker (`FRONTEND_PREFIX_MARKER_*`); `trace.sh commit`
  fails closed on `sha-*` digest mismatch (`ECR_SHA_DIGEST_MISMATCH`) and
  candidate-run conflicts (`CANDIDATE_RUN_CONFLICT`); the digest lookup
  attributes the OCI revision to the release manifest
  (`ociRevisionSource: "release-manifest"`, never a claimed label read); a
  configured service omitted by `describe-services` and malformed frontend
  markers fail closed as `OBSERVED_READ_ERROR`; prefix-marker S3 keys are
  derived from each manifest's `releasePrefix`/`versionMarker`; the GitHub
  index fetch selects exactly `release-manifest.json`; the CLI rejects invalid
  JSON with exit 2 instead of a traceback.

**Verification:** `tests/scripts/release_traceability_test.sh` passes, and the
existing `release_contract_test.sh`, `candidate_evidence_test.sh`,
`ecr_release_tagging_test.sh`, `production_hardening_test.sh`, and
`lifecycle_test.sh` gates all still pass (309 Python tests, ruff + shellcheck
clean, `git diff --check` clean). No AWS CLI commands and no staging/production
lifecycle commands were run.

**Deferred to the consolidated verification pass (NOT claimed here):** the
read-only live smoke test of all four lookups + the audit against real
production AWS state and real GitHub Releases. The live plan checkboxes in
`03_RELEASE_TRACEABILITY.md` are annotated accordingly.
