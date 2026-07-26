# INITIAL DEPLOYMENT TUTORIAL — Learn What We Did

> Educational companion to `INITIAL-DEPLOYMENT.md`. Each section explains *what*, *why*, and *how to do it via AWS Console UI*.

---

## 0. AWS Profile Setup

```bash
aws sts get-caller-identity --profile dpm-profile --region eu-north-1
# Output: Account 799111666795, User admin
```

**What this means:** Every AWS CLI command needs authentication. Our profile `dpm-profile` is an IAM user named `admin`. The region is Stockholm (`eu-north-1`).

**UI equivalent:** Console → click your username top-right → shows Account ID and region.

---

## 1. ECR — Docker Image Registry

### What is it?
Amazon Elastic Container Registry. Like Docker Hub, but private to our AWS account. Each microservice has its own repository.

### Repositories Created
```
onlineshop-auth       →   799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth
onlineshop-items      →   799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items
onlineshop-api-gateway → 799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-api-gateway
```

### UI: How to see it
AWS Console → Elastic Container Registry → Repositories → Private

### CLI: Build & Push
```bash
# 1. Login Docker to ECR
aws ecr get-login-password --profile dpm-profile --region eu-north-1 | \
  docker login --username AWS --password-stdin 799111666795.dkr.ecr.eu-north-1.amazonaws.com

# 2. Build image (from Items/ directory)
cd Items
docker build -t 799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items:sha-ba7905d .

# 3. Push
docker push 799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-items:sha-ba7905d
```

### Why SHA tags?
Each image is tagged with the git commit SHA (`sha-ba7905d`). This is immutable — we always know exactly which code is in which image. `latest` is a mutable tag that should be avoided in production.

---

## 2. ECS Cluster — Container Orchestration

### What is it?
Elastic Container Service. Manages where and how Docker containers run. We use **Fargate** (serverless) — no EC2 instances to manage.

```bash
aws ecs create-cluster --profile dpm-profile --region eu-north-1 \
  --cluster-name onlineshop-cluster
# → Cluster ARN: arn:aws:ecs:eu-north-1:799111666795:cluster/onlineshop-cluster
```

**UI:** ECS → Clusters → Create Cluster → "Networking only (Fargate)"

---

## 3. IAM Execution Role — Giving ECS Permission

### What is it?
ECS needs an IAM role to:
- Pull images from ECR
- Send logs to CloudWatch
- Read secrets from Secrets Manager

Without this role, ECS can't even start the container.

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

# Attach AWS-managed policy (ECR + CloudWatch)
aws iam attach-role-policy --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

# Custom policy for Secrets Manager
aws iam put-role-policy --role-name ecsTaskExecutionRole \
  --policy-name secretsmanager-read-onlineshop \
  --policy-document '{
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

**UI:** IAM → Roles → Create Role → AWS Service → Elastic Container Service → Elastic Container Service Task → attach policies

---

## 4. Security Groups — Network Firewall

### What are they?
Virtual firewalls that control which traffic can reach which resources. Every ECS task and the ALB gets a security group.

### Our Security Groups

```
Internet → [ALB SG: port 80] → [ECS SG: all ports] → tasks
                                [ECS SG] → [DB SG: port 5432] → RDS
```

| Rule | Source | Destination | Port |
|------|--------|-------------|------|
| Public HTTP | 0.0.0.0/0 | ALB SG | 80 |
| ALB → Tasks | ALB SG | ECS SG | all |
| Tasks → DB | ECS SG | DB SG | 5432 |
| **Task → Task (MISSING!)** | ECS SG | ECS SG | 9000, 9001 |

### CLI
```bash
# Create ALB SG
aws ec2 create-security-group --group-name onlineshop-alb-sg --vpc-id vpc-xxx

# Allow HTTP from anywhere
aws ec2 authorize-security-group-ingress --group-id sg-xxx \
  --protocol tcp --port 80 --cidr 0.0.0.0/0

# Allow ECS to receive from ALB
aws ec2 authorize-security-group-ingress --group-id $ECS_SG \
  --protocol tcp --port 0-65535 --source-group $ALB_SG

# Allow RDS to receive from ECS
aws ec2 authorize-security-group-ingress --group-id $DB_SG \
  --protocol tcp --port 5432 --source-group $ECS_SG
```

