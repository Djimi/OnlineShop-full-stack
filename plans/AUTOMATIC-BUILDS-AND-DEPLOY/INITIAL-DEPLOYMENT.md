# INITIAL DEPLOYMENT — Commands & Outputs Log

> Raw log of what was run, why, and what output we got.

---

## 1. Add Actuator to Items (no actuator = no ECS health check)

```bash
# Build Items with actuator changes
cd Items && ./mvnw clean package -DskipTests
# → BUILD SUCCESS
cd api-gateway && ./mvnw clean package -DskipTests
# → BUILD SUCCESS
```

## 2. Build & Push Docker Images to ECR

```bash
cd Items && docker build -t .../onlineshop-items:sha-ba7905d .
cd api-gateway && docker build -t .../onlineshop-api-gateway:sha-ba7905d .

docker push .../onlineshop-items:sha-ba7905d
docker push .../onlineshop-api-gateway:sha-ba7905d
# → both pushed successfully
```

## 3. ECS Cluster

```bash
aws ecs create-cluster --cluster-name onlineshop-cluster
# → ACTIVE
```

## 4. IAM Execution Role (for ECR pulls + Secrets Manager)

```bash
aws iam create-role --role-name ecsTaskExecutionRole \
  --assume-role-policy-document '{"Statement":[{...ecs-tasks.amazonaws.com...}]}'
# → arn:aws:iam::799111666795:role/ecsTaskExecutionRole

aws iam attach-role-policy --policy-arn arn:aws:iam::aws:policy/.../AmazonECSTaskExecutionRolePolicy
aws iam put-role-policy --policy-name secretsmanager-read-onlineshop \
  --policy-document '{"Statement":[{...secretsmanager:GetSecretValue...}]}'
```

## 5. Security Groups

| SG | ID | Purpose |
|----|-------|---------|
| ALB | sg-0b5427a6a3bf31c29 | Public HTTP ingress |
| ECS | sg-0b209104a6b15b157 | Task-to-task + ALB-to-task |
| DB | sg-04ba95188d8374d96 | RDS (pre-existing) |

Rules:
- ALB SG: inbound tcp:80 from 0.0.0.0/0
- ECS SG: inbound tcp:0-65535 from ALB SG
- DB SG: inbound tcp:5432 from ECS SG

**MISSING:** Self-referencing rule on ECS SG for ports 9000, 9001, 6379. API Gateway → Auth/Items traffic blocked.

## 6. Cloud Map Namespace (for Service Connect)

```bash
aws servicediscovery create-private-dns-namespace \
  --name onlineshop.local --vpc vpc-06eeb0bc47ecdbd61
```

## 7. Task Definitions

| Service | Revision | Image | Key Config |
|---------|----------|-------|------------|
| Auth | 3 | sha-befc22... | HikariCP pool 10, Secrets Manager, startPeriod 180s |
| Items | 4 | sha-ba7905d | HikariCP pool 10, Actuator health, startPeriod 180s |
| API Gateway | 12 | sha-ba7905d | Redis sidecar, rate-limit disabled, Service Connect DNS (`auth`/`items`) |

## 8. ALB + Target Group + Listener

```bash
aws elbv2 create-load-balancer --name onlineshop-alb --subnets <3-subnets> --sg sg-0b5427a6a3bf31c29
# → DNS: onlineshop-alb-199112777.eu-north-1.elb.amazonaws.com

aws elbv2 create-target-group --name onlineshop-gateway-tg --port 10000 --target-type ip
aws elbv2 create-listener --port 80 --default-actions forward to gateway-tg
```

## 9. ECS Services (with Service Connect)

```bash
aws ecs create-service --service-name onlineshop-auth --task-def onlineshop-auth:3 \
  --launch-type FARGATE --network-config awsvpc --service-connect enabled

aws ecs create-service --service-name onlineshop-items --task-def onlineshop-items:4 \
  --launch-type FARGATE --network-config awsvpc --service-connect enabled

aws ecs create-service --service-name onlineshop-api-gateway --task-def onlineshop-api-gateway:7 \
  --launch-type FARGATE --network-config awsvpc --load-balancer gateway-tg --service-connect enabled
# → All ACTIVE
```

## 10. Issues Encountered & Resolved

### RDS Connection Pool Exhaustion
**Symptom:** `FATAL: remaining connection slots are reserved`  
**Cause:** Auth HikariCP had `max-pool-size: 100`, db.t4g.micro has ~25 connection limit  
**Fix:** Overrode via `SPRING_DATASOURCE_HIKARI_MAXIMUMPOOLSIZE=10`

### Items Container Crash (no actuator)
**Symptom:** Container health check `/actuator/health/liveness` returned 404  
**Fix:** Added actuator to Items, rebuilt image

### Redis Sidecar Startup Race
**Symptom:** API Gateway `Unable to connect to localhost:6379`  
**Cause:** Redis container not ready when Spring connects  
**Fix:** `dependsOn: {condition: HEALTHY}`, disabled rate limiter (`GATEWAY_RATELIMIT_ENABLED=false`)

### Service Connect DNS Not Resolving
**Symptom:** `java.nio.channels.UnresolvedAddressException` for `auth.onlineshop.local`  
**Status:** RESOLVED (2026-07-26). Service Connect was never enabled on the ECS services — only the Cloud Map namespace existed. Fixed by enabling Service Connect on all three services and updating gateway `SPRING_APPLICATION_JSON` to use `http://auth:9001` and `http://items:9000`.

### API Gateway → Auth Traversal Blocked
**Symptom:** Request times out when API Gateway forwards to Auth on port 9001  
**Cause:** ECS security group has no self-referencing rule for ports 9000/9001  
**Status:** TO FIX — add self-referencing inbound rules on ECS SG

---

## Current State — ALL ISSUES RESOLVED

**Full flow works:** register → login → token validation → items list (5 products).

All three services: **RUNNING** (1 each), **HEALTHY**

| Service | Revision | Image | Status |
|---------|----------|-------|--------|
| Auth | 3 | sha-befc22... | DB connected, pool 10 |
| Items | 4 | sha-ba7905d | DB connected, Actuator |
| API Gateway | 12 | sha-ba7905d | Redis UP, No rate-limit, Service Connect |

**ALB:** `http://onlineshop-alb-199112777.eu-north-1.elb.amazonaws.com`

**Issues Resolved:**
- SG self-referencing rules added (ports 9000-9001, 6379)
- Resilience4j TimeLimiter: 3s → 5s
- Service Connect DNS: enabled on all 3 services (2026-07-26), gateway uses `auth`/`items` hostnames
- Rate limiting: disabled (`GATEWAY_RATELIMIT_ENABLED=false`)

**Remaining tech debt (Pass 2):**
- Rate limiter lazy Redis connection
