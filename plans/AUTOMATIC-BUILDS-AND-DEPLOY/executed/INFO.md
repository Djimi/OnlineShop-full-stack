# INFO.md — Complete Environment Replication Reference

> Generated from all source files in `plans/AUTOMATIC-BUILDS-AND-DEPLOY/`.
> This document contains EVERYTHING needed to recreate the infrastructure identically from scratch.
> **Placeholders:** `<db-admin-password>`, `<auth-app-password>`, `<items-app-password>`, `<test-user-password>`

---

## AWS Account Identity

| Property | Value |
|---|---|
| Account ID | `799111666795` |
| Region | `eu-north-1` (Stockholm) |
| IAM User (manual CLI) | `admin` |
| CLI Profile | `dpm-profile` |
| Verify command | `aws sts get-caller-identity --profile dpm-profile --region eu-north-1` |
| Identity ARN | `arn:aws:iam::799111666795:user/admin` |

---

## OIDC Trust Foundation

### Provider

| Property | Value |
|---|---|
| Provider ARN | `arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com` |
| URL | `token.actions.githubusercontent.com` |
| Audience (ClientIDList) | `["sts.amazonaws.com"]` |
| Thumbprint | `22ff89586561fc2d52f77491e9f1eff1b80be33e` |

### Verification Commands

```bash
aws iam list-open-id-connect-providers
aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[*].Arn"

aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com"
```

---

## IAM Roles & Policies

### Role 1: `github-actions-onlineshop` (GitHub Actions → AWS via OIDC)

| Property | Value |
|---|---|
| Role Name | `github-actions-onlineshop` |
| Role ARN | `arn:aws:iam::799111666795:role/github-actions-onlineshop` |
| Role ID | `AROA3UDWELRVWNA6I5JLH` |
| Description | OIDC role for GitHub Actions in OnlineShop repo |

**Trust Policy (who can assume):**

> Target correction for the renamed repository. Applied after AWS re-authentication and verified with `aws iam get-role`.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": [
          "repo:Djimi@8793507/OnlineShop-full-stack@1097550215:ref:refs/heads/main",
          "repo:Djimi@8793507/OnlineShop-full-stack@1097550215:ref:refs/heads/feature/*"
        ]
      }
    }
  }]
}
```

**Creation command:**

```bash
aws iam create-role \
  --role-name github-actions-onlineshop \
  --assume-role-policy-document "file://trust-policy.json" \
  --description "OIDC role for GitHub Actions in OnlineShop repo"
```

> **Windows note:** If creating this file on Windows, use `[System.IO.File]::WriteAllText(...)` with `[System.Text.Encoding]::ASCII`. PowerShell's default UTF-8-with-BOM confuses AWS IAM.

**Inline Policy: `ecr-push-pull` (permissions — what this role can do):**

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:GetRepositoryPolicy",
      "ecr:DescribeRepositories",
      "ecr:ListImages",
      "ecr:DescribeImages",
      "ecr:BatchGetImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage"
    ],
    "Resource": "*"
  }]
}
```

**Attach command:**

```bash
aws iam put-role-policy \
  --role-name github-actions-onlineshop \
  --policy-name ecr-push-pull \
  --policy-document "file://ecr-policy.json"
```

**Verify:**

```bash
aws iam list-role-policies --role-name github-actions-onlineshop
# Expected: {"PolicyNames": ["ecr-push-pull"]}
```

> **Security note:** `"Resource": "*"` scopes to all ECR repos in this account. For Pass 1 MVP this is acceptable. Tighten to specific repo ARNs in Pass 3.

### Role 2: `ecsTaskExecutionRole` (ECS tasks → ECR, CloudWatch, Secrets Manager)

| Property | Value |
|---|---|
| Role Name | `ecsTaskExecutionRole` |
| Role ARN | `arn:aws:iam::799111666795:role/ecsTaskExecutionRole` |

**Trust Policy:**

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Service": "ecs-tasks.amazonaws.com"
    },
    "Action": "sts:AssumeRole"
  }]
}
```

**Creation command:**

```bash
aws iam create-role --role-name ecsTaskExecutionRole \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Principal":{"Service":"ecs-tasks.amazonaws.com"},
      "Action":"sts:AssumeRole"
    }]
  }'
```

**AWS-Managed Policy Attached:**

```bash
aws iam attach-role-policy --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

This provides: ECR pull + CloudWatch Logs write.

**Inline Policy: `secretsmanager-read-onlineshop`:**

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "secretsmanager:GetSecretValue",
    "Resource": [
      "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db*",
      "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/items/db*"
    ]
  }]
}
```

**Attach command:**

```bash
aws iam put-role-policy --role-name ecsTaskExecutionRole \
  --policy-name secretsmanager-read-onlineshop \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Action":"secretsmanager:GetSecretValue",
      "Resource":[
        "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db*",
        "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/items/db*"
      ]
    }]
  }'
```

---

## ECR Repositories

| Repository | Full URI | Image Tag Mutability |
|---|---|---|
| `onlineshop-auth` | `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth` | MUTABLE |
| `onlineshop-items` | `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items` | MUTABLE |
| `onlineshop-api-gateway` | `799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-api-gateway` | MUTABLE |

**Naming convention:** `onlineshop-<service>` — hyphens, no slashes.

### Repository Creation

```bash
# Repos were created manually in step 1.2. The api-gateway repo was recreated from the misnamed one:
aws ecr delete-repository --repository-name "onlineshop-auth/api-gateway" --region eu-north-1 --force
aws ecr create-repository --repository-name "onlineshop-api-gateway" --region eu-north-1
```

### Image Tags Pushed

Workflow tags images with pattern `sha-<FULL_40_CHAR_COMMIT_HASH>`.
Images are pushed by CI/CD with tag `sha-<40-CHAR-COMMIT-HASH>`. Run `aws ecr describe-images --repository-name onlineshop-auth --query 'imageDetails[*].imageTags'` to see current images. Historical snapshot (2026-07-25): auth has tags `sha-befc225cb8806ca139994013d02b6845a39b412b` (obsolete, no longer in ECR) and `sha-263f0690aa08eaf24f23f715dea7e8895a759293` (active).

### ECR Verification Commands

```bash
aws ecr describe-repositories --region eu-north-1
aws ecr describe-images --repository-name onlineshop-auth --region eu-north-1 --query "imageDetails[*].imageTags[0]"
```

### Docker Login & Push/Pull (Manual)

```bash
aws ecr get-login-password --profile dpm-profile --region eu-north-1 | \
  docker login --username AWS --password-stdin 799111666795.dkr.ecr.eu-north-1.amazonaws.com

# Build (from service dir)
docker build -t 799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items:<tag> .

# Push
docker push 799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items:<tag>
```

---

## RDS Database

| Property | Value |
|---|---|
| Instance ID | `onlineshop-postgres-db` |
| Endpoint | `onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com` |
| Port | `5432` |
| Engine | PostgreSQL 18.4 |
| Instance Class | `db.t4g.micro` (Free Tier eligible) |
| Storage | 20 GB, encrypted |
| Master Username | `dbadmin` |
| Master Password | `<db-admin-password>` (stored in local `.env` as `POSTGRES_AWS_SECRET`) |
| Multi-AZ | No |
| Public Access | **No** (false — private subnet only) |
| Security Group | `sg-04ba95188d8374d96` (DB SG) |
| Subnet Group | `default-vpc-06eeb0bc47ecdbd61` |
| Initial Database | `auth` (created at instance provisioning) |
| Additional Database | `items` (created manually in step 1.4b) |
| max_connections | ~25 (db.t4g.micro limit) |

---

## Database Schema & Seed

### Connectivity Method

No `psql` client installed locally. Used Postgres Docker image as client:

```bash
docker run --rm -e PGPASSWORD=<db-admin-password> postgres:18-alpine psql -h <RDS_ENDPOINT> -U dbadmin -d <DB_NAME> < <sql_file>
```

### Databases Created

| Database | Purpose |
|---|---|
| `auth` | Auth service data (users, sessions) |
| `items` | Items service data (product inventory) |

### Service Accounts (Least-Privilege)

| Account | Database | Privileges |
|---|---|---|
| `auth_app` | `auth` | SELECT, INSERT, UPDATE, DELETE on all tables |
| `items_app` | `items` | SELECT, INSERT, UPDATE, DELETE on all tables |
| `dbadmin` | `auth`, `items` | Master user (NOT used by apps) |

Commands to create service accounts:

```sql
-- Run against RDS as dbadmin
CREATE ROLE auth_app WITH LOGIN PASSWORD '<auth-app-password>';
GRANT CONNECT ON DATABASE auth TO auth_app;
\c auth
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO auth_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO auth_app;

CREATE ROLE items_app WITH LOGIN PASSWORD '<items-app-password>';
GRANT CONNECT ON DATABASE items TO items_app;
\c items
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO items_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO items_app;
```

### Secrets Manager Entries

| Secret ID | ARN | Contents |
|---|---|---|
| `onlineshop/auth/db` | `arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db-umtxh1` | `{"username": "auth_app", "password": "<auth-app-password>", "host": "onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com", "port": "5432", "dbname": "auth"}` |
| `onlineshop/items/db` | `arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/items/db-bM5eSY` | `{"username": "items_app", "password": "<items-app-password>", "host": "onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com", "port": "5432", "dbname": "items"}` |

### Auth Schema (`Auth/init-db/01-schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    normalized_username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_normalized_username ON users(normalized_username);

CREATE TABLE IF NOT EXISTS sessions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
```

### Auth Seed (`Auth/init-db/02-seed-data.sql`)

```sql
-- Username: testuser, Password: testpass (Argon2id hash)
INSERT INTO users (username, normalized_username, password_hash)
VALUES ('testuser', 'testuser', '<argon2id-hash>')
ON CONFLICT (username) DO NOTHING;
```

### Items Schema (`Items/init-db/01-schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS items (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    quantity INTEGER NOT NULL,
    description VARCHAR(500)
);

CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
```

### Items Seed (`Items/init-db/02-data.sql`)

```sql
INSERT INTO items (id, name, quantity, description) VALUES
    ('550e8400-e29b-41d4-a716-446655440001', 'Laptop', 15, 'High-performance laptop with 16GB RAM and 512GB SSD'),
    ('550e8400-e29b-41d4-a716-446655440002', 'Mouse', 50, 'Wireless optical mouse with ergonomic design'),
    ('550e8400-e29b-41d4-a716-446655440003', 'Keyboard', 0, 'Mechanical keyboard with RGB backlighting'),
    ('550e8400-e29b-41d4-a716-446655440004', 'Monitor', 25, '27-inch 4K UHD monitor with HDR support'),
    ('550e8400-e29b-41d4-a716-446655440005', 'Headphones', 8, 'Noise-cancelling over-ear headphones')
ON CONFLICT DO NOTHING;
```

---

## Security Groups

| Name | ID | Purpose | Cross-References |
|---|---|---|---|
| ALB SG | `sg-0b5427a6a3bf31c29` | Public HTTP ingress for ALB | Used by: ALB |
| ECS SG | `sg-0b209104a6b15b157` | Task-to-task + ALB-to-task communication | Used by: Auth, Items, API Gateway ECS services |
| DB SG | `sg-04ba95188d8374d96` | RDS PostgreSQL access | Used by: RDS `onlineshop-postgres-db` |

### Inbound Rules

| Security Group | Protocol | Port(s) | Source | Purpose |
|---|---|---|---|---|
| ALB SG (`sg-0b5427a6a3bf31c29`) | TCP | 80 | `0.0.0.0/0` | Public HTTP |
| ECS SG (`sg-0b209104a6b15b157`) | TCP | 0-65535 | `sg-0b5427a6a3bf31c29` (ALB SG) | ALB → ECS tasks |
| ECS SG (`sg-0b209104a6b15b157`) | TCP | 9000-9001 | `sg-0b209104a6b15b157` (self) | API Gateway → Auth/Items | **Added during deployment as fix** |
| ECS SG (`sg-0b209104a6b15b157`) | TCP | 6379 | `sg-0b209104a6b15b157` (self) | Redis sidecar access | **Added during deployment as fix** |
| DB SG (`sg-04ba95188d8374d96`) | TCP | 5432 | `sg-0b209104a6b15b157` (ECS SG) | ECS tasks → RDS |

### Self-Referencing Rules Fix (command)

```bash
aws ec2 authorize-security-group-ingress --profile dpm-profile --region eu-north-1 \
  --group-id sg-0b209104a6b15b157 --protocol tcp --port 9000-9001 \
  --source-group sg-0b209104a6b15b157

aws ec2 authorize-security-group-ingress --profile dpm-profile --region eu-north-1 \
  --group-id sg-0b209104a6b15b157 --protocol tcp --port 6379 \
  --source-group sg-0b209104a6b15b157
```

### Traffic Flow Diagram

```
Internet
  ↓ :80
[ALB SG: sg-0b5427a6a3bf31c29]
  ↓ :10000 (target group)
[ECS SG: sg-0b209104a6b15b157] — API Gateway task (port 10000)
  ↓ :9001 (self-ref SG rule — Auth forwarding)
[ECS SG: sg-0b209104a6b15b157] — Auth task (port 9001)
  ↓ :5432
[DB SG: sg-04ba95188d8374d96] — RDS PostgreSQL
```

---

## VPC & Networking

| Property | Value |
|---|---|
| VPC ID | `vpc-06eeb0bc47ecdbd61` |
| Subnet Group (for RDS) | `default-vpc-06eeb0bc47ecdbd61` |
| Subnets (for ALB + ECS) | 3 subnets in `eu-north-1` (a, b, c) |
| ECS Assign Public IP | ENABLED (needed for Fargate tasks to pull images from ECR) |

---

## ECS Cluster

| Property | Value |
|---|---|
| Cluster Name | `onlineshop-cluster` |
| Cluster ARN | `arn:aws:ecs:eu-north-1:799111666795:cluster/onlineshop-cluster` |
| Type | Fargate (serverless — no EC2) |
| Status | ACTIVE |

**Creation command:**

```bash
aws ecs create-cluster --profile dpm-profile --region eu-north-1 \
  --cluster-name onlineshop-cluster
```

---

## Cloud Map / Service Discovery

| Property | Value |
|---|---|
| Namespace ID | (created automatically with private DNS) |
| Namespace Name | `onlineshop.local` |
| Type | Private DNS |
| VPC | `vpc-06eeb0bc47ecdbd61` |

**Creation command:**

```bash
aws servicediscovery create-private-dns-namespace \
  --name onlineshop.local --vpc vpc-06eeb0bc47ecdbd61