### What's Missing: Self-Referencing Rule
The API Gateway needs to call Auth (port 9001) and Items (port 9000). They're all in the same ECS SG. Security groups don't automatically allow traffic between members — we need an explicit self-referencing rule:
```bash
aws ec2 authorize-security-group-ingress --group-id $ECS_SG \
  --protocol tcp --port 9000-9001 --source-group $ECS_SG
```

**UI:** VPC → Security Groups → select ECS SG → Inbound Rules → Edit → Add Rule → Type: Custom TCP, Port: 9000-9001, Source: same SG ID

---

## 5. ECS Task Definition — Container Blueprint

### What is it?
Declares what Docker image, how much CPU/memory, which ports, environment variables, health checks, and secrets each container needs.

### Key Parts

**CPU/Memory:**
- Auth/Items: 256 CPU (0.25 vCPU) / 512 MB
- API Gateway: 512 CPU / 1024 MB (+128 MB Redis sidecar)

**Secrets from Secrets Manager:**
```json
{
  "name": "SPRING_DATASOURCE_PASSWORD",
  "valueFrom": "arn:aws:secretsmanager:...:secret:onlineshop/auth/db-umtxh1:password::"
}
```
The `:password::` suffix tells ECS to extract the `password` field from the JSON secret.

**Health Check:**
```json
{
  "command": ["CMD-SHELL", "curl -f http://localhost:9001/actuator/health/liveness || exit 1"],
  "startPeriod": 180
}
```
- `startPeriod: 180` — wait 3 minutes before health checks (Spring Boot needs ~90-120s to start + DB connection)
- If health check fails 3 times, ECS restarts the container

**Redis Sidecar (API Gateway only):**
```json
{
  "name": "redis-sidecar",
  "image": "public.ecr.aws/docker/library/redis:7.4-alpine",
  "essential": false,
  "memoryReservation": 128,
  "command": ["redis-server", "--save", "", "--maxmemory", "64mb", "--maxmemory-policy", "allkeys-lru"]
}
```
Runs alongside the API Gateway in the same task, accessible at `localhost:6379`.

**UI:** ECS → Task Definitions → Create New Task Definition → Fargate

---

## 6. ALB — Application Load Balancer

### What is it?
Entry point for internet traffic. Receives HTTP requests and forwards them to ECS tasks.

```bash
aws elbv2 create-load-balancer --name onlineshop-alb \
  --subnets subnet-xxx subnet-yyy subnet-zzz \
  --security-groups sg-alb
# → DNS: onlineshop-alb-199112777.eu-north-1.elb.amazonaws.com
```

**Target group** — the backend that ALB sends traffic to:
```bash
aws elbv2 create-target-group --name onlineshop-gateway-tg \
  --protocol HTTP --port 10000 --target-type ip --vpc-id vpc-xxx
```

**Listener** — what port the ALB listens on and where to forward:
```bash
aws elbv2 create-listener --load-balancer-arn $ALB_ARN \
  --protocol HTTP --port 80 \
  --default-actions Type=forward,TargetGroupArn=$TG_ARN
```

**UI:** EC2 → Load Balancers → Create → Application Load Balancer

---

## 7. ECS Service — Keep Containers Running

### What is it?
Ensures the desired number of task copies are always running. If a task dies, ECS starts a new one.

```bash
aws ecs create-service \
  --cluster onlineshop-cluster \
  --service-name onlineshop-auth \
  --task-definition onlineshop-auth:3 \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[...],securityGroups=[$ECS_SG],assignPublicIp=ENABLED}"
```

