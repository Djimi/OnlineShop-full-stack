# API Gateway - Service Documentation

## Overview

The API Gateway is the entry point to OnlineShop services. It routes traffic, enforces auth, and adds resilience.

| Property   | Value                                  |
|------------|----------------------------------------|
| Port       | 10000                                  |
| Tech Stack | Spring Cloud Gateway, Redis, Caffeine  |
| Location   | `/api-gateway`                         |
| Database   | None (uses Redis for caching/limits)   |

## Docker Compose Build

Run from the repository root:

```bash
docker compose up -d --build api-gateway
```

`api-gateway/Dockerfile` is a self-contained multi-stage build. It compiles the current source inside Docker, so a host-side `target/*.jar` is not required. Use `docker compose up -d --build` to rebuild and start the complete stack.

## CI Verification

On every push and pull request, the independent API Gateway Java lane uses
Temurin 25 and runs this command from the `api-gateway/` module root:

```bash
./mvnw --batch-mode clean verify
```

The Java matrix uploads narrowly scoped Surefire/Failsafe reports under a
unique lane-specific artifact name. Separately, the root image job builds Auth,
Items, API Gateway, and frontend images with `docker compose build
auth-service items-service api-gateway frontend`; `common` is library-only.
The Gateway Docker build uses `-DskipTests`, so image packaging is not test
verification. Images are neither published nor deployed, and this image job
does not wait for the Java or frontend checks.

Only pull requests use the image job's locally built images to start the full
Compose stack with `docker compose up -d --no-build --wait --wait-timeout 300`.
After bounded gateway and frontend readiness checks, the current three API E2E
tests run through the gateway, Auth, and Items. They do not browser-test the
frontend or directly test each infrastructure service or administrative UI.
PR E2E reports, bounded Compose status, and redacted bounded application logs
on failure are retained before `docker compose down -v --remove-orphans` is
always attempted.

## Responsibilities

- **Routing**: `/auth/**` → Auth (rewritten to `/api/v1/auth/**`), `/items/**` → Items (rewritten to `/api/v1/items/**`).
- **Authentication**: Bearer token required for `/items/**`.
- **Public info endpoint**: `/api/product-info` → static product info (no auth, no downstream call).
- **Token caching**: L1 Caffeine + L2 Redis for validation results.
- **Rate limiting**: Bucket4j + Redis.
- **Resilience/observability**: Resilience4j + Micrometer/Prometheus.

## Contracts (Examples)

- Public: `/auth/**`
- Public: `/api/product-info` (static info endpoint; unversioned by design)
- Protected: `/items/**`

Example:
```bash
curl -H "Authorization: Bearer <token>" \
  http://localhost:10000/items
```

## Authentication Flow (Example)

```
1. Request hits gateway
2. /auth/** → forward without auth
3. /items/** → require Authorization header
4. Validate token: L1 → L2 → Auth service
5. On success → add X-User-Id, X-Username headers → forward
6. On failure → 401 Unauthorized
```

## Token Validation Caching

- **L1 (Caffeine)**: Nanosecond local hits.
- **L2 (Redis)**: Shared cache across instances.

Example metric (tag-based):
```
gateway.cache.operations.total{layer="l1", service="auth", result="hit"}
```

## Technology Stack

- **Spring Boot 4.X.X** with Java 25
- **Spring Cloud Gateway (Web MVC)** - Non-reactive, supports virtual threads
- **RestClient** - Modern synchronous HTTP client
- **Caffeine** - L1 local in-memory cache
- **Redis** - L2 distributed cache + rate limiting store
- **Bucket4j** - Rate limiting
- **Resilience4j** - Circuit breakers, retries, timeouts
- **Micrometer** - Metrics collection with Prometheus export
- **Lombok** - Compile-time annotations (provided scope)

## Project Structure

```
api-gateway/
├── src/main/java/com/onlineshop/gateway/
│   ├── cache/              # Token caching (L1/L2)
│   ├── config/             # Spring configuration
│   ├── dto/                # Data transfer objects
│   ├── exception/          # Custom exceptions
│   ├── filter/             # Security and validation filters
│   ├── metrics/            # Metrics instrumentation
│   ├── ratelimit/          # Rate limiting filter
│   ├── service/            # Business logic services
│   └── validation/         # Token sanitization
└── src/test/java/          # Unit and integration tests
```

## Resilience Notes

- Service boots even if Redis is down (cache is optional).
- Cached tokens continue to work during Auth outages; uncached tokens fail fast.
- Auth validation uses the annotation-backed Resilience4j `TimeLimiterRegistry`
  with a 5-second timeout to accommodate cold interservice validation. Future
  changes must keep the registry and `@TimeLimiter(name = "authService")`
  configuration aligned.
- `CompletableFuture.join()` wraps downstream resilience exceptions; the
  authentication filter unwraps `CompletionException`/`ExecutionException` so
  service-unavailable and timeout failures retain their 503/504 status instead
  of falling through to a generic 502.
- Rate limiting can be disabled via `gateway.ratelimit.enabled=false` (useful for tests).

## Rate Limiting Behavior

- All paths except `/actuator` and CORS preflight OPTIONS are limited. `/auth/**`
  (login/register/validate) is limited per IP with the anonymous bucket, so
  brute-force attempts are throttled.
- `RateLimitFilter` runs after `AuthenticationFilter`, so authenticated `/items/**`
  requests are keyed per user (`user:<id>`) while anonymous requests are keyed per
  IP (`ip:<addr>`).
- Failed authentications (missing/invalid/expired tokens) are additionally throttled
  per IP in `AuthenticationFilter` itself under a separate `failed:ip:<addr>` bucket
  (429 instead of 401 once it is exhausted), because those requests are rejected
  before the rate-limit filter ever sees them. The separate bucket keeps a user's
  failed retries from exhausting the anonymous bucket that their own login uses.
- Bucket capacity comes from `burst`; refill is greedy at `requests-per-minute`.
- `resolveClientIp` trusts the last `X-Forwarded-For` entry only when the direct
  peer matches configured `trusted-proxies` or is a private, link-local, or
  loopback address. Otherwise it uses `getRemoteAddr()`. A forged value from a
  trusted network can exhaust only that IP's bucket for one refill window.
- Rate limiting fails open when Redis is unavailable.

## Development Notes

- In this workspace, VS Code Spring Boot live-information auto-JMX is disabled via `.vscode/settings.json` so IDE launches do not compete with the gateway's HTTP port `10000`.

## Multi-Worktree Ports

Create non-main worktrees with the persisted root `wtc <name>` command (use
`wtc <name> false` to remain in the current checkout); it runs
`scripts/create-worktree.py` and fast-forwards the current checkout with
`git pull --ff-only` first.
It writes `GATEWAY_PORT` for Docker Compose to the root `.env`. Automatic
translation of that value into host-run Spring variables is deliberately
outside the worktree-creation command.

Repository automation must follow [Script Guidelines](../docs/SCRIPT_GUIDELINES.md).
