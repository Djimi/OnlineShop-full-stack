# API Gateway Service

## Overview

| Property   | Value                                  |
|------------|----------------------------------------|
| Port       | 10000                                  |
| Tech Stack | Spring Cloud Gateway, Redis, Caffeine  |
| Location   | `/api-gateway`                         |
| Database   | None (uses Redis for caching/limits)   |

## Responsibilities

- **Routing**: Forward `/auth/**` → Auth service (rewritten to `/api/v1/auth/**`) and `/items/**` → Items service (rewritten to `/api/v1/items/**`).
- **Authentication**: Enforce Bearer token auth for protected routes.
- **Token validation caching**: L1 Caffeine + L2 Redis to avoid per-request Auth calls.
- **Rate limiting**: Distributed limits via Bucket4j + Redis.
- **Resilience & observability**: Retries/timeouts/circuit breakers (Resilience4j) + Micrometer metrics.

The Docker Compose/E2E stack sets `GATEWAY_RATELIMIT_ENABLED=false` because
rate limiting is optional for local validation. The staging/production task
definitions make the same choice explicitly; token caching still uses Redis.

## Contracts (Examples)

- **Public routes** (no auth): `/auth/**`
- **Protected routes** (auth required): `/items/**`

Example request:
```bash
curl -H "Authorization: Bearer <token>" \
  http://localhost:10000/items
```

## Authentication Flow (Example)

```
1. Request hits gateway
2. /auth/** → forward without auth
3. /items/** → require Authorization: Bearer <token>
4. Validate token: L1 cache → L2 cache → Auth service
5. On success → add X-User-Id, X-Username headers → forward
6. On invalid/expired token → 401 Unauthorized; downstream Auth unavailable →
   503; Auth validation timeout → 504
```

## Token Validation Caching

- **L1 (Caffeine)**: Nanosecond local hits (fast path).
- **L2 (Redis)**: Shared cache across gateway instances.

Example metric (tag-based):
```
gateway.cache.operations.total{layer="l1", service="auth", result="hit"}
```

## Key Files

| Purpose         | Location                                                         |
|-----------------|------------------------------------------------------------------|
| Configuration   | `api-gateway/src/main/resources/application.yml`                 |
| Auth filter     | `api-gateway/src/main/java/.../filter/AuthenticationFilter.java` |
| Token cache     | `api-gateway/src/main/java/.../service/AuthValidationService.java` |
| Cache config    | `api-gateway/src/main/java/.../config/CacheConfig.java`          |
| Rate limiting   | `api-gateway/src/main/java/.../ratelimit/`                       |

## Running Locally

### With Docker Compose (Recommended)
```bash
docker compose up -d --build api-gateway
```

`api-gateway/Dockerfile` builds the service from source in a Maven build stage, so a host-side `target/*.jar` is not required. From the repository root, `docker compose up -d --build` rebuilds and starts the complete stack.

### Standalone
```bash
cd api-gateway
JAVA_HOME="/c/Program Files/Java/jdk-25" ./mvnw.cmd spring-boot:run
```

Requires: Redis, Auth Service, Items Service running.

## Health Check

```bash
curl http://localhost:10000/actuator/health
```

## Common Issues

- **CORS errors**: Check allowed origins in `application.yml` and verify calls go through the gateway.
- **Token cache misses**: Ensure Redis is up (`docker compose ps redis`, `redis-cli ping`).