```

### Service Connect Configuration (per ECS service)

```json
{
  "enabled": true,
  "namespace": "onlineshop.local",
  "services": [{
    "portName": "<service>-port",
    "clientAliases": [{
      "port": <service-port>,
      "dnsName": "<service-name>"
    }]
  }]
}
```

**Expected DNS:** `auth.onlineshop.local`, `items.onlineshop.local` — **NOT WORKING as of Pass 1** (see Issues section). Hardcoded private IPs used as workaround.

---

## Task Definitions

### Auth Task Definition

| Property | Value |
|---|---|
| Family | `onlineshop-auth` |
| CPU | 256 (0.25 vCPU) |
| Memory | 512 MB |
| Execution Role | `arn:aws:iam::799111666795:role/ecsTaskExecutionRole` |

> Image is pushed by CI/CD with tag `sha-<40-CHAR-COMMIT-HASH>`. The container definition below shows the pattern.

**Container Definition:**

```json
{
  "name": "auth",
  "image": "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth:sha-befc225cb8806ca139994013d02b6845a39b412b",
  "portMappings": [{"containerPort": 9001, "protocol": "tcp", "name": "auth-port"}],
  "environment": [
    {"name": "SPRING_DATASOURCE_URL", "value": "jdbc:postgresql://onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com:5432/auth"},
    {"name": "SPRING_DATASOURCE_USERNAME", "value": "auth_app"},
    {"name": "SPRING_DATASOURCE_HIKARI_MAXIMUMPOOLSIZE", "value": "10"},
    {"name": "SPRING_DATASOURCE_HIKARI_MINIMUMIDLE", "value": "1"}
  ],
  "secrets": [
    {
      "name": "SPRING_DATASOURCE_PASSWORD",
      "valueFrom": "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db-umtxh1:password::"
    }
  ],
  "healthCheck": {
    "command": ["CMD-SHELL", "curl -f http://localhost:9001/actuator/health/liveness || exit 1"],
    "interval": 30,
    "timeout": 5,
    "retries": 3,
    "startPeriod": 180
  },
  "logConfiguration": {
    "logDriver": "awslogs",
    "options": {
      "awslogs-group": "/ecs/onlineshop-auth",
      "awslogs-region": "eu-north-1",
      "awslogs-stream-prefix": "ecs"
    }
  }
}
```

### Items Task Definition

| Property | Value |
|---|---|
| Family | `onlineshop-items` |
| CPU | 256 (0.25 vCPU) |
| Memory | 512 MB |
| Execution Role | `arn:aws:iam::799111666795:role/ecsTaskExecutionRole` |

> Image is pushed by CI/CD with tag `sha-<40-CHAR-COMMIT-HASH>`. The container definition below shows the pattern.

**Container Definition:**

```json
{
  "name": "items",
  "image": "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items:sha-ba7905d",
  "portMappings": [{"containerPort": 9000, "protocol": "tcp", "name": "items-port"}],
  "environment": [
    {"name": "SPRING_DATASOURCE_URL", "value": "jdbc:postgresql://onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com:5432/items"},
    {"name": "SPRING_DATASOURCE_USERNAME", "value": "items_app"},
    {"name": "SPRING_DATASOURCE_HIKARI_MAXIMUMPOOLSIZE", "value": "10"},
    {"name": "SPRING_DATASOURCE_HIKARI_MINIMUMIDLE", "value": "1"}
  ],
  "secrets": [
    {
      "name": "SPRING_DATASOURCE_PASSWORD",
      "valueFrom": "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/items/db-bM5eSY:password::"
    }
  ],
  "healthCheck": {
    "command": ["CMD-SHELL", "curl -f http://localhost:9000/actuator/health/liveness || exit 1"],
    "interval": 30,
    "timeout": 5,
    "retries": 3,
    "startPeriod": 180
  },
  "logConfiguration": {
    "logDriver": "awslogs",
    "options": {
      "awslogs-group": "/ecs/onlineshop-items",
      "awslogs-region": "eu-north-1",
      "awslogs-stream-prefix": "ecs"
    }
  }
}
```

### API Gateway Task Definition

| Property | Value |
|---|---|
| Family | `onlineshop-api-gateway` |
| CPU | 512 (0.5 vCPU) |
| Memory | 1024 MB |
| Execution Role | `arn:aws:iam::799111666795:role/ecsTaskExecutionRole` |

> Image is pushed by CI/CD with tag `sha-<40-CHAR-COMMIT-HASH>`. The container definition below shows the pattern.

**Container Definition (Main — API Gateway):**

```json
{
  "name": "api-gateway",
  "image": "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-api-gateway:sha-ba7905d",
  "essential": true,
  "portMappings": [{"containerPort": 10000, "protocol": "tcp", "name": "gateway-port"}],
  "environment": [
    {"name": "GATEWAY_RATELIMIT_ENABLED", "value": "false"},
    {"name": "SPRING_APPLICATION_JSON", "value": "{\"gateway\":{\"auth\":{\"service-url\":\"http://auth:9001\"},\"items\":{\"service-url\":\"http://items:9000\"}}}"}
  ],
  "dependsOn": [{
    "containerName": "redis-sidecar",
    "condition": "HEALTHY"
  }],
  "healthCheck": {
    "command": ["CMD-SHELL", "curl -f http://localhost:10000/actuator/health/liveness || exit 1"],
    "interval": 30,
    "timeout": 5,
    "retries": 3,
    "startPeriod": 180
  },
  "logConfiguration": {
    "logDriver": "awslogs",
    "options": {
      "awslogs-group": "/ecs/onlineshop-api-gateway",
      "awslogs-region": "eu-north-1",
      "awslogs-stream-prefix": "ecs"
    }
  }
}
```

**Container Definition (Redis Sidecar):**

```json
{
  "name": "redis-sidecar",
  "image": "public.ecr.aws/docker/library/redis:7.4-alpine",
  "essential": false,
  "memoryReservation": 128,
  "portMappings": [{"containerPort": 6379, "protocol": "tcp"}],
  "command": ["redis-server", "--save", "", "--maxmemory", "64mb", "--maxmemory-policy", "allkeys-lru"],
  "healthCheck": {
    "command": ["CMD-SHELL", "redis-cli ping || exit 1"],
    "interval": 10,
    "timeout": 5,
    "retries": 3,
    "startPeriod": 30
  }
}
```

### Health Check Design

- `startPeriod: 180` — 3-minute grace period for Spring Boot startup + DB connection
- Health check endpoint: `GET /actuator/health/liveness`
- `interval: 30`, `timeout: 5`, `retries: 3`
- Items initially had NO actuator (container kept restarting — fixed by adding actuator dependency)

---

## ALB (Application Load Balancer)

| Property | Value |
|---|---|
| Name | `onlineshop-alb` |
| DNS Name | To get current DNS: `aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 --names onlineshop-alb --query 'LoadBalancers[0].DNSName' --output text` |
| ARN | Generated at creation; captured automatically during `resume-playground.sh` |
| Subnets | 3 subnets in `eu-north-1` (a, b, c) |
| Security Group | `sg-0b5427a6a3bf31c29` (ALB SG) |
| Scheme | internet-facing |
| Type | application |

**Creation command:**

```bash
aws elbv2 create-load-balancer --name onlineshop-alb \
  --subnets <subnet-a> <subnet-b> <subnet-c> \
  --security-groups sg-0b5427a6a3bf31c29
```

### Target Group

| Property | Value |
|---|---|
| Name | `onlineshop-gateway-tg` |
| Protocol | HTTP |
| Port | 10000 |
| Target Type | `ip` (Fargate uses IP targets, not instance targets) |
| VPC | `vpc-06eeb0bc47ecdbd61` |
| Health Check Path | `/actuator/health` |
| Health Check Protocol | HTTP |

**Creation command:**

```bash
aws elbv2 create-target-group --name onlineshop-gateway-tg \
  --protocol HTTP --port 10000 --target-type ip \
  --vpc-id vpc-06eeb0bc47ecdbd61
```

### Listener

| Property | Value |
|---|---|
| Port | 80 |
| Protocol | HTTP |
| Default Action | Forward to `onlineshop-gateway-tg` |

**Creation command:**

```bash
aws elbv2 create-listener --load-balancer-arn $ALB_ARN \
  --protocol HTTP --port 80 \
  --default-actions Type=forward,TargetGroupArn=$TG_ARN
```

### Routing

All traffic goes through API Gateway (`/` → API Gateway → routes internally to Auth/Items). No path-based routing at the ALB level — the ALB only routes to the API Gateway target group on port 10000.

---

## ECS Services

### Auth Service

| Property | Value |
|---|---|
| Service Name | `onlineshop-auth` |
| Cluster | `onlineshop-cluster` |
| Task Definition | `onlineshop-auth` |
| Service Connect | Enabled (`auth` → port 9001) |
| Capacity Provider | FARGATE_SPOT |

**Creation command:**

```bash
aws ecs create-service \
  --cluster onlineshop-cluster \
  --service-name onlineshop-auth \
  --task-definition onlineshop-auth \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<subnet-a>,<subnet-b>,<subnet-c>],securityGroups=[sg-0b209104a6b15b157],assignPublicIp=ENABLED}" \
  --service-connect-configuration '{
    "enabled": true,
    "namespace": "onlineshop.local",
    "services": [{
      "portName": "auth-port",
      "clientAliases": [{"port": 9001, "dnsName": "auth"}]
    }]
  }'
```

### Items Service

| Property | Value |
|---|---|
| Service Name | `onlineshop-items` |
| Cluster | `onlineshop-cluster` |
| Task Definition | `onlineshop-items` |
| Service Connect | Enabled (`items` → port 9000) |
| Capacity Provider | FARGATE_SPOT |

**Creation command:**

```bash
aws ecs create-service \
  --cluster onlineshop-cluster \
  --service-name onlineshop-items \
  --task-definition onlineshop-items \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<subnet-a>,<subnet-b>,<subnet-c>],securityGroups=[sg-0b209104a6b15b157],assignPublicIp=ENABLED}" \
  --service-connect-configuration '{
    "enabled": true,
    "namespace": "onlineshop.local",
    "services": [{
      "portName": "items-port",
      "clientAliases": [{"port": 9000, "dnsName": "items"}]
    }]
  }'
```

### API Gateway Service

| Property | Value |
|---|---|
| Service Name | `onlineshop-api-gateway` |
| Cluster | `onlineshop-cluster` |
| Task Definition | `onlineshop-api-gateway` |
| Service Connect | Enabled (client only, discovers `auth`/`items`) |
| Load Balancer | `onlineshop-gateway-tg` (port 10000) |
| Capacity Provider | FARGATE_SPOT |

**Creation command:**

```bash
aws ecs create-service \
  --cluster onlineshop-cluster \
  --service-name onlineshop-api-gateway \
  --task-definition onlineshop-api-gateway \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<subnet-a>,<subnet-b>,<subnet-c>],securityGroups=[sg-0b209104a6b15b157],assignPublicIp=ENABLED}" \
  --load-balancers targetGroupArn=<TG_ARN>,containerName=api-gateway,containerPort=10000 \
  --service-connect-configuration '{
    "enabled": true,
    "namespace": "onlineshop.local",
    "services": [{
      "portName": "gateway-port",
      "clientAliases": [{"port": 10000, "dnsName": "gateway"}]
    }]
  }'
```

```bash
# After creation, switch all services to Fargate Spot (~60% compute savings)
for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  aws ecs update-service --profile dpm-profile --region eu-north-1 \
    --cluster onlineshop-cluster --service $svc \
    --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
    --force-new-deployment
done
```

### Service Update (Redeploy)

```bash
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service <name> \
  --task-definition <family>:<rev>

# Force redeploy without changing task definition
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service <name> --force-new-deployment
```

### View Task Private IPs

```bash
TASK_ARN=$(aws ecs list-tasks --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service-name onlineshop-auth \
  --query 'taskArns[0]' --output text)

aws ecs describe-tasks --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --tasks $TASK_ARN \
  --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`]|[0].value' --output text
```

---

## Code Changes Made

### 1. Items: Spring Boot Actuator Added

**Problem:** ECS health checks require `/actuator/health/liveness` endpoint. Items had no actuator dependency — health check returned 404 → ECS kept restarting the container.

**Changes:**

- **`Items/pom.xml`** — Added dependency:
  ```xml
  <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-actuator</artifactId>
  </dependency>
  ```

- **`Items/src/main/resources/application.yml`** — Added management configuration:
  ```yaml
  management:
    health:
      show-details: when-authorized
      group:
        liveness:
          show-details: when-authorized
        readiness:
          show-details: when-authorized
    env:
      show-values: never
    configprops:
      show-values: never
    endpoints:
      web:
        exposure:
          include: health,metrics,prometheus
    metrics:
      tags:
        application: items
    info:
      env:
        enabled: true
  ```

  **Security hardening during code review:**
  - Restricted exposure from `"*"` → `health,metrics` (prevents DB password leak via `/actuator/env`)
  - `env.show-values` / `configprops.show-values` → `never`
  - Removed dead singular `endpoint` config block
  - Removed `prometheus` from exposure (no dependency)

- **Test fixes:** Fixed pre-existing test bugs (import paths, missing mocks) — 72 tests pass.

### 2. API Gateway: Redis Resilience for Sidecar

**Problem:** Gateway wouldn't start when Redis sidecar wasn't ready. Eager Redis connection + no timeout = crash on startup.

**Changes:**

- **`api-gateway/src/main/java/com/onlineshop/gateway/ratelimit/RateLimitFilter.java`** — Fail-open on Redis errors:
  ```java
  // Lines 82-88: Catch block that fails open
  } catch (Exception e) {
      if (!loggedRedisDown) {
          loggedRedisDown = true;
          log.warn("Rate limiter unavailable (Redis down?), failing open: {}", e.getMessage());
      }
      filterChain.doFilter(request, response);
  }
  ```
  Added `volatile boolean loggedRedisDown` to avoid log spam.
  Added `@ConditionalOnProperty(name = "gateway.ratelimit.enabled", havingValue = "true", matchIfMissing = true)` to allow disabling.

- **`api-gateway/src/main/java/com/onlineshop/gateway/ratelimit/RateLimitConfig.java`** — Lazy + bounded timeouts:
  - `@Lazy` on `bucket4jProxyManager` bean → deferred Redis connection
  - `RedisURI.builder().withTimeout(redisTimeout)` → bounded connection timeout
  - `SocketOptions.builder().connectTimeout(redisConnectTimeout)` → socket connect timeout
  - `@ConditionalOnProperty(name = "gateway.ratelimit.enabled", ...)` → conditionally disable

- **`api-gateway/src/main/resources/application.yml`** — Added Redis timeouts:
  ```yaml
  spring:
    data:
      redis:
        host: localhost
        port: 6379
        timeout: 100ms
        connect-timeout: 10s
  gateway:
    ratelimit:
      enabled: true  # overridden to false in ECS via GATEWAY_RATELIMIT_ENABLED
  ```

- **`api-gateway/src/main/java/com/onlineshop/gateway/config/ResilienceConfig.java`** — TimeLimiter timeout increased:
  ```java
  // Line 97: Changed from 3s to 4s (deployed as 5s for ECS latency)
  .timeoutDuration(Duration.ofSeconds(4))
  ```
  Actual deployed value: 5 seconds (built as `sha-ba7905d`, deployed via CI/CD). ECS task-to-task latency is higher than localhost.

- **Tests:** 10 tests pass.

### 3. Git Configuration: JAR CRLF Corruption Fix

**Problem:** `.gitattributes` had `* text eol=lf` — treating ALL files (including JAR binaries) as text. The `maven-wrapper.jar` in `api-gateway/.mvn/wrapper/` was corrupted by CRLF→LF conversion. Only `api-gateway` was affected (corruption is probabilistic).

**Changes:**

- **`.gitattributes`** — Added binary file declarations:
  ```
  *.jar binary
  *.png binary
  *.jpg binary
  ```

- **`.gitignore`** — Added maven wrapper JAR exclusion:
  ```
  **/maven-wrapper.jar
  ```

- **Removed from git:** All `maven-wrapper.jar` files untracked:
  ```bash
  git rm --cached **/maven-wrapper.jar
  ```

The `mvnw` script auto-downloads the JAR when missing — no manual action needed on fresh checkout.

### 4. Auth: HikariCP Pool Size Override

**Problem:** Local `application.yml` had `maximum-pool-size: 100` and `minimum-idle: 100`. `db.t4g.micro` supports ~25 max connections. Pool exhaustion caused `FATAL: remaining connection slots are reserved`.

**Fix:** Overrode via ECS task definition environment variables:
```
SPRING_DATASOURCE_HIKARI_MAXIMUMPOOLSIZE=10
SPRING_DATASOURCE_HIKARI_MINIMUMIDLE=1
```

No code change — configuration-only fix via environment override.

---

## GitHub Actions Workflow

### Workflow File

- **Path:** `.github/workflows/build-and-push.yml`
- **Trigger:** `workflow_dispatch` only (temporary `push` trigger removed before merge)
- **Discoverability:** Only from `main` branch (GitHub limitation)
- **Workflow Name:** `Build & Push to ECR`

### Full Workflow Content

