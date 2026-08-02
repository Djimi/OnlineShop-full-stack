# Multi-Worktree Local Development

## Overview

This project supports running multiple git worktrees simultaneously on the same machine without port collisions. Each worktree gets a unique set of host ports via a slot-based port isolation scheme managed by `scripts/dev-env.sh`.

For a full step-by-step walkthrough of creating a worktree and starting its stack, see [MULTI_WORKTREE_WORKFLOW.md](./MULTI_WORKTREE_WORKFLOW.md).

## Quick Start (new worktree)

```bash
# In your new worktree, run once before the first docker compose up:
scripts/dev-env.sh

# Then start the stack as usual:
docker compose up -d --build
```

Thats it. The script generates a `.env` file with your worktree's unique ports. Main checkout uses the legacy ports unchanged.

**Note:** The initial slot is derived from the worktree directory name — renaming before the first `dev-env.sh` run changes the slot. After first run, the slot is stored in `.env` and is stable.

## `scripts/dev-env.sh` Usage

```bash
scripts/dev-env.sh              # Create/refresh managed .env block for this worktree
scripts/dev-env.sh --check       # Guard: exit 0 if safe to 'up', 1 if you forgot dev-env.sh
scripts/dev-env.sh --regenerate  # Down old stack, bump slot, rewrite .env
scripts/dev-env.sh --exports     # Print export variables for host-run dev mode
scripts/dev-env.sh --set-slot N  # Force a specific slot (1-631)
```

**Note:** `--set-slot` only rewrites `.env` with a new slot — it does NOT take down existing containers from a previous slot. Use `docker compose down` first if you want to stop the old stack, or use `--regenerate` for a migrating change.

## Port Mapping

Each worktree gets a slot number (1-631). Services sit at fixed offsets within the slot's 20-port block:

| Offset | Service | Slot-0 (legacy) | Slot N = 20000 + N×20 + offset |
|--------|---------|-----------------|-------------------------------|
| +0 | api-gateway | 10000 | 20000+N×20+0 |
| +1 | items-service | 9000 | +1 |
| +2 | auth-service | 9001 | +2 |
| +3 | frontend | 5173 | +3 |
| +4 | items-postgres | 5432 | +4 |
| +5 | auth-postgres | 5433 | +5 |
| +6 | pgadmin | 5051 | +6 |
| +7 | redis | 6379 | +7 |
| +8 | kafka (external) | 29092 | +8 |
| +9 | kafka-ui | 8080 | +9 |

> **Kafka host port:** The Kafka external port is a single published listener (`PLAINTEXT_HOST`). External Kafka clients use this port; in-cluster consumers use Docker DNS (`kafka:9092`).

All ports for a slot are sequential from the gateway: items = gateway+1, auth = gateway+2, etc. In Postman, set one variable (`gateway_url = http://localhost:<GATEWAY_PORT>`) and derive the rest by adding offsets.

**Note:** Port offset derivation (gateway+N) only works for worktree slots (slots 1-631) where all ports live in a contiguous block. On the main checkout (slot 0), legacy ports are non-sequential — set each Postman variable individually.

## Collision Probability

631 slots × 20-port blocks. With 8 worktrees active, the probability of two sharing the same initial hash-derived slot is ~4.3%. If a collision occurs, it is caught at generation time (bind check) or surfaces as a bind error at `docker compose up`. Recovery: `scripts/dev-env.sh --regenerate`.

## Container Names

All `container_name:` directives have been removed from `docker-compose.yml`. Containers are named by Compose with the project prefix (e.g., `onlineshop-wt47-items-postgres-1`). Use `docker compose exec <service>` (not `docker exec <container>`) to run commands inside containers.

## Daily Work

- **Start**: `docker compose up -d --build` — plain compose, no wrapper.
- **Stop**: `docker compose down` (add `-v` to drop volumes).
- **Logs**: `docker compose logs -f items-service`.
- **Status**: `docker compose ps`.
- **URL discovery**: Re-run `scripts/dev-env.sh` (idempotent) or read the `.env` managed block.

## Host-Run Dev Mode

Run a service on the host (e.g., `./mvnw spring-boot:run`) while infra runs in Docker:

```bash
source <(scripts/dev-env.sh --exports)
# Then start your service — all env vars are set

# For frontend:
npm run dev -- --port "$FRONTEND_PORT"
```

Per-service variable names are also exported for use in scripts and tooling that need distinct env vars per service:
- Items: `ITEMS_SERVER_PORT`, `ITEMS_DATASOURCE_URL`, `ITEMS_DATASOURCE_USERNAME`, `ITEMS_DATASOURCE_PASSWORD`
- Auth: `AUTH_SERVER_PORT`, `AUTH_DATASOURCE_URL`, `AUTH_DATASOURCE_USERNAME`, `AUTH_DATASOURCE_PASSWORD`
- Gateway: `GATEWAY_SERVER_PORT`

The generic `SERVER_PORT`, `SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME`, `SPRING_DATASOURCE_PASSWORD` are still exported for backward compatibility (last service defined overwrites previous — only one service can use these at a time on the host).

**WARNING:** `VITE_API_URL` must be set when running the frontend on the host. Without it, `api.ts` falls back to `http://localhost:10000` (main's gateway) — a silent cross-worktree data-plane mixup. The `--exports` output includes this warning.

## E2E Tests Against a Worktree

```bash
E2E_BASE_URL=http://localhost:<GATEWAY_PORT> ./mvnw clean test -f e2e-tests/pom.xml
```

## Regenerate After Collision

If `docker compose up` fails with a bind error:

```bash
scripts/dev-env.sh --regenerate
docker compose up -d --build
```

To also drop DB volumes:
```bash
scripts/dev-env.sh --regenerate --volumes
```

## Teardown

Before `git worktree remove`, run `docker compose down -v` to clean up containers and volumes. After worktree removal, the `.env` is gone and the project becomes hard to address.