**Service Connect** — enables DNS-based service discovery between tasks. Each task gets an Envoy proxy sidecar that handles DNS resolution of short names to task IPs automatically:
```json
{
  "enabled": true,
  "namespace": "onlineshop.local",
  "services": [{
    "portName": "auth-port",
    "clientAliases": [{"port": 9001, "dnsName": "auth"}]
  }]
}
```
This makes `auth` resolve to the Auth task's IP (port 9001) and `items` to the Items task's IP (port 9000) from within any task in the `onlineshop.local` namespace. No `.onlineshop.local` suffix needed — the Envoy proxy resolves bare names.

**UI:** ECS → Cluster → Create Service → Fargate

---

## 8. RDS Connection Pool Issue

### Problem
Auth had `spring.datasource.hikari.maximum-pool-size: 100` in its application.yml. The `db.t4g.micro` instance supports ~25 max connections. Both Auth and Items tried to open connections, exhausting the pool.

### Error
```
FATAL: remaining connection slots are reserved for roles with privileges of the "pg_use_reserved_connections" role
```

### Fix
Overrode pool size via environment variable in task definition:
```
SPRING_DATASOURCE_HIKARI_MAXIMUMPOOLSIZE=10
SPRING_DATASOURCE_HIKARI_MINIMUMIDLE=1
```

**Lesson:** Always override application.yml defaults for production. A `t4g.micro` RDS needs pool sizes ≤10.

---

## 9. Redis Startup Race

### Problem
The API Gateway connects to Redis eagerly during Spring context initialization. If Redis sidecar isn't ready yet, the app crashes.

### Error
```
Unable to connect to localhost/<unresolved>:6379
Connection initialization timed out after 100 millisecond(s)
```

### Fix
1. **`dependsOn: {condition: HEALTHY}`** in task definition — Gateway container waits for Redis health check to pass before starting
2. **`GATEWAY_RATELIMIT_ENABLED=false`** — disabled rate limiting (which requires Redis) to avoid crash

---

## 10. Service Connect DNS (FIXED)

### Problem
`auth.onlineshop.local` didn't resolve from within the API Gateway task. The Cloud Map namespace existed but Service Connect was never enabled on the ECS services — no Envoy proxy sidecar was running, so DNS resolution failed.

### Error
```
java.nio.channels.UnresolvedAddressException: http://auth.onlineshop.local:9001/api/v1/auth/login
```

### Root Cause
`serviceConnectConfiguration` was `null` on all three ECS services. The Cloud Map namespace (`onlineshop.local`) and service entries existed as orphaned artifacts from a previous attempt, but were not connected to the running ECS tasks.

### Fix (2026-07-26)
Enabled Service Connect via `update-service` on all three services:

```bash
# Auth — expose itself as "auth" on port 9001
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service onlineshop-auth \
  --service-connect-configuration '{
    "enabled": true, "namespace": "onlineshop.local",
    "services": [{"portName": "auth-port", "clientAliases": [{"port": 9001, "dnsName": "auth"}]}]
  }'

# Items — expose itself as "items" on port 9000
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service onlineshop-items \
  --service-connect-configuration '{
    "enabled": true, "namespace": "onlineshop.local",
    "services": [{"portName": "items-port", "clientAliases": [{"port": 9000, "dnsName": "items"}]}]
  }'

# API Gateway — client only (no exposed port)
aws ecs update-service --profile dpm-profile --region eu-north-1 \
  --cluster onlineshop-cluster --service onlineshop-api-gateway \
  --service-connect-configuration '{"enabled": true, "namespace": "onlineshop.local"}'
```

Updated `SPRING_APPLICATION_JSON` in the gateway task definition (revision 12) to use DNS names:
```json
{"gateway":{"auth":{"service-url":"http://auth:9001"},"items":{"service-url":"http://items:9000"}}}
```

**Key insight:** Service Connect injects an Envoy proxy sidecar into each task. The sidecar handles DNS resolution of short names (`auth`, `items`) → task IPs automatically.

### Previous Workaround (deprecated)
The old workaround hardcoded private IPs in `SPRING_APPLICATION_JSON`, which broke on every task restart:
```json
{"gateway":{"auth":{"service-url":"http://172.31.23.124:9001"},"items":{"service-url":"http://172.31.26.229:9000"}}}
```