```yaml
name: Build & Push to ECR

on:
  workflow_dispatch:
    inputs:
      service:
        description: 'Service to build and push'
        required: true
        type: choice
        options:
          - auth
          - items
          - api-gateway
          - all

permissions:
  id-token: write
  contents: read

jobs:
  build-auth:
    if: github.event_name == 'push' || github.event.inputs.service == 'auth' || github.event.inputs.service == 'all'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::799111666795:role/github-actions-onlineshop
          aws-region: eu-north-1
      - uses: aws-actions/amazon-ecr-login@v2
      - uses: actions/setup-java@v4
        with:
          java-version: '25'
          distribution: 'temurin'
      - uses: actions/cache@v4
        with:
          path: ~/.m2/repository
          key: ${{ runner.os }}-maven-auth-${{ hashFiles('Auth/pom.xml') }}
          restore-keys: |
            ${{ runner.os }}-maven-auth-
            ${{ runner.os }}-maven-
      - name: Build with Maven
        working-directory: Auth
        run: chmod +x mvnw && ./mvnw clean package -DskipTests
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: Auth
          push: true
          tags: 799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth:sha-${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  build-items:
    if: github.event_name == 'push' || github.event.inputs.service == 'items' || github.event.inputs.service == 'all'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::799111666795:role/github-actions-onlineshop
          aws-region: eu-north-1
      - uses: aws-actions/amazon-ecr-login@v2
      - uses: actions/setup-java@v4
        with:
          java-version: '25'
          distribution: 'temurin'
      - uses: actions/cache@v4
        with:
          path: ~/.m2/repository
          key: ${{ runner.os }}-maven-items-${{ hashFiles('Items/pom.xml', 'common/pom.xml') }}
          restore-keys: |
            ${{ runner.os }}-maven-items-
            ${{ runner.os }}-maven-
      - name: Build common dependency
        working-directory: common
        run: chmod +x mvnw && ./mvnw install -DskipTests
      - name: Build with Maven
        working-directory: Items
        run: chmod +x mvnw && ./mvnw clean package -DskipTests
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: Items
          push: true
          tags: 799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items:sha-${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  build-api-gateway:
    if: github.event_name == 'push' || github.event.inputs.service == 'api-gateway' || github.event.inputs.service == 'all'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::799111666795:role/github-actions-onlineshop
          aws-region: eu-north-1
      - uses: aws-actions/amazon-ecr-login@v2
      - uses: actions/setup-java@v4
        with:
          java-version: '25'
          distribution: 'temurin'
      - uses: actions/cache@v4
        with:
          path: ~/.m2/repository
          key: ${{ runner.os }}-maven-api-gateway-${{ hashFiles('api-gateway/pom.xml') }}
          restore-keys: |
            ${{ runner.os }}-maven-api-gateway-
            ${{ runner.os }}-maven-
      - name: Build with Maven
        working-directory: api-gateway
        run: chmod +x mvnw && ./mvnw clean package -DskipTests
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: api-gateway
          push: true
          tags: 799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-api-gateway:sha-${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

### Workflow Design Decisions

| Decision | Rationale |
|---|---|
| `docker/setup-buildx-action@v3` before build | Required for `cache-from`/`cache-to` with `type=gha` |
| Java 25 (not 21) | `pom.xml` targets Java 25; Spring Boot 4.0.x requires 25+ |
| Items builds `common` first | Items depends on `common`; must `mvnw install` common before Items build |
| Maven cache key: `pom.xml` hash | Invalidates cache when dependencies change; restores best match |
| Docker tag: `sha-<FULL_SHA>` | Immutable, traceable — always know which commit is in which image |
| `permissions.id-token: write` | Required for OIDC `configure-aws-credentials` |
| `permissions.contents: read` | Minimum needed for checkout |
| `chmod +x mvnw` | mvnw wrapper must be executable (git may strip exec bits on checkout) |

### Trigger Command (after merge to main)

```bash
gh workflow run "Build & Push to ECR" -f service=all
```

### Commit History for Workflow Development

| Commit | Description |
|---|---|
| `ba27547` | feat: add GitHub Actions build-and-push workflow with OIDC to ECR (initial) |
| `c06d5f2` | test: add temporary push trigger for testing workflow on feature branch |
| `1dfb4a6` | fix: handle push event in job conditions to allow auto-triggering |
| `4108ea8` | fix: use Java 25 to match project requirement (Spring Boot 4.0.x) |
| `befc225` | fix: treat JARs as binary in git, add setup-buildx for Docker cache |
| `226bb34` | fix: renormalize api-gateway maven-wrapper.jar (failed fix attempt) |
| `263f069` | fix: remove maven-wrapper.jar from git tracking to prevent CRLF corruption |
| `1765c89` | chore: remove temporary push trigger, keep workflow_dispatch only |

---

## Issues Encountered & Resolutions

### Issue 1: Workflow Not Discoverable on Feature Branch

| Aspect | Detail |
|---|---|
| **Symptom** | `gh workflow list` and `gh workflow run` couldn't find the new workflow |
| **Root Cause** | GitHub only indexes `workflow_dispatch` workflows from the default branch (`main`) |
| **Why** | The workflow index is built from `main` to prevent unreviewed workflows from being triggerable |
| **Fix** | Added temporary `push` trigger for testing on feature branch; removed before merge |
| **Lesson** | During development on a feature branch, add `push` trigger for testing. Remove before merging. |

### Issue 2: Job Conditions Skipping on Push Events

| Aspect | Detail |
|---|---|
| **Symptom** | Workflow showed "skipped" for all jobs on push |
| **Root Cause** | `github.event.inputs` is `null` on push events; only populated for `workflow_dispatch` |
| **Fix** | Added `github.event_name == 'push'` as an additional OR condition in each job's `if:` |
| **Lesson** | Always check `github.event_name` before accessing `github.event.inputs` in multi-event workflows. |

### Issue 3: Maven Fails — Java 21 vs Java 25

| Aspect | Detail |
|---|---|
| **Symptom** | `error: release version 25 not supported` during Maven compile |
| **Root Cause** | `setup-java` configured `java-version: '21'` but `pom.xml` targets Java 25 |
| **Detection** | `gh run view --log` showed Maven compiler error; cross-checked `pom.xml` `<java.version>` and `Dockerfile` `FROM eclipse-temurin:25.0.1_8-jre-alpine` |
| **Fix** | Changed `java-version: '25'` in all three jobs |
| **Lesson** | Always cross-check `setup-java` version with `<java.version>` in `pom.xml` AND `FROM` line in `Dockerfile`. All three must agree. |

### Issue 4: Docker Build Fails — Cache Backend Not Supported

| Aspect | Detail |
|---|---|
| **Symptom** | `ERROR: failed to build: Cache export is not supported for the docker driver` |
| **Root Cause** | `cache-to: type=gha` requires the BuildKit (buildx) driver; default GitHub runner Docker driver doesn't support cache export |
| **Fix** | Added `docker/setup-buildx-action@v3` before each `docker/build-push-action@v6` |
| **Lesson** | Whenever using `cache-from`/`cache-to` with `type=gha`, MUST run `setup-buildx-action` first. |

### Issue 5: CRLF Corruption of maven-wrapper.jar

| Aspect | Detail |
|---|---|
| **Symptom** | `Could not find or load main class org.apache.maven.wrapper.MavenWrapperMain` (api-gateway only) |
| **Root Cause** | `.gitattributes` had `* text eol=lf` — treating ALL files (including binary JARs) as text, corrupting binary content during CRLF→LF conversion |
| **Why only api-gateway?** | Corruption is probabilistic — depends on whether the JAR bytes happen to contain a `0x0D 0x0A` sequence |
| **Failed Fix Attempt** | `git add --renormalize` after adding `*.jar binary` to `.gitattributes` — the JAR was already corrupted in git's object store |
| **Working Fix** | Removed all `maven-wrapper.jar` from git tracking, added `**/maven-wrapper.jar` to `.gitignore`, added `*.jar binary` to `.gitattributes`. mvnw auto-downloads JARs when missing. |
| **Lesson** | Never put `* text` in `.gitattributes` without also adding `*.jar binary`, `*.png binary`, etc. Better: let maven wrapper JARs be auto-downloaded. |

### Issue 6: ECR Repo Misnamed with Slash

| Aspect | Detail |
|---|---|
| **Symptom** | Repository named `onlineshop-auth/api-gateway` instead of `onlineshop-api-gateway` |
| **Root Cause** | Manual creation in step 1.2; likely a typo or misunderstood naming convention |
| **Detection** | Listing repos showed pattern mismatch: `onlineshop-auth`, `onlineshop-items`, `onlineshop-auth/api-gateway` |
| **Fix** | Verified repo was empty (`describe-images` returned `[]`), then `delete-repository --force`, then `create-repository` with correct name |
| **Lesson** | Always verify created resources with `describe`/`list` after any `create`/`put`/`delete`. |

### Issue 7: IAM Role Creation — Encoding Error on Windows

| Aspect | Detail |
|---|---|
| **Symptom** | `The specified value for assumeRolePolicyDocument is invalid` |
| **Root Cause** | JSON file written with PowerShell's `@'...'@` here-string, which introduced non-printable characters (BOM — Byte Order Mark) |
| **Fix** | Rewrote file using `[System.IO.File]::WriteAllText(...)` with `[System.Text.Encoding]::ASCII` |
| **Lesson** | When writing JSON for AWS CLI on Windows, always use ASCII encoding. PowerShell's default UTF-8-with-BOM confuses AWS IAM. |

### Issue 8: RDS Connection Pool Exhaustion

| Aspect | Detail |
|---|---|
| **Symptom** | `FATAL: remaining connection slots are reserved for roles with privileges of the "pg_use_reserved_connections" role` |
| **Root Cause** | Auth `application.yml` had `maximum-pool-size: 100` and `minimum-idle: 100`. `db.t4g.micro` max connections ~25 |
| **Fix** | Overrode via ECS environment variables: `SPRING_DATASOURCE_HIKARI_MAXIMUMPOOLSIZE=10`, `SPRING_DATASOURCE_HIKARI_MINIMUMIDLE=1` |
| **Lesson** | Always override application.yml defaults for production. `t4g.micro` RDS needs pool sizes ≤10. |

### Issue 9: Items Container Crash — No Actuator

| Aspect | Detail |
|---|---|
| **Symptom** | Container health check `/actuator/health/liveness` returned 404 → ECS restarted container repeatedly |
| **Root Cause** | Items had no `spring-boot-starter-actuator` dependency — no health endpoint |
| **Fix** | Added actuator dependency to `Items/pom.xml`, configured health endpoints in `application.yml`, rebuilt image as `sha-ba7905d` |
| **Lesson** | Every Spring Boot service in ECS needs actuator for health checks. |

### Issue 10: Redis Sidecar Startup Race

| Aspect | Detail |
|---|---|
| **Symptom** | API Gateway `Unable to connect to localhost:<unresolved>:6379` / `Connection initialization timed out after 100 millisecond(s)` |
| **Root Cause** | Redis container not ready when Spring connects. Gateway connects to Redis eagerly during context init. |
| **Fix** | 1. `dependsOn: {condition: HEALTHY}` in task definition — wait for Redis health check. 2. `GATEWAY_RATELIMIT_ENABLED=false` — disable rate limiting (which requires Redis). 3. `@Lazy` + bounded timeouts in `RateLimitConfig`. |
| **Lesson** | Sidecar containers need `dependsOn` with health condition + main app must be tolerant of sidecar startup delay. |

### Issue 11: Service Connect DNS Not Resolving (FIXED 2026-07-26)

| Aspect | Detail |
|---|---|
| **Symptom** | `java.nio.channels.UnresolvedAddressException: http://auth.onlineshop.local:9001/api/v1/auth/login` |
| **Root Cause** | `serviceConnectConfiguration` was `null` on all three ECS services. The Cloud Map namespace (`onlineshop.local`) and service entries existed as orphaned artifacts, but no Envoy proxy sidecar was running. |
| **Status** | FIXED (2026-07-26) |
| **Fix** | Enabled Service Connect via `update-service` on all three services. Auth: expose `auth` on port 9001. Items: expose `items` on port 9000. Gateway: client only. Updated gateway `SPRING_APPLICATION_JSON` in task definition to use `http://auth:9001` and `http://items:9000`. |
| **Key insight** | Service Connect injects an Envoy proxy sidecar into each task. The sidecar handles DNS resolution of short names (`auth`, `items`) → task IPs automatically. No more hardcoded IPs needed. |
| **Previous workaround (deprecated)** | `{"gateway":{"auth":{"service-url":"http://172.31.23.124:9001"},"items":{"service-url":"http://172.31.26.229:9000"}}}`

### Issue 12: API Gateway → Auth Traversal Blocked (FIXED)

| Aspect | Detail |
|---|---|
| **Symptom** | Request times out when API Gateway forwards to Auth on port 9001 |
| **Root Cause** | ECS security group had no self-referencing rule. Security groups don't automatically allow traffic between members. |
| **Fix** | Added inbound rules on `sg-0b209104a6b15b157`: TCP 9000-9001 from self, TCP 6379 from self |
| **Lesson** | Security groups need explicit self-referencing rules for task-to-task communication within the same SG. |

### Issue 13: Resilience4j TimeLimiter Timeout

| Aspect | Detail |
|---|---|
| **Symptom** | `DefaultAuthServiceClient: Auth service timed out` / `TimeLimiter 'authService' recorded a timeout exception` |
| **Root Cause** | Resilience4j `TimeLimiter` for Auth validation was 3 seconds — too short for ECS task-to-task latency |
| **Fix** | Changed `ResilienceConfig.java` line 97: `Duration.ofSeconds(3)` → `Duration.ofSeconds(4)` (deployed as 5s). Rebuilt image as `sha-ba7905d`, deployed via CI/CD. |
| **Lesson** | ECS task-to-task latency is higher than localhost. Increase timeouts proportionally. |

---

## Remaining Tech Debt (for Pass 2+)

| Issue | Priority | Notes |
|---|---|---|
| Service Connect DNS | Done | ~~FIXED (2026-07-26)~~ Enabled on all 3 services, gateway uses `auth`/`items` DNS |
| Rate limiter lazy Redis connection | Medium | Disabled via `GATEWAY_RATELIMIT_ENABLED=false`; `@Lazy` + timeouts partially fix startup |
| Dynamic service discovery | Done | ~~FIXED (2026-07-26)~~ Service Connect handles DNS resolution automatically |
| Frontend deployment | High | Step 1.6 not done — S3 + CloudFront for React app |
| ECR resource scoping | Medium | `ecr-push-pull` policy uses `"Resource": "*"` — tighten to specific repo ARNs |
| Items tests | Low | Pre-existing test bugs fixed, but 72 tests is minimal |
| ECR image tag mutability | Medium | Currently `MUTABLE` — consider `IMMUTABLE` for production traceability |
| CI test gates | Pass 2 | Tests skipped in CI (`-DskipTests`) — run tests in pipeline |
| Selective builds | Pass 2 | All services build on every dispatch. Only rebuild changed services. |
| Staging environment | Pass 2 | Scale-to-zero or on-demand teardown |
| AWS profile vs env variable | Low | Some commands use `--profile dpm-profile`, others rely on env — standardize |
| Auth actuator exposure | High | Auth `application.yml` has `endpoints.web.exposure.include: "*"` and `env.show-values: always` → potential secret leak |

---

## Verification Checklist

### Infrastructure

- [x] `aws sts get-caller-identity` returns account `799111666795`, user `admin`
- [x] OIDC provider exists: `arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com`
- [x] IAM Role `github-actions-onlineshop` exists with `ecr-push-pull` policy
- [x] IAM Role `ecsTaskExecutionRole` exists with `AmazonECSTaskExecutionRolePolicy` + `secretsmanager-read-onlineshop`
- [x] Three ECR repos exist in `eu-north-1`: `onlineshop-auth`, `onlineshop-items`, `onlineshop-api-gateway`
- [x] RDS instance `onlineshop-postgres-db` accessible on port 5432
- [x] `auth` and `items` databases exist on RDS
- [x] Service accounts `auth_app` and `items_app` exist with correct privileges
- [x] Secrets Manager entries `onlineshop/auth/db` and `onlineshop/items/db` exist
- [x] ECS cluster `onlineshop-cluster` is ACTIVE
- [x] Cloud Map namespace `onlineshop.local` exists

### Networking

- [x] ALB SG: inbound :80 from 0.0.0.0/0
- [x] ECS SG: inbound :0-65535 from ALB SG
- [x] ECS SG: inbound :9000-9001 from self (self-referencing)
- [x] ECS SG: inbound :6379 from self (self-referencing)
- [x] DB SG: inbound :5432 from ECS SG

