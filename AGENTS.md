# AGENTS.md - Project Guide for agents

## Project Overview

Microservices-based e-commerce learning platform


## AWS CLI Commands — MANDATORY

**ALL AWS CLI commands MUST include `--profile dpm-profile --region eu-north-1`. NO exceptions — even for seemingly harmless commands like `sts get-caller-identity`.**

```bash
# Always verify identity before any AWS work:
aws sts get-caller-identity --profile dpm-profile --region eu-north-1
```

- The profile uses IAM user credentials (not SSO). Without `--profile dpm-profile`, commands fail with "session expired" or unauthorized errors.
- If commands fail with `Your session has expired`, tell the user to re-authenticate. Do NOT retry.
- Every `create`/`put`/`delete` MUST be followed by a `describe`/`get`/`list` to confirm the change took effect.

## Your role

You are staff engineer with a lot of experience and always propose modern architectural and technological approaches. When there are multiple solutions which are all great, you explain them and ask which one should be used.

## Core values
- Always be super skeptical of my ideas — be devil's advocate. Always ask why, how, and whether there is a better way. Always propose alternatives. Be ruthless and direct, not a yes man.
- Keep secrets and sensitive information safe. Never expose them in code, logs, documentation, or interactive sessions. If you need to use a secret internally, do so; but when showing anything to me that involves it, always substitute a placeholder (e.g., `<db-admin-password>`).
- Before you act, think first! Check what the user asked for — answer if it is only a question, act if it is a command. If you are not sure, ask for clarification. Always ask for clarification if the request is ambiguous or unclear.


## Documentation Maintenance

**When making ANY changes to the project, treat each microservice as a separate module.
On each change you MUST update the files related to the respective microservice, so they are ABSOLUTELY independent and only know about each other on an architectural level.**

1. This file (`AGENTS.md`) if the change affects project-wide documentation
2. All referenced documentation files affected by the change (see sections below)
3. All files referenced by those files (recursive update through the entire reference chain)
4. All service-level `AGENTS.md` files in each microservice directory (e.g., `Auth/AGENTS.md`, `Items/AGENTS.md`, etc.)

**Documentation must always stay in sync. Propagate updates through the entire documentation tree.**

---

## Maven usage
When using Maven commands you MUST use the Maven wrapper (`./mvnw`) inside the service's folder you are working on — never from a parent or sibling directory. Always run from the target service's root folder (e.g., `Items/`, `Auth/`).

## Before Committing
**ALWAYS run tests first** — see [docs/TESTING_STRATEGY.md](./docs/TESTING_STRATEGY.md) for which tests to run. Never commit without passing tests.
1. Run unit + integration tests for the affected service from its directory: `./mvnw clean test`
2. If available, also run E2E tests from `e2e-tests/`: `./mvnw clean test`
3. Only commit if ALL tests pass.

## Quick Reference

### Project Identity

| Property | Value |
|----------|-------|
| AWS Account ID | `799111666795` |
| AWS Region | `eu-north-1` (Stockholm) |
| OIDC Role | `arn:aws:iam::799111666795:role/github-actions-onlineshop` |
| ECR Registry | `799111666795.dkr.ecr.eu-north-1.amazonaws.com` |
| ECR Naming | `onlineshop-<service>` (auth, items, api-gateway) |

### Services & Ports

| Service | Port | Java Version | Maven Wrapper | Depends On |
|---------|------|-------------|---------------|------------|
| Auth | 9001 | 25 | Yes | — |
| Items | 9000 | 25 | Yes | common |
| API Gateway | 10000 | 25 | Yes | — |
| Common | — | 25 | Yes | — |
| Frontend | 5173 | — | No | — |
| E2E Tests | — | — | Yes | — |

## Starting Services Locally

### First time in a worktree (required before `docker compose up`)

```bash
scripts/dev-env.sh --check  # Agent pre-up guard — exits 1 if you forgot dev-env.sh
```

### First-time setup (run once):

```bash
scripts/dev-env.sh           # Assigns unique ports, writes .env
```

