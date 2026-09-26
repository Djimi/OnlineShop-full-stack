# Debug Info

> **Important:** Java service Dockerfiles build from source in Docker. When making code changes, rebuild and restart the affected service from the repository root:
>
> ```bash
> docker compose up -d --build <service-name>
> ```
>
> A host-side Maven package and manual `docker compose down` are not required. Docker reuses unchanged layers and includes changes from the service build context.
>
> `docker build` builds one image; use `docker compose up -d --build` when you want to rebuild and start the complete stack.

## Multi-Worktree Port Conflicts

New worktrees must be created with the persisted `wtc` function:

```bash
wtc <name> [true|false] [-b [<ref>]] [--branch <branch>] [--name <path>]
```

It runs `git pull --ff-only` on the current checkout before creating anything.
If the checkout has no upstream or has diverged, creation stops without a
merge or rebase. Direct use of `scripts/create-worktree.py` is supported, but
only the Bash function can change the caller's current directory.

If an external application takes a port after allocation and `docker compose
up` reports a bind error, stop that application and retry Compose. The
creation command checks all 20 ports while allocating but cannot prevent a
later process from binding one.

If the selected base ref predates the Compose port variables, creation stops
after checkout and prints exact commands for removing the incomplete worktree
and branch.

**Manual debug:** find what's using a port:
```bash
ss -tlnH "sport = :<port>"    # shows the process listening on <port>
docker ps --filter publish=<port>  # shows the container
```

**Missing `.env` claim?** The worktree was not created through the supported
Python command, or allocation failed after Git created it. Inspect and remove
the incomplete worktree, then recreate it with `wtc` or
`scripts/create-worktree.py`.

See [docs/MULTI_WORKTREE.md](./MULTI_WORKTREE.md) for full multi-worktree guide.


## Essential Commands

> **Note:** All `docker compose` commands must be run from the root project directory.

```bash
# Build current application sources and start all services
docker compose up -d --build

# Stop all services
docker compose down

# Stop all services AND remove volumes (DATABASE DATA WILL BE LOST)
# DANGER: Only run when explicitly requested and confirmed by user
docker compose down -v

# Apply code changes to a service (run from the repository root)
docker compose up -d --build <service-name>

# Host-side package is optional and is not needed by Docker Compose:
# cd <service-directory> && ./mvnw clean package -DskipTests

# Run unit + integration tests (from service directory)
./mvnw clean verify

# Run application with local profile without docker containers.
./mvnw spring-boot:run -Dspring-boot.run.profiles=local

# Run e2e tests (from e2e-tests/, requires docker compose up first)
cd e2e-tests && ./mvnw clean test

# Frontend development
cd frontend && npm run dev

# Frontend build
cd frontend && npm run build
```
> **Important:** If you ran into an issue that container is existing, check whether it was created by that docker compose file or not. If not, you need to remove it manually using `docker rm <container-id>` command.

---