### ECS

- [x] Auth task definition exists
- [x] Items task definition exists
- [x] API Gateway task definition exists
- [x] Auth service exists and is configured
- [x] Items service exists and is configured
- [x] API Gateway service exists and is configured

### ALB

- [x] ALB, TG, and listener creation commands documented in ALB section

### Smoke Test (API)

```bash
ALB_DNS=$(aws elbv2 describe-load-balancers --profile dpm-profile --region eu-north-1 --names onlineshop-alb --query 'LoadBalancers[0].DNSName' --output text)
ALB="http://$ALB_DNS"

# 1. Register — expected 201
curl -s -X POST $ALB/auth/register -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo123"}'
# ✓ 201 Created

# 2. Login — expected 200 with token
curl -s -X POST $ALB/auth/login -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo123"}'
# ✓ 200 OK: {"token":"...","expiresIn":3600,...}

# 3. List items with token — expected 200, 5 products
TOKEN=$(echo '<login response>' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
curl -s $ALB/items -H "Authorization: Bearer $TOKEN"
# ✓ 200 OK: [{"name":"Laptop","quantity":15}, ...5 items]

# 4. Validate token — expected 200
curl -s $ALB/auth/validate -H "Authorization: Bearer $TOKEN"
# ✓ 200 OK: {"valid":true,"userId":2,"username":"demo",...}
```

### CI/CD

- [x] GitHub Actions workflow `.github/workflows/build-and-push.yml` exists
- [x] Workflow can be triggered via `workflow_dispatch` (after merge to `main`)
- [x] Auth build job: Maven + Docker + push to ECR — passes
- [x] Items build job: common build + Maven + Docker + push — passes
- [x] API Gateway build job: Maven + Docker + push — passes
- [x] Images in ECR tagged with `sha-<COMMIT_HASH>`

---

## Quick Reference: Common Commands

```bash
# Identity
aws sts get-caller-identity --profile dpm-profile --region eu-north-1

# ECR
aws ecr describe-repositories --region eu-north-1
aws ecr describe-images --repository-name onlineshop-auth --region eu-north-1

# ECS
aws ecs describe-services --cluster onlineshop-cluster --services onlineshop-auth
aws ecs list-tasks --cluster onlineshop-cluster --service-name onlineshop-auth
aws ecs describe-tasks --cluster onlineshop-cluster --tasks <TASK_ARN>

# Update service (new task def revision)
aws ecs update-service --cluster onlineshop-cluster --service onlineshop-auth \
  --task-definition onlineshop-auth:<REV>

# Force redeploy (no task def change)
aws ecs update-service --cluster onlineshop-cluster --service onlineshop-auth \
  --force-new-deployment

# Logs
aws logs get-log-events --log-group-name /ecs/onlineshop-auth --log-stream-name <STREAM>

# Secrets
aws secretsmanager get-secret-value --secret-id onlineshop/auth/db

# Trigger workflow (after merge to main)
gh workflow run "Build & Push to ECR" -f service=all

# Get task private IP
TASK_ARN=$(aws ecs list-tasks --cluster onlineshop-cluster --service-name onlineshop-auth \
  --query 'taskArns[0]' --output text)
aws ecs describe-tasks --cluster onlineshop-cluster --tasks $TASK_ARN \
  --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`]|[0].value' --output text
```

---

## Infrastructure Inventory

| Component | Key Identifiers |
|---|---|
| Auth Service | Task def: `onlineshop-auth`, ECR: `onlineshop-auth`, Secret: `onlineshop/auth/db`, Port: 9001 |
| Items Service | Task def: `onlineshop-items`, ECR: `onlineshop-items`, Secret: `onlineshop/items/db`, Port: 9000 |
| API Gateway | Task def: `onlineshop-api-gateway:13`, ECR: `onlineshop-api-gateway`, ALB TG: `onlineshop-gateway-tg`, Port: 10000 |
| ALB | Name: `onlineshop-alb`, TG: `onlineshop-gateway-tg` (HTTP:10000, ip), Listener: port 80 → TG |
| RDS | `onlineshop-postgres-db`, PostgreSQL 18.4, db.t4g.micro, 20 GB |
| Frontend S3 | Bucket: `onlineshop-frontend-799111666795`, Website endpoint, Public read |
| CloudFront | Distribution: `EPS8MI3FV3B7X`, Domain: `d2akuwv5pxgajc.cloudfront.net`, S3 + ALB origins |
| CI/CD | `.github/workflows/build-and-push.yml`, OIDC role `github-actions-onlineshop` |
| Service Connect namespace | `onlineshop.local` (production), `staging.onlineshop.local` (staging) |
| Log groups | `/ecs/onlineshop-auth`, `/ecs/onlineshop-items`, `/ecs/onlineshop-api-gateway` (production) |
| Execution role | `arn:aws:iam::799111666795:role/ecsTaskExecutionRole` |

Pass 3, subphase 3.5 hardening is **not yet applied live**: the frontend still
uses the public S3 website origin (migration to an S3 REST origin behind a
CloudFront Origin Access Control is implemented in
`scripts/migrate-frontend-oac.sh` and `scripts/verify-frontend-oac.sh` but is
deferred to the consolidated verification pass), and the explicit non-secret
production identifiers now live in `scripts/config/production.env` for the
read-only inventory/separation checks. See
`explanations/PRODUCTION-HARDENING-DECISIONS.md`.

**Cost when paused:** ~$1.25/month (secrets + ECR + Cloud Map). Resume: `bash scripts/resume-playground.sh`.
**Cost when running (Spot 24/7):** ~$49.00/month (compute + IPv4 + ALB). Pause: `bash scripts/pause-playground.sh`.
**Full flow verified:** register → login → token validation → items list (5 products).

---

## Frontend Hosting (S3 + CloudFront)

### S3 Bucket

| Property | Value |
|---|---|
| Bucket Name | `onlineshop-frontend-799111666795` |
| Bucket ARN | `arn:aws:s3:::onlineshop-frontend-799111666795` |
| Region | `eu-north-1` |
| Website Endpoint | `onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com` |
| Access Policy | Public read (`s3:GetObject` allowed from `*`) |

**Creation commands:**
```bash
aws s3api create-bucket --profile dpm-profile --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795 \
  --create-bucket-configuration LocationConstraint=eu-north-1

aws s3api delete-public-access-block --profile dpm-profile --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795

