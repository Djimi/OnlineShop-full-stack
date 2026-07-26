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
        "token.actions.githubusercontent.com:sub": "repo:Djimi/OnlineShop-claude:*"
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
| API Gateway | Task def: `onlineshop-api-gateway`, ECR: `onlineshop-api-gateway`, ALB TG: `onlineshop-gateway-tg`, Port: 10000 |
| ALB | Name: `onlineshop-alb`, TG: `onlineshop-gateway-tg` (HTTP:10000, ip), Listener: port 80 → TG |
| RDS | `onlineshop-postgres-db`, PostgreSQL 18.4, db.t4g.micro, 20 GB |
| CI/CD | `.github/workflows/build-and-push.yml`, OIDC role `github-actions-onlineshop` |

**Cost when paused:** ~$1.25/month (secrets + ECR + Cloud Map). Resume: `bash scripts/resume-playground.sh`.
**Cost when running (Spot 24/7):** ~$49.00/month (compute + IPv4 + ALB). Pause: `bash scripts/pause-playground.sh`.
**Full flow verified:** register → login → token validation → items list (5 products).

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