---

## 11. Self-Referencing SG Rule — FIXED

### Problem
ECS security group `sg-0b209104a6b15b157` had no self-referencing rules. API Gateway couldn't reach Auth:9001 or Items:9000 inside the same SG.

### Fix
```bash
aws ec2 authorize-security-group-ingress --profile dpm-profile --region eu-north-1 \
  --group-id sg-0b209104a6b15b157 --protocol tcp --port 9000-9001 \
  --source-group sg-0b209104a6b15b157
aws ec2 authorize-security-group-ingress --profile dpm-profile --region eu-north-1 \
  --group-id sg-0b209104a6b15b157 --protocol tcp --port 6379 \
  --source-group sg-0b209104a6b15b157
```
**UI:** VPC → Security Groups → `sg-0b209104a6b15b157` → Inbound Rules → Add: Custom TCP 9000-9001 / Source: same SG ID

---

## 12. Resilience4j TimeLimiter Timeout — FIXED

### Problem
Gateway's `DefaultAuthServiceClient.validateToken()` calls Auth to validate tokens. Resilience4j `TimeLimiter` was 3 seconds — too short for ECS task-to-task latency.

### Error
```
DefaultAuthServiceClient: Auth service timed out
TimeLimiter 'authService' recorded a timeout exception.
```

### Fix
`api-gateway/src/main/java/.../config/ResilienceConfig.java` line 97:
```java
// Before
.timeLimiter("authService", config -> config
    .timeoutDuration(Duration.ofSeconds(3)));
// After
.timeLimiter("authService", config -> config
    .timeoutDuration(Duration.ofSeconds(5)));
```

Rebuilt image `onlineshop-api-gateway:sha-ba7905d`, pushed to ECR, deployed as revision 11.

---

## 13. Full Smoke Test — VERIFIED WORKING

```bash
ALB="http://onlineshop-alb-199112777.eu-north-1.elb.amazonaws.com"

# 1. Register
curl -s -X POST $ALB/auth/register -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo123"}'
# → 201 Created: {"userId":2,"username":"demo",...}

# 2. Login
curl -s -X POST $ALB/auth/login -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo123"}'
# → 200 OK: {"token":"0490ea0297a2d...","expiresIn":3600,...}

# 3. Get items
TOKEN=$(echo '<login response>' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
curl -s $ALB/items -H "Authorization: Bearer $TOKEN"
# → 200 OK: [{"name":"Laptop","quantity":15}, ...5 products]

# 4. Validate token
curl -s $ALB/auth/validate -H "Authorization: Bearer $TOKEN"
# → 200 OK: {"valid":true,"userId":2,"username":"demo",...}
```

### Current Deployed Resources
| Component | Value |
|-----------|-------|
| ALB (public) | `onlineshop-alb-199112777.eu-north-1.elb.amazonaws.com` |
| ECS Cluster | `onlineshop-cluster` |
| Service Connect | `onlineshop.local` (auth→`auth:9001`, items→`items:9000`) |
| Auth | rev 3, image sha-befc22... |
| Items | rev 4, image sha-ba7905d |
| API Gateway | rev 12, image sha-ba7905d |
| RDS | `onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com:5432` |

---

## Quick Reference: Common AWS CLI Commands

| Action | Command |
|--------|---------|
| Who am I | `aws sts get-caller-identity --profile dpm-profile` |
| List services | `aws ecs describe-services --cluster onlineshop-cluster --services <name>` |
| View logs | `aws logs get-log-events --log-group-name /ecs/<svc> --log-stream-name <stream>` |
| Update service | `aws ecs update-service --cluster onlineshop-cluster --service <name> --task-definition <family>:<rev>` |
| Force redeploy | Add `--force-new-deployment` to update-service |
| Task IP | `aws ecs describe-tasks --tasks <arn> --query 'tasks[0].attachments[0].details'` |
| Secrets | `aws secretsmanager get-secret-value --secret-id onlineshop/auth/db` |