### Multi-worktree guide

See [docs/MULTI_WORKTREE.md](./docs/MULTI_WORKTREE.md) for full port isolation details, collision recovery, host-run dev mode, and Postman setup.

### Build and start

```bash
# Build every application image from the current source and start the full stack.
docker compose up -d --build
```

`docker build` builds one image only. Use the Compose command above for the complete local stack: it builds Auth, Items, API Gateway, and frontend, then starts those containers plus the database and infrastructure containers.

All services, databases, Redis, Kafka, and the frontend are defined in `docker-compose.yml`. The Auth, Items, and API Gateway Dockerfiles compile their applications in a Maven build stage; the frontend image installs its dependencies and copies the current source. The frontend auto-connects to the API gateway via `VITE_API_URL=http://localhost:<GATEWAY_PORT>` (set by Compose `environment:`).

`--build` uses Docker's cache for unchanged layers and rebuilds layers affected by source changes. It is not a test command; run the service Maven or npm tests separately when needed. For a simple stop/restart without changes, use `docker compose down` / `docker compose up -d`.

> **Build contexts:** Items uses the repository root as its context because it builds the `common` library first. Auth and API Gateway use their service directories. `common` is a library, not a separate deployable Compose service.

## Dockerfile Conventions

1. **Self-contained application builds** — Java service Dockerfiles use multi-stage builds and run Maven inside Docker, eliminating a host-side `./mvnw package` prerequisite. Use the repository root as the context when a service depends on another project (e.g., Items → common).
2. **Cache mounts** — Always use `--mount=type=cache,target=/root/.m2,id=maven-repo` on RUN lines that invoke Maven. Use an explicit `id=` so mounts are shared across RUN steps.
3. **Base image tags** — Pin to a specific Alpine version (e.g., `eclipse-temurin:25.0.1_8-jre-alpine-3.23`), not a floating tag.
4. **COPY granularity** — When a RUN step processes an entire directory tree, use `COPY dir/ dir/` (directory-level) not file-level COPY. File-level COPY is only justified when it creates a distinct layer that can be cached independently of sibling RUN steps. If all files feed a single RUN, use the simplest COPY possible.
5. **Healthchecks** — Use `curl -f <actuator-endpoint> || exit 1`, not raw `curl` and not business endpoints.
6. **`hadolint`** — Run `hadolint` on any changed Dockerfile before committing. Configuration is in `.hadolint.yaml`.

## CI/CD & AWS Infrastructure

See [docs/CI_CD_GOTCHAS.md](./docs/CI_CD_GOTCHAS.md) for the full pitfall checklist. Always read that file before working on CI/CD or AWS infra.

### Before any AWS work
- Always run `aws sts get-caller-identity --profile dpm-profile --region eu-north-1` first in any new terminal session
- Always pass `--profile dpm-profile --region eu-north-1` explicitly on every command; AWS resources are region-scoped and invisible across regions
- Every `create`/`put`/`delete` MUST be followed by a `describe`/`get`/`list` to confirm the change took effect

### GitHub Actions development rules
1. **Version check always:** Before setting `java-version` in `setup-java`, cross-check `<java.version>` in `pom.xml` AND the `FROM` line in `Dockerfile`. All three must agree.
2. **Workflow dispatch testability:** `workflow_dispatch` workflows are ONLY indexed by GitHub from the default branch (`main`). During development on a feature branch, temporarily add a `push` trigger. Remove it before merging.
3. **Event context guard:** `github.event.inputs` is `null` on `push` events — it only exists for `workflow_dispatch`. Always check `github.event_name == 'workflow_dispatch'` before accessing `.inputs`.
4. **BuildKit requirement:** Any `docker/build-push-action` using `cache-from`/`cache-to` (type=gha) MUST be preceded by `docker/setup-buildx-action@v3`. The default runner Docker driver does not support cache export.
5. **Post-mutation verify:** Every AWS `create`/`put`/`delete` must be followed by a `describe`/`get`/`list` to confirm it took effect.

