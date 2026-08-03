# Pass 1 — MVP: Running on AWS

**Goal:** Get the OnlineShop accessible on AWS with the shortest path possible. No polish, no automation beyond the bare minimum. Prove the system works in the cloud.

**Exit criteria:** All four components (Auth, Items, API Gateway, Frontend) are reachable on AWS. A developer can manually trigger a deployment.

---

## Tasks

### 1.1 AWS Account & OIDC Foundation

- [x] Create (or confirm) AWS account under the Free Tier
- [x] Select initial AWS region (cheapest suitable EU region — likely `eu-north-1` Stockholm or `eu-central-1` Frankfurt)
- [x] Set up GitHub → AWS OIDC trust (no long-lived access keys — req 01 §12)


### 1.2 Container Registry (ECR)

- [x] Create ECR repositories: `onlineshop-auth`, `onlineshop-items`, `onlineshop-api-gateway` (fixed naming from initial mistake)
- [x] Confirm each service Dockerfile builds locally and produces a working image
- [x] Push images manually (or via a trivial GH Actions workflow) with a SHA-based tag

### 1.3 Minimal GitHub Actions — Build & Push

- [x] **1.3.1 IAM role for GitHub Actions → AWS (OIDC)**
  - Created IAM role `github-actions-onlineshop` (arn:aws:iam::799111666795:role/github-actions-onlineshop)
  - Applied trust policy: OIDC from configured subject `repo:Djimi@8793507/OnlineShop-full-stack@1097550215` on `main` and `feature/*` refs with `sts.amazonaws.com` audience
  - Attached inline policy `ecr-push-pull` for ECR operations
  - Additional permissions (ECS, S3, CloudFront) to be added in steps 1.5/1.6
- [x] **1.3.2 Store secrets in AWS Secrets Manager** — Not needed for Pass 1 MVP (no build-time secrets; runtime secrets come in 1.4)
- [x] **1.3.3 Create workflow YAML** (`.github/workflows/build-and-deploy.yml`):
    - Triggers: pushes to `feature/**` and `main`, pull requests to `main`, and `workflow_dispatch`
    - Steps per service: checkout → tests → OIDC auth → ECR login → Docker build → push with `sha-<FULL_SHA>` tag
    - Items also builds `common` first (dependency)
    - Pull-request builds do not request AWS credentials or push images
- [x] **1.3.4 Enable caching** (added to workflow):
  - Maven: `actions/cache@v4` with service-specific keys based on `pom.xml` hashes, `~/.m2/repository` path
  - Docker: `docker/build-push-action` with `type=gha` cache backend (`mode=max`)

### 1.4 Database Layer

- [x] Decide: RDS Free Tier PostgreSQL vs. containerized PostgreSQL on ECS
  - RDS Free Tier (`db.t3.micro` / `db.t4g.micro`, 20 GB) is free for 12 months — **preferred for MVP**
  - Containerized PG is cheaper long-term but no managed backups
- [x] Provision PostgreSQL instance(s) for Auth and Items
- [x] Apply init-db scripts (`01-schema.sql`, `02-seed-data.sql`) to each database
- [x] Store DB credentials in Secrets Manager; wire ECS task definitions to fetch them

### 1.5 ECS Cluster & Services (Fargate)

- [x] Create ECS cluster
- [x] Create task definitions for Auth, Items, API Gateway
  - Fargate Spot where possible (cost savings)
  - Minimal resource allocation (0.25 vCPU / 0.5 GB per task is a good starting point)
- [x] Create ECS services (desired count = 1 per service)
- [x] Set up a single ALB (Application Load Balancer) with path-based routing:
  - `/api/auth/**` → Auth service
  - `/api/items/**` → Items service
  - `/api/**` → API Gateway (or route everything through Gateway)
- [x] Configure security groups: ALB ↔ ECS tasks ↔ RDS
- [x] Verify backend health checks pass

### 1.6 Frontend — S3 + CloudFront

- [x] Create S3 bucket for frontend static assets
- [x] Build frontend (`npm run build`) and upload `dist/` to S3
- [x] Create CloudFront distribution pointing to S3 origin
- [x] Configure CloudFront to forward `/auth/*` and `/items/*` requests to the ALB (origin for backend)
- [x] Verify frontend loads and can communicate with backend APIs

### 1.7 Smoke Test

- [x] Manually verify: frontend loads, login works, items list renders
- [x] Document the public URL(s) in `executed/INFO.md` and `WHAT-WAS-DONE.md`

---

## Cost Estimate (MVP)

| Resource | Estimated Monthly Cost |
|---|---|
| ECS Fargate (3 tasks, minimal) | ~$1–3 (Spot pricing) |
| ALB | ~$0.50 (minimal traffic) |
| RDS Free Tier | $0 (12 months) |
| ECR | ~$0.10 |
| S3 + CloudFront | ~$0.10 (minimal traffic) |
| **Total** | **~$2–4** |

> If the ALB pushes cost above $5, consider replacing it with direct ECS Service Connect or a cheaper routing approach (evaluated in Pass 2).

---

## Out of Scope for Pass 1

- Automated CI triggers on push/PR (Pass 2)
- Selective builds / change detection (Pass 2)
- Test gates in CI (Pass 2)
- Branch protection enforcement (Pass 2)
- Staging environment (Pass 2)
- Release identity model (Pass 3)
- Notifications, dashboards, runbooks (Pass 4)
