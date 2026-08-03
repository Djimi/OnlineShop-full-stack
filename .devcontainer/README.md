# OnlineShop Dev Container

A VS Code Dev Container that lets you run **all** OnlineShop services from source — Auth, Items, api-gateway, and the React frontend — against the existing Postgres / Redis / Kafka infra stack, with full debug support and full host-side port access (Postman, browser, DBeaver, pgAdmin all keep working).

## How to run it

**Prerequisites (one-time):**
1. Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) and make sure it's running. Use the WSL2 backend (the default).
2. Install [VS Code](https://code.visualstudio.com/) and the **Dev Containers** extension (`ms-vscode-remote.remote-containers`).

**Open the project inside the container:**
1. Open the repo root in VS Code: `File` → `Open Folder` → `D:\CodingProjects\OnlineShop-full-stack`.
2. **Stop any previously running compose stack** from the root: `docker compose down` in PowerShell. (The root `docker-compose.yml` maps the same ports as the devcontainer — e.g., port 10000 for api-gateway — so leftover containers will cause "port already in use" errors.)
3. `Ctrl+Shift+P` → **Dev Containers: Reopen in Container**.
4. First image build takes ~2–3 min (image pull, JDK 25 + Node LTS install). Subsequent reopens take seconds.
5. When VS Code finishes attaching, you'll see `OnlineShop Workspace` in the lower-left status bar.

**First-time setup (only needed once — cached in named volumes):**
```bash
# Maven deps (~3-5 min total, run in parallel or sequentially)
cd /workspaces/OnlineShop-full-stack
(cd Auth && ./mvnw -DskipTests compile) &
(cd Items && ./mvnw -DskipTests compile) &
(cd api-gateway && ./mvnw -DskipTests compile) &
wait

# Frontend deps (~20s)
cd frontend && npm install
```
After this, the `.m2` and `node_modules` volumes persist across container rebuilds — you only re-download if you delete the Docker volumes.

**Start the services (each in its own VS Code terminal or Run config):**
1. Open the **Run and Debug** view (`Ctrl+Shift+D`) and click ▶ on `AuthApplication`, then `ItemsApplication`, then `ApiGatewayApplication`. Breakpoints, hot-reload, variable inspection — all work.
2. Frontend: open a new terminal inside the container and run:
   ```bash
   cd frontend && npm run dev -- --host 0.0.0.0
   ```
   (`--host 0.0.0.0` is required so VS Code can forward port 5173 out of the container.)
3. Open `http://localhost:5173` in your Windows browser. Postman → `http://localhost:10000`. DBeaver → `localhost:5432` (items) and `localhost:5433` (auth). All ports are forwarded to the Windows host exactly like before.

**Use Claude Code inside the container:**
- **Headless / scripted:** `claude -p "your prompt here"`
- **Interactive (IDE):** the Anthropic Claude Code extension is pre-installed and reads credentials from `~/.claude`, which is bind-mounted from your Windows `%USERPROFILE%\.claude` so you stay logged in across rebuilds.

**To stop:** close the VS Code window. The workspace + infra containers stop automatically (`shutdownAction: stopCompose`).

**To rebuild after changing `.devcontainer/*`:** `Ctrl+Shift+P` → **Dev Containers: Rebuild Container**.

## What's inside

| Component | Where |
|-----------|-------|
| JDK 25 (Temurin) | `workspace` container |
| Maven (system + project `./mvnw`) | `workspace` container |
| Node LTS + npm | `workspace` container |
| Claude Code CLI (`claude`) | `workspace` container, installed at post-create |
| Postgres × 2, Redis, Kafka, Kafka UI, pgAdmin | sibling containers from root `docker-compose.yml` |
| auth-service / items-service / api-gateway | **NOT auto-started.** You launch them from inside the workspace. |

## How services find infra

A Spring profile `devcontainer` is activated automatically (via `SPRING_PROFILES_ACTIVE=devcontainer` in `remoteEnv`). Each service has a tiny `application-devcontainer.yml` that overrides **only** infra hostnames:

| Service | Override |
|---------|----------|
| Auth | `spring.datasource.url` → `auth-postgres:5432` |
| Items | `spring.datasource.url` → `items-postgres:5432` |
| api-gateway | `spring.data.redis.host` → `redis` |

The gateway's `gateway.auth.service-url` / `gateway.items.service-url` stay at `localhost:9001` / `localhost:9000` because Auth and Items run **inside the same workspace container** as the gateway.

## How to run a service

### From VS Code (with debugger)

Open the Run and Debug view (`Ctrl+Shift+D`) and pick:
- `AuthApplication`
- `ItemsApplication`
- `ApiGatewayApplication`

The launch configs in `.vscode/launch.json` need no changes — they inherit `SPRING_PROFILES_ACTIVE` from the container env.
The workspace also disables `boot-java.live-information.automatic-connection.on` in `.vscode/settings.json` so the Spring Boot dev tools do not auto-inject JMX/live-data wiring that can collide with the gateway's HTTP port `10000` during VS Code launches.

### From a terminal

```bash
cd Auth && ./mvnw spring-boot:run
# in another terminal
cd Items && ./mvnw spring-boot:run
# in another terminal
cd api-gateway && ./mvnw spring-boot:run
```

### Frontend

```bash
cd frontend && npm run dev -- --host 0.0.0.0
```

`--host 0.0.0.0` is required so VS Code can forward port 5173 out of the container to your Windows host.

## How to use Claude Code inside the container

**Headless / scripted:**
```bash
claude -p "summarize what changed in the auth service this week"
```

**Interactive (VS Code extension):** the Anthropic Claude Code extension is pre-installed and reads credentials from `~/.claude` — which is bind-mounted from your Windows `%USERPROFILE%\.claude`, so you stay logged in across rebuilds.

## Port forwarding

| Service | Container port | Host port | Source |
|---------|---------------:|----------:|--------|
| items-postgres | 5432 | 5432 | root compose |
| auth-postgres | 5432 | 5433 | root compose |
| redis | 6379 | 6379 | root compose |
| kafka | 9092 | 9092 | root compose |
| kafka-ui | 8080 | 8080 | root compose |
| pgadmin | 80 | 5051 | root compose |
| items-service | 9000 | 9000 | devcontainer forwardPorts |
| auth-service | 9001 | 9001 | devcontainer forwardPorts |
| api-gateway | 10000 | 10000 | devcontainer forwardPorts |
| frontend (vite) | 5173 | 5173 | devcontainer forwardPorts |

Everything is reachable from your Windows host as `http://localhost:<port>` exactly like before.

## Rebuilding

- **Code change:** none required — `..` is bind-mounted into the container.
- **devcontainer.json or Dockerfile change:** `Ctrl+Shift+P` → "Dev Containers: Rebuild Container".
- **Wipe Maven cache:** `docker volume rm <compose-project>_onlineshop-m2` (Compose project name varies; use `docker volume ls` to find it).

## Troubleshooting

- **Port already in use (e.g., 10000)** — a previous `docker compose up` from the repo root left containers running with the same port mappings. Run `docker compose down` from PowerShell before reopening in the devcontainer.
- **VS Code launch shows JMX live-data refresh failures or a false `10000 already in use` for `api-gateway`** — the workspace disables Spring Boot live-information auto-JMX in `.vscode/settings.json`. Reload the VS Code window if the extension still uses stale settings from before the change.
- **`./mvnw: Permission denied`** — run `chmod +x Auth/mvnw Items/mvnw api-gateway/mvnw common/mvnw`. The post-create script does this; if you cloned with Windows line endings disabled, you may need to redo it.
- **`MavenWrapperMain` ClassNotFoundException** — the committed `maven-wrapper.jar` is stale. Delete it and let mvnw re-download: `rm Auth/.mvn/wrapper/maven-wrapper.jar && cd Auth && ./mvnw -DskipTests compile`.
- **First Maven run is slow** — Maven deps are cached in the `onlineshop-m2` named volume. The very first build downloads everything (~3-5 min). After that, rebuilds are instant and survive container rebuilds.
- **Service can't reach Postgres** — verify the profile is active in the running JVM: hit `http://localhost:9001/actuator/env` and check `spring.profiles.active`. If empty, restart the run config.
- **Claude CLI not found** — the post-create install may have failed offline. Run `npm install -g @anthropic-ai/claude-code` manually (no sudo — use nvm's npm).
