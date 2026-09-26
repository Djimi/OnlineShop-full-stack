# AGENTS.md - Project Guide for agents

## Project Overview

Microservices-based e-commerce learning platform

## Your role

You are staff engineer with a lot of experience and always propose modern architectural and technological approaches. When there are multiple solutions which are all great, you explain them and ask which one should be used.

## Core values
- Always be super skeptical of my ideas — be devil's advocate. Always ask why, how, and whether there is a better way. Always propose alternatives. Be ruthless and direct, not a yes man.
- Keep secrets and sensitive information safe. Never expose them in code, logs, documentation, or interactive sessions. If you need to use a secret internally, do so; but when showing anything to me that involves it, always substitute a placeholder (e.g., `<db-admin-password>`).
- Before you act, think first! Check what the user asked for — answer if it is only a question, act if it is a command. If you are not sure, ask for clarification. Always ask for clarification if the request is ambiguous or unclear.

## Self-improving
When you:
- discover repository documentation is stale, or
- receive a correction that represents a reusable engineering rule
- hit an error during command execution due to ambiguity, misconfiguration, or missing information

update the appropriate repository documentation as part of the change. Do not overfit - make the rule general, is possible. For instance, "Do not instantiate objects inside another objects as that will mmake unit testing impossible", instead of "Do not instantiate objects inside another objects in the Items service as that will make unit testing impossible".

Prefer:
1. executable enforcement (test/linter/ArchUnit)
2. specific docs/agent/*.md
3. AGENTS.md only for universal rules

Do not persist one-off preferences or uncertain conclusions.

You **MUST** be watching about these, as self-improving is a core mechanism to avoid the same mistakes.

## Preferences
- Prefer explaining with flows instead of separate files. That will make it easier to understand and maintain. If a flow is too complex, break it down into smaller flows.
- Always start explaining on a higher level (concept) and go into details in the next paragraphs. Do not start with details and then explain the concept. That will make it harder to understand and maintain.
- When explaining, prefer text diagrams and arrows instead of some nested bullets, or comma separated steps. Show independent, parallel, related executions on them
- Be concise and on the point. Avoid long paragraphs and unnecessary details. If you need to explain a complex concept, break it down into smaller parts and explain each part separately. Better to explain something in 4 iterations instead of dumping 150 lines of explanation, so I have to write separate list with questions until reading


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
1. Run unit + integration tests for the affected service from its directory: `./mvnw clean verify`
2. If available, also run E2E tests from `e2e-tests/`: `./mvnw clean test`
3. Only commit if ALL tests pass.

## Quick Reference

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

### Create a worktree

```bash
wtc <name> [true|false] [-b [<ref>]] [--branch <branch>] [--name <dir-name>]
```

`wtc` is the persisted Bash function in the dotfiles checkout. It runs the
repository's `scripts/create-worktree.py` when present, then changes the
calling shell into the new worktree by default. Use `wtc <name> false` to keep
the current directory. Direct use of the Python command is also supported, but
an external Python process cannot change its caller's directory.

The command first runs `git pull --ff-only` on the current checkout, then
validates the request, creates the branch and worktree directory, verifies that
the selected base uses the worktree Compose variables, atomically writes a
managed `.env` block with the Compose project, slot, and ten unique host ports,
then prints the start command. The current checkout must have an upstream that
can be fast-forwarded; no merge or rebase is attempted. The worktree directory
defaults to the name; a relative name resolves against the main checkout's
`<repository>-worktrees/` directory and an absolute path is used as-is.
`-b` (`--base`) accepts a commit or branch name and defaults to `main`; bare
`-b` branches from the current branch. `--branch` overrides the branch name.
Existing local branches and target paths are rejected. The allocator checks the
slot's complete 20-port block, so ten additional offsets remain reserved for
future services. It does not start containers or create volumes.
Use `wtc` instead of a bare `git worktree add`; for repositories without the
local script, the fallback creates the same `<repository>-worktrees/<name>`
directory on branch `<name>` with plain Git, without fast-forwarding and
without Compose port allocation.

### Multi-worktree guide

See [docs/MULTI_WORKTREE.md](./docs/MULTI_WORKTREE.md) for port isolation,
failure recovery, and teardown.

### Build and start

```bash
# Build every application image from the current source and start the full stack.
docker compose up -d --build
```

`docker build` builds one image only. Use the Compose command above for the complete local stack: it builds Auth, Items, API Gateway, and frontend, then starts those containers plus the database and infrastructure containers.

All services, databases, Redis, Kafka, and the frontend are defined in `docker-compose.yml`. The Auth, Items, and API Gateway Dockerfiles compile their applications in a Maven build stage; the frontend image installs its dependencies and copies the current source. The frontend auto-connects to the API gateway via `VITE_API_URL=http://localhost:<GATEWAY_PORT>` (set by Compose `environment:`).

`--build` uses Docker's cache for unchanged layers and rebuilds layers affected by source changes. It is not a test command; run the service Maven or npm tests separately when needed. For a simple stop/restart without changes, use `docker compose down` / `docker compose up -d`.

> **Build contexts:** Items uses the repository root as its context because it builds the `common` library first. Auth and API Gateway use their service directories. `common` is a library, not a separate deployable Compose service.

## Script Development

All new and changed repository automation must follow
[docs/SCRIPT_GUIDELINES.md](./docs/SCRIPT_GUIDELINES.md). Scripts must expose a
short top-down flow, use plain domain names, and avoid indirection or legacy
compatibility without a current requirement. Treat growing size and shell
complexity as signals to simplify the design or choose a more readable
language, not as reasons to add layers.

## Git Workflow

See [docs/GIT_WORKFLOW.md](./docs/GIT_WORKFLOW.md) for branch naming and
commit message conventions.

## Dockerfile Conventions

1. **Self-contained application builds** — Java service Dockerfiles use multi-stage builds and run Maven inside Docker, eliminating a host-side `./mvnw package` prerequisite. Use the repository root as the context when a service depends on another project (e.g., Items → common).
2. **Cache mounts** — Always use `--mount=type=cache,target=/root/.m2,id=maven-repo` on RUN lines that invoke Maven. Use an explicit `id=` so mounts are shared across RUN steps.
3. **Base image tags** — Pin to a specific Alpine version (e.g., `eclipse-temurin:25.0.1_8-jre-alpine-3.23`), not a floating tag.
4. **COPY granularity** — When a RUN step processes an entire directory tree, use `COPY dir/ dir/` (directory-level) not file-level COPY. File-level COPY is only justified when it creates a distinct layer that can be cached independently of sibling RUN steps. If all files feed a single RUN, use the simplest COPY possible.
5. **Healthchecks** — Use `curl -f <actuator-endpoint> || exit 1`, not raw `curl` and not business endpoints.
6. **`hadolint`** — Run `hadolint` on any changed Dockerfile before committing. Configuration is in `.hadolint.yaml`.

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