### Windows PowerShell → AWS JSON
PowerShell's default UTF-8-with-BOM encoding confuses AWS IAM. When creating JSON files for AWS:
```powershell
# DON'T use @'...'@ here-strings — they add a BOM
# DO use explicit ASCII encoding
[System.IO.File]::WriteAllText("path.json", $jsonString, [System.Text.Encoding]::ASCII)
```

## Playground Start/Stop

When not actively developing, pause the AWS playground to save ~$38/month:

```bash
# Stop the playground (scale ECS to 0 + delete ALB) — reduces to ~$1.25/month
bash plans/AUTOMATIC-BUILDS-AND-DEPLOY/scripts/pause-playground.sh

# Start the playground (recreate ALB + scale ECS to 1) — wait ~3-5 min for startup
bash plans/AUTOMATIC-BUILDS-AND-DEPLOY/scripts/resume-playground.sh
```

**Cost summary:**

| State | Monthly Cost |
|---|---|
| Running (Spot 24/7) | ~$49.00 |
| Running (Spot 8hr/day + ALB paused) | ~$17-18 |
| Paused | ~$1.25 |

Both scripts are self-contained with hardcoded infrastructure IDs — no `jq` required. See `plans/AUTOMATIC-BUILDS-AND-DEPLOY/explanations/COST-EXPLANATION.md` for breakdown.

## Maven Build Dependencies & Parallel Builds

### Dependency Graph
Auth - no dependencies on other projects
api-gateway - no dependencies on other projects
Items - depends on `common` (uses shared models/utilities)
common - no dependencies on other projects
e2e-tests - not part of the build graph (contains only tests, build separately when needed)
frontend - not part of the build graph (separate React app, build separately when needed)

### Parallel Build Strategy for agents

When asked to build multiple projects, **analyze the dependency graph above** and run builds in parallel whenever possible to save time.

## Git Workflow

See [docs/GIT_WORKFLOW.md](./docs/GIT_WORKFLOW.md) for:
- Branch naming conventions
- Commit message format (Conventional Commits)
- Pull request guidelines
- Release process
- Versioning strategy (Semantic Versioning)
- Merge policies
- Tagging conventions
- Hotfix procedures
- CI/CD integration
- Code review checklist
- Issue tracking and linking
- Documentation updates
- Rollback procedures
- Changelog maintenance
- Feature branching strategy

## Architecture & API Design

See [docs/API_DESIGN.md](./docs/API_DESIGN.md) for:
- API versioning and request/response format
- Error handling (RFC 9457 Problem Details)
- Observability and metrics standards (tag-based dimensional metrics)
- Logging standards
- Gateway exception: public, unversioned info endpoints (e.g., `/api/product-info`) when no service owns the data

## Testing Strategy
ALWAYS read this file before designing or writing tests!

See [docs/TESTING_STRATEGY.md](./docs/TESTING_STRATEGY.md) for:
- When to run tests
- Testing levels (unit, integration, e2e)
- Coverage requirements
- Test data management
- Mocking and stubbing guidelines
- CI testing integration
- Performance testing
- Security testing
- Test documentation

## Debug Info

See [docs/DEBUG_INFO.md](./docs/DEBUG_INFO.md) for:
- Troubleshooting guides
- Common issues and solutions

## Future Ideas

See [docs/CONCEPTS_TO_TRY.md](./docs/CONCEPTS_TO_TRY.md) for:
- Experimental concepts to explore (which are the target for future spikes)
- Future improvements

## Planning

- Add all plans in [planning](./planning/) folder
- Use the following name pattern `<feature-name>-PLAN.md`, for example `Migrating-auth-service-to-ddd-PLAN.md`
- In each plan create tasks to be done and when done put ticks on them, so I know what is implemented, what has left, etc.
- Create list with issues also - mainly technological (closed ports, things to be set up, etc). For the issues which are solved put green tick on them and explain how they are fixed briefly. In that way I will know what are the issues which left after the implementation
