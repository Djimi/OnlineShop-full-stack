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
./mvnw test                 # Run all tests
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

**Validate contract:** invalid tokens return HTTP `200` with `valid=false` in the response body, not a 4xx error.

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

## Host-Run Dev Mode (Multi-Worktree)

In a non-main worktree, run the auth service on the host with `--exports` to pick up worktree-specific ports:

```bash
source <(scripts/dev-env.sh --exports)
./mvnw spring-boot:run
```

## AWS CLI Conventions

For AWS CLI commands (infrastructure queries, deployments, etc.), see the root [AGENTS.md](../AGENTS.md) — all AWS commands MUST include `--profile dpm-profile --region eu-north-1`.