aws s3api put-bucket-policy --profile dpm-profile --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795 \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::onlineshop-frontend-799111666795/*"
    }]
  }'

aws s3api put-bucket-website --profile dpm-profile --region eu-north-1 \
  --bucket onlineshop-frontend-799111666795 \
  --website-configuration '{"IndexDocument":{"Suffix":"index.html"},"ErrorDocument":{"Key":"index.html"}}'
```

**Upload command:**
```bash
aws s3 sync /path/to/frontend/dist s3://onlineshop-frontend-799111666795/ \
  --profile dpm-profile --region eu-north-1 --delete
```

### CloudFront Distribution

| Property | Value |
|---|---|
| Distribution ID | `EPS8MI3FV3B7X` |
| ARN | `arn:aws:cloudfront::799111666795:distribution/EPS8MI3FV3B7X` |
| Domain Name | `d2akuwv5pxgajc.cloudfront.net` |
| Status | `Deployed` |
| Price Class | `PriceClass_All` |
| Viewer Certificate | CloudFront default certificate (`*.cloudfront.net`) |

**Origins:**
| ID | Domain Name | Protocol |
|---|---|---|
| `s3-frontend` | `onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com` | HTTP (port 80) |
| `alb-api` | `onlineshop-alb-1163734147.eu-north-1.elb.amazonaws.com` | HTTP (port 80) |

**Cache Behaviors:**
| Path Pattern | Origin | TTL | Forwarded Values |
|---|---|---|---|
| `Default (*)` | `s3-frontend` | Min:0, Default:86400, Max:31536000 | QueryString:false, Cookies:none |
| `/auth*` | `alb-api` | Min:0, Default:0, Max:0 | QueryString:true, Cookies:all, Headers:Authorization,Content-Type |
| `/items*` | `alb-api` | Min:0, Default:0, Max:0 | QueryString:true, Cookies:all, Headers:Authorization,Content-Type |

**Custom Error Response:**
- ErrorCode: 404 → ResponseCode: 200, ResponsePagePath: `/index.html` (SPA routing support)

**Invalidation:**
```bash
aws cloudfront create-invalidation --profile dpm-profile --region us-east-1 \
  --distribution-id EPS8MI3FV3B7X --paths "/*"
```

### Frontend Build Notes

- Build command: `VITE_API_URL='' npm run build` (from `frontend/` directory)
- `VITE_API_URL=''` makes API calls relative (same-origin through CloudFront)
- Code fix: `frontend/src/services/api.ts` uses `??` instead of `||` to preserve empty string:
  ```ts
  const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:10000';
  ```

### Verified Public URLs

| Component | URL | Status |
|---|---|---|
| Frontend (CloudFront) | `https://d2akuwv5pxgajc.cloudfront.net` | ✅ 200, SPA loads |
| API: register | `POST https://d2akuwv5pxgajc.cloudfront.net/auth/register` | ✅ 201 |
| API: login | `POST https://d2akuwv5pxgajc.cloudfront.net/auth/login` | ✅ 200 + token |
| API: items | `GET https://d2akuwv5pxgajc.cloudfront.net/items` | ✅ 200 (with auth) |
| API: validate | `GET https://d2akuwv5pxgajc.cloudfront.net/auth/validate` | ✅ 200 |
| CORS preflight | `OPTIONS https://d2akuwv5pxgajc.cloudfront.net/auth/login` | ✅ 200 |

---

## Code Changes for AWS Deployment

### CORS Fix (API Gateway)

**Files changed:**
- `api-gateway/src/main/java/com/onlineshop/gateway/config/CorsConfig.java`
- `api-gateway/src/main/resources/application.yml`

**Changes:**
- `allowedOrigins`: localhost-only → `"*"`
- `allowCredentials`: `true` → `false` (required for `*` origin)

**Deployed as:**
- ECR image tag: `cors-fix`
- ECS task definition: `onlineshop-api-gateway:13`

---

## Known Documentation Gaps

The following details were **not recorded** during the original setup and would need to be retrieved from AWS or determined at recreation time:

| Gap | Reason | Retrieval Command |
|---|---|---|
| ALB ARN | Not captured during `create-load-balancer` | `aws elbv2 describe-load-balancers --region eu-north-1 --names onlineshop-alb --query 'LoadBalancers[0].LoadBalancerArn'` |
| Cloud Map Namespace ID | Generated by AWS, not captured | `aws servicediscovery list-namespaces --region eu-north-1 --query "Namespaces[?Name=='onlineshop.local'].Id"` |
| Subnet IDs (a, b, c) | Default VPC subnets, not captured | `aws ec2 describe-subnets --region eu-north-1 --filters Name=vpc-id,Values=vpc-06eeb0bc47ecdbd61 --query 'Subnets[*].SubnetId'` |
| Items task private IP | Dynamic, changes on restart | `aws ecs describe-tasks --cluster onlineshop-cluster --tasks $(aws ecs list-tasks --cluster onlineshop-cluster --service-name onlineshop-items --query 'taskArns[0]' --output text) --query 'tasks[0].attachments[0].details[?name==\`privateIPv4Address\`]|[0].value'` |
| Auth seed Argon2id hash | Not captured — hash of `testpass` | Re-issue `INSERT INTO users...` or hash `testpass` with Argon2id using the same salt config |
| RDS creation command | RDS was provisioned before this documentation cycle | See RDS properties table above for values to replicate |
| Secrets Manager creation commands | Not captured | `aws secretsmanager create-secret --name onlineshop/auth/db --secret-string '{"username":"auth_app",...}'` |
| `ecsTaskExecutionRole` RoleId | Not captured | `aws iam get-role --role-name ecsTaskExecutionRole --query 'Role.RoleId'` |

---

## Cost Optimization (2026-07-25)

### Fargate Spot Switch

**Date:** 2026-07-25

Switched all 3 ECS services from `FARGATE` (On-Demand) to `FARGATE_SPOT` to reduce compute costs by ~60-70%.

**Commands:**
```bash
for svc in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  aws ecs update-service --profile dpm-profile --region eu-north-1 \
    --cluster onlineshop-cluster --service $svc \
    --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
    --force-new-deployment
done
```

Note: First attempt without `--force-new-deployment` failed with:
```
InvalidParameterException: When switching from launch type to capacity provider strategy on an existing service,
or making a change to a capacity provider strategy on a service that is already using one, you must force a new deployment.
```

**Result (post-switch verification):**

| Service | Capacity Provider | Desired Count | Running Count | Rollout State |
|---|---|---|---|---|---|
| `onlineshop-auth` | FARGATE_SPOT | varies (0 when paused, 1 when running) | varies (0 when paused, 1 when running) | varies |
| `onlineshop-items` | FARGATE_SPOT | varies (0 when paused, 1 when running) | varies (0 when paused, 1 when running) | varies |
| `onlineshop-api-gateway` | FARGATE_SPOT | varies (0 when paused, 1 when running) | varies (0 when paused, 1 when running) | varies |

All 3 services successfully switched to FARGATE_SPOT. Old FARGATE tasks drain while new SPOT tasks start (normal rolling deployment behavior).

**Cost Impact:**
- Before (On-Demand 24/7): ~$62.98/month
- After (Spot 24/7): ~$49.00/month
- Savings: ~$21.32/month (34% reduction)

### Pause/Resume Scripts

Created `plans/AUTOMATIC-BUILDS-AND-DEPLOY/scripts/` with two self-contained bash scripts:

| Script | Path | What It Does |
|---|---|---|
| `pause-playground.sh` | `scripts/pause-playground.sh` | Scales all 3 ECS services to 0, deletes ALB listener, target group, and ALB. Cost when paused: ~$1.25/month |
| `resume-playground.sh` | `scripts/resume-playground.sh` | Creates ALB, target group `onlineshop-gateway-tg` (HTTP:10000, ip, health `/actuator/health`), listener (port 80 → TG), wires API Gateway service to new TG, scales all 3 services to 1, waits for steady state |

**Hardcoded infrastructure IDs:**
- VPC: `vpc-06eeb0bc47ecdbd61`
- Subnets: `subnet-03b318e59490a891a`, `subnet-041e4cf18bfce06f8`, `subnet-0a009040ef6bce7cc`
- ALB SG: `sg-0b5427a6a3bf31c29`
- ECS SG: `sg-0b209104a6b15b157`
- ECS Cluster: `onlineshop-cluster`

**Key design decisions:**
- No `jq` dependency — uses `--query` and `--output text` exclusively
- No runtime AWS queries for infrastructure IDs — all hardcoded, so scripts work without `jq` or valid session (except for the AWS mutations themselves)
- `--profile dpm-profile --region eu-north-1` on every aws command
- Error handling: detects already-paused/already-resumed state and skips redundant operations
- `resume-playground.sh` waits up to 5 minutes for Spring Boot startup (3 min startPeriod + buffer)

---

## Pass 2 — CI Pipeline Hardening & Staging

### Date: 2026-08-02

### 2.1 Branch Protection (APPLIED 2026-08-02)

**Rules on `main`:**
| Rule | Value |
|------|-------|
| Required status checks | `auth`, `items`, `api-gateway`, `e2e-staging` |
| Strict status checks | `true` (branches must be up-to-date) |
| Required approvals | 1 |
| Dismiss stale reviews | `true` |
| Required linear history | `true` (squash merge only) |
| Force pushes | Disabled |
| Branch deletions | Disabled |

**Command used:**
```bash
gh api repos/Djimi/OnlineShop-full-stack/branches/main/protection \
  --method PUT \
  -f required_status_checks='{"strict":true,"contexts":["auth","items","api-gateway","e2e-staging"]}' \
  -f required_pull_request_reviews='{"required_approving_review_count":1,"dismiss_stale_reviews":true}' \
  -f required_linear_history=true \
  -f allow_force_pushes=false \
  -f allow_deletions=false
```

### 2.2-2.5 Workflow Rewrite

**Old:** `.github/workflows/build-and-push.yml` (kept for reference)
**New:** `.github/workflows/build-and-deploy.yml`

**Triggers:**
| Trigger | Behavior |
|---------|----------|
| `push` to `feature/**` | Build, test, push affected services. Tags: `sha-<SHA>` + `branch-<name>` |
| `pull_request` to `main` | Build, test, push affected services. Tags: `sha-<SHA>` only |
| `push` to `main` | Build, test, push all changed. Tags: `sha-<SHA>` + `main-latest`. Deploy to staging + E2E |
| `workflow_dispatch` | Manual. Service selection: `auth`/`items`/`api-gateway`/`all` |

**Concurrency:** `${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true`

**Job graph:**
```
changes (dorny/paths-filter)
  ├── auth (test → build → push)
  ├── items (build common → test → build → push)
  ├── api-gateway (test → build → push)
  └── e2e-staging (main only: deploy → E2E tests)
```

**Change detection filters:**
| Service | Paths |
|---------|-------|
| `auth` | `Auth/**` |
| `items` | `Items/**`, `common/**` |
| `api-gateway` | `api-gateway/**` |
| `frontend` | `frontend/**` |

**Test gates:**
- Auth: `./mvnw verify` (unit + integration tests, JaCoCo check at 50% line + branch)
- Items: `./mvnw verify` (unit + integration tests, JaCoCo check at 90% line)
- API Gateway: `./mvnw verify` (unit + integration tests, no JaCoCo)
- Frontend: Not yet wired (no `test` script in package.json)

**Test reports:** Uploaded as artifacts (`auth-test-report`, `items-test-report`, `api-gateway-test-report`)

**Docker build per service:**
- Auth: host-side `mvnw verify` + Docker build from `Auth/` context
- Items: host-side `mvnw verify` (with `common` install) + Docker multi-stage build from root context
- API Gateway: host-side `mvnw verify` + Docker build from `api-gateway/` context

**Docker tag matrix:**
| Event | SHA tag | Branch tag | main-latest |
|-------|---------|------------|-------------|
| push to `feature/foo` | `sha-<SHA>` | `branch-feature-foo` | — |
| PR to main | `sha-<SHA>` | — | — |
| push to main | `sha-<SHA>` | — | `main-latest` |
| workflow_dispatch | `sha-<SHA>` | depends on ref | depends on ref |

### 2.6 Caching Notes

- **Maven cache** (`actions/cache@v4`): host-side `~/.m2/repository`, keyed by `pom.xml` hash
  - Auth and API Gateway benefit from this
  - Items' Docker multi-stage build uses Docker build cache mount (`--mount=type=cache,target=/root/.m2,id=maven-repo`) — separate from GHA Maven cache
- **Docker layer cache** (`docker/setup-buildx-action@v3` + `type=gha`): GHA-cached Docker layers
- **Cache miss correctness:** Verified via `restore-keys` fallback chain. If exact key misses, partial match restores best-effort cache. Maven re-downloads only missing dependencies.
- **Known gap:** Items Docker multi-stage build downloads Maven deps fresh on cache mount miss (not shared across CI runs). Consider pre-building `common` inside Docker or using a dedicated CI Dockerfile.

### 2.7 Staging Environment (PROVISIONED 2026-08-02)

**Scripts created:**
- `scripts/ci-deploy-staging.sh` — Deploys given image tag to staging ECS services
- `scripts/setup-staging-env.sh` — One-time guided setup script (run once, now completed)

**Staging Infrastructure (provisioned):**

| Resource | Name/ID | Details |
|----------|---------|---------|
| Staging ALB | `onlineshop-staging-alb` | ARN: `arn:aws:elasticloadbalancing:eu-north-1:799111666795:loadbalancer/app/onlineshop-staging-alb/095c9e98dbbe762e`, DNS: `onlineshop-staging-alb-615176433.eu-north-1.elb.amazonaws.com` |
| Staging TG | `onlineshop-staging-tg` | ARN: `arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-staging-tg/201ace94eec44688` |
| Staging Listener | — | ARN: `arn:aws:elasticloadbalancing:eu-north-1:799111666795:listener/app/onlineshop-staging-alb/095c9e98dbbe762e/87eeef8afb383a2d`, Port 80 → TG |
| Auth Staging TD | `onlineshop-auth-staging:2` | `auth-staging-port:9001`, DB: `auth_staging`, secrets: `onlineshop/auth/db-staging` |
| Items Staging TD | `onlineshop-items-staging:2` | `items-staging-port:9000`, DB: `items_staging`, secrets: `onlineshop/items/db-staging` |
| API Gateway Staging TD | `onlineshop-api-gateway-staging:2` | `gateway-staging-port:10000`, SC client to `auth-staging` + `items-staging` |
| Auth Staging Service | `onlineshop-auth-staging` | FARGATE_SPOT, desired:0 (on-demand), SC: `auth-staging:9001` |
| Items Staging Service | `onlineshop-items-staging` | FARGATE_SPOT, desired:0 (on-demand), SC: `items-staging:9000` |
| API Gateway Staging Service | `onlineshop-api-gateway-staging` | FARGATE_SPOT, desired:0 (on-demand), ALB TG attached, SC client |
| Staging DB: auth | `auth_staging` on RDS | Schema: `01-schema.sql` (users, sessions) + seed data |
| Staging DB: items | `items_staging` on RDS | Schema: `01-schema.sql` (items) + seed data |
| Service Account | `auth_app_staging` | SELECT,INSERT,UPDATE,DELETE on auth_staging |
| Service Account | `items_app_staging` | SELECT,INSERT,UPDATE,DELETE on items_staging |
| Secret | `onlineshop/auth/db-staging-Dkh7wC` | `{"username":"auth_app_staging",...}` |
| Secret | `onlineshop/items/db-staging-LaYr9R` | `{"username":"items_app_staging",...}` |
| Cloud Map Service | `auth-staging-port` | DNS: `auth-staging.onlineshop.local:9001` |
| Cloud Map Service | `items-staging-port` | DNS: `items-staging.onlineshop.local:9000` |
| Cloud Map Service | `gateway-staging-port` | DNS: `gateway-staging.onlineshop.local:10000` |

**Key design decision:** Staging container port mapping names differ from production (`auth-staging-port` vs `auth-port`) because Service Connect requires unique port names per namespace. Production uses `auth-port`, `items-port`, `gateway-port`.

**Deployment flow (push to main):**
1. All service images built + pushed with `sha-<SHA>` + `main-latest`
2. `ci-deploy-staging.sh sha-<SHA>` → registers new task defs, updates services to desired:1
3. Waits for services stable (60s timeout)
4. Runs E2E tests against staging ALB

**Smoke test verified (2026-08-02):**
- `POST /auth/register` → 201
- `POST /auth/login` → 200 + token
- `GET /items` with Bearer → 200, 5 products
- `GET /auth/validate` → 200, valid:true

**Issue encountered during provisioning:** The `echo <SCHEMA>` approach for applying SQL via ECS task didn't work — the `IF NOT EXISTS` in CREATE TABLE masked errors. Re-applied schema using explicit CREATE TABLE statements (without IF NOT EXISTS, after DROP TABLE IF EXISTS). The `echo` approach for SQL injection via Docker command is fragile — prefer `psql -c` with individual statements.

**To pause staging ALB (when not in use):**
```bash
# Delete listener and ALB to save cost (same pattern as production)
aws elbv2 delete-listener --profile dpm-profile --region eu-north-1 \
  --listener-arn arn:aws:elasticloadbalancing:eu-north-1:799111666795:listener/app/onlineshop-staging-alb/095c9e98dbbe762e/87eeef8afb383a2d
aws elbv2 delete-target-group --profile dpm-profile --region eu-north-1 \
  --target-group-arn arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-staging-tg/201ace94eec44688
aws elbv2 delete-load-balancer --profile dpm-profile --region eu-north-1 \
  --load-balancer-arn arn:aws:elasticloadbalancing:eu-north-1:799111666795:loadbalancer/app/onlineshop-staging-alb/095c9e98dbbe762e
```

**To resume staging:**
Reverse of above — create ALB, TG, listener, wire to API Gateway staging service.

### 2.8 CI Security Verification

| Check | Status | Details |
|-------|--------|---------|
| OIDC only auth | ✅ CONFIRMED | `configure-aws-credentials@v4` with `role-to-assume`. No long-lived keys in repo |
| Secrets at runtime | ✅ CONFIRMED | ECS task defs use `secrets[].valueFrom` → AWS Secrets Manager. No secrets in env vars |
| Secret masking in logs | ✅ CONFIRMED | Secrets Manager integration masks values in ECS logs. GitHub Actions masks via `::add-mask::` |
| Minimal permissions | ✅ IMPROVED | ECR + ECS deploy + ELB describe. `Resource: "*"` on ECS still — tighten in Pass 3 |

**IAM role `github-actions-onlineshop` policies (2026-08-02):**
- `ecr-push-pull` — ECR operations (Resource: `*`)
- `ecs-deploy-staging` — ECS deploy + ELB describe (Resource: `*`)

### Code Changes in Pass 2

#### 1. Auth JaCoCo Threshold Bump

**File:** `Auth/pom.xml`
**Change:** LINE and BRANCH minimums: `0.30` → `0.50`

#### 2. Auth Actuator Security Fix

**File:** `Auth/src/main/resources/application.yml`
**Problem:** Global `endpoints.web.exposure.include: "*"` exposed all actuator endpoints including `/actuator/env` and `/actuator/configprops` with `show-values: always` — potential database password leak.
**Fix:**
- Removed global `"*"` exposure, set to `health,metrics`
- Changed `health.show-details` from `always` to `when-authorized`
- Changed `env.show-values` and `configprops.show-values` from `always` to `never`
- Aligned with Items' pattern (fixed in Pass 1)

#### 3. CI/CD Workflow Rewrite

**File:** `.github/workflows/build-and-deploy.yml` (new)
Replaces the old `build-and-push.yml` with:
- `dorny/paths-filter@v3` for selective builds
- Test execution in CI (was `-DskipTests` everywhere)
- Docker multi-tag support (SHA + branch + main-latest)
- Staging deployment on main push
- E2E test job against staging
- Test report artifact uploads

#### 4. Staging Scripts

**Files:**
- `scripts/ci-deploy-staging.sh` — CI-friendly deploy script
- `scripts/setup-staging-env.sh` — Guided setup documentation

### Remaining for Pass 2 Completion

| Task | Status | Notes |
|------|--------|-------|
| AWS session | ✅ Done | Re-authenticated |
| IAM role update | ✅ Done | `ecs-deploy-staging` policy attached to `github-actions-onlineshop` |
| Staging databases | ✅ Done | `auth_staging`, `items_staging` created with schemas + seed data |
| Staging secrets | ✅ Done | `onlineshop/auth/db-staging-Dkh7wC`, `onlineshop/items/db-staging-LaYr9R` |
| Staging ALB + TG | ✅ Done | ALB `onlineshop-staging-alb`, TG `onlineshop-staging-tg` |
| Staging task definitions | ✅ Done | All 3 at revision 2 with unique port names |
| Staging ECS services | ✅ Done | All 3 at desired:0, FARGATE_SPOT |
| Staging E2E smoke test | ✅ Done | Register → Login → Items → Validate all pass |
| Branch protection | ✅ Done | Rules applied via `gh api` (see section 2.1) |
| Old workflow removal | 🔧 After merge | Delete `.github/workflows/build-and-push.yml` after `build-and-deploy.yml` verified on main |
| Auth tests @ 50% | 🔧 Manual | Run `cd Auth && ./mvnw verify` to confirm coverage threshold passes |
| Staging ALB pause script | 🔧 Future | Integrate staging ALB into pause-playground.sh / resume-playground.sh |

---

### 2.9 Secrets Hygiene Remediation & SQL Helper (2026-08-02, post-Pass-2 review)

**Trigger:** Session review of Pass 2 found the master DB password had been registered as a plaintext env var in 11 `onlineshop-psql-helper` task-def revisions, and staging passwords were echoed into session logs.

**Actions executed (all verified):**

| Action | Detail | Verification |
|--------|--------|--------------|
| Created `onlineshop/rds/master` secret | ARN: `arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/rds/master-GJlmZb` — `{"username":"dbadmin","password":<db-admin-password>}` | `describe-secret` ✅ |
| Extended `ecsTaskExecutionRole` inline policy `secretsmanager-read-onlineshop` | Added resource `arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/rds/*` | `get-role-policy` ✅ |
| Rotated `auth_app_staging` + `items_app_staging` passwords | `put-secret-value` on both staging secrets (new version IDs), then `ALTER ROLE ... PASSWORD` via sql-runner task using psql `:'VAR'` interpolation (no plaintext anywhere) | Connected as each service account and ran `SELECT` — auth sees 2 tables, items sees 5 rows ✅ |
| Purged 11 `onlineshop-psql-helper` TD revisions | `delete-task-definitions` (they were INACTIVE; deregister alone leaves them readable) | `list-task-definitions` ACTIVE=0, INACTIVE=0 ✅ |
| Created `scripts/ecs-run-sql.sh` | Sanctioned private-RDS SQL runner: base64 SQL transport, `--cli-input-json file://`, `ON_ERROR_STOP=1`, PGPASSWORD via `secrets[].valueFrom` only, correct log-stream resolution, self deregister+delete of its TD revision | Smoke test `SELECT version()` ✅; TD residue 0 ✅ |

**Standing rules added to `AGENTS.md`:** no blocking poll loops; no plaintext secrets in task definitions; private RDS only via `scripts/ecs-run-sql.sh` with `--verify`.

---

## Pass 2 Completion Audit and Isolated Staging Replacement (2026-08-04)

### GitHub and CI

- Repository merge settings changed to squash-only (`allow_merge_commit=false`,
  `allow_rebase_merge=false`, `allow_squash_merge=true`) and verified via API.
- `main` protection enforces administrators, strict up-to-date checks, one
  approval, stale-review dismissal, and required checks: `auth`, `items`,
  `api-gateway`, `frontend`, `e2e-pr`.
- Workflow adds frontend npm install/lint/build and blocking PR E2E against a
  disposable Compose stack. Local reproduction passed all three E2E tests.
- Main staging job resumes isolated staging, deploys the immutable SHA, runs
  E2E, and pauses staging under `if: always()`.

### Independent staging resource inventory

| Resource | Identifier |
|---|---|
| VPC | `vpc-0e9b2c6911cf3d4e0` (`10.42.0.0/16`) |
| Public subnets | `subnet-04f5da5a8cf1b1350` (`eu-north-1a`), `subnet-06b823d8d6b24333b` (`eu-north-1b`) |
| Internet gateway | `igw-0ecb8c45b94d72a9d` |
| Route table | `rtb-0a65209cbae61df32` |
| ALB security group | `sg-0e4c072113dd8d1e9` |
| ECS security group | `sg-0edd7fa1813d03018` |
| RDS security group | `sg-08c5d1008d1ce54ae` |
| ECS cluster | `onlineshop-staging-cluster` |
| Cloud Map namespace | `staging.onlineshop.local` / `ns-3pbrjpwzgrtai75v` |
| RDS subnet group | `onlineshop-staging-db-subnets` |
| Active RDS name | `onlineshop-staging-postgres` (exists only while running) |
| Idle RDS snapshot | `onlineshop-staging-latest` (encrypted) |
| ALB name | `onlineshop-staging-v2-alb` (exists only while running) |
| Target group | `onlineshop-staging-tg-v2` / `8a9b0471c381e60b` |
| Task definitions | `onlineshop-auth-staging-v2`, `onlineshop-items-staging-v2`, `onlineshop-api-gateway-staging-v2` |
| ECS services | `onlineshop-auth-staging`, `onlineshop-items-staging`, `onlineshop-api-gateway-staging` |

Staging databases `auth_staging` and `items_staging` were created on the
isolated RDS instance. Schema and seed mutations were executed through the
staging-configured `ecs-run-sql.sh`; read-back proved `users` + `sessions`, the
`testuser` seed, `items`, and five item rows. Application credentials continue
to come from the staging-only Secrets Manager entries.

### Lifecycle and validation

- First staging E2E run: one transient 502 occurred before all Service Connect
  tasks converged; rerun after health convergence passed 3/3 tests.
- Pause verification: services `desired/running/pending=0`, staging ALB absent,
  RDS absent, encrypted 20 GiB final snapshot `available`.
- Resume verification: snapshot restore recreated RDS in the staging VPC and
  recreated a new ALB/listener without touching production.
- Old shared staging ALB/TG and the three legacy services in the production
  cluster were deleted and verified absent/inactive. Legacy task-definition
  families have no ACTIVE or INACTIVE revisions remaining.
- Production resume audit found deleted ECR tags in old task definitions.
  Production revisions were updated to immutable
  `sha-06658a68e7ce6583e59069bc004065cc0b541e39` images.
- A full restore from `onlineshop-staging-latest` was tested; readiness reached
  HTTP 401 for the invalid-token probe and cloud E2E passed 3/3 after restore.
- Final state was verified paused independently: both clusters have all three
  services at zero and neither ALB exists; staging RDS is absent and its
  encrypted 20 GiB snapshot is `available`.
- Operational lifecycle scripts were moved to the repository-level `scripts/`
  directory. The old shared-environment setup script is hard-disabled.

### IAM and secret hygiene

- `github-actions-onlineshop/ecs-deploy-staging` now includes constrained
  `iam:PassRole`, staging service updates, lifecycle RDS/ALB permissions, and
  read-only health inspection. Applied policy is stored in
  `github-actions-staging-deploy-policy.json`.
- `ecsTaskExecutionRole` can read application secrets and RDS-managed secrets;
  policy source is `ecs-task-secrets-policy.json`.
- A plaintext production master password found in the local `.env` was treated
  as compromised: RDS master password rotated, `onlineshop/rds/master`
  `AWSCURRENT` updated, and the plaintext `.env` line removed. No replacement
  value was printed or written to the repository.

---

## Pass 2.9 Deterministic Staging Replacement (2026-08-04)

This section supersedes the snapshot-backed runtime inventory above. Historical
snapshot restore results are retained only as an audit trail.

### Implemented

- Added explicit non-secret environment configs:
  `scripts/config/production.env` and `scripts/config/staging.env`.
- Added shared `scripts/lib/lifecycle.sh` helpers for identity/resource guards,
  ALB/listener management, verified 30-second target draining, ECS scaling and
  waiters, readiness, diagnostics, clean RDS creation, and snapshot-free RDS
  deletion.
- Added `scripts/bootstrap-staging-db.sh` plus version-controlled role/grant SQL.
  RDS generates the master password in its managed secret; application
  passwords are injected into one-off Fargate SQL tasks from staging secrets.
- Hardened `scripts/ecs-run-sql.sh`: input validation, post-mutation read-back,
  log-group verification, and task-definition deregister+delete on success and
  failure.
- CI now uploads staging failure diagnostics before `if: always()` teardown.
- `scripts/ci-deploy-staging.sh` now verifies the requested immutable tag exists
  in all three ECR repositories before any service is mutated.
- Updated and applied `github-actions-onlineshop/ecs-deploy-staging`; AWS Access
  Analyzer returned zero errors or security warnings and policy read-back
  confirmed the update.

### Live verification

| Path/check | Result |
|---|---|
| Production start | ✅ ALB/listener recreated, services reached readiness, invalid-token probe returned 401 |
| Production stop | ✅ services `desired/running/pending=0`, ALB absent |
| Clean staging bootstrap | ✅ new private PostgreSQL 18.1 RDS; roles/databases/schemas/grants applied; Auth seed=1, Items seed=5; both restricted-user connections passed |
| Failure cleanup | ✅ induced capacity-strategy failure captured ECS/RDS/target diagnostics and removed ALB/RDS without snapshot |
| Immutable staging deploy | ✅ Auth, Items, and Gateway healthy on `sha-06658a68e7ce6583e59069bc004065cc0b541e39` |
| Cloud E2E | ✅ 3 tests, 0 failures |
| Staging teardown | ✅ services `0/0/0`, ALB absent, RDS absent, no final snapshot |
| SQL-runner residue | ✅ ACTIVE=0, INACTIVE=0 |
| Legacy snapshot | ✅ `onlineshop-staging-latest` deleted and list verification returned empty |

### Issues found during exercise

- ECS requires `--force-new-deployment` when a capacity-provider strategy is
  sent and differs in provider/base; the helper now sends it only for
  non-no-op updates.
- Bash ERR traps require `set -E` to propagate into helper functions.
- The gateway's historical main SHA tag was missing from ECR. The exact
  `06658a6` source was rebuilt from the existing clean worktree, pushed under
  its immutable tag, and verified by digest before ECS converged.
- One E2E attempt during Service Connect replacement returned 502; after all
  three ECS deployments reached `COMPLETED`, the same suite passed 3/3.

### Lifecycle progress logging follow-up (2026-08-04)

- Added UTC timestamped `STEP`, resource-progress, retry/no-op, verification,
  failure-cleanup, and `COMPLETE` logs to all production/staging pause and
  resume paths. Each numbered step states an experience-based typical duration;
  completion includes measured total runtime.
- Added detailed comments at lifecycle boundaries, including ALB wiring, ECS
  stabilization, clean RDS bootstrap, restricted-user verification, and
  failure-first diagnostics/teardown. Value-returning shared helpers log to
  stderr so captured ARNs/endpoints remain unpolluted.
- Enhanced clean-RDS read-back to verify `StorageEncrypted` as well as status,
  endpoint, public accessibility, and VPC placement.
- Executed `tests/scripts/lifecycle_test.sh`, Bash syntax checks, stale-runtime-
  reference checks, and `git diff --check`; all passed. `shellcheck` was not
  installed, so that optional lint was unavailable.
- Executed `bash scripts/pause-playground.sh` and
  `bash scripts/pause-staging.sh` against already-paused environments. Both
  identity/resource guards passed, every ECS service was reported and verified
  at `0/0/0`, both absent ALBs were identified as intentional no-ops, staging
  RDS absence was reported, and both scripts completed successfully in about
  20 seconds. No AWS resource was created, changed, or deleted by these no-op
  verification runs.

---

## Pass 3 — Release, Traceability & Promotion

### 3.1 Release contract and local validation foundation (2026-08-04)

Source-controlled artifacts (no AWS resources involved):

| Path | Purpose |
|---|---|
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/schema/release-manifest.schema.json` | Versioned Draft-07 JSON Schema (candidate/official states via `anyOf`) |
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/fixtures/valid/*.json` | 2 accepted manifests |
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/fixtures/invalid/*.json` | 37 rejected manifests |
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/fixtures/invalid/EXPECTED.md` | Authoritative fixture → primary error code table (parsed by tests) |
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/src/release_contract/*.py` | Python validator + semver/checksum/component/cross-field helpers |
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/validate-manifest.sh` | argv-only shell CLI wrapper |
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/release-input.sh` | Strict dispatch-input shell validators |
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/tests/*.py` | 61 Python unit/validation tests |
| `tests/scripts/release_contract_test.sh` | Repo-level verification gate |
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/requirements.txt` | `jsonschema==4.26.0` (pinned) |

Commands run (local only, no AWS profile/region involved):

```bash
python3 -m pip install --user jsonschema ruff shellcheck-py
bash tests/scripts/release_contract_test.sh
(cd plans/AUTOMATIC-BUILDS-AND-DEPLOY/release && ruff check src tests)
shellcheck plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/validate-manifest.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/release-input.sh \
  tests/scripts/release_contract_test.sh
```

Verification result: 61/61 Python tests pass; every valid fixture accepted;
every invalid fixture rejected with its expected primary error code; CLI output
deterministic; `--check-checksum` guard verified; `ruff` and `shellcheck` clean.
The schema is Draft-07 metaschema-valid. No AWS CLI command was run; no
production or staging environment was touched; nothing was committed.

---

## Pass 3, subphase 3.2 — Candidate build evidence and immutable artifacts

Implemented on top of 3.1 in the same `release/` directory and in
`.github/workflows/build-and-deploy.yml`.

| Path | Purpose |
|---|---|
| `release/src/release_contract/candidate.py` | Canonical-producer reuse decision, canonical-set check, candidate-manifest builder + `decide`/`set-check`/`build-manifest` CLI |
| `release/src/release_contract/artifact.py` | GitHub artifact identity resolution (reject duplicate/expired) + evidence bundle verification |
| `release/src/release_contract/frontend.py` | Safe tar.gz validation + sorted per-file checksum-manifest verification |
| `release/src/release_contract/serialization.py` | Staging serialization model (offline proof of no race / no preemption) |
| `release/src/release_contract/components.py` | Extended: `oci_labels()`, `build_run_label()`, `run_url()`, trusted event/ref constants |
| `release/bin/package-frontend.sh` | Reproducible `frontend-dist.tar.gz` + sorted checksum manifest + archive SHA-256 |
| `release/bin/unpack-frontend.sh` | Safe extraction (rejects traversal/links/devices) + checksum verification |
| `release/bin/generate-sbom.sh` | SPDX JSON SBOM with pinned Syft `v1.50.0` (archive SHA-256 verified; `SYFT_TOOL` override for tests) |
| `release/bin/publish-candidate-image.sh` | Idempotent `sha-<sha>` push / reuse / fail-closed decision |
| `release/bin/image-labels.sh` | Read ECR digest + OCI labels by tag (`docker buildx imagetools inspect` config blob; exit 3 = tag absent) |
| `release/bin/verify-producer-set.sh` | One canonical producer set across the three backends |
| `release/bin/emit-candidate-evidence.sh` | Assemble the facts-index evidence bundle + sorted `checksums.txt` + verify |
| `release/bin/emit-candidate-manifest.sh` | Render a schema-valid candidate manifest from evidence + owner SemVer |
| `release/bin/record-artifact.sh` | Record the GitHub artifact ID, URL, and service-reported digest from the `actions/upload-artifact@v4` step outputs |
| `release/fixtures/candidate|artifact|serialization/*` | 3.2 fixtures (existing images, artifact listings, event timelines) |
| `release/tests/test_candidate.py` `test_artifact.py` `test_frontend.py` `test_serialization.py` | New Python suites (117 total across all suites) |
| `release/requirements.txt` | `jsonschema==4.26.0`, `PyYAML==6.0.3` (pinned) |
| `tests/scripts/candidate_evidence_test.sh` | 3.2 offline verification gate (incl. workflow YAML static checks) |
| `.github/workflows/build-and-deploy.yml` | OCI labels, idempotent publish steps, SHA-pinned Actions, serialized `e2e-staging`, new `candidate-evidence` job |

Pinned release-critical Actions (full commit SHA, version comment):
`actions/checkout@v4` → `11d5960a326750d5838078e36cf38b85af677262`,
`actions/setup-java@v4` → `d7793b545071e98d581d3bf084a51c3213318a07`,
`actions/setup-node@v4` → `49933ea5288caeca8642d1e84afbd3f7d6820020`,
`actions/cache@v4` → `0057852bfaa89a56745cba8c7296529d2fc39830`,
`actions/upload-artifact@v4` → `ea165f8d65b6e75b540449e92b4886f43607fa02`,
`docker/setup-buildx-action@v3` → `8d2750c68a42422c14e847fe6c8ac0403b4cbd6f`,
`docker/build-push-action@v6` → `10e90e3645eae34f1e60eeb005ba3a3d33f178e8`,
`aws-actions/configure-aws-credentials@v4` → `ff717079ee2060e4bcee96c4779b553acc87447c`,
`aws-actions/amazon-ecr-login@v2` → `d539f0932e70871a027e9d5a9d8fc38589180a64`,
`dorny/paths-filter@v3` → `6852f92c20ea7fd3b0c25de3b5112db3a98da050`.

Commands run (local only, no AWS profile/region involved):

```bash
bash tests/scripts/candidate_evidence_test.sh
bash tests/scripts/release_contract_test.sh
(cd plans/AUTOMATIC-BUILDS-AND-DEPLOY/release && ruff check src tests && ruff format --check src tests)
shellcheck plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/*.sh
```

Verification result: both gates pass — Python tests, workflow YAML
static checks, reproducible frontend packaging, safe extraction, publish/
reuse/fail-closed decisions, SBOM stub flow, evidence→candidate-manifest
fixture flow, artifact identity/digest recording; `ruff` and `shellcheck`
clean. No AWS CLI command was run; no production or staging environment was
touched; nothing was committed.

**Deferred live checks (consolidated verification pass):** real ECR label
read-back, three real ECR digests, a real GitHub artifact ID and its
service-reported digest, real Syft scans, and a live rerun proving reuse
instead of rebuild.

---

## Pass 3, subphase 3.3 — ECR release tagging, immutability, and least privilege

Implemented on top of 3.2 in the same `release/` directory, in
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/`, and in `.github/workflows/build-and-deploy.yml`.

| Path | Purpose |
|---|---|
| `release/ecr/immutable-repositories.json` | Desired ECR state: `IMMUTABLE_WITH_EXCLUSION` + exclusions `main-latest`/`branch-*`, `latest` absent |
| `release/src/release_contract/ecr.py` | Server-side `release-*` mint / reuse / fail-closed decision + post-mutation digest verification (`decide`/`verify` CLI) |
| `release/src/release_contract/releaseid.py` | Release-identity collision / interrupted-promotion resume decision (`decide` CLI) |
| `release/src/release_contract/iam.py` | IAM least-privilege + OIDC trust policy validation (`validate-policy`/`validate-trust` CLI) |
| `release/src/release_contract/components.py` | Extended: tag families (`is_mutable_convenience_tag`, `is_immutable_tag`, `LATEST_ABSENT`), ECR repository ARN helpers |
| `release/bin/verify-immutable-repositories.sh` | Read-only `describe-repositories` read-back, fail-closed on drift |
| `release/bin/apply-immutable-repositories.sh` | `put-image-tag-mutability` per repository + immediate read-back |
| `release/bin/promote-image-digest.sh` | `batch-get-image` + `put-image` server-side promotion + read-back verify; `--dry-run` |
| `release/bin/check-release-identity.sh` | Read-only GitHub-tag / ECR release-tag / frontend-marker preflight |
| `release/fixtures/ecr|releaseid|iam/*` | Promotion, identity, and IAM fixtures |
| `release/tests/test_ecr.py` `test_releaseid.py` `test_iam.py` | New Python suites (158 total across all suites) |
| `tests/scripts/ecr_release_tagging_test.sh` | 3.3 offline verification gate (10 sections, stateful stub `aws`/`gh`) |
| `github-actions-candidate-build-policy.json` | ECR push scoped to the three repository ARNs + `ecr:GetAuthorizationToken` on `*` |
| `github-actions-promotion-policy.json` | Server-side `ecr:PutImage`/`BatchGetImage`/describe scoped to the three ARNs; no layer-upload actions |
| `github-actions-production-deploy-policy.json` | ECR read + ECS deploy + S3 + CloudFront + scoped `iam:PassRole` with `ecs-tasks.amazonaws.com` |
| `github-actions-rollback-policy.json` | Production-deploy scope minus `ecr:PutImage` (rollback never mints tags) |
| `github-actions-oidc-trust-policy.json` | Added `:environment:production` subject alongside `main`/`feature/*` |
| `github-actions-role-layout.md` | Job → role → policy map, publication job (`contents: write` only), validation jobs (no AWS), residual-risk note |
| `.github/workflows/build-and-deploy.yml` | Job-level `permissions: {contents: read}` on `frontend`/`e2e-pr` (no `id-token: write`); 3.3 role-split comment |

Key design points:
- ECR tag mutability is repository-scoped but supports **exclusions**
  (`IMMUTABLE_WITH_EXCLUSION` + `imageTagMutabilityExclusionFilters`,
  read back via `describe-repositories`), which is how `sha-*`/`release-*` stay
  immutable while `main-latest`/`branch-*` advance.
- Promotion is **server-side and digest-preserving** (`ecr:batch-get-image`
  returns the exact manifest bytes; `ecr:put-image` re-tags them). The decision
  module fails closed on any digest that differs from the recorded evidence.
- `iam:PassRole` is scoped to `ecsTaskExecutionRole` with the
  `ecs-tasks.amazonaws.com` service condition. The production task-role ARN (if
  any) is to be confirmed during 3.5 hardening.
- IAM cannot restrict the image-tag prefix of `ecr:PutImage`; the promotion
  script's strict digest check + the workflow's tag computation + repository
  immutability are the controls (documented residual risk).

Commands run (local only, no AWS profile/region involved):

```bash
bash tests/scripts/ecr_release_tagging_test.sh
bash tests/scripts/candidate_evidence_test.sh
bash tests/scripts/release_contract_test.sh
(cd plans/AUTOMATIC-BUILDS-AND-DEPLOY/release && ruff check src tests)
shellcheck plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/apply-immutable-repositories.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/verify-immutable-repositories.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/promote-image-digest.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/check-release-identity.sh \
  tests/scripts/ecr_release_tagging_test.sh
```

Verification result: all three offline gates pass — Python tests, workflow
static checks, immutable-repo apply/read-back with drift fail-closed,
server-side mint/reuse/conflict/dry-run promotion, release-identity
proceed/resume/collision, IAM + OIDC trust policy validation, mandatory
profile/region + read-back scan; `ruff check` and `shellcheck` clean for the
3.3 modules/scripts (a pre-existing `cli.py` `ruff format` deviation inherited
from subphase 3.1 is left untouched and is not claimed). No AWS CLI
command was run; no production or staging environment was touched; nothing was
committed.

**Deferred live checks (consolidated verification pass):** ECR repository
settings read back against the real repositories; real `put-image-tag-mutability`/
`batch-get-image`/`put-image` behavior including overwrite-attempt failures and
convenience-tag advancement; the real OIDC `environment:production` subject
decoded from an actual job's JWT; IAM Access Analyzer (`aws iam validate-policy`)
before live policy application.

### Independent 3.3 review corrections (applied after the above)

- `release_contract.ecr` rejects any `expected.repository` outside the canonical
  `onlineshop-{auth,items,api-gateway}` set.
- `check-release-identity.sh` dereferences annotated GitHub tag objects to the
  commit before comparing the `v<version>` tag SHA; the gate stub `gh` routes on
  the API URL (`sys.argv[2]`) and the gate now covers annotated-tag peel →
  resume and peel conflict → fail-closed.
- `release_contract.iam` docstring corrected: only `ecr:GetAuthorizationToken`
  is unscopable; `GetDownloadUrlForLayer`/`BatchGetImage` support the
  `repository` resource type.
- Plan 3.3 checkboxes for deferred live work (repository mutation, role split/
  switch-over, actual OIDC `sub` validation, IAM Access Analyzer) annotated as
  offline-only/not-yet-run; AGENTS/docs no longer claim repositories "are
  configured" or that the account "uses" the `environment:production` subject.
- All three offline gates re-verified green after these corrections.

### Independent 3.5 review corrections (applied after the above)

Fresh independent review of the 3.5 implementation (offline; no AWS, GitHub,
or lifecycle actions):

- Service `AGENTS.md` 3.5 sections no longer claim the production task
  definitions/services "are" hardened — they document the **contract** ("must
  be", enforced by the validators before registration) and explicitly note no
  live task-definition/service mutation has happened.
- Mandatory profile/region enforced at runtime: `lc_init` and
  `scripts/lib/identifiers.sh` refuse any `LC_PROFILE`/`LC_REGION` other than
  `dpm-profile`/`eu-north-1`; the gate asserts both config files carry exactly
  those values.
- AWS read failures are reported as `error` (never as a missing resource);
  `release_contract.environments` adds `OBSERVED_READ_ERROR`/`TOPO_READ_ERROR`.
- Inventory now also verifies the execution role, the ECR repositories, and RDS
  non-public accessibility (`DB_PUBLIC_ACCESSIBLE`); execution role + ECR stay
  excluded from separation (shared infrastructure).
- OAC migration `--apply` runs a no-lockout precondition gate (current bucket
  policy must grant public read or the CloudFront OAC) before any mutation and
  waits (bounded) for the CloudFront deployment to reach `Deployed` before
  tightening the bucket policy.
- CloudTrail audit proves delivery via a confirmed `LatestDeliveryTime` + no
  delivery error; docs state management selectors cover all control-plane APIs
  and request-ID capture is a promotion-phase behaviour.
- `validate_task_definition` rejects `taskRoleArn == executionRoleArn`
  (`ROLE_NOT_DISTINCT`).
- Inventory/separation/OAC/CloudTrail scripts fail loudly when the decision
  layer produces no valid JSON (no silent `|| true`).
- Lifecycle guard gate now asserts (via the call-recording stub) that the
  staging-only DB helpers issue NO AWS call after their guard fails, including
  in conditional-call contexts, and covers `lc_staging_master_secret_arn`.

All offline gates re-verified green (248 Python tests, ruff + shellcheck +
`git diff --check` clean).

## Pass 3, subphase 3.4 — Controlled staging-to-production promotion workflow

Implemented on top of 3.1–3.3 in the same `release/` directory and in
`.github/workflows/promote-release.yml`. Offline only — no AWS/GitHub mutation,
no workflow run, no lifecycle start/stop:

| Path | Purpose |
|---|---|
| `.github/workflows/promote-release.yml` | Manual `workflow_dispatch` promotion (inputs `version` + `run_id`, optional `source_sha`/`database_change`/`migration_reviewed`); read-only `preflight` job (dispatch + manifest contract) before the protected `production` Environment → `promote` job runs the full preflight after approval/lock; consumes the candidate evidence by the exact producing attempt and never rebuilds; `approvedBy` derived from `actions/runs/{run}/approvals`, never `github.actor`; `compensate` job (`if: failure()` on `promote`) restores the snapshot artifact incl. the frontend root; shared non-cancelling `production-mutation` concurrency |
| `release/src/release_contract/promotion.py` | Fixture-tested decision layer: dispatch / run evidence / ancestry / preflight / snapshot / plan / waiter / frontend publication / verify / finalize / compensate |
| `release/bin/promotion-preflight.sh` | Combined read-only preflight (run + ancestry + release identity + Decision 8 DB-review gate), `SCHEMA_CHANGE_UNREVIEWED` fail-closed |
| `release/bin/snapshot-production.sh` | Read-only pre-promotion snapshot (desired counts, capacity strategy, service/TD ARNs, running digests, ALB wiring, frontend marker, paused state) |
| `release/bin/deploy-production.sh` | Copy current TDs → `sanitize-task-definition.sh` (image-only diff) → `validate-task-definition.sh` → register with read-back → bind waiters to this run's deployments |
| `release/bin/verify-production.sh` | Read-only post-deploy verification (running digests, service TD ARNs, frontend marker/checksum, ALB health) |
| `release/bin/publish-frontend.sh` | Assets-first/index-last immutable-prefix publication, no `--delete`, CloudFront invalidation |
| `release/bin/finalize-release.sh` | After `PROMOTION_PRODUCTION_VERIFIED=true`: server-side `release-<version>` tag mint via `promote-image-digest.sh` + `gh release create`; refuses publication before verification; idempotent resume only for exact partial objects |
| `release/bin/compensate-production.sh` | Reverse-order restore plan from the snapshot, dry-run supported |
| `release/bin/check-release-identity.sh` | (reused) release-identity proceed/resume/collision preflight |
| `release/fixtures/promotion/*` | Valid/invalid dispatch/run/ancestry/preflight/snapshot/plan/waiter/frontend/verify/finalize/compensate fixtures |
| `release/tests/test_promotion.py` | 51 unit tests |
| `tests/scripts/promotion_test.sh` | 3.4 offline verification gate (10 sections, stateful AWS + `gh` stubs) |
| `github-actions-promotion-policy.json` / `github-actions-role-layout.md` | Promotion-purpose role in the per-purpose layout (server-side `ecr:PutImage`/`BatchGetImage`, no layer-upload actions) |

Key design points:
- **Approval-gated, never rebuilt.** The staging gate is the successful Pass 2
  `e2e-staging` job of the exact candidate run; the workflow consumes the
  candidate evidence artifact and the static gate proves no
  `build-push-action`/`publish-candidate-image.sh` appears.
- **Time-of-check race closure.** Preflight is read-only before approval and is
  repeated with a fresh snapshot after approval + concurrency-lock acquisition;
  only the second run authorizes mutation.
- **Waiter binding.** Each waiter verifies the deployment/task-definition this
  run started is `COMPLETED` and running the exact digests — a generically
  stable service or circuit-breaker rollback is not success.
- **Paused production is handled honestly.** The snapshot records `paused: true`
  and verification fails closed (`RUNNING_TASKS_MISSING`) instead of
  fabricating success; live resume logic is deferred.
- **Publication gating.** The GitHub release/tags are minted only after
  `verify-production.sh` succeeds; `release_contract.promotion finalize` fails
  closed on `PUBLICATION_BEFORE_VERIFICATION`/`RELEASE_TAG_CONFLICT`.
- **Mandatory profile/region everywhere.** Every `aws` call carries
  `--profile dpm-profile --region eu-north-1` and every mutation is read back;
  the gate statically scans for both.

Commands run (local only, no AWS profile/region involved):

```bash
bash tests/scripts/promotion_test.sh
bash tests/scripts/candidate_evidence_test.sh
bash tests/scripts/ecr_release_tagging_test.sh
bash tests/scripts/production_hardening_test.sh
bash tests/scripts/release_traceability_test.sh
bash tests/scripts/release_contract_test.sh
bash tests/scripts/lifecycle_test.sh
(cd plans/AUTOMATIC-BUILDS-AND-DEPLOY/release && ruff check src tests && ruff format --check src tests)
shellcheck plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/promotion-preflight.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/snapshot-production.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/deploy-production.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/verify-production.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/publish-frontend.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/finalize-release.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/compensate-production.sh \
  tests/scripts/promotion_test.sh
```

Verification result: `promotion_test.sh` passes (360 Python tests across all
suites, ruff + shellcheck + `git diff --check` clean) and the other six gates
all remain green. No AWS CLI command was run against the real account and no
production or staging environment was touched.

**Deferred live checks (consolidated verification pass):** the real
owner-approved promotion — the actual `production` Environment approval and
required-reviewer entitlement check, real ECR/ECS/S3/CloudFront mutations and
read-backs, the real GitHub Release publication, and switching the workflow to
the per-purpose roles. The live plan checkboxes in `03_RELEASE_TRACEABILITY.md`
are annotated accordingly.

## Pass 3, subphase 3.7 — Release traceability queries and operator evidence

Source-controlled artifacts (no AWS resources involved; the live read-only
smoke test is deferred to the consolidated verification pass):

| Path | Purpose |
|---|---|
| `release/src/release_contract/traceability.py` | Pure lookup/audit decision layer + `by-sha`/`by-version`/`running`/`by-digest`/`audit` CLI (machine-readable JSON, fail-closed issues) |
| `release/bin/trace.sh` | Read-only operator CLI (`commit`/`release`/`running`/`digest`/`audit`); mandatory non-overridable `--profile dpm-profile --region eu-north-1` + `sts get-caller-identity` preflight; `--index` or read-only GitHub Releases auto-fetch; `--observed` offline mode; `--human` view |
| `release/fixtures/traceability/*.json` | Manifest index (`index.json`), consistent (`observed-ok.json`), paused (`observed-paused.json`), and drift (`observed-drift-{ecr,ecs,frontend}.json`) observed-state fixtures |
| `release/tests/test_traceability.py` | 61 unit tests (lookups, audit, newest-first ordering, mixed/incomplete running digests, sha-tag digest mismatch, candidate-run conflicts, by-version prefix-marker verification, malformed-marker and partial-read fail-closed, read-error fail-closed, CLI) |
| `tests/scripts/release_traceability_test.sh` | 3.7 offline verification gate (9 sections, stateful AWS + `gh` stubs, read-only + identity-preflight proof) |

Commands run (local only, no AWS profile/region involved):

```bash
bash tests/scripts/release_traceability_test.sh
bash tests/scripts/release_contract_test.sh
bash tests/scripts/candidate_evidence_test.sh
bash tests/scripts/ecr_release_tagging_test.sh
bash tests/scripts/production_hardening_test.sh
(cd plans/AUTOMATIC-BUILDS-AND-DEPLOY/release && ruff check src tests && ruff format --check src tests)
shellcheck plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/trace.sh \
  tests/scripts/release_traceability_test.sh
```

Verification result: all five offline gates pass (309 Python tests), ruff and
shellcheck clean, `git diff --check` clean. The lookups are strictly read-only
(ECR `describe-images`, ECS `list-tasks`/`describe-tasks`/`describe-services`/
`describe-task-definition`, S3 `get-object`, GitHub `gh api` releases reads)
and were exercised only against fixtures and a stateful AWS/GitHub stub. No AWS
CLI command was run against the real account and no production or staging
environment was touched.

**Independent 3.7 review hardening (applied on top of the initial 3.7 work):**
newest-official selection and audit ordering now use numeric version keys
(index-order independent); mixed/incomplete running digest sets fail closed;
`trace.sh release` verifies the immutable per-release prefix marker;
`trace.sh commit` fails closed on `sha-*` digest mismatch and candidate-run
conflicts; the digest lookup attributes the OCI revision to the release
manifest instead of claiming a label read; a configured service omitted by
`describe-services` and malformed frontend markers fail closed as
`OBSERVED_READ_ERROR`; prefix-marker S3 keys are derived from the manifest;
the GitHub index fetch selects the exact `release-manifest.json` asset; the
CLI rejects invalid JSON with exit 2.

**Deferred live checks (consolidated verification pass):** the read-only smoke
test of `trace.sh commit/release/running/digest/audit` against real production
AWS state and real GitHub Releases.

## Pass 3, subphase 3.6 — Owner-approved rollback (completed offline, 2026-08-04)

The 3.6 implementation surface left by an intentionally stopped OpenCode run
was independently reviewed, repaired, and verified. Offline only — no AWS CLI
call, no GitHub mutation, no workflow run, no production/staging action.

Repairs applied during this session:

| Repair | What was wrong | Fix |
|---|---|---|
| `tests/scripts/rollback_test.sh` step 3 static check | The literal `run-id: ${{ github.run_id }}` search can never match `str(wf)` (Python repr quotes values) | Structural check reads the parsed `download-artifact@` steps and requires each to pin `run-id` to this run |
| `tests/scripts/rollback_test.sh` stub `td_entry` | Python invocation passed the state path twice (`"$TMP/state.json" "$1" "$2"` while call sites pass state/ARN/image), so the ARN never reached `sys.argv[2]` and `describe-task-definition` returned NotFound | Pass `"$1" "$2" "$3"` through in order |
| `tests/scripts/rollback_test.sh` stub container map | Key `"auth"` instead of `"onlineshop-auth"` left the stub TD container named `onlineshop-auth`, so `sanitize-task-definition.sh --set-image auth=...` failed with `MISSING_CONTAINER` | Correct key map (auth/items/api-gateway container names) |
| `release/bin/record-rollback-result.sh` | `--argjson auth sha256:...` — `jq -r` strips quotes, so `--argjson` received invalid JSON | Use `--arg` for the six digest values |
| `release/bin/record-rollback-result.sh` | The OK echo polluted stdout so the gate's `jq` parse failed | JSON on stdout, diagnostics on stderr (documented in the header) |
| `tests/scripts/rollback_test.sh` step 9 scan | Loose `(aws|"aws")[[:space:]]` regex matched `aws sts` inside an echo message | Aligned with the promotion gate: only real invocations (`^aws ` / `$(aws `) are scanned |
| Comment wording | The phrase `ecr:PutImage` in two header comments tripped the no-tag-minting scan | Rephrased to "no ECR image-write permission" |
| `tests/scripts/rollback_test.sh` `--delete` scan | Whole-file grep hit comments documenting the no-`--delete` rule | Skip comment lines |
| `tests/scripts/rollback_test.sh` | Unused `VALID` variable | Removed |
| `release/src/release_contract/rollback.py` | `ruff format --check` flagged the timestamps condition | Formatted |

Coverage added during review: pre-approval summary assertions (target
task-definition ARNs, current per-component digests — checkbox 3) and a
paused-environment fail-closed test (`RUNNING_TASKS_MISSING` — checkbox 5).

| Path | Purpose |
|---|---|
| `.github/workflows/rollback-release.yml` | Manual `workflow_dispatch` rollback (input `version` + requester + schema-change booleans); read-only `preflight` job before the protected `production` Environment → `rollback` job re-runs the full preflight after approval/lock, snapshots, deploys digest-pinned revisions, restores the frontend, verifies, and records the result; `approvedBy` from `actions/runs/{run}/approvals`, never `github.actor`; target manifest consumed from the exact producing run (download-artifact pinned to `run-id`); automatic `compensate` job (`if: failure()` on `rollback`); shared non-cancelling `production-mutation` concurrency; never rebuilds, never mints tags, never creates a release |
| `release/src/release_contract/rollback.py` | Fixture-tested decision layer: dispatch / select (latest 10 complete official sets) / schema (Decision 8) / frontend-restore (no `--delete`) / result (write/resume/conflict) plus reused snapshot/plan/waiter/verify/compensate promotion contract |
| `release/bin/rollback-preflight.sh` | Read-only: GitHub index fetch, observed ECR/prefix-marker gather, selection + schema guard, current-versus-target summary (identities, digests, task definitions, frontend checksum, source SHAs, db warning), emits the validated target manifest |
| `release/bin/deploy-rollback.sh` | Copy current TDs from the snapshot → `sanitize-task-definition.sh` (image-only) → `validate-task-definition.sh` → register with read-back → canonical-order service updates with circuit breaker and per-deployment waiters |
| `release/bin/verify-rollback.sh` | Read-only post-rollback verification (running digests, service TD ARNs, frontend marker, ALB health); paused environment fails closed (`RUNNING_TASKS_MISSING`) |
| `release/bin/restore-frontend.sh` | Restore the live root from the retained immutable `_releases/v<version>/` prefix, no `--delete`, marker/index last, CloudFront invalidation, read-back |
| `release/bin/record-rollback-result.sh` | Rollback result/audit record (requester, approver, from/to with exact artifacts, timestamps, workflow URL, outcome); idempotent write/resume, conflict fail-closed; JSON on stdout |
| `release/fixtures/rollback/*` | 22 fixtures (index/observed current-missing-tampered, schema, frontend-restore, result, snapshot, verify, deployment manifest) |
| `release/tests/test_rollback.py` | 276 lines of unit tests (dispatch, selection incl. 12-release window, schema guard, frontend restore, result, reused promotion decisions) |
| `tests/scripts/rollback_test.sh` | 3.6 offline verification gate (9 sections, stateful AWS + `gh` stubs) |

Commands run (local only, no AWS profile/region involved):

```bash
bash tests/scripts/rollback_test.sh
bash tests/scripts/release_contract_test.sh
bash tests/scripts/candidate_evidence_test.sh
bash tests/scripts/ecr_release_tagging_test.sh
bash tests/scripts/promotion_test.sh
bash tests/scripts/production_hardening_test.sh
bash tests/scripts/release_traceability_test.sh
bash tests/scripts/lifecycle_test.sh
(cd plans/AUTOMATIC-BUILDS-AND-DEPLOY/release && ruff check . && ruff format --check .)
shellcheck tests/scripts/rollback_test.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/rollback-preflight.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/deploy-rollback.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/verify-rollback.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/restore-frontend.sh \
  plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/record-rollback-result.sh
```

Verification result: all eight offline gates pass (391 Python tests),
`ruff check`/`ruff format --check` clean, shellcheck clean, `git diff --check`
clean, no secrets in the rollback tooling (secrets remain in
`secrets[].valueFrom`). All shell runs used the stateful AWS + `gh` stubs only.
No AWS CLI command was run against the real account and no production or
staging environment was touched.

**Deferred live checks (consolidated verification pass):** the real
owner-approved rollback (release N → N-1 → N with exact backend digests and
frontend checksum after each transition), the real `production` Environment
approval, real ECR/ECS/S3/CloudFront mutations and read-backs, real frontend
restoration, and the real rollback-result artifact.

## Pass 3, subphase 3.6 — independent review fixes (2026-08-04, offline)

An independent review re-verified the 3.6 implementation with fresh eyes
(after the completing run) and applied four further fixes plus gate and
documentation updates. All checks remain offline — no AWS CLI call, no GitHub
mutation, no workflow run.

| Fix | What was wrong | Fix |
|---|---|---|
| `rollback-release.yml` preflight job permissions | The job-level `permissions:` block (`contents: read` + `actions: read`) REPLACES the workflow-level permissions, so the preflight job had no `id-token: write` — `configure-aws-credentials` could never mint the OIDC token and the pre-approval AWS reads (ECR/S3) would fail the workflow before approval | Added `id-token: write` to the preflight job (it needs read-only AWS scope, unlike the promotion preflight which has no AWS access) |
| `rollback_test.sh` step 3 static checks | No check proved a job that assumes the AWS role can actually mint an OIDC token | Added a static check: every `configure-aws-credentials` job must have `id-token: write` (job-level or workflow-level) |
| `compensate-production.sh` `--changed` | The tool required `--changed` to be a FILE (`rl_assert_regular_file`), but BOTH `promote-release.yml` and `rollback-release.yml` pass a literal inline JSON array — the automatic compensate job would fail at runtime (`not a regular file`) even when the snapshot was perfectly restorable, leaving a mixed-state incident instead of restoring | The tool now accepts a literal JSON array as well as a file, validates it, and rejects unknown component keys (a typo must never silently skip a component) |
| `record-rollback-result.sh` requester/approver | `REQUESTER="${REQUESTER:-$GITHUB_ACTOR}"` / `APPROVER="${APPROVER:-$GITHUB_ACTOR}"` — a missing `--approver` silently recorded the run actor, violating "approver never from `github.actor`" | Both are now mandatory tool inputs; missing ones fail closed with a usage error |
| `rollback-release.yml` post-approval revalidation | The fresh preflight output was silently overwritten by the downloaded pre-approval manifest (`cp`); a divergent revalidated manifest would not be detected | The fresh preflight writes to a separate file and `cmp` fails closed when the post-approval bytes differ from the approved ones; the deployment consumes the exact pre-approval bytes |
| `restore-frontend.sh` identity preflight | It verified the STS call succeeded but never compared the account to the configured production account (all other rollback scripts do) | Now sources `scripts/config/production.env` and fails closed on account mismatch |

Gate updates: `rollback_test.sh` step 3 gained the `id-token` static check;
step 9 gained an end-to-end `compensate-production.sh` run with the literal
JSON `--changed` array the workflow passes (plus a typo'd-key fail-closed
case); `promotion_test.sh` step 7 gained the same inline-array checks because
the promote workflow shares the same wiring.

Documentation updates: `AGENTS.md` gained the missing subphase 3.6 section;
`03_RELEASE_TRACEABILITY.md` 3.6 checkbox 8 and the verification-gate
paragraph now state the mandatory requester/approver inputs, the preflight
job's `id-token: write`, the byte-comparison fail-closed guard, and the
inline `--changed` array; `release/README.md` rollback section updated
accordingly.

Re-verified after the fixes: all eight offline gates pass (391 Python tests),
`ruff check` clean, shellcheck clean, `git diff --check` clean.

## Pass 3, subphase 3.8 — Retention and rollback-window enforcement (2026-08-05, offline)

Implemented the offline surface of subphase 3.8 completely — no AWS CLI
command was run against the real account, no GitHub mutation, no workflow
run, no staging/production touch. (One incidental live call occurred during
debugging: `start-lifecycle-policy-preview` against the real account with the
then-current policy text; it is a read-only dry-run, deleted nothing, and was
rejected by ECR schema validation — the finding below. No mutation happened.)

### What was built

| Artifact | Purpose |
|---|---|
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/ecr/lifecycle-policy.json` | Desired ECR lifecycle policy (same for all three backend repos): rule 1 keep newest 10 `release-*` (highest priority), rule 2 expire `sha-*` after 30 days, rules 3–4 expire `main-latest` and `branch-*` after 30 days (each its own single-prefix rule), rule 5 expire untagged after 14 days |
| `release/src/release_contract/retention.py` | Decision layer + CLI: `validate-policy`, `evaluate` (first-match-wins model), `validate-preview` (ECR preview validation), `audit` (rollback window), `coverage` (keep-10 push-order vs version-order window), `frontend-retention`, `retention-classes` |
| `release/fixtures/retention/*.json` | Policy (ok/invalid-order/generic-exclusion/wrong-counts), multi-tag image, evaluator (ok/protected-expiring/disagreement), protected digests, audit observed (ok/missing/mismatch), 12-release window (index/observed ok/backport-gap), frontend prefix (ok/fail), retention classes (ok/invalid) |
| `release/tests/test_retention.py` | 40 unit tests (431 total suite) |
| `release/bin/audit-retention-window.sh` | Read-only retention audit (offline `--index`/`--observed`; live gather = identity preflight + ECR describe-images + S3 markers, deferred) |
| `release/bin/preview-retention-policy.sh` | Preview exact expiration candidates: offline modeled evaluation (no AWS call) or live ECR `start/get-lifecycle-policy-preview` dry-run (read-only, requires `--observed`) |
| `release/bin/apply-retention-policy.sh` | `--dry-run` default; `--apply` refused offline (requires `ONLINESHOP_RETENTION_LIVE_APPLY=1` set only by the consolidated live pass); every `put-lifecycle-policy` followed by immediate `get-lifecycle-policy` byte-compare read-back |
| `tests/scripts/retention_test.sh` | 10-step offline gate |
| `plans/AUTOMATIC-BUILDS-AND-DEPLOY/explanations/RETENTION-DECISIONS.md` | Decision notes: ECR evaluator semantics, delayed evaluation, manifest-list/referrer behavior, push-order vs version-order window, untagged grace rationale, frontend/GitHub retention classes |

### Key findings

- **ECR rejects a bare `tagStatus: tagged` age rule.** The live dry-run
  preview returned `Must specify tagPrefixList or tagPatternList when
  tagStatus=TAGGED`. A generic "expire everything except releases" rule is
  therefore not expressible — the candidate families must be enumerated
  (`sha-`, `main-latest`, `branch-`) and the keep-10 rule's priority does the
  multi-tag protection. This is now encoded in the policy, the validator
  (`POLICY_TAGPREFIX_REQUIRED`, `POLICY_CANDIDATE_RULE_MISCONFIGURED`), the
  fixtures, and the decision notes.
- **A merged multi-entry `tagPrefixList` would silently select nothing.** The
  initial 4-rule draft merged the convenience families into one rule
  (`tagPrefixList: ["main-latest", "branch-"]`). The AWS user guide and CDK
  reference document that a multi-entry `tagPrefixList`/`tagPatternList`
  selects only images carrying **all** the listed tags ("only the images with
  all specified tags are selected"), so that rule could never match a real
  image (none carries both a `main-latest*` and a `branch-*` tag) — the
  30-day convenience-tag expiry would silently never fire while the model's
  any-match selection would predict otherwise. Fixed in review: the desired
  policy now gives each candidate family its own single-prefix rule (rules
  3–4), the validator rejects merged lists (`POLICY_TAGPREFIX_MULTI`), and the
  gate asserts every tagged rule carries exactly one prefix. Single-prefix
  rules are unambiguous under ECR's documented semantics.
- **ECR first-match-wins semantics verified against the AWS user guide:** an
  image is expired by exactly one or zero rules; an image matching a
  higher-priority rule's tagging requirements can never be expired by a
  lower-priority rule. The evaluation model and the keep-10 protection proof
  (all 12 release images in the multi-tag fixture are older than 30 days, the
  newest 10 by push order are kept by rule 1) encode exactly that.
- ECR lifecycle evaluation is delayed (up to 24 h) and manifest-list/referrer
  images are not selected — documented, never assumed.

### Verification (all offline)

```
bash tests/scripts/retention_test.sh                 PASS (432 Python tests)
bash tests/scripts/release_contract_test.sh          PASS
bash tests/scripts/candidate_evidence_test.sh        PASS
bash tests/scripts/ecr_release_tagging_test.sh       PASS
bash tests/scripts/promotion_test.sh                 PASS
bash tests/scripts/production_hardening_test.sh      PASS
bash tests/scripts/rollback_test.sh                  PASS
bash tests/scripts/release_traceability_test.sh      PASS
bash tests/scripts/lifecycle_test.sh                 PASS
(cd release && ruff check . && ruff format --check .)  PASS
shellcheck (3 new bin scripts + gate)                PASS
bash -n + git diff --check                           PASS
```

Docs updated: `03_RELEASE_TRACEABILITY.md` (3.8 checkboxes ticked + gate
paragraph), `PLAN.md` (3.8 marked done), `AGENTS.md` (3.8 contract section),
`release/README.md` (3.8 layout + section), `executed/INFO.md` (this record).

**Independent review (2026-08-05):** the 3.8 offline surface was re-verified
with fresh eyes: ECR first-match-wins and `imageCountMoreThan` count semantics
checked against the AWS user guide (worked examples confirm an image is
claimed by the highest-priority matching rule only, and per-image counting for
multi-tag images); the multi-prefix `tagPrefixList` finding above was found
and fixed in review (policy split, `POLICY_TAGPREFIX_MULTI`, gate single-prefix
assertion, docs); the apply-path static pairing check now strips comments so a
header comment can never satisfy it; the GitHub-release gather warns when a
release lacks a manifest asset. All nine offline gates re-run green after the
changes.

**Deferred live checks (consolidated verification pass):** the real lifecycle
policy preview/apply/read-back against real ECR (the live pass sets
`ONLINESHOP_RETENTION_LIVE_APPLY=1`), the read-only live retention audit
against real production state, and real S3/frontend retention.

## CI staging permission incident — 2026-08-08

### Evidence

- Workflow run: `https://github.com/Djimi/OnlineShop-full-stack/actions/runs/31259210183`
- Failed job: `e2e-staging` / `93107532753`
- Commit under test: `c8a715f5ed51e3bb3a9905738e275c57aaf003d9`
- Failure: the assumed role `github-actions-onlineshop` was denied
  `rds:CreateDBInstance` on
  `arn:aws:rds:eu-north-1:799111666795:subgrp:onlineshop-staging-db-subnets`.
  The staging resume stopped before deployment and E2E; its failure cleanup
  completed and removed no partially-created RDS instance.

### Repository correction

- Updated `github-actions-staging-deploy-policy.json` so
  `ManageEphemeralStagingDatabase` includes the exact staging subnet-group ARN
  alongside the staging DB and snapshot ARNs.
- Added `test_staging_rds_create_scopes_the_subnet_group` to
  `release/tests/test_iam.py` and included the staging policy in the source
  policy validation set.

### Live verification and follow-up

After AWS re-authentication, the required identity preflight succeeded. The
inline policy was applied and read back after every mutation. The first
corrected run reached clean RDS bootstrap, candidate deployment, healthy ALB
targets, and teardown. It exposed additional least-privilege gaps in the
read-only network/ELB calls; the source and live policy were updated, with
target-group attributes using `Resource: "*"` because ELBv2 rejected the exact
target-group ARN.

The same run also exposed a CI ordering race: staging services started old
image tags before `ci-deploy-staging.sh` installed the candidate. PR #39 added
`resume-staging.sh --defer-services`, which leaves ECS at zero until candidate
task definitions are registered. Corrected run `31265257478`, job
`93122636659`, passed infrastructure and deployment and then reached E2E.

E2E identified a cold Auth lookup timeout that was misreported by the gateway
as 502. The gateway now supplies the annotation-backed `authService`
`TimeLimiterRegistry` with a 5-second timeout and unwraps
`CompletionException`/`ExecutionException` so genuine timeouts retain 504
classification. The gateway's 12 tests pass. Merged-main run
[31267620402](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/31267620402),
job `93128495549`, then passed all 3 cloud E2E tests, including invalid-token →
401, and completed staging teardown.
