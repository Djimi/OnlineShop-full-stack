# CLAUDE.md — Auth Service

## Quick Reference

| Property   | Value             |
|------------|-------------------|
| Port       | 9001              |
| Language   | Java 25           |
| Framework  | Spring Boot 4.1.0 |
| Database   | PostgreSQL 18     |
| Build Tool | Maven 3.9.12      |

## Commands

```bash
./mvnw clean install        # Build
./mvnw spring-boot:run      # Run
./mvnw spring-boot:run -Dspring-boot.run.arguments="--spring.profiles.active=db-troubleshooting"  # Run with DB diagnostics
./mvnw verify               # Run all unit + integration tests
```

## Docker Compose Build

Run from the repository root:

```bash
docker compose up -d --build auth-service
```

`Auth/Dockerfile` is a self-contained multi-stage build. It compiles the current source inside Docker, so a host-side `target/*.jar` is not required. Use `docker compose up -d --build` to rebuild and start the complete stack.

## Service Overview

Handles user authentication and session management: registration, login, session token validation, and session lifecycle. No caching layer — all reads hit the database directly.

Base package: `src/main/java/com/onlineshop/auth/`

Standard Spring Boot layered architecture: `controller/` → `service/` → `repository/` with JPA entities (`User`, `Session`), DTOs for request/response, custom exceptions with a global handler, and security configuration. Database init scripts live in `init-db/` (outside `src`).

## API Endpoints

| Method | Endpoint                   | Description            |
|--------|----------------------------|------------------------|
| POST   | `/api/v1/auth/register`    | Register new user      |
| POST   | `/api/v1/auth/login`       | User login             |
| GET    | `/api/v1/auth/validate`    | Validate session token |

**Validate contract:** invalid tokens return HTTP `200` with `valid=false` in the response body, not a 4xx error. A session is valid only when the current time is between its `createdAt` and `expiresAt` values, inclusive.

## Database

- Schema: [init-db/01-schema.sql](./init-db/01-schema.sql)
- Seed data: [init-db/02-seed-data.sql](./init-db/02-seed-data.sql)

## Configuration

Main config: [src/main/resources/application.yml](./src/main/resources/application.yml)

### DB Troubleshooting Profile

Activate with `--spring.profiles.active=db-troubleshooting`. Enables:

- Hibernate statistics and session event logging
- Slow query logging (configurable via `auth.troubleshooting.hibernate.slow-query-threshold-ms`, default unset)
- Verbose Hibernate SQL and statistics log levels
- HikariCP pool diagnostics at `TRACE` (`com.zaxxer.hikari.*`)
- Datasource acquisition timing with pool state (active/idle/waiting/total), sub-ms precision
- Console log format includes log level and logger name

Optional threshold overrides:

```bash
--auth.troubleshooting.hibernate.slow-query-threshold-ms=100
--auth.troubleshooting.datasource.acquire-slow-threshold-ms=2
```

### Local Compose Connection Capacity

The default `application.yml` Hikari settings currently reserve 100 Auth
connections (`minimum-idle` and `maximum-pool-size`). The local PostgreSQL
container may therefore reject administrative `psql` connections while
`auth-service` is running. For a local database inspection or cleanup:

```text
stop auth-service → run the narrowly scoped psql query → read back the result
                  → start auth-service → verify its health endpoint
```

This is an operational constraint, not a recommendation for production pool
sizing. Any pool-size change needs a separate capacity and deployment review.

## Multi-Worktree Ports

Create non-main worktrees with the persisted root `wtc <name>` command (use
`wtc <name> false` to remain in the current checkout); it runs
`scripts/create-worktree.py` and fast-forwards the current checkout with
`git pull --ff-only` first.
It writes the Auth and database host ports used by Docker Compose to the root
`.env`. Automatic translation of those Compose values into host-run Spring
variables is deliberately outside the worktree-creation command.

Repository automation must follow [Script Guidelines](../docs/SCRIPT_GUIDELINES.md).
